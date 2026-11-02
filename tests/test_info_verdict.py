# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""info のテキストの結論の 1 行・VRAM の行の印・実測の行(JSON の形は変えない)。"""

from __future__ import annotations

from dataclasses import replace

from genmodelfit import cli, i18n
from genmodelfit.hardware import detect
from genmodelfit.models import Estimate


def _info(capsys, *args: str) -> list[str]:
    assert cli.main(["info", *args, "--no-cache"]) == 0
    return capsys.readouterr().out.splitlines()


def test_too_tight_names_the_offload_value_and_percent(capsys):
    # 0.15 から画像は動的 VRAM の式で逃がす量が小さい(8 GB では動く)ので、入らない 2 GB で見る
    out = _info(capsys, "flux_schnell_full_text_to_image", "--vram", "2", "--ram", "32", "--lang", "ja")
    text = "\n".join(out)
    # 名前の行のすぐ下に結論、次に実測
    assert out[1].startswith("  この PC:    Too tight — 逃がしても VRAM に入らない見込み")
    assert "逃がして 3.0 GB / 2.0 GB、148%" in text and "推定" in out[1] + out[2]
    assert any(line.startswith("  実測:       なし(推定だけ)") for line in out[1:4])
    # VRAM の行: 判定に使った値(逃がす)にだけ印
    assert text.count("←判定") == 1 and "(GPU+RAM)←判定" in text.replace("\n", "").replace(" ", "")
    # runtime の見出しは「動かす道具」(資料の「動かし方」= GPU / GPU~ / GPU+RAM と分ける)
    assert "  動かす道具: comfyui" in out and not any(line.startswith("  動かし方:") for line in out)


def test_offload_row_says_slow_and_mode(capsys):
    # 0.15 から画像は動的 VRAM の式で逃がす量が小さい(8 GB では Good)ので、ぎりぎりの 3.5 GB で見る
    out = _info(capsys, "flux_schnell", "--vram", "3.5", "--ram", "32", "--lang", "en")
    head = " ".join(line.strip() for line in out[1:4])
    assert head.startswith(
        "this PC:  Marginal (GPU+RAM) — runs by offloading part of the weights to main memory"
    )
    assert "with offload / 3.5 GB, 87%" in head and "estimate; slower" in head
    assert "measured: none (estimate only)" in head
    assert "  runs with: comfyui" in out


def test_verdict_from_same_gpu_measurement_marks_measured_value():
    """同じ GPU の実測で段階を決めた行は、3 つの値でなく実測から決めた量に印を付け、
    「実測で決めた」と書く。"""
    s = detect(vram_gb=8, ram_gb=32)
    e = Estimate(
        vram_gb=9.0,
        vram_offload_gb=4.0,
        ram_gb=12.0,
        disk_gb=6.0,
        basis="measured",
        fit="good",
        run_mode="gpu",
        vram_sequential_gb=7.5,
        basis_detail="measured",
        fit_from="measurement",
        measured_peak_vram_gb=6.0,
        measured_level_vram_gb=6.5,
        sample_count=2,
    )
    with i18n.forced("ja"):
        line = cli._info_verdict(e, s)
        vram = cli._vram_line(e, s)
    assert line.startswith("Good(GPU) — 全部 GPU に載せて動く(実測から 6.5 GB / 8.0 GB、81%")
    assert "同じ GPU の別々の出どころ 2 つの実測で決めた" in line
    assert vram.count("←判定") == 1 and "実測から 6.5 GB(GPU) ←判定" in vram
    # 見積もりだけの行は「推定」
    est = replace(e, basis="estimated", basis_detail="estimated", fit_from="estimate", sample_count=None)
    with i18n.forced("ja"):
        assert "。推定)" in cli._info_verdict(est, s)
