# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""実測の無い式を先に勧める・GPU 全体の使用量(measurement.device)を照合まで通す・nvidia-smi の開始前の値を
降ろした後に読み直す(#200)。

本物の GPU・ComfyUI・nvidia-smi には触らない(ComfyUI は test_bench の偽のサーバー)。提出の値は試験用に
作った架空の値。件数は同梱の一覧から数え、決め打ちしない。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from datetime import date

import pytest
from test_bench import GIB, GPU0, FakeComfy, _opts, _quiet

from genmodelfit import bench, cli, contrib, coverage, gpustat, hardware, i18n, measure
from genmodelfit import calibrate as cal
from genmodelfit.db import Database, bundled_text, load, parse_measurements
from genmodelfit.estimate import estimate, is_community, no_size_input, settings_for, stages_for
from genmodelfit.models import Gpu, Measurement, Settings, SystemInfo

SDXL = "image_sdxl_simple"
TODAY = date(2026, 11, 17)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """tests/test_bench.py と同じ偽物(空いている RTX 3070 が 1 枚。本物の nvidia-smi は呼ばない)。"""
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(bench, "smi_gpus", lambda: [dict(GPU0)])
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)


@pytest.fixture(scope="module")
def db() -> Database:
    return load(use_cache=False)


@pytest.fixture(scope="module")
def maint() -> list[Measurement]:
    return parse_measurements(json.loads(bundled_text("measurements.json")))


def pc(vram: float = 8.0, name: str = "NVIDIA GeForce RTX 3070") -> SystemInfo:
    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram, backend="cuda")
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])


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
        "seconds": 12.0,
        "comfyui_version": "0.40.0",
    }
    d.update(kw)
    return Measurement(**d)


def _device(
    before: float = 1.2,
    peak: float = 6.8,
    after: float | None = 6.5,
    tool: str = "nvidia-smi",
    torch: float | None = 0.05,
):
    return {"tool": tool, "before_gb": before, "peak_gb": peak, "after_gb": after, "before_torch_gb": torch}


# --- 1. 勧める順 -------------------------------------------------------------------------------------


def _all_keys(db: Database) -> set[str]:
    return {k for e in db.models for k in coverage.formula_keys(e)}


def _pairs(sugg) -> list[tuple[str, tuple[str, ...]]]:
    return [(x.model_id, tuple(x.args)) for x in sugg]


def test_formula_keys_follow_the_stage_formula(db):
    staged = db.get(SDXL)
    st = stages_for(staged, settings_for(staged, None, sized=not no_size_input(staged)))
    assert coverage.formula_keys(staged) == (f"work:{st.work}", f"vae:{st.vae}")
    plain = db.get("utility_moge_depth_estimation")  # 段ごとに当てられない
    assert coverage.formula_keys(plain) == (f"model:{plain.id}",)


def test_measured_keys(db):
    staged = db.get(SDXL)
    keys = set(coverage.formula_keys(staged))
    m = _measurement()
    assert coverage.measured_keys(db.models, [m]) == (keys, set())
    st = {"sampler_runs": 1, "before_sampling": None, "sampling": None, "after_sampling": None}
    assert coverage.measured_keys(db.models, [replace(m, stages=st)]) == (keys, keys)
    # 使えない実測(OOM・ピーク 0)・一覧に無いモデルは数えない
    bad = [replace(m, outcome="oom"), replace(m, peak_vram_gb=0.0), replace(m, model_id="image_not_in_list")]
    assert coverage.measured_keys(db.models, bad) == (set(), set())


def test_order_is_the_old_order_when_every_formula_is_measured(db, monkeypatch):
    s = pc()
    real = measure.suggestions(db, s, variants_per_model=None)
    every = _all_keys(db)
    monkeypatch.setattr(coverage, "measured_keys", lambda models, ms: (set(every), set(every)))
    same = measure.suggestions(db, s, variants_per_model=None)
    assert same and all(x.new_formulas == 0 for x in same)
    assert _pairs(same) == _pairs(sorted(same, key=measure._order))  # 今までの並び
    assert set(_pairs(same)) == set(_pairs(real))  # 勧める集合は同じ(並びだけが変わる)
    assert len(_pairs(real)) == len(set(_pairs(real)))


def _order_tail(x) -> tuple:
    return measure._order(x)[2:]  # 時間 → 落とす量 → 名前 …


def test_unmeasured_formula_first_within_the_same_installed_state(db, monkeypatch):
    s = pc()
    every = _all_keys(db)
    monkeypatch.setattr(coverage, "measured_keys", lambda models, ms: (set(every), set(every)))
    old = measure.suggestions(db, s, variants_per_model=None)
    last = old[-1]  # 今までの並びで最後(時間が長い・落とす量が多い)
    assert all(x.installed.state == last.installed.state for x in old)
    left = every - set(last.formulas)
    monkeypatch.setattr(coverage, "measured_keys", lambda models, ms: (set(left), set(left)))
    new = measure.suggestions(db, s, variants_per_model=None)
    pos = {p: i for i, p in enumerate(_pairs(new))}
    # 実測の無い式のモデルは、どの式にも実測のあるモデルより先に出る(時間が長くても)
    fresh = [x for x in new if set(x.formulas) & set(last.formulas)]
    known = [x for x in new if not set(x.formulas) & set(last.formulas)]
    assert fresh and known
    first = pos[(fresh[0].model_id, tuple(fresh[0].args))]
    assert first == 0 and first < min(pos[(x.model_id, tuple(x.args))] for x in known)
    # 今までの並びでは最後の式が、今は先頭(時間・落とす量は実測のある式のモデルより短くない)
    assert new[0].new_formulas > 0 and _order_tail(new[0]) >= min(_order_tail(x) for x in known)
    assert all(x.new_formulas == 0 for x in known)


