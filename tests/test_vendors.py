"""NVIDIA 以外の GPU(#22)・CPU で動く系統(#39)・同じ GPU で成功した実測の段階(#26)。

本物の PowerShell・lspci・system_profiler は呼ばない(conftest が塞ぐ)。出力は tests/fixtures/hardware の
フィクスチャ(個体の ID は 0 に置き換えた)で差し替え、OS・CPU は hardware.Host で決める。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from frozenlist import frozen_db

import genmodelfit as gmf
from genmodelfit import hardware, i18n
from genmodelfit.estimate import Coefficients, estimate
from genmodelfit.hardware import (
    Host,
    Memory,
    classify_adapter,
    classify_gpu,
    detect,
    parse_lspci,
    parse_system_profiler,
    parse_wmi_video,
)
from genmodelfit.models import Adapter, Gpu, ModelEntry, ModelFile, Settings, SystemInfo
from genmodelfit.recommend import judge, select
from genmodelfit.table import _unusable_gpus, make_console, mode_cell, no_gpu_line, pc_line, print_fit_table

FIX = Path(__file__).parent / "fixtures" / "hardware"
GB = 2**30
ORDER = ["too_tight", "marginal", "good", "perfect"]

WIN11 = Host("Windows", 26100, cpu="13th Gen Intel(R) Core(TM) i5-13600KF")
WIN10 = Host("Windows", 19045)
LINUX = Host("Linux", cpu="AMD Ryzen 9 7950X 16-Core Processor")
MAC = Host("Darwin", mac_version=(15, 1), cpu="Apple M2 Pro")


def fx(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def reg(name: str) -> list[dict]:
    return json.loads(fx(name))


@pytest.fixture(scope="module")
def db():
    return gmf.load_db()


def _no_nvidia(monkeypatch, adapters: list[Adapter]):
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda: None)
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda **_: (adapters, []))


def system_of(monkeypatch, adapters, host: Host, ram_gb: float = 32.0, **kw) -> SystemInfo:
    _no_nvidia(monkeypatch, adapters)
    return detect(memory=Memory(total=int(ram_gb * GB), available=int(ram_gb * GB)), host=host, **kw)


# --- 検出と対応表 ---------------------------------------------------------------------------------


def rx7900xtx_linux(monkeypatch, ram_gb=64.0):
    adapters = parse_lspci(fx("lspci_rx7900xtx.txt"), lambda slot: 24 * GB)
    return system_of(monkeypatch, adapters, LINUX, ram_gb)


def test_rx7900xtx_linux_rocm(monkeypatch):
    s = rx7900xtx_linux(monkeypatch)
    g = s.best_gpu
    assert (g.vendor, g.backend, g.gfx_target, g.arch, g.memory, g.vram_total_gb) == (
        "amd",
        "rocm",
        "gfx1100",
        "RDNA 3",
        "dedicated",
        24.0,
    )
    assert g.bf16 is True and g.fp8 is False and g.compute_dtype == "fp16"
    assert any("NVIDIA の専用 VRAM の GPU と同じ基準で決めた(AMD" in n for n in s.notes)  # #108
    # gfx1100 は pytorch.org の ROCm 版に入っているので、TheRock の注意は出さない
    assert not any("TheRock" in n for n in s.notes)
    assert pc_line_en(s).startswith(
        "PC: AMD Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M 24.0 GiB VRAM (ROCm, estimated)"
    )
    assert pc_line(s).startswith(
        "PC: AMD Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M VRAM 24.0 GiB(ROCm、推定)"
    )


def test_rx6600_windows_rdna2(monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_rx6600.json"), reg("registry_rx6600.json"))
    s = system_of(monkeypatch, adapters, WIN11)
    g = s.best_gpu
    assert (g.backend, g.gfx_target, g.arch, g.bf16, round(g.vram_total_gb)) == (
        "rocm",
        "gfx1032",
        "RDNA 2",
        False,
        8,
    )
    # RDNA 2 は bf16 を持たないが、ComfyUI は fp16 を持たない系統も bf16 で計算する(作業用メモリは倍にしない)
    e = ModelEntry(
        id="t",
        name="t",
        modality="image",
        precision="bf16",
        files=[
            ModelFile(name="qwen_image_bf16.safetensors", size_bytes=2 * GB, directory="diffusion_models")
        ],
    )
    est = estimate(e, s, coefficients=Coefficients())
    assert any("bf16 で動かす" in r and "RDNA 2" in r for r in est.reasons)
    # Windows 10 は対象外(ComfyUI の案内は Windows 11)
    adapters, _ = parse_wmi_video(fx("wmi_rx6600.json"), reg("registry_rx6600.json"))
    s10 = system_of(monkeypatch, adapters, WIN10)
    assert s10.best_gpu is None and any("Windows 11 が要る" in n for n in s10.notes)
    # 表の先頭の行は、使えるようにする条件(Windows 11)を出す
    assert "(needs Windows 11 for generation → judged as CPU)" in " ".join(unusable_en(s10))
    assert "(生成には Windows 11 が要る → CPU として判定)" in " ".join(_unusable_gpus(s10))
    # 版が分からないときは条件を言わない
    s_unknown = system_of(monkeypatch, adapters, Host("Windows", None))
    assert s_unknown.best_gpu is None
    assert all("needs Windows 11" not in x for x in unusable_en(s_unknown))


def test_arc_a770_windows_xpu(monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_arc_a770.json"), reg("registry_arc_a770.json"))
    s = system_of(monkeypatch, adapters, WIN11)
    g = s.best_gpu
    assert (g.vendor, g.backend, g.memory, round(g.vram_total_gb, 1)) == ("intel", "xpu", "dedicated", 15.9)
    assert pc_line_en(s).startswith("PC: Intel(R) Arc(TM) A770 Graphics 15.9 GiB VRAM (XPU, estimated)")
    # Linux の lspci では VRAM が読めないので、名前から少ないほうの 8 GB とみなす
    a = Adapter(name="Intel Arc A770", vendor="intel", source="lspci")
    g2 = classify_adapter(a, LINUX, 32.0)
    assert g2.vram_total_gb == 8.0 and a.vram_source == "name"


def test_core_ultra_155h_igpu_shares_half_of_ram(monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_core_ultra_155h.json"), [])
    s = system_of(monkeypatch, adapters, WIN11, ram_gb=32.0)
    g = s.best_gpu
    assert (g.backend, g.memory) == ("xpu", "shared")
    assert g.vram_total_gb == pytest.approx(0.12 + 16.0, abs=0.01)  # 取り分け 128 MB + RAM の半分
    assert "(integrated; 16.1 GiB shared with RAM, XPU, estimated)" in pc_line_en(s)
    assert "(内蔵。メモリと共有で 16.1 GiB、XPU、推定)" in pc_line(s)


def test_uhd770_is_cpu_with_a_reason(monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_uhd770.json"), [])
    s = system_of(monkeypatch, adapters, WIN11, ram_gb=15.7)
    assert s.best_gpu is None and s.vram_gb == 0
    a = s.other_gpus[0]
    assert a.usable is False and a.integrated is True and "XPU 版" in a.reason
    line = pc_line_en(s)
    assert line.startswith(
        "PC: Intel(R) UHD Graphics 770 (integrated; not usable for generation → judged as CPU), RAM 15.7 GiB"
    )
    assert pc_line(s).startswith(
        "PC: Intel(R) UHD Graphics 770(内蔵。生成に使えない → CPU として判定)、メモリ 15.7 GiB"
    )


def test_m2_pro_unified_memory(monkeypatch):
    adapters = parse_system_profiler(fx("system_profiler_m2pro.json"))
    assert [(a.name, a.vendor) for a in adapters] == [("Apple M2 Pro", "apple")]
    s = system_of(monkeypatch, adapters, MAC, ram_gb=32.0)
    g = s.best_gpu
    assert (g.backend, g.memory, g.vram_total_gb) == ("mps", "unified", 24.0)
    assert pc_line_en(s).startswith("PC: Apple M2 Pro (unified memory; 24.0 GiB for the GPU, MPS, estimated)")
    # macOS 13 は MPS 版 PyTorch の対象外
    adapters = parse_system_profiler(fx("system_profiler_m2pro.json"))
    s13 = system_of(monkeypatch, adapters, Host("Darwin", mac_version=(13, 6)), ram_gb=32.0)
    assert s13.best_gpu is None and any("macOS 14.0 以降" in n for n in s13.notes)


def test_ryzen_ai_max_395_apu(monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_ryzen_ai_max_395.json"), reg("registry_ryzen_ai_max_395.json"))
    host = Host("Windows", 26100, cpu="AMD RYZEN AI MAX+ 395 w/ Radeon 8060S")
    s = system_of(monkeypatch, adapters, host, ram_gb=64.0)
    g = s.best_gpu
    assert (g.backend, g.gfx_target, g.memory) == ("rocm", "gfx1151", "shared")
    assert g.vram_total_gb == pytest.approx(64.0 + 32.0)  # BIOS で取り分けた 64 GB + 見えている RAM の半分


def test_generic_radeon_graphics_resolved_by_cpu():
    a = Adapter(name="AMD Radeon(TM) Graphics", vendor="amd", vram_gb=0.5)
    g = classify_adapter(a, Host("Windows", 26100, cpu="AMD Ryzen 7 8845HS w/ Radeon 780M Graphics"), 32.0)
    assert g is not None and g.gfx_target == "gfx1103" and g.memory == "shared"
    b = Adapter(name="AMD Radeon(TM) Graphics", vendor="amd", vram_gb=0.5)
    assert classify_adapter(b, Host("Windows", 26100, cpu="AMD Ryzen 7 5800X 8-Core Processor"), 32.0) is None
    assert b.integrated is True and "世代(gfx)が名前から分からない" in b.reason


def test_rdna1_and_raphael_are_cpu():
    a = Adapter(name="AMD Radeon RX 5700 XT", vendor="amd", vram_gb=8.0)
    assert classify_adapter(a, LINUX, 32.0) is None and "RDNA 2 以降" in a.reason
    r = Adapter(name="AMD Raphael", vendor="amd")
    assert classify_adapter(r, LINUX, 32.0) is None and r.integrated is True


def test_rdna2_not_in_pytorch_org_mentions_therock():
    a = Adapter(name="AMD Radeon RX 6600", vendor="amd", vram_gb=8.0)
    g = classify_adapter(a, LINUX, 32.0)
    assert g.gfx_target == "gfx1032" and any("TheRock" in n for n in g.notes)


def test_nvidia_present_keeps_nvidia(monkeypatch):
    """NVIDIA の判定は変えない: NVIDIA の GPU があれば AMD・Intel は判定に使わない。"""
    g = classify_gpu(
        Gpu(
            name="NVIDIA GeForce RTX 3070",
            vendor="nvidia",
            vram_total_gb=8.0,
            driver="566.03",
            backend="cuda",
            compute_capability="8.6",
        ),
        windows_driver=True,
        comfy_windows=True,
    )
    adapters, _ = parse_wmi_video(fx("wmi_arc_a770.json"), reg("registry_arc_a770.json"))
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda **_: (adapters, []))
    s = detect(gpus=[g], memory=Memory(total=32 * GB, available=32 * GB), other_gpus=True, host=WIN11)
    assert s.best_gpu.name == "NVIDIA GeForce RTX 3070" and [x.backend for x in s.gpus] == ["cuda"]
    assert any("判定に使わない" in n for n in s.notes)


# --- 判定(推定・good まで・共有メモリ) ---------------------------------------------------------------


def small_image(main_gb: float = 2.0) -> ModelEntry:
    return ModelEntry(
        id="t",
        name="t",
        modality="image",
        precision="fp16",
        files=[
            ModelFile(
                name="flux1-dev_fp16.safetensors", size_bytes=int(main_gb * GB), directory="diffusion_models"
            ),
            ModelFile(name="ae.safetensors", size_bytes=int(0.3 * GB), directory="vae"),
        ],
    )


def vendor_system(kind: str, vram: float, ram: float = 32.0) -> SystemInfo:
    if kind == "amd":
        g = Gpu(
            name="AMD Radeon RX 7900 XTX",
            vendor="amd",
            vram_total_gb=vram,
            backend="rocm",
            compute_dtype="fp16",
            bf16=True,
            fp8=False,
            gfx_target="gfx1100",
        )
    elif kind == "intel":
        g = Gpu(
            name="Intel(R) Arc(TM) A770 Graphics",
            vendor="intel",
            vram_total_gb=vram,
            backend="xpu",
            compute_dtype="fp16",
            bf16=True,
            fp8=False,
        )
    elif kind == "apple":
        g = Gpu(
            name="Apple M2 Pro",
            vendor="apple",
            vram_total_gb=ram * hardware.UNIFIED_MEMORY_SHARE,
            backend="mps",
            compute_dtype="fp16",
            bf16=True,
            fp8=False,
            memory="unified",
        )
    elif kind == "apu":
        g = Gpu(
            name="AMD Radeon(TM) 8060S Graphics",
            vendor="amd",
            vram_total_gb=vram + ram * hardware.SHARED_MEMORY_SHARE,
            backend="rocm",
            compute_dtype="fp16",
            bf16=True,
            fp8=False,
            memory="shared",
            gfx_target="gfx1151",
        )
    else:
        g = classify_gpu(
            Gpu(
                name="NVIDIA GeForce RTX 4090",
                vendor="nvidia",
                vram_total_gb=vram,
                driver="581.57",
                backend="cuda",
                compute_capability="8.9",
            ),
            windows_driver=True,
            comfy_windows=True,
        )
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


@pytest.mark.parametrize("kind", ["amd", "intel", "apple", "apu"])
def test_non_nvidia_is_not_capped_and_not_verified(kind):
    """#108: NVIDIA 以外も同じ基準(上限なし)。同梱の実測が無いので確認済みの種類ではない(✓ を出さない)。"""
    from genmodelfit.estimate import platform_verification

    system = vendor_system(kind, 48.0, 64.0)
    e = estimate(small_image(1.0), system, coefficients=Coefficients())
    nv_system = vendor_system("nvidia", 48.0, 64.0)
    nv = estimate(small_image(1.0), nv_system, coefficients=Coefficients())
    assert e.fit == nv.fit == "perfect" and e.basis == "estimated"
    assert not any("Good まで" in r for r in e.reasons)
    assert platform_verification(system)["verified"] is False
    assert platform_verification(nv_system)["verified"] is True
    assert "✓" not in pc_line(system) and "✓" in pc_line(nv_system)


