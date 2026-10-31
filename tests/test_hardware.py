"""ハードウェアの検出(#21)。本物の nvidia-smi・PowerShell・lspci・レジストリは呼ばない
(conftest が塞ぐ)。出力は tests/fixtures/hardware のフィクスチャで差し替える。"""

from __future__ import annotations

import getpass
import importlib
import json
import os
import platform
from pathlib import Path

import pytest

from genmodelfit import cli, hardware
from genmodelfit.estimate import Coefficients, estimate, ram_limit
from genmodelfit.hardware import (
    QUERY,
    QUERY_OLD,
    Memory,
    cc_from_name,
    classify_gpu,
    comfy_precision,
    detect,
    parse_compute_cap,
    parse_lspci,
    parse_nvidia_smi,
    parse_paging_files,
    parse_wmi_video,
    query_nvidia_smi,
    torch_support,
)
from genmodelfit.i18n import forced
from genmodelfit.models import Gpu, ModelEntry, ModelFile, SystemInfo

E = importlib.import_module("genmodelfit.estimate")

FIX = Path(__file__).parent / "fixtures" / "hardware"
GB = 2**30


def fx(name: str) -> str:
    return (FIX / name).read_bytes().decode("utf-8")


def reg(name: str) -> list[dict]:
    return json.loads(fx(name))


def gpu(name: str, cc: str | None, driver: str | None = "581.57", vram: float = 8.0, **kw) -> Gpu:
    return Gpu(
        name=name,
        vendor="nvidia",
        vram_total_gb=vram,
        driver=driver,
        backend="cuda",
        compute_capability=cc,
        **kw,
    )


def classify(g: Gpu, windows: bool = True) -> Gpu:
    return classify_gpu(g, windows_driver=windows, comfy_windows=windows)


MEM = Memory(total=32 * GB, available=24 * GB)


class FakeRun:
    """_run の差し替え。呼ばれたコマンドを覚え、決めた順に (終了コード, 出力) を返す。"""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        assert timeout > 0
        return self.results.pop(0) if self.results else None


# --- nvidia-smi の出力 -------------------------------------------------------------------------


def test_real_rtx3070_output():
    """この PC(RTX 3070、ドライバ 566.03)の本物の出力。改行は CRLF。"""
    gpus = parse_nvidia_smi(fx("nvsmi_rtx3070_win.csv"), QUERY)
    assert len(gpus) == 1
    g = classify(gpus[0])
    assert (g.name, g.vram_total_gb, g.vram_free_gb, g.vram_used_gb) == (
        "NVIDIA GeForce RTX 3070",
        8.0,
        4.25,
        3.58,
    )
    assert (g.driver, g.compute_capability, g.compute_capability_source, g.arch) == (
        "566.03",
        "8.6",
        "nvidia-smi",
        "ampere",
    )
    assert g.backend == "cuda"
    assert g.torch_cuda == ["cu129", "cu128", "cu126"]  # CUDA 13 は 580 以上
    assert (g.compute_dtype, g.bf16, g.fp8) == ("fp16", True, False)
    assert any("cu130・cu132" in n and "580" in n for n in g.notes)


def test_multi_gpu_uses_the_largest():
    gpus = [classify(g) for g in parse_nvidia_smi(fx("nvsmi_multi.csv"), QUERY)]
    s = SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=64, ram_available_gb=64, gpus=gpus)
    assert [g.name for g in gpus] == ["NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 3060"]
    assert s.best_gpu.name == "NVIDIA GeForce RTX 4090" and s.vram_gb == pytest.approx(23.99, abs=0.01)
    assert gpus[0].fp8 is True and gpus[1].fp8 is False


def test_na_values_and_unified_memory():
    gb10, rtx = parse_nvidia_smi(fx("nvsmi_na.csv"), QUERY)
    # メモリを CPU と共有する GB10 は VRAM を返さない → 判定に使わない
    assert gb10.vram_total_gb == 0 and gb10.backend == "none" and gb10.vram_free_gb is None
    assert any("VRAM の量を返さない" in n for n in gb10.notes)
    # 空きが [N/A] なら 総量 − 使用量。ドライバが [Not Supported] なら None(ドライバでは断らない)
    assert rtx.vram_free_gb == pytest.approx((10240 - 512) / 1024, abs=0.01) and rtx.driver is None
    classify(rtx)
    assert rtx.backend == "cuda" and any("ドライバの版が読めない" in n for n in rtx.notes)


def test_garbage_lines_are_skipped():
    gpus = parse_nvidia_smi(fx("nvsmi_garbage.txt"), QUERY)
    assert len(gpus) == 1  # 数の読めない VRAM の行だけが残り、判定には使わない
    assert gpus[0].backend == "none" and gpus[0].vram_total_gb == 0
    assert parse_nvidia_smi("", QUERY) == [] and parse_nvidia_smi("\n\n", QUERY) == []
    assert parse_nvidia_smi(fx("nvsmi_no_driver.txt"), QUERY) == []


@pytest.mark.parametrize(
    ("text", "cc"),
    [
        ("8.6", (8, 6)),
        ("12.0", (12, 0)),
        ("8,9", (8, 9)),
        ("[N/A]", None),
        ("", None),
        ("abc", None),
        ("8", None),
    ],
)
def test_compute_cap_values(text, cc):
    assert parse_compute_cap(text) == cc


def test_old_driver_falls_back_to_query_without_compute_cap():
    run = FakeRun((2, fx("nvsmi_old_error.txt")), (0, fx("nvsmi_old_maxwell.csv")))
    import genmodelfit.hardware as h

    h_run = h._run
    try:
        h._run = run
        gpus, notes = query_nvidia_smi("nvidia-smi")
    finally:
        h._run = h_run
    assert [c[1] for c in run.calls] == [
        f"--query-gpu={','.join(QUERY)}",
        f"--query-gpu={','.join(QUERY_OLD)}",
    ]
    assert len(gpus) == 1 and any("compute_cap を聞けなかった" in n for n in notes)
    g = classify(gpus[0])
    # 名前から Maxwell と推す。ドライバ 472 は CUDA 12 の PyTorch に足りないので CPU、更新すれば動く
    assert (g.compute_capability, g.compute_capability_source, g.arch) == ("5.2", "name", "maxwell")
    assert g.backend == "none" and g.torch_cuda == []
    assert any("525 以上" in n and "更新すれば GPU で動く" in n for n in g.notes)


