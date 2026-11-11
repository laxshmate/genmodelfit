# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""いろいろな提出への強さ(#90)。分析で作った約 60 種の提出(案件 ID は A1・F26 などの名前で引く)のうち、
提出の流れ(検査・まとめ・データとして正しいかの決まり)で扱うものを試す。段階の決まり(物理的な下限・
2 つ以上の出どころ)は PR #95 の範囲。"""

from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from genmodelfit import cli, contrib
from genmodelfit.db import Database, load
from genmodelfit.estimate import files_fingerprint, pick_measurement
from genmodelfit.hardware import nvidia_name_well_formed
from genmodelfit.models import Gpu, Measurement, SystemInfo

TEMPLATE = {
    "contrib_schema": 2,
    "kind": "genmodelfit-measurement",
    "tool": {"name": "genmodelfit", "version": "0.10.1"},
    "date": "2026-10-27",
    "system": {
        "os_family": "windows",
        "ram_total_gb": 32,
        "gpu": {
            "name": "NVIDIA GeForce RTX 3070",
            "vram_gb": 8.0,
            "arch": "ampere",
            "compute_capability": "8.6",
            "driver": "610.88",
            "vendor": "nvidia",
            "backend": "cuda",
        },
    },
    "comfyui_version": "0.40.0",
    "measurement": {
        "model_id": "image_sdxl_simple",
        "settings": {"width": 832, "height": 832, "steps": 15},
        "outcome": "ok",
        "standard": True,
        "offload": "dynamic",
        "peak_vram_gb": 6.9,
        "peak_ram_gb": 7.4,
        "seconds": 40.0,
        "vram_metric": "torch_reserved",
    },
}


@pytest.fixture(scope="module")
def db() -> Database:
    return load(use_cache=False)


def sub(gpu: dict | None = None, **m) -> dict:
    d = copy.deepcopy(TEMPLATE)
    if gpu is not None:
        d["system"]["gpu"] = gpu
    d["measurement"].update(m)
    return d


def as_sub(d: dict, path: str | None = None) -> contrib.Submission:
    h = contrib.content_hash(d)
    return contrib.Submission(path=path or h, sha256=h, data=d)


def write(root: Path, d: dict, rel: str | None = None, raw: bytes | None = None) -> Path:
    p = root / (rel or contrib.suggested_path(d).split("/", 2)[2])
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_bytes(raw)
    else:
        p.write_text(contrib.dumps(d), encoding="utf-8", newline="\n")
    return p


def pc(name: str = "NVIDIA GeForce RTX 3070", vram: float = 8.0) -> SystemInfo:
    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram, backend="cuda")
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])


def codes(r, kind: str = "errors") -> set[str]:
    return {x["code"] for x in getattr(r, kind)}


def rows_of(subs, sources=None) -> list[Measurement]:
    return [Measurement.from_dict(r) for r in contrib.aggregate(subs, sources)]


# --- 1. 足すだけ・置き場所(H3・F21b・F22・F26・F27・E1・empty) -----------------------------------------


def test_layout_errors_and_empty_folder(tmp_path, capsys):
    """置けるのは <GPU>/<日付>-<sha8>.json(小文字の .json)だけ。ほかは誤り layout。提出が 0 件なら失敗。"""
    root = tmp_path / "contrib"
    d = sub()
    good = write(root, d)
    name = good.name
    write(root, d, name)  # F21b: フォルダの直下
    write(root, d, f"rtx-3070/sub/{name}")  # F22: 深いフォルダ
    write(root, d, f"rtx-3070/{name.replace('.json', '.txt')}")  # F26
    write(root, d, "rtx-3070/2026-10-27-00000000.JSON")  # F27(Windows は大文字小文字を区別しないので別の名前)
    reports = contrib.validate_dirs([root], today=None)
    by = {Path(r.path).relative_to(root).as_posix(): r for r in reports}
    assert by[f"rtx-3070/{name}"].ok
    for rel in (
        name,
        f"rtx-3070/sub/{name}",
        f"rtx-3070/{name[:-5]}.txt",
        "rtx-3070/2026-10-27-00000000.JSON",
    ):
        assert "layout" in codes(by[rel]), rel
    assert cli.main(["contrib", "validate", str(root)]) == 1
    capsys.readouterr()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["--lang", "ja", "contrib", "validate", str(empty)]) == 1
    assert "検査する提出がありません" in capsys.readouterr().err
    # 置けない場所のファイルはまとめにも使わない
    subs, reps = contrib.load_dir(root)
    assert [Path(s.path).name for s in subs] == [name] and sum(not r.ok for r in reps) == 4


def test_exact_duplicate_in_two_places_is_an_error(tmp_path):
    """E1: 同じ中身を 2 か所に置いても 1 件(2 件目は duplicate)。"""
    root = tmp_path / "contrib"
    d = sub()
    write(root, d)
    write(root, d, "rtx-3070/" + contrib.suggested_path(d).rsplit("/", 1)[1].replace("2026", "2026", 1))
    other = sub(peak_vram_gb=7.0)
    write(root, other)
    reports = contrib.validate_dirs([root])
    assert sum(r.ok for r in reports) == 2