@pytest.mark.parametrize("kind", ["apple", "apu"])
def test_shared_memory_never_offloads(kind):
    big = small_image(20.0)
    e = estimate(big, vendor_system(kind, 0.5, 32.0), coefficients=Coefficients())
    assert e.run_mode != "offload"


@pytest.mark.parametrize("kind", ["nvidia", "amd", "intel", "apu"])
def test_fit_monotonic_in_vram_across_vendors(kind):
    for e in (small_image(2.0), small_image(6.0), small_image(14.0)):
        prev = -1
        for vram in (2, 4, 6, 8, 12, 16, 24, 32, 48):
            fit = estimate(e, vendor_system(kind, vram), coefficients=Coefficients()).fit
            assert ORDER.index(fit) >= prev, (kind, vram, fit)
            prev = ORDER.index(fit)


@pytest.mark.parametrize("kind", ["apple", "apu", "amd"])
def test_fit_monotonic_in_ram_across_vendors(kind):
    for e in (small_image(2.0), small_image(6.0), small_image(14.0)):
        prev = -1
        for ram in (8, 12, 16, 24, 32, 48, 64, 96):
            fit = estimate(e, vendor_system(kind, 8.0, ram), coefficients=Coefficients()).fit
            assert ORDER.index(fit) >= prev, (kind, ram, fit)
            prev = ORDER.index(fit)


