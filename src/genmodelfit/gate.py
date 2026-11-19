# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""自動でおすすめに出してよいかの判定(Issue #3)。

使う側(AI生成キット)は、人が確かめずにおすすめを出す。そのため判定は**保守的**にする:
分からないもの(ライセンス不明・HEAD 未確認・版不明など)はすべて「保留」。全部の条件を満たしたものだけ auto。

条件(id は機械で読む名前。変えない):

| id | 通る条件 |
|---|---|
| license | すべてのファイルのライセンス(本家のもの)が許可リストにある |
| gated | どのファイルも利用申請が要らない(gated=false。不明は保留)。部品の本家が申請を求めるものも保留 |
| official_workflow | ComfyUI 公式テンプレート(openSource が false でない) |
| custom_nodes | テンプレートがカスタムノードを要らない |
| comfyui_version | テンプレートの minComfyUIVersion が、使う ComfyUI の版以下 |
| url_reachable | すべての URL が匿名の HEAD で 200 / 302 / 307 を返し、確認が新しい(既定 30 日) |
| sha256 | すべてのファイルに 64 桁の sha256 がある |
| content | 無検閲(uncensored)でない(allow_uncensored で外せる) |
| size_known | すべてのファイルの大きさが分かる |
| app_runtime | (runtime が ComfyUI 以外のモデルだけ。ComfyUI の 3 条件の代わり)常に保留。アプリが確かめる |
| reviewed | 確認の印(review)が確認待ち(pending)でない。印が無い古い一覧は通る(#42) |

判定の結果は一覧(db.json)に持たない(毎回ここで計算する)。持つのは update が記録した HEAD の結果だけ。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hf import (
    COMPONENTS,
    LINEAGE_WHY_EN,
    UNVERIFIED_WHY_EN,
    UPSTREAM_GATED,
    component_for,
    families_for,
    foreign_marker,
    is_repackager,
    license_tier,
    lineage_for,
    unverified_component_for,
    verified_row_for,
)
from .i18n import get_language, tr
from .models import ModelEntry, ModelFile

CHECK_IDS = (
    "license",
    "gated",
    "official_workflow",
    "custom_nodes",
    "comfyui_version",
    "url_reachable",
    "sha256",
    "content",
    "size_known",
    "app_runtime",
    "reviewed",
)
# runtime が ComfyUI 以外のモデル(手のモデルの piper・irodori-tts。#36)で、ComfyUI の 3 条件の代わりに使う条件
COMFYUI_CHECKS = ("official_workflow", "custom_nodes", "comfyui_version")

# 商用で使えて、使い方の制限が無いライセンス(理由は docs/gate.md)。
# openrail 系(使い方の制限)・非商用・売上の基準つきのコミュニティライセンス・llama / gemma の規約・
# other・不明は入れない。cc-by-* は表示の義務の段(hf.TIER_ATTRIBUTION)なので既定では入れない
# (表示を出せるアプリは --license-allowlist で足す)
DEFAULT_LICENSE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "apache-2.0",
        "mit",
        "bsd-2-clause",
        "bsd-3-clause",
        "cc0-1.0",
        "unlicense",
    }
)

# 匿名の HEAD で「取れる」とみなす応答。docs/gate.md に実測を書いた:
# LFS のファイルは 302(CDN へ)、LFS でない小さいファイルは 307(/api/resolve-cache へ)。
# リポジトリの名前が変わったものも 307 を返すが、無いファイルでも 307 になるので、update はその転送を
# 1 回だけ追って最後の応答を記録する(fetch.HttpFetcher.head_status)。記録に残る 307 は /api/resolve-cache への
# 転送だけ。gated は 401、無いファイルは 404
REACHABLE_STATUSES = frozenset({200, 302, 307})

