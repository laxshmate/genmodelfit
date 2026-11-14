# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""share で送るものを広げる(#132): 測ったときの見積もり・時間の内訳・VRAM を超えた結果・失敗の記録。

本物の GPU・ComfyUI・GitHub・通信には触らない。"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from genmodelfit import bench, calibrate, cli, contrib, measure
from genmodelfit.db import Database, cache_dir, load_measurements, parse_measurements
from genmodelfit.db import load as load_db
from genmodelfit.models import Gpu, Measurement, Settings, SystemInfo

FIX = Path(__file__).parent / "fixtures" / "contrib"
GOOD = sorted(p for p in FIX.rglob("*.json") if p.parent.name == "rtx-3070")


@pytest.fixture(scope="module")
def db():
    return load_db(use_cache=False)


def pc() -> SystemInfo:
    g = Gpu(
        name="NVIDIA GeForce RTX 3070", vendor="nvidia", vram_total_gb=8.0, backend="cuda", driver="566.03"
    )
    return SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])


def meas(**kw) -> Measurement:
    base = {
        "model_id": "image_sdxl_simple",
        "gpu_name": "NVIDIA GeForce RTX 3070",
        "gpu_vram_gb": 8.0,
        "peak_vram_gb": 6.9,
        "settings": Settings(width=832, height=832, steps=20),
        "seconds": 41.2,
        "driver": "566.03",
        "comfyui_version": "0.40.0",
        "offload": "dynamic",
        "date": "2026-10-28",
        "vram_metric": "torch_reserved",
        "gpu_backend": "cuda",
    }
    return Measurement(**{**base, **kw})


TIMING = {
    "queue_s": 0.1,
    "load_s": 4.2,
    "sampling_s": 20.0,
    "sec_per_step": 1.0,
    "decode_s": 2.5,
    "save_s": None,
    "total_s": 41.2,
    "cold_load": True,
}


def v3(db, **kw) -> dict:
    e = db.get("image_sdxl_simple")
    est = contrib.measurement_estimate(
        e, pc(), Settings(width=832, height=832, steps=20), [], exclude_gpu="NVIDIA GeForce RTX 3070"
    )
    return contrib.build_submission(
        meas(**kw), pc(), estimate=est, timing=TIMING, db_info=contrib.db_info(db)
    )


def errors(data, db=None) -> list[str]:
    r = contrib.validate_submission(data, db, raw=contrib.dumps(data), check_filename=False)
    return [e["code"] + ": " + e["message"] for e in r.errors]


# --- 1. 測ったときの見積もり・時間の内訳・一覧の版 ------------------------------------------------------


def test_v3_submission_carries_estimate_timing_and_db(db):
    sub = v3(db)
    assert sub["contrib_schema"] == 3
    mm = sub["measurement"]
    est = mm["estimate"]
    assert set(est) == contrib.ESTIMATE_KEYS
    assert est["fit_level"] in contrib.ESTIMATE_LEVELS and est["basis"] in contrib.ESTIMATE_BASES
    assert est["run_mode"] in contrib.ESTIMATE_MODES and est["vram_gb"] > 0 and est["work_gb"] > 0
    assert list(mm["timing"]) == list(contrib.TIMING_ORDER) and mm["timing"]["cold_load"] is True
    assert sub["db"]["generated_at"] == db.generated_at[:10] and len(sub["db"]["sha256"]) == 12
    assert "spilled" not in mm
    assert errors(sub, db) == []
    checks = {c["id"]: c["ok"] for c in contrib.privacy_checklist(sub, contrib.dumps(sub))}
    assert all(v is not False for v in checks.values()), checks


STAGES = {
    "sampler_runs": 1,
    "before_sampling": {"peak_gb": 0.38, "min_gb": 0.09, "samples": 46},
    "sampling": {"peak_gb": 0.44, "min_gb": 0.44, "samples": 86},
    "after_sampling": {"peak_gb": 6.9, "min_gb": 0.09, "samples": 8},
}


def test_submission_carries_stage_peaks_when_given(db):
    """段ごとの山(#100 の宿題 2)は、bench が分けられたときだけ提出に入る(contrib_schema は 3 のまま)。"""
    base = v3(db)
    assert "stages" not in base["measurement"]
    sub = contrib.build_submission(meas(), pc(), stages={**STAGES, "extra": 1})
    assert sub["contrib_schema"] == 3
    assert sub["measurement"]["stages"] == STAGES  # 決まった項目だけ
    assert errors(sub, db) == []