def _sugg(mid: str, formulas: tuple[str, ...], seconds: int = 60, state: str = "unknown", remeasure=None):
    return measure.Suggestion(
        model_id=mid,
        model_name=mid,
        name=mid,
        modality="image",
        precision=None,
        fit_level="good",
        installed=measure.Installed(state=state),
        seconds=seconds,
        seconds_basis="typical",
        group_key=mid,
        remeasure=remeasure,
        formulas=formulas,
    )


def test_same_formula_is_not_suggested_twice_in_a_row():
    a = _sugg("a", ("work:x", "vae:v"), seconds=10)
    b = _sugg("b", ("work:x", "vae:v"), seconds=20)  # a と同じ式
    c = _sugg("c", ("work:y", "vae:w"), seconds=90)  # 別の式(時間は長い)
    d = _sugg("d", ("work:z", "vae:u"), seconds=5)  # 式に実測がある
    out = measure._pick_order([d, c, b, a], {"work:z", "vae:u"}, {"work:z", "vae:u"})
    # b は a と同じ式なので、時間の長い c の後(実測のある d とは、残りの鍵で並ぶ: 時間の短い d が先)
    assert [x.model_id for x in out] == ["a", "c", "d", "b"]
    assert [x.new_formulas for x in out] == [2, 2, 0, 0]
    # VAE だけ同じなら、生成の段の分だけ新しい(1)。最初はどれも 2 なので時間の短い e から
    e = _sugg("e", ("work:q", "vae:v"), seconds=1)
    out = measure._pick_order([a, b, e], set(), set())
    assert [(x.model_id, x.new_formulas) for x in out] == [("e", 2), ("a", 1), ("b", 0)]
    # 結果は入力の順によらない
    assert [x.model_id for x in measure._pick_order([b, a, e], set(), set())] == ["e", "a", "b"]
    assert out[0].to_dict()["new_formulas"] == 2


def test_stage_peaks_break_ties_and_old_rules_come_first():
    # 実測はあるが段ごとの山の無い式は、山のある式より先(時間が長くても)
    a = _sugg("a", ("work:x", "vae:v"), seconds=90)
    b = _sugg("b", ("work:y", "vae:w"), seconds=10)
    keys = {"work:x", "vae:v", "work:y", "vae:w"}
    out = measure._pick_order([b, a], keys, {"work:y", "vae:w"})
    assert [x.model_id for x in out] == ["a", "b"] and all(x.new_formulas == 0 for x in out)
    # 入っているもの → 入っていないもの、まだ測っていない → 測り直し は、実測の無い式より先に効く
    yes = _sugg("yes", ("work:z", "vae:u"), state="yes")
    no = _sugg("no", ("work:x2", "vae:v2"), state="no")
    again = _sugg("again", ("work:x3", "vae:v3"), state="yes", remeasure=("0.39.0", "0.40.0"))
    out = measure._pick_order([again, no, yes], {"work:z", "vae:u"}, set())
    assert [x.model_id for x in out] == ["yes", "no", "again"]


def test_this_pcs_measurement_moves_the_same_formula_back(db):
    s = pc()
    before = measure.suggestions(db, s, variants_per_model=None)
    y = next(x for x in before if x.new_formulas == 2 and x.settings is None)
    twins = [x for x in before if x.formulas == y.formulas]
    # ほかの GPU で測ったこの PC の実測(この GPU の提案からは外れない)。式は実測ありになる
    other = _measurement(model_id=y.model_id, gpu_name="NVIDIA GeForce RTX 5080", gpu_vram_gb=15.92)
    other = replace(other, settings=Settings(), seconds=None)
    after = measure.suggestions(db, s, variants_per_model=None, extra=[other])
    moved = [x for x in after if x.formulas == y.formulas]
    assert moved and all(x.new_formulas == 0 for x in moved)
    first_old = min(_pairs(after).index((x.model_id, tuple(x.args))) for x in moved)
    fresh = [i for i, x in enumerate(after) if x.new_formulas > 0]
    assert fresh and max(fresh) < first_old  # 実測の無い式のモデルが先
    assert len(moved) == len(twins)


