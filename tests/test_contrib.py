"""利用者が出す実測(#34): 提出用の JSON・検査・まとめ・根拠の段階・校正への取り込み・CLI の形。

tests/fixtures/contrib の 4 件は試験用に作った架空の値(note に SYNTHETIC TEST FIXTURE と書いた)。
data/contrib には何も足さない(本物の提出だけを置く)。
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from genmodelfit import cli, contrib
from genmodelfit.calibrate import build_check_rows, build_rows, calibrate
from genmodelfit.db import COMMUNITY_FILE, Database, bundled_text, load, parse_measurements
from genmodelfit.estimate import estimate, pick_measurement, usable, working_only
from genmodelfit.models import BASIS_DETAILS, Gpu, Measurement, Settings, SystemInfo

FIX = Path(__file__).parent / "fixtures" / "contrib"
TODAY = date(2026, 10, 21)
GOOD = sorted(FIX.rglob("*.json"))
SDXL_3070 = [p for p in GOOD if p.parent.name == "rtx-3070"]


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db() -> Database:
    return load(use_cache=False)


def good() -> dict:
    return json.loads(SDXL_3070[0].read_text(encoding="utf-8"))


def pc(vram: float, name: str = "NVIDIA GeForce RTX 3070") -> SystemInfo:
    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram, backend="cuda")
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])


# --- 提出用の JSON --------------------------------------------------------------------------------


def test_fixtures_are_synthetic_and_valid(db):
    assert len(GOOD) == 4
    for p in GOOD:
        assert "SYNTHETIC TEST FIXTURE" in json.loads(p.read_text(encoding="utf-8"))["measurement"]["note"]
    reports = contrib.validate_files(GOOD, db, today=TODAY)
    assert all(r.ok for r in reports), [r.errors for r in reports]
    # 名前は <GPU>/<日付>-<内容の sha256 の先頭 8 桁>.json
    for p, r in zip(GOOD, reports, strict=True):
        assert r.suggested_path.endswith(f"{p.parent.name}/{p.name}")
    assert not (Path(__file__).parent.parent / "data" / "contrib").exists() or not any(
        "SYNTHETIC" in p.read_text(encoding="utf-8")
        for p in (Path(__file__).parent.parent / "data" / "contrib").rglob("*.json")
    )


def test_build_submission_fields_and_hash():
    m = Measurement(
        model_id="image_sdxl_simple",
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        peak_vram_gb=6.9,
        settings=Settings(width=832, height=832, steps=20),
        seconds=41.2,
        driver="566.03",
        comfyui_version="0.40.0",
        offload="dynamic",
        source="genmodelfit bench, my own words",
        date="2026-10-20",
        note="x",
        vram_metric="torch_reserved",
    )
    sub = contrib.build_submission(m)  # PC の情報なし(ComfyUI が別の PC)
    assert set(sub) == contrib.TOP_KEYS - {"db"}  # db は渡したときだけ(#132)
    assert sub["system"]["os_family"] is None and sub["system"]["ram_total_gb"] is None
    assert sub["system"]["gpu"]["arch"] == "ampere" and sub["system"]["gpu"]["compute_capability"] == "8.6"
    assert "source" not in json.dumps(sub)  # 自由に書ける source は入れない
    assert sub["measurement"]["settings"] == {"width": 832, "height": 832, "steps": 20}
    # hash は内容だけで決まる(キーの順・字下げによらない)
    shuffled = json.loads(json.dumps(sub, sort_keys=True))
    assert contrib.content_hash(shuffled) == contrib.content_hash(sub)
    assert (
        contrib.suggested_path(sub)
        == f"data/contrib/rtx-3070/2026-10-20-{contrib.content_hash(sub)[:8]}.json"
    )


def test_build_submission_rounds_ram_and_keeps_only_os_family():
    s = SystemInfo(os="Windows 11", cpu="SECRET CPU", cpu_cores=8, ram_total_gb=31.87, ram_available_gb=3)
    m = Measurement(model_id="x", gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=23.99, peak_vram_gb=1.0)
    sub = contrib.build_submission(m, s)
    assert sub["system"]["os_family"] == "windows" and sub["system"]["ram_total_gb"] == 32
    assert "SECRET CPU" not in json.dumps(sub) and "Windows 11" not in json.dumps(sub)
    s.overrides = ["ram"]  # --ram で上書きした値は、その PC の値ではないので入れない
    assert contrib.build_submission(m, s)["system"]["ram_total_gb"] is None


def test_scrub_text_drops_whole_sentences(monkeypatch):
    monkeypatch.setattr(contrib, "_identity_words", lambda: ["MYHOST-1234", "alice42"])
    text = (
        "ComfyUI の実行時間 3.45 秒。"
        r"失敗: FileNotFoundError: C:\Users\John Smith\ComfyUI\models\x.safetensors が無い。"
        "MYHOST-1234 で測った。計測 17 回。"
        "/home/alice42/comfy/x.py を見た。"
        " GPU-0f3e1a2b-1111-2222-3333-444455556666 0000:01:00.0 http://192.168.1.20:8188/prompt"
    )
    out = contrib.scrub_text(text)
    # 空白を含むパスの続き(Smith\…)も残らないよう、当たった文ごと落とす
    assert out == "ComfyUI の実行時間 3.45 秒。計測 17 回。(個人の情報らしい文を 4 個省いた)", out
    assert not contrib.privacy_findings({"note": out})


# 攻撃の台本(priv.py / priv2.py)の文。どれも検査で誤りになり、scrub すると検査を通る
LEAKY_NOTES = [
    r"saved in C:\Users\John Smith\AppData\x.json",
    r"saved in C:\Users\山田 太郎\Documents\x",
    r"see \fileserver\share\bob",
    "see \\\\fileserver\\share\\bob",
    "see //fileserver/share/bob",
    r"in %USERPROFILE%\Documents\comfy",
    r"in $HOME/comfy ok",
    r"in ~/comfy",
    "Ｃ：＼Ｕｓｅｒｓ＼ｊｏｈｎ",
    "C" + chr(0x200B) + ":" + chr(92) + "Users" + chr(92) + "john",
    "C:¥Users¥john",
    r"\\?\C:\Users\john",
    r"Users\john\comfy",
    "/home/john/comfy",
    "/Users/john",
    "/mnt/d/ai/comfy",
    "E:/My Stuff/Taro Yamada/comfy",
    r"C:\Program Files\ComfyUI\models\johnsmith_lora.safetensors",
    "host my-gaming-rig.local",
    "PC name is KOYO-PC",
    "PC 名は taro",
    "mac aa:bb:cc:dd:ee:ff",
    "ip 192.168.1.10",
    "ip fe80::1ff:fe23:4567:890a",
    "GPU-1a2b3c4d-1111-2222-3333-444455556666",
    "SN: 1324021012345",
    "at 14:05 on 2026-10-20",
    "at 2pm",
    "user john.doe@example.com",
    "github @KoyoYeager",
    "PCI 0000:01:00.0",
    "PCI 01:00.0",
]


@pytest.mark.parametrize("note", LEAKY_NOTES)
def test_leaky_notes_are_rejected_and_scrubbed(note, db):
    d = good()
    d["measurement"]["note"] = note
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert "privacy" in {e["code"] for e in r.errors}, (note, r.errors)
    scrubbed = contrib.scrub_text(note)
    assert scrubbed == "(個人の情報らしい文を 1 個省いた)", scrubbed
    d["measurement"]["note"] = scrubbed
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert r.ok, r.errors


@pytest.mark.parametrize(
    "note",
    [
        "used 1024x1024 at 20 steps, 1.5.2 version",
        "ran at 2026-10-20",
        "ComfyUI の実行時間 3.45 秒。計測 17 回",
    ],
)
def test_ordinary_notes_pass(note, db):
    d = good()
    d["measurement"]["note"] = note
    assert contrib.validate_submission(d, db, today=TODAY, check_filename=False).ok


def test_patterns_are_fast_on_long_adversarial_strings():
    import time

    for s in ("a" * 2000, "a@" * 1000, "a." * 1000, "1:" * 1000, "/a" * 1000, "\\a" * 600, "-" * 2000):
        t0 = time.perf_counter()
        contrib.privacy_findings({"note": s})
        contrib.scrub_text(s)
        assert time.perf_counter() - t0 < 0.5, s[:10]


# --- 個人の情報(番兵の値): bench --submission ----------------------------------------------------

from test_privacy_pc import SENT_HOST, SENT_UUID, outbound, sentinel_pc  # noqa: E402, F401


def test_bench_submission_has_no_pc_identity(sentinel_pc, outbound, tmp_path, capsys, db):  # noqa: F811
    from test_bench import FakeComfy

    forbidden, home = sentinel_pc
    secret_note = (
        f"{SENT_HOST} で {forbidden[4]} が {home}\\ComfyUI を使い {SENT_UUID} 10.0.0.7 a@b.example 12:34:56。"
        f"{home} の下"
    )
    out_dir = tmp_path / "fork" / "data" / "contrib"
    out_dir.mkdir(parents=True)
    with FakeComfy() as fake:
        args = [
            "bench",
            "image_sdxl_simple",
            "--url",
            fake.url,
            "--submission",
            str(out_dir),
            "--note",
            secret_note,
        ]
        assert cli.main([*args, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    path = Path(data["submission"]["saved_to"])
    assert data["submission"]["path_in_repo"].startswith("data/contrib/rtx-3070/")
    assert path.parent.name == "rtx-3070" and path.parent.parent == out_dir
    text = path.read_text(encoding="utf-8")
    for word in [*forbidden, SENT_UUID, "10.0.0.7", "a@b.example", "12:34:56", "ComfyUI\\"]:
        assert word not in text, word
    sub = json.loads(text)
    assert sub["system"]["ram_total_gb"] == 31  # 番兵の 31.4159 を丸めた値だけ
    assert sub["system"]["gpu"]["name"] == "NVIDIA GeForce RTX 3070"  # 測った GPU(ComfyUI が返した名前)
    assert sub["system"]["gpu"]["arch"] == "ampere"
    r = contrib.validate_files([path], db)[0]
    # 偽の ComfyUI はすぐ終わる。時計の細かさで 0.0 にも 0.1 にもなる(Windows)ので、0.1 未満なら時間の値だけが
    # 範囲の外になり、0.1 以上なら誤りは無い。どちらでも名前・個人の情報などは通る
    secs = sub["measurement"]["seconds"]
    assert secs < 1.0
    if secs < 0.1:
        assert [e["code"] for e in r.errors] == ["value"] and "seconds" in r.errors[0]["message"], r.errors
    else:
        assert r.errors == [], r.errors
    checks = contrib.privacy_checklist(sub, text)
    assert all(c["ok"] is not False for c in checks), checks


# --- 検査 -----------------------------------------------------------------------------------------


def _set(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d[k]
    if value is _DEL:
        del d[keys[-1]]
    else:
        d[keys[-1]] = value


_DEL = object()

BAD_CASES = [
    ("system.hostname", "DESKTOP-1", "unknown_field"),
    ("measurement.serial", "ABC", "unknown_field"),
    ("measurement.note", r"C:\Users\bob\ComfyUI", "privacy"),
    ("measurement.note", "/home/bob/ComfyUI", "privacy"),
    ("measurement.note", "mail me: bob@example.org", "privacy"),
    ("measurement.note", "GPU-12345678-aaaa-bbbb-cccc-1234567890ab", "privacy"),
    ("measurement.note", "server 192.168.0.12", "privacy"),
    ("measurement.note", "on DESKTOP-ABC1234", "privacy"),
    ("measurement.note", "pci 0000:01:00.0", "privacy"),
    ("measurement.note", "http://example.org/x", "privacy"),
    ("date", "2026-10-20T10:11:12Z", "date"),
    ("date", "2027-01-01", "date"),
    ("contrib_schema", 4, "schema"),
    ("measurement.peak_vram_gb", 9.5, "value"),
    ("measurement.peak_vram_gb", -1, "value"),
    ("measurement.seconds", 0, "value"),
    ("measurement.seconds", _DEL, "value"),
    ("measurement.outcome", "crashed", "value"),
    ("measurement.peak_ram_gb", 64.0, "value"),
    ("measurement.offload", "magic", "value"),
    ("measurement.vram_metric", "nvidia_smi_total", "value"),
    ("measurement.settings.width", 10, "settings"),
    ("measurement.settings.steps", 2.5, "settings"),
    ("measurement.settings.prompt", "a cat", "unknown_field"),
    ("system.gpu.compute_capability", "7.5", "gpu_inconsistent"),
    ("system.gpu.arch", "turing", "gpu_inconsistent"),
    ("system.gpu.vram_gb", 0, "value"),
    ("system.gpu.driver", "latest", "value"),
    ("system.os_family", "Windows 11 26100", "value"),
    ("system.ram_total_gb", -4, "value"),
    ("measurement.model_id", "", "value"),
    ("kind", "other", "schema"),
    ("tool.name", "llmfit", "schema"),
    ("tool.version", "0.3.0 by john", "schema"),
    ("system.gpu.name", "NVIDIA GeForce RTX 3070 (john's rig)", "value"),
    ("system.gpu.name", "RTX 3070 | <img src=x>\n::warning::gpu", "string"),
    ("comfyui_version", "0.40.0+john", "value"),
    ("comfyui_version", "0.40", "value"),
    ("measurement.settings.precision", "johnpc", "settings"),
    ("measurement.model_id", "x\n::error title=pwn::injected", "string"),
    ("measurement.model_id", "a" * 201, "string"),
    ("measurement.model_id", "image <b>", "value"),
    ("measurement.note", "x" * 2001, "string"),
    ("measurement.note", "line1\nline2", "string"),
    ("measurement.note", "bidi \u202e txt", "string"),
    ("measurement.offload", ["none"], "string"),
]


@pytest.mark.parametrize(("key", "value", "code"), BAD_CASES, ids=[f"{k}={v!r}" for k, v, _ in BAD_CASES])
def test_validate_catches_bad_value(key, value, code, db):
    d = good()
    _set(d, key, value)
    r = contrib.validate_submission(d, db, today=TODAY)
    assert not r.ok
    assert code in {e["code"] for e in r.errors}, r.errors


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("measurement.standard", False, "nonstandard"),
        ("system.ram_total_gb", 31.4, "ram_not_rounded"),
        ("system.gpu.driver", None, "driver_missing"),
        ("measurement.settings.frames", 49, "settings_modality"),
    ],
)
def test_validate_warnings_do_not_fail(key, value, code, db):
    d = good()
    _set(d, key, value)
    r = contrib.validate_submission(d, db, today=TODAY)
    assert r.ok, r.errors
    assert code in {w["code"] for w in r.warnings}


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("measurement.model_id", "image_not_in_db", "unknown_model"),
        ("system.gpu.name", "AMD Radeon RX 7900 XTX", "unknown_gpu"),
        ("measurement.peak_vram_gb", 2.0, "deviation"),  # 式の見積もり(約 8 GB)から 40% より外れる
        (
            "measurement.offload",
            "none",
            "below_weights",
        ),  # 全部載せなのに重み + 固定分より小さい(見積もりは下限)
    ],
)
def test_validate_notices_do_not_fail(key, value, code, db, tmp_path, capsys):
    """llmfit と同じく、機械的な検査だけを失敗にする(2026-10-22)。式から外れる・一覧に無いモデルなどは
    確認(notices)としてジョブのまとめに出すだけで、まとめにも使う(マージするメンテナが見る)。"""
    d = good()
    _set(d, key, value)
    if key == "system.gpu.name":  # 表に無い GPU は世代・compute の照合もしない
        d["system"]["gpu"]["arch"] = d["system"]["gpu"]["compute_capability"] = None
    r = contrib.validate_submission(d, db, today=TODAY)
    assert r.ok and code in {v["code"] for v in r.notices}, (r.errors, r.notices)
    p = tmp_path / contrib.suggested_path(d).split("/", 2)[2]
    p.parent.mkdir(parents=True)
    p.write_text(contrib.dumps(d), encoding="utf-8")
    assert cli.main(["contrib", "validate", str(p)]) == 0
    out = capsys.readouterr().out
    assert "確認 " + code in out and "確認つきの ok 1" in out
    assert cli.main(["contrib", "validate", str(p), "--markdown"]) == 0
    md = capsys.readouterr().out
    assert "マージする人へ: 確認の項目のある提出が 1 件あります" in md and "ok(**確認**)" in md
    subs, _ = contrib.load_dir(tmp_path, db)
    assert len(subs) == 1  # まとめにも使う


def test_review_flags_and_reviewed_list_are_gone(tmp_path, capsys):
    """人が sha8 ごとに確かめる仕組み(--allow-review・--reviewed・data/contrib/REVIEWED)は外した
    (2026-10-22)。"""
    assert not hasattr(contrib, "read_reviewed") and not hasattr(contrib, "REVIEWED_FILE")
    for args in (
        ["contrib", "validate", str(FIX), "--allow-review", "deadbeef"],
        ["contrib", "validate", str(FIX), "--reviewed", str(tmp_path / "REVIEWED")],
        ["contrib", "aggregate", str(FIX), "--allow-review", "deadbeef"],
    ):
        with pytest.raises(SystemExit) as e:
            cli.main(args)
        assert e.value.code == 2
    capsys.readouterr()


def test_validate_filename_duplicates_and_unreadable(tmp_path, db):
    d = good()
    good_name = contrib.suggested_path(d).split("/", 2)[2]
    ok = tmp_path / good_name
    ok.parent.mkdir(parents=True)
    ok.write_text(contrib.dumps(d), encoding="utf-8")
    dup = tmp_path / "copy" / good_name.split("/")[1]  # 同じ内容(字下げが違っても同じ hash)
    dup.parent.mkdir()
    dup.write_text(json.dumps(d, sort_keys=True), encoding="utf-8")
    wrong_hash = ok.parent / "2026-10-20-00000000.json"
    wrong_hash.write_text(contrib.dumps(d), encoding="utf-8")
    broken = ok.parent / "2026-10-20-11111111.json"
    broken.write_text("{not json", encoding="utf-8")
    reports = contrib.validate_files([ok, dup, wrong_hash, broken], db, today=TODAY)
    codes = [{e["code"] for e in r.errors} for r in reports]
    assert codes[0] == set()
    assert "duplicate" in codes[1] and "filename" in codes[1]  # フォルダ名も GPU と違う
    assert "filename" in codes[2] and "duplicate" in codes[2]
    assert codes[3] == {"read"}
    assert reports[1].errors[0]["message"].endswith("と同じ内容です") or any(
        e["code"] == "duplicate" for e in reports[1].errors
    )


def test_file_limits(tmp_path, db):
    big = tmp_path / "rtx-3070" / "2026-10-20-00000000.json"
    big.parent.mkdir()
    d = good()
    big.write_text(json.dumps(d) + " " * (contrib.MAX_FILE_BYTES + 1), encoding="utf-8")
    deep = tmp_path / "rtx-3070" / "2026-10-20-11111111.json"
    deep.write_text("[" * 30000 + "]" * 30000, encoding="utf-8")  # 64 KB 未満で入れ子が深い
    nested = tmp_path / "rtx-3070" / "2026-10-20-22222222.json"
    d2 = good()
    d2["measurement"]["settings"] = {"width": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}
    nested.write_text(json.dumps(d2), encoding="utf-8")
    reports = contrib.validate_files([big, deep, nested], db, today=TODAY)
    assert [{e["code"] for e in r.errors} >= {"read"} for r in reports[:2]] == [True, True]
    assert "大きすぎます" in reports[0].errors[0]["message"]
    assert "入れ子が深すぎます" in reports[1].errors[0]["message"]
    assert not reports[2].ok


def test_symlinks_are_rejected(tmp_path, db):
    target = tmp_path / "outside.json"
    target.write_text(contrib.dumps(good()), encoding="utf-8")
    root = tmp_path / "contrib" / "rtx-3070"
    root.mkdir(parents=True)
    link = root / "2026-10-20-33333333.json"
    try:
        link.symlink_to(target)
        (tmp_path / "contrib" / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("シンボリックリンクを作れない環境")
    files = contrib.find_files(tmp_path / "contrib")
    assert files == [link]  # シンボリックリンクのフォルダはたどらない
    r = contrib.validate_files(files, db, today=TODAY)[0]
    assert {e["code"] for e in r.errors} == {"read"} and "シンボリックリンク" in r.errors[0]["message"]


def test_markdown_cells_are_neutralized(tmp_path, capsys):
    """攻撃の台本 md.py: モデル ID・GPU 名・フォルダ名に Markdown / HTML / ワークフローのコマンドを入れる。"""
    d = good()
    d["measurement"]["model_id"] = (
        "x\n::error title=pwn::injected\n| a | <a href=https://evil.example>click</a> |"
    )
    d["system"]["gpu"]["name"] = "RTX 3070 | <img src=x> \n::warning::gpu"
    p = tmp_path / "evil`name" / "2026-10-20-deadbeef.json"
    p.parent.mkdir()
    p.write_text(json.dumps(d), encoding="utf-8")
    deep = tmp_path / "deep" / "2026-10-20-00000000.json"
    deep.parent.mkdir()
    deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert cli.main(["contrib", "validate", str(tmp_path), "--markdown"]) == 1
    out = capsys.readouterr().out
    for bad in ("<a ", "<img", "\n::", "`name", "](", "| a |"):
        assert bad not in out, bad
    body = [line for line in out.splitlines() if line.startswith("| **NG**")]
    assert len(body) == 2 and all(line.count(" | ") == 4 for line in body)  # 列は増えない
    assert all(not line.startswith("::") for line in out.splitlines())
    assert contrib.md_cell("a|b`c<d>[e](f)\nx") == "a&#124;b&#96;c&lt;d&gt;&#91;e&#93;(f) x"
    # GFM の自動リンク(scheme://・www.・メール)も切る
    cell = contrib.md_cell("https://evil.example/p.png www.evil.example a@b.example")
    assert "://" not in cell and "www." not in cell and "@" not in cell


# --- まとめる -------------------------------------------------------------------------------------


def _sub(
    peak: float, *, gpu: str = "NVIDIA GeForce RTX 3070", outcome: str = "ok", standard: bool = True, w=832
):
    d = good()
    d["system"]["gpu"]["name"] = gpu
    d["measurement"].update(peak_vram_gb=peak, outcome=outcome, standard=standard)
    d["measurement"]["settings"]["width"] = w
    if outcome == "oom":
        d["measurement"]["offload"] = "oom"
    return contrib.Submission(path="-", sha256=contrib.content_hash(d), data=d)


def test_robust_filter():
    assert contrib.robust_filter([5.0, 9.0]) == ([0, 1], [], 2.0)  # 2 件では外れ値を見分けない
    keep, drop, _ = contrib.robust_filter([6.9, 7.0, 7.02, 7.05, 3.1])
    assert drop == [4] and keep == [0, 1, 2, 3]
    keep, drop, _ = contrib.robust_filter([7.0, 7.0, 7.0, 7.1])  # MAD 0 でも小さな差は外さない(下限)
    assert drop == []


def test_aggregate_outliers_counts_and_determinism():
    subs = [_sub(6.9), _sub(7.02), _sub(7.05), _sub(3.1), _sub(6.0, standard=False)]
    subs += [_sub(5.0, gpu="NVIDIA GeForce RTX 4060")]  # 別の GPU は別の組
    subs += [_sub(7.3, outcome="oom", w=1024), _sub(7.4, outcome="oom", w=1024)]  # OOM だけの組
    rows = contrib.aggregate(subs)
    assert rows == contrib.aggregate(list(reversed(subs)))  # 並びによらない
    assert json.dumps(rows) == json.dumps(contrib.aggregate(subs[3:] + subs[:3]))
    by = {(r["gpu_name"], r["settings"]["width"], r["outcome"]): r for r in rows}
    main = by[("NVIDIA GeForce RTX 3070", 832, "ok")]
    assert main["n_submissions"] == 4  # 標準でない 1 件は数えない
    assert (main["n_used"], main["n_outliers"], main["n_oom"]) == (3, 1, 0)
    assert main["peak_vram_gb"] == 7.02 and main["consistent"] is True
    assert main["outlier_submissions"] == [_sub(3.1).sha256[:8]]
    assert main["origin"] == "community" and main["source"].startswith("community")
    one = by[("NVIDIA GeForce RTX 4060", 832, "ok")]
    assert one["n_used"] == 1 and one["consistent"] is False  # 1 件は係数の合わせ直しには入れない
    oom = by[("NVIDIA GeForce RTX 3070", 1024, "oom")]
    assert oom["n_oom"] == 2 and oom["consistent"] is False
    ms = parse_measurements(contrib.community_document(rows))
    # 見積もりには ok の行を 1 件から使う(OOM だけの行は使わない)。件数(n_used・n_oom)も読める
    assert [usable(m) for m in ms].count(True) == 2
    assert {(m.n_used, m.n_oom) for m in ms if usable(m)} == {(3, 0), (1, 0)}
    assert "approved" not in main


def test_group_with_any_oom_is_not_consistent():
    """ok と OOM が混ざる組は ok の中央値を使い、OOM の件数も持つ(perfect の根拠にはしない)。"""
    rows = contrib.aggregate([_sub(6.9), _sub(7.0), _sub(7.9, outcome="oom")])
    assert rows[0]["n_oom"] == 1 and rows[0]["consistent"] is False
    assert rows[0]["outcome"] == "ok" and rows[0]["peak_vram_gb"] == 6.95 and rows[0]["n_used"] == 2
    assert usable(Measurement.from_dict(rows[0]))


def test_grouping_normalizes_gpu_names_and_precision():
    a, b = _sub(6.9), _sub(7.0, gpu="GeForce RTX 3070")
    for s, prec in ((a, "BF16"), (b, "bfloat16")):
        s.data["measurement"]["settings"]["precision"] = prec
        s.sha256 = contrib.content_hash(s.data)
    rows = contrib.aggregate([a, b])
    assert len(rows) == 1 and rows[0]["n_used"] == 2 and rows[0]["settings"]["precision"] == "bf16"


def test_grouping_keeps_vram_variants_apart():
    a, b = _sub(6.9, gpu="NVIDIA GeForce RTX 4060 Ti"), _sub(7.0, gpu="NVIDIA GeForce RTX 4060 Ti")
    b.data["system"]["gpu"]["vram_gb"] = 15.99
    b.sha256 = contrib.content_hash(b.data)
    rows = contrib.aggregate([a, b])
    assert sorted(r["gpu_vram_gb"] for r in rows) == [8.0, 15.99]  # 8 GB と 16 GB は混ぜない


def test_wide_spread_is_not_consistent():
    rows = contrib.aggregate([_sub(5.0), _sub(6.5)])  # 2 件だが幅が 26%
    assert rows[0]["n_used"] == 2 and rows[0]["consistent"] is False


def test_bundled_community_file_is_regenerated_from_its_listed_submissions(db):
    """同梱の measurements_community.json は、submissions に並べた data/contrib の提出をまとめ直したものと
    一致する(版を出すときに
    `genmodelfit contrib aggregate --output src/genmodelfit/data/measurements_community.json`。
    docs/release.md)。マージしてまだまとめていない提出が data/contrib にあってもよい(#90。提出の PR は
    data/contrib だけを変えるので、マージで main の試験を落とさない)。"""
    root = Path(__file__).parent.parent / "data" / "contrib"
    doc = json.loads(bundled_text(COMMUNITY_FILE))
    st = contrib.bundled_status(root, doc, db)
    assert st["invalid"] == [] and st["missing"] == [], st
    assert st["matches"] and st["ok"], st
    assert st["file_sources"] == 0, st  # 版のまとめは出どころを git のコミットで数える(#95)
    hashes = [e["sha256"] for e in doc["submissions"]]
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes) and hashes == sorted(hashes)
    assert all(isinstance(e["source"], str) and e["source"] for e in doc["submissions"])


def test_unlisted_new_submission_keeps_the_bundled_check_green(tmp_path, db):
    """提出の PR をマージした直後(data/contrib に新しい提出があり、同梱のまとめにはまだ無い)も通る。
    まとめに入れた提出が消えた・まとめを手で変えたときは通らない。"""
    root = tmp_path / "data" / "contrib"
    first, second = SDXL_3070[0], SDXL_3070[1]
    for f in (first, second):
        (root / "rtx-3070").mkdir(parents=True, exist_ok=True)
        shutil.copy(f, root / "rtx-3070" / f.name)
    subs, _ = contrib.load_dir(root, db)
    listed = [s for s in subs if s.path.endswith(first.name)]
    doc = contrib.community_document(contrib.aggregate(listed), listed)
    st = contrib.bundled_status(root, doc, db)
    assert st["ok"] and st["listed"] == 1 and [Path(x).name for x in st["pending"]] == [second.name]
    # 空のまとめ(今の main)と、提出のフォルダが無いとき
    empty = contrib.community_document([], [])
    assert contrib.bundled_status(tmp_path / "none", empty, db)["ok"]
    assert contrib.bundled_status(root, empty, db)["ok"]
    # まとめに入れた提出が無い・まとめの値が違う
    (root / "rtx-3070" / first.name).unlink()
    assert not contrib.bundled_status(root, doc, db)["ok"]
    shutil.copy(first, root / "rtx-3070" / first.name)
    bad = copy.deepcopy(doc)
    bad["measurements"][0]["peak_vram_gb"] += 1
    assert not contrib.bundled_status(root, bad, db)["matches"]
    # 古い版の読み方(measurements だけ)で読める
    assert len(parse_measurements(doc)) == 1


def test_cli_aggregate_check(tmp_path, capsys, monkeypatch):
    from genmodelfit import db as dbmod

    # 同梱のまとめの中身によらない(版を出すときに提出が入っても通る): 空のまとめに差し替える
    real = dbmod.bundled_text
    empty = contrib.dumps(contrib.community_document([], []))
    monkeypatch.setattr(dbmod, "bundled_text", lambda name: empty if name == COMMUNITY_FILE else real(name))
    root = tmp_path / "contrib"
    (root / "rtx-3070").mkdir(parents=True)
    # フォルダが無いのは指定の間違い(0 件として通さない)
    assert cli.main(["--lang", "ja", "contrib", "aggregate", str(tmp_path / "none"), "--check"]) == 1
    assert "提出のフォルダがありません" in capsys.readouterr().err
    assert cli.main(["--lang", "ja", "contrib", "aggregate", str(root), "--check"]) == 0
    out = capsys.readouterr().out
    assert "まだ入っていないもの 0 件" in out
    shutil.copy(SDXL_3070[0], root / "rtx-3070" / SDXL_3070[0].name)
    assert cli.main(["--lang", "ja", "contrib", "aggregate", str(root), "--check"]) == 1
    out = capsys.readouterr().out
    assert "まだ入っていないもの 1 件" in out and SDXL_3070[0].name in out and "--output" in out
    assert cli.main(["contrib", "aggregate", str(root), "--check", "--json"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert d["action"] == "aggregate_check" and len(d["pending"]) == 1 and d["ok"] is True
    assert cli.main(["contrib", "aggregate", str(root), "--check", "--output", str(tmp_path / "x.json")]) == 1
    # aggregate --output は使った提出の hash を残す
    out_file = tmp_path / "c.json"
    # git の履歴が無い(出どころを提出のファイルごとに数える)ので、版のまとめとしては書き出さない(#95)
    assert cli.main(["contrib", "aggregate", str(root), "--output", str(out_file)]) == 1
    assert not out_file.exists()
    assert (
        cli.main(["contrib", "aggregate", str(root), "--output", str(out_file), "--allow-file-sources"]) == 0
    )
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    h = contrib.content_hash(json.loads(SDXL_3070[0].read_text(encoding="utf-8")))
    assert doc["submissions"] == [{"sha256": h, "source": f"file:{h}"}]  # git の外なので提出ごと


# --- 根拠の段階 -----------------------------------------------------------------------------------


def _community(m: Measurement, **kw) -> Measurement:
    d = {
        **vars(m),
        "settings": m.settings,
        "origin": "community",
        "n_submissions": 3,
        "n_used": 3,
        "n_sources": 3,  # 別々の出どころ(提出を足した PR)の数(#90)。sample_count はこの数(#95)
        "n_oom": 0,
        "consistent": True,
        "vram_metric": None,
    }
    d.update(kw)
    return Measurement(**d)


def _floor(entry, s: Settings) -> float:
    """全部 GPU に載せたときの物理的な下限(重み + 固定分。作業用メモリ 0)。"""
    from genmodelfit.estimate import apply_precision, components, load_coefficients, need

    c = load_coefficients()
    return need(apply_precision(components(entry), entry, s), 0.0, c).full


def test_basis_ordering(db):
    """ほかの利用者の実測は 1 件から、両方の向きに使う(2026-10-22、llmfit に合わせた)。"""
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id and m.origin is None and usable(m)]
    assert len(maint) == 1 and maint[0].settings.width == 832
    s1536 = Settings(width=1536, height=1536)
    system = pc(12)

    def est(ms, s=s1536, sysi=system):
        return estimate(entry, sysi, s, ms)

    base = est(maint)
    assert (est([]).basis, est([]).basis_detail) == ("estimated", "estimated")
    assert (base.basis, base.basis_detail) == ("estimated", "calibrated")
    assert base.sample_count == 1 and est([]).sample_count is None
    # 大きい値: 値は実測そのもの(basis measured、点数も実測として数える)。件数を持つ
    high = _community(
        maint[0], gpu_name="NVIDIA GeForce RTX 3070", gpu_vram_gb=12.0, peak_vram_gb=base.vram_gb + 2
    )
    high.settings = Settings(width=1536, height=1536, steps=20)
    e = est([*maint, high])
    assert (e.basis, e.basis_detail, e.sample_count) == ("measured", "measured", 3)  # 作者と分けない(#108)
    assert e.vram_gb == round(high.peak_vram_gb, 2) and e.vram_gb > base.vram_gb
    assert e.measured_on.startswith("NVIDIA GeForce RTX 3070")
    assert any(f"ほかの利用者の実測(出どころ 3。中央値 {high.peak_vram_gb:.2f} GB)" in r for r in e.reasons)
    assert e.to_dict()["sample_count"] == 3
    # 重み + 固定分(物理的な下限)より小さい値: 作業用メモリの情報を持たないので、情報を持つ作者の実測を
    # 押しのけない(#151)。作者の実測が無ければ、ずらさない(値は式)
    floor = _floor(entry, s1536)
    low = _community(high, peak_vram_gb=floor - 1.0)
    e = est([*maint, low])
    assert (e.vram_gb, e.basis, e.basis_detail) == (base.vram_gb, base.basis, base.basis_detail)
    assert any("作業用メモリの情報を持たないので" in r for r in e.reasons)
    e = est([low])
    assert e.vram_gb == est([]).vram_gb and e.vram_gb > floor
    assert (e.basis, e.basis_detail, e.sample_count) == ("estimated", "estimated", None)
    assert any("作業用メモリの情報を持たず" in r for r in e.reasons)
    mid = _community(high, peak_vram_gb=round((floor + base.vram_gb) / 2, 2))  # 下限より上なら、そのまま使う
    e = est([*maint, mid])
    assert e.vram_gb == mid.peak_vram_gb < base.vram_gb and e.basis == "measured"
    # 1 件だけ・そろっていない(consistent でない)まとめも使う
    one = _community(high, n_submissions=1, n_used=1, n_sources=1, consistent=False)
    e = est([*maint, one])
    assert e.basis_detail == "measured" and e.sample_count == 1
    assert pick_measurement([one], entry, s1536, system.best_gpu) is one
    # 別の GPU・GPU の無い PC では、ほかの利用者の実測は使わない(llmfit と同じ。0.9.0)
    assert pick_measurement([one], entry, s1536) is None
    assert pick_measurement([one], entry, s1536, pc(12, "NVIDIA GeForce RTX 4090").best_gpu) is None
    # 同じ設定なら、この PC と同じ名前の GPU の実測 → 作者の実測 → ほかの利用者の実測
    same_maint = Measurement(**{**vars(maint[0]), "settings": Settings(width=1536, height=1536)})
    e = est([high, same_maint])  # この PC は RTX 3070。同じ GPU のほかの利用者の実測が先
    assert e.basis_detail == "measured" and e.sample_count == 3
    e = est([high, same_maint], sysi=pc(12, "NVIDIA GeForce RTX 4090"))  # 別の GPU なら作者の実測が先
    assert (e.basis, e.basis_detail, e.sample_count) == ("measured", "measured", 1)
    # measured_community は #108 で measured にまとめた
    assert set(BASIS_DETAILS) == {"measured", "calibrated", "estimated"}
    d = e.to_dict()  # JSON は後方互換: basis は 2 値のまま、basis_detail と sample_count を足しただけ
    assert d["basis"] in ("measured", "estimated") and d["basis_detail"] in BASIS_DETAILS


def test_community_value_is_capped_at_the_gpu_size(db):
    """GPU の総量 × 1.02 + 0.25 GiB を超える値(validate の「ありえない値」)は、見積もりでも上限までにする。"""
    entry = db.get("image_sdxl_simple")
    m = next(x for x in db.measurements if x.model_id == entry.id)
    big = _community(m, gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=24.0, peak_vram_gb=40.0)
    e = estimate(entry, pc(24, "NVIDIA GeForce RTX 4090"), Settings(width=832, height=832), [big])
    assert e.vram_gb == round(24.0 * 1.02 + 0.25, 2) and e.basis == "estimated"
    assert any("GPU の総量を超える" in r for r in e.reasons)


def _unmeasured_image(db):
    measured = {m.model_id for m in db.measurements}
    return next(
        e
        for e in db.models
        if e.modality == "image" and e.id not in measured and e.total_size_bytes and e.reviewed
    )


def test_calibrated_spread_is_bounded(db):
    """設定の違う推定へ広げるときは、実測 ÷ 式 を [0.6, 1.4] に収める(llmfit の [0.05, 3.0] に当たる)。"""
    from genmodelfit.estimate import COMMUNITY_CALIBRATION_RANGE

    assert COMMUNITY_CALIBRATION_RANGE == (0.6, 1.4)
    entry = _unmeasured_image(db)
    s512, sdef = Settings(width=512, height=512), None
    sysi = pc(80, "NVIDIA A100-SXM4-80GB")  # ほかの利用者の実測は同じ GPU の PC にだけ使う(0.9.0)
    est_m = estimate(entry, sysi, s512, []).vram_gb
    a = estimate(entry, sysi, sdef, [])
    assert a.settings["width"] == 1024  # 既定の設定は、ほかの利用者の実測の設定に変えない
    for factor, sign in ((1.8, 1), (0.2, -1)):
        m = _community(
            Measurement(
                model_id=entry.id, gpu_name="NVIDIA A100-SXM4-80GB", gpu_vram_gb=80.0, peak_vram_gb=1
            ),
            peak_vram_gb=round(est_m * factor, 2),
            settings=s512,
        )
        same = estimate(entry, sysi, s512, [m])
        other = estimate(entry, sysi, sdef, [m])
        if sign > 0:
            assert other.basis_detail == "calibrated" and other.sample_count == 3
            assert same.vram_gb == m.peak_vram_gb  # 同じ設定なら実測そのもの(上は GPU の総量まで)
            assert other.vram_gb == pytest.approx(a.vram_gb + 0.4 * est_m, abs=0.02)
        else:
            # 物理的な下限より小さい実測は作業用メモリの情報を持たないので、どの設定でもずらさない(#151)
            assert m.peak_vram_gb < _floor(entry, s512)
            assert same.vram_gb == est_m and other.vram_gb == a.vram_gb
            assert other.basis_detail == "estimated" and other.sample_count is None


def test_torch_reserved_is_a_lower_bound(db):
    """作者の torch_reserved の実測(CUDA の文脈を含まない)は「これ以上」としてだけ使う。
    ほかの利用者の実測は物理的な下限まで下げてよい(上の試験)。
    #100: 同梱の RTX 3070 の 2 件は動的 VRAM(作業用メモリだけ)なので見積もりに使わない。全部 GPU に載せた
    (offload none の)torch_reserved の実測として決まりを確かめる。"""
    for mid in ("utility_birefnet_remove_background", "3d_moge_perspective_to_mesh"):
        entry = db.get(mid)
        bundled = [m for m in db.measurements if m.model_id == mid]
        assert bundled and all(m.vram_metric == "torch_reserved" and working_only(m) for m in bundled)
        assert estimate(entry, pc(8), None, bundled).basis_detail == "estimated"  # 照合だけ(#100)
        ms = _full_rows(bundled)
        with_m = estimate(entry, pc(8), None, ms)
        without = estimate(entry, pc(8), Settings(**{k: v for k, v in with_m.settings.items()}), [])
        assert with_m.vram_gb >= without.vram_gb > ms[0].peak_vram_gb
        assert with_m.basis == "estimated" and with_m.basis_detail == "calibrated"


# --- 大きさの入力の無いテンプレート(#90) -----------------------------------------------------------

SIZELESS = ("utility_birefnet_remove_background", "utility_moge_depth_estimation")


def _sizeless_sub(mid: str, peak: float, gpu: str = "NVIDIA GeForce RTX 3070") -> contrib.Submission:
    """bench が大きさの入力の無いテンプレートで書く提出(settings に幅・高さが無い)。"""
    d = good()
    d["system"]["gpu"]["name"] = gpu
    d["measurement"].update(
        model_id=mid, peak_vram_gb=peak, settings={"precision": "fp16"}, offload="dynamic", vram_metric=None
    )  # 全体の量の実測(torch_reserved で動的 VRAM の提出は作業用メモリだけなので見積もりに使わない。#100)
    return contrib.Submission(path="-", sha256=contrib.content_hash(d), data=d)


def _full_rows(ms: list[Measurement]) -> list[Measurement]:
    """作業用メモリだけの実測(動的 VRAM の torch_reserved。#100)を、全部 GPU に載せた実測(offload none)に
    書き換えたもの。#100 より前の決まり(作者の torch_reserved は下限)を確かめる試験で使う。"""
    return [Measurement(**{**vars(m), "offload": "none"}) if working_only(m) else m for m in ms]


def _own(db) -> list[Measurement]:
    """同梱の作者の実測だけ(同梱のほかの利用者のまとめの中身によらない試験にする)。動的 VRAM の
    torch_reserved の行(#100 から照合だけ)は、全部 GPU に載せた実測に書き換える(_full_rows)。"""
    return _full_rows([m for m in db.measurements if m.origin != "community"])


def test_no_size_input_is_decided_only_by_the_hand_table(db):
    from genmodelfit.estimate import NO_SIZE_INPUT, no_size_input

    assert no_size_input(db.get("utility_birefnet_remove_background"))
    assert no_size_input(db.get("utility_moge_depth_estimation"))
    # 3d は大きさを持たない種類、表に無いモデルは今までどおり
    assert not no_size_input(db.get("3d_moge_perspective_to_mesh"))
    assert not no_size_input(db.get("image_sdxl_simple"))
    # 表の項目はどれも画像・動画のモデルで、根拠の文がある
    for mid, why in NO_SIZE_INPUT.items():
        e = db.get(mid)
        assert e is not None and e.modality in ("image", "video") and why, mid
    # 同梱の作者の、標準で大きさの無い画像・動画の実測は、どれも表にある
    for m in _own(db):
        e = db.get(m.model_id)
        if (
            e
            and e.modality in ("image", "video")
            and m.standard
            and not (m.settings.width or m.settings.height)
        ):
            assert m.model_id in NO_SIZE_INPUT, m.model_id


def test_one_sizeless_community_row_does_not_change_a_sized_model(db):
    """チェッカーの再現(flux_schnell): 大きさの入力のあるモデルに大きさの無い提出が 1 件あっても、判定の設定は
    既定の大きさのまま、段階も上がらない(大きさの入力の有無は手の表だけで決める)。"""
    e = db.get("flux_schnell")
    d = good()
    d["system"]["gpu"].update(name="NVIDIA GeForce RTX 3060", vram_gb=12.0)
    d["measurement"].update(model_id=e.id, peak_vram_gb=6.0, settings={"steps": 4}, note="x")
    cm = [
        Measurement.from_dict(r)
        for r in contrib.aggregate([contrib.Submission("-", contrib.content_hash(d), d)])
    ]
    system = pc(12, "NVIDIA GeForce RTX 3060")
    before = estimate(e, system, None, _own(db))
    after = estimate(e, system, None, [*_own(db), *cm])
    assert after.settings["width"] == before.settings["width"] == 1024
    assert (after.fit, after.basis, after.fit_from) == (before.fit, before.basis, before.fit_from)
    assert after.vram_gb >= before.vram_gb - 1e-9 or not any("ほかの利用者の実測" in r for r in after.reasons)
    # validate は settings_missing を出す(提出そのものを根拠に数えない)
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert "settings_missing" in {w["code"] for w in r.warnings}


def test_bundled_sizeless_author_measurements_match_without_a_size(db):
    """同梱の作者の実測(RTX 3070、大きさ無し)が、指定の無い判定と同じ設定になって使われる。作者の
    torch_reserved は下限としてだけ使う(test_torch_reserved_is_a_lower_bound)ので、値は式のまま。"""
    from genmodelfit.estimate import _prepare, _settings_match, load_coefficients

    for mid in ("utility_birefnet_remove_background", "3d_moge_perspective_to_mesh"):
        entry = db.get(mid)
        system = pc(8)
        est = estimate(entry, system, None, _own(db))
        s = Settings(**est.settings)
        assert s.width is None and s.height is None and s.batch == 1, est.settings
        prep = _prepare(entry, _own(db), load_coefficients(), s, system.best_gpu)
        m = prep.measurement
        assert (
            m is not None
            and m.gpu_name == "NVIDIA GeForce RTX 3070"
            and _settings_match(m.settings, s, entry)
        )
        assert prep.clamped and est.basis_detail == "calibrated" and est.fit == "perfect"
        # 値は今までどおり(作業用メモリは種類の既定の大きさで見積もる)
        if entry.modality == "image":
            at1024 = estimate(entry, system, Settings(width=1024, height=1024), _own(db))
            assert at1024.settings["width"] == 1024 and at1024.vram_gb == est.vram_gb
            assert estimate(entry, system, None, []).vram_gb == est.vram_gb


def test_sizeless_community_measurement_is_used_on_the_same_gpu(db):
    """#90 の通し試験: 大きさの無い提出(MoGe 深度推定。PR #92)が、同じ GPU の PC の判定に使われる
    (BiRefNet は同じ GPU の作者の実測が先に選ばれる。pick_measurement の並び)。"""
    for mid in ("utility_moge_depth_estimation",):
        entry = db.get(mid)
        before = estimate(entry, pc(8), None, _own(db))
        peak = round(_floor(entry, Settings(precision="fp16")) + 0.2, 2)
        cm = [Measurement.from_dict(r) for r in contrib.aggregate([_sizeless_sub(mid, peak)])]
        after = estimate(entry, pc(8), None, [*_own(db), *cm])
        assert "width" not in after.settings
        assert after.basis == "measured" and after.basis_detail == "measured", (mid, after)
        assert any("ほかの利用者の実測" in r for r in after.reasons)
        assert after.measured_on == "NVIDIA GeForce RTX 3070 (8 GB)" and after.sample_count == 1
        assert after.vram_gb == peak and after.vram_gb < before.vram_gb
        # 別の GPU の PC の判定は変えない
        other = pc(24, "NVIDIA GeForce RTX 4090")
        a, b = (
            estimate(entry, other, None, _own(db)),
            estimate(entry, other, None, [*_own(db), *cm]),
        )
        assert (a.vram_gb, a.basis, a.fit) == (b.vram_gb, b.basis, b.fit) and b.basis == "estimated"
        # 大きさを指定したときは今までどおり(実測とは違う設定 = 補正した推定)
        sized = estimate(entry, pc(8), Settings(width=1024, height=1024), [*_own(db), *cm])
        assert sized.basis == "estimated" and sized.settings["width"] == 1024


def test_settings_missing_only_for_templates_with_a_size_input(db):
    for mid in SIZELESS:
        r = contrib.validate_submission(_sizeless_sub(mid, 1.5).data, db, today=TODAY, check_filename=False)
        assert r.ok and "settings_missing" not in {w["code"] for w in r.warnings}, (mid, r.warnings)
    d = good()  # image_sdxl_simple は作者の実測に大きさがある(大きさの入力のあるテンプレート)
    d["measurement"]["settings"] = {"steps": 20}
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert "settings_missing" in {w["code"] for w in r.warnings}
    d["measurement"]["settings"] = {"width": 832, "steps": 20}  # 片方だけも
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert "settings_missing" in {w["code"] for w in r.warnings}
    # 表のモデルで batch が 1 でない(0.10.1 の bench が MoGe の batch_size を枚数と記録した)ときは確認を出す
    sub = _sizeless_sub("utility_moge_depth_estimation", 1.5).data
    sub["measurement"]["settings"]["batch"] = 4
    r = contrib.validate_submission(sub, db, today=TODAY, check_filename=False)
    assert r.ok and "batch_sizeless" in {v["code"] for v in r.notices}
    r = contrib.validate_submission(
        _sizeless_sub("utility_moge_depth_estimation", 1.5).data, db, today=TODAY, check_filename=False
    )
    assert "batch_sizeless" not in {v["code"] for v in r.notices}


def test_unmeasured_models_keep_the_default_size(db):
    """実測の無い画像・動画のモデルの判定の設定と値は今までどおり(種類の既定の大きさ)。"""
    from genmodelfit.estimate import NO_SIZE_INPUT

    measured = {m.model_id for m in db.measurements}
    n = 0
    for e in db.models:
        if e.modality not in ("image", "video") or e.id in measured:
            continue
        est = estimate(e, pc(8), None, db.measurements)
        if est.unknown_size:
            continue
        if e.id in NO_SIZE_INPUT:  # 手の表のモデルは大きさを持たないが、値は既定の大きさと同じ
            at = estimate(e, pc(8), Settings(width=1024, height=1024), db.measurements)
            assert "width" not in est.settings and est.vram_gb == at.vram_gb, e.id
            continue
        assert est.settings["width"] and est.settings["height"], e.id
        n += 1
    assert n > 100


ORDER = ["too_tight", "marginal", "good", "perfect", "unknown"]


def _poison(db, peaks, *, offload="none", gpu=("NVIDIA GeForce RTX 4090", 24.0, "ada", "8.9")):
    """攻撃の台本 poison2.py: 全モデルに、判定の設定で小さな値の提出を 2 件ずつ作る(検査は見ない)。"""
    out = []
    for t in db.models:
        e0 = estimate(t, pc(24), None, db.measurements)
        if e0.unknown_size:
            continue
        sett = {k: v for k, v in e0.settings.items() if k != "batch" and v is not None}
        subs = []
        for i, v in enumerate(peaks):
            d = good()
            d["system"]["gpu"].update(name=gpu[0], vram_gb=gpu[1], arch=gpu[2], compute_capability=gpu[3])
            d["measurement"].update(
                model_id=t.id, settings=sett, peak_vram_gb=v, offload=offload, note=f"x{i}"
            )
            subs.append(contrib.Submission(path=f"p{i}", sha256=contrib.content_hash(d), data=d))
        out.append((t, subs))
    return out


def test_poisoned_low_submissions_stop_at_the_physical_floor(db):
    """書き換えた小さな値の提出 2 件(以前の決まりで防いでいた攻撃)。今の決まりでは必要量を下げるが、
    全部載せの値は重み + 固定分より下げない。validate は失敗にせず、確認(below_weights・deviation)を出す
    (マージするメンテナが見る。docs/accuracy.md「残る危険」)。"""
    poisoned = _poison(db, (0.5, 0.505))
    assert len(poisoned) > 250
    noticed = 0
    for t, subs in poisoned:
        r = contrib.validate_submission(subs[0].data, db, today=TODAY, check_filename=False)
        assert r.ok
        noticed += bool({v["code"] for v in r.notices} & {"below_weights", "deviation"})
        cm = [Measurement.from_dict(x) for x in contrib.aggregate(subs)]
        for v in (8, 24):
            a = estimate(t, pc(v), None, db.measurements)
            b = estimate(t, pc(v), None, [*db.measurements, *cm])
            s = Settings(**b.settings)
            assert b.vram_gb >= round(_floor(t, s), 2) - 0.011, (t.id, v)
            assert b.vram_gb <= a.vram_gb + 1e-9, (t.id, v)
    assert noticed == len(poisoned)  # どれもマージする人に知らせる


def _upward(db, factor, models):
    out = []
    for t in models:
        e0 = estimate(t, pc(24), None, db.measurements)
        sett = {k: v for k, v in e0.settings.items() if k != "batch" and v is not None}
        subs = []
        for i, eps in enumerate((0, 0.005)):
            d = good()
            d["system"]["gpu"].update(
                name="NVIDIA GeForce RTX 4090", vram_gb=24.0, arch="ada", compute_capability="8.9"
            )
            v = min(24.0, round(e0.vram_gb * factor * (1 + eps), 2))
            # 全部 GPU に載せた実測(動的 VRAM の torch_reserved は作業用メモリだけ。#100)
            d["measurement"].update(
                model_id=t.id, settings=sett, peak_vram_gb=v, note=f"x{i}", offload="none"
            )
            subs.append(contrib.Submission(path=f"p{i}", sha256=contrib.content_hash(d), data=d))
        out.append((t, subs))
    return out


def _sized(db):
    return [t for t in db.models if not estimate(t, pc(24), None, db.measurements).unknown_size]


def test_upward_submissions_raise_needs_and_are_noticed(db):
    """+39% の提出 2 件: 同じ設定では値をそのまま使い(必要量を上げる)、validate は失敗にしない。
    よくある PC で段階を変える提出は確認(fit_change)としてマージする人に知らせる。"""
    measured = {m.model_id for m in db.measurements}
    # #100: 余裕のある GPU(RTX 4090)の山からの正のずらしは全部載せにだけ足すので、段階が変わるのは全部載せで
    # 帯をまたぐモデルだけ。まばらに選ぶと 0 件になりうるので 4 件に 1 件
    models = [t for t in _sized(db) if t.id not in measured][::4]
    changed = 0
    for t, subs in _upward(db, 1.39, models):
        cm = [Measurement.from_dict(x) for x in contrib.aggregate(subs)]
        # ほかの利用者の実測は同じ GPU(RTX 4090)の PC にだけ使う。別の GPU の PC は動かさない(0.9.0)
        assert estimate(t, pc(24), None, [*db.measurements, *cm]) == estimate(
            t, pc(24), None, db.measurements
        )
        b = estimate(t, pc(24, "NVIDIA GeForce RTX 4090"), None, [*db.measurements, *cm])
        # 張り付いた値(上限で切り詰めた)は値が実測ではないので calibrated
        # (#108 で measured_community をまとめた)
        assert b.basis_detail == ("measured" if b.basis == "measured" else "calibrated"), t.id
        if cm[0].peak_vram_gb < 0.9 * 24:  # 張り付いた値は「これ以上」としてだけ使う
            assert b.vram_gb == cm[0].peak_vram_gb, t.id
        assert b.sample_count == 2
        r = contrib.validate_submission(subs[0].data, db, today=TODAY, check_filename=False)
        assert r.ok
        _rel, changes = contrib.community_effect(t, contrib._own_measurements(db), cm)
        if changes:
            changed += 1
            assert "fit_change" in {v["code"] for v in r.notices}, t.id
    assert changed > 0


def test_cli_info_and_table_show_community_counts(db, capsys, tmp_path, monkeypatch):
    """理由・info・JSON に件数を出す(info の fit の行は measured n=3)。表の VRAM の印は作者の実測と同じ m
    (#108。作者・ほかの利用者を印で分けない)。"""
    from genmodelfit import hardware

    # この PC の GPU は実測と同じ A100(ほかの利用者の実測は同じ GPU の PC にだけ使う。0.9.0)
    # --vram で上書きした総量は実測の GPU の VRAM と合わせる(VRAM の違う GPU の実測は使わない。0.9.1)
    a100 = Gpu(name="NVIDIA A100-SXM4-80GB", vendor="nvidia", vram_total_gb=80.0, backend="cuda")
    monkeypatch.setattr(hardware, "detect_gpus", lambda **kw: ([a100], []))
    entry = _unmeasured_image(db)
    a = estimate(entry, pc(16), None, db.measurements)
    sett = Settings(**{k: v for k, v in a.settings.items() if k != "batch"})
    row = _community(
        Measurement(model_id=entry.id, gpu_name="NVIDIA A100-SXM4-80GB", gpu_vram_gb=80.0, peak_vram_gb=1),
        peak_vram_gb=round(a.vram_gb * 1.1, 2),
        settings=sett,
    )
    meas = tmp_path / "cache" / "measurements"
    meas.mkdir(parents=True)
    (meas / "community.json").write_text(
        json.dumps({"measurements": [vars(row) | {"settings": vars(row.settings)}]}), encoding="utf-8"
    )
    assert cli.main(["info", entry.id, "--vram", "80", "--ram", "32", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "measured n=3" in out and "ほかの利用者の実測(出どころ 3。中央値" in out
    assert cli.main(["info", entry.id, "--vram", "80", "--ram", "32", "--no-cache", "--json"]) == 0
    e = json.loads(capsys.readouterr().out)["estimate"]
    assert (e["basis_detail"], e["sample_count"]) == ("measured", 3)
    args = ["fit", "--vram", "80", "--ram", "32", "--no-cache", "--all-workflows", "--min-fit", "too_tight"]
    assert cli.main([*args, "--modality", "image", "-n", "500"]) == 0
    out = capsys.readouterr().out
    assert "VRAM の c" not in out and "m = 実測" in out  # 凡例(c は #108 で無くした)
    assert re.search(r" \d+\.\dm ", out) and not re.search(r" \d+\.\dc ", out), out  # 表の VRAM は m


# --- NVIDIA 以外の GPU の段階(同じ GPU の実測があれば good の上限を外す) -----------------------------

FAKE_AMD = "AMD Radeon RX 9999 XT"  # 架空の GPU(試験用)


def amd_pc(vram: float = 24.0, name: str = FAKE_AMD) -> SystemInfo:
    g = Gpu(
        name=name,
        vendor="amd",
        vram_total_gb=vram,
        backend="rocm",
        compute_dtype="fp16",
        bf16=True,
        fp8=False,
        gfx_target="gfx1201",
    )
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=64, ram_available_gb=64, gpus=[g])


def test_non_nvidia_has_no_good_cap_and_measurements_only_raise(db):
    """#108: NVIDIA 以外の GPU にも段階の上限は無い(NVIDIA と同じ基準)。同じ GPU の実測は段階を上げるだけで、
    下げない(#95 の own の分岐を外した)。"""
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id]
    a = estimate(entry, amd_pc(), None, maint)
    nv = estimate(entry, pc(24.0, "NVIDIA GeForce RTX 3090"), None, maint)
    assert a.fit == nv.fit == "perfect"
    assert not any("Good まで" in r or "上限(good)" in r for r in a.reasons)
    sett = Settings(**{k: v for k, v in a.settings.items() if k != "batch"})
    one = _community(
        maint[0], gpu_name=FAKE_AMD, gpu_vram_gb=24.0, peak_vram_gb=8.2, settings=sett, n_used=1, n_sources=1
    )
    b = estimate(entry, amd_pc(), None, [*maint, one])
    assert (b.fit, b.basis_detail, b.sample_count) == ("perfect", "measured", 1)
    amd = _community(one, n_used=2, n_sources=2)
    b = estimate(entry, amd_pc(), None, [*maint, amd])
    assert (b.fit, b.basis_detail, b.sample_count) == ("perfect", "measured", 2)
    # 実測で決まる段階が見積もりより低い(上限に張り付いた実測は good まで)ときも、下げない
    pinned = _community(amd, gpu_vram_gb=24.0, peak_vram_gb=12.0, offload="lowvram")
    c = estimate(entry, amd_pc(), None, [*maint, pinned])
    assert c.fit == "perfect" and c.fit_from == "estimate"
    for kw in ({"gpu_name": "AMD Radeon RX 7900 XTX"}, {"n_oom": 1}):
        assert estimate(entry, amd_pc(), None, [*maint, _community(amd, **kw)]).fit == "perfect", kw
    c = estimate(entry, amd_pc(), None, [*maint, _community(amd, n_oom=1)])
    assert any("OOM の報告 1 件" in r for r in c.reasons)


def test_community_measurement_on_the_same_gpu_sets_the_level(db):
    """#26 の「実測で段階を決める」は、同じ GPU のほかの利用者の実測にも使う(作者の実測と同じ扱い)。
    #95: 別々の出どころが 2 つ以上そろったときだけ、段階に使う量は max(ピーク + 文脈の分, 重み + 固定分)。"""
    from genmodelfit.estimate import gpu_precision, measured_level

    entry = db.get("image_sdxl_simple")
    a = estimate(entry, pc(16, "NVIDIA GeForce RTX 4060 Ti"), None, [])
    sett = Settings(**{k: v for k, v in a.settings.items() if k != "batch"})
    m = _community(
        Measurement(
            model_id=entry.id, gpu_name="NVIDIA GeForce RTX 4060 Ti", gpu_vram_gb=16.0, peak_vram_gb=1
        ),
        peak_vram_gb=9.0,
        settings=sett,
    )
    s = Settings(**a.settings)
    for name, expect in (
        ("NVIDIA GeForce RTX 4060 Ti", "perfect"),
        ("NVIDIA GeForce RTX 4070 Ti SUPER", None),
    ):
        sysi = pc(16, name)
        lvl, _why = measured_level([m], entry, s, sysi, gpu_precision(entry, sysi, s), 0.0)
        assert (lvl.level if lvl else None) == expect, name
    e = estimate(entry, pc(16, "NVIDIA GeForce RTX 4060 Ti"), None, [m])
    # 見積もりも perfect(実測で段階を上げるのは、見積もりより良いときだけ)
    assert (e.fit, e.run_mode) == ("perfect", "gpu")
    # Wan 2.2 5B の重み + 固定分(約 18.4 GB)は 16 GB に収まらないので、小さな実測でも段階は決めない
    wan = db.get("video_wan2_2_5B_ti2v")
    w = estimate(wan, pc(16, "NVIDIA GeForce RTX 4060 Ti"), None, [])
    wm = _community(
        m,
        model_id=wan.id,
        peak_vram_gb=15.2,
        offload="dynamic",
        settings=Settings(**{k: v for k, v in w.settings.items() if k != "batch"}),
    )
    assert estimate(wan, pc(16, "NVIDIA GeForce RTX 4060 Ti"), None, [wm]).fit_from == "estimate"


def test_cli_info_json_has_basis_detail(capsys):
    args = ["info", "image_sdxl_simple", "--vram", "16", "--ram", "32", "--no-cache", "--json"]
    assert cli.main([*args, "--width", "832", "--height", "832"]) == 0  # 実測の設定
    e = json.loads(capsys.readouterr().out)["estimate"]
    assert e["basis"] == "measured" and e["basis_detail"] == "measured"
    assert cli.main(args) == 0  # 指定しなければ既定の設定(1024x1024)。実測は値をずらすだけ(#95)
    e = json.loads(capsys.readouterr().out)["estimate"]
    assert e["basis"] == "estimated" and e["basis_detail"] == "calibrated"


# --- 校正への取り込み ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_report(db):
    subs, _ = contrib.load_dir(FIX, db)
    rows = contrib.aggregate(subs)
    return rows, contrib.accuracy_report(contrib.report_db(db, rows), threshold_pct=10.0)


def test_report_on_fixtures(fixture_report):
    _rows, rep = fixture_report
    check = [x for x in rep["check"] if x["origin"] == "community"]
    assert [(x["model_id"], x["gpu_name"]) for x in check] == [
        ("image_sdxl_simple", "NVIDIA GeForce RTX 3070")
    ]
    assert check[0]["n_submissions"] == 3 and check[0]["measured_gb"] == 6.96
    assert check[0]["quantity"] == "work"  # 動的 VRAM の torch_reserved は作業用メモリと比べる(#100)
    assert rep["n_fit"] == 9  # torch_reserved の提出は係数の合わせに入れない(照合だけ)
    # 閾値 10% にすると、SDXL の作者の実測の LOO(+12.5%。#100 の段ごとの式)が引っかかる。
    # 作業用メモリだけの照合と作者の照合(RTX 3070)は判定に入れない
    assert rep["breach"] is True
    assert {(b["kind"], b["model_id"]) for b in rep["breaches"]} == {("loo", "image_sdxl_simple")}
    assert set(rep["by_modality"]) >= {"image", "3d"}
    assert rep["by_modality"]["image"]["evaluable"] is True


def test_calibration_includes_consistent_community_rows_of_the_same_metric(db):
    maint = parse_measurements(json.loads(bundled_text("measurements.json")))
    base = next(m for m in maint if m.model_id == "image_sdxl_simple" and m.vram_metric is None)
    same = _community(
        base, gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=24, peak_vram_gb=8.3, vram_metric=None
    )
    same.settings = Settings(width=1024, height=1024)
    loose = _community(same, consistent=False, peak_vram_gb=30.0)
    other = _community(same, vram_metric="torch_reserved")
    test_db = Database(models=db.models, measurements=[*maint, same, loose, other])
    rows = build_rows(test_db)
    assert len(rows) == 10 and sum(r.m.origin == "community" for r in rows) == 1
    assert sum(r.m.origin == "community" for r in build_check_rows(test_db)) == 1
    result = calibrate(test_db, date="2026-10-21")
    assert result["n"] == 10
    assert [x["origin"] for x in result["loo"]].count("community") == 1
    assert {x["origin"] for x in result["check"]} == {"maintainer", "community"}


# --- CLI の形 -------------------------------------------------------------------------------------


def test_cli_validate_json_and_exit_codes(tmp_path, capsys):
    assert cli.main(["contrib", "validate", str(FIX), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d) == {
        "genmodelfit",
        "command",
        "action",
        "ok",
        "files",
        "valid",
        "invalid",
        "notices",
        "warnings",
        "reports",
    }
    assert d["command"] == "contrib" and d["action"] == "validate" and d["files"] == 4 and d["ok"] is True
    assert set(d["reports"][0]) == {
        "path",
        "ok",
        "errors",
        "warnings",
        "notices",
        "sha256",
        "suggested_path",
        "model_id",
        "gpu_name",
    }
    bad = tmp_path / "rtx-3070" / "x.json"
    bad.parent.mkdir()
    b = good()
    b["measurement"]["note"] = r"C:\Users\bob"
    bad.write_text(json.dumps(b), encoding="utf-8")
    assert cli.main(["contrib", "validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "NG" in out and "privacy" in out and "filename" in out
    assert cli.main(["contrib", "validate", str(FIX), "--markdown"]) == 0
    assert "| 結果 | ファイル |" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:  # ファイルを渡さないのは使い方の誤り
        cli.main(["contrib", "validate"])
    assert e.value.code == 2


def test_cli_validate_strict_turns_warnings_into_failure(tmp_path, capsys):
    d = good()
    d["measurement"]["standard"] = False
    p = tmp_path / contrib.suggested_path(d).split("/", 2)[2]
    p.parent.mkdir(parents=True)
    p.write_text(contrib.dumps(d), encoding="utf-8")
    assert cli.main(["contrib", "validate", str(p)]) == 0
    assert cli.main(["contrib", "validate", str(p), "--strict"]) == 1
    capsys.readouterr()


def test_cli_show(capsys):
    assert cli.main(["contrib", "show", str(SDXL_3070[0])]) == 0
    out = capsys.readouterr().out
    assert "これがファイルの中身のすべてです" in out and "=== 個人の情報の点検 ===" in out
    assert "NVIDIA GeForce RTX 3070" in out and "[NG]" not in out
    assert cli.main(["contrib", "show", str(SDXL_3070[0]), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d) == {"genmodelfit", "command", "action", "file", "ok", "submission", "checklist", "report"}
    assert [c["id"] for c in d["checklist"]] == [
        "known_fields",
        "paths",
        "host_user",
        "device_ids",
        "network",
        "date_only",
        "note_reviewed",
    ]


def test_cli_show_flags_problems(tmp_path, capsys):
    d = good()
    d["system"]["hostname"] = "DESKTOP-ABC1234"
    p = tmp_path / "x.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert cli.main(["contrib", "show", str(p)]) == 1
    out = capsys.readouterr().out
    assert "[NG] 決まった項目だけ" in out and "[NG] PC の名前" in out


def test_cli_make_from_bench_file(tmp_path, capsys):
    m = Measurement(
        model_id="image_sdxl_simple",
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        peak_vram_gb=6.5,
        settings=Settings(width=832, height=832, steps=20),
        seconds=40.0,
        driver="566.03",
        comfyui_version="0.40.0",
        date="2026-10-20",
        vram_metric="torch_reserved",
        note=r"保存先 C:\Users\carol\out",
    )
    src = tmp_path / "bench.json"
    src.write_text(json.dumps({"measurements": [vars(m) | {"settings": vars(m.settings)}]}), encoding="utf-8")
    out = tmp_path / "data" / "contrib"
    assert (
        cli.main(["contrib", "make", "--from", str(src), "--output", str(out), "--no-pc-info", "--json"]) == 0
    )
    d = json.loads(capsys.readouterr().out)
    assert d["action"] == "make" and len(d["submissions"]) == 1
    s = d["submissions"][0]
    assert set(s) == {"saved_to", "path_in_repo", "submission"}
    saved = Path(s["saved_to"])
    assert saved.relative_to(out).as_posix() == s["path_in_repo"].split("/", 2)[2]
    assert "carol" not in saved.read_text(encoding="utf-8")
    assert cli.main(["contrib", "validate", str(out)]) == 0
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.main(["contrib", "make", "--from", str(bad)]) == 1


def test_cli_aggregate_json_report_and_breach(tmp_path, capsys):
    # git の外に写して数える(リポジトリの中では、同じコミットで足した 3 件が 1 つの出どころになる。#90)
    fix = tmp_path / "fix"
    shutil.copytree(FIX, fix)
    out = tmp_path / "community.json"
    args = ["contrib", "aggregate", str(fix), "--output", str(out), "--json", "--allow-file-sources"]
    assert cli.main(args) == 0
    d = json.loads(capsys.readouterr().out)
    keys = {
        "action",
        "dir",
        "files",
        "valid",
        "invalid",
        "notices",
        "submissions_used",
        "groups",
        "consistent",
    }
    assert keys <= set(d)
    assert (d["files"], d["groups"], d["consistent"], d["sources_from"]) == (4, 2, 1, "files")
    assert json.loads(out.read_text(encoding="utf-8")) == d["community"]
    assert "report" not in d
    assert (
        cli.main(["contrib", "aggregate", str(fix), "--report", "--threshold", "10", "--fail-on-breach"]) == 3
    )
    text = capsys.readouterr().out
    assert "警告: 誤差が閾値 10% を超えた" in text and "| 種類 | LOO 件数 |" in text
    assert "### ほかの利用者の実測で変わる段階" in text
    assert cli.main(["contrib", "aggregate", str(fix), "--report", "--json"]) == 0  # 既定の 25% は超えない
    rep = json.loads(capsys.readouterr().out)["report"]
    assert rep["breach"] is False and rep["threshold_pct"] == 25.0
    assert {"breach", "breaches", "by_modality", "summary", "loo", "check", "n_fit", "fit_changes"} <= set(
        rep
    )
    assert rep["fit_changes"] == []  # 試験用の提出は段階を変えない


def test_cli_aggregate_missing_dir_and_empty_output(tmp_path, capsys):
    out = tmp_path / "community.json"
    out.write_text("keep", encoding="utf-8")
    assert cli.main(["contrib", "aggregate", str(tmp_path / "none"), "--output", str(out)]) == 1
    assert "フォルダがありません" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["contrib", "aggregate", str(empty), "--output", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "keep"  # 空のまとめで上書きしない
    assert (
        cli.main(["contrib", "aggregate", str(empty), "--output", str(out), "--allow-empty", "--json"]) == 0
    )
    d = json.loads(capsys.readouterr().out)
    assert d["groups"] == 0 and json.loads(out.read_text(encoding="utf-8"))["measurements"] == []


# --- ライブラリは何も表示しない ----------------------------------------------------------------------


def test_library_writes_nothing(tmp_path, capfd, db):
    subs, _reports = contrib.load_dir(FIX, db)
    rows = contrib.aggregate(subs)
    contrib.write_community(rows, tmp_path / "c.json")
    contrib.validate_files([*GOOD, tmp_path / "missing.json"], db)
    contrib.privacy_checklist(good())
    contrib.share_urls(good(), contrib.dumps(good()))
    contrib.share_check(contrib.privacy_checklist(good()), contrib.validate_submission(good(), db))
    contrib.bundled_status(FIX, contrib.community_document(rows, subs), db)
    m = subs[0].measurement
    contrib.write_submission(contrib.build_submission(m), tmp_path / "s" / "x.json")
    copy.deepcopy(rows)
    shutil.rmtree(tmp_path / "s")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def outside_text(job) -> str:
    return next(s for s in job["steps"] if "data/contrib の外" in s.get("name", ""))["run"]


def test_contrib_workflow_is_read_only_and_uses_base_tool():
    import yaml

    root = Path(__file__).parent.parent
    doc = yaml.safe_load((root / ".github" / "workflows" / "contrib.yml").read_text(encoding="utf-8"))
    assert set(doc[True]) == {"pull_request"}  # pull_request_target は使わない(YAML 1.1 では on が True)
    assert doc[True]["pull_request"]["paths"] == ["data/contrib/**"]
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["validate"]
    assert job["permissions"] == {"contents": "read"}
    text = json.dumps(doc)
    assert "secrets." not in text
    checkouts = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout")]
    assert all(s["with"]["persist-credentials"] is False for s in checkouts)
    tool = next(s for s in checkouts if s["with"].get("path") == "tool")
    assert (
        tool["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    )  # 検査の道具は PR の側から入れない
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "contrib validate pr/data/contrib --markdown" in runs and "GITHUB_STEP_SUMMARY" in runs
    assert "contrib aggregate pr/data/contrib --report" in runs
    # actions はコミットの SHA で固定する
    import re

    for s in job["steps"]:
        if "uses" in s:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
    # 提出から来る文字はログに出さない(まとめにだけ書く)。genmodelfit の間はワークフローのコマンドを止める
    for s in job["steps"]:
        if "genmodelfit contrib" in s.get("run", ""):
            assert "::stop-commands::" in s["run"] and "cat " not in s["run"]
            assert "--reviewed" not in s["run"] and "--allow-review" not in s["run"]  # 2026-10-22 に外した
            assert "SHOW_ERR" in s["run"]  # 標準エラーも無害にしてまとめに書く
    assert "md_cell" in doc["env"]["SHOW_ERR"]
    assert "REVIEWED" not in json.dumps(doc)
    outside = next(s for s in job["steps"] if "data/contrib の外" in s.get("name", ""))
    assert "exit 1" in outside["run"]  # data/contrib の外を変えた PR は失敗


def test_torch_reserved_measurement_sets_perfect_with_the_context_added(db):
    """bench の提出(torch_reserved)は文脈の分(0.5 GiB)を足した値で帯を決め、perfect の根拠にもなる(0.9.0)。
    足した値が 60% を超えれば good。上限に張り付いた実測は今までどおり perfect にしない。"""
    from genmodelfit.estimate import TORCH_RESERVED_CONTEXT_GB, gpu_precision, measured_level

    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id]
    a = estimate(entry, amd_pc(), None, maint)
    sett = Settings(**{k: v for k, v in a.settings.items() if k != "batch"})
    tr = _community(
        maint[0],
        gpu_name=FAKE_AMD,
        gpu_vram_gb=24.0,
        peak_vram_gb=8.2,
        settings=sett,
        n_used=2,
        n_sources=2,  # 段階を決めるのは別々の出どころ 2 つ以上(#95)
        vram_metric="torch_reserved",
        gpu_backend="rocm",
        offload="none",  # 全部 GPU に載せた実測(動的 VRAM の torch_reserved は作業用メモリだけ。#100)
    )
    b = estimate(entry, amd_pc(), None, [*maint, tr])
    assert b.fit == "perfect"  # NVIDIA 以外にも上限は無い(#108)
    pc = amd_pc()
    band, _why = measured_level([tr], entry, sett, pc, gpu_precision(entry, pc, sett), 0.0)
    assert band is not None and band.level == "perfect" and band.amount_gb == 8.7
    assert f"文脈などの {TORCH_RESERVED_CONTEXT_GB:g} GB を足した" in band.line and "最大 8.7 GB" in band.line
    # 14.0 + 0.5 = 14.5 GB は 24 GB の 60.4%: 足さなければ perfect(58.3%)だが、足すと good
    edge = _community(tr, peak_vram_gb=14.0, vram_metric="torch_reserved")
    band, _why = measured_level([edge], entry, sett, pc, gpu_precision(entry, pc, sett), 0.0)
    assert band is not None and band.level == "good"
    # 見積もりの必要量も同じ GPU の実測の値(14.0 GB + ずらし)になるので good(実測で段階を下げたのではない)
    assert estimate(entry, amd_pc(), None, [*maint, edge]).fit == "good"
    pinned = _community(tr, gpu_vram_gb=8.0, peak_vram_gb=7.9, vram_metric="torch_reserved")
    assert estimate(entry, amd_pc(8.0), None, [*maint, pinned]).fit != "perfect"


def test_measurement_vendor_prefers_the_recorded_backend(db):
    """Apple の MPS の実測は GPU 名が "mps"。名前から推すと NVIDIA になるので、
    backend の記録を先に見る(0.9.0)。"""
    from genmodelfit.estimate import gpu_precision, measured_level, measurement_vendor

    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id]
    mps = Measurement(
        **{
            **vars(maint[0]),
            "gpu_name": "mps",
            "gpu_backend": "mps",
            "gpu_vram_gb": 21.0,
            "peak_vram_gb": 8.0,
            "vram_metric": "torch_reserved",
            "offload": "none",  # 動的 VRAM の torch_reserved は作業用メモリだけ(#100)
        }
    )
    assert measurement_vendor(mps) == "apple"
    # 記録が無ければ名前で推す。古い "mps" の実測も apple(会社としてだけ使う)
    assert measurement_vendor(Measurement(**{**vars(mps), "gpu_backend": None})) == "apple"
    other = Measurement(**{**vars(mps), "gpu_name": "Some GPU", "gpu_backend": None})
    assert measurement_vendor(other) == "nvidia"
    rocm = Measurement(**{**vars(mps), "gpu_name": "Some GPU", "gpu_backend": "rocm"})
    assert measurement_vendor(rocm) == "amd"
    # NVIDIA の PC では、Apple の実測は「別の会社の実測」(補正だけ。段階の根拠にしない)
    nv = SystemInfo(
        os="test",
        cpu="test",
        cpu_cores=8,
        ram_total_gb=64,
        ram_available_gb=64,
        gpus=[
            Gpu(
                name="NVIDIA GeForce RTX 4090",
                vendor="nvidia",
                vram_total_gb=24.0,
                backend="cuda",
                compute_dtype="bf16",
                bf16=True,
                fp8=True,
            )
        ],
    )
    s = Settings(**{k: v for k, v in vars(mps.settings).items()})
    e = estimate(entry, nv, s, [mps])
    assert e.basis_detail == "calibrated" and e.fit_from != "measurement"
    assert measured_level([mps, mps], entry, s, nv, gpu_precision(entry, nv, s), 0.0)[0] is None
    # Apple の PC では、同じ GPU(チップ名)の実測が 2 つそろえば段階の根拠になる(#95)。
    # 名前が "mps" の古い実測はどのチップか分からないので、同じ GPU とみなさない
    apple = SystemInfo(
        os="test",
        cpu="test",
        cpu_cores=8,
        ram_total_gb=32,
        ram_available_gb=32,
        gpus=[
            Gpu(
                name="Apple M2 Pro",
                vendor="apple",
                vram_total_gb=21.0,
                backend="mps",
                compute_dtype="fp16",
                bf16=True,
                fp8=False,
                memory="shared",
            )
        ],
    )
    prec = gpu_precision(entry, apple, s)
    chip = Measurement(**{**vars(mps), "gpu_name": "Apple M2 Pro"})
    chip2 = Measurement(**{**vars(chip), "peak_vram_gb": 8.2})  # 作者・この PC の行は何行あっても 1 つ
    assert measured_level([chip, chip2], entry, s, apple, prec, 0.0)[0] is None
    other = Measurement(**{**vars(chip2), "origin": "community", "n_sources": 1, "n_oom": 0})
    assert measured_level([chip, other], entry, s, apple, prec, 0.0)[0] is not None
    assert measured_level([chip], entry, s, apple, prec, 0.0)[0] is None  # 出どころ 1 つ
    assert measured_level([mps, mps], entry, s, apple, prec, 0.0)[0] is None


def test_aggregate_keeps_gpu_backend():
    """まとめた行にも backend を残す(estimate.measurement_vendor が会社を名前より先にこれで決める)。"""
    a, b = _sub(6.9, gpu="mps"), _sub(7.0, gpu="mps")
    for s in (a, b):
        s.data["system"]["gpu"].update(vendor="apple", backend="mps")
        s.data["system"]["gpu"].pop("arch", None)
        s.data["system"]["gpu"].pop("compute_capability", None)
        s.sha256 = contrib.content_hash(s.data)
    rows = contrib.aggregate([a, b])
    assert rows[0]["gpu_backend"] == "mps"
    assert Measurement.from_dict(rows[0]).gpu_backend == "mps"
    assert contrib.aggregate([_sub(6.9)])[0]["gpu_backend"] is None  # 記録の無い提出


# --- 0.9.0 の検証役の指摘: 同じ GPU だけ・切り詰め・NaN・名前と型番 ID・点検の PC・会社の食い違い -------


def _amd_gpu(name: str = "AMD Radeon RX 7900 XTX", vram: float = 24.0, pci: str | None = None) -> Gpu:
    return Gpu(
        name=name,
        vendor="amd",
        vram_total_gb=vram,
        backend="rocm",
        compute_dtype="fp16",
        bf16=True,
        fp8=False,
        gfx_target="gfx1100",
        pci_id=pci,
    )


def _sys(g: Gpu, ram: float = 64.0) -> SystemInfo:
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


def test_community_measurement_moves_only_the_same_gpu(db):
    """A: ほかの利用者の実測は、この PC と同じ GPU のときだけ必要量・段階に使う(llmfit と同じ)。
    別の GPU・GPU の無い PC の判定は、足しても足さなくても同じ。
    作者の同梱の実測は今までどおりどの PC にも使う。"""
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id and m.origin is None]
    s = Settings(width=1024, height=1024)
    big = _community(
        maint[0], gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=24.0, peak_vram_gb=20.0, settings=s
    )
    others = [pc(8), pc(12, "NVIDIA GeForce RTX 3060"), pc(24, "NVIDIA RTX A5000"), _sys(_amd_gpu())]
    cpu = SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[])
    for sysi in [*others, cpu]:
        assert estimate(entry, sysi, s, [*maint, big]) == estimate(entry, sysi, s, maint), sysi.gpus
        assert estimate(entry, sysi, None, [*maint, big]) == estimate(entry, sysi, None, maint)
    same = estimate(entry, pc(24, "NVIDIA GeForce RTX 4090"), s, [*maint, big])
    assert same.vram_gb == 20.0 and same.basis_detail == "measured"
    # 書き方の違う同じ GPU の名前((R)・(TM)・™・®・NVIDIA / GeForce の有無・大小)でも同じ GPU
    for name in ("GeForce RTX 4090", "NVIDIA(R) GeForce(TM) RTX 4090", "nvidia geforce rtx™ 4090"):
        assert estimate(entry, pc(24, name), s, [*maint, big]).vram_gb == 20.0, name
    # 作者の実測(origin なし)は別の GPU の PC でも使う(同梱の判定を保つ)
    assert estimate(entry, pc(12, "NVIDIA GeForce RTX 3060"), s, maint).basis_detail == "calibrated"


def test_generic_gpu_names_never_match_but_pci_ids_do(db):
    """D・E: 「AMD Radeon(TM) Graphics」のような汎用の名前は同じ GPU とみなさない(APU の世代が違うもの
    が一致する)。型番 ID が両方にあれば ID で照合する(Linux と Windows で名前が違っても同じ GPU)。"""
    from genmodelfit.estimate import gpu_key, same_gpu

    generic = "AMD Radeon(TM) Graphics"
    assert not same_gpu(generic, None, _amd_gpu(generic))
    assert not same_gpu("Intel(R) Arc(TM) Graphics", None, _amd_gpu("Intel(R) Arc(TM) Graphics"))
    assert same_gpu(generic, "1002:15bf", _amd_gpu(generic, pci="1002:15bf"))
    assert not same_gpu(generic, "1002:15c8", _amd_gpu(generic, pci="1002:15bf"))  # 740M と 780M
    # Linux の lspci の名前と ComfyUI の名前が違っても、ID が同じなら同じ GPU
    lspci = "AMD Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M"
    assert same_gpu("AMD Radeon RX 7900 XTX", "1002:744c", _amd_gpu(lspci, pci="1002:744c"))
    assert not same_gpu("AMD Radeon RX 7900 XTX", None, _amd_gpu(lspci, pci="1002:744c"))
    # 名前の正規化: (R)・(TM)・™・®・大小・空白・NVIDIA / GeForce の語
    assert gpu_key("NVIDIA GeForce RTX™ 4090®") == gpu_key("rtx 4090") == "rtx-4090"
    assert gpu_key("AMD Device 744c") != gpu_key("Intel Device 744c")
    entry = db.get("image_sdxl_simple")
    m0 = next(m for m in db.measurements if m.model_id == entry.id)
    comm = _community(m0, gpu_name=generic, gpu_vram_gb=16.0, peak_vram_gb=3.0, gpu_backend="rocm")
    apu = _sys(_amd_gpu(generic, vram=16.0))
    assert estimate(entry, apu, None, [comm]) == estimate(entry, apu, None, [])


def test_peak_ram_and_vram_bounds(db):
    """B: vram_gb は 256 GiB まで。peak_ram_gb は提出の ram_total_gb × 1.05 + 1 を超えたら誤り。
    見積もりの側もその値で切り詰める。影響は同じ GPU の PC だけ(A)。"""
    d = good()
    d["system"]["gpu"]["vram_gb"] = 257.0
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    assert any("vram_gb" in e["message"] for e in r.errors), r.errors
    d = good()
    d["measurement"]["peak_ram_gb"] = 32 * 1.05 + 1.01
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    assert any("最大メモリ" in e["message"] for e in r.errors), r.errors
    m = contrib.to_measurement(d)
    assert m.peak_ram_gb == pytest.approx(32 * 1.05 + 1.0)
    d["measurement"]["peak_ram_gb"] = 500.0  # 検査を通らないファイルでも、見積もりへはこの値まで
    m = contrib.to_measurement(d)
    assert m.peak_ram_gb == pytest.approx(34.6)
    m.n_used, m.n_oom = 1, 0
    entry = db.get(m.model_id)
    s = m.settings
    on_3070 = estimate(entry, pc(8), s, [m])
    assert on_3070.ram_gb <= 34.6 + 1e-6
    assert estimate(entry, pc(8, "NVIDIA GeForce RTX 3060 Ti"), s, [m]) == estimate(
        entry, pc(8, "NVIDIA GeForce RTX 3060 Ti"), s, []
    )


@pytest.mark.parametrize(
    "where",
    ["measurement.peak_vram_gb", "measurement.seconds", "measurement.peak_ram_gb", "system.gpu.vram_gb"],
)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_nan_inf_negative_are_errors(where, bad):
    """C: NaN・inf・負の数は、どの数の項目でも誤り。"""
    d = good()
    _set(d, where, bad)
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    assert not r.ok and any(e["code"] == "value" for e in r.errors), r.errors


def test_nan_is_refused_when_reading_and_writing(tmp_path):
    """C: 書き出し(提出・まとめ・bench の保存)は NaN を書かない。読み込み(提出・実測のファイル)は断る。"""
    from genmodelfit import db as dbm

    d = good()
    d["measurement"]["peak_vram_gb"] = float("nan")
    with pytest.raises(ValueError):
        contrib.dumps(d)
    with pytest.raises(ValueError):
        contrib.canonical(d)
    p = tmp_path / "x.json"
    p.write_text(json.dumps(good()).replace('"peak_vram_gb": 6.9', '"peak_vram_gb": NaN'), encoding="utf-8")
    with pytest.raises(contrib.SubmissionReadError):
        contrib.read_submission(p)
    rows = (
        '{"measurements": [{"model_id": "x", "gpu_name": "g", "gpu_vram_gb": 8, "peak_vram_gb": Infinity}]}'
    )
    with pytest.raises(ValueError):
        dbm.parse_measurements(dbm._loads_measurements(rows))
    with pytest.raises(ValueError):  # 読めても有限でない値は断る
        dbm.parse_measurements(
            {"measurements": [{"model_id": "x", "gpu_name": "g", "gpu_vram_gb": 8, "peak_vram_gb": -1}]}
        )


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("NVIDIA GeForce RTX 3070", True),
        ("NVIDIA(R) GeForce(TM) RTX 3070", True),
        ("NVIDIA GeForce RTX™ 3070®", True),
        ("NVIDIA GeForce RTX 3070 (bob)", False),
        ("NVIDIA TITAN X (Pascal)", True),  # #101: 括弧の世代名だけは許す
        ("NVIDIA GeForce RTX 3070 [x]", False),
    ],
)
def test_gpu_name_allows_trademarks_only(name, ok):
    """D: GPU 名に ComfyUI・PyTorch・Windows の (R)・(TM)・™・® は入れられる(ほかの括弧・記号は断る)。"""
    d = good()
    d["system"]["gpu"]["name"] = name
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    assert (not any(e["code"] == "value" and "gpu.name" in e["message"] for e in r.errors)) is ok, r.errors


@pytest.mark.parametrize(
    ("name", "vendor", "backend"),
    [
        ("NVIDIA GeForce RTX 3070", "amd", "rocm"),
        ("NVIDIA GeForce RTX 3070", "intel", "xpu"),
        ("AMD Radeon RX 7900 XTX", "nvidia", "cuda"),
        ("Intel(R) Arc(TM) A770 Graphics", "amd", "rocm"),
        ("Apple M2 Pro", "amd", "rocm"),
    ],
)
def test_name_and_vendor_mismatch_is_an_error(name, vendor, backend):
    """I: 提出の GPU 名と vendor が食い違えば誤り(名前が NVIDIA なのに amd など)。"""
    d = good()
    gpu = d["system"]["gpu"]
    gpu.update(name=name, vendor=vendor, backend=backend)
    if vendor != "nvidia":
        gpu.update(arch=None, compute_capability=None)
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    assert any(e["code"] == "gpu_inconsistent" for e in r.errors), r.errors


@pytest.mark.parametrize(
    ("pci", "vendor", "ok"),
    [
        ("1002:744c", "amd", True),
        ("8086:56a0", "intel", True),
        ("10de:2484", None, True),
        ("1002:744c", "intel", False),
        ("1002:744C", "amd", False),  # 小文字だけ
        ("0000:03:00.0", "amd", False),  # 差し場所(バス番号)は入れない
    ],
)
def test_submission_pci_id(pci, vendor, ok):
    """E: 提出の pci_id は PCI の型番 ID(vendor:device、小文字)で、vendor ID が会社と合うものだけ。"""
    d = good()
    gpu = d["system"]["gpu"]
    if vendor == "amd":
        gpu.update(
            name="AMD Radeon RX 7900 XTX", vendor="amd", backend="rocm", arch=None, compute_capability=None
        )
    elif vendor == "intel":
        gpu.update(
            name="Intel(R) Arc(TM) A770 Graphics",
            vendor="intel",
            backend="xpu",
            arch=None,
            compute_capability=None,
        )
    gpu["pci_id"] = pci
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    bad = [e for e in r.errors if "pci_id" in e["message"]]
    assert (not bad) is ok, r.errors
    if ok:
        assert contrib.to_measurement(d).gpu_pci_id == pci
        checks = {c["id"]: c for c in contrib.privacy_checklist(d)}
        assert checks["known_fields"]["ok"] is True


def test_aggregate_groups_by_pci_id():
    """E: 型番 ID のある提出は ID でまとめる(Linux と Windows で名前の違う同じ GPU を 1 つの組に)。"""
    a, b = _sub(6.9, gpu="AMD Radeon RX 7900 XTX"), _sub(7.0, gpu="AMD Radeon RX 7900 XT 7900 XTX 7900 GRE")
    for s in (a, b):
        s.data["system"]["gpu"].update(
            vendor="amd", backend="rocm", arch=None, compute_capability=None, pci_id="1002:744c"
        )
        s.sha256 = contrib.content_hash(s.data)
    rows = contrib.aggregate([a, b])
    assert len(rows) == 1 and rows[0]["gpu_pci_id"] == "1002:744c" and rows[0]["n_used"] == 2


def test_fit_change_check_uses_a_pc_with_the_submitted_gpu(db):
    """F: fit_change の点検は、提出と同じ GPU(名前・ID・会社、VRAM は提出の vram_gb、RAM 16/32/64)の PC で
    見る。同じ GPU の実測で段階が上がる(見積もり good → 実測 perfect)のも notices に出る。"""
    entry = db.get("image_sdxl_simple")
    d = good()
    d["system"]["gpu"].update(
        name="AMD Radeon RX 7800 XT",
        vram_gb=16.0,
        vendor="amd",
        backend="rocm",
        arch=None,
        compute_capability=None,
        driver="25.10.1",
        pci_id="1002:747e",
    )
    gpu = _amd_gpu("AMD Radeon RX 7800 XT", 16.0, pci="1002:747e")
    a = estimate(entry, _sys(gpu), None, db.measurements)
    assert a.fit == "good"  # 見積もり 9.84 GB は 16 GB の 61%
    sett = {k: v for k, v in a.settings.items() if k != "batch" and v is not None}
    # 重み + 固定分(7.93 GB)以上の値(それより小さい実測は段階を決めない。#95)
    d["measurement"].update(settings=sett, peak_vram_gb=9.0, offload="none")
    r = contrib.validate_submission(d, db, check_filename=False, today=TODAY)
    assert r.ok, r.errors
    # 1 つの提出だけでは段階は変わらない(段階を決めるのは別々の出どころ 2 つ以上。#95)
    assert not [n for n in r.notices if n["code"] == "fit_change"], r.notices
    m = contrib.to_measurement(d)
    m.n_used, m.n_oom, m.n_sources, m.consistent = 2, 0, 2, True  # 2 つの PR からそろった提出
    _rel, changes = contrib.community_effect(entry, contrib._own_measurements(db), [m], m.settings)
    assert changes and all(c["before"] == "good" and c["after"] == "perfect" for c in changes)
    assert {c["ram_gb"] for c in changes} <= set(contrib.CHECK_RAM_GB)
    assert all(c["gpu_name"] == "AMD Radeon RX 7800 XT" and c["vram_gb"] == 16.0 for c in changes)


def test_measured_level_needs_the_same_gpu_name(db):
    """変異 M06: NVIDIA 以外の GPU でも、実測で段階を上げるのはこの PC と同じ GPU の実測だけ。同じ会社・同じ
    VRAM でも名前の違う GPU の実測(作者・この PC の bench の実測も)では上げない(#108 で上限は無くした)。"""
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id]
    pc16 = amd_pc(16.0)
    a = estimate(entry, pc16, None, maint)
    assert a.fit == "good"  # 見積もりは 16 GB の 61%
    sett = Settings(**{k: v for k, v in a.settings.items() if k != "batch"})
    other = Measurement(
        **{
            **vars(maint[0]),
            "gpu_name": "AMD Radeon RX 9998 XT",
            "gpu_vram_gb": 16.0,
            "peak_vram_gb": 9.0,  # 重み + 固定分(7.93 GB)以上、16 GB の 56%
            "settings": sett,
            "gpu_backend": "rocm",
        }
    )
    e = estimate(entry, pc16, None, [*maint, other])
    assert e.fit == "good" and e.fit_from == "estimate"
    same = Measurement(**{**vars(other), "gpu_name": FAKE_AMD})
    assert estimate(entry, pc16, None, [*maint, same]).fit == "good"  # 出どころ 1 つ(#95)
    same2 = Measurement(**{**vars(same), "peak_vram_gb": 9.2})  # 作者・この PC の行は何行あっても 1 つ
    assert estimate(entry, pc16, None, [*maint, same, same2]).fit == "good"
    other = Measurement(**{**vars(same2), "origin": "community", "n_sources": 1, "n_oom": 0})
    assert estimate(entry, pc16, None, [*maint, same, other]).fit == "perfect"


@pytest.mark.parametrize(
    ("name", "vendor", "pci", "expect"),
    [
        ("AMD Radeon RX 7900 XTX", "amd", "1002:744c", "ok"),
        ("AMD Radeon RX 7600", "amd", "1002:744c", "error"),  # 744c は Navi 31、7600 は Navi 33
        ("AMD Radeon(TM) Graphics", "amd", "1002:15bf", "ok"),  # 名前から決まらないときは誤りにしない
        ("AMD Radeon RX 7900 XTX", "amd", "1002:ffff", "notice"),  # 表に無い ID
        ("Intel(R) Arc(TM) A770 Graphics", "intel", "8086:56a0", "ok"),
        ("Intel(R) Arc(TM) B580 Graphics", "intel", "8086:56a0", "error"),
        ("Intel(R) Arc(TM) A770 Graphics", "intel", "8086:5690", "error"),  # 5690 は A770M(16 GB)
        ("Intel(R) Arc(TM) 140V GPU (16GB)", "intel", "8086:64a0", "ok"),
    ],
)
def test_submission_pci_id_matches_the_name(name, vendor, pci, expect):
    """N1: 提出の pci_id を名前と照らす。表(hardware.PCI_DEVICE_NAMES)にある ID なのに名前と合わなければ誤り、
    表に無い ID は確認(notices の unknown_gpu)。"""
    d = good()
    backend = {"amd": "rocm", "intel": "xpu"}[vendor]
    d["system"]["gpu"].update(
        name=name, vendor=vendor, backend=backend, arch=None, compute_capability=None, pci_id=pci
    )
    r = contrib.validate_submission(d, check_filename=False, today=TODAY)
    bad = [e for e in r.errors if e["code"] == "gpu_inconsistent"]
    unknown = [n for n in r.notices if n["code"] == "unknown_gpu" and pci in n["message"]]
    assert (bool(bad), bool(unknown)) == (expect == "error", expect == "notice"), (r.errors, r.notices)
    assert not [e for e in r.errors if "gpu.name" in e["message"]], r.errors  # N4: (16GB) も名前に入れられる


def test_fit_change_also_checks_the_nominal_vram(db, monkeypatch):
    """N3: 名前の表で公称の VRAM が分かる GPU(Intel の Arc、0.9.1 から NVIDIA の VRAM 違いのある GPU)は、
    提出の VRAM に加えて公称の VRAM の PC でも
    点検する(A770 は 8 GB と 16 GB の版が同じ名前)。"""
    seen = []
    real = contrib._measured_pc

    def spy(m, ram):
        seen.append(m.gpu_vram_gb)
        return real(m, ram)

    monkeypatch.setattr(contrib, "_measured_pc", spy)
    entry = db.get("image_sdxl_simple")
    m0 = next(m for m in db.measurements if m.model_id == entry.id)
    m = _community(m0, gpu_name="Intel(R) Arc(TM) A770 Graphics", gpu_vram_gb=16.0, gpu_backend="xpu")
    contrib.community_effect(entry, contrib._own_measurements(db), [m])
    assert set(seen) == {16.0, 8.0}
    seen.clear()
    nv = _community(m0, gpu_name="NVIDIA GeForce RTX 4060 Ti", gpu_vram_gb=16.0)
    contrib.community_effect(entry, contrib._own_measurements(db), [nv])
    assert set(seen) == {16.0, 8.0}  # 0.9.1: NVIDIA も公称の VRAM の表(estimate.NVIDIA_VRAM_VARIANTS)で点検
    seen.clear()
    nv = _community(m0, gpu_name="NVIDIA GeForce RTX 4070", gpu_vram_gb=12.0)
    contrib.community_effect(entry, contrib._own_measurements(db), [nv])
    assert set(seen) == {12.0}  # VRAM 違いの無い GPU は今のまま


# --- 0.9.1: 同じ名前で VRAM の違う GPU の実測を同じ GPU として使わない ----------------------------------


def test_same_gpu_requires_close_vram():
    """同じ名前・同じ型番 ID でも VRAM が違えば別の GPU(RTX 4060 Ti の 8 / 16 GB、
    PCI ID 744c の XT・XTX・GRE)。
    公称 16 と nvidia-smi の 15.9、Linux の 5.80 と Windows の 6.0 のような数え方の違い(95% 以上)は同じ。"""
    from genmodelfit.estimate import nvidia_vram_variants, same_gpu

    nv = "NVIDIA GeForce RTX 4060 Ti"
    assert same_gpu(nv, None, pc(16, nv).gpus[0], 16.0)
    assert same_gpu(nv, None, pc(15.9, nv).gpus[0], 16.0)
    assert not same_gpu(nv, None, pc(8, nv).gpus[0], 16.0)
    assert not same_gpu("NVIDIA GeForce RTX 3060", None, pc(12, "NVIDIA GeForce RTX 3060").gpus[0], 8.0)
    assert same_gpu(nv, None, pc(8, nv).gpus[0])  # VRAM を渡さなければ従来どおり
    lspci = "AMD Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M"
    xtx, gre = _amd_gpu(lspci, 24.0, "1002:744c"), _amd_gpu(lspci, 16.0, "1002:744c")
    assert same_gpu("AMD Radeon RX 7900 XTX", "1002:744c", xtx, 24.0)
    assert not same_gpu("AMD Radeon RX 7900 XTX", "1002:744c", gre, 24.0)  # 0.9.0 の検証役の指摘 N2
    # 統合メモリ・共有メモリの GPU は総量が PC のメインメモリで決まるので比べない
    apple = Gpu(name="Apple M2 Pro", vendor="apple", vram_total_gb=24.0, backend="mps", memory="unified")
    assert same_gpu("Apple M2 Pro", None, apple, 16.0)
    assert nvidia_vram_variants("NVIDIA GeForce RTX 4060 Ti") == (8.0, 16.0)
    assert nvidia_vram_variants("NVIDIA GeForce RTX 4060 Ti Laptop GPU") == ()
    assert nvidia_vram_variants("NVIDIA GeForce RTX 4070") == ()


def test_community_16gb_measurement_not_used_on_8gb_card(db):
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id and m.origin is None]
    s = Settings(width=1024, height=1024)
    nv = "NVIDIA GeForce RTX 4060 Ti"
    c16 = _community(maint[0], gpu_name=nv, gpu_vram_gb=16.0, peak_vram_gb=14.0, settings=s)
    assert estimate(entry, pc(8, nv), s, [*maint, c16]) == estimate(entry, pc(8, nv), s, maint)
    assert estimate(entry, pc(16, nv), s, [*maint, c16]).vram_gb == 14.0


