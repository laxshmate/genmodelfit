# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""モデルを落とす(Issue #31。`genmodelfit download` と `gmf.plan_download()` / `gmf.download()`)。

- plan_download: 落とすファイルの一覧・大きさ・ライセンス・置き場所・止める理由を、通信せずに決める
  (精度は recommend と同じ選び方: この PC で逃がさずに載る中で一番品質の高いもの、無ければ段階が一番良いもの。
  recommend.variant_key)
- download: 計画どおりに落とす。同意(confirm / yes)が無ければ始めない

安全のための約束(README「Download」):
- 一覧の URL だけを使い、転送先は 1 回ごとに許したホスト(HOST_POLICY)か確かめる。https だけ
- ~/.netrc もトークンも使わない(fetch.make_session)。PC の情報は送らない(送るのは User-Agent と Range だけ)
- 書き込みは置き場所(root)の中だけ(`..`・絶対パス・ドライブ名・シンボリックリンクで外へ出るものは拒む)
- 書きながら sha256 を計り、合わなければ消して失敗にする。`.part` から HTTP Range で続ける
  (相手が Range に応じなければ最初から)。既にあるファイルは sha256 が合えば飛ばし、合わなければ上書きしない
- 空きは「落とす残り + 1 GiB」を見る。gated・無検閲(content を指定しないとき)・ライセンス不明
  (include_unknown_license を指定しないとき)は落とさない
