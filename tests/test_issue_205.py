# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""毎週の更新の PR に「実測の無い式を持つ増えた項目」を出す(#205)。知らせるだけ。

通信しない。同梱の一覧のモデル ID・件数は決め打ちしない: 式の鍵は試験の中で差し替え(coverage.formula_keys)、
一覧は小さな ModelEntry で作る。run() は test_weekly.py と同じに、作り直しを差し替えて動かす。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import random
import re
from datetime import UTC, datetime, timedelta

import pytest

from genmodelfit import coverage, weekly
from genmodelfit.db import Database, load, load_measurements
from genmodelfit.diff import diff_db
from genmodelfit.models import Measurement, ModelEntry, Settings

NOW = datetime(2026, 10, 21, tzinfo=UTC)

# id -> 式の鍵(試験用に差し替える)。A と G は実測がある。
KEYS = {
    "A": ("work:w1", "vae:v1"),  # 両方あり(A の実測)
    "B": ("work:w1", "vae:v2"),  # 生成だけあり
    "C": ("work:w3", "vae:v1"),  # VAE だけあり
    "D": ("work:w4", "vae:v4"),  # どちらも無し
    "E": ("work:w4", "vae:v5"),  # どちらも無し
    "F": ("model:F",),  # 当てられない・実測なし(image)
    "G": ("model:G",),  # 当てられない・実測あり
    "H": ("model:H",),  # 当てられない・実測なし(audio)
    "I": ("work:w1", "vae:v6"),  # 生成だけあり(B と合わせて work_only を vae_only と違う数にする)
}
MODALITY = {"H": "audio"}


def _entry(i: str) -> ModelEntry:
    return ModelEntry(id=i, name=i, modality=MODALITY.get(i, "image"), source="huggingface")


def _m(model_id: str, peak: float = 5.0) -> Measurement:
    return Measurement(
        model_id=model_id,
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        peak_vram_gb=peak,
        settings=Settings(width=1024, height=1024, steps=20),
        offload="dynamic",
        vram_metric="torch_reserved",
        date="2026-11-17",
        note="SYNTHETIC TEST FIXTURE",
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fake_keys(monkeypatch):
    monkeypatch.setattr(coverage, "formula_keys", lambda e: KEYS.get(e.id, (f"model:{e.id}",)))


def _db(ids=tuple(KEYS)) -> Database:
    return Database(models=[_entry(i) for i in ids])


MEAS = [_m("A"), _m("G")]


# --- 1. unmeasured_new ---------------------------------------------------------------------------


def test_counts_and_formulas(fake_keys):
    u = coverage.unmeasured_new(_db(), [*KEYS, "not-in-db"], MEAS)
    assert u["added"] == 9
    c = u["counts"]
    assert c == {
        "both": 1,
        "vae_only": 1,
        "work_only": 2,
        "none": 2,
        "not_staged": 2,
        "not_staged_measured": 1,
    }
    assert sum(c.values()) == u["added"]
    got = [(f["key"], f["kind"], f["models"]) for f in u["formulas"]]
    assert got == [
        ("work:w4", "work", ["D", "E"]),
        ("vae:v2", "vae", ["B"]),
        ("vae:v4", "vae", ["D"]),
        ("vae:v5", "vae", ["E"]),
        ("vae:v6", "vae", ["I"]),
        ("work:w3", "work", ["C"]),
    ]
    assert not {"work:w1", "vae:v1"} & {f["key"] for f in u["formulas"]}  # 実測のある鍵は入らない
    assert u["not_staged"] == [
        {"modality": "audio", "models": ["H"]},
        {"modality": "image", "models": ["F"]},
    ]  # G は実測があるので入らない
    json.dumps(u)


def test_order_is_deterministic(fake_keys):
    base = coverage.unmeasured_new(_db(), list(KEYS), MEAS)
    for seed in range(5):
        ids = list(KEYS)
        random.Random(seed).shuffle(ids)
        db = _db(random.Random(seed + 100).sample(list(KEYS), len(KEYS)))
        assert coverage.unmeasured_new(db, ids, list(reversed(MEAS))) == base


def test_duplicate_added_ids_count_once(fake_keys):
    once = coverage.unmeasured_new(_db(), list(KEYS), MEAS)
    assert coverage.unmeasured_new(_db(), [*KEYS, *KEYS, "A", "D"], MEAS) == once


def test_ignores_ids_not_in_db(fake_keys):
    u = coverage.unmeasured_new(_db(), ["x", "y"], MEAS)
    assert u["added"] == 0 and u["formulas"] == [] and u["not_staged"] == []
    assert sum(u["counts"].values()) == 0


def test_does_not_read_user_cache(fake_keys, tmp_path):
    """この PC のキャッシュの実測(bench)を置いても結果が変わらない(measurements を渡さない呼び方)。"""
    before = coverage.unmeasured_new(_db(), KEYS)
    d = tmp_path / "cache" / "measurements"
    d.mkdir(parents=True)
    (d / "mine.json").write_text(
        json.dumps({"measurements": [dataclasses.asdict(_m(i)) for i in KEYS]}), encoding="utf-8"
    )
    assert {m.model_id for m in load_measurements(include_cache=True)} >= set(KEYS)  # 置いたものは読める
    assert coverage.unmeasured_new(_db(), KEYS) == before
    assert before["counts"]["both"] == 0  # 同梱の実測には架空の ID が無い


# --- 3. pr_body ----------------------------------------------------------------------------------


def _d():
    return diff_db(Database(models=[]), Database(models=[]))


def _u(**kw):
    u = {
        "added": 9,
        "counts": {
            "both": 1,
            "vae_only": 1,
            "work_only": 2,
            "none": 2,
            "not_staged": 2,
            "not_staged_measured": 1,
        },
        "formulas": [
            {"key": "work:a|b_c", "kind": "work", "models": ["x|1", "y_`2", "z[3](u)", "w4", "v5"]},
            {"key": "vae:k", "kind": "vae", "models": ["q"]},
        ],
        "not_staged": [{"modality": "im|age", "models": ["m1", "m2"]}],
    }
    u.update(kw)
    return u


def _cells(line: str) -> list[str]:
    r"""表の 1 行のセル(中身の | は \| になっているので、\ の付かない | だけで区切る)。"""
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]


