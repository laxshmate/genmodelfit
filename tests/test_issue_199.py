# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""段ごとの山を使う側まで通す・ほかの利用者の実測を出どころ 1 つから照合に出す・確かめた範囲の表(#199)。

提出の見本は試験用に作った架空の値。件数は同梱の一覧から数え、決め打ちしない。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace

import pytest

from genmodelfit import calibrate as cal
from genmodelfit import contrib, i18n
from genmodelfit.coverage import coverage, coverage_table
from genmodelfit.db import Database, bundled_text, load, parse_measurements
from genmodelfit.estimate import estimate, files_fingerprint, no_size_input, settings_for, stages_for
from genmodelfit.models import Gpu, Measurement, Settings, SystemInfo

SDXL = "image_sdxl_simple"


@pytest.fixture(scope="module")
def db() -> Database:
    return load(use_cache=False)


@pytest.fixture(scope="module")
def maint() -> list[Measurement]:
    return parse_measurements(json.loads(bundled_text("measurements.json")))


def _stages(runs: int, before: float | None, sampling: float | None, after: float | None) -> dict:
    def one(v):
        return None if v is None else {"peak_gb": v, "min_gb": round(v / 2, 2), "samples": 5}

    return {
        "sampler_runs": runs,
        "before_sampling": one(before),
        "sampling": one(sampling),
        "after_sampling": one(after),
    }


def _measurement(peak: float = 5.0, **kw) -> Measurement:
    d = {
        "model_id": SDXL,
        "gpu_name": "NVIDIA GeForce RTX 3070",
        "gpu_vram_gb": 8.0,
        "peak_vram_gb": peak,
        "settings": Settings(width=1024, height=1024, steps=20),
        "offload": "dynamic",
        "vram_metric": "torch_reserved",
        "date": "2026-11-17",
        "note": "SYNTHETIC TEST FIXTURE",
    }
    d.update(kw)
    return Measurement(**d)


def _sub(peak: float, stages: dict | None) -> contrib.Submission:
    d = contrib.build_submission(_measurement(peak), None, stages=stages)
    return contrib.Submission(path="-", sha256=contrib.content_hash(d), data=d)


# --- 1. stages を使う側まで通す ----------------------------------------------------------------------


def test_to_measurement_keeps_stages():
    st = _stages(1, 1.0, 3.0, 4.0)
    sub = _sub(5.0, st)
    assert sub.data["measurement"]["stages"] == st
    assert contrib.to_measurement(sub.data).stages == st
    assert contrib.to_measurement(_sub(5.0, None).data).stages is None


def test_aggregate_stages_median_of_sources():
    a1 = _sub(5.0, _stages(1, 1.0, 3.0, 4.0))
    a2 = _sub(5.1, _stages(1, 1.2, 3.4, 4.4))  # 出どころ A: 1.1・3.2・4.2
    b1 = _sub(5.2, _stages(1, 2.0, 5.0, None))  # 出どころ B: 仕上げの段は読めなかった
    b2 = _sub(5.3, _stages(2, 9.0, 9.0, 9.0))  # サンプラの数が最頻値と違う: 段のまとめに入れない
    b3 = _sub(5.25, None)  # 段の無い提出
    subs = [a1, a2, b1, b2, b3]
    sources = {a1.sha256: "A", a2.sha256: "A", b1.sha256: "B", b2.sha256: "B", b3.sha256: "B"}
    [row] = contrib.aggregate(subs, sources)
    assert row["n_sources"] == 2 and row["n_used"] == 5  # 段の有無はまとめの値に関係しない
    st = row["stages"]
    assert st["sampler_runs"] == 1 and st["sources"] == 2
    assert st["before_sampling"] == {"peak_gb": 1.55, "min_gb": 0.78}  # median(1.1, 2.0)、median(0.55, 1.0)
    assert st["sampling"] == {"peak_gb": 4.1, "min_gb": 2.05}  # median(3.2, 5.0)、median(1.6, 2.5)
    assert st["after_sampling"] == {"peak_gb": 4.2, "min_gb": 2.1}  # A だけ
    # 同梱の形で書いて読み直しても Measurement.stages に入る
    doc = contrib.community_document([row], subs, sources)
    [m] = parse_measurements(json.loads(contrib.dumps(doc)))
    assert m.stages == st


def test_aggregate_without_stages_has_no_key():
    rows = contrib.aggregate([_sub(5.0, None), _sub(5.1, None)])
    assert rows and all("stages" not in r for r in rows)
    assert contrib.aggregate_stages([]) is None