# HEAD の結果を信じる日数の既定。これより古い確認は保留(上流でファイルが消えたり gated になったりする)
DEFAULT_MAX_HEAD_AGE_DAYS = 30

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class GatePolicy:
    """判定の方針。使う側が差し替える。

    comfyui_version: 使う ComfyUI の版。**使う側のアプリは必ず渡す**(AI生成キットなら "0.40.0")。
        None なら evaluate_offline に渡した sources に update が記録した最新のリリースを使い、
        sources も無ければ comfyui_version は保留
    max_head_age_days: HEAD の確認をこの日数まで信じる。古ければ url_reachable は保留。None は期限なし
    """

    license_allowlist: frozenset[str] = DEFAULT_LICENSE_ALLOWLIST
    comfyui_version: str | None = None
    allow_uncensored: bool = False
    max_head_age_days: int | None = DEFAULT_MAX_HEAD_AGE_DAYS

    def to_dict(self) -> dict[str, Any]:
        return {
            "license_allowlist": sorted(self.license_allowlist),
            "comfyui_version": self.comfyui_version,
            "allow_uncensored": self.allow_uncensored,
            "max_head_age_days": self.max_head_age_days,
        }


@dataclass
class Check:
    id: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "passed": self.passed, "detail": self.detail}


@dataclass
class GateResult:
    model_id: str
    checks: list[Check] = field(default_factory=list)
    comfyui_version: str | None = None  # 比べた ComfyUI の版

    @property
    def auto(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def held_by(self) -> list[str]:
        return [c.id for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto": self.auto,
            "held_by": self.held_by,
            "comfyui_version": self.comfyui_version,
            "checks": [c.to_dict() for c in self.checks],
        }


# --- 版の比較 -------------------------------------------------------------------------------------


_PRE = re.compile(r"^(rc|dev|a|alpha|b|beta|pre|preview)\d*$", re.IGNORECASE)


def _split_version(v: str | None) -> tuple[tuple[int, ...], bool] | None:
    """ "v0.42.1" → ((0, 42, 1), False)。"0.4.0-rc1"・"0.4.0rc1"・"0.4.0.dev3" → ((0, 4, 0), True)。
    数字で始まらない・印が読めない版は None。"+build" の後ろは見ない。"""
    if not isinstance(v, str):
        return None
    s = v.strip().lstrip("vV").split("+", 1)[0]
    if not s:
        return None
    nums: list[int] = []
    pre = False
    for part in re.split(r"[.\-_]", s):
        m = re.match(r"\d+", part)
        if m and not pre:
            nums.append(int(m.group()))
            rest = part[m.end() :]
            if rest:
                if not _PRE.match(rest):
                    return None
                pre = True
            continue
        if nums and _PRE.match(part):
            pre = True
            continue
        return None
    return (tuple(nums), pre) if nums else None


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """ "v0.42.1" → (0, 42, 1)。読めなければ None。プレリリースの印(-rc1・.dev など)は数字だけを返す
    (印は version_le が見る)。"""
    sv = _split_version(v)
    return sv[0] if sv else None


def version_le(a: str | None, b: str | None) -> bool | None:
    """a <= b か。どちらかが読めなければ None。足りない桁は 0 とみなす(0.3 == 0.3.0)。
    数字が同じなら、プレリリース(0.4.0-rc1・0.4.0.dev1)はリリース(0.4.0)より前とみなす
    (必要な版の側でも、使う版の側でも)。"""
    sa, sb = _split_version(a), _split_version(b)
    if sa is None or sb is None:
        return None
    (pa, prea), (pb, preb) = sa, sb
    n = max(len(pa), len(pb))
    ka = (pa + (0,) * (n - len(pa)), 0 if prea else 1)
    kb = (pb + (0,) * (n - len(pb)), 0 if preb else 1)
    return ka <= kb


# --- 許可リスト -----------------------------------------------------------------------------------


def parse_allowlist(text: str) -> frozenset[str]:
    """ "mit, apache-2.0" や、1 行に 1 つ(# から後は注釈)の文字列を許可リストにする。"""
    items: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for part in line.split(","):
            p = part.strip().lower()
            if p:
                items.add(p)
    return frozenset(items)


def load_allowlist(value: str) -> frozenset[str]:
    """CLI の --license-allowlist。ファイルのパスならその中身、そうでなければ「a,b,c」。"""
    p = Path(value)
    if p.is_file():
        return parse_allowlist(p.read_text(encoding="utf-8"))
    return parse_allowlist(value)


def _license_label(lic: str | None, name: str | None) -> str:
    if lic is None:
        return tr("不明", "unknown")
    if lic == "other" and name:
        return f"other({name})"
    return lic


def license_allowed(lic: str | None, name: str | None, allow: Iterable[str]) -> bool:
    """ファイルのライセンスが許可リストにあるか。other は license_name で照合する
    (例: "mit-and-apache-2.0" を許可リストに足せば通せる)。"""
    allow = {a.lower() for a in allow}
    if lic is None:
        return False
    lic = lic.lower()
    if lic == "other":
        return bool(name) and name.lower() in allow
    return lic in allow


# --- ファイルのライセンスを、判定のために保守的に読む ----------------------------------------------
#
# 一覧の license は、置き場所のリポジトリの表記をそのまま持つことがある。リポジトリの中には別のモデルの部品
# (テキストエンコーダ・画像の読み取り部・検出器など)が入っていて、その部品の本当のライセンスはリポジトリの
# 表記と違う(例: HiDream の llama_3.1_8b_instruct は Llama 3.1 の規約、TripoSplat の dino_v3_vit_h は
# DINOv3 の規約だが、リポジトリの表記は mit)。そこで判定では、一覧の値を書き換えずに次の順に読む
# (どれかで決まったらそこで止める。docs/gate.md の「ファイルのライセンスの読み方」):
#   1. ファイル名が制限付きの部品(RESTRICTED_COMPONENTS・hf の部品の表で寛容でない行)なら、その規約
#   2. ファイル名が本家を確かめられない部品(hf.UNVERIFIED_COMPONENTS。RT-DETRv4 など)なら「確かめられない」
#   3. 名前(ファイル名・ライセンスを読んだ道)が、元のライセンスを読めない系統(hf.LINEAGES。Stable Diffusion 2
#      から作った SDPose・Lotus など)なら「確かめられない」。系統の表(hf.FAMILIES)より寛容な値も同じ
#   4. ライセンスを再配布側の表記からしか読めていない(license_from が再配布のリポジトリ)なら、
#      出どころの分かる寛容な部品(T5・UMT5・CLIP など)のときだけそのライセンス、それ以外は「確かめられない」
#   5. ファイル名が、置き場所・ライセンスの出どころと別の系統の部品を示す(hf.FOREIGN_MARKERS)なら、
#      本家を確かめた行(hf の部品の表・系統の表)がファイル名に当たるときだけ通す。
#      当たらなければ「確かめられない」
#   6. それ以外は一覧の値(本家から読んだもの)

# (ファイル名の正規表現, ライセンス(None は確かめられない), 根拠(日本語, 英語), 本家が利用申請を求めるか)。
# hf の部品の表と重なるが、一覧が古い決め方で作られていても判定で落とせるよう、名前を広めに取って別に持つ
RESTRICTED_COMPONENTS: tuple[tuple[re.Pattern[str], str | None, tuple[str, str], bool], ...] = (
    (
        re.compile(r"llama|llava", re.IGNORECASE),
        "llama3.1",
        (
            "Meta Llama の規約(https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct)",
            "the Meta Llama license (https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct)",
        ),
        False,
    ),
    (
        re.compile(r"t5gemma|gemma[-_ ]?[123](?![0-9])", re.IGNORECASE),
        "gemma",
        (
            "Gemma 利用規約(https://ai.google.dev/gemma/terms)",
            "the Gemma Terms of Use (https://ai.google.dev/gemma/terms)",
        ),
        False,
    ),
    (
        re.compile(r"dino[\W_]?v3", re.IGNORECASE),
        "other",
        (
            "DINOv3 の規約(https://ai.meta.com/resources/models-and-libraries/dinov3-license。"
            "https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m は gated)",
            "the DINOv3 license (https://ai.meta.com/resources/models-and-libraries/dinov3-license;"
            " https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m is gated)",
        ),
        True,
    ),
    (
        re.compile(r"(^|[^a-z0-9])sam[\W_]?3(?!\d)", re.IGNORECASE),
        "other",
        (
            "SAM 3 の規約(https://huggingface.co/facebook/sam3。gated)",
            "the SAM 3 license (https://huggingface.co/facebook/sam3; gated)",
        ),
        True,
    ),
    (
        re.compile(r"depth[\W_]?anything[\W_]?v2[\W_]*(vit)?[blg]", re.IGNORECASE),
        "cc-by-nc-4.0",
        (
            "Depth Anything V2 Base/Large/Giant は非商用(https://huggingface.co/depth-anything/Depth-Anything-V2-Large)",
            "Depth Anything V2 Base/Large/Giant is non-commercial"
            " (https://huggingface.co/depth-anything/Depth-Anything-V2-Large)",
        ),
        False,
    ),
    (
        re.compile(r"(lumina|neta).*(all[\W_]?in[\W_]?one|aio)|^lumina_2\.safetensors$", re.IGNORECASE),
        "gemma",
        (
            "Lumina Image 2.0 系の一式のファイルはテキストエンコーダ Gemma 2 2B を含む(Gemma 利用規約)",
            "all-in-one Lumina Image 2.0 files include the Gemma 2 2B text encoder (the Gemma Terms of Use)",
        ),
        False,
    ),
)
PERMISSIVE_COMPONENTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"umt5[-_]?xxl", re.IGNORECASE), "apache-2.0", "https://huggingface.co/google/umt5-xxl"),
    (
        re.compile(r"(^|[^a-z0-9])t5(xxl|[-_]v1_1|[-_]base)", re.IGNORECASE),
        "apache-2.0",
        "https://huggingface.co/google/t5-v1_1-xxl",
    ),
    (re.compile(r"(^|[^a-z0-9])clip_l(?![a-z0-9])", re.IGNORECASE), "mit", "https://github.com/openai/CLIP"),
    (
        re.compile(r"(^|[^a-z0-9])clip_g(?![a-z0-9])", re.IGNORECASE),
        "mit",
        "https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k",
    ),
    (
        re.compile(r"(^|[^a-z0-9])clip_vision_h(?![a-z0-9])", re.IGNORECASE),
        "mit",
        "https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K",
    ),
    (
        re.compile(r"(^|[^a-z0-9])wan_?2[._]1_vae", re.IGNORECASE),
        "apache-2.0",
        "https://huggingface.co/Wan-AI/Wan2.1-T2V-14B",
    ),
)