def test_section_in_body():
    body = weekly.pr_body(_d(), {"status": {}, "unmeasured_formulas": _u()})
    assert "### 実測の無い式(増えた項目)" in body
    assert (
        "増えた 9 件のうち、両方あり 1・片方だけ 3・どちらも無し 2・式を当てられない 3(うち実測あり 1)"
        in body
    )
    assert "| 式の鍵 | 段 | 増えた項目の数 | 項目 |" in body
    assert "ほか 2 件" in body  # 5 件のうち 3 件を出す
    assert "| 生成 |" in body and "| VAE |" in body
    assert "im\\|age 2 件" in body
    assert (
        "`genmodelfit share` は、まだ実測の無い式のモデルを先に勧めます(#200。既定は sfw のモデル)。" in body
    )
    assert "全部は artifact の result.json" not in body  # 15 行以内なら出ない
    # 段の名前: work: の鍵の行は「生成」、vae: の鍵の行は「VAE」
    for ln in body.splitlines():
        if ln.startswith("| work"):
            assert _cells(ln)[1] == "生成", ln
        if ln.startswith("| vae"):
            assert _cells(ln)[1] == "VAE", ln
    # 置き場所: 「マージの前に確かめること」の直前
    assert body.index("### 実測の無い式") < body.index("### マージの前に確かめること")
    for raw in ("x|1", "y_`2", "z[3](u)", "work:a|b_c"):
        assert raw not in body, raw
    for esc in ("x\\|1", "y\\_\\`2", "z\\[3\\](u)", "work:a\\|b\\_c"):
        assert esc in body, esc
    # 表の行の区切りが壊れていない(中身の | は \| になっていて、行の区切りは 5 本)
    rows = [ln for ln in body.splitlines() if ln.startswith(("| work", "| vae"))]
    assert len(rows) == 2 and all(ln.replace("\\|", "").count("|") == 5 for ln in rows)


