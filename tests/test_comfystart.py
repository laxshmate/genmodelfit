# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""起動中の ComfyUI が無いとき、見つけた ComfyUI を起動して、測り終えたら止める(#124)。

本物のプロセス・通信・ハードウェアは使わない(subprocess と session を偽物に差し替える)。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genmodelfit import cli, comfystart, measure
from genmodelfit.models import Gpu, SystemInfo


def _exe(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return p


def _comfy(d: Path) -> Path:
    for m in ("main.py", "nodes.py"):
        _exe(d / m)
    for m in ("comfy", "comfy_extras", "comfy_api", "models"):
        (d / m).mkdir(parents=True, exist_ok=True)
    return d


def venv_install(tmp_path: Path) -> Path:
    d = _comfy(tmp_path / "ComfyUI")
    _exe(d / ".venv" / "Scripts" / "python.exe")
    return d


# --- 起動の仕方 -----------------------------------------------------------------------------------


def test_plan_portable(tmp_path):
    root = tmp_path / "ComfyUI_windows_portable"
    d = _comfy(root / "ComfyUI")
    py = _exe(root / "python_embeded" / "python.exe")
    plan = comfystart.plan_start(d)
    assert (plan.kind, plan.python, plan.main, plan.cwd) == ("portable", py, d / "main.py", d)
    argv = plan.argv(12345)
    assert argv[:3] == [str(py), "-s", str(d / "main.py")]
    assert argv[3:] == ["--listen", "127.0.0.1", "--port", "12345", "--disable-auto-launch"]
    assert "--windows-standalone-build" not in argv  # ブラウザを開かない


@pytest.mark.parametrize("venv", [".venv", "venv"])
def test_plan_venv(tmp_path, venv):
    d = _comfy(tmp_path / "ComfyUI")
    py = _exe(d / venv / "bin" / "python")
    plan = comfystart.plan_start(d)
    assert plan.kind == "venv" and plan.python == py and "-s" not in plan.argv(1)


def test_plan_without_venv_is_refused(tmp_path):
    d = _comfy(tmp_path / "ComfyUI")
    with pytest.raises(comfystart.StartError) as e:
        comfystart.plan_start(d)
    assert "venv" in str(e.value)  # システムの python を当て推量で使わない


def test_plan_desktop(tmp_path):
    base = tmp_path / "Documents" / "ComfyUI"
    for name in ("models", "input", "user", "output", "custom_nodes"):
        (base / name).mkdir(parents=True)
    lad = tmp_path / "Local"
    ud = tmp_path / "Roaming" / "ComfyUI"
    _exe(ud / "extra_models_config.yaml")
    with pytest.raises(comfystart.StartError):  # 本体の python もアプリも無い
        comfystart.plan_start(base, desktop=True, platform="win32", localappdata=lad, home=tmp_path)
    py = _exe(base / ".venv" / "Scripts" / "python.exe")
    with pytest.raises(comfystart.StartError):  # アプリが見つからない
        comfystart.plan_start(base, desktop=True, platform="win32", localappdata=lad, home=tmp_path)
    main = _exe(lad / "Programs" / "@comfyorgcomfyui-electron" / "resources" / "ComfyUI" / "main.py")
    plan = comfystart.plan_start(
        base, desktop=True, user_data=ud, platform="win32", localappdata=lad, home=tmp_path
    )
    assert (plan.kind, plan.python, plan.main, plan.cwd) == ("desktop", py, main, main.parent)
    argv = plan.argv(5)
    assert argv[argv.index("--base-directory") + 1] == str(base)
    assert argv[argv.index("--extra-model-paths-config") + 1] == str(ud / "extra_models_config.yaml")
    assert "--disable-auto-launch" in argv and argv[argv.index("--listen") + 1] == "127.0.0.1"


def test_free_port_is_a_real_port():
    p = comfystart.free_port()
    assert 0 < p < 65536


def test_start_disabled(monkeypatch):
    for v in ("0", "off", "no", "false"):
        monkeypatch.setenv(comfystart.START_ENV, v)
        assert comfystart.start_disabled()
    monkeypatch.delenv(comfystart.START_ENV)
    assert not comfystart.start_disabled()


# --- 起動・待つ・止める ----------------------------------------------------------------------------


class FakeProc:
    def __init__(self, exits_after: int | None = None):
        self.pid = 4321
        self.polls = 0
        self.exits_after = exits_after
        self.code = None

    def poll(self):
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            self.code = 1
        return self.code

    def wait(self, timeout=None):
        self.code = self.code if self.code is not None else -9
        return self.code


def test_launch_runs_a_separate_process(tmp_path):
    plan = comfystart.plan_start(venv_install(tmp_path))
    seen = {}

    def popen(argv, **kw):
        seen.update(argv=argv, **kw)
        return FakeProc()

    log = tmp_path / "cache" / "comfyui-x.log"
    st = comfystart.launch(plan, port=40001, log=log, popen=popen)
    assert st.url == "http://127.0.0.1:40001" and st.log == log and log.exists()
    assert seen["argv"] == plan.argv(40001) and seen["cwd"] == str(plan.cwd)
    if sys.platform == "win32":
        assert seen["creationflags"] & 0x200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert seen["start_new_session"] is True
    comfystart.stop(st, run=lambda *a, **k: SimpleNamespace(returncode=0))


class Session:
    def __init__(self, codes):
        self.codes = list(codes)
        self.urls = []

    def get(self, url, **kw):
        self.urls.append((url, kw))
        return SimpleNamespace(status_code=self.codes.pop(0) if self.codes else 200)


def _started(tmp_path, proc):
    plan = comfystart.plan_start(venv_install(tmp_path))
    log = tmp_path / "comfyui.log"
    log.write_text("line1\nTraceback\nboom\n", encoding="utf-8")
    return comfystart.Started(plan, proc, 40002, log)


def test_wait_ready_polls_system_stats(tmp_path):
    st = _started(tmp_path, FakeProc())
    s = Session([0, 500, 200])
    waited = []
    comfystart.wait_ready(
        st, session=s, sleep=lambda x: None, progress=lambda w, line: waited.append((w, line))
    )
    assert [u for u, _ in s.urls] == ["http://127.0.0.1:40002/system_stats"] * 3
    assert s.urls[0][1]["proxies"] == {"http": None, "https": None, "all": None}
    assert len(waited) == 2 and waited[0][1] == "boom"  # ログの最後の空でない行を渡す


def test_wait_ready_reports_an_exit_with_the_log(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: stopped.append(st) or True)
    st = _started(tmp_path, FakeProc(exits_after=1))
    with pytest.raises(comfystart.StartError) as e:
        comfystart.wait_ready(st, session=Session([500, 500]), sleep=lambda x: None)
    assert str(st.log) in str(e.value) and e.value.tail[-1] == "boom" and stopped == [st]


def test_wait_ready_times_out(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: stopped.append(st) or True)
    st = _started(tmp_path, FakeProc())
    t = iter(range(0, 1000, 100))
    with pytest.raises(comfystart.StartError) as e:
        comfystart.wait_ready(
            st, timeout=250, session=Session([500] * 10), sleep=lambda x: None, clock=lambda: next(t)
        )
    assert "250" in str(e.value) and stopped == [st]


def test_stop_kills_only_our_process_tree(tmp_path, monkeypatch):
    calls = []
    st = _started(tmp_path, FakeProc())
    monkeypatch.setattr(sys, "platform", "win32")
    assert comfystart.stop(st, run=lambda argv, **k: calls.append(argv) or SimpleNamespace(returncode=0))
    assert calls == [["taskkill", "/T", "/F", "/PID", "4321"]]
    assert comfystart.stop(st, run=lambda *a, **k: pytest.fail("2 回は止めない"))
    other = _started(tmp_path / "other", FakeProc())
    other.process.pid = 9999  # 前から起動していた ComfyUI(止めない)
    assert all("9999" not in c for c in calls) and other.process.code is None
    killed = []
    st2 = _started(tmp_path / "b", FakeProc())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid), raising=False)
    assert comfystart.stop(st2)
    assert killed == [4321]