- テンプレートの既定の値で使わない部品のうち、無効・素通しのノードや出力につながらないノードにしかないもの
  (ModelFile.optional が inactive。#81)は落とさない(with_optional=True で落とす)。スイッチで切ってある部品
  (switch_off)は ComfyUI の検証で要るので落とす

ライブラリの関数は何も表示しない。進み具合は progress に渡す(DownloadProgress)。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from .estimate import GIB
from .fetch import TIMEOUT, make_session
from .i18n import tr

if TYPE_CHECKING:
    import requests

    from .db import Database
    from .gate import GatePolicy
    from .models import ModelEntry, ModelFile, Settings, SystemInfo
    from .recommend import Fit

log = logging.getLogger("genmodelfit")

MARGIN_BYTES = GIB  # 空きの余裕(落とす残り + 1 GiB)
CHUNK = 1024 * 1024
MAX_REDIRECTS = 10
MAX_STALLED = 4  # 進まなかった失敗(接続・時間切れ・5xx・429)をこの回数まで続けてやり直す
MAX_RETRY_AFTER = 300.0
MAX_RESTARTS = 3  # 相手が Range に応じず最初から落とし直す回数の上限
MAX_RECEIVE_FACTOR = 3  # 1 ファイルで受け取る量の上限(大きさの何倍まで)
OTHER_DIR = "other"  # ComfyUI の置き場所(directory)が一覧に無いファイルの置き場所

# 計画のファイルの状態
STATUSES = ("download", "resume", "present", "conflict", "unavailable")
# 止める理由の種類(DownloadPlan.blocked_codes。#75)。uncensored と unknown_license だけは、利用者が同意の指定
# (content・include_unknown_license。CLI は端末の質問か --uncensored・--any-license)で外せる。
# ほかは指定では外れない
BLOCK_CODES = (
    "uncensored",
    "unknown_license",
    "gated",
    "no_files",
    "unavailable",
    "conflict",
    "budget",
    "disk",
)
CONSENT_CODES = ("uncensored", "unknown_license")


class DownloadError(RuntimeError):
    """落とせなかった(通信・中身の不一致・空き不足など)。plan があれば添える。"""

    def __init__(self, message: str, plan: DownloadPlan | None = None):
        super().__init__(message)
        self.plan = plan


class DownloadRefused(DownloadError):
    """安全のための約束で落とさない(gated・無検閲・ライセンス不明・同意が無い・置き場所の外など)。"""


class DownloadDeclined(DownloadRefused):
    """同意が得られなかった(confirm が False を返した)。"""


class ModelNotFound(DownloadError):
    """名前・ID に合うモデルが無い、または 2 件以上ある(candidates に候補)。"""

    def __init__(self, message: str, candidates: list[ModelEntry] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


class HostNotAllowed(DownloadError):
    """許していないホスト・https でない URL(転送先も含む)。"""


class ChecksumMismatch(DownloadError):
    """落としたファイルの sha256 が一覧と合わない(ファイルは消した)。"""


class DiskFull(DownloadError):
    """空きが足りない(落とす残り + 1 GiB)。"""


# --- 通信先 ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HostPolicy:
    """落とすときに話してよい相手。最初の URL は start_hosts、転送先は hosts か suffixes に合うもの。"""

    start_hosts: frozenset[str]
    hosts: frozenset[str]
    suffixes: tuple[str, ...]
    schemes: tuple[str, ...] = ("https",)
    any_port: bool = False  # 試験用(手元のサーバ)。既定は決まったポート(443)だけ

    def allowed(self, url: str, *, start: bool = False) -> bool:
        host_port = url_host(url)
        if host_port is None:
            return False
        scheme, host, port = host_port
        if scheme not in self.schemes:
            return False
        if port is not None and port != {"https": 443, "http": 80}.get(scheme) and not self.any_port:
            return False
        if start:
            return host in self.start_hosts
        return host in self.hosts or any(host.endswith(s) for s in self.suffixes)

    def check(self, url: str, *, start: bool = False) -> None:
        if not self.allowed(url, start=start):
            raise HostNotAllowed(url_refusal(url, start=start))


def url_refusal(url: str, *, start: bool = False) -> str:
    """URL を使わない理由(形が不正 か 許していない取得先・転送先)。"""
    hp = url_host(url)
    if hp is None:
        return tr(
            f"URL の形が不正です(ホストを 1 通りに読めません): {url[:200]!r}",
            f"Malformed URL (the host cannot be read one way): {url[:200]!r}",
        )
    if start:
        return tr(
            f"許していない取得先です(https の Hugging Face・GitHub だけ): {hp[1]}",
            f"Source host not allowed (https Hugging Face / GitHub only): {hp[1]}",
        )
    return tr(
        f"許していない転送先です(https の Hugging Face・GitHub だけ): {hp[1]}",
        f"Redirect target not allowed (https Hugging Face / GitHub only): {hp[1]}",
    )


# URL のどこにあっても拒む文字(\ は urlsplit と urllib3 でホストの読み方が分かれる。制御文字も同じ)
_URL_BAD = re.compile(r"[\\\x00-\x1f\x7f]")
# scheme と authority(ホスト:ポート)に入っていたら拒む文字(空白・% の符号化・@ の資格情報)
_AUTHORITY = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(//[^/?#]*)?")
_AUTHORITY_BAD = re.compile(r"[\s%@]")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def url_host(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port)。読み方が 2 通りに分かれうる URL は None(拒む)。

    requests が実際につなぐ先は urllib3.util.parse_url が読むホスト。urlsplit と urllib3 で違うホストに読める
    URL(`https://evil.com\\.hf.co/` など)で許可の判定をすり抜けられないように、\\・制御文字(どこでも)と、
    scheme・ホスト:ポートの中の空白・%・@(資格情報)・ASCII でないホストを拒む。パスの空白などは requests と
    同じく % に符号化してから比べ、2 つの読み方が同じホスト・ポートのときだけ返す。"""
    from requests.utils import requote_uri
    from urllib3.util import parse_url

    if not isinstance(url, str) or _URL_BAD.search(url):
        return None
    m = _AUTHORITY.match(url)
    if m is None or _AUTHORITY_BAD.search(m.group(0)):
        return None
    try:
        # パス・問い合わせの空白などは requests と同じく % に符号化してから、2 通りの読み方を比べる
        norm = requote_uri(url)
        u = urlsplit(norm)
        a_port = u.port
        p = parse_url(norm)
    except ValueError:
        return None
    except Exception:  # urllib3 の LocationParseError など
        return None
    if "@" in u.netloc or "%" in u.netloc or p.auth:
        return None
    a_host = (u.hostname or "").lower()
    b_host = (p.host or "").lower()
    scheme = (u.scheme or "").lower()
    if not a_host or a_host != b_host or scheme != (p.scheme or "").lower() or a_port != p.port:
        return None
    if len(a_host) > 253 or not all(_LABEL.fullmatch(x) for x in a_host.split(".")):
        return None  # DNS の名前として正しいもの(英数字とハイフン、各 63 文字まで)だけ。末尾の点も拒む
    return scheme, a_host, a_port


# Hugging Face(LFS は cdn-lfs*.hf.co などの CDN、Xet は cas-bridge.xethub.hf.co へ転送する)と
# GitHub(リリースの添付は objects / release-assets.githubusercontent.com へ転送する)
HOST_POLICY = HostPolicy(
    start_hosts=frozenset({"huggingface.co", "github.com"}),
    hosts=frozenset(
        {
            "huggingface.co",
            "hf.co",
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
    ),
    suffixes=(".huggingface.co", ".hf.co"),
)


# --- 置き場所 -------------------------------------------------------------------------------------

_DIR_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
# Windows の予約名(拡張子・空白が付いても同じ。COM¹ など上付きの数字も。CONIN$ / CONOUT$)
_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$)\s*(\..*)?$",
    re.IGNORECASE,
)


def _bad_part(p: str) -> bool:
    """パスの 1 部分として使えないか(`..`・Windows で危ない文字と予約名・末尾の点や空白・制御や書式の文字
    (右から左への上書きなど)・落とすときの一時ファイルと同じ末尾 .part / .lock)。"""
    return (
        not p
        or p in (".", "..")
        or bool(_BAD_CHARS.search(p))
        or bool(_RESERVED.match(p))
        or p.endswith((".", " "))
        or p.lower().endswith((".part", ".lock"))
        or any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in p)
        or len(p) > 200
    )


def safe_relpath(directory: str | None, name: str) -> tuple[str, ...] | None:
    """置き場所の中の相対パス(部分の並び)。外へ出る・Windows で危ない名前なら None。

    name は ComfyUI のサブフォルダを含んでよい("split/vae/x.safetensors")。`..`・`.`・空の部分・
    絶対パス・ドライブ名(`:`)・`\\`・制御文字・予約名(CON など)・末尾の点や空白は拒む。"""
    folder = directory or OTHER_DIR
    if not _DIR_OK.match(folder) or _bad_part(folder):
        return None
    parts = name.split("/")
    if any(_bad_part(p) for p in parts):
        return None
    return (folder, *parts)


def _real(path: Path) -> Path:
    """resolve() から Windows の拡張パスの頭(\\\\?\\)を外したもの。"""
    s = str(path.resolve())
    if s.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + s[8:])
    if s.startswith("\\\\?\\"):
        return Path(s[4:])
    return Path(s)


def _is_link(path: Path) -> bool:
    """リンク(シンボリックリンク・ジャンクション)か。無い・読めないときは False(呼び出し側が別に確かめる)。"""
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)  # Python 3.12 から
    return bool(is_junction and is_junction(path))


def _inside(path: Path, root: Path) -> bool:
    """path が root の中か。最後の名前はリンクでなければ解決しない(親のフォルダを解決して名前を足す)。

    最後の名前まで resolve() すると、別のスレッド・プロセスが消している途中(Windows の削除待ち)の
    .lock・.part で、Python が拡張パス(\\\\?\\C:\\…)のまま返し、中のファイルを「外」と誤る
    (#41。test_two_threads_same_destination が負荷のもとで 60 回に 7〜8 回落ちた)。"""
    try:
        if path.name in ("", ".", "..") or _is_link(path):
            target = _real(path)
        else:
            target = _real(path.parent) / path.name
        return target.is_relative_to(_real(root))
    except (OSError, ValueError):
        return False


def default_output_dir() -> Path:
    """--output-dir も --comfyui-dir も無いときの置き場所(利用者のデータの場所の genmodelfit/models)。"""
    from platformdirs import user_data_dir

    return Path(user_data_dir("genmodelfit", appauthor=False)) / "models"


def comfyui_models_dir(comfyui_dir: str | os.PathLike[str]) -> Path:
    """ComfyUI のフォルダ → models。ComfyUI に見えない(models も main.py も無い)なら DownloadRefused。"""
    base = Path(comfyui_dir).expanduser()
    if not (base / "models").is_dir() and not (base / "main.py").is_file():
        raise DownloadRefused(
            tr(
                f"ComfyUI のフォルダに見えません(models も main.py もありません): {base}"
                "(ComfyUI を入れたフォルダを指定してください。別の場所なら --output-dir)",
                f"This does not look like a ComfyUI folder (no models or main.py): {base} "
                "(give the folder where ComfyUI is installed; for another place use --output-dir)",
            )
        )
    return base / "models"


def _free_bytes(path: Path) -> int | None:
    p = path
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


# --- 計画 -----------------------------------------------------------------------------------------


@dataclass
class PlannedFile:
    """落とす 1 ファイル。status は STATUSES(unavailable / conflict があれば落とさない)。"""

    name: str
    url: str | None
    sha256: str | None
    size_bytes: int | None
    directory: str | None
    role: str | None
    license: str | None  # 判定に使うライセンス(gate.resolve_file_license。不明は None)
    license_name: str | None
    license_url: str | None
    license_note: str | None  # 一覧の値と違う読み方をした理由・確かめられない理由
    license_allowed: bool  # 許可リスト(gate の既定か --license-allowlist)にあるか
    gated: bool | None
    dest: str  # 置く場所(絶対パス)。置けないときは ""
    relpath: str  # 置き場所の中の相対パス(/ 区切り)
    status: str = "download"
    part_bytes: int = 0  # 途中まで落とした .part の大きさ
    reason: str | None = None  # unavailable / conflict の理由
    verified: bool = False  # present のとき、計画で sha256 まで計ったか(download は必ず計り直す)
    # 一覧に置き場所が無く、同梱の一覧の根拠から置き場所を決めたときの根拠
    # (機械が読む形。#75。infer_directory)。
    # 一覧の値・分からないときは None
    directory_basis: dict[str, Any] | None = None
    # license_url が無いとき、条件を確かめる場所(モデルのページ。#75)。license_url があれば None
    check_url: str | None = None

    @property
    def remaining_bytes(self) -> int:
        if self.status not in ("download", "resume"):
            return 0
        return max(0, (self.size_bytes or 0) - self.part_bytes)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["remaining_bytes"] = self.remaining_bytes
        return d


@dataclass
class DownloadPlan:
    """落とす計画(plan_download の結果)。blocked が空でなければ download は始めない。"""

    model_id: str
    name: str
    model_name: str
    modality: str
    content: str
    precision: str | None
    fit_level: str
    run_mode_label: str
    auto: bool
    layout: str  # comfyui(ComfyUI の models)/ output(--output-dir か既定の場所。同じ並び)
    root: str  # 置き場所(models に当たるフォルダ)
    files: list[PlannedFile] = field(default_factory=list)
    disk_free_bytes: int | None = None
    budget_bytes: int | None = None
    blocked: list[str] = field(default_factory=list)  # 落とさない理由
    blocked_codes: list[str] = field(default_factory=list)  # blocked と同じ順の種類(BLOCK_CODES。#75)
    warnings: list[str] = field(default_factory=list)  # 落とせるが知らせること
    alternatives: list[dict[str, Any]] = field(default_factory=list)  # 同じモデルのほかの精度・使い方
    license_url: str | None = None  # モデル一式のライセンスの場所
    chosen: str = ""  # なぜこのワークフローを選んだか
    # 落とさない任意の部品(ModelFile.optional が inactive。#81。with_optional=True なら空)のファイル名
    skipped_optional: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes or 0 for f in self.files)

    @property
    def download_bytes(self) -> int:
        """これから落とす残り(既にある・.part の分を除く)。"""
        return sum(f.remaining_bytes for f in self.files)

    @property
    def present_bytes(self) -> int:
        return sum(f.size_bytes or 0 for f in self.files if f.status == "present")

    @property
    def disk_needed_bytes(self) -> int:
        return self.download_bytes + MARGIN_BYTES if self.download_bytes else 0

    @property
    def ok(self) -> bool:
        return not self.blocked

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["files"] = [f.to_dict() for f in self.files]
        d.update(
            total_bytes=self.total_bytes,
            download_bytes=self.download_bytes,
            present_bytes=self.present_bytes,
            disk_needed_bytes=self.disk_needed_bytes,
            margin_bytes=MARGIN_BYTES,
            ok=self.ok,
        )
        return d


def _same_use(m: ModelEntry, members: Iterable[ModelEntry]) -> list[ModelEntry]:
    from .tasks import main_use

    use = main_use(m)
    return [x for x in members if main_use(x) == use]


def resolve_candidates(db: Database, query: str, content: str = "sfw") -> tuple[list[ModelEntry], bool]:
    """名前か ID → 候補のワークフロー(同じモデルの仲間)と、ID がそのまま合ったか。

    info と同じ順: ID がそのまま合えばそれ(内容によらず)/ モデルの表示名(大文字小文字を区別しない)
    ならその仲間 / ID・名前の一部(content で絞る)。一部の一致が 2 件以上で、同じモデルの仲間でなければ
    ModelNotFound(candidates に候補。先頭を黙って選ばない)。"""
    from .groups import index_for

    q = query.strip()
    if not q:
        raise ModelNotFound(tr("モデルの名前か ID が空です", "The model name or id is empty"))
    exact = db.get(q)
    if exact is not None:
        return [exact], True
    idx = index_for(db)
    key = idx.find(q)
    if key is not None:
        return list(idx.members[key]), False
    ql = q.lower()
    hits = [m for m in db.models if ql in m.id.lower() or ql in m.name.lower()]
    shown = [m for m in hits if content == "all" or m.content == content]
    if shown:
        keys = {idx.key_of.get(m.id) for m in shown}
        if len(keys) == 1:
            return list(idx.members[keys.pop()]), False
        if len(shown) == 1:
            return shown, False
        raise ModelNotFound(
            tr(
                f"「{q}」に合うモデルが {len(shown)} 件あります。ID かモデル名で指定してください",
                f'{len(shown)} models match "{q}". Give an id or a model name',
            ),
            shown,
        )
    if hits:
        kind_set = sorted({m.content for m in hits})
        kinds = "・".join(kind_set)
        raise ModelNotFound(
            tr(
                f"モデルが見つかりません: {q}(content {content} で外れた {kinds} のモデルが "
                f"{len(hits)} 件あります。"
                'content="all" にするか ID で指定してください)',
                f"No such model: {q} ({len(hits)} {'/'.join(kind_set)} models are hidden by "
                f'content {content}; use content="all" or give the id)',
            )
        )
    raise ModelNotFound(tr(f"モデルが見つかりません: {q}", f"No such model: {q}"))


def _hash_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


# 置き場所を決める根拠にする、パラメータ数の一致の幅(相対)。SDXL の inpainting(入力の層が 14,400 個多い)が
# sd_xl_base_1.0 と 4e-6 違う。別の系統はけた違いに離れる(docs/download.ja.md「コマンドライン」)
PARAMS_MATCH = 1e-5


# ComfyUI の folder_paths で同じフォルダとして読まれる古い名前
# (unet は diffusion_models、clip は text_encoders。
# estimate.DIRECTORY_ROLE も同じ組で同じ役割にしている)。一致の数え方ではこの組を 1 つの置き場所とみなす
DIRECTORY_ALIASES = {"unet": "diffusion_models", "clip": "text_encoders"}
GGUF_NODE = "ComfyUI-GGUF"  # GGUF を diffusion_models / text_encoders から読むのに要るカスタムノード


@dataclass(frozen=True)
class _DirRef:
    """公式テンプレートのファイル(置き場所が一覧にあり、ヘッダのパラメータ数が分かるもの)。"""

    params: int
    directory: str
    name: str
    template: str = ""


def directory_refs(db: Database) -> list[_DirRef]:
    """置き場所を決める手本: 公式テンプレート(利用者のテンプレートは除く)の、
    置き場所とパラメータ数のあるファイル。"""
    out = []
    for m in db.models:
        if m.source != "comfyui-template" or (m.template and m.template.username not in (None, "ComfyUI")):
            continue
        for f in m.files:
            if f.directory and f.header_params:
                folder = DIRECTORY_ALIASES.get(f.directory, f.directory)
                out.append(_DirRef(f.header_params, folder, f.name.rsplit("/", 1)[-1], m.id))
    return out


def infer_directory(f: ModelFile, refs: Iterable[_DirRef]) -> tuple[str | None, dict[str, Any] | None]:
    """一覧に置き場所が無いファイル(出どころによらない)の置き場所を、同梱の一覧の根拠から決める(#75)。
    (置き場所, 根拠)。

    根拠は 1 つだけ: 本体のファイルのヘッダのパラメータ数が、公式テンプレートのファイルと PARAMS_MATCH の幅で
    一致し、一致したファイルの置き場所が 1 通り(DIRECTORY_ALIASES は同じ置き場所。例 SDXL の全部入り =
    sd_xl_base_1.0 と同じ数 → checkpoints)。名前・タグからの推測はしない。決められなければ (None, None)
    (other に置く)。根拠は {kind, template, file, params, reference_params[, needs]}(needs は GGUF を
    diffusion_models / text_encoders に置くとき要るカスタムノード)。"""
    if f.directory or not f.header_params or f.role not in (None, "main"):
        return None, None
    n = f.header_params
    hits = [r for r in refs if abs(r.params - n) <= n * PARAMS_MATCH]
    dirs = {r.directory for r in hits}
    if len(dirs) != 1:
        return None, None
    ref = min(hits, key=lambda r: (abs(r.params - n), r.name))
    basis: dict[str, Any] = {
        "kind": "header_params",
        "template": ref.template,
        "file": ref.name,
        "params": n,
        "reference_params": ref.params,
    }
    if f.name.lower().endswith(".gguf") and ref.directory in ("diffusion_models", "text_encoders"):
        basis["needs"] = GGUF_NODE
    return ref.directory, basis


def check_url(m: ModelEntry, f: ModelFile) -> str | None:
    """license_url が無いファイルの条件を確かめる場所(モデルのページ)。HF の ID ならそのページ、
    無ければ一覧のモデルのページ(homepage。https のものだけ)、それも無ければファイルの HF のリポジトリ
    (公式テンプレートでは Comfy-Org などの置き場所で、作者とは限らない)。"""
    if m.id.startswith("hf:") and "/" in m.id:
        return f"https://huggingface.co/{m.id[3:]}"
    if m.homepage and m.homepage.lower().startswith("https://"):
        return m.homepage
    if f.repo_id:
        return f"https://huggingface.co/{f.repo_id}"
    return None


def _plan_file(
    f: ModelFile,
    root: Path,
    allow: frozenset[str],
    verify_existing: bool,
    m: ModelEntry | None = None,
    refs: Iterable[_DirRef] = (),
) -> PlannedFile:
    from .gate import license_allowed, resolve_file_license

    r = resolve_file_license(f)
    directory, basis = (f.directory, None) if f.directory else infer_directory(f, refs)
    rel = safe_relpath(directory, f.name)
    dest = root.joinpath(*rel) if rel else None
    pf = PlannedFile(
        name=f.name,
        url=f.url,
        sha256=(f.sha256 or "").lower() or None,
        size_bytes=f.size_bytes,
        directory=directory,
        role=f.role,
        license=r.license,
        license_name=r.license_name,
        license_url=f.license_url,
        license_note=r.note,
        license_allowed=license_allowed(r.license, r.license_name, allow),
        gated=True if (f.gated or r.gated) else f.gated,
        dest=str(dest) if dest else "",
        relpath="/".join(rel) if rel else "",
        directory_basis=basis,
        check_url=None if f.license_url or m is None else check_url(m, f),
    )
    reason = None
    if rel is None or dest is None or not _inside(dest, root):
        reason = tr(
            f"置き場所の外を指す名前です({directory or '-'}/{f.name})",
            f"The name points outside the destination ({directory or '-'}/{f.name})",
        )
    elif not f.url:
        reason = tr("一覧に URL がありません", "The list has no URL")
    elif not HOST_POLICY.allowed(f.url, start=True):
        reason = url_refusal(f.url, start=True)
    elif not pf.sha256 or not re.fullmatch(r"[0-9a-f]{64}", pf.sha256):
        reason = tr(
            "一覧に sha256 が無く、中身を確かめられません",
            "The list has no sha256, so the content cannot be verified",
        )
    elif not f.size_bytes or f.size_bytes <= 0:
        reason = tr(
            "一覧に大きさが無く、空きを確かめられません",
            "The list has no size, so the free space cannot be checked",
        )
    if reason:
        pf.status, pf.reason = "unavailable", reason
        return pf
    assert dest is not None
    part = dest.with_name(dest.name + ".part")
    for p in (dest, part):
        if p.is_symlink():
            pf.status, pf.reason = (
                "conflict",
                tr(f"シンボリックリンクには書きません: {p}", f"Will not write to a symbolic link: {p}"),
            )
            return pf
    if dest.exists():
        if not dest.is_file() or dest.stat().st_size != f.size_bytes:
            pf.status, pf.reason = (
                "conflict",
                tr(
                    "同じ名前の別のファイルがあります(上書きしません)",
                    "A different file with the same name exists (not overwritten)",
                ),
            )
        elif not verify_existing:
            pf.status = "present"  # 大きさだけ(download がその場で sha256 を計る)
        elif _hash_file(dest) == pf.sha256:
            pf.status, pf.verified = "present", True
        else:
            pf.status, pf.reason = (
                "conflict",
                tr(
                    "同じ名前のファイルの sha256 が違います(上書きしません)",
                    "A file with the same name has a different sha256 (not overwritten)",
                ),
            )
        return pf
    if part.is_file():
        size = part.stat().st_size
        if 0 < size < (f.size_bytes or 0) or size == f.size_bytes:
            pf.status, pf.part_bytes = "resume", size
    return pf


def _alt_dict(fit: Fit, p: DownloadPlan | None = None) -> dict[str, Any]:
    from .recommend import memory_use

    _, need, _ = memory_use(fit.estimate, fit.system)
    return {
        "id": fit.model.id,
        "precision": fit.model.precision,
        "use": fit.use,
        "fit_level": fit.estimate.fit,
        "memory_required_gb": round(need, 2) if need is not None else None,
        "size_bytes": fit.model.total_size_bytes,
        "auto": fit.auto,
        # 指定では外せない理由(gated・ファイル無し・取得できないファイル・同じ名前の別のファイル)
        # が無いか(#75)。
        # 無検閲・ライセンス不明・空き・上限は数えない
        "ok": p is not None
        and not any(c in ("gated", "no_files") for c in p.blocked_codes)
        and all(f.status not in ("unavailable", "conflict") for f in p.files),
    }


def _block(plan: DownloadPlan, code: str, text: str) -> None:
    """止める理由を足す(blocked と blocked_codes を同じ順に保つ)。"""
    plan.blocked.append(text)
    plan.blocked_codes.append(code)


def _model_blocks(
    m: ModelEntry, content: str, include_unknown_license: bool, files: list[PlannedFile]
) -> list[tuple[str, str]]:
    """モデル一式で止める理由((種類, 文) の並び)。"""
    out: list[tuple[str, str]] = []
    if m.content == "uncensored" and content not in ("all", "uncensored"):
        out.append(
            (
                "uncensored",
                tr(
                    '無検閲のモデルです。落とすなら同意が要ります(content="all" か "uncensored")',
                    "This is an uncensored model. Downloading it needs consent"
                    ' (content="all" or "uncensored")',
                ),
            )
        )
    gated = [f.name for f in files if f.gated]
    if gated:
        out.append(
            (
                "gated",
                tr(
                    "利用申請(gated)が要るファイルがあります: "
                    + ", ".join(gated[:3])
                    + "。genmodelfit はログイン情報を扱わないので落としません"
                    "(配布元のページで申請して自分で落としてください)",
                    "Some files need an access request (gated): "
                    + ", ".join(gated[:3])
                    + ". genmodelfit does not handle login credentials, so it will not download them"
                    " (request access on the publisher's page and download them yourself)",
                ),
            )
        )
    unknown = [f.name for f in files if f.license is None]
    if unknown and not include_unknown_license:
        out.append(
            (
                "unknown_license",
                tr(
                    "ライセンスが分からないファイルがあります: "
                    + ", ".join(unknown[:3])
                    + "。中身の条件を自分で確かめたうえで落とすなら同意が要ります"
                    "(include_unknown_license=True)",
                    "Some files have an unknown license: "
                    + ", ".join(unknown[:3])
                    + ". Downloading them after checking the terms yourself needs consent"
                    " (include_unknown_license=True)",
                ),
            )
        )
    if not m.files:
        out.append(
            ("no_files", tr("一覧にファイルの記載がありません", "The list has no files for this model"))
        )
    return out


def download_files(m: ModelEntry, with_optional: bool = False) -> tuple[list[ModelFile], list[str]]:
    """落とすファイルと、落とさない任意の部品の名前(#81)。無効・素通しのノードや出力につながらないノードに
    しかない部品(inactive)は prompt に入らず ComfyUI の検証もされないので、with_optional でなければ
    落とさない。"""
    from .estimate import optional_kind

    if with_optional:
        return list(m.files), []
    keep = [f for f in m.files if optional_kind(f) != "inactive"]
    return keep, [f.name for f in m.files if optional_kind(f) == "inactive"]


def plan_download(
    model: str,
    *,
    precision: str | None = None,
    budget_gb: float | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    comfyui_dir: str | os.PathLike[str] | None = None,
    content: str = "sfw",
    include_unknown_license: bool = False,
    license_allowlist: Iterable[str] | None = None,
    verify_existing: bool = True,
    settings: Settings | None = None,
    system_info: SystemInfo | None = None,
    db: Database | None = None,
    comfyui_version: str | None = None,
    policy: GatePolicy | None = None,
    with_optional: bool = False,
) -> DownloadPlan:
    """落とす計画を作る(通信しない・何も書かない)。CLI の `download --list` と同じ。

    model: モデルの表示名・ワークフローの ID・名前の一部(info と同じ探し方)。ID がそのまま合えばその
    ワークフロー、表示名・一部なら同じモデルの仲間から、この PC で逃がさずに載る中で一番精度の高いもの
    (無ければ段階が一番良いもの。recommend と同じ選び方)。
    precision: その精度の仲間だけ(ID を指定したときも、同じ使い方の仲間から選ぶ)。
    budget_gb: 落とす量(既にあるファイルを除く。GiB)の上限。超えない中で一番良いものを選ぶ。
    output_dir / comfyui_dir: 置き場所(両方は ValueError)。comfyui_dir なら <dir>/models/<directory>/<名前>、
    output_dir(既定は default_output_dir())も同じ並び。content: 無検閲は "all" / "uncensored" のときだけ
    落とせる。include_unknown_license: ライセンスの分からないファイルを含んでも落とせる。
    verify_existing: 既に置いてあるファイルの sha256 を計る(False なら大きさだけ)。
    with_optional: テンプレートの既定の値で使わない部品のうち、無効のノードなどにしかないもの(inactive。#81)も
    落とす(既定は落とさず、plan.skipped_optional に名前を入れる)。
    止める理由は plan.blocked、知らせることは plan.warnings(例外にはしない)。名前が合わない・
    2 件以上合う・置き場所が ComfyUI に見えないときは ModelNotFound / DownloadRefused。
    """
    from .db import load as load_db
    from .estimate import load_coefficients
    from .gate import DEFAULT_LICENSE_ALLOWLIST, GatePolicy
    from .groups import index_for, precision_label
    from .hardware import detect
    from .recommend import CONTENT_FILTERS, judge, variant_key

    if content not in CONTENT_FILTERS:
        raise ValueError(
            tr(
                f"content が正しくありません: {content!r}(使えるのは {', '.join(CONTENT_FILTERS)})",
                f"Invalid content: {content!r} (choose from {', '.join(CONTENT_FILTERS)})",
            )
        )
    if output_dir is not None and comfyui_dir is not None:
        raise ValueError(
            tr(
                "output_dir と comfyui_dir は同時に指定できません",
                "output_dir and comfyui_dir cannot be used together",
            )
        )
    if budget_gb is not None and (isinstance(budget_gb, bool) or not budget_gb > 0):
        raise ValueError(
            tr(
                f"budget_gb は 0 より大きい数です: {budget_gb!r}",
                f"budget_gb must be a number above 0: {budget_gb!r}",
            )
        )
    db = db or load_db()
    system = system_info or detect()
    pol = policy or GatePolicy(comfyui_version=comfyui_version)
    allow = frozenset(a.lower() for a in license_allowlist) if license_allowlist else pol.license_allowlist
    allow = allow or DEFAULT_LICENSE_ALLOWLIST
    if comfyui_dir is not None:
        root, layout = comfyui_models_dir(comfyui_dir), "comfyui"
    else:
        root, layout = (Path(output_dir).expanduser() if output_dir else default_output_dir()), "output"
    root = Path(os.path.abspath(root))

    cands, exact = resolve_candidates(db, model, content)
    idx = index_for(db)
    if precision:
        want = precision.strip().lower()
        pool = cands
        if exact:  # ID を指定して精度も指定: 同じモデル・同じ使い方の仲間から
            pool = _same_use(cands[0], idx.members[idx.key_of[cands[0].id]])
        cands = [m for m in pool if (m.precision or "").lower() == want]
        if not cands:
            have = sorted({precision_label(m.precision) for m in pool})
            raise ModelNotFound(
                tr(
                    f"精度 {precision} のワークフローがありません(あるのは {', '.join(have)})",
                    f"No workflow with precision {precision} (available: {', '.join(have)})",
                )
            )
    c = load_coefficients()
    fits = [judge(m, system, db, settings=settings, coefficients=c, policy=pol) for m in cands]
    budget = int(budget_gb * GIB) if budget_gb is not None else None

    refs = directory_refs(db)
    plans: list[tuple[tuple, DownloadPlan, Fit]] = []
    for fit in fits:
        m = fit.model
        use, skipped = download_files(m, with_optional)
        files = [_plan_file(f, root, allow, verify_existing and len(fits) == 1, m, refs) for f in use]
        p = DownloadPlan(
            model_id=m.id,
            name=m.name,
            model_name=fit.model_name,
            modality=m.modality,
            content=m.content,
            precision=m.precision,
            fit_level=fit.estimate.fit,
            run_mode_label=fit.run_mode_label,
            auto=fit.auto,
            layout=layout,
            root=str(root),
            files=files,
            budget_bytes=budget,
            license_url=m.license_url,
            skipped_optional=skipped,
        )
        for code, text in _model_blocks(m, content, include_unknown_license, files):
            _block(p, code, text)
        over = budget is not None and p.download_bytes > budget
        # 候補の順: 止める理由が無い → 上限に収まる → recommend と同じ選び方(variant_key)
        plans.append(((bool(p.blocked), over, *variant_key(fit)), p, fit))
    plans.sort(key=lambda x: x[0])
    _, plan, fit = plans[0]
    if len(plans) > 1 and verify_existing:
        # 選んだものだけ計り直す
        use = download_files(fit.model, with_optional)[0]
        plan.files = [_plan_file(f, root, allow, True, fit.model, refs) for f in use]
    plan.alternatives = [_alt_dict(x[2], x[1]) for x in plans[1:]]
    plan.chosen = (
        tr("ID で指定", "Given by id")
        if exact and not precision
        else _chosen_reason(fit, len(plans), precision, budget is not None)
    )
    _finish(plan, fit, allow)
    return plan


def _chosen_reason(fit: Fit, count: int, precision: str | None, budget: bool) -> str:
    """名前で選んだときの、選んだ決まり。

    recommend.variant_key と同じ順(docs/download.md「コマンドライン」)。"""
    from .recommend import runs_clean

    if count == 1:
        rule = tr("候補はこれだけ", "the only candidate")
    elif runs_clean(fit.estimate):
        rule = tr(
            "この PC で逃がさずに載る(good 以上で GPU)中で一番精度の高いもの",
            "the highest precision that fits this PC without offloading (good or better on the GPU)",
        )
    else:
        rule = tr(
            "逃がさずに載るものが無いので、段階が一番良いもの(同じなら逃がさない載せ方、次に精度の高いもの)",
            "nothing fits without offloading, so the best fit level"
            " (then the faster run mode, then the higher precision)",
        )
    extra = []
    if precision:
        extra.append(tr(f"精度 {precision} の中から", f"among precision {precision}"))
    if budget:
        extra.append(tr("--budget に収まるものを先に", "those within --budget first"))
    extra.append(tr(f"{count} 件から。recommend と同じ選び方", f"from {count}; chosen like recommend"))
    return f"{rule}" + tr(f"({'、'.join(extra)})", f" ({'; '.join(extra)})")


def _finish(plan: DownloadPlan, fit: Fit, allow: frozenset[str]) -> None:
    """計画の止める理由・知らせることを足す(ファイルの状態と空き)。"""
    root = Path(plan.root)
    for f in plan.files:
        if f.status == "unavailable":
            _block(plan, "unavailable", f"{f.name}: {f.reason}")
        elif f.status == "conflict":
            _block(
                plan,
                "conflict",
                tr(
                    f"{f.relpath}: {f.reason}(別の場所へ移すか、--output-dir で別の置き場所を)",
                    f"{f.relpath}: {f.reason} (move it elsewhere, or choose another place with --output-dir)",
                ),
            )
    if plan.budget_bytes is not None and plan.download_bytes > plan.budget_bytes:
        _block(
            plan,
            "budget",
            tr(
                f"落とす量 {plan.download_bytes / GIB:.2f} GB が上限 "
                f"{plan.budget_bytes / GIB:.2f} GB を超えます(--budget)",
                f"The download of {plan.download_bytes / GIB:.2f} GB exceeds the limit of "
                f"{plan.budget_bytes / GIB:.2f} GB (--budget)",
            ),
        )
    plan.disk_free_bytes = _free_bytes(root)
    if plan.disk_free_bytes is not None and plan.disk_needed_bytes > plan.disk_free_bytes:
        _block(
            plan,
            "disk",
            tr(
                f"空きが足りません: 要る {plan.disk_needed_bytes / GIB:.2f} GB(落とす残り + 1 GB)、"
                f"空き {plan.disk_free_bytes / GIB:.2f} GB",
                f"Not enough free disk: needs {plan.disk_needed_bytes / GIB:.2f} GB (the rest to download "
                f"+ 1 GB), free {plan.disk_free_bytes / GIB:.2f} GB",
            ),
        )
    names = [f"{f.name}({_lic(f)})" for f in plan.files if f.license is not None and not f.license_allowed]
    if names:
        plan.warnings.append(
            tr(
                "許可リスト(商用で使えて使い方の制限が無いライセンス)に無いライセンスのファイルがあります: "
                + ", ".join(names[:4])
                + "。それぞれの条件を読んでから使ってください",
                "Some files have a license outside the allowlist (commercial use with no use restrictions): "
                + ", ".join(names[:4])
                + ". Read each license before use",
            )
        )
    unknown = [f.name for f in plan.files if f.license is None]
    if unknown and not plan.blocked:
        plan.warnings.append(tr(UNKNOWN_WARNING_JA, UNKNOWN_WARNING_EN) + ", ".join(unknown[:4]))
    if fit.estimate.fit in ("too_tight",):
        plan.warnings.append(
            tr(
                "この PC では動かない見込みです(fit too_tight)",
                "Not expected to run on this PC (fit too_tight)",
            )
        )
    elif fit.estimate.unknown_size:
        plan.warnings.append(
            tr(
                "大きさが分からず、この PC で動くか判定できていません",
                "The size is unknown, so whether it runs on this PC is not judged",
            )
        )
    other = [f.name for f in plan.files if not f.directory and f.status != "unavailable"]
    if other:
        plan.warnings.append(
            tr(
                f"ComfyUI のどのフォルダに置くか分からないファイルは models/{OTHER_DIR} に置きます。"
                f"ComfyUI は models/{OTHER_DIR} を読まないので、使う前に正しいフォルダ"
                "(checkpoints・diffusion_models・loras など。モデルのページの説明に従う)へ移してください: ",
                f"Files whose ComfyUI folder is unknown go to models/{OTHER_DIR}. ComfyUI does not read "
                f"models/{OTHER_DIR}: move them to the right folder (checkpoints, diffusion_models, loras,"
                " ...; see the model page) before use: ",
            )
            + ", ".join(other[:4])
            + (f" (+{len(other) - 4})" if len(other) > 4 else "")
        )
    if plan.layout == "output":
        plan.warnings.append(
            tr(
                LAYOUT_WARNING_JA,
                LAYOUT_WARNING_EN,
            )
        )
    if plan.skipped_optional:
        n = len(plan.skipped_optional)
        names = ", ".join(plan.skipped_optional[:4]) + (f" (+{n - 4})" if n > 4 else "")
        plan.warnings.append(
            tr(
                "テンプレートが既定で使わない部品(無効・素通しのノードなどにしかないもの)"
                f"{n} 個は落としません: {names}。"
                "そのノードを有効にして使うなら with_optional=True(CLI は --with-optional)",
                f"Not downloading {n} part(s) the template does not use by default (only on muted or bypassed"
                f" nodes, or nodes that reach no output): {names}. To enable those nodes, pass"
                " with_optional=True (CLI: --with-optional)",
            )
        )
    gguf = [f.name for f in plan.files if (f.directory_basis or {}).get("needs") == GGUF_NODE]
    if gguf:
        plan.warnings.append(
            tr(
                f"GGUF のファイルを読むには ComfyUI にカスタムノード {GGUF_NODE} が要ります: ",
                f"ComfyUI needs the {GGUF_NODE} custom nodes to load the GGUF files: ",
            )
            + ", ".join(gguf[:4])
        )


# ライセンス不明を含めて落とすときの知らせの頭(CLI は端末で尋ねるとき、自分の「同意」の行に置き換える)
UNKNOWN_WARNING_JA = "ライセンスが分からないファイルを含みます(条件は自分で確かめる): "
UNKNOWN_WARNING_EN = "Includes files with an unknown license (check their terms yourself): "

# --output-dir か既定の場所に置くときの知らせ(CLI は既定の場所のとき、自分の案内に置き換える)
LAYOUT_WARNING_JA = (
    "ComfyUI の models と同じ並びで置きます(ComfyUI のフォルダではありません)。ComfyUI で使うなら"
    " --comfyui-dir を指定するか、extra_model_paths.yaml にこのフォルダを足してください"
)
LAYOUT_WARNING_EN = (
    "Files are laid out like ComfyUI's models folder (this is not the ComfyUI folder). To use them in"
    " ComfyUI, pass --comfyui-dir or add this folder to extra_model_paths.yaml"
)


def _lic(f: PlannedFile) -> str:
    if f.license is None:
        return tr("不明", "unknown")
    return f"other: {f.license_name}" if f.license == "other" and f.license_name else f.license


license_label = _lic


# --- 落とす ---------------------------------------------------------------------------------------


@dataclass
class DownloadProgress:
    """progress に渡す進み具合。phase: check(既にあるファイル・.part を計る)/ download / restart
    (相手が Range に応じず最初から)/ retry(つながらず待つ)/ verify / skip(既にある)/
    done(1 ファイル終わり)。"""

    phase: str
    file_index: int  # 1 から
    file_count: int
    name: str
    file_bytes: int  # このファイルの済んだ量
    file_total: int | None
    done_bytes: int  # 全体の済んだ量(今回落とした分 + .part + 既にある分)
    total_bytes: int
    message: str | None = None


@dataclass
class DownloadResult:
    plan: DownloadPlan
    downloaded: list[str] = field(default_factory=list)  # 落とした(相対パス)
    resumed: list[str] = field(default_factory=list)  # .part から続けた
    restarted: list[str] = field(default_factory=list)  # Range に応じず最初から落とし直した
    skipped: list[str] = field(default_factory=list)  # 既にあって sha256 が合った
    bytes_downloaded: int = 0  # 今回の通信で受け取った量
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.plan.model_id,
            "root": self.plan.root,
            "downloaded": list(self.downloaded),
            "resumed": list(self.resumed),
            "restarted": list(self.restarted),
            "skipped": list(self.skipped),
            "bytes_downloaded": self.bytes_downloaded,
            "seconds": round(self.seconds, 1),
            "files": [{"relpath": f.relpath, "dest": f.dest, "sha256": f.sha256} for f in self.plan.files],
        }


@dataclass
class DownloadRecord:
    """この実行で新しく置いたもの(#145。share・measure が測るために落としたものを、あとで消せるように)。

    download(record=...) に渡すと、落とし終えたファイルと、この実行で作った .part・.lock を足していく
    (中断・失敗で例外が上がっても、それまでの分は残る)。もとからあったファイル(既にあって飛ばしたもの・
    前の実行の .part)は入れない。同じパスは 1 回だけ。消すのは置き場所(root)の中の、今もあるファイルだけ。
    何も表示しない。"""

    entries: list[tuple[str, str]] = field(default_factory=list)  # (絶対パス, 置き場所)

    def add(self, path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None:
        item = (str(path), str(root))
        if all(os.path.normcase(p) != os.path.normcase(item[0]) for p, _ in self.entries):
            self.entries.append(item)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def existing(self) -> list[tuple[Path, int]]:
        """今もある、置き場所の中のふつうのファイル(リンクでない)と大きさ。記録した順。"""
        out: list[tuple[Path, int]] = []
        for p, root in self.entries:
            path = Path(p)
            try:
                if path.is_symlink() or not path.is_file() or not _inside(path, Path(root)):
                    continue
                out.append((path, path.stat().st_size))
            except OSError:
                continue
        return out

    def delete(self) -> tuple[list[Path], list[tuple[Path, str]]]:
        """existing() のファイルを消す。(消したもの, [(消せなかったもの, 理由)])。"""
        removed: list[Path] = []
        failed: list[tuple[Path, str]] = []
        for path, _size in self.existing():
            try:
                path.unlink()
                removed.append(path)
            except FileNotFoundError:
                continue
            except OSError as e:
                failed.append((path, str(e.strerror or e)))
        if removed:
            log.info("deleted %d downloaded file(s)", len(removed))
        return removed, failed


def _retry_after(resp: requests.Response, n: int) -> float:
    try:
        return min(MAX_RETRY_AFTER, max(0.0, float(resp.headers.get("Retry-After", ""))))
    except ValueError:
        return float(min(60, 2**n))


def _check_hop(hosts: HostPolicy, url: str, *, start: bool) -> None:
    """URL そのものと、requests が実際に使う形(prepare した URL)の両方を確かめる。"""
    import requests

    hosts.check(url, start=start)
    try:
        prepared = requests.Request("GET", url).prepare().url or ""
    except requests.RequestException as e:
        raise HostNotAllowed(
            tr(f"URL を読めません: {url[:200]!r}", f"Cannot parse the URL: {url[:200]!r}")
        ) from e
    hosts.check(prepared, start=start)
    if url_host(prepared) != url_host(url):
        raise HostNotAllowed(
            tr(
                f"URL の読み方が分かれます: {url[:200]!r}",
                f"The URL can be read in more than one way: {url[:200]!r}",
            )
        )


def _open(
    session: requests.Session, url: str, headers: dict[str, str], hosts: HostPolicy, timeout
) -> requests.Response:
    """GET(転送は自分で追い、相対の転送先を解決したあとの URL を 1 回ごとに確かめてからつなぐ)。"""
    _check_hop(hosts, url, start=True)
    for _ in range(MAX_REDIRECTS + 1):
        resp = session.get(url, headers=headers, stream=True, allow_redirects=False, timeout=timeout)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            resp.close()
            if not loc:
                raise DownloadError(tr(f"転送先がありません: {url}", f"Redirect without a target: {url}"))
            if _URL_BAD.search(loc):
                raise HostNotAllowed(
                    tr(
                        f"転送先の URL に使えない文字があります: {loc[:200]!r}",
                        f"The redirect URL has characters that are not allowed: {loc[:200]!r}",
                    )
                )
            url = urljoin(url, loc)
            _check_hop(hosts, url, start=False)
            log.debug("download: redirect -> %s", url_host(url))
            continue
        return resp
    raise DownloadError(tr(f"転送が多すぎます: {url}", f"Too many redirects: {url}"))


def _content_range_start(resp: requests.Response) -> int | None:
    m = re.match(r"bytes (\d+)-\d+/(\d+|\*)", resp.headers.get("content-range", ""))
    return int(m.group(1)) if m else None


def _stale_lock(path: Path) -> bool:
    """落としている途中の印(.lock)が、もう動いていないプロセスのものか。"""
    import psutil

    try:
        pid = int(path.read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        try:  # 読めない・書きかけ: 1 時間より古ければ残骸とみなす
            return time.time() - path.stat().st_mtime > 3600
        except OSError:
            return True
    return not psutil.pid_exists(pid)


def _retire_stale_lock(path: Path) -> bool:
    """残骸の .lock を片付ける。消す代わりに、名前の重ならない墓標へ改名してから中身を見直す
    (残骸と判断してから改名するまでに別のプロセスが新しい .lock を作っていたら、それを消さずに戻す)。"""
    import uuid

    tomb = path.with_name(f"{path.name}.stale-{uuid.uuid4().hex}")
    try:
        os.rename(path, tomb)  # 原子的。同じ .lock を 2 つのプロセスが同時に改名しても、通るのは 1 つだけ
    except FileNotFoundError:
        return True  # ほかのプロセスが先に片付けた: 作り直しを試す
    except OSError:
        return False
    if _stale_lock(tomb):
        with contextlib.suppress(OSError):
            tomb.unlink()
        return True
    # 改名したのは新しい(生きている)印だった: 元へ戻す(戻せなければ残す。持ち主は終わると消そうとする)
    with contextlib.suppress(OSError):
        os.rename(tomb, path)
    return False


@contextlib.contextmanager
def _file_lock(path: Path, name: str):
    """同じファイルを 2 つの download が同時に書かないように、.lock を排他で作る(残骸は片付けてやり直す)。"""
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if attempt == 0 and _stale_lock(path) and _retire_stale_lock(path):
                continue
            raise DownloadError(
                tr(
                    f"{name}: 別の download が同じファイルを落としています(終わってからもう一度。"
                    f"止まったまま残っているなら {path} を消してください)",
                    f"{name}: another download is fetching the same file (try again when it ends; "
                    f"if it is stuck, delete {path})",
                )
            ) from None
        except OSError as e:
            raise DownloadError(
                tr(
                    f"{name}: 書けません({e.strerror or e}): {path}",
                    f"{name}: cannot write ({e.strerror or e}): {path}",
                )
            ) from e
    else:  # pragma: no cover - 2 回とも残骸を消した直後に別のプロセスが作った
        raise DownloadError(
            tr(
                f"{name}: 別の download が同じファイルを落としています",
                f"{name}: another download is fetching the same file",
            )
        )
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(f"{os.getpid()} {int(time.time())}\n")
        yield
    finally:
        _remove_lock(path)


def _remove_lock(path: Path, tries: int = 50) -> None:
    """自分の .lock を消す。Windows では、鍵を取れなかった別の download が残骸かを確かめるために
    読んでいる間は消せない(PermissionError)ので、少し待ってやり直す(#85)。"""
    for _ in range(tries):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.02)
        except OSError:
            return


def _fetch_one(
    pf: PlannedFile,
    *,
    session: requests.Session,
    hosts: HostPolicy,
    root: Path,
    sleep: Callable[[float], None],
    timeout,
    report: Callable[[str, int, str | None], None],
    result: DownloadResult,
    record: DownloadRecord | None = None,
) -> None:
    dest = Path(pf.dest)
    part = dest.with_name(dest.name + ".part")
    lock = dest.with_name(dest.name + ".lock")
    # 置き場所の中かを、フォルダを作る前に確かめる(作ったあとにもう一度。間でリンクに替えられたとき)
    for p in (dest, part, lock):
        if not _inside(p, root) or p.is_symlink():
            raise DownloadRefused(
                tr(f"置き場所の外には書きません: {p}", f"Will not write outside the destination: {p}")
            )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DownloadError(
            tr(
                f"{pf.relpath}: フォルダを作れません({e.strerror or e}): {dest.parent}",
                f"{pf.relpath}: cannot create the folder ({e.strerror or e}): {dest.parent}",
            )
        ) from e
    if not _inside(dest.parent, root):
        raise DownloadRefused(
            tr(
                f"置き場所の外には書きません: {dest.parent}",
                f"Will not write outside the destination: {dest.parent}",
            )
        )
    import requests

    with _file_lock(lock, pf.relpath):
        if record is not None:  # この実行で作った印(ふつうは終わると消える。残ったときに消せるように)
            record.add(lock, root)
        if os.path.lexists(dest):  # 鍵を待つ間に、別の download が同じファイルを置き終えた
            _existing_ok(dest, pf, report)
            result.skipped.append(pf.relpath)
            return
        if record is not None and not os.path.lexists(part):  # 前の実行の .part は入れない
            record.add(part, root)
        try:
            _transfer(
                pf,
                dest,
                part,
                session=session,
                hosts=hosts,
                sleep=sleep,
                timeout=timeout,
                report=report,
                result=result,
            )
            if record is not None:
                record.add(dest, root)
        except DownloadError:
            raise
        except requests.RequestException as e:
            raise DownloadError(
                tr(
                    f"{pf.relpath}: 通信の失敗({type(e).__name__})。もう一度実行すると続きから落とします",
                    f"{pf.relpath}: network error ({type(e).__name__}). Run again to resume",
                )
            ) from e
        except OSError as e:  # 空き不足・権限・別のプロセスとの取り合いなど
            raise DownloadError(
                tr(
                    f"{pf.relpath}: 書けません({e.strerror or e})",
                    f"{pf.relpath}: cannot write ({e.strerror or e})",
                )
            ) from e


def _transfer(
    pf: PlannedFile,
    dest: Path,
    part: Path,
    *,
    session: requests.Session,
    hosts: HostPolicy,
    sleep: Callable[[float], None],
    timeout,
    report: Callable[[str, int, str | None], None],
    result: DownloadResult,
) -> None:
    import requests

    size = pf.size_bytes or 0
    hasher, hashed = None, -1  # .part のどこまでを hasher に入れたか
    stalled = restarts = 0
    received = 0  # このファイルで受け取った量の合計(上限 MAX_RECEIVE_FACTOR × 大きさ)
    resumed = restarted = False
    while True:
        have = part.stat().st_size if part.is_file() else 0
        if have > size:
            part.unlink()
            have = 0
        if hashed != have:  # .part の中身を計り直す(初回・やり直しのあと)
            report("check", have, None)
            hasher = hashlib.sha256()
            if have:
                with part.open("rb") as fh:
                    for b in iter(lambda: fh.read(CHUNK), b""):
                        hasher.update(b)
            hashed = have
        assert hasher is not None
        if have == size:
            break
        headers = {"Accept-Encoding": "identity"}
        if have:
            headers["Range"] = f"bytes={have}-"
        got = 0
        try:
            resp = _open(session, pf.url or "", headers, hosts, timeout)
            try:
                code = resp.status_code
                if code == 416 and have:  # 範囲の外: .part を捨てて最初から
                    part.unlink()
                    hashed = -1
                    stalled += 1
                    if stalled >= MAX_STALLED:
                        raise DownloadError(
                            tr(
                                f"{pf.name}: 範囲の要求が通りません(HTTP 416)",
                                f"{pf.name}: the range request is refused (HTTP 416)",
                            )
                        )
                    continue
                if code in (429, 500, 502, 503, 504):
                    stalled += 1
                    if stalled >= MAX_STALLED:
                        raise DownloadError(
                            tr(f"{pf.name}: HTTP {code} が続きました", f"{pf.name}: HTTP {code} repeatedly")
                        )
                    wait = _retry_after(resp, stalled)
                    report(
                        "retry",
                        have,
                        tr(
                            f"HTTP {code}。{wait:.0f} 秒待ってやり直します",
                            f"HTTP {code}. Retrying in {wait:.0f} s",
                        ),
                    )
                    sleep(wait)
                    continue
                if code in (401, 403):
                    raise DownloadRefused(
                        tr(
                            f"{pf.name}: HTTP {code}(ログインか利用申請が要るファイルです。"
                            "genmodelfit はログイン情報を扱わないので落としません)",
                            f"{pf.name}: HTTP {code} (the file needs a login or an access request; "
                            "genmodelfit does not handle login credentials, so it will not download it)",
                        )
                    )
                if code >= 400:
                    raise DownloadError(f"{pf.name}: HTTP {code}")
                if code == 206 and have and _content_range_start(resp) == have:
                    mode = "ab"
                    resumed = True
                elif code == 206:  # 頼んだ位置と違う続き: .part を捨てて最初から
                    part.unlink(missing_ok=True)
                    hashed = -1
                    stalled += 1
                    if stalled >= MAX_STALLED:
                        raise DownloadError(
                            tr(
                                f"{pf.name}: 続きの位置が合いません(HTTP 206)",
                                f"{pf.name}: the resumed position does not match (HTTP 206)",
                            )
                        )
                    continue
                elif code == 200:
                    if have:  # 相手が Range に応じない: 最初から(回数に上限)
                        restarts += 1
                        if restarts > MAX_RESTARTS:
                            raise DownloadError(
                                tr(
                                    f"{pf.name}: 相手が続きからの取得(Range)に応じず、最後まで受け取れません"
                                    f"(最初からを {MAX_RESTARTS} 回やり直しました)",
                                    f"{pf.name}: the server does not support resuming (Range) and the file "
                                    f"could not be received in full (restarted {MAX_RESTARTS} times)",
                                )
                            )
                        restarted = True
                        report(
                            "restart",
                            0,
                            tr(
                                "相手が続きからの取得(Range)に応じないので最初から落とします",
                                "The server does not support resuming (Range); downloading from the start",
                            ),
                        )
                    have, hasher, hashed, mode = 0, hashlib.sha256(), 0, "wb"
                else:
                    raise DownloadError(
                        tr(
                            f"{pf.name}: 思わない応答です(HTTP {code})",
                            f"{pf.name}: unexpected response (HTTP {code})",
                        )
                    )
                with part.open(mode) as fh:
                    for chunk in resp.iter_content(chunk_size=CHUNK):
                        if not chunk:
                            continue
                        if have + got + len(chunk) > size:
                            raise DownloadError(
                                tr(
                                    f"{pf.name}: 一覧の大きさ({size} バイト)より大きいので止めました",
                                    f"{pf.name}: stopped because it is larger than the listed size "
                                    f"({size} bytes)",
                                )
                            )
                        received += len(chunk)
                        if received > MAX_RECEIVE_FACTOR * size + CHUNK:
                            raise DownloadError(
                                tr(
                                    f"{pf.name}: 大きさの {MAX_RECEIVE_FACTOR} 倍を受け取っても終わらないので"
                                    "止めました",
                                    f"{pf.name}: stopped because it did not finish after receiving "
                                    f"{MAX_RECEIVE_FACTOR} times its size",
                                )
                            )
                        fh.write(chunk)
                        hasher.update(chunk)
                        got += len(chunk)
                        hashed = have + got
                        result.bytes_downloaded += len(chunk)
                        report("download", have + got, None)
            finally:
                resp.close()
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            stalled = 0 if got else stalled + 1
            if stalled >= MAX_STALLED:
                raise DownloadError(
                    tr(
                        f"{pf.name}: つながりません({type(e).__name__})。"
                        "もう一度実行すると続きから落とします",
                        f"{pf.name}: cannot connect ({type(e).__name__}). Run again to resume",
                    )
                ) from e
            wait = float(min(60, 2**stalled))
            report(
                "retry",
                have + got,
                tr(
                    f"つながりません({type(e).__name__})。{wait:.0f} 秒待って続きから",
                    f"Cannot connect ({type(e).__name__}). Resuming in {wait:.0f} s",
                ),
            )
            sleep(wait)
            continue
        if got:
            stalled = 0
        elif have + got < size:
            stalled += 1
            if stalled >= MAX_STALLED:
                raise DownloadError(
                    tr(
                        f"{pf.name}: 途中で切れ、続きを受け取れません",
                        f"{pf.name}: the transfer was cut and cannot be resumed",
                    )
                )
    report("verify", size, None)
    if hasher.hexdigest() != pf.sha256:
        part.unlink(missing_ok=True)
        raise ChecksumMismatch(
            tr(
                f"{pf.name}: sha256 が一覧と合いません(消しました)。一覧 {pf.sha256}、"
                f"落としたもの {hasher.hexdigest()}",
                f"{pf.name}: sha256 does not match the list (deleted). List {pf.sha256}, "
                f"downloaded {hasher.hexdigest()}",
            )
        )
    if dest.exists() or dest.is_symlink():  # 落としている間に別のものが置かれた: 上書きしない
        part.unlink(missing_ok=True)  # 置かなかった写しを残さない
        raise DownloadRefused(
            tr(
                f"{pf.relpath}: 落としている間に同じ名前のファイルが置かれました(上書きしません)",
                f"{pf.relpath}: a file with the same name appeared during the download (not overwritten)",
            )
        )
    os.replace(part, dest)
    result.downloaded.append(pf.relpath)
    if resumed:
        result.resumed.append(pf.relpath)
    if restarted:
        result.restarted.append(pf.relpath)


def _existing_ok(dest: Path, f: PlannedFile, emit: Callable[[str, int, str | None], None]) -> bool:
    """置き場所に既にあるファイルを、計画によらずその場で sha256 まで計る。
    合えば True、違えば DownloadRefused。"""
    if dest.is_symlink() or not dest.is_file():
        raise DownloadRefused(
            tr(
                f"{f.relpath}: 同じ名前のファイルでないものがあります(上書きしません)",
                f"{f.relpath}: something that is not a file has the same name (not overwritten)",
            )
        )
    if dest.stat().st_size != f.size_bytes:
        raise DownloadRefused(
            tr(
                f"{f.relpath}: 同じ名前の別のファイルがあります(大きさが違う。上書きしません)",
                f"{f.relpath}: a different file with the same name exists (size differs; not overwritten)",
            )
        )
    done = [0]

    def tick(n: int) -> None:
        done[0] += n
        emit("check", done[0], None)

    if _hash_file(dest, tick) != f.sha256:
        raise DownloadRefused(
            tr(
                f"{f.relpath}: 同じ名前のファイルの sha256 が一覧と違います(上書きしません)",
                f"{f.relpath}: a file with the same name has a sha256 that differs from the list "
                "(not overwritten)",
            )
        )
    return True


def download(
    plan_or_model: DownloadPlan | str,
    *,
    progress: Callable[[DownloadProgress], None] | None = None,
    confirm: Callable[[DownloadPlan], bool] | None = None,
    yes: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    timeout=TIMEOUT,
    record: DownloadRecord | None = None,
    **plan_kwargs: Any,
) -> DownloadResult:
    """計画どおりに落とす(何も表示しない)。plan_or_model が文字列なら
    plan_download(plan_or_model, **plan_kwargs)。

    同意: confirm(plan) が True を返したときだけ始める(ライセンス・大きさ・置き場所を見せて尋ねる関数)。
    confirm を渡さないなら yes=True で同意済みを示す(どちらも無ければ DownloadRefused)。
    plan.blocked があれば DownloadRefused、空きが足りなければ DiskFull、sha256 が合わなければ ChecksumMismatch
    (そのファイルは消す)、許していない転送先なら HostNotAllowed、書けない(空き・権限)・同じファイルを別の
    download が落としているなら DownloadError。既に置いてあるファイルは、計画の状態によらずその場で sha256 を
    計り、合うときだけ飛ばす(違えば上書きせず DownloadRefused)。中断(KeyboardInterrupt)は .part を残して
    そのまま上げる(もう一度呼ぶと続きから)。
    record(DownloadRecord)を渡すと、この呼び出しで新しく置いたファイルと、作った .part・.lock を足す
    (#145。例外で止まっても、それまでの分が残る。もとからあったファイルは入れない)。
    """
    if isinstance(plan_or_model, DownloadPlan):
        if plan_kwargs:
            raise TypeError(
                tr(
                    "計画を渡すときは plan_download の引数を渡せません: ",
                    "plan_download arguments cannot be given together with a plan: ",
                )
                + ", ".join(plan_kwargs)
            )
        plan = plan_or_model
    else:
        plan = plan_download(plan_or_model, **plan_kwargs)
    if plan.blocked:
        raise DownloadRefused(tr("落とせません: ", "Cannot download: ") + " / ".join(plan.blocked), plan)
    if confirm is not None:
        if not confirm(plan):
            raise DownloadDeclined(
                tr("同意が得られなかったので落としません", "Not downloading: not agreed"), plan
            )
    elif not yes:
        raise DownloadRefused(
            tr("同意がありません(confirm を渡すか yes=True)", "No consent (pass confirm or yes=True)"), plan
        )
    root = Path(plan.root)
    hosts = HOST_POLICY
    # 空き(計画のあとで変わっていることがあるので、始める前に今のファイルの状態で見直す)
    todo = [f for f in plan.files if f.dest and not os.path.lexists(f.dest)]
    need = sum(max(0, (f.size_bytes or 0) - _part_size(f)) for f in todo)
    free = _free_bytes(root)
    if todo and free is not None and need + MARGIN_BYTES > free:
        raise DiskFull(
            tr(
                f"空きが足りません: 要る {(need + MARGIN_BYTES) / GIB:.2f} GB(落とす残り + 1 GB)、"
                f"空き {free / GIB:.2f} GB",
                f"Not enough free disk: needs {(need + MARGIN_BYTES) / GIB:.2f} GB "
                "(the rest to download + 1 GB), "
                f"free {free / GIB:.2f} GB",
            ),
            plan,
        )
    result = DownloadResult(plan)
    start = time.monotonic()
    total = plan.total_bytes
    base = [0]  # 済んだファイルの合計

    def emit(i: int, f: PlannedFile, phase: str, n: int, msg: str | None) -> None:
        if progress:
            progress(
                DownloadProgress(
                    phase, i, len(plan.files), f.relpath, n, f.size_bytes, base[0] + n, total, msg
                )
            )

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DownloadError(
            tr(
                f"置き場所を作れません({e.strerror or e}): {root}",
                f"Cannot create the destination ({e.strerror or e}): {root}",
            ),
            plan,
        ) from e
    session = make_session()
    try:
        for i, f in enumerate(plan.files, 1):
            dest = Path(f.dest)
            report = lambda phase, n, msg, i=i, f=f: emit(i, f, phase, n, msg)  # noqa: E731
            if os.path.lexists(dest):
                _existing_ok(dest, f, report)  # 計画の present を信じず、その場で計る
                result.skipped.append(f.relpath)
                emit(
                    i,
                    f,
                    "skip",
                    f.size_bytes or 0,
                    tr(
                        "既にあり sha256 が合うので飛ばします",
                        "Already present with a matching sha256; skipped",
                    ),
                )
            else:
                _fetch_one(
                    f,
                    session=session,
                    hosts=hosts,
                    root=root,
                    sleep=sleep,
                    timeout=timeout,
                    report=report,
                    result=result,
                    record=record,
                )
            base[0] += f.size_bytes or 0
            emit(i, f, "done", 0, None)
    except DownloadError as e:
        if e.plan is None:
            e.plan = plan
        raise
    except OSError as e:  # 既にあるファイルを読めないなど
        raise DownloadError(
            tr(f"読み書きできません({e.strerror or e})", f"Cannot read or write ({e.strerror or e})"), plan
        ) from e
    finally:
        session.close()
        result.seconds = time.monotonic() - start
    log.info(
        "download %s: %d files, %d bytes", plan.model_id, len(result.downloaded), result.bytes_downloaded
    )
    return result


def _part_size(f: PlannedFile) -> int:
    p = Path(f.dest + ".part") if f.dest else None
    try:
        return p.stat().st_size if p and p.is_file() else 0
    except OSError:
        return 0