def test_old_kepler_is_cpu_even_after_driver_update(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run", FakeRun((2, fx("nvsmi_old_error.txt")), (0, fx("nvsmi_old_kepler.csv")))
    )
    gpus, _ = query_nvidia_smi("nvidia-smi")
    g = classify(gpus[0])
    assert g.arch == "kepler" and g.backend == "none"
    assert any("Kepler" in n and "更新しても GPU では動かない" in n for n in g.notes)


@pytest.mark.parametrize(
    ("results", "want"),
    [
        ([None], "動かない・時間内に終わらない"),
        ([(9, fx("nvsmi_no_driver.txt")), (9, fx("nvsmi_no_driver.txt"))], "終了コード 9"),
        ([(6, fx("nvsmi_no_devices.txt")), (6, fx("nvsmi_no_devices.txt"))], "GPU が見つからない"),
        ([(0, "garbage\n"), (0, "garbage\n")], "出力が読めない"),
    ],
)
def test_nvidia_smi_failures_never_raise(monkeypatch, results, want):
    monkeypatch.setattr(hardware, "_run", FakeRun(*results))
    gpus, notes = query_nvidia_smi("nvidia-smi")
    assert gpus == [] and any(want in n for n in notes)


def test_nvidia_smi_locations(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    cands = hardware.nvidia_smi_candidates()
    if os.name == "nt":
        assert any(c.lower().endswith(r"system32\nvidia-smi.exe") for c in cands)
        nvsmi = next(c for c in cands if c.endswith(r"NVIDIA Corporation\NVSMI\nvidia-smi.exe"))
        monkeypatch.setattr(hardware.os.path, "isfile", lambda p: p == nvsmi)
        assert hardware.find_nvidia_smi() == nvsmi
    else:
        assert "/usr/lib/wsl/lib/nvidia-smi" in cands
    monkeypatch.setattr(hardware.os.path, "isfile", lambda p: False)
    assert hardware.find_nvidia_smi() is None
    s = detect(memory=MEM)
    assert s.gpus == [] and any("nvidia-smi が見つからない" in n for n in s.notes)


# --- 世代ごとの判定 -------------------------------------------------------------------------------

ARCH_CASES = [
    # (名前, cc, ドライバ, Windows か) → (backend, arch, 計算の精度, bf16, fp8, 動く CUDA 版)
    ("NVIDIA GeForce GTX 780", "3.5", "474.44", True, ("none", "kepler", None, None, None, [])),
    ("Tesla K80", "3.7", "535.183.01", False, ("none", "kepler", None, None, None, [])),
    ("NVIDIA GeForce GTX 980", "5.2", "572.16", True, ("cuda", "maxwell", "fp32", False, False, ["cu126"])),
    ("Quadro K2200", "5.0", "552.22", True, ("cuda", "maxwell", "fp32", False, False, ["cu126"])),
    ("NVIDIA GeForce GTX 1080", "6.1", "572.16", True, ("cuda", "pascal", "fp16", False, False, ["cu126"])),
    (
        "NVIDIA GeForce GTX 1080",
        "6.1",
        "570.86.15",
        False,
        ("cuda", "pascal", "fp32", False, False, ["cu126"]),
    ),
    ("NVIDIA GeForce GT 1030", "6.1", "572.16", True, ("cuda", "pascal", "fp32", False, False, ["cu126"])),
    ("NVIDIA TITAN V", "7.0", "572.16", True, ("cuda", "volta", "fp16", False, False, ["cu126"])),
    (
        "NVIDIA GeForce RTX 2080",
        "7.5",
        "566.03",
        True,
        ("cuda", "turing", "fp16", False, False, ["cu129", "cu128", "cu126"]),
    ),
    (
        "NVIDIA GeForce GTX 1660 SUPER",
        "7.5",
        "566.03",
        True,
        ("cuda", "turing", "fp32", False, False, ["cu129", "cu128", "cu126"]),
    ),
    (
        "NVIDIA GeForce RTX 3070",
        "8.6",
        "566.03",
        True,
        ("cuda", "ampere", "fp16", True, False, ["cu129", "cu128", "cu126"]),
    ),
    (
        "NVIDIA GeForce RTX 4090",
        "8.9",
        "581.57",
        True,
        ("cuda", "ada", "fp16", True, True, ["cu132", "cu130", "cu129", "cu128", "cu126"]),
    ),
    (
        "NVIDIA H100 PCIe",
        "9.0",
        "580.95.05",
        False,
        ("cuda", "hopper", "fp16", True, True, ["cu132", "cu130", "cu129", "cu128", "cu126"]),
    ),
    (
        "NVIDIA GeForce RTX 5090",
        "12.0",
        "576.02",
        True,
        ("cuda", "blackwell", "fp16", True, True, ["cu129", "cu128"]),
    ),
    (
        "NVIDIA GeForce RTX 5090",
        "12.0",
        "581.57",
        True,
        ("cuda", "blackwell", "fp16", True, True, ["cu132", "cu130", "cu129", "cu128"]),
    ),
    # #101: 表より新しい世代は断らない(断るのは表より古い世代だけ)
    ("NVIDIA Future GPU", "13.0", "700.00", True, ("cuda", "unknown", "fp16", True, True, [])),
    # Blackwell は CUDA 12.8 の版のドライバ(570)から
    ("NVIDIA GeForce RTX 5080", "12.0", "566.03", True, ("none", "blackwell", None, None, None, [])),
]


@pytest.mark.parametrize(("name", "cc", "driver", "windows", "want"), ARCH_CASES)
def test_arch_judgement(name, cc, driver, windows, want):
    g = classify(gpu(name, cc, driver), windows)
    assert (g.backend, g.arch, g.compute_dtype, g.bf16, g.fp8, g.torch_cuda) == want
    if g.backend == "none":
        assert g.notes, "CPU 扱いには理由を付ける"


@pytest.mark.parametrize(
    ("driver", "windows", "usable"),
    [
        ("527.41", True, True),
        ("527.37", True, True),  # 表 3 は 12.x を 525 以上(OS で分けない)
        ("525.05", True, True),
        ("524.99", True, False),
        ("525.60.13", False, True),
        ("520.61.05", False, False),
        ("472.12", True, False),
    ],
)
def test_driver_minimum_for_cuda12(driver, windows, usable):
    g = classify(gpu("NVIDIA GeForce GTX 1070", "6.1", driver), windows)
    assert (g.backend == "cuda") is usable


def test_unknown_cc_does_not_reject():
    sup = torch_support(None, "566.03", windows_driver=True)
    assert sup.usable and sup.builds == ["cu129", "cu128", "cu126"]
    g = classify(gpu("NVIDIA Mystery GPU", None, "566.03"))
    assert g.backend == "cuda" and g.arch is None and g.compute_dtype is None


@pytest.mark.parametrize(
    ("name", "cc"),
    [
        ("NVIDIA GeForce GTX 750 Ti", (5, 0)),
        ("Quadro K620", (5, 0)),  # 名前は K だが Maxwell
        ("Quadro K4000", (3, 0)),
        ("NVIDIA GeForce GTX TITAN X", (5, 2)),
        ("NVIDIA TITAN X (Pascal)", (6, 1)),
        ("NVIDIA TITAN X", (6, 1)),  # GeForce の付かない TITAN X は Pascal
        ("Quadro K6000", (3, 5)),
        ("Quadro M2000", (5, 2)),
        ("Quadro M1200", (5, 0)),
        ("NVIDIA TITAN Xp", (6, 1)),
        ("NVIDIA GeForce GTX TITAN", (3, 5)),
        ("NVIDIA GeForce GTX 1650", (7, 5)),
        ("NVIDIA GeForce RTX 2050", (8, 6)),  # ノート用の RTX 2050 は Ampere
        ("NVIDIA GeForce RTX 2060 SUPER", (7, 5)),
        ("NVIDIA RTX A4000", (8, 6)),
        ("NVIDIA RTX 4000 Ada Generation", (8, 9)),
        ("NVIDIA GeForce RTX 5070 Ti", (12, 0)),
        ("Tesla V100-SXM2-16GB", (7, 0)),
        ("Tesla P100-PCIE-16GB", (6, 0)),
        ("NVIDIA GeForce GTX 960M", None),  # 迷う名前は推さない
        ("NVIDIA Something New", None),
    ],
)
def test_cc_from_name(name, cc):
    assert cc_from_name(name) == cc


def test_comfy_precision_follows_comfyui_name_lists():
    # Pascal の一覧(小文字で比べる): Quadro P620 は "p6" に当たる。Windows だけ fp16
    assert comfy_precision((6, 1), "Quadro P620", windows=True)[0] == "fp16"
    assert comfy_precision((6, 1), "Quadro P620", windows=False)[0] == "fp32"
    # fp16 が壊れている一覧(大文字・小文字を区別する)
    assert comfy_precision((7, 5), "NVIDIA T1000 8GB", windows=True)[0] == "fp32"
    assert comfy_precision((7, 5), "NVIDIA GeForce MX450", windows=True)[0] == "fp32"
    assert comfy_precision((7, 5), "Quadro RTX 4000", windows=True)[0] == "fp16"
    assert comfy_precision(None, "x", windows=True) == (None, None, None)


# --- NVIDIA 以外の GPU(WMI・レジストリ・lspci) -------------------------------------------------


def test_wmi_this_pc_drops_virtual_monitors_and_uses_registry_vram():
    adapters, notes = parse_wmi_video(fx("wmi_this_pc.json"), reg("registry_this_pc.json"))
    assert [(a.name, a.vendor, a.vram_gb, a.vram_source) for a in adapters] == [
        ("NVIDIA GeForce RTX 3070", "nvidia", 8.0, "registry")  # AdapterRAM は 4 GiB で頭打ち
    ]
    assert notes == []


def test_wmi_single_object_amd_with_registry_qword():
    adapters, _ = parse_wmi_video(fx("wmi_amd_single.json"), reg("registry_amd_stale.json"))
    assert [(a.name, a.vendor, a.vram_gb, a.vram_source) for a in adapters] == [
        ("AMD Radeon RX 7800 XT", "amd", 15.98, "registry")
    ]


def test_wmi_capped_adapter_ram_is_not_used_and_stale_entries_are_ignored():
    adapters, _ = parse_wmi_video(fx("wmi_intel_nvidia.json"), reg("registry_amd_stale.json"))
    got = {a.name: (a.vendor, a.vram_gb, a.vram_source) for a in adapters}
    assert set(got) == {"Intel(R) UHD Graphics 770", "NVIDIA GeForce RTX 4070"}  # リモートの画面は捨てる
    assert got["Intel(R) UHD Graphics 770"] == ("intel", 0.12, "registry")
    # 4070 はレジストリに無く(残っているのは取り外した GTX 1060)、AdapterRAM は頭打ちの値なので分からない
    assert got["NVIDIA GeForce RTX 4070"] == ("nvidia", None, None)


def test_registry_ambiguous_and_binary_values():
    pnp = "PCI\\VEN_1002&DEV_73BF&SUBSYS_0E3A1002&REV_C1\\6&00000000&0&00000009"
    wmi = json.dumps([{"Name": "AMD Radeon RX 6800", "AdapterRAM": 4293918720, "PNPDeviceID": pnp}])
    two = [
        {"DriverDesc": "AMD Radeon RX 6800", "HardwareInformation.qwMemorySize": 16 * GB},
        {"DriverDesc": "AMD Radeon(TM) RX 6800", "HardwareInformation.qwMemorySize": 8 * GB},
    ]
    assert parse_wmi_video(wmi, two)[0][0].vram_gb is None  # 同じ名前で値が違う → 使わない
    binary = [
        {
            "MatchingDeviceId": "pci\\ven_1002&dev_73bf",
            "HardwareInformation.qwMemorySize": (16 * GB).to_bytes(8, "little"),
        }
    ]
    assert parse_wmi_video(wmi, binary)[0][0].vram_gb == 16.0
    tiny = [{"MatchingDeviceId": "pci\\ven_1002&dev_73bf", "HardwareInformation.qwMemorySize": 16384}]
    assert parse_wmi_video(wmi, tiny)[0][0].vram_gb is None  # MB で書いたとみられる値は使わない


def test_wmi_notes_for_missing_driver_and_device_errors():
    adapters, _ = parse_wmi_video(fx("wmi_basic_nvidia.json"), [])
    assert [a.vendor for a in adapters] == ["nvidia", "amd"]
    assert adapters[1].vram_gb == 0.5 and adapters[1].vram_source == "wmi"
    # #101: ドライバの無い NVIDIA の注意は名前に頼らず _other_gpu_notes が出す(nvidia-smi で判定しないとき)
    assert any("nvidia-smi で確かめられない" in n for n in hardware._other_gpu_notes(adapters, False))
    # #101: AMD・Intel のコード 43 の部品は classify_adapter が理由にして断る(注意を 2 回出さない)
    assert adapters[1].config_error == 43
    assert hardware.classify_adapter(adapters[1], hardware.Host("Windows", 26100), 32.0) is None
    assert "コード 43" in adapters[1].reason


@pytest.mark.parametrize("text", ["", "null", "not json", "[1, 2]", '{"Name": "x"}'])
def test_wmi_garbage(text):
    assert parse_wmi_video(text, None) == ([], [])


def test_lspci():
    seen: list[str] = []

    def vram_of(slot):
        seen.append(slot)
        return 16 * GB if slot == "0000:0b:00.0" else None

    adapters = parse_lspci(fx("lspci_amd_nvidia.txt"), vram_of)
    assert [(a.name, a.vendor, a.vram_gb) for a in adapters] == [
        ("NVIDIA GeForce RTX 3070", "nvidia", None),
        ("AMD Radeon RX 7700 XT / 7800 XT", "amd", 16.0),
        ("AMD Raphael", "amd", None),
        ("Intel Arc A770", "intel", None),
    ]
    assert len(seen) == 4 and parse_lspci("garbage") == []


# --- detect のまとめ方 -----------------------------------------------------------------------------


def _patch_nvidia(monkeypatch, text: str | None):
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda: None if text is None else "nvidia-smi")
    monkeypatch.setattr(hardware, "_run", FakeRun((0, text)) if text is not None else FakeRun())