# --- measure・share から ---------------------------------------------------------------------------


@pytest.fixture
def this_pc(monkeypatch):
    g = Gpu(name="NVIDIA GeForce RTX 3070", vendor="nvidia", vram_total_gb=8.0, backend="cuda")
    s = SystemInfo(os="Windows 11", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    monkeypatch.setattr(cli, "_system", lambda args: s)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: s)
    return s


@pytest.fixture
def starter(monkeypatch):
    """起動・待つ・止めるを差し替える(本物のプロセスを作らない)。"""
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    events: list = []

    def launch(plan, **k):
        events.append(("launch", plan.kind, str(plan.comfyui_dir)))
        return comfystart.Started(plan, FakeProc(), 40100, Path("comfyui.log"))

    monkeypatch.setattr(comfystart, "launch", launch)
    monkeypatch.setattr(comfystart, "wait_ready", lambda st, **k: events.append(("ready", st.url)))
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: events.append(("stop", st.url)) or True)
    return events


def _fake_run(monkeypatch, events, error=None):
    def run(args, url, entry, settings, free=False, progress=None):
        events.append(("run", url))
        if error:
            return {"error": error}
        from test_share_pr import _write_sub

        m = SimpleNamespace(
            outcome="ok",
            peak_vram_gb=5.0,
            seconds=3.0,
            model_id=entry.id,
            gpu_name="g",
            gpu_vram_gb=8.0,
            standard=True,
        )
        return {
            "error": None,
            "result": SimpleNamespace(warnings=[], failed=None, nonstandard=[], to_dict=lambda: {}),
            "measurement": m,
            "submission": _write_sub(len(events)),  # 送る用の提出を書く(measure と同じ置き場所)
            "path_in_repo": None,
            "saved_to": None,
            "not_saved": "x",
        }

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setattr(cli, "_share_hint", lambda m: "hint")
    # 入っていないファイルは measure が先に落とす(落としたことにする)
    monkeypatch.setattr(
        cli, "_measure_download", lambda args, mid, **k: events.append(("download", mid)) or 0
    )