def test_measurement_from_another_vendor_is_calibration_only(db):
    s = vendor_system("amd", 24.0, 64.0)
    e = judge(db.get("image_sdxl_simple"), s, db).estimate
    assert e.basis == "estimated" and e.basis_detail == "calibrated" and e.fit_from == "estimate"
    assert any("AMD の GPU での実測は無い" in r for r in e.reasons)


# --- #39 CPU で動く系統 -------------------------------------------------------------------------------


def no_gpu(ram: float) -> SystemInfo:
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[])


@pytest.mark.parametrize(
    "model_id",
    [
        "image_sdxl_simple",
        "hf:stabilityai/stable-audio-3-small-sfx",
        "manual:stable-audio-3-small-music",
        "hf:stabilityai/stable-audio-open-1.0",
    ],
)
@pytest.mark.parametrize("ram", [16, 32])
def test_sd_and_stable_audio_run_on_cpu(db, model_id, ram):
    m = db.get(model_id)
    if model_id.startswith("hf:"):
        # HF の項目は凍らせた一覧から読み(毎週の更新で消えても同じ入力で。#197)、系統は今の表で当て直す
        from genmodelfit.families import family_of, vae_of

        db = frozen_db()
        m = db.get(model_id)
        m.comfy_family = family_of(m)
        m.comfy_vae = vae_of(m, m.comfy_family)
    e = judge(m, no_gpu(ram), db).estimate
    assert (e.fit, e.run_mode) == ("marginal", "cpu"), (model_id, ram, e.reasons[:2])
    assert any("CPU で動く系統" in r for r in e.reasons) and any("CPU で動く根拠" in r for r in e.reasons)


