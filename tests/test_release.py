"""署名と、署名つきの一覧の取り込み(--from-release)。ネットワークには触らない。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from genmodelfit import remote, signing
from genmodelfit.cli import main
from genmodelfit.models import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


def _keypair() -> tuple[bytes, bytes]:
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = k.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub


def test_roundtrip_and_rejections() -> None:
    priv, pub = _keypair()
    sig = signing.sign(b"hello", priv)
    assert len(sig) == 64
    assert signing.verify(b"hello", sig, pub)
    assert not signing.verify(b"hellO", sig, pub)  # 改ざん
    _, other_pub = _keypair()
    assert not signing.verify(b"hello", sig, other_pub)  # 別の鍵
    assert not signing.verify(b"hello", b"short", pub)  # 壊れた署名
    assert not signing.verify(b"hello", sig, b"not a key")  # 壊れた鍵
    assert signing.decode_sig(signing.encode_sig(sig)) == sig


def test_bad_private_key() -> None:
    with pytest.raises(signing.SigningError):
        signing.sign(b"x", b"not pem")


def test_embedded_public_keys_load() -> None:
    keys = signing.trusted_public_keys()
    assert len(keys) >= 1
    # 同梱の鍵では、別の鍵の署名は通らない
    priv, _ = _keypair()
    assert not signing.verify(b"x", signing.sign(b"x", priv))


def test_two_trusted_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """鍵を替える間は新旧の公開鍵を並べて同梱する。どちらの署名も通り、知らない鍵は通らない。"""
    old_priv, old_pub = _keypair()
    new_priv, new_pub = _keypair()
    stranger, _ = _keypair()
    monkeypatch.setattr(signing, "embedded_public_keys_pem", lambda: [old_pub, new_pub])
    assert signing.verify(b"x", signing.sign(b"x", old_priv))
    assert signing.verify(b"x", signing.sign(b"x", new_priv))
    assert not signing.verify(b"x", signing.sign(b"x", stranger))
    assert signing.verify(b"x", signing.sign(b"x", new_priv), [old_pub, new_pub])
    # 古い鍵を外したら、古い鍵の署名は通らない
    monkeypatch.setattr(signing, "embedded_public_keys_pem", lambda: [new_pub])
    assert not signing.verify(b"x", signing.sign(b"x", old_priv))


def test_no_trusted_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    priv, _ = _keypair()
    monkeypatch.setattr(signing, "embedded_public_keys_pem", list)
    assert not signing.verify(b"x", signing.sign(b"x", priv))


def test_sign_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    priv, pub = _keypair()
    f = tmp_path / "db.json"
    f.write_bytes(b"{}")
    monkeypatch.setenv("GENMODELFIT_SIGNING_KEY", priv.decode())
    assert signing.main(["sign", str(f)]) == 0
    sig = signing.decode_sig((tmp_path / "db.json.sig").read_text())
    assert signing.verify(b"{}", sig, pub)
    monkeypatch.delenv("GENMODELFIT_SIGNING_KEY")
    assert signing.main(["sign", str(f)]) == 1


# --- 日時 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-20T00:00:00Z",
        "2026-10-20T00:00:00z",
        "2026-10-20T00:00:00+00:00",
        "2026-10-20T09:00:00+09:00",
        "2026-10-20T00:00:00.0Z",
        "2026-10-20T00:00:00.000000000Z",
    ],
)
def test_parse_time_same_instant(text: str) -> None:
    assert remote.parse_time(text) == datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_parse_time_fraction() -> None:
    t = remote.parse_time("2026-10-20T00:00:00.5Z")
    assert t.microsecond == 500000
    assert remote.parse_time("2026-10-20T00:00:00.1234567+00:00").microsecond == 123456
    assert remote.parse_time("2026-10-20T00:00:00.5Z") > remote.parse_time("2026-10-20T00:00:00Z")


@pytest.mark.parametrize(
    "bad", [None, "", "yesterday", "2026-10-20T00:00:00", 20261020, "2026-13-01T00:00:00Z"]
)
def test_parse_time_rejects(bad) -> None:
    with pytest.raises(ValueError):
        remote.parse_time(bad)


# --- --from-release ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, content: bytes, status: int = 200, headers: dict[str, str] | None = None):
        self.content, self.status_code = content, status
        self.headers = headers or {}
        self.chunks = 0

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size: int = 1):
        for i in range(0, len(self.content), chunk_size):
            self.chunks += 1
            yield self.content[i : i + chunk_size]

    def close(self) -> None:
        pass


class FakeSession:
    """名前 → 中身。中身にリストを渡すと、取るたびに順に返す(最後のものを繰り返す)。"""

    def __init__(self, files: dict[str, bytes | list[bytes]] | Exception):
        self.files = files
        self.calls: list[str] = []

    def get(self, url: str, **kw) -> FakeResp:
        assert kw.get("stream") is True
        if isinstance(self.files, Exception):
            raise self.files
        name = url.rsplit("/", 1)[1]
        self.calls.append(name)
        if name not in self.files:
            return FakeResp(b"", 404)
        v = self.files[name]
        if isinstance(v, list):
            n = self.calls.count(name) - 1
            v = v[min(n, len(v) - 1)]
        return FakeResp(v)


def _db_bytes(generated_at: str | None = "2026-10-20T00:00:00Z", **over) -> bytes:
    d = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "sources": {}, "models": []}
    if generated_at is None:
        del d["generated_at"]
    d.update(over)
    return json.dumps(d).encode()


def _sig(data: bytes, priv: bytes) -> bytes:
    return signing.encode_sig(signing.sign(data, priv)).encode()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """同梱の公開鍵の代わりにテスト用の鍵を差し込み、キャッシュを一時フォルダにする。"""
    priv, pub = _keypair()
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(signing, "embedded_public_keys_pem", lambda: [pub])
    monkeypatch.setattr(remote, "RETRY_DELAY", 0)
    return priv, tmp_path / "db.json"


def _patch_session(monkeypatch: pytest.MonkeyPatch, files) -> FakeSession:
    s = FakeSession(files)
    monkeypatch.setattr(remote, "_http_get", s.get)
    return s


def _signed(monkeypatch, priv: bytes, data: bytes) -> FakeSession:
    return _patch_session(monkeypatch, {"db.json": data, "db.json.sig": _sig(data, priv)})


def _state(cache: Path) -> dict:
    return json.loads((cache.parent / remote.STATE_NAME).read_text(encoding="utf-8"))


def test_from_release_ok(env, monkeypatch, capsys) -> None:
    priv, cache = env
    cache.write_text("old")
    data = _db_bytes()
    _signed(monkeypatch, priv, data)
    assert main(["update", "--from-release"]) == 0
    assert cache.read_bytes() == data
    assert "更新しました" in capsys.readouterr().out
    assert not list(cache.parent.glob("*.tmp"))
    assert _state(cache)["generated_at"] == "2026-10-20T00:00:00+00:00"


def test_from_release_json(env, monkeypatch, capsys) -> None:
    priv, cache = env
    _signed(monkeypatch, priv, _db_bytes())
    assert main(["update", "--from-release", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "update" and out["models"] == 0 and out["output"] == str(cache)


def test_from_release_output(env, monkeypatch, tmp_path, capsys) -> None:
    """--output があれば、確かめた一覧をそこへ書く(キャッシュには触らない)。"""
    priv, cache = env
    cache.write_text("old")
    data = _db_bytes()
    _signed(monkeypatch, priv, data)
    out = tmp_path / "sub" / "out.json"
    assert main(["update", "--from-release", "--output", str(out)]) == 0
    assert out.read_bytes() == data
    assert cache.read_text() == "old"
    assert str(out) in capsys.readouterr().out


def test_from_release_two_keys(env, monkeypatch) -> None:
    """鍵の入れ替えの途中: 新しい鍵の署名も古い鍵の署名も取り込める。"""
    old_priv, cache = env
    new_priv, new_pub = _keypair()
    old_pub = signing.embedded_public_keys_pem()[0]
    monkeypatch.setattr(signing, "embedded_public_keys_pem", lambda: [old_pub, new_pub])
    _signed(monkeypatch, new_priv, _db_bytes("2026-10-20T00:00:00Z"))
    assert main(["update", "--from-release"]) == 0
    _signed(monkeypatch, old_priv, _db_bytes("2026-10-21T00:00:00Z"))
    assert main(["update", "--from-release"]) == 0
    assert json.loads(cache.read_text())["generated_at"] == "2026-10-21T00:00:00Z"


def test_from_release_bad_signature(env, monkeypatch, capsys) -> None:
    priv, cache = env
    cache.write_text("old")
    data = _db_bytes()
    s = _patch_session(
        monkeypatch, {"db.json": _db_bytes(generated_at="2099-01-01"), "db.json.sig": _sig(data, priv)}
    )
    assert main(["update", "--from-release"]) == 1
    assert cache.read_text() == "old"
    assert not (cache.parent / remote.STATE_NAME).exists()
    assert "署名が合いません" in capsys.readouterr().err
    assert s.calls.count("db.json") == 2  # 1 回だけ取り直す


def test_from_release_retry_recovers(env, monkeypatch) -> None:
    """資産の置き換えの途中に当たって組がずれても、取り直して合えば取り込む。"""
    priv, cache = env
    old, new = _db_bytes("2026-10-19T00:00:00Z"), _db_bytes("2026-10-20T00:00:00Z")
    s = _patch_session(monkeypatch, {"db.json": [new], "db.json.sig": [_sig(old, priv), _sig(new, priv)]})
    assert main(["update", "--from-release"]) == 0
    assert cache.read_bytes() == new
    assert s.calls.count("db.json.sig") == 2


@pytest.mark.parametrize("sig", [b"", b"\n", b"not base64!!\n", b"@@@@"])
def test_from_release_malformed_sig(env, monkeypatch, capsys, sig) -> None:
    _, cache = env
    cache.write_text("old")
    _patch_session(monkeypatch, {"db.json": _db_bytes(), "db.json.sig": sig})
    assert main(["update", "--from-release"]) == 1
    assert cache.read_text() == "old"
    assert "署名" in capsys.readouterr().err


def test_from_release_bad_schema_signed(env, monkeypatch, capsys) -> None:
    priv, cache = env
    _signed(monkeypatch, priv, _db_bytes(schema_version=SCHEMA_VERSION + 1))
    assert main(["update", "--from-release"]) == 1
    assert not cache.exists()
    assert "形が正しくありません" in capsys.readouterr().err


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"null",
        b"\xff\xfe",
        b"not json",
        _db_bytes(models=["a string, not an object"]),
        _db_bytes(models=[1]),
        _db_bytes(models=[{"id": 1, "files": "x"}]),
        _db_bytes(sources=None, models=[None]),
        _db_bytes(generated_at=None),
        _db_bytes(generated_at="2026-10-20"),
        _db_bytes(generated_at="2026-10-20T00:00:00"),
        _db_bytes(generated_at=12345),
    ],
)
def test_from_release_malformed_signed_data(env, monkeypatch, capsys, data) -> None:
    """署名は合うが中身がおかしい一覧: トレースバックではなく RemoteError。--json でも決まった形で出す。"""
    priv, cache = env
    cache.write_text("old")
    _signed(monkeypatch, priv, data)
    assert main(["update", "--from-release", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "update" and "形が正しくありません" in out["error"]
    assert cache.read_text() == "old"


def test_from_release_locked_cache(env, monkeypatch, capsys) -> None:
    """Windows でキャッシュをほかのプログラムが開いていて置き換えられない(PermissionError)。"""
    priv, cache = env
    cache.write_text("old")
    _signed(monkeypatch, priv, _db_bytes())

    def locked(src, dst):
        raise PermissionError(13, "別のプロセスが使用中です", str(dst))

    monkeypatch.setattr(remote.os, "replace", locked)
    assert main(["update", "--from-release", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "書き込めませんでした" in out["error"]
    assert cache.read_text() == "old"
    assert not list(cache.parent.glob("*.tmp"))
    assert main(["update", "--from-release"]) == 1
    assert "書き込めませんでした" in capsys.readouterr().err


def test_from_release_rejects_older(env, monkeypatch, capsys) -> None:
    priv, cache = env
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00Z"))
    assert main(["update", "--from-release"]) == 0
    before = cache.read_bytes()
    _signed(monkeypatch, priv, _db_bytes("2026-10-01T00:00:00Z"))
    assert main(["update", "--from-release"]) == 1
    assert cache.read_bytes() == before
    assert "古い一覧" in capsys.readouterr().err
    # 同じ配布の取り直しは通す
    _signed(monkeypatch, priv, before)
    assert main(["update", "--from-release"]) == 0


def test_from_release_z_vs_offset(env, monkeypatch) -> None:
    """文字列の比較なら "...Z" > "...+00:00" になるが、同じ時刻なので古いとは扱わない。"""
    priv, _ = env
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00Z"))
    assert main(["update", "--from-release"]) == 0
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00+00:00"))
    assert main(["update", "--from-release"]) == 0
    # 文字列なら "2026-10-20T09:00:00+09:00" > "2026-10-20T01:00:00Z" だが、実際は 0 時なので古い
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T01:00:00Z"))
    assert main(["update", "--from-release"]) == 0
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T09:00:00+09:00"))
    assert main(["update", "--from-release"]) == 1


def test_from_release_fractional_seconds(env, monkeypatch) -> None:
    priv, _ = env
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00.5Z"))
    assert main(["update", "--from-release"]) == 0
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00Z"))
    assert main(["update", "--from-release"]) == 1
    _signed(monkeypatch, priv, _db_bytes("2026-10-20T00:00:00.75Z"))
    assert main(["update", "--from-release"]) == 0


def test_from_release_ignores_local_cache_time(env, monkeypatch) -> None:
    """手元の update が書いたキャッシュの日時(配布より新しい)とは比べない。比べるのは前に取り込んだ配布。"""
    priv, cache = env
    cache.write_bytes(_db_bytes("2099-01-01T00:00:00Z"))
    data = _db_bytes("2026-10-20T00:00:00Z")
    _signed(monkeypatch, priv, data)
    assert main(["update", "--from-release"]) == 0
    assert cache.read_bytes() == data


def test_from_release_future_state_needs_force(env, monkeypatch, capsys) -> None:
    """記録が未来の日時になってしまったら、普段は拒否し、--force で取り込んで記録を直す。"""
    priv, cache = env
    (cache.parent / remote.STATE_NAME).write_text(json.dumps({"generated_at": "2099-01-01T00:00:00Z"}))
    data = _db_bytes("2026-10-20T00:00:00Z")
    _signed(monkeypatch, priv, data)
    assert main(["update", "--from-release"]) == 1
    assert not cache.exists()
    capsys.readouterr()
    assert main(["update", "--from-release", "--force"]) == 0
    assert cache.read_bytes() == data
    assert _state(cache)["generated_at"] == "2026-10-20T00:00:00+00:00"
    assert main(["update", "--from-release"]) == 0  # 直った後は --force なしで通る


def test_from_release_corrupt_state(env, monkeypatch) -> None:
    priv, cache = env
    (cache.parent / remote.STATE_NAME).write_text("{broken")
    _signed(monkeypatch, priv, _db_bytes())
    assert main(["update", "--from-release"]) == 0
    assert _state(cache)["generated_at"] == "2026-10-20T00:00:00+00:00"


def test_from_release_network_error(env, monkeypatch, capsys) -> None:
    _, cache = env
    cache.write_text("old")
    _patch_session(monkeypatch, requests.ConnectionError("down"))
    assert main(["update", "--from-release"]) == 1
    assert cache.read_text() == "old"
    assert "ダウンロードできませんでした" in capsys.readouterr().err


def test_from_release_missing_sig(env, monkeypatch) -> None:
    _, cache = env
    _patch_session(monkeypatch, {"db.json": _db_bytes()})
    assert main(["update", "--from-release"]) == 1
    assert not cache.exists()


def test_get_stops_when_too_large(monkeypatch) -> None:
    """上限を超えたら、最後まで読まずにやめる。"""
    monkeypatch.setattr(remote, "MAX_BYTES", 10)
    resp = FakeResp(b"x" * (3 * 1024 * 1024))
    monkeypatch.setattr(remote, "_http_get", lambda url, **kw: resp)
    with pytest.raises(remote.RemoteError, match="大きすぎます"):
        remote._get("https://example.invalid/db.json", 1)
    assert resp.chunks == 1  # 3 つのうち最初の 1 つで止まる


def test_get_rejects_large_content_length(monkeypatch) -> None:
    monkeypatch.setattr(remote, "MAX_BYTES", 10)
    resp = FakeResp(b"x", headers={"Content-Length": "999"})
    monkeypatch.setattr(remote, "_http_get", lambda url, **kw: resp)
    with pytest.raises(remote.RemoteError, match="大きすぎます"):
        remote._get("https://example.invalid/db.json", 1)
    assert resp.chunks == 0


def test_atomic_write_cleans_tmp_on_error(tmp_path, monkeypatch) -> None:
    target = tmp_path / "x.json"
    target.write_text("old")

    def boom(src, dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(remote.os, "replace", boom)
    with pytest.raises(remote.RemoteError):
        remote._atomic_write(target, b"new")
    assert target.read_text() == "old"
    assert os.listdir(tmp_path) == ["x.json"]


# --- ワークフロー -------------------------------------------------------------------------------


def _wf(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["release.yml", "data.yml", "ci.yml", "contrib.yml"])
def test_workflow_yaml_parses(name: str) -> None:
    assert _wf(name)["jobs"]


def test_ci_workflow_runs_only_after_merge_or_for_forks() -> None:
    # 全試験は手元(tools/ci_local.py)で通してから push する。ci.yml はマージ後・手動・フォークからの PR だけ
    doc = _wf("ci.yml")
    assert set(doc[True]) == {"push", "pull_request", "workflow_dispatch"}
    assert doc[True]["push"] == {"branches": ["main"]}
    fork_only = (
        "github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.full_name != github.repository"
    )
    for job in doc["jobs"].values():
        assert job["if"] == fork_only


@pytest.mark.parametrize(
    ("listing", "want"),
    [
        # 既定が docker-desktop(bash が無い)なら、ほかの WSL 2 の最初のもの(2026-11-11)
        (
            "  NAME              STATE    VERSION\n* docker-desktop    Running  2\n"
            "  Ubuntu-24.04      Stopped  2\n  PoshOnTmux        Stopped  1\n",
            "Ubuntu-24.04",
        ),
        # 既定が使えるなら既定のまま(-d を付けない)
        ("  NAME    STATE    VERSION\n* Ubuntu  Running  2\n  docker-desktop  Running  2\n", None),
        # ほかに使えるものが無い・読めない
        ("  NAME  STATE  VERSION\n* docker-desktop  Running  2\n  Old  Stopped  1\n", None),
        ("", None),
    ],
)
def test_ci_local_skips_docker_desktop_distro(listing: str, want: str | None) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("ci_local", ROOT / "tools" / "ci_local.py")
    ci_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_local)
    assert ci_local.wsl_distro(listing) == want
    utf16 = listing.encode("utf-16-le").decode("utf-16-le")  # wsl.exe は UTF-16 で出す(読んだ後は同じ文字)
    assert ci_local.wsl_distro(utf16) == want


def test_ci_local_matches_ci_workflow() -> None:
    # 手元の台本が ci.yml と同じ版・同じコマンドを流すこと(片方だけ変えない)
    import importlib.util

    spec = importlib.util.spec_from_file_location("ci_local", ROOT / "tools" / "ci_local.py")
    ci_local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_local)
    jobs = _wf("ci.yml")["jobs"]
    assert jobs["test"]["strategy"]["matrix"]["python"] == ci_local.VERSIONS
    assert jobs["wheel"]["strategy"]["matrix"]["python"] == ci_local.WHEEL_VERSIONS
    assert set(jobs["test"]["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    lint = next(st for st in jobs["test"]["steps"] if st.get("name") == "Lint")
    assert lint["if"] == f"matrix.python == '{ci_local.LINT_VERSION}'"
    test_runs = "\n".join(st.get("run", "") for st in jobs["test"]["steps"])
    for args in ci_local.SMOKE:
        assert "genmodelfit " + " ".join(args) in test_runs
    wheel_runs = "\n".join(st.get("run", "") for st in jobs["wheel"]["steps"])
    for args in ci_local.WHEEL_COMMANDS:
        assert "genmodelfit " + " ".join(args) in wheel_runs
    assert "tools/check_dist.py dist" in wheel_runs


def test_check_dist(tmp_path: Path) -> None:
    import importlib.util
    import tarfile
    import zipfile

    spec = importlib.util.spec_from_file_location("check_dist", ROOT / "tools" / "check_dist.py")
    check_dist = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_dist)

    def build(extra_whl: list[str], drop_sdist: str = "") -> Path:
        d = tmp_path / f"d{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        with zipfile.ZipFile(d / "g-1-py3-none-any.whl", "w") as z:
            for n in check_dist.NEED_WHEEL + extra_whl:
                z.writestr("genmodelfit-1.dist-info/" + n if n.startswith("licenses/") else n, "x")
        src = tmp_path / "src.txt"
        src.write_text("x")
        with tarfile.open(d / "g-1.tar.gz", "w:gz") as t:
            for n in check_dist.NEED_SDIST:
                if n != drop_sdist:
                    t.add(src, arcname="g-1/" + n)
        return d

    assert check_dist.check(build([])) == []
    assert check_dist.check(build(["genmodelfit/__pycache__/a.pyc"])) != []
    assert check_dist.check(build([], drop_sdist="CHANGELOG.md")) == ["sdist に CHANGELOG.md がない"]
    assert check_dist.check(tmp_path / "missing") != []


def test_release_workflow_shape() -> None:
    doc = _wf("release.yml")
    pub = doc["jobs"]["publish"]
    assert pub["environment"] == "pypi"
    assert pub["permissions"]["id-token"] == "write"
    assert doc[True]["push"]["tags"] == ["v*"]  # YAML 1.1 では on が True になる
    # 手動実行(workflow_dispatch)でタグを選んでも公開しない
    for job in ("publish", "github-release"):
        cond = doc["jobs"][job]["if"]
        assert "github.event_name == 'push'" in cond and "refs/tags/v" in cond
    assert doc["permissions"] == {"contents": "read"}
    # GitHub Release の本文は CHANGELOG のその版の節。日付が無ければ build で止まる(PyPI にも出さない)
    build_runs = [st.get("run", "") for st in doc["jobs"]["build"]["steps"]]
    assert any("tools/changelog_notes.py" in r and "--out release-notes/notes.md" in r for r in build_runs)
    rel_runs = "\n".join(st.get("run", "") for st in doc["jobs"]["github-release"]["steps"])
    assert "--notes-file release-notes/notes.md" in rel_runs
    assert "--generate-notes" not in rel_runs
    assert doc["jobs"]["publish"]["needs"] == "build"


def _changelog_notes():
    import importlib.util

    spec = importlib.util.spec_from_file_location("changelog_notes", ROOT / "tools" / "changelog_notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CHANGELOG = """# Changelog

