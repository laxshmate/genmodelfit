# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""段階の決め方の見直し(#108。docs/scoring.md「段階の上限」「確認済みの GPU の種類」)。"""

from __future__ import annotations

import importlib
from collections import Counter

import pytest

from genmodelfit import i18n
from genmodelfit.db import load
from genmodelfit.hardware import Host, classify_adapter
from genmodelfit.models import Adapter, Gpu, Measurement, ModelEntry, ModelFile, Settings, SystemInfo
from genmodelfit.recommend import evaluate, mode_label
from genmodelfit.table import legend, pc_line

E = importlib.import_module("genmodelfit.estimate")
GIB = 1024**3
_L = ("perfect", "good", "marginal", "too_tight", "unknown")


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


def _sys(g: Gpu | None, ram: float = 32.0) -> SystemInfo:
    return SystemInfo(
        os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g] if g else []
    )


def nv(vram: float, ram: float = 32.0) -> SystemInfo:
    return _sys(Gpu(name="NVIDIA GeForce RTX 3060", vendor="nvidia", vram_total_gb=vram, backend="cuda"), ram)


def amd(vram: float, ram: float = 32.0) -> SystemInfo:
    g = Gpu(
        name="AMD Radeon RX 7800 XT",
        vendor="amd",
        vram_total_gb=vram,
        backend="rocm",
        compute_dtype="fp16",
        bf16=True,
        fp8=False,
        gfx_target="gfx1101",
    )
    return _sys(g, ram)


def apple(vram: float, ram: float = 64.0) -> SystemInfo:
    g = Gpu(
        name="Apple M2 Pro",
        vendor="apple",
        vram_total_gb=vram,
        backend="mps",
        compute_dtype="fp16",
        bf16=True,
        fp8=False,
        memory="unified",
    )
    return _sys(g, ram)


def gb10(mem: float) -> SystemInfo:
    g = Gpu(
        name="NVIDIA GB10",
        vendor="nvidia",
        vram_total_gb=mem,
        backend="cuda",
        memory="shared",
        carveout_gb=0.0,
        compute_capability="12.1",
        arch="blackwell",
        compute_dtype="bf16",
        bf16=True,
        fp8=True,
    )
    return _sys(g, mem)


def model(gb: float, modality: str = "image", tasks: tuple[str, ...] = ("t2i",)) -> ModelEntry:
    return ModelEntry(
        id=f"m{gb}",
        name="m",
        modality=modality,
        tasks=list(tasks),
        files=[ModelFile(name="dit.safetensors", size_bytes=int(gb * GIB), directory="diffusion_models")],
    )


# --- 1. 逃がす(GPU+RAM)は good まで。逃がすのは段階が上がるときだけ ----------------------------------


def test_offload_is_capped_at_good_and_chosen_only_when_it_raises_the_level():
    choice = E.gpu_choice
    assert choice(30, 30, 4, 10) == ("good", "offload")  # 40%: 逃がす行は perfect にしない
    assert choice(12, 11, 5, 10) == ("good", "offload")  # 全部も順に載せ替えも 98% 超
    assert choice(12, 9, 5, 10) == ("good", "offload")  # 順に載せ替え 90%(marginal)→ 逃がせば good
    assert choice(12, 8, 5, 10) == ("good", "seq")  # good で並べば逃がさない
    assert choice(5, 5, 2, 10) == ("perfect", "full")
    assert choice(12, 9, 9, 10) == ("marginal", "seq")  # 逃がしても marginal なら逃がさない
    assert choice(12, 9, 5, 10, allow_offload=False) == ("marginal", "seq")


