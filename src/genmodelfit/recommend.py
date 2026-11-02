# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""おすすめ順に並べる・Score(#95。決まりは docs/scoring.md)。

並べ方(docs/scoring.md「並び」): 動く → 判定できない → 動かない →(--json・ライブラリは自動でおすすめに出せる
もの)→ 段階(逃がさずに載る / Marginal。Marginal の中は動かし方の速い順)→ 公式 → 人気の順位 → Score → ID。
Score(0〜100)= memory_fit: その動かし方で一番きつい資源の使用率 r の曲線(llmfit の fit_score と同じ式・定数。
docs/scoring.md「Score」)。段階・準備・実測の有無・人気は入れない。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .db import Database
from .estimate import GIB, Coefficients, estimate, gpu_choice, load_coefficients, shared_memory
from .groups import group_key, index_for, is_community, precision_rank
from .i18n import tr
from .models import (
    CONTENT_LEVELS,
    FIT_LEVELS,
    FIT_UNKNOWN,
    MODALITIES,
    Estimate,
    ModelEntry,
    Settings,
    SystemInfo,
    fit_rank,
)
from .tasks import TOOL_TASKS, is_tool, main_use, parse_modalities, task_codes

if TYPE_CHECKING:
    from .gate import GatePolicy

# 内容の絞り込み。既定は sfw(無検閲は all / uncensored を指定したときだけ出す)
CONTENT_FILTERS = ("all", *CONTENT_LEVELS)
DEFAULT_CONTENT = "sfw"

FIT_LABEL = {
    "perfect": "Perfect",
    "good": "Good",
    "marginal": "Marginal",
    "too_tight": "Too tight",
    FIT_UNKNOWN: "?",  # 大きさが分からず判定できない(Too tight とは言わない。#28)
}

# Score の曲線の定数(llmfit の fit_score。llmfit-core/src/fit.rs:2335-2349、AlexsJones/llmfit 2f35825)。
# r がこれ以下なら 100、超えたら正規分布の片側の形で下がる(docs/scoring.md「Score」)
SCORE_FLAT_UNTIL = 0.70
SCORE_WIDTH = 0.20
# 動く行(perfect / good / marginal)の下限。r = 0.98(marginal の端)の値
SCORE_RUNNABLE_FLOOR = 37.5
RUNNABLE_LEVELS = ("perfect", "good", "marginal")


def memory_fit(r: float | None, level: str) -> float:
    """収まり(0〜100。丸める前の値)。r はその動かし方で一番きつい資源の使用率(usage_ratio)。
    動かない・判定できない・r が分からないときは 0。動く行は SCORE_RUNNABLE_FLOOR を下限にする
    (実測で段階を決めた行・ページファイルで動く CPU の行は r が 0.98 を超えても動く)。"""
    if level not in RUNNABLE_LEVELS or r is None or not math.isfinite(r):
        return 0.0  # 分からない r(None・NaN・無限大)は判定できないものと同じ 0
    if r <= SCORE_FLAT_UNTIL:
        v = 100.0
    else:
        v = 100.0 * math.exp(-0.5 * ((r - SCORE_FLAT_UNTIL) / SCORE_WIDTH) ** 2)
    return max(v, SCORE_RUNNABLE_FLOOR)


def score_components(e: Estimate, system: SystemInfo | None) -> dict[str, float]:
    """JSON の score_components(#95 から memory_fit だけ)。"""
    return {"memory_fit": round(memory_fit(usage_ratio(e, system), e.fit), 1)}


def total_score(components: dict[str, float]) -> float:
    return round(components["memory_fit"], 1)


def _sum_licenses(m: ModelEntry) -> list[str | None]:
    """ファイルごとのライセンス(重ならないように。不明は None で最後)。ファイルが無ければ一式のもの。"""
    if not m.files:
        return [m.license]
    found = {f.license for f in m.files}
    return sorted((x for x in found if x is not None), key=str) + ([None] if None in found else [])


def license_unknown(m: ModelEntry) -> bool:
    """どれかのファイル(ファイルが無ければ一式)のライセンスが分からないか。既定では出さない(#28)。"""
    return None in _sum_licenses(m)


def license_matches(m: ModelEntry, allowed: Iterable[str] | None) -> bool:
    """すべてのファイルのライセンスが allowed にあるか(不明は合わない)。allowed が None なら絞らない。"""
    if allowed is None:
        return True
    allow = {a.strip().lower() for a in allowed if a.strip()}
    lics = _sum_licenses(m)
    return bool(lics) and all(x is not None and x.lower() in allow for x in lics)


