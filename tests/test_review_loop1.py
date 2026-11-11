# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""自己改善ループ 1 回目(2026-11-11)のレビュー指摘の回帰試験。"""

from __future__ import annotations

import errno
import json

import pytest

from genmodelfit import cli


def _raise(exc: BaseException):
    def f(args):
        raise exc

    return f


def test_bad_filename_einval_is_an_error_not_success(monkeypatch, capsys):
    # Windows で不正な名前("x|y.json")を open すると EINVAL(22)。パイプ閉じと取り違えて 0 にしない
    monkeypatch.setattr(cli, "cmd_types", _raise(OSError(errno.EINVAL, "Invalid argument", "x|y.json")))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)
    code = cli.main(["types"])
    assert code == 1
    err = capsys.readouterr().err
    assert "x|y.json" in err


def test_permission_error_has_no_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "cmd_types", _raise(PermissionError(errno.EACCES, "Permission denied", "out.json"))
    )
    code = cli.main(["types"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "out.json" in captured.err


def test_os_error_with_json_gives_the_error_envelope(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "cmd_types", _raise(OSError(errno.ENOSPC, "No space left on device", "out.json"))
    )
    code = cli.main(["types", "--json"])
    assert code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "types"
    assert "out.json" in doc["error"]


@pytest.mark.parametrize("exc", [OSError(errno.EINVAL, "Invalid argument")])
def test_closed_pipe_einval_without_filename_still_succeeds(monkeypatch, exc):
    # パイプを閉じられた EINVAL(ファイル名なし)は、これまでどおり 0
    monkeypatch.setattr(cli, "cmd_types", _raise(exc))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)
    quieted = []
    monkeypatch.setattr(cli, "_quiet_stdout", lambda: quieted.append(True))
    assert cli.main(["types"]) == 0
    assert quieted


def test_comfyui_logs_are_pruned(tmp_path):
    from genmodelfit import comfystart

    for i in range(15):
        (tmp_path / f"comfyui-20261101-0000{i:02d}.log").write_text("x", encoding="utf-8")
    (tmp_path / "other.log").write_text("keep", encoding="utf-8")
    comfystart.prune_logs(tmp_path, 10)
    left = sorted(p.name for p in tmp_path.glob("comfyui-*.log"))
    assert len(left) == 10
    assert left[0] == "comfyui-20261101-000005.log"  # 古いものから消える
    assert (tmp_path / "other.log").exists()


def test_broken_cache_measurement_does_not_stop_loading(caplog):
    from genmodelfit.db import DatabaseError, cache_dir, load_measurements

    d = cache_dir() / "measurements"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    bundled = len(load_measurements(include_cache=False))
    assert len(load_measurements(include_cache=True)) == bundled
    assert "broken.json" in caplog.text
    # 渡されたフォルダの壊れたファイルは、これまでどおりエラー
    with pytest.raises(DatabaseError):
        load_measurements([d], include_cache=False)


@pytest.mark.parametrize("bad", ["../x", "a/b.png", "x.png?raw=1", "x#y", "..", ".hidden", "%2e%2e", ""])
def test_template_urls_reject_path_tricks(bad):
    from genmodelfit.workflow import WorkflowError, template_input_url, template_url

    with pytest.raises(WorkflowError):
        template_input_url(bad)
    with pytest.raises(WorkflowError):
        template_url(bad)


def test_template_urls_keep_normal_names():
    from genmodelfit.workflow import TEMPLATES_RAW, template_input_url, template_url

    assert (
        template_url("image_z_image_turbo_int8") == f"{TEMPLATES_RAW}/templates/image_z_image_turbo_int8.json"
    )
    assert template_input_url("my image (1).png") == f"{TEMPLATES_RAW}/input/my%20image%20%281%29.png"
