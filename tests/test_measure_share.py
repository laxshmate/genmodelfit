# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""measure・share・実測の Issue の受け取り(#113)。

本物の GPU・ComfyUI・ブラウザ・クリップボード・通信には触らない。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from genmodelfit import bench, cli, contrib, measure
from genmodelfit.db import cache_dir
from genmodelfit.db import load as load_db
from genmodelfit.models import Gpu, Measurement, ModelEntry, ModelFile, SystemInfo

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "contrib"
GOOD = sorted(p for p in FIX.rglob("*.json") if p.parent.name == "rtx-3070")
GPU0 = {"index": 0, "uuid": "GPU-aaaa", "name": "NVIDIA GeForce RTX 3070", "driver": "566.03"}


def pc(vram: float = 8.0, name: str = "NVIDIA GeForce RTX 3070") -> SystemInfo:
    g = Gpu(name=name, vendor="nvidia", vram_total_gb=vram, backend="cuda")
    return SystemInfo(
        os="Windows 11", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g]
    )


def no_gpu() -> SystemInfo:
    return SystemInfo(os="Windows 11", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[])


@pytest.fixture(scope="module")
def db():
    return load_db(use_cache=False)


@pytest.fixture
def this_pc(monkeypatch):
    """この PC は RTX 3070 8 GB(本物の検出は呼ばない)。"""
    s = pc()
    monkeypatch.setattr(cli, "_system", lambda args: s)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: s)
    return s


@pytest.fixture
def fake_gpu(monkeypatch):
    """bench の GPU の確かめ(tests/test_bench.py と同じ偽物。空いている RTX 3070 が 1 枚)。"""
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(bench, "smi_gpus", lambda: [dict(GPU0)])
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    real_run = bench.run

    def run(*a, **k):  # 偽の ComfyUI はすぐ終わるので、検査に通る時間にする
        res = real_run(*a, **k)
        res.measurement.seconds = 2.0
        return res

    monkeypatch.setattr(bench, "run", run)


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


def answers(monkeypatch, *values: str | None):
    """input() の答えを順に返す(None は EOF)。尋ねた文も残す。"""
    asked: list[str] = []
    it = iter(values)

    def ask(prompt=""):
        asked.append(prompt)
        v = next(it)
        if v is None:
            raise EOFError
        return v

    monkeypatch.setattr("builtins.input", ask)
    return asked


def entry(**kw) -> ModelEntry:
    files = kw.pop(
        "files",
        [
            ModelFile(name="m.safetensors", directory="diffusion_models", size_bytes=10),
            ModelFile(name="t.safetensors", directory="text_encoders", size_bytes=20),
        ],
    )
    base = {"id": "image_x", "name": "X", "modality": "image", "files": files}
    return ModelEntry(**{**base, **kw})


def meas(model_id: str = "image_x", **kw) -> Measurement:
    base = {
        "model_id": model_id,
        "gpu_name": "NVIDIA GeForce RTX 3070",
        "gpu_vram_gb": 8.0,
        "peak_vram_gb": 5.0,
    }
    return Measurement(**{**base, **kw})


# --- 入っているか・時間・この GPU の実測 -----------------------------------------------------------


def test_installed_state(tmp_path):
    e = entry()
    assert measure.installed_state(e, []).state == "unknown"
    assert measure.installed_state(entry(files=[]), [tmp_path]).state == "unknown"
    root = tmp_path / "models"
    st = measure.installed_state(e, [root])
    assert st.state == "no" and st.missing_bytes == 30 and st.missing_files == 2 and st.total_bytes == 30
    (root / "diffusion_models").mkdir(parents=True)
    (root / "diffusion_models" / "m.safetensors").write_bytes(b"x" * 10)
    # ComfyUI の古い名前(text_encoders → clip)のフォルダにあっても入っているとみなす
    (root / "clip").mkdir()
    (root / "clip" / "t.safetensors").write_bytes(b"y" * 19)  # 大きさが違えば入っていない
    st = measure.installed_state(e, [tmp_path / "other", root])
    assert st.state == "no" and st.missing_bytes == 20 and st.missing_files == 1
    (root / "clip" / "t.safetensors").write_bytes(b"y" * 20)
    assert measure.installed_state(e, [root]).state == "yes"


def test_installed_state_never_leaves_the_folder(tmp_path):
    e = entry(files=[ModelFile(name="../../evil.bin", directory="vae", size_bytes=1)])
    (tmp_path / "evil.bin").write_bytes(b"x")
    assert measure.installed_state(e, [tmp_path / "models"]).state == "no"


def test_time_hint():
    e = entry()
    assert measure.time_hint(e, []) == (60, "typical")
    assert measure.time_hint(entry(modality="video"), []) == (300, "typical")
    ms = [meas(seconds=18.0), meas(seconds=41.0), meas(seconds=23.0), meas(seconds=999.0, outcome="oom")]
    assert measure.time_hint(e, ms) == (30, "measured")  # 中央値 23 → 10 秒単位に切り上げ。oom は使わない
    assert measure.time_hint(e, [meas(seconds=0.4)]) == (10, "measured")


def test_has_measurement_here():
    e = entry()
    g = pc().best_gpu
    assert not measure.has_measurement_here(e, [], g)
    assert measure.has_measurement_here(e, [meas()], g)
    assert measure.has_measurement_here(e, [meas(outcome="oom")], g)
    # 作業用メモリだけの実測も数える(同じ GPU で測り直しても材料が増えない)
    assert measure.has_measurement_here(e, [meas(vram_metric="torch_reserved", offload="dynamic")], g)
    assert not measure.has_measurement_here(e, [meas(outcome="error")], g)
    assert not measure.has_measurement_here(e, [meas(standard=False)], g)
    assert not measure.has_measurement_here(e, [meas(gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=24)], g)
    assert not measure.has_measurement_here(e, [meas(gpu_vram_gb=16.0)], g)  # 同じ名前でも VRAM が違う
    assert not measure.has_measurement_here(e, [meas(model_id="image_y")], g)


# --- 提案 -----------------------------------------------------------------------------------------


def test_suggestions_rules(db):
    s = pc()
    sugg = measure.suggestions(db, s)
    assert sugg, "RTX 3070 に載るテンプレートはある"
    by_id = {m.id: m for m in db.models}
    from genmodelfit.recommend import judge

    keys = set()
    for x in sugg:
        m = by_id[x.model_id]
        assert m.source == "comfyui-template" and not x.model_id.startswith("hf:")
        f = judge(m, s, db, settings=x.settings)  # 設定を変えた測定は、その設定で載るもの(#116)
        assert f.estimate.fit in ("perfect", "good") and f.estimate.run_mode == "gpu"
        # 「測ってある」はこの PC の実測だけ(同梱の作者の実測・ほかの利用者のまとめは提案から外さない)
        here = measure.measured_by_this_pc(db.measurements)
        assert not measure.has_measurement_here(m, here, s.best_gpu, x.settings)
        key = (x.group_key, tuple(x.args))
        assert key not in keys  # 同じモデル・同じ設定は 1 件
        keys.add(key)
        assert x.installed.state == "unknown" and x.seconds % 10 == 0
    order = [(x.seconds, x.installed.missing_bytes) for x in sugg]
    assert order == sorted(order)
    assert measure.suggestions(db, no_gpu()) == []


def test_suggestions_put_installed_first_and_drop_measured(db, tmp_path):
    s = pc()
    first = measure.suggestions(db, s)
    last = first[-1]
    m = db.get(last.model_id)
    root = tmp_path / "models"
    from genmodelfit.downloader import directory_refs, infer_directory

    refs = directory_refs(db)
    for f in m.files:
        d = f.directory or infer_directory(f, refs)[0]
        p = root / d / f.name
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            fh.truncate(f.size_bytes or 0)  # 大きさだけ同じ(中身は見ない)
    again = measure.suggestions(db, s, roots=[root])
    assert again[0].model_id == last.model_id and again[0].installed.state == "yes"
    # この GPU で測ったら出さない
    db2 = load_db(use_cache=False)
    db2.measurements.append(meas(model_id=first[0].model_id))
    # テンプレートのままの行は出さない(設定を変えた測定は、その設定の実測が無いので残る。#116)
    rows = [x for x in measure.suggestions(db2, s) if x.model_id == first[0].model_id]
    assert all(x.settings is not None for x in rows)


