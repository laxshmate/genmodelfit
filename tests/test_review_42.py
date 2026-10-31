# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""確認の印(review)と、毎週の変化の分類(diff の classification)の試験(#42)。ネットワークには触らない。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import genmodelfit as gmf
from genmodelfit import cli, review, weekly
from genmodelfit.db import Database, DatabaseError, load, parse_db
from genmodelfit.diff import diff_db
from genmodelfit.gate import CHECK_IDS, GatePolicy, evaluate_offline
from genmodelfit.hf import TIER_PERMISSIVE, license_tier
from genmodelfit.models import ModelEntry, Review

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
URL = "https://huggingface.co/org/model/blob/main/LICENSE"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(scope="module")
def bundled() -> Database:
    return load(use_cache=False)


def _auto(db: Database) -> ModelEntry:
    p = GatePolicy(max_head_age_days=None)
    return next(m for m in db.models if evaluate_offline(m, p, sources=db.sources).auto)


def _sized(db: Database) -> ModelEntry:
    """大きさの分かる ComfyUI のモデルで、実測の無いもの(実測は大きさを変えても動かない)。"""
    measured = {m.model_id for m in db.measurements}
    return next(
        m
        for m in db.models
        if m.id not in measured
        and m.runtime in (None, "comfyui")
        and m.files
        and all(f.size_bytes for f in m.files)
        and m.files_complete
    )


def _restricted(db: Database) -> ModelEntry:
    return next(m for m in db.models if license_tier(m.license, m.license_name) > TIER_PERMISSIVE and m.files)


def _scale(m: ModelEntry, k: float) -> ModelEntry:
    m = copy.deepcopy(m)
    for f in m.files:
        f.size_bytes = int(f.size_bytes * k)
    return m


def _loosen(m: ModelEntry) -> ModelEntry:
    m = copy.deepcopy(m)
    m.license, m.license_name = "apache-2.0", None
    for f in m.files:
        f.license, f.license_name = "apache-2.0", None
    return m


# --- 形・後方互換 ------------------------------------------------------------------------------------


# 同梱の一覧の承認に使ってよい名前(review approve --by)。claude-maker-<番号> は別に正規表現で見る
APPROVERS = {review.DECIDED_BY_WEEKLY, "KoyoYeager"}


def test_bundled_list_starts_all_ok(bundled):
    assert bundled.models
    for m in bundled.models:
        assert m.review is not None and m.review.status == "ok", m.id
        if m.review.decided_by == review.BASELINE:
            assert m.review.decided_at == "2026-10-21"
            continue
        # あとから review approve で確かめたもの(#127 から)。誰が・いつ・何を根拠にしたかを確かめる。
        # claude-maker-<Issue の番号> はメンテナが PR で根拠を見てからマージする(docs/release.md)
        who = m.review.decided_by or ""
        assert who in APPROVERS or re.fullmatch(r"claude-maker-\d+", who), (m.id, who)
        assert review.check_evidence(m.review.evidence), m.id  # https でホストのある URL(1 つ以上)
        assert "2026-10-21" <= (m.review.decided_at or "") <= date.today().isoformat(), m.id
    assert bundled.sources["review"]["pending"] == 0


def test_old_list_without_review_reads_as_ok(bundled):
    """review の無い古い一覧も読め、確認済みとして扱う(アプリの絞り込み `review is None or status == ok`)。"""
    data = bundled.to_dict()
    for m in data["models"]:
        m.pop("review")
    old = parse_db(json.loads(json.dumps(data)), "path")
    assert all(m.review is None and m.reviewed for m in old.models)
    kept = [e for e in old.models if e.review is None or e.review.status == "ok"]
    assert len(kept) == len(old.models)
    r = evaluate_offline(old.models[0], sources=old.sources)
    check = next(c for c in r.checks if c.id == "reviewed")
    assert check.passed and "印が無い" in check.detail


def test_review_round_trips_and_type_checked(bundled):
    data = json.loads(json.dumps(bundled.to_dict()))
    data["models"][0]["review"] = {"status": "pending", "reasons": ["new"], "evidence": []}
    db = parse_db(data, "path")
    r = db.models[0].review
    assert r == Review("pending", ["new"]) and not db.models[0].reviewed
    data["models"][0]["review"] = {"status": "ok", "reasons": "new"}
    with pytest.raises(DatabaseError, match="reasons"):
        parse_db(data, "path")
    data["models"][0]["review"] = {"status": "rejected-later", "reasons": []}  # 知らない値は ok でない
    assert not parse_db(data, "path").models[0].reviewed


# --- update が付ける印 --------------------------------------------------------------------------------


def test_new_entries_are_pending(bundled):
    prev = copy.deepcopy(bundled.models[1:])
    new = copy.deepcopy(bundled.models[:3])
    new[1].content = "uncensored"
    stats = review.apply_reviews(new, prev)
    assert new[0].review.status == "pending" and new[0].review.reasons == ["new"]
    assert new[1].review.reasons == ["uncensored_new"]  # 前からある sfw → uncensored
    assert new[2].review.status == "ok"
    assert stats["pending"] >= 1 and stats["marked"]["new"] == 1