def test_contrib_workflow_requires_add_only_and_the_exact_layout():
    import yaml

    wf = yaml.safe_load(Path(".github/workflows/contrib.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["validate"]["steps"]
    step = next(s for s in steps if "足すだけ" in s.get("name", ""))
    run = step["run"]
    assert "--diff-filter=DMRTC --name-only HEAD^1 HEAD -- data/contrib" in run
    assert "--diff-filter=A --raw HEAD^1 HEAD" in run  # 普通のファイルだけ(リンク・サブモジュールは断る)
    pr_checkout = next(s for s in steps if s.get("with", {}).get("path") == "pr")
    assert pr_checkout["with"]["fetch-depth"] == 2  # マージのコミットとその 1 つ目の親を比べる
    assert (
        "--diff-filter=A" in run
        and r"^data/contrib/[a-z0-9-]+/[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}(-failures)?\.json$" in run
    )
    assert step["working-directory"] == "pr" and "$added" not in run.replace('"$added"', "").split("echo")[0]
    # 安全の性質は変えない: pull_request・読むだけのトークン・向き先の道具
    assert set(wf[True]) == {"pull_request"} and wf["permissions"] == {"contents": "read"}
    assert any(s.get("with", {}).get("ref") == "${{ github.event.pull_request.base.sha }}" for s in steps)


# --- 4. 個人の情報(F1〜F12・F15・F24) --------------------------------------------------------------

LEAKS = {
    "F1_b64_path": base64.b64encode(rb"C:\Users\taro\ComfyUI").decode(),
    "F1b_b64_email": base64.b64encode(b"taro.yamada@example.com").decode(),
    "F2b_fullwidth_at": "taro＠example.com",
    "F3_url_noscheme": "comfy at example.com/u/taro",
    "F3b_domain": "ran on my-server.example.net port 8188",
    "F4_email_obf": "taro [at] example [dot] com",
    "F4b_email_obf2": "taro(アット)example.com",
    "F7_phone": "090-1234-5678",
    "F7b_phone_intl": "+81 90 1234 5678",
    "F10_mac_dot": "aa.bb.cc.dd.ee.ff",
    "F10b_mac_cisco": "001a.2b3c.4d5e",
    "F11_gpu_uuid_nodash": "GPU 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
    "F12_serial_jp": "製品番号 ABC123456",
}


@pytest.mark.parametrize("case", sorted(LEAKS))
def test_leaky_notes_are_errors(case, db):
    r = contrib.validate_submission(sub(note=LEAKS[case]), db, check_filename=False)
    assert "privacy" in codes(r), (case, r.errors)
    assert not contrib._hits(contrib.scrub_text(LEAKS[case]) or ""), case  # scrub は文ごと落とす


def test_new_patterns_do_not_hit_real_data(db):
    """作者の実測の note(bench の文)・一覧のモデル ID・名前・GPU 名の表は、新しい形に当たらない。"""
    raw = json.loads(Path("src/genmodelfit/data/measurements.json").read_text(encoding="utf-8"))
    notes = [m.get("note") or "" for m in raw["measurements"]]
    assert any("nvidia-smi" in n for n in notes)
    for n in notes:
        assert not contrib._hits(n), n
    for e in db.models:
        r = contrib.validate_submission(sub(model_id=e.id), db, check_filename=False)
        assert "privacy" not in codes(r), (e.id, r.errors)
    for name in (
        "NVIDIA GeForce RTX 4070 Ti SUPER",
        "Intel(R) Arc(TM) 140V GPU (16GB)",
        "AMD Radeon RX 7900 XTX",
    ):
        assert not contrib._hits(name), name


def test_note_length_limit():
    assert contrib.MAX_NOTE_CHARS == 500
    r = contrib.validate_submission(sub(note="計測 13 回。" * 80), check_filename=False)
    assert "string" in codes(r)
    m = Measurement(
        model_id="x", gpu_name="NVIDIA GeForce RTX 3070", gpu_vram_gb=8.0, peak_vram_gb=1, note="a。" * 400
    )
    assert len(contrib.build_submission(m)["measurement"]["note"]) <= 500  # bench の提出は切り詰める


def test_bom_and_duplicate_keys_are_clear_read_errors(tmp_path):
    """F15: BOM は消さずに断る(理由を出す)。F24: 同じキーが 2 回ある JSON は断る(後の値が黙って勝つため)。"""
    root = tmp_path / "contrib"
    d = sub()
    txt = contrib.dumps(d)
    bom = write(root, d, raw=("\ufeff" + txt).encode("utf-8"))
    r = contrib.validate_files([bom])[0]
    assert "read" in codes(r) and "BOM" in r.errors[0]["message"]
    dup = tmp_path / "dup.json"
    dup.write_text(txt.replace('"outcome": "ok"', '"outcome": "oom", "outcome": "ok"'), encoding="utf-8")
    r = contrib.validate_files([dup])[0]
    assert "read" in codes(r) and "outcome" in r.errors[0]["message"]


# --- 3. GPU の名前(A7・A7b・F14・A9・A4・A8) -----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["NVIDIA Graphics Device", "GPU", "none", "null"],
    ids=["A7_odd_name", "A7b_fake_name_GPU", "placeholder_none", "placeholder_null"],
)
def test_generic_nvidia_names_are_kept_but_never_used(name, db):
    """#101: 断るのは汎用の名前と名前の代わりの値だけ(llmfit と同じ)。"""
    gpu = {"name": name, "vram_gb": 8.0, "driver": "610.88", "vendor": "nvidia", "backend": "cuda"}
    d = sub(gpu=gpu, peak_vram_gb=6.0)
    r = contrib.validate_submission(d, db, check_filename=False)
    assert "unknown_gpu" in codes(r, "notices"), (r.errors, r.notices)
    rows = contrib.aggregate([as_sub(d)])
    assert rows[0]["gpu_known"] is False
    entry = db.get("image_sdxl_simple")
    system = pc(name, 8.0)
    m = pick_measurement([Measurement.from_dict(rows[0])], entry, None, system.best_gpu)
    assert m is None


def test_ill_formed_name_is_a_maintainer_notice_and_only_matches_itself(db):
    """F14(#101 で変えた): 形に合わない名前(人名入り)は誤りでなく確認 gpu_name_form(メンテナが確かめ、
    本人は contrib show で見る)。判定には使うが、同じ文字列を返す PC にしか当たらない(same_gpu)。"""
    name = "NVIDIA GeForce RTX 3070 taro yamada"
    gpu = {"name": name, "vram_gb": 8.0, "driver": "610.88", "vendor": "nvidia", "backend": "cuda"}
    d = sub(gpu=gpu, peak_vram_gb=6.0)
    r = contrib.validate_submission(d, db, check_filename=False)
    assert r.ok and "gpu_name_form" in codes(r, "notices"), (r.errors, r.notices)
    rows = contrib.aggregate([as_sub(d)])
    assert rows[0]["gpu_known"] is True
    entry = db.get("image_sdxl_simple")
    row = Measurement.from_dict(rows[0])
    assert pick_measurement([row], entry, None, pc("NVIDIA GeForce RTX 3070", 8.0).best_gpu) is None


@pytest.mark.parametrize(
    ("name", "vram", "ok"),
    [
        ("NVIDIA GeForce RTX 3070", 16.0, False),  # A9
        ("NVIDIA GeForce RTX 3070", 7.99, True),
        ("NVIDIA GeForce RTX 4060 Ti", 8.0, True),  # A8
        ("NVIDIA GeForce RTX 4060 Ti", 15.99, True),
        ("NVIDIA GeForce RTX 4060 Ti", 12.0, False),
        ("NVIDIA GeForce RTX 4070 Laptop GPU", 8.0, True),  # A4: ノート用は量の表に入れない
        ("NVIDIA GeForce RTX 4090", 47.99, False),  # #101: 改造品の 48 GB 版
    ],
)
def test_vram_off_the_name_is_a_notice(name, vram, ok, db):
    """#101 で変えた: 公称の量に合わない VRAM は誤り(gpu_inconsistent)でなく確認 gpu_vram_nominal
    (改造品があるため。別の VRAM の PC に混ざらないことは same_gpu の VRAM の比が守る)。"""
    gpu = {"name": name, "vram_gb": vram, "driver": "610.88", "vendor": "nvidia", "backend": "cuda"}
    r = contrib.validate_submission(sub(gpu=gpu, peak_vram_gb=5.0), db, check_filename=False)
    assert "gpu_inconsistent" not in codes(r), r.errors
    assert ("gpu_vram_nominal" not in codes(r, "notices")) is ok, r.notices
    assert contrib.aggregate([as_sub(sub(gpu=gpu, peak_vram_gb=5.0))])[0]["gpu_known"] is True


def test_non_nvidia_gpus_keep_their_behaviour(db):
    """A1・A2: AMD・Intel は PCI の型番 ID で照合する(名前の表は NVIDIA だけ)。"""
    amd = {
        "name": "AMD Radeon RX 7900 XTX",
        "vram_gb": 24.0,
        "vendor": "amd",
        "backend": "rocm",
        "driver": "24.10.1",
        "pci_id": "1002:744c",
    }
    row = contrib.aggregate([as_sub(sub(gpu=amd, peak_vram_gb=7.0))])[0]
    assert row["gpu_known"] is True


# --- 2. 測ったときの一覧のファイル(G3) ------------------------------------------------------------


def test_files_fp_is_recorded_and_stale_rows_are_unused(db):
    entry = db.get("flux1_krea_dev")
    fp = files_fingerprint(entry)
    assert fp and len(fp) == 64
    shas = sorted(f.sha256.lower() for f in entry.files)
    import hashlib

    assert fp == hashlib.sha256("\n".join(shas).encode()).hexdigest()  # 決め方(docs/schema.md「contrib」)
    d = sub(
        model_id=entry.id,
        settings={"width": 1024, "height": 1024, "steps": 20},
        peak_vram_gb=7.6,
        files_fp=fp,
        offload="none",  # 動的 VRAM の torch_reserved は作業用メモリだけで、見積もりに使わない(#100)
    )
    assert contrib.validate_submission(d, db, check_filename=False).ok
    rows = rows_of([as_sub(d)])
    assert rows[0].files_fp == fp
    system = pc()
    assert pick_measurement(rows, entry, None, system.best_gpu) is not None
    # 上流がファイルを差し替えた(一覧の sha256 が変わった)ら、前の実測は使わない
    swapped = copy.deepcopy(entry)
    swapped.files[0].sha256 = "0" * 64
    assert pick_measurement(rows, swapped, None, system.best_gpu) is None
    # 指紋の無い行(contrib_schema 1・作者の実測)は今までどおり使う
    old = copy.deepcopy(d)
    old["contrib_schema"] = 1
    del old["measurement"]["files_fp"]
    assert pick_measurement(rows_of([as_sub(old)]), swapped, None, system.best_gpu) is not None
    # schema 1 に files_fp は誤り・形の違う files_fp も誤り
    bad = copy.deepcopy(d)
    bad["contrib_schema"] = 1
    assert "schema" in codes(contrib.validate_submission(bad, db, check_filename=False))
    bad = copy.deepcopy(d)
    bad["measurement"]["files_fp"] = "XYZ"
    assert "value" in codes(contrib.validate_submission(bad, db, check_filename=False))
    # 違う指紋の提出は別の行になる
    other = copy.deepcopy(d)
    other["measurement"]["files_fp"] = "1" * 64
    assert len(contrib.aggregate([as_sub(d), as_sub(other)])) == 2


def test_bench_submission_carries_files_fp(db):
    from genmodelfit import bench

    args = type(
        "A",
        (),
        {
            "template": "flux1_krea_dev",
            "url": "http://127.0.0.1:1",
            "workflow": None,
            "frames": None,
            "length": None,
            "steps": None,
            "timeout": None,
        },
    )()
    opts = bench.options_from_args(args)
    assert opts.files_fp == files_fingerprint(db.get("flux1_krea_dev"))
    m = Measurement(
        model_id="flux1_krea_dev",
        gpu_name="NVIDIA GeForce RTX 3070",
        gpu_vram_gb=8.0,
        peak_vram_gb=7.0,
        files_fp=opts.files_fp,
    )
    s = contrib.build_submission(m)
    assert s["contrib_schema"] == 3 and s["measurement"]["files_fp"] == opts.files_fp


# --- 5. まとめ(D2・D3・C5・E2・n_sources) ----------------------------------------------------------


def test_error_runs_are_counted_not_turned_into_oom():
    """D2: error で終わった提出は行の値にしない(error だけなら行を作らない)。"""
    err = sub(outcome="error", peak_vram_gb=2.0, seconds=None)
    assert contrib.aggregate([as_sub(err)]) == []
    rows = contrib.aggregate([as_sub(err), as_sub(sub(peak_vram_gb=6.9))])
    assert len(rows) == 1 and rows[0]["outcome"] == "ok" and rows[0]["n_error"] == 1
    assert rows[0]["peak_vram_gb"] == 6.9


def test_oom_heavy_rows_are_not_runs_evidence():
    """D3・D3b: OOM の出どころが ok の出どころ以上なら runs_evidence は false。"""
    oom = sub(outcome="oom", offload="oom", peak_vram_gb=7.9, seconds=None)
    oom2 = sub(outcome="oom", offload="oom", peak_vram_gb=7.8, seconds=None, note="x")
    ok1 = sub(peak_vram_gb=6.9)
    assert contrib.aggregate([as_sub(ok1), as_sub(oom)])[0]["runs_evidence"] is False  # 1 対 1
    assert contrib.aggregate([as_sub(ok1), as_sub(oom), as_sub(oom2)])[0]["runs_evidence"] is False
    ok2 = sub(peak_vram_gb=7.0)
    assert contrib.aggregate([as_sub(ok1), as_sub(ok2), as_sub(oom)])[0]["runs_evidence"] is True
    assert contrib.aggregate([as_sub(ok1)])[0]["runs_evidence"] is True


def test_steps_are_not_part_of_the_key():
    """C5: ステップ数の違い・欠けは同じ組(判定の照合 estimate._settings_match もステップ数を見ない)。"""
    a = sub(settings={"width": 832, "height": 832}, peak_vram_gb=7.0)
    b = sub(settings={"width": 832, "height": 832, "steps": 30}, peak_vram_gb=7.2)
    rows = contrib.aggregate([as_sub(a), as_sub(b)])
    assert len(rows) == 1 and rows[0]["n_used"] == 2 and rows[0]["settings"]["steps"] == 30


def test_one_source_counts_once():
    """E2・E2b: 1 つの出どころ(同じ PR)の 20 件の小さな値は 1 つの値(その中央値)としてだけ数える。"""
    honest = [as_sub(sub(peak_vram_gb=v), f"h{i}") for i, v in enumerate((6.9, 7.0, 7.1))]
    spam = [as_sub(sub(peak_vram_gb=1.0, seconds=40 + i * 0.1), f"s{i}") for i in range(20)]
    sources = {s.sha256: f"pr-honest-{i}" for i, s in enumerate(honest)} | {s.sha256: "pr-spam" for s in spam}
    row = contrib.aggregate(honest + spam, sources)[0]
    assert row["n_sources"] == 3 and row["peak_vram_gb"] == 7.0 and row["n_outliers"] == 20
    # 出どころが分からない(git の外)ときは提出ごとに数えるので、数の多い側が勝つ(出どころを git で数える理由)
    row = contrib.aggregate(honest + spam)[0]
    assert row["n_sources"] == 20 and row["peak_vram_gb"] == 1.0


def _git(cwd: Path, *args: str, email: str = "t@example.invalid") -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", f"user.email={email}", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_n_sources_from_git_history(tmp_path, capsys):
    """出どころ = 送った人(#113)。PR の側でファイルを足したコミットの書いた人で見る(マージのコミットを作った
    メンテナではない)。別の人の 2 件は 2 つ。"""
    repo = tmp_path / "repo"
    root = repo / "data" / "contrib"
    root.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    a, b, c = (sub(peak_vram_gb=v) for v in (6.9, 7.0, 7.1))
    write(root, a)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    _git(repo, "switch", "-q", "-c", "pr")
    write(root, b)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "pr 1", email="other@example.invalid")
    write(root, c)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "pr 2", email="other@example.invalid")
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "pr", "-m", "merge pr")
    subs, _ = contrib.load_dir(root)
    sources, via_git = contrib.submission_sources(root, subs)
    assert via_git and len(set(sources.values())) == 2
    assert contrib.aggregate(subs, sources)[0]["n_sources"] == 2
    # まだコミットしていない提出は 1 件で 1 つ
    write(root, sub(peak_vram_gb=7.05))
    subs, _ = contrib.load_dir(root)
    sources, _ = contrib.submission_sources(root, subs)
    assert len(set(sources.values())) == 3
    assert cli.main(["contrib", "aggregate", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sources_from"] == "git"
    # git の外では提出ごと(出力にそう書く)
    plain = tmp_path / "plain"
    shutil.copytree(root, plain)
    assert contrib.submission_sources(plain, contrib.load_dir(plain)[0]) == ({}, False)
    assert cli.main(["--lang", "ja", "contrib", "aggregate", str(plain)]) == 0
    assert "出どころは提出ごとに数えた" in capsys.readouterr().out


# --- 6. 版(B1・B4c) --------------------------------------------------------------------------------


def test_version_ranges_are_notices(db):
    r = contrib.validate_submission(sub(), db, check_filename=False)
    assert not {"tool_newer", "comfyui_newer", "comfyui_old"} & codes(r, "notices")
    d = sub()
    d["tool"]["version"] = "99.0.0"  # B4c
    d["comfyui_version"] = "9.99.999"  # B1b
    r = contrib.validate_submission(d, db, check_filename=False)
    assert r.ok and {"tool_newer", "comfyui_newer"} <= codes(r, "notices")
    d = sub(model_id="image_z_image_turbo_int8", settings={"width": 768, "height": 768, "steps": 6})
    d["comfyui_version"] = "0.3.0"  # テンプレートは 0.11.0 以上
    r = contrib.validate_submission(d, db, check_filename=False)
    assert r.ok and "comfyui_old" in codes(r, "notices")


# --- チェッカーの指摘(#90 の 2 回目) ---------------------------------------------------------------


def _repo_with_two_prs(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    root = repo / "data" / "contrib"
    root.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    write(root, sub(peak_vram_gb=6.9))
    write(root, sub(peak_vram_gb=7.0, seconds=41.0))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR 1: two runs")
    write(root, sub(peak_vram_gb=7.1, seconds=42.0))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "PR 2", email="second@example.invalid")
    return repo, root


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_bundled_status_needs_no_git_history(tmp_path, db):
    """同梱のまとめは提出ごとの出どころを記録するので、浅い clone(CI)・git の外(ci_local の WSL の写し)でも
    同じまとめを作り直せる。"""
    repo, root = _repo_with_two_prs(tmp_path)
    subs, _ = contrib.load_dir(root, db)
    sources, via_git = contrib.submission_sources(root, subs)
    assert via_git and len(set(sources.values())) == 2
    doc = json.loads(
        contrib.dumps(contrib.community_document(contrib.aggregate(subs, sources), subs, sources))
    )
    assert doc["measurements"][0]["n_sources"] == 2
    assert {e["source"] for e in doc["submissions"]} == set(sources.values())
    assert contrib.bundled_status(root, doc, db)["ok"]
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "file://" + repo.as_posix(), str(shallow))
    assert contrib.bundled_status(shallow / "data" / "contrib", doc, db)["ok"]
    nogit = tmp_path / "nogit" / "data" / "contrib"
    shutil.copytree(root, nogit)
    assert contrib.bundled_status(nogit, doc, db)["ok"]
    # 古い形(sha256 の文字列だけ)も読める(出どころは提出ごと)
    old = contrib.community_document(contrib.aggregate(subs), subs)
    old["submissions"] = [e["sha256"] for e in old["submissions"]]
    st = contrib.bundled_status(nogit, json.loads(json.dumps(old)), db)
    assert st["missing"] == [] and st["listed"] == 3


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_sources_follow_renames(tmp_path):
    """1 つの PR で足した 5 件をメンテナが別のフォルダへ動かしても、出どころは 1 つ(最初に足したコミット)。"""
    repo = tmp_path / "repo"
    root = repo / "data" / "contrib"
    root.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "x").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    for i in range(5):
        write(root, sub(peak_vram_gb=1.0 + i * 0.01, seconds=40.0 + i))
    shutil.move(str(root / "rtx-3070"), str(root / "old-slug"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one PR", email="sender@example.invalid")
    _git(repo, "mv", "data/contrib/old-slug", "data/contrib/rtx-3070")
    _git(repo, "commit", "-q", "-m", "maintainer: move", email="maintainer@example.invalid")
    subs, _ = contrib.load_dir(root)
    sources, via_git = contrib.submission_sources(root, subs)
    assert via_git and len(subs) == 5 and len(set(sources.values())) == 1
    who, _ = contrib.submission_submitters(root, subs)
    assert set(who.values()) == {"email:sender@example.invalid"}  # 動かしたメンテナではない


# Linux の CUDA は公称より少なく報告する(6 GB → 5934 MiB = 5.80 GiB など)。Windows の値も受け付ける
LINUX_WINDOWS_TOTALS = [
    ("NVIDIA GeForce RTX 2060", 5.80),
    ("NVIDIA GeForce RTX 3050 6GB", 5.80),
    ("NVIDIA GeForce RTX 3070", 7.79),
    ("NVIDIA GeForce RTX 3070", 7.99),
    ("NVIDIA GeForce RTX 3080", 9.78),
    ("NVIDIA GeForce RTX 2080 Ti", 10.76),
    ("NVIDIA GeForce RTX 3060", 11.77),
    ("NVIDIA GeForce RTX 4090", 23.69),
    ("NVIDIA GeForce RTX 4080", 15.92),
]


@pytest.mark.parametrize(("name", "vram"), LINUX_WINDOWS_TOTALS)
def test_reported_vram_totals_are_accepted(name, vram, db):
    gpu = {"name": name, "vram_gb": vram, "driver": "580.65.06", "vendor": "nvidia", "backend": "cuda"}
    r = contrib.validate_submission(sub(gpu=gpu, peak_vram_gb=3.0), db, check_filename=False)
    assert "gpu_inconsistent" not in codes(r), r.errors


def test_far_vram_is_a_notice(db):
    """#101 で変えた(以前は誤り): 名前の公称の量から遠い VRAM は確認 gpu_vram_nominal。"""
    for name, vram in (("NVIDIA GeForce RTX 3070", 7.4), ("NVIDIA GeForce RTX 3050 6GB", 7.99)):
        gpu = {"name": name, "vram_gb": vram, "driver": "580.65.06", "vendor": "nvidia", "backend": "cuda"}
        r = contrib.validate_submission(sub(gpu=gpu, peak_vram_gb=3.0), db, check_filename=False)
        assert "gpu_inconsistent" not in codes(r) and "gpu_vram_nominal" in codes(r, "notices"), (name, vram)


KNOWN_NVIDIA = [
    "NVIDIA GeForce RTX 5080",
    "NVIDIA GeForce RTX 3060 Ti",
    "NVIDIA GeForce RTX 4090 D",
    "NVIDIA GeForce RTX 5090 D",
    "NVIDIA GeForce RTX 5090 D v2",
    "NVIDIA GeForce RTX 4070 Ti SUPER",
    "NVIDIA GeForce RTX 3050 6GB",
    "NVIDIA GeForce RTX 3050 OEM",
    "NVIDIA GeForce RTX 3050 Ti Laptop GPU",
    "NVIDIA GeForce RTX 5090 Laptop GPU",
    "NVIDIA GeForce RTX 2080 Super with Max-Q Design",
    "NVIDIA GeForce RTX 2070 with Max-Q Design",
    "NVIDIA GeForce GTX 1660 SUPER",
    "NVIDIA GeForce GTX 1060 6GB",
    "NVIDIA GeForce GTX 1060 3GB",
    "NVIDIA TITAN RTX",
    "NVIDIA TITAN X (Pascal)",
    "NVIDIA RTX A2000 12GB",
    "NVIDIA RTX A1000 6GB Laptop GPU",
    "NVIDIA RTX 4000 Ada Generation",
    "NVIDIA RTX 4000 SFF Ada Generation",
    "NVIDIA RTX 3500 Ada Generation Laptop GPU",
    "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
    "NVIDIA RTX PRO 6000 Blackwell Server Edition",
    "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition",
    "NVIDIA RTX PRO 4000 Blackwell",
    "Quadro RTX 5000",
    "Quadro P4000",
    "Quadro T1000",
    "NVIDIA T1000 8GB",
    "Tesla T4",
    "Tesla V100-SXM2-16GB",
    "Tesla V100-PCIE-32GB",
    "NVIDIA A100-SXM4-80GB",
    "NVIDIA A100 80GB PCIe",
    "NVIDIA H100 80GB HBM3",
    "NVIDIA H100 NVL",
    "NVIDIA L40S",
    "NVIDIA GH200 480GB",
    "NVIDIA B200",
    "NVIDIA GB10",
    "NVIDIA GeForce MX550",
]
# #101 で変えた: 名前の表(#90 の nvidia_name_known)をやめ、断るのは汎用の名前・名前の代わりの値だけ。
# 以前「知らない」とした名前のうち、形に合うもの(表に無い実在の CMP 30HX・改造品の 4090 48GB・数字の語が
# 1 つの「RTX 3060 3060」など)は確認もなく使い、形に合わないもの(人名・数字の続き・知らない語)は確認
# gpu_name_form にして使う(作った名前は同じ文字列を返す PC にしか当たらない)
FORMED_NVIDIA = [
    "NVIDIA GeForce RTX 3060 16GB",
    "NVIDIA GeForce RTX 3070 99GB",
    "NVIDIA GeForce GTX 1660 SUPER generation design with",
    "NVIDIA GeForce RTX 3060 Ti D",
    "NVIDIA GeForce RTX 3070 D v2 OEM",
    "NVIDIA GeForce RTX 3070 sxm5",
    "NVIDIA GeForce RTX 4090 48GB",
    "NVIDIA CMP 30HX",
    # Checker の指摘で通すようにした実在の名前
    "Tesla V100-DGXS-32GB",
    "Tesla V100-FHHL-16GB",
    "NVIDIA P106-100",
    "NVIDIA P104-100",
    "NVIDIA P102-100",
    "NVIDIA RTX6000-Ada-48Q",
    "NVIDIA RTX 6000-Ada-48Q",
    "NVIDIA H20-3e",
    "NVIDIA GeForce MX450",
    "GRID RTX8000-48Q",
    "NVIDIA H100XM-80C",
    "NVIDIA A100-1-5C",
    "Tesla K20Xm",
    "Quadro GV100",
]
ILL_FORMED_NVIDIA = [
    "NVIDIA GeForce RTX 3070 taro",
    "NVIDIA GeForce RTX 3060 1234 5678",
    "NVIDIA GeForce RTX 4090 090-1234-5678",
    "NVIDIA GeForce RTX 3070 TY1990",
    "NVIDIA GeForce RTX 4090 ABC123 XYZ99",
    "NVIDIA Quadro RTX 31337",
    # Checker の指摘で通さないようにした作った名前(型番の頭の文字は NVIDIA の系列だけ・数字だけの語・繰り返し)
    "TY1985",
    "NVIDIA KEN1990",
    "NVIDIA tom99",
    "NVIDIA abc123def",
    "NVIDIA GeForce RTX 4090 A 1985",
    "NVIDIA GeForce RTX 4090 1985 AC",
    "NVIDIA GeForce RTX 3060 3060",
    "NVIDIA GeForce RTX 3070 8GB 8GB",
    "NVIDIA GeForce RTX 3060 Ti Ti super",
    "NVIDIA GeForce RTX 4090 (Taro)",
    "NVIDIA GeForce kay12 d",
]


@pytest.mark.parametrize("name", KNOWN_NVIDIA + FORMED_NVIDIA + ILL_FORMED_NVIDIA)
def test_non_generic_nvidia_names_are_used(name):
    assert contrib.gpu_known(name), name


@pytest.mark.parametrize("name", KNOWN_NVIDIA + FORMED_NVIDIA)
def test_known_nvidia_names_are_well_formed(name):
    assert nvidia_name_well_formed(name), name


@pytest.mark.parametrize("name", ILL_FORMED_NVIDIA)
def test_ill_formed_nvidia_names(name):
    assert not nvidia_name_well_formed(name), name


@pytest.mark.parametrize(
    "name", ["", " ", "N/A", "n/a", "none", "null", "unknown", "-", "NVIDIA Graphics Device"]
)
def test_placeholder_names_are_not_used(name):
    assert not contrib.gpu_known(name, "nvidia"), name


# bench の失敗の理由などに入る、個人の情報ではない文字(ノードの名前・git のコミット・公開のドメイン)
PUBLIC_NOTES = [
    "Qwen2VLForConditionalGeneration",
    "Wan22FunControlToVideo",
    "HunyuanVideo15ImageToVideo",
    "Hunyuan3Dv2ConditioningMultiView",
    "CosmosPredict2ImageToVideoLatent",
    "EmptyAceStep15LatentAudio",
    "DownloadAndLoadDepthAnythingV2Model",
    "ComfyUI 0.3.68 commit a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    "https を使わず huggingface.co と hf.co と comfy.org と pytorch.org を見た",
    "github.com/comfyanonymous の版",
]


@pytest.mark.parametrize("text", PUBLIC_NOTES)
def test_public_words_in_notes_are_kept(text):
    assert not contrib._hits(text), text
    note = f"失敗: {text} で止まった。計測 13 回"
    assert contrib.scrub_text(note) == note


def test_known_misses_are_documented():
    """自動の点検は形を見るだけ(意味は分からない)。見逃すものは CONTRIBUTING に書いた
    (利用者が note を読む)。"""
    for text in ("yamada-desktop", "user: yamada", "t a r o @ g m a i l", "山田太郎のPC", "dGFybw=="):
        assert not contrib._hits(text), text
    for page in ("CONTRIBUTING.md", "CONTRIBUTING.ja.md"):
        assert "yamada-desktop" in Path(page).read_text(encoding="utf-8")


def test_stale_rows_are_listed(db):
    """一覧のファイルが変わった(files_fp が違う)同梱のまとめの行は、--check と bundled_status で知らせる。"""
    entry = db.get("flux1_krea_dev")
    d = sub(
        model_id=entry.id,
        settings={"width": 1024, "height": 1024, "steps": 20},
        peak_vram_gb=7.6,
        files_fp=files_fingerprint(entry),
    )
    s = as_sub(d)
    doc = contrib.community_document(contrib.aggregate([s]), [s])
    assert contrib.bundled_status(Path("no-such-dir"), doc, db)["stale"] == []
    changed = copy.deepcopy(db)
    changed.get(entry.id).files[0].sha256 = "0" * 64
    assert contrib.bundled_status(Path("no-such-dir"), doc, changed)["stale"] == [
        [entry.id, "NVIDIA GeForce RTX 3070"]
    ]


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("git") is None, reason="CI と同じ bash の検査(Linux・WSL で流す)"
)
def test_workflow_add_only_step_in_a_real_repo(tmp_path):
    """contrib.yml の「足すだけ」の段を本物の git で動かす(HEAD^1 と HEAD を比べる)。"""
    import yaml

    wf = yaml.safe_load(Path(".github/workflows/contrib.yml").read_text(encoding="utf-8"))
    run = next(s for s in wf["jobs"]["validate"]["steps"] if "足すだけ" in s.get("name", ""))["run"]
    step = tmp_path / "step.sh"
    step.write_text(run, encoding="utf-8")
    good = "data/contrib/rtx-3070/2026-10-27-0123abcd.json"

    def case(change) -> int:
        repo = tmp_path / f"r{len(list(tmp_path.iterdir()))}"
        (repo / "data/contrib/rtx-3070").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        (repo / "data/contrib/rtx-3070/2026-10-01-aaaaaaaa.json").write_text("{}")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        change(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "--allow-empty", "-m", "pr")
        env = {**os.environ, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
        return subprocess.run(
            ["bash", str(step)], cwd=repo, env=env, capture_output=True, check=False
        ).returncode

    assert case(lambda r: (r / good).write_text("{}")) == 0
    assert case(lambda r: (r / "data/contrib/rtx-3070/2026-10-01-aaaaaaaa.json").unlink()) == 1  # 消す
    assert case(lambda r: (r / "data/contrib/rtx-3070/2026-10-01-aaaaaaaa.json").write_text("[]")) == 1
    assert case(lambda r: (r / good.replace(".json", ".JSON")).write_text("{}")) == 1
    assert case(lambda r: (r / "data/contrib/README.md").write_text("x")) == 1
    assert case(lambda r: None) == 1  # 何も足していない


# --- 3 回目の仕上げ ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "nvidia.com.taro.jp",
        "evil.github.com.attacker.jp",
        "pytorch.org.example.com",
        "github.company.jp",
        "taro.github.io",
    ],
)
def test_public_domains_must_end_the_host(text):
    assert "domain" in {k for k, _ in contrib._hits(text)}, text