def _how(e: Estimate, vram: float, system: SystemInfo | None = None) -> tuple[str, str]:
    """(段階, 載せ方 full / seq / offload)。estimate.gpu_choice と同じ選び方。逃がさずに GPU で動く行
    (run_mode が gpu)は全部載せ・順に載せ替えから選ぶ(逃がす段階はメインメモリにもよるので、
    判定で選ばなかった逃がす載せ方をここで選び直さない。#108)。"""
    if e.run_mode == "offload":
        return e.fit, "offload"
    allow = not shared_memory(system) and e.run_mode != "gpu"
    # 実測で段階を決めた行は載せたままの量で全部載せを外さない(#102。estimate と同じ)
    resident = (
        None if getattr(e, "fit_from", "estimate") == "measurement" else getattr(e, "vram_resident_gb", None)
    )
    return gpu_choice(
        e.vram_gb, e.vram_sequential_gb, e.vram_offload_gb, vram, allow_offload=allow, resident=resident
    )


def level_cap(e: Estimate, system: SystemInfo | None = None) -> str | None:
    """動かし方で段階に上限があるとき、その動かし方(JSON の level_cap)。sequential(GPU~、good まで)/
    offload(GPU+RAM、good まで)/ cpu(生成は marginal まで、CPU で実時間に動くと
    作者が書いている読み上げ(estimate.CPU_REALTIME_TTS)・1 回の計算の道具は good まで。estimate.cpu_top)。
    全部 GPU に載る・動かない・判定できないときは None。"""
    label = mode_label(e, system)
    return {"GPU~": "sequential", "GPU+RAM": "offload", "CPU": "cpu"}.get(label)


def mode_label(e: Estimate, system: SystemInfo | None = None) -> str:
    """表の Mode と JSON の run_mode_label。GPU / GPU~(部品を順に載せ替え)/ GPU+RAM / CPU /
    -(動かない = 当てはまらない)/ ?(大きさが分からず判定していない = 分からない。#30)。"""
    if e.unknown_size:
        return "?"
    if e.run_mode == "none":
        return "-"
    if e.run_mode == "cpu":
        return "CPU"
    if e.run_mode == "offload":
        return "GPU+RAM"
    if getattr(e, "fit_from", "estimate") == "measurement":
        return "GPU"  # 同じ設定で成功した実測で決めた(#26)
    v = system.vram_gb if system is not None else 0.0
    return "GPU~" if v and _how(e, v, system)[1] == "seq" else "GPU"


def _ram_short(e: Estimate, system: SystemInfo | None) -> bool:
    return bool(system is not None and system.ram_total_gb and e.ram_gb > system.ram_total_gb)


def vram_need(e: Estimate, system: SystemInfo | None) -> float | None:
    """判定の根拠にした VRAM(GiB)。CPU・大きさ不明・GPU が無いときは None。"""
    vram = system.vram_gb if system is not None else 0.0
    if e.unknown_size or e.run_mode == "cpu" or vram <= 0:
        return None
    if getattr(e, "fit_from", "estimate") == "measurement" and e.fit != "too_tight":
        # 段階に使った量(#95: max(実測のピーク + 文脈の分, 重み + 固定分))。古い Estimate はピーク
        level_gb = getattr(e, "measured_level_vram_gb", None) or e.measured_peak_vram_gb
        if level_gb:
            return level_gb
    level, how = _how(e, vram, system)
    pick = {"full": e.vram_gb, "seq": e.vram_sequential_gb or e.vram_gb, "offload": e.vram_offload_gb}
    if e.fit == "too_tight":
        # VRAM は足りていてメモリが理由なら、VRAM の側は判定に使った載せ方の値を出す
        if _ram_short(e, system) and level != "too_tight":
            return pick[how]
        return e.vram_offload_gb  # 動かないときは「最低これだけ要る」を出す
    if e.run_mode == "offload":
        return e.vram_offload_gb
    return pick[how]


def gpu_need(e: Estimate, system: SystemInfo | None) -> float | None:
    """GPU で動かすのに要る VRAM(GiB)。run_mode が cpu のときも出す(0.7.1、#58)。
    GPU を判定に使っていない・大きさ不明のときは None。cpu 以外は vram_need と同じ値。
    cpu のときは GPU に載らなかったので、動かし方のうち一番小さい値(動かない判定と同じ vram_offload_gb)。
    共有メモリの GPU でも同じ規則(cpu と too_tight で意味を変えない)。"""
    vram = system.vram_gb if system is not None else 0.0
    if e.unknown_size or vram <= 0:
        return None
    if e.run_mode != "cpu":
        return vram_need(e, system)
    return e.vram_offload_gb


