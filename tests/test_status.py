"""一覧の新しさ(gmf.data_status、`system` の Data の欄)の試験(Issue #18)。

期限の判定が gate の url_reachable と同じであること(境界ちょうど・1 秒後)、出どころの見分け、
CLI の表示と JSON を確かめる。時刻はすべて固定する。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

import genmodelfit as gmf
from genmodelfit import cli, remote
from genmodelfit.db import load as load_db
from genmodelfit.gate import GatePolicy, evaluate_offline
from genmodelfit.status import DataStatus, compute


def _bundled_checked() -> datetime:
    """同梱の一覧の HEAD の確認日時(一番古いもの)。毎週の更新(#19)で変わるので、一覧から読む。"""
    db = load_db(use_cache=False)
    times = [f.head_checked_at for m in db.models for f in m.files if f.head_checked_at]
    return datetime.fromisoformat(min(times).replace("Z", "+00:00"))


CHECKED = _bundled_checked()


def _z(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _auto(db, m, now) -> bool:
    return evaluate_offline(m, GatePolicy(), sources=db.sources, now=now).auto


@pytest.fixture
def bundled():
    return load_db(use_cache=False)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(d))
    return d


def test_bundled_is_fresh_today(bundled):
    st = gmf.data_status(bundled, now=CHECKED + timedelta(hours=1))
    assert isinstance(st, DataStatus)
    assert st.source == "bundled"
    assert st.expired is False
    assert st.auto_count == st.auto_count_if_fresh > 0
    assert st.models == len(bundled.models)
    assert st.max_head_age_days == 30
    assert st.heads_checked_oldest == _z(CHECKED)
    assert st.expires_at == _z(CHECKED + timedelta(days=30))
    assert st.generated_at == bundled.generated_at


def test_bundled_expires_after_30_days(bundled):
    later = (CHECKED + timedelta(days=42)).replace(hour=0, minute=0, second=0, microsecond=0)
    st = gmf.data_status(bundled, now=later)
    assert st.expired is True
    assert st.auto_count == 0
    assert st.auto_count_if_fresh > 0
    assert st.expires_at == _z(CHECKED + timedelta(days=30))
    assert st.checked_at == _z(later)


@pytest.mark.parametrize(("delta", "expired"), [(timedelta(0), False), (timedelta(seconds=1), True)])
def test_boundary_matches_gate(bundled, delta, expired):
    """expires_at ちょうどはまだ有効、1 秒後に切れる。gate の url_reachable と同じ答え。"""
    now = CHECKED + timedelta(days=30) + delta
    st = gmf.data_status(bundled, now=now)
    assert st.expired is expired
    auto = sum(_auto(bundled, m, now) for m in bundled.models)
    assert st.auto_count == auto


def test_partial_expiry_counts_as_expired(bundled):
    """一部の項目の確認だけが古くても expired(自動のおすすめが減っている)。"""
    now = datetime(2026, 10, 20, tzinfo=UTC)
    fresh = compute(bundled, now=now)
    auto_ids = [m.id for m in bundled.models if _auto(bundled, m, now)]
    target = bundled.get(auto_ids[0])
    target.files[0].head_checked_at = "2026-08-01T00:00:00Z"
    st = compute(bundled, now=now)
    assert st.expired is True
    assert st.auto_count == fresh.auto_count - 1
    assert st.auto_count_if_fresh == fresh.auto_count_if_fresh
    assert st.heads_checked_oldest == "2026-08-01T00:00:00Z"
    assert st.expires_at == "2026-08-31T00:00:00Z"


def test_no_age_limit_never_expires(bundled):
    st = compute(bundled, GatePolicy(max_head_age_days=None), now=datetime(2030, 1, 1, tzinfo=UTC))
    assert st.expired is False and st.expires_at is None and st.max_head_age_days is None
    assert st.auto_count == st.auto_count_if_fresh


def test_naive_now_is_utc(bundled):
    naive = (CHECKED + timedelta(days=42)).replace(hour=0, minute=0, second=0, tzinfo=None)
    st = gmf.data_status(bundled, now=naive)
    assert st.expired is True and st.checked_at == _z(naive.replace(tzinfo=UTC))


def test_default_clock_is_gate_clock(bundled):
    """now を渡さなければ gate と同じ時計(試験では conftest の SNAPSHOT_NOW)。"""
    st = gmf.data_status(bundled)
    assert st.checked_at == "2026-10-21T00:00:00Z" and st.expired is False


def test_to_dict_is_json(bundled):
    d = gmf.data_status(bundled, now=datetime(2026, 10, 20, tzinfo=UTC)).to_dict()
    assert set(d) == {
        "source",
        "generated_at",
        "heads_checked_oldest",
        "heads_checked_newest",
        "max_head_age_days",
        "expired",
        "expires_at",
        "models",
        "auto_count",
        "auto_count_if_fresh",
        "checked_at",
    }
    json.dumps(d)


def test_source_cache_and_release(cache, bundled):
    cache.mkdir(parents=True)
    bundled.save(cache / "db.json")
    db = load_db()
    assert db.origin == "cache"
    assert gmf.data_status(db).source == "cache"
    # update --from-release が残す記録の日時と一覧の日時が同じなら release(表記の違い Z / +00:00 は同じ)
    gen = remote.parse_time(bundled.generated_at)
    (cache / remote.STATE_NAME).write_text(json.dumps({"generated_at": gen.isoformat()}), encoding="utf-8")
    assert gmf.data_status(load_db()).source == "release"
    # 後で手元の update が書き換えた(日時が違う)なら cache
    (cache / remote.STATE_NAME).write_text(
        json.dumps({"generated_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8"
    )
    assert gmf.data_status(load_db()).source == "cache"


def test_source_path(tmp_path, bundled):
    p = tmp_path / "db.json"
    bundled.save(p)
    assert gmf.data_status(load_db(p)).source == "path"


def test_comfyui_version_changes_candidates(bundled):
    now = datetime(2026, 10, 20, tzinfo=UTC)
    old = gmf.data_status(bundled, now=now, comfyui_version="0.1.0")
    assert old.auto_count_if_fresh < gmf.data_status(bundled, now=now).auto_count_if_fresh


def test_cli_system_json(cache, capsys):
    assert cli.main(["system", "--json", "--no-cache", "--vram", "8", "--ram", "32"]) == 0
    out = json.loads(capsys.readouterr().out)
    st = out["database"]["status"]
    assert st["source"] == "bundled" and st["expired"] is False and st["auto_count"] > 0
    assert out["database"]["origin"] == "bundled"


def test_cli_system_text_shows_expired(cache, capsys, monkeypatch):
    from genmodelfit import gate

    monkeypatch.setattr(gate, "_now", lambda: CHECKED + timedelta(days=42))
    assert cli.main(["system", "--no-cache", "--vram", "8", "--ram", "32"]) == 0
    out = capsys.readouterr().out
    assert "=== 一覧 ===" in out
    assert "期限切れ" in out and "update --from-release" in out
    assert re.search(r"自動のおすすめ: \d+ 件中 0 件", out)
    assert cli.main(["system", "--no-cache", "--vram", "8", "--ram", "32", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "=== Data ===" in out
    assert "EXPIRED" in out and "update --from-release" in out
    assert "Auto-recommendable: 0 of" in out


def test_cli_system_max_head_age_none(cache, capsys, monkeypatch):
    from genmodelfit import gate

    monkeypatch.setattr(gate, "_now", lambda: datetime(2026, 12, 1, tzinfo=UTC))
    assert cli.main(["system", "--json", "--no-cache", "--vram", "8", "--max-head-age-days", "none"]) == 0
    st = json.loads(capsys.readouterr().out)["database"]["status"]
    assert st["expired"] is False and st["max_head_age_days"] is None