def test_hex_runs():
    """32 桁以上の 16 進は当てる(16 進で書き換えたパスも)。ちょうど 40 桁(git のコミット)・
    64 桁(sha256)は当てない。"""
    assert "hex_id" in {k for k, _ in contrib._hits(rb"C:\Users\yamada.taro\ComfyUI".hex())}
    assert "hex_id" in {k for k, _ in contrib._hits("1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d")}
    assert not contrib._hits("commit a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")
    assert not contrib._hits("sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")


def test_short_padded_base64():
    """= の詰め物があれば 16 字から当てる(「C:\\Users\\taro」の base64)。"""
    enc = base64.b64encode(rb"C:\Users\taro").decode()
    assert enc.endswith("=") and "encoded" in {k for k, _ in contrib._hits(enc)}
    for name in (
        "t2v14Bfp8e4m3fnscaledKJ",
        "Wan2.2-Lightning_I2V-A14B-4steps-lora_HIGH_fp16",
        "SDXL1.0VAEFix0.9",
    ):
        assert not contrib._hits(name), name


def test_known_misses_include_user_names_under_public_domains():
    for text in ("github.com/taro", "hf.co/yamada"):
        assert not contrib._hits(text), text
    assert "github.com/taro" in Path("CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "github.com/taro" in Path("CONTRIBUTING.ja.md").read_text(encoding="utf-8")


def test_bench_gpu_tools_are_blocked_in_tests():
    """試験では bench が本物の nvidia-smi を動かさない(tests/conftest.py の _no_real_hardware)。"""
    from genmodelfit import bench

    assert bench._nvidia_smi() is None and bench.smi_gpus() == [] and bench.gpu_used_now("0") is None


BOT = "41898282+github-actions[bot]@users.noreply.github.com"


class _Repo:
    """main にマージのコミットで PR を入れていく試験用のリポジトリ。"""

    def __init__(self, tmp_path: Path):
        self.repo = tmp_path / "repo"
        self.root = self.repo / "data" / "contrib"
        self.root.mkdir(parents=True)
        self.v = 6.0
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "x").write_text("x")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "init")

    def _add(self, msg: str, email: str) -> None:
        self.v = round(self.v + 0.01, 2)
        write(self.root, sub(peak_vram_gb=self.v, seconds=40.0 + self.v))
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", msg, email=email)

    def pr(self, branch: str, commits: list[tuple[str, str]], owner: str = "laxshmate", n: int = 1) -> None:
        """branch で commits(本文, メール)を足し、GitHub の「Create a merge commit」と同じ 1 行目で
        マージする。"""
        _git(self.repo, "switch", "-q", "-c", branch)
        for msg, email in commits:
            self._add(msg, email)
        _git(self.repo, "switch", "-q", "main")
        _git(
            self.repo,
            "merge",
            "-q",
            "--no-ff",
            branch,
            "-m",
            f"Merge pull request #{n} from {owner}/{branch}",
            email="maintainer@example.invalid",
        )

    def direct(self, msg: str, email: str) -> None:
        self._add(msg, email)

    def sources(self) -> tuple[list, dict, dict]:
        subs, _ = contrib.load_dir(self.root)
        who, ok = contrib.submission_submitters(self.root, subs)
        src, ok2 = contrib.submission_sources(self.root, subs)
        assert ok and ok2
        return subs, who, src


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_one_person_is_one_source(tmp_path):
    """同じ人が別々の PR・Issue で何件送っても 1 つの出どころ(#113)。人は GitHub のアカウント: PR は
    マージのコミットの 1 行目の PR の元のアカウント、Issue のボットの PR は Submitted-by の人。
    出どころの値にメールアドレスやアカウント名を書かない。"""
    r = _Repo(tmp_path)
    r.pr("fix-1", [("PR 1", "alice@example.invalid")], owner="alice", n=1)
    r.pr("fix-2", [("PR 2", "alice@work.invalid")], owner="alice", n=2)  # 同じアカウント・別のメール(B)
    r.pr("measurement/issue-3", [("issue 3\n\nSubmitted-by: github:Bob", BOT)], n=4)
    r.pr("measurement/issue-5", [("issue 5\n\nSubmitted-by: github:bob", BOT)], n=6)  # 同じ人の別の Issue
    r.pr("bobs-run", [("PR by bob", "bob@example.invalid")], owner="bob", n=7)  # 同じ人の PR
    r.pr("measurement/issue-8", [("issue 8\n\nSubmitted-by: github:carol", BOT)], n=9)
    subs, who, src = r.sources()
    assert sorted(set(who.values())) == ["github:alice", "github:bob", "github:carol"]
    assert len(set(src.values())) == 3
    text = json.dumps(src)
    assert "alice" not in text and "bob" not in text and "@" not in text
    assert contrib.aggregate(subs, src)[0]["n_sources"] == 3
    mid = subs[0].data["measurement"]["model_id"]
    assert contrib.submitter_count(r.root, "BOB", model_id=mid, gpu_name="NVIDIA GeForce RTX 3070") == 3
    assert contrib.submitter_count(r.root, "dave", model_id=mid, gpu_name="NVIDIA GeForce RTX 3070") == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_same_account_two_prs_with_different_emails_is_one_source(tmp_path):
    r = _Repo(tmp_path)
    r.pr("a", [("1", "one@example.invalid")], owner="taro", n=1)
    r.pr("b", [("2", "two@example.invalid")], owner="Taro", n=2)
    _subs, who, src = r.sources()
    assert set(who.values()) == {"github:taro"} and len(set(src.values())) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_claimed_noreply_address_does_not_join_the_victim(tmp_path):
    """PR のコミットに人の noreply のアドレスを書いても、その人の出どころとつながらない
    (PR の元のアカウントになる)。"""
    r = _Repo(tmp_path)
    r.pr("measurement/issue-1", [("issue\n\nSubmitted-by: github:victim", BOT)], n=2)
    r.pr("evil", [("x", "123+victim@users.noreply.github.com")], owner="mallory", n=3)
    _subs, who, src = r.sources()
    assert sorted(set(who.values())) == ["github:mallory", "github:victim"]
    assert len(set(src.values())) == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_one_pr_is_at_most_one_source(tmp_path):
    """1 つの PR は、中のコミットの書いた人を何人にしても(ボットのメールアドレスと Submitted-by を偽っても)
    1 つの出どころで、人は PR の元のアカウント(#90 と同じ。git の書いた人は誰でも書ける)。"""
    r = _Repo(tmp_path)
    commits = [(f"run {i}", f"sock{i}@example.invalid") for i in range(4)]
    commits += [(f"fake\n\nSubmitted-by: github:puppet{i}", BOT) for i in range(3)]
    r.pr("many-names", commits, owner="sock", n=1)
    subs, who, src = r.sources()
    assert len(subs) == 7 and len(set(src.values())) == 1 and set(who.values()) == {"github:sock"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_fork_branch_named_like_the_bot_is_not_trusted(tmp_path):
    """フォークの measurement/issue-N のブランチ(マージのコミットの持ち主がこのリポジトリと違う)の
    Submitted-by は信じず、人はフォークの持ち主。直接 main に入れたコミット(squash など)の
    Submitted-by も信じない。"""
    r = _Repo(tmp_path)
    r.pr("measurement/issue-1", [("x\n\nSubmitted-by: github:victim", BOT)], owner="mallory", n=1)
    r.direct("squashed\n\nSubmitted-by: github:victim2", BOT)
    _subs, who, _src = r.sources()
    assert "github:mallory" in who.values() and not any("victim" in k for k in who.values())


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_direct_commits_fall_back_to_the_author_email(tmp_path):
    """GitHub の PR のマージでないもの(main に直接・squash・rebase)は、書いた人のメールアドレスで
    数える(弱い)。"""
    r = _Repo(tmp_path)
    r.direct("a", "a@example.invalid")
    r.direct("b", "a@example.invalid")
    r.direct("c", "c@example.invalid")
    _subs, who, src = r.sources()
    assert set(who.values()) == {"email:a@example.invalid", "email:c@example.invalid"}
    assert len(set(src.values())) == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_bot_trailer_id_survives_a_rename(tmp_path):
    """Issue のボットのコミットの Submitted-by-id(アカウントの数の ID)で数える: 名前を変えても同じ人。
    ID の無い前の版のコミットはアカウント名で数え、数字でない ID は使わない。"""
    r = _Repo(tmp_path)
    r.pr("measurement/issue-1", [("a\n\nSubmitted-by: github:oldname\nSubmitted-by-id: 4242", BOT)], n=2)
    r.pr("measurement/issue-3", [("b\n\nSubmitted-by: github:newname\nSubmitted-by-id: 4242", BOT)], n=4)
    r.pr("measurement/issue-5", [("c\n\nSubmitted-by: github:legacy", BOT)], n=6)  # ID の無い前の版
    r.pr("measurement/issue-7", [("d\n\nSubmitted-by: github:odd\nSubmitted-by-id: 12ab", BOT)], n=8)
    subs, who, src = r.sources()
    assert sorted(set(who.values())) == ["github-id:4242", "github:legacy", "github:odd"]
    assert len(set(src.values())) == 3
    mid = subs[0].data["measurement"]["model_id"]
    gpu = "NVIDIA GeForce RTX 3070"
    assert contrib.submitter_count(r.root, "newname", model_id=mid, gpu_name=gpu, user_id=4242) == 2
    assert contrib.submitter_count(r.root, "legacy", model_id=mid, gpu_name=gpu) == 1
    assert contrib.submitter_count(r.root, "x", model_id=mid, gpu_name=gpu, user_id="4242; rm") == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_issue_and_fork_pr_of_one_account_is_one_source(tmp_path):
    """同じアカウントが Issue(ボットのコミットは ID の鍵)とフォークの PR(PR の元のアカウント名の鍵)で
    送ったものは 1 つの出どころ(アカウントごとに 1 回。docs/schema.md「contrib」)。
    2026-11-11 まで 2 つに数えていた。"""
    r = _Repo(tmp_path)
    r.pr("measurement/issue-1", [("a\n\nSubmitted-by: github:Alice\nSubmitted-by-id: 77", BOT)], n=2)
    r.pr("my-run", [("b", "alice@example.invalid")], owner="alice", n=3)  # 同じ人のフォークの PR
    r.pr("measurement/issue-4", [("c\n\nSubmitted-by: github:legacy", BOT)], n=5)  # 前の版のボット(名前だけ)
    r.pr("legacy-run", [("d", "l@example.invalid")], owner="legacy", n=6)
    r.pr("other", [("e", "o@example.invalid")], owner="bob", n=7)
    _subs, who, src = r.sources()
    assert sorted(set(who.values())) == ["github-id:77", "github:alice", "github:bob", "github:legacy"]
    assert len(set(src.values())) == 3  # alice(ID と名前) / legacy / bob


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")
def test_name_from_a_fork_pr_alone_does_not_link_ids(tmp_path):
    """名前と ID を結ぶのは受け取るボットのコミット(両方の trailer)だけ。フォークの PR の名前が、
    ID だけを持つ別の人に結び付かない。"""
    r = _Repo(tmp_path)
    r.pr("measurement/issue-1", [("a\n\nSubmitted-by: github:carol\nSubmitted-by-id: 5", BOT)], n=2)
    r.pr("x", [("b", "m@example.invalid")], owner="mallory", n=3)
    _subs, _who, src = r.sources()
    assert len(set(src.values())) == 2
