# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""毎週の更新の無検閲の上限を、元の一覧を作り直してからの週の数に合わせる(#197)。ネットワークには触らない。

1 週あたりは今までと同じ 5 件(weekly.MAX_NEW_UNCENSORED)。通らなかった週の分がたまっても、次の週に通れる。
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from frozenlist import FROZEN_DB, frozen_db

from genmodelfit import update, weekly
from genmodelfit.db import Database, load
from genmodelfit.models import ModelEntry, Review

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(k, raising=False)


def _base(generated_at: str | None) -> Database:
    db = frozen_db()
    db.generated_at = generated_at
    return db


def _run(tmp_path, monkeypatch, generated_at: str | None, n: int, **kw) -> dict:
    """元の一覧の作り直した日を generated_at にし、新しく無検閲の項目を n 件足した作り直しで
    weekly を回す。"""

    def fake(fetch, out_path, *, allow_partial, progress, previous):
        new = copy.deepcopy(previous)
        stamp = _iso(NOW - timedelta(hours=1))
        for m in new.models:
            for f in m.files:
                if f.head_checked_at:
                    f.head_checked_at = stamp
        new.generated_at = stamp
        new.models += [
            ModelEntry(
                id=f"hf:n/{i}", name=f"n{i}", modality="image", source="huggingface", content="uncensored"
            )
            for i in range(n)
        ]
        new.save(out_path)
        return new

    monkeypatch.setattr(weekly, "update_database", fake)
    monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
    base = tmp_path / f"base-{n}.json"
    _base(generated_at).save(base)
    return weekly.run(tmp_path / f"out-{n}", base=base, fetch=object(), now=NOW, gpu_watch=False, **kw)


def _uncensored(r: dict) -> list[str]:
    return [p for p in r["problems"] if "無検閲" in p]


@pytest.mark.parametrize(
    "ago", [timedelta(hours=12), timedelta(days=6, hours=20), timedelta(days=7), timedelta(days=7, hours=3)]
)
def test_a_normal_week_stays_five(tmp_path, monkeypatch, ago):
    """ふだんの週(前の作り直しから 7 日 + 数時間。毎週の起動の時刻はずれる)は今までと同じ 5 件
    (1 週あたりの割合は緩めない)。"""
    made = _iso(NOW - ago)
    assert weekly.uncensored_limit(_base(made), NOW) == (5, 1)
    r = _run(tmp_path, monkeypatch, made, 6)
    assert r["ok"] is False and _uncensored(r)
    assert "6 件あります(上限 5 件 = 5 件 × 1 週)" in _uncensored(r)[0]
    r = _run(tmp_path, monkeypatch, made, 5)
    assert r["ok"] is True, r["problems"]


def test_weeks_since_the_rebuild_add_up(tmp_path, monkeypatch):
    """27 日前に作り直した一覧なら 4 週 = 20 件(通らなかった週の分がたまっても通る)。"""
    made = _iso(NOW - timedelta(days=27))
    assert weekly.uncensored_limit(_base(made), NOW) == (20, 4)
    r = _run(tmp_path, monkeypatch, made, 16)
    assert r["ok"] is True, r["problems"]
    r = _run(tmp_path, monkeypatch, made, 21)
    assert r["ok"] is False
    (msg,) = _uncensored(r)
    assert "21 件あります(上限 20 件 = 5 件 × 4 週)" in msg and "選び方の規則" in msg


