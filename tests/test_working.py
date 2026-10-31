# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""段ごとの作業用メモリ(#100。working.py、docs/accuracy.md「式」)。"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from genmodelfit import families, i18n
from genmodelfit.db import load
from genmodelfit.models import Gpu, Measurement, Settings, SystemInfo
from genmodelfit.working import LATENTS, VAE_WORK, WORKS, stage_memory

E = importlib.import_module("genmodelfit.estimate")
GIB = 1024**3


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


def _st(db, mid, **kw):
    m = db.get(mid)
    s = E.settings_for(m, Settings(**kw) if kw else None, sized=not E.no_size_input(m))
    return stage_memory(m, s)


def nv(vram, ram=32.0, **gpu):
    g = Gpu(name="NVIDIA GeForce RTX 3060", vendor="nvidia", vram_total_gb=vram, backend="cuda", **gpu)
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


def amd(vram, gfx="gfx1101", ram=32.0):
    g = Gpu(
        name="AMD Radeon RX 7800 XT",
        vendor="amd",
        vram_total_gb=vram,
        backend="rocm",
        compute_dtype="fp16",
        bf16=True,
        fp8=False,
        gfx_target=gfx,
    )
    return SystemInfo(os="Linux", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


def apple(vram, ram=64.0):
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
    return SystemInfo(os="Darwin", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


def cpu(ram=64.0):
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[])


# --- 系統ごとの値(既定の設定。GiB) -------------------------------------------------------------
# (行, VAE, S, V, V_tile, T)。ComfyUI の見込みの式(working.py の出典)をそのまま計算した値

PINNED = {
    "image_sdxl_simple": ("sdxl", "kl", 0.2560, 4.2539, 1.0635, 0.0),
    "flux1_krea_dev": ("flux", "kl", 0.9920, 4.2539, 1.0635, 0.0),
    "image_z_image_turbo_int8": ("zimage", "kl", 0.8960, 4.2539, 1.0635, 0.0),
    "text_to_video_wan": ("wan_1.3b", "wan", 1.7692, 5.2071, 0.2686, 0.0),
    "video_wan2_2_5B_ti2v": ("wan_5b", "wan22", 0.8846, 5.9509, 0.9766, 0.0),
    "video_wan2_2_14B_t2v": ("wan_14b", "wan", 5.8974, 5.2071, 0.2686, 0.0),
    "video_wan2_2_14B_i2v": ("wan_14b", "wan", 5.8974, 5.2071, 0.7324, 0.0),
    "video_ltx2_t2v": ("ltxav", "ltxv", 0.8414, 4.9095, 0.4200, 1.8809),
    "ltxv_text_to_video": ("ltxv", "ltxv", 0.4608, 4.9095, 0.1465, 0.0),
    "audio_ace_step_1_5_checkpoint": ("ace15", "oobleck", 0.0688, 2.8610, 0.9766, 0.0320),
    "audio_stable_audio_example": ("stable_audio", "oobleck", 0.0084, 0.8202, 0.8202, 0.0),
    "hunyuan_video_text_to_video": ("hunyuan_video", "hunyuan_video", 4.6069, 16.6626, 1.7090, 0.0),
    "image_flux2_klein_text_to_image": ("flux2_4b", "flux2", 1.1703, 4.2539, 4.2539, 0.0),
}


@pytest.mark.parametrize("mid", sorted(PINNED))
def test_pinned_stage_values(db, mid):
    work, vae, S, V, Vt, T = PINNED[mid]
    st = _st(db, mid)
    assert (st.work, st.vae) == (work, vae)
    assert pytest.approx(S, abs=1e-4) == st.S
    assert pytest.approx(V, abs=1e-4) == st.V
    assert st.V_tile == pytest.approx(Vt, abs=1e-4)
    assert pytest.approx(T, abs=1e-4) == st.T


def test_formula_by_hand():
    """式の形を手で確かめる(出典の式そのまま)。"""
    # SDXL 1024²: 潜在 128×128、S = 128·128 × 2 B × 0.01 × 0.8 MiB、V = 2178 × 128 × 128 × 64 × 2 B
    assert WORKS["sdxl"].factor == 0.8
    s = 128 * 128 * 2 * 0.01 * 0.8 / 1024
    v = 2178 * 128 * 128 * 64 * 2 / GIB
    assert PINNED["image_sdxl_simple"][2] == pytest.approx(s, abs=1e-4)
    assert PINNED["image_sdxl_simple"][3] == pytest.approx(v, abs=1e-4)
    # Wan 1.3B 832×480×81: 潜在 21×60×104、dim 1536 / 2222。V = 7000 × 60 × 104 × 64 × 2 B
    assert PINNED["text_to_video_wan"][2] == pytest.approx(
        21 * 60 * 104 * 0.02 * 1536 / 2222 / 1024, abs=1e-4
    )
    assert PINNED["text_to_video_wan"][3] == pytest.approx(7000 * 60 * 104 * 64 * 2 / GIB, abs=1e-4)
    # LTX-2(LTXAV)は詰めた形: 面積に 128 チャンネルと音声の潜在が入る。文章の段は Gemma の 642 × 3 MiB
    area = 128 * 11 * 15 * 26 + 8 * 16 * 81
    assert PINNED["video_ltx2_t2v"][2] == pytest.approx(area * 0.02 * 0.077 / 1024, abs=1e-4)
    assert PINNED["video_ltx2_t2v"][5] == pytest.approx(642 * 3 / 1024, abs=1e-4)


# --- 形の性質 ----------------------------------------------------------------------------------


def test_wan21_vae_is_constant_in_frames(db):
    a, b = _st(db, "text_to_video_wan", frames=33), _st(db, "text_to_video_wan", frames=121)
    assert pytest.approx(b.V) == a.V and a.V_tile == pytest.approx(b.V_tile)
    assert b.S > a.S  # 生成の段はコマ数に比例


def test_ltx_vae_is_linear_in_latent_frames(db):
    a, b = _st(db, "ltxv_text_to_video", frames=41), _st(db, "ltxv_text_to_video", frames=81)
    assert pytest.approx(11 / 6) == b.V / a.V  # 潜在のコマ (F - 1) // 8 + 1


def test_audio_vae_is_linear_in_seconds(db):
    a, b = (
        _st(db, "audio_ace_step_1_5_checkpoint", seconds=20),
        _st(db, "audio_ace_step_1_5_checkpoint", seconds=60),
    )
    assert pytest.approx(3.0) == b.V / a.V
    assert b.V_tile == pytest.approx(a.V_tile)  # 256 コマのタイル


def test_denoise_is_linear_in_latent_area(db):
    a, b = (
        _st(db, "image_sdxl_simple", width=512, height=512),
        _st(db, "image_sdxl_simple", width=1024, height=1024),
    )
    assert pytest.approx(4.0) == b.S / a.S
    two = stage_memory(
        db.get("image_sdxl_simple"), E.settings_for(db.get("image_sdxl_simple"), Settings(batch=2))
    )
    assert pytest.approx(2 * b.S) == two.S and pytest.approx(b.V) == two.V  # 枚数は S にだけ掛ける


# --- タイル ------------------------------------------------------------------------------------


def test_tiles_are_vram_independent_definitions(db):
    # 2D: 潜在の面積 4096(64×64)。1024² の KL は 2178 × 64 × 64 × 64 × 2 B
    assert PINNED["image_sdxl_simple"][4] == pytest.approx(2178 * 64**3 * 2 / GIB, abs=1e-4)
    # 512² ならタイルは画像の潜在の大きさまで(V_tile = V)
    small = _st(db, "image_sdxl_simple", width=512, height=512)
    assert small.V_tile == pytest.approx(small.V)
    # 16 倍圧縮の VAE(Flux2): 1024² は潜在 64×64 で 1 枚のタイル。2048² でもタイルは同じ
    k1, k2 = (
        _st(db, "image_flux2_klein_text_to_image"),
        _st(db, "image_flux2_klein_text_to_image", width=2048, height=2048),
    )
    assert (
        k1.V_tile == pytest.approx(k1.V)
        and k2.V_tile == pytest.approx(k1.V)
        and pytest.approx(4 * k1.V) == k2.V
    )
    # 3D: 時間 2 コマ・空間 256 / 8 = 32 角(Wan 2.1 は 4 コマ以下で 2200)
    assert PINNED["text_to_video_wan"][4] == pytest.approx(2200 * 32 * 32 * 64 * 2 / GIB, abs=1e-4)
    # 符号化(Wan の画像から動画は動画全体を符号化): 6000 × 480 × 832 × 2 B、タイルは 256 画素の角
    i2v = _st(db, "video_wan2_2_14B_i2v")
    assert pytest.approx(max(6000 * 480 * 832 * 2, 7000 * 60 * 104 * 64 * 2) / GIB, abs=1e-4) == i2v.V
    assert i2v.V_tile == pytest.approx(6000 * 256 * 256 * 2 / GIB, abs=1e-4)
    # 1D: 256 コマ(ACE-Step 1.5 は 25 コマ/秒、Oobleck の 1000 × L × 2048 × 2 B)
    assert PINNED["audio_ace_step_1_5_checkpoint"][4] == pytest.approx(1000 * 256 * 2048 * 2 / GIB, abs=1e-4)


def test_tile_note_only_when_vae_does_not_fit(db):
    hv = db.get("hunyuan_video_text_to_video")
    small = E.estimate(hv, nv(12), None, db.measurements)
    assert any("タイルに分けて" in r for r in small.reasons)
    big = E.estimate(db.get("image_sdxl_simple"), nv(24), None, db.measurements)
    assert not any("タイルに分けて" in r for r in big.reasons)


def test_flux2_vae_note(db):
    e = E.estimate(db.get("image_flux2_klein_text_to_image"), nv(16), None, db.measurements)
    assert any("ComfyUI 0.40 の見込み" in r for r in e.reasons)


# --- 単調さ(VRAM を増やして段階が下がらない) ----------------------------------------------------

_L = ("perfect", "good", "marginal", "too_tight", "unknown")


@pytest.mark.parametrize("kind", ["nvidia", "amd", "apple"])
def test_levels_are_monotonic_in_vram(db, kind):
    bad = []
    for m in db.models:
        prev = _L.index(E.estimate(m, cpu(), None, db.measurements).fit)  # GPU なし(CPU)から始める
        for vram in range(4, 33, 2):
            system = {"nvidia": nv, "amd": amd, "apple": apple}[kind](float(vram))
            k = _L.index(E.estimate(m, system, None, db.measurements).fit)
            if k > prev:
                bad.append((m.id, vram, _L[prev], _L[k]))
                break
            prev = k
    assert not bad, bad[:10]


# --- 会社の違い --------------------------------------------------------------------------------


def test_no_split_attention_penalty_on_apple_and_cpu(db):
    m = db.get("image_sdxl_simple")
    s = E.settings_for(m, None)
    base = E.stages_for(m, s)
    for system in (apple(24.0), cpu()):
        prec = E.gpu_precision(m, system, s)
        st = E.stages_for(m, s, prec)
        assert pytest.approx(base.S) == st.S  # SDPA の式のまま(分割式の 0.15 は使わない)
    e = E.estimate(m, apple(24.0), None, db.measurements)
    assert any("分割式の注意機構" in r for r in e.reasons)
    assert not any("分割式の注意機構" in r for r in E.estimate(m, nv(24), None, db.measurements).reasons)


def test_amd_sdpa_targets():
    assert E.sdpa_attention(amd(16).best_gpu)  # gfx1101 は SDPA
    assert not E.sdpa_attention(amd(16, gfx="gfx1030").best_gpu)  # RDNA 2 は分割式
    assert not E.sdpa_attention(apple(16).best_gpu)


def test_amd_kl_vae_ratio(db):
    m = db.get("image_sdxl_simple")
    s = E.settings_for(m, None)
    base = stage_memory(m, s)
    on_amd = stage_memory(m, s, amd=True)
    assert pytest.approx(2.73 * base.V) == on_amd.V and on_amd.V_tile == pytest.approx(2.73 * base.V_tile)
    assert pytest.approx(base.S) == on_amd.S
    wan = db.get("text_to_video_wan")
    sw = E.settings_for(wan, None)
    assert (
        pytest.approx(stage_memory(wan, sw).V) == stage_memory(wan, sw, amd=True).V
    )  # Wan の VAE は KL でない
    e = E.estimate(m, amd(16), None, db.measurements)
    assert any("2.73" in r for r in e.reasons)


def test_bf16_less_gpu_doubles_kl_vae_only(db):
    m = db.get("image_sdxl_simple")
    s = E.settings_for(m, None)
    assert (
        pytest.approx(2 * stage_memory(m, s).V) == stage_memory(m, s, no_bf16=True).V
    )  # KL は fp16 を持たない
    wan = db.get("text_to_video_wan")
    sw = E.settings_for(wan, None)
    assert (
        pytest.approx(stage_memory(wan, sw).V) == stage_memory(wan, sw, no_bf16=True).V
    )  # Wan は fp16 で動く
    turing = nv(8, compute_dtype="fp16", bf16=False, fp8=False, arch="turing")
    prec = E.gpu_precision(m, turing, s)
    assert prec.no_bf16 and pytest.approx(2 * stage_memory(m, s).V) == E.stages_for(m, s, prec).V


# --- 当てられないモデル --------------------------------------------------------------------------

# #100 より前の値(RTX 3060 8 GB 相当、既定の設定。全部載せ GiB と段階)
FALLBACK_BEFORE = {
    "manual:tsukuyomi-chan-piper-plus": (1.52, "perfect"),
    "3d_hunyuan3d_image_to_model": (6.7, "good"),
    "utility_birefnet_remove_background": (2.13, "perfect"),
    "image_flux3_klein_t2i": (18.22, "good"),  # 逃がして good(#108 で逃がすのは good まで)
}


@pytest.mark.parametrize("mid", sorted(FALLBACK_BEFORE))
def test_fallback_models_unchanged(db, mid):
    m = db.get(mid)
    assert stage_memory(m, E.settings_for(m, None, sized=not E.no_size_input(m))) is None
    e = E.estimate(m, nv(8), None, db.measurements)
    assert (e.vram_gb, e.fit) == FALLBACK_BEFORE[mid]


def test_fallback_uses_the_whole_old_formula(db):
    for m in db.models:
        s = E.settings_for(m, None, sized=not E.no_size_input(m))
        if stage_memory(m, s) is not None:
            continue
        parts = E.apply_precision(E.components(m), m, s)
        if parts.unknown:
            continue
        old = E.need(parts, E.activation_gb(m, s, E.load_coefficients()), E.load_coefficients())
        assert E.formula_need(m, s, E.load_coefficients()).full == pytest.approx(old.full), m.id


def test_fallback_count_is_documented(db):
    """当てられないモデルの件数を docs/accuracy(英日)に書く(毎週変わるので、書いた数と ±15 以内)。"""
    n = sum(
        1 for m in db.models if stage_memory(m, E.settings_for(m, None, sized=not E.no_size_input(m))) is None
    )
    root = Path(__file__).parent.parent / "docs"
    for name in ("accuracy.md", "accuracy.ja.md"):
        text = (root / name).read_text(encoding="utf-8")
        found = re.search(r"<!-- fallback-count: (\d+) -->", text)
        assert found, name
        assert abs(int(found.group(1)) - n) <= 15, (name, n)


# --- 作者の RTX 5080 の張り付いていない 4 件(値を固定。誤差の試験にはしない) -------------------

RTX5080 = {
    "image_z_image_turbo_int8": 13.87,
    "image_sdxl_simple": 9.00,
    "text_to_video_wan": 11.34,
    "audio_ace_step_1_5_checkpoint": 12.73,  # #148: タイルにしない Oobleck の V(20 秒で 1.9 GB)
}


@pytest.mark.parametrize("mid", sorted(RTX5080))
def test_rtx5080_rows_pinned(db, mid):
    m = next(x for x in db.measurements if x.model_id == mid and x.gpu_name.endswith("RTX 5080"))
    e = db.get(mid)
    s = E.settings_for(e, m.settings, sized=not E.no_size_input(e))
    n = E.formula_need(e, s, E.load_coefficients(), E.components(e))
    assert n.stages is not None and not E.is_censored(m)
    assert round(n.full, 2) == RTX5080[mid]


# --- 実測の扱い ---------------------------------------------------------------------------------


def test_working_only_measurements_are_not_used(db):
    e = db.get("image_sdxl_simple")
    m = Measurement(
        model_id=e.id,
        gpu_name="NVIDIA GeForce RTX 3060",
        gpu_vram_gb=12.0,
        peak_vram_gb=2.3,
        settings=Settings(width=1024, height=1024),
        offload="dynamic",
        vram_metric="torch_reserved",
    )
    assert E.working_only(m)
    assert E.pick_measurement([m], e, E.settings_for(e, None)) is None
    a = E.estimate(e, nv(12), None, [m])
    b = E.estimate(e, nv(12), None, [])
    assert (a.vram_gb, a.fit, a.basis) == (b.vram_gb, b.fit, b.basis)
    full = Measurement(**{**vars(m), "offload": "none"})
    assert not E.working_only(full)


def test_measurement_shift_is_added_to_all_three(db):
    e = db.get("image_z_image_turbo_int8")
    c = E.load_coefficients()
    s = E.settings_for(e, Settings(width=768, height=768))
    parts = E.apply_precision(E.components(e), e, s)
    st = E.stages_for(e, s)
    base, shifted = E.need_staged(parts, st, c), E.need_staged(parts, st, c, 0.7)
    assert shifted.full - base.full == pytest.approx(0.7)
    assert shifted.sequential - base.sequential == pytest.approx(0.7)
    assert shifted.offload - base.offload == pytest.approx(0.7)
    # 下限(重み + 固定分)は S = V = T = 0 の値のまま(#95)
    zero = E.need(parts, 0.0, c)
    assert zero.full == pytest.approx(c.overhead_gb + parts.total) and base.full > zero.full


# --- 表の約束 ------------------------------------------------------------------------------------


def test_tables_cite_the_commit():
    assert families.COMFYUI_COMMIT.startswith("426f5ee")
    for w in WORKS.values():
        assert "@426f5ee" in w.source and w.latent in LATENTS and w.vae in VAE_WORK, w.key
        assert w.vae in w.vaes and all(v in families.VAES for v in w.vaes), w.key
    for v in VAE_WORK.values():
        assert "@426f5ee" in v.source, v.key
    for lat in LATENTS.values():
        assert "@426f5ee" in lat.source, lat.key


def test_vae_kinds_from_file_names(db):
    assert db.get("video_wan2_2_5B_ti2v").comfy_vae == "wan22"
    assert db.get("text_to_video_wan").comfy_vae == "wan"
    assert db.get("image_flux2_klein_text_to_image").comfy_vae == "flux2"
    assert db.get("audio_ace_step_1_5_checkpoint").comfy_vae == "oobleck"
    assert all(m.comfy_vae == "qwen21" for m in db.models if m.comfy_family == "qwen_image21")


def test_size_bins(db):
    assert _st(db, "video_wan2_2_14B_t2v").work == "wan_14b"
    assert _st(db, "video_wan_vace_14B_v2v").work == "wan_vace_14b"
    assert _st(db, "image_flux2").work == "flux2_32b"
    assert _st(db, "image_flux2_klein_9b_kv_image_edit").work == "flux2_9b"
    assert _st(db, "ltxv_text_to_video").work == "ltxv"
    assert _st(db, "video_ltx2_3_t2v").work == "ltxav"
    # 大きさの分からない Flux3・Stable Audio 3 は当てない(今までの式)
    assert _st(db, "image_flux3_klein_t2i") is None
    assert _st(db, "audio_stable_audio_3_medium") is None


# --- 下限と実測のずらし(#100 のチェッカーの M1・S1・S3) -------------------------------------------


def _floor_need(entry, s):
    c = E.load_coefficients()
    return E.need(E.apply_precision(E.components(entry), entry, s), 0.0, c)


@pytest.mark.parametrize(
    ("mid", "kw"),
    [
        ("image_sdxl_simple", {"width": 256, "height": 256}),
        ("image_sdxl_simple", {}),
        ("audio_ace_step_1_5_checkpoint", {"seconds": 5}),
        ("audio_ace_step_1_5_checkpoint", {}),
        ("image_z_image_turbo_int8", {"width": 256, "height": 256}),
        ("text_to_video_wan", {"width": 256, "height": 256, "frames": 17}),
    ],
)
def test_shift_never_goes_below_the_floor(db, mid, kw):
    """実測からの負のずらしは段ごとに 0 で切る: 実測と違う設定でも、3 つの量は重み + 固定分(作業用メモリ 0)の
    値を下回らない(#95 の下限)。"""
    e = db.get(mid)
    s = E.settings_for(e, Settings(**kw) if kw else None)
    fl = _floor_need(e, s)
    for vram in (6.0, 8.0, 16.0, 24.0):
        est = E.estimate(e, nv(vram), Settings(**kw) if kw else None, db.measurements)
        assert est.vram_gb >= round(fl.full, 2) - 0.005, (mid, vram)
        assert est.vram_sequential_gb >= round(fl.sequential, 2) - 0.005, (mid, vram)
        assert est.vram_offload_gb >= round(fl.offload, 2) - 0.005, (mid, vram)


def test_all_staged_models_respect_the_floor(db):
    for m in db.models:
        s = E.settings_for(m, None, sized=not E.no_size_input(m))
        if E.stages_for(m, s) is None or E.components(m).unknown:
            continue
        fl = _floor_need(m, s)
        est = E.estimate(m, nv(12.0), None, db.measurements)
        assert est.vram_gb >= round(fl.full, 2) - 0.005, m.id
        assert est.vram_sequential_gb >= round(fl.sequential, 2) - 0.005, m.id
        assert est.vram_offload_gb >= round(fl.offload, 2) - 0.005, m.id


def test_positive_shift_from_a_roomy_gpu_goes_to_full_load_only(db):
    """RTX 5080(16 GB)は Wan 2.1 1.3B の式の全部載せ(11.34 GB)以上なので、+1.96 GB は全部載せにだけ足す
    (余裕のある GPU で VAE をタイルに分けずに仕上げた山。順に載せ替え・逃がすでは起きない)。"""
    e = db.get("text_to_video_wan")
    s = Settings(width=512, height=512, frames=49)
    prep = E._prepare(e, db.measurements, E.load_coefficients(), E.settings_for(e, s), None)
    assert prep.delta == pytest.approx(13.3 - 11.34, abs=0.01) and prep.full_only
    a = E.estimate(e, nv(16.0), s, db.measurements)
    b = E.estimate(e, nv(16.0), s, [])
    assert a.vram_gb == 13.3 and a.vram_sequential_gb == b.vram_sequential_gb
    assert a.vram_offload_gb == b.vram_offload_gb
    # 6 GB は逃がして good。8 GB は順に載せ替えて 97% でぎりぎりなので、逃がして good(#108)
    six, eight = E.estimate(e, nv(6.0), s, db.measurements), E.estimate(e, nv(8.0), s, db.measurements)
    assert (six.fit, six.run_mode) == ("good", "offload")
    assert (eight.fit, eight.run_mode) == ("good", "offload") and eight.vram_sequential_gb <= 8.0 * 0.98
    # 小さな GPU の実測(式の全部載せより VRAM が小さい)からの正のずらしは 3 つに足す
    small = Measurement(**{**vars(prep.measurement), "gpu_vram_gb": 8.0, "peak_vram_gb": 7.9})
    assert not E._full_only(small, 11.34, 0.5)


def test_measurement_slightly_below_the_floor_stays_measured(db):
    """作者の ACE-Step 1.5 の実測 10.80 GB は下限 10.82 GB を 0.02 GB 下回る(数え方の誤差)。値は下限、
    根拠は measured のまま、理由に出す。"""
    e = db.get("audio_ace_step_1_5_checkpoint")
    s = Settings(seconds=20)
    with i18n.forced("ja"):
        est = E.estimate(e, nv(16.0), s, db.measurements)
    assert est.basis == "measured" and est.vram_gb == pytest.approx(10.82, abs=0.01)
    assert any("わずかに下回る" in r for r in est.reasons)
    assert E.MEASURED_FLOOR_TOLERANCE == 0.1  # 下限の大きさによらない(#100 の再チェック)


def test_working_only_community_rows_never_set_the_level(db):
    """今の NVIDIA の bench の提出(動的 VRAM の torch_reserved)は、出どころが 2 つ以上そろっても段階を決めない
    (照合だけ。#100)。"""
    e = db.get("image_sdxl_simple")
    system = nv(12.0)
    s = E.settings_for(e, None)
    row = Measurement(
        model_id=e.id,
        gpu_name="NVIDIA GeForce RTX 3060",
        gpu_vram_gb=12.0,
        peak_vram_gb=2.3,
        settings=Settings(width=1024, height=1024),
        offload="dynamic",
        vram_metric="torch_reserved",
        origin="community",
        n_submissions=3,
        n_used=3,
        n_sources=3,
        source_ids=["a" * 12, "b" * 12, "c" * 12],
        n_oom=0,
        consistent=True,
    )
    lvl, _ = E.measured_level([row], e, s, system, E.gpu_precision(e, system, s), 0.0)
    assert lvl is None
    est = E.estimate(e, system, None, [row])
    assert est.fit_from == "estimate" and est.basis_detail == "estimated"


# --- #100 の再チェック: 下限の許しは作者・ほかの利用者で同じ。当てられないモデルも正のずらしは全部載せ ---


@pytest.mark.parametrize("origin", [None, "community"])
def test_floor_tolerance_is_the_same_for_author_and_community(db, origin):
    e = db.get("image_sdxl_simple")
    s = E.settings_for(e, None)
    fl = round(_floor_need(e, s).full, 2)
    extra = (
        {
            "origin": "community",
            "n_submissions": 2,
            "n_used": 2,
            "n_sources": 2,
            "n_oom": 0,
            "consistent": True,
        }
        if origin
        else {}
    )

    def row(peak):
        return Measurement(
            model_id=e.id,
            gpu_name="NVIDIA GeForce RTX 3060",
            gpu_vram_gb=12.0,
            peak_vram_gb=peak,
            settings=Settings(width=1024, height=1024),
            offload="none",
            **extra,
        )

    near = E.estimate(e, nv(12.0), None, [row(round(fl - 0.05, 2))])
    assert near.basis == "measured" and near.vram_gb == pytest.approx(fl, abs=0.01)
    assert any("下限" in r for r in near.reasons) or any("floor" in r for r in near.reasons)
    far = E.estimate(e, nv(12.0), None, [row(round(fl - 0.5, 2))])
    assert far.basis == "estimated" and far.vram_gb == pytest.approx(fl, abs=0.01)


def test_fallback_positive_shift_goes_to_full_load_only(db):
    """当てられないモデル(3D)も、張り付いていない実測からの正のずらしは全部載せにだけ足す。"""
    e = db.get("3d_hunyuan3d-v2.1")
    assert E.stages_for(e, E.settings_for(e, None)) is None
    a = E.estimate(e, nv(16.0), None, db.measurements)
    b = E.estimate(e, nv(16.0), None, [])
    assert a.vram_gb == 9.4 and a.vram_gb > b.vram_gb
    assert (a.vram_sequential_gb, a.vram_offload_gb) == (b.vram_sequential_gb, b.vram_offload_gb)
    # GPU の上限に張り付いた実測(これ以上としてだけ使う)からのずらしは 3 つに足す
    m = next(x for x in db.measurements if x.model_id == e.id)
    assert not E._full_only(Measurement(**{**vars(m), "peak_vram_gb": 15.5}), 9.12, 6.0)


def test_calibrate_uses_bundled_measurements_only(tmp_path, monkeypatch, capsys):
    """この PC の bench の実測(キャッシュ)は合わせ直しに入れない(どの PC でも calibration.json が同じ)。"""
    import json

    from genmodelfit import calibrate as cal
    from genmodelfit.db import bundled_text

    cache = tmp_path / "cache"
    (cache / "measurements").mkdir(parents=True)
    extra = {
        "model_id": "utility_moge_depth_estimation",
        "gpu_name": "NVIDIA GeForce RTX 3070",
        "gpu_vram_gb": 8.0,
        "peak_vram_gb": 0.78,
        "settings": {"precision": "fp16"},
        "offload": "dynamic",
        "vram_metric": "torch_reserved",
    }
    (cache / "measurements" / "x.json").write_text(json.dumps({"measurements": [extra]}), encoding="utf-8")
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(cache))
    committed = json.loads(bundled_text("calibration.json"))
    again = cal.calibrate(date=committed["date"])
    assert again["check"] == committed["check"] and again["coefficients"] == committed["coefficients"]
    assert all(x["model_id"] != "utility_moge_depth_estimation" for x in again["check"])
    assert cal.main(["--dry-run", "--date", committed["date"]]) == 0
    assert "1 件は使わない" in capsys.readouterr().out