@pytest.mark.parametrize(
    ("edit", "needle"),
    [
        (lambda s: s["sampling"].update(peak_gb=7.5), "peak_gb"),  # 実測のピーク(6.9)より大きい
        (lambda s: s["sampling"].update(min_gb=0.5), "min_gb"),  # 最大より大きい最小
        (lambda s: s["sampling"].update(samples=0), "samples"),
        (lambda s: s.update(sampler_runs=0), "sampler_runs"),
        (lambda s: s["sampling"].update(extra=1), "extra"),
        (lambda s: s.pop("after_sampling"), "after_sampling"),
    ],
)
def test_bad_stage_peaks_are_rejected(db, edit, needle):
    sub = contrib.build_submission(meas(), pc(), stages=copy.deepcopy(STAGES))
    edit(sub["measurement"]["stages"])
    assert any(needle in e for e in errors(sub, db)), errors(sub, db)


def test_stage_peaks_need_torch_reservations_and_schema_3(db):
    sub = contrib.build_submission(meas(vram_metric="gpu_used"), pc(), stages=copy.deepcopy(STAGES))
    assert any("vram_metric" in e and "stages" in e for e in errors(sub, db)), errors(sub, db)
    old = contrib.build_submission(meas(), pc(), stages=copy.deepcopy(STAGES))
    old["contrib_schema"] = 2
    assert any("stages" in e and "contrib_schema 3" in e for e in errors(old, db))
    # 段の 1 つが null(その時間に読めた回が無い)は受け付ける
    ok = contrib.build_submission(meas(), pc(), stages={**STAGES, "before_sampling": None})
    assert errors(ok, db) == []


def test_estimate_does_not_use_the_measurement_or_this_pcs_measurements(monkeypatch, tmp_path):
    """_measure_run の見積もりは同梱の実測だけで作る(キャッシュの実測・送る前の提出を入れない)。
    同梱の実測からも同じ GPU・同じモデルのもの(作者の PC の実測かもしれない)を除く。"""
    seen = {}

    def spy(entry, system, settings, measurements, *, exclude_gpu=None):
        seen.update(ms=measurements, gpu=exclude_gpu)
        return None

    monkeypatch.setattr(contrib, "measurement_estimate", spy)
    cli._BUNDLED_MEASUREMENTS.clear()
    # この PC のキャッシュに同じモデルの実測がある(見積もりには入れない)
    d = cache_dir() / "measurements"
    d.mkdir(parents=True, exist_ok=True)
    row = asdict(meas(note="cache-marker"))
    (d / "x.json").write_text(json.dumps({"measurements": [row]}), encoding="utf-8")
    assert any(x.note == "cache-marker" for x in load_measurements(include_cache=True))
    entry = load_db(use_cache=False).get("image_sdxl_simple")
    cli._measure_estimate(entry, pc(), meas())
    assert "cache-marker" not in {x.note for x in seen["ms"]}
    assert len(seen["ms"]) == len(load_measurements(include_cache=False))
    assert seen["gpu"] == "NVIDIA GeForce RTX 3070"


def test_estimate_excludes_bundled_measurements_of_this_gpu_and_model(db):
    """同じ GPU・同じモデルの実測は見積もりに使わない(その実測があっても無くても同じ見積もり)。"""
    e = db.get("image_sdxl_simple")
    s = Settings(width=832, height=832, steps=20)
    owner = meas(peak_vram_gb=3.0, origin=None)  # 作者の PC の(この PC かもしれない)実測
    base = contrib.measurement_estimate(e, pc(), s, [], exclude_gpu="NVIDIA GeForce RTX 3070")
    with_owner = contrib.measurement_estimate(e, pc(), s, [owner], exclude_gpu="NVIDIA GeForce RTX 3070")
    assert with_owner == base and base["measurements"] == "bundled-excluding-this-gpu"
    used = contrib.measurement_estimate(e, pc(), s, [owner])  # 除かなければ実測が使われる
    assert used != base and "measurements" not in used


def test_measurement_estimate_needs_this_pc():
    assert contrib.measurement_estimate(None, pc(), None, []) is None
    assert contrib.measurement_estimate(object(), None, None, []) is None


# --- 2. 時間の内訳(/history の時刻と、ログの進み具合の行) ------------------------------------------