def memory_use(e: Estimate, system: SystemInfo | None) -> tuple[str, float | None, float | None]:
    """(種類, 要る GiB, ある GiB)。GPU で動かすなら VRAM、CPU で動くならメインメモリ。
    GPU が要るのに無いときは VRAM の最低限と 0。"""
    has_gpu = system is not None and system.vram_gb > 0
    if e.unknown_size:  # 要る量は分からないが、ある量は分かる(#30)
        if has_gpu:
            return "vram", None, system.vram_gb
        return "ram", None, system.ram_total_gb if system is not None else None
    if e.run_mode == "cpu":
        return "ram", e.ram_gb, system.ram_total_gb if system is not None else None
    need = vram_need(e, system)
    if need is None:  # GPU が要るのに無い
        return "vram", e.vram_offload_gb, 0.0
    return "vram", need, system.vram_gb if system is not None else None


def usage_ratio(e: Estimate, system: SystemInfo | None) -> float | None:
    """その動かし方で一番きつい資源の使用率 r(丸める前。表の Mem% と JSON の utilization_pct。
    docs/scoring.md「Score」)。
    GPU / GPU~ / 実測で段階を決めた行 = 判定に使った VRAM ÷ VRAM(memory_use)、
    CPU = 要るメモリ ÷ メインメモリ、
    GPU+RAM = max(逃がしたときの VRAM ÷ VRAM, 要るメモリ ÷ メインメモリ)。メインメモリが足りない行は
    どの動かし方でも max(上の値, 要るメモリ ÷ メインメモリ)。大きさ不明・割る相手が無いときは None。"""
    if e.unknown_size:
        return None
    _kind, need, avail = memory_use(e, system)
    r = need / avail if need is not None and avail else None
    ram_total = system.ram_total_gb if system is not None else 0.0
    if ram_total and (e.run_mode == "offload" or _ram_short(e, system)):
        ram_r = e.ram_gb / ram_total
        r = ram_r if r is None else max(r, ram_r)
    return r


@dataclass
class Fit:
    model: ModelEntry
    estimate: Estimate
    score: float = 0.0
    gate: dict[str, Any] | None = None  # 自動でおすすめに出せるか(#3 の gate.evaluate の結果)
    score_components: dict[str, float] = field(default_factory=dict)
    system: SystemInfo | None = field(default=None, repr=False, compare=False)
    group_key: str = ""  # 同じモデル(大きさまで同じ)の鍵(groups.group_key)
    use: str = ""  # 表に出す用途(tasks.USES の名前)
    model_name: str = ""  # モデルの表示名(groups.ModelIndex。一覧の中で重ならない)
    workflows: list[dict[str, Any]] = field(default_factory=list)  # 同じモデルのほかの項目
    variants: list[dict[str, Any]] = field(default_factory=list)  # 精度ごとの一番良い項目
    popularity: float = 0.0  # 人気の順位(0〜100。モデルの値。popularity.py。並びの鍵だけに使う)

    @property
    def auto(self) -> bool:
        return bool(self.gate and self.gate.get("auto"))

    @property
    def run_mode_label(self) -> str:
        return mode_label(self.estimate, self.system)

    def to_dict(self, full: bool = False) -> dict[str, Any]:
        """JSON の 1 件(CLI の recommend / fit / search の models と同じ形。docs/schema.md)。"""
        m, e = self.model, self.estimate
        kind, need, avail = memory_use(e, self.system)
        r = usage_ratio(e, self.system)
        util = round(100 * r, 1) if r is not None else None
        gneed = gpu_need(e, self.system)
        d: dict[str, Any] = {
            "id": m.id,
            "name": m.name,
            "model_name": self.model_name,
            "modality": m.modality,
            "tasks": m.tasks,
            "content": m.content,
            "source": m.source,
            "runtime": m.runtime,
            "languages": {k: list(v) for k, v in m.languages.items()},
            "description": dict(m.description),
            "fit_level": e.fit,
            "fit_label": FIT_LABEL[e.fit],
            "run_mode": e.run_mode,
            "run_mode_label": self.run_mode_label,
            "level_cap": level_cap(e, self.system),
            "score": round(self.score, 1),
            "score_components": dict(self.score_components),
            "popularity": round(self.popularity, 1),
            "memory_kind": kind,
            "memory_required_gb": round(need, 2) if need is not None else None,
            "memory_available_gb": round(avail, 2) if avail is not None else None,
            "utilization_pct": util,
            "gpu_memory_required_gb": round(gneed, 2) if gneed is not None else None,
            "ram_required_gb": None if e.unknown_size else e.ram_gb,
            "disk_size_gb": e.disk_gb,
            "total_size_gb": round((m.total_size_bytes or 0) / GIB, 2) or None,
            "license": m.license,
            "licenses": _sum_licenses(m),
            "auto": self.auto,
            "community": is_community(m),
            "held_reasons": list((self.gate or {}).get("held_by", [])),
            "estimate_confidence": e.basis,
            "notes": list(e.reasons),
            "files_count": len(m.files),
            "use": self.use,
            "precision": m.precision,
            "precision_source": m.precision_source,
            "group_key": self.group_key,
            "variants": [dict(v) for v in self.variants],
            "workflows": [dict(w) for w in self.workflows],
            "estimate": e.to_dict(),
            "gate": self.gate,
        }
        if full:
            d["model"] = m.to_dict()
        return d