def test_sdxl_cpu_ram_counts_fp32_weights(db):
    e = judge(db.get("image_sdxl_simple"), no_gpu(32), db).estimate
    assert 12.0 < e.ram_gb < 20.0  # 本体 4.8 GB と VAE を fp32 に広げた分 + 作業用メモリ × 2 + 固定分
    assert any("CPU は推定・実測なし" in r for r in e.reasons)  # CPU での実測は無い
    tiny = judge(db.get("image_sdxl_simple"), no_gpu(4), db).estimate
    assert tiny.fit == "too_tight"  # メモリが足りなければ動かない


def test_cpu_family_excludes_untested_versions(db):
    from genmodelfit.estimate import cpu_family

    sa = [m for m in db.models if "medium" in m.id.lower() and (m.comfy_family == "stable_audio")]
    assert all(cpu_family(m) is None for m in sa)
    assert cpu_family(db.get("utility_image_upscale_supir")) is None


def test_cpu_judgement_monotonic_in_ram(db):
    for mid in ("image_sdxl_simple", "manual:stable-audio-3-small-music"):
        prev = -1
        for ram in (4, 8, 12, 16, 24, 32, 64):
            fit = judge(db.get(mid), no_gpu(ram), db).estimate.fit
            assert ORDER.index(fit) >= prev
            prev = ORDER.index(fit)