def _logs(t0: float) -> list[dict]:
    def at(dt):
        return datetime.fromtimestamp(t0 + dt).isoformat()

    return [
        {"t": at(0.2), "m": "got prompt"},
        {"t": at(3.0), "m": "\r  0%|          | 0/20 [00:00<?, ?it/s]"},
        {"t": at(13.0), "m": "\r100%|##########| 20/20 [00:10<00:00,  2.00it/s]"},
        {"t": at(14.0), "m": "\r  0%|          | 0/10 [00:00<?, ?it/s]"},
        {"t": at(19.0), "m": "\r100%|##########| 10/10 [00:05<00:00,  2.00s/it]"},
    ]


def test_timing_breakdown_from_history_and_logs():
    t0 = 1_800_000_000.0
    entry = {
        "status": {
            "messages": [
                ["execution_start", {"timestamp": (t0 + 0.5) * 1000}],
                ["execution_cached", {"nodes": [], "timestamp": (t0 + 0.5) * 1000}],
                ["execution_success", {"timestamp": (t0 + 21.0) * 1000}],
            ]
        }
    }
    t = bench.timing_breakdown(entry, _logs(t0), t0, 21.5, local=True, cold_load=False, steps=[20, 10])
    assert t["queue_s"] == 0.5 and t["load_s"] == 2.5  # サンプラの始まり(3.0)− 実行の始まり(0.5)
    assert t["sampling_s"] == 15.0 and t["sec_per_step"] == 0.5  # 10 秒 + 5 秒 / 30 ステップ
    assert t["decode_s"] == 2.0 and t["save_s"] is None and t["total_s"] == 21.5
    assert t["cold_load"] is False
    # 別の PC の ComfyUI(時計が違う): 時刻を比べる項目は出さない
    far = bench.timing_breakdown(entry, _logs(t0), t0, 21.5, local=False, steps={20, 10})
    assert far["queue_s"] is None and far["load_s"] is None and far["decode_s"] is None
    assert far["sampling_s"] == 15.0 and far["cold_load"] is None
    # ログが無い・読めない時刻: 取れない項目は null
    none = bench.timing_breakdown(entry, [{"t": "1", "m": "start"}], t0, 21.5, local=True, steps=[20])
    assert none["sampling_s"] is None and none["sec_per_step"] is None and none["queue_s"] == 0.5


# --- 3. VRAM を超えた結果 ----------------------------------------------------------------------------


def test_spilled_is_marked_and_accepted_only_with_the_mark(db):
    sub = v3(db, peak_vram_gb=9.31)
    mm = sub["measurement"]
    assert mm["spilled"] is True and mm["spilled_gb"] == 1.31
    assert errors(sub, db) == []
    no_mark = copy.deepcopy(sub)
    del no_mark["measurement"]["spilled"], no_mark["measurement"]["spilled_gb"]
    assert any("exceeds the GPU total" in e or "超えています" in e for e in errors(no_mark, db))
    fake = copy.deepcopy(v3(db))
    fake["measurement"].update(spilled=True, spilled_gb=0.5)  # 超えていないのに印
    assert errors(fake, db)
    oom = copy.deepcopy(sub)
    oom["measurement"]["outcome"] = "oom"
    assert errors(oom, db)  # spilled は ok だけ
    huge = copy.deepcopy(sub)
    huge["measurement"].update(peak_vram_gb=60.0, spilled_gb=52.0)  # GPU + メインメモリを超える
    assert errors(huge, db)
    old = copy.deepcopy(sub)
    old["contrib_schema"] = 2
    assert any("contrib_schema 3" in e for e in errors(old, db))


def test_spilled_result_is_sent_with_a_mark(monkeypatch):
    def run(opts, log=None, progress=None):
        m = meas(peak_vram_gb=9.31, model_id=opts.model_id, settings=Settings())
        return bench.BenchResult(measurement=m, workflow_source="t", warnings=[], timing=dict(TIMING))

    monkeypatch.setattr(bench, "run", run)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: pc())
    args = cli.build_parser().parse_args(["share"])
    e = load_db(use_cache=False).get("image_sdxl_simple")
    out = cli._measure_run(args, "http://127.0.0.1:8188", e, None)
    assert out["submission"] is not None and not out["unsendable"]
    data = json.loads(Path(out["submission"]).read_text(encoding="utf-8"))
    assert data["measurement"]["spilled"] is True and data["measurement"]["timing"]["total_s"] == 41.2
    assert data["measurement"]["estimate"]["basis"] in ("measured", "estimated") and data["db"]


