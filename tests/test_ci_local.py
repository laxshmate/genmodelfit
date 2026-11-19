# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""tools/ci_local.py の再開(--resume)と tools/ci_plugins/ci_resume.py(pytest のプラグイン)の試験。

本物の uv・ruff・全体の pytest・WSL は呼ばない(subprocess.run を偽物にする)。プラグインだけは、
tmp_path に作った小さな試験ファイルを本物の pytest の子プロセスで流す。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PLUGINS = TOOLS / "ci_plugins"


def _load():
    spec = importlib.util.spec_from_file_location("ci_local_under_test", TOOLS / "ci_local.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- tree_key ---------------------------------------------------------------------------------


@pytest.fixture
def ci(tmp_path, monkeypatch):
    mod = _load()
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("aaa", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("bbb", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", src)
    monkeypatch.setattr(mod, "tracked_files", lambda: sorted(_listing(src)))
    return mod


def _listing(src: Path) -> list[str]:
    return [p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()]


def test_tree_key_same_content_same_key(ci) -> None:
    assert ci.tree_key() == ci.tree_key()
    assert len(ci.tree_key()) == 64


def test_tree_key_changes(ci) -> None:
    base = ci.tree_key()
    src = ci.ROOT
    (src / "a.txt").write_text("aab", encoding="utf-8")  # 1 バイト変える
    changed = ci.tree_key()
    assert changed != base
    (src / "a.txt").write_text("aaa", encoding="utf-8")
    assert ci.tree_key() == base  # 戻せば同じ
    (src / "c.txt").write_text("c", encoding="utf-8")  # 足す
    added = ci.tree_key()
    assert added not in (base, changed)
    (src / "c.txt").rename(src / "d.txt")  # 名前を変える
    assert ci.tree_key() not in (base, changed, added)


def test_tree_key_none_when_git_fails(ci, monkeypatch, capsys) -> None:
    def boom():
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(ci, "tracked_files", boom)
    assert ci.tree_key() is None
    assert "鍵を作れませんでした" in capsys.readouterr().out
    monkeypatch.setattr(ci, "tracked_files", lambda: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert ci.tree_key() is None


def test_tree_key_none_when_file_unreadable(ci, monkeypatch) -> None:
    monkeypatch.setattr(ci, "tracked_files", lambda: ["a.txt", "gone.txt"])
    assert ci.tree_key() is None


# --- main の流れ(偽の subprocess.run) ---------------------------------------------------------


class Fake:
    """subprocess.run の偽物。呼ばれたコマンドを記録し、fail / interrupt に当たるものは失敗・中断する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.fail = lambda cmd: False
        self.interrupt = lambda cmd: False
        self.on_call = None

    def __call__(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        if self.interrupt(cmd):
            raise KeyboardInterrupt
        self.calls.append((cmd, kw))
        if self.on_call:
            self.on_call(cmd)
        if cmd[:2] == ["uv", "build"]:
            dist = Path(cmd[cmd.index("--out-dir") + 1])
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "x-0-py3-none-any.whl").write_bytes(b"w")
            (dist / "x-0.tar.gz").write_bytes(b"t")
        rc = 1 if self.fail(cmd) else 0
        if cmd[-1] == "import pytest":  # 配布物の venv には pytest が入っていない
            rc = 1
        return SimpleNamespace(returncode=rc, stdout=b"")

    def cmds(self, pred) -> list[list[str]]:
        return [c for c, _ in self.calls if pred(c)]

    def count(self, pred) -> int:
        return len(self.cmds(pred))


def is_pytest(c: list[str]) -> bool:
    return c[1:3] == ["-m", "pytest"]


def is_ruff_check(c: list[str]) -> bool:
    return c[1:4] == ["-m", "ruff", "check"]


def is_ruff_format(c: list[str]) -> bool:
    return c[1:4] == ["-m", "ruff", "format"]


def is_cli(c: list[str]) -> bool:
    return Path(c[0]).stem == "genmodelfit" and c[1] in ("system", "recommend", "fit")


def is_venv(c: list[str]) -> bool:
    return c[:2] == ["uv", "venv"]


def is_install(c: list[str]) -> bool:
    return c[:3] == ["uv", "pip", "install"]


@pytest.fixture
def run(ci, tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(ci.subprocess, "run", fake)
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/bin/" + name)
    envs = tmp_path / "envs"

    def go(*argv: str) -> int:
        return ci.main(["--env-dir", str(envs), *argv])

    go.fake = fake
    go.envs = envs
    go.json = lambda: json.loads((envs / f"resume-{ci.OS_TAG}.json").read_text(encoding="utf-8"))
    return go


TESTS_ONLY = ["--python", "3.12", "--no-wheel"]
SIX = [
    "py3.12 ruff check",
    "py3.12 ruff format --check",
    "py3.12 pytest",
    "py3.12 CLI system",
    "py3.12 CLI recommend",
    "py3.12 CLI fit",
]


def test_stop_then_resume_skips_passed(run, ci, capsys) -> None:
    run.fake.interrupt = is_pytest  # 3 つ目の検査(pytest)で止められた
    with pytest.raises(KeyboardInterrupt):
        run(*TESTS_ONLY)
    assert run.json()["passed"] == SIX[:2]
    assert run.json()["key"] == ci.tree_key()
    capsys.readouterr()

    run.fake.calls.clear()
    run.fake.interrupt = lambda c: False
    assert run("--resume", *TESTS_ONLY) == 0
    out = capsys.readouterr().out
    f = run.fake
    assert f.count(is_ruff_check) == 0 and f.count(is_ruff_format) == 0
    assert f.count(is_pytest) == 1 and f.count(is_cli) == 3
    assert f.count(is_venv) == 1 and f.count(is_install) == 1  # 環境の用意は毎回
    assert "再開: 同じ中身で前に通った検査 2 個を飛ばします" in out
    assert "skip" in out
    assert "(再開: 2 個は同じ中身で前に通ったので飛ばしました)" in out
    assert run.json()["passed"] == SIX


def test_resume_without_flag_runs_everything_and_resets(run, capsys) -> None:
    assert run(*TESTS_ONLY) == 0
    assert run.json()["passed"] == SIX
    run.fake.calls.clear()
    run.fake.interrupt = is_pytest
    with pytest.raises(KeyboardInterrupt):
        run(*TESTS_ONLY)  # --resume 無し: 記録は消えて新しく始まる
    assert run.json()["passed"] == SIX[:2]
    run.fake.interrupt = lambda c: False
    run.fake.calls.clear()
    assert run(*TESTS_ONLY) == 0
    assert (
        run.fake.count(is_pytest) == 1 and run.fake.count(is_ruff_check) == 1 and run.fake.count(is_cli) == 3
    )
    assert "(再開:" not in capsys.readouterr().out.split("===== まとめ")[-1]


def test_changed_content_runs_everything(run, ci, capsys) -> None:
    assert run(*TESTS_ONLY) == 0
    (ci.ROOT / "a.txt").write_text("changed", encoding="utf-8")
    run.fake.calls.clear()
    capsys.readouterr()
    assert run("--resume", *TESTS_ONLY) == 0
    assert (
        run.fake.count(is_pytest) == 1 and run.fake.count(is_ruff_check) == 1 and run.fake.count(is_cli) == 3
    )
    assert "再開: 中身が変わったので最初から流します" in capsys.readouterr().out
    # 飛ばしの環境変数も付かない
    env = next(kw for c, kw in run.fake.calls if is_pytest(c))["env"]
    assert "GMF_CI_RESUME_SKIP" not in env


def test_no_record_runs_everything(run, capsys) -> None:
    assert run("--resume", *TESTS_ONLY) == 0
    assert "再開: 記録が無いので最初から流します" in capsys.readouterr().out
    assert run.fake.count(is_pytest) == 1 and run.fake.count(is_cli) == 3


def test_failed_check_is_not_recorded(run) -> None:
    run.fake.fail = is_ruff_format
    assert run(*TESTS_ONLY) == 1
    assert "py3.12 ruff format --check" not in run.json()["passed"]
    assert "py3.12 ruff check" in run.json()["passed"]
    run.fake.calls.clear()
    run.fake.fail = lambda c: False
    assert run("--resume", *TESTS_ONLY) == 0
    assert run.fake.count(is_ruff_format) == 1  # 落ちたものはもう一度流す
    assert run.fake.count(is_ruff_check) == 0


def test_failed_pytest_is_not_recorded_but_cli_still_runs(run) -> None:
    run.fake.fail = is_pytest
    assert run(*TESTS_ONLY) == 1
    assert "py3.12 pytest" not in run.json()["passed"]
    run.fake.calls.clear()
    run.fake.fail = lambda c: False
    assert run("--resume", *TESTS_ONLY) == 0
    assert run.fake.count(is_pytest) == 1


@pytest.mark.parametrize(
    "text", ["{not json", "[]", '{"key": 1, "passed": "x"}', '{"key": "k", "passed": [1]}', ""]
)
def test_broken_record_starts_over(run, ci, text, capsys) -> None:
    run.envs.mkdir(parents=True)
    (run.envs / f"resume-{ci.OS_TAG}.json").write_text(text, encoding="utf-8")
    (run.envs / f"resume-{ci.OS_TAG}-py3.12.txt").write_text("tests/x.py::t\n", encoding="utf-8")
    assert run("--resume", *TESTS_ONLY) == 0
    assert "最初から流します" in capsys.readouterr().out
    assert run.fake.count(is_pytest) == 1 and run.fake.count(is_ruff_check) == 1
    assert run.json()["passed"] == SIX
    assert not (run.envs / f"resume-{ci.OS_TAG}-py3.12.txt").exists()  # 古い試験の記録も消える


def test_key_none_records_nothing_and_no_plugin(run, ci, monkeypatch, capsys) -> None:
    run.envs.mkdir(parents=True)
    (run.envs / f"resume-{ci.OS_TAG}.json").write_text('{"key": "k", "passed": ["py3.12 pytest"]}', "utf-8")
    monkeypatch.setattr(ci, "tree_key", lambda: None)
    assert run("--resume", *TESTS_ONLY) == 0
    assert "記録も飛ばしもしません" in capsys.readouterr().out
    assert not list(run.envs.glob("resume-*"))  # 古い記録も残さない
    assert run.fake.count(is_pytest) == 1 and run.fake.count(is_ruff_check) == 1
    cmd = run.fake.cmds(is_pytest)[0]
    assert cmd == [cmd[0], "-m", "pytest", "-q", "-p", "no:cacheprovider"]  # 今までどおり
    env = next(kw for c, kw in run.fake.calls if is_pytest(c)).get("env") or {}
    assert "GMF_CI_RESUME_FILE" not in env


def test_outer_resume_env_does_not_leak(run, ci, monkeypatch, tmp_path) -> None:
    """外の環境にある再開の指定(この道具を pytest の中から流したとき)を、子プロセスに渡さない。"""
    monkeypatch.setenv("GMF_CI_RESUME_FILE", str(tmp_path / "outer.txt"))
    monkeypatch.setenv("GMF_CI_RESUME_SKIP", "1")
    assert run(*TESTS_ONLY) == 0  # --resume 無し
    for c, kw in run.fake.calls:
        env = kw.get("env") or {}
        assert "GMF_CI_RESUME_SKIP" not in env, c
        if is_pytest(c):
            assert Path(env["GMF_CI_RESUME_FILE"]) == (run.envs / f"resume-{ci.OS_TAG}-py3.12.txt").resolve()
        else:
            assert "GMF_CI_RESUME_FILE" not in env, c
    assert not (tmp_path / "outer.txt").exists()


def test_step_runs_in_root_of_the_moment(run, ci, monkeypatch, tmp_path) -> None:
    """作業フォルダは流すときの ROOT(関数を定義したときの値で固定しない)。"""
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "tree_key", lambda: "k")
    assert run(*TESTS_ONLY) == 0
    assert all(kw.get("cwd") == tmp_path for c, kw in run.fake.calls if is_pytest(c) or is_ruff_check(c))


def test_pytest_command_and_env(run, ci) -> None:
    assert run(*TESTS_ONLY) == 0
    call = next((c, kw) for c, kw in run.fake.calls if is_pytest(c))
    cmd, env = call[0], call[1]["env"]
    assert cmd[3:] == ["-q", "-p", "no:cacheprovider", "-p", "ci_resume"]
    assert env["PYTHONPATH"] == str(PLUGINS)
    assert Path(env["GMF_CI_RESUME_FILE"]) == (run.envs / f"resume-{ci.OS_TAG}-py3.12.txt").resolve()
    assert Path(env["GMF_CI_RESUME_FILE"]).is_absolute()
    assert "GMF_CI_RESUME_SKIP" not in env  # --resume 無しでは飛ばさない
    # --resume + 前の記録があるとき(pytest だけ未完了の記録を作る)
    run.fake.interrupt = is_pytest
    with pytest.raises(KeyboardInterrupt):
        run(*TESTS_ONLY)
    run.fake.interrupt = lambda c: False
    run.fake.calls.clear()
    assert run("--resume", *TESTS_ONLY) == 0
    env = next(kw for c, kw in run.fake.calls if is_pytest(c))["env"]
    assert env["GMF_CI_RESUME_SKIP"] == "1"
    assert env["PYTHONUTF8"] == "1" and "VIRTUAL_ENV" not in env


def test_file_changed_during_run_drops_record(run, ci) -> None:
    def touch(cmd) -> None:
        if is_pytest(cmd):
            (ci.ROOT / "a.txt").write_text("edited while running", encoding="utf-8")

    run.fake.on_call = touch
    assert run(*TESTS_ONLY) == 0
    # pytest が通った時点で鍵が変わっているので、記録は捨てられ、続きからは流せない
    assert not list(run.envs.glob("resume-*"))
    run.fake.on_call = None
    run.fake.calls.clear()
    assert run("--resume", *TESTS_ONLY) == 0
    assert run.fake.count(is_pytest) == 1 and run.fake.count(is_ruff_check) == 1


# --- 配布物 ------------------------------------------------------------------------------------


WHEEL_ONLY = ["--python", "3.12", "--no-tests"]


def is_build(c: list[str]) -> bool:
    return c[:2] == ["uv", "build"]


def test_wheel_failure_is_not_recorded(run) -> None:
    run.fake.fail = lambda c: c[0] == "uvx"  # twine が落ちる
    assert run(*WHEEL_ONLY) == 1
    assert run.json()["passed"] == []
    run.fake.calls.clear()
    run.fake.fail = lambda c: False
    assert run("--resume", *WHEEL_ONLY) == 0
    assert run.fake.count(is_build) == 1  # 配布物は最初から


def test_wheel_all_passed_is_skipped_as_a_whole(run, capsys) -> None:
    assert run(*WHEEL_ONLY) == 0
    assert run.json()["passed"] == ["wheel 3.12"]
    run.fake.calls.clear()
    capsys.readouterr()
    assert run("--resume", *WHEEL_ONLY) == 0
    assert run.fake.calls == []  # 中のコマンドは 1 つも呼ばれない
    out = capsys.readouterr().out
    assert "skip" in out and "配布物" in out
    assert "(再開: 1 個は同じ中身で前に通ったので飛ばしました)" in out


def test_wheel_for_other_versions_is_not_skipped(run) -> None:
    assert run(*WHEEL_ONLY) == 0
    run.fake.calls.clear()
    assert run("--resume", "--python", "3.12", "3.15", "--no-tests") == 0
    assert run.fake.count(is_build) == 1  # 3.15 の分は流していないので、配布物は最初から


# --- WSL ---------------------------------------------------------------------------------------


def _wsl_args(**kw) -> argparse.Namespace:
    base = {"python": ["3.12"], "no_wheel": False, "no_tests": False, "resume": False, "wsl_distro": "D"}
    return argparse.Namespace(**{**base, **kw})


def _wsl_script(ci, monkeypatch, args, keys) -> str:
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return SimpleNamespace(returncode=0)

    it = iter(keys)
    monkeypatch.setattr(ci, "IS_WIN", True)
    monkeypatch.setattr(ci, "tree_key", lambda: next(it))
    monkeypatch.setattr(ci.subprocess, "run", fake_run)
    assert ci.run_in_wsl(args)
    return seen[0][-1]


def test_wsl_command_has_tree_key_and_resume(ci, monkeypatch) -> None:
    script = _wsl_script(ci, monkeypatch, _wsl_args(resume=True), ["abc123", "abc123"])
    assert "--tree-key abc123" in script and "--resume" in script
    script = _wsl_script(ci, monkeypatch, _wsl_args(), ["abc123", "abc123"])
    assert "--tree-key abc123" in script and "--resume" not in script


def test_wsl_key_not_passed_when_files_change_while_copying(ci, monkeypatch) -> None:
    script = _wsl_script(ci, monkeypatch, _wsl_args(resume=True), ["k1", "k2"])
    assert "--tree-key none" in script
    script = _wsl_script(ci, monkeypatch, _wsl_args(resume=True), [None, None])
    assert "--tree-key none" in script


def test_tree_key_none_value_means_no_record(run, ci) -> None:
    # WSL の中: 外から "none" を渡されたら記録も飛ばしもしない
    assert run("--tree-key", "none", "--resume", *TESTS_ONLY) == 0
    assert not list(run.envs.glob("resume-*"))
    assert run("--tree-key", "deadbeef", *TESTS_ONLY) == 0
    assert run.json()["key"] == "deadbeef"


# --- --merge(マージの前の分)・PYYAML_FORCE_LIBYAML ----------------------------------------------------


@pytest.mark.parametrize(
    "extra", [["--quick"], ["--linux"], ["--linux-only"], ["--python", "3.12"], ["--python", "3.12", "3.15"]]
)
def test_merge_cannot_combine(ci, monkeypatch, extra) -> None:
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/bin/" + name)
    with pytest.raises(SystemExit) as e:
        ci.main(["--merge", *extra])
    assert e.value.code == 2


def test_merge_runs_quick_versions_in_wsl_only(ci, monkeypatch) -> None:
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/bin/" + name)
    seen = []

    def fake_wsl(args) -> bool:
        seen.append(args)
        return True

    def no_local(*a, **kw):
        raise AssertionError("--merge は Windows(この OS)では流さない")

    monkeypatch.setattr(ci, "run_in_wsl", fake_wsl)
    monkeypatch.setattr(ci.subprocess, "run", no_local)
    monkeypatch.setattr(ci, "run_tests", no_local)
    monkeypatch.setattr(ci, "run_wheel", no_local)
    assert ci.main(["--merge"]) == 0
    (args,) = seen
    assert args.python == ci.QUICK_VERSIONS == ["3.12", "3.15"]
    assert not args.no_wheel and not args.no_tests  # 配布物も流す


def test_merge_wsl_command_is_linux_only_quick_versions(ci, monkeypatch) -> None:
    # --merge を main から通し、WSL に渡す引数(版・--resume・--no-wheel)を見る
    got = []
    real = ci.run_in_wsl
    monkeypatch.setattr(ci, "run_in_wsl", lambda a: got.append(a) or True)
    assert ci.main(["--merge", "--resume", "--no-wheel", "--wsl-distro", "D"]) == 0
    (args,) = got
    monkeypatch.setattr(ci, "run_in_wsl", real)
    script = _wsl_script(ci, monkeypatch, args, ["k", "k"])
    assert f"--python {' '.join(ci.QUICK_VERSIONS)}" in script
    assert "--resume" in script and "--no-wheel" in script
    # ruff は WSL の中で最初の版(= LINT_VERSION)の 1 回だけ
    assert ci.QUICK_VERSIONS[0] == ci.LINT_VERSION


def test_install_sets_pyyaml_force_libyaml_off(run, ci) -> None:
    # 3.15 の Windows に pyyaml の完成品が無くても、組み立ての道具が無い PC で入るように
    assert run(*TESTS_ONLY) == 0
    env = next(kw for c, kw in run.fake.calls if is_install(c))["env"]
    assert env["PYYAML_FORCE_LIBYAML"] == "0"
    assert ci.INSTALL_ENV == {"PYYAML_FORCE_LIBYAML": "0"}


def test_versions_default_to_all(run, ci) -> None:
    assert run("--no-wheel") == 0
    assert len(run.fake.cmds(is_pytest)) == len(ci.VERSIONS) == 4
    run.fake.calls.clear()
    assert run("--quick", "--no-wheel") == 0
    assert len(run.fake.cmds(is_pytest)) == 2
    assert len(run.fake.cmds(is_ruff_check)) == 1


# --- pytest のプラグイン(tools/ci_plugins/ci_resume.py)-------------------------------------------------


SAMPLE = """\
import pytest


def test_ok1():
    pass


def test_ok2():
    pass


def test_fail():
    assert False


def test_skip():
    pytest.skip("x")


@pytest.mark.xfail
def test_xfail():
    assert False


@pytest.fixture
def bad_teardown():
    yield
    raise RuntimeError("teardown")


def test_teardown_fail(bad_teardown):
    pass


@pytest.mark.parametrize("x", ["日本語", "あ"])
def test_ja(x):
    pass
"""


def _pytest(
    cwd: Path, *args: str, record: Path | None = None, skip: bool = False
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GMF_CI_"))}
    env["PYTHONPATH"] = str(PLUGINS)
    env["PYTHONUTF8"] = "1"
    if record is not None:
        env["GMF_CI_RESUME_FILE"] = str(record)
    if skip:
        env["GMF_CI_RESUME_SKIP"] = "1"
    (cwd / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "ci_resume", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_plugin_records_only_clean_tests(tmp_path) -> None:
    (tmp_path / "t.py").write_text(SAMPLE, encoding="utf-8")
    rec = tmp_path / "rec.txt"
    res = _pytest(tmp_path, "t.py", record=rec)
    assert res.returncode == 1, res.stdout + res.stderr
    got = _lines(rec)
    assert sorted(g for g in got if "test_ja" not in g) == [
        "t.py::test_ok1",
        "t.py::test_ok2",
        "t.py::test_skip",
        "t.py::test_xfail",
    ]
    assert len([g for g in got if g.startswith("t.py::test_ja[")]) == 2  # 日本語の ID
    assert not any("test_fail" in g or "teardown" in g for g in got)


def test_plugin_skips_recorded_and_runs_the_rest(tmp_path) -> None:
    (tmp_path / "t.py").write_text(SAMPLE, encoding="utf-8")
    rec = tmp_path / "rec.txt"
    _pytest(tmp_path, "t.py", record=rec)
    before = _lines(rec)
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    out = res.stdout
    assert res.returncode == 1, out
    assert "1 failed" in out and "1 error" in out and "6 deselected" in out
    # 動いたのは落ちる 1 件と teardown で落ちる 1 件だけ(後者は call が passed で teardown が error)
    assert "1 failed, 1 passed, 6 deselected, 1 error" in out
    assert "ci_resume: 前に通った 6 件を飛ばした" in out
    assert _lines(rec) == before  # 落ちる 2 件は増えない


def test_plugin_all_recorded_reruns_everything(tmp_path) -> None:
    # 全部が記録にあるとき: 1 件も流さずに成功にしない(前の回が終わりの処理で落ちたのかもしれない)
    (tmp_path / "t.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8")
    rec = tmp_path / "rec.txt"
    assert _pytest(tmp_path, "t.py", record=rec).returncode == 0
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 0, res.stdout
    assert "2 passed" in res.stdout and "deselected" not in res.stdout
    assert "全部流し直した" in res.stdout and "件を飛ばした" not in res.stdout
    # 全部が記録にあっても、今度落ちるなら落ちる(記録で成功にならない)
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _boom():\n    raise RuntimeError('x')\n",
        encoding="utf-8",
    )
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 1 and "2 errors" in res.stdout, res.stdout


def test_plugin_no_tests_at_all_stays_exit_5(tmp_path) -> None:
    rec = tmp_path / "rec.txt"
    rec.write_text("t.py::test_a\n", encoding="utf-8")
    res = _pytest(tmp_path, record=rec, skip=True)
    assert res.returncode == 5, res.stdout
    assert "飛ばした" not in res.stdout
    # 試験ファイルはあるが 1 件も合わない(deselect 0 件)ときも 5 のまま
    (tmp_path / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert _pytest(tmp_path, "t.py", "-k", "nomatch", record=rec, skip=True).returncode == 5


def test_plugin_without_env_does_nothing(tmp_path) -> None:
    (tmp_path / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    res = _pytest(tmp_path, "t.py")
    assert res.returncode == 0, res.stdout
    assert all(p.suffix != ".txt" for p in tmp_path.iterdir())  # 記録のファイルを作らない
    res = _pytest(tmp_path, "t.py", skip=True)  # FILE 無しで SKIP だけ: 飛ばさない
    assert res.returncode == 0 and "1 passed" in res.stdout


def test_plugin_ignores_torn_last_line(tmp_path) -> None:
    # 書いている途中で殺された(改行で終わらない)最後の行は信用しない
    (tmp_path / "t.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_ab():\n    pass\n", encoding="utf-8"
    )
    rec = tmp_path / "rec.txt"
    rec.write_bytes(b"t.py::test_a\nt.py::test_ab")  # test_ab は途中で切れたのかもしれない
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 0, res.stdout
    assert "1 passed" in res.stdout and "1 deselected" in res.stdout
    # 切れた行は追記の前に消す(改行で閉じて 1 行にしない)。test_ab は流して通ったので改めて 1 行
    assert _lines(rec) == ["t.py::test_a", "t.py::test_ab"]
    assert rec.read_bytes().endswith(b"\n")


def test_plugin_torn_line_never_becomes_a_record(tmp_path) -> None:
    # 切れた行と同じ名前の試験が今度は落ちる → 次の回でも飛ばされない(切れた行が記録として残らない)
    (tmp_path / "t.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_ab():\n    assert False\n", encoding="utf-8"
    )
    rec = tmp_path / "rec.txt"
    rec.write_bytes(b"t.py::test_ab")  # 記録の最初の行が切れている
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 1 and "1 failed, 1 passed" in res.stdout, res.stdout
    assert _lines(rec) == ["t.py::test_a"]
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 1 and "1 failed, 1 deselected" in res.stdout, res.stdout


def test_plugin_missing_record_file_is_empty(tmp_path) -> None:
    (tmp_path / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    rec = tmp_path / "nope.txt"
    res = _pytest(tmp_path, "t.py", record=rec, skip=True)
    assert res.returncode == 0 and "1 passed" in res.stdout
    assert _lines(rec) == ["t.py::test_a"]