def test_measure_json_has_new_formulas(capsys, monkeypatch):
    s = pc()
    monkeypatch.setattr(cli, "_system", lambda args: s)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: s)
    assert cli.main(["measure", "--json", "--limit", "3"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert all(isinstance(x["new_formulas"], int) and 0 <= x["new_formulas"] <= 2 for x in d["suggestions"])


# --- 2. bench の device -------------------------------------------------------------------------------


def test_device_usage_shapes():
    p = bench.Peaks(device_used=int(6 * GIB), gpu_used_mib=6144.0, samples=3, smi_samples=4)
    stats = {"vram_total": 8 * GIB, "vram_free": 7 * GIB}
    got = bench.device_usage(stats, int(1 * GIB), p, int(5.5 * GIB), None, None, None)
    want = {
        "tool": "system_stats",
        "before_gb": 1.0,
        "peak_gb": 6.0,
        "after_gb": 5.5,
        "before_torch_gb": None,
    }
    assert got == want  # torch_vram_total が無ければ開始前の torch は null
    torch = {**stats, "torch_vram_total": int(0.5 * GIB)}
    assert bench.device_usage(torch, int(GIB), p, None, None, None, None)["before_torch_gb"] == 0.5
    # 終わりの値が測っている間の最大より大きければ、それが最大
    assert bench.device_usage(stats, int(GIB), p, int(7 * GIB), None, None, None)["peak_gb"] == 7.0
    assert bench.device_usage(stats, int(GIB), p, None, None, None, None)["after_gb"] is None
    # /system_stats に無ければ nvidia-smi(NVIDIA だけ)
    smi = bench.device_usage({"torch_vram_total": 0}, 0, p, None, (1024.0, 0.0), None, 5120.0)
    assert smi == {
        "tool": "nvidia-smi",
        "before_gb": 1.0,
        "peak_gb": 6.0,
        "after_gb": 5.0,
        "before_torch_gb": 0.0,
    }
    assert bench.device_usage({}, 0, p, None, (1024.0, 0.0), object(), 5120.0) is None  # NVIDIA 以外
    assert bench.device_usage({}, 0, replace(p, smi_samples=0), None, (1024.0, 0.0), None, None) is None
    assert bench.device_usage({}, 0, p, None, None, None, None) is None
    # 最大が開始前より小さい(ほかのプログラムが手放した)ときは作らない
    assert bench.device_usage(stats, int(7 * GIB), p, None, None, None, None) is None


def test_bench_records_device_from_system_stats(capsys, db):
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, free_before=True), log=_quiet)
    with FakeComfy() as fake:  # 偽の ComfyUI は送った後ずっと torch 5 GiB を返すので、別の偽物で
        assert cli.main(["bench", SDXL, "--url", fake.url, "--free", "--json"]) == 0
    # 降ろした後: torch 0 + ほかのアプリ 1 GiB。実行中は torch 5 GiB(偽の ComfyUI は終わっても降ろさない)
    want = {"tool": "system_stats", "before_gb": 1.0, "peak_gb": 6.0, "after_gb": 6.0, "before_torch_gb": 0.0}
    assert res.device == want and res.measurement.device == want
    out = json.loads(capsys.readouterr().out)
    assert out["device"] == want and out["measurement"]["device"] == want
    sub = contrib.build_submission(res.measurement, None, device=res.device)
    assert sub["measurement"]["device"] == want
    # bench の note の文は device_from_note が読める(項目の無い古い提出と同じ文)
    note = contrib.device_from_note(res.measurement.note)
    assert note["delta_gb"] == 5.0 and note["before_torch_gb"] == 0.0


def _smi_reads(monkeypatch, before_free: float, after_free: float, peak: float, last: float):
    """nvidia-smi の読み取りの偽物: 開始前の 5 回は before_free、降ろした後は after_free(MiB)。
    測っている間の読み取り(Monitor._read_smi)は最大 peak・最後 last。"""
    calls = {"n": 0}

    def used(gpu_id):
        calls["n"] += 1
        return (before_free if calls["n"] <= 5 else after_free, 0.0)

    def read_smi(self):
        with self._lock:
            self.peaks.gpu_used_mib = max(self.peaks.gpu_used_mib, peak)
            self.peaks.smi_samples += 2
            self.last_used_mib = last

    monkeypatch.setattr(bench, "gpu_used_now", used)
    monkeypatch.setattr(bench.Monitor, "_read_smi", read_smi)
    return calls


def test_nvidia_smi_start_is_read_again_after_free(monkeypatch):
    calls = _smi_reads(monkeypatch, 4096.0, 1024.0, 6144.0, 5120.0)
    with FakeComfy() as fake:
        fake.no_vram = True  # GPU 全体の使用量を nvidia-smi で読む
        res = bench.run(_opts(fake.url, free_before=True, force=True), log=_quiet)
    assert calls["n"] == 6  # 開始前の 5 回 + 降ろした後の 1 回
    m = res.measurement
    assert "開始前 1.00 GB 使用" in m.note and "最大 6.00 GB" in m.note and "増分 5.00 GB" in m.note
    want = {"tool": "nvidia-smi", "before_gb": 1.0, "peak_gb": 6.0, "after_gb": 5.0, "before_torch_gb": 0.0}
    assert res.device == want


def test_nvidia_smi_start_is_kept_without_free_or_when_unreadable(monkeypatch):
    calls = _smi_reads(monkeypatch, 4096.0, 1024.0, 6144.0, 5120.0)
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url), log=_quiet)  # --free なし: 読み直さない
    assert calls["n"] == 5 and "開始前 4.00 GB 使用" in res.measurement.note
    seq = iter([(4096.0, 0.0)] * 5)
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: next(seq, None))  # 降ろした後は読めない
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, free_before=True), log=_quiet)
    assert "開始前 4.00 GB 使用" in res.measurement.note


