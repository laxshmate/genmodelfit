# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""毎週の一覧の更新(`python -m genmodelfit.weekly`、#19)の試験。ネットワークには触らない。

- 作り直しの結果を差し替えて、確かめ(HEAD の期限・件数・自動のおすすめ)・差分・PR の本文・--apply を見る
- 本物の作り直しは tests/fixtures/update の記録(DirFetcher)で動かす。記録は一部が欠けている(読めない
  テンプレート・HF の検索)ので、失敗(PR を作らない)の道を通る
- 記録して(--record)読み直す(--offline-sources)道
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from genmodelfit import weekly
from genmodelfit.db import Database, load
from genmodelfit.diff import diff_db
from genmodelfit.update import LICENSE_POLICY, UpdateFailed

FIX = Path(__file__).parent / "fixtures" / "update"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(scope="module")
def bundled() -> Database:
    return load(use_cache=False)


def _fresh(db: Database, when: datetime) -> Database:
    new = copy.deepcopy(db)
    stamp = when.isoformat().replace("+00:00", "Z")
    for m in new.models:
        for f in m.files:
            if f.head_checked_at:
                f.head_checked_at = stamp
    new.generated_at = stamp
    return new


def _fake_update(monkeypatch, make):
    """weekly が呼ぶ作り直しを差し替える(make(old) が新しい一覧を返す)。"""
    seen = {}

    def fake(fetch, out_path, *, allow_partial, progress, previous):
        seen["previous"] = previous
        seen["allow_partial"] = allow_partial
        db = make(previous)
        db.save(out_path)
        return db

    monkeypatch.setattr(weekly, "update_database", fake)
    monkeypatch.setattr(weekly, "inspect_database", lambda *a, **k: None)
    return seen


def test_ok_writes_files_and_applies(tmp_path, monkeypatch, bundled):
    def make(old):
        new = _fresh(old, NOW - timedelta(hours=1))
        new.models.pop(0)
        return new

    seen = _fake_update(monkeypatch, make)
    target = tmp_path / "applied.json"
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), apply_to=target, now=NOW)
    assert r["ok"] is True and r["problems"] == [], r["problems"]
    assert seen["previous"].generated_at == bundled.generated_at  # 引き継ぎの元は --base(main の一覧)
    for name in ("db.json", "diff.json", "pr_body.md", "result.json"):
        assert (tmp_path / "out" / name).is_file(), name
    assert target.read_bytes() == (tmp_path / "out" / "db.json").read_bytes()
    assert r["applied_to"] == str(target)
    saved = json.loads((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["summary"]["removed"] == 1
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "自動ではマージしません" in body and "消えた項目: 1 件" in body
    assert "手元の実行" in body  # GitHub Actions の外
    assert r["status"]["expires_at"] > (NOW + timedelta(days=7)).isoformat()


def test_ci_body_says_tests_ran(tmp_path, monkeypatch, bundled):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "laxshmate/genmodelfit")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert r["ok"] and "https://github.com/laxshmate/genmodelfit/actions/runs/42" in body
    assert "ruff と pytest を通してあります" in body and "CI(ci.yml)は自動では動きません" in body


def test_stale_heads_fail(tmp_path, monkeypatch, bundled):
    """作り直したのに HEAD の確認が古い(期限まで 7 日以内) → 失敗にし、--apply しない。"""
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW - timedelta(days=25)))
    base, target = tmp_path / "base.json", tmp_path / "applied.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), apply_to=target, now=NOW)
    assert r["ok"] is False and any("HEAD の期限が近すぎます" in p for p in r["problems"])
    assert not target.exists()
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert body.startswith("## 確かめに落ちました")


def test_shrunk_list_and_no_auto_fail(tmp_path, monkeypatch, bundled):
    def make(old):
        new = _fresh(old, NOW)
        new.models = [m for m in new.models if m.content == "uncensored"]  # 自動のおすすめが 0 件・半分未満
        return new

    _fake_update(monkeypatch, make)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    text = " ".join(r["problems"])
    assert r["ok"] is False and "0 件" in text and "減りました" in text


def test_update_failed_is_reported(tmp_path, monkeypatch, bundled):
    def boom(*a, **k):
        raise UpdateFailed("ネットワークにつながらない")

    monkeypatch.setattr(weekly, "update_database", boom)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    assert r["ok"] is False and r["problems"] == ["ネットワークにつながらない"]
    assert json.loads((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))["ok"] is False


