# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""全部載せを選べるかを、タイルにしない VAE の作業用メモリで決める(#102。docs/accuracy.md「タイル」)。

段階の単調さ(VRAM を増やして段階が下がらない)は tests/test_levels.py の
test_levels_are_monotonic_in_memory が同梱の全モデルで確かめる(載せたままの量も VRAM によらない)。"""

import importlib

import pytest

import genmodelfit
from genmodelfit import i18n
from genmodelfit.db import load
from genmodelfit.models import Settings
from genmodelfit.recommend import mode_label, recommend, vram_need

E = importlib.import_module("genmodelfit.estimate")  # genmodelfit.estimate は関数の名前でもある


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


def nv(vram: float):
    return genmodelfit.system_from(
        [{"name": "NVIDIA GeForce RTX 4080", "vendor": "nvidia", "vram_gb": vram}],
        ram_gb=64,
        gpu_usable=True,
        windows=True,
    )


def _settings(entry, **kw) -> Settings:
    return E.settings_for(entry, Settings(**kw), sized=not E.no_size_input(entry))


def test_sdxl_1536_on_16gb_swaps_parts(db):
    entry = db.get("image_sdxl_simple")
    c = E.load_coefficients()
    big = E.estimate(entry, nv(16.0), Settings(width=1536, height=1536, steps=25), [])
    small = E.estimate(entry, nv(16.0), Settings(width=1024, height=1024, steps=25), [])
    sys16 = nv(16.0)
    assert mode_label(big, sys16) == "GPU~" and big.fit in ("good", "marginal")
    assert mode_label(small, sys16) == "GPU" and small.fit == "perfect"
    assert big.vram_resident_gb > big.vram_gb
    assert small.vram_resident_gb >= small.vram_gb
    # 使う量(vram_gb)は今までどおり V_eff の式
    s = _settings(entry, width=1536, height=1536, steps=25)
    n = E.formula_need(entry, s, c)
    st = n.stages
    parts = E.apply_precision(E.components(entry), entry, s)
    w = c.weight_factor
    assert n.full == pytest.approx(c.overhead_gb + parts.total * w + max(st.S, st.V_eff, st.T))
    assert n.resident == pytest.approx(c.overhead_gb + parts.total * w + max(st.S, st.V, st.T))
    assert big.vram_gb == pytest.approx(round(n.full, 2))
    # 理由: 載せたままの量を出し、仕上げの作業用メモリが入らないと書く
    with i18n.forced("ja"):
        ja = E.estimate(entry, nv(16.0), Settings(width=1536, height=1536, steps=25), [])
    assert any("仕上げ(VAE)に ComfyUI が見込む作業用メモリ" in r for r in ja.reasons)
    assert any(f"全部を載せたままでは {ja.vram_resident_gb:.1f} GB" in r for r in ja.reasons)
    with i18n.forced("en"):
        en = E.estimate(entry, nv(16.0), Settings(width=1536, height=1536, steps=25), [])
    assert any("ComfyUI unloads the model before decoding" in r for r in en.reasons)
    # 24 GB なら全部載せのまま
    assert (
        mode_label(E.estimate(entry, nv(24.0), Settings(width=1536, height=1536, steps=25), []), nv(24.0))
        == "GPU"
    )


# #102 の段 2(--disable-dynamic-vram)の 21 回:
# (名前, モデル, 設定, ComfyUI が loaded partially だったか, 使える GiB)
# 使える量 = 15.92 − 開始前の使用量(RTX 5080 16GB、ComfyUI 0.43.0)
_RUNS = [
    ("moge", "utility_moge_depth_estimation", {}, False, 14.24),
    ("hy3d", "3d_hunyuan3d_image_to_model", {"steps": 20}, False, 14.03),
    ("sdxl_512", "image_sdxl_simple", {"width": 512, "height": 512, "steps": 25}, False, 13.96),
    ("sdxl_768", "image_sdxl_simple", {"width": 768, "height": 768, "steps": 25}, False, 13.96),
    ("sdxl_1024", "image_sdxl_simple", {"width": 1024, "height": 1024, "steps": 25}, False, 13.96),
    ("sdxl_1536", "image_sdxl_simple", {"width": 1536, "height": 1536, "steps": 25}, True, 14.03),
    ("ace_10", "audio_ace_step_1_5_checkpoint", {"seconds": 10, "steps": 8}, False, 13.93),
    ("ace_30", "audio_ace_step_1_5_checkpoint", {"seconds": 30, "steps": 8}, False, 13.99),
    ("ace_60", "audio_ace_step_1_5_checkpoint", {"seconds": 60, "steps": 8}, True, 13.93),
    ("ace_120", "audio_ace_step_1_5_checkpoint", {"seconds": 120, "steps": 8}, True, 13.93),
    (
        "wan13_480_17",
        "text_to_video_wan",
        {"width": 832, "height": 480, "frames": 17, "steps": 30},
        True,
        13.97,
    ),
    (
        "wan13_480_33",
        "text_to_video_wan",
        {"width": 832, "height": 480, "frames": 33, "steps": 30},
        True,
        14.04,
    ),
    (
        "wan13_480_49",
        "text_to_video_wan",
        {"width": 832, "height": 480, "frames": 49, "steps": 30},
        True,
        13.98,
    ),
    (
        "wan13_480_81",
        "text_to_video_wan",
        {"width": 832, "height": 480, "frames": 81, "steps": 30},
        True,
        13.88,
    ),
    (
        "wan13_512_49",
        "text_to_video_wan",
        {"width": 512, "height": 512, "frames": 49, "steps": 30},
        False,
        13.98,
    ),
    (
        "wan5b_480_49",
        "video_wan2_2_5B_ti2v",
        {"width": 832, "height": 480, "frames": 49, "steps": 20},
        True,
        13.91,
    ),
    (
        "wan5b_480_81",
        "video_wan2_2_5B_ti2v",
        {"width": 832, "height": 480, "frames": 81, "steps": 20},
        True,
        13.91,
    ),
    (
        "wan5b_704_81",
        "video_wan2_2_5B_ti2v",
        {"width": 1280, "height": 704, "frames": 81, "steps": 20},
        True,
        13.91,
    ),
    ("zimg8_768", "image_z_image_turbo_int8", {"width": 768, "height": 768, "steps": 8}, True, 13.98),
    ("zimg8_1024", "image_z_image_turbo_int8", {"width": 1024, "height": 1024, "steps": 8}, True, 13.90),
    ("zimg8_1536", "image_z_image_turbo_int8", {"width": 1536, "height": 1536, "steps": 8}, True, 13.91),
]


def _needs(db):
    c = E.load_coefficients()
    out = []
    for name, mid, kw, partial, usable in _RUNS:
        entry = db.get(mid)
        assert entry is not None, mid
        n = E.formula_need(entry, _settings(entry, **kw), c)
        out.append((name, n, partial, usable))
    return out


def test_resident_matches_comfyui_partial_loads(db):
    """載せたままの量が使える量を超える回 ↔ ComfyUI が loaded partially にした回が 21 回中 19 回以上一致する
    (分かっている外れ: wan13_512_49。載せたままの 14.04 GB が使える量 13.98 GB を
    超えたのに partial でなかった。差は 0.06 GB)。今までの全部載せ(V_eff)では
    13 回以下しか一致しない(この直しの理由)。"""
    rows = _needs(db)
    assert len(rows) == 21
    resident = [
        (name, ((n.full if n.resident is None else n.resident) > usable) == partial)
        for name, n, partial, usable in rows
    ]
    # 今までの全部載せ = タイルの V_eff(#148 でタイルにしない V を使う VAE は、そのタイルの見積もり n.tiled)
    old = [
        (name, ((n.full if n.tiled is None else n.tiled.full) > usable) == partial)
        for name, n, partial, usable in rows
    ]
    hits = sum(ok for _, ok in resident)
    assert hits >= 19, [name for name, ok in resident if not ok]
    assert sum(ok for _, ok in old) <= 13


def test_resident_does_not_depend_on_vram_and_is_at_least_full(db):
    c = E.load_coefficients()
    for m in db.models:
        if E.components(m).unknown:
            continue
        n = E.formula_need(m, _settings(m), c)
        if n.resident is not None:
            assert n.resident >= n.full - 1e-9, m.id


def test_gpu_choice_resident_gate():
    # 全部載せは 60% 以下でも、載せたままの量が 98% を超えれば順に載せ替え(good まで)
    assert E.gpu_choice(8.0, 5.0, 4.0, 16.0) == ("perfect", "full")
    assert E.gpu_choice(8.0, 5.0, 4.0, 16.0, resident=16.5) == ("good", "seq")
    assert E.gpu_choice(8.0, 5.0, 4.0, 16.0, resident=15.0) == ("perfect", "full")
    # VRAM を増やせば全部載せに戻る(段階は下がらない)
    assert E.gpu_choice(8.0, 5.0, 4.0, 24.0, resident=16.5) == ("perfect", "full")


def test_measured_level_is_not_gated():
    """実測で段階を決めた行(fit_from = measurement)は載せたままの量で全部載せを外さない(実測が先)。"""
    from genmodelfit.models import Estimate

    e = Estimate(
        vram_gb=8.0,
        vram_offload_gb=4.0,
        ram_gb=8.0,
        disk_gb=None,
        basis="measured",
        fit="perfect",
        run_mode="gpu",
        vram_sequential_gb=5.0,
        fit_from="measurement",
        measured_level_vram_gb=8.0,
        vram_resident_gb=20.0,
    )
    assert mode_label(e, nv(16.0)) == "GPU"
    e.fit_from = "estimate"
    assert mode_label(e, nv(16.0)) == "GPU~"


def test_recommend_mode_agrees_with_estimate(db):
    """表の Mode は見積もりの選んだ動かし方(載せたままの量で全部載せを外した行は GPU~)と一致する。"""
    sys16 = nv(16.0)
    entry = db.get("image_sdxl_simple")
    s = Settings(width=1536, height=1536, steps=25)
    rows = [
        r
        for r in recommend(db, sys16, settings=s, content="all", min_fit="too_tight", limit=None)
        if r.model.id == entry.id
    ]
    assert rows
    e = rows[0].estimate
    assert e.vram_resident_gb is not None and E.fit_band(e.vram_resident_gb, sys16.vram_gb) == "too_tight"
    assert mode_label(e, sys16) == "GPU~"
    assert vram_need(e, sys16) == e.vram_sequential_gb
    assert e.to_dict()["vram_resident_gb"] == e.vram_resident_gb