def test_other_vendor_start_is_read_again_after_free(monkeypatch):
    """NVIDIA 以外の読み取り役(gpustat.Probe)でも、降ろした後に読み直す(device は作らない)。"""
    monkeypatch.setattr(bench, "smi_gpus", lambda: pytest.fail("NVIDIA 以外の GPU で nvidia-smi を呼んだ"))
    monkeypatch.setattr(gpustat.platform, "system", lambda: "Linux")
    calls = {"n": 0}

    def run(args, timeout):
        if args[0] != "rocm-smi":
            return None
        calls["n"] += 1
        used = 4 * GIB if calls["n"] <= 3 else GIB  # 開始前の 3 回は 4 GiB、降ろした後は 1 GiB
        return 0, json.dumps({"card0": {"GPU use (%)": "3", "VRAM Total Used Memory (B)": str(used)}})

    monkeypatch.setattr(hardware, "_run", run)
    with FakeComfy() as fake:
        fake.device = ("cuda:0 AMD Radeon RX 7900 XTX : native", "cuda")
        fake.pytorch_version = "2.9.1+rocm7.1"
        res = bench.run(_opts(fake.url, free_before=True), log=_quiet)
    assert calls["n"] >= 4 and "rocm-smi" in res.measurement.note
    assert "開始前 1.00 GB 使用" in res.measurement.note
    assert res.device["tool"] == "system_stats"  # /system_stats があればそちら


# --- 2. 提出の measurement.device ------------------------------------------------------------------


def _valid(d: dict, db: Database):
    return contrib.validate_submission(d, db, today=TODAY, check_filename=False)


def _built(device: dict | None = None, **kw) -> dict:
    return contrib.build_submission(_measurement(**kw), None, device=device or _device())


def test_device_round_trip(db):
    d = _built()
    assert d["contrib_schema"] == 3 and d["measurement"]["device"] == _device()
    r = _valid(d, db)
    assert r.ok, r.errors
    assert contrib.to_measurement(d).device == _device()
    plain = contrib.build_submission(_measurement(), None)
    assert "device" not in plain["measurement"] and contrib.to_measurement(plain).device is None
    assert _valid(plain, db).ok
    # 決まった項目だけを入れる
    extra = contrib.build_submission(_measurement(), None, device={**_device(), "pid": 1234})
    assert extra["measurement"]["device"] == _device()
    assert contrib.DEVICE_TOOLS == ("system_stats", "nvidia-smi")


@pytest.mark.parametrize(
    "change, where",
    [
        ({"pid": 1}, "measurement.device.pid"),
        ({"tool": "rocm-smi"}, "measurement.device.tool"),
        ({"before_gb": -0.5}, "measurement.device.before_gb"),
        ({"peak_gb": None}, "measurement.device.peak_gb"),
        ({"after_gb": "x"}, "measurement.device.after_gb"),
        ({"before_gb": 7.0, "peak_gb": 6.0, "after_gb": 5.0}, "measurement.device.peak_gb"),
        ({"after_gb": 6.9}, "measurement.device.after_gb"),
        ({"peak_gb": 8.5, "after_gb": None}, "measurement.device.peak_gb"),  # 7.9 GiB × 1.05 を超える
        ({"before_torch_gb": -0.1}, "measurement.device.before_torch_gb"),
        ({"before_torch_gb": "x"}, "measurement.device.before_torch_gb"),
        ({"before_torch_gb": 9.0}, "measurement.device.before_torch_gb"),  # 総量 × 1.05 を超える
        # system_stats では、開始前の torch は GPU 全体の開始前の値に入っている
        ({"tool": "system_stats", "before_torch_gb": 1.22}, "measurement.device.before_torch_gb"),
    ],
)
def test_device_errors(db, change, where):
    d = _built()
    d["measurement"]["device"] = {**_device(), **change}
    r = _valid(d, db)
    assert not r.ok and any(where in e["message"] for e in r.errors), r.errors


def test_device_missing_key_and_rounding(db):
    for key in ("after_gb", "before_torch_gb"):
        d = _built()
        del d["measurement"]["device"][key]
        r = _valid(d, db)
        assert not r.ok and any(f"measurement.device.{key}" in e["message"] for e in r.errors)
    # 丸めの 0.01 は許す
    d = _built()
    d["measurement"]["device"] = _device(before=1.2, peak=1.19, after=1.2)
    assert _valid(d, db).ok
    d["measurement"]["device"] = _device(after=None, torch=None)
    assert _valid(d, db).ok
    d["measurement"]["device"] = _device(tool="system_stats", torch=1.21)
    assert _valid(d, db).ok
    d["measurement"]["device"] = _device(torch=3.0)  # nvidia-smi では開始前の値と比べない
    assert _valid(d, db).ok


@pytest.mark.parametrize("schema", [1, 2])
def test_device_is_a_schema_3_field(db, schema):
    d = _built()
    d["contrib_schema"] = schema
    d.pop("db", None)
    r = _valid(d, db)
    assert not r.ok and any("$.measurement.device" in e["message"] for e in r.errors), r.errors