def test_uncensored_change_and_new_uncensored(bundled):
    a = copy.deepcopy(bundled.models[0])
    b = copy.deepcopy(a)
    b.content = "uncensored"
    assert review.review_reasons(a, b) == ["uncensored_new"]
    assert review.review_reasons(None, b) == ["new", "uncensored_new"]


def test_license_toward_permissive_is_pending(bundled):
    a = _restricted(bundled)
    b = _loosen(a)
    assert review.review_reasons(a, b) == ["license_permissive"]
    assert review.review_reasons(b, a) == []  # 厳しい側は安全な向き


def test_need_decrease_is_pending_and_increase_is_not(bundled):
    a = _sized(bundled)
    # 大きさが変われば files_changed も付く(必要量の比べ方とは別の理由)
    assert review.review_reasons(a, _scale(a, 0.5)) == ["need_decreased", "files_changed"]
    # 大きくなっても同じ URL の中身が差し替わったので確認待ち(0.9.0。diff の分類と同じ)
    assert review.review_reasons(a, _scale(a, 1.5)) == ["files_changed"]
    assert not review.need_decreased(a, _scale(a, 0.999))  # 1% 以内の揺れは必要量では拾わない
    head_only = copy.deepcopy(a)
    for f in head_only.files:
        f.head_status, f.head_checked_at = 404, "2026-10-01T00:00:00Z"
    assert not review.need_decreased(a, head_only)  # HEAD だけの変化は見積もらない


def _with(m: ModelEntry, **kw) -> ModelEntry:
    m = copy.deepcopy(m)
    for k, v in kw.items():
        setattr(m.files[0], k, v)
    return m


def test_files_changed_conditions(bundled):
    a = _with(_sized(bundled), sha256="a" * 64)
    fc = review.files_changed
    assert not fc(a, copy.deepcopy(a))
    assert fc(a, _with(a, url=a.files[0].url + "x"))  # url が変わった
    assert fc(a, _with(a, url=a.files[0].url + "?download=true"))  # クエリも比べる(0.9.0。diff と同じ)
    assert not fc(a, _with(a, url=a.files[0].url + "#frag"))  # フラグメントだけは同じ(サーバーに送られない)
    assert fc(a, _with(a, repo_id="someone/else"))  # repo_id が変わった(0.9.0)
    assert fc(a, _with(a, sha256="b" * 64))  # 前も今も値があって違う
    assert fc(a, _with(a, sha256=None))  # 確かめた値が消えた
    assert not fc(_with(a, sha256=None), a)  # 初めて確かめた
    assert fc(a, _with(a, size_bytes=a.files[0].size_bytes - 1))
    assert fc(a, _with(a, size_bytes=None)) and fc(_with(a, size_bytes=None), a)
    assert fc(a, _with(a, size_bytes=a.files[0].size_bytes + 1))  # 大きくなっただけでも付ける(0.9.0)
    gone = copy.deepcopy(a)
    gone.files = gone.files[:-1]
    assert fc(a, gone) and fc(gone, a)  # 減った・増えた
    head = _with(a, head_status=404, head_checked_at="2026-10-01T00:00:00Z")
    assert not fc(a, head)  # HEAD の結果だけでは付けない
    assert review.review_reasons(a, _with(a, sha256="b" * 64)) == ["files_changed"]
    other = copy.deepcopy(a)
    other.downloads = (other.downloads or 0) + 5
    other.name = "renamed"
    assert review.review_reasons(a, other) == []  # ファイル以外の変化では付けない


def test_files_changed_turns_ok_into_pending(bundled):
    a = _with(_sized(bundled), sha256="a" * 64)
    prev = copy.deepcopy(a)
    prev.review = Review("ok", [], "owner", "2026-10-14", [URL])
    cur = _with(a, sha256="b" * 64)
    stats = review.apply_reviews([cur], [prev])
    assert cur.review.status == "pending" and cur.review.reasons == ["files_changed"]
    assert stats["marked"] == {"files_changed": 1}


def test_carry_over_and_union_of_reasons(bundled):
    a = _sized(bundled)
    prev = copy.deepcopy(a)
    prev.review = Review("pending", ["new"], note="先週から")
    cur = _scale(a, 0.5)
    review.apply_reviews([cur], [prev])
    assert cur.review.status == "pending" and cur.review.reasons == ["new", "need_decreased", "files_changed"]
    assert cur.review.note == "先週から"
    same = copy.deepcopy(a)
    review.apply_reviews([same], [prev])
    assert same.review == prev.review and same.review is not prev.review
    ok_prev = copy.deepcopy(a)
    ok_prev.review = Review("ok", ["new"], "owner", "2026-10-14", [URL])
    same = copy.deepcopy(a)
    review.apply_reviews([same], [ok_prev])
    assert same.review == ok_prev.review
    legacy = copy.deepcopy(a)
    legacy.review = None
    same = copy.deepcopy(a)
    review.apply_reviews([same], [legacy])
    assert same.review.status == "ok" and same.review.note == review.LEGACY_NOTE


def test_no_previous_list_means_all_new(bundled):
    es = copy.deepcopy(bundled.models[:2])
    review.apply_reviews(es, None)
    assert all(e.review.reasons[0] == "new" and not e.reviewed for e in es)


