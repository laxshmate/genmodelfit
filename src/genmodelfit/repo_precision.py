# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""本体の見出しも名前も精度を示さないとき、置き場所の Hugging Face のリポジトリから精度を読む(#30)。

1. Hub API(`/api/models/<repo>`)の `safetensors.parameters`(dtype ごとの要素の数。リポジトリの .safetensors の
   合計)。一つの dtype が 9 割以上なら、その dtype を本体の精度とみなす(本体が .safetensors のときだけ)。
   ただし要素の数 × 1 要素のバイト数の合計が、この項目の本体の safetensors(同じリポジトリの分割も足す)の
   大きさと 5% 以内で合うときだけ(リポジトリに別の重みも並ぶと、集計が本体を表さない)。
   gated のリポジトリでも匿名で読める(ファイルの見出しは 401 で読めない)
2. 本体と同じフォルダの `config.json` の `torch_dtype` / `dtype`(API のファイルの一覧にあって、gated でない
   ときだけ取る。重みの見出しと違い、小さい JSON を 1 回取るだけ)

匿名で送る(fetch.HttpFetcher: User-Agent 付き・~/.netrc を送らない・429 は Retry-After を守る)。1 つずつ順に、
間を少し空けて送る。結果は <cache>/repo_precision.json に残し、offline=True ならそれだけを使う。
読めなかったもの(401・404・一時的な失敗)は、通信してよいときに読み直す。
API が 401 のリポジトリ(非公開・消えた)は集計の
`unauthorized` に出す(毎週の更新で公開されたか分かるように)。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import ModelEntry, ModelFile

HF = "https://huggingface.co"
CACHE_FILE = "repo_precision.json"
DELAY_S = 0.3  # 要求の間に空ける秒数
DOMINANT_SHARE = 0.9  # API の dtype の合計のうち、この割合以上を占める dtype だけを使う
# API の要素の数 × 1 要素のバイト数と、本体の safetensors の大きさの合計の差がこの割合以内のときだけ使う
# (リポジトリに別の重みも並ぶと、集計が本体の精度を表さないので)
SIZE_TOLERANCE = 0.05
ST_EXTS = (".safetensors", ".sft")
# config.json の torch_dtype / dtype → 精度
CONFIG_DTYPES = {
    "float32": "fp32",
    "fp32": "fp32",
    "float16": "fp16",
    "fp16": "fp16",
    "half": "fp16",
    "bfloat16": "bf16",
    "bf16": "bf16",
}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def api_url(repo_id: str, revision: str = "main") -> str:
    """ファイルの大きさ(blobs)を付けない、軽い API の URL。"""
    if revision in ("main", ""):
        return f"{HF}/api/models/{repo_id}"
    return f"{HF}/api/models/{repo_id}/revision/{quote(revision, safe='')}"


def config_url(repo_id: str, revision: str, path: str) -> str:
    return f"{HF}/{repo_id}/resolve/{quote(revision or 'main', safe='')}/{quote(path)}"


def api_bytes(parameters: Any) -> int | None:
    """API の safetensors.parameters の、要素の数 × 1 要素のバイト数の合計(およそのファイルの大きさ)。"""
    from .headers import ST_DTYPES

    if not isinstance(parameters, dict):
        return None
    total = 0.0
    for k, v in parameters.items():
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            total += v * ST_DTYPES.get(str(k).upper(), (1, None))[0]
    return int(total) or None


def size_matches(parameters: Any, file_bytes: int | None) -> bool:
    """API の集計の大きさが、本体の safetensors の大きさ(file_bytes)と SIZE_TOLERANCE 以内で合うか。
    大きさが分からなければ合わないとみなす(決めない)。"""
    want = api_bytes(parameters)
    if not want or not file_bytes:
        return False
    return abs(want - file_bytes) <= file_bytes * SIZE_TOLERANCE


def dominant_dtype(parameters: Any) -> str | None:
    """API の safetensors.parameters({"BF16": 9078581248, …})→ 精度。一つの dtype が 9 割以上のときだけ。"""
    from .headers import ST_DTYPES

    if not isinstance(parameters, dict):
        return None
    counts = {
        str(k).upper(): int(v)
        for k, v in parameters.items()
        if isinstance(v, int) and not isinstance(v, bool) and v > 0
    }
    total = sum(counts.values())
    if not total:
        return None
    top = max(counts, key=lambda k: (counts[k], k))
    if counts[top] < total * DOMINANT_SHARE:
        return None
    return ST_DTYPES.get(top, (1, None))[1]


def config_dtype(data: Any) -> str | None:
    """config.json の一番上の torch_dtype(古い transformers)/ dtype(新しい transformers)→ 精度。"""
    if not isinstance(data, dict):
        return None
    for key in ("torch_dtype", "dtype"):
        v = data.get(key)
        if isinstance(v, str):
            p = CONFIG_DTYPES.get(v.strip().lower().removeprefix("torch."))
            if p:
                return p
    return None