def _write(root: Path, data: dict) -> Path:
    return contrib.write_submission(data, str(root) + "/")


def test_aggregate_and_calibrate_ignore_spilled_and_failures(db, tmp_path):
    root = tmp_path / "contrib"
    good = json.loads(GOOD[0].read_text(encoding="utf-8"))
    _write(root, good)
    spilled = v3(
        db, peak_vram_gb=9.31, model_id="image_sdxl_simple", settings=Settings(width=1024, height=1024)
    )
    _write(root, spilled)
    fail = contrib.build_failures(
        [
            contrib.failure_record(
                "image_sdxl_simple", "oom", seconds=3.0, error_type="torch.OutOfMemoryError"
            )
        ],
        pc(),
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        comfyui_version="0.40.0",
        date="2026-10-28",
    )
    _write(root, fail)
    subs, reports = contrib.load_dir(root, db)
    assert all(r.ok for r in reports), [r.errors for r in reports]
    assert len(reports) == 3 and len(subs) == 2  # 失敗の記録は検査するが読み込まない
    rows = contrib.aggregate(subs)
    assert len(rows) == 1 and rows[0]["settings"].get("width") == 832  # spilled(1024)は行にならない
    doc = contrib.community_document(rows, subs)
    cal_db = Database(models=db.models, measurements=parse_measurements(doc))
    for r in calibrate.build_rows(cal_db) + calibrate.build_check_rows(cal_db):
        assert getattr(r, "peak", None) != 9.31


# --- 4. 失敗の記録 ------------------------------------------------------------------------------------


def _failures(db, recs) -> dict:
    return contrib.build_failures(
        recs,
        pc(),
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        driver="566.03",
        backend="cuda",
        comfyui_version="0.40.0",
        db_info=contrib.db_info(db),
        date="2026-10-28",
    )


def test_failure_records_are_built_and_accepted(db):
    recs = [
        contrib.failure_record(
            "image_sdxl_simple", "missing_nodes", missing_nodes=["LoadMoGeModel"], before_run=True
        ),
        contrib.failure_record("image_sdxl_simple", "timeout", seconds=1800.4, timeout_s=1800),
        contrib.failure_record(
            "image_sdxl_simple", "error", seconds=3.0, error_type="ValueError", node_type="KSampler"
        ),
        contrib.failure_record(
            "image_sdxl_simple", "oom", seconds=5.0, error_type="torch.OutOfMemoryError", peak_vram_gb=7.8
        ),
    ]
    data = _failures(db, recs)
    assert data["kind"] == contrib.FAILURES_KIND and len(data["failures"]) == 4
    assert errors(data, db) == []
    path = contrib.suggested_path(data)
    assert path.startswith("data/contrib/rtx-3070/2026-10-28-") and path.endswith("-failures.json")
    assert contrib.issue_title(data) == "measurement failures: 4 on NVIDIA GeForce RTX 3070"
    # 名前の決まり: 失敗の記録のファイル名は -failures.json、実測は付けない
    r = contrib.validate_files([_write_named(db, data, "2026-10-28-00000000.json")], db)[0]
    assert any(e["code"] == "filename" for e in r.errors)


def _write_named(db, data, name) -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp()) / "rtx-3070"
    d.mkdir()
    p = d / name
    p.write_text(contrib.dumps(data), encoding="utf-8")
    return p


@pytest.mark.parametrize("outcome", ["interrupted", "interference", "ok"])
def test_interrupted_and_interference_are_never_sent(db, outcome):
    assert contrib.failure_record("image_sdxl_simple", outcome) is None
    data = _failures(db, [contrib.failure_record("image_sdxl_simple", "error")])
    data["failures"][0]["outcome"] = outcome
    assert any("outcome" in e for e in errors(data, db))


def test_interrupted_run_is_not_a_failure_record():
    entry = {"status": {"status_str": "error", "messages": [["execution_interrupted", {"timestamp": 1}]]}}
    kind = bench._history_error_kind(entry, {})
    assert kind["interrupted"] is True
    m = meas(outcome="error")
    res = bench.BenchResult(measurement=m, workflow_source="t", warnings=[], error_kind=kind)
    assert cli._failure_from_result(res, type("E", (), {"id": "image_sdxl_simple"})()) is None
    m.standard = False  # ほかのプログラムが GPU を使った(interference)など、標準でない失敗も送らない
    res = bench.BenchResult(
        measurement=m, workflow_source="t", warnings=[], error_kind={"interrupted": False}
    )
    assert cli._failure_from_result(res, type("E", (), {"id": "image_sdxl_simple"})()) is None