def test_resolve_keeps_template_workflows(db):
    s = pc()
    f = measure.resolve(db, s, "image_sdxl_simple")
    assert f.model.id == "image_sdxl_simple"
    hf = next(m for m in db.models if m.source == "huggingface")
    from genmodelfit.downloader import ModelNotFound

    with pytest.raises(ModelNotFound, match="bench --workflow"):
        measure.resolve(db, s, hf.id)


def test_library_writes_nothing(db, capfd, tmp_path):
    measure.suggestions(db, pc(), roots=[tmp_path])
    measure.resolve(db, pc(), "image_sdxl_simple")
    measure.saved_submissions()
    contrib.intake_issue("x", "a", 1, db=db)
    out, err = capfd.readouterr()
    assert out == "" and err == ""


# --- measure の CLI --------------------------------------------------------------------------------


def test_measure_lists_suggestions_without_a_terminal(capsys, monkeypatch, this_pc):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("端末でなければ尋ねない"))
    assert cli.main(["--lang", "ja", "measure", "-n", "3"]) == 0
    cap = capsys.readouterr()
    lines = [x for x in cap.out.splitlines() if re.match(r"\s+\d+\. ", x)]
    assert len(lines) == 3 and "目安" in cap.out and "genmodelfit measure <ID>" in cap.err


def test_measure_suggestions_json(capsys, this_pc):
    assert cli.main(["measure", "--json", "--limit", "2"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["command"] == "measure" and d["action"] == "suggest" and len(d["suggestions"]) == 2
    assert d["comfyui"] == {"url": None, "found_by": None}  # 探すのを止めてある(conftest)
    s = d["suggestions"][0]
    assert set(s) >= {"model_id", "installed", "download_bytes", "seconds", "seconds_basis", "fit_level"}


def test_measure_choice_q_does_nothing(capsys, monkeypatch, this_pc):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "q")
    monkeypatch.setattr(bench, "run", lambda *a, **k: pytest.fail("測らない"))
    assert cli.main(["--lang", "ja", "measure", "--url", "http://127.0.0.1:9"]) == 0
    assert "測りませんでした" in capsys.readouterr().err


def test_measure_list_without_comfyui_does_not_ask(capsys, monkeypatch, this_pc):
    # 一覧は見せるが、ComfyUI が起動していなければ番号を尋ねずに起動の仕方を出す(#123)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("起動していなければ尋ねない"))
    assert cli.main(["--lang", "ja", "measure", "-n", "3"]) == 1
    cap = capsys.readouterr()
    assert len([x for x in cap.out.splitlines() if re.match(r"\s+\d+\. ", x)]) == 3
    assert "エラー: ComfyUI を探していません" in cap.err  # 試験では探すのを止めてある(conftest)
    assert "ComfyUI を起動してから" in cap.err and "--url" in cap.err and "Traceback" not in cap.err


def test_measure_without_comfyui_explains_how_to_start(capsys, monkeypatch, this_pc):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("起動していなければ尋ねない"))
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple"]) == 1
    cap = capsys.readouterr()
    assert "=== 測る前に ===" in cap.out and "見つかりません(起動していない)" in cap.out
    assert "ComfyUI を起動してから" in cap.err and "--url" in cap.err and "Traceback" not in cap.err


def test_measure_finds_a_running_comfyui(monkeypatch, this_pc, tmp_path):
    from genmodelfit import comfyfind

    base = tmp_path / "ComfyUI"
    (base / "models").mkdir(parents=True)
    (base / "main.py").write_text("")
    cand = comfyfind.Candidate(base, "running", "127.0.0.1:8000")
    monkeypatch.setattr(comfyfind, "search_disabled", lambda: False)
    monkeypatch.setattr(comfyfind, "running_candidates", lambda *a, **k: [cand])
    args = cli.build_parser().parse_args(["measure"])
    url, how, running = cli._measure_comfyui(args)
    assert (url, how, running) == ("http://127.0.0.1:8000", "running", cand)
    assert cli._measure_roots(args, running) == [base / "models"]
    args = cli.build_parser().parse_args(["measure", "--url", "http://10.0.0.2:8188"])
    assert cli._measure_comfyui(args) == ("http://10.0.0.2:8188", "option", None)


def test_measure_runs_and_points_to_share(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "")  # Enter = はい
    with FakeComfy() as fake:
        assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--url", fake.url]) == 0
    out = capsys.readouterr().out
    assert asked == ["測りますか? [Y/n] "]  # 測る前に 1 回だけ。送るかどうかは尋ねない
    assert "目安" in out and "何も送りません" in out
    assert "(--url で指定)" in out
    subs = list((cache_dir() / "submissions" / "rtx-3070").glob("*.json"))
    assert len(subs) == 1 and f"送る用:    {subs[0]}" in out
    assert out.rstrip().splitlines()[-1].startswith("genmodelfit share で送ると、")
    assert list((cache_dir() / "measurements").glob("*.json"))


def test_measure_no_means_no(capsys, monkeypatch, this_pc, fake_gpu):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "n")
    monkeypatch.setattr(bench, "run", lambda *a, **k: pytest.fail("測らない"))
    # やめただけなので誤りではない(share の q と同じ)
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--url", "http://127.0.0.1:9"]) == 0
    assert "測りませんでした" in capsys.readouterr().err


def test_measure_json_needs_yes_and_reports(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    assert cli.main(["measure", "image_sdxl_simple", "--json", "--url", "http://127.0.0.1:9"]) == 1
    assert "--yes" in json.loads(capsys.readouterr().out)["error"]
    with FakeComfy() as fake:
        assert cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["action"] == "measure" and d["model_id"] == "image_sdxl_simple"
    assert d["comfyui"] == {"url": fake.url, "found_by": "option"} and d["installed"]["state"] == "unknown"
    assert d["time_hint"]["basis"] in ("measured", "typical") and d["measurement"]["outcome"] == "ok"
    assert Path(d["submission"]["saved_to"]).is_file() and "genmodelfit share" in d["hint"]


def test_measure_busy_gpu_is_not_measured(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (6000.0, 90.0))
    with FakeComfy() as fake:
        assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--yes", "--url", fake.url]) == 1
    err = capsys.readouterr().err
    assert "エラー" in err and "次にすること: ほかの生成" in err  # measure に無い --force だけを案内しない
    assert not (cache_dir() / "submissions").exists()


def test_measure_offers_download_when_not_installed(capsys, monkeypatch, this_pc, tmp_path):
    comfy = tmp_path / "ComfyUI"
    (comfy / "models").mkdir(parents=True)
    (comfy / "main.py").write_text("")
    calls: list[str] = []

    def fake_download(args, model_id, **k):
        calls.append(model_id)
        return 1  # 利用者が落とすのを断った

    monkeypatch.setattr(cli, "_measure_download", fake_download)
    monkeypatch.setattr(bench, "run", lambda *a, **k: pytest.fail("落とせなければ測らない"))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "y")
    argv = ["--lang", "ja", "measure", "image_sdxl_simple", "--url", "http://127.0.0.1:9"]
    assert cli.main([*argv, "--comfyui-dir", str(comfy)]) == 1
    out = capsys.readouterr().out
    assert calls == ["image_sdxl_simple"] and "入っていない(落とす量" in out and "先に落とします" in out
    # --json では落とさず、download を案内する
    calls.clear()
    assert (
        cli.main(
            ["measure", "image_sdxl_simple", "--json", "--yes", "--comfyui-dir", str(comfy), "--url", "u"]
        )
        == 1
    )
    d = json.loads(capsys.readouterr().out)
    assert calls == [] and "genmodelfit download image_sdxl_simple" in d["error"]