def test_other_gpus_not_checked_when_nvidia_found(monkeypatch):
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))

    def boom():
        raise AssertionError("NVIDIA の GPU があるときは既定では調べない")

    monkeypatch.setattr(hardware, "detect_other_gpus", boom)
    s = detect(memory=MEM)
    assert s.best_gpu.name == "NVIDIA GeForce RTX 3070" and not s.other_gpus_checked and s.other_gpus == []


def test_amd_only_pc_is_judged_as_cpu_with_a_note(monkeypatch):
    """Windows 10 の AMD は ROCm 版 PyTorch の対象外なので CPU(理由付き)。
    Windows 11 なら ROCm で判定(#22)。"""
    _patch_nvidia(monkeypatch, None)
    adapters, _ = parse_wmi_video(fx("wmi_amd_single.json"), reg("registry_amd_stale.json"))
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda: (adapters, []))
    s = detect(memory=MEM, host=hardware.Host("Windows", 19045))
    assert s.best_gpu is None and s.vram_gb == 0 and s.other_gpus_checked
    assert s.other_gpus[0].name == "AMD Radeon RX 7800 XT"
    assert any(
        "AMD Radeon RX 7800 XT(VRAM 15.98 GB)" in n and "Windows 11 が要る" in n and "CPU として判定した" in n
        for n in s.notes
    )
    adapters, _ = parse_wmi_video(fx("wmi_amd_single.json"), reg("registry_amd_stale.json"))
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda: (adapters, []))
    s = detect(memory=MEM, host=hardware.Host("Windows", 26100))
    assert s.best_gpu.backend == "rocm" and s.vram_gb == 15.98 and s.best_gpu.gfx_target == "gfx1101"