@dataclass
class ResolvedLicense:
    license: str | None  # None = 確かめられない
    license_name: str | None
    note: str | None = None  # 一覧の値と違う読み方をしたときの理由
    gated: bool = False  # 本家が利用申請(規約への同意)を求める


def _own_names(f: ModelFile) -> list[str]:
    """ファイルの「自分の系統」を表す名前(置き場所のリポジトリと、ライセンスを読んだ道)。"""
    return [n for n in (f.repo_id, f.license_from, *f.license_path) if n]


def _lineage_names(f: ModelFile) -> list[str]:
    """系統を見る名前。再配布のリポジトリの名前は入れない(Comfy-Org/SDPose の中の RT-DETRv4 など、
    リポジトリに同梱された別のモデルまで同じ系統にしないため)。"""
    base = f.name.rsplit("/", 1)[-1]
    names = [base]
    names += [n for n in (f.repo_id, f.license_from, *f.license_path) if n and not is_repackager(n)]
    return names


def resolve_file_license(f: ModelFile) -> ResolvedLicense:
    base = f.name.rsplit("/", 1)[-1]
    upstream_gated = any(e.startswith(UPSTREAM_GATED) for e in f.license_evidence)
    # 1. 制限付きの部品(どのリポジトリの中にあっても)
    for pat, lic, why, gated in RESTRICTED_COMPONENTS:
        if pat.search(base):
            return ResolvedLicense(
                lic, None, tr(f"ファイル名から {why[0]}", f"from the file name: {why[1]}"), gated
            )
    comp = component_for(base)
    if comp and comp.everywhere:
        return ResolvedLicense(
            comp.license,
            comp.license_name,
            tr(f"ファイル名から {comp.key}({comp.url})", f"from the file name: {comp.key} ({comp.url})"),
            comp.gated,
        )
    # 2. 本家を確かめられない部品
    unv = unverified_component_for(base)
    if unv:
        why = unv[1] if get_language() == "ja" else UNVERIFIED_WHY_EN.get(unv[0], unv[1])
        return ResolvedLicense(None, None, tr(f"ファイル名から {why}", f"from the file name: {why}"))
    if f.license is None:
        return ResolvedLicense(None, None, None, upstream_gated)
    # 3. 元のライセンスを読めない系統・系統の表より寛容な値
    names = _lineage_names(f)
    lin = lineage_for(names)
    if lin:
        why = lin[1] if get_language() == "ja" else LINEAGE_WHY_EN.get(lin[0], lin[1])
        return ResolvedLicense(None, None, tr(f"系統 {lin[0]}: {why}", f"lineage {lin[0]}: {why}"))
    fams = families_for(names)
    if fams:
        fw = max(fams, key=lambda x: license_tier(x.license, x.license_name))
        if license_tier(f.license, f.license_name) < license_tier(fw.license, fw.license_name):
            label = _license_label(f.license, f.license_name)
            return ResolvedLicense(
                None,
                None,
                tr(
                    f"一覧の {label} は系統 {fw.key} の "
                    f"{_license_label(fw.license, fw.license_name)} より寛容",
                    f"the listed {label} is more permissive than "
                    f"{_license_label(fw.license, fw.license_name)}"
                    f" of lineage {fw.key}",
                ),
            )
    # 4. 再配布側の表記しか無い
    src = f.license_from or f.repo_id
    if src and is_repackager(src):
        for pat, lic, why in PERMISSIVE_COMPONENTS:
            if pat.search(base):
                return ResolvedLicense(lic, None, tr(f"ファイル名から {why}", f"from the file name: {why}"))
        label = _license_label(f.license, f.license_name)
        return ResolvedLicense(
            None,
            None,
            tr(
                f"再配布側({src})の表記 {label} だけで、本家を確かめられない",
                f"only the repackager's ({src}) label {label}; the original cannot be checked",
            ),
        )
    # 5. 別の系統の部品
    fk = foreign_marker(base, _own_names(f))
    if fk:
        row = verified_row_for(base)
        if row is None:
            return ResolvedLicense(
                None,
                None,
                tr(
                    f"置き場所 {f.repo_id} の本体と別の系統({fk})の部品とみえ、本家を確かめた表に無い",
                    f"looks like a part of another lineage ({fk}) than the main model in {f.repo_id},"
                    " and is not in the table of checked originals",
                ),
            )
        if row in COMPONENTS and license_tier(row.license, row.license_name) >= license_tier(
            f.license, f.license_name
        ):
            return ResolvedLicense(
                row.license,
                row.license_name,
                tr(f"ファイル名から {row.key}({row.url})", f"from the file name: {row.key} ({row.url})"),
            )
    return ResolvedLicense(f.license, f.license_name, None, upstream_gated)


