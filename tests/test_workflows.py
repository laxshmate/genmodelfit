# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GitHub Actions のワークフローの形の試験(#19)。読めること・権限・アクションの固定・毎週の PR の約束。

約束を変えたら docs/release.md「毎週の一覧の更新」も直す。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
SHA = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # PyYAML は on を True と読む


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _code(name: str) -> str:
    """コメントの行を除いた本文(説明の文に出てくる語で引っかからないように)。"""
    text = (WF / name).read_text(encoding="utf-8")
    return "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))


def _uses(wf: dict) -> list[str]:
    return [s["uses"] for job in wf["jobs"].values() for s in _steps(job) if "uses" in s]


@pytest.mark.parametrize("name", sorted(p.name for p in WF.glob("*.yml")))
def test_workflows_parse(name):
    wf = _load(name)
    assert wf["name"] and _on(wf) and wf["jobs"]
    assert wf["permissions"] == {"contents": "read"}  # 既定は読むだけ。書くジョブだけ広げる
    for job in wf["jobs"].values():
        assert "runs-on" in job and _steps(job)


@pytest.fixture(scope="module")
def weekly():
    return _load("weekly-data.yml")


def test_weekly_trigger(weekly):
    on = _on(weekly)
    # #42: 定期の実行はやめ、手動の予備だけ(毎週の更新はオーナーの PC のスキル)
    assert set(on) == {"workflow_dispatch"}
    assert weekly["concurrency"]["cancel-in-progress"] is False


def test_weekly_actions_are_pinned(weekly):
    for u in _uses(weekly):
        assert SHA.match(u), f"コミットで固定していない: {u}"


@pytest.mark.parametrize("name", sorted(p.name for p in WF.glob("*.yml")))
def test_all_actions_are_pinned(name):
    # 署名の鍵(data.yml)・PyPI の公開(release.yml)を使うジョブもあるので、
    # 動くタグ(@v4・@release/v1)は使わない。版はコメント(# v4.2.2)に残す
    for u in _uses(_load(name)):
        assert SHA.match(u), f"{name}: コミットで固定していない: {u}"


def test_release_pins_twine():
    # build のジョブが dist を検査する道具も、版を固定する(動く最新を取ると、公開する dist に触れられる)
    assert re.search(r"uvx twine@\d+\.\d+\.\d+ check", _code("release.yml"))


def test_weekly_permissions(weekly):
    jobs = weekly["jobs"]
    assert set(jobs) == {"build", "pr"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["pr"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert jobs["pr"]["needs"] == "build"
    for job in jobs.values():
        assert "environment" not in job  # 署名の鍵(data-signing)は使わない
        assert job.get("timeout-minutes")
    text = (WF / "weekly-data.yml").read_text(encoding="utf-8")
    assert "secrets." not in text and "GENMODELFIT_SIGNING_KEY" not in text
    assert "persist-credentials: false" in text


def test_weekly_build_steps(weekly):
    steps = _steps(weekly["jobs"]["build"])
    runs = [s.get("run", "") for s in steps]
    names = [s.get("uses", "").split("@")[0] for s in steps]
    i_restore = names.index("actions/cache/restore")
    i_weekly = next(i for i, r in enumerate(runs) if "python -m genmodelfit.weekly" in r)
    i_save = names.index("actions/cache/save")
    i_test = next(i for i, r in enumerate(runs) if "pytest -q" in r)
    assert i_restore < i_weekly < i_save < i_test
    assert "always()" in steps[i_save]["if"]  # 失敗・時間切れでもキャッシュを残す
    assert steps[i_weekly].get("timeout-minutes")
    cmd = runs[i_weekly]
    assert "--apply src/genmodelfit/data/db.json" in cmd and "--rate-limit-budget" in cmd
    assert "--dry-run" not in cmd
    # #34 の実測の集計はあるときだけ
    assert any("contrib aggregate --help" in r and "--report" in r for r in runs)
    # HEAD の期限が 7 日より先か
    assert any("expires_at" in r and "timedelta(days=7)" in r for r in runs)


def test_weekly_pr_step(weekly):
    steps = _steps(weekly["jobs"]["pr"])
    pr = next(s for s in steps if s.get("uses", "").startswith("peter-evans/create-pull-request@"))
    w = pr["with"]
    assert w["branch"] == "automated/weekly-data" and w["token"] == "${{ github.token }}"
    assert w["body-path"] == "weekly-out/pr_body.md"
    assert set(w["labels"].split()) == {"automated", "data-update"}
    assert w["add-paths"] == "src/genmodelfit/data/db.json"
    text = _code("weekly-data.yml")
    for bad in ("gh pr merge", "gh pr review", "auto-merge", "automerge", "gh release", "signing sign"):
        assert bad not in text, bad  # 自動でマージしない・ここでは公開しない
    assert any("result.json" in s.get("run", "") for s in steps)  # 確かめを通った一覧だけ


def test_data_yml_only_signs_main(weekly):
    data = _load("data.yml")
    text = _code("data.yml")
    assert not re.search(r"genmodelfit update(?! --from-release)", text)  # 作り直さない(案内の文は除く)
    assert "genmodelfit.weekly" not in text
    assert _on(data)["push"]["paths"] == ["src/genmodelfit/data/db.json"]
    assert data["jobs"]["sign"]["if"] == "github.ref == 'refs/heads/main'"
    # #42: 署名の前に確認待ちの項目を除く(0.5.x は review を知らない)
    runs = [st.get("run", "") for st in _steps(data["jobs"]["sign"])]
    i_pub = next(i for i, r in enumerate(runs) if "python -m genmodelfit.review publish" in r)
    i_sign = next(i for i, r in enumerate(runs) if "genmodelfit.signing sign" in r)
    assert i_pub < i_sign
