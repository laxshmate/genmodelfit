# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""重みのファイルの見出しだけを読んで、精度と要素の数を知る(#28)。重み本体は落とさない。

- safetensors: 先頭 8 バイト(見出しの長さ、符号なし 64 bit のリトルエンディアン)→ その長さの JSON
  (テンソルごとの dtype と shape)。HTTP の Range 要求で 2 回だけ読む
- GGUF: 先頭の固定の部分 → メタデータ(general.file_type)→ テンソルの情報(型と形)。足りなければ
  Range で少しずつ読み足す(上限 MAX_GGUF_BYTES)
- 読んだ結果は URL と sha256 ごとにキャッシュ(<cache>/file_headers.json)に残す。キャッシュだけで
  動かせる(offline=True)。HTTP は fetch.HttpFetcher(User-Agent 付き・~/.netrc を送らない・429 は
  Retry-After を守る)。1 つずつ順に、間を少し空けて送る

精度の決め方: 要素の数 × 1 要素のバイト数が一番大きい dtype(重みの大部分の形式)。
F32 → fp32、F16 → fp16、BF16 → bf16、F8_* → fp8、I8 → int8。U8 など 4 bit を詰めた形式は
見出しでは分からない(None。ファイル名の nvfp4 / w4a8 などを使う)。
GGUF は general.file_type(llama.cpp の LLAMA_FTYPE)、無ければテンソルの型の一番多いもの。
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .i18n import tr
from .models import ModelEntry, ModelFile

MAX_SAFETENSORS_HEADER = 100 * 1024 * 1024  # 見出しがこれより大きいものは読まない(壊れている)
GGUF_CHUNK = 1024 * 1024  # GGUF を読み足す単位
MAX_GGUF_BYTES = 64 * 1024 * 1024  # GGUF の見出しを読む上限(トークナイザの大きな表を持つものがある)
DELAY_S = 0.2  # 要求の間に空ける秒数(相手に負担をかけない)
CACHE_FILE = "file_headers.json"
WEIGHT_EXTS = (".safetensors", ".sft", ".gguf")

# safetensors の dtype → (1 要素のバイト数, 精度)。精度が None のものは見出しでは決めない
ST_DTYPES: dict[str, tuple[float, str | None]] = {
    "F64": (8, "fp32"),  # 倍精度は ComfyUI でも全精度として扱う
    "F32": (4, "fp32"),
    "F16": (2, "fp16"),
    "BF16": (2, "bf16"),
    "F8_E4M3": (1, "fp8"),
    "F8_E5M2": (1, "fp8"),
    "F8_E4M3FN": (1, "fp8"),
    "F8_E8M0": (1, None),  # 尺度(スケール)だけに使う
    "I64": (8, None),
    "I32": (4, None),
    "I16": (2, None),
    "I8": (1, "int8"),
    "U8": (1, None),  # 4 bit を詰めたもの(nvfp4 など)にも使う
    "BOOL": (1, None),
    "F4": (0.5, "fp4"),
    "F6_E2M3": (0.75, None),
    "F6_E3M2": (0.75, None),
}

# GGUF の general.file_type(llama.cpp の llama_ftype)→ 名前
GGUF_FTYPES = {
    0: "f32",
    1: "f16",
    2: "q4_0",
    3: "q4_1",
    7: "q8_0",
    8: "q5_0",
    9: "q5_1",
    10: "q2_k",
    11: "q3_k_s",
    12: "q3_k_m",
    13: "q3_k_l",
    14: "q4_k_s",
    15: "q4_k_m",
    16: "q5_k_s",
    17: "q5_k_m",
    18: "q6_k",
    19: "iq2_xxs",
    20: "iq2_xs",
    21: "q2_k_s",
    22: "iq3_xs",
    23: "iq3_xxs",
    24: "iq1_s",
    25: "iq4_nl",
    26: "iq3_s",
    27: "iq3_m",
    28: "iq2_s",
    29: "iq2_m",
    30: "iq4_xs",
    31: "iq1_m",
    32: "bf16",
    36: "tq1_0",
    37: "tq2_0",
}
# GGUF のテンソルの型(ggml_type)→ (名前, 1 要素のおよそのバイト数)
GGML_TYPES: dict[int, tuple[str, float]] = {
    0: ("f32", 4),
    1: ("f16", 2),
    2: ("q4_0", 0.5625),
    3: ("q4_1", 0.625),
    6: ("q5_0", 0.6875),
    7: ("q5_1", 0.75),
    8: ("q8_0", 1.0625),
    9: ("q8_1", 1.125),
    10: ("q2_k", 0.328),
    11: ("q3_k", 0.43),
    12: ("q4_k", 0.5625),
    13: ("q5_k", 0.6875),
    14: ("q6_k", 0.82),
    15: ("q8_k", 1.14),
    16: ("iq2_xxs", 0.26),
    17: ("iq2_xs", 0.29),
    18: ("iq3_xxs", 0.38),
    19: ("iq1_s", 0.19),
    20: ("iq4_nl", 0.5625),
    21: ("iq3_s", 0.43),
    22: ("iq2_s", 0.31),
    23: ("iq4_xs", 0.53),
    24: ("i8", 1),
    25: ("i16", 2),
    26: ("i32", 4),
    27: ("i64", 8),
    28: ("f64", 8),
    29: ("iq1_m", 0.22),
    30: ("bf16", 2),
    34: ("tq1_0", 0.21),
    35: ("tq2_0", 0.26),
}