def test_measure_starts_comfyui_and_stops_it(capsys, monkeypatch, this_pc, starter, tmp_path):
    d = venv_install(tmp_path)
    _fake_run(monkeypatch, starter)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = []
    monkeypatch.setattr("builtins.input", lambda p="": asked.append(p) or "")
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--comfyui-dir", str(d)]) == 0
    out = capsys.readouterr().out
    assert "起動します" in out  # 画面は幅で折り返すので、頭だけ見る
    assert asked == ["測りますか? [Y/n] "]  # 起動のための質問は足さない
    assert starter == [
        ("download", "image_sdxl_simple"),  # 落とすのは起動の前
        ("launch", "venv", str(d)),
        ("ready", "http://127.0.0.1:40100"),
        ("run", "http://127.0.0.1:40100"),
        ("stop", "http://127.0.0.1:40100"),
    ]


def _cwd_comfyui(monkeypatch, tmp_path) -> Path:
    """今の作業フォルダが ComfyUI(ほかの探し方は効かない)。"""
    d = venv_install(tmp_path)
    monkeypatch.chdir(d)
    monkeypatch.delenv("GENMODELFIT_COMFYUI_DIR", raising=False)
    from genmodelfit import comfyfind

    monkeypatch.setenv(comfyfind.SEARCH_ENV, "1")  # 試験の既定は探さない(conftest)。ここだけ探す
    monkeypatch.setattr(comfyfind, "comfy_port_status", lambda *a, **k: None)  # 起動中のものは無い
    monkeypatch.setattr(comfyfind, "running_candidates", lambda *a, **k: [])
    real = comfyfind.find_candidates
    monkeypatch.setattr(
        comfyfind,
        "find_candidates",
        lambda *, probe, cwd=None, places=None: [c for c in real(probe=False, cwd=d) if c.source == "cwd"],
    )
    return d


def test_cwd_comfyui_is_started_only_after_yes(capsys, monkeypatch, this_pc, starter, tmp_path):
    """作業フォルダで見つけた ComfyUI は、そのフォルダの python を動かすので、y/N で確かめてから起動する
    (RT-001)。いいえなら起動しない。"""
    d = _cwd_comfyui(monkeypatch, tmp_path)
    _fake_run(monkeypatch, starter)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = iter(["", "n"])  # 測りますか → Enter、起動しますか → n
    asked = []
    monkeypatch.setattr("builtins.input", lambda p="": asked.append(p) or next(answers))
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple"]) == 1
    assert not any(e[0] == "launch" for e in starter)
    assert asked[-1] == "起動しますか? [y/N] "
    err = capsys.readouterr().err
    assert str(d) in err and "python" in err
    starter.clear()
    answers = iter(["", "y"])
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple"]) == 0
    assert ("launch", "venv", str(d)) in starter