def test_community_effect_also_checks_nvidia_vram_variants(db, monkeypatch):
    entry = db.get("image_sdxl_simple")
    maint = [m for m in db.measurements if m.model_id == entry.id and m.origin is None]
    nv = "NVIDIA GeForce RTX 3060"
    s = Settings(width=1024, height=1024)
    m = _community(maint[0], gpu_name=nv, gpu_vram_gb=12.0, peak_vram_gb=9.0, settings=s)
    from genmodelfit.contrib import community_effect

    seen = []
    real = contrib._measured_pc
    monkeypatch.setattr(
        contrib, "_measured_pc", lambda mm, ram: (seen.append(mm.gpu_vram_gb), real(mm, ram))[1]
    )
    _rel, changes = community_effect(entry, maint, [m], m.settings)
    assert set(seen) == {12.0, 8.0}  # 表の両方の VRAM の PC を見た(M14)
    assert all(c["vram_gb"] in (8.0, 12.0) for c in changes)
    # 8 GB 版の PC では 12 GB の実測は使われないので、段階は変わらない
    assert not [c for c in changes if c["vram_gb"] == 8.0]


# --- ブラウザで送る(share。#90) ------------------------------------------------------------------


def _pr92(tmp_path: Path) -> Path:
    """PR #92 と同じ形の提出(MoGe 深度推定、RTX 3070。値は試験用)を書く。"""
    d = good()
    d["measurement"].update(
        model_id="utility_moge_depth_estimation",
        settings={"precision": "fp16"},
        peak_vram_gb=0.78,
        note="SYNTHETIC TEST FIXTURE。GPU 全体(nvidia-smi)の増分 1.46 GB。計測 13 回",
    )
    return contrib.write_submission(d, str(tmp_path / "subs") + "/")