# --fit / fit=: 出す段階(llmfit の画面の All / Runnable / Perfect / Good / Marginal にならう。0.1.1)
FIT_FILTERS: dict[str, tuple[str, ...]] = {
    "perfect": ("perfect",),
    "good": ("good",),
    "marginal": ("marginal",),
    "runnable": ("perfect", "good", "marginal"),
    "all": (*FIT_LEVELS, FIT_UNKNOWN),
}
# --sort / sort=: 種類ごとの表の中の並べ方(0.1.1)
SORTS = ("type", "score", "vram", "disk", "name")


def check_filters(
    *,
    modality: str | Iterable[str] | None = None,
    content: str = DEFAULT_CONTENT,
    min_fit: str | None = None,
    fit: str | None = None,
    sort: str = "score",
) -> tuple[str, ...] | None:
    """絞り込みの値を確かめる。知らない値は ValueError(組み込み側が誤りに気づけるように)。
    modality は 1 つ・カンマ区切り・並びのどれでもよく、確かめた並びを返す。"""
    mods = parse_modalities(modality)
    if content not in CONTENT_FILTERS:
        raise ValueError(_bad("content", content, CONTENT_FILTERS))
    if min_fit is not None and min_fit not in FIT_LEVELS:
        raise ValueError(_bad("min_fit", min_fit, FIT_LEVELS))
    if fit is not None and fit not in FIT_FILTERS:
        raise ValueError(_bad("fit", fit, FIT_FILTERS))
    if fit is not None and min_fit is not None:
        raise ValueError(
            tr("fit と min_fit は同時に指定できません", "fit and min_fit cannot be given together")
        )
    if sort not in SORTS:
        raise ValueError(_bad("sort", sort, SORTS))
    return mods


def _bad(name: str, value: object, allowed: Iterable[str]) -> str:
    ok = ", ".join(allowed)
    return tr(
        f"{name} が正しくありません: {value!r}(使えるのは {ok})", f"invalid {name}: {value!r} (allowed: {ok})"
    )


def _license_list(license: str | Iterable[str] | None) -> list[str] | None:
    """--license / license=: カンマ区切りの文字列か並び。空なら ValueError。"""
    if license is None:
        return None
    items = license.split(",") if isinstance(license, str) else list(license)
    out = [x.strip().lower() for x in items if x and x.strip()]
    if not out:
        raise ValueError(tr("license が空です(例: apache-2.0,mit)", "license is empty (e.g. apache-2.0,mit)"))
    return out


def _check_limit(limit: int | None, name: str = "limit") -> None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError(
            tr(
                f"{name} は 1 以上の整数か None です: {limit!r}",
                f"{name} must be an integer >= 1 or None: {limit!r}",
            )
        )


def _runs(f: Fit) -> int:
    """並びの鍵 1: 動くもの 0 → 判定できない 1 → 動かない 2(表・--json・ライブラリで同じ。#95)。"""
    return {FIT_UNKNOWN: 1, "too_tight": 2}.get(f.estimate.fit, 0)


# Marginal の中の動かし方の順(速い順。docs/scoring.md「並び」)
MODE_ORDER = {"GPU": 0, "GPU~": 1, "GPU+RAM": 2, "CPU": 3}


