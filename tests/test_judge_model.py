"""アプリが 1 モデルを判定する公開の関数 gmf.judge_model / gmf.system_from(#27)。

AI生成キット(local_model_ai_gen_kit の packages/aigenkit/src/aigenkit/core/gmf.py)は、これまで内部の関数
(recommend.judge / vram_need・hardware.classify_gpu / comfy_precision / parse_compute_cap・models.Gpu)で
判定していた。公開の関数が同じ答えを返すことを、アプリの呼び方を写したもので確かめる。
nvidia-smi などは呼ばない。
"""

from __future__ import annotations

import json

import pytest

import genmodelfit as gmf
from genmodelfit.db import bundled_text, parse_db, parse_measurements
from genmodelfit.downloader import ModelNotFound

# アプリが genmodelfit の見積もりを使うモデル
# (AI生成キットの profiles の gmf_id のうち gmf_fit を切っていないもの)
# 凍らせた一覧に移さない: 同梱の一覧から消えた週は、アプリの要件が崩れるので試験を落として人が見る(#197)
APP_MODELS = [
    "image_z_image_turbo_int8",
    "image_z_image_turbo",
    "image_sdxl_simple",
    "video_wan2_2_5B_ti2v",
    "text_to_video_wan",
    "audio_ace_step_1_5_checkpoint",
    "hf:stabilityai/stable-audio-3-small-sfx",
    "3d_hunyuan3d_image_to_model",
    "3d_hunyuan3d-v2.1",
]
# (GPU 名, VRAM GB, ドライバ)。アプリが nvidia-smi で読んだ値の形。
# 新しい・古い世代、古いドライバ、版が分からないもの
GPUS = [
    ("NVIDIA GeForce RTX 5080", 16.0, "581.57"),
    ("NVIDIA GeForce RTX 3060", 12.0, "560.94"),
    ("NVIDIA GeForce RTX 4060 Laptop GPU", 8.0, ""),
    ("NVIDIA GeForce GTX 1060 6GB", 6.0, "391.35"),  # ドライバが古い(genmodelfit だけなら CPU 扱い)
    ("NVIDIA GeForce GTX 1080 Ti", 11.0, "560.94"),  # Pascal(Windows では fp16)
    ("NVIDIA GeForce GTX 780", 3.0, "474.44"),  # Kepler(今の PyTorch が対応しない)
    ("Quadro K620", 2.0, "552.22"),  # Maxwell
    ("NVIDIA RTX A2000", 4.0, "560.94"),
]
RAM_FOR_GMF_GB = 1024.0  # アプリはメモリを十分にあるものとして渡す(メモリはアプリが判定する)


@pytest.fixture(scope="module")
def app_db():
    """アプリと同じ読み方(同梱の db.json と measurements.json だけ。利用者のキャッシュは読まない)。"""
    db = parse_db(json.loads(bundled_text("db.json")), "bundled")
    db.measurements = parse_measurements(json.loads(bundled_text("measurements.json")))
    return db


def _app_system(name: str, vram_gb: float, driver: str):
    """AI生成キット core/gmf.py の _system を写したもの(内部の関数を使う、今のアプリの呼び方)。"""
    from genmodelfit.hardware import classify_gpu, comfy_precision, parse_compute_cap
    from genmodelfit.models import Gpu

    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram_gb, driver=driver or None, backend="cuda")
    classify_gpu(g, windows_driver=True, comfy_windows=True)
    if g.backend != "cuda":
        g.backend = "cuda"
        g.compute_dtype, g.bf16, g.fp8 = comfy_precision(
            parse_compute_cap(g.compute_capability), g.name, windows=True
        )
    return gmf.SystemInfo(
        os="Windows",
        cpu="",
        cpu_cores=0,
        ram_total_gb=RAM_FOR_GMF_GB,
        ram_available_gb=RAM_FOR_GMF_GB,
        gpus=[g],
        disk_free_gb=None,
    )


def _app_fit(db, mid: str, name: str, vram_gb: float, driver: str):
    """AI生成キット core/gmf.py の _gpu_fit を写したもの → (段階, 動かし方, 根拠の VRAM, 根拠)。"""
    from genmodelfit.recommend import judge, vram_need

    system = _app_system(name, vram_gb, driver)
    e = judge(db.get(mid), system, db).estimate
    return e.fit, e.run_mode, vram_need(e, system), e.basis