def test_cwd_comfyui_is_not_started_without_a_terminal(capsys, monkeypatch, this_pc, starter, tmp_path):
    d = _cwd_comfyui(monkeypatch, tmp_path)
    _fake_run(monkeypatch, starter)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda p="": pytest.fail("尋ねない"))
    cli.main(["measure", "image_sdxl_simple", "--yes"])
    assert not any(e[0] == "launch" for e in starter)
    assert "--comfyui-dir" in capsys.readouterr().err
    # --comfyui-dir で選んだものは、尋ねずに起動する
    starter.clear()
    assert cli.main(["measure", "image_sdxl_simple", "--yes", "--comfyui-dir", str(d)]) == 0
    assert ("launch", "venv", str(d)) in starter


def test_measure_stops_comfyui_after_a_failure(capsys, monkeypatch, this_pc, starter, tmp_path):
    d = venv_install(tmp_path)
    _fake_run(monkeypatch, starter, error="boom")
    args = ["measure", "image_sdxl_simple", "--comfyui-dir", str(d), "--yes"]
    assert cli.main(args) == 1
    assert starter[-1] == ("stop", "http://127.0.0.1:40100")
    assert "boom" in capsys.readouterr().err
    # --json の計画に、起動の仕方が出る
    assert cli.main(["measure", "--json", "--comfyui-dir", str(d)]) == 0
    assert json.loads(capsys.readouterr().out)["comfyui"]["start"] == {"kind": "venv", "comfyui_dir": str(d)}


def test_measure_no_start_keeps_the_guidance(capsys, monkeypatch, this_pc, starter, tmp_path):
    d = venv_install(tmp_path)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda p="": pytest.fail("尋ねない"))
    assert (
        cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--comfyui-dir", str(d), "--no-start"]) == 1
    )
    assert "ComfyUI を起動してから" in capsys.readouterr().err and starter == []


def test_measure_explains_why_it_cannot_start(capsys, monkeypatch, this_pc, starter, tmp_path):
    d = _comfy(tmp_path / "ComfyUI")  # venv が無い
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda p="": pytest.fail("尋ねない"))
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--comfyui-dir", str(d)]) == 1
    err = capsys.readouterr().err
    assert "仮想環境(.venv・venv)が見つかりません" in err and starter == []


