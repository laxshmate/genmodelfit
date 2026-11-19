# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""配布された一覧(GitHub Release の data-latest)を取り込む。`genmodelfit update --from-release`。

手順: db.json と db.json.sig を取る → 同梱の信じる公開鍵のどれかで署名を確かめる →
形(schema_version)を確かめる → 前に取り込んだ配布より古くないか確かめる →
書き出し先(既定はキャッシュ)を原子的に置き換える。
どこかで失敗したら、今のキャッシュ(無ければ同梱のデータ)には一切触らずに RemoteError を出す。

古さの比較は、前に取り込んだ配布の generated_at を別ファイル(キャッシュの release_state.json)に残して行う。
キャッシュの db.json は手元の `genmodelfit update` も書くので、比べる相手にしない。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from . import fetch as _fetch
from .db import Database, DatabaseError, cache_dir, cached_db_path, parse_db
from .fetch import USER_AGENT, make_session
from .i18n import tr
from .signing import SigningError, decode_sig, verify

if TYPE_CHECKING:
    import requests

RELEASE_BASE_URL = "https://github.com/laxshmate/genmodelfit/releases/download/data-latest"
MAX_BYTES = 64 * 1024 * 1024  # 一覧としては十分大きい上限
RETRY_DELAY = 5.0  # 署名が合わないときに 1 回だけ取り直すまでの秒数(資産の置き換えの途中に当たった場合)
STATE_NAME = "release_state.json"


class RemoteError(DatabaseError):
    pass


MAX_REDIRECTS = 10


def state_path(state_dir: Path | None = None) -> Path:
    """取り込みの記録のパス。state_dir を渡さなければキャッシュのフォルダ。"""
    return (state_dir if state_dir is not None else cache_dir()) / STATE_NAME


def _http_get(url: str, **kw: Any) -> requests.Response:
    """GET(~/.netrc の資格情報は送らない。fetch.make_session)。試験で差し替える。"""
    with make_session() as s:
        return s.get(url, **kw)


def _origin(url: str) -> tuple[str, str, int | None]:
    """(スキーム, ホスト, ポート)。ポートが不正な URL(https://x:99999/)は ValueError。"""
    u = urlsplit(url)
    return (u.scheme, (u.hostname or "").lower(), u.port)