@pytest.mark.parametrize(
    ("ago", "limit"),
    [
        (timedelta(days=6, hours=20), 5),
        (timedelta(days=7, hours=3), 5),  # 切り上げなら 10 件(ふだんの週で 2 倍になる)
        (timedelta(days=10), 5),
        (timedelta(days=10, hours=11), 5),
        (timedelta(days=10, hours=12), 10),  # 10.5 日 = 1.5 週。切り捨てなら 5 件(たまった分を数えない)
        (timedelta(days=14), 10),
        (timedelta(days=14, hours=2), 10),
        (timedelta(days=17, hours=12), 15),  # 2.5 週。round() の偶数への丸めなら 2 週 = 10 件
        (timedelta(days=27.2), 20),
    ],
)
def test_weeks_are_rounded_to_the_nearest(ago, limit):
    """週の数は四捨五入(切り上げ・切り捨てでも、round() の偶数への丸めでもない)。"""
    assert weekly.uncensored_limit(_base(_iso(NOW - ago)), NOW) == (limit, limit // 5)


@pytest.mark.parametrize("made", [None, "", "not a date", "2026-11-17", _iso(NOW + timedelta(days=3))])
def test_unreadable_or_future_date_is_one_week(tmp_path, monkeypatch, made):
    """日が読めない(無い・形が違う・タイムゾーンが無い)・今より先なら 1 週 = 5 件。"""
    assert weekly.uncensored_limit(_base(made), NOW) == (5, 1)


def test_unreadable_date_fails_at_six(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, "not a date", 6)
    assert r["ok"] is False and "上限 5 件 = 5 件 × 1 週" in _uncensored(r)[0]
    assert _run(tmp_path, monkeypatch, "not a date", 5)["ok"] is True


def test_weeks_are_capped(tmp_path, monkeypatch):
    """週の数には上限がある(8 週 = 40 件まで。超えたら今までどおり失敗して人が見る)。"""
    assert weekly.MAX_UNCENSORED_WEEKS == 8
    for days in (8 * 7, 8 * 7 + 1, 400):
        assert weekly.uncensored_limit(_base(_iso(NOW - timedelta(days=days))), NOW) == (40, 8), days
    assert weekly.uncensored_limit(_base("1970-01-01T00:00:00Z"), NOW) == (40, 8)
    made = _iso(NOW - timedelta(days=400))
    assert _run(tmp_path, monkeypatch, made, 40)["ok"] is True
    r = _run(tmp_path, monkeypatch, made, 41)
    assert r["ok"] is False and "41 件あります(上限 40 件 = 5 件 × 8 週)" in _uncensored(r)[0]


def test_allow_many_uncensored_only_warns(tmp_path, monkeypatch):
    made = _iso(NOW - timedelta(days=27))
    r = _run(tmp_path, monkeypatch, made, 21, allow_many_uncensored=True)
    assert r["ok"] is True and not _uncensored(r)
    (msg,) = [w for w in r["warnings"] if "無検閲" in w]
    assert "21 件あります(上限 20 件 = 5 件 × 4 週)" in msg
    body = (tmp_path / "out-21" / "pr_body.md").read_text(encoding="utf-8")
    assert "上限 20 件 = 5 件 × 4 週" in body.split("### 要約")[0]


def test_heads_only_keeps_the_rebuild_date(tmp_path, monkeypatch):
    """作り直した日は generated_at。HEAD だけのやり直し(update --heads-only)では変わらないので、週の数は
    戻らない(たまった分がまた通らなくなることが無い)。"""

    class Fake:
        def head_status(self, url):
            return 302

        def get_json(self, url):
            assert url == update.COMFYUI_RELEASE_URL
            return {"tag_name": "v9.9.9"}

    src = load(FROZEN_DB)
    out = tmp_path / "db.json"
    db = update.refresh_heads(Fake(), out, db_path=str(FROZEN_DB), use_cache=False)
    assert db.sources["head_check"]["urls"] > 0  # HEAD はやり直した
    assert load(out).generated_at == db.generated_at == src.generated_at
    later = src.generated_time + timedelta(days=27)
    assert weekly.uncensored_limit(load(out), later) == weekly.uncensored_limit(src, later) == (20, 4)


def _with_pending(db: Database, ids: list[str]) -> Database:
    """db の写しで、ids の項目を確認待ち(毎週の確かめで保留にした形)にしたもの。sources の数も合わせる。"""
    out = copy.deepcopy(db)
    for m in out.models:
        if m.id in ids:
            m.review = Review("pending", ["new"])
    rv = out.sources.get("review") if isinstance(out.sources.get("review"), dict) else {}
    pending = sum(not m.reviewed for m in out.models)
    out.sources["review"] = {**rv, "ok": len(out.models) - pending, "pending": pending}
    return out


def test_pending_entries_in_the_base_carry_over(tmp_path, monkeypatch):
    """前の一覧(main の db.json)に確認待ちが残っていても(hold は pending のまま main に残る。
    docs/release.md「毎週のスキル」)、毎週の流れは落ちず、前からの確認待ちは review_queue.json に残り、
    それだけを理由に problems が増えない。"""
    made = _iso(NOW - timedelta(days=7, hours=3))
    clean = _base(made)
    ids = [m.id for m in clean.models if m.source == "huggingface"][:3]
    assert len(ids) == 3

    def run(base_db: Database, name: str) -> dict:
        def fake(fetch, out_path, *, allow_partial, progress, previous):
            new = copy.deepcopy(previous)  # 作り直しは前の確認の印を引き継ぐ
            stamp = _iso(NOW - timedelta(hours=1))
            for m in new.models:
                for f in m.files:
                    if f.head_checked_at:
                        f.head_checked_at = stamp
            new.generated_at = stamp
            new.save(out_path)
            return new

        monkeypatch.setattr(weekly, "update_database", fake)
        monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
        base = tmp_path / f"{name}.json"
        base_db.save(base)
        return weekly.run(tmp_path / name, base=base, fetch=object(), now=NOW, gpu_watch=False)

    plain = run(clean, "clean")
    held = run(_with_pending(clean, ids), "held")
    assert held["ok"] is True, held["problems"]
    assert held["problems"] == plain["problems"] == []
    q = json.loads((tmp_path / "held" / "review_queue.json").read_text(encoding="utf-8"))
    assert sorted(p["id"] for p in q["pending"]) == sorted(ids)
    assert sorted(p["id"] for p in held["review_pending"]) == sorted(ids)
    body = (tmp_path / "held" / "pr_body.md").read_text(encoding="utf-8")
    assert "### 確認待ち(review: pending)の項目: 3 件" in body


@pytest.mark.parametrize(("ago", "weeks"), [(timedelta(days=7, hours=3), 1), (timedelta(days=27), 4)])
def test_limit_and_weeks_are_shown_every_week(tmp_path, monkeypatch, ago, weeks):
    """上限を超えない週も、新しく無検閲の数・上限・週の数を result.json と PR の本文に出す
    (判定は変えない)。"""
    r = _run(tmp_path, monkeypatch, _iso(NOW - ago), 3)
    assert r["ok"] is True, r["problems"]
    assert r["uncensored"] == {"new": 3, "limit": 5 * weeks, "weeks": weeks}
    saved = json.loads((tmp_path / "out-3" / "result.json").read_text(encoding="utf-8"))
    assert saved["uncensored"] == r["uncensored"]
    body = (tmp_path / "out-3" / "pr_body.md").read_text(encoding="utf-8")
    assert f"新しく無検閲の印: 3 件(上限 {5 * weeks} 件 = 5 件 × {weeks} 週)" in body
    assert ("たまった分かを項目で確かめてください" in body) is (weeks >= 2)


def test_the_line_does_not_change_the_verdict(tmp_path, monkeypatch):
    """本文の行の有無で ok・problems・classification・warnings が変わらない。"""
    made = _iso(NOW - timedelta(days=27))
    a = _run(tmp_path / "a", monkeypatch, made, 21)
    monkeypatch.setattr(weekly, "uncensored_lines", lambda u: [])
    b = _run(tmp_path / "b", monkeypatch, made, 21)
    for k in ("ok", "problems", "classification", "warnings"):
        assert a[k] == b[k], k
    assert "新しく無検閲の印: 21 件" not in (tmp_path / "b" / "out-21" / "pr_body.md").read_text(
        encoding="utf-8"
    )


def test_old_result_without_the_key_does_not_break_the_body():
    """古い result(uncensored が無い)・形の違う値でも本文を作れる(行は出さない)。"""
    from genmodelfit.diff import diff_db

    old = frozen_db()
    new = copy.deepcopy(old)
    new.models.append(
        ModelEntry(id="hf:n/x", name="x", modality="image", source="huggingface", content="uncensored")
    )
    d = diff_db(old, new)
    for result in (
        {"status": {}},
        {"status": {}, "uncensored": None},
        {"status": {}, "uncensored": {"new": 1}},
    ):
        body = weekly.pr_body(d, result)
        assert "### 新しく無検閲の印が付いた項目" in body and "新しく無検閲の印: " not in body
    assert weekly.uncensored_lines({"new": 0, "limit": 5, "weeks": 1}) == [
        "新しく無検閲の印: 0 件(上限 5 件 = 5 件 × 1 週)",
        "",
    ]


@pytest.mark.parametrize("made", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_dates_out_of_range_are_unreadable(made):
    """UTC にすると年が 1〜9999 の外に出る日時は読めないもの(None)。上限は 1 週(落ちない)。"""
    from genmodelfit.db import parse_generated_at

    assert parse_generated_at(made) is None
    db = _base(made)
    assert db.generated_time is None
    assert weekly.uncensored_limit(db, NOW) == (5, 1)