def test_gpu_perfect_or_good_rows_never_switch_to_offload(db):
    """逃がす行に替わるのは、全部載せ・順に載せ替えで 85% を超える(marginal 以下の)行だけ。"""
    for vram in (6.0, 8.0, 12.0, 16.0, 24.0):
        system = nv(vram, 64.0)
        for m in db.models:
            e = E.estimate(m, system, None, db.measurements)
            if e.run_mode != "offload" or e.unknown_size:
                continue
            level, _how = E.gpu_choice(e.vram_gb, e.vram_sequential_gb, 0, vram, allow_offload=False)
            assert level in ("marginal", "too_tight"), (m.id, vram, level)


def test_switched_offload_row_says_it_is_tight_on_the_gpu(db):
    # 86%: 既定で切ってある高速化の LoRA(switch_off)を数えない(#81。数えると 91%)
    e = E.estimate(db.get("video_wan2_2_14B_i2v"), nv(24.0, 64.0), None, db.measurements)
    assert (e.fit, e.run_mode) == ("good", "offload")
    assert (
        e.reasons[0].startswith("GPU だけでは 86%")
        and "でぎりぎりなので、一部をメインメモリへ逃がす" in e.reasons[0]
    )
    with i18n.forced("en"):
        e = E.estimate(db.get("video_wan2_2_14B_i2v"), nv(24.0, 64.0), None, db.measurements)
    assert e.reasons[0].startswith("On the GPU alone it is tight at 86%")


def test_offload_level_also_uses_main_memory():
    m = model(12.0)
    roomy = E.estimate(m, nv(8.0, 64.0), Settings(width=1024, height=1024))
    tight = E.estimate(m, nv(8.0, 17.0), Settings(width=1024, height=1024))  # 15 / 17 GB
    assert (roomy.fit, roomy.run_mode) == ("good", "offload")
    assert tight.run_mode == "offload" and tight.fit == "marginal"  # メインメモリが 85% を超える


def test_non_nvidia_offload_mentions_dynamic_vram(db):
    e = E.estimate(model(12.0), amd(8.0, 64.0), Settings(width=1024, height=1024))
    assert e.run_mode == "offload" and any("Dynamic VRAM" in r for r in e.reasons)
    n = E.estimate(model(12.0), nv(8.0, 64.0), Settings(width=1024, height=1024))
    assert n.run_mode == "offload" and not any("Dynamic VRAM" in r for r in n.reasons)


def test_no_limits_without_a_gpu(db):
    """限界は逃がさずに GPU で good になる設定なので、GPU の無い PC では出さない(#108)。"""
    m = db.get("hf:hexgrad/Kokoro-82M")
    assert E.estimate(m, _sys(None, 64.0), None, db.measurements, with_limits=True).limits is None


def test_limits_text_says_gpu_without_offloading():
    from genmodelfit.cli import format_limits

    none = {"level": "good", "max_side_px": None, "max_frames": None, "at_cap": []}
    with i18n.forced("ja"):
        assert format_limits(none) == "逃がさずに GPU で Good になる設定は無い(解像度や長さを下げても)"
    with i18n.forced("en"):
        assert "Good on the GPU without offloading" in format_limits(none)
        assert format_limits({"level": "good", "max_seconds": 30, "at_cap": []}).startswith(
            "Good on this PC's GPU without offloading up to 30 s"
        )


def test_limits_count_only_rows_that_run_on_the_gpu(db):
    """限界は逃がさずに GPU で good 以上の行だけで数える。逃がして good の行があっても限界は変わらない。"""
    e = db.get("text_to_video_wan")
    base = Settings(width=512, height=512, frames=49)
    for vram in (8.0, 10.0, 16.0):
        lim = E.limits_for(e, nv(vram, 64.0), base, db.measurements)
        if lim is None or lim.get("max_side_px") is None:
            continue
        side = lim["max_side_px"]
        at = E.estimate(e, nv(vram, 64.0), Settings(width=side, height=side, frames=49), db.measurements)
        assert at.fit in ("perfect", "good") and at.run_mode == "gpu"
        if "max_side_px" not in lim["at_cap"]:
            over = Settings(width=side + 32, height=side + 32, frames=49)
            o = E.estimate(e, nv(vram, 64.0), over, db.measurements)
            assert not (o.fit in ("perfect", "good") and o.run_mode == "gpu")


