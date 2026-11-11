# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""実測から見積もりの係数を合わせる。`python -m genmodelfit.calibrate`

合わせるのは「全部載せ」の VRAM(estimate の vram_gb)だけ:
    予測 = overhead_gb + 重みの合計 + 作業用メモリ
作業用メモリは、段ごとに当てられるモデル(#100。working.stage_memory)は ComfyUI の見込み max(S, V_eff, T) で、
係数を持たない。当てられないモデルは今までの種類ごとの式(Σ 係数 × 設定から決まる量)。
#100 から合わせ直すのは 3D の作業用メモリ(act_3d_gb)だけ。固定分と画像・動画・音声の係数は 0.10 で合わせた値に
固定する(FROZEN。固定分は RTX 3070 を測り直してから決める。docs/accuracy.md「合わせ方」)。
「相対誤差の 2 乗和 + 事前値(#1 の仮の値)からのずれへの弱い罰」を、範囲付きの座標降下で最小にする。

- GPU の上限に張り付いた実測(estimate.is_censored)は「予測がこれ以上」という片側の条件としてだけ使う
- 精度は 1 件ずつ外して残りで合わせ、外した 1 件を当てる(leave-one-out)で測る
結果は src/genmodelfit/data/calibration.json に書き、docs/accuracy.md の表を標準出力に出す。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from . import i18n
from .db import Database, load_measurements
from .db import load as load_db
from .estimate import (
    FITTED,
    Coefficients,
    activation_features,
    components,
    formula_need,
    is_censored,
    is_community,
    no_size_input,
    settings_for,
    usable,
    working_only,
)
from .i18n import tr
from .models import Measurement

# 範囲(物理的にありえる幅)と事前値。事前値は #1 の仮の値で、実測を見る前に決めたもの
BOUNDS: dict[str, tuple[float, float]] = {
    "overhead_gb": (0.3, 1.5),
    "act_image_gb_per_mp": (0.0, 4.0),
    "act_video_gb_per_mp": (0.0, 30.0),
    "act_audio_gb_per_min": (0.0, 5.0),
    "act_3d_gb": (0.0, 4.0),
}
# 合わせ直す係数(#100 から 3D だけ)と、固定する係数
# (0.10 の calibration.json の値。2026-10-20 に 9 件で合わせた)
FIT = ("act_3d_gb",)
FROZEN = {
    "overhead_gb": 1.472,
    "act_image_gb_per_mp": 0.2284,
    "act_video_gb_per_mp": 9.8704,
    "act_audio_gb_per_min": 0.0455,
}
assert set(FIT) | set(FROZEN) == set(FITTED)
PRIOR = {k: getattr(Coefficients(), k) for k in FIT}
RIDGE = 1e-4  # 事前値への罰の重み((値 - 事前値) / 事前値 の 2 乗に掛ける)
SWEEPS = 300


@dataclass
class Row:
    """実測 1 件を「予測 = 定数 + Σ 合わせる係数 × 特徴」の形にしたもの。定数 = 固定分 + 重み + 作業用メモリの
    うち合わせない分(段ごとの見込み、または固定した係数 × 特徴)。"""

    m: Measurement
    weights_gb: float
    features: dict[str, float]  # 合わせる係数(FIT)の特徴だけ
    censored: bool
    modality: str = ""
    const_gb: float = 0.0
    work_gb: float = 0.0  # 合わせる前の作業用メモリ(照合で作業用メモリだけの実測と比べる)
    staged: bool = False

    def predict(self, theta: dict[str, float]) -> float:
        return self.const_gb + sum(theta[k] * v for k, v in self.features.items())


# 係数を合わせる実測の測り方。v0.1 は記録の無い RTX 5080 の 9 件(None)で合わせる。
# bench の torch_reserved の実測は docs/accuracy.md で照合として別に示す
CALIBRATION_METRIC: str | None = None


def frozen_coefficients() -> Coefficients:
    """合わせない係数を固定した値にした Coefficients(FIT の係数は事前値のまま。Row は FIT を特徴で持つ)。"""
    return Coefficients(**FROZEN)


def _rows(db: Database, c: Coefficients | None, *, same_metric: bool) -> list[Row]:
    c = c or frozen_coefficients()
    rows = []
    for m in db.measurements:
        e = db.get(m.model_id)
        # OOM・エラー・標準でない設定の実測は使わない(estimate.usable)。ほかの利用者の実測は、見積もりには
        # 1 件から使うが、全モデルに効く係数の合わせ直しと照合には、そろったもの(consistent)だけ入れる
        if e is None or not usable(m) or (is_community(m) and not m.consistent):
            continue
        if not (m.peak_vram_gb or 0) > 0:  # 割り算に使う(0 のピークは測り損ね)
            continue
        if (getattr(m, "vram_metric", None) == CALIBRATION_METRIC) != same_metric:
            continue
        parts = components(e)
        if parts.unknown:
            continue
        s = settings_for(e, m.settings, sized=not no_size_input(e))
        n = formula_need(e, s, c, parts)
        weights = parts.total * c.weight_factor
        if n.stages is not None:
            feats: dict[str, float] = {}
            const = n.full
            work = n.act
        else:
            act = activation_features(e.modality, s, c)
            feats = {k: v for k, v in act.items() if k in FIT}
            work = sum(getattr(c, k) * v for k, v in act.items() if k not in FIT)
            const = c.overhead_gb + weights + work
        rows.append(Row(m, weights, feats, is_censored(m), e.modality, const, work, n.stages is not None))
    return rows


def build_rows(db: Database, c: Coefficients | None = None) -> list[Row]:
    """係数を合わせる実測(測り方が CALIBRATION_METRIC のもの)。ほかの利用者の実測(#34)も、
    測り方が同じでそろっていれば(consistent)入る。"""
    return _rows(db, c, same_metric=True)


def build_check_rows(db: Database, c: Coefficients | None = None) -> list[Row]:
    """照合だけに使う実測(測り方が違うもの。bench の torch_reserved。同梱の RTX 3070 の 2 件と、
    ほかの利用者の実測のまとめ)。全件で合わせた係数で当て、誤差を出す(係数の合わせには入れない)。"""
    return _rows(db, c, same_metric=False)


def _origin(m: Measurement) -> str:
    return getattr(m, "origin", None) or "maintainer"


def check(rows: list[Row], theta: dict[str, float]) -> list[dict]:
    out = []
    for r in rows:
        # 作業用メモリだけの実測(estimate.working_only)は作業用メモリの見積もりと比べる
        only = working_only(r.m)
        pred = r.work_gb + sum(theta[k] * v for k, v in r.features.items()) if only else r.predict(theta)
        y = r.m.peak_vram_gb
        out.append(
            {
                "model_id": r.m.model_id,
                "modality": r.modality,
                "gpu_name": r.m.gpu_name,
                "gpu_vram_gb": r.m.gpu_vram_gb,
                "origin": _origin(r.m),
                "n_submissions": getattr(r.m, "n_submissions", None),
                "vram_metric": r.m.vram_metric,
                "settings": {k: v for k, v in vars(r.m.settings).items() if v is not None and k != "batch"},
                "measured_gb": y,
                "censored": r.censored,
                "quantity": "work" if only else "full",
                "predicted_gb": round(pred, 2),
                "error_pct": round((pred - y) / y * 100, 1),
            }
        )
    return out


def _loss(rows: list[Row], theta: dict[str, float]) -> float:
    total = 0.0
    for r in rows:
        y = r.m.peak_vram_gb
        rel = (r.predict(theta) - y) / y
        if r.censored:
            rel = min(0.0, rel)  # 予測が実測以上なら罰なし
        total += rel * rel
    for k in FIT:
        total += RIDGE * ((theta[k] - PRIOR[k]) / PRIOR[k]) ** 2
    return total


def _argmin_1d(f, lo: float, hi: float, iters: int = 100) -> float:
    """凸な 1 変数関数の最小(黄金分割)。"""
    g = (5**0.5 - 1) / 2
    a, b = lo, hi
    x1, x2 = b - g * (b - a), a + g * (b - a)
    f1, f2 = f(x1), f(x2)
    for _ in range(iters):
        if f1 <= f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - g * (b - a)
            f1 = f(x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + g * (b - a)
            f2 = f(x2)
    return (a + b) / 2


def fit(rows: list[Row]) -> dict[str, float]:
    """範囲付きの座標降下。目的関数は凸(1 次の予測の 2 乗・片側 2 乗・罰の和)なので初期値によらない。"""
    theta = dict(PRIOR)
    for _ in range(SWEEPS):
        before = _loss(rows, theta)
        for k in FIT:
            lo, hi = BOUNDS[k]
            theta[k] = _argmin_1d(lambda v, k=k: _loss(rows, {**theta, k: v}), lo, hi)
        if before - _loss(rows, theta) < 1e-12:
            break
    return {k: round(v, 4) for k, v in theta.items()}


def leave_one_out(rows: list[Row]) -> list[dict]:
    out = []
    full = fit(rows)
    for i, r in enumerate(rows):
        theta = fit(rows[:i] + rows[i + 1 :])
        pred = r.predict(theta)
        y = r.m.peak_vram_gb
        out.append(
            {
                "model_id": r.m.model_id,
                "modality": r.modality,
                "gpu_name": r.m.gpu_name,
                "origin": _origin(r.m),
                "settings": {k: v for k, v in vars(r.m.settings).items() if v is not None and k != "batch"},
                "note": r.m.note,
                "measured_gb": y,
                "censored": r.censored,
                "fit_predicted_gb": round(r.predict(full), 2),
                "loo_predicted_gb": round(pred, 2),
                "loo_error_pct": round((pred - y) / y * 100, 1),
                # 比べる基準: 重み(ファイルの大きさ)だけで当てたときの誤差
                "weights_only_error_pct": round((r.weights_gb - y) / y * 100, 1),
                # 張り付いた実測は「予測 ≥ 実測」なら当たり。ただし重みだけで実測を超えるものは
                # 外れようがない(作業用メモリ・固定分は 0 以上)ので、情報を持たない
                "loo_ok": (pred >= y) if r.censored else None,
                "censored_trivial": bool(r.censored and r.weights_gb >= y),
            }
        )
    return out


def summarize(loo: list[dict], target_pct: float = 20.0) -> dict:
    """LOO の要約。目標の判定(target_met)は、張り付いていない実測が 2 件以上ある種類だけで行う
    (1 件しか無い種類はその 1 件を外すと係数が事前値に戻り、知らないモデルを当てる力を測れない)。
    該当が無ければ None。張り付いた実測は判定に使わない。"""
    pts = [x for x in loo if not x["censored"]]
    errs = [abs(x["loo_error_pct"]) for x in pts]
    base = [abs(x["weights_only_error_pct"]) for x in pts]
    cens = [x for x in loo if x["censored"]]
    by: dict[str, dict] = {}
    for mod in sorted({x["modality"] for x in loo}):
        e = [abs(x["loo_error_pct"]) for x in pts if x["modality"] == mod]
        by[mod] = {
            "n_point": len(e),
            "n_censored": sum(1 for x in cens if x["modality"] == mod),
            "max_abs_pct": round(max(e), 1) if e else None,
            "target_met": (max(e) <= target_pct) if len(e) >= 2 else None,
        }
    ev = [abs(x["loo_error_pct"]) for x in pts if by[x["modality"]]["n_point"] >= 2]
    return {
        "target_abs_pct": target_pct,
        "n_point": len(errs),
        "n_censored": len(cens),
        "mean_abs_pct": round(sum(errs) / len(errs), 1) if errs else None,
        "max_abs_pct": round(max(errs), 1) if errs else None,
        "weights_only_mean_abs_pct": round(sum(base) / len(base), 1) if base else None,
        "censored_ok": sum(1 for x in cens if x["loo_ok"]),
        "censored_trivial": sum(1 for x in cens if x["censored_trivial"]),
        "n_evaluable": len(ev),
        "evaluable_modalities": [k for k, v in by.items() if v["n_point"] >= 2],
        "evaluable_mean_abs_pct": round(sum(ev) / len(ev), 1) if ev else None,
        "evaluable_max_abs_pct": round(max(ev), 1) if ev else None,
        "target_met": (max(ev) <= target_pct) if ev else None,
        "by_modality": by,
    }


def bundled_db() -> tuple[Database, int]:
    """同梱の一覧と同梱の実測だけの Database と、除いたこの PC の bench の実測(キャッシュ)の件数。
    calibration.json はどの PC で作っても同じにする(#100。オーナーの PC のキャッシュの実測が混ざっていた)。"""
    db = load_db(use_cache=False)
    every = len(db.measurements)
    db.measurements = load_measurements(include_cache=False)
    return db, every - len(db.measurements)


def calibrate(db: Database | None = None, date: str | None = None) -> dict:
    db = db or bundled_db()[0]
    rows = build_rows(db)
    theta = fit(rows)
    loo = leave_one_out(rows)
    checked = check(build_check_rows(db), theta)
    fixed = Coefficients()
    return {
        "source": "calibration.json",
        "date": date or _dt.date.today().isoformat(),
        "method": (
            "全部載せの VRAM = overhead + 重み + 作業用メモリ。作業用メモリは段ごとに当てられるモデルは"
            " ComfyUI の見込み max(S, V_eff, T)(係数なし)、ほかは種類ごとの式。"
            "合わせるのは act_3d_gb だけで、"
            "固定分と画像・動画・音声の係数は 0.10 の値に固定(frozen)。相対誤差の 2 乗和"
            "(張り付いた実測は片側)"
            f" + 事前値への罰(重み {RIDGE})を、範囲付きの座標降下で最小化。精度は leave-one-out"
        ),
        "n": len(rows),
        "n_censored": sum(r.censored for r in rows),
        "n_staged": sum(r.staged for r in rows),
        "coefficients": {**FROZEN, **theta},
        "frozen": dict(FROZEN),
        "prior": PRIOR,
        "bounds": {k: list(BOUNDS[k]) for k in FIT},
        "fixed": {
            k: getattr(fixed, k)
            for k in (
                "weight_factor",
                "act_video_ref_frames",
                "act_video_frame_exponent",
                "offload_fraction",
                "ram_extra_gb",
                "good_margin",
            )
        },
        "loo": loo,
        "summary": summarize(loo),
        "check": checked,
    }


def markdown_table(result: dict) -> str:
    lines = [
        "| モデル | 設定 | 実測 GB | 全件で合わせた予測 GB | LOO 予測 GB | LOO 誤差 | 重みだけの誤差 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for x in result["loo"]:
        s = x["settings"]
        bits = []
        if s.get("width"):
            bits.append(f"{s['width']}x{s['height']}")
        if s.get("frames"):
            bits.append(f"{s['frames']} コマ")
        if s.get("seconds"):
            bits.append(f"{s['seconds']:g} 秒")
        if "文章から" in (x.get("note") or ""):
            bits.append("文章から")
        if x["censored"] and x.get("censored_trivial"):
            err = "張り付き(重みだけで実測を超えるので情報なし)"
        elif x["censored"]:
            err = f"張り付き({'≥ 実測で当たり' if x['loo_ok'] else '実測より小さい: 外れ'})"
        else:
            err = f"{x['loo_error_pct']:+.1f}%"
        lines.append(
            f"| {x['model_id']} | {'・'.join(bits) or '-'} | {x['measured_gb']:.1f} |"
            f" {x['fit_predicted_gb']:.2f} | {x['loo_predicted_gb']:.2f} | {err} |"
            f" {x['weights_only_error_pct']:+.1f}% |"
        )
    sm = result["summary"]
    lines.append("")
    by = sm["by_modality"]
    ev = "・".join(f"{k} {by[k]['n_point']}" for k in sm["evaluable_modalities"])
    one = "・".join(k for k, v in by.items() if v["n_point"] < 2)
    if sm["n_evaluable"]:
        lines.append(
            f"評価できたのは {sm['n_evaluable']} 件({ev})、LOO の最大誤差 {sm['evaluable_max_abs_pct']}%"
            f"(平均 {sm['evaluable_mean_abs_pct']}%)。目標 ±{sm['target_abs_pct']:g}% の範囲に"
            f"{'入った' if sm['target_met'] else '入らなかった'}。"
        )
    else:
        lines.append("評価できた実測はない(種類ごとに張り付いていない実測が 2 件以上要る)。")
    if one:
        lines.append(f"{one} は張り付いていない実測が 1 件以下のため評価できない(target_met は null)。")
    lines.append(
        f"張り付いた {sm['n_censored']} 件のうち {sm['censored_trivial']} 件は"
        "重みだけで実測を超え、情報を持たない。"
        f" 参考: 張り付いていない全 {sm['n_point']} 件の LOO 平均絶対誤差 {sm['mean_abs_pct']}%、"
        f"最大 {sm['max_abs_pct']}%(重みだけなら平均 {sm['weights_only_mean_abs_pct']}%)"
    )
    if result.get("check"):
        lines += ["", *check_table(result["check"])]
    return "\n".join(lines)


def _settings_text(s: dict) -> str:
    bits = []
    if s.get("width"):
        bits.append(f"{s['width']}x{s['height']}")
    if s.get("frames"):
        bits.append(tr(f"{s['frames']} コマ", f"{s['frames']} frames"))
    if s.get("seconds"):
        bits.append(tr(f"{s['seconds']:g} 秒", f"{s['seconds']:g} s"))
    return tr("・", ", ").join(bits) or "-"


def check_table(rows: list[dict]) -> list[str]:
    """照合の表(係数の合わせに使わない測り方の実測を、全件で合わせた係数で当てたもの)。
    提出から来る文字(モデル ID・GPU 名)は md_cell で無害にする。
    文は表示の言語で出す(contrib aggregate --report。calibrate の main は
    docs/accuracy.ja.md 用に ja に固める)。"""
    from .contrib import md_cell

    lines = [
        tr(
            "照合(測り方が違うので係数の合わせには使わない。全件で合わせた係数で当てた値):",
            "Cross-check (measured differently, so not used for fitting;"
            " predicted with the coefficients fitted on all):",
        ),
        "",
        tr(
            "| モデル | GPU | 出どころ | 設定 | 比べた量 | 実測 GiB | 推定 GiB | 誤差 |",
            "| Model | GPU | Source | Settings | Compared | Measured GiB | Estimated GiB | Error |",
        ),
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for x in rows:
        community = x["origin"] == "community"
        who = tr("ほかの利用者", "other users") if community else tr("メンテナ", "maintainer")
        if x["origin"] == "community" and x.get("n_submissions"):
            who += tr(f"({x['n_submissions']} 件)", f" ({x['n_submissions']})")
        err = tr("張り付き", "censored") if x["censored"] else f"{x['error_pct']:+.0f}%"
        what = (
            tr("作業用メモリ(重みを含まない実測)", "working memory (measurement without weights)")
            if x.get("quantity") == "work"
            else tr("全部載せ", "all on GPU")
        )
        lines.append(
            f"| {md_cell(x['model_id'])} | {md_cell(x['gpu_name'])} | {who} |"
            f" {_settings_text(x['settings'])} | {what} |"
            f" {x['measured_gb']:.2f} | {x['predicted_gb']:.2f} | {err} |"
        )
    return lines


def default_output() -> Path:
    return Path(__file__).parent / "data" / "calibration.json"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m genmodelfit.calibrate", description=__doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=default_output(), help="書き出し先")
    p.add_argument("--dry-run", action="store_true", help="書き出さずに表だけ出す")
    p.add_argument("--date", help="記録する日付(既定は今日)")
    args = p.parse_args(argv)
    # 出す表は docs/accuracy.ja.md にそのまま貼るので、表示の言語によらず日本語にする(#73)
    with i18n.forced("ja"):
        return _main(args)


def _main(args: argparse.Namespace) -> int:
    db, excluded = bundled_db()
    if excluded:
        print(f"この PC の bench の実測(キャッシュ){excluded} 件は使わない(同梱の実測だけで合わせる)")
    result = calibrate(db, date=args.date)
    if not args.dry_run:
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"wrote {args.output}")
    print(json.dumps(result["coefficients"], ensure_ascii=False))
    print(markdown_table(result))
    return 0


__all__ = [
    "Row",
    "build_check_rows",
    "build_rows",
    "calibrate",
    "check",
    "check_table",
    "fit",
    "leave_one_out",
    "main",
    "markdown_table",
]

if __name__ == "__main__":
    raise SystemExit(main())