def test_measure_reports_a_start_failure(capsys, monkeypatch, this_pc, tmp_path):
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    d = venv_install(tmp_path)
    stopped = []
    monkeypatch.setattr(
        comfystart, "launch", lambda plan, **k: comfystart.Started(plan, FakeProc(), 1, tmp_path / "c.log")
    )

    def not_ready(st, **k):
        raise comfystart.StartError(f"ComfyUI exited (log: {st.log})", st.log, ["Traceback", "boom"])

    monkeypatch.setattr(comfystart, "wait_ready", not_ready)
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: stopped.append(st) or True)
    monkeypatch.setattr(cli, "_measure_run", lambda *a, **k: pytest.fail("測らない"))
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: 0)
    assert cli.main(["measure", "image_sdxl_simple", "--comfyui-dir", str(d), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "c.log" in err and "boom" in err


def test_share_starts_comfyui_and_stops_it(capsys, monkeypatch, this_pc, starter, tmp_path):
    from test_share_pr import TOKEN, FakeGitHub, sugg

    d = venv_install(tmp_path)
    monkeypatch.setattr(measure, "suggestions", lambda *a, **k: [sugg("a_installed", 60)])
    _fake_run(monkeypatch, starter)
    fake = FakeGitHub()
    monkeypatch.setattr(cli, "_share_session", lambda: fake)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--yes", "--json", "--comfyui-dir", str(d)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["comfyui"]["start"]["kind"] == "venv"
    assert out["comfyui"]["started"]["url"] == "http://127.0.0.1:40100"
    assert [e[0] for e in starter] == ["launch", "ready", "run", "stop"]


def test_share_dry_run_does_not_start(capsys, monkeypatch, this_pc, starter, tmp_path):
    from test_share_pr import sugg

    d = venv_install(tmp_path)
    monkeypatch.setattr(measure, "suggestions", lambda *a, **k: [sugg("a_installed", 60)])
    assert cli.main(["--lang", "ja", "share", "--dry-run", "--comfyui-dir", str(d)]) == 0
    assert "起動します" in capsys.readouterr().out and starter == []


def test_share_asks_then_starts_then_drops_missing_nodes(capsys, monkeypatch, this_pc, starter, tmp_path):
    from test_share_pr import TOKEN, FakeGitHub, sugg

    d = venv_install(tmp_path)
    monkeypatch.setattr(
        measure, "suggestions", lambda *a, **k: [sugg("needs_new_nodes", 60), sugg("b_ok", 60)]
    )
    _fake_run(monkeypatch, starter)
    checked = []

    def node_check(url):
        checked.append(url)
        return SimpleNamespace(missing=lambda mid: ["NewNode"] if mid == "needs_new_nodes" else [])

    monkeypatch.setattr(cli, "_node_check", node_check)
    monkeypatch.setattr(cli, "_share_session", lambda: FakeGitHub())
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda p="": starter.append(("ask", p[:5])) or "")
    assert cli.main(["--lang", "ja", "share", "--comfyui-dir", str(d)]) == 0
    kinds = [e[0] for e in starter]
    assert kinds[:3] == ["ask", "launch", "ready"]  # 尋ねてから起動する
    assert [e for e in starter if e[0] == "run"] == [("run", "http://127.0.0.1:40100")]  # b_ok だけ
    assert checked == ["http://127.0.0.1:40100"]  # 起動したあとで確かめる
    out = capsys.readouterr().out
    assert "=== 測る計画(ComfyUI を確かめたあと) ===" in out and "needs_new_nodes" in out


def test_launch_hides_github_tokens_and_checks_the_port(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gho_x")
    monkeypatch.setenv("GH_TOKEN", "gho_y")
    plan = comfystart.plan_start(venv_install(tmp_path))
    seen = {}
    busy = iter([True, False])
    monkeypatch.setattr(comfystart, "port_in_use", lambda port: next(busy))
    ports = iter([1111, 2222])
    monkeypatch.setattr(comfystart, "free_port", lambda: next(ports))
    st = comfystart.launch(
        plan, log=tmp_path / "l.log", popen=lambda argv, **kw: seen.update(kw) or FakeProc()
    )
    assert "GITHUB_TOKEN" not in seen["env"] and "GH_TOKEN" not in seen["env"]
    assert st.port == 2222  # 使われていたポートは避けた
    comfystart.stop(st, run=lambda *a, **k: SimpleNamespace(returncode=0))


def test_share_asks_download_after_start_when_installed_ones_cannot_run(
    capsys, monkeypatch, this_pc, starter, tmp_path
):
    from test_share_pr import TOKEN, FakeGitHub, sugg

    d = venv_install(tmp_path)
    monkeypatch.setattr(
        measure,
        "suggestions",
        lambda *a, **k: [sugg("old_nodes", 60), sugg("dl_me", 60, "no", 1024**3)],
    )
    _fake_run(monkeypatch, starter)
    monkeypatch.setattr(
        cli,
        "_node_check",
        lambda url: SimpleNamespace(missing=lambda mid: ["X"] if mid == "old_nodes" else []),
    )
    monkeypatch.setattr(cli, "_share_session", lambda: FakeGitHub())
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(
        "builtins.input", lambda p="": starter.append(("ask", p[:6])) or ("y" if "落とし" in p else "")
    )
    assert cli.main(["--lang", "ja", "share", "--comfyui-dir", str(d)]) == 0
    kinds = [e[0] if e[0] != "ask" else "ask:" + e[1] for e in starter]
    # 入っているものを測るかを尋ね → 起動 → どれも使えない → (起動したまま)落とすかを尋ねる → 落として測る
    assert kinds[:5] == ["ask:1 件を測っ", "launch", "ready", "ask:落として測り", "download"]
    assert ("run", "http://127.0.0.1:40100") in starter and starter[-1][0] in ("stop", "ask")
    assert ("stop", "http://127.0.0.1:40100") in starter
