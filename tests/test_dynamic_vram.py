# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""動的 VRAM の提出の表示と照合(#217)。

tests/fixtures/dynamic_vram の 1 件は、本物の RTX 3070 8 GB・ComfyUI 0.19.3(動的 VRAM)で測った提出
(SDXL 1024px 25 steps fp16)。torch の確保量 2.28、GPU 全体の増分 6.46、見積もり 6.25。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genmodelfit import cli, contrib
from genmodelfit.db import load as load_db

REAL = Path(__file__).parent / "fixtures" / "dynamic_vram" / "rtx3070_sdxl_dynamic.json"
GIB = 1024**3


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return load_db(use_cache=False)


def real() -> dict:
    return copy.deepcopy(json.loads(REAL.read_text(encoding="utf-8")))


def codes(d: dict, db) -> set[str]:
    r = contrib.validate_submission(d, db, check_filename=False)
    assert not r.errors, r.errors
    return {v["code"] for v in r.notices}


# --- 1. 表示 ---------------------------------------------------------------------------------------------


def test_delta_is_one_function_in_gib():
    mm = real()["measurement"]
    assert contrib.submission_delta_gb(mm) == pytest.approx(6.46, abs=0.005)
    assert contrib.device_delta_gb({"delta_gb": 3.0}) == 3.0
    assert contrib.device_delta_gb({"before_gb": 1.0, "peak_gb": 4.5}) == 3.5
    assert contrib.device_delta_gb(None) is None and contrib.device_delta_gb({"tool": "system_stats"}) is None
    # device の無い古い提出は note の「/system_stats の GPU 全体の増分 6.46 GB」から(単位は GiB)
    # (note は GPU の総量が分かるときだけ読む)
    mm.pop("device")
    assert contrib.submission_delta_gb(mm, 8.0) == pytest.approx(6.46, abs=0.005)
    # 動的 VRAM でも torch_reserved でもなければ動的 VRAM の扱いにしない
    assert contrib.is_dynamic_torch(real()["measurement"])
    other = real()["measurement"] | {"offload": "none"}
    assert not contrib.is_dynamic_torch(other)


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_show_puts_the_whole_gpu_increase_before_torch(capsys, lang):
    assert cli.main(["--lang", lang, "contrib", "show", str(REAL)]) == 0
    out = capsys.readouterr().out.splitlines()
    head, torch_ = (
        ("GPU 全体の増分:", "torch の確保量:") if lang == "ja" else ("Whole-GPU increase:", "torch reserved:")
    )
    i = next(i for i, x in enumerate(out) if x.startswith(head))
    j = next(j for j, x in enumerate(out) if x.startswith(torch_))
    assert i + 1 == j and "6.46 GiB" in out[i] and "2.28 GiB" in out[j]
    assert "動的 VRAM" in out[i] + out[j] if lang == "ja" else "dynamic VRAM" in out[i] + out[j]
    assert not any(x.startswith(("最大 VRAM:", "Peak VRAM:")) for x in out)


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_show_without_delta_adds_the_caveat(lang):
    from genmodelfit import i18n

    d = real()
    d["measurement"].pop("device")
    d["measurement"]["note"] = "ComfyUI の実行時間 21.71 秒"
    with i18n.forced(lang):
        lines = cli.format_submission(d)
    peak = next(x for x in lines if x.startswith(("最大 VRAM:", "Peak VRAM:")))
    assert (
        "2.28 GiB" in peak
        and ("作業用メモリの一部だけ" if lang == "ja" else "only part of the working memory") in peak
    )
    assert not any("増分" in x or "increase" in x for x in lines)


def test_other_submissions_keep_the_old_line():
    d = real()
    d["measurement"]["offload"] = "none"
    line = next(x for x in cli.format_submission(d) if x.startswith("最大 VRAM:"))
    assert "測り方 torch_reserved" in line and "作業用メモリ" not in line


def test_measure_result_and_share_line_show_the_increase(capsys):
    from genmodelfit.models import Measurement

    mm = real()["measurement"]
    m = Measurement(
        model_id="image_sdxl_simple",
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        outcome="ok",
        peak_vram_gb=2.28,
        offload="dynamic",
        vram_metric="torch_reserved",
        device=mm["device"],
        note=mm["note"],
    )
    rows = cli._vram_rows(cli._measurement_view(m))
    assert rows and rows[0][1].startswith("6.46 GiB") and rows[1][1].startswith("2.28 GiB")
    inline = cli._peak_vram_inline(m)
    assert inline.index("6.46") < inline.index("2.28")
    m.offload = "none"
    assert cli._vram_rows(cli._measurement_view(m)) is None
    assert cli._peak_vram_inline(m) == "最大 VRAM 2.28 GB"


# --- 2. 照合 ---------------------------------------------------------------------------------------------


def test_real_dynamic_submission_has_no_deviation_notice(db):
    assert "deviation" not in codes(real(), db)


def test_dynamic_compares_the_whole_gpu_increase_with_the_estimate(db):
    # 式の見積もり(全部載せ)は約 9.0 GB。24 GB の GPU で増分が 13 GB なら 40% を超えて外れる
    d = real()
    d["system"]["gpu"]["vram_gb"] = 24.0
    d["measurement"]["device"].update(before_gb=1.0, peak_gb=14.0, after_gb=3.0)
    r = contrib.validate_submission(d, db, check_filename=False)
    assert not r.errors, r.errors
    notes = [v for v in r.notices if v["code"] == "deviation"]
    assert notes and "13.00" in notes[0]["message"] and "GPU 全体の増分" in notes[0]["message"]