def test_aggregate_stages_all_empty_has_no_key():
    """どの段の山も読めなかった提出だけなら、行に stages を書かない(空の段だけの行を作らない)。"""
    subs = [_sub(5.0, _stages(1, None, None, None)), _sub(5.1, _stages(1, None, None, None))]
    assert subs[0].data["measurement"]["stages"]["sampling"] is None
    rows = contrib.aggregate(subs)
    assert rows and all("stages" not in r for r in rows)
    # 1 つでも読めた段があれば書く
    [row] = contrib.aggregate([*subs, _sub(5.2, _stages(1, None, 3.0, None))])
    assert row["stages"]["sampling"]["peak_gb"] == 3.0 and row["stages"]["before_sampling"] is None


def test_bundled_community_rows_read_without_stages(db):
    comm = [m for m in db.measurements if m.origin == "community"]
    assert comm and all(m.stages is None for m in comm)  # 今の提出には段ごとの山が無い


# --- 2. 照合に出どころ 1 つから出す ------------------------------------------------------------------


def test_single_source_community_rows_in_check_only(db, maint):
    base = _measurement(2.3, gpu_name="NVIDIA GeForce RTX 5080", gpu_vram_gb=15.92)
    one = replace(base, origin="community", n_submissions=1, n_used=1, n_sources=1, consistent=False)
    same_metric = replace(one, vram_metric=None, peak_vram_gb=9.0)  # 係数の合わせの測り方
    unknown_gpu = replace(one, gpu_name="Generic GPU", gpu_known=False)
    test_db = Database(models=db.models, measurements=[*maint, one, same_metric, unknown_gpu])
    check_rows = cal.build_check_rows(test_db)
    comm = [r for r in check_rows if r.m.origin == "community"]
    assert [r.m for r in comm] == [one]  # 名前の表に無い GPU の行は出さない
    assert all(r.m.origin != "community" for r in cal.build_rows(test_db))  # 合わせには入れない
    rows = cal.check(check_rows, cal.fit(cal.build_rows(test_db)))
    c = [x for x in rows if x["origin"] == "community"]
    assert len(c) == 1 and c[0]["n_sources"] == 1 and c[0]["consistent"] is False
    assert all(x["n_sources"] is None for x in rows if x["origin"] == "maintainer")
    with i18n.forced("ja"):
        assert "ほかの利用者(1 件・出どころ 1)" in "\n".join(cal.check_table(c))
    with i18n.forced("en"):
        assert "other users (1 submission, 1 source)" in "\n".join(cal.check_table(c))


def test_community_check_rows_need_current_files(db, maint):
    """測ったときのファイルの指紋が今の一覧と違う行は、照合に出さない(別のファイルの量を今の式と比べない)。"""
    now = files_fingerprint(db.get(SDXL))
    assert now
    old = "0" * len(now) if now != "0" * len(now) else "1" * len(now)
    base = _measurement(2.3, gpu_name="NVIDIA GeForce RTX 5080", gpu_vram_gb=15.92)
    one = replace(base, origin="community", n_submissions=1, n_used=1, n_sources=1, consistent=False)

    def community(m):
        test_db = Database(models=db.models, measurements=[*maint, m])
        return [r.m for r in cal.build_check_rows(test_db) if r.m.origin == "community"]

    assert community(replace(one, files_fp=now)) == [replace(one, files_fp=now)]
    assert community(one) == [one]  # 指紋の無い行は今までどおり出す
    assert community(replace(one, files_fp=old)) == []


def test_inconsistent_community_check_rows_do_not_breach(db, maint):
    """そろっていない行は照合の表に出すが、閾値の判定には入れない(今までどおりそろった行だけ)。"""
    m = _measurement(3.0, gpu_name="NVIDIA GeForce RTX 5080", gpu_vram_gb=15.92, offload="none")
    one = replace(m, origin="community", n_submissions=1, n_used=1, n_sources=1, consistent=False)
    rep = contrib.accuracy_report(Database(models=db.models, measurements=[*maint, one]), threshold_pct=10.0)
    assert any(x["origin"] == "community" and x["quantity"] == "full" for x in rep["check"])
    assert not [b for b in rep["breaches"] if b["kind"] == "community_check"]
    both = replace(one, consistent=True, n_sources=2)
    rep = contrib.accuracy_report(Database(models=db.models, measurements=[*maint, both]), threshold_pct=10.0)
    assert [b for b in rep["breaches"] if b["kind"] == "community_check"]


# --- 3. 段ごとの行 -----------------------------------------------------------------------------------


def _staged_and_not(db):
    staged = db.get(SDXL)
    s = settings_for(staged, Settings(width=1024, height=1024))
    assert stages_for(staged, s) is not None
    plain = db.get("utility_moge_depth_estimation")  # 段ごとに当てられない(実測のある画像の道具)
    assert stages_for(plain, settings_for(plain, None, sized=not no_size_input(plain))) is None
    return staged, plain


