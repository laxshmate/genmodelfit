# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""#148: ACE-Step 1.5 の仕上げ(Oobleck の VAE)の作業用メモリが曲の長さで増える(docs/accuracy.md「タイル」)。

ComfyUI はまずタイルにせずに復号し(sd.py:1278-1311@426f5ee)、CUDA の OOM のときだけタイルに分ける
(sd.py:1312-1326)。Oobleck の V = 1000 × L × 2048 × 2 バイト(sd.py:735)、L = round(秒 × 48000 / 1920)
(nodes_ace.py:105)。実測(RTX 3070・RTX 5080)はこの V の 0.8〜1.0 倍(30 秒以上)。
"""

from __future__ import annotations

import importlib

import pytest

from genmodelfit.db import load
from genmodelfit.models import Gpu, Settings, SystemInfo
from genmodelfit.working import VAE_WORK, stage_memory

E = importlib.import_module("genmodelfit.estimate")
GIB = 1024**3
ACE15 = "audio_ace_step_1_5_split"  # 実測の無い ACE-Step 1.5(式だけ)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


def nv(vram: float, ram: float = 32.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


def _st(db, mid, sec):
    m = db.get(mid)
    return stage_memory(m, E.settings_for(m, Settings(seconds=sec), sized=not E.no_size_input(m)))


def test_only_the_oobleck_vae_counts_the_untiled_decode():
    # 実測で確かめた VAE だけ(Wan などの VAE は別の Issue。docs/accuracy.md「タイル」)
    assert [k for k, v in VAE_WORK.items() if v.untiled] == ["oobleck"]


@pytest.mark.parametrize("sec", [10, 30, 60, 120, 240])
def test_oobleck_working_memory_follows_comfyui_formula(db, sec):
    st = _st(db, ACE15, sec)
    L = round(sec * 48000 / 1920)  # nodes_ace.py:105
    v, vt = st.V, st.V_tile
    assert v == pytest.approx(1000 * L * 2048 * 2 / GIB)  # sd.py:735、bf16 の 2 バイト
    assert vt == pytest.approx(1000 * min(L, 256) * 2048 * 2 / GIB)  # decode_tiled_1d(sd.py:1196)
    assert st.untiled and st.V_eff == st.V  # 使う量はタイルにしない V
    t = st.tiled()
    if L > 256:
        assert t is not None and t.V_eff == st.V_tile and t.fallback
    else:
        assert t is None


def test_working_memory_grows_with_length(db):
    vs = [_st(db, ACE15, s).V_eff for s in (10, 30, 60, 120, 240)]
    assert vs == sorted(vs)
    assert vs[3] / vs[1] == pytest.approx(4.0, rel=1e-3)  # 長さに比例(30 → 120 秒で 4 倍)


def test_measured_points_are_within_the_formula():
    """issue #148 と PR #141 の作業用メモリの実測(torch_reserved・動的 VRAM)は V の 0.8〜1.0 倍(30 秒以上)。
    式を実測に合わせない(倍率は掛けない)ので、式は実測より多め(安全側)。"""
    measured = {  # 秒: GB(RTX 3070 8 GB、ComfyUI 0.19.3)
        30: 2.34,
        60: 5.00,
        120: 9.31,
    }
    # RTX 5080 16 GB(PR #141)。30 秒の 0.47 GB と、10 秒の点はどれも使わない
    # (#131: 山が 0.02〜0.3 秒しか続かず、0.2 秒ごとに読む bench が取り逃がす)
    measured_5080 = {120: 9.5}
    for sec, gb in [*measured.items(), *measured_5080.items()]:
        v = 1000 * round(sec * 25) * 2048 * 2 / GIB
        assert 0.8 <= gb / v <= 1.0, (sec, gb, v)


# 実測の無い ACE-Step 1.5 の段階(NVIDIA、メインメモリ 32 GB)。#148 の前はどの長さも 8〜16 GB で good、
# 24 GB で 240 秒以外 perfect だった
EXPECTED = {
    10: ("good", "good", "good", "perfect"),
    30: ("good", "good", "good", "perfect"),
    60: ("marginal", "good", "good", "good"),
    120: ("marginal", "marginal", "good", "good"),
    240: ("marginal", "marginal", "marginal", "marginal"),
}


@pytest.mark.parametrize("sec", sorted(EXPECTED))
def test_levels_by_length(db, sec):
    m = db.get(ACE15)
    got = tuple(E.estimate(m, nv(v), Settings(seconds=sec)).fit for v in (8, 12, 16, 24))
    assert got == EXPECTED[sec]


@pytest.mark.parametrize("mid", [ACE15, "audio_ace_step_1_5_checkpoint", "audio_ace_step1_5_xl_turbo"])
@pytest.mark.parametrize("sec", [10, 30, 60, 120, 240, 600])
def test_more_vram_never_lowers_the_level(db, mid, sec):
    m = db.get(mid)
    order = ("too_tight", "marginal", "good", "perfect")
    levels = [
        order.index(E.estimate(m, nv(v), Settings(seconds=sec), db.measurements).fit)
        for v in (4, 6, 8, 10, 12, 16, 20, 24, 32, 48, 96)
    ]
    assert levels == sorted(levels), (mid, sec, levels)


def test_tiled_fallback_is_capped_at_marginal_with_a_note(db):
    m = db.get(ACE15)
    e = E.estimate(m, nv(8), Settings(seconds=120))
    assert e.fit == "marginal" and e.run_mode == "gpu"
    # 表の量はタイルの見積もり(段階・動かし方と同じ量)
    assert e.vram_sequential_gb < 8
    joined = " ".join(e.reasons)
    assert "11.4 GB" in joined and "marginal" in joined
    # 入るなら逃げ道は使わない(注意も出さない)
    e2 = E.estimate(m, nv(16), Settings(seconds=120))
    assert e2.fit == "good" and "marginal" not in " ".join(e2.reasons)


def test_cpu_has_no_tiled_fallback(db):
    """CPU の確保の失敗は CUDA の OOM でないので、ComfyUI はタイルに分けずに失敗する
    (model_management.py:380・389-395)。CPU の判定はタイルにしない V のまま。"""
    m = db.get(ACE15)
    n = E.formula_need(m, E.settings_for(m, Settings(seconds=120)), E.load_coefficients())
    assert n.tiled is not None
    _fit, mode, _r, _ram, used = E._judge_tiled(n, nv(0), E.load_coefficients(), m)
    assert used is n and mode in ("cpu", "none")


def test_other_vaes_are_unchanged(db):
    """Oobleck 以外の VAE は今までどおりタイルの値(min(V, V_tile))で、タイルの見積もりを持たない。"""
    c = E.load_coefficients()
    seen = 0
    for m in db.models:
        if E.components(m).unknown:
            continue
        n = E.formula_need(m, E.settings_for(m, None, sized=not E.no_size_input(m)), c)
        st = n.stages
        if st is None or st.vae == "oobleck":
            continue
        seen += 1
        assert not st.untiled and st.V_eff == min(st.V, st.V_tile) and n.tiled is None, m.id
    assert seen > 100


def test_ace_step_1_is_unchanged(db):
    # ACE-Step 1(music_dcae の VAE)は実測が無いので今までどおり
    st = _st(db, "audio_ace_step_1_t2a_song", 120)
    assert st.vae == "ace" and not st.untiled and st.V_eff == st.V_tile < st.V