@pytest.fixture
def no_network(monkeypatch):
    """この中で通信したら落とす(share は URL を作るだけ。README「Privacy」)。"""
    import socket

    import requests

    def deny(*a, **k):
        raise AssertionError("network used")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(requests.Session, "request", deny)


@pytest.fixture
def browser(monkeypatch):
    opened: list[str] = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


@pytest.fixture
def clipboard(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda text: copied.append(text) or True)
    return copied


def test_share_urls_prefill_the_issue_form(tmp_path):
    from urllib.parse import parse_qs, urlparse

    path = _pr92(tmp_path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    u = contrib.share_urls(data, text)
    assert u["path_in_repo"] == contrib.suggested_path(data) and u["path_in_repo"].endswith(path.name)
    url = urlparse(u["issue_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://github.com/laxshmate/genmodelfit/issues/new"
    q = parse_qs(url.query)
    assert q["template"] == ["measurement.yml"] and q["submission"] == [text]
    assert q["title"] == ["measurement: utility_moge_depth_estimation on NVIDIA GeForce RTX 3070"]
    # 中身の無いフォームは題だけを入れた同じページ
    assert u["form_url"] == u["issue_url"].split("&submission=")[0]
    assert "submission=" not in u["form_url"]
    assert u["issue_url_length"] == len(u["issue_url"]) <= contrib.SHARE_URL_LIMIT
    # 改行は LF にそろえる
    crlf = contrib.share_urls(data, text.replace("\n", "\r\n"))
    assert crlf["issue_url"] == u["issue_url"]
    # 長すぎる中身は URL に入れず、中身の無いフォームだけ
    long = contrib.share_urls(data, text + " " * contrib.SHARE_URL_LIMIT)
    assert long["issue_url"] is None and long["form_url"] == u["form_url"]
    assert "new/main" not in json.dumps(u)  # 前の「新しいファイル」のページ(PR)は使わない


def test_issue_template_matches_the_share_url():
    """share が入れる欄の id・フォームの名前・印が、.github/ISSUE_TEMPLATE/measurement.yml と合う。"""
    import yaml

    root = Path(__file__).parent.parent
    form = yaml.safe_load((root / ".github" / "ISSUE_TEMPLATE" / contrib.ISSUE_TEMPLATE).read_text("utf-8"))
    assert form["labels"] == [contrib.ISSUE_LABEL]
    fields = {b.get("id"): b for b in form["body"] if b.get("id")}
    box = fields[contrib.ISSUE_FIELD]
    assert box["type"] == "textarea" and box["attributes"]["render"] == "json"
    # 「Create」を 1 回押すだけ: 必須のチェックボックスなど、押す手間を増やす欄を置かない
    assert not [b for b in form["body"] if b["type"] in ("checkboxes", "dropdown", "input")]


def test_share_check_refuses_ng_and_errors_only(db):
    d = good()
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert contrib.share_check(contrib.privacy_checklist(d), r) == []
    d["measurement"]["note"] = r"saved in C:\Users\John Smith\x.json"
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    why = contrib.share_check(contrib.privacy_checklist(d), r)
    assert why and any("privacy" in w for w in why)
    d = good()
    d["measurement"]["peak_vram_gb"] = 0.5  # 確認(deviation・below_weights)だけでは断らない
    r = contrib.validate_submission(d, db, today=TODAY, check_filename=False)
    assert r.notices and contrib.share_check(contrib.privacy_checklist(d), r) == []


def test_cli_share_prints_the_url_without_a_terminal(
    tmp_path, capsys, monkeypatch, no_network, browser, clipboard
):
    path = _pr92(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["--lang", "ja", "contrib", "share", str(path)]) == 0
    out = capsys.readouterr().out
    assert "これがファイルの中身のすべてです" in out and "=== 個人の情報の点検 ===" in out
    text = path.read_text(encoding="utf-8")
    assert contrib.share_urls(json.loads(text), text)["issue_url"] in out
    assert "「Create」を押す" in out and "genmodelfit は何も送りません" in out and "[y/N]" not in out
    assert browser == [] and clipboard == []  # 端末でなければ開かない・写さない


def test_cli_share_opens_the_form_in_a_terminal(
    tmp_path, capsys, monkeypatch, no_network, browser, clipboard
):
    path = _pr92(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("share は送るかを尋ねない"))
    assert cli.main(["--lang", "ja", "share", "--issue", str(path)]) == 0
    out = capsys.readouterr().out
    text = path.read_text(encoding="utf-8")
    assert browser == [contrib.share_urls(json.loads(text), text)["issue_url"]] and clipboard == []
    assert "ブラウザで開いたページ" in out


def test_cli_share_long_content_goes_to_the_clipboard(
    tmp_path, capsys, monkeypatch, no_network, browser, clipboard
):
    path = _pr92(tmp_path)
    monkeypatch.setattr(contrib, "SHARE_URL_LIMIT", 100)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--lang", "ja", "share", "--issue", str(path)]) == 0
    out = capsys.readouterr().out
    text = path.read_text(encoding="utf-8")
    assert clipboard == [text]
    assert browser == [contrib.share_urls(json.loads(text), text)["form_url"]]
    assert "クリップボードに写しました" in out and "「Measurement (JSON)」の欄に貼り付ける" in out
    assert "---" not in out.splitlines()  # 写せたときは中身を画面に重ねて出さない


def test_cli_share_long_content_without_clipboard_prints_it(
    tmp_path, capsys, monkeypatch, no_network, browser
):
    path = _pr92(tmp_path)
    monkeypatch.setattr(contrib, "SHARE_URL_LIMIT", 100)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda text: False)
    assert cli.main(["--lang", "ja", "share", "--issue", str(path)]) == 0
    out = capsys.readouterr().out
    # 写す範囲(2 つの --- の行の間)は中身だけ。ファイルの場所(ユーザ名が入る)は出さない
    lines = out.splitlines()
    a = lines.index("---")
    b = lines.index("---", a + 1)
    between = "\n".join(lines[a + 1 : b])
    assert between + "\n" == path.read_text(encoding="utf-8") and str(path.resolve()) not in between


def test_cli_share_json_never_opens_a_browser(tmp_path, capsys, monkeypatch, no_network, browser, clipboard):
    path = _pr92(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["contrib", "share", str(path), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["command"] == "contrib" and d["action"] == "share" and d["ok"] is True
    assert d["opened"] is False and browser == [] and clipboard == []
    assert d["refused"] == [] and d["share"]["path_in_repo"].endswith(path.name)
    assert d["share"]["issue_url"].startswith("https://github.com/laxshmate/genmodelfit/issues/new?template=")
    assert d["marked_shared"] is False  # --json は見るだけ(--mark-sent のときだけ印を付ける)
    assert cli.main(["contrib", "share", str(path), "--json", "--mark-sent"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_shared"] is True


def test_cli_share_refuses_personal_information(tmp_path, capsys, monkeypatch, no_network, browser):
    d = good()
    d["measurement"]["note"] = r"saved in C:\Users\John Smith\x.json"
    path = tmp_path / "leaky.json"
    path.write_text(contrib.dumps(d), encoding="utf-8")
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--lang", "ja", "share", "--issue", str(path)]) == 1
    cap = capsys.readouterr()
    assert "送れません" in cap.err and "github.com/laxshmate" not in cap.out and browser == []
    assert cli.main(["share", "--issue", str(path), "--json"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert j["ok"] is False and j["share"] is None and j["refused"] and j["marked_shared"] is False


def test_bench_share_writes_to_the_cache_and_shows_the_url(tmp_path, capsys, monkeypatch, browser):
    from test_bench import GPU0, FakeComfy

    from genmodelfit import bench
    from genmodelfit.db import cache_dir

    # 本物の GPU には触らない(偽の GPU が 1 枚あり、空いていることにする。tests/test_bench.py と同じ)
    monkeypatch.setattr(bench, "smi_gpus", lambda: [dict(GPU0)])
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    real_run = bench.run

    def run(*a, **k):  # 偽の ComfyUI はすぐ終わり、時間が 0.1 秒未満(検査の誤り)になることがあるので
        res = real_run(*a, **k)
        res.measurement.seconds = 2.0
        return res

    monkeypatch.setattr(bench, "run", run)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    with FakeComfy() as fake:
        assert cli.main(["--lang", "ja", "bench", "image_sdxl_simple", "--url", fake.url, "--share"]) == 0
    out = capsys.readouterr().out
    subs = list((cache_dir() / "submissions" / "rtx-3070").glob("*.json"))
    assert len(subs) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}\.json", subs[0].name)
    assert "=== GitHub で送る ===" in out and "issues/new?template=measurement.yml" in out and browser == []
    with FakeComfy() as fake:
        args = ["bench", "image_sdxl_simple", "--url", fake.url, "--share", "--json"]
        args += ["--submission", str(tmp_path / "mine") + "/"]
        assert cli.main(args) == 0
    d = json.loads(capsys.readouterr().out)
    share = d["submission"]["share"]
    assert share["ok"] is True and share["opened"] is False
    assert Path(d["submission"]["saved_to"]).parent.parent == tmp_path / "mine"