@pytest.mark.parametrize(
    ("counts", "policy", "allowed"),
    [
        ({"hf_repos_failed_transient": 2, "entries_affected": 3, "carried_over": 3}, LICENSE_POLICY, True),
        ({"hf_repos_failed_transient": 2, "entries_affected": 3}, LICENSE_POLICY - 1, False),
        ({"templates_failed": 1}, LICENSE_POLICY, False),
        ({"hf_search_failed": 1}, LICENSE_POLICY, False),
        ({"rate_limit_budget_exhausted": True}, LICENSE_POLICY, False),
    ],
)
def test_partial_rules(counts, policy, allowed):
    new = Database(sources={"partial": True, "partial_counts": counts})
    old = Database(sources={"license_policy": policy})
    assert (weekly.partial_problems(new, old) == []) is allowed
    assert weekly.partial_problems(Database(sources={"partial": False}), old) == []


def test_partial_needs_allow_partial(tmp_path, monkeypatch, bundled):
    def make(old):
        new = _fresh(old, NOW)
        new.sources["partial"] = True
        new.sources["partial_counts"] = {"hf_repos_failed_transient": 1, "entries_affected": 1}
        return new

    seen = _fake_update(monkeypatch, make)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "a", base=base, fetch=object(), now=NOW)
    assert seen["allow_partial"] is True  # 書き出しまではさせて、weekly が決める
    assert r["ok"] is False and "--allow-partial" in r["problems"][0]
    r = weekly.run(tmp_path / "b", base=base, fetch=object(), now=NOW, allow_partial=True)
    assert r["ok"] is True and r["partial"] is True
    body = (tmp_path / "b" / "pr_body.md").read_text(encoding="utf-8")
    assert "一部を取得できないまま作りました" in body


def test_real_update_from_fixtures_fails_without_pr(tmp_path, bundled):
    """記録(tests/fixtures/update)での本物の作り直し。記録は一部が欠けているので失敗(PR を作らない)。"""
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    target = tmp_path / "applied.json"
    r = weekly.run(
        tmp_path / "out", fetch=weekly.replay_fetcher(src), apply_to=target, allow_partial=True, now=NOW
    )
    assert r["ok"] is False and not target.exists()
    text = " ".join(r["problems"])
    assert "テンプレートの JSON が 1 件読めませんでした" in text and "検索" in text
    assert (tmp_path / "out" / "db.json").is_file()  # 中身は見られる(PR にはしない)


def test_record_and_replay(tmp_path):
    class Inner:
        rate_limited = 3

        def get_json(self, url):
            return {"url": url}

        def get_text(self, url):
            return "text"

        def head_size(self, url):
            return 10

        def head_status(self, url):
            return 302

        def get_range(self, url, start, end):
            return b"x" * (end - start + 1)

    rec = weekly.WeeklyRecorder(Inner(), tmp_path / "rec")
    url = "https://huggingface.co/api/models/a/b?blobs=true"
    head = "https://huggingface.co/a/b/resolve/main/m.safetensors"
    assert rec.get_json(url) == {"url": url}
    assert rec.head_status(head) == 302
    assert rec.get_range(head, 0, 7) == b"x" * 8 and rec.rate_limited == 3  # 中身のものを使う
    rec.save_heads()
    replay = weekly.replay_fetcher(tmp_path / "rec")
    assert replay.get_json(url) == {"url": url} and replay.head_status(head) == 302


def test_main_offline_exit_code(tmp_path, capsys):
    out = tmp_path / "out"
    code = weekly.main(["--dry-run", "--out", str(out), "--offline-sources", str(FIX), "--json"])
    assert code == 1
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is False and res["offline"] is True


def test_main_usage_errors(tmp_path):
    with pytest.raises(SystemExit) as e:
        weekly.main(["--out", str(tmp_path), "--apply", "x.json", "--dry-run"])
    assert e.value.code == 2


