# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""tools/ci_local.py の pytest で、通った試験を 1 件ずつ記録し、続きから流すためのプラグイン。

    python -m pytest -q -p no:cacheprovider -p ci_resume      # PYTHONPATH にこのフォルダを足して使う

このファイルだけを専用のフォルダに置く(PYTHONPATH に足しても、ほかの道具は試験から import できない)。

環境変数(どれも無ければ何もしない。置いてあるだけで害は無い):
- GMF_CI_RESUME_FILE: 通った試験の nodeid を 1 行ずつ追記するファイル(UTF-8)
- GMF_CI_RESUME_SKIP=1: そのファイルにある nodeid の試験を集めた中から外す(deselect)

記録するのは setup・call・teardown のどれも失敗しなかった試験(passed・skipped・xfail)だけ。
failed・error は書かない。pytest は 1 プロセスで流す前提(xdist は使わない)。

集めた試験が全部記録にあるときは、1 件も外さず全部流し直す。前の回が「試験は全部通ったのに、終わりの処理で
失敗した」のか「終わる直前に止められた」のかを記録から区別できないので、1 件も流さずに成功にはしない。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_bad: set[str] = set()  # setup・call・teardown のどれかが失敗した nodeid
_state = {"deselected": 0, "all_done": False, "tail_checked": False}


def _file() -> str | None:
    return os.environ.get("GMF_CI_RESUME_FILE") or None


def _read(path: str) -> set[str]:
    """改行で終わっていない最後の行は、書いている途中で止められたかもしれないので捨てる
    (切れた nodeid が別の試験の nodeid と一致して、通っていない試験を飛ばすのを防ぐ)。"""
    try:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return set()
    lines = text.split("\n")
    return {ln for ln in lines[:-1] if ln}


def _drop_torn_tail(path: str) -> None:
    """改行で終わっていない最後の行(書いている途中で止められた行)を、追記の前に消す。
    改行を足して閉じると、切れた nodeid が次の回から 1 行として読まれてしまう。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return
    if data and not data.endswith(b"\n"):
        Path(path).write_bytes(data[: data.rfind(b"\n") + 1])


def _append(path: str, nodeid: str) -> None:
    if "\n" in nodeid or "\r" in nodeid:
        return
    try:
        data = (nodeid + "\n").encode("utf-8")
    except UnicodeEncodeError:
        return
    if not _state["tail_checked"]:
        _state["tail_checked"] = True
        _drop_torn_tail(path)
    with open(path, "ab") as f:
        f.write(data)
        f.flush()


# trylast: ほかのプラグイン・conftest が並べ替え・絞り込みを済ませた後の一覧を見る
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    path = _file()
    if not path or os.environ.get("GMF_CI_RESUME_SKIP") != "1":
        return
    done = _read(path)
    skipped = [it for it in items if it.nodeid in done]
    keep = [it for it in items if it.nodeid not in done]
    if skipped and not keep:
        _state["all_done"] = True  # 全部が記録にある: 外さずに全部流し直す
        return
    if skipped:
        config.hook.pytest_deselected(items=skipped)
        items[:] = keep
    _state["deselected"] = len(skipped)


def pytest_runtest_logreport(report) -> None:
    path = _file()
    if not path:
        return
    if report.failed:
        _bad.add(report.nodeid)
    if report.when == "teardown" and report.nodeid not in _bad:
        _append(path, report.nodeid)


def pytest_terminal_summary(terminalreporter) -> None:
    if _state["deselected"] > 0:
        terminalreporter.write_line(f"ci_resume: 前に通った {_state['deselected']} 件を飛ばした")
    if _state["all_done"]:
        terminalreporter.write_line("ci_resume: 全部が記録にあったので、飛ばさずに全部流し直した")