# --- 条件ごとの判定 -------------------------------------------------------------------------------


def _no_files() -> str:
    return tr("ファイルの記載がありません", "no files listed")


def _not_official() -> str:
    return tr("公式テンプレートではないため確かめられない", "cannot be checked: not an official template")


def _first(items: list[str], more: int) -> str:
    s = tr("、", ", ").join(items[:3])
    return s + (tr(f" ほか {more - 3} 件", f" and {more - 3} more") if more > 3 else "")


def check_license(m: ModelEntry, allow: frozenset[str]) -> Check:
    if not m.files:
        return Check("license", False, _no_files())
    resolved = [(f, resolve_file_license(f)) for f in m.files]
    bad = [(f, r) for f, r in resolved if not license_allowed(r.license, r.license_name, allow)]
    if bad:
        items = [
            f"{f.name}: {_license_label(r.license, r.license_name)}"
            + ((tr(f"({r.note})", f" ({r.note})")) if r.note else "")
            for f, r in bad
        ]
        return Check(
            "license",
            False,
            tr("許可リストに無いライセンス: ", "license not in the allowlist: ") + _first(items, len(items)),
        )
    labels = sorted({_license_label(r.license, r.license_name) for _, r in resolved})
    return Check(
        "license", True, tr("すべて許可リストにある: ", "all in the allowlist: ") + ", ".join(labels)
    )