def test_rejected_ids_are_dropped(bundled):
    es = copy.deepcopy(bundled.models[:3])
    rej = review.parse_rejected(
        f"# 注釈\n{es[0].id}\t2026-10-21\tnon-commercial\n{review.id_hash(es[1].id)}\t2026-10-21\tsafety\n\n"
    )
    kept, n = review.drop_rejected(es, rej)
    assert [e.id for e in kept] == [es[2].id] and n == 2
    assert review.bundled_rejected() == {}  # 同梱の記録は注釈だけで始める


def test_partial_refresh_marks_new_manual_models(tmp_path, bundled, monkeypatch):
    """--heads-only などの一部のやり直しでも、増えた項目(手のモデルなど)は pending になる。"""
    from genmodelfit import update

    db = copy.deepcopy(bundled)
    before = copy.deepcopy(db.models[1:])
    update._finish_reviews(db, before)
    assert db.models[0].review.reasons == ["new"] and db.sources["review"]["pending"] == 1


# --- gate ------------------------------------------------------------------------------------------


def test_gate_holds_pending(bundled):
    m = copy.deepcopy(_auto(bundled))
    p = GatePolicy(max_head_age_days=None)
    assert evaluate_offline(m, p, sources=bundled.sources).auto
    m.review = Review("pending", ["new"])
    r = evaluate_offline(m, p, sources=bundled.sources)
    assert not r.auto and r.held_by == ["reviewed"]
    assert "new" in r.checks[-1].detail
    assert CHECK_IDS[-1] == "reviewed"


# --- CLI と Python ---------------------------------------------------------------------------------


@pytest.fixture
def dbfile(tmp_path, bundled):
    db = copy.deepcopy(bundled)
    db.models[0].review = Review("pending", ["new"])
    db.models[1].review = Review("pending", ["license_permissive"])
    p = tmp_path / "data" / "db.json"
    db.save(p)
    return p, db.models[0].id, db.models[1].id, db.models[2].id