def test_dynamic_without_delta_has_no_deviation_notice(db):
    d = real()
    d["measurement"].pop("device")
    d["measurement"]["note"] = "ComfyUI の実行時間 21.71 秒"  # 増分が分からない
    # torch の確保量 2.28 は式の作業用メモリ 1.06 から 114% 外れるが、もう比べない
    assert "deviation" not in codes(d, db)
    d["measurement"]["settings"] = {"width": 512, "height": 512, "steps": 25, "precision": "fp16"}
    d["measurement"]["settings_override"] = True
    assert "deviation" not in codes(d, db)


def test_dynamic_small_delta_is_noticed_unless_the_gpu_was_filled(db):
    d = real()
    d["measurement"]["device"].update(
        before_gb=1.05, peak_gb=3.5, after_gb=2.0
    )  # 増分 2.45、GPU は埋まっていない
    assert "deviation" in codes(d, db)
    d["measurement"]["device"].update(before_gb=5.0, peak_gb=7.5, after_gb=5.0)  # 増分 2.5、7.5 >= 8 × 0.9
    assert "deviation" not in codes(d, db)
    # 埋まっていても、見積もりより大きく超える増分は知らせる(16 GB の GPU で増分 14.8)
    d["system"]["gpu"]["vram_gb"] = 16.0
    d["measurement"]["device"].update(before_gb=1.0, peak_gb=15.8, after_gb=3.0)
    assert "deviation" in codes(d, db)


def test_non_dynamic_still_compares_the_peak(db):
    d = real()
    d["measurement"]["offload"] = "none"
    d["measurement"]["peak_vram_gb"] = 2.0
    d["measurement"]["stages"]["after_sampling"]["peak_gb"] = 2.0
    d["measurement"].pop("device")
    assert "deviation" in codes(d, db)


def _note_only(delta: float, vram: float = 8.0) -> dict:
    d = real()
    d["measurement"].pop("device")
    d["measurement"]["note"] = (
        f"/system_stats の GPU 全体の増分 {delta:.2f} GB。開始前に /free でモデルを解放。"
        "ComfyUI の実行時間 21.71 秒"
    )
    d["system"]["gpu"]["vram_gb"] = vram
    return d


def test_note_only_small_increase_is_not_noticed_but_large_is(db):
    # note からは最大(peak_gb)が分からず、埋まったかを決められない: 小さい側は出さない(全部載せ約 9.0)
    assert "deviation" not in codes(_note_only(2.0), db)
    assert "deviation" in codes(_note_only(14.0, vram=24.0), db)


def test_device_without_gpu_total_does_not_notice_the_small_side(db):
    # device はあっても GPU の総量が分からなければ「埋まった」が決められない: 小さい側は出さない(note と同じ)
    d = real()
    d["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)  # 増分 2.45 < 全部載せ約 9.0
    assert "deviation" in codes(d, db)  # 総量 8 GB なら埋まっていないので出す
    for vram in (None, 0):
        d["system"]["gpu"]["vram_gb"] = vram
        r = contrib.validate_submission(d, db, check_filename=False)  # 総量が無いことは誤りとして別に出る
        assert "deviation" not in {v["code"] for v in r.notices}, vram
    # 大きい側は総量が分からなくても出す(増分 13 > 全部載せ約 9.0 × 1.4)
    d["measurement"]["device"].update(before_gb=1.0, peak_gb=14.0, after_gb=3.0)
    r = contrib.validate_submission(d, db, check_filename=False)
    assert "deviation" in {v["code"] for v in r.notices}


def test_note_increase_is_capped_like_device_of(db):
    d = _note_only(999.0)
    mm = d["measurement"]
    assert contrib.submission_delta_gb(mm, 8.0) is None  # 8 × 1.05 を超える
    assert contrib.submission_delta_gb(mm) is None  # 総量が分からなければ note は読まない
    assert contrib.submission_delta_gb(_note_only(6.46)["measurement"]) is None
    assert contrib.submission_delta_gb(_note_only(6.46)["measurement"], 8.0) == pytest.approx(6.46)
    assert "deviation" not in codes(d, db)
    lines = cli.format_submission(d)
    assert not any("999" in x for x in lines if not x.startswith("メモ:"))
    assert any(x.startswith("最大 VRAM:") for x in lines)


def _cols(s: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def test_ja_rows_line_up_by_display_width():
    lines = cli.format_submission(real())
    a = next(x for x in lines if x.startswith("GPU 全体の増分:"))
    b = next(x for x in lines if x.startswith("torch の確保量:"))
    # 値(数)の始まる桁が 2 行でそろう(全角は 2 桁で数える)
    assert _cols(a[: a.index("6.46")]) == _cols(b[: b.index("2.28")])
    # ほかの行(12 桁)と同じ作法: 見出しが短ければ 12 桁にそろえる
    assert cli._label("最大 VRAM", 12) == "最大 VRAM:  " and _cols(cli._label("時間", 12)) == 12