def test_stage_rows(db, maint):
    staged, plain = _staged_and_not(db)
    st = _stages(1, 0.5, 3.0, 6.0)
    a = _measurement(6.0, stages=st)
    b = _measurement(1.0, model_id=plain.id, settings=Settings(), stages=_stages(1, 0.2, None, 0.8))
    test_db = Database(models=db.models, measurements=[*maint, a, b])
    rows = cal.check(cal.build_check_rows(test_db), cal.fit(cal.build_rows(test_db)))
    ra = next(x for x in rows if x["model_id"] == SDXL and x.get("stages"))
    sm = stages_for(staged, settings_for(staged, a.settings, sized=not no_size_input(staged)))
    want = {
        "before_sampling": (0.5, {"T": sm.T}),
        "sampling": (3.0, {"S": sm.S, "S_full": sm.S_full}),
        "after_sampling": (6.0, {"V": sm.V, "V_tile": sm.V_tile}),
    }
    assert [x["stage"] for x in ra["stages"]] == list(want)
    for x in ra["stages"]:
        peak, terms = want[x["stage"]]
        assert x["measured_gb"] == peak
        assert [t["term"] for t in x["terms"]] == list(terms)
        for t in x["terms"]:
            f = terms[t["term"]]
            assert t["formula_gb"] == round(f, 2)
            assert t["ratio"] == (round(peak / f, 2) if f > 0 else None)
    rb = next(x for x in rows if x["model_id"] == plain.id and x.get("stages"))
    assert all(t["formula_gb"] is None and t["ratio"] is None for x in rb["stages"] for t in x["terms"])
    assert next(x for x in rb["stages"] if x["stage"] == "sampling")["measured_gb"] is None
    # 段ごとの山の無い行には stages のキーを書かない
    assert sum("stages" in x for x in rows) == 2
    with i18n.forced("ja"):
        ja = cal.stage_table(rows)
    with i18n.forced("en"):
        en = cal.stage_table(rows)
    assert ja[0].startswith("段ごとの照合") and en[0].startswith("Cross-check by stage")
    assert len(ja) == len(en) == 4 + 2 * 5  # 見出し 4 行 + 2 行 × 段の項 5
    t_row = next(x for x in ja if x.startswith(f"| {SDXL} ") and "| T |" in x)
    assert f"| サンプラの前 | 0.50 | T | {sm.T:.2f} |" in t_row
    assert any("before sampling | 0.50 | T |" in x for x in en)
    assert any(f"| {plain.id} " in x and "| - | - |" in x for x in ja)


def test_stage_table_only_with_stage_rows(db):
    assert cal.stage_table([]) == []
    result = json.loads(bundled_text("calibration.json"))
    assert cal.stage_table(result["check"]) == []  # 同梱の実測には段ごとの山が無い
    assert "段ごとの照合" not in cal.markdown_table(result)


# --- 4. 確かめた範囲の表 -----------------------------------------------------------------------------


def test_coverage_counts_every_model_once(db):
    cov = coverage(db)
    n = len(db.models)
    assert cov["n_models"] == n
    assert cov["n_staged_models"] + cov["n_not_staged_models"] == n
    t = cov["totals"]
    assert t["work"]["models"] == t["vae"]["models"] == cov["n_staged_models"]
    assert t["not_staged"]["models"] == cov["n_not_staged_models"]
    seen = [i for x in cov["work"] for i in x["measured"] + x["unmeasured"]]
    seen += [i for x in cov["not_staged"] for i in x["measured"] + x["unmeasured"]]
    assert sorted(seen) == sorted(e.id for e in db.models)  # ちょうど 1 回ずつ
    vae_seen = [i for x in cov["vae"] for i in x["measured"] + x["unmeasured"]]
    assert sorted(vae_seen) == sorted(i for x in cov["work"] for i in x["measured"] + x["unmeasured"])
    for g in ("work", "vae", "not_staged"):
        assert t[g]["kinds"] == len(cov[g])
        assert t[g]["measured_kinds"] == sum(1 for x in cov[g] if x["n_measured"])
        assert t[g]["whole_kinds"] == sum(1 for x in cov[g] if x["n_whole"])
        assert t[g]["whole_models"] == sum(x["n_whole"] for x in cov[g])
        assert all(x["n_whole"] <= x["n_measured"] <= x["n_models"] for x in cov[g])
        assert t[g]["models_in_whole_kinds"] <= t[g]["models_in_measured_kinds"] <= t[g]["models"]