@pytest.mark.parametrize("gpu", GPUS, ids=[g[0] for g in GPUS])
@pytest.mark.parametrize("mid", APP_MODELS)
def test_judge_model_matches_what_the_app_computes(app_db, mid, gpu):
    name, vram, driver = gpu
    want = _app_fit(app_db, mid, name, vram, driver)
    fit = gmf.judge_model(
        mid,
        gpu_name=name,
        vram_gb=vram,
        driver=driver or None,
        ram_gb=RAM_FOR_GMF_GB,
        gpu_usable=True,  # アプリが自分の入れる PyTorch で確かめた
        windows=True,
        db=app_db,
    )
    d = fit.to_dict()
    got = (d["fit_level"], d["run_mode"], d["memory_required_gb"], d["estimate_confidence"])
    need = round(want[2], 2) if want[2] is not None else None
    if want[1] == "cpu":
        # CPU で動く判定(#39 の SD 系・Stable Audio)では、アプリの vram_need は None で、memory_required_gb は
        # メインメモリの量(memory_kind が ram)。段階・動かし方・根拠は同じ
        assert d["memory_kind"] == "ram" and need is None
        need = d["memory_required_gb"]
    assert got == (want[0], want[1], need, want[3])
    assert d["id"] == mid
    # 見積もりそのものも同じ(丸める前の値)
    from genmodelfit.recommend import judge

    assert fit.estimate == judge(app_db.get(mid), _app_system(name, vram, driver), app_db).estimate


def test_system_from_decides_generation_and_precision():
    s = gmf.system_from(
        [{"name": "NVIDIA GeForce GTX 1080 Ti", "vram_gb": 11, "driver": "560.94"}], ram_gb=32, windows=True
    )
    g = s.best_gpu
    assert g is not None and g.arch == "pascal" and g.compute_capability == "6.1"
    assert g.compute_dtype == "fp16"  # Windows の ComfyUI は Pascal でも fp16
    assert s.vram_gb == 11 and s.ram_total_gb == s.ram_available_gb == 32
    assert s.swap_max_gb is None and s.commit_limit_gb is None  # 渡したメモリで判定する
    lin = gmf.system_from(
        [{"name": "NVIDIA GeForce GTX 1080 Ti", "vram_gb": 11, "driver": "560.94"}], ram_gb=32, windows=False
    )
    assert lin.best_gpu.compute_dtype == "fp32"


def test_system_from_unusable_gpu_is_cpu_unless_the_caller_checked():
    old = {"name": "NVIDIA GeForce GTX 1060 6GB", "vram_gb": 6, "driver": "391.35"}
    s = gmf.system_from([old], ram_gb=16, windows=True)
    assert s.best_gpu is None and s.vram_gb == 0  # ドライバが古い: CPU として判定(理由は notes)
    assert any("ドライバ" in n for n in s.notes)
    checked = gmf.system_from([old], ram_gb=16, gpu_usable=True, windows=True)
    assert checked.vram_gb == 6 and checked.best_gpu.arch == "pascal"
    assert gmf.system_from([old], ram_gb=16, gpu_usable=False).vram_gb == 0
    assert gmf.system_from([], ram_gb=16).gpus == []
    # NVIDIA 以外は 0.11.0 から検出と同じ対応表で判定する(#96。tests/test_issue_96.py)。
    # gpu_usable=False ならどの会社の GPU も使わない
    amd = {"name": "AMD Radeon RX 7900 XTX", "vram_gb": 24, "vendor": "amd"}
    assert gmf.system_from([amd], ram_gb=16, gpu_usable=False).vram_gb == 0
    assert gmf.system_from([amd], ram_gb=16, windows=True, windows_build=19045).vram_gb == 0  # Windows 10
    assert gmf.system_from([amd], ram_gb=16, windows=True, windows_build=22000).vram_gb == 24