# --- note から読む -----------------------------------------------------------------------------------

FREED = (
    "GPU 全体(nvidia-smi)の増分 5.44 GB(開始前 7.82 GB 使用、最大 13.25 GB)。"
    "/system_stats の GPU 全体の増分 7.11 GB。"
    "開始前に /free でモデルを解放(解放前 torch 0.31 GB、解放後 0.09 GB)。"
    "ComfyUI の実行時間 13.07 秒。計測 414 回"
)
NOT_FREED = (
    "/system_stats の GPU 全体の増分 6.53 GB。"
    "/free はしていない(開始前に読み込み済み torch 0.40 GB)。計測 84 回"
)
FREED_ONLY = (
    "/system_stats の GPU 全体の増分 6.81 GB。開始前に /free でモデルを解放。ComfyUI の実行時間 11.73 秒"
)


def _from_note(delta: float, torch: float | None) -> dict:
    return {
        "tool": "system_stats",
        "before_gb": None,
        "peak_gb": None,
        "after_gb": None,
        "before_torch_gb": torch,
        "delta_gb": delta,
        "from_note": True,
    }


def test_device_from_note():
    assert contrib.device_from_note(FREED) == _from_note(7.11, 0.09)
    assert contrib.device_from_note(NOT_FREED) == _from_note(6.53, 0.4)
    assert contrib.device_from_note(FREED_ONLY) == _from_note(6.81, 0.0)
    assert contrib.device_from_note("/system_stats の GPU 全体の増分 1.5 GB。計測 3 回") == _from_note(
        1.5, None
    )
    for none in ("SYNTHETIC TEST FIXTURE", "", None, "開始前に /free でモデルを解放", 3):
        assert contrib.device_from_note(none) is None


def test_device_of():
    m = _measurement(note=FREED)
    assert contrib.device_of(m) == _from_note(7.11, 0.09)
    assert contrib.device_of(replace(m, device=_device())) == _device()  # 項目が勝つ
    assert contrib.device_of(replace(m, vram_metric=None)) is None  # bench の測り方でない
    assert contrib.device_of(replace(m, outcome="oom")) is None
    assert contrib.device_of(replace(m, outcome="error")) is None
    # 読む経路では note を読まない(Measurement.device は項目のまま)
    d = contrib.build_submission(_measurement(note=FREED), None)
    assert contrib.to_measurement(d).device is None and contrib.device_of(contrib.to_measurement(d))


DELTA_LINE = "/system_stats の GPU 全体の増分 {} GB。開始前に /free でモデルを解放"


def test_device_from_note_reads_strictly():
    """note は提出する人が書ける文。読めなかったときの 0.00・2 つの文・長い数・ASCII でない数字は読まない。"""
    assert contrib.device_from_note(DELTA_LINE.format("6.53")) == _from_note(6.53, 0.0)
    bad = ("0.00", "0", "12345.00", "9" * 31, "9" * 400, "1e309", "-1.50", "1.2345", "６.５３", "٦.٥٣")
    for num in bad:
        assert contrib.device_from_note(DELTA_LINE.format(num)) is None, num
    twice = DELTA_LINE.format("6.53") + "。" + DELTA_LINE.format("7.00")
    assert contrib.device_from_note(twice) is None  # bench は 1 つしか書かない


def test_device_of_has_the_same_limit_as_the_field():
    """note から読んだ増分・開始前の torch にも、項目の検査と同じ上限(GPU の総量 × DEVICE_VRAM_RATIO)。"""
    m = _measurement(note=DELTA_LINE.format("8.30"))  # 8 GB の GPU
    assert contrib.device_of(m)["delta_gb"] == 8.3
    assert contrib.device_of(replace(m, note=DELTA_LINE.format("8.50"))) is None
    assert contrib.device_of(replace(m, note=DELTA_LINE.format("50.00"))) is None
    torch = DELTA_LINE.format("2.00") + "(解放前 torch 9.00 GB、解放後 9.00 GB)"
    assert contrib.device_of(replace(m, note=torch)) is None
    # 総量が分からなければ note は読まない(#217 で submission_device にそろえた。比べない側)
    unknown = replace(m, gpu_vram_gb=None)
    assert contrib.device_of(replace(unknown, note=DELTA_LINE.format("5.00"))) is None
    # 最後まで動かなかった実測は、項目があっても読まない(#217。同じ理由)
    assert contrib.device_of(replace(m, device=_device(), outcome="oom")) is None
    # bench が device を作れなかった実測(note は 0.00): 照合の行を作らない
    assert contrib.device_of(replace(m, note=DELTA_LINE.format("0.00"))) is None