def test_all_gpus_lists_others_without_using_them(monkeypatch):
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))
    adapters, _ = parse_wmi_video(fx("wmi_basic_nvidia.json"), [])
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda **_: (adapters[1:], []))
    s = detect(memory=MEM, other_gpus=True)
    assert s.best_gpu.name == "NVIDIA GeForce RTX 3070" and s.vram_gb == 8.0
    assert any("判定に使わない" in n for n in s.notes)


def test_other_gpu_probe_failure_is_silent(monkeypatch):
    _patch_nvidia(monkeypatch, None)  # _run は何も返さない(PowerShell・lspci が無い・時間切れ)
    s = detect(memory=MEM)
    assert s.other_gpus == [] and s.best_gpu is None


def test_vram_override_keeps_arch_or_assumes_a_plain_gpu(monkeypatch):
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))
    s = detect(memory=MEM, vram_gb=24)
    g = s.best_gpu
    assert (g.name, g.vram_total_gb, g.vram_free_gb, g.arch, g.compute_dtype) == (
        "NVIDIA GeForce RTX 3070",
        24,
        None,
        "ampere",
        "fp16",
    )
    kepler = classify(gpu("NVIDIA GeForce GTX 780", "3.5", "474.44"))
    s = detect(memory=MEM, vram_gb=12, gpus=[kepler])
    assert s.best_gpu.name == "(仮定) 12 GB GPU" and s.best_gpu.arch is None
    assert detect(memory=MEM, vram_gb=0, gpus=[kepler]).vram_gb == 0