def test_body_lists_permissive_changes_first(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    a = next(m for m in new.models if m.license is None)
    a.license = "mit"
    body = weekly.pr_body(diff_db(old, new), {"status": {}})
    assert "ライセンスが寛容な側に変わった項目: 1 件" in body
    assert f"| ⚠ | {weekly.md(a.id)} | 不明 | mit |" in body
    assert len(body) <= weekly.MAX_BODY_CHARS


def test_popularity_bias_is_reported(tmp_path, monkeypatch, bundled):
    """人気のかたより(#95。docs/scoring.md「人気」): 相関を result に入れ、PR の本文に表を出す。
    -0.3 を下回った種類は注意に出す(PR は止めない)。"""
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    assert r["ok"] and {b["modality"] for b in r["popularity_bias"] if b["checked"]} >= {"image", "video"}
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "人気のかたよりの確認" in body and "人気のかたより:" not in body
    flagged = {"modality": "image", "models": 30, "rho": -0.42, "checked": True, "flagged": True}
    # かたよりが出た週は、result の warnings に入れ、merge-check も safe にしない(自動でマージしない)
    from genmodelfit import popularity

    monkeypatch.setattr(popularity, "vram_bias", lambda db, coefficients=None: [flagged])
    r = weekly.run(tmp_path / "out2", base=base, fetch=object(), now=NOW)
    assert any("人気のかたより: image" in w for w in r["warnings"])
    weekly_body = (tmp_path / "out2" / "pr_body.md").read_text(encoding="utf-8")
    assert weekly_body.count("人気のかたより: image") == 1
    mc = weekly.merge_check(base, base)
    assert mc["safe"] is False and any(x["id"] == "popularity_bias:image" for x in mc["reasons"])
    body = weekly.pr_body(diff_db(bundled, bundled), {"status": {}, "popularity_bias": [flagged]})
    assert "人気のかたより: image で要る VRAM と使用数の順位の相関が -0.42" in body


EVIL = [
    "<!-- hidden",
    "@octocat please",
    "![img](https://evil.example/x.png)",
    "<img src=x onerror=alert(1)>",
    "</details><details open>",
    "[link](https://evil.example)",
    "a|b`code`*em*_u_" + chr(92) + "back#1~~s~~",
]


def test_md_neutralizes_untrusted_text():
    for t in EVIL:
        out = weekly.md(t)
        assert "<" not in out and ">" not in out, out
        assert "@" not in out or "@" + chr(0x200B) in out
        for ch in "[]!|`*_#~":
            # 記号はすべてバックスラッシュの後ろにある
            assert all(out[i - 1] == "\\" for i, c in enumerate(out) if c == ch), (t, out)
    assert weekly.md("a" + chr(10) + "b" + chr(9) + "c") == "a b c" and weekly.md(None) == "—"


def test_body_escapes_malicious_names(bundled):
    old, new = copy.deepcopy(bundled), copy.deepcopy(bundled)
    from genmodelfit.models import ModelEntry, ModelFile

    for i, t in enumerate(EVIL):
        new.models.append(
            ModelEntry(
                id=f"hf:{t}/{i}",
                name=t,
                modality="image",
                source="huggingface",
                content="uncensored",
                content_evidence=[t],
                license="other",
                license_name=t,
                files=[ModelFile(name=t, url=f"https://huggingface.co/{t}/resolve/main/x", head_status=401)],
            )
        )
    new.sources["huggingface"]["unauthorized"] = [*new.sources["huggingface"]["unauthorized"], *EVIL]
    body = weekly.pr_body(diff_db(old, new), {"status": {}, "warnings": EVIL[:2]})
    for bad in ("<!--", "<img", "@octocat", "[img](", "[link](", "</details><details open>"):
        assert bad not in body, bad
    assert body.count("<details>") == body.count("</details>")


def test_truncated_body_keeps_details_balanced(monkeypatch):
    monkeypatch.setattr(weekly, "MAX_BODY_CHARS", 2_000)
    nl = chr(10)
    row = "| a | b |" + nl
    body = "intro" + nl + "<details><summary>x</summary>" + nl + nl + row * 1_000 + nl + "</details>" + nl
    out = weekly.limit_body(body)
    assert len(out) <= 2_000 and out.count("<details>") == out.count("</details>") == 1
    assert "省きました" in out and row in out
    assert out.split("…")[0].rstrip(nl).endswith("|")  # 行の途中で切らない


def test_many_new_uncensored(tmp_path, monkeypatch, bundled):
    from genmodelfit.models import ModelEntry

    def make(old):
        new = _fresh(old, NOW)
        new.models += [
            ModelEntry(
                id=f"hf:n/{i}", name=f"n{i}", modality="image", source="huggingface", content="uncensored"
            )
            for i in range(weekly.MAX_NEW_UNCENSORED + 1)
        ]
        return new

    _fake_update(monkeypatch, make)
    # 前の一覧はふだんの週(7 日 + 数時間前)に作り直したものにする: 上限は 1 週 = 5 件(#197)。
    # 同梱の一覧の日付のままだと、NOW との前後で道が変わる
    old = copy.deepcopy(bundled)
    old.generated_at = (NOW - timedelta(days=7, hours=3)).isoformat().replace("+00:00", "Z")
    assert weekly.uncensored_limit(old, NOW) == (weekly.MAX_NEW_UNCENSORED, 1)
    base = tmp_path / "base.json"
    old.save(base)
    r = weekly.run(tmp_path / "a", base=base, fetch=object(), now=NOW)
    assert r["ok"] is False and any("無検閲" in p for p in r["problems"])
    r = weekly.run(tmp_path / "b", base=base, fetch=object(), now=NOW, allow_many_uncensored=True)
    assert r["ok"] is True and r["warnings"]
    body = (tmp_path / "b" / "pr_body.md").read_text(encoding="utf-8")
    assert "新しく無検閲の印が付いた項目が" in body.split("### 要約")[0]


def test_shrink_per_source(tmp_path, monkeypatch, bundled):
    def make(old):
        new = _fresh(old, NOW)
        new.models = [m for m in new.models if m.source != "huggingface"]  # 全体は半分を切らない
        return new

    _fake_update(monkeypatch, make)
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    assert r["ok"] is False
    assert any(p.startswith("Hugging Face の項目が") for p in r["problems"])
    assert not any(p.startswith("項目が") for p in r["problems"])


def test_old_expiry_in_summary(tmp_path, monkeypatch, bundled):
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    old_exp = r["status_old"]["expires_at"]
    assert old_exp and f"| HEAD の期限(expires{chr(92)}_at) | {old_exp} |" in body


def test_code_block_contains_untrusted_report():
    rep = "| x | <img src=x> @user ```` [a](b) <!--"
    out = weekly.code_block(rep)
    fence = out.split("text", 1)[0]
    assert len(fence) == 5 and out.endswith(fence + "\n")  # 中身の ```` より長い囲み
    assert rep in out
    assert weekly.code_block("x" * 10, max_chars=3).count("省きました") == 1


# --- GPU の表の見張り(#103) -----------------------------------------------------------------------------


class _GpuRaw:
    """tests/fixtures/gpu_watch_raw の写しで上流の GPU の一覧を返す(通信しない)。"""

    def __init__(self):
        from genmodelfit import gpuwatch

        self.inner = gpuwatch._RawDir(Path(__file__).parent / "fixtures" / "gpu_watch_raw")

    def get_text(self, url):
        return self.inner.get_text(url)

    def get_json(self, url):
        return self.inner.get_json(url)


def test_gpu_watch_section_in_the_body(tmp_path, monkeypatch, bundled):
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=_GpuRaw(), now=NOW)
    assert r["ok"] is True  # 見張りの候補は結果(ok)に効かない
    w = r["gpu_watch"]
    assert w["candidates"] and w["unreadable"] == []
    saved = json.loads((tmp_path / "out" / "gpu_watch.json").read_text(encoding="utf-8"))
    assert saved == w
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "### GPU 表の見張り" in body and "マージを止めません" in body
    assert any(c["item"].startswith("1002:75b0 Navi 50") for c in w["candidates"])
    assert r"| rocm\_hw\_specs | 表に無い | Ryzen AI 9 HX 575 (Radeon 990M) |" in body  # md() を通す
    assert f"ほか {len(w['candidates']) - weekly.MAX_ROWS} 件" in body  # 長い表は gpu_watch.json へ
    assert "GPU 表の見張り」に候補があれば" in body
    assert body.index("### GPU 表の見張り") < body.index("### マージの前に確かめること")


