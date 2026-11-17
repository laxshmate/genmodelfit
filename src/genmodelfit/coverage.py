# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""確かめた範囲の表(#199)。`python -m genmodelfit.calibrate --coverage`

一覧の全モデルに、判定の既定の設定(estimate.settings_for に設定の指定なし)で段ごとの式(estimate.stages_for。
NVIDIA の半精度)を当て、生成の段の鍵(working.StageMem.work)ごと・VAE の鍵(StageMem.vae)ごと・
当てられないモデル(種類ごと)に、使うモデルの数と、実測のあるモデルの数を数える。
「実測がある」= db.measurements に、そのモデルの使える実測(estimate.usable。作者・ほかの利用者、GPU・測り方を
問わない)でピークが 0 より大きいものが 1 つ以上ある。段ごとの山(Measurement.stages)があればそれも数える。
そのうち「全体の量の実測」= 重みを含み(estimate.working_only でない)、GPU の上限に張り付いていない
(estimate.is_censored でない)もの。作業用メモリだけの実測は重みの分を確かめられず、張り付いた実測は下限しか
分からないので、分けて数える。
一覧は毎週変わるので calibration.json には入れない。通信しない・何も印字しない。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .db import Database, load_measurements
from .estimate import is_censored, no_size_input, settings_for, stages_for, usable, working_only
from .i18n import tr
from .models import Measurement, ModelEntry

GROUPS = ("work", "vae", "not_staged")


def formula_keys(entry: ModelEntry) -> tuple[str, ...]:
    """そのモデルの式の鍵(#200。measure の勧める順)。判定の既定の設定で段ごとの式を当てられるなら
    ("work:<生成の段の鍵>", "vae:<VAE の鍵>")、当てられないなら ("model:<モデル ID>",)。"""
    st = stages_for(entry, settings_for(entry, None, sized=not no_size_input(entry)))
    if st is None:
        return (f"model:{entry.id}",)
    return (f"work:{st.work}", f"vae:{st.vae}")


def measured_keys(
    models: Iterable[ModelEntry], measurements: Iterable[Measurement]
) -> tuple[set[str], set[str]]:
    """(使える実測(_measured の「実測がある」と同じ条件)のあるモデルの式の鍵の集合,
    そのうち段ごとの山(stages)のある実測のあるモデルの式の鍵の集合)。一覧に無いモデルの実測は数えない。"""
    have, _, staged = _measured(measurements)
    keys: set[str] = set()
    staged_keys: set[str] = set()
    for e in models:
        if e.id in have:
            k = formula_keys(e)
            keys.update(k)
            if e.id in staged:
                staged_keys.update(k)
    return keys, staged_keys


def _measured(measurements: Iterable[Measurement]) -> tuple[set[str], set[str], set[str]]:
    """(実測のあるモデル ID, そのうち全体の量の実測のあるモデル ID, 段ごとの山のある実測のあるモデル ID)。"""
    have: set[str] = set()
    whole: set[str] = set()
    staged: set[str] = set()
    for m in measurements:
        if not usable(m) or not (m.peak_vram_gb or 0) > 0:
            continue
        have.add(m.model_id)
        if not working_only(m) and not is_censored(m):
            whole.add(m.model_id)
        if isinstance(getattr(m, "stages", None), dict):
            staged.add(m.model_id)
    return have, whole, staged


def _item(
    key: str, ids: set[str], have: set[str], whole: set[str], staged: set[str], name: str
) -> dict[str, Any]:
    return {
        name: key,
        "n_models": len(ids),
        "n_measured": len(ids & have),
        "n_whole": len(ids & whole),
        "n_staged": len(ids & staged),
        "measured": sorted(ids & have),
        "unmeasured": sorted(ids - have),
    }


def _totals(items: list[dict[str, Any]]) -> dict[str, int]:
    hit = [x for x in items if x["n_measured"]]
    whole = [x for x in items if x["n_whole"]]
    return {
        "kinds": len(items),
        "measured_kinds": len(hit),
        "whole_kinds": len(whole),
        "models": sum(x["n_models"] for x in items),
        "models_in_measured_kinds": sum(x["n_models"] for x in hit),
        "models_in_whole_kinds": sum(x["n_models"] for x in whole),
        "measured_models": sum(x["n_measured"] for x in items),
        "whole_models": sum(x["n_whole"] for x in items),
        "staged_models": sum(x["n_staged"] for x in items),
    }