def test_vram_override_names_the_generation_not_a_fictional_gpu(monkeypatch):
    """--vram で量だけ変えたとき、理由・注意は「<名前> の世代」と書く(#150)。判定・数値は変えない。"""
    import dataclasses

    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))
    over = detect(memory=MEM, vram_gb=16).best_gpu
    assert Gpu(name="x", vendor="nvidia", vram_total_gb=1).vram_overridden is False
    assert over.vram_overridden is True and over.name == "NVIDIA GeForce RTX 3070"  # name は照合のまま
    assert over.label == "NVIDIA GeForce RTX 3070 の世代"
    c = Coefficients()
    ampere = arch_gpu("NVIDIA GeForce RTX 3090", "8.6", "566.03")
    plain = dataclasses.replace(ampere)
    flagged = dataclasses.replace(ampere, vram_overridden=True)
    assert plain.label == plain.name
    a = estimate(image("fp8"), pc(16, g=plain), coefficients=c)
    b = estimate(image("fp8"), pc(16, g=flagged), coefficients=c)
    assert any("NVIDIA GeForce RTX 3090 は fp8 の計算に対応しない" in r for r in a.reasons)
    assert any("NVIDIA GeForce RTX 3090 の世代 は fp8 の計算に対応しない" in r for r in b.reasons)
    assert not any("3090 は fp8" in r for r in b.reasons)
    da, db = dataclasses.asdict(a), dataclasses.asdict(b)
    for k in ("reasons", "notes"):
        da.pop(k, None)
        db.pop(k, None)
    assert da == db  # 段階・数値はラベル以外同じ
    with forced("en"):
        assert flagged.label == "the NVIDIA GeForce RTX 3090 generation"
        e = estimate(image("fp8"), pc(16, g=flagged), coefficients=c)
        want = "the NVIDIA GeForce RTX 3090 generation does not support fp8 compute"
        assert any(want in r for r in e.reasons)


# --- メモリ ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "want"),
    [
        (["?:\\pagefile.sys"], (None, True)),
        (["C:\\pagefile.sys 0 0"], (None, True)),
        (["C:\\pagefile.sys"], (None, True)),
        (["C:\\pagefile.sys 1024 4096"], (4096 * 2**20, False)),
        (["C:\\pagefile.sys 1024 4096", "D:\\pagefile.sys 2048 2048"], (6144 * 2**20, False)),
        ([], (0, False)),
        ([""], (0, False)),
        (["garbage"], (None, None)),
    ],
)
def test_paging_files(values, want):
    assert parse_paging_files(values) == want


def test_linux_swaps_skip_zram():
    text = (
        "Filename\tType\tSize\tUsed\tPriority\n"
        "/swap.img                               file\t\t8388604\t\t0\t\t-2\n"
        "/dev/zram0                              partition\t4194300\t\t0\t\t100\n"
    )
    assert hardware._linux_swaps(text) == 8388604 * 1024


@pytest.mark.skipif(os.name != "nt", reason="Windows の読み方")
def test_windows_memory(monkeypatch):
    monkeypatch.setattr(
        hardware,
        "_global_memory_status",
        lambda: {
            "total_phys": 32 * GB,
            "avail_phys": 8 * GB,
            "commit_limit": 67 * GB,
            "commit_avail": 7 * GB,
        },
    )
    monkeypatch.setattr(hardware, "_reg_paging_files", lambda: ["C:\\pagefile.sys 4096 8192"])
    m = hardware.read_memory()
    assert (m.commit_limit, m.commit_used, m.swap_total) == (67 * GB, 60 * GB, 35 * GB)
    assert (m.swap_max, m.swap_auto) == (8 * GB, False)
    monkeypatch.setattr(hardware, "_reg_paging_files", lambda: [])
    m = hardware.read_memory()
    assert m.swap_max == 0 and any("ページファイルが無い" in n for n in m.notes)
    monkeypatch.setattr(hardware, "_reg_paging_files", lambda: None)
    assert hardware.read_memory().swap_max is None  # 読めなければ倍率で見る


def test_ram_override_drops_swap_info():
    mem = Memory(total=32 * GB, available=8 * GB, swap_total=4 * GB, swap_max=4 * GB, swap_auto=False)
    s = detect(memory=mem, gpus=[], ram_gb=16)
    assert s.ram_total_gb == 16 and s.swap_max_gb is None and s.swap_auto is None
    s = detect(memory=mem, gpus=[])
    assert (s.swap_total_gb, s.swap_max_gb, s.swap_auto) == (4.0, 4.0, False)


# --- 見積もりへの効き方 ---------------------------------------------------------------------------


def pc(vram: float, ram: float = 32.0, g: Gpu | None = None, **kw) -> SystemInfo:
    gpus = []
    if vram:
        g = g or Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")
        g.vram_total_gb = vram
        gpus = [g]
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus, **kw)


def image(
    precision: str = "fp16", main_gb: float = 4.0, main: str = "unet", vae: str = "vae.safetensors"
) -> ModelEntry:
    """main・vae の名前で ComfyUI の系統が決まる(unet・vae.safetensors は分からない系統)。"""
    files = [
        ModelFile(
            name=f"{main}_{precision}.safetensors", size_bytes=int(main_gb * GB), directory="diffusion_models"
        ),
        ModelFile(name="clip_l.safetensors", size_bytes=int(0.25 * GB), directory="text_encoders"),
        ModelFile(name=vae, size_bytes=int(0.3 * GB), directory="vae"),
    ]
    return ModelEntry(id="t", name="t", modality="image", files=files, precision=precision)


def arch_gpu(name, cc, driver="572.16", windows=True) -> Gpu:
    return classify(gpu(name, cc, driver), windows)


