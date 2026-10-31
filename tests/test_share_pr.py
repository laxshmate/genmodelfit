# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""share が測って PR で送る(#124)。

本物の GitHub・GPU・ComfyUI・ブラウザには触らない(requests の session は偽物に差し替える)。"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genmodelfit import cli, contrib, github, measure
from genmodelfit.models import Gpu, ModelEntry, SystemInfo

FIX = Path(__file__).parent / "fixtures" / "contrib"
GOOD = sorted(p for p in FIX.rglob("*.json") if p.parent.name == "rtx-3070")
TOKEN = "gho_SECRETtoken1234567890"
UP = "https://api.github.com/repos/laxshmate/genmodelfit"
FORK = "https://api.github.com/repos/alice/genmodelfit"


# --- 偽の GitHub ----------------------------------------------------------------------------------


class Resp:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


HEAD_SHA = "a" * 40  # 本家の main の先頭
OLD_BASE = "4" * 40  # 古いフォークの main の先頭(本家との共通の祖先)


class FakeGitHub:
    """GitHub の API の偽物。呼ばれた順と中身を calls に残す。"""

    def __init__(
        self,
        *,
        fork_exists=False,
        open_prs=None,
        existing=(),
        user_ok=True,
        polls=None,
        on_main=(),
        branches=(),
        stale=False,
        diverged=False,
    ):
        # stale: 前からあるフォークで、本家の新しいコミットが無い(合わせるまで、それを指すブランチは 404)
        self.stale = stale
        self.diverged = diverged
        self.on_main = set(on_main)
        self.branches = set(branches)
        self.calls: list[tuple[str, str, dict]] = []
        self.fork_exists = fork_exists
        self.open_prs = open_prs or []
        self.existing = set(existing)
        self.user_ok = user_ok
        self.polls = list(polls or [])
        self.put: dict[str, dict] = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        path = url.split("?")[0]
        if url == github.DEVICE_CODE_URL:
            return Resp(
                200,
                {
                    "device_code": "dev-code",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 5,
                    "expires_in": 900,
                },
            )
        if url == github.TOKEN_URL:
            return Resp(200, self.polls.pop(0))
        if path == "https://api.github.com/user":
            if not self.user_ok:
                return Resp(401, {"message": "Bad credentials"})
            return Resp(200, {"login": "alice"})
        if method == "GET" and path == FORK:
            if self.fork_exists:
                return Resp(
                    200,
                    {
                        "full_name": "alice/genmodelfit",
                        "fork": True,
                        "parent": {"full_name": "laxshmate/genmodelfit"},
                    },
                )
            return Resp(404, {"message": "Not Found"})
        if method == "POST" and path == f"{UP}/forks":
            self.fork_exists = True
            return Resp(202, {"full_name": "alice/genmodelfit"})
        if method == "GET" and path == f"{UP}/pulls":
            return Resp(200, self.open_prs)
        if method == "GET" and path == f"{UP}/git/ref/heads/main":
            return Resp(200, {"object": {"sha": HEAD_SHA}})
        if method == "GET" and path == f"{UP}/compare/main...alice:main":
            # 古いフォークは古い共通の祖先、作ったばかり・合っているフォークは本家の先頭
            base = OLD_BASE if self.stale else HEAD_SHA
            return Resp(
                200, {"status": "behind" if self.stale else "identical", "merge_base_commit": {"sha": base}}
            )
        if method == "POST" and path == f"{FORK}/git/refs":
            if self.stale and kw["json"]["sha"] != OLD_BASE:
                return Resp(404, {"message": "Not Found"})  # 古いフォークに無いコミット
            name = kw["json"]["ref"][len("refs/heads/") :]
            if name in self.branches:
                return Resp(422, {"message": "Reference already exists"})
            self.branches.add(name)
            return Resp(201, {"ref": kw["json"]["ref"]})
        if method == "GET" and path.startswith(f"{FORK}/git/ref/heads/"):
            name = path[len(f"{FORK}/git/ref/heads/") :]
            return Resp(200 if name in self.branches else 404, {})
        if method == "GET" and path.startswith(f"{UP}/contents/"):
            rel = path[len(f"{UP}/contents/") :]
            return Resp(200 if rel in self.on_main else 404, {"message": "Not Found"})
        if method == "PUT" and path.startswith(f"{FORK}/contents/"):
            rel = path[len(f"{FORK}/contents/") :]
            if rel in self.existing:
                return Resp(422, {"message": 'Invalid request.\n\n"sha" wasn\'t supplied.'})
            self.put[rel] = kw["json"]
            return Resp(201, {"content": {"path": rel}})
        if method == "POST" and path == f"{UP}/pulls":
            return Resp(201, {"html_url": "https://github.com/laxshmate/genmodelfit/pull/200"})
        return Resp(500, {"message": f"unexpected {method} {url}"})

    def steps(self) -> list[str]:
        """呼び出しを短い名前の並びに(順を確かめる)。"""
        out = []
        for method, url, _kw in self.calls:
            path = url.split("?")[0]
            if path.endswith("/user"):
                out.append("user")
            elif path == FORK:
                out.append("get-fork")
            elif path.endswith("/forks"):
                out.append("fork")
            elif path == f"{UP}/pulls":
                out.append("list-prs" if method == "GET" else "open-pr")
            elif path.endswith("/git/ref/heads/main"):
                out.append("head")
            elif "/compare/" in path:
                out.append("base")
            elif path.endswith("/git/refs"):
                out.append("branch")
            elif "/git/ref/heads/" in path:
                out.append("branch?")
            elif path.startswith(f"{UP}/contents/"):
                out.append("on-main?")
            elif "/contents/" in path:
                out.append("put")
            else:
                out.append(f"{method} {path}")
        return out


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(cli, "_share_session", lambda: fake)
    return fake


@pytest.fixture
def no_session(monkeypatch):
    def deny():
        raise AssertionError("session を作った(通信しないはず)")

    monkeypatch.setattr(cli, "_share_session", deny)


@pytest.fixture
def this_pc(monkeypatch):
    g = Gpu(name="NVIDIA GeForce RTX 3070", vendor="nvidia", vram_total_gb=8.0, backend="cuda")
    s = SystemInfo(os="Windows 11", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    monkeypatch.setattr(cli, "_system", lambda args: s)
    monkeypatch.setattr(cli, "detect", lambda *a, **k: s)
    return s


def answers(monkeypatch, *values):
    asked: list[str] = []
    it = iter(values)

    def ask(prompt=""):
        asked.append(prompt)
        v = next(it)
        if v is None:
            raise EOFError
        return v

    monkeypatch.setattr("builtins.input", ask)
    return asked


def _write_sub(i: int) -> Path:
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["peak_vram_gb"] = 5.0 + i / 10
    d["measurement"]["note"] = None
    return contrib.write_submission(d, str(measure.submissions_dir()) + "/")


def sugg(model_id: str, secs: int, state: str = "yes", missing: int = 0):
    e = ModelEntry(id=model_id, name=model_id, modality="image")
    return measure.Suggestion(
        model_id=model_id,
        model_name=model_id,
        name=model_id,
        modality="image",
        precision=None,
        fit_level="good",
        installed=measure.Installed(state, missing, missing, 1 if missing else 0),
        seconds=secs,
        seconds_basis="typical",
        group_key=model_id,
        fit=SimpleNamespace(model=e),
    )


@pytest.fixture
def plan(monkeypatch):
    """提案と測る処理を差し替える。測ると送る用の提出を書く(measure と同じ置き場所)。"""
    state = {
        "sugg": [
            sugg("a_installed_short", 60),
            sugg("b_not_installed", 60, "no", 2 * 1024**3),
            sugg("c_installed", 120),
            sugg("d_installed_too_long", 200),
            sugg("e_installed", 100),
        ],
        "ran": [],
        "urls": [],
        "roots": None,
        "events": [],
    }

    def fake_suggestions(db, system, *, roots=None, **k):
        state["roots"] = roots
        return state["sugg"]

    def fake_run(args, url, entry, settings, free=False, progress=None):
        state["ran"].append(entry.id)
        state["urls"].append(url)
        state.setdefault("free", []).append(free)
        state["events"].append(f"run:{entry.id}")
        p = _write_sub(len(state["ran"]))
        m = SimpleNamespace(outcome="ok", peak_vram_gb=5.0, seconds=12.0)
        return {
            "error": None,
            "result": SimpleNamespace(warnings=[]),
            "measurement": m,
            "submission": p,
            "path_in_repo": None,
            "saved_to": None,
            "not_saved": None,
        }

    monkeypatch.setattr(measure, "suggestions", fake_suggestions)
    monkeypatch.setattr(cli, "_measure_run", fake_run)
    return state


# --- 1. 計画: 入っているものだけ、目安の合計 ≤ 300 秒 ------------------------------------------------


def test_share_plan_takes_installed_within_the_budget():
    s = [sugg("a", 60), sugg("b", 60, "no", 1), sugg("c", 120), sugg("d", 200), sugg("e", 100)]
    picked, total = measure.share_plan(s, 300)
    assert [x.model_id for x in picked] == ["a", "c", "e"] and total == 280
    dl, dl_total = measure.share_plan(s, 300 - total, state="no")
    assert dl == [] and dl_total == 0  # 残り 20 秒には収まらない
    dl, _ = measure.share_plan(s, 100, state="no")
    assert [x.model_id for x in dl] == ["b"]
    assert measure.SHARE_BUDGET_SECONDS == 300


def test_share_measures_installed_plan_after_one_question(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "")  # 測りますか → はい、送り方 → 1(PR)
    argv = ["--lang", "ja", "share", "--url", "http://127.0.0.1:8188"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert plan["ran"] == ["a_installed_short", "c_installed", "e_installed"]  # 60 + 120 + 100 ≤ 300
    assert asked[0] == "3 件を測って送りますか? [Y/n] " and asked[1].startswith("送り方: 1) PR")
    assert len(asked) == 2  # 落とす候補は時間が残らないので尋ねない
    assert "ComfyUI:    http://127.0.0.1:8188(--url で指定)" in out  # measure と同じ画面の部品
    assert "入っている" in out and "約 60 秒" in out
    assert plan["urls"] == ["http://127.0.0.1:8188"] * 3
    assert "https://github.com/laxshmate/genmodelfit/pull/200" in out


def test_share_auto_finds_a_running_comfyui(capsys, monkeypatch, this_pc, plan, gh, tmp_path):
    from genmodelfit import comfyfind

    base = tmp_path / "ComfyUI"
    (base / "models").mkdir(parents=True)
    (base / "main.py").write_text("")
    cand = comfyfind.Candidate(base, "running", "127.0.0.1:8000")
    monkeypatch.setattr(comfyfind, "search_disabled", lambda: False)
    monkeypatch.setattr(comfyfind, "running_candidates", lambda *a, **k: [cand])
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--yes", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["comfyui"] == {"url": "http://127.0.0.1:8000", "found_by": "running"}
    assert plan["urls"][0] == "http://127.0.0.1:8000"
    assert plan["roots"] == [base / "models"]  # measure と同じ _measure_roots


# --- 2. 入っていないもの: y か --download のときだけ落とす ------------------------------------------


@pytest.fixture
def dl_plan(plan):
    plan["sugg"] = [sugg("a_installed_short", 60), sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    return plan


def test_share_does_not_download_when_answered_no(capsys, monkeypatch, this_pc, dl_plan, gh):
    calls: list[str] = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: calls.append(mid) or 0)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "", "")  # 測る → はい、落とす → Enter(既定は No)、送る → はい
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert asked[1] == "落として測りますか? [y/N] "
    assert calls == [] and dl_plan["ran"] == ["a_installed_short"]
    assert len(gh.put) == 1  # 測ったものだけを送る


def test_share_downloads_when_answered_yes(capsys, monkeypatch, this_pc, dl_plan, gh):
    calls: list[tuple] = []
    monkeypatch.setattr(
        cli, "_measure_download", lambda args, mid, **k: calls.append((mid, k.get("consented"))) or 0
    )
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "y", "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    # 落とすのは measure と同じ _measure_download(--yes を付けないので download が自分で同意を尋ねる)
    assert calls == [("b_not_installed", True)]  # 名前と量を見せて同意を取ったので、download で尋ね直さない
    assert dl_plan["ran"] == ["a_installed_short", "b_not_installed"]


def test_share_yes_alone_never_downloads(capsys, monkeypatch, this_pc, dl_plan, gh):
    calls: list[str] = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: calls.append(mid) or 0)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("--yes では尋ねない"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--yes", "--json", "--url", "http://x:1"]) == 0
    assert calls == []
    d = json.loads(capsys.readouterr().out)
    assert d["downloaded"] is False and [m["model_id"] for m in d["measured"]] == ["a_installed_short"]
    # --download で落とす(尋ねない)。download の同意の決まりは download のまま(--yes・--any-license を渡す)
    seen = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: seen.append(args) or 0)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["share", "--yes", "--download", "--any-license", "--url", "http://x:1"]) == 0
    assert len(seen) == 1 and seen[0].yes and seen[0].include_unknown_license


def test_share_download_forwards_consent_to_download(monkeypatch):
    got = []
    monkeypatch.setattr(cli, "cmd_download", lambda ns: got.append(ns) or 0)
    args = cli.build_parser().parse_args(["share", "--yes", "--any-license", "--comfyui-dir", "D:/C"])
    assert cli._measure_download(args, "image_sdxl_simple") == 0
    ns = got[0]
    assert ns.yes and ns.include_unknown_license and ns.comfyui_dir == "D:/C" and ns.content == "sfw"


def test_share_json_refuses_to_download():
    assert cli.main(["share", "--json", "--yes", "--download"]) == 2  # 使い方の誤り


# --- 3. ComfyUI が無く、送っていない実測も無い ------------------------------------------------------


def test_share_without_comfyui_and_nothing_to_send(capsys, monkeypatch, this_pc, plan, no_session):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("起動していなければ尋ねない"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share"]) == 1
    cap = capsys.readouterr()
    assert "測るもの(入っているもの" in cap.out  # 計画は見せる(#123 と同じ)
    assert "エラー: ComfyUI を探していません" in cap.err and "ComfyUI を起動してから" in cap.err
    assert plan["ran"] == []


def test_share_without_comfyui_offers_the_unsent_ones(capsys, monkeypatch, this_pc, plan, gh):
    _write_sub(1)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "")
    assert cli.main(["--lang", "ja", "share"]) == 0
    cap = capsys.readouterr()
    assert len(asked) == 1 and asked[0].startswith("送り方:") and plan["ran"] == []
    assert "送っていない実測 1 件だけを送るか" in cap.err and len(gh.put) == 1


# --- 4. ログインは測る前 -------------------------------------------------------------------------


def test_share_login_happens_before_measuring(capsys, monkeypatch, this_pc, plan, gh):
    gh.user_ok = False
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    # 端末: 使えない環境変数のトークンは知らせて続ける(最後に Issue を選べる)
    asked = answers(monkeypatch, "", "1", "n")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert asked[0] == "3 件を測って送りますか? [Y/n] " and "このままでは PR で送れません" in err
    assert len(plan["ran"]) == 3 and asked[2].startswith("PR で送れませんでした")
    # 端末でない --yes(PR): 測る前に止まる
    plan["ran"].clear()
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    assert plan["ran"] == [] and "GITHUB_TOKEN のトークンが使えません" in capsys.readouterr().err