def check_gated(m: ModelEntry) -> Check:
    if not m.files:
        return Check("gated", False, _no_files())
    gated = [f.name for f in m.files if f.gated is True]
    unknown = [f.name for f in m.files if f.gated is None]
    upstream = [f.name for f in m.files if f.gated is not True and resolve_file_license(f).gated]
    if gated:
        return Check(
            "gated",
            False,
            tr("利用申請(gated)が要る: ", "access request (gated) needed: ") + _first(gated, len(gated)),
        )
    if upstream:
        return Check(
            "gated",
            False,
            tr(
                "本家が利用申請(規約への同意)を求める部品: ",
                "parts whose original requires an access request (agreeing to terms): ",
            )
            + _first(upstream, len(upstream)),
        )
    if unknown:
        return Check(
            "gated",
            False,
            tr("利用申請が要るか不明: ", "unknown whether an access request is needed: ")
            + _first(unknown, len(unknown)),
        )
    return Check("gated", True, tr("利用申請は要らない", "no access request needed"))


def check_official(m: ModelEntry) -> Check:
    if m.source != "comfyui-template" or m.template is None:
        return Check(
            "official_workflow",
            False,
            tr(
                f"ComfyUI 公式テンプレートではない(出どころ: {m.source})",
                f"not an official ComfyUI template (source: {m.source})",
            ),
        )
    if m.template.open_source is False:
        return Check(
            "official_workflow",
            False,
            tr("公式テンプレートだが openSource が false", "official template, but openSource is false"),
        )
    note = (
        ""
        if m.template.open_source
        else tr(
            "(openSource の記載なし。ローカルで動くテンプレートとして扱う)",
            " (openSource not stated; treated as a template that runs locally)",
        )
    )
    return Check(
        "official_workflow",
        True,
        tr(f"公式テンプレート {m.template.name}{note}", f"official template {m.template.name}{note}"),
    )