def test_failure_files_need_schema_3_and_known_fields(db):
    data = _failures(db, [contrib.failure_record("image_sdxl_simple", "timeout", seconds=10, timeout_s=10)])
    old = copy.deepcopy(data)
    old["contrib_schema"] = 2
    assert any("contrib_schema 3" in e for e in errors(old, db))
    odd = copy.deepcopy(data)
    odd["failures"][0]["message"] = "boom"
    assert any("unknown_field" in e for e in errors(odd, db))
    checks = {c["id"]: c for c in contrib.privacy_checklist(odd, contrib.dumps(odd))}
    assert checks["known_fields"]["ok"] is False and "failures[0].message" in checks["known_fields"]["detail"]
    empty = copy.deepcopy(data)
    empty["failures"] = []
    assert errors(empty, db)


# --- 5. 個人の情報の点検がすべての新しい項目に効く ----------------------------------------------------


SECRET_TEXTS = [
    r"C:\Users\bobsecret\ComfyUI\models\x.safetensors",
    "/home/bobsecret/ComfyUI/models/x.safetensors",
    "bobsecret@example.org",
]


def test_error_texts_with_paths_never_reach_the_payload(db, monkeypatch):
    monkeypatch.setattr(contrib, "_identity_words", lambda: ["bobsecret", "DESKTOP-BOBPC"])
    wf = {"5": {"class_type": "KSampler", "inputs": {}}}
    recs = []
    for text in SECRET_TEXTS:
        # ComfyUI が受け付けなかった: 文(message・details)にパス
        body = {
            "error": {"type": "prompt_outputs_failed_validation", "message": text, "details": text},
            "node_errors": {
                "5": {
                    "class_type": "KSampler",
                    "errors": [{"type": "required_input_missing", "message": text}],
                }
            },
        }
        f = bench._prompt_error_kind(body, wf)
        assert f["error_type"] == "required_input_missing" and f["node_type"] == "KSampler"
        recs.append(contrib.failure_record("image_sdxl_simple", **f))
        # 実行中の失敗: 例外の文にパス、型名・ノード名に名前を入れても残らない
        entry = {
            "status": {
                "status_str": "error",
                "messages": [
                    [
                        "execution_error",
                        {
                            "exception_type": text,
                            "exception_message": text,
                            "node_type": text,
                            "node_id": "5",
                        },
                    ]
                ],
            }
        }
        k = bench._history_error_kind(entry, wf)
        assert k["node_type"] is None  # 送ったワークフローに無い名前は入れない
        recs.append(contrib.failure_record("image_sdxl_simple", "error", error_type=k["error_type"]))
        recs.append(
            contrib.failure_record("image_sdxl_simple", "missing_nodes", missing_nodes=[text, "OkNode"])
        )
    recs.append(contrib.failure_record("image_sdxl_simple", "error", error_type="bobsecret.Error"))
    recs.append(contrib.failure_record("image_sdxl_simple", "error", node_type="DESKTOP-BOBPC"))
    data = _failures(db, recs)
    text = contrib.dumps(data)
    for word in ("bobsecret", "Users", "home", "example.org", "BOBPC", "boom"):
        assert word not in text
    assert errors(data, db) == []
    assert all(c["ok"] is not False for c in contrib.privacy_checklist(data, text))


@pytest.mark.parametrize(
    ("where", "value"),
    [
        (("failures", 0, "error_type"), r"C:\Users\bob\x"),
        (("failures", 0, "node_type"), "/home/bob/x"),
        (("failures", 0, "missing_nodes"), ["/home/bob/x"]),
        (("db", "generated_at"), "2026-10-21T06:20:18Z"),
        (("db", "sha256"), "bob@example.org"),
    ],
)
def test_validator_rejects_personal_text_in_failure_files(db, where, value):
    data = _failures(db, [contrib.failure_record("image_sdxl_simple", "error", error_type="ValueError")])
    if where[0] == "failures" and where[2] == "missing_nodes":
        data["failures"][0]["outcome"] = "missing_nodes"
        data["failures"][0].pop("error_type", None)
        data["failures"][0].pop("node_type", None)
    node = data
    for k in where[:-1]:
        node = node[k]
    node[where[-1]] = value
    assert errors(data, db)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("estimate", {"fit_level": "good", "note": r"C:\Users\bob"}),
        ("estimate", {"fit_level": "C:/Users/bob"}),
        ("timing", {"queue_s": "/home/bob"}),
        ("timing", {"host": "DESKTOP-ABC1234"}),
        ("spilled", "yes"),
    ],
)
def test_privacy_and_shape_cover_the_new_measurement_fields(db, key, value):
    sub = v3(db)
    sub["measurement"][key] = value
    assert errors(sub, db)