## [1.1.0] - Unreleased

### Added

- new thing

## [1.0.0] - 2026-01-02

### Fixed

- a fix

## [0.9.0] - 2025-12-01

## [0.8.0] - 2025-11-01

- old

[1.0.0]: https://example.invalid/v1.0.0
"""


def test_changelog_notes_section() -> None:
    cn = _changelog_notes()
    assert cn.release_notes(_CHANGELOG, "1.0.0") == "### Fixed\n\n- a fix\n"
    assert cn.release_notes(_CHANGELOG, "0.8.0") == "- old\n"  # 末尾のリンクの定義は入れない
    with pytest.raises(cn.NotesError, match="日付"):
        cn.release_notes(_CHANGELOG, "1.1.0")  # Unreleased のままではタグを打てない
    with pytest.raises(cn.NotesError, match="空"):
        cn.release_notes(_CHANGELOG, "0.9.0")
    with pytest.raises(cn.NotesError, match="節"):
        cn.release_notes(_CHANGELOG, "2.0.0")


def test_changelog_notes_cli(tmp_path: Path, capsys) -> None:
    cn = _changelog_notes()
    log = tmp_path / "CHANGELOG.md"
    log.write_text(_CHANGELOG, encoding="utf-8")
    out = tmp_path / "notes" / "notes.md"
    assert cn.main(["v1.0.0", "--changelog", str(log), "--out", str(out)]) == 0  # タグの名前(v つき)も受ける
    assert out.read_text(encoding="utf-8") == "### Fixed\n\n- a fix\n"
    assert cn.main(["v1.1.0", "--changelog", str(log)]) == 1
    assert "::error::" in capsys.readouterr().err


def test_changelog_has_the_current_version_and_dated_older_ones() -> None:
    # 今の版の節はある(Unreleased でもよい。日付はタグを打つ前に入れ、release.yml が確かめる)。
    # それより前の版の節は、どれも日付があって release.yml の本文を取り出せる
    from genmodelfit import __version__

    cn = _changelog_notes()
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    cn.section(text, __version__)
    versions = re.findall(r"^## \[([^\]]+)\]", text, flags=re.M)
    assert versions[0] == __version__
    for v in versions[1:]:
        assert cn.release_notes(text, v).strip(), v


def test_data_workflow_shape() -> None:
    doc = _wf("data.yml")
    # 最初の公開: main にコミットした db.json に署名して置く(作り直しと schedule は Issue #19)
    assert set(doc[True]) == {"workflow_dispatch", "push"}
    assert doc[True]["push"] == {"branches": ["main"], "paths": ["src/genmodelfit/data/db.json"]}
    assert doc["permissions"] == {"contents": "read"}
    jobs = doc["jobs"]
    assert set(jobs) == {"sign", "upload", "verify"}
    # 配った直後の確かめ: upload の後に、読むだけの権限で、main と固定の版の両方で取り込む
    assert jobs["verify"]["needs"] == "upload"
    assert jobs["verify"]["strategy"]["matrix"]["client"][0] == "main"
    assert any("update --from-release" in st.get("run", "") for st in jobs["verify"]["steps"])
    runs = [st.get("run", "") for job in jobs.values() for st in job["steps"]]
    assert not any(re.search(r"genmodelfit update(?! --from-release)", r) for r in runs)  # 作り直さない
    # 秘密鍵は data-signing の environment の署名ステップにだけ渡す。main からだけ動く
    assert jobs["sign"]["environment"] == "data-signing"
    assert jobs["sign"]["if"] == "github.ref == 'refs/heads/main'"
    names = [st.get("name") for st in jobs["sign"]["steps"]]
    assert names.index("署名する") < names.index("同梱の公開鍵で確かめる")
    for name, job in jobs.items():
        for step in job["steps"]:
            uses_secret = "secrets." in json.dumps(step)
            assert uses_secret == (name == "sign" and step.get("name") == "署名する"), (name, step)
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False
        want = "write" if name == "upload" else "read"
        assert job["permissions"] == {"contents": want}, name
