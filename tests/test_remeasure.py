# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# ruff: noqa: F811  (fixtures imported from test_share_pr)

"""ComfyUI の版が変わったら、この PC で測ったものも測り直しとして出す(#147)。

同じ版なら出さない。まだ測っていないものの後に並べ、「ComfyUI 0.19 → 0.20 で測り直し」と理由を出す。
今の版は、起動中の ComfyUI なら /system_stats、起動する前なら入っている ComfyUI の comfyui_version.py・
pyproject.toml の文字から読む(import しない)。本物の ComfyUI・GPU・GitHub には触らない。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_share_pr import GOOD, TOKEN, db, gh, sugg, this_pc  # noqa: F401
from test_share_pr import plan as share_plan  # noqa: F401

from genmodelfit import cli, comfystart, contrib, measure
from genmodelfit.models import Gpu, Measurement, ModelEntry, Settings, SystemInfo, Template

GPU = Gpu(name="NVIDIA GeForce RTX 3070", vendor="nvidia", vram_total_gb=8.0, backend="cuda")
PC = SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[GPU])


def _m(version, date="2026-10-01", **kw) -> Measurement:
    return Measurement(
        model_id="t1",
        gpu_name=GPU.name,
        gpu_vram_gb=8.0,
        peak_vram_gb=5.0,
        comfyui_version=version,
        date=date,
        **kw,
    )


ENTRY = ModelEntry(
    id="t1", name="T1", modality="image", source="comfyui-template", template=Template(name="t1")
)


# --- 版を比べる ---------------------------------------------------------------------------------


def test_stale_version_only_when_every_run_was_on_another_version():
    assert measure.stale_version(ENTRY, [_m("0.19.0")], GPU, "0.20.0") == "0.19.0"
    assert measure.stale_version(ENTRY, [_m("0.19.0")], GPU, "v0.19.0 ") is None  # 同じ版(表記をそろえる)
    # 同じ版の実測が 1 件でもあれば出さない(同じ人の実測は 1 回分にしか数えない)
    assert measure.stale_version(ENTRY, [_m("0.18.0"), _m("0.20.0")], GPU, "0.20.0") is None
    # いちばん新しい実測の版を出す
    runs = [_m("0.18.0", "2026-09-01"), _m("0.19.1", "2026-10-02")]
    assert measure.stale_version(ENTRY, runs, GPU, "0.20.0") == "0.19.1"
    # 分からないときは出さない: 今の版・実測の版が無い、実測が無い
    assert measure.stale_version(ENTRY, [_m("0.19.0")], GPU, None) is None
    assert measure.stale_version(ENTRY, [_m(None)], GPU, "0.20.0") is None
    assert measure.stale_version(ENTRY, [], GPU, "0.20.0") is None
    # ほかの GPU の実測は数えない
    other = Gpu(name="NVIDIA GeForce RTX 4090", vendor="nvidia", vram_total_gb=24.0, backend="cuda")
    assert measure.stale_version(ENTRY, [_m("0.19.0")], other, "0.20.0") is None


def test_stale_version_for_a_setting_looks_at_that_setting_only():
    run = _m("0.19.0", settings=Settings(width=512, height=512), settings_override=True)
    want = Settings(width=512, height=512)
    assert measure.stale_version(ENTRY, [run], GPU, "0.20.0", want) == "0.19.0"
    assert measure.stale_version(ENTRY, [run], GPU, "0.20.0", Settings(width=768, height=768)) is None
    assert measure.stale_version(ENTRY, [run], GPU, "0.20.0") is None  # テンプレートのままの実測ではない


# --- 提案 ---------------------------------------------------------------------------------------


def _save(model_id: str, version: str) -> dict:
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"].update(model_id=model_id, settings={}, note=None)
    d["measurement"].pop("files_fp", None)
    d["comfyui_version"] = version
    contrib.write_submission(d, str(measure.submissions_dir()) + "/")
    return d


def test_suggestions_offer_a_remeasure_after_the_new_ones(db):
    before = measure.suggestions(db, PC)
    target = next(x for x in before if x.settings is None)
    _save(target.model_id, "0.19.0")
    extra = cli._saved_measurements()
    same = measure.suggestions(db, PC, extra=extra, comfyui_version="0.19.0")
    assert target.model_id not in [x.model_id for x in same if x.settings is None]
    unknown = measure.suggestions(db, PC, extra=extra)  # 今の版が分からない: 今までどおり出さない
    assert target.model_id not in [x.model_id for x in unknown if x.settings is None]
    newer = measure.suggestions(db, PC, extra=extra, comfyui_version="0.20.0", variants_per_model=None)
    again = [x for x in newer if x.remeasure]
    row = next(x for x in again if x.model_id == target.model_id and x.settings is None)
    assert row.remeasure == ("0.19.0", "0.20.0")
    assert row.to_dict()["remeasure"] == {"from": "0.19.0", "to": "0.20.0"}
    # まだ測っていないものが先、測り直しは後
    first = newer.index(again[0])
    assert all(x.remeasure is None for x in newer[:first]) and all(x.remeasure for x in newer[first:])
    assert [x.to_dict()["remeasure"] for x in newer[:first]] == [None] * first


def test_row_says_why_it_is_remeasured():
    x = sugg("a", 60)
    x.remeasure = ("0.19", "0.20")
    with cli.i18n.forced("ja"):
        assert cli._suggestion_row(1, x).endswith("約 60 秒  ComfyUI 0.19 → 0.20 で測り直し")
        lines = cli._suggestion_lines([x])
        assert any(line.startswith("「測り直し」の行は") for line in lines)
    with cli.i18n.forced("en"):
        assert cli._suggestion_row(1, x).endswith("about 60 s  remeasure: ComfyUI 0.19 → 0.20")
    assert "測り直し" not in cli._suggestion_row(1, sugg("b", 60))


# --- 今の版 -------------------------------------------------------------------------------------


def test_installed_version_reads_text_only(tmp_path):
    d = tmp_path / "ComfyUI"
    d.mkdir()
    assert comfystart.installed_version(d) is None
    (d / "pyproject.toml").write_text(
        '[tool.x]\nversion = "9.9.9"\n\n[project]\nname = "ComfyUI"\nversion = "0.19.3"\n', encoding="utf-8"
    )
    assert comfystart.installed_version(d) == "0.19.3"  # [project] の節だけを見る
    (d / "comfyui_version.py").write_text(
        '# generated\n__version__ = "0.20.1"\nraise SystemExit("must not run")\n', encoding="utf-8"
    )
    assert comfystart.installed_version(d) == "0.20.1"  # 読むだけで実行しない
    (d / "comfyui_version.py").write_text('__version__ = "not a version"\n', encoding="utf-8")
    assert comfystart.installed_version(d) == "0.19.3"


def test_current_version_running_dry_run_and_before_start(monkeypatch, tmp_path):
    from test_comfystart import venv_install

    args = SimpleNamespace(dry_run=False)
    checker = SimpleNamespace(missing=lambda mid: [], comfyui_version=lambda: "0.20.0")
    assert cli._current_comfyui_version(args, "http://127.0.0.1:8188", None, checker) == "0.20.0"
    monkeypatch.setattr(cli, "_node_check", lambda url: pytest.fail("ほかの PC には尋ねない"))
    dry = SimpleNamespace(dry_run=True)
    assert cli._current_comfyui_version(dry, "http://192.0.2.1:8188", None) is None
    d = venv_install(tmp_path)
    (d / "comfyui_version.py").write_text('__version__ = "0.21.0"\n', encoding="utf-8")
    start = comfystart.plan_start(d)
    assert cli._current_comfyui_version(args, None, start) == "0.21.0"  # 起動する前に文字で読む
    assert cli._current_comfyui_version(args, None, None) is None


def test_measure_list_passes_the_version_and_shows_it(capsys, monkeypatch, this_pc, tmp_path):
    from test_comfystart import venv_install

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    d = venv_install(tmp_path)
    (d / "comfyui_version.py").write_text('__version__ = "0.21.0"\n', encoding="utf-8")
    seen = {}

    def fake(db, system, **k):
        seen.update(k)
        return []

    monkeypatch.setattr(measure, "suggestions", fake)
    assert cli.main(["measure", "--json", "--comfyui-dir", str(d)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["comfyui_version"] == "0.21.0" and out["comfyui"]["version"] == "0.21.0"


def test_share_passes_the_running_version(capsys, monkeypatch, this_pc, share_plan, gh):
    seen = {}

    def spy(db, system, **k):
        seen.update(k)
        return share_plan["sugg"]

    monkeypatch.setattr(measure, "suggestions", spy)
    checker = SimpleNamespace(missing=lambda mid: [], comfyui_version=lambda: "0.20.0")
    monkeypatch.setattr(cli, "_node_check", lambda url: checker)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--yes", "--json", "--url", "http://127.0.0.1:8188"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["comfyui_version"] == "0.20.0" and out["comfyui"]["version"] == "0.20.0"
