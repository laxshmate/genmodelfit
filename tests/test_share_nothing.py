# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# ruff: noqa: F811  (fixtures imported from test_share_pr)

"""share の「送るものはありません」に、理由と次にできることを出す(#146)。

本物の GitHub・GPU・ComfyUI には触らない(test_share_pr の偽物を使う)。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_share_pr import TOKEN, FakeNodes, _write_sub, answers, gh, no_session, plan, sugg, this_pc  # noqa: F401

from genmodelfit import cli, measure


def codes(d: dict) -> list[str]:
    return [r["code"] for r in d["reasons"]]


def test_already_measured_here_says_so_and_points_to_measure(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = []
    monkeypatch.setattr(measure, "measured_here_count", lambda db, s, extra=(): 3)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "送るものはありません"
    assert (
        "  - この PC に入っていて GPU に載るモデルは、この PC でもう測ってあります(この GPU で 3 件)" in out
    )
    assert "  - 送っていない実測はありません" in out and "--all" not in out
    assert out.rstrip().endswith("次にできること: ほかのモデルを測る: genmodelfit measure <名前>")
    assert cli.main(["--lang", "en", "share", "--yes", "--json", "--url", "http://x:1"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert codes(d) == ["measured_here", "no_unsent"] and d["sent"] is False
    assert d["reason"].startswith("Nothing to send. The installed models")
    assert d["next_step"] == "Measure another model: genmodelfit measure <name>"


def test_missing_nodes_say_update_comfyui(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("a_installed_short", 60)]
    nodes = FakeNodes({"a_installed_short": ["LoadMoGeModel"]})
    monkeypatch.setattr(cli, "_node_check", lambda url: nodes)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    # ComfyUI がほかの PC(どの GPU か分からない)なので失敗の記録は書かず、送るものは無い
    assert cli.main(["--lang", "ja", "share", "--yes", "--json", "--url", "http://x:1"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert codes(d) == ["missing_nodes", "no_unsent"]
    assert d["next_step"].startswith("ComfyUI を更新してから、もう一度 genmodelfit share")


def test_declined_download_points_to_download_flag(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: pytest.fail("落とさない"))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "")  # 落とす → Enter(既定は No)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    assert "  - 入っていない 1 件(2.0 GB)は、落とすのを断りました" in out
    assert "  - この PC に入っていて、GPU に載り測れるモデルがありません" in out
    assert "もう測ってあります" not in out  # この PC で測ったものは無いので、それは理由にしない
    assert "次にできること: 落として測る: genmodelfit share --download" in out


def test_declined_download_and_installed_over_budget_give_both(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("a_long", 400), sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "")  # 落とす → Enter(既定は No)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    assert "  - 時間の上限(300 秒)に収まるものがありません" in out
    assert "  - 入っていない 1 件(2.0 GB)は、落とすのを断りました" in out
    # 次の一手は 1 行だけ: 入っているものを測れるように上限を広げる(断った落とす話は繰り返さない)
    assert out.splitlines()[-1] == "次にできること: 上限を広げる: genmodelfit share --budget 400"


def test_yes_without_terminal_says_it_never_downloads(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "en", "share", "--yes", "--json", "--url", "http://x:1"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert "not_downloaded" in codes(d) and d["next_step"].endswith("genmodelfit share --download")


def test_sent_before_points_to_resend(capsys, monkeypatch, gh):
    _write_sub(1)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--no-measure", "--yes", "--json"]) == 0  # 送って印を付ける
    capsys.readouterr()
    assert cli.main(["--lang", "ja", "share", "--no-measure", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "  - 測らない指定です(--no-measure)" in out
    assert (
        "送っていない実測はありません(送ったものを送り直すなら genmodelfit share --no-measure --all)" in out
    )
    assert "次にできること: 送ったものを送り直す: genmodelfit share --no-measure --all" in out


def test_failed_runs_are_a_reason(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("a_installed_short", 60)]
    monkeypatch.setattr(cli, "_measure_run", lambda *a, **k: {"error": "ValueError: boom"})
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "en", "share", "--yes", "--json", "--url", "http://x:1"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert codes(d) == ["failed", "no_unsent"]


def _args(**kw):
    return SimpleNamespace(no_measure=False, **kw)


def test_reasons_from_the_data_share_has(monkeypatch, tmp_path):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    unknown = sugg("u", 60, "unknown")
    long = sugg("l", 400)
    with cli.i18n.forced("en"):
        d = cli._share_nothing(_args(), sugg=[unknown], budget=300)
        assert codes(d) == ["installed_unknown", "no_unsent"]
        assert "--comfyui-dir" in d["next_step"]
        d = cli._share_nothing(_args(), sugg=[long], budget=300)
        assert codes(d) == ["budget", "no_unsent"] and d["next_step"].endswith("--budget 400")
        # 入っていないものも含めて、どれも収まらない
        d = cli._share_nothing(_args(), sugg=[sugg("n", 500, "no", 1)], budget=300, measured_here=lambda: 0)
        assert codes(d) == ["no_installed", "budget", "no_unsent"] and d["next_step"].endswith("--budget 500")
        # この PC で測ったものがあるときだけ「もう測ってある」
        d = cli._share_nothing(_args(), sugg=[], budget=300, measured_here=lambda: 2)
        assert codes(d) == ["measured_here", "no_unsent"]
        d = cli._share_nothing(_args(), sugg=[], budget=300, measured_here=lambda: 0)
        assert codes(d) == ["no_installed", "no_unsent"]
        d = cli._share_nothing(
            _args(_share_failures_known=True),
            sugg=[sugg("a", 60)],
            skipped=[{"model_id": "a", "name": "a", "missing_nodes": ["X"]}],
            budget=300,
            interrupted=True,
            failed=[{"model_id": "c", "error": "x", "unsendable": ["spilled"]}],
        )
        assert codes(d) == ["missing_nodes", "unsendable", "interrupted", "failures_sent", "no_unsent"]
        # 何も分からないときも次の一手を出す
        d = cli._share_nothing(_args(), sugg=[sugg("a", 60)], budget=300)
        assert codes(d) == ["no_unsent"] and "genmodelfit measure" in d["next_step"]


def test_measured_here_count_counts_this_pcs_template_runs(this_pc):
    from genmodelfit.db import Database
    from genmodelfit.models import Measurement, ModelEntry, Template

    e = ModelEntry(
        id="t1", name="T1", modality="image", source=measure.TEMPLATE_SOURCE, template=Template(name="t1")
    )
    other = ModelEntry(id="hf:x/y", name="Y", modality="image", source="huggingface")
    m = Measurement(model_id="t1", gpu_name="NVIDIA GeForce RTX 3070", gpu_vram_gb=8.0, peak_vram_gb=5.0)
    m2 = Measurement(model_id="hf:x/y", gpu_name="NVIDIA GeForce RTX 3070", gpu_vram_gb=8.0, peak_vram_gb=5.0)
    db = Database(models=[e, other], measurements=[m, m2])
    assert measure.measured_here_count(db, this_pc) == 1
    assert measure.measured_here_count(Database(models=[e], measurements=[]), this_pc, extra=[m]) == 1
    assert measure.measured_here_count(Database(models=[e], measurements=[]), this_pc) == 0