def _tier(f: Fit) -> tuple[int, int]:
    """並びの鍵 2・3: 逃がさずに載る(perfect / good で GPU・GPU~)→ marginal。marginal の中だけ動かし方の
    速い順(GPU → GPU~ → GPU+RAM → CPU)。逃がさずに載る中では動かし方を使わない(24 GB の Z-Image で
    bf16 の good・GPU~ を代表にした行が沈まないように。#28)。"""
    if runs_clean(f.estimate):
        return (0, 0)
    mode = MODE_ORDER.get(mode_label(f.estimate, f.system), len(MODE_ORDER))
    return (fit_rank(f.estimate.fit), mode if f.estimate.fit == "marginal" else 0)


def sort_key(f: Fit) -> tuple:
    """1 項目ずつの並び(evaluate。variant_key の最後の鍵): 動く → 判定できない → 動かない → auto → 段階 →
    公式(利用者のテンプレートは後)→ 人気の順位 → Score → ID。"""
    return (
        _runs(f),
        not f.auto,
        fit_rank(f.estimate.fit),
        is_community(f.model),
        -round(f.popularity, 1),
        -round(f.score, 1),
        f.model.id,
    )


# 表の中の段階の順(判定できないものは too_tight より前。動かないと分かったものを一番後ろに)
LIST_LEVEL_ORDER = {"perfect": 0, "good": 1, "marginal": 2, FIT_UNKNOWN: 3, "too_tight": 4}


def list_key(f: Fit, sort: str = "score", auto_first: bool = True, verified_first: bool = True) -> tuple:
    """表の並べ方(fit / search の --sort、recommend。docs/scoring.md「並び」)。鍵 1 は どれも 動く →
    判定できない → 動かない。

    auto_first=True(recommend)なら sort は使わず: 鍵 1 →(verified_first なら自動のおすすめに出せるもの)→
        段階(逃がさずに載る → marginal。marginal の中は動かし方の速い順)→ 公式 → 人気の順位 → Score → ID。
        verified_first=False(CLI の recommend の表。#84)なら自動のおすすめに出せるかを並びに使わない。
        ライブラリと --json は True(使う側のアプリが人の確認なしにおすすめを出すため)
    type(fit / search の既定): 種類(image → video → music → sfx → tts → 3d)→ 段階(perfect → good →
        marginal → 判定できない → too_tight)→ 公式 → 人気の順位 → Score → ID
    score: 種類をまたいだ 1 つの順位。鍵 1 → 段階(逃がさずに載る / marginal)→ Score → 人気の順位 → ID
    vram: 判定に使ったメモリ(表の VRAM)の少ない順 / disk: ダウンロードの小さい順 / name: 名前の順
        (どれも鍵 1 で分けてから)"""
    level = LIST_LEVEL_ORDER.get(f.estimate.fit, 4)
    score = -round(f.score, 1)
    pop = -round(f.popularity, 1)
    name = (f.model_name or f.model.name or f.model.id).lower()
    if auto_first:
        verified = (not f.auto,) if verified_first else ()
        return (_runs(f), *verified, *_tier(f), is_community(f.model), pop, score, f.model.id)
    if sort == "type":
        mod = MODALITIES.index(f.model.modality) if f.model.modality in MODALITIES else len(MODALITIES)
        return (mod, level, is_community(f.model), pop, score, f.model.id)
    if sort == "score":
        return (_runs(f), 0 if runs_clean(f.estimate) else 1, score, pop, f.model.id)
    if sort == "vram":
        _kind, need, _avail = memory_use(f.estimate, f.system)
        rest: tuple = (need is None, need or 0.0, level, score)
    elif sort == "disk":
        d = f.estimate.disk_gb
        rest = (d is None, d or 0.0, level, score)
    else:  # name
        rest = ()
    return (_runs(f), *rest, name, f.model.id)


# 同じモデルの中で表に出す 1 件を選ぶときの用途の順(生成を先に。補助ツール・高解像度化は最後)
REP_USE_ORDER = ("gen", "edit", "i2v", "lipsync", "motion", "voice", "upscale", "tool")


# 「逃がさずに GPU で動く」とみなす段階(この中では精度の高いものを選ぶ。#28)
CLEAN_LEVELS = ("perfect", "good")


def runs_clean(e: Estimate) -> bool:
    """GPU に逃がさずに載る(GPU か GPU~ で good 以上)か。"""
    return e.fit in CLEAN_LEVELS and e.run_mode == "gpu"