def check_custom_nodes(m: ModelEntry) -> Check:
    if m.template is None:
        return Check("custom_nodes", False, _not_official())
    nodes = m.template.requires_custom_nodes
    if nodes:
        return Check(
            "custom_nodes",
            False,
            tr("カスタムノードが要る: ", "custom nodes needed: ") + _first(list(nodes), len(nodes)),
        )
    return Check("custom_nodes", True, tr("カスタムノードは要らない", "no custom nodes needed"))


def check_app_runtime(m: ModelEntry) -> Check:
    """ComfyUI 以外の仕組みで動かすモデル。この一覧では動かし方を確かめられないので、常に保留。"""
    return Check(
        "app_runtime",
        False,
        tr(
            f"ComfyUI 以外の仕組み({m.runtime})で動かすモデル。動かせるかは使う側(アプリ)が確かめる",
            f"runs on something other than ComfyUI ({m.runtime}); the app using it checks that it runs",
        ),
    )


def check_reviewed(m: ModelEntry) -> Check:
    """確認の印(#42)。pending(新しい項目・寛容な側へのライセンスの変化・必要量の減少・新しく無検閲)は、
    人が一次資料で確かめる(genmodelfit review approve)まで自動のおすすめに出さない。"""
    r = m.review
    if r is None:
        return Check(
            "reviewed",
            True,
            tr(
                "確認の印が無い(印を入れる前の一覧。確認済みとして扱う)",
                "no review mark (a list from before the marks; treated as reviewed)",
            ),
        )
    if r.status == "ok":
        who = (
            tr(f"({r.decided_by}、{r.decided_at})", f" ({r.decided_by}, {r.decided_at})")
            if r.decided_by
            else ""
        )
        return Check("reviewed", True, tr(f"確認済み{who}", f"reviewed{who}"))
    why = ", ".join(r.reasons) or tr("理由の記載なし", "no reason given")
    return Check(
        "reviewed",
        False,
        tr(
            f"確認待ち({r.status}: {why})。根拠を付けて確かめるまで自動で出さない",
            f"waiting for review ({r.status}: {why}); not auto-recommended until checked with evidence",
        ),
    )


