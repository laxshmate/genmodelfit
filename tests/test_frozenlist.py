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
    rec = out["sources"][tool.HASH_KEY]
    assert rec[new_id] == tool.entry_hash(by[new_id]) and set(rec) == set(by)  # 足した項目の記録も足す


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
    out = _raw(copy_)
    by = {m["id"]: m for m in out["models"]}
    assert by[a]["name"] == "changed" and by[b]["name"] != "changed"
    assert out["sources"][tool.HASH_KEY][a] == tool.entry_hash(by[a])  # 記録を置き換える
    assert tool.main(["--check", "--frozen", str(copy_)]) == 0
    assert tool.main(["--frozen", str(copy_), "--db", str(src), "--refresh", "hf:no-such/model-197"]) == 1


def _write_in_form(tool, path: Path, raw: dict) -> None:
    """道具と同じ形で書く(形の検査には掛からず、記録の検査だけが見つける手の直し)。"""
    path.write_text(tool.render(raw), encoding="utf-8", newline="\n")


def test_check_finds_a_hand_edit_of_an_entry(tool, copy_):
    """項目の中身を手で直すと、同じ形で書いても --check が落ち、引数なしの書き直しは書かずに失敗する。
    --rehash ではっきり認めたら通る。"""
    raw = _raw(copy_)
    m = next(m for m in raw["models"] if m["content"] == "uncensored")
    m["content"] = "sfw"
    _write_in_form(tool, copy_, raw)
    assert tool.render(_raw(copy_)) == copy_.read_text(encoding="utf-8")  # 形は合っている
    assert tool.hash_problems(_raw(copy_)) == [m["id"]]
    assert tool.main(["--check", "--frozen", str(copy_)]) == 1
    before = copy_.read_bytes()
    assert tool.main(["--frozen", str(copy_)]) == 1
    assert copy_.read_bytes() == before
    assert tool.main(["--rehash", "--frozen", str(copy_)]) == 0
    assert tool.main(["--check", "--frozen", str(copy_)]) == 0
    # 項目を消した記録・記録の無い項目も見つける
    raw = _raw(copy_)
    gone = raw["models"].pop()
    _write_in_form(tool, copy_, raw)
    assert tool.hash_problems(_raw(copy_)) == [gone["id"]]
    assert tool.main(["--check", "--frozen", str(copy_)]) == 1


@pytest.mark.parametrize("args", [["--check"], [], ["--add", "x"], ["--refresh", "x"], ["--rehash"]])
def test_unreadable_schema_ends_with_a_message(tool, copy_, capsys, args):
    """一覧の形の版が違う(SCHEMA_VERSION を上げた後など)と、traceback でなく文と終了コード 1 で終わる。"""
    raw = _raw(copy_)
    raw["schema_version"] = 999
    copy_.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    before = copy_.read_bytes()
    assert tool.main([*args, "--frozen", str(copy_)]) == 1
    err = capsys.readouterr().err
    assert ("今の形で読めません" in err or "形の版が違う" in err) and "Traceback" not in err
    assert copy_.read_bytes() == before


def test_rederive_restores_a_broken_derived_value(tool, copy_):
    """計算で決まる値を壊した写しに --rederive を当てると、今の規則の値に戻る(記録も作り直す)。"""
    raw = _raw(copy_)
    orig = {m["id"]: m for m in _raw(copy_)["models"]}  # 壊す前の写し
    m = next(m for m in raw["models"] if m.get("comfy_family"))
    m["comfy_family"] = "no-such-family"
    copy_.write_text(
        json.dumps(tool._with_hashes(raw), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    assert tool.main(["--rederive", "--frozen", str(copy_)]) == 0
    out = _raw(copy_)
    assert {x["id"]: x for x in out["models"]} == orig
    assert tool.main(["--check", "--frozen", str(copy_)]) == 0


REDERIVE = "tools/freeze_test_list.py --rederive で当て直す"


@pytest.fixture(scope="module")
def frozen_entries():
    return load(FROZEN_DB, user_measurements=False).models


def test_frozen_families_match_the_current_rules(frozen_entries):
    """凍らせた項目の系統・VAE が今の表と同じ(同梱の一覧の test_bundled_catalog_has_families と同じ
    比べ方)。"""
    from genmodelfit.families import FAMILIES, VAES, family_of, vae_of

    bad = [
        m.id
        for m in frozen_entries
        if m.comfy_family != family_of(m) or m.comfy_vae != vae_of(m, m.comfy_family)
    ]
    assert not bad, f"{bad}: {REDERIVE}"
    assert all(m.comfy_family is None or m.comfy_family in FAMILIES for m in frozen_entries)
    assert all(m.comfy_vae is None or m.comfy_vae in VAES for m in frozen_entries)


def test_frozen_app_fields_are_stable_under_reapplying(frozen_entries):
    """当て直しでアプリ向けの値が変わらない(同梱の一覧の test_bundled_is_stable_under_reapplying と同じ)。"""
    import copy

    from genmodelfit import appinfo

    models = copy.deepcopy(frozen_entries)
    before = {m.id: (m.languages, m.runtime, m.description) for m in models}
    appinfo.apply_app_fields(models)
    bad = [m.id for m in models if (m.languages, m.runtime, m.description) != before[m.id]]
    assert not bad, f"{bad}: {REDERIVE}"
    for m in models:
        if m.source != "manual":  # 手のモデルの項目は manual.json に書いてある
            m.languages, m.runtime, m.description = {}, None, {}
    appinfo.apply_app_fields(models)
    bad = [m.id for m in models if (m.languages, m.runtime, m.description) != before[m.id]]
    assert not bad, f"{bad}: {REDERIVE}"


def test_frozen_completeness_is_stable_under_reapplying(frozen_entries):
    import copy

    from genmodelfit.update import set_completeness

    models = copy.deepcopy(frozen_entries)
    before = {m.id: (m.files_complete, m.size_floor_bytes) for m in models}
    for m in models:
        set_completeness(m)
    bad = [m.id for m in models if (m.files_complete, m.size_floor_bytes) != before[m.id]]
    assert not bad, f"{bad}: {REDERIVE}"