def pc_line_en(s) -> str:
    """表の先頭の行の英語(日本語は #76 で日本語だけ)。"""
    with i18n.forced("en"):
        return str(pc_line(s))


def unusable_en(s) -> list[str]:
    with i18n.forced("en"):
        return [str(x) for x in _unusable_gpus(s)]


# --- 表の先頭の行と Mode ---------------------------------------------------------------------------------


def test_no_gpu_header_lines(db, monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_uhd770.json"), [])
    s = system_of(monkeypatch, adapters, WIN11, ram_gb=15.7)
    sel = select(db, s, min_fit="marginal", include_tools=False, drop_unknown_size=True)
    console = make_console(width=100)
    with console.capture() as cap, i18n.forced("en"):
        print_fit_table(
            sel.fits, title="Recommended", system=s, console=console, matched=sel.matched, hidden=sel.hidden
        )
    lines = cap.get().splitlines()
    assert lines[0].startswith(
        "PC: Intel(R) UHD Graphics 770 (integrated; not usable for generation → judged"
    )
    tools = sel.hidden["tools"]
    assert any(
        "This PC has no usable GPU: only models that run on CPU are shown"
        f" ({tools} tools hidden: --include-tools)" in x
        for x in lines[:3]
    )
    assert all(len(x) <= 100 for x in lines)


def test_mode_cell_gpu_req_and_ram(db):
    e = judge(db.get("image_z_image_turbo"), no_gpu(32), db).estimate
    assert e.fit == "too_tight" and mode_cell(e, no_gpu(32)) == "GPU req"
    tiny = judge(db.get("image_sdxl_simple"), no_gpu(4), db).estimate
    assert mode_cell(tiny, no_gpu(4)) == "RAM"
    assert no_gpu_line(vendor_system("amd", 24.0), {}) == ""


# --- #26 同じ GPU で成功した実測 ---------------------------------------------------------------------


def nvidia(name: str, vram: float, ram: float = 32.0) -> SystemInfo:
    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram, driver="610.88", backend="cuda")
    classify_gpu(g, windows_driver=True, comfy_windows=True)
    return SystemInfo(os="Windows", cpu="", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


# 作者の Wan 2.2 5B の実測の設定(AI生成キットの「はやい」画質。#95 から指定しないと既定の設定で判定する)
WAN22_AT = Settings(width=640, height=640, frames=73)


def test_issue_26_measured_success_on_same_gpu_is_good(db):
    """Issue #26: RTX 5080 16GB で 15.8 GB・56 秒で成功した Wan 2.2 5B が marginal / offload だった。
    #95 から、段階に使う量は max(実測のピーク + 文脈の分, 重み + 固定分)。Wan 2.2 5B の重み + 固定分は
    約 18.4 GB で 15.9 GB に収まらない(実測は動的 VRAM で重みの一部を逃がしていた: offload dynamic・
    上限に張り付き)ので、同じ GPU・同じ設定の出どころが 2 つそろっても段階は実測で決めない。
    実測で段階が決まる組は tests/test_scoring.py(SDXL)。"""
    s = nvidia("NVIDIA GeForce RTX 5080", 15.9)
    e = judge(db.get("video_wan2_2_5B_ti2v"), s, db, settings=WAN22_AT).estimate
    assert e.basis == "measured" and e.fit_from == "estimate"
    assert e.measured_level_vram_gb is None
    # 指定しなければ既定の設定(832x480・81 コマ)で判定し、実測は値をずらすだけ
    d = judge(db.get("video_wan2_2_5B_ti2v"), s, db).estimate
    assert d.fit_from == "estimate" and d.basis == "estimated"


def test_issue_26_smaller_gpu_stays_estimated(db):
    e = judge(
        db.get("video_wan2_2_5B_ti2v"), nvidia("NVIDIA GeForce RTX 3060", 12.0), db, settings=WAN22_AT
    ).estimate
    # #100 の段ごとの式で、全部載せ・順に載せ替えの使用率が 85% を超えて marginal。逃がせば good なので
    # 逃がす(#108)。段階は見積もりで決める(同じ GPU の実測ではない)
    assert (e.fit, e.run_mode, e.fit_from) == ("good", "offload", "estimate")
    assert any("段階は見積もりで決めた" in r for r in e.reasons)


def test_issue_26_pinned_measurement_is_not_perfect(db):
    """張り付いた実測(動的 VRAM が空きを使い切った)は、大きな GPU でも perfect の根拠にしない。"""
    e = judge(db.get("video_wan2_2_5B_ti2v"), nvidia("NVIDIA GeForce RTX 5090", 31.8), db).estimate
    assert e.fit in ("good", "perfect") and e.run_mode == "gpu"
    if e.fit_from == "measurement":
        assert e.fit == "good"


def test_issue_26_monotonic_in_vram(db):
    m = db.get("video_wan2_2_5B_ti2v")
    prev = -1
    for vram in (8, 12, 15.9, 16, 20, 24, 32, 48):
        fit = judge(m, nvidia("NVIDIA GeForce RTX 5080", vram), db).estimate.fit
        assert ORDER.index(fit) >= prev, (vram, fit)
        prev = ORDER.index(fit)


def test_issue_26_ram_still_limits(db):
    e = judge(db.get("video_wan2_2_5B_ti2v"), nvidia("NVIDIA GeForce RTX 5080", 15.9, ram=4.0), db).estimate
    assert e.fit in ("marginal", "too_tight")


# --- Checker の指摘(2026-10-21)の回帰 ------------------------------------------------------------


@pytest.mark.parametrize(
    "cpu",
    ["AMD Ryzen 9 7945HX with Radeon Graphics", "AMD Ryzen 7 7745HX", "AMD Ryzen 9 9955HX3D"],
)
def test_dragon_and_fire_range_igpu_is_cpu(cpu):
    a = Adapter(name="AMD Radeon(TM) Graphics", vendor="amd", vram_gb=0.5)
    assert hardware.amd_gfx(a.name, cpu) == ("gfx1036", True)
    assert classify_adapter(a, Host("Windows", 26100, cpu=cpu), 32.0) is None and a.integrated is True


@pytest.mark.parametrize(
    ("name", "gfx"),
    [
        ("AMD Radeon RX 9070 XT", "gfx1201"),
        ("AMD Radeon RX 9060 XT", "gfx1200"),
        ("AMD Radeon RX 6850M XT", "gfx1031"),
        ("AMD Radeon RX 6800M", "gfx1031"),
        ("AMD Radeon RX 6800S", "gfx1032"),
        ("AMD Radeon RX 6650M", "gfx1032"),
        ("AMD Radeon RX 6550M", "gfx1034"),
        ("AMD Radeon RX 7700S", "gfx1102"),
        ("AMD Radeon RX 7600M XT", "gfx1102"),
        ("AMD Radeon RX 7800 XT", "gfx1101"),
        ("AMD Radeon RX 6800 XT", "gfx1030"),
    ],
)
def test_amd_mobile_names(name, gfx):
    assert hardware.amd_gfx(name)[0] == gfx


@pytest.mark.parametrize("name", ["Intel(R) Arc(TM) A770M Graphics", "Intel(R) Arc(TM) A370M Graphics"])
def test_arc_mobile_is_xpu_on_windows(name):
    a = Adapter(name=name, vendor="intel", vram_gb=8.0)
    g = classify_adapter(a, WIN11, 32.0)
    assert g is not None and g.backend == "xpu" and g.memory == "dedicated"


def test_unknown_os_versions_are_not_assumed():
    a = Adapter(name="AMD Radeon RX 7900 XTX", vendor="amd", vram_gb=24.0)
    assert classify_adapter(a, Host("Windows"), 32.0) is None and "Windows 11" in a.reason
    i = Adapter(name="Intel(R) Arc(TM) A770 Graphics", vendor="intel", vram_gb=16.0)
    assert classify_adapter(i, Host("Windows"), 32.0) is None
    m = Adapter(name="Apple M2 Pro", vendor="apple")
    assert classify_adapter(m, Host("Darwin"), 32.0) is None and "macOS 14.0" in m.reason


def test_unusable_nvidia_still_probes_others(monkeypatch):
    """Kepler の NVIDIA しか使えないときも、既定で AMD・Intel を調べて判定に使う。"""
    kepler = Gpu(
        name="NVIDIA GeForce GTX 780",
        vendor="nvidia",
        vram_total_gb=3.0,
        driver="470.0",
        backend="cuda",
        compute_capability="3.5",
    )
    classify_gpu(kepler, windows_driver=True, comfy_windows=True)
    assert kepler.backend == "none"
    adapters, _ = parse_wmi_video(fx("wmi_arc_a770.json"), reg("registry_arc_a770.json"))
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(hardware, "detect_gpus", lambda **kw: ([kepler], []))
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda: (adapters, []))
    s = detect(memory=Memory(total=32 * GB, available=32 * GB), host=WIN11)
    assert s.other_gpus_checked and s.best_gpu is not None and s.best_gpu.backend == "xpu"