def test_gpu_watch_lists_unreadable_sources_and_never_fails(tmp_path, monkeypatch, bundled):
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW)  # 何も取れない
    assert r["ok"] is True
    assert {u["source"] for u in r["gpu_watch"]["unreadable"]} >= {"pci_ids", "torch_release"}
    body = (tmp_path / "out" / "pr_body.md").read_text(encoding="utf-8")
    assert "読めなかった出典" in body and "候補なし" in body

    from genmodelfit import gpuwatch

    def boom(*a, **k):
        raise RuntimeError("bug in watch")

    monkeypatch.setattr(gpuwatch, "watch", boom)
    r = weekly.run(tmp_path / "out2", base=base, fetch=object(), now=NOW)
    assert r["ok"] is True and r["gpu_watch"]["unreadable"][0]["source"] == "gpuwatch"


def test_gpu_watch_can_be_skipped_and_escapes_text(tmp_path, monkeypatch, bundled):
    _fake_update(monkeypatch, lambda old: _fresh(old, NOW))
    base = tmp_path / "base.json"
    bundled.save(base)
    r = weekly.run(tmp_path / "out", base=base, fetch=object(), now=NOW, gpu_watch=False)
    assert "gpu_watch" not in r
    assert "GPU 表の見張り" not in (tmp_path / "out" / "pr_body.md").read_text(
        encoding="utf-8"
    )  # 確かめる項目も出さない
    evil = {
        "candidates": [
            {"source": "pci_ids", "kind": "not in tables", "item": "<img src=x> @owner", "detail": "|x|"}
        ],
        "unreadable": [],
        "read": [],
    }
    text = "\n".join(weekly.gpu_watch_section(evil))
    assert "<img" not in text and "@owner" not in text.replace("@​owner", "")