class HeaderError(ValueError):
    """見出しを読めない(形が違う・大きすぎる)。"""


@dataclass
class HeaderInfo:
    precision: str | None  # 精度(ModelFile.header_precision に入れる値)
    params: int | None  # 要素の数の合計
    dtypes: dict[str, int] = field(default_factory=dict)  # dtype → バイト数(およそ)

    def to_dict(self) -> dict[str, Any]:
        return {"precision": self.precision, "params": self.params, "dtypes": dict(self.dtypes)}


def _numel(shape: Any) -> int:
    """形(整数の並び)の要素の数。整数でない・負の大きさ・並びでない形は HeaderError。"""
    if shape is None:
        return 1
    if not isinstance(shape, list):
        raise HeaderError(
            tr(f"テンソルの形が並びではありません: {shape!r}", f"tensor shape is not a list: {shape!r}")[:200]
        )
    n = 1
    for d in shape:
        if isinstance(d, bool) or not isinstance(d, int) or d < 0:
            raise HeaderError(tr(f"テンソルの形がおかしい: {shape!r}", f"bad tensor shape: {shape!r}")[:200])
        n *= d
    return n


def _guarded(fn):
    """見出しの形がおかしいときの例外(ValueError・TypeError・OverflowError・struct.error)を HeaderError に
    そろえる(NeedMore と HeaderError はそのまま)。"""
    import functools

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (HeaderError, NeedMore):
            raise
        except (ValueError, TypeError, OverflowError, AttributeError, struct.error) as e:
            raise HeaderError(
                tr(f"見出しの形がおかしい: {type(e).__name__}: {e}", f"bad header: {type(e).__name__}: {e}")[
                    :300
                ]
            ) from e

    return run


@_guarded
def parse_safetensors_header(header: bytes) -> HeaderInfo:
    """safetensors の見出しの JSON(先頭 8 バイトの後ろ)から精度と要素の数を求める。"""
    try:
        data = json.loads(header.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise HeaderError(
            tr(f"safetensors の見出しが JSON ではありません: {e}", f"the safetensors header is not JSON: {e}")
        ) from e
    if not isinstance(data, dict):
        raise HeaderError(tr("safetensors の見出しの形が違います", "unexpected safetensors header shape"))
    by_bytes: dict[str, float] = {}
    params = 0
    for name, t in data.items():
        if name == "__metadata__" or not isinstance(t, dict):
            continue
        if not isinstance(t.get("dtype", ""), str):
            raise HeaderError(
                tr(f"dtype が文字列ではありません: {name}", f"dtype is not a string: {name}")[:200]
            )
        dtype = str(t.get("dtype", "")).upper()
        n = _numel(t.get("shape"))
        params += n
        size = ST_DTYPES.get(dtype, (1, None))[0]
        by_bytes[dtype] = by_bytes.get(dtype, 0.0) + n * size
    if not by_bytes:
        return HeaderInfo(None, None)
    top = max(by_bytes, key=lambda k: (by_bytes[k], k))
    prec = ST_DTYPES.get(top, (1, None))[1]
    return HeaderInfo(prec, params, {k: int(v) for k, v in sorted(by_bytes.items())})


class NeedMore(Exception):
    """GGUF の見出しを読むのに、もっとバイトが要る。"""

    def __init__(self, upto: int):
        super().__init__(upto)
        self.upto = upto


class _Reader:
    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.buf):
            raise NeedMore(end)
        out = self.buf[self.pos : end]
        self.pos = end
        return out

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> str:
        n = self.u64()
        if n > MAX_GGUF_BYTES:
            raise HeaderError(tr("GGUF の文字列が長すぎます", "GGUF string too long"))
        return self.take(n).decode("utf-8", errors="replace")