# --- 6. schema 2 の提出も受け取る ----------------------------------------------------------------------


def test_schema_1_and_2_submissions_are_still_accepted(db):
    for p in GOOD:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["contrib_schema"] in (1, 2)
        assert errors(data, db) == []
    two = json.loads(GOOD[0].read_text(encoding="utf-8"))
    two["contrib_schema"] = 2
    assert errors(two, db) == []


def test_intake_accepts_failure_files_and_spilled(db):
    sub = v3(db, peak_vram_gb=9.31)
    body = "### Measurement (JSON)\n\n```json\n" + contrib.dumps(sub) + "```\n"
    res = contrib.intake_issue(body, "alice", 7, db=db, existing=None)
    assert res.ok, res.reasons
    fail = _failures(db, [contrib.failure_record("image_sdxl_simple", "timeout", seconds=9, timeout_s=9)])
    body = "```json\n" + contrib.dumps(fail) + "```\n"
    res = contrib.intake_issue(body, "alice", 8, db=db, existing=None)
    assert res.ok, res.reasons
    assert res.path_in_repo.endswith("-failures.json") and res.title.startswith("measurement failures: 1")
    assert contrib._REPO_PATH.fullmatch(res.path_in_repo)


def test_long_failure_files_fall_back_to_pasting(db):
    recs = [
        contrib.failure_record(
            f"image_model_{i:02d}",
            "missing_nodes",
            missing_nodes=[f"SomeCustomNodeName{j}" for j in range(8)],
        )
        for i in range(40)
    ]
    data = _failures(db, recs)
    urls = contrib.share_urls(data, contrib.dumps(data))
    assert urls["issue_url"] is None and urls["issue_url_length"] > contrib.SHARE_URL_LIMIT
    assert urls["form_url"].startswith("https://github.com/laxshmate/genmodelfit/issues/new")


# --- 送る前の画面 --------------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_screen_shows_the_new_fields(db, lang):
    from genmodelfit import i18n

    sub = v3(db, peak_vram_gb=9.31)
    fail = _failures(
        db,
        [
            contrib.failure_record(
                "image_sdxl_simple", "missing_nodes", missing_nodes=["LoadMoGeModel"], before_run=True
            ),
            contrib.failure_record(
                "image_sdxl_simple", "error", error_type="ValueError", node_type="KSampler"
            ),
        ],
    )
    with i18n.forced(lang):
        text = "\n".join(cli.format_submission(sub))
        ftext = "\n".join(cli.format_submission(fail))
    words = {
        "ja": [
            "見積もり:",
            "時間の内訳:",
            "VRAM 超え:",
            "一覧の版:",
            "失敗の記録: 2 件",
            "足りないノード LoadMoGeModel",
        ],
        "en": ["Estimate:", "Timing:", "Spilled:", "List:", "Failures:   2", "missing nodes LoadMoGeModel"],
    }[lang]
    for w in words:
        assert w in text + "\n" + ftext, w
    assert "ValueError" in ftext and "KSampler" in ftext


def test_pr_body_lists_failure_files(db):
    from genmodelfit import github

    fail = _failures(db, [contrib.failure_record("image_sdxl_simple", "timeout", seconds=9, timeout_s=9)])
    body = github.pr_body([v3(db), fail])
    assert "Failure records" in body and "timeout" in body


# --- share: 失敗の記録を 1 ファイルにする ----------------------------------------------------------------


def test_cold_load_is_known_only_for_a_comfyui_we_started():
    args = cli.build_parser().parse_args(["share"])
    started = object()
    cli._share_cold_load(args, "http://127.0.0.1:8188", "a", started)
    assert args._cold_load is True
    cli._share_cold_load(args, "http://127.0.0.1:8188", "a", started)
    assert args._cold_load is False  # 同じ ComfyUI で 2 回目(/free のあと)
    cli._share_cold_load(args, "http://127.0.0.1:8188", "b", None)  # 利用者の ComfyUI: 前は分からない
    assert args._cold_load is None
    cli._share_cold_load(args, "http://127.0.0.1:8188", "b", None)
    assert args._cold_load is False