def check_comfyui_version(m: ModelEntry, target: str | None) -> Check:
    if m.template is None:
        return Check("comfyui_version", False, _not_official())
    need = m.template.min_comfyui_version
    if not target:
        return Check(
            "comfyui_version",
            False,
            tr(
                "使う ComfyUI の版が分からない(update で記録するか指定する)",
                "the ComfyUI version in use is unknown (record it with update or pass it)",
            ),
        )
    if not need:
        return Check(
            "comfyui_version",
            False,
            tr(
                "テンプレートに必要な版(minComfyUIVersion)の記載がない",
                "the template does not state the version it needs (minComfyUIVersion)",
            ),
        )
    ok = version_le(need, target)
    if ok is None:
        return Check(
            "comfyui_version",
            False,
            tr(
                f"版を読めない: 必要 {need} / 使う {target}",
                f"cannot read the versions: needs {need} / uses {target}",
            ),
        )
    if not ok:
        return Check(
            "comfyui_version",
            False,
            tr(
                f"ComfyUI {need} 以上が要る(使う版 {target})",
                f"needs ComfyUI {need} or later (uses {target})",
            ),
        )
    return Check(
        "comfyui_version", True, tr(f"必要 {need} ≦ 使う版 {target}", f"needs {need} <= uses {target}")
    )


def _parse_time(v: str | None) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _now() -> datetime:
    """今の時刻(UTC)。試験では差し替える。"""
    return datetime.now(UTC)


def check_url(m: ModelEntry, max_age_days: int | None = None, now: datetime | None = None) -> Check:
    if not m.files:
        return Check("url_reachable", False, _no_files())
    no_url = [f.name for f in m.files if not f.url]
    unchecked = [f.name for f in m.files if f.url and f.head_status is None]
    bad = [
        f"{f.name}: HTTP {f.head_status}"
        for f in m.files
        if f.url and f.head_status is not None and f.head_status not in REACHABLE_STATUSES
    ]
    if no_url:
        return Check("url_reachable", False, tr("URL が無い: ", "no URL: ") + _first(no_url, len(no_url)))
    if bad:
        return Check(
            "url_reachable",
            False,
            tr("匿名で取れない: ", "not downloadable anonymously: ") + _first(bad, len(bad)),
        )
    if unchecked:
        return Check(
            "url_reachable",
            False,
            tr("未確認(HEAD の記録なし): ", "unchecked (no HEAD record): ")
            + _first(unchecked, len(unchecked)),
        )
    if max_age_days is not None:
        now = now or _now()
        times = [_parse_time(f.head_checked_at) for f in m.files]
        if any(t is None for t in times):
            no_time = [f.name for f, t in zip(m.files, times, strict=True) if t is None]
            return Check(
                "url_reachable",
                False,
                tr("確認の日時が無い: ", "no check time: ") + _first(no_time, len(no_time)),
            )
        oldest = min(t for t in times if t is not None)
        age = (now - oldest).total_seconds() / 86400
        if age > max_age_days:
            return Check(
                "url_reachable",
                False,
                tr(
                    f"確認が古い({oldest:%Y-%m-%d}、{age:.0f} 日前。{max_age_days} 日まで。"
                    "genmodelfit update --heads-only でやり直す)",
                    f"check too old ({oldest:%Y-%m-%d}, {age:.0f} days ago; up to {max_age_days} days;"
                    " redo with genmodelfit update --heads-only)",
                ),
            )
    codes = sorted({f.head_status for f in m.files if f.head_status is not None})
    return Check(
        "url_reachable",
        True,
        tr("匿名の HEAD で取れる: ", "reachable by anonymous HEAD: ") + ", ".join(str(c) for c in codes),
    )


def check_sha256(m: ModelEntry) -> Check:
    if not m.files:
        return Check("sha256", False, _no_files())
    bad = [f.name for f in m.files if not (isinstance(f.sha256, str) and _SHA256.match(f.sha256.lower()))]
    if bad:
        return Check("sha256", False, tr("sha256 が無い: ", "no sha256: ") + _first(bad, len(bad)))
    return Check("sha256", True, tr("すべてのファイルに sha256 がある", "every file has a sha256"))