def test_share_without_any_login_says_how(capsys, monkeypatch, this_pc, plan, gh):
    assert github.client_id() == "Ov23liLX2TDKfSO6B955"  # 登録した OAuth アプリ(公開してよい値)
    # --json ではデバイス認証ができない: 測る前に止まる
    assert cli.main(["share", "--yes", "--json", "--url", "http://x:1"]) == 1
    assert "GITHUB_TOKEN" in json.loads(capsys.readouterr().out)["error"] and plan["ran"] == []
    assert gh.calls == []
    # client id を空にするとデバイス認証を使わない
    monkeypatch.setenv(github.CLIENT_ID_ENV, "")
    assert github.client_id() is None
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    # 端末なら資格情報が無くても測り(ログインは送り方で PR を選んだときだけ)、PR で送れなければ Issue を勧める
    asked = answers(monkeypatch, "", "1", "n")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert "ログインできません。GITHUB_TOKEN を設定するか、--issue で送ってください" in err
    assert plan["ran"] == ["a_installed_short", "c_installed", "e_installed"]
    assert asked[2].startswith("PR で送れませんでした。同じファイルを Issue")
    assert "genmodelfit share --no-measure" in err and measure.load_shared() == {}
    monkeypatch.setenv(github.CLIENT_ID_ENV, "Iv1.test")
    assert github.client_id() == "Iv1.test"


# --- 5. デバイス認証 -----------------------------------------------------------------------------


def test_device_flow_pending_slow_down_then_token():
    fake = FakeGitHub(
        polls=[
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": TOKEN, "expires_in": 28800, "refresh_token": "ghr_REFRESH"},
        ]
    )
    slept: list[float] = []
    shown = []
    tok = github.device_flow(fake, "cid", shown.append, sleep=slept.append)
    assert tok == (TOKEN, 28800.0) and slept == [5, 5, 10]  # slow_down で 5 秒増やす
    assert shown[0].user_code == "ABCD-1234"
    body = fake.calls[0][2]["data"]
    assert body == {"client_id": "cid", "scope": "public_repo"}
    assert fake.calls[1][2]["data"]["grant_type"] == github.DEVICE_GRANT


@pytest.mark.parametrize(("error", "kind"), [("expired_token", "expired"), ("access_denied", "denied")])
def test_device_flow_stops(error, kind):
    fake = FakeGitHub(polls=[{"error": "authorization_pending"}, {"error": error}])
    with pytest.raises(github.GitHubError) as e:
        github.device_flow(fake, "cid", lambda dc: None, sleep=lambda s: None)
    assert e.value.kind == kind


def test_login_by_device_flow_caches_the_token(monkeypatch):
    monkeypatch.setenv(github.CLIENT_ID_ENV, "Iv1.test")
    fake = FakeGitHub(polls=[{"access_token": TOKEN, "expires_in": 28800, "refresh_token": "ghr_REFRESH"}])
    c = github.login(fake, interactive=True, on_code=lambda dc: None, sleep=lambda s: None)
    assert (c.login, c.source) == ("alice", "device") and github.read_cached_token() == TOKEN
    saved = json.loads(github.token_path().read_text(encoding="utf-8"))
    assert set(saved) == {"access_token", "expires_at"} and "ghr_REFRESH" not in json.dumps(saved)
    if os.name == "posix":
        assert (github.token_path().stat().st_mode & 0o777) == 0o600
    assert TOKEN not in repr(c) and TOKEN not in json.dumps(c.to_dict())
    c2 = github.login(FakeGitHub(), interactive=False)
    assert c2.source == "cache"
    # 使えなくなったキャッシュは消して、端末でなければ止まる
    with pytest.raises(github.GitHubError) as e:
        github.login(FakeGitHub(user_ok=False), interactive=False)
    assert e.value.kind == "no_login" and not github.token_path().exists()  # 401 でキャッシュを消した


def test_expired_cached_token_runs_the_device_flow_again(monkeypatch):
    import time

    github.write_cached_token("gho_OLD", 28800, now=lambda: time.time() - 30000)  # 期限切れ
    assert github.read_cached_token() is None and not github.token_path().exists()
    github.write_cached_token("gho_OLD", 28800, now=lambda: time.time() - 30000)
    fake = FakeGitHub(polls=[{"access_token": TOKEN, "expires_in": 28800}])
    shown = []
    logs: list[str] = []
    c = github.login(fake, interactive=True, on_code=shown.append, log=logs.append, sleep=lambda s: None)
    assert c.source == "device" and shown and logs  # 期限切れを知らせてデバイス認証をやり直した
    assert all("gho_OLD" not in str(kw) for _m, _u, kw in fake.calls)  # 切れたトークンは送らない
    assert github.read_cached_token() == TOKEN
    # 端末でなければ、期限切れで止まる(デバイス認証はできない)
    github.write_cached_token("gho_OLD", 28800, now=lambda: time.time() - 30000)
    with pytest.raises(github.GitHubError) as e:
        github.login(FakeGitHub(), interactive=False)
    assert e.value.kind == "no_login"