def test_build_submission_takes_stages_and_device_from_the_measurement():
    """bench --share・contrib make は実測だけを渡す。実測の stages・device を落とさない。"""
    stages = {
        "sampler_runs": 1,
        "before_sampling": None,
        "sampling": {"peak_gb": 3.0, "min_gb": 1.0, "samples": 5},
        "after_sampling": None,
    }
    m = _measurement(device=_device(), stages=stages)
    mm = contrib.build_submission(m, None)["measurement"]
    assert mm["device"] == _device() and mm["stages"] == stages
    other = _device(2.0, 7.0, 6.0)
    assert contrib.build_submission(m, None, device=other)["measurement"]["device"] == other  # 渡した方が勝つ
    bare = contrib.build_submission(_measurement(), None)["measurement"]
    assert "device" not in bare and "stages" not in bare


# --- まとめ ------------------------------------------------------------------------------------------


def _sub(peak: float, device: dict | None, note: str = "SYNTHETIC TEST FIXTURE") -> contrib.Submission:
    d = contrib.build_submission(_measurement(peak, note=note), None, device=device)
    return contrib.Submission(path="-", sha256=contrib.content_hash(d), data=d)


def test_aggregate_device_median_of_sources():
    a1 = _sub(5.0, _device(1.0, 6.0, 5.0, torch=0.1))
    a2 = _sub(5.1, _device(1.2, 6.4, 5.4, torch=0.3))  # 出どころ A: 1.1・6.2・5.2・0.2、増分 5.1
    b1 = _sub(
        5.2, _device(2.0, 9.0, None, torch=None)
    )  # 出どころ B: 終わりの値・torch は読めなかった。増分 7.0
    b2 = _sub(5.3, _device(0.0, 3.0, 3.0, tool="system_stats"))  # 道具が最頻値と違う: 入れない
    b3 = _sub(5.25, None)  # device の無い提出(note にも文が無い)
    subs = [a1, a2, b1, b2, b3]
    sources = {a1.sha256: "A", a2.sha256: "A", b1.sha256: "B", b2.sha256: "B", b3.sha256: "B"}
    [row] = contrib.aggregate(subs, sources)
    assert row["n_sources"] == 2 and row["n_used"] == 5  # device の有無はまとめの値に関係しない
    want = {
        "tool": "nvidia-smi",
        "sources": 2,
        "before_gb": 1.55,  # median(1.1, 2.0)
        "peak_gb": 7.6,  # median(6.2, 9.0)
        "after_gb": 5.2,  # A だけ
        "before_torch_gb": 0.2,  # A だけ
        "delta_gb": 6.05,  # median(5.1, 7.0)
        "from_note": 0,
    }
    assert row["device"] == want
    doc = contrib.community_document([row], subs, sources)
    [m] = parse_measurements(json.loads(contrib.dumps(doc)))
    assert m.device == want


def test_aggregate_device_mixes_note_and_field():
    a1 = _sub(5.0, None, note=FREED)  # 出どころ A: note から(増分 7.11、torch 0.09)
    a2 = _sub(5.1, _device(1.0, 8.0, 7.0, tool="system_stats", torch=0.0))  # A: 項目(増分 7.0)
    b1 = _sub(5.2, None, note=NOT_FREED)  # 出どころ B: note から(増分 6.53、torch 0.4)
    subs = [a1, a2, b1]
    sources = {a1.sha256: "A", a2.sha256: "A", b1.sha256: "B"}
    [row] = contrib.aggregate(subs, sources)
    assert row["device"] == {
        "tool": "system_stats",
        "sources": 2,
        "before_gb": 1.0,  # 項目の提出だけ
        "peak_gb": 8.0,
        "after_gb": 7.0,
        "before_torch_gb": 0.22,  # median(median(0.09, 0.0), 0.4) = median(0.045, 0.4)
        "delta_gb": 6.79,  # median(median(7.11, 7.0), 6.53) = median(7.055, 6.53)
        "from_note": 2,
    }


def test_aggregate_device_ignores_a_note_above_the_gpu_total():
    """他人の 1 件の提出の note で、まとめの行の値を好きに変えられない。"""
    [row] = contrib.aggregate([_sub(5.0, None, note=DELTA_LINE.format("50.00"))])
    assert "device" not in row
    [row] = contrib.aggregate([_sub(5.0, None, note=DELTA_LINE.format("0.00"))])
    assert "device" not in row


def test_aggregate_device_tie_prefers_system_stats():
    a = _sub(5.0, _device(1.0, 6.0, 5.0))  # nvidia-smi
    b = _sub(5.1, _device(1.0, 8.0, 7.0, tool="system_stats", torch=0.0))
    for subs in ([a, b], [b, a]):
        [row] = contrib.aggregate(subs, {a.sha256: "A", b.sha256: "B"})
        assert row["device"]["tool"] == "system_stats" and row["device"]["sources"] == 1
        assert row["device"]["delta_gb"] == 7.0


def test_aggregate_without_device_has_no_key():
    rows = contrib.aggregate([_sub(5.0, None), _sub(5.1, None)])
    assert rows and all("device" not in r for r in rows)
    assert contrib.aggregate_device([]) is None
    # bench の測り方でない・失敗の提出は note を読まない
    oom = contrib.build_submission(_measurement(5.0, note=FREED, outcome="oom"), None)
    assert contrib.device_of(contrib.to_measurement(oom)) is None