def test_section_absent_when_nothing():
    for u in (None, _u(added=0, formulas=[], not_staged=[]), _u(formulas=[], not_staged=[])):
        body = weekly.pr_body(_d(), {"status": {}, "unmeasured_formulas": u})
        assert "実測の無い式" not in body
    body = weekly.pr_body(_d(), {"status": {}})  # 古い result.json
    assert "実測の無い式" not in body and "マージの前に確かめること" in body


def test_error_section():
    body = weekly.pr_body(_d(), {"status": {}, "unmeasured_formulas": {"error": "ValueError"}})
    assert "### 実測の無い式(増えた項目)" in body
    assert "一覧を作れませんでした(ValueError)。更新の確かめには関係しません。" in body
    assert "| 式の鍵 |" not in body


def test_without_measured_not_staged_has_no_parenthesis():
    c = dict(_u()["counts"], not_staged_measured=0)
    body = weekly.pr_body(_d(), {"status": {}, "unmeasured_formulas": _u(counts=c)})
    assert "式を当てられない 2。" in body and "うち実測あり" not in body


# 節の大きさの上限: 15 行 x (鍵 80 字 + id 3 つ x 80 字 + 区切り・「ほか n 件」の余裕 60 字) を、すべての字が
# 2 字に化ける最悪(md() のバックスラッシュ)として 2 倍し、見出し・要約・最後の文の余裕 1500 字を足す
SECTION_LIMIT = 15 * 2 * (80 + 3 * 80 + 60) + 1500


def test_section_is_small_and_after_uncensored():
    keys = [
        {
            "key": "work:" + "_" * 150 + str(i),
            "kind": "work",
            "models": ["m_" + "_" * 200 + str(j) for j in range(5)],
        }
        for i in range(40)
    ]
    u = _u(formulas=keys, added=200)
    sec = "\n".join(weekly.unmeasured_section(u))
    rows = [ln for ln in sec.splitlines() if ln.startswith("| work")]
    assert len(rows) == weekly.MAX_UNMEASURED_ROWS == 15
    assert "…ほか 25 件(全部は artifact の result.json)" in sec
    assert len(sec) < SECTION_LIMIT, len(sec)
    assert "_" * 90 not in sec.replace("\\_", "_")  # 80 字を超えた分は「…」
    assert "…" in sec
    # 無検閲の節・GPU 表の見張りより後ろ
    e = ModelEntry(
        id="u1",
        name="u1",
        modality="image",
        source="huggingface",
        content="uncensored",
        content_evidence=["e"],
    )
    gw = {"candidates": [], "unreadable": [], "read": [], "baseline": None}
    result = {"status": {}, "unmeasured_formulas": u, "gpu_watch": gw}
    body = weekly.pr_body(diff_db(Database(models=[]), Database(models=[e])), result)
    i = body.index("### 実測の無い式")
    assert body.index("### 新しく無検閲の印が付いた項目") < i
    assert body.index("### GPU 表の見張り") < i < body.index("### マージの前に確かめること")


def test_section_not_staged_only():
    body = weekly.pr_body(_d(), {"status": {}, "unmeasured_formulas": _u(formulas=[])})
    assert "### 実測の無い式(増えた項目)" in body and "| 式の鍵 |" not in body


# --- 4. run() は ok・分類・merge-check を変えない ---------------------------------------------------


NEW_ID = "hf:test-owner/issue-205-new"


@pytest.fixture(scope="module")
def bundled() -> Database:
    return load(use_cache=False)