def _status(e: Exception) -> int | None:
    s = getattr(e, "status", None)
    return s if isinstance(s, int) else None


class RepoPrecisionCache:
    """API の結果(リポジトリ@版 → 状態・dtype・config.json の場所)と、
    config.json の結果(URL → 状態・dtype)。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.api: dict[str, dict[str, Any]] = {}
        self.config: dict[str, dict[str, Any]] = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.api = raw.get("api") if isinstance(raw.get("api"), dict) else {}
                    self.config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = {
            "note": "genmodelfit が読んだリポジトリの精度(Hub API の safetensors・config.json)",
            "api": self.api,
            "config": self.config,
        }
        tmp.write_text(
            json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8", newline="\n"
        )
        tmp.replace(self.path)


@dataclass
class RepoPrecisionStats:
    files: int = 0  # 調べた本体のファイル(見出しも名前も精度を示さないもの)
    repos: int = 0
    from_cache: int = 0
    fetched: int = 0  # 送った要求の数
    hf_api: int = 0  # API の dtype で決まったファイル
    config: int = 0  # config.json で決まったファイル
    unknown: int = 0  # どちらでも決まらないファイル
    skipped_offline: int = 0
    unauthorized: list[str] = field(default_factory=list)  # API が 401 のリポジトリ
    # API の dtype は一つに決まるが、集計の大きさが本体の大きさと合わず使わなかったリポジトリ
    size_mismatch: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["unauthorized"] = sorted(set(self.unauthorized))
        d["errors"] = self.errors[:20]
        return d


def target_files(entries: Iterable[ModelEntry]) -> list[ModelFile]:
    """リポジトリから精度を読むファイル: 本体(一番大きいもの)が Hugging Face にあり、見出しも名前も精度を
    示さないもの。"""
    return [f for _e, f in _targets(entries)]


def repo_main_bytes(e: ModelEntry, f: ModelFile) -> int | None:
    """f と同じリポジトリにある、この項目の本体の safetensors の大きさの合計(分割されたものも足す)。"""
    from .estimate import _role_of

    sizes = [
        g.size_bytes
        for g in e.files
        if g.repo_id == f.repo_id
        and (g is f or _role_of(g) == "main")
        and g.name.lower().endswith(ST_EXTS)
        and g.size_bytes
    ]
    return sum(sizes) or None


def _targets(entries: Iterable[ModelEntry]) -> list[tuple[ModelEntry, ModelFile]]:
    from .groups import main_files
    from .hf import guess_precision, parse_hf_url

    out = []
    for e in entries:
        mains = sorted(main_files(e), key=lambda f: (-(f.size_bytes or 0), f.name))
        if not mains:
            continue
        f = mains[0]
        if not parse_hf_url(f.url) or f.header_precision or guess_precision(f.name):
            continue
        out.append((e, f))
    return out


Getter = Callable[[str], Any]  # URL → JSON(失敗は FetchError。status に応答コード)


def inspect_repos(
    entries: list[ModelEntry],
    get_json: Getter | None,
    cache: RepoPrecisionCache,
    *,
    offline: bool = False,
    delay: float = DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> RepoPrecisionStats:
    """target_files のファイルに repo_precision / repo_precision_source を入れる(決まらなければ None)。
    offline=True か get_json が無いときはキャッシュだけを使う。"""
    from .hf import parse_hf_url

    targets = _targets(entries)
    stats = RepoPrecisionStats(files=len(targets))
    offline = offline or get_json is None
    sent = 0
    done: set[str] = set()  # この回で既に問い合わせた API・config.json(失敗しても同じ回では送り直さない)

    def fetch(url: str) -> Any:
        nonlocal sent
        if sent:
            sleep(delay)
        sent += 1
        stats.fetched += 1
        return get_json(url)  # type: ignore[misc]

    def api_row(repo: str, rev: str) -> dict[str, Any] | None:
        key = f"{repo}@{rev or 'main'}"
        row = cache.api.get(key)
        if (row is not None and row.get("status") == 200) or offline or key in done:
            return row
        done.add(key)
        try:
            data = fetch(api_url(repo, rev))
            if not isinstance(data, dict):
                raise ValueError("API の応答が JSON の辞書ではありません")
            st = data.get("safetensors") if isinstance(data.get("safetensors"), dict) else {}
            configs = sorted(
                s["rfilename"]
                for s in data.get("siblings") or []
                if isinstance(s, dict)
                and isinstance(s.get("rfilename"), str)
                and s["rfilename"].rsplit("/", 1)[-1] == "config.json"
            )
            row = {
                "status": 200,
                "gated": bool(data.get("gated")) if data.get("gated") is not None else None,
                "parameters": st.get("parameters") if isinstance(st.get("parameters"), dict) else None,
                "configs": configs,
                "checked_at": _now(),
            }
        except Exception as e:  # 1 つ読めなくても残りは続ける(理由は記録する)
            row = {"status": _status(e), "error": f"{type(e).__name__}: {e}"[:300], "checked_at": _now()}
            stats.errors.append(f"{repo}: {row['error']}"[:300])
        cache.api[key] = row
        return row

    def config_row(url: str, allowed: bool) -> dict[str, Any] | None:
        row = cache.config.get(url)
        if (row is not None and row.get("status") == 200) or offline or not allowed or url in done:
            return row
        done.add(url)
        try:
            row = {"status": 200, "precision": config_dtype(fetch(url)), "checked_at": _now()}
        except Exception as e:
            row = {"status": _status(e), "error": f"{type(e).__name__}: {e}"[:300], "checked_at": _now()}
            stats.errors.append(f"{url}: {row['error']}"[:300])
        cache.config[url] = row
        return row

    repos: set[str] = set()
    for e, f in targets:
        repo, rev, path = parse_hf_url(f.url)  # type: ignore[misc]
        repos.add(repo)
        before = stats.fetched
        prec: str | None = None
        source: str | None = None
        api = api_row(repo, rev)
        if api is None:
            stats.skipped_offline += 1
        elif api.get("status") == 401 and repo not in stats.unauthorized:
            stats.unauthorized.append(repo)
        if api and api.get("status") == 200:
            params = api.get("parameters")
            if path.lower().endswith(ST_EXTS):
                if size_matches(params, repo_main_bytes(e, f)):
                    prec = dominant_dtype(params)
                    source = "hf_api" if prec else None
                elif dominant_dtype(params):
                    stats.size_mismatch.append(repo)
            if not prec:
                folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
                cfg = f"{folder}config.json"
                if cfg in (api.get("configs") or []):
                    row = config_row(config_url(repo, rev, cfg), allowed=not api.get("gated"))
                    if row and row.get("status") == 200 and row.get("precision"):
                        prec, source = row["precision"], "config"
        if stats.fetched == before and api is not None:
            stats.from_cache += 1
        f.repo_precision, f.repo_precision_source = prec, source
        if source == "hf_api":
            stats.hf_api += 1
        elif source == "config":
            stats.config += 1
        else:
            stats.unknown += 1
    stats.repos = len(repos)
    return stats


# 匿名の API は、非公開のリポジトリにも、無いリポジトリにも 401 を返す(2026-10-20 に、無い名前
# this-org-zzz/does-not-exist-123 でも 401 を確かめた)。匿名では見分けられないので、無いと確かめたものだけを
# 表に書く(リポジトリ → 確かめた方法)。毎週の更新の PR で「非公開で、公開を待つもの」と分けて見られるように
KNOWN_MISSING = {
    "google/gemma-3-12b": "Google の公開の一覧(api/models?author=google&search=gemma-3-12b、2026-10-20)は "
    "gemma-3-12b-it・gemma-3-12b-pt と QAT 版だけ。名前の誤り(-it / -pt の付け忘れ)",
}


def split_unauthorized(repos: Iterable[str]) -> tuple[list[str], list[str]]:
    """401 のリポジトリを (非公開か無い, 無いと確かめた) に分ける。"""
    rs = sorted(set(repos))
    return [r for r in rs if r not in KNOWN_MISSING], [r for r in rs if r in KNOWN_MISSING]


def recheck_unauthorized(
    repos: Iterable[str],
    get_json: Getter,
    *,
    delay: float = DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """前に API が 401 だったリポジトリ(非公開・消えた)を匿名で確かめ直す。
    返す値: {"unauthorized": まだ読めない(全部), "private_or_missing": そのうち KNOWN_MISSING でないもの,
    "missing": そのうち無いと確かめたもの, "readable": 読めるようになった,
    "failed": ほかの理由で確かめられない, "checked_at"}。
    読めるようになったものは、次の `update`(作り直し)でライセンス・ファイルが入る。"""
    still: list[str] = []
    readable: list[str] = []
    failed: list[str] = []
    for i, repo in enumerate(sorted(set(repos))):
        if i:
            sleep(delay)
        try:
            get_json(api_url(repo))
            readable.append(repo)
        except Exception as e:
            (still if _status(e) == 401 else failed).append(repo)
    private, missing = split_unauthorized(still)
    return {
        "unauthorized": still,
        "private_or_missing": private,
        "missing": missing,
        "readable": readable,
        "failed": failed,
        "checked_at": _now(),
    }