def coverage(db: Database) -> dict[str, Any]:
    """式の種類ごとに、どこまで実測で確かめたか。

    - work: 生成の段の鍵ごと / vae: VAE の鍵ごと / not_staged: 段ごとに当てられないモデルの
      種類(modality)ごと。
      どれも n_models・n_measured・n_whole(全体の量を張り付かずに測った実測のあるモデル)・n_staged と、
      モデル ID の一覧(measured・unmeasured)。並びは使うモデルの多い順 → 鍵の順
    - totals: 群ごとの kinds(種類の数)・measured_kinds(実測のある種類の数)・whole_kinds(全体の量の実測の
      ある種類の数)・models(使うモデルの数)・models_in_measured_kinds(実測のある種類を使うモデルの数)・
      models_in_whole_kinds・measured_models・whole_models・staged_models
    - n_models(一覧の件数)・n_staged_models(段ごとに当てられるモデルの数)・n_not_staged_models
    どのモデルも、当てられるなら work と vae に 1 回ずつ、当てられないなら not_staged に 1 回だけ数える。"""
    have, whole, staged = _measured(db.measurements)
    work: dict[str, set[str]] = {}
    vae: dict[str, set[str]] = {}
    other: dict[str, set[str]] = {}
    groups = {"work": work, "vae": vae}
    for e in db.models:
        keys = formula_keys(e)
        if keys[0].startswith("model:"):
            other.setdefault(e.modality, set()).add(e.id)
        else:
            for k in keys:
                kind, _, name = k.partition(":")
                groups[kind].setdefault(name, set()).add(e.id)

    def items(groups: dict[str, set[str]], name: str) -> list[dict[str, Any]]:
        out = [_item(k, v, have, whole, staged, name) for k, v in groups.items()]
        return sorted(out, key=lambda x: (-x["n_models"], x[name]))

    out = {
        "work": items(work, "key"),
        "vae": items(vae, "key"),
        "not_staged": items(other, "modality"),
    }
    n_staged = len({i for v in work.values() for i in v})
    return {
        "n_models": len(db.models),
        "n_staged_models": n_staged,
        "n_not_staged_models": sum(len(v) for v in other.values()),
        **out,
        "totals": {g: _totals(out[g]) for g in GROUPS},
    }


def unmeasured_new(
    db: Database,
    added_ids: Iterable[str],
    measurements: Iterable[Measurement] | None = None,
) -> dict[str, Any]:
    """毎週の更新で増えた項目のうち、実測の無い式を持つものを数える(#205。知らせるだけ)。

    added_ids のうち db にある項目それぞれに formula_keys を当て、measured_keys(今の「実測がある」の条件)で
    実測の有無を分ける。measurements を渡さないときは同梱の実測(作者の + ほかの利用者のまとめ)だけを読み、
    この PC のキャッシュの実測は読まない(メンテナの PC の実測で毎週の PR の本文が変わらないように。
    db.measurements は読み込みの既定でキャッシュの実測を含むので使わない)。

    返す値(JSON に書ける。同じ入力なら同じ出力):
    - added: db にある増えた項目の数(db に無い id は数えない)
    - counts: both(生成の段も VAE も実測あり)・vae_only・work_only・none(段ごとの式は当てられるが
      どちらも実測なし)・not_staged(段ごとの式を当てられず、model:<id> にも実測なし)・
      not_staged_measured(同じく当てられないが model:<id> に実測あり)。和は added
    - formulas: 実測の無い work: / vae: の鍵ごとに {key, kind, models}。項目の多い鍵が先、同数なら鍵の順
    - not_staged: 実測の無い当てられない項目を種類(modality)ごとに {modality, models}(種類の順)"""
    if measurements is None:
        measurements = load_measurements(include_cache=False)
    have_keys, _ = measured_keys(db.models, measurements)
    by_id = {e.id: e for e in db.models}
    entries = [by_id[i] for i in sorted(set(added_ids)) if i in by_id]
    counts = dict.fromkeys(("both", "vae_only", "work_only", "none", "not_staged", "not_staged_measured"), 0)
    formulas: dict[str, list[str]] = {}
    other: dict[str, list[str]] = {}
    for e in entries:
        keys = formula_keys(e)
        if keys[0].startswith("model:"):
            if keys[0] in have_keys:
                counts["not_staged_measured"] += 1
            else:
                counts["not_staged"] += 1
                other.setdefault(e.modality, []).append(e.id)
            continue
        work_ok, vae_ok = keys[0] in have_keys, keys[1] in have_keys
        counts[
            "both" if work_ok and vae_ok else "vae_only" if vae_ok else "work_only" if work_ok else "none"
        ] += 1
        for k in keys:
            if k not in have_keys:
                formulas.setdefault(k, []).append(e.id)
    rows = [{"key": k, "kind": k.partition(":")[0], "models": sorted(v)} for k, v in formulas.items()]
    rows.sort(key=lambda r: (-len(r["models"]), r["key"]))
    return {
        "added": len(entries),
        "counts": counts,
        "formulas": rows,
        "not_staged": [{"modality": m, "models": sorted(v)} for m, v in sorted(other.items())],
    }