# GGUF のメタデータの値の型 → 固定の大きさ(バイト)。8 = 文字列、9 = 配列
_GGUF_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def _gguf_value(r: _Reader, vtype: int) -> Any:
    if vtype == 8:
        return r.string()
    if vtype == 9:
        etype = r.u32()
        count = r.u64()
        if etype in _GGUF_FIXED:
            r.take(_GGUF_FIXED[etype] * count)
            return None
        for _ in range(count):
            _gguf_value(r, etype)
        return None
    if vtype not in _GGUF_FIXED:
        raise HeaderError(tr(f"GGUF の値の型 {vtype} を知りません", f"unknown GGUF value type {vtype}"))
    raw = r.take(_GGUF_FIXED[vtype])
    if vtype in (4, 5):
        return struct.unpack("<I" if vtype == 4 else "<i", raw)[0]
    if vtype in (10, 11):
        return struct.unpack("<Q" if vtype == 10 else "<q", raw)[0]
    return None


@_guarded
def parse_gguf_header(buf: bytes) -> HeaderInfo:
    """GGUF の先頭(buf)から精度と要素の数を求める。足りなければ NeedMore(要るバイト数)。"""
    r = _Reader(buf)
    if r.take(4) != b"GGUF":
        raise HeaderError(tr("GGUF ではありません", "not GGUF"))
    version = r.u32()
    if version < 2:
        raise HeaderError(
            tr(f"GGUF の版 {version} には対応していません", f"GGUF version {version} is not supported")
        )
    n_tensors = r.u64()
    n_kv = r.u64()
    ftype: int | None = None
    for _ in range(n_kv):
        key = r.string()
        vtype = r.u32()
        value = _gguf_value(r, vtype)
        if key == "general.file_type" and isinstance(value, int):
            ftype = value
    by_bytes: dict[str, float] = {}
    params = 0
    for _ in range(n_tensors):
        r.string()
        n_dims = r.u32()
        n = 1
        for _d in range(n_dims):
            n *= r.u64()
        ttype = r.u32()
        r.u64()  # offset
        name, size = GGML_TYPES.get(ttype, (f"type{ttype}", 1))
        params += n
        by_bytes[name] = by_bytes.get(name, 0.0) + n * size
    if ftype is not None and ftype in GGUF_FTYPES:
        prec = f"gguf-{GGUF_FTYPES[ftype]}"
    elif by_bytes:
        prec = f"gguf-{max(by_bytes, key=lambda k: (by_bytes[k], k))}"
    else:
        prec = "gguf"
    return HeaderInfo(prec, params or None, {k: int(v) for k, v in sorted(by_bytes.items())})


RangeGetter = Callable[[str, int, int], bytes]  # (url, 始め, 終わり(含む)) → バイト列


def read_header(url: str, get_range: RangeGetter) -> HeaderInfo:
    """URL の重みのファイルの見出しを Range 要求で読む。重み本体は読まない。"""
    name = url.split("?", 1)[0].lower()
    if name.endswith(".gguf"):
        upto = GGUF_CHUNK
        buf = b""
        while True:
            if len(buf) < upto:
                buf += get_range(url, len(buf), upto - 1)
            try:
                return parse_gguf_header(buf)
            except NeedMore as e:
                if e.upto > MAX_GGUF_BYTES or len(buf) < upto:  # 上限を超える・ファイルの終わり
                    raise HeaderError(
                        tr(
                            "GGUF の見出しが大きすぎるか、途中で切れています",
                            "the GGUF header is too large or truncated",
                        )
                    ) from e
                upto = min(MAX_GGUF_BYTES, max(e.upto, upto * 2))
    head = get_range(url, 0, 7)
    if len(head) < 8:
        raise HeaderError(
            tr(
                "safetensors の先頭 8 バイトを読めません",
                "cannot read the first 8 bytes of the safetensors file",
            )
        )
    n = struct.unpack("<Q", head[:8])[0]
    if n <= 0 or n > MAX_SAFETENSORS_HEADER:
        raise HeaderError(
            tr(
                f"safetensors の見出しの長さがおかしい({n} バイト)",
                f"bad safetensors header length ({n} bytes)",
            )
        )
    body = get_range(url, 8, 8 + n - 1)
    if len(body) < n:
        raise HeaderError(
            tr("safetensors の見出しが途中で切れています", "the safetensors header is truncated")
        )
    return parse_safetensors_header(body[:n])