def test_system_from_takes_the_largest_usable_gpu():
    s = gmf.system_from(
        [
            {"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12, "driver": "560.94"},
            {"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24, "driver": "560.94"},
        ],
        ram_gb=64,
    )
    assert s.best_gpu.name == "NVIDIA GeForce RTX 4090"


@pytest.mark.parametrize(
    "bad",
    [
        {"vram_gb": 8},
        {"name": "x"},
        {"name": "x", "vram_gb": -1},
        {"name": "x", "vram_gb": True},
        {"name": " ", "vram_gb": 8},
    ],
)
def test_system_from_rejects_bad_gpus(bad):
    with pytest.raises(ValueError):
        gmf.system_from([bad], ram_gb=16)


def test_judge_model_by_name_cpu_only_and_errors(app_db):
    fit = gmf.judge_model(
        "Z-Image-Turbo", gpu_name="NVIDIA GeForce RTX 5080", vram_gb=16, driver="581.57", ram_gb=64, db=app_db
    )
    assert fit.model.id.startswith("image_z_image_turbo")
    assert {w["id"] for w in fit.workflows} | {fit.model.id} >= {
        "image_z_image_turbo",
        "image_z_image_turbo_int8",
    }
    cpu = gmf.judge_model("manual:tsukuyomi-chan-piper-plus", cpu_only=True, ram_gb=16, db=app_db)
    assert cpu.system.vram_gb == 0 and cpu.to_dict()["fit_level"] in ("perfect", "good", "marginal")
    with pytest.raises(ModelNotFound):
        gmf.judge_model("no-such-model-xyz", cpu_only=True, ram_gb=16, db=app_db)
    with pytest.raises(ValueError):
        gmf.judge_model(
            "image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", ram_gb=16, db=app_db
        )  # VRAM が無い
    with pytest.raises(ValueError):
        gmf.judge_model("image_sdxl_simple", content="nsfw", cpu_only=True, db=app_db)
    s = gmf.system_from([], ram_gb=16)
    with pytest.raises(ValueError):
        gmf.judge_model("image_sdxl_simple", system_info=s, cpu_only=True, db=app_db)
    assert gmf.judge_model("image_sdxl_simple", system_info=s, db=app_db).system is s


def test_judge_model_to_dict_is_the_recommend_row(app_db):
    """返す値は recommend の 1 件と同じ形(docs/schema.md の models の 1 件)。"""
    s = gmf.system_from([{"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12, "driver": "560.94"}], ram_gb=32)
    one = gmf.judge_model("image_sdxl_simple", system_info=s, db=app_db).to_dict()
    rows = {
        f.model.id: f.to_dict() for f in gmf.fit_list(fit="all", all_workflows=True, system_info=s, db=app_db)
    }
    row = rows["image_sdxl_simple"]
    assert set(one) == set(row)
    for k in ("fit_level", "run_mode", "memory_required_gb", "score", "licenses", "auto", "estimate", "gate"):
        assert one[k] == row[k], k


def test_public_api_names_are_stable():
    """互換の対象は __all__ に入る名前(docs/schema.md「Python API (stable)」)。
    消したり名前を変えたりしない。"""
    for name in (
        "judge_model",
        "system_from",
        "ModelNotFound",
        "Fit",
        "SystemInfo",
        "recommend",
        "fit_list",
        "system",
        # 0.11.0(#96)
        "gpu_family",
        "self_check",
        "parse_safetensors_header",
        "HeaderError",
        "HeaderInfo",
    ):
        assert name in gmf.__all__, name
        assert hasattr(gmf, name), name
    # genmodelfit.recommend は関数(同じ名前のモジュールを隠す。docs に注意を書いた)
    assert callable(gmf.recommend)


def test_judge_model_does_not_run_hardware_probes(monkeypatch, app_db):
    """GPU を渡したときは nvidia-smi・PowerShell を呼ばない(呼ぶと失敗させる)。"""
    from genmodelfit import hardware

    def boom(*a, **k):
        raise AssertionError("呼ばない")

    monkeypatch.setattr(hardware, "detect_gpus", boom)
    monkeypatch.setattr(hardware, "detect_other_gpus", boom)
    gmf.judge_model("image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", vram_gb=12, ram_gb=16, db=app_db)
    gmf.judge_model("image_sdxl_simple", cpu_only=True, ram_gb=16, db=app_db)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True, "8"])
def test_amounts_must_be_finite_and_not_negative(app_db, bad):
    """NaN は奥で「cannot convert float NaN to integer」、無限大は perfect になっていた(#27 の検証)。
    入口で断る。"""
    with pytest.raises(ValueError, match="vram_gb"):
        gmf.judge_model(
            "image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", vram_gb=bad, ram_gb=16, db=app_db
        )
    with pytest.raises(ValueError, match="ram_gb"):
        gmf.judge_model("image_sdxl_simple", cpu_only=True, ram_gb=bad, db=app_db)
    with pytest.raises(ValueError, match="ram_gb"):
        gmf.system_from([], ram_gb=bad)
    with pytest.raises(ValueError, match="vram_gb"):
        gmf.system_from([{"name": "NVIDIA GeForce RTX 3060", "vram_gb": bad}], ram_gb=16)


def test_cpu_only_refuses_gpu_arguments(app_db):
    for kw in (
        {"gpu_usable": True},
        {"gpu_usable": False},
        {"gpu_name": "NVIDIA GeForce RTX 3060"},
        {"vram_gb": 8},
    ):
        with pytest.raises(ValueError, match="cpu_only"):
            gmf.judge_model("image_sdxl_simple", cpu_only=True, ram_gb=16, db=app_db, **kw)