def coverage_table(cov: dict[str, Any]) -> list[str]:
    """確かめた範囲の表(Markdown。tr() で日英)。モデル ID は一覧から来るので md_cell を通す。"""
    from .contrib import md_cell

    t = cov["totals"]
    w, v, o = t["work"], t["vae"], t["not_staged"]
    n = cov["n_staged_models"]
    lines = [
        tr(
            f"確かめた範囲(一覧の {cov['n_models']} モデルに既定の設定で段ごとの式を当てた。"
            f"当てられる {n}、当てられない {cov['n_not_staged_models']}):",
            f"Coverage (the stage formula applied to the {cov['n_models']} models in the list with the"
            f" default settings: {n} covered by the stage formula, {cov['n_not_staged_models']} not):",
        ),
        "",
        tr(
            f"- 生成の段: {w['kinds']} 種のうち実測あり {w['measured_kinds']} 種"
            f"(その種類を使うモデル {w['models_in_measured_kinds']} / {n})。"
            f"うち全体の量の実測あり {w['whole_kinds']} 種({w['models_in_whole_kinds']} / {n})",
            f"- Sampling stage: {w['measured_kinds']} of {w['kinds']} kinds measured"
            f" (models using them: {w['models_in_measured_kinds']} / {n});"
            f" {w['whole_kinds']} with a whole-amount measurement ({w['models_in_whole_kinds']} / {n})",
        ),
        tr(
            f"- VAE: {v['kinds']} 種のうち実測あり {v['measured_kinds']} 種"
            f"(その種類を使うモデル {v['models_in_measured_kinds']} / {n})。"
            f"うち全体の量の実測あり {v['whole_kinds']} 種({v['models_in_whole_kinds']} / {n})",
            f"- VAE: {v['measured_kinds']} of {v['kinds']} kinds measured"
            f" (models using them: {v['models_in_measured_kinds']} / {n});"
            f" {v['whole_kinds']} with a whole-amount measurement ({v['models_in_whole_kinds']} / {n})",
        ),
        tr(
            f"- 当てられないモデル: {o['models']} のうち実測あり {o['measured_models']}"
            f"(うち全体の量の実測あり {o['whole_models']})",
            f"- Models without the stage formula: {o['measured_models']} of {o['models']} measured"
            f" ({o['whole_models']} with a whole-amount measurement)",
        ),
        "",
        tr(
            "全体の量の実測 = 重みを含み、GPU の上限に張り付いていない実測。ほかは作業用メモリだけの実測か、"
            "張り付いた実測(下限しか分からない)。",
            "A whole-amount measurement includes the weights and is not pinned at the GPU's limit. The rest"
            " are working-memory-only measurements or censored ones (a lower bound only).",
        ),
        "",
        tr(
            "| 区分 | 鍵 | モデル | 実測あり | うち全体の量 | 段ごとの山あり | 実測のあるモデル |",
            "| Group | Key | Models | Measured | Whole amount | With stage peaks | Measured models |",
        ),
        "|---|---|---:|---:|---:|---:|---|",
    ]
    label = {
        "work": tr("生成の段", "sampling stage"),
        "vae": "VAE",
        "not_staged": tr("当てられない", "no stage formula"),
    }
    for g in GROUPS:
        name = "modality" if g == "not_staged" else "key"
        for x in cov[g]:
            ids = ", ".join(md_cell(i) for i in x["measured"]) or "-"
            lines.append(
                f"| {label[g]} | {md_cell(x[name])} | {x['n_models']} | {x['n_measured']} |"
                f" {x['n_whole']} | {x['n_staged']} | {ids} |"
            )
    return lines


__all__ = ["coverage", "coverage_table", "formula_keys", "measured_keys", "unmeasured_new"]