def test_logout(capsys):
    github.write_cached_token(TOKEN)
    assert cli.main(["--lang", "ja", "share", "--logout"]) == 0
    assert "消しました" in capsys.readouterr().out and github.read_cached_token() is None
    assert cli.main(["share", "--logout", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_hosts_are_only_github():
    with pytest.raises(github.GitHubError):
        github._request(FakeGitHub(), "GET", "http://api.github.com/user")
    with pytest.raises(github.GitHubError):
        github._request(FakeGitHub(), "GET", "https://example.com/user")


# --- 6. フォーク・ブランチ・ファイル・PR の順と中身 -------------------------------------------------


def _creds():
    return github.Credentials(TOKEN, "alice", "env:GITHUB_TOKEN")


def test_submit_new_pr_order_and_content():
    fake = FakeGitHub()
    files = [("data/contrib/rtx-3070/2026-10-29-aaaaaaaa.json", '{"a": 1}\n')]
    res = github.submit(
        fake,
        _creds(),
        files,
        title="measurements: NVIDIA GeForce RTX 3070 (1)",
        body="b",
        branch="genmodelfit-share/rtx-3070/12345678",
        match_prefix="genmodelfit-share/rtx-3070/",
        sleep=lambda s: None,
    )
    assert fake.steps() == [
        "on-main?",
        "get-fork",
        "fork",
        "get-fork",
        "list-prs",
        "base",
        "branch",
        "put",
        "open-pr",
    ]
    for _m, _u, kw in fake.calls:
        assert kw["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert kw["allow_redirects"] is False
    branch = next(kw for m, u, kw in fake.calls if u.endswith("/git/refs"))["json"]
    assert branch == {"ref": "refs/heads/genmodelfit-share/rtx-3070/12345678", "sha": HEAD_SHA}
    put = fake.put["data/contrib/rtx-3070/2026-10-29-aaaaaaaa.json"]
    assert base64.b64decode(put["content"]).decode() == '{"a": 1}\n'
    assert put["branch"] == "genmodelfit-share/rtx-3070/12345678"
    pr = next(kw for m, u, kw in fake.calls if m == "POST" and u == f"{UP}/pulls")["json"]
    assert pr == {
        "title": "measurements: NVIDIA GeForce RTX 3070 (1)",
        "head": "alice:genmodelfit-share/rtx-3070/12345678",
        "base": "main",
        "body": "b",
    }
    assert res.pr_url.endswith("/pull/200") and not res.reused and res.uploaded == [files[0][0]]


def test_submit_adds_to_the_open_pr_and_skips_existing_files():
    open_pr = {
        "html_url": "https://github.com/laxshmate/genmodelfit/pull/150",
        "head": {
            "ref": "genmodelfit-share/rtx-3070/old00000",
            "user": {"login": "alice"},
            "repo": {"full_name": "alice/genmodelfit"},
        },
    }
    other = {  # ほかの人の PR・share でないブランチには足さない
        "html_url": "https://github.com/laxshmate/genmodelfit/pull/151",
        "head": {
            "ref": "genmodelfit-share/rtx-3070/x",
            "user": {"login": "bob"},
            "repo": {"full_name": "bob/genmodelfit"},
        },
    }
    a = "data/contrib/rtx-3070/2026-10-29-aaaaaaaa.json"
    b = "data/contrib/rtx-3070/2026-10-29-bbbbbbbb.json"
    fake = FakeGitHub(fork_exists=True, open_prs=[other, open_pr], existing={a})
    res = github.submit(
        fake,
        _creds(),
        [(a, "{}\n"), (b, "{}\n")],
        title="t",
        body="b",
        branch="genmodelfit-share/rtx-3070/new11111",
        match_prefix="genmodelfit-share/rtx-3070/",
    )
    # 新しいブランチ・PR は作らない。本家の main にあるかは、置く前にすべて確かめる
    assert fake.steps() == ["on-main?", "on-main?", "get-fork", "list-prs", "put", "put"]
    assert res.reused and res.pr_url.endswith("/pull/150")
    assert res.uploaded == [b] and res.skipped == [a]
    assert fake.put[b]["branch"] == "genmodelfit-share/rtx-3070/old00000"


def test_submit_explains_a_missing_scope():
    class NoWrite(FakeGitHub):
        def request(self, method, url, **kw):
            if method == "PUT":
                self.calls.append((method, url, kw))
                return Resp(404, {"message": "Not Found"})
            return super().request(method, url, **kw)

    with pytest.raises(github.GitHubError) as e:
        github.submit(
            NoWrite(fork_exists=True),
            _creds(),
            [("data/contrib/x/2026-10-29-aaaaaaaa.json", "{}")],
            title="t",
            body="b",
            branch="genmodelfit-share/x-1",
            match_prefix="genmodelfit-share/x-",
        )
    assert e.value.kind == "scope" and "public_repo" in str(e.value) and TOKEN not in str(e.value)


def test_pr_body_and_title_are_harmless():
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["model_id"] = "x | [link](https://evil.example)"
    body = github.pr_body([d])
    assert github.PR_MARKER in body and "genmodelfit share" in body and "](https://" not in body
    assert github.pr_title("NVIDIA GeForce RTX 3070", 2) == "measurements: NVIDIA GeForce RTX 3070 (2)"


# --- 7. 送ったら印が付き、次の --no-measure では出ない ----------------------------------------------


def test_sent_ones_are_marked_and_not_sent_again(capsys, monkeypatch, gh):
    _write_sub(1)
    _write_sub(2)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--no-measure", "--yes", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["sent"] is True and d["marked_shared"] == 2 and len(d["prs"]) == 1
    assert d["prs"][0]["url"].endswith("/pull/200") and len(d["prs"][0]["uploaded"]) == 2
    assert len(measure.load_shared()) == 2
    title = next(kw for m, u, kw in gh.calls if m == "POST" and u == f"{UP}/pulls")["json"]["title"]
    assert title == "measurements: NVIDIA GeForce RTX 3070 (2)"
    n = len(gh.calls)
    assert cli.main(["share", "--no-measure", "--yes", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["pending"] == [] and d["sent"] is False and len(gh.calls) == n  # 送るものが無ければ通信しない


def test_share_file_sends_one_file_by_pr(capsys, monkeypatch, gh):
    p = _write_sub(1)
    _write_sub(2)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "1")
    assert cli.main(["--lang", "ja", "share", str(p)]) == 0
    assert len(asked) == 1 and asked[0].startswith("送り方:") and len(gh.put) == 1
    assert len(measure.saved_submissions()) == 1


def test_share_refuses_personal_information(capsys, monkeypatch, gh):
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["note"] = r"C:\Users\bob\ComfyUI"
    p = contrib.write_submission(d, str(measure.submissions_dir()) + "/")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", str(p), "--yes", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["files"][0]["ok"] is False and gh.calls == []


# --- 8. --dry-run は通信しない・--issue は今のまま --------------------------------------------------


def test_dry_run_makes_no_session(capsys, monkeypatch, this_pc, plan, no_session):
    _write_sub(1)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--dry-run", "--url", "http://x:1"]) == 0
    assert "--dry-run" in capsys.readouterr().out and plan["ran"] == []
    assert cli.main(["share", "--dry-run", "--json", "--url", "http://x:1"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["dry_run"] is True and len(d["plan"]) == 3 and len(d["files"]) == 1
    assert measure.load_shared() == {}


def test_issue_flag_rules(capsys):
    assert cli.main(["share", "--print-url"]) == 2  # --print-url は --issue のときだけ(使い方の誤り)
    assert cli.main(["share", "--issue", "--dry-run"]) == 2
    assert cli.main(["share", "--issue", "--download"]) == 2


def test_json_and_no_terminal_need_yes(capsys, monkeypatch, no_session):
    assert cli.main(["share", "--json", "--no-measure"]) == 2
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["share", "--no-measure"]) == 2


# --- 9. トークンはどこにも出ない ------------------------------------------------------------------


def test_token_never_shown(capsys, caplog, monkeypatch, this_pc, plan, gh):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "")
    assert cli.main(["share", "--url", "http://x:1"]) == 0
    cap = capsys.readouterr()
    assert cli.main(["share", "--no-measure", "--yes", "--json", "--all"]) == 0
    cap2 = capsys.readouterr()
    for text in (cap.out, cap.err, cap2.out, cap2.err, caplog.text):
        assert TOKEN not in text
    gh.user_ok = False
    assert cli.main(["share", "--no-measure", "--yes", "--json", "--all"]) == 1
    assert TOKEN not in capsys.readouterr().out


def test_library_prints_nothing(capfd, monkeypatch):
    monkeypatch.setenv(github.CLIENT_ID_ENV, "Iv1.test")
    github.login(
        FakeGitHub(polls=[{"access_token": TOKEN, "expires_in": 28800}]),
        interactive=True,
        on_code=lambda dc: None,
        sleep=lambda s: None,
    )
    github.submit(
        FakeGitHub(),
        _creds(),
        [("data/contrib/x/2026-10-29-aaaaaaaa.json", "{}")],
        title="t",
        body="b",
        branch="genmodelfit-share/x-1",
        match_prefix="genmodelfit-share/x-",
        sleep=lambda s: None,
    )
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_github_module_is_not_imported_by_read_only_commands():
    import subprocess

    code = "import genmodelfit.cli as c, sys; c.main(['types']); print('genmodelfit.github' in sys.modules)"
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert r.stdout.strip().splitlines()[-1] == "False"


# --- 実機で見つかったこと(#124): 足りないノード・順・次にすること・--download の案内 ---------------------


class FakeNodes:
    def __init__(self, missing):
        self.missing_map = missing
        self.asked = []

    def missing(self, model_id):
        self.asked.append(model_id)
        return self.missing_map.get(model_id, [])


def test_share_skips_models_needing_missing_nodes_and_backfills(capsys, monkeypatch, this_pc, plan, gh):
    nodes = FakeNodes({"a_installed_short": ["LoadMoGeModel", "MoGePanoramaInference"]})
    monkeypatch.setattr(cli, "_node_check", lambda url: nodes)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    # a を外し、時間の残りで c・e を測る(d は 200 秒で収まらない)
    assert plan["ran"] == ["c_installed", "e_installed"]
    assert "この ComfyUI に無いノードが要る。ComfyUI を更新すると測れます" in out
    assert "LoadMoGeModel, MoGePanoramaInference" in out
    assert "a_installed_short" not in nodes.asked[1:]  # 1 回だけ確かめる
    out_json = []
    plan["ran"].clear()
    assert cli.main(["share", "--yes", "--json", "--url", "http://x:1", "--all"]) == 0
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["skipped"] == [
        {
            "model_id": "a_installed_short",
            "name": "a_installed_short",
            "missing_nodes": ["LoadMoGeModel", "MoGePanoramaInference"],
        }
    ]


def test_share_asks_before_logging_in(capsys, monkeypatch, this_pc, plan, gh):
    events = []
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    real_check = cli._share_precheck  # 測る前の資格情報の確かめ
    monkeypatch.setattr(cli, "_share_precheck", lambda args: events.append("login") or real_check(args))
    monkeypatch.setattr("builtins.input", lambda p="": events.append("ask:" + p[:6]) or "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert events[:2] == ["ask:3 件を測っ", "login"]  # 計画 → 確認 → ログイン → 測る


def test_share_suggests_download_when_nothing_was_measured(capsys, monkeypatch, this_pc, dl_plan, gh):
    def fail_run(args, url, entry, settings, free=False, progress=None):
        return {"error": "boom"}

    monkeypatch.setattr(cli, "_measure_run", fail_run)
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: pytest.fail("--yes だけでは落とさない"))
    monkeypatch.setattr("builtins.input", lambda p="": pytest.fail("--yes では尋ねない"))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    out = capsys.readouterr().out
    assert "--download を付けると落として測ります" in out and "2.0 GB" in out
    assert "落としません(--downloadを付けると" in re.sub(r"\s+", "", out)  # 計画の画面も尋ねないことを言う
    assert cli.main(["share", "--yes", "--json", "--url", "http://x:1"]) == 1
    assert json.loads(capsys.readouterr().out)["download_hint"] == {"count": 1, "bytes": 2 * 1024**3}


def test_measure_stops_early_when_nodes_are_missing(capsys, monkeypatch, this_pc):
    monkeypatch.setattr(cli, "_node_check", lambda url: FakeNodes({"image_sdxl_simple": ["NewNode"]}))
    monkeypatch.setattr(cli, "_measure_run", lambda *a, **k: pytest.fail("測らない"))
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: pytest.fail("落とさない"))
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--yes", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert "ComfyUI に無いノードを使っています: NewNode" in err
    assert "次にすること: ComfyUI を新しい版に更新してください" in err and "起動していて" not in err


def test_measure_list_hides_models_needing_missing_nodes(capsys, monkeypatch, this_pc):
    from genmodelfit import measure as m

    sugg_all = m.suggestions(cli._db(cli.build_parser().parse_args(["measure"])), this_pc)[:3]
    first = sugg_all[0].model_id
    monkeypatch.setattr(cli, "_node_check", lambda url: FakeNodes({first: ["NewNode"]}))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "q")
    assert cli.main(["--lang", "ja", "measure", "-n", "3", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    rows = [x for x in out.splitlines() if x.lstrip().startswith(("1.", "2.", "3."))]
    assert len(rows) == 2 and f"[{first}]: NewNode" in out


def test_next_step_for_missing_nodes_says_update():
    from genmodelfit.workflow import missing_nodes_message

    msg = missing_nodes_message(["A", "B"])
    hint = cli._measure_next_step(msg, "x")
    assert "更新" in hint and "起動していて" not in hint
    assert "起動していて" in cli._measure_next_step("ComfyUI(http://x)に接続できません: refused", "x")


def test_missing_nodes_and_node_check():
    from genmodelfit import bench
    from genmodelfit.workflow import missing_nodes

    ui = {
        "nodes": [
            {"id": 1, "type": "KSampler", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []},
            {"id": 2, "type": "LoadMoGeModel", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []},
            {"id": 3, "type": "Gone", "mode": 4, "inputs": [], "outputs": [], "widgets_values": []},
            {"id": 4, "type": "Note", "mode": 0, "inputs": [], "outputs": [], "widgets_values": []},
        ],
        "links": [],
    }
    assert missing_nodes(ui, {"KSampler": {}}) == ["LoadMoGeModel"]  # 素通し・メモは数えない

    class Client:
        s = None
        calls = 0

        def object_info(self):
            Client.calls += 1
            return {"KSampler": {}}

        def template(self, name):
            return ui if name == "t" else None

    nc = bench.NodeCheck("http://127.0.0.1:1", client=Client())
    assert nc.missing("t") == ["LoadMoGeModel"] and nc.missing("t") == ["LoadMoGeModel"]
    assert Client.calls == 1

    class Down(Client):
        def object_info(self):
            raise bench.BenchError("down")

    assert (
        bench.NodeCheck("http://127.0.0.1:1", client=Down()).missing("t") == []
    )  # 確かめられなければ外さない


# --- 落とすかを尋ねる時(#124 のオーナーの決定) --------------------------------------------------------


def test_no_usable_installed_asks_download_first_before_logging_in(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    events = []
    monkeypatch.setattr(
        cli, "_measure_download", lambda args, mid, **k: events.append("download:" + mid) or 0
    )
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    real_check = cli._share_precheck  # 測る前の資格情報の確かめ
    monkeypatch.setattr(cli, "_share_precheck", lambda args: events.append("login") or real_check(args))
    monkeypatch.setattr("builtins.input", lambda p="": events.append("ask:" + p) or "y")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert events[:3] == ["ask:落として測りますか? [y/N] ", "login", "download:b_not_installed"]
    assert "この PC に入っていて測れるモデルはありません" in capsys.readouterr().out
    assert plan["ran"] == ["b_not_installed"]


def test_declining_the_download_with_nothing_else_ends_quietly(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: pytest.fail("落とさない"))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "")  # 既定は No
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    cap = capsys.readouterr()
    assert "送るものはありません" in cap.out and gh.calls == []  # ログインもしない


def test_usable_installed_are_measured_before_the_download_question(
    capsys, monkeypatch, this_pc, dl_plan, gh
):
    events = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: events.append("download") or 0)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    real_run = cli._measure_run
    monkeypatch.setattr(
        cli, "_measure_run", lambda *a, **k: events.append("run:" + a[2].id) or real_run(*a, **k)
    )

    def ask(p=""):
        events.append("ask:" + p[:6])
        return "n" if "落とし" in p else ""

    monkeypatch.setattr("builtins.input", ask)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert events[:3] == ["ask:1 件を測っ", "run:a_installed_short", "ask:落として測り"]
    assert "download" not in events


def test_yes_without_download_never_starts_comfyui_for_downloads(
    capsys, monkeypatch, this_pc, plan, gh, tmp_path
):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(comfystart, "launch", lambda *a, **k: pytest.fail("起動しない"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    d = venv_install(tmp_path)
    assert cli.main(["--lang", "ja", "share", "--yes", "--comfyui-dir", str(d)]) == 0
    out = capsys.readouterr().out
    assert "--download を付けると落として測ります" in out and gh.calls == []


# --- やり直し・印・PR の見分け・上限(Checker #1・4・6・7) ----------------------------------------------


class FlakyPR(FakeGitHub):
    fail_pr = True

    def request(self, method, url, **kw):
        if method == "POST" and url == f"{UP}/pulls" and self.fail_pr:
            self.calls.append((method, url, kw))
            return Resp(502, {"message": "Bad Gateway"})
        r = super().request(method, url, **kw)
        if method == "PUT" and r.status_code == 201:
            self.existing.add(url.split("/contents/")[1])
        return r


def test_retry_after_open_pr_failure_opens_the_pr():
    fake = FlakyPR(fork_exists=True)
    files = [("data/contrib/rtx-3070/2026-10-29-aaaaaaaa.json", "{}\n")]
    kw = dict(
        title="t",
        body="b",
        branch="genmodelfit-share/rtx-3070/12345678",
        match_prefix="genmodelfit-share/rtx-3070/",
        sleep=lambda s: None,
    )
    with pytest.raises(github.GitHubError):
        github.submit(fake, _creds(), files, **kw)
    fake.fail_pr = False
    res = github.submit(fake, _creds(), files, **kw)
    assert res.uploaded == [] and res.skipped == [files[0][0]] and res.on_main == []
    assert res.pr_url and res.pr_url.endswith("/pull/200")  # ブランチにだけあるので PR を開く
    assert res.delivered == [files[0][0]]


def test_files_already_merged_are_marked_without_a_pr():
    path = "data/contrib/rtx-3070/2026-10-29-aaaaaaaa.json"
    fake = FakeGitHub(fork_exists=True, existing={path}, on_main={path})
    res = github.submit(
        fake,
        _creds(),
        [(path, "{}")],
        title="t",
        body="b",
        branch="genmodelfit-share/rtx-3070/1",
        match_prefix="genmodelfit-share/rtx-3070/",
    )
    assert res.pr_url is None and res.delivered == [path]
    assert "open-pr" not in fake.steps()


def test_cli_marks_nothing_when_the_pr_could_not_be_opened(capsys, monkeypatch):
    _write_sub(1)
    fake = FlakyPR()
    monkeypatch.setattr(cli, "_share_session", lambda: fake)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["share", "--no-measure", "--yes", "--json"]) == 1
    capsys.readouterr()
    assert measure.load_shared() == {}  # PR が無いので印を付けない
    fake.fail_pr = False
    assert cli.main(["share", "--no-measure", "--yes", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["prs"][0]["url"].endswith("/pull/200") and len(measure.load_shared()) == 1


def test_open_pr_match_is_exact_and_paginated():
    def pr(n, ref, owner="alice"):
        return {
            "html_url": f"https://github.com/laxshmate/genmodelfit/pull/{n}",
            "head": {"ref": ref, "user": {"login": owner}, "repo": {"full_name": f"{owner}/genmodelfit"}},
        }

    class Paged(FakeGitHub):
        def request(self, method, url, **kw):
            if method == "GET" and url == f"{UP}/pulls":
                self.calls.append((method, url, kw))
                if kw["params"]["page"] == "1":
                    return Resp(200, [pr(i, f"other/{i}", "bob") for i in range(100)])
                return Resp(
                    200,
                    [
                        pr(300, "genmodelfit-share/rtx-4060-ti/aaaa"),
                        pr(301, "genmodelfit-share/rtx-4060/bbbb"),
                    ],
                )
            return super().request(method, url, **kw)

    hit = github.find_open_pr(Paged(), TOKEN, "alice", "alice/genmodelfit", "genmodelfit-share/rtx-4060/")
    assert hit == ("genmodelfit-share/rtx-4060/bbbb", "https://github.com/laxshmate/genmodelfit/pull/301")

    class Broken(FakeGitHub):
        def request(self, method, url, **kw):
            if url == f"{UP}/pulls":
                return Resp(500, {"message": "oops"})
            return super().request(method, url, **kw)

    with pytest.raises(github.GitHubError):  # 一覧を読めなければ、2 本目を開かずに止める
        github.find_open_pr(Broken(), TOKEN, "alice", "alice/genmodelfit", "genmodelfit-share/x/")


def test_new_fork_waits_for_git_data():
    class Slow(FakeGitHub):
        refs_tries = 0
        put_tries = 0

        def request(self, method, url, **kw):
            if method == "POST" and url.endswith("/git/refs") and self.refs_tries < 2:
                self.refs_tries += 1
                self.calls.append((method, url, kw))
                return Resp(409, {"message": "Git Repository is empty."})
            if method == "PUT" and self.put_tries < 1:
                self.put_tries += 1
                self.calls.append((method, url, kw))
                return Resp(404, {"message": "Not Found"})
            return super().request(method, url, **kw)

    slept = []
    res = github.submit(
        Slow(),
        _creds(),
        [("data/contrib/x/2026-10-29-aaaaaaaa.json", "{}")],
        title="t",
        body="b",
        branch="genmodelfit-share/x/1",
        match_prefix="genmodelfit-share/x/",
        sleep=slept.append,
    )
    # フォークを作ったあと: ブランチで 2 回、置くので 1 回待った(404 を scope の不足と言わない)
    assert res.pr_url and slept[-3:] == [2.0, 4.0, 2.0]


def test_rate_limits(monkeypatch):
    waits = []
    monkeypatch.setattr(github, "_sleep", waits.append)

    class Limited(FakeGitHub):
        def __init__(self, headers, times):
            super().__init__()
            self.h, self.times = headers, times

        def request(self, method, url, **kw):
            if url.endswith("/user") and self.times:
                self.times -= 1
                return Resp(403, {"message": "You have exceeded a secondary rate limit"}, self.h)
            return super().request(method, url, **kw)

    assert github.whoami(Limited({"Retry-After": "3"}, 1), TOKEN) == "alice" and waits == [3.0]
    with pytest.raises(github.GitHubError) as e:
        github.whoami(Limited({"Retry-After": "600"}, 5), TOKEN)
    assert e.value.kind == "rate_limit" and "約 10 分" in str(e.value)
    with pytest.raises(github.GitHubError) as e:
        github.whoami(Limited({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"}, 5), TOKEN)
    assert e.value.kind == "rate_limit"
    assert github._rate_limited(403, {"message": "Resource not accessible by integration"}, {}) is None


# --- トークンの期限(Checker #5) ----------------------------------------------------------------------


def test_token_expired_while_measuring(capsys, monkeypatch, this_pc, plan, gh):
    github.write_cached_token(TOKEN, 28800)
    calls = {"n": 0}
    real = gh.request

    def request(method, url, **kw):
        if url.endswith("/user"):
            calls["n"] += 1
            if calls["n"] > 1:  # 測り終えたら切れていた
                return Resp(401, {"message": "Bad credentials"})
        return real(method, url, **kw)

    gh.request = request
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert "期限が切れました。測った結果は保存してあります" in err and "--no-measure" in err
    assert not github.token_path().exists() and measure.load_shared() == {}
    assert len(measure.saved_submissions()) == 3


# --- 前からの組み込みの形(Checker #10) ----------------------------------------------------------------


def test_json_without_yes_keeps_the_old_listing_and_preview(capsys, no_session):
    p = _write_sub(1)
    assert cli.main(["share", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["listed"] is True and len(d["submissions"]) == 1
    assert cli.main(["share", str(p), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["listed"] is False and d["ok"] is True and d["share"]["issue_url"]


def test_file_with_measure_flags_is_a_usage_error(capsys):
    p = _write_sub(1)
    assert cli.main(["share", str(p), "--no-measure"]) == 2
    assert cli.main(["share", str(p), "--download"]) == 2


def test_old_fork_branches_from_the_common_base(monkeypatch):
    # 前からあるフォークは本家の新しいコミットを持たず、本家の先頭からのブランチは 404。フォークを合わせるのは
    # workflow の scope が要るので、共通の祖先から作る(2026-10-29 の本番)
    fake = FakeGitHub(fork_exists=True, stale=True)
    creds = github.Credentials(token="t", login="alice", source="env")
    r = github.submit(
        fake,
        creds,
        [("data/contrib/rtx-3070/a.json", "{}")],
        title="t",
        body="b",
        branch="genmodelfit-share/rtx-3070/aaaa",
        match_prefix="genmodelfit-share/rtx-3070/",
        sleep=lambda s: None,
    )
    steps = fake.steps()
    assert steps.index("base") < steps.index("branch") and r.pr_url
    ref = next(kw for m, u, kw in fake.calls if u.endswith("/git/refs"))["json"]
    assert ref["sha"] == OLD_BASE
    assert not any(u.endswith("/merge-upstream") for _m, u, _kw in fake.calls)  # フォークの main は変えない
    # 作ったばかりのフォークは本家の先頭から
    fresh = FakeGitHub()
    github.submit(
        fresh,
        creds,
        [("data/contrib/rtx-3070/a.json", "{}")],
        title="t",
        body="b",
        branch="genmodelfit-share/rtx-3070/aaaa",
        match_prefix="genmodelfit-share/rtx-3070/",
        sleep=lambda s: None,
    )
    # 作ったばかりのフォークも同じ決め方(共通の祖先 = 本家の先頭)。POST /forks が別の名前の前からの
    # フォークを返しても、本家の先頭を当て推量しない
    ref = next(kw for m, u, kw in fresh.calls if u.endswith("/git/refs"))["json"]
    assert "base" in fresh.steps() and ref["sha"] == HEAD_SHA


# --- 合計の時間・送り方を選ぶ・測り直さない(#124 のオーナーの決定 2) ------------------------------------


def test_download_text_uses_an_example_line_speed():
    assert cli._download_text(1024**3) == "落とす量 1.0 GB(100 Mbps で約 2 分。回線しだい)"
    assert "約 10 分" in cli._download_text(int(6.5 * 1024**3))


def test_plan_shows_the_total_time(capsys, monkeypatch, this_pc, dl_plan, gh, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    d = venv_install(tmp_path)
    assert cli.main(["--lang", "ja", "share", "--dry-run", "--comfyui-dir", str(d)]) == 0
    out = re.sub(r"\s+", "", capsys.readouterr().out)  # 折り返しの空白を除いて比べる
    assert "合計の目安:約2分(ComfyUIの起動約45秒、測る約60秒)" in out
    assert "落とす量2.0GB(100Mbpsで約3分。回線しだい)" in out
    # --download なら落とす時間も合計に入れる
    assert cli.main(["--lang", "ja", "share", "--dry-run", "--download", "--comfyui-dir", str(d)]) == 0
    out = re.sub(r"\s+", "", capsys.readouterr().out)
    assert "落としたものを測る約60秒、落とす量2.0GB(100Mbpsで約3分" in out


def test_download_question_shows_the_time(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    out = re.sub(r"\s+", "", capsys.readouterr().out)
    assert "落とす量2.0GB(100Mbpsで約3分。回線しだい)、測る時間の目安約60秒。合わせて約4分" in out


def test_choosing_issue_sends_without_signing_in(capsys, monkeypatch, this_pc, plan, gh):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "2")  # 測る → はい、送り方 → 2(Issue)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert asked[1].startswith("送り方:") and len(opened) == 3  # 1 つのファイルに 1 つの Issue
    assert all("issues/new?template=measurement.yml" in u for u in opened)
    assert gh.calls == [] and len(measure.load_shared()) == 3
    out = capsys.readouterr().out
    assert out.count("=== 個人の情報の点検 ===") == 3  # 中身は 1 回だけ見せる


def test_choosing_q_keeps_the_results(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "q")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert "genmodelfit share --no-measure" in err and gh.calls == [] and measure.load_shared() == {}
    assert len(measure.saved_submissions()) == 3
    # 次の share は測り直さず、それを送る
    plan["ran"].clear()
    answers(monkeypatch, "", "1")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--no-measure", "--url", "http://x:1"]) == 0
    assert plan["ran"] == [] and len(measure.load_shared()) == 3


def test_issue_yes_measures_then_prints_issue_urls(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["share", "--issue", "--yes", "--url", "http://x:1"]) == 0
    cap = capsys.readouterr()
    assert plan["ran"] == ["a_installed_short", "c_installed", "e_installed"]
    assert cap.out.count("https://github.com/laxshmate/genmodelfit/issues/new?") == 3 and gh.calls == []
    # URL を出しただけでも印を付ける(二重に送らない)。送らなかったときに測り直さずに選び直せることを知らせる
    assert len(measure.load_shared()) == 3 and "genmodelfit share --issue --all" in cap.err


def test_pr_failure_offers_the_issue_form(capsys, monkeypatch, this_pc, plan):
    import webbrowser

    class Down(FakeGitHub):
        def request(self, method, url, **kw):
            if method == "POST" and url.endswith("/forks"):
                return Resp(500, {"message": "boom"})
            return super().request(method, url, **kw)

    monkeypatch.setattr(cli, "_share_session", lambda: Down())
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "1", "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 1
    assert asked[2].startswith("PR で送れませんでした") and len(opened) == 3
    # 端末でなければ、測り直さずに送る方法を言って止める
    plan["ran"].clear()
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["--lang", "ja", "share", "--yes", "--no-measure", "--all", "--url", "http://x:1"]) == 1
    assert "genmodelfit share --no-measure" in capsys.readouterr().err


def test_existing_credentials_are_checked_before_measuring(capsys, monkeypatch, this_pc, plan, gh):
    github.write_cached_token(TOKEN, 28800)
    events = []
    real = gh.request
    gh.request = lambda method, url, **kw: events.append(url.rsplit("/", 1)[-1]) or real(method, url, **kw)
    real_run = cli._measure_run
    monkeypatch.setattr(cli, "_measure_run", lambda *a, **k: events.append("run") or real_run(*a, **k))
    assert cli.main(["share", "--yes", "--url", "http://x:1"]) == 0
    assert events[0] == "user" and events.index("run") < events.index("forks")


def test_saved_results_are_not_measured_again(db):
    from genmodelfit.models import Gpu, SystemInfo

    g = Gpu(name="NVIDIA GeForce RTX 3070", vendor="nvidia", vram_total_gb=8.0, backend="cuda")
    pc = SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    before = measure.suggestions(db, pc)
    target = next(x for x in before if x.settings is None)
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"].update(model_id=target.model_id, settings={}, note=None)
    d["measurement"].pop("files_fp", None)
    contrib.write_submission(d, str(measure.submissions_dir()) + "/")
    after = measure.suggestions(db, pc, extra=cli._saved_measurements())
    assert target.model_id not in [x.model_id for x in after if x.settings is None]
    # 送った印を付けても(まだ一覧に取り込まれていなくても)測り直さない
    measure.mark_shared(d)
    again = measure.suggestions(db, pc, extra=cli._saved_measurements())
    assert target.model_id not in [x.model_id for x in again if x.settings is None]


@pytest.fixture
def db():
    from genmodelfit.db import load as load_db

    return load_db(use_cache=False)


def test_measure_flushes_the_screen_before_starting(capsys, monkeypatch, this_pc, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    d = venv_install(tmp_path)
    flushed = []
    real_flush = sys.stdout.flush
    monkeypatch.setattr(sys.stdout, "flush", lambda: flushed.append(True) or real_flush())

    def start(args, plan, holder):
        assert flushed, "画面を出し切る前に起動の途中経過を出した"
        raise cli.CliError("stop here")

    monkeypatch.setattr(cli, "_comfyui_start", start)
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: 0)
    assert cli.main(["measure", "image_sdxl_simple", "--comfyui-dir", str(d), "--yes"]) == 1


# --- 2 回目の確かめ(#124): 重ねて送らない・同意した一覧の外を落とさない・保存したログイン ------------------


def test_pr_failure_offers_issues_only_for_files_not_placed(capsys, monkeypatch, this_pc, plan):
    import webbrowser

    open_pr = {
        "html_url": "https://github.com/laxshmate/genmodelfit/pull/150",
        "head": {
            "ref": "genmodelfit-share/rtx-3070/old00000",
            "user": {"login": "alice"},
            "repo": {"full_name": "alice/genmodelfit"},
        },
    }

    class Mid(FakeGitHub):
        puts = 0

        def request(self, method, url, **kw):
            if method == "PUT":
                self.puts += 1
                if self.puts == 2:  # 2 つ目を置くところで落ちる
                    return Resp(500, {"message": "boom"})
            return super().request(method, url, **kw)

    fake = Mid(fork_exists=True, open_prs=[open_pr])
    monkeypatch.setattr(cli, "_share_session", lambda: fake)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "1", "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 1
    cap = capsys.readouterr()
    # 開いている PR に 1 つ置けた: それは送った印を付け、Issue では残りの 2 つだけ
    assert "送れた PR: https://github.com/laxshmate/genmodelfit/pull/150" in cap.out
    assert len(opened) == 2 and len(measure.load_shared()) == 3
    assert "1 件はもう PR かブランチに置いたので" in cap.err


def test_cached_token_that_no_longer_works_is_removed_and_issue_still_works(
    capsys, monkeypatch, this_pc, plan
):
    import webbrowser

    github.write_cached_token(TOKEN, 28800)
    fake = FakeGitHub(user_ok=False)
    monkeypatch.setattr(cli, "_share_session", lambda: fake)
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "2")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert (
        "使えなくなったので消しました(PR を選んだときにログインします)" in err
        and "ログインし直します" not in err
    )
    assert not github.token_path().exists() and len(plan["ran"]) == 3


def test_downloads_stay_within_the_approved_list(capsys, monkeypatch, this_pc, starter_env, tmp_path):
    from test_comfystart import venv_install

    d = venv_install(tmp_path)
    shown = [sugg("dl_a", 60, "no", 1024**3), sugg("dl_b", 60, "no", 1024**3)]
    later = [sugg("dl_c", 30, "no", 1024**3)]
    calls = {"n": 0}

    def suggestions(*a, **k):
        calls["n"] += 1
        return shown + later

    monkeypatch.setattr(measure, "suggestions", suggestions)
    # 起動したあと、dl_a は足りないノードで外れ、空いた時間に dl_c が入りうる
    monkeypatch.setattr(
        cli, "_node_check", lambda url: SimpleNamespace(missing=lambda mid: ["X"] if mid == "dl_a" else [])
    )
    got = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: got.append(mid) or 0)
    monkeypatch.setattr(cli, "_measure_run", lambda *a, **k: {"error": "x"})
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_share_session", lambda: FakeGitHub())
    assert (
        cli.main(["--lang", "ja", "share", "--yes", "--download", "--budget", "120", "--comfyui-dir", str(d)])
        == 1
    )
    assert got == ["dl_b"]  # dl_c は見せていないので落とさない
    assert "落とさないもの: dl_a [dl_a](ComfyUI に無いノードが要る)" in capsys.readouterr().err


@pytest.fixture
def starter_env(monkeypatch):
    from genmodelfit import comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(
        comfystart,
        "launch",
        lambda plan, **k: comfystart.Started(
            plan, SimpleNamespace(pid=1, poll=lambda: None), 40200, Path("c.log")
        ),
    )
    monkeypatch.setattr(comfystart, "wait_ready", lambda st, **k: None)
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: True)


def test_issue_hints_point_to_share_issue(capsys, monkeypatch):
    _write_sub(1)
    _write_sub(2)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli.main(["--lang", "ja", "share", "--issue"]) == 0
    assert "genmodelfit share --issue <ファイル>" in capsys.readouterr().err
    assert cli.main(["--lang", "ja", "share", "--issue", "--print-url"]) == 1
    assert "genmodelfit share --issue <ファイル> --print-url" in capsys.readouterr().err


# --- 実機の 2 回目(#124): 続けて測るときは毎回空ける・測れたが送れないものも黙って落とさない --------------


def test_share_frees_before_every_run_and_says_so(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert plan["free"] == [True, True, True]
    out = capsys.readouterr().out
    assert "測る前に毎回 ComfyUI のメモリを空けます(読み込み済みのモデルは外れます)" in out


def test_nonstandard_result_is_listed_not_dropped(capsys, monkeypatch, this_pc, plan, gh):
    def run(args, url, entry, settings, free=False, progress=None):
        m = SimpleNamespace(outcome="ok", peak_vram_gb=0.7, seconds=7.7, standard=False)
        res = SimpleNamespace(
            warnings=["3 個のノードが前の結果を使い、実行されませんでした"],
            nonstandard=["前の結果を使ったノードが 3 個(一部だけ実行)"],
            failed=None,
        )
        return {"error": None, "result": res, "measurement": m, "submission": None}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--json", "--url", "http://x:1"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert d["measured"] == [] and len(d["failed"]) == 3
    assert (
        "標準でない条件で測ったので、保存も送りもしません: 前の結果を使ったノードが 3 個"
        in d["failed"][0]["error"]
    )
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    out = capsys.readouterr().out
    assert "測れなかったもの: 3 件" in out and "送れません: 標準でない条件" in out


def test_measure_frees_a_started_comfyui_or_with_free(capsys, monkeypatch, this_pc, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    seen = []

    def run(args, url, entry, settings, free=False, progress=None):
        seen.append(free)
        return {"error": "stop"}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setattr(cli, "_measure_download", lambda *a, **k: 0)
    assert cli.main(["measure", "image_sdxl_simple", "--yes", "--url", "http://x:1"]) == 1
    assert cli.main(["measure", "image_sdxl_simple", "--yes", "--free", "--url", "http://x:1"]) == 1
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(
        comfystart,
        "launch",
        lambda plan, **k: comfystart.Started(
            plan, SimpleNamespace(pid=1, poll=lambda: None), 40300, Path("c.log")
        ),
    )
    monkeypatch.setattr(comfystart, "wait_ready", lambda st, **k: None)
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: True)
    assert (
        cli.main(["measure", "image_sdxl_simple", "--yes", "--comfyui-dir", str(venv_install(tmp_path))]) == 1
    )
    assert seen == [False, True, True]  # 利用者の ComfyUI は --free のときだけ、起動したものはいつも


def test_measure_run_passes_free_to_bench(monkeypatch):
    from genmodelfit import bench

    got = []

    def run(opts, log=None, progress=None):
        got.append(opts.free_before)
        raise bench.BenchError("x")

    monkeypatch.setattr(bench, "run", run)
    e = SimpleNamespace(id="image_sdxl_simple", precision="fp16", files=[])
    args = cli.build_parser().parse_args(["share"])
    assert cli._measure_run(args, "http://x:1", e, None, free=True)["error"] == "x"
    assert cli._measure_run(args, "http://x:1", e, None)["error"] == "x"
    assert got == [True, False]


def test_deviation_uses_the_measured_settings(monkeypatch):
    from genmodelfit.db import load as load_db

    estimate = sys.modules["genmodelfit.estimate"]  # gmf.estimate は関数なのでモジュールを直に取る

    seen = []
    real = estimate.formula_need

    def spy(entry, settings, *a, **k):
        seen.append((settings.width, settings.height))
        return real(entry, settings, *a, **k)

    monkeypatch.setattr(estimate, "formula_need", spy)
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"].update(settings={"width": 512, "height": 512, "steps": 25}, settings_override=True)
    contrib.validate_submission(d, load_db(use_cache=False), check_filename=False)
    assert (512, 512) in seen and (1024, 1024) not in seen


# --- 途中経過の表示(#124。rich。ライブラリは表示しない) ------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _rendered_lines(text: str) -> list[str]:
    from rich.cells import cell_len

    out = []
    for raw in re.split(r"[\r\n]", _ANSI.sub("", text)):
        if raw.strip():
            out.append((raw, cell_len(raw)))
    return out


def _term(width: int = 80):
    import io

    from rich.console import Console

    return Console(force_terminal=True, file=io.StringIO(), width=width, color_system=None)


def test_views_render_in_a_terminal_within_the_width():
    from genmodelfit.downloader import DownloadProgress

    con = _term()
    v = cli._StartView(45, 300, console=con)
    v.update(12, "Starting server " + "x" * 200)
    v.update(200, "まだ読み込んでいます" * 20)  # 目安の 3 倍を超えた
    v.done(31)
    m = cli._MeasureView("測る 1/2: SDXL1.0 Base 768×768" + "長い名前" * 30, 20, console=con)
    m.update({"phase": "running", "elapsed": 5, "peak_vram_gb": 1.2})
    m.update({"phase": "running", "elapsed": 70, "peak_vram_gb": 1.3})  # 目安を超えた
    m.done()
    d = cli._DownloadView(console=con)
    for phase, n in (("check", 0), ("download", 3 * 1024**3), ("verify", 6 * 1024**3), ("done", 6 * 1024**3)):
        d(
            DownloadProgress(
                phase,
                1,
                1,
                "checkpoints/" + "very_long_model_name_" * 8 + ".safetensors",
                n,
                6 * 1024**3,
                n,
                6 * 1024**3,
            )
        )
    d.close()
    text = con.file.getvalue()
    lines = _rendered_lines(text)
    assert lines and all(w <= 80 for _l, w in lines)
    flat = _ANSI.sub("", text)
    assert "ComfyUI の準備ができました(31 秒)" in flat and "目安より長くかかっています" in flat
    assert "目安を超えています" in flat and "確かめています" in flat and "全体" in flat


def test_views_are_plain_lines_without_a_terminal(capsys):
    import io

    from rich.console import Console

    from genmodelfit.downloader import DownloadProgress

    plain = Console(file=io.StringIO(), force_terminal=False)
    t = iter(range(0, 1000, 3))
    v = cli._StartView(45, 300, console=plain, clock=lambda: next(t))
    for w in range(0, 30, 3):
        v.update(w, "log line")
    v.done(31)
    err = capsys.readouterr().err.splitlines()
    # 約 10 秒ごとに 1 行と、終わりの 1 行だけ(書き直しの制御文字は出さない)
    assert len(err) == 4 and err[-1] == "ComfyUI の準備ができました(31 秒)" and "\r" not in "".join(err)
    d = cli._DownloadView(console=plain)
    total = 10 * 1024**3
    for n in range(0, total + 1, total // 20):
        d(DownloadProgress("download", 1, 1, "checkpoints/m.safetensors", n, total, n, total))
    d(DownloadProgress("done", 1, 1, "checkpoints/m.safetensors", total, total, total, total))
    err = capsys.readouterr().err.splitlines()
    assert 10 <= len(err) <= 13 and err[0].startswith("[1/1] 取得 checkpoints/m.safetensors")
    assert err[-1].startswith("[1/1] 済み")


def test_json_shows_no_progress(capsys):
    v = cli._MeasureView("x", 10, enabled=False)
    v.update({"phase": "running", "elapsed": 1})
    v.done()
    assert capsys.readouterr() == ("", "")


def test_share_prints_numbered_steps(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    out = capsys.readouterr().out
    heads = re.findall(r"== 手順 (\d+)/(\d+): (.+?) ==", out)
    assert heads == [
        ("1", "4", "GitHub の確認"),
        ("2", "4", "測る(3 件)"),
        ("3", "4", "送る中身を見せる"),
        ("4", "4", "送る"),
    ]
    assert "測っています 1/3: a_installed_short" in out and "— 残り 2 件、あと約 220 秒" in out


def test_steps_grow_when_downloading_is_decided_later(capsys):
    s = cli._Steps([("measure", "測る(1 件)"), ("show", "送る中身を見せる"), ("send", "送る")])
    s.start("measure")
    s.start("download", "落として測る(1 件)")
    out = capsys.readouterr().out
    assert "== 手順 2/4: 落として測る(1 件) ==" in out and "手順が 1 つ増えて 4 になりました" in out


def test_bench_reports_progress(monkeypatch):
    from test_bench import FakeComfy

    from genmodelfit import bench

    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(
        bench,
        "smi_gpus",
        lambda: [{"index": 0, "uuid": "GPU-a", "name": "NVIDIA GeForce RTX 3070", "driver": "1"}],
    )
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    seen = []
    with FakeComfy() as fake:
        opts = bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60)
        bench.run(opts, progress=seen.append)
    assert seen and all({"phase", "elapsed", "peak_vram_gb"} <= set(x) for x in seen)
    assert {x["phase"] for x in seen} <= {"queued", "running", "waiting"}


# --- 50 の場面の確かめ(#124 の Tester)で見つかったことと、足りなかった試験 ----------------------------------


def _bench_gpu(monkeypatch, apps=None):
    from genmodelfit import bench

    gpu = {"index": 0, "uuid": "GPU-a", "name": "NVIDIA GeForce RTX 3070", "driver": "1"}
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(bench, "smi_gpus", lambda: [gpu])
    monkeypatch.setattr(bench, "smi_compute_apps", apps or (lambda: []))
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench, "sample_gpu", lambda gpu_id, **k: [(1024.0, 0.0)])
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    return bench


def test_A5_method_question_asks_again_on_other_input(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "x", "")  # 測る → はい、送り方 → x(聞き直す)→ Enter(既定 1)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert [a.startswith("送り方:") for a in asked] == [False, True, True] and len(gh.put) == 3
    assert "1・2・q で選んでください" in capsys.readouterr().err


def test_A6_ctrl_c_at_the_first_question_does_nothing(
    capsys, monkeypatch, this_pc, plan, gh, starter_env, tmp_path
):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.setattr(comfystart, "launch", lambda *a, **k: pytest.fail("起動しない"))
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def ctrl_c(p=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", ctrl_c)
    assert cli.main(["share", "--comfyui-dir", str(venv_install(tmp_path))]) == 130
    assert plan["ran"] == [] and gh.calls == []


def test_B5_partly_failed_sends_the_rest(capsys, monkeypatch, this_pc, plan, gh):
    real = cli._measure_run

    def run(args, url, entry, settings, **k):
        if entry.id == "c_installed":
            return {"error": "ValueError: boom"}
        return real(args, url, entry, settings, **k)

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    out = capsys.readouterr().out
    assert len(gh.put) == 2 and "測れなかったもの: 1 件" in out and "c_installed: ValueError: boom" in out


@pytest.mark.parametrize(
    ("argv", "setup", "want"),
    [
        (["--budget", "5"], None, "時間の上限(5 秒)に収まるものがありません"),
        (["--no-measure"], None, "送っていない実測はありません"),
        ([], "empty", "この PC に入っていて、GPU に載り測れるモデルがありません"),
    ],
)
def test_B6_B7_B8_nothing_to_do_says_why(capsys, monkeypatch, this_pc, plan, gh, argv, setup, want):
    if setup == "empty":
        plan["sugg"] = []
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1", *argv]) == 0
    assert want in capsys.readouterr().out and gh.calls == [] and plan["ran"] == []


def test_C1_another_program_starting_on_the_gpu_makes_it_nonstandard(monkeypatch):
    from test_bench import FakeComfy

    n = {"calls": 0}

    def apps():
        n["calls"] += 1
        return [(1, "GPU-a")] if n["calls"] == 1 else [(1, "GPU-a"), (4242, "GPU-a"), (7, "GPU-other")]

    bench = _bench_gpu(monkeypatch, apps)
    monkeypatch.setattr(bench, "GPU_APPS_EVERY", 0.0)
    # 計算のプロセス(種類 C)が現れた: 数値が狂う
    monkeypatch.setattr(bench, "smi_process_types", lambda: {1: "C+G", 4242: "C"})
    with FakeComfy() as fake:
        opts = bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60, free_before=True)
        res = bench.run(opts)
    assert res.measurement.standard is False
    assert any("ほかのプログラムが GPU を使い始めた" in x and "4242" in x for x in res.nonstandard)
    assert not any("PID 7" in x for x in res.nonstandard)
    # 画面のアプリ(C+G)が開いただけで、GPU 全体の使用量が増えていない: 断らない(共用の PC)
    n["calls"] = 0
    monkeypatch.setattr(bench, "smi_process_types", lambda: {1: "C+G", 4242: "C+G"})
    with FakeComfy() as fake:
        opts = bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60, free_before=True)
        res = bench.run(opts)
    assert res.measurement.standard is True and res.nonstandard == []


def test_C3_other_programs_using_vram_stop_the_run(monkeypatch):
    from test_bench import FakeComfy

    bench = _bench_gpu(monkeypatch)
    with FakeComfy() as fake:
        fake.other_apps = 6 * 1024**3
        opts = bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60, free_before=True)
        with pytest.raises(bench.BenchError) as e:
            bench.run(opts)
        assert not fake.submitted
    assert "ほかのプログラムが VRAM を約" in str(e.value)


def test_C3_before_starting_comfyui(capsys, monkeypatch, this_pc, plan, gh, starter_env, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import bench, comfystart

    monkeypatch.setattr(bench, "vram_in_use", lambda name=None: (int(5.8 * 1024**3), 8 * 1024**3))
    monkeypatch.setattr(comfystart, "launch", lambda *a, **k: pytest.fail("起動しない"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--comfyui-dir", str(venv_install(tmp_path))]) == 1
    err = capsys.readouterr().err
    assert "ほかのプログラムが VRAM を約 5.8 GB 使っています" in err and plan["ran"] == []


def test_C5_timeout_hint():
    hint = cli._measure_next_step("1800 秒で終わらなかったので中止しました", "x")
    assert "--timeout" in hint and "起動していて" not in hint


def test_D1_running_comfyui_is_not_stopped(capsys, monkeypatch, this_pc, plan, gh):
    from genmodelfit import comfystart

    monkeypatch.setattr(comfystart, "stop", lambda *a, **k: pytest.fail("もとから動いていたものは止めない"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert "測る前に毎回 ComfyUI のメモリを空けます" in capsys.readouterr().out


def test_D8_comfyui_dying_is_restarted_once(capsys, monkeypatch, this_pc, plan, gh, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    launched = []

    class Proc:
        def __init__(self):
            self.pid, self.code = 1, None

        def poll(self):
            return self.code

    def launch(p, **k):
        launched.append(Proc())
        return comfystart.Started(p, launched[-1], 40400 + len(launched), tmp_path / "c.log")

    (tmp_path / "c.log").write_text("Traceback\nCUDA error\n", encoding="utf-8")
    monkeypatch.setattr(comfystart, "launch", launch)
    monkeypatch.setattr(comfystart, "wait_ready", lambda st, **k: None)
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: True)
    urls = []

    def run(args, url, entry, settings, **k):
        urls.append(url)
        launched[-1].code = 1  # 測っている途中で ComfyUI が落ちる(毎回)
        return {"error": "ComfyUI(...)に接続できません(止まったか、起動していません)"}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--comfyui-dir", str(venv_install(tmp_path))]) == 1
    err = capsys.readouterr().err
    # 1 回目で落ちる → 起動し直して 2 件目 → また落ちる → やめて送るかに進む(3 件目は測らない)
    assert len(launched) == 2 and urls == ["http://127.0.0.1:40401", "http://127.0.0.1:40402"]
    assert err.count("ComfyUI が止まりました") == 2 and "CUDA error" in err
    assert "ComfyUI を起動し直して続けます" in err and "HTTPConnectionPool" not in err
    assert "ブラウザで開けることを確かめて" not in err  # 止まったときは「起動を確かめて」の案内を出さない


def test_E2_ctrl_c_while_downloading_in_share_stops(capsys, monkeypatch, this_pc, dl_plan, gh):
    calls = []
    monkeypatch.setattr(cli, "_measure_download", lambda args, mid, **k: calls.append(mid) or 130)
    dl_plan["sugg"].append(sugg("z_not_installed", 30, "no", 1024**3))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    # Ctrl+C で止めたので 130(測った分は送る)
    assert cli.main(["--lang", "ja", "share", "--yes", "--download", "--url", "http://x:1"]) == 130
    err = capsys.readouterr().err
    assert calls == ["b_not_installed"] and "止めました。そこまでの実測を送るかに進みます" in err
    assert dl_plan["ran"] == ["a_installed_short"] and len(gh.put) == 1  # 測った分は送る


def test_F7_network_down_before_measuring_warns_in_a_terminal(capsys, monkeypatch, this_pc, plan):
    import requests

    github.write_cached_token(TOKEN, 28800)

    class Down(FakeGitHub):
        def request(self, method, url, **kw):
            raise requests.ConnectionError("no route")

    monkeypatch.setattr(cli, "_share_session", lambda: Down())
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "q")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert "GitHub に繋がりません" in err and "Issue でも送れます" in err and len(plan["ran"]) == 3


@pytest.mark.parametrize("stage", ["start", "measure", "method", "send", "login"])
def test_I1_ctrl_c_at_every_stage(capsys, monkeypatch, this_pc, plan, gh, tmp_path, stage):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    stopped = []
    monkeypatch.setattr(
        comfystart,
        "launch",
        lambda p, **k: comfystart.Started(
            p, SimpleNamespace(pid=1, poll=lambda: None), 40500, tmp_path / "c.log"
        ),
    )

    def ready(st, **k):
        if stage == "start":
            raise KeyboardInterrupt

    monkeypatch.setattr(comfystart, "wait_ready", ready)
    monkeypatch.setattr(comfystart, "stop", lambda st, **k: stopped.append(st) or True)
    real_run = cli._measure_run

    def run(*a, **k):
        if stage == "measure" and plan["ran"]:
            raise KeyboardInterrupt
        return real_run(*a, **k)

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_measure_run", run)
    if stage == "send":
        monkeypatch.setattr(cli, "_share_submit", boom)
    if stage == "login":
        monkeypatch.setattr(cli, "_share_login", boom)
    else:
        monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def ask(p=""):
        if stage == "method" and p.startswith("送り方"):
            raise KeyboardInterrupt
        return ""

    monkeypatch.setattr("builtins.input", ask)
    code = cli.main(["--lang", "ja", "share", "--comfyui-dir", str(venv_install(tmp_path))])
    err = capsys.readouterr().err
    assert stopped, "起動した ComfyUI は止める"
    assert "Traceback" not in err
    if stage in ("start", "measure"):
        assert "止めました。そこまでの実測を送るかに進みます" in err and code == 130
    if stage in ("method", "send", "login"):
        assert code == 130 and "genmodelfit share --no-measure" in err
    assert len(measure.saved_submissions(include_shared=True)) == len(plan["ran"])  # 測った分は残る


def test_K1_no_measure_numbers_only_the_steps_that_run(capsys, monkeypatch, gh):
    _write_sub(1)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--no-measure"]) == 0
    heads = re.findall(r"== 手順 (\d+)/(\d+): (.+?) ==", capsys.readouterr().out)
    assert heads == [("1", "2", "送る中身を見せる"), ("2", "2", "送る")]


def test_J2_declining_the_download_in_measure_is_not_an_error(capsys, monkeypatch, this_pc, tmp_path):
    from test_comfystart import _comfy

    def declined(ns):
        ns._declined = True
        return 1

    monkeypatch.setattr(cli, "cmd_download", declined)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "")
    d = _comfy(tmp_path / "ComfyUI")
    argv = ["--lang", "ja", "measure", "image_sdxl_simple", "--url", "http://x:1", "--comfyui-dir", str(d)]
    assert cli.main(argv) == 0
    assert "やめました(落とさないので測りません)" in capsys.readouterr().out


def test_folder_paths_shapes_of_comfyui_versions(tmp_path):
    from genmodelfit import comfyfind

    base = tmp_path / "ComfyUI"
    for d in ("main.py", "nodes.py"):
        (base / d).parent.mkdir(parents=True, exist_ok=True)
        (base / d).write_text("")
    for d in ("comfy", "comfy_extras", "comfy_api", "models/checkpoints", "models/vae", "custom_nodes"):
        (base / d).mkdir(parents=True, exist_ok=True)
    ck, vae, cn = (
        str(base / "models" / "checkpoints"),
        str(base / "models" / "vae"),
        str(base / "custom_nodes"),
    )
    shapes = [
        {"checkpoints": ck, "vae": vae, "custom_nodes": cn},  # 古い版: 文字列
        {"checkpoints": [ck, "D:/other"], "vae": [vae], "custom_nodes": [cn]},  # 0.19: パスの並び
        {"checkpoints": [[ck], [".safetensors"]], "vae": [[vae], []], "custom_nodes": [[cn], []]},
    ]
    for data in shapes:
        norm = comfyfind.normalize_folder_paths(data)
        assert norm == {"checkpoints": ck, "vae": vae, "custom_nodes": cn}
        c = comfyfind.from_folder_paths(norm, 8188)
        assert c is not None and c.path == base
    assert comfyfind.normalize_folder_paths({"x": [], "y": 3}) is None


def test_unsendable_result_is_kept_but_not_offered(capsys, monkeypatch, this_pc):
    from genmodelfit import bench
    from genmodelfit.models import Measurement

    def run(opts, log=None, progress=None):
        m = Measurement(
            model_id=opts.model_id,
            gpu_name="NVIDIA GeForce RTX 3070",
            gpu_vram_gb=8.0,
            # GPU とメインメモリを合わせた量より大きい(ありえない値。#132 から、GPU より大きいだけなら
            # spilled の印を付けて送れる: test_spilled_result_is_sent_with_a_mark)
            peak_vram_gb=30.0,
            seconds=30.0,
            outcome="ok",
            vram_metric="torch_reserved",
            offload="dynamic",
        )
        return bench.BenchResult(measurement=m, workflow_source="t", warnings=[])

    monkeypatch.setattr(bench, "run", run)
    db = cli._db(cli.build_parser().parse_args(["measure"]))
    e = next(m for m in db.models if m.id == "image_sdxl_simple")
    out = cli._measure_run(cli.build_parser().parse_args(["share"]), "http://x:1", e, None)
    assert out["submission"] is None and out["unsendable"] and out["saved_to"] is not None
    assert measure.saved_submissions() == []  # 送れないものは「送っていない実測」にしない(毎回失敗しない)


def test_existing_unsendable_submission_is_retired_once(capsys, monkeypatch, gh):
    d = json.loads(GOOD[0].read_text(encoding="utf-8"))
    d["measurement"]["note"] = "C:" + chr(92) + "Users" + chr(92) + "bob" + chr(92) + "ComfyUI"
    bad = contrib.write_submission(d, str(measure.submissions_dir()) + "/")
    good = _write_sub(1)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--no-measure"]) == 0  # 送れるものは送り、失敗にしない
    err = capsys.readouterr().err
    assert "送れないので、次から出しません" in err and Path(str(bad) + ".unsendable").exists()
    assert len(gh.put) == 1 and good.exists()
    assert cli.main(["--lang", "ja", "share", "--yes", "--no-measure"]) == 0
    assert "送っていない実測はありません" in capsys.readouterr().out


def test_license_unknown_models_are_not_suggested_by_default():
    from genmodelfit.db import load as load_db
    from genmodelfit.models import Gpu, SystemInfo

    db = load_db(use_cache=False)
    g = Gpu(name="NVIDIA GeForce RTX 4090", vendor="nvidia", vram_total_gb=24.0, backend="cuda")
    pc = SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=64, ram_available_gb=64, gpus=[g])
    by_id = {m.id: m for m in db.models}
    default = measure.suggestions(db, pc, variants_per_model=None)
    assert not any(by_id[x.model_id].license_unknown for x in default)
    with_unknown = measure.suggestions(db, pc, variants_per_model=None, include_unknown_license=True)
    assert len(with_unknown) >= len(default)


def test_non_nvidia_gpu_measures(capsys, monkeypatch, plan, gh):
    """AMD・Intel・Apple の GPU でも測る(0.13.1。0.13.0 は誤って「NVIDIA だけ」で止めた)。
    本物の bench を通す場面は tests/test_measure_vendors.py。"""
    from genmodelfit.models import Gpu, SystemInfo

    g = Gpu(name="AMD Radeon RX 7800 XT", vendor="amd", vram_total_gb=16.0, backend="rocm")
    s = SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    monkeypatch.setattr(cli, "_system", lambda args: s)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert "NVIDIA" not in capsys.readouterr().err
    assert plan["ran"] == ["a_installed_short", "c_installed", "e_installed"] and len(gh.put) == 3


def test_J3_and_D5_hints(capsys, monkeypatch, this_pc, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    monkeypatch.setattr(cli, "_node_check", lambda url: SimpleNamespace(missing=lambda m: ["NewNode"]))
    assert cli.main(["--lang", "ja", "measure", "image_sdxl_simple", "--yes", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert "NewNode" in err and "--workflow" not in err
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    d = venv_install(tmp_path)
    argv = ["--lang", "ja", "measure", "image_sdxl_simple", "--no-start", "--comfyui-dir", str(d)]
    assert cli.main(argv) == 1
    assert "--no-start なので ComfyUI を起動しません" in capsys.readouterr().err


def test_stderr_comes_after_what_was_printed(monkeypatch):
    order = []

    class Out:
        def write(self, t):
            order.append(("out", t))

        def flush(self):
            order.append(("flush", ""))

    class Err:
        def write(self, t):
            order.append(("err", t))

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Out())
    monkeypatch.setattr(sys, "stderr", Err())
    assert cli.main(["share", "--print-url"]) == 2
    first_err = next(i for i, (k, _t) in enumerate(order) if k == "err")
    assert order[first_err - 1][0] == "flush"


def test_sending_reports_each_file():
    logs = []
    github.submit(
        FakeGitHub(),
        _creds(),
        [
            ("data/contrib/x/2026-10-29-aaaaaaaa.json", "{}"),
            ("data/contrib/x/2026-10-29-bbbbbbbb.json", "{}"),
        ],
        title="t",
        body="b",
        branch="genmodelfit-share/x/1",
        match_prefix="genmodelfit-share/x/",
        log=logs.append,
        sleep=lambda s: None,
    )
    assert "送っています 1/2: data/contrib/x/2026-10-29-aaaaaaaa.json" in logs and "PR を開いています" in logs


# --- L: 追加のエラーの場面(#124。つながる先ごと) ---------------------------------------------------------


def test_L1a_windows_job_object_and_posix_parent_death(monkeypatch, tmp_path):
    from test_comfystart import FakeProc, venv_install

    from genmodelfit import comfystart

    plan_ = comfystart.plan_start(venv_install(tmp_path))
    jobs = []
    monkeypatch.setattr(comfystart, "kill_on_close_job", lambda proc: jobs.append(proc) or "job")
    monkeypatch.setattr(sys, "platform", "win32")
    st = comfystart.launch(plan_, port=1, log=tmp_path / "a.log", popen=lambda argv, **kw: FakeProc())
    assert jobs == [st.process] and st._job == "job"  # 端末を閉じても OS が子を止める(KILL_ON_JOB_CLOSE)
    comfystart.stop(st, run=lambda *a, **k: SimpleNamespace(returncode=0))
    seen = {}
    monkeypatch.setattr(sys, "platform", "linux")
    st = comfystart.launch(
        plan_, port=1, log=tmp_path / "b.log", popen=lambda argv, **kw: seen.update(kw) or FakeProc()
    )
    assert seen["start_new_session"] is True and seen["preexec_fn"] is comfystart._die_with_parent
    st.stopped = True
    # Job Object を作れない環境(ハンドルが無い)でも落ちない
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.undo()
    assert comfystart.kill_on_close_job(SimpleNamespace()) is None


def test_L1a_hangup_is_handled_like_ctrl_c(monkeypatch):
    import signal

    calls = []
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    monkeypatch.setattr(signal, "signal", lambda sig, h: calls.append((sig, h)) or "old")
    assert cli.main(["share", "--print-url"]) == 2
    assert calls[0] == (1, cli._hangup) and calls[-1] == (1, "old")  # 入れて、終わったら戻す
    with pytest.raises(KeyboardInterrupt):
        cli._hangup(1, None)


def test_L1b_sleep_gap_does_not_count_as_waiting(monkeypatch):
    from test_bench import FakeComfy

    bench = _bench_gpu(monkeypatch)
    monkeypatch.setattr(bench, "SLEEP_GAP", -1.0)  # 毎回スリープしていたことにする
    with FakeComfy() as fake:
        opts = bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60, free_before=True)
        res = bench.run(opts)
    assert "PC がスリープした" in res.nonstandard and res.measurement.standard is False


def test_L1c_only_one_measuring_run_at_a_time(capsys, monkeypatch, this_pc, plan, gh):
    from genmodelfit.db import cache_dir

    lock = cache_dir() / cli.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getppid()), encoding="ascii")  # 生きているほかのプロセス
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    assert "ほかの genmodelfit" in capsys.readouterr().err and plan["ran"] == []
    lock.write_text("999999999", encoding="ascii")  # もう無いプロセス(古い印)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert len(plan["ran"]) == 3 and not lock.exists()  # 終わったら消す


def test_L2a_L2b_nvidia_smi_odd_output_and_several_gpus(monkeypatch):
    from genmodelfit import bench

    monkeypatch.setattr(bench, "_smi_query", lambda args: "garbage\n,,\n")
    assert bench.vram_in_use() is None and bench.smi_gpus() == []
    monkeypatch.setattr(bench, "_smi_query", lambda args: None)  # 無い・答えない
    assert bench.vram_in_use() is None
    out = "0, NVIDIA GeForce RTX 4090, 1000, 24564\n1, NVIDIA GeForce RTX 3070, 3000, 8192\n"
    monkeypatch.setattr(bench, "_smi_query", lambda args: out)
    assert bench.vram_in_use() == (1000 * 1024**2, 24564 * 1024**2)  # いちばん大きいもの
    assert bench.vram_in_use("NVIDIA GeForce RTX 3070") == (3000 * 1024**2, 8192 * 1024**2)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert bench.vram_in_use("NVIDIA GeForce RTX 4090")[1] == 8192 * 1024**2  # ComfyUI が使うのは 1 番


def test_L3a_no_second_comfyui_on_the_same_folder(monkeypatch, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfyfind, comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(comfyfind, "comfy_port_status", lambda *a, **k: (8000, 200))
    args = cli.build_parser().parse_args(["share", "--comfyui-dir", str(venv_install(tmp_path))])
    plan_, why = cli._comfyui_start_plan(args, None)
    assert plan_ is None and "--url http://127.0.0.1:8000" in why and "2 台目は起動しません" in why


def test_L3d_comfyui_behind_authentication():
    from genmodelfit import bench

    class S:
        def request(self, method, url, **kw):
            return SimpleNamespace(status_code=401, json=lambda: {})

    with pytest.raises(bench.BenchError) as e:
        bench.ComfyClient("http://x:1", session=S()).system_stats()
    assert "ログイン(認証)" in str(e.value)


def test_L3e_user_jobs_in_the_queue():
    from test_bench import FakeComfy

    from genmodelfit import bench

    with FakeComfy("busy") as fake:
        with pytest.raises(bench.BenchError) as e:
            bench.run(bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60, force=True))
        assert not fake.submitted
    assert "キュー" in str(e.value) and "キューの仕事が終わるのを待って" in cli._measure_next_step(
        str(e.value), "x"
    )


def test_L3f_L3g_L3h_node_error_cancel_or_full_disk_is_listed(capsys, monkeypatch, this_pc, plan, gh):
    def run(args, url, entry, settings, **k):
        m = SimpleNamespace(outcome="error", peak_vram_gb=1.0, seconds=3.0, standard=True)
        res = SimpleNamespace(
            warnings=[], nonstandard=[], failed="SaveImage: [Errno 28] No space left on device"
        )
        return {"error": None, "result": res, "measurement": m, "submission": None}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--json", "--url", "http://x:1"]) == 1
    d = json.loads(capsys.readouterr().out)
    assert len(d["failed"]) == 3 and "結果が error なので送りません(SaveImage" in d["failed"][0]["error"]


def test_L3i_L3j_folder_names_and_log_lines(tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import comfystart

    d = venv_install(tmp_path / "日本語 フォルダ")
    plan_ = comfystart.plan_start(d)
    assert str(d / "main.py") in plan_.argv(1)
    log = tmp_path / "c.log"
    log.write_bytes("起動中\n".encode() + b"\xff\xfe broken\n" + "\x1b[32mINFO\x1b[0m 準備\x07\r\n".encode())
    assert comfystart.last_log_line(log) == "INFO 準備"
    assert comfystart.clean_line("\x1b]0;title\x07ok\x1b[2K") == "ok"


def test_L4a_template_unreachable_is_said(capsys, monkeypatch, this_pc, plan, gh):
    from genmodelfit import bench

    class Down:
        s = None

        def object_info(self):
            return {"KSampler": {}}

        def template(self, name):
            return None

    monkeypatch.setattr(
        bench, "fetch_template", lambda c, m, s: (_ for _ in ()).throw(bench.BenchError("offline"))
    )
    checker = bench.NodeCheck("http://x:1", client=Down())
    assert checker.missing("a") == [] and checker.unchecked == ["a"]
    monkeypatch.setattr(cli, "_node_check", lambda url: checker)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert "ノードを確かめられなかったもの" in capsys.readouterr().err


def test_L4b_missing_files_hint():
    hint = cli._measure_next_step("必要なファイルがサーバーにありません: x", "image_x")
    assert "genmodelfit download image_x" in hint and "--comfyui-dir" in hint


def test_L5a_cache_not_writable(monkeypatch, this_pc):
    from genmodelfit import bench
    from genmodelfit.models import Measurement

    def run(opts, log=None, progress=None):
        m = Measurement(model_id=opts.model_id, gpu_name="g", gpu_vram_gb=8.0, peak_vram_gb=1.0, outcome="ok")
        return bench.BenchResult(measurement=m, workflow_source="t", warnings=[])

    def save(m, path):
        raise PermissionError(13, "denied", str(path))

    monkeypatch.setattr(bench, "run", run)
    monkeypatch.setattr(bench, "save", save)
    e = SimpleNamespace(id="image_sdxl_simple", precision="fp16", files=[])
    out = cli._measure_run(cli.build_parser().parse_args(["share"]), "http://x:1", e, None)
    assert "保存できません" in out["error"] and "GENMODELFIT_CACHE_DIR" in out["error"]


def test_L5b_broken_marks_and_token_files(capsys):
    measure.shared_path().parent.mkdir(parents=True, exist_ok=True)
    measure.shared_path().write_text("{not json", encoding="utf-8")
    assert measure.load_shared() == {}
    _write_sub(1)
    assert len(measure.saved_submissions()) == 1
    github.token_path().parent.mkdir(parents=True, exist_ok=True)
    github.token_path().write_text("{broken", encoding="utf-8")
    assert github.read_cached_token() is None and not github.token_path().exists()


def test_L7a_organization_restricts_oauth_apps():
    class Restricted(FakeGitHub):
        def request(self, method, url, **kw):
            if method == "PUT":
                return Resp(
                    403,
                    {
                        "message": "Although you appear to have the correct authorization credentials, the"
                        " `laxshmate` organization has enabled OAuth App access restrictions"
                    },
                )
            return super().request(method, url, **kw)

    with pytest.raises(github.GitHubError) as e:
        github.submit(
            Restricted(fork_exists=True),
            _creds(),
            [("data/contrib/x/2026-10-29-aaaaaaaa.json", "{}")],
            title="t",
            body="b",
            branch="genmodelfit-share/x/1",
            match_prefix="genmodelfit-share/x/",
            sleep=lambda s: None,
        )
    assert e.value.kind == "org_restricted" and "--issue" in str(e.value) and "scope" not in str(e.value)


def test_L7b_another_repository_named_genmodelfit():
    class Other(FakeGitHub):
        def request(self, method, url, **kw):
            if method == "GET" and url == FORK:
                return Resp(200, {"full_name": "alice/genmodelfit", "fork": False})  # フォークでない別のもの
            if method == "POST" and url.endswith("/forks"):
                return Resp(202, {"full_name": "alice/genmodelfit-1"})
            if method == "GET" and url.endswith("/repos/alice/genmodelfit-1"):
                return Resp(200, {"full_name": "alice/genmodelfit-1"})
            return super().request(method, url, **kw)

    fork, fresh = github.ensure_fork(Other(), TOKEN, "alice", sleep=lambda s: None)
    assert fork == "alice/genmodelfit-1" and fresh


def test_L9a_cp932_console_does_not_crash():
    import io

    from rich.console import Console

    from genmodelfit.downloader import DownloadProgress

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp932", errors="strict")
    con = Console(file=cli._SafeStream(stream), force_terminal=True, width=60, color_system=None)
    v = cli._StartView(45, 300, console=con)
    v.update(3, "━━ ⠋ … ✓ 起動中")
    v.done(4)
    m = cli._MeasureView("測る 1/1: SDXL 768×768", 20, console=con)
    m.update({"phase": "running", "elapsed": 30, "peak_vram_gb": 1.0})
    m.done()
    d = cli._DownloadView(console=con)
    d(DownloadProgress("download", 1, 1, "m.safetensors", 1, 2, 1, 2))
    d.close()
    stream.flush()
    assert raw.getvalue()  # 書けない文字は ? になって、落ちない


def test_L9b_L9c_narrow_and_dumb_terminals(monkeypatch, capsys):
    import io

    from rich.console import Console

    con = _term(40)
    v = cli._MeasureView("測る 1/2: とても長い名前のモデル 1536×1536", 20, console=con)
    v.update({"phase": "queued", "elapsed": 5, "peak_vram_gb": 0.5})
    v.done()
    assert all(w <= 40 for _l, w in _rendered_lines(con.file.getvalue()))
    monkeypatch.setenv("TERM", "dumb")
    dumb = Console(file=io.StringIO(), force_terminal=True)
    v = cli._MeasureView("x", 20, console=dumb)
    assert v.live is False  # 書き換えない(普通の行)
    v.update({"phase": "running", "elapsed": 1})
    assert "x:" in capsys.readouterr().err


def test_L11a_answers_pointing_at_system_folders_are_not_used(tmp_path):
    from genmodelfit import comfyfind

    assert not comfyfind.usable_models_dir(tmp_path / "missing")
    assert comfyfind.usable_models_dir(tmp_path)
    if os.name == "nt":
        assert not comfyfind.usable_models_dir(Path("C:/Windows/System32"))
        assert not comfyfind.usable_models_dir(Path("C:/"))
    else:
        assert not comfyfind.usable_models_dir(Path("/usr/lib"))
        assert not comfyfind.usable_models_dir(Path("/"))


def test_L11b_broken_object_info():
    from genmodelfit import bench
    from genmodelfit.workflow import missing_nodes

    assert missing_nodes({"nodes": []}, "not a dict") == []

    class Broken:
        s = None

        def object_info(self):
            raise bench.BenchError("ComfyUI の /object_info の応答が JSON ではありません")

        def template(self, name):
            return None

    nc = bench.NodeCheck("http://x:1", client=Broken())
    assert nc.missing("a") == [] and nc.unchecked == ["a"]


def test_L12a_macos_desktop_app_place(tmp_path, monkeypatch):
    from genmodelfit import comfystart

    base = tmp_path / "Documents" / "ComfyUI"
    for name in ("models", "input", "user", "output", "custom_nodes"):
        (base / name).mkdir(parents=True)
    (base / ".venv" / "bin").mkdir(parents=True)
    (base / ".venv" / "bin" / "python").write_text("")
    res = tmp_path / "Applications" / "ComfyUI.app" / "Contents" / "Resources"
    (res / "ComfyUI").mkdir(parents=True)
    (res / "ComfyUI" / "main.py").write_text("")
    monkeypatch.setattr(comfystart, "DESKTOP_RESOURCES_MAC", tmp_path / "nowhere")
    p = comfystart.plan_start(base, desktop=True, platform="darwin", home=tmp_path)
    assert (
        p.kind == "desktop"
        and p.python == base / ".venv" / "bin" / "python"
        and p.main == res / "ComfyUI" / "main.py"
    )


# --- 2 回目の確かめ(#124 の Tester)------------------------------------------------------------------------


def test_r2_log_tail_is_cleaned(tmp_path):
    from genmodelfit import comfystart

    log = tmp_path / "c.log"
    log.write_text("ok\n\x1b[31mTraceback\x1b[0m\x08\n\x1b]0;t\x07CUDA error\x07\n\n", encoding="utf-8")
    assert comfystart.tail(log, 5) == ["ok", "Traceback", "CUDA error"]


def test_r2_anything_answering_on_the_port_counts(monkeypatch):
    from genmodelfit import comfyfind, fetch

    class S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            return SimpleNamespace(status_code=401, json=lambda: {})

    monkeypatch.setattr(comfyfind, "search_disabled", lambda: False)
    monkeypatch.setattr(fetch, "make_session", lambda: S())
    assert comfyfind.comfy_port_answering() == 8188
    why = cli._HeadReason("A ComfyUI already seems to run on port 8188 …")
    lines = cli._not_running_lines("", None, why)
    assert lines == [str(why)]  # 「見つかりません」と食い違う行を出さない


def test_r2_only_unsendable_results_exit_0(capsys, monkeypatch, this_pc, plan, gh):
    def run(args, url, entry, settings, **k):
        m = SimpleNamespace(outcome="ok", peak_vram_gb=9.3, seconds=3.0, standard=True)
        res = SimpleNamespace(warnings=[], nonstandard=[], failed=None)
        return {
            "error": None,
            "result": res,
            "measurement": m,
            "submission": None,
            "unsendable": ["peak > GPU"],
        }

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 0
    assert "測れたが、送れません" in capsys.readouterr().out and gh.calls[:0] == []


def test_r2_cache_folder_that_cannot_be_made(capsys, monkeypatch, this_pc, plan, gh, tmp_path):
    f = tmp_path / "a-file"
    f.write_text("x")
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(f / "sub"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert "キャッシュのフォルダを作れません" in err and "Traceback" not in err and plan["ran"] == []


def test_r2_bench_reasons_follow_the_language(monkeypatch):
    from test_bench import FakeComfy

    from genmodelfit import i18n

    bench = _bench_gpu(monkeypatch)
    with i18n.forced("en"), FakeComfy("partial_cached") as fake:
        res = bench.run(bench.BenchOptions(model_id="image_sdxl_simple", url=fake.url, timeout=60))
    assert res.nonstandard and all(x.isascii() for x in res.nonstandard)
    assert "前の結果を使ったノード" in res.measurement.note  # 実測の note は日本語のまま


@pytest.mark.parametrize(
    ("msg", "want"),
    [
        ("テンプレートを取れません(GitHub に繋がりません: ProxyError)", "回線(プロキシの設定も)"),
        ("ComfyUI がワークフローを受け付けませんでした: node 3 (KSampler): bad", "上のノードの理由"),
        ("測りましたが、保存できません(PermissionError: x)", "GENMODELFIT_CACHE_DIR"),
        ("ComfyUI が止まりました(ログ: c.log)", "ログを見て"),
        ("ValueError: boom", "もう一度実行してください"),
    ],
)
def test_r2_next_steps_are_specific(msg, want):
    hint = cli._measure_next_step(msg, "x")
    assert want in hint
    if "接続" not in msg:
        assert "起動していて" not in hint


def test_r2_no_workflow_hint_in_measure_and_share():
    m = (
        "テンプレート x が見つかりません(ComfyUI にも GitHub にも無い)。"
        "名前を確かめるか、--workflow で API 形式のワークフローを渡してください"
    )
    assert "--workflow" not in cli._without_workflow_hint(m)


def test_r2_no_misleading_plan_without_a_usable_gpu(capsys, monkeypatch, plan, gh):
    from genmodelfit.models import Gpu, SystemInfo

    # 使えない GPU(backend none)だけ: 測る計画を見せない(AMD・Intel・Apple の使える GPU は測る。0.13.1)
    g = Gpu(name="AMD Radeon RX 580", vendor="amd", vram_total_gb=8.0, backend="none")
    s = SystemInfo(os="Windows 11", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=[g])
    monkeypatch.setattr(cli, "_system", lambda args: s)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    out, err = capsys.readouterr()
    assert "測るもの" not in out and "測りに使える GPU が見つかりません" in err


def test_r2_token_messages_tell_expired_from_broken(capsys, monkeypatch, this_pc, plan, gh):
    import time

    github.write_cached_token(TOKEN, 28800, now=lambda: time.time() - 30000)
    assert github.cached_token_status() == "expired"
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "q")
    cli.main(["--lang", "ja", "share", "--url", "http://x:1"])
    assert "期限が切れたので消しました" in capsys.readouterr().err
    github.token_path().write_text("{broken", encoding="utf-8")
    assert github.cached_token_status() == "broken"
    answers(monkeypatch, "", "q")
    plan["ran"].clear()
    cli.main(["--lang", "ja", "share", "--url", "http://x:1", "--all"])
    err = capsys.readouterr().err
    assert "ファイルが壊れていたので消しました" in err and "期限" not in err


def test_r2_missing_space_in_english(capsys, monkeypatch, this_pc, plan, gh):
    from genmodelfit import i18n

    def run(args, url, entry, settings, **k):
        m = SimpleNamespace(outcome="error", peak_vram_gb=1.0, seconds=3.0, standard=True)
        res = SimpleNamespace(warnings=[], nonstandard=[], failed="Interrupted")
        return {"error": None, "result": res, "measurement": m, "submission": None}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    with i18n.forced("en"):
        cli.main(["--lang", "en", "share", "--yes", "--url", "http://x:1"])
    assert "not sent (Interrupted)" in capsys.readouterr().out


def test_L7d_github_outage_keeps_the_results(capsys, monkeypatch, this_pc, plan):
    class Outage(FakeGitHub):
        def request(self, method, url, **kw):
            if "/user" not in url:
                return Resp(503, {"message": "Service Unavailable"})
            return super().request(method, url, **kw)

    monkeypatch.setattr(cli, "_share_session", lambda: Outage())
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert cli.main(["--lang", "ja", "share", "--yes", "--url", "http://x:1"]) == 1
    assert "genmodelfit share --no-measure" in capsys.readouterr().err
    assert len(measure.saved_submissions()) == 3 and measure.load_shared() == {}


def test_L7e_device_code_expires():
    fake = FakeGitHub(polls=[{"error": "authorization_pending"}] * 5)
    t = iter(range(0, 10000, 400))
    with pytest.raises(github.GitHubError) as e:
        github.device_flow(fake, "cid", lambda dc: None, sleep=lambda s: None, clock=lambda: next(t))
    assert e.value.kind == "expired"


def test_L7f_token_revoked_while_measuring(capsys, monkeypatch, this_pc, plan, gh):
    github.write_cached_token(TOKEN, 28800)
    n = {"user": 0}
    real = gh.request

    def request(method, url, **kw):
        if url.endswith("/user"):
            n["user"] += 1
            if n["user"] > 1:
                return Resp(401, {"message": "Bad credentials"})
        return real(method, url, **kw)

    gh.request = request
    monkeypatch.setenv(github.CLIENT_ID_ENV, "Iv1.test")
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    shown = []
    real_login = github.login

    def login(session, **k):
        shown.append("device")
        return github.Credentials(TOKEN, "alice", "device")

    monkeypatch.setattr(
        github,
        "login",
        lambda session, **k: login(session, **k) if k.get("interactive") else real_login(session, **k),
    )
    answers(monkeypatch, "", "1")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert shown == ["device"] and len(gh.put) == 3  # 送るときに 401 → ログインし直して送った


def test_L8a_no_browser_and_no_clipboard(capsys, monkeypatch):
    import webbrowser

    p = _write_sub(1)
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda text: False)
    monkeypatch.setattr(contrib, "SHARE_URL_LIMIT", 10)  # URL に入りきらない
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    assert cli.main(["--lang", "ja", "share", "--issue", str(p)]) == 0
    out = capsys.readouterr().out
    assert "次のフォームをブラウザで開き" in out and "---" in out  # 写し方の案内と中身


# --- 3 回目の確かめ(#124 の Tester)------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("types", "want"),
    [({4242: "C+G"}, set()), ({4242: "G"}, set()), ({4242: "C"}, {4242}), ({}, set())],
)
def test_r3_only_new_compute_processes_count(monkeypatch, types, want):
    from genmodelfit import bench

    monkeypatch.setattr(bench, "smi_process_types", lambda: types)
    peaks = SimpleNamespace(smi_samples=10, gpu_used_mib=7800.0, torch_vram=2 * 1024**3)
    assert bench._interfering({4242}, (600.0, 0.0), peaks) == want  # GPU 全体が大きく増えても C+G は数えない


def test_r3_wording(monkeypatch, tmp_path):
    from test_comfystart import venv_install

    from genmodelfit import bench, comfyfind, comfystart, i18n

    with i18n.forced("en"):
        assert ", " in bench._process_names({999999991, 999999992})
        assert github.token_status_message("broken").startswith("The ")
        assert github.token_status_message("expired").startswith("The ")
    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    args = cli.build_parser().parse_args(["share", "--comfyui-dir", str(venv_install(tmp_path))])
    monkeypatch.setattr(comfyfind, "comfy_port_status", lambda *a, **k: (8188, 401))
    _p, why = cli._comfyui_start_plan(args, None)
    assert "認証" in why and "フォルダを読めません" not in why
    monkeypatch.setattr(comfyfind, "comfy_port_status", lambda *a, **k: (8000, 404))
    _p, why = cli._comfyui_start_plan(args, None)
    assert "ComfyUI でないもの" in why


def test_r3_no_plan_screen_when_another_server_is_on_the_port(
    capsys, monkeypatch, this_pc, plan, gh, tmp_path
):
    from test_comfystart import venv_install

    from genmodelfit import comfyfind, comfystart

    monkeypatch.delenv(comfystart.START_ENV, raising=False)
    monkeypatch.setattr(comfyfind, "comfy_port_status", lambda *a, **k: (8188, 200))
    assert cli.main(["--lang", "ja", "share", "--yes", "--comfyui-dir", str(venv_install(tmp_path))]) == 1
    cap = capsys.readouterr()
    assert "ComfyUI:" not in cap.out and "合計の目安" not in cap.out
    assert "--url http://127.0.0.1:8188" in cap.err and "見つかりません" not in cap.err


# --- 4 回目の確かめ: すぐ消える計算のプロセスも取りこぼさない(#124) ---------------------------------------


def _watch(monkeypatch, frames, types_by_frame, used_by_frame):
    """frames[i] は i 回目に見える新しい PID、types_by_frame[i] はそのときの表の種類、
    used_by_frame[i] は GPU 全体の使用量(MiB)。"""
    from genmodelfit import bench

    i = {"n": 0}
    monkeypatch.setattr(bench, "_gpu_app_pids", lambda uuid: {1} | frames[i["n"]])
    monkeypatch.setattr(bench, "smi_process_types", lambda: types_by_frame[i["n"]])
    w = bench.GpuAppWatch("GPU-a", {1}, lambda: set(), lambda: used_by_frame[i["n"]])
    w.last_used = 600.0
    for n in range(len(frames)):
        i["n"] = n
        w.sample()
    return w


def test_r4_short_lived_compute_process_is_caught(monkeypatch):
    # 2 回目の見張りで現れ(種類 C を読めた)、3 回目には消えている
    w = _watch(monkeypatch, [set(), {50}, set()], [{}, {50: "C"}, {}], [700.0, 900.0, 800.0])
    assert set(w.interfering()) == {50}


def test_r4_short_lived_desktop_app_is_ignored(monkeypatch):
    w = _watch(monkeypatch, [set(), {60}, set()], [{}, {60: "C+G"}, {}], [700.0, 5000.0, 800.0])
    assert w.interfering() == {}  # GPU 全体が増えても、画面のアプリ(C+G)は数えない


def test_r4_unknown_type_counts_only_with_a_memory_rise(monkeypatch):
    w = _watch(monkeypatch, [set(), {70}, set()], [{}, {}, {}], [700.0, 900.0, 800.0])
    assert w.interfering() == {}  # 種類を読めないまま消え、増えも 1 GiB 以下
    w = _watch(monkeypatch, [set(), {71}, set()], [{}, {}, {}], [700.0, 2500.0, 800.0])
    assert set(w.interfering()) == {71}  # 現れる直前より 1 GiB を超えて増えた


# --- #132: 失敗の記録も送る ----------------------------------------------------------------------------


def _failing_run(outcomes):
    """measure の 1 件を、順に outcomes の結果にする偽物(失敗は failure の記録を返す)。"""
    it = iter(outcomes)

    def run(args, url, entry, settings, free=False, progress=None):
        kind = next(it)
        if kind == "ok":
            p = _write_sub(1)
            m = SimpleNamespace(outcome="ok", peak_vram_gb=5.0, seconds=12.0)
            return {"error": None, "result": SimpleNamespace(warnings=[]), "measurement": m, "submission": p}
        rec = contrib.failure_record(entry.id, kind, seconds=30.0, timeout_s=30.0, error_type="ValueError")
        return {"error": f"{kind}!", "result": None, "measurement": None, "submission": None, "failure": rec}

    return run


def test_share_sends_measurements_and_one_failure_file(capsys, monkeypatch, this_pc, plan, gh):
    nodes = FakeNodes({"a_installed_short": ["LoadMoGeModel"]})
    nodes.comfyui_version = lambda: "0.40.0"
    monkeypatch.setattr(cli, "_node_check", lambda url: nodes)
    monkeypatch.setattr(cli, "_measure_run", _failing_run(["ok", "timeout"]))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    code = cli.main(["share", "--yes", "--json", "--url", "http://127.0.0.1:8188"])
    d = json.loads(capsys.readouterr().out)
    assert code == 1  # 測れなかったものがある(今までどおり)
    failures = [p for p in gh.put if p.endswith("-failures.json")]
    assert len(failures) == 1 and len(gh.put) == 2 and d["failures_file"]
    body = json.loads(base64.b64decode(gh.put[failures[0]]["content"]).decode("utf-8"))
    assert body["kind"] == "genmodelfit-failures" and body["comfyui_version"] == "0.40.0"
    assert [(f["model_id"], f["outcome"]) for f in body["failures"]] == [
        ("e_installed", "timeout"),
        ("a_installed_short", "missing_nodes"),
    ]
    assert contrib.validate_submission(body, raw=json.dumps(body), check_filename=False).ok


def test_share_with_only_failures_still_sends_them(capsys, monkeypatch, this_pc, plan, gh):
    monkeypatch.setattr(cli, "_measure_run", _failing_run(["error", "error", "error"]))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    cli.main(["share", "--yes", "--json", "--url", "http://127.0.0.1:8188"])
    d = json.loads(capsys.readouterr().out)
    assert d["sent"] is True and [f["path_in_repo"].endswith("-failures.json") for f in d["files"]] == [True]


def test_share_with_nothing_measured_and_no_failures_does_nothing(capsys, monkeypatch, this_pc, plan, gh):
    def run(args, url, entry, settings, free=False, progress=None):  # 接続できないなど、送らない失敗
        return {"error": "Cannot connect", "result": None, "measurement": None, "submission": None}

    monkeypatch.setattr(cli, "_measure_run", run)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    cli.main(["share", "--yes", "--json", "--url", "http://127.0.0.1:8188"])
    d = json.loads(capsys.readouterr().out)
    assert d["sent"] is False and d["files"] == [] and gh.put == {}


def test_share_with_only_missing_nodes_sends_the_failure_file(capsys, monkeypatch, this_pc, plan, gh):
    plan["sugg"] = [sugg("a_installed_short", 60)]
    nodes = FakeNodes({"a_installed_short": ["LoadMoGeModel"]})
    monkeypatch.setattr(cli, "_node_check", lambda url: nodes)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    cli.main(["share", "--yes", "--json", "--url", "http://127.0.0.1:8188"])
    d = json.loads(capsys.readouterr().out)
    assert plan["ran"] == [] and d["sent"] is True
    assert [p for p in gh.put] and all(p.endswith("-failures.json") for p in gh.put)


# --- デバイス認証を待つあいだの操作(0.13.0。コードを打ち間違えても黙って 15 分待たない) ---------------------


class FakeKeys:
    """待つあいだのキーの偽物。read は keys を順に返し、尽きたら None。抜けたか(端末を戻したか)を残す。"""

    def __init__(self, keys):
        self.keys = list(keys)
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1

    def read(self):
        if not self.keys:
            return None
        k = self.keys.pop(0)
        if isinstance(k, BaseException):
            raise k
        return k


@pytest.fixture
def login_env(monkeypatch, gh):
    """デバイス認証で PR を送る場面: 送っていない実測 1 件、端末、偽の時計(待つと進む)、偽のブラウザ。"""
    import webbrowser

    monkeypatch.setenv(github.CLIENT_ID_ENV, "Iv1.test")
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    now = {"t": 0.0}
    monkeypatch.setattr(cli, "_login_sleep", lambda s: now.__setitem__("t", now["t"] + s))
    monkeypatch.setattr(cli, "_login_clock", lambda: now["t"])
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    _write_sub(1)

    def keys(*seq):
        fk = FakeKeys(seq)
        monkeypatch.setattr(cli, "_login_keys", lambda: fk)
        return fk

    return SimpleNamespace(gh=gh, opened=opened, keys=keys, now=now)


PENDING = {"error": "authorization_pending"}


def _device_codes(gh) -> int:
    return sum(1 for _m, u, _k in gh.calls if u == github.DEVICE_CODE_URL)


def test_mistyped_code_then_n_gets_a_new_code(capsys, monkeypatch, login_env):
    gh = login_env.gh
    gh.polls = [PENDING, PENDING, {"access_token": TOKEN, "expires_in": 28800}]
    # 25 回(5 秒 ÷ 0.2 秒)ごとに 1 回尋ねる。2 回尋ねても入らない(打ち間違えた)ので n を押す
    fk = login_env.keys(*([None] * 55), "n")
    answers(monkeypatch, "")  # 送り方 → 1(PR)
    assert cli.main(["--lang", "ja", "share", "--no-measure"]) == 0
    err = capsys.readouterr().err
    assert _device_codes(gh) == 2  # 新しいコードでやり直した
    assert err.count("コード ABCD-1234 を入れる") == 2
    assert "コードを間違えたら、同じページで入れ直せます(コードは 15 分有効)" in err
    assert "r ブラウザを開き直す / n 新しいコード / q やめて Issue で送る" in err
    assert "GitHub: alice としてログイン" in err and gh.put  # PR を送った
    assert fk.entered == fk.exited >= 1  # 端末を戻した


def test_q_stops_signing_in_and_sends_issues(capsys, monkeypatch, login_env):
    login_env.gh.polls = [PENDING] * 3
    fk = login_env.keys(None, None, "q")
    answers(monkeypatch, "")
    assert cli.main(["--lang", "ja", "share", "--no-measure"]) == 0
    err = capsys.readouterr().err
    assert "ログインをやめました。Issue で送ります" in err
    assert any("issues/new?template=measurement.yml" in u for u in login_env.opened)
    assert login_env.gh.put == {} and len(measure.load_shared()) == 1
    assert fk.entered == fk.exited


def test_r_opens_the_browser_again(capsys, monkeypatch, login_env):
    login_env.gh.polls = [{"access_token": TOKEN, "expires_in": 28800}]
    login_env.keys(None, "r")
    answers(monkeypatch, "")
    assert cli.main(["--lang", "ja", "share", "--no-measure"]) == 0
    assert login_env.opened.count("https://github.com/login/device") == 2
    assert "ブラウザで開き直しました" in capsys.readouterr().err


@pytest.mark.parametrize(("answer", "pr"), [("", True), ("n", False)])
def test_expired_code_asks_to_sign_in_again_or_send_issues(capsys, monkeypatch, login_env, answer, pr):
    gh = login_env.gh
    gh.polls = [{"error": "expired_token"}, {"access_token": TOKEN, "expires_in": 28800}]
    login_env.keys()
    asked = answers(monkeypatch, "", answer)  # 送り方 → PR、もう一度ログインしますか → answer
    assert cli.main(["--lang", "ja", "share", "--no-measure"]) == 0
    assert asked[1] == "もう一度ログインしますか? [Y/n](n なら Issue で送ります) "
    assert bool(gh.put) is pr and _device_codes(gh) == (2 if pr else 1)
    if not pr:
        assert any("issues/new" in u for u in login_env.opened)
    assert len(measure.saved_submissions(include_shared=True)) == 1  # 実測は消えない


def test_ctrl_c_while_waiting_restores_the_terminal(capsys, monkeypatch, login_env):
    login_env.gh.polls = [PENDING] * 3
    fk = login_env.keys(None, KeyboardInterrupt())
    answers(monkeypatch, "")
    assert cli.main(["--lang", "ja", "share", "--no-measure"]) == 130
    assert fk.entered == fk.exited == 1
    assert "genmodelfit share --no-measure" in capsys.readouterr().err
    assert len(measure.saved_submissions()) == 1  # 送っていないまま残る


def test_device_flow_on_wait_new_and_stop():
    fake = FakeGitHub(polls=[PENDING] * 5)
    t = {"now": 0.0}
    sleep = lambda s: t.__setitem__("now", t["now"] + s)  # noqa: E731
    seen: list[float] = []

    def stop_after_3(dc, left):
        seen.append(left)
        return github.WAIT_STOP if len(seen) > 3 else None

    with pytest.raises(github.GitHubError) as e:
        github.device_flow(
            fake, "cid", lambda dc: None, sleep=sleep, clock=lambda: t["now"], on_wait=stop_after_3
        )
    assert e.value.kind == "cancelled" and seen[0] == 900 and seen[-1] < 900
    with pytest.raises(github.GitHubError) as e:
        github.device_flow(
            fake,
            "cid",
            lambda dc: None,
            sleep=sleep,
            clock=lambda: t["now"],
            on_wait=lambda dc, left: github.WAIT_NEW,
        )
    assert e.value.kind == "new_code"


def test_key_source_reads_nothing_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with cli._KeySource() as k:
        assert k.read() is None and not k.enabled
    assert not k.enabled


def test_key_source_puts_the_terminal_back_on_posix(monkeypatch):
    """POSIX: 待つあいだは cbreak にし、抜けるとき(例外でも)元の設定に戻す(本物の端末は触らない)。"""
    import types

    calls = []
    termios = types.SimpleNamespace(
        TCSADRAIN=1,
        tcgetattr=lambda fd: calls.append(("get", fd)) or ["old"],
        tcsetattr=lambda fd, when, attrs: calls.append(("set", fd, attrs)),
    )
    tty = types.SimpleNamespace(setcbreak=lambda fd: calls.append(("cbreak", fd)))
    monkeypatch.setitem(sys.modules, "termios", termios)
    monkeypatch.setitem(sys.modules, "tty", tty)
    monkeypatch.setattr(sys, "platform", "linux")
    fake_in = SimpleNamespace(isatty=lambda: True, fileno=lambda: 7)
    monkeypatch.setattr(sys, "stdin", fake_in)
    with pytest.raises(KeyboardInterrupt), cli._KeySource() as k:
        assert k.enabled
        raise KeyboardInterrupt
    assert calls == [("get", 7), ("cbreak", 7), ("set", 7, ["old"])]