def test_measure_download_uses_the_download_command(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_download", lambda ns: seen.append(ns) or 0)
    args = cli.build_parser().parse_args(["measure", "x", "--yes", "--comfyui-dir", "D:/C"])
    assert cli._measure_download(args, "image_sdxl_simple") == 0
    ns = seen[0]
    assert ns.model == "image_sdxl_simple" and ns.yes is True and ns.comfyui_dir == "D:/C"
    # 同意の決まりは download のまま: 無検閲・ライセンス不明の同意は足さない
    assert ns.content == "sfw" and ns.include_unknown_license is False


def test_measure_download_reaches_the_plan_without_crashing(db, monkeypatch, tmp_path):
    # 0.12.0 では content が None のまま plan_download に渡り ValueError で落ちた(#124 の本番で見つかった)
    from genmodelfit import downloader

    seen = []
    real = downloader.plan_download

    def spy(*a, **k):
        seen.append(k.get("content"))
        return real(*a, **k)

    monkeypatch.setattr(downloader, "plan_download", spy)
    monkeypatch.setattr(cli, "plan_download", spy, raising=False)
    (tmp_path / "models").mkdir()
    (tmp_path / "main.py").write_text("")
    args = cli.build_parser().parse_args(["measure", "x", "--comfyui-dir", str(tmp_path)])
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    cli._measure_download(args, "image_sdxl_simple")  # 同意しないので落とさずに終わる
    assert seen == ["sfw"]


def test_measure_unknown_model_lists_candidates(capsys, this_pc):
    assert cli.main(["measure", "sdxl", "--json", "--yes"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert d["candidates"] and "error" in d


def test_measure_without_gpu(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_system", lambda args: no_gpu())
    assert cli.main(["--lang", "ja", "measure"]) == 1
    assert "GPU が見つかりません" in capsys.readouterr().err


# --- share の CLI ---------------------------------------------------------------------------------


def _saved(n: int = 2) -> list[Path]:
    out = []
    for i in range(n):
        d = json.loads(GOOD[0].read_text(encoding="utf-8"))
        d["measurement"]["peak_vram_gb"] = 6.0 + i
        d["measurement"]["note"] = None
        out.append(contrib.write_submission(d, str(measure.submissions_dir()) + "/"))
    return out


def test_share_lists_unsent_and_marks_after_opening(capsys, monkeypatch, browser, clipboard):
    paths = _saved(2)
    assert cli.main(["share", "--issue", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["command"] == "share" and d["listed"] is True and len(d["submissions"]) == 2
    assert all(s["shared"] is False for s in d["submissions"])
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "2")
    assert cli.main(["--lang", "ja", "share", "--issue"]) == 0
    out = capsys.readouterr().out
    assert (
        "=== 送っていない実測 ===" in out
        and len(browser) == 1
        and "issues/new?template=measurement.yml" in browser[0]
    )
    marks = json.loads(measure.shared_path().read_text(encoding="utf-8"))["shared"]
    assert len(marks) == 1
    assert cli.main(["share", "--issue", "--json"]) == 0
    left = json.loads(capsys.readouterr().out)["submissions"]
    assert len(left) == 1 and Path(left[0]["file"]) in paths
    assert cli.main(["share", "--issue", "--json", "--all"]) == 0
    assert len(json.loads(capsys.readouterr().out)["submissions"]) == 2


def test_share_nothing_to_send(capsys, monkeypatch, browser):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--lang", "ja", "share", "--issue"]) == 0
    assert "送っていない実測はありません" in capsys.readouterr().out and browser == []


def test_share_print_url(capsys, monkeypatch, browser):
    p = _saved(1)[0]
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["share", "--issue", str(p), "--print-url"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert (
        len(out) == 1
        and out[0].startswith("https://github.com/laxshmate/genmodelfit/issues/new?")
        and browser == []
    )


def test_share_not_marked_when_the_browser_fails(capsys, monkeypatch):
    p = _saved(1)[0]
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--lang", "ja", "share", "--issue", str(p)]) == 0
    assert "次のページ" in capsys.readouterr().out  # 開けなければ URL を見せる
    assert measure.load_shared() == {}


def test_contrib_share_is_share(capsys):
    p = _saved(1)[0]
    assert cli.main(["contrib", "share", str(p), "--json"]) == 0
    a = json.loads(capsys.readouterr().out)
    assert cli.main(["share", "--issue", str(p), "--json"]) == 0
    b = json.loads(capsys.readouterr().out)
    assert a["command"] == "contrib" and a["action"] == "share" and b["command"] == "share"
    assert a["share"] == b["share"]


# --- クリップボード -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "env", "tools", "argv", "encoding"),
    [
        ("win32", {}, {"clip"}, ["clip"], "utf-16-le"),
        ("darwin", {}, {"pbcopy"}, ["pbcopy"], "utf-8"),
        ("linux", {"WAYLAND_DISPLAY": "w"}, {"wl-copy", "xclip"}, ["wl-copy"], "utf-8"),
        ("linux", {}, {"xclip", "xsel"}, ["xclip", "-selection", "clipboard"], "utf-8"),
        ("linux", {}, {"xsel"}, ["xsel", "--clipboard", "--input"], "utf-8"),
    ],
)
def test_copy_to_clipboard_uses_os_tools(monkeypatch, platform, env, tools, argv, encoding):
    import shutil

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}" if name in tools else None)
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert cli._copy_to_clipboard('{"note": "測った"}') is True
    cmd, kw = calls[0]
    assert cmd == [f"/bin/{argv[0]}", *argv[1:]] and "shell" not in kw
    data = kw["input"]
    text = data.decode(encoding)
    assert text.lstrip("\ufeff") == '{"note": "測った"}'


def test_copy_to_clipboard_without_a_tool(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("道具が無ければ呼ばない"))
    assert cli._copy_to_clipboard("x") is False


def test_copy_to_clipboard_failure_is_false(monkeypatch):
    import shutil

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: "clip")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("clip", 10)

    monkeypatch.setattr(subprocess, "run", boom)
    assert cli._copy_to_clipboard("x") is False


# --- Issue の受け取り -----------------------------------------------------------------------------


def _body(text: str) -> str:
    return f"### Measurement (JSON)\n\n```json\n{text}\n```\n"


def test_extract_issue_json():
    j = '{"a": 1}'
    assert contrib.extract_issue_json(_body(j)) == j
    assert contrib.extract_issue_json(_body(j).replace("\n", "\r\n")) == j
    assert contrib.extract_issue_json("```\n" + j + "\n```") == j
    assert contrib.extract_issue_json(j) == j
    assert contrib.extract_issue_json("```sh\nrm -rf /\n```\n" + _body(j)) == j  # { で始まる囲みだけ
    assert contrib.extract_issue_json("_No response_") is None
    assert contrib.extract_issue_json(None) is None


def test_intake_accepts_a_good_submission(db, tmp_path):
    text = GOOD[0].read_text(encoding="utf-8")
    data = json.loads(text)
    res = contrib.intake_issue(_body(text), "alice", 12, db=db, existing=tmp_path / "none")
    assert res.ok and res.reasons == []
    assert res.path_in_repo == contrib.suggested_path(data) and res.sha256 == contrib.content_hash(data)
    assert res.content == contrib.dumps(data)  # 書き直した中身(本文の文字そのままではない)
    assert (
        "Closes #12" in res.pr_body and "@alice" in res.pr_body and "issue #12 by alice" in res.commit_message
    )
    assert res.title == f"measurement: {data['measurement']['model_id']} on {data['system']['gpu']['name']}"


def test_intake_refuses_duplicates_and_bad_input(db, tmp_path):
    text = GOOD[0].read_text(encoding="utf-8")
    data = json.loads(text)
    existing = tmp_path / "data" / "contrib"
    contrib.write_submission(data, str(existing) + "/")
    res = contrib.intake_issue(_body(text), "alice", 1, db=db, existing=existing)
    assert not res.ok and any("already" in r or "既に" in r for r in res.reasons)
    for body in ("hello", _body("{not json"), _body('{"a": 1, "a": 2}'), _body("{" + " " * 70000 + "}")):
        r = contrib.intake_issue(body, "alice", 1, db=db)
        assert not r.ok and r.reasons and r.content is None and r.pr_body == ""


def test_intake_comment_is_harmless(db):
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["note"] = "see https://evil.example/x @everyone <img src=x> C:\\Users\\John Smith\\x"
    res = contrib.intake_issue(_body(json.dumps(d)), "mallory", 3, db=db)
    assert not res.ok
    body = "\n".join(line for line in res.comment.splitlines() if line.startswith("- "))
    assert "@everyone" not in body and "<img" not in body and "https://" not in body


def test_intake_author_is_checked(db):
    text = GOOD[0].read_text(encoding="utf-8")
    res = contrib.intake_issue(_body(text), "bad name; rm -rf /", 4, db=db)
    assert res.ok and "@unknown" in res.pr_body and "rm -rf" not in res.pr_body + res.commit_message


def test_cli_intake_writes_files_and_prints_nothing_from_the_body(tmp_path, capsys):
    text = GOOD[0].read_text(encoding="utf-8")
    ev = tmp_path / "event.json"
    ev.write_text(
        json.dumps({"issue": {"number": 9, "body": _body(text), "user": {"login": "bob"}}}), "utf-8"
    )
    out = tmp_path / "out"
    assert (
        cli.main(["contrib", "intake", str(ev), "--existing", str(tmp_path / "none"), "--out", str(out)]) == 0
    )
    assert capsys.readouterr().out == "accepted\n"
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    path = (out / "path.txt").read_text(encoding="utf-8").strip()
    assert res["ok"] and path == res["path_in_repo"]
    assert re.fullmatch(r"data/contrib/[a-z0-9-]+/[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}\.json", path)
    assert json.loads((out / "submission.json").read_text(encoding="utf-8")) == json.loads(text)
    comment = (out / "comment.md").read_text(encoding="utf-8")
    assert "Accepted" in comment and "受け付けました" in comment  # 英語と日本語
    pr = (out / "pr_body.md").read_text(encoding="utf-8")
    assert pr.count("Closes #9") == 1 and "@bob" in pr
    # 断るとき
    ev.write_text(
        json.dumps({"issue": {"number": 9, "body": "::error::x", "user": {"login": "bob"}}}), "utf-8"
    )
    out2 = tmp_path / "out2"
    assert cli.main(["contrib", "intake", str(ev), "--out", str(out2)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("refused") and "::" not in printed
    assert not (out2 / "submission.json").exists() and (out2 / "comment.md").is_file()
    ev.write_text("{}", "utf-8")
    assert cli.main(["contrib", "intake", str(ev), "--out", str(out2)]) == 1


# --- ワークフロー --------------------------------------------------------------------------------


def _wf() -> tuple[dict, str]:
    yaml = pytest.importorskip("yaml")
    path = ROOT / ".github" / "workflows" / "measurement-intake.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")), path.read_text(encoding="utf-8")


def test_intake_workflow_treats_the_body_as_data():
    wf, text = _wf()
    on = wf.get("on", wf.get(True))
    # 印を付けたとき(labeled)は動かない: フォームの Issue は作ったときに印が付いていて、2 回動くため
    assert set(on) == {"issues"} and set(on["issues"]["types"]) == {"opened", "edited"}
    assert wf["permissions"] == {"contents": "read"}
    check, publish = wf["jobs"]["check"], wf["jobs"]["publish"]
    assert check["permissions"] == {"contents": "read"}  # パッケージを入れるジョブは読むだけ
    assert publish["permissions"] == {"contents": "write", "pull-requests": "write", "issues": "write"}
    assert publish["needs"] == "check"
    assert "'measurement'" in check["if"] and "github.event.issue.state == 'open'" in check["if"]
    assert "github.event.changes.body != null" in check["if"]  # 題だけの編集では動かない
    sha = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
    for job in (check, publish):
        for st in job["steps"]:
            if "uses" in st:
                assert sha.match(st["uses"]), st["uses"]
                if st["uses"].startswith("actions/checkout@"):
                    assert st["with"]["persist-credentials"] is False
            assert "${{" not in st.get("run", "")  # 式は run に埋め込まない(env だけ)
            for value in st.get("env", {}).values():
                assert not re.search(r"issue\.(body|title|user)|comment\.body", str(value))
    # 書くジョブはほかの人のパッケージを入れない(git と gh だけ)
    pub_runs = "\n".join(st.get("run", "") for st in publish["steps"])
    pub_uses = [st["uses"].split("@")[0] for st in publish["steps"] if "uses" in st]
    assert set(pub_uses) == {"actions/checkout", "actions/download-artifact"}
    assert "pip install" not in pub_runs and "uv " not in pub_runs and "genmodelfit" not in pub_runs
    code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
    assert "secrets." not in code and "pull_request_target" not in code
    chk_runs = "\n".join(st.get("run", "") for st in check["steps"])
    assert 'contrib intake "$GITHUB_EVENT_PATH"' in chk_runs and "::stop-commands::" in chk_runs
    assert (
        next(st for st in check["steps"] if st.get("uses", "").startswith("actions/checkout"))["with"][
            "fetch-depth"
        ]
        == 0
    )  # 同じ人の前の提出を数えるため
    assert r"^data/contrib/[a-z0-9-]+/[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}(-failures)?\.json$" in pub_runs
    assert "--body-file" in pub_runs and "gh pr merge" not in pub_runs


def test_intake_workflow_is_idempotent_and_follows_edits():
    wf, _ = _wf()
    run = "\n".join(st.get("run", "") for st in wf["jobs"]["publish"]["steps"])
    # 同じ中身の PR があれば push もコメントもしない
    i_same = run.index('[ "$have" = "$want" ]')
    assert "exit 0" in run[i_same : i_same + 80] and run.index("git push") > i_same
    # 別の Issue の PR に同じ中身があれば知らせて止める
    assert "measurement/issue-[0-9]+$" in run and "dup.md" in run
    # 通らない中身なら印を付けて PR を閉じ、通る中身なら PR を直して知らせる。閉じた PR は push の前に開き直す
    assert 'close_invalid "$pr" "$OUT/comment_pr_closed.md"' in run and 'gh pr close "$1"' in run
    assert '--add-label "$invalid"' in run and 'invalid="measurement-invalid"' in run
    assert "gh pr edit" in run and "comment_updated.md" in run
    assert run.index("gh pr reopen") < run.index("git push")
    # 開き直すのはボットが閉じた(印のある)PR だけ。メンテナが閉じた PR は開き直さない
    i_reopen = run.index("gh pr reopen")
    assert 'index(\\"${invalid}\\")' in run[:i_reopen] and "--remove-label" in run[i_reopen - 200 : i_reopen]
    assert "not reopening" in run
    # 別の Issue と同じ中身に変わったら、この Issue の古い PR も閉じる
    i_dup = run.index("dup.md")
    assert 'close_invalid "$pr" "$OUT/dup.md"' in run[i_dup:]
    # 見るのはボットの PR だけ(フォークの同じ名前のブランチを除く)。gh pr list はすべて bot_prs を通す
    assert "(.isCrossRepository | not)" in run and '.author.login == "app/github-actions"' in run
    assert run.count("gh pr list") == 1 and run.count("bot_prs ") >= 5
    # 同じ理由で断るコメントは 1 回だけ
    assert "marker.txt" in run and "same notice as before" in run
    # 同じ知らせ(同じ中身の別の Issue・メンテナが閉じた)も 1 回だけ。開き直せなければ新しい PR を作る
    assert 'comment_issue "$OUT/dup.md" "$mark"' in run and 'comment_issue "$OUT/maint.md" "$mark"' in run
    assert "measurement-intake:duplicate=" in run and "measurement-intake:maintainer-closed" in run
    assert 'if gh pr reopen "$closed"; then' in run and "opening a new PR" in run
    assert run.count("gh issue comment") == 3  # comment_issue の中と、PR を作った・直したとき


def test_intake_comments_for_edits(db):
    text = GOOD[0].read_text(encoding="utf-8")
    ok = contrib.intake_issue(_body(text), "alice", 5, db=db, prior=2)
    assert ok.ok and "Submitted-by: github:alice" in ok.commit_message
    assert "already sent for this GPU and model: 2" in ok.pr_body or "既に送った提出: 2 件" in ok.pr_body
    assert "#5" in ok.comment_updated and ok.path_in_repo in ok.comment_updated
    bad = contrib.intake_issue("nothing", "alice", 5, db=db)
    assert not bad.ok and "#5" in bad.comment_pr_closed
    unknown = contrib.intake_issue(_body(text), "bad name", 5, db=db)
    assert "Submitted-by" not in unknown.commit_message  # アカウント名が分からなければ書かない


def test_intake_ignores_the_runner_identity(db, monkeypatch):
    """intake は GitHub の実行機の名前・ユーザ名(runner など)を「この PC の名前」として断らない。"""
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["note"] = "runner measured with bench"
    text = json.dumps(d)
    monkeypatch.setattr(contrib, "_identity_words", lambda: ["runner"])
    checks = contrib.privacy_checklist(d, text)
    assert any(c["id"] == "host_user" and c["ok"] is False for c in checks)  # share では断る(手元の PC)
    assert contrib.intake_issue(_body(text), "alice", 1, db=db).ok


def test_share_print_url_without_a_file(capsys, monkeypatch, browser):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["share", "--issue", "--print-url"]) == 1  # 送るものが無い
    assert "genmodelfit measure" in capsys.readouterr().err
    p = _saved(1)[0]
    assert cli.main(["share", "--issue", "--print-url"]) == 0  # 1 つだけならそれ
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("https://github.com/") and browser == []
    assert measure.load_shared()  # URL を出したので印を付けた
    _saved(3)
    assert cli.main(["--lang", "ja", "share", "--issue", "--print-url"]) == 1  # 2 つ以上なら選ぶように言う
    err = capsys.readouterr().err
    assert "送るファイルを指定してください" in err and str(p) not in err  # 送ったものは出さない


def test_share_json_preview_does_not_mark(capsys):
    p = _saved(1)[0]
    assert cli.main(["share", "--issue", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_shared"] is False and measure.load_shared() == {}
    assert cli.main(["share", "--issue", str(p), "--json", "--mark-sent"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_shared"] is True and measure.load_shared()


def test_share_without_a_terminal_does_not_mark(capsys, monkeypatch, browser):
    p = _saved(1)[0]
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["share", "--issue", str(p)]) == 0
    assert "issues/new" in capsys.readouterr().out and browser == [] and measure.load_shared() == {}


@pytest.mark.parametrize(
    ("outcome", "metric", "offload", "word"),
    [
        ("ok", "torch_reserved", "dynamic", "照合"),
        ("ok", "torch_reserved", "none", "段階も実測で決まります"),
        ("oom", "torch_reserved", "oom", "段階を決めなくなります"),
    ],
)
def test_share_hint_matches_how_it_is_used(outcome, metric, offload, word):
    m = meas(outcome=outcome, vram_metric=metric, offload=offload)
    assert word in cli._share_hint(m) and "次の版が出てから" in cli._share_hint(m)


def test_measure_next_step_never_only_says_force():
    busy = "GPU の使用率が 90% です。空いてから測るか、--force を付けてください"
    assert "ほかの生成" in cli._measure_next_step(busy, "image_x")
    assert "genmodelfit download image_x" in cli._measure_next_step(
        "必要なファイルがサーバーにありません", "image_x"
    )
    assert "ComfyUI" in cli._measure_next_step("connection refused", "image_x")


def test_intake_marker_is_stable_per_reason_set(db, tmp_path):
    a = contrib.intake_issue("nothing", "x", 1, db=db)
    b = contrib.intake_issue("still nothing", "x", 1, db=db)
    c = contrib.intake_issue(_body("{not json"), "x", 1, db=db)
    assert a.marker == b.marker != c.marker and a.marker.startswith("<!-- measurement-intake:reasons=")
    assert a.comment.rstrip().endswith(a.marker)
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"issue": {"number": 1, "body": "nothing", "user": {"login": "x"}}}), "utf-8")
    assert cli.main(["contrib", "intake", str(ev), "--out", str(tmp_path / "o")]) == 0
    from genmodelfit import i18n

    with i18n.forced("en"):  # marker.txt は英語の理由の組の印
        en = contrib.intake_issue("nothing", "x", 1, db=db).marker
    assert (tmp_path / "o" / "marker.txt").read_text(encoding="utf-8").strip() == en


def test_intake_writes_the_numeric_account_id(db, tmp_path):
    text = GOOD[0].read_text(encoding="utf-8")
    ok = contrib.intake_issue(_body(text), "alice", 5, db=db, author_id=583231)
    assert "Submitted-by: github:alice\nSubmitted-by-id: 583231\n" in ok.commit_message
    assert (
        "Submitted-by-id"
        not in contrib.intake_issue(_body(text), "alice", 5, db=db, author_id="58x").commit_message
    )
    assert "- [ ] " in ok.pr_body  # マージの前に作者と同じ人の前の提出を確かめる項目
    ev = tmp_path / "e.json"
    ev.write_text(
        json.dumps({"issue": {"number": 9, "body": _body(text), "user": {"login": "bob", "id": 77}}}), "utf-8"
    )
    assert (
        cli.main(
            ["contrib", "intake", str(ev), "--existing", str(tmp_path / "none"), "--out", str(tmp_path / "o")]
        )
        == 0
    )
    assert "Submitted-by-id: 77" in (tmp_path / "o" / "commit_msg.txt").read_text(encoding="utf-8")


# --- 設定を変えた測定(#116) ------------------------------------------------------------------------


def _set(**kw):
    from genmodelfit.models import Settings

    return Settings(**kw)


def without_community(db):
    """同梱のほかの利用者のまとめ(measurements_community.json)を外した一覧(試験を同梱のデータから切り離す)。"""
    from genmodelfit.estimate import is_community

    db.measurements = [m for m in db.measurements if not is_community(m)]
    return db


def defaults_measured(db=None, **override_seconds):
    """この GPU(RTX 3070)で、テンプレートのままの実測がある一覧(設定を変えた測定が出る前提)。
    同梱のほかの利用者のまとめは外す(試験が同梱のデータの増減に左右されないように。実測は下の明示した行だけ)。"""
    db = without_community(db or load_db(use_cache=False))
    rows = {
        "image_sdxl_simple": (_set(width=1024, height=1024, steps=20), 20.0),
        "text_to_video_wan": (_set(width=832, height=480, frames=33, steps=20), 120.0),
        "audio_ace_step_1_5_checkpoint": (_set(seconds=120.0, steps=8), 60.0),
    }
    for model_id, (st, secs) in rows.items():
        db.measurements.append(meas(model_id, settings=st, seconds=override_seconds.get(model_id, secs)))
    return db


@pytest.fixture
def measured_db(monkeypatch):
    db = defaults_measured()
    monkeypatch.setattr(cli, "_db", lambda args: db)
    return db


def test_variants_per_modality():
    img = measure.variants(entry(tasks=["t2i"]))
    assert [(v.width, v.height) for v in img] == [(512, 512), (768, 768), (1024, 1024), (1536, 1536)]
    assert all(v.frames is None and v.seconds is None for v in img)
    vid = measure.variants(entry(id="video_x", modality="video", tasks=["i2v"]))
    # 動画の大きさはテンプレートのまま
    assert [v.frames for v in vid] == [17, 33, 49, 81] and all(v.width is None for v in vid)
    mus = measure.variants(entry(id="audio_x", modality="music", tasks=["t2a"]))
    assert [v.seconds for v in mus] == [10, 30, 60, 120]
    sfx = measure.variants(entry(id="sfx_x", modality="sfx", tasks=["t2a"]))
    assert [v.seconds for v in sfx] == [10, 30, 60, 120]
    # 設定を入力で決めない用途・種類には出さない
    assert measure.variants(entry(tasks=["i2i"])) == []
    assert measure.variants(entry(tasks=["upscale"])) == []
    assert measure.variants(entry(id="3d_x", modality="3d", tasks=["i23d"])) == []
    assert measure.variants(entry(id="tts_x", modality="tts", tasks=["tts"])) == []
    assert measure.variants(entry(id="video_x", modality="video", tasks=["v2v"])) == []
    # 大きさの入力の無いテンプレート(estimate.NO_SIZE_INPUT)
    assert measure.variants(entry(id="utility_birefnet_remove_background", tasks=["t2i"])) == []


def test_variants_skip_inputs_the_template_does_not_have():
    """テンプレートのままの実測にその項目が無い(入力が無い)ものは出さない(コマ数を batch_size で決める
    AnimateDiff など)。設定を変えた実測は手がかりにしない。"""
    v = entry(id="video_x", modality="video", tasks=["t2v"])
    no_frames = meas("video_x", settings=_set(width=512, height=512, steps=20))
    assert measure.variants(v, [no_frames]) == []
    assert [x.frames for x in measure.variants(v, [meas("video_x", settings=_set(frames=16))])] == [
        17,
        33,
        49,
        81,
    ]
    overridden = meas("video_x", settings=_set(width=512, height=512), settings_override=True)
    assert len(measure.variants(v, [overridden])) == 4  # 設定を変えた実測からは分からない
    img = entry(tasks=["t2i"])
    assert measure.variants(img, [meas(settings=_set(steps=20))]) == []  # 幅・高さの入力が無い
    a = entry(id="audio_x", modality="music", tasks=["t2a"])
    assert measure.variants(a, [meas("audio_x", settings=_set(steps=8))]) == []


def test_variant_args_and_settings_dict():
    assert measure.bench_args(None) == [] and measure.settings_dict(None) is None
    assert measure.settings_dict(_set()) is None
    assert measure.bench_args(_set(width=1536, height=1536)) == ["--resolution", "1536x1536"]
    assert measure.bench_args(_set(frames=81)) == ["--frames", "81"]
    assert measure.bench_args(_set(seconds=120.0)) == ["--length", "120"]
    assert measure.settings_dict(_set(seconds=30.0, steps=8)) == {"seconds": 30.0}  # ステップ数は変えない


def test_has_measurement_here_with_settings():
    e = entry(tasks=["t2i"])
    g = pc().best_gpu
    at512 = meas(settings=_set(width=512, height=512, steps=20))
    want512, want1024 = _set(width=512, height=512), _set(width=1024, height=1024)
    assert measure.has_measurement_here(e, [at512], g)  # 記録の無い古い行はテンプレートのまま
    assert measure.has_measurement_here(e, [at512], g, want512)
    assert not measure.has_measurement_here(e, [at512], g, want1024)
    assert not measure.has_measurement_here(e, [meas()], g, want512)  # 設定の無い実測は別の設定
    # 設定を変えた実測は、その設定には数えるが、テンプレートのままには数えない
    over = meas(settings=want512, settings_override=True)
    assert measure.has_measurement_here(e, [over], g, want512)
    assert not measure.has_measurement_here(e, [over], g)
    # 同じ設定でも、ほかの GPU・標準でない・失敗は数えない
    other_gpu = meas(settings=want512, gpu_name="NVIDIA GeForce RTX 4090", gpu_vram_gb=24)
    assert not measure.has_measurement_here(e, [other_gpu], g, want512)
    assert not measure.has_measurement_here(e, [meas(settings=want512, standard=False)], g, want512)
    assert not measure.has_measurement_here(e, [meas(settings=want512, outcome="error")], g, want512)
    assert measure.has_measurement_here(e, [meas(settings=want512, outcome="oom")], g, want512)
    # 動画はコマ数だけ比べる(大きさはテンプレートのまま)
    v = entry(id="video_x", modality="video", tasks=["t2v"])
    m49 = meas("video_x", settings=_set(width=832, height=480, frames=49))
    assert measure.has_measurement_here(v, [m49], g, _set(frames=49))
    assert not measure.has_measurement_here(v, [m49], g, _set(frames=81))


def test_video_variants_are_judged_at_the_template_size():
    v = entry(id="video_x", modality="video", tasks=["t2v"])
    want = _set(frames=81)
    assert measure.judged_settings(v, [], want) is want  # 分からなければ種類ごとの既定(832x480)
    old = meas("video_x", settings=_set(width=640, height=640, frames=73), date="2026-10-01")
    new = meas("video_x", settings=_set(width=1280, height=704, frames=121), date="2026-10-20")
    over = meas("video_x", settings=_set(width=320, height=320), settings_override=True, date="2026-10-27")
    got = measure.judged_settings(v, [old, new, over], want)
    assert (got.width, got.height, got.frames) == (1280, 704, 81)  # テンプレートのままの新しい実測から
    img = entry(tasks=["t2i"])
    assert measure.judged_settings(img, [old], _set(width=512, height=512)).width == 512


def test_time_hint_scales_with_settings():
    e = entry(tasks=["t2i"])
    # 種類ごとの値(画像 60 秒)は既定の 1024x1024 での値として伸び縮みさせる
    assert measure.time_hint(e, [], _set(width=512, height=512)) == (20, "typical")  # 60 × 1/4 = 15
    assert measure.time_hint(e, [], _set(width=1536, height=1536)) == (140, "typical")  # 60 × 2.25 = 135
    # 実測はそれぞれの設定から伸び縮みさせてから中央値(512 の 20 秒 → 80 秒、既定の 40 秒 → 40 秒)
    ms = [meas(seconds=20.0, settings=_set(width=512, height=512)), meas(seconds=40.0)]
    assert measure.time_hint(e, ms, _set(width=1024, height=1024)) == (60, "measured")
    v = entry(id="video_x", modality="video", tasks=["t2v"])
    assert measure.time_hint(v, [], _set(frames=17)) == (70, "typical")  # 300 × 17/81 = 63
    mv = [meas("video_x", seconds=100.0, settings=_set(width=512, height=512, frames=49))]
    # 大きさは変えないので、比はコマ数だけ
    assert measure.time_hint(v, mv, _set(frames=98)) == (200, "measured")
    a = entry(id="audio_x", modality="music", tasks=["t2a"])
    ma = [meas("audio_x", seconds=20.0, settings=_set(seconds=20.0))]
    assert measure.time_hint(a, ma, _set(seconds=120.0)) == (120, "measured")
    assert measure.time_hint(a, ma) == (20, "measured")  # 設定を渡さなければ今までどおり


def _rows(sugg, model_id):
    return [x for x in sugg if x.model_id == model_id]


def test_variants_come_after_the_template_run(db):
    """設定を変えた測定は、この GPU にテンプレートのままの実測があるモデルだけ(先にそちらを測ってもらう)。
    設定を変えた実測では、テンプレートのままの行は消えない。"""
    s = pc()
    first = _rows(measure.suggestions(db, s, variants_per_model=None), "image_sdxl_simple")
    assert [x.settings for x in first] == [None]
    db2 = load_db(use_cache=False)
    db2.measurements.append(
        meas("image_sdxl_simple", settings=_set(width=512, height=512), settings_override=True)
    )
    rows = _rows(measure.suggestions(db2, s, variants_per_model=None), "image_sdxl_simple")
    assert [x.settings for x in rows] == [None]  # まだテンプレートのまま
    done = _rows(measure.suggestions(defaults_measured(), s, variants_per_model=None), "image_sdxl_simple")
    assert done and all(x.settings is not None for x in done)
    # テンプレートの値(1024x1024)は、テンプレートのままの実測があるので出さない(重ならない)。
    # 1536 は 8 GB に全部載らない(試験の実測 5.0 GB は下限より小さいので値をずらさない。#151)
    assert {x.settings.width for x in done} == {512, 768}


def test_suggestions_mix_variants_and_cap_per_model():
    s = pc()
    db = defaults_measured()
    sugg = measure.suggestions(db, s)
    var = [x for x in sugg if x.settings is not None]
    assert var, "RTX 3070 で設定を変えて測れるものがある"
    assert any(x.settings is None for x in sugg)  # テンプレートのままの行も同じ一覧に
    per_model: dict[str, int] = {}
    for x in var:
        per_model[x.group_key] = per_model.get(x.group_key, 0) + 1
        assert x.args and x.to_dict()["settings"] == measure.settings_dict(x.settings)
        assert x.to_dict()["args"] == x.args
    assert max(per_model.values()) <= measure.VARIANTS_PER_MODEL
    # 上限を外すと、同じモデルの設定がもっと出る(上限は並べたあとで切る: 残るのは先に並ぶもの)。
    # 8 GB で載る設定が 2 つまでのモデルしか無いので、上限 1 で比べる(#151)
    every = measure.suggestions(db, s, variants_per_model=None)
    one = measure.suggestions(db, s, variants_per_model=1)
    assert len(every) > len(one)
    for key in per_model:
        assert len([x for x in one if x.group_key == key and x.settings is not None]) == 1
    for key in per_model:
        all_of = [x for x in every if x.group_key == key and x.settings is not None]
        kept = [x for x in var if x.group_key == key]
        assert [x.args for x in kept] == [x.args for x in all_of[: len(kept)]]
    # 並びは入っているもの → 時間 → 落とす量(テンプレートのままと設定を変えたものを混ぜて)
    order = [(x.installed.state != "yes", x.seconds, x.installed.missing_bytes) for x in sugg]
    assert order == sorted(order)
    # どの行も、その設定(動画はテンプレートの大きさ)で GPU に載る
    from genmodelfit.estimate import estimate

    for x in every:
        m = db.get(x.model_id)
        st = measure.judged_settings(m, db.measurements, x.settings) if x.settings else None
        e = estimate(m, s, st, db.measurements)
        assert e.fit in ("perfect", "good") and e.run_mode == "gpu"


def test_suggestions_drop_variants_measured_here(db):
    s = pc()
    db2 = defaults_measured()
    db2.measurements.append(
        meas("image_sdxl_simple", settings=_set(width=512, height=512), settings_override=True)
    )
    widths = {
        x.settings.width
        for x in _rows(measure.suggestions(db2, s, variants_per_model=None), "image_sdxl_simple")
    }
    assert widths == {768}  # 1536 は 8 GB に全部載らない(#151)


def test_suggestions_cap_the_time():
    s = pc()
    every = measure.suggestions(defaults_measured(), s, variants_per_model=None)
    assert every and all(x.seconds <= measure.MAX_SUGGEST_SECONDS for x in every)
    # SDXL を 1024 px で 400 秒なら、1536 px は(ほかの GPU の実測との中央値でも)300 秒を超えるので出さない
    slow = defaults_measured(image_sdxl_simple=400.0)
    widths = {
        x.settings.width
        for x in _rows(measure.suggestions(slow, s, variants_per_model=None), "image_sdxl_simple")
    }
    assert widths == {512, 768}
    # 動画で時間が種類ごとのおおよその値(時間の実測が無い)なら、17・33 コマだけ
    # (RTX 4090 24 GB で、テンプレートのまま 49 コマの実測があり、時間はどの実測にも無い)
    big = pc(vram=24.0, name="NVIDIA GeForce RTX 4090")
    wan = "text_to_video_wan"
    db = without_community(load_db(use_cache=False))
    db.measurements = [x for x in db.measurements if x.model_id != wan]
    here = {"gpu_name": "NVIDIA GeForce RTX 4090", "gpu_vram_gb": 24.0}
    db.measurements.append(meas(wan, settings=_set(width=832, height=480, frames=49), **here))
    rows = _rows(measure.suggestions(db, big, variants_per_model=None), wan)
    assert {x.settings.frames for x in rows} == {17, 33}
    assert all(x.seconds_basis == "typical" for x in rows)
    # 時間の実測があれば 81 コマも(49 はテンプレートのままの実測と同じなので出さない)
    db.measurements[-1].seconds = 60.0
    rows = _rows(measure.suggestions(db, big, variants_per_model=None), wan)
    assert {x.settings.frames for x in rows} == {17, 33, 81}
    assert all(x.seconds_basis == "measured" for x in rows)


def test_measure_lists_variants_with_their_arguments(capsys, monkeypatch, this_pc, measured_db):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["--lang", "en", "measure", "-n", "40"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"\[\S+ --(resolution \d+x\d+|frames \d+|length \d+)\]", out)
    assert cli.main(["measure", "--json", "-n", "40"]) == 0
    rows = json.loads(capsys.readouterr().out)["suggestions"]
    assert any(r["settings"] is None and r["args"] == [] for r in rows)
    v = next(r for r in rows if r["settings"])
    assert v["args"][0] in ("--resolution", "--frames", "--length")


def test_measure_settings_need_a_model_and_a_good_resolution(capsys, this_pc):
    assert cli.main(["--lang", "ja", "measure", "--resolution", "512x512"]) == 1
    assert "測るモデルといっしょに" in capsys.readouterr().err
    assert cli.main(["--lang", "en", "measure", "image_sdxl_simple", "--resolution", "512"]) == 1
    assert "WIDTHxHEIGHT" in capsys.readouterr().err


def test_measure_passes_settings_to_bench(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    seen = []
    real_run = bench.run
    monkeypatch.setattr(bench, "run", lambda opts, *a, **k: seen.append(opts) or real_run(opts, *a, **k))
    with FakeComfy() as fake:
        argv = ["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]
        assert cli.main([*argv, "--resolution", "1536x1536"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert (seen[0].width, seen[0].height, seen[0].frames, seen[0].seconds) == (1536, 1536, None, None)
    assert seen[0].strict_settings and d["settings"] == {"width": 1536, "height": 1536}
    # 設定を変えた印が実測と提出に入る
    assert d["measurement"]["settings_override"] is True
    sub = json.loads(Path(d["submission"]["saved_to"]).read_text(encoding="utf-8"))
    assert sub["measurement"]["settings_override"] is True
    rep = contrib.validate_submission(sub, check_filename=False)
    assert not [e for e in rep.errors if "settings_override" in e["message"]]
    big = d["time_hint"]["seconds"]
    seen.clear()
    with FakeComfy() as fake:
        assert cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["settings"] is None and seen[0].width is None and not seen[0].strict_settings
    assert d["measurement"]["settings_override"] is None
    sub = json.loads(Path(d["submission"]["saved_to"]).read_text(encoding="utf-8"))
    assert "settings_override" not in sub["measurement"]
    assert big > d["time_hint"]["seconds"]  # 時間の目安も設定に合わせて伸びる


def test_measure_stops_before_generating_when_a_setting_cannot_be_set(capsys, monkeypatch, this_pc, fake_gpu):
    """テンプレートに無い入力(画像のコマ数)を頼んだら、生成の前に止めて理由を言う。"""
    from test_bench import FakeComfy

    with FakeComfy() as fake:
        argv = ["--lang", "ja", "measure", "image_sdxl_simple", "--yes", "--url", fake.url, "--frames", "81"]
        assert cli.main(argv) == 1
        assert fake.submitted == []  # 生成を送っていない
    err = capsys.readouterr().err
    assert (
        "頼んだ設定を書けません" in err and "frames 81" in err and "次にすること: このテンプレートでは" in err
    )
    assert not (cache_dir() / "submissions").exists()


def test_bench_without_strict_keeps_going(monkeypatch):
    """bench(strict_settings なし)は今までどおり、書けない設定を注意にして測る。"""
    opts = bench.BenchOptions(model_id="x", url="u", frames=81)
    assert opts.settings_override and not opts.strict_settings
    assert not bench.BenchOptions(model_id="x", url="u").settings_override
    assert bench.BenchOptions(model_id="x", url="u", steps=4).settings_override


def test_measure_warns_when_the_measured_settings_differ(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    real_run = bench.run

    def run(opts, *a, **k):
        res = real_run(opts, *a, **k)
        res.measurement.settings.width = 1024  # 書けたはずの値が、測った設定では違った
        return res

    monkeypatch.setattr(bench, "run", run)
    with FakeComfy() as fake:
        argv = [
            "measure",
            "image_sdxl_simple",
            "--json",
            "--yes",
            "--url",
            fake.url,
            "--resolution",
            "512x512",
        ]
        assert cli.main(argv) == 0
    d = json.loads(capsys.readouterr().out)
    assert any("differ" in w or "違います" in w for w in d["warnings"])


def test_measure_screen_shows_the_settings(capsys, monkeypatch, this_pc):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("起動していなければ尋ねない"))
    argv = ["--lang", "ja", "measure", "audio_ace_step_1_5_checkpoint", "--length", "120"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert "設定:       長さ 120 秒(ほかは公式のテンプレートのまま)" in out
    assert "比で伸び" in out


def test_measure_choosing_a_variant_measures_it(capsys, monkeypatch, this_pc, fake_gpu, measured_db):
    from test_bench import FakeComfy

    sugg = measure.suggestions(measured_db, pc())[: cli.MEASURE_LIMIT]
    k = next(i for i, x in enumerate(sugg) if x.settings is not None)
    want = sugg[k].settings
    seen = []
    real_run = bench.run
    monkeypatch.setattr(bench, "run", lambda opts, *a, **kw: seen.append(opts) or real_run(opts, *a, **kw))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli, "_choose_number", lambda n: k)
    answers(monkeypatch, "")
    with FakeComfy() as fake:
        cli.main(["--lang", "ja", "measure", "--url", fake.url])
    out = capsys.readouterr().out
    got = (seen[0].width, seen[0].height, seen[0].frames, seen[0].seconds)
    assert got == (want.width, want.height, want.frames, want.seconds)
    assert "ほかは公式のテンプレートのまま" in out


def test_settings_override_in_submissions_and_aggregate(db):
    m = meas("image_sdxl_simple", settings=_set(width=512, height=512, steps=20), settings_override=True)
    m.vram_metric, m.offload = "torch_reserved", "dynamic"
    sub = contrib.build_submission(m, pc())
    assert sub["measurement"]["settings_override"] is True
    assert contrib.to_measurement(sub).settings_override is True
    assert not [
        e
        for e in contrib.validate_submission(sub, db, check_filename=False).errors
        if "override" in e["message"]
    ]
    bad = json.loads(json.dumps(sub))
    bad["measurement"]["settings_override"] = "yes"
    errs = contrib.validate_submission(bad, db, check_filename=False).errors
    assert any("settings_override" in e["message"] for e in errs)
    # 印の無い提出はテンプレートのまま
    plain = contrib.build_submission(meas("image_sdxl_simple", settings=_set(width=1024, height=1024)), pc())
    assert "settings_override" not in plain["measurement"]
    assert contrib.to_measurement(plain).settings_override is None


def test_measure_saves_a_failure_record_for_the_next_share(capsys, monkeypatch, this_pc, fake_gpu):
    """#132: measure で測れなかったら、失敗の記録を送っていない提出として保存する(次の share が送る)。"""
    from test_bench import FakeComfy

    with FakeComfy("error") as fake:
        assert cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]) == 1
    d = json.loads(capsys.readouterr().out)
    assert d["timing"]["total_s"] is not None  # bench と同じ時間の内訳
    p = Path(d["failures_file"])
    assert p.name.endswith("-failures.json") and p.parent.name == "rtx-3070"
    data = json.loads(p.read_text(encoding="utf-8"))
    (f,) = data["failures"]
    assert (f["model_id"], f["outcome"], f["error_type"], f["node_type"]) == (
        "image_sdxl_simple",
        "error",
        "ValueError",
        "KSampler",
    )
    assert "boom" not in p.read_text(encoding="utf-8")  # 例外の本文は入れない
    assert [s.to_dict().get("kind") for s in measure.saved_submissions()] == ["failures"]
    # 同じ失敗をもう一度: 書かない(share で二重に送らない)
    with FakeComfy("error") as fake:
        cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url])
    assert "failures_file" not in json.loads(capsys.readouterr().out)
    assert len(measure.saved_submissions()) == 1


def test_measure_saves_missing_nodes_found_before_running(capsys, monkeypatch, this_pc):
    """#132: measure が足りないノードで測る前に止めたときも、ComfyUI の版つきで失敗の記録を保存する。"""
    from types import SimpleNamespace

    nodes = SimpleNamespace(missing=lambda mid: ["LoadMoGeModel"], comfyui_version=lambda: "0.40.0")
    monkeypatch.setattr(cli, "_node_check", lambda url: nodes)
    monkeypatch.setattr(bench, "run", lambda *a, **k: pytest.fail("測らない"))
    argv = ["measure", "image_sdxl_simple", "--json", "--yes", "--url", "http://127.0.0.1:9"]
    assert cli.main(argv) == 1
    d = json.loads(capsys.readouterr().out)
    data = json.loads(Path(d["failures_file"]).read_text(encoding="utf-8"))
    assert data["comfyui_version"] == "0.40.0"
    assert data["failures"] == [
        {
            "model_id": "image_sdxl_simple",
            "outcome": "missing_nodes",
            "seconds": None,
            "missing_nodes": ["LoadMoGeModel"],
            "before_run": True,
        }
    ]
    assert cli.main(argv) == 1  # もう一度: 同じ記録は書かない
    assert "failures_file" not in json.loads(capsys.readouterr().out)


def test_other_pcs_measurements_do_not_remove_suggestions():
    """ほかの PC の実測(同梱の作者の実測・ほかの利用者のまとめ)が同じ GPU にあっても、この PC の提案から
    外さない。外すのはこの PC の実測(キャッシュの実測・送る前の提出)だけ。"""
    from dataclasses import replace

    s = pc()
    db = without_community(load_db(use_cache=False))
    first = [x.settings for x in measure.suggestions(db, s) if x.model_id == "image_sdxl_simple"]
    assert first == [None]
    # 同じ GPU・テンプレートのままの、ほかの利用者のまとめの行(origin community)
    row = replace(meas("image_sdxl_simple", settings=_set(width=1024, height=1024)), origin="community")
    db.measurements.append(row)
    again = [x.settings for x in measure.suggestions(db, s) if x.model_id == "image_sdxl_simple"]
    assert again == [None]
    # この PC に保存した実測(送る前の提出など)なら外す
    mine = meas("image_sdxl_simple", settings=_set(width=1024, height=1024))
    rows = [x for x in measure.suggestions(db, s, extra=[mine]) if x.model_id == "image_sdxl_simple"]
    assert all(x.settings is not None for x in rows)


# --- 一瞬の山の「試した」印(#131) ------------------------------------------------------------------


SDXL = "image_sdxl_simple"


def test_brief_peak_marker_round_trip_and_stops_suggesting():
    s = pc()
    db = defaults_measured()
    widths = {x.settings.width for x in _rows(measure.suggestions(db, s, variants_per_model=None), SDXL)}
    assert 512 in widths
    brief = db.get("image_sdxl_simple")
    from genmodelfit.estimate import files_fingerprint

    m = meas(
        "image_sdxl_simple",
        settings=_set(width=512, height=512),
        settings_override=True,
        standard=False,
        files_fp=files_fingerprint(brief),
        comfyui_version="0.19.3",
    )
    path = measure.save_brief_peak(m)
    measure.save_brief_peak(m)  # 同じ行は 1 つ
    assert path == cache_dir() / measure.BRIEF_PEAKS_FILE
    assert len(json.loads(path.read_text(encoding="utf-8"))["measurements"]) == 1
    (tried,) = measure.brief_peak_tried()
    assert tried.standard is True and tried.comfyui_version == "0.19.3"
    # 見積もり・照合に使う実測(キャッシュの measurements)には入らない
    assert not (cache_dir() / "measurements").exists()
    widths = {x.settings.width for x in _rows(measure.suggestions(db, s, variants_per_model=None), SDXL)}
    assert 512 not in widths and 768 in widths
    # 壊れた印は無視する
    path.write_text("{", encoding="utf-8")
    assert measure.brief_peak_tried() == []


def test_measure_marks_brief_peak_as_tried(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    def brief(opts, *a, **k):
        res = real(opts, *a, **k)
        res.measurement.standard = False
        res.nonstandard = ["the peak VRAM was a brief spike"]
        res.brief_peak = True
        return res

    real = bench.run
    monkeypatch.setattr(bench, "run", brief)
    with FakeComfy() as fake:
        assert cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["measurement"]["standard"] is False and not d.get("submission")
    assert not list((cache_dir() / "submissions").rglob("*.json"))  # 送る用は作らない
    (tried,) = measure.brief_peak_tried()
    assert tried.model_id == "image_sdxl_simple"


def test_measure_does_not_mark_other_nonstandard(capsys, monkeypatch, this_pc, fake_gpu):
    from test_bench import FakeComfy

    def other(opts, *a, **k):
        res = real(opts, *a, **k)
        res.measurement.standard = False
        res.nonstandard = ["the peak VRAM was a brief spike", "other work ran during the measurement"]
        res.brief_peak = True
        return res

    real = bench.run
    monkeypatch.setattr(bench, "run", other)
    with FakeComfy() as fake:
        assert cli.main(["measure", "image_sdxl_simple", "--json", "--yes", "--url", fake.url]) == 0
    capsys.readouterr()
    assert measure.brief_peak_tried() == []  # ほかの理由もあるなら、測り直す意味がある


def test_aggregate_leaves_out_listed_nonstandard_submissions(monkeypatch):
    listed = contrib.nonstandard_submissions()
    assert len(listed) == 4 and all(len(k) == 64 and "#131" in v for k, v in listed.items())
    community = ROOT / "src" / "genmodelfit" / "data" / "measurements_community.json"
    doc = json.loads(community.read_text("utf-8"))
    rows = [(r["gpu_name"], r["model_id"], r["settings"].get("width")) for r in doc["measurements"]]
    assert ("NVIDIA GeForce RTX 3070", "image_sdxl_simple", 512) not in rows
    assert ("NVIDIA GeForce RTX 3070", "image_sdxl_simple", 1024) in rows
    # 同じ提出でも、表から外せば値に使う
    db = load_db(use_cache=False)
    subs, _ = contrib.load_dir(ROOT / "data" / "contrib", db)
    with_512 = [s for s in subs if s.sha256 in listed]
    assert with_512 and contrib.aggregate(with_512) == []
    monkeypatch.setattr(contrib, "nonstandard_submissions", lambda: {})
    assert len(contrib.aggregate(with_512)) == 4