def test_review_list_json(dbfile, capsys):
    p, a, b, _ = dbfile
    assert cli.main(["review", "list", "--json", "--db", str(p)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["command"] == "review" and d["pending"] == 2
    assert [m["id"] for m in d["models"]] == [a, b]
    assert d["models"][0]["reasons"] == ["new"] and d["models"][0]["status"] == "pending"
    assert cli.main(["review", "list", "--db", str(p)]) == 0
    assert "確認待ち 2 件" in capsys.readouterr().out


def test_approve_needs_https_evidence(dbfile, capsys):
    p, a, _, c = dbfile
    before = p.read_bytes()
    assert cli.main(["review", "approve", a, "--db", str(p)]) == 1
    assert "根拠" in capsys.readouterr().err
    assert cli.main(["review", "approve", a, "--db", str(p), "--evidence", "http://x.example/"]) == 1
    assert cli.main(["review", "approve", c, "--db", str(p), "--evidence", URL]) == 1  # 確認待ちでない
    capsys.readouterr()
    assert cli.main(["review", "approve", "nope", "--db", str(p), "--evidence", URL, "--json"]) == 1
    assert "一覧に無い" in json.loads(capsys.readouterr().out)["error"]
    assert p.read_bytes() == before
    with pytest.raises(SystemExit):  # --db は必須(書き換える先を明示する)
        cli.main(["review", "approve", a, "--evidence", URL])


def test_approve_writes_in_place(dbfile, capsys):
    p, a, _, _ = dbfile
    args = ["review", "approve", a, "--db", str(p), "--evidence", URL, "--evidence", URL + "2"]
    assert cli.main([*args, "--note", "原文を確かめた", "--by", "owner", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["review"]["status"] == "ok" and out["review"]["evidence"] == [URL, URL + "2"]
    m = load(p).get(a)
    assert m.review.status == "ok" and m.review.reasons == ["new"] and m.review.decided_by == "owner"
    assert m.review.note == "原文を確かめた" and len(m.review.decided_at) == 10
    assert load(p).sources["review"]["pending"] == 1


def test_reject_removes_and_records(dbfile, capsys):
    p, a, b, _ = dbfile
    with pytest.raises(SystemExit):  # 理由(--note)は必須
        cli.main(["review", "reject", a, "--db", str(p)])
    assert cli.main(["review", "reject", a, "--db", str(p), "--note", "非商用だけ"]) == 0
    rej = p.parent / review.REJECTED_FILE
    text = rej.read_text(encoding="utf-8")
    assert text.startswith("#") and f"\n{a}\t" in text and "非商用だけ" in text
    assert load(p).get(a) is None
    assert cli.main(["review", "reject", b, "--db", str(p), "--note", "safety", "--hide-name", "--json"]) == 0
    capsys.readouterr()
    text = rej.read_text(encoding="utf-8")
    assert b not in text and "sha256:" + hashlib.sha256(b.encode()).hexdigest() in text
    parsed = review.parse_rejected(text)
    assert review.is_rejected(a, parsed) and review.is_rejected(b, parsed)


def test_python_api_prints_nothing(dbfile, capfd):
    p, a, b, _ = dbfile
    db = load(p)
    assert [x["id"] for x in gmf.review_list(db)] == [a, b]
    m = gmf.review_approve(str(p), a, [URL], note="ok")
    assert m.reviewed
    with pytest.raises(ValueError, match="根拠"):
        gmf.review_approve(str(p), b, [])
    res = gmf.review_reject(str(p), b, "safety", hide_name=True)
    assert res["key"].startswith("sha256:")
    out, err = capfd.readouterr()
    assert out == "" and err == ""


# --- diff の分類 -------------------------------------------------------------------------------------


def _cl(old, new):
    return diff_db(old, new)["classification"]


def test_classification_safe_changes(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    assert _cl(old, new) == {
        "safe": True,
        "judgement_needed": [],
        "safe_changes": _cl(old, new)["safe_changes"],
    }
    new.models.pop(0)  # 消えた
    r = _restricted(new)
    j = new.models.index(r)
    stricter = copy.deepcopy(r)
    stricter.license, stricter.license_name = "cc-by-nc-4.0", None  # 厳しい側
    new.models[j] = stricter
    for f in new.models[-1].files:  # HEAD の失敗
        f.head_status = 404
    new.sources = {**new.sources, "counts": {"models": 1}}  # sources の数
    c = _cl(old, new)
    assert c["safe"] is True and c["judgement_needed"] == [], c["judgement_needed"]
    sc = c["safe_changes"]
    assert sc["removed"] == 1 and sc["license_stricter"] >= 1 and sc["stats"] >= 1


def test_classification_judgement_needed(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    gone = old.models.pop(0)  # new には有る = 増えた
    a = _sized(old)
    new.models[new.models.index(new.get(a.id))] = _scale(a, 0.5)
    r = _restricted(old)
    new.models[new.models.index(new.get(r.id))] = _loosen(r)
    u = next(m for m in new.models if m.content == "sfw" and m.id not in (a.id, r.id, gone.id))
    u.content = "uncensored"
    c = _cl(old, new)
    got = {j["id"]: j["reasons"] for j in c["judgement_needed"]}
    assert c["safe"] is False
    assert got[gone.id][0] == "new"
    assert "need_decreased" in got[a.id] and "license_permissive" in got[r.id]
    assert got[u.id] == ["uncensored_new"]
    assert got[a.id][:2] == ["need_decreased", "files_changed"]  # 小さくなった: 両方


def test_classification_file_growth_needs_judgement(bundled):
    """同じ URL のファイルが大きくなった = 中身が差し替わった。安全な週にしない(0.9.0。前は安全)。"""
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    a = _sized(old)
    new.models[new.models.index(new.get(a.id))] = _scale(a, 1.5)
    c = _cl(old, new)
    assert c["safe"] is False
    assert {j["id"]: j["reasons"] for j in c["judgement_needed"]} == {a.id: ["files_changed"]}


def test_classification_review_transitions(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    old.models[0].review = Review("pending", ["new"])
    new.models[0].review = Review("ok", ["new"], "claude", "2026-10-21", [URL])
    old.models[1].review = Review("pending", ["new"])
    new.models[1].review = Review("pending", ["new"])  # 前から確認待ち(安全)
    new.models[2].review = Review("pending", ["need_decreased"])
    c = _cl(old, new)
    got = {j["id"]: j["reasons"] for j in c["judgement_needed"]}
    assert got == {
        new.models[0].id: ["review_approved"],
        new.models[2].id: ["need_decreased", "review_pending"],
    }
    assert c["safe_changes"]["pending_carried"] == 1


def test_auto_gained_by_head_recovery_is_safe(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    m = _auto(old)
    for f in m.files:
        f.head_status = 503
    c = _cl(old, new)
    assert c["safe"] is True and c["safe_changes"]["auto_gained_head_only"] == 1


# --- weekly ----------------------------------------------------------------------------------------


def test_weekly_writes_review_queue(tmp_path, monkeypatch, bundled):
    stamp = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def fake(fetch, out_path, *, allow_partial, progress, previous):
        db = copy.deepcopy(previous)
        for m in db.models:
            for f in m.files:
                if f.head_checked_at:
                    f.head_checked_at = stamp
        db.generated_at = stamp
        db.models[0].review = Review("pending", ["new"])
        db.models[0].name = "evil <!-- @someone [x](y) |"
        db.save(out_path)
        return db

    monkeypatch.setattr(weekly, "update_database", fake)
    monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    assert r["ok"], r["problems"]
    q = json.loads((tmp_path / "out" / "review_queue.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in q["pending"]] == [bundled.models[0].id]
    assert q["classification"]["safe"] is False
    assert r["classification"] == {"safe": False, "judgement_needed": 1}
    assert r["files"]["review_queue"].endswith("review_queue.json")
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "### 確認待ち(review: pending)の項目: 1 件" in body and "判断が要る変化" in body
    assert "<!--" not in body and "@someone" not in body  # md() で無害にする


def test_weekly_safe_week_says_so(tmp_path, monkeypatch, bundled):
    stamp = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def fake(fetch, out_path, *, allow_partial, progress, previous):
        db = copy.deepcopy(previous)
        for m in db.models:
            for f in m.files:
                if f.head_checked_at:
                    f.head_checked_at = stamp
        db.generated_at = stamp
        db.save(out_path)
        return db

    monkeypatch.setattr(weekly, "update_database", fake)
    monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    assert r["ok"] and r["classification"]["safe"] is True
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "安全な向きの変化だけ" in body and "の項目: 0 件" in body


def test_update_from_fixtures_marks_against_previous(tmp_path):
    """本物の作り直し(記録の応答): 前の一覧が無ければ全部 new、同じ一覧を前にすれば確認待ちは増えない。"""
    from genmodelfit.fetch import DirFetcher
    from genmodelfit.update import build_database

    fix = Path(__file__).parent / "fixtures" / "update"
    first = build_database(DirFetcher(fix), head=False, carry_over=False)
    assert first.models and all(m.review.reasons[0] == "new" for m in first.models)
    assert first.sources["review"]["pending"] == len(first.models)
    for m in first.models:
        m.review = Review("ok", [], "test", "2026-10-21")
    again = build_database(DirFetcher(fix), head=False, previous=first)
    assert again.sources["review"]["pending"] == 0, [m.id for m in again.models if not m.reviewed]


# --- Checker の指摘(許すものの表・根拠のホスト・配る一覧・merge-check) -------------------------------


def _pair(bundled):
    return copy.deepcopy(bundled), copy.deepcopy(bundled)


def _reasons_for(old, new, mid):
    got = {j["id"]: j["reasons"] for j in _cl(old, new)["judgement_needed"]}
    return got.get(mid)


def test_url_and_sha_swap_to_another_repo_is_judgement(bundled):
    old, new = _pair(bundled)
    m = new.get(_auto(old).id)
    for f in m.files:  # 大きさは同じのまま、別のリポジトリのファイルに差し替える
        f.url = (f.url or "").replace("huggingface.co/", "huggingface.co/evil-") + "?x"
        f.sha256 = "f" * 64
        f.repo_id = "evil/" + (f.repo_id or "x")
    assert "files_changed" in _reasons_for(old, new, m.id)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda m: m.tasks.append("zzz"), "use_changed"),
        (lambda m: setattr(m, "modality", "video" if m.modality != "video" else "image"), "use_changed"),
        (
            lambda m: m.languages.update({"prompt": [*m.languages.get("prompt", []), "ja"]}),
            "other_change:languages",
        ),
        (lambda m: m.description.update({"ja": "説明を変えた"}), "other_change:description"),
        (lambda m: setattr(m, "license_url", "https://example.com/other"), "other_change:license_url"),
        (lambda m: setattr(m, "name", m.name + " v2"), "other_change:name"),
        (lambda m: setattr(m.review, "status", "OK"), "review_pending"),
    ],
)
def test_field_changes_outside_the_allowlist_are_judgement(bundled, change, reason):
    old, new = _pair(bundled)
    m = next(x for x in new.models if "ja" not in x.languages.get("prompt", []))
    change(m)
    assert reason in (_reasons_for(old, new, m.id) or []), _cl(old, new)["judgement_needed"][:3]


def test_pure_head_and_popularity_changes_are_safe(bundled):
    old, new = _pair(bundled)
    for m in new.models:
        m.downloads = (m.downloads or 0) + 7
        m.likes = (m.likes or 0) + 1
        m.downloads_all_time = (m.downloads_all_time or 0) + 9
        m.updated = "2026-10-28T00:00:00Z"
        if m.template:
            m.template.usage = (m.template.usage or 0) + 3
        for f in m.files:
            f.head_checked_at = "2026-10-28T00:00:00Z"
    c = _cl(old, new)
    assert c["safe"] is True, c["judgement_needed"][:3]


def test_head_recovery_is_safe_only_with_the_same_files(bundled):
    old, new = _pair(bundled)
    mid = _auto(old).id
    for f in old.get(mid).files:
        f.head_status = 503
    assert _cl(old, new)["safe"] is True
    for f in new.get(mid).files:
        f.sha256 = "e" * 64
    assert "auto_gained" in _reasons_for(old, new, mid)


def test_unknown_keys_count_in_merge_check(tmp_path, bundled, capsys):
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    bundled.save(base)
    data = json.loads(base.read_text(encoding="utf-8"))
    for f in data["models"][0]["files"]:
        f["head_checked_at"] = "2026-10-28T00:00:00Z"
    head.write_text(json.dumps(data), encoding="utf-8")
    assert weekly.main(["merge-check", "--base", str(base), "--head", str(head), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["safe"] is True and r["reasons"] == []
    data["models"][1]["future_field"] = {"x": 1}  # この版が知らない鍵
    data["schema_extra"] = 1
    head.write_text(json.dumps(data), encoding="utf-8")
    assert (
        weekly.main(["merge-check", "--base", str(base), "--head", str(head), "--json"])
        == weekly.MERGE_NOT_SAFE
    )
    r = json.loads(capsys.readouterr().out)
    got = {j["id"]: j["reasons"] for j in r["reasons"]}
    assert r["safe"] is False and got[data["models"][1]["id"]] == ["other_change:future_field"]
    assert got["(database)"] == ["other_change:schema_extra"]
    assert weekly.main(["merge-check", "--base", str(base), "--head", str(tmp_path / "nope.json")]) == 1
    capsys.readouterr()


@pytest.mark.parametrize(
    "bad", ["https://", "https:///x", "https:// x", "http://example.com", "ftp://x.example/"]
)
def test_evidence_needs_a_host(bad):
    with pytest.raises(review.ReviewError):
        review.check_evidence([bad])
    assert review.check_evidence(["https://huggingface.co/x"]) == ["https://huggingface.co/x"]


def test_published_list_has_no_pending(tmp_path, bundled):
    """0.5.x は review を知らないので、配る一覧(data-latest)には確認待ちを入れない。元の一覧は変えない。"""
    db = copy.deepcopy(bundled)
    db.models[0].review = Review("pending", ["new"])
    out = review.publishable(db)
    assert db.models[0].review.status == "pending" and len(db.models) == len(bundled.models)
    assert len(out.models) == len(db.models) - 1 and all(m.reviewed for m in out.models)
    assert out.sources["review"]["withheld_pending"] == 1 and out.sources["counts"]["models"] == len(
        out.models
    )
    src, dst = tmp_path / "src.json", tmp_path / "dst.json"
    db.save(src)
    assert review.main(["publish", str(src), str(dst)]) == 0
    pub = json.loads(dst.read_text(encoding="utf-8"))
    assert db.models[0].id not in {m["id"] for m in pub["models"]}
    # 0.5.x の読み方(知らない鍵 review を捨てる)でも確認待ちが出てこない
    assert all(m.get("review", {}).get("status") == "ok" for m in pub["models"])


# --- 同じ ID・判断の一覧を当てる(apply)・確認待ちの一覧の中身 -----------------------------------------


def test_duplicate_ids_are_refused(tmp_path, bundled, capsys):
    """Checker の作った例: 自動で出せる項目を写し、URL・sha256 を差し替えて元の前に置く。"""
    base, head = tmp_path / "base.json", tmp_path / "head.json"
    bundled.save(base)
    data = json.loads(base.read_text(encoding="utf-8"))
    orig_id = _auto(bundled).id
    k = next(i for i, m in enumerate(data["models"]) if m["id"] == orig_id)
    evil = copy.deepcopy(data["models"][k])
    for f in evil["files"]:
        f["url"] = "https://huggingface.co/evil/repo/resolve/main/" + f["name"]
        f["sha256"] = "d" * 64
    data["models"].insert(k, evil)
    head.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatabaseError, match="同じモデル ID"):
        parse_db(json.loads(json.dumps(data)), "path")
    with pytest.raises(DatabaseError, match="同じモデル ID"):
        load(head)
    code = weekly.main(["merge-check", "--base", str(base), "--head", str(head), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and "safe" not in out and "同じモデル ID" in out["error"]
    db = copy.deepcopy(bundled)
    db.models.insert(0, copy.deepcopy(db.models[3]))
    with pytest.raises(DatabaseError):
        db.save(tmp_path / "dup.json")


def test_files_changed_is_listed_once(bundled):
    old, new = _pair(bundled)
    m = next(x for x in new.models if len(x.files) >= 2)
    for f in m.files:
        f.sha256 = "c" * 64
        f.url = (f.url or "") + "?v=2"
    assert _reasons_for(old, new, m.id).count("files_changed") == 1


@pytest.fixture
def pending_db(tmp_path, bundled):
    db = copy.deepcopy(bundled)
    for m in db.models[:4]:
        m.review = Review("pending", ["new"])
    p = tmp_path / "data" / "db.json"
    db.save(p)
    return p, [m.id for m in db.models[:4]]


def _apply(tmp_path, dbp, decisions, *extra):
    f = tmp_path / "decisions.json"
    f.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    return review.main(["apply", str(f), "--db", str(dbp), "--json", *extra])


def test_apply_decisions(tmp_path, pending_db, capsys):
    p, ids = pending_db
    evil_note = "<!-- @owner [x](javascript:alert(1)) | <img src=x> `code`"
    decisions = {
        "decisions": [
            {"id": ids[0], "action": "approve", "evidence": [URL], "note": evil_note},
            {"id": ids[1], "action": "reject", "note": "非商用だけ", "evidence": [], "safety": False},
            {"id": ids[2], "action": "reject", "note": "安全上の理由", "safety": True},
            {"id": ids[3], "action": "hold"},
        ]
    }
    assert _apply(tmp_path, p, decisions) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] and out["counts"] == {"approve": 1, "reject": 2, "hold": 1}
    assert out["pending_left"] == 1
    text = json.dumps(out, ensure_ascii=False)
    assert "<!--" not in text and "<img" not in text and "@owner" not in text and "[x](" not in text
    assert ids[2] not in text  # 安全上の理由で外したものは名前を出さない
    db = load(p)
    m = db.get(ids[0])
    assert m.review.status == "ok" and m.review.decided_by == review.DECIDED_BY_WEEKLY
    assert m.review.note == evil_note and m.review.evidence == [URL]  # 一覧にはそのまま(データ)
    assert db.get(ids[1]) is None and db.get(ids[2]) is None and not db.get(ids[3]).reviewed
    rej = (p.parent / review.REJECTED_FILE).read_text(encoding="utf-8")
    assert f"{ids[1]}\t" in rej and ids[2] not in rej and review.id_hash(ids[2]) in rej


CONTROL = [chr(10), chr(13), chr(0), chr(0x1B), chr(0x202E), chr(0x200B)]


@pytest.mark.parametrize(
    "bad",
    [
        {"decisions": [{"id": "X", "action": "approve", "evidence": [URL]}], "extra": 1},  # 知らない鍵(外)
        {"decisions": [{"id": "X", "action": "approve", "evidence": [URL], "by": "me"}]},  # 知らない鍵
        {"decisions": [{"id": "X", "action": "merge"}]},  # 知らない action
        {"decisions": [{"id": "X", "action": "approve", "evidence": []}]},  # 根拠なし
        {"decisions": [{"id": "X", "action": "approve", "evidence": ["https://"]}]},
        {"decisions": [{"id": "X", "action": "approve", "evidence": ["javascript:alert(1)"]}]},
        {"decisions": [{"id": "X", "action": "approve", "evidence": ["http://example.com/"]}]},
        {"decisions": [{"id": "X", "action": "approve", "evidence": URL}]},  # 並びでない
        {"decisions": [{"id": "X", "action": "approve", "evidence": [URL], "safety": True}]},
        {"decisions": [{"id": "X", "action": "reject"}]},  # 理由なし
        {"decisions": [{"id": "X", "action": "reject", "note": "x", "safety": "yes"}]},
        {"decisions": [{"id": "X", "action": "hold", "note": "a" * 501}]},
        *[{"decisions": [{"id": "X", "action": "hold", "note": f"a{c}b"}]} for c in CONTROL],
        {"decisions": [{"id": "X", "action": "hold"}, {"id": "X", "action": "hold"}]},  # 同じ id
        {"decisions": [{"id": "NOT_PENDING", "action": "hold"}]},
        {
            "decisions": [
                {"id": "X", "action": "hold"},
                {"id": "nope", "action": "approve", "evidence": [URL]},
            ]
        },
        {"decisions": {"id": "X"}},
        [],
    ],
)
def test_apply_refuses_bad_decisions_and_changes_nothing(tmp_path, pending_db, bundled, capsys, bad):
    p, ids = pending_db
    text = (
        json.dumps(bad)
        .replace('"X"', json.dumps(ids[0]))
        .replace('"NOT_PENDING"', json.dumps(bundled.models[9].id))
    )
    bad = json.loads(text)
    before = p.read_bytes()
    first_ok = {"id": ids[1], "action": "approve", "evidence": [URL]}  # 正しい判断があっても全部当てない
    if isinstance(bad, dict) and isinstance(bad.get("decisions"), list) and "extra" not in bad:
        bad["decisions"].insert(0, first_ok)
    assert _apply(tmp_path, p, bad) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["error"]
    assert p.read_bytes() == before
    assert not (p.parent / review.REJECTED_FILE).exists()


def test_apply_max_approve_and_unreadable(tmp_path, pending_db, capsys):
    p, ids = pending_db
    before = p.read_bytes()
    many = {"decisions": [{"id": i, "action": "approve", "evidence": [URL]} for i in ids[:3]]}
    assert _apply(tmp_path, p, many, "--max-approve", "2") == 2
    assert p.read_bytes() == before
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert review.main(["apply", str(tmp_path / "broken.json"), "--db", str(p)]) == 2
    good = {"decisions": [{"id": ids[0], "action": "hold"}]}
    assert _apply(tmp_path, tmp_path / "missing.json", good) == 1
    capsys.readouterr()


def test_queue_items_have_what_the_reviewer_needs(bundled):
    db = copy.deepcopy(bundled)
    hf_entry = next(m for m in db.models if m.id.startswith("hf:"))
    tmpl = next(m for m in db.models if m.source == "comfyui-template" and m.files)
    for m in (hf_entry, tmpl):
        m.review = Review("pending", ["new"])
    q = review.queue(db, {"safe": False})
    items = {i["id"]: i for i in q["pending"]}
    for m in (hf_entry, tmpl):
        it = items[m.id]
        assert {"id", "name", "modality", "reasons", "files", "homepage", "card_url"} <= set(it)
        assert it["card_url"] and it["card_url"].startswith("https://")
        f = it["files"][0]
        assert {"repo", "path", "license", "license_url"} <= set(f)
    assert items[hf_entry.id]["card_url"] == "https://huggingface.co/" + hf_entry.id[3:]


def test_python_m_review_has_no_runpy_warning():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "genmodelfit.review", "apply", "--help"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0 and b"RuntimeWarning" not in r.stderr


# --- 改行は LF・カードの言語は読めない回に揺れない(毎週の実行の試しで見つかったもの) -----------------------


def test_writers_use_lf_and_bundled_round_trips(tmp_path, bundled):
    from importlib import resources

    src = Path(str(resources.files("genmodelfit.data").joinpath("db.json")))
    raw = src.read_bytes()
    assert b"\r\n" not in raw
    db = parse_db(json.loads(raw.decode("utf-8")), "path")
    out = tmp_path / "db.json"
    db.save(out)
    assert out.read_bytes() == raw  # 同じ形(indent=1・ensure_ascii=False・末尾の改行なし)で LF
    data = load(out)
    data.models[0].review = Review("pending", ["new"])
    data.save(out)
    assert b"\r\n" not in out.read_bytes()
    assert review.approve_file(out, data.models[0].id, [URL]).reviewed
    assert b"\r\n" not in out.read_bytes()
    review.reject_file(out, data.models[1].id, "理由")
    assert b"\r\n" not in out.read_bytes()
    assert b"\r\n" not in (tmp_path / review.REJECTED_FILE).read_bytes()
    assert review.main(["publish", str(out), str(tmp_path / "pub.json")]) == 0
    assert b"\r\n" not in (tmp_path / "pub.json").read_bytes()
    weekly._write_json(tmp_path / "w.json", {"a": [1, 2]})
    assert b"\r\n" not in (tmp_path / "w.json").read_bytes()


class _Cards:
    """cached_repo だけの偽の HfClient。None は読めなかった(401・失敗)。"""

    def __init__(self, cards):
        self.cards = cards

    def cached_repo(self, repo):
        langs = self.cards.get(repo)
        if langs is None:
            return None

        from types import SimpleNamespace

        return SimpleNamespace(card={"language": langs})


def test_card_languages_do_not_flap_when_a_card_is_unreadable(bundled):
    from genmodelfit import appinfo

    m = copy.deepcopy(bundled.get("video_wan_vace_inpainting"))
    repos = appinfo.card_repos(m)
    assert len(repos) >= 3
    every = {r: [] for r in repos}
    every[repos[1]] = ["en"]
    every[repos[-1]] = ["en", "zh"]
    assert appinfo._card_for_entry(m, _Cards(every)) == ["en"]
    gated = dict(every)
    gated[repos[1]] = None  # gated のカードが読めない回
    assert appinfo._card_for_entry(m, _Cards(gated)) is None  # 次のカードの zh を使わない
    assert appinfo._card_for_entry(m, _Cards({r: [] for r in repos})) == []
    no_repo = copy.deepcopy(m)
    no_repo.files = []
    assert appinfo._card_for_entry(no_repo, _Cards({})) is None  # 読むカードが無い
    m.card_languages = ["en"]
    appinfo.apply_app_fields([m], _Cards(gated))
    assert m.card_languages == ["en"] and m.languages.get("prompt") == ["en"]


def test_rebuild_carries_card_languages_from_the_previous_list(bundled):
    from genmodelfit import appinfo

    prev = [copy.deepcopy(bundled.get("video_wan_dancer"))]
    assert "zh" in prev[0].card_languages
    fresh = copy.deepcopy(prev[0])
    fresh.card_languages, fresh.languages = None, {}
    assert appinfo.carry_card_languages([fresh], prev) == 1
    appinfo.apply_app_fields([fresh], _Cards({}))  # どのカードも読めない
    assert fresh.card_languages == prev[0].card_languages and fresh.languages == prev[0].languages
    read = copy.deepcopy(fresh)
    appinfo.apply_app_fields([read], _Cards({r: ["en"] for r in appinfo.card_repos(read)}))
    assert read.card_languages == ["en"]  # 読めたら作り直す


def test_discovered_from_change_is_safe():
    """どの検索で見つかったかの記録が変わるだけなら安全。

    2026-10-21 の初回の毎週の実行で csm-1b が当たった。"""
    import copy

    from genmodelfit.db import load
    from genmodelfit.diff import diff_db

    old = load(use_cache=False)
    new = copy.deepcopy(old)
    hf = next(m for m in new.models if m.source == "huggingface")
    hf.discovered_from = [*hf.discovered_from, "https://huggingface.co/api/models?search=other-query"]
    assert diff_db(old, new)["classification"]["safe"] is True


def test_files_changed_agrees_with_diff(bundled):
    """review の files_changed と diff の分類の files_changed は、1 つのファイルの変化で食い違わない
    (URL のクエリ・フラグメント・repo_id・大きさの両方の向き・sha256・名前・置き場所)。
    違うのは、決めて分けた 2 つだけ: 初めて sha256 が入った(review は付けない)・並びだけの変化。"""
    from dataclasses import asdict

    from genmodelfit.diff import field_change_reasons

    a = _with(_sized(bundled), sha256="a" * 64, repo_id="org/repo")
    f0 = a.files[0]
    cases = {
        "url": dict(url=f0.url + "x"),
        "query": dict(url=f0.url + "?download=true"),
        "fragment": dict(url=f0.url + "#frag"),
        "repo_id": dict(repo_id="org/other"),
        "grow": dict(size_bytes=f0.size_bytes + 1),
        "shrink": dict(size_bytes=f0.size_bytes - 1),
        "size_unknown": dict(size_bytes=None),
        "sha": dict(sha256="b" * 64),
        "sha_gone": dict(sha256=None),
        "head": dict(head_status=404),
    }
    for label, kw in cases.items():
        b = _with(a, **kw)
        by_review = review.files_changed(a, b)
        by_diff = "files_changed" in field_change_reasons(asdict(a), asdict(b))
        assert by_review == by_diff, (label, by_review, by_diff)
    # 大きくなっただけ: diff の分類だけでも判断が要る(review を通さない経路。変異 M15)
    assert field_change_reasons(asdict(a), asdict(_with(a, size_bytes=f0.size_bytes + 1))) == [
        "files_changed"
    ]