def test_bundled_community_rows_have_device_from_the_note(db):
    """data/contrib の、note に増分の文がある提出から作られる同梱の行には、全部 device がある。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "data" / "contrib"
    subs, _ = contrib.load_dir(root, db)
    noted = {
        (s.data["measurement"]["model_id"], s.data["system"]["gpu"]["name"])
        for s in subs
        if contrib.device_from_note((s.data.get("measurement") or {}).get("note")) is not None
    }
    comm = [m for m in db.measurements if is_community(m)]
    rows = [m for m in comm if (m.model_id, m.gpu_name) in noted]
    assert noted and rows
    assert all(isinstance(m.device, dict) and m.device["from_note"] >= 1 for m in rows)
    assert all(m.device["tool"] == "system_stats" and m.device["delta_gb"] is not None for m in rows)


# --- 照合 --------------------------------------------------------------------------------------------


def _check_rows(db, maint, *ms):
    test_db = Database(models=db.models, measurements=[*maint, *ms])
    rows = cal.build_check_rows(test_db)
    theta = cal.fit(cal.build_rows(test_db))
    out = cal.check(rows, theta)
    mine = [(r, x) for r, x in zip(rows, out, strict=True) if any(r.m is m for m in ms)]
    return mine, theta, out


def test_device_check_rows_and_table(db, maint):
    a = _measurement(5.0, device=_device(1.2, 6.8, 6.5))
    pinned = _measurement(
        5.5, settings=Settings(width=768, height=768, steps=20), device=_device(1.0, 7.5, 7.0)
    )
    comm = replace(
        _measurement(4.0, gpu_name="NVIDIA GeForce RTX 5080", gpu_vram_gb=15.92),
        origin="community",
        n_submissions=2,
        n_used=2,
        n_sources=2,
        consistent=True,
        device={**_device(1.0, 6.0, 5.0), "delta_gb": 4.4, "sources": 2, "from_note": 1},
    )
    noted = _measurement(4.5, settings=Settings(width=512, height=512, steps=20), note=FREED_ONLY)
    # 開始前に torch 0.40 GB(増分 2.00 GB なので張り付いてはいない)
    small = NOT_FREED.replace("6.53 GB", "2.00 GB")
    preloaded = _measurement(4.6, settings=Settings(width=640, height=640, steps=20), note=small)
    mine, theta, out = _check_rows(db, maint, a, pinned, comm, noted, preloaded)
    by = {r.m.peak_vram_gb: (r, x["device"]) for r, x in mine}
    assert len(by) == 5
    ra, da = by[5.0]
    assert da["tool"] == "nvidia-smi" and da["delta_gb"] == 5.6
    assert da["clean"] is True and da["from_note"] is False
    assert ra.overhead_gb == cal.FROZEN["overhead_gb"]
    assert da["whole_gb"] == round(5.6 + ra.overhead_gb, 2)
    assert da["formula_gb"] == round(ra.predict(theta), 2) and da["pinned"] is False
    assert da["error_pct"] == round(
        (ra.predict(theta) - (5.6 + ra.overhead_gb)) / (5.6 + ra.overhead_gb) * 100, 1
    )
    _, dp = by[5.5]
    assert dp["pinned"] is True and dp["error_pct"] is None  # 7.5 ≥ 0.9 × 8
    _, dc = by[4.0]
    assert dc["delta_gb"] == 4.4 and dc["from_note"] is True  # まとめの行は提出ごとの増分の中央値
    _, dn = by[4.5]
    assert dn["from_note"] is True and dn["delta_gb"] == 6.81 and dn["clean"] is True
    assert dn["pinned"] is (dn["whole_gb"] >= 0.9 * 8.0)  # 6.81 + 固定分 ≥ 7.2: 入りきっていない
    _, dpre = by[4.6]
    assert dpre["clean"] is False and dpre["pinned"] is False and dpre["error_pct"] is None
    with i18n.forced("ja"):
        ja = cal.device_table([x for _, x in mine])
    with i18n.forced("en"):
        en = cal.device_table([x for _, x in mine])
    assert ja[0].startswith("GPU 全体の使用量の照合") and en[0].startswith(
        "Cross-check of the whole-GPU usage"
    )
    assert len(ja) == len(en) == 4 + 5
    assert sum("| 張り付き |" in x for x in ja[4:]) == sum("| pinned |" in x for x in en[4:]) >= 1
    assert sum("| 開始前に読み込み済み |" in x for x in ja[4:]) == 1
    assert sum("| preloaded |" in x for x in en[4:]) == 1
    assert any(f"| {SDXL} " in x and "| nvidia-smi | 5.60 |" in x for x in ja)
    assert sum(" (note) |" in x for x in ja) == 3  # note から読んだ行(まとめの行を含む)
    md = cal.markdown_table({"loo": [], "summary": _summary(), "check": out})
    assert "GPU 全体の使用量の照合" in md


def test_pinned_conditions(db, maint):
    """張り付き: 実測が張り付いた(is_censored)・device の最大・増分 + 固定分 のどれかが総量の 90% 以上。"""
    s = Settings(width=1024, height=1024, steps=20)
    base = _measurement(3.0, settings=s, device=_device(1.0, 3.0, 3.0))
    censored = replace(base, offload="lowvram")  # is_censored
    peak = replace(base, peak_vram_gb=3.1, device=_device(0.5, 7.3, 7.0))  # 7.3 ≥ 7.2
    whole = replace(  # 増分 6.0 + 固定分 1.47 ≥ 7.2(最大は無い)
        base, peak_vram_gb=3.2, device={**_device(), "before_gb": None, "peak_gb": None, "delta_gb": 6.0}
    )
    mine, _, _ = _check_rows(db, maint, base, censored, peak, whole)
    got = {r.m.peak_vram_gb if r.m.offload != "lowvram" else "censored": x["device"] for r, x in mine}
    assert got[3.0]["pinned"] is False and got[3.0]["error_pct"] is not None
    for k in ("censored", 3.1, 3.2):
        assert got[k]["pinned"] is True and got[k]["error_pct"] is None, k


def test_unknown_baseline_and_unreadable_note_in_the_check(db, maint):
    s512 = Settings(width=512, height=512, steps=20)
    unknown = _measurement(4.0, device=_device(1.0, 4.0, 4.0, torch=None))  # 開始前の torch が分からない
    unread = _measurement(6.0, settings=s512, note=DELTA_LINE.format("0.00"))  # bench が読めなかった
    mine, _, _ = _check_rows(db, maint, unknown, unread)
    by = {r.m.peak_vram_gb: x for r, x in mine}
    d = by[4.0]["device"]
    assert d["clean"] is False and d["baseline"] == "unknown" and d["error_pct"] is None
    assert not by[6.0].get("device")  # 増分 0.00 の note から行を作らない
    with i18n.forced("ja"):
        ja = cal.device_table([x for _, x in mine])
    with i18n.forced("en"):
        en = cal.device_table([x for _, x in mine])
    assert len(ja) == len(en) == 4 + 1
    assert "| 開始前の量が不明 |" in ja[4] and "| baseline unknown |" in en[4]


def test_device_table_does_not_print_minus_zero(db, maint):
    m = _measurement(4.0, device=_device(1.0, 4.0, 4.0))
    mine, _, _ = _check_rows(db, maint, m)
    [(_, x)] = mine
    assert "baseline" not in x["device"]
    for err, text in ((-0.2, "| +0% |"), (0.4, "| +0% |"), (-3.6, "| -4% |"), (12.3, "| +12% |")):
        row = {**x, "device": {**x["device"], "error_pct": err}}
        assert cal.device_table([row])[4].endswith(text), err


def _summary() -> dict:
    return {
        "by_modality": {},
        "evaluable_modalities": [],
        "n_evaluable": 0,
        "n_censored": 0,
        "censored_trivial": 0,
        "n_point": 0,
        "mean_abs_pct": 0,
        "max_abs_pct": 0,
        "weights_only_mean_abs_pct": 0,
    }


def test_bundled_check_rows_have_device_where_the_note_has_it(db, maint):
    assert cal.device_table([]) == []
    result = json.loads(bundled_text("calibration.json"))
    rows = cal.build_check_rows(Database(models=db.models, measurements=[*maint, *_community(db)]))
    want = sum(contrib.device_of(r.m) is not None for r in rows)
    assert want == sum("device" in x for x in result["check"]) and want > 0  # 数は同梱のデータから数える
    with i18n.forced("ja"):
        table = cal.device_table(result["check"])
    assert len(table) == 4 + want
    assert "GPU 全体の使用量の照合" in cal.markdown_table(result)


def _community(db):
    return [m for m in db.measurements if is_community(m)]


# --- 判定が変わらない --------------------------------------------------------------------------------


@pytest.mark.parametrize("dynamic", [False, True])
def test_device_does_not_change_estimate(db, maint, dynamic):
    e = db.get(SDXL)
    m = _measurement(
        9.2 if not dynamic else 2.5,
        gpu_name="NVIDIA GeForce RTX 5080",
        gpu_vram_gb=15.92,
        offload="dynamic" if dynamic else "none",
        vram_metric=None if not dynamic else "torch_reserved",
    )
    with_dev = replace(m, device=_device(1.0, 12.0, 11.0))
    g = Gpu(name="NVIDIA GeForce RTX 5080", vendor="nvidia", vram_total_gb=15.92, backend="cuda")
    one = SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    s = Settings(width=1024, height=1024)
    a = estimate(e, one, s, [*maint, m])
    b = estimate(e, one, s, [*maint, with_dev])
    assert dataclasses.asdict(a) == dataclasses.asdict(b)


def test_bundled_device_does_not_change_estimate(db):
    """同梱のデータで、ほかの利用者の実測のあるモデル全部: 行の device を外しても見積もりは同じ。"""
    comm = _community(db)
    assert comm and any(m.device for m in comm)
    bare = [replace(m, device=None) if is_community(m) else m for m in db.measurements]
    for m in {(x.model_id, x.gpu_name, x.gpu_vram_gb) for x in comm}:
        model_id, gpu, vram = m
        g = Gpu(name=gpu, vendor="nvidia", vram_total_gb=vram, backend="cuda")
        one = SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
        e = db.get(model_id)
        a = estimate(e, one, None, db.measurements)
        b = estimate(e, one, None, bare)
        assert dataclasses.asdict(a) == dataclasses.asdict(b), model_id