def test_fp32_compute_doubles_working_memory_and_vae():
    c = Coefficients()
    e = image()
    half = estimate(e, pc(12, g=arch_gpu("NVIDIA GeForce RTX 3060", "8.6")), coefficients=c)
    full = estimate(e, pc(12, g=arch_gpu("NVIDIA GeForce GTX 980", "5.2")), coefficients=c)
    act = c.act_image_gb_per_mp * 1024 * 1024 / 1e6
    assert full.vram_gb == pytest.approx(half.vram_gb + act + 0.3, abs=0.02)
    assert any("fp32 で計算する" in r for r in full.reasons)
    assert not any("fp32" in r for r in half.reasons)


def test_precision_notes_for_bf16_and_fp8():
    c = Coefficients()
    turing = arch_gpu("NVIDIA GeForce RTX 2080", "7.5", "566.03")
    ampere = arch_gpu("NVIDIA GeForce RTX 3090", "8.6", "566.03")
    ada = arch_gpu("NVIDIA GeForce RTX 4090", "8.9", "581.57")
    wan = image("bf16", main="wan2.1_t2v_1.3B", vae="wan_2.1_vae.safetensors")  # fp16 で動かせる系統
    bf = estimate(wan, pc(24, g=turing), coefficients=c)
    assert any("bf16 の重みを fp16 で動かす" in r for r in bf.reasons)
    same = estimate(wan, pc(24, g=ampere), coefficients=c)
    assert bf.vram_gb == same.vram_gb  # 2 バイトどうしなので VRAM は同じ
    f8 = estimate(image("fp8"), pc(24, g=ampere), coefficients=c)
    assert any("fp8 の計算に対応しない" in r for r in f8.reasons)
    assert not any("fp8 の計算" in r for r in estimate(image("fp8"), pc(24, g=ada), coefficients=c).reasons)


@pytest.mark.parametrize("case", ARCH_CASES, ids=lambda c: f"{c[0]}-{c[1]}-{c[3]}")
def test_fit_monotonic_in_vram_per_arch(case):
    """同じ世代のまま VRAM を増やしても段階は下がらない。"""
    name, cc, driver, windows, _ = case
    order = ["too_tight", "marginal", "good", "perfect"]
    for e in (
        image("fp16", 2.0),
        image("bf16", 6.0, main="qwen_image", vae="qwen_image_vae.safetensors"),
        image("bf16", 6.0, main="ltx-2-19b-dev"),
        image("bf16", 4.0, main="flux1-dev", vae="ae.safetensors"),
        image("fp8", 11.0, main="z_image_turbo", vae="ae.safetensors"),
    ):
        prev = -1
        for vram in (2, 4, 6, 8, 10, 12, 16, 24, 32, 48):
            g = classify(gpu(name, cc, driver), windows)
            if g.backend != "cuda":
                break
            fit = estimate(e, pc(vram, g=g), coefficients=Coefficients()).fit
            assert order.index(fit) >= prev, (name, vram, fit)
            prev = order.index(fit)


def test_free_vram_short_is_a_note_not_a_level():
    c = Coefficients()
    g = arch_gpu("NVIDIA GeForce RTX 3070", "8.6", "566.03")
    g.vram_free_gb, g.vram_used_gb = 1.0, 7.0
    busy = estimate(image(), pc(8, g=g), coefficients=c)
    g2 = arch_gpu("NVIDIA GeForce RTX 3070", "8.6", "566.03")
    idle = estimate(image(), pc(8, g=g2), coefficients=c)
    assert busy.fit == idle.fit and busy.vram_free_short and not idle.vram_free_short
    assert any("ほかのアプリが 7.0 GB 使用中" in r and "閉じれば動く" in r for r in busy.reasons)


def test_ram_limit_uses_real_pagefile():
    fixed = pc(0, 16, swap_max_gb=4.0, swap_auto=False)
    assert ram_limit(fixed) == (20.0, 4.0)
    assert ram_limit(pc(0, 16, swap_max_gb=64.0, swap_auto=False)) == (24.0, 64.0)  # 倍率まで
    assert ram_limit(pc(0, 16, swap_auto=True)) == (24.0, None)
    assert ram_limit(pc(0, 16)) == (24.0, None)  # 分からなければこれまでどおり
    assert ram_limit(pc(0, 16, swap_max_gb=0.0, swap_auto=False)) == (16.0, 0.0)


def test_ram_check_with_fixed_pagefile():
    c = Coefficients()
    big = image("fp16", 17.0)  # 逃がすとメインメモリに全部品が載る
    sys_fixed = pc(12, 16, swap_max_gb=2.0, swap_auto=False)
    sys_auto = pc(12, 16, swap_auto=True)
    tight = estimate(big, sys_fixed, coefficients=c)
    loose = estimate(big, sys_auto, coefficients=c)
    assert tight.fit == "too_tight" and any("ページファイルは最大 2.0 GB" in r for r in tight.reasons)
    assert loose.fit == "marginal"


def test_fit_monotonic_in_ram_with_fixed_pagefile():
    order = ["too_tight", "marginal", "good", "perfect"]
    for e in (image("fp16", 6.0), image("fp16", 14.0)):
        prev = -1
        for ram in (4, 8, 12, 16, 24, 32, 64):
            fit = estimate(e, pc(8, ram, swap_max_gb=2.0, swap_auto=False), coefficients=Coefficients()).fit
            assert order.index(fit) >= prev
            prev = order.index(fit)


# --- 出力とプライバシー ---------------------------------------------------------------------------


def test_system_json_has_no_private_values(monkeypatch, capsys):
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))
    adapters, _ = parse_wmi_video(fx("wmi_this_pc.json"), reg("registry_this_pc.json"))
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda **_: (adapters, []))
    monkeypatch.setattr(hardware, "read_memory", lambda: MEM)
    assert cli.main(["system", "--json", "--all-gpus"]) == 0
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["system"]["other_gpus_checked"] is True
    banned = ["PCI\\", "VEN_", "SUBSYS", "00000000", "GPU-", "pci.bus_id", "uuid"]
    with_names = [platform.node(), getpass.getuser()]
    for word in banned + [w for w in with_names if w and len(w) >= 3]:
        assert word.lower() not in out.lower(), word