def test_other_vendor_community_measurement_only_calibrates(db):
    from genmodelfit.models import Measurement, Settings

    m = db.get("image_sdxl_simple")
    base = next(x for x in db.measurements if x.model_id == m.id)
    comm = Measurement(
        model_id=m.id,
        gpu_name="NVIDIA GeForce RTX 4090",
        gpu_vram_gb=24.0,
        peak_vram_gb=base.peak_vram_gb,
        settings=Settings(**vars(base.settings)),
        origin="community",
        n_submissions=3,
        consistent=True,
    )
    s = vendor_system("amd", 24.0, 64.0)
    e = estimate(m, s, measurements=[comm], coefficients=Coefficients())
    # ほかの利用者の実測は同じ GPU の PC にだけ使う(0.9.0)。別の会社の GPU では使わない
    assert e.basis == "estimated" and e.basis_detail == "estimated" and e.fit_from == "estimate"
    # 作者・この PC の実測(origin なし)は、別の会社の GPU でも補正だけに使う(今までどおり)
    own = Measurement(**{**vars(comm), "origin": None, "n_submissions": None, "consistent": None})
    e = estimate(m, s, measurements=[own], coefficients=Coefficients())
    assert e.basis == "estimated" and e.basis_detail == "calibrated" and e.fit_from == "estimate"