def _run(tmp_path, monkeypatch, bundled, name, section, add=True):
    stamp = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def fake(fetch, out_path, *, allow_partial, progress, previous):
        db = copy.deepcopy(previous)
        for m in db.models:
            for f in m.files:
                if f.head_checked_at:
                    f.head_checked_at = stamp
        db.generated_at = stamp
        if add:
            extra = copy.deepcopy(db.models[0])
            extra.id = NEW_ID
            db.models.append(extra)
        db.save(out_path)
        return db

    monkeypatch.setattr(weekly, "update_database", fake)
    monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
    if isinstance(section, Exception):

        def boom(*a, **k):
            raise section

        monkeypatch.setattr(coverage, "unmeasured_new", boom)
    elif section is not None:
        monkeypatch.setattr(coverage, "unmeasured_new", lambda *a, **k: section)
    base = tmp_path / "base.json"
    bundled.save(base)
    out = tmp_path / name
    r = weekly.run(out, base=base, fetch=object(), now=NOW, gpu_watch=False)
    mc = weekly.merge_check(base, out / "db.json")
    body = (out / "pr_body.md").read_text(encoding="utf-8")
    saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
    return r, mc, body, saved


def test_run_writes_section_and_does_not_change_judgement(tmp_path, monkeypatch, bundled):
    real, mc_real, _, saved = _run(tmp_path, monkeypatch, bundled, "real", None)
    assert real["ok"], real["problems"]
    u = real["unmeasured_formulas"]
    assert saved["unmeasured_formulas"] == u and u["added"] == 1
    assert sum(u["counts"].values()) == 1
    empty = {"added": 0, "counts": dict.fromkeys(u["counts"], 0), "formulas": [], "not_staged": []}
    bodies = []
    for i, section in enumerate((empty, _u())):
        r, mc, body, _ = _run(tmp_path, monkeypatch, bundled, f"x{i}", section)
        bodies.append(body)
        for k in ("ok", "problems", "classification", "warnings", "partial", "summary", "status"):
            assert r[k] == real[k], k
        assert {k: v for k, v in mc.items() if k != "head"} == {
            k: v for k, v in mc_real.items() if k != "head"
        }
    assert "実測の無い式" not in bodies[0] and "### 実測の無い式(増えた項目)" in bodies[1]


def test_run_does_not_read_user_cache(tmp_path, monkeypatch, bundled):
    """run() の new は load_db の既定でキャッシュの実測も読むが、unmeasured_formulas には混ざらない。"""
    before, _, _, _ = _run(tmp_path, monkeypatch, bundled, "nocache", None)
    d = tmp_path / "cache" / "measurements"
    d.mkdir(parents=True)
    (d / "mine.json").write_text(
        json.dumps({"measurements": [dataclasses.asdict(_m(NEW_ID))]}), encoding="utf-8"
    )
    assert NEW_ID in {m.model_id for m in load_measurements(include_cache=True)}
    after, _, _, saved = _run(tmp_path, monkeypatch, bundled, "cache", None)
    assert after["unmeasured_formulas"] == before["unmeasured_formulas"] == saved["unmeasured_formulas"]


def test_safe_week_classification_unchanged_by_section(tmp_path, monkeypatch, bundled):
    """変化の無い週(safe)に、中身のある節を返させても classification は丸ごと同じ。"""
    empty = {"added": 0, "counts": dict.fromkeys(_u()["counts"], 0), "formulas": [], "not_staged": []}
    a, _, _, _ = _run(tmp_path, monkeypatch, bundled, "s0", empty, add=False)
    b, _, body, _ = _run(tmp_path, monkeypatch, bundled, "s1", _u(), add=False)
    assert a["classification"] == b["classification"] and a["classification"]["safe"] is True
    for k in ("ok", "problems", "warnings"):
        assert a[k] == b[k], k
    assert "### 実測の無い式(増えた項目)" in body


def test_exception_does_not_stop_the_update(tmp_path, monkeypatch, bundled):
    fine, mc_ok, _, _ = _run(tmp_path, monkeypatch, bundled, "fine", None)
    r, mc, body, saved = _run(tmp_path, monkeypatch, bundled, "boom", ValueError("C:/secret/place"))
    assert r["unmeasured_formulas"] == {"error": "ValueError"} == saved["unmeasured_formulas"]
    assert "secret" not in body and "一覧を作れませんでした(ValueError)" in body
    for k in ("ok", "problems", "classification", "warnings", "summary"):
        assert r[k] == fine[k], k
    assert {k: v for k, v in mc.items() if k != "head"} == {k: v for k, v in mc_ok.items() if k != "head"}