def _session_get(session: requests.Session, url: str, timeout: float) -> requests.Response:
    """呼び出し側のセッションで url を取る(転送は自分で辿る)。

    - ~/.netrc は読ませない: 要求ごとに何もしない認証を渡す(requests は要求にもセッションにも認証が
      無いときだけ netrc を読む)。転送のたびに requests が netrc を読み直す(rebuild_auth)ので、
      allow_redirects=False にして転送はここで辿る。session.auth が真ならば呼び出し側の選んだものとして、
      最初のホストにだけ使う(別のホストへの転送には付けない)。空(None・()・"")は無いものとして扱う。
    - verify と proxies はセッションの値を要求ごとに渡す(渡さないと、環境変数 REQUESTS_CA_BUNDLE などが
      セッションの verify より先に効く。requests#3829)。プロキシの環境変数は、セッションに無い分だけ使う。
    - User-Agent は要求ごとに固定の値を付ける。セッションのほかのヘッダーは書き換えない。
    """
    try:
        first = _origin(url)
    except ValueError as e:
        raise RemoteError(tr(f"URL が正しくありません: {url}: {e}", f"invalid URL: {url}: {e}")) from e
    for _ in range(MAX_REDIRECTS + 1):
        try:
            same = _origin(url) == first
        except ValueError as e:
            raise RemoteError(
                tr(f"転送先の URL が正しくありません: {url}: {e}", f"invalid redirect URL: {url}: {e}")
            ) from e
        headers: dict[str, Any] = {"User-Agent": USER_AGENT}
        if not same:
            headers["Authorization"] = None  # セッションのヘッダーにあっても、別のホストへは送らない
        r = session.get(
            url,
            timeout=timeout,
            stream=True,
            headers=headers,
            auth=session.auth if (session.auth and same) else _fetch._NoAuth(),
            verify=session.verify,
            proxies=dict(session.proxies),
            allow_redirects=False,
        )
        loc = r.headers.get("Location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            return r
        r.close()
        url = urljoin(url, loc)
    raise RemoteError(tr(f"転送が多すぎます: {url}", f"too many redirects: {url}"))


def _get(url: str, timeout: float, session: requests.Session | None = None) -> bytes:
    """url を取る。MAX_BYTES を超えたら読むのをやめて RemoteError。
    session があればそれで取る(無ければ _http_get = fetch.make_session)。"""
    import requests

    try:
        r = (
            _session_get(session, url, timeout)
            if session is not None
            else _http_get(url, timeout=timeout, stream=True)
        )
        try:
            r.raise_for_status()
            length = r.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise RemoteError(tr(f"ファイルが大きすぎます: {url}", f"file too large: {url}"))
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise RemoteError(tr(f"ファイルが大きすぎます: {url}", f"file too large: {url}"))
        finally:
            r.close()
    except requests.RequestException as e:
        raise RemoteError(
            tr(f"ダウンロードできませんでした: {url}: {e}", f"download failed: {url}: {e}")
        ) from e
    return bytes(buf)


def _atomic_write(path: Path, data: bytes) -> None:
    """一時ファイルに書いてから置き換える。失敗したら RemoteError(元のファイルはそのまま)。"""
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        raise RemoteError(
            tr(
                f"書き込めませんでした: {path}: {e}"
                "(ほかのプログラムがこのファイルを開いていないか確かめて、もう一度実行してください)",
                f"cannot write: {path}: {e} (make sure no other program has this file open, then run again)",
            )
        ) from e
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


_FRACTION = re.compile(r"(\.\d+)")


def parse_time(value: Any) -> datetime:
    """ISO 8601 の日時を UTC の aware な datetime にする。末尾の Z も受け付ける。

    タイムゾーンの無い日時・形の違うものは ValueError(Python 3.10 の fromisoformat に合わせて、
    小数の秒は 6 桁にそろえてから読む)。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(tr(f"日時がありません: {value!r}", f"no date and time: {value!r}"))
    s = value.strip()
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    s = _FRACTION.sub(lambda m: "." + (m.group(1)[1:] + "000000")[:6], s, count=1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            tr(f"日時にタイムゾーンがありません: {value!r}", f"the date and time has no time zone: {value!r}")
        )
    return dt.astimezone(UTC)


def _read_state(state_dir: Path | None = None) -> datetime | None:
    """前に取り込んだ配布の generated_at。無い・壊れているときは None(次に取り込むと書き直す)。"""
    try:
        return parse_time(json.loads(state_path(state_dir).read_text(encoding="utf-8"))["generated_at"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _fetch_verified(base: str, timeout: float, session: requests.Session | None = None) -> bytes:
    """db.json と署名を取り、署名が合えば db.json を返す。合わなければ少し待って 1 回だけ取り直す。

    data.yml は db.json と db.json.sig を順に置き換えるので、その途中に取ると組み合わせがずれることがある。
    """
    for attempt in range(2):
        data = _get(f"{base}/db.json", timeout, session)
        sig_text = _get(f"{base}/db.json.sig", timeout, session)
        try:
            sig = decode_sig(sig_text)
        except SigningError:
            sig = b""
        if sig and verify(data, sig):
            return data
        if attempt == 0:
            time.sleep(RETRY_DELAY)
    if not sig:
        raise RemoteError(
            tr(
                "署名ファイルの形が正しくありません(取り込みません)",
                "the signature file is malformed (not imported)",
            )
        )
    raise RemoteError(
        tr(
            "署名が合いません。改ざんされたか、壊れた一覧です(取り込みません)",
            "the signature does not match: the list was tampered with or is broken (not imported)",
        )
    )


def update_from_release(
    base_url: str = RELEASE_BASE_URL,
    *,
    timeout: float = 30,
    dest: Path | None = None,
    force: bool = False,
    session: requests.Session | None = None,
    state_dir: Path | None = None,
) -> Database:
    """署名つきの一覧を取り、確かめてから dest(既定はキャッシュ)に置く。取り込んだ一覧を返す。

    force=True なら、前に取り込んだ配布より古くても取り込む(記録の日時がずれたときの戻し方)。
    session: 通信に使う requests.Session(会社のネットワークの CA バンドルを付けたものなど)。
        渡すと、この呼び出しの GET はすべてそれで行う(セッションの verify・proxies・headers はそのまま使う)。
        どちらの場合も ~/.netrc の資格情報は送らない(要求ごとに何もしない認証を渡して netrc の参照を止める)。
        ただし session.auth があれば呼び出し側の選んだものとして使う
        (最初のホストにだけ。別のホストへの転送には付けない)。
        User-Agent は固定の値を要求ごとに付ける。署名の確かめ・上限・失敗の扱いは渡さないときと同じ。
    state_dir: 取り込みの記録(release_state.json)を読み書くフォルダ(既定はキャッシュ)。
        環境変数やモジュールの値は書き換えない。
    """
    data = _fetch_verified(base_url.rstrip("/"), timeout, session)
    try:
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(tr("一覧が JSON のオブジェクトではありません", "the list is not a JSON object"))
        db = parse_db(raw, "cache")
        new_time = parse_time(raw.get("generated_at"))
    except (ValueError, TypeError, KeyError, AttributeError, DatabaseError) as e:
        raise RemoteError(tr(f"一覧の形が正しくありません: {e}", f"the list is malformed: {e}")) from e
    last = _read_state(state_dir)
    if last is not None and new_time < last and not force:
        raise RemoteError(
            tr(
                f"前に取り込んだ一覧より古い一覧です({new_time.isoformat()} < {last.isoformat()})。"
                "取り込みません(わざと戻すときは --force)",
                f"the list is older than the one imported before"
                f" ({new_time.isoformat()} < {last.isoformat()});"
                " not imported (use --force to go back on purpose)",
            )
        )
    # 記録を先に進める。後の書き込みが失敗しても、同じ配布はもう一度取り込める(同じ日時は通す)
    state = {"generated_at": new_time.isoformat(), "accepted_at": datetime.now(UTC).isoformat()}
    _atomic_write(state_path(state_dir), (json.dumps(state) + "\n").encode("utf-8"))
    _atomic_write(dest or cached_db_path(), data)
    return db


def __getattr__(name: str) -> Any:
    """`_NoAuth` は fetch から遅れて取る(import genmodelfit で requests を読まない。Issue #74)。"""
    if name == "_NoAuth":
        return _fetch._NoAuth
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