def test_shared_igpu_ram_includes_gpu_share(db):
    """Radeon 780M・メモリ 12 GB: SDXL は GPU の分を載せると、メインメモリも足りない(#100 から MOSS-Image は
    GPU の分だけで足りないので、SDXL で確かめる)。"""
    a = Adapter(name="AMD Radeon 780M Graphics", vendor="amd", vram_gb=0.5)
    g = classify_adapter(a, WIN11, 12.0)
    assert g.carveout_gb == 0.5
    s = SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=12.0, ram_available_gb=12.0, gpus=[g])
    e = judge(db.get("image_sdxl_simple"), s, db).estimate
    assert e.ram_gb > 12.0 and e.fit in ("marginal", "too_tight")
    assert any("メインメモリから使う" in r for r in e.reasons)


def test_issue_26_peak_must_fit_this_vram(db):
    """公称 16 GB の実測 15.8 GB は、15.6 GB(95% 以上)の GPU では段階の根拠にしない(収まらない)。"""
    e = judge(db.get("video_wan2_2_5B_ti2v"), nvidia("NVIDIA GeForce RTX 5080", 15.6), db).estimate
    assert e.fit_from == "estimate"


def test_pc_line_hides_unknown_vendor_and_legend_caps(db, monkeypatch):
    adapters, _ = parse_wmi_video(fx("wmi_uhd770.json"), [])
    adapters.append(Adapter(name="Microsoft Basic Display Adapter", vendor="unknown", source="wmi"))
    s = system_of(monkeypatch, adapters, WIN11, ram_gb=16.0)
    assert "Microsoft" not in pc_line(s)
    from genmodelfit.table import legend

    sel = select(db, vendor_system("amd", 24.0, 64.0), min_fit="marginal", drop_unknown_size=True, limit=3)
    with i18n.forced("en"):
        lines = legend(sel.fits, sel.hidden, system=vendor_system("amd", 24.0, 64.0))
    # #108: 会社の上限の行は無くした。確認済みでない種類には ✓ の行も出さない
    assert not any("AMD / Intel / Apple" in x or "✓" in x for x in lines)
    assert all(len(x) <= 100 for x in lines)
    nv = vendor_system("nvidia", 24.0, 64.0)
    sel = select(db, nv, min_fit="marginal", drop_unknown_size=True, limit=3)
    with i18n.forced("en"):
        lines = legend(sel.fits, sel.hidden, system=nv)
    assert len([x for x in lines if x.startswith("✓ verified GPU kind")]) == 1
    assert all(len(x) <= 100 for x in lines)
    lines = legend(sel.fits, sel.hidden, system=nv)
    assert len([x for x in lines if x.startswith("✓ 確認済みの種類")]) == 1


# --- #56: 安全側に倒れすぎていた名前 --------------------------------------------------------------


@pytest.mark.parametrize("cpu", ["AMD Ryzen Z1 Extreme", "AMD Ryzen Z1"])
def test_ryzen_z1_is_gfx1103_apu(cpu):
    a = Adapter(name="AMD Radeon(TM) Graphics", vendor="amd", vram_gb=4.0)
    assert hardware.amd_gfx(a.name, cpu) == ("gfx1103", True)
    g = classify_adapter(a, Host("Windows", 26100, cpu=cpu), 16.0)
    assert g is not None and g.backend == "rocm" and g.memory == "shared" and g.gfx_target == "gfx1103"