def variant_key(f: Fit) -> tuple:
    """同じモデルの中で表に出す 1 件を選ぶ順: 用途(--task があればそれに合う用途の中で、生成 → 編集 → …
    → 高解像度化 → 補助ツール)→ GPU に逃がさずに載る(good 以上で GPU / GPU~)ものがあれば、その中で
    精度が高い → 段階が良い → … / 無ければ 段階が良い → メインメモリに収まる → 逃がさない載せ方
    (GPU → GPU+RAM → CPU)→ 精度が高い → おすすめ順。

    llmfit と同じく「逃がさずに載る中で一番品質の高い精度」を選ぶ(#28。24 GB の Z-Image Turbo は int8 の
    perfect より bf16 の good)。逃がさないと載らないときだけ段階を優先する。"""
    use = REP_USE_ORDER.index(f.use) if f.use in REP_USE_ORDER else len(REP_USE_ORDER)
    # 利用者のテンプレートは、公式のワークフローがあれば代表にしない
    community = is_community(f.model)
    slow = {"gpu": 0, "offload": 1, "cpu": 2}.get(f.estimate.run_mode, 3)  # 同じ段階なら逃がさないほう(速い)
    clean = runs_clean(f.estimate)
    prec = precision_rank(f.model.precision)
    # 動く行どうしで同じ段階なら、メインメモリの足りない量(GB を切り上げた整数。0.1 GB 程度の差で精度を
    # 落とさない)が小さいほう(収まらないとページファイルを使い、とても遅くなる)。両方収まれば 0 で差は無い。
    # 動かない行(too_tight)の代表は変えない。2026-11-02(#166): 動的 VRAM の式で 8・6 GB の bf16 も
    # 逃がして動く判定になり、RAM 16 GB でも bf16 を選んでいた
    ram_short = 0
    if f.system is not None and f.system.ram_total_gb and f.estimate.fit != "too_tight":
        ram_short = math.ceil(round(max(0.0, f.estimate.ram_gb - f.system.ram_total_gb), 2))
    return (
        community,
        use,
        not clean,
        prec if clean else 0,
        fit_rank(f.estimate.fit),
        ram_short,
        slow,
        prec,
        *sort_key(f),
    )


def _gate(m: ModelEntry, policy: GatePolicy | None, db: Database) -> dict[str, Any]:
    from .gate import evaluate_offline

    return evaluate_offline(m, policy, sources=db.sources).to_dict()


def judge(
    m: ModelEntry,
    system: SystemInfo,
    db: Database,
    *,
    settings: Settings | None = None,
    coefficients: Coefficients | None = None,
    with_limits: bool = False,
    policy: GatePolicy | None = None,
) -> Fit:
    """1 モデルの判定・gate・点数。"""
    from .popularity import for_db

    c = coefficients or load_coefficients()
    e = estimate(m, system, settings, db.measurements, c, with_limits=with_limits)
    g = _gate(m, policy, db)
    comps = score_components(e, system)
    idx = index_for(db)
    key = idx.key_of.get(m.id) or group_key(m)
    name = idx.name_of.get(key) or m.name
    return Fit(
        m,
        e,
        total_score(comps),
        g,
        comps,
        system,
        group_key=key,
        use=main_use(m),
        model_name=name,
        popularity=for_db(db).get(key, 0.0),
    )


def evaluate(
    db: Database,
    system: SystemInfo,
    *,
    modality: str | Iterable[str] | None = None,
    content: str = DEFAULT_CONTENT,
    settings: Settings | None = None,
    coefficients: Coefficients | None = None,
    models: Iterable[ModelEntry] | None = None,
    with_limits: bool = False,
    policy: GatePolicy | None = None,
) -> list[Fit]:
    """種類と内容で絞って全部を判定し、おすすめ順に並べる(too_tight も含む。まとめない)。"""
    mods = check_filters(modality=modality, content=content)
    c = coefficients or load_coefficients()
    out = [
        judge(m, system, db, settings=settings, coefficients=c, with_limits=with_limits, policy=policy)
        for m in (models if models is not None else db.models)
        if (not mods or m.modality in mods) and (content == "all" or m.content == content)
    ]
    out.sort(key=sort_key)
    return out


def matches_query(m: ModelEntry, query: str) -> bool:
    q = query.strip().lower()
    return q in m.id.lower() or q in m.name.lower()


@dataclass
class Selection:
    """絞り込みの結果。

    matched: 件数(limit・per_modality)で切る前の行の数(まとめたときはモデルの数)
    hidden: 外した理由ごとの件数。1 件は最初に当たった理由で 1 回だけ数える。
        matched + hidden の合計 = 調べた項目の数(search は語に合った項目の数、それ以外は一覧の全部)
    groups: 種類ごとの {modality, matched, shown}(MODALITIES の順。0 件の種類も入る)
    """

    fits: list[Fit]
    matched: int
    hidden: dict[str, int]
    groups: list[dict[str, Any]] = field(default_factory=list)
    considered: int = 0  # 調べた項目の数