def check_content(m: ModelEntry, allow_uncensored: bool) -> Check:
    if m.content == "sfw":
        return Check("content", True, tr("無検閲の印は無い", "not marked uncensored"))
    if m.content == "uncensored" and allow_uncensored:
        return Check(
            "content",
            True,
            tr(
                "無検閲(方針で自動のおすすめを許可)", "uncensored (auto-recommendation allowed by the policy)"
            ),
        )
    if m.content == "uncensored":
        return Check(
            "content",
            False,
            tr(
                "無検閲のモデルは初心者向けの自動おすすめに出さない(一覧・絞り込みには出る)",
                "uncensored models are not auto-recommended to beginners"
                " (they still appear in lists and filters)",
            ),
        )
    return Check(
        "content", False, tr(f"内容の印が分からない: {m.content}", f"unknown content mark: {m.content}")
    )


def check_size(m: ModelEntry) -> Check:
    if not m.files:
        return Check("size_known", False, _no_files())
    if getattr(m, "files_complete", None) is False:
        # テンプレートがモデルのファイルを全部は書いていない(カスタムノードが自分で取るなど)
        return Check(
            "size_known",
            False,
            tr(
                "ファイルの一覧が完全でない(files_complete=false)",
                "the file list is incomplete (files_complete=false)",
            ),
        )
    unknown = [f.name for f in m.files if not f.size_bytes or f.size_bytes <= 0]
    if unknown:
        return Check(
            "size_known", False, tr("大きさが分からない: ", "unknown size: ") + _first(unknown, len(unknown))
        )
    total = m.total_size_bytes or 0
    return Check("size_known", True, tr(f"合計 {total / 1e9:.2f} GB", f"{total / 1e9:.2f} GB in total"))


# --- まとめ ---------------------------------------------------------------------------------------


def recorded_comfyui_version(sources: dict[str, Any] | None) -> str | None:
    """update が一覧に記録した ComfyUI の最新の版(無ければ None)。"""
    c = (sources or {}).get("comfyui")
    v = c.get("latest_release") if isinstance(c, dict) else None
    return v if isinstance(v, str) and v else None


def evaluate_offline(
    model: ModelEntry,
    policy: GatePolicy | None = None,
    *,
    sources: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> GateResult:
    """一覧に記録された情報だけで判定する(ネットには触らない)。

    sources: 一覧の `sources`(db.sources)。policy.comfyui_version が None のとき、ここに記録された
    最新の版を使う。どちらも無ければ comfyui_version は保留になる。
    """
    p = policy or GatePolicy()
    target = p.comfyui_version or recorded_comfyui_version(sources)
    if model.runtime in (None, "comfyui"):
        runtime_checks = [
            check_official(model),
            check_custom_nodes(model),
            check_comfyui_version(model, target),
        ]
    else:
        runtime_checks = [check_app_runtime(model)]
    checks = [
        check_license(model, p.license_allowlist),
        check_gated(model),
        *runtime_checks,
        check_url(model, p.max_head_age_days, now),
        check_sha256(model),
        check_content(model, p.allow_uncensored),
        check_size(model),
        check_reviewed(model),
    ]
    return GateResult(model.id, checks, target)


def summarize(results: Iterable[GateResult]) -> dict[str, Any]:
    """件数のまとめ: auto / held、落ちた条件ごとの件数、最初に落ちた条件ごとの件数。"""
    res = list(results)
    failing = {cid: 0 for cid in CHECK_IDS}
    first = {cid: 0 for cid in CHECK_IDS}
    for r in res:
        held = r.held_by
        for cid in held:
            failing[cid] = failing.get(cid, 0) + 1
        if held:
            first[held[0]] = first.get(held[0], 0) + 1
    auto = sum(1 for r in res if r.auto)
    return {
        "models": len(res),
        "auto": auto,
        "held": len(res) - auto,
        "failing": failing,
        "first_failing": first,
    }