# --- キャッシュと、一覧への書き込み -------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HeaderCache:
    """URL ごとの読んだ結果。sha256 が変わったら読み直す。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict[str, Any]] = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                    self.data = raw["files"]
            except (OSError, ValueError):
                self.data = {}

    def get(self, f: ModelFile) -> dict[str, Any] | None:
        row = self.data.get(f.url or "")
        if row is None or (f.sha256 and row.get("sha256") and row["sha256"] != f.sha256):
            return None
        return row

    def put(self, f: ModelFile, info: HeaderInfo | None, error: str | None = None) -> None:
        row: dict[str, Any] = {"sha256": f.sha256, "checked_at": _now()}
        if info is not None:
            row.update(info.to_dict())
        if error:
            row["error"] = error
        self.data[f.url or ""] = row

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = {"note": "genmodelfit が読んだ重みのファイルの見出し(精度・要素の数)", "files": self.data}
        tmp.write_text(
            json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8", newline="\n"
        )
        tmp.replace(self.path)


def target_files(entries: Iterable[ModelEntry], *, mains_only: bool = True) -> list[ModelFile]:
    """見出しを読むファイル(Hugging Face の safetensors / GGUF。既定は本体だけ)。

    本体は役割が main のものと、groups.main_files が本体とみなすもの(役割が main のファイルが無ければ、
    テキストエンコーダ・VAE 以外で一番大きいファイル。背景の除去・深度の推定などの道具の重みは
    役割が other なので、#30 で足した)。"""
    from .estimate import _role_of
    from .groups import main_files
    from .hf import parse_hf_url

    out = []
    for e in entries:
        picked = main_files(e) if mains_only else []
        for f in e.files:
            if mains_only and _role_of(f) != "main" and not any(f is g for g in picked):
                continue
            if not f.url or not parse_hf_url(f.url):
                continue
            if not f.url.split("?", 1)[0].lower().endswith(WEIGHT_EXTS):
                continue
            out.append(f)
    return out


@dataclass
class InspectStats:
    files: int = 0  # 見出しを持つ対象のファイル(のべ)
    urls: int = 0  # 対象の URL(重ならない)
    from_cache: int = 0
    fetched: int = 0
    failed: int = 0
    skipped_offline: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["errors"] = self.errors[:20]
        return d


def inspect_files(
    entries: list[ModelEntry],
    get_range: RangeGetter | None,
    cache: HeaderCache,
    *,
    offline: bool = False,
    delay: float = DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> InspectStats:
    """本体のファイルの見出しを読み(キャッシュがあればそれを使い)、ModelFile.header_precision /
    header_params に入れる。offline=True ならキャッシュだけを使う(通信しない)。1 つずつ順に送る。"""
    files = target_files(entries)
    stats = InspectStats(files=len(files))
    by_url: dict[str, list[ModelFile]] = {}
    for f in files:
        by_url.setdefault(f.url or "", []).append(f)
    stats.urls = len(by_url)
    sent = 0
    for i, (url, fs) in enumerate(sorted(by_url.items()), 1):
        row = cache.get(fs[0])
        # 前に読めなかったもの(一時的な失敗かもしれない)は、通信してよいときは読み直す
        if (row is None or row.get("error")) and not offline and get_range is not None:
            if sent:
                sleep(delay)
            sent += 1
            try:
                info = read_header(url, get_range)
                cache.put(fs[0], info)
            except Exception as e:  # 1 つ読めなくても残りは続ける(理由は記録する)
                cache.put(fs[0], None, f"{type(e).__name__}: {e}"[:300])
                stats.errors.append(f"{url}: {type(e).__name__}: {e}"[:300])
            row = cache.get(fs[0])
            stats.fetched += 1
            if progress and i % 25 == 0:
                progress(tr(f"  見出し {i}/{stats.urls}", f"  headers {i}/{stats.urls}"))
        elif row is not None and not row.get("error"):
            stats.from_cache += 1
        else:
            stats.skipped_offline += 1
            continue
        if row is None or row.get("error"):
            stats.failed += 1
            continue
        for f in fs:
            f.header_precision = row.get("precision")
            f.header_params = row.get("params")
    return stats