# hidden のキー(数える順)。docs/schema.md
HIDDEN_KEYS = (
    "modality",
    "uncensored",
    "sfw",
    "tools",
    "task",
    "license",
    "license_unknown",
    "size_unknown",
    "too_tight",
    "below_min_fit",
    "other_fit",
    "held",
    "same_model",
)


def _member_dict(f: Fit, matched: bool) -> dict[str, Any]:
    _, need, _ = memory_use(f.estimate, f.system)
    return {
        "id": f.model.id,
        "name": f.model.name,
        "tasks": list(f.model.tasks),
        "use": f.use,
        "precision": f.model.precision,
        "precision_source": f.model.precision_source,
        "fit_level": f.estimate.fit,
        "memory_required_gb": round(need, 2) if need is not None else None,
        "disk_size_gb": f.estimate.disk_gb,
        "auto": f.auto,
        "matched": matched,
    }


def select(
    db: Database,
    system: SystemInfo,
    *,
    query: str | None = None,
    modality: str | Iterable[str] | None = None,
    content: str = DEFAULT_CONTENT,
    min_fit: str | None = None,
    fit: str | None = None,
    license: str | Iterable[str] | None = None,
    task: str | Iterable[str] | None = None,
    include_tools: bool = True,
    include_unknown_license: bool = False,
    auto_only: bool = False,
    drop_unknown_size: bool = False,
    group: bool = True,
    limit: int | None = None,
    per_modality: int | None = None,
    sort: str = "score",
    auto_first: bool = True,
    verified_first: bool = True,
    settings: Settings | None = None,
    coefficients: Coefficients | None = None,
    with_limits: bool = False,
    policy: GatePolicy | None = None,
) -> Selection:
    """recommend / fit / search の共通の絞り込み。

    group=True なら同じモデル(groups.group_key: 大きさまで同じで、精度か使い方だけが違う)を 1 行にまとめ、
    段階が良く精度が高いものを出す(variant_key)。per_modality を渡すと種類ごとに上位その数だけ、
    種類の順(MODALITIES)に並べる。query に合わないものは数えない(hidden にも入れない)。
    どれかのファイルのライセンスが分からないものは、include_unknown_license=True でなければ出さない
    (hidden の license_unknown。#28)。

    出す段階: fit(FIT_FILTERS の名前。runnable = perfect / good / marginal、all = 判定できないものも)か、
    min_fit(その段階以上。判定できないものは min_fit が too_tight のときだけ)。どちらも無ければ全部
    (判定できないものも)。drop_unknown_size=True なら判定できないものは必ず外す。
    並べ方は list_key(sort、auto_first。auto_first=True のときは sort を使わず recommend の順)。
    verified_first=False は CLI の recommend の表だけが使う(自動のおすすめを先にしない。#84)。
    件数(limit・per_modality)はこの並びで切る。
    """
    mods = check_filters(modality=modality, content=content, min_fit=min_fit, fit=fit, sort=sort)
    allow = _license_list(license)
    _check_limit(limit)
    _check_limit(per_modality, "per_modality")
    if query is not None and not query.strip():
        raise ValueError(tr("query が空です", "query is empty"))
    codes = task_codes(task.split(",") if isinstance(task, str) else task) if task is not None else None
    if codes and codes & TOOL_TASKS:
        include_tools = True  # 補助ツールの用途を指定したら、補助ツールを外さない
    hidden = dict.fromkeys(HIDDEN_KEYS, 0)
    c = coefficients or load_coefficients()
    if fit is not None:
        allowed: tuple[str, ...] = FIT_FILTERS[fit]
    elif min_fit is not None:
        allowed = FIT_LEVELS[: FIT_LEVELS.index(min_fit) + 1]
        if min_fit == "too_tight":
            allowed = (*allowed, FIT_UNKNOWN)
    else:
        allowed = (*FIT_LEVELS, FIT_UNKNOWN)
    if drop_unknown_size:
        allowed = tuple(x for x in allowed if x != FIT_UNKNOWN)
    judged: dict[str, Fit] = {}

    def get(m: ModelEntry) -> Fit:
        if m.id not in judged:
            judged[m.id] = judge(
                m, system, db, settings=settings, coefficients=c, with_limits=with_limits, policy=policy
            )
        return judged[m.id]

    considered = 0
    passed: list[Fit] = []
    for m in db.models:
        if query is not None and not matches_query(m, query):
            continue
        considered += 1
        if mods and m.modality not in mods:
            hidden["modality"] += 1
            continue
        if content != "all" and m.content != content:
            hidden[m.content] = hidden.get(m.content, 0) + 1
            continue
        if not include_tools and is_tool(m):
            hidden["tools"] += 1
            continue
        if codes is not None and not codes & set(m.tasks):
            hidden["task"] += 1
            continue
        if not license_matches(m, allow):
            hidden["license"] += 1
            continue
        if not include_unknown_license and license_unknown(m):
            hidden["license_unknown"] += 1
            continue
        f = get(m)
        e = f.estimate
        if e.fit not in allowed:
            if e.unknown_size:
                hidden["size_unknown"] += 1
            elif e.fit == "too_tight":
                hidden["too_tight"] += 1
            else:
                hidden["below_min_fit" if fit is None else "other_fit"] += 1
            continue
        if auto_only and not f.auto:
            hidden["held"] += 1
            continue
        if codes:
            f.use = main_use(m, codes)
        passed.append(f)

    # 同じモデル(group_key)の仲間。表に出さない仲間も workflows に入れる
    members = index_for(db).members
    passed_ids = {f.model.id for f in passed}
    by_key: dict[str, list[Fit]] = {}
    for f in passed:
        by_key.setdefault(f.group_key, []).append(f)
    rows: list[Fit] = []
    for key, fits in by_key.items():
        fits.sort(key=variant_key)
        reps = fits[:1] if group else fits
        hidden["same_model"] += len(fits) - len(reps)
        if group:
            # 行の値はすべて代表のもの(Score も。表の Mem% と合う。#95 で「仲間の一番高い点」をやめた)
            reps = [replace(reps[0])]
        for rep in reps:
            others = [get(m) for m in members[key] if m.id != rep.model.id]
            others.sort(key=lambda x: (x.model.id not in passed_ids, *variant_key(x)))
            rep.workflows = [_member_dict(o, o.model.id in passed_ids) for o in others]
            best: dict[str | None, Fit] = {rep.model.precision: rep}
            for v in fits:  # 精度ごとの一番良いもの(今の絞り込みに合うものから)
                best.setdefault(v.model.precision, v)
            rep.variants = [
                {
                    "precision": v.model.precision,
                    "workflow_id": v.model.id,
                    "memory_required_gb": _member_dict(v, True)["memory_required_gb"],
                    "fit_level": v.estimate.fit,
                    "auto": v.auto,
                    "disk_size_gb": v.estimate.disk_gb,
                }
                for v in sorted(best.values(), key=lambda v: precision_rank(v.model.precision))
            ]
        rows += reps
    rows.sort(key=lambda f: list_key(f, sort, auto_first, verified_first))
    matched = len(rows)
    counts = {mod: sum(1 for f in rows if f.model.modality == mod) for mod in MODALITIES}
    if per_modality:
        shown = [f for mod in MODALITIES for f in [r for r in rows if r.model.modality == mod][:per_modality]]
    else:
        shown = rows[:limit] if limit else rows
    groups = [
        {"modality": mod, "matched": counts[mod], "shown": sum(1 for f in shown if f.model.modality == mod)}
        for mod in MODALITIES
        if not mods or mod in mods
    ]
    return Selection(shown, matched, {k: v for k, v in hidden.items() if v}, groups, considered)


def recommend(
    db: Database,
    system: SystemInfo,
    *,
    modality: str | Iterable[str] | None = None,
    content: str = DEFAULT_CONTENT,
    min_fit: str = "marginal",
    license: str | Iterable[str] | None = None,
    task: str | Iterable[str] | None = None,
    include_tools: bool = False,
    include_unknown_license: bool = False,
    auto_only: bool = False,
    group: bool = True,
    limit: int | None = 10,
    per_modality: int | None = None,
    settings: Settings | None = None,
    coefficients: Coefficients | None = None,
    with_limits: bool = False,
    policy: GatePolicy | None = None,
) -> list[Fit]:
    """おすすめ順(大きさ・ライセンスが分からないものは出さない)。CLI の recommend と同じ既定。"""
    return select(
        db,
        system,
        modality=modality,
        content=content,
        min_fit=min_fit,
        license=license,
        task=task,
        include_tools=include_tools,
        include_unknown_license=include_unknown_license,
        auto_only=auto_only,
        drop_unknown_size=True,
        group=group,
        limit=limit,
        per_modality=per_modality,
        settings=settings,
        coefficients=coefficients,
        with_limits=with_limits,
        policy=policy,
    ).fits