def test_share_writes_one_failure_file_and_not_twice(monkeypatch):
    s = pc()
    args = cli.build_parser().parse_args(["share"])
    args._share_failures = [
        contrib.failure_record("image_sdxl_simple", "timeout", seconds=30.0, timeout_s=30.0),
    ]
    skipped = [{"model_id": "image_sdxl_simple", "name": "x", "missing_nodes": ["LoadMoGeModel"]}]

    class Nodes:
        def comfyui_version(self):
            return "0.40.0"

    p = cli._share_write_failures(args, s, "http://127.0.0.1:8188", Nodes(), skipped)
    assert p is not None and p.name.endswith("-failures.json") and p.parent.name == "rtx-3070"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert [f["outcome"] for f in data["failures"]] == ["timeout", "missing_nodes"]
    assert data["comfyui_version"] == "0.40.0" and data["failures"][1]["before_run"] is True
    saved = measure.saved_submissions()
    assert [x.to_dict()["kind"] for x in saved] == ["failures"] and saved[0].to_dict()["count"] == 2
    # 同じ share でもう 1 回・次の share で同じ失敗: 書かない
    assert cli._share_write_failures(args, s, "http://127.0.0.1:8188", Nodes(), skipped) is None
    args2 = cli.build_parser().parse_args(["share"])
    assert cli._share_write_failures(args2, s, "http://127.0.0.1:8188", Nodes(), skipped) is None
    # 別の PC の ComfyUI で何も測れなかった: どの GPU か分からないので書かない
    args3 = cli.build_parser().parse_args(["share"])
    args3._share_failures = [contrib.failure_record("image_x", "error", error_type="ValueError")]
    assert cli._share_write_failures(args3, s, "http://192.0.2.1:8188", None, []) is None


# --- 確かめ(Checker)の指摘への試験(#132) -----------------------------------------------------------


def test_only_the_samplers_progress_lines_count():
    """ほかの処理の進み具合(Hugging Face の取得・ステップ数の違う行)を混ぜない。
    合わない・長すぎる値は null。"""
    t0 = 1_800_000_000.0
    entry = {
        "status": {
            "messages": [
                ["execution_start", {"timestamp": (t0 + 1) * 1000}],
                ["execution_success", {"timestamp": (t0 + 31) * 1000}],
            ]
        }
    }

    def at(dt):
        return datetime.fromtimestamp(t0 + dt).isoformat()

    noise = [
        {"t": at(2), "m": "Fetching 3 files: 100%|###| 3/3 [25:00:00<00:00, 0.01s/it]"},
        {"t": at(3), "m": "\r100%|####| 600/600 [00:15<00:00, 40.0it/s]"},  # 音の復元など(ACE-Step)
    ]
    sampler = [{"t": at(20), "m": "\r100%|####| 50/50 [00:12<00:00,  4.1it/s]"}]
    t = bench.timing_breakdown(entry, noise + sampler, t0, 32.0, local=True, steps={50})
    assert t["sampling_s"] == 12.0 and t["sec_per_step"] == 0.24
    t = bench.timing_breakdown(entry, noise, t0, 32.0, local=True, steps={50})
    assert t["sampling_s"] is None and t["sec_per_step"] is None  # 600 ステップの行は使わない
    assert bench.timing_breakdown(entry, noise + sampler, t0, 32.0, local=True)["sampling_s"] is None
    long = [{"t": at(20), "m": "\r100%|####| 50/50 [01:40<00:00,  0.5it/s]"}]  # 実行(30 秒)より長い
    assert bench.timing_breakdown(entry, long, t0, 32.0, local=True, steps={50})["sampling_s"] is None


def test_sampler_steps_from_the_workflow():
    wf = {
        "1": {"class_type": "KSampler", "inputs": {"steps": 25}},
        "2": {
            "class_type": "KSamplerAdvanced",
            "inputs": {"steps": 30, "start_at_step": 10, "end_at_step": 40},
        },
        "3": {"class_type": "X", "inputs": {"steps": ["1", 0]}},
    }
    assert bench.sampler_steps(wf) == {25, 30, 20}