def test_system_text(monkeypatch, capsys):
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))
    mem = Memory(
        total=32 * GB,
        available=8 * GB,
        swap_total=35 * GB,
        swap_auto=True,
        commit_limit=67 * GB,
        commit_used=60 * GB,
    )
    monkeypatch.setattr(hardware, "read_memory", lambda: mem)
    assert cli.main(["system", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "compute capability 8.6 (ampere), driver 566.03, cuda: used for judging" in out
    assert "PyTorch CUDA builds that run here: cu129, cu128, cu126" in out
    assert "Pagefile: 35.0 GB now, system managed (grows); commit 60.0 / 67.0 GB in use" in out
    assert "3.6 GB used by other apps" in out and "Notes:" in out
    assert "Display adapters" not in out  # NVIDIA があれば既定では調べない
    _patch_nvidia(monkeypatch, fx("nvsmi_rtx3070_win.csv"))  # nvidia-smi の答えは 1 回分
    assert cli.main(["system"]) == 0  # 日本語(#76)
    out = capsys.readouterr().out
    assert "compute capability 8.6(ampere)、ドライバ 566.03、cuda: 判定に使う" in out
    assert "この PC で動く PyTorch の CUDA 版: cu129, cu128, cu126" in out
    assert "ページファイル: 今 35.0 GB、OS が管理(増える)。コミット 60.0 / 67.0 GB 使用中" in out
    assert "ほかのアプリが 3.6 GB 使用中" in out and "\n注意:\n" in out
    assert "画面の部品" not in out


def test_linux_nvidia_without_nvidia_smi_is_noted(monkeypatch):
    _patch_nvidia(monkeypatch, None)
    monkeypatch.setattr(hardware, "detect_other_gpus", lambda: (parse_lspci(fx("lspci_amd_nvidia.txt")), []))
    s = detect(memory=MEM, host=hardware.Host("Linux"))
    # RX 7800 XT は VRAM が読めず(sysfs なし)、Raphael は画面用の小さな内蔵 GPU、
    # Arc A770 は名前から 8 GB とみなす
    assert s.best_gpu is not None and s.best_gpu.name == "Intel Arc A770" and s.best_gpu.backend == "xpu"
    assert any("NVIDIA GeForce RTX 3070 があるが、nvidia-smi で確かめられない" in n for n in s.notes)


def test_crlf_and_lf_outputs_read_the_same():
    text = fx("nvsmi_multi.csv")
    lf = parse_nvidia_smi(text.replace("\r\n", "\n"), QUERY)
    crlf = parse_nvidia_smi(text.replace("\r\n", "\n").replace("\n", "\r\n"), QUERY)
    assert lf == crlf and len(lf) == 2


# --- ComfyUI の系統(#21 の Checker: supported_inference_dtypes・working_dtypes) -------------------

FAMILY_GPUS = {
    "maxwell": ("NVIDIA GeForce GTX 980", "5.2"),
    "pascal_win": ("NVIDIA GeForce GTX 1080", "6.1"),
    "volta": ("Tesla V100-PCIE-16GB", "7.0"),
    "turing": ("NVIDIA GeForce RTX 2080 Ti", "7.5"),
    "turing16": ("NVIDIA GeForce GTX 1660 SUPER", "7.5"),
    "ampere": ("NVIDIA GeForce RTX 3060", "8.6"),
    "ada": ("NVIDIA GeForce RTX 4090", "8.9"),
    "blackwell": ("NVIDIA GeForce RTX 5090", "12.0"),
}
# (本体の名前, VAE) → 期待: bf16 を持たない GPU で作業用メモリが 2 倍になるか、VAE が 2 倍になるか
FAMILY_CASES = [
    ("qwen_image", "qwen_image_vae.safetensors", True, False),  # 本体は bf16 だけ、VAE は Wan(fp16 可)
    ("ltx-2-19b-dev", "vae.safetensors", True, False),  # VAE は分からない
    ("hunyuan_video_t2v_720p", "hunyuan_video_vae_bf16.safetensors", True, False),
    ("z_image_turbo", "ae.safetensors", True, True),
    ("acestep_v1.5_turbo", "vae.safetensors", True, False),
    ("flux1-dev", "ae.safetensors", False, True),  # 本体は fp16 可、KL の VAE は bf16 か fp32
    ("flux1-fill-dev", "ae.safetensors", True, True),  # FluxInpaint は bf16 だけ
    ("wan2.1_t2v_1.3B", "wan_2.1_vae.safetensors", False, False),
    (
        "hunyuanvideo1.5_720p_t2v",
        "hunyuanvideo15_vae_fp16.safetensors",
        False,
        False,
    ),  # HunyuanVideo15 は fp16 可
    ("sd_xl_base_1.0", "vae.safetensors", False, False),
]


@pytest.mark.parametrize("arch", list(FAMILY_GPUS))
@pytest.mark.parametrize(("main", "vae", "act2", "vae2"), FAMILY_CASES)
def test_family_precision_per_arch(arch, main, vae, act2, vae2):
    c = Coefficients()
    name, cc = FAMILY_GPUS[arch]
    g = arch_gpu(name, cc, "581.57")
    e = image("bf16", 6.0, main=main, vae=vae)
    ampere = pc(48, g=arch_gpu("NVIDIA GeForce RTX 3090", "8.6", "581.57"))
    base = estimate(e, ampere, coefficients=c)
    got = estimate(e, pc(48, g=g), coefficients=c)
    fp32 = g.compute_dtype == "fp32"  # 半精度を使わない GPU はこれまでどおり作業用メモリと VAE を 2 倍
    work2 = fp32 or (g.bf16 is False and act2)
    vae2x = fp32 or (g.bf16 is False and vae2)
    s = E.settings_for(e, None)
    st0 = E.stages_for(e, s, E.gpu_precision(e, ampere, s))
    if st0 is None:  # 段ごとに当てられない(系統・VAE が分からない)ものは今までの種類ごとの式
        act = c.act_image_gb_per_mp * 1024 * 1024 / 1e6
        want = (act if work2 else 0) + (0.3 if vae2x else 0)
    else:
        # 段ごとの作業用メモリ(#100): 生成の段 S は fp32 の計算で 2 倍、仕上げの段 V は VAE が fp32 で 2 倍
        st = E.stages_for(e, s, E.gpu_precision(e, pc(48, g=g), s))
        assert pytest.approx(st0.S * (2 if work2 else 1)) == st.S, (arch, main)
        assert pytest.approx(st0.V * (2 if vae2x else 1)) == st.V, (arch, main)
        want = (0.3 if vae2x else 0) + st.peak - st0.peak
    assert got.vram_gb - base.vram_gb == pytest.approx(want, abs=0.02), (arch, main)
    if g.bf16 is False and g.compute_dtype != "fp32" and act2:
        assert any("fp16 で動かさない" in r for r in got.reasons)


def test_unknown_family_is_noted_only_where_it_matters():
    c = Coefficients()
    e = image("bf16", 6.0)  # unet_bf16・vae.safetensors は系統が分からない
    turing = estimate(e, pc(12, g=arch_gpu("NVIDIA GeForce RTX 2080 Ti", "7.5", "566.03")), coefficients=c)
    ampere = estimate(e, pc(12, g=arch_gpu("NVIDIA GeForce RTX 3060", "8.6", "566.03")), coefficients=c)
    assert any("系統(ComfyUI のどのモデルか)が分からない" in r for r in turing.reasons)
    assert not any("系統" in r for r in ampere.reasons)
    assert turing.vram_gb == ampere.vram_gb


@pytest.mark.parametrize(
    ("name", "family", "vae"),
    [
        ("qwen_image_fp8_e4m3fn.safetensors", "qwen_image", "wan"),  # VAE のファイルが無ければ系統の既定
        ("qwen_image_2.1_int8_convrot.safetensors", "qwen_image21", "qwen21"),  # #100
        ("ltx-2.3-22b-dev-fp8.safetensors", "ltxv", "ltxv"),
        ("Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors", "wan", "wan"),
        ("anima-base-v1.0.safetensors", "anima", "wan"),
        ("flux1-fill-dev.safetensors", "flux_fill", "kl"),
        ("flux1-canny-dev.safetensors", "flux", "kl"),
        ("flux-2-klein-4b-fp8.safetensors", "flux2", "flux2"),  # #100: Flux2 の VAE を分けた
        ("flux3-image-klein-int8-convrot.safetensors", "flux3", "flux2"),
        ("hunyuanvideo1.5_720p_t2v_fp16.safetensors", "hunyuan_video15", "hunyuan_video15"),
        ("ace_step_1.5_turbo_aio.safetensors", "ace15", "oobleck"),  # #100
        ("ace_step_v1_3.5b.safetensors", "ace", "ace"),
        ("capybara_v0.1.safetensors", None, None),
        ("moge_2_vitl_normal_fp16.safetensors", None, None),
    ],
)
def test_family_of_names(name, family, vae):
    from genmodelfit.families import family_of, vae_of

    e = ModelEntry(id="x", name="x", modality="image", files=[ModelFile(name=name, size_bytes=GB)])
    assert family_of(e) == family
    assert vae_of(e, family) == vae


def test_bundled_catalog_has_families():
    from genmodelfit.db import load
    from genmodelfit.families import FAMILIES, VAES, family_of, vae_of

    db = load(use_cache=False)
    known = [m for m in db.models if m.comfy_family]
    assert len(known) >= 270
    for m in db.models:  # 同梱の一覧は今の表で作り直してある(update と同じ結果)
        assert m.comfy_family == family_of(m), m.id
        assert m.comfy_vae == vae_of(m, m.comfy_family), m.id
        assert m.comfy_family is None or m.comfy_family in FAMILIES
        assert m.comfy_vae is None or m.comfy_vae in VAES
    unknown_media = [m.id for m in db.models if not m.comfy_family and m.modality in ("image", "video")]
    assert all(
        "capybara" in i or "utility" in i or "moge" in i or i.startswith(("hf:", "templates"))
        for i in unknown_media
    )


def test_old_db_without_family_fields_still_reads():
    old = {"id": "x", "name": "x", "modality": "image", "files": [{"name": "qwen_image_bf16.safetensors"}]}
    e = ModelEntry.from_dict(old)
    assert e.comfy_family is None and e.comfy_vae is None  # 見積もりは名前から決め直す
    from genmodelfit.families import family_of

    assert family_of(e) == "qwen_image"


def test_nvidia_smi_partial_failure_keeps_readable_rows(monkeypatch):
    out = (
        "NVIDIA GeForce RTX 3090, 24576, 24000, 576, 581.57, 8.6\n"
        "Unable to determine the device handle for GPU0000:02:00.0: Unknown Error\n"
    )
    monkeypatch.setattr(hardware, "_run", FakeRun((15, out)))
    gpus, notes = query_nvidia_smi("nvidia-smi")
    assert [g.name for g in gpus] == ["NVIDIA GeForce RTX 3090"]
    assert any("1 枚の GPU の情報を読めなかった" in n for n in notes)
    assert not any("0000:02:00.0" in n for n in notes)  # 差し場所は写さない


def test_multiple_gpu_models_get_a_device_note(monkeypatch):
    _patch_nvidia(monkeypatch, fx("nvsmi_multi.csv"))
    s = detect(memory=MEM)
    assert s.best_gpu.name == "NVIDIA GeForce RTX 4090"
    assert any("CUDA の 0 番" in n and "--cuda-device" in n for n in s.notes)


def test_unusable_gpu_has_no_precision():
    g = classify(gpu("NVIDIA GeForce GTX 780", "3.5", "474.44"))
    assert g.backend == "none" and (g.compute_dtype, g.bf16, g.fp8) == (None, None, None)
    assert any(n.startswith("Kepler 世代") for n in g.notes)
    future = classify(gpu("NVIDIA Future GPU", "13.0", "700.00"))  # #101: 断らず、注意だけ
    assert future.backend == "cuda" and any("表" in n and "新しい世代" in n for n in future.notes)
