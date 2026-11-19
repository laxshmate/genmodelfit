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
    # 全部載せ約 9.0 が入る 12 GB の GPU(12 × 0.9 = 10.8)で、埋まったかの規則を見る
    d = real()
    d["system"]["gpu"]["vram_gb"] = 12.0
    d["measurement"]["device"].update(
        before_gb=1.05, peak_gb=3.5, after_gb=2.0
    )  # 増分 2.45、GPU は埋まっていない
    assert "deviation" in codes(d, db)
    d["measurement"]["device"].update(before_gb=8.5, peak_gb=11.0, after_gb=8.5)  # 増分 2.5、11.0 >= 10.8
    assert "deviation" not in codes(d, db)
    # 8 GB の GPU では全部載せ約 9.0 が 8 × 0.9 を超える(全部載せに届かない):
    # 埋まっていなくても小さい側は出さない
    d["system"]["gpu"]["vram_gb"] = 8.0
    d["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)
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
    d["system"]["gpu"]["vram_gb"] = 12.0
    d["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)  # 増分 2.45 < 全部載せ約 9.0
    assert "deviation" in codes(d, db)  # 総量 12 GB なら埋まっていないので出す
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


# --- 3. 失敗した提出・note の最大・桁の多い整数・精度の表(#217 の続き、#218) ------------------------------


def _smi(before: float, peak: float, tool: str = "nvidia-smi") -> str:
    """bench が note に書く道具の文(bench.py の note_parts の 1 つ目)。"""
    delta = max(0.0, peak - before)
    return f"GPU 全体({tool})の増分 {delta:.2f} GB(開始前 {before:.2f} GB 使用、最大 {peak:.2f} GB)。"


def test_failed_submission_has_no_increase_row(db):
    # 最後まで動かなかった提出の増分は見積もりと比べる量でない: 「見積もりと比べるのはこちら」を出さない
    # (照合も ok だけ)。device の項目があっても、note から読む古い提出でも同じ
    for d in (real(), _note_only(6.46)):
        d["measurement"]["outcome"] = "oom"
        assert contrib.submission_delta_gb(d["measurement"], 8.0) is None
        lines = cli.format_submission(d)
        assert not any(x.startswith("GPU 全体の増分:") for x in lines)
        assert any(x.startswith("最大 VRAM:") for x in lines)
        d["measurement"]["outcome"] = "ok"
        assert contrib.submission_delta_gb(d["measurement"], 8.0) == pytest.approx(6.46, abs=0.005)
    # share の途中経過・measure の表示(Measurement から)も同じ
    d = real()
    d["measurement"]["outcome"] = "oom"
    m = contrib.to_measurement(d)
    assert contrib.submission_delta_gb(cli._measurement_view(m)) is None
    assert "GPU 全体の増分" not in cli._peak_vram_inline(m)


def test_note_peak_lets_the_small_side_be_checked(db):
    # note の道具の文から GPU 全体の最大を読み、device があるときと同じ「埋まった」の判定に使う
    d = _note_only(2.0, vram=12.0)
    d["measurement"]["note"] = _smi(1.5, 3.4) + d["measurement"]["note"]
    assert contrib.note_peak_gb(d["measurement"]["note"]) == pytest.approx(3.4)
    assert contrib.submission_peak_gb(d["measurement"], 12.0) == pytest.approx(3.4)
    assert "deviation" in codes(d, db)  # 3.4 < 12 × 0.9: 埋まっていない。増分 2.0 ↔ 全部載せ約 9.0
    d["measurement"]["note"] = _smi(8.6, 10.9) + _note_only(2.0)["measurement"]["note"]
    assert "deviation" not in codes(d, db)  # 10.9 >= 10.8: 埋まった


@pytest.mark.parametrize(
    "smi",
    [
        _smi(1.0, 9.0),  # 総量 8 × 1.05 を超える
        _smi(0.5, 1.5),  # 最大 1.5 < 増分 2.0(合わない)
        _smi(1.5, 3.4) + _smi(1.5, 3.4),  # 2 つある(bench は 1 つしか書かない)
        _smi(1.5, 3.4, "nvidia-smi、複数の GPU の合計"),  # 複数の GPU の合計は 1 枚の総量と比べない
        "GPU 全体(nvidia-smi)の増分 1.90 GB(開始前 1.50 GB 使用、最大 33333.4 GB)。",  # 長い数
        "GPU 全体(nvidia-smi)の増分 1.90 GB(開始前 1.50 GB 使用、最大 ３.4 GB)。",  # 全角の数字
    ],
)
def test_note_peak_that_does_not_fit_is_not_used(db, smi):
    d = _note_only(2.0)
    d["measurement"]["note"] = smi + d["measurement"]["note"]
    assert contrib.submission_peak_gb(d["measurement"], 8.0) is None
    assert "deviation" not in codes(d, db)  # 読めなければ今までどおり小さい側を出さない


def test_note_peak_regex_is_linear():
    import time

    note = "GPU 全体(" + "a" * 400 + ")の増分 1 GB(開始前 1 GB 使用、最大 " + "9" * 60
    t = time.perf_counter()
    assert contrib.note_peak_gb(note) is None
    assert time.perf_counter() - t < 0.5


def test_bundled_old_submissions_are_checked_on_the_small_side(db):
    # 同梱の data/contrib の古い提出(device の無いもの)でも、note の最大で小さい側を確かめる。
    # 件数は決め打ちしない
    root = Path(__file__).parents[1] / "data" / "contrib"
    subs = sorted(root.rglob("*.json"))
    assert subs
    peaks = 0
    for p in subs:
        d = json.loads(p.read_text(encoding="utf-8"))
        mm = d["measurement"]
        if "device" not in mm and contrib.is_dynamic_torch(mm):
            vram = d["system"]["gpu"]["vram_gb"]
            peaks += contrib.submission_peak_gb(mm, vram) is not None
    assert peaks > 0


BIG = int("9" * 400)


@pytest.mark.parametrize(
    "where",
    [
        ("measurement", "device", "peak_gb"),
        ("measurement", "device", "delta_gb"),
        ("system", "gpu", "vram_gb"),
        ("measurement", "peak_vram_gb"),
        ("measurement", "settings", "width"),
        ("measurement", "estimate", "vram_gb"),
        ("measurement", "estimate", "vram_resident_gb"),
        ("measurement", "estimate", "work_gb"),
        ("measurement", "estimate", "ram_required_gb"),
        ("measurement", "estimate", "seconds"),
        ("measurement", "timing", "queue_s"),
        ("measurement", "timing", "sampling_s"),
        ("measurement", "timing", "sec_per_step"),
        ("measurement", "timing", "total_s"),
    ],
)
def test_huge_integer_is_an_error_not_a_crash(db, where, tmp_path, capsys):
    d = real()
    x = d
    for k in where[:-1]:
        x = x[k]
    x[where[-1]] = BIG
    r = contrib.validate_submission(d, db, check_filename=False)
    assert any(".".join(where) in e["message"] for e in r.errors), r.errors
    assert not r.ok
    for f in (contrib.submission_delta_gb, contrib.submission_peak_gb):
        f(d["measurement"], BIG)
        f(d["measurement"], d["system"]["gpu"]["vram_gb"])
    contrib.device_delta_gb(d["measurement"].get("device"))
    contrib.dynamic_comparison(d["measurement"], 6.25, BIG)
    p = tmp_path / "big.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert cli.main(["contrib", "validate", str(p)]) != 0
    assert cli.main(["contrib", "show", str(p)]) != 0
    capsys.readouterr()


def test_num_rejects_integers_too_large_for_float():
    assert not contrib._num(BIG) and contrib._num(10**300) and not contrib._num(float("nan"))
    assert contrib.number_problems({"a": BIG}) and not contrib.number_problems({"a": 10**300})


def test_calibrate_compares_dynamic_rows_by_the_increase(db):
    from genmodelfit import calibrate as cal
    from genmodelfit.estimate import working_only

    rows = cal.build_check_rows(db)
    theta = cal.fit(cal.build_rows(db))
    checked = cal.check(rows, theta)
    dyn = [(r, x) for r, x in zip(rows, checked, strict=True) if working_only(r.m)]
    assert dyn
    for r, x in dyn:
        view = contrib.measurement_view(r.m)
        got = contrib.dynamic_comparison(view, r.predict(theta), r.overhead_gb, r.m.gpu_vram_gb)
        assert x["torch_gb"] == r.m.peak_vram_gb and x["predicted_gb"] == round(r.predict(theta), 2)
        if got is None:
            assert x["quantity"] == "unchecked" and x["error_pct"] is None
            continue
        # 比べる量は増分 + 式の固定分(GPU 全体の表の device_check と同じ値)
        assert x["quantity"] == "device_delta" and x["measured_gb"] == round(got.whole_gb, 2)
        assert x["delta_gb"] == round(got.delta_gb, 2)
        assert x["measured_gb"] == x["device"]["whole_gb"]
        # 誤差も GPU 全体の表と同じ計算・同じ丸めの順(calibrate.error_pct)
        if x["error_pct"] is not None and x["device"]["error_pct"] is not None:
            assert x["error_pct"] == x["device"]["error_pct"]
        assert x["clean"] is got.clean
        assert x["comparable"] is got.comparable and x["filled"] is got.filled
        assert (x["error_pct"] is None) is (not got.comparable)
    # 照合の行は係数の合わせに入らない(係数は同梱の calibration.json と同じ)
    committed = json.loads(
        (Path(cal.__file__).parent / "data" / "calibration.json").read_text(encoding="utf-8")
    )
    assert {**cal.FROZEN, **theta} == committed["coefficients"]
    table = "\n".join(cal.check_table(checked))
    assert "GPU 全体の増分 + 固定分(動的 VRAM)" in table or "whole-GPU increase + fixed part" in table
    assert "-0%" not in table  # device_table と同じく「-0%」と書かない


def test_dynamic_increase_is_compared_with_the_fixed_part_added(db):
    # 増分は開始前からの差で、起動済みの ComfyUI の固定分(CUDA の文脈など)を含まない。全部載せの式は固定分を
    # 含むので、増分 + 式の固定分(device_check と同じ device_whole_gb)を見積もりと比べる
    from genmodelfit.estimate import load_coefficients

    over = load_coefficients().overhead_gb
    assert over > 0
    mm = _note_only(6.46)["measurement"]
    got = contrib.dynamic_comparison(mm, 6.25, over, 8.0)
    assert got.delta_gb == pytest.approx(6.46) and got.whole_gb == pytest.approx(6.46 + over)
    assert contrib.device_whole_gb(1.0, over) == pytest.approx(1.0 + over)
    # 16 GB の GPU で増分 11.5: 増分だけなら全部載せ約 9.0 から 40% 以内、固定分を足すと 40% を超える
    d = _note_only(11.5, vram=16.0)
    full = contrib._formula_full(db.get(d["measurement"]["model_id"]), None)
    assert full is not None and abs(11.5 - full[0]) / full[0] < contrib.NOTICE_DEVIATION
    assert abs(11.5 + over - full[0]) / full[0] > contrib.NOTICE_DEVIATION
    assert "deviation" in codes(d, db)


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_display_shows_the_amount_compared_with_the_estimate(capsys, lang):
    # 表示も照合と同じ量(増分 + 式の固定分。contrib.device_whole_gb・係数の overhead_gb)を示し、
    # 見積もりと比べるかも照合と同じ判定(contrib.estimate_comparison)で書く
    from genmodelfit import i18n
    from genmodelfit.estimate import load_coefficients

    over = load_coefficients().overhead_gb
    whole = contrib.device_whole_gb(6.46, over)
    assert f"{whole:.2f}" == "7.93"

    def shown(d: dict) -> str:
        with i18n.forced(lang):
            lines = cli.format_submission(d, db=load_db(use_cache=False))
            return next(x for x in lines if x.startswith(("GPU 全体の増分:", "Whole-GPU")))

    # 本物の提出: GPU が埋まった(7.51 >= 8 × 0.9)うえ 7.93 は全部載せより小さいので比べない(照合と同じ)
    d = real()
    assert "deviation" not in {
        v["code"]
        for v in contrib.validate_submission(d, load_db(use_cache=False), check_filename=False).notices
    }
    line = shown(d)
    assert f"+ {'固定分' if lang == 'ja' else 'fixed part'} {over:.2f} = {whole:.2f} GiB" in line
    assert ("GPU が埋まったので、見積もりとは比べない" if lang == "ja" else "as the GPU was filled") in line
    assert ("を見積もりと比べる" if lang == "ja" else "is what compares") not in line
    # 埋まっていなければ、全部載せが GPU に入るとき(12 GB)は比べる。8 GB では全部載せ約 9.0 が GPU より大きい
    d["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)
    line = shown(d)
    assert (
        "見積もりが GPU より大きいので" if lang == "ja" else "the estimate is larger than the GPU"
    ) in line
    d["system"]["gpu"]["vram_gb"] = 12.0
    line = shown(d)
    assert (" GiB を見積もりと比べる。" if lang == "ja" else "GiB is what compares with the estimate") in line
    # 開始前に torch が確保していた(clean でない)ときは比べない
    d["measurement"]["device"]["before_torch_gb"] = 6.5
    line = shown(d)
    assert ("開始前に torch が確保していたので" if lang == "ja" else "as torch held memory before") in line
    # validate が照らさない提出(メインメモリに逃がした・最大 VRAM が 0)は、表示も「比べる」と言わない
    # (contrib.cross_checked の 1 か所)
    for change in ({"spilled": True}, {"peak_vram_gb": 0}):
        e = real()
        e["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)
        e["measurement"].update(change)
        line = shown(e)
        assert ("見積もりと比べ" if lang == "ja" else "the estimate") not in line, change
    # share の途中経過の 1 行も同じ量と判定
    with i18n.forced(lang):
        inline = cli._peak_vram_inline(contrib.to_measurement(real()), load_db(use_cache=False))
    assert f"{over:.2f} = {whole:.2f} GiB" in inline and "2.28" in inline
    assert ("GPU が埋まった" if lang == "ja" else "the GPU was filled") in inline
    capsys.readouterr()


def test_not_clean_baseline_is_not_compared(db):
    # 開始前に torch が確保していた(before_torch_gb 6.5 > DEVICE_CLEAN_TORCH_GB)提出は、増分が小さく出るので
    # どちらの側も比べない(calibrate.device_check と同じ device_clean)
    d = real()
    d["measurement"]["device"].update(before_gb=7.0, peak_gb=23.0, after_gb=8.0, before_torch_gb=6.5)
    d["system"]["gpu"]["vram_gb"] = 24.0
    got = contrib.dynamic_comparison(d["measurement"], 9.0, 1.47, 24.0)
    assert got is not None and got.clean is False and got.comparable is False
    assert "deviation" not in codes(d, db)
    d["measurement"]["device"]["before_torch_gb"] = 0.1
    assert "deviation" in codes(d, db)  # 増分 16.0 + 固定分 は全部載せ約 9.0 の 40% を超える
    assert contrib.device_clean({"before_torch_gb": 0.25}) and not contrib.device_clean({"tool": "x"})


def test_offload_is_matched_case_insensitively():
    mm = real()["measurement"] | {"offload": "Dynamic"}
    assert contrib.is_dynamic_torch(mm)


def test_huge_integer_in_a_failures_file_does_not_crash_show():
    # 失敗の記録のファイル(#132)の数も、float にできない整数・数でない値で表示が落ちない(#218)
    d = {
        "failures": [
            {
                "model_id": "image_sdxl_simple",
                "outcome": "oom",
                "seconds": BIG,
                "timeout_s": BIG,
                "peak_vram_gb": BIG,
            },
            {
                "model_id": "image_sdxl_simple",
                "outcome": "error",
                "seconds": "x",
                "peak_vram_gb": "y",
            },
        ]
    }
    lines = cli.format_submission(d)
    assert sum("不明" in x or "unknown" in x for x in lines) >= 2
    assert cli._num_text(BIG) in ("不明", "unknown") and cli._num_text(2.5, " s") == "2.5 s"


def test_report_counts_dynamic_rows_and_rows_not_compared(db):
    # --report の要約は、動的 VRAM の行(device_delta)を全部載せの行と別に数え、比べなかった行の数を必ず出す
    # (比べないのは見積もりより小さい側だけなので、抜いたまま要約すると良く見える)。breach の判定は変えない
    rep = contrib.accuracy_report(db, threshold_pct=25.0)
    rows = [x for x in rep["check"] if not x["censored"]]
    n = {
        k: sum(b[k] for b in rep["by_modality"].values())
        for k in ("check_n", "check_dynamic_n", "check_not_compared_n")
    }
    assert n["check_n"] == sum(1 for x in rows if x["quantity"] == "full" and x["error_pct"] is not None)
    assert n["check_dynamic_n"] == sum(
        1 for x in rows if x["quantity"] == "device_delta" and x["error_pct"] is not None
    )
    assert n["check_not_compared_n"] == sum(1 for x in rows if x["error_pct"] is None)
    assert n["check_not_compared_n"] > 0  # 同梱の実測に比べない行がある(件数は決め打ちしない)
    for b in rep["by_modality"].values():
        assert {"check_dynamic_mean_abs_pct", "check_dynamic_max_abs_pct"} <= set(b)
    # 動的 VRAM の行は閾値の判定(breach)に入れない(今までどおり)
    dyn_ids = {x["model_id"] for x in rows if x["quantity"] != "full"}
    assert not [x for x in rep["breaches"] if x["kind"] == "community_check" and x["model_id"] in dyn_ids]
    from genmodelfit import i18n

    with i18n.forced("ja"):
        text = "\n".join(cli.format_report(rep))
    assert "| 動的 VRAM の照合 件数 | 動的 VRAM の照合 最大 | 比べなかった照合 |" in text


def test_show_db_option_is_used_for_the_display(tmp_path, capsys):
    # contrib show --db X: 表示の「見積もりと比べる」も、照合と同じ X の一覧で決める
    # (モデルの無い一覧では書かない)
    d = real()
    d["system"]["gpu"]["vram_gb"] = 12.0  # 全部載せ約 9.0 が入る GPU
    d["measurement"]["device"].update(before_gb=1.05, peak_gb=3.5, after_gb=2.0)  # 埋まっていない: 比べる
    sub = tmp_path / "s.json"
    sub.write_text(json.dumps(d), encoding="utf-8")
    full = json.loads((Path(contrib.__file__).parent / "data" / "db.json").read_text(encoding="utf-8"))
    full["models"] = [m for m in full["models"] if m["id"] != d["measurement"]["model_id"]]
    other = tmp_path / "db.json"
    other.write_text(json.dumps(full), encoding="utf-8")

    def line(*extra: str) -> str:
        cli.main(["--lang", "ja", "contrib", "show", *extra, str(sub)])
        return next(x for x in capsys.readouterr().out.splitlines() if x.startswith("GPU 全体の増分:"))

    assert " GiB を見積もりと比べる。" in line()
    got = line("--db", str(other))
    assert "見積もりと比べ" not in got and "固定分 1.47 = " in got


@pytest.mark.parametrize(
    "where,value",
    [
        (("system",), "x"),
        (("system",), 5),
        (("system", "gpu"), "x"),
        (("tool",), "x"),
        (("measurement",), 5),
        (("measurement", "settings"), "x"),
        (("system", "gpu", "vendor"), [1]),
        (("system", "gpu", "vendor"), {"a": 1}),
    ],
)
def test_wrong_shape_is_an_error_not_a_crash(db, where, value, tmp_path, capsys):
    # 形の違う提出: validate は誤りを返し、show は読めないところを「不明」と書いて落ちない(前からの問題)
    d = real()
    x = d
    for k in where[:-1]:
        x = x[k]
    x[where[-1]] = value
    r = contrib.validate_submission(d, db, check_filename=False)
    assert not r.ok and r.errors
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert cli.main(["--lang", "ja", "contrib", "validate", str(p)]) != 0
    assert cli.main(["--lang", "ja", "contrib", "show", str(p)]) != 0
    assert "不明" in capsys.readouterr().out


def test_one_reader_of_the_whole_gpu_usage():
    # GPU 全体の使用量の読み方は submission_device の 1 か所(device_of・まとめ・表示・照合)。
    # note の道具の最大も peak_gb に入れ、まとめにも残る
    d = _note_only(2.0)
    d["measurement"]["note"] = _smi(1.5, 3.4) + d["measurement"]["note"]
    mm = d["measurement"] | {"gpu_vram_gb": 8.0}
    got = contrib.submission_device(mm)
    assert got["peak_gb"] == pytest.approx(3.4) and got["delta_gb"] == pytest.approx(2.0)
    m = contrib.to_measurement(d)
    assert contrib.device_of(m) == contrib.submission_device(contrib.measurement_view(m))
    assert contrib.device_of(m)["peak_gb"] == pytest.approx(3.4)
    # まとめ(aggregate_device)にも note の最大が入る
    agg = contrib.aggregate_device([("a", m)])
    assert agg is not None and agg["peak_gb"] == pytest.approx(3.4) and agg["from_note"] == 1
    # 総量が分からなければ note は読まない・最後まで動かなかった実測は読まない(比べない側)
    assert contrib.submission_device(mm | {"gpu_vram_gb": None}) is None
    assert contrib.submission_device(real()["measurement"] | {"outcome": "oom"}) is None


def test_bundled_aggregate_has_note_peaks():
    # 同梱のまとめの行は、note の道具の最大を device.peak_gb に持つ(件数は決め打ちしない)
    rows = json.loads(
        (Path(contrib.__file__).parent / "data" / "measurements_community.json").read_text("utf-8")
    )
    rows = rows["measurements"] if isinstance(rows, dict) else rows
    devs = [r["device"] for r in rows if isinstance(r.get("device"), dict)]
    assert devs and any(isinstance(x.get("peak_gb"), (int, float)) for x in devs)


def test_estimate_larger_than_the_gpu_is_not_compared_on_the_smaller_side(db):
    # 全部載せの見積もりが GPU の総量 × 0.9 より大きいと、動的 VRAM は全部載せに届かない:
    # 「埋まった」と同じく増分 + 固定分が見積もりより小さい側は比べない(大きい側は比べる)。
    # ACE-Step 型: 総量 15.92・見積もり 22.26・最大は総量の 82%
    mm = {
        "offload": "dynamic",
        "vram_metric": "torch_reserved",
        "outcome": "ok",
        "peak_vram_gb": 9.5,
        "gpu_vram_gb": 15.92,
        "device": {
            "tool": "system_stats",
            "before_gb": 3.95,
            "peak_gb": 13.05,
            "after_gb": 4.0,
            "before_torch_gb": 0.09,
        },
    }
    got = contrib.dynamic_comparison(mm, 22.26, 1.47)
    assert got.filled is False and got.over_gpu is True and got.comparable is False
    # 見積もりが総量の 0.9 以下なら今までどおり比べる
    got = contrib.dynamic_comparison(mm, 14.0, 1.47)
    assert got.over_gpu is False and got.comparable is True
    # 大きい側は見積もりが GPU より大きくても比べる
    big = mm | {"device": mm["device"] | {"before_gb": 1.0, "peak_gb": 14.0}}
    assert contrib.dynamic_comparison(big, 13.0, 1.47).comparable is True
    # 同梱の ACE-Step の提出(RTX 5080、見積もり 22.26)は、もう deviation を出さない
    path = Path(__file__).parents[1] / "data" / "contrib" / "rtx-5080" / "2026-10-29-768c13a0.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    assert "deviation" not in codes(d, db)
    from genmodelfit import i18n

    with i18n.forced("ja"):
        line = next(x for x in cli.format_submission(d, db=db) if x.startswith("GPU 全体の増分:"))
    assert "見積もりが GPU より大きいので、見積もりとは比べない" in line
