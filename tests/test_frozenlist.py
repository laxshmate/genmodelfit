# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""凍らせた一覧(tests/fixtures/frozen/db.json)と、それを作る tools/freeze_test_list.py の試験(#197)。
手入れの手順は tests/frozenlist.py の先頭。ネットワークには触らない。"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from frozenlist import FROZEN_DB

from genmodelfit.db import load

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "src" / "genmodelfit" / "data" / "db.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("freeze_test_list", ROOT / "tools" / "freeze_test_list.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def copy_(tmp_path) -> Path:
    dst = tmp_path / "frozen.json"
    shutil.copyfile(FROZEN_DB, dst)
    return dst


def _raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_frozen_list_is_in_the_tools_form(tool):
    """凍らせた一覧は道具の出力と同じ形(並び・数え直した sources)。違えば道具で書き直す。"""
    assert tool.main(["--check"]) == 0
    raw = _raw(FROZEN_DB)
    ids = [m["id"] for m in raw["models"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    db = load(FROZEN_DB, user_measurements=False)
    assert raw["sources"]["counts"]["models"] == len(db.models)
    assert raw["sources"]["review"]["pending"] == sum(not m.reviewed for m in db.models)
    frm = raw["sources"]["_frozen_from"]
    assert set(frm) >= {"commit", "generated_at"} and frm["generated_at"] == raw["generated_at"]


def test_check_finds_a_hand_edit(tool, copy_):
    raw = _raw(copy_)
    raw["models"].reverse()  # 並びを崩す
    copy_.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    assert tool.main(["--check", "--frozen", str(copy_)]) == 1
    raw["models"].reverse()
    raw["sources"]["counts"]["models"] += 1  # 数え直していない
    copy_.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    assert tool.main(["--check", "--frozen", str(copy_)]) == 1


def test_no_arguments_keep_the_frozen_entries(tool, copy_, tmp_path):
    """引数なしは今の凍らせた項目を保つ(写す元の一覧の中身で置き換えない)。同じ入力なら同じバイト。"""
    other = _raw(copy_)
    for m in other["models"]:
        m["name"] = "changed"
    src = tmp_path / "other.json"
    src.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    before = copy_.read_bytes()
    assert tool.main(["--frozen", str(copy_), "--db", str(src)]) == 0
    assert copy_.read_bytes() == before


def test_add_copies_an_entry_from_the_bundled_list(tool, copy_):
    bundled = _raw(BUNDLED)
    frozen = _raw(copy_)
    have = {m["id"] for m in frozen["models"]}
    new_id = next(m["id"] for m in bundled["models"] if m["id"] not in have)
    assert tool.main(["--frozen", str(copy_), "--add", new_id]) == 0
    out = _raw(copy_)
    by = {m["id"]: m for m in out["models"]}
    assert by[new_id] == next(m for m in bundled["models"] if m["id"] == new_id)  # そのまま写す
    for m in frozen["models"]:  # 前からの項目は動かさない
        assert by[m["id"]] == m
    assert out["sources"]["counts"]["models"] == len(frozen["models"]) + 1
    assert out["sources"]["_frozen_later"][-1]["ids"] == [new_id]
    assert out["sources"]["_frozen_from"] == frozen["sources"]["_frozen_from"]
    assert tool.main(["--check", "--frozen", str(copy_)]) == 0
    assert load(copy_, user_measurements=False).get(new_id) is not None


def test_add_fails_for_an_id_not_in_the_bundled_list(tool, copy_):
    before = copy_.read_bytes()
    assert tool.main(["--frozen", str(copy_), "--add", "hf:no-such/model-197"]) == 1
    have = next(m["id"] for m in _raw(copy_)["models"])
    assert tool.main(["--frozen", str(copy_), "--add", have]) == 1  # もう凍らせてある(--refresh を使う)
    assert copy_.read_bytes() == before


def test_refresh_rewrites_only_the_named_entry(tool, copy_, tmp_path):
    frozen = _raw(copy_)
    a, b = frozen["models"][0]["id"], frozen["models"][1]["id"]
    other = json.loads(json.dumps(frozen))
    for m in other["models"]:
        m["name"] = "changed"
    src = tmp_path / "other.json"
    src.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    assert tool.main(["--frozen", str(copy_), "--db", str(src), "--refresh", a]) == 0
    by = {m["id"]: m for m in _raw(copy_)["models"]}
    assert by[a]["name"] == "changed" and by[b]["name"] != "changed"
    assert tool.main(["--frozen", str(copy_), "--db", str(src), "--refresh", "hf:no-such/model-197"]) == 1