# --- 2. CPU だけ: 生成は marginal まで、CPU で実時間に動く読み上げ(表)・1 回の計算の道具は good まで ---


@pytest.mark.parametrize(
    ("mid", "level"),
    [
        ("hf:hexgrad/Kokoro-82M", "marginal"),  # 作者が CPU について書いていない
        ("manual:tsukuyomi-chan-piper-plus", "good"),  # 読み上げ(Piper)
        ("manual:supertonic-3", "good"),  # 読み上げ(Supertonic。ONNX Runtime の CPU。#127)
        ("hf:myshell-ai/MeloTTS-English", "good"),  # 「Fast enough for CPU real-time inference」
        ("hf:FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "marginal"),  # 言語モデル + フローマッチングの読み上げ
        ("hf:microsoft/VibeVoice-Realtime-0.5B", "marginal"),  # 拡散の頭の読み上げ
        ("utility_birefnet_remove_background", "good"),  # 背景除去(matting)
        ("audio_melbandroformer_audio_separation", "good"),  # 音の分離
        ("image_sdxl_simple", "marginal"),  # 画像の生成(拡散)
        ("audio_stable_audio_example", "marginal"),  # 効果音の生成(拡散)
    ],
)
def test_cpu_only_caps(db, mid, level):
    e = E.estimate(db.get(mid), _sys(None, 64.0), None, db.measurements)
    assert (e.fit, e.run_mode) == (level, "cpu")
    assert any(f"段階は {level} まで" in r for r in e.reasons)
    if E.cpu_realtime_tts(db.get(mid)) is not None:
        assert any("根拠: " in r for r in e.reasons)  # 作者の資料の文を出す


def test_cpu_top_table():
    assert E.cpu_top(model(1.0, "tts", ("tts",))) == "marginal"  # 表に無い読み上げは生成と同じ
    kokoro = model(1.0, "tts", ("tts",))
    kokoro.id = "hf:hexgrad/Kokoro-82M"
    assert E.cpu_top(kokoro) == "marginal" and E.cpu_realtime_tts(kokoro) is None
    melo = model(1.0, "tts", ("tts",))
    melo.id = "hf:myshell-ai/MeloTTS-English"
    assert E.cpu_top(melo) == "good" and E.cpu_realtime_tts(melo).name == "MeloTTS"
    assert {row.name for row in E.CPU_REALTIME_TTS} == {"MeloTTS", "Piper", "VieNeu-TTS", "Supertonic"}
    piper = model(1.0, "tts", ("tts",))
    piper.runtime = "piper"
    assert E.cpu_top(piper) == "good"
    supertonic = model(1.0, "tts", ("tts",))
    supertonic.runtime = "supertonic"  # #127
    assert E.cpu_top(supertonic) == "good" and E.cpu_realtime_tts(supertonic).name == "Supertonic"
    assert all(row.evidence for row in E.CPU_REALTIME_TTS)  # どの行にも作者の資料の文
    for task in ("estimate", "matting", "segment", "separate", "interp"):
        assert E.cpu_top(model(1.0, "image", (task,))) == "good", task
    assert E.cpu_top(model(1.0, "image", ("upscale",))) == "marginal"  # 一覧の高解像度化は拡散の系統
    for mod, task in (("image", "t2i"), ("video", "t2v"), ("music", "t2a"), ("sfx", "t2a"), ("3d", "i23d")):
        assert E.cpu_top(model(1.0, mod, (task,))) == "marginal", mod
    assert E.cpu_top(model(1.0, "image", ("estimate", "t2i"))) == "marginal"  # 生成を含めば生成


def test_cpu_level_uses_the_ram_band(db):
    m = db.get("hf:myshell-ai/MeloTTS-English")
    big = E.estimate(m, _sys(None, 64.0), None, db.measurements)
    assert big.fit == "good"
    need = big.ram_gb
    tight = E.estimate(m, _sys(None, need / 0.9), None, db.measurements)  # 90%
    assert tight.fit == "marginal"


# --- 3. GPU の種類の上限をやめ、確認済みの種類に印 -----------------------------------------------------


def test_platform_verified_with_the_bundled_data():
    v = E.platform_verification(nv(8.0))
    assert v["verified"] is True and v["kind"] == "nvidia/dedicated"
    assert v["models"] == ["NVIDIA GeForce RTX 3070", "NVIDIA GeForce RTX 5080"]  # 2 機種
    for system in (amd(16.0), apple(24.0), gb10(119.7)):
        v = E.platform_verification(system)
        assert v["verified"] is False and v["models"] == [], system.best_gpu.name
    assert E.platform_verification(_sys(None))["kind"] is None
    over = nv(8.0)
    over.overrides.append("vram")
    assert E.platform_verification(over)["verified"] is False  # GPU が仮定(--vram)


def _m(name: str, **kw) -> Measurement:
    return Measurement(**{"model_id": "x", "gpu_name": name, "gpu_vram_gb": 16, "peak_vram_gb": 8, **kw})


def test_one_model_with_many_sources_is_not_verified():
    """1 機種の実測は、何件・何人・どの出どころでも 1 つ(#108)。"""
    author = [_m("AMD Radeon RX 7800 XT"), _m("AMD Radeon RX 7800 XT", peak_vram_gb=9)]
    community = _m("AMD Radeon RX 7800 XT", origin="community", n_sources=5, source_ids=list("abcde"))
    v = E.platform_verification(amd(16.0), [*author, community])
    assert v["verified"] is False and v["models"] == ["AMD Radeon RX 7800 XT"]
    # 同じ型番 ID なら名前の書き方が違っても同じ機種
    pci = [_m("AMD Radeon RX 7800 XT", gpu_pci_id="1002:747e"), _m("Navi 32", gpu_pci_id="1002:747e")]
    assert E.platform_verification(amd(16.0), pci)["verified"] is False
    # 同じ名前でも VRAM の違う版(比 0.95 未満)は別の機種
    assert E.platform_verification(amd(16.0), [author[0], _m("AMD Radeon RX 7800 XT", gpu_vram_gb=8)])[
        "verified"
    ]


@pytest.mark.parametrize(
    ("first", "second"),
    [({}, {}), ({"origin": "community", "n_sources": 1}, {}), ({}, {"origin": "community", "n_sources": 1})],
)
def test_two_models_are_verified_whatever_the_origin(first, second):
    """2 機種で動いた記録があれば確認済み。作者・ほかの利用者を分けない。"""
    rows = [_m("AMD Radeon RX 7800 XT", **first), _m("AMD Radeon RX 7600", **second)]
    v = E.platform_verification(amd(16.0), rows)
    assert v["verified"] is True and v["models"] == ["AMD Radeon RX 7600", "AMD Radeon RX 7800 XT"]


def test_kind_rows_and_memory_form():
    ok = _m("AMD Radeon RX 7800 XT")
    for bad in (
        _m("AMD Radeon RX 7600", outcome="oom"),
        _m("AMD Radeon RX 7600", standard=False),
        _m("AMD Radeon RX 7600", offload="lowvram"),  # 重みを CPU 側に置いた
        _m("AMD Radeon RX 7600", origin="community", runs_evidence=False),
        _m("AMD Radeon(TM) Graphics"),  # 汎用の名前は機種が決まらない
        _m("AMD Radeon 890M"),  # 内蔵 GPU は amd/shared(別の種類)
    ):
        assert E.platform_verification(amd(16.0), [ok, bad])["verified"] is False, bad
    assert E.measurement_kind(_m("AMD Radeon 890M")) == "amd/shared"
    # 汎用の名前は内蔵 GPU の名前: 型番 ID が表にあればその名前で、無ければ共有
    assert E.measurement_kind(_m("AMD Radeon(TM) Graphics")) == "amd/shared"
    assert E.measurement_kind(_m("AMD Radeon(TM) Graphics", gpu_pci_id="1002:15bf")) == "amd/shared"
    assert E.measurement_kind(_m("Intel(R) Arc(TM) Graphics")) == "intel/shared"
    # 型番 ID が専用の VRAM の GPU(744c = Navi 31)なら、名前が汎用でも専用
    assert E.measurement_kind(_m("AMD Radeon(TM) Graphics", gpu_pci_id="1002:744c")) == "amd/dedicated"
    assert E.measurement_kind(_m("NVIDIA GB10")) == "nvidia/shared"
    assert E.measurement_kind(_m("mps", gpu_backend="mps")) == "apple/shared"


def test_mark_only_when_verified():
    with i18n.forced("en"):
        assert "✓ verified GPU kind" in pc_line(nv(8.0))
        for system in (amd(16.0), apple(24.0), gb10(119.7), _sys(None)):
            assert "✓" not in pc_line(system)
    assert "✓ 確認済みの種類" in pc_line(nv(8.0))


def test_gate_and_auto_do_not_depend_on_the_gpu_kind(db):
    """確認済みの種類は gate(自動のおすすめ)に入れない。AMD でも NVIDIA でも auto の集まりは同じ。"""
    a = {f.model.id for f in evaluate(db, amd(16.0)) if f.auto}
    n = {f.model.id for f in evaluate(db, nv(16.0)) if f.auto}
    assert a == n and a


def test_no_vendor_cap_and_fair_distribution(db):
    """AMD 16 GB と NVIDIA 16 GB の段階の分布が近い(会社で段階を抑えない)。"""
    ca = Counter(E.estimate(m, amd(16.0), None, db.measurements).fit for m in db.models)
    cn = Counter(E.estimate(m, nv(16.0), None, db.measurements).fit for m in db.models)
    assert ca["perfect"] > 0
    for level in ("perfect", "good", "marginal", "too_tight"):
        assert abs(ca[level] - cn[level]) <= 0.05 * len(db.models), (level, ca, cn)
    g = E.estimate(model(6.0), gb10(119.7), Settings(width=1024, height=1024))
    assert g.fit == "perfect"  # DGX Spark も上限なし(Perfect は速さの保証ではない)


def test_measurements_only_raise_on_non_nvidia(db):
    """#95 の own の分岐(実測で perfect → good に下げる)は外した。実測は上げるだけ。"""
    entry = db.get("image_sdxl_simple")
    est = E.estimate(entry, amd(24.0, 64.0), None, [])
    assert est.fit == "perfect"
    sett = Settings(**{k: v for k, v in est.settings.items() if k != "batch"})
    pinned = Measurement(
        model_id=entry.id,
        gpu_name="AMD Radeon RX 7800 XT",
        gpu_vram_gb=24.0,
        peak_vram_gb=8.5,
        settings=sett,
        offload="lowvram",  # 張り付いた実測(段階は good まで)
        gpu_backend="rocm",
    )
    rows = [pinned, Measurement(**{**vars(pinned), "origin": "community", "n_sources": 1})]
    assert E.estimate(entry, amd(24.0, 64.0), None, rows).fit == "perfect"


@pytest.mark.parametrize("kind", ["nvidia", "amd", "apple", "gb10"])
def test_levels_are_monotonic_in_memory(db, kind):
    make = {"nvidia": nv, "amd": amd, "apple": apple, "gb10": gb10}[kind]
    bad = []
    for m in db.models:
        prev = _L.index(E.estimate(m, _sys(None, 4.0 if kind == "gb10" else 32.0), None, db.measurements).fit)
        for vram in range(4, 33, 4):
            k = _L.index(E.estimate(m, make(float(vram)), None, db.measurements).fit)
            if k > prev:
                bad.append((m.id, vram, _L[prev], _L[k]))
                break
            prev = k
    assert not bad, bad[:10]


def test_cpu_only_levels_are_monotonic_in_ram(db):
    bad = []
    for m in db.models:
        prev = len(_L)
        for ram in (4, 8, 16, 32, 64):
            k = _L.index(E.estimate(m, _sys(None, float(ram)), None, db.measurements).fit)
            if k > prev:
                bad.append((m.id, ram))
                break
            prev = k
    assert not bad, bad[:10]


# --- 4. 断る理由を 3 つに分ける ---------------------------------------------------------------------------

WIN11 = Host("Windows", 26100, cpu="AMD Ryzen 9 7950X")


def test_refusal_categories():
    with i18n.forced("ja"):
        used = Adapter(name="AMD Radeon RX 7800 XT", vendor="amd", vram_gb=16.0)
        g = classify_adapter(used, WIN11, 32.0)
        assert g is not None and any("同じ基準で決めた" in n for n in g.notes)  # 使う(未確認。印なし)
        small = Adapter(name="AMD Radeon 820M Graphics", vendor="amd", vram_gb=0.5)
        assert classify_adapter(small, WIN11, 32.0) is None
        assert "CPU より速い見込みが無い" in small.reason and "CPU として判定" in small.reason
        both = Adapter(name="Krackan2 [Radeon 820M / 840M]", vendor="amd", vram_gb=0.5, shared_gb=16.0)
        linux330 = Host("Linux", None, cpu="AMD Ryzen AI 5 330 w/ Radeon 820M")
        assert classify_adapter(both, linux330, 32.0) is None and "2 CU" in both.reason  # CPU の名前で 820M
        both = Adapter(name="Krackan2 [Radeon 820M / 840M]", vendor="amd", vram_gb=0.5, shared_gb=16.0)
        assert classify_adapter(both, Host("Linux", None, cpu="AMD Ryzen AI 5 340"), 32.0) is not None
        new = Adapter(
            name="AMD Radeon Graphics", vendor="amd", vram_gb=0.5, shared_gb=16.0, gfx_target="gfx1170"
        )
        assert classify_adapter(new, Host("Linux", None, cpu="x"), 32.0) is None
        assert "PyTorch が動く根拠が無い" in new.reason


# --- 5・7. 凡例 ----------------------------------------------------------------------------------------


def test_legend_lines(db):
    fits = evaluate(db, nv(8.0, 64.0))
    with i18n.forced("en"):
        lines = legend(fits, {}, system=nv(8.0, 64.0))
    text = "\n".join(lines)
    assert "Level caps: GPU~ and GPU+RAM at most Good; CPU at most Marginal" in text
    assert "not a speed guarantee" in text
    assert "GPU+RAM: the level is often set by RAM" in text  # 逃がす行があるとき
    assert "✓ verified GPU kind" in text
    assert all(len(x) <= 100 for x in lines)
    measured = [f for f in fits if f.estimate.fit_from == "measurement"]
    if measured:
        assert "can be Good even above 98% Mem%" in text
    lines = legend(fits, {}, system=nv(8.0, 64.0))
    assert any(x.startswith("Perfect は余裕があるという意味で、速さの保証ではない") for x in lines)
    assert any("GPU+RAM の段階はメモリの量で決まることが多い" in x for x in lines)


def test_measured_rows_mark_m_only(db):
    """表の VRAM の印は m だけ(作者・ほかの利用者を分けない。#108)。"""
    from genmodelfit.models import BASIS_DETAILS

    assert BASIS_DETAILS == ("measured", "calibrated", "estimated")
    fits = [f for f in evaluate(db, nv(16.0)) if f.estimate.basis == "measured"]
    assert fits and all(mode_label(f.estimate, nv(16.0)) for f in fits)