def test_bad_timing_never_blocks_sending_the_measurement(monkeypatch):
    def run(opts, log=None, progress=None):
        m = meas(model_id=opts.model_id, settings=Settings())
        bad = dict(TIMING, sampling_s=90005.0, sec_per_step=3913.0)  # 検査に通らない値
        return bench.BenchResult(measurement=m, workflow_source="t", warnings=[], timing=bad)

    monkeypatch.setattr(bench, "run", run)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: pc())
    e = load_db(use_cache=False).get("image_sdxl_simple")
    out = cli._measure_run(cli.build_parser().parse_args(["share"]), "http://127.0.0.1:8188", e, None)
    assert out["submission"] is not None and not out["unsendable"]
    data = json.loads(Path(out["submission"]).read_text(encoding="utf-8"))
    assert "timing" not in data["measurement"] and data["measurement"]["peak_vram_gb"] == 6.9


def test_node_names_are_kept_safely_and_never_empty(db):
    rec = contrib.failure_record(
        "image_x",
        "missing_nodes",
        missing_nodes=[
            "LayerMask: MaskGrow",
            "画像のリサイズ",
            r"C:\Users\bob\x",
            "x" * 150,
            "CR Prompt List",
        ],
    )
    assert rec["missing_nodes"] == ["CR Prompt List", "LayerMask: MaskGrow", "画像のリサイズ"]
    assert rec["other_nodes"] == 2  # 形に合わない名前は数だけ
    only_bad = contrib.failure_record(
        "image_x", "missing_nodes", missing_nodes=["/home/bob/n", "a.example.com"]
    )
    assert only_bad["missing_nodes"] == [] and only_bad["other_nodes"] == 2
    assert contrib.failure_record("image_x", "missing_nodes", missing_nodes=[]) is None
    assert errors(_failures(db, [rec, only_bad]), db) == []


def test_one_bad_record_drops_only_that_record(monkeypatch):
    args = cli.build_parser().parse_args(["share"])
    good = contrib.failure_record("image_sdxl_simple", "timeout", seconds=30.0, timeout_s=30.0)
    bad = dict(good, model_id="bad id with spaces")  # 一覧の ID の形でない
    args._share_failures = [bad, good]
    p = cli._share_write_failures(args, pc(), "http://127.0.0.1:8188", None, [])
    assert p is not None
    assert [f["model_id"] for f in json.loads(p.read_text(encoding="utf-8"))["failures"]] == [
        "image_sdxl_simple"
    ]


def test_failure_files_stay_under_the_size_limit(db):
    recs = [
        contrib.failure_record(
            f"image_m{i:02d}", "missing_nodes", missing_nodes=[f"N{j:02d}" + "a" * 90 for j in range(30)]
        )
        for i in range(50)
    ]
    data = _failures(db, recs)
    assert 1 <= len(data["failures"]) < 50
    assert len(contrib.dumps(data).encode("utf-8")) <= contrib.MAX_FILE_BYTES
    assert errors(data, db) == []


def test_receiving_side_applies_every_detector_to_failure_strings(db):
    data = _failures(db, [contrib.failure_record("image_sdxl_simple", "timeout", seconds=9, timeout_s=9)])
    data["failures"].append(
        {"model_id": "image_z", "outcome": "error", "node_type": "Taro Yamada 090-1234-5678"}
    )
    data["failures"].append(
        {"model_id": "image_z", "outcome": "missing_nodes", "missing_nodes": ["taro.example.com"]}
    )
    errs = errors(data, db)
    assert any("failures[1].node_type" in e for e in errs) and any(
        "failures[2].missing_nodes" in e for e in errs
    )
    odd = _failures(db, [contrib.failure_record("image_sdxl_simple", "timeout", seconds=9, timeout_s=9)])
    odd["failures"] += [5, "x"]  # 記録でないもの: 断るが、落ちない
    assert errors(odd, db) and contrib.issue_title(odd).startswith("measurement failures: 3")


def test_missing_model_files_are_not_a_failure_record():
    body = {
        "node_errors": {
            "4": {"class_type": "CheckpointLoaderSimple", "errors": [{"type": "value_not_in_list"}]}
        }
    }
    assert (
        bench._prompt_error_kind(body, {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {}}}) is None
    )


def test_missing_nodes_are_not_sent_twice_by_measure_and_share():
    a = contrib.failure_record(
        "image_x", "missing_nodes", missing_nodes=["B", "A"], settings=Settings(width=512, height=512)
    )
    b = contrib.failure_record("image_x", "missing_nodes", missing_nodes=["A", "B"], before_run=True)
    assert measure.failure_key(a, "0.40.0") == measure.failure_key(b, None)