@pytest.mark.parametrize(
    "cpu", ["AMD Ryzen 9 7940HX with Radeon Graphics", "AMD Ryzen 7 7840HX", "AMD Ryzen 9 9850HX"]
)
def test_dragon_range_40hx_reason_is_two_cu(cpu):
    a = Adapter(name="AMD Radeon(TM) Graphics", vendor="amd", vram_gb=0.5)
    assert hardware.amd_gfx(a.name, cpu) == ("gfx1036", True)
    assert classify_adapter(a, Host("Windows", 26100, cpu=cpu), 32.0) is None
    assert "2 CU" in a.reason and "分からない" not in a.reason


@pytest.mark.parametrize("name", ["AMD Radeon RX 6300", "AMD Radeon PRO W6400", "AMD Radeon RX 6300M"])
def test_rx6300_is_navi24(name):
    assert hardware.amd_gfx(name) == ("gfx1034", False)
    g = classify_adapter(Adapter(name=name, vendor="amd", vram_gb=2.0), LINUX, 32.0)
    assert g is not None and g.gfx_target == "gfx1034"


def test_lspci_unknown_device_keeps_device_id():
    text = (
        "0000:03:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] "
        "Device [1002:7551] (rev c0)\n"
        "0000:04:00.0 VGA compatible controller [0300]: Intel Corporation Device [8086:e20b]\n"
    )
    names = [a.name for a in parse_lspci(text)]
    assert names == ["AMD Device 7551", "Intel Device e20b"]


@pytest.mark.parametrize(
    ("name", "vendor", "gfx_or_backend"),
    [
        ("AMD Device 7551", "amd", "gfx1201"),  # Navi 48(Radeon AI PRO R9700)
        ("AMD Device 744c", "amd", "gfx1100"),
        ("AMD Device 7424", "amd", "gfx1034"),  # RX 6300
        ("Intel Device e20b", "intel", "xpu"),  # B580
    ],
)
def test_old_pciids_device_is_resolved_by_pci_id(name, vendor, gfx_or_backend):
    a = Adapter(name=name, vendor=vendor, vram_gb=12.0, source="lspci")
    g = classify_adapter(a, LINUX, 32.0)
    assert g is not None and gfx_or_backend in (g.gfx_target, g.backend)
    assert any("update-pciids" in n for n in g.notes) and a.name == name


@pytest.mark.parametrize(("name", "vendor"), [("AMD Device 7fff", "amd"), ("Intel Device e2ff", "intel")])
def test_unknown_pci_id_hints_update_pciids(name, vendor):
    a = Adapter(name=name, vendor=vendor, vram_gb=8.0)
    assert classify_adapter(a, LINUX, 32.0) is None and "update-pciids" in a.reason


def test_old_pciids_raphael_is_cpu_with_two_cu_reason():
    a = Adapter(name="AMD Device 164e", vendor="amd", vram_gb=0.5)
    assert classify_adapter(a, LINUX, 32.0) is None and "2 CU" in a.reason


@pytest.mark.parametrize(
    ("name", "vram"),
    [
        ("Intel Arc A770M Graphics", 16.0),
        ("Intel Arc A730M Graphics", 12.0),
        ("Intel Arc A550M Graphics", 8.0),
        ("Intel Arc A370M Graphics", 4.0),
        ("Intel Device 5690", 16.0),  # 古い pci.ids の A770M
    ],
)
def test_linux_arc_mobile_uses_nominal_vram(name, vram):
    a = Adapter(name=name, vendor="intel", source="lspci")  # Linux の xe・i915 は VRAM を sysfs に出さない
    g = classify_adapter(a, LINUX, 32.0)
    assert g is not None and g.vram_total_gb == vram and a.vram_source == "name"
    assert any("公称" in n for n in g.notes)


def test_linux_arc_without_nominal_vram_is_cpu():
    a = Adapter(name="Intel Arc A570M Graphics", vendor="intel", source="lspci")
    assert classify_adapter(a, LINUX, 32.0) is None and "VRAM" in a.reason


@pytest.mark.parametrize(
    ("name", "host"),
    [
        (
            "AMD Custom GPU 0405",
            Host("Windows", 26100, cpu="AMD Custom APU 0405"),
        ),  # Windows のドライバの名前
        ("AMD VanGogh [AMD Custom GPU 0405]", LINUX),  # lspci(pci.ids)
        ("AMD Device 163f", LINUX),  # 古い pci.ids
        ("AMD Device 1435", LINUX),  # OLED 版(Sephiroth)
    ],
)
def test_steam_deck_van_gogh_is_rdna2_apu(name, host):
    """Van Gogh(gfx1033)は 8 CU の RDNA 2(Valve の仕様)で、TheRock の Release Ready。680M と同じく GPU。"""
    a = Adapter(name=name, vendor="amd", vram_gb=1.0)
    g = classify_adapter(a, host, 16.0)
    assert g is not None and g.backend == "rocm" and g.gfx_target == "gfx1033" and g.memory == "shared"
    assert "2 CU" not in (a.reason or "")