def test_coverage_follows_measurements(db):
    empty = coverage(Database(models=db.models, measurements=[]))
    assert all(empty["totals"][g]["measured_kinds"] == 0 for g in ("work", "vae", "not_staged"))
    staged, plain = _staged_and_not(db)
    one = _measurement(5.0)
    added = coverage(Database(models=db.models, measurements=[one]))
    assert added["totals"]["work"]["measured_kinds"] == 1 and added["totals"]["vae"]["measured_kinds"] == 1
    key = stages_for(staged, settings_for(staged, None)).work
    row = next(x for x in added["work"] if x["key"] == key)
    assert SDXL in row["measured"] and row["n_staged"] == 0
    # 作業用メモリだけの実測(重みを含まない)は「実測あり」だが「全体の量の実測」ではない
    assert row["n_whole"] == 0 and added["totals"]["work"]["whole_kinds"] == 0
    assert added["totals"]["vae"]["whole_kinds"] == 0

    def whole_of(m):
        cov = coverage(Database(models=db.models, measurements=[m]))
        return next(x for x in cov["work"] if x["key"] == key)["n_whole"], cov["totals"]["work"][
            "measured_kinds"
        ]

    full_amount = replace(one, vram_metric=None, offload="none")  # 重みを含み、8 GB の上限に届いていない
    assert whole_of(full_amount) == (1, 1)
    assert whole_of(replace(full_amount, peak_vram_gb=7.99)) == (0, 1)  # 上限に張り付いた実測は下限だけ
    assert whole_of(replace(full_amount, offload="lowvram")) == (0, 1)  # 逃がして測った実測も下限だけ
    both = coverage(Database(models=db.models, measurements=[one, full_amount]))
    assert next(x for x in both["work"] if x["key"] == key)["n_whole"] == 1  # どれか 1 つあれば数える
    with_st = coverage(Database(models=db.models, measurements=[replace(one, stages=_stages(1, 1, 2, 3))]))
    assert next(x for x in with_st["work"] if x["key"] == key)["n_staged"] == 1
    # 使えない実測(OOM・ピーク 0)は数えない
    bad = [replace(one, outcome="oom"), replace(one, peak_vram_gb=0.0)]
    assert coverage(Database(models=db.models, measurements=bad))["totals"]["work"]["measured_kinds"] == 0
    other = coverage(Database(models=db.models, measurements=[replace(one, model_id=plain.id)]))
    assert other["totals"]["not_staged"]["measured_models"] == 1
    assert other["totals"]["not_staged"]["whole_models"] == 0
    # 同梱の実測を抜くと数が減る
    full = coverage(db)
    assert full["totals"]["work"]["measured_kinds"] > empty["totals"]["work"]["measured_kinds"]


def test_coverage_table_languages(db):
    cov = coverage(db)
    with i18n.forced("ja"):
        ja = coverage_table(cov)
    with i18n.forced("en"):
        en = coverage_table(cov)
    w = cov["totals"]["work"]
    assert f"生成の段: {w['kinds']} 種のうち実測あり {w['measured_kinds']} 種" in "\n".join(ja)
    assert f"Sampling stage: {w['measured_kinds']} of {w['kinds']} kinds measured" in "\n".join(en)
    assert f"うち全体の量の実測あり {w['whole_kinds']} 種" in "\n".join(ja)
    assert f"{w['whole_kinds']} with a whole-amount measurement" in "\n".join(en)
    rows = len(cov["work"]) + len(cov["vae"]) + len(cov["not_staged"])
    assert len(ja) == len(en) == 10 + rows
    head = next(x for x in ja if x.startswith("| 区分 "))
    assert all(x.count("|") == head.count("|") for x in ja if x.startswith("|"))  # 列の数がそろっている


def test_calibrate_coverage_writes_nothing(tmp_path, capsys):
    out = tmp_path / "calibration.json"
    before = bundled_text("calibration.json")
    assert cal.main(["--coverage", "--output", str(out)]) == 0
    assert not out.exists() and bundled_text("calibration.json") == before
    text = capsys.readouterr().out
    assert text.startswith("確かめた範囲") and "wrote" not in text


# --- 判定が変わらない --------------------------------------------------------------------------------


@pytest.mark.parametrize("dynamic", [False, True])
def test_stages_do_not_change_estimate(db, maint, dynamic):
    e = db.get(SDXL)
    m = _measurement(
        9.2 if not dynamic else 2.5,
        gpu_name="NVIDIA GeForce RTX 5080",
        gpu_vram_gb=15.92,
        offload="dynamic" if dynamic else "none",
        vram_metric=None if not dynamic else "torch_reserved",
    )
    with_st = replace(m, stages=_stages(1, 0.5, 3.0, 6.0))
    g = Gpu(name="NVIDIA GeForce RTX 5080", vendor="nvidia", vram_total_gb=15.92, backend="cuda")
    pc = SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    s = Settings(width=1024, height=1024)
    a = estimate(e, pc, s, [*maint, m])
    b = estimate(e, pc, s, [*maint, with_st])
    assert dataclasses.asdict(a) == dataclasses.asdict(b)
