"""download(#31): 計画・同意・sha256・続きから・置き場所・転送先の約束。

手元の http.server(127.0.0.1、空いているポート)から落とす。許す転送先(downloader.HOST_POLICY)は
試験のあいだだけ 127.0.0.1 の http にする(既定の約束は test_host_policy_default で見る)。
外へのネットワークには出ない(実際の Hugging Face から落とす試験は test_network_download。既定では飛ばす)。
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote

import pytest
import requests
from clihelp import overlong_lines

import genmodelfit as gmf
from genmodelfit import cli, downloader, i18n
from genmodelfit.db import Database
from genmodelfit.downloader import (
    ChecksumMismatch,
    DiskFull,
    DownloadDeclined,
    DownloadError,
    DownloadRefused,
    HostNotAllowed,
    HostPolicy,
    safe_relpath,
)
from genmodelfit.fetch import USER_AGENT
from genmodelfit.models import Gpu, ModelEntry, ModelFile, SystemInfo, Template

MIB = 1024 * 1024
BODY_A = bytes(range(256)) * (3 * MIB // 256 + 17)  # 3 MiB と少し(1 MiB ずつ受け取るので途中で切れる)
BODY_B = b"vae-weights-" * 5000


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pc(vram: float = 8.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")]
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=32, ram_available_gb=32, gpus=gpus)


class _Handler(http.server.BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}
    seen: ClassVar[list[dict[str, str]]] = []
    range_ok: ClassVar[bool] = True
    cut_after: ClassVar[int | None] = None  # 最初の GET をこのバイト数で切る
    status_once: ClassVar[list[tuple[int, dict[str, str]]]] = []
    port: ClassVar[int] = 0

    def do_GET(self) -> None:
        cls = type(self)
        cls.seen.append(
            {"method": "GET", "path": self.path, **{k.lower(): v for k, v in self.headers.items()}}
        )
        if cls.status_once:
            code, headers = cls.status_once.pop(0)
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/r/"):  # 同じホストの中の転送(許す)
            self._redirect("/f/" + self.path[3:])
            return
        if self.path.startswith("/evil/"):  # 別のホストへの転送(許さない)
            self._redirect(f"http://localhost:{cls.port}/f/" + self.path[6:])
            return
        name = unquote(self.path.rsplit("/", 1)[-1])
        data = cls.files.get(name)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rng = self.headers.get("Range")
        start = 0
        if rng and cls.range_ok:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if cls.cut_after is not None and not rng:
            n, cls.cut_after = cls.cut_after, None
            self.wfile.write(body[:n])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a) -> None:
        pass


@pytest.fixture
def server(tmp_path, monkeypatch):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1\nlogin secret-user\npassword secret-pass\n", encoding="utf-8")
    monkeypatch.setenv("NETRC", str(netrc))
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    _Handler.files = {"tiny_unet.safetensors": BODY_A, "tiny_vae.safetensors": BODY_B}
    _Handler.seen = []
    _Handler.range_ok = True
    _Handler.cut_after = None
    _Handler.status_once = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    monkeypatch.setattr(
        downloader,
        "HOST_POLICY",
        HostPolicy(
            start_hosts=frozenset({"127.0.0.1"}),
            hosts=frozenset({"127.0.0.1"}),
            suffixes=(),
            schemes=("http",),
            any_port=True,
        ),
    )
    yield f"http://127.0.0.1:{_Handler.port}"
    httpd.shutdown()
    httpd.server_close()


def _file(base: str, name: str, body: bytes, directory: str | None, path: str = "f", **kw) -> ModelFile:
    d = {
        "name": name,
        "url": f"{base}/{path}/{name.rsplit('/', 1)[-1]}",
        "sha256": sha(body),
        "size_bytes": len(body),
        "directory": directory,
        "license": "mit",
        "license_url": "https://example.invalid/LICENSE",
        "gated": False,
        "role": "main",
    }
    d.update(kw)
    return ModelFile(**d)


def make_db(base: str, *, files: list[ModelFile] | None = None, content: str = "sfw") -> Database:
    files = files or [
        _file(base, "sub/tiny_unet.safetensors", BODY_A, "diffusion_models"),
        _file(base, "tiny_vae.safetensors", BODY_B, "vae", path="r", role="vae"),  # 転送を 1 回はさむ
    ]
    m = ModelEntry(
        id="test_tiny",
        name="Tiny Test",
        modality="image",
        tasks=["t2i"],
        files=files,
        template=Template(name="test_tiny", username="ComfyUI"),
        precision="fp16",
        license="mit",
        content=content,
    )
    return Database(models=[m])


@pytest.fixture
def comfy(tmp_path) -> Path:
    d = tmp_path / "ComfyUI"
    (d / "models").mkdir(parents=True)
    return d


def plan(db: Database, comfy: Path, **kw):
    return gmf.plan_download("test_tiny", db=db, system_info=pc(), comfyui_dir=comfy, **kw)


def _gets(path_part: str) -> list[dict[str, str]]:
    return [s for s in _Handler.seen if path_part in s["path"]]


# --- 正常 ---------------------------------------------------------------------------------------


def test_download_places_files_by_comfyui_folder(server, comfy):
    db = make_db(server)
    p = plan(db, comfy)
    assert p.ok and p.layout == "comfyui" and Path(p.root) == comfy / "models"
    assert [f.relpath for f in p.files] == [
        "diffusion_models/sub/tiny_unet.safetensors",
        "vae/tiny_vae.safetensors",
    ]
    assert p.download_bytes == len(BODY_A) + len(BODY_B)
    events = []
    res = gmf.download(p, yes=True, progress=events.append)
    a = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors"
    b = comfy / "models" / "vae" / "tiny_vae.safetensors"
    assert a.read_bytes() == BODY_A and b.read_bytes() == BODY_B
    assert not list(comfy.rglob("*.part"))
    assert res.downloaded == [f.relpath for f in p.files] and res.bytes_downloaded == p.download_bytes
    assert {e.phase for e in events} >= {"download", "verify", "done"}
    assert events[-1].done_bytes == p.total_bytes
    # 送ったもの: User-Agent だけ(~/.netrc の資格情報もトークンも付けない)
    for s in _Handler.seen:
        assert s["user-agent"] == USER_AGENT
        assert "authorization" not in s and "cookie" not in s
    # 2 回目は既にあるので通信しない
    _Handler.seen = []
    p2 = plan(db, comfy)
    assert [f.status for f in p2.files] == ["present", "present"] and p2.download_bytes == 0
    res2 = gmf.download(p2, yes=True)
    assert res2.skipped == res.downloaded and _Handler.seen == []


def test_output_dir_uses_same_layout(server, tmp_path):
    db = make_db(server)
    out = tmp_path / "models out"
    res = gmf.download("test_tiny", yes=True, db=db, system_info=pc(), output_dir=out)
    assert (out / "vae" / "tiny_vae.safetensors").read_bytes() == BODY_B
    assert res.plan.layout == "output"


def test_comfyui_dir_must_look_like_comfyui(tmp_path):
    with pytest.raises(DownloadRefused, match="ComfyUI"):
        gmf.plan_download(
            "test_tiny", db=make_db("https://huggingface.co"), system_info=pc(), comfyui_dir=tmp_path
        )
    with pytest.raises(ValueError):
        gmf.plan_download("x", output_dir=tmp_path, comfyui_dir=tmp_path)


# --- 中身の確かめ -------------------------------------------------------------------------------


def test_sha256_mismatch_deletes_file(server, comfy):
    db = make_db(server)
    db.models[0].files[1].sha256 = "0" * 64
    p = plan(db, comfy)
    with pytest.raises(ChecksumMismatch, match="sha256"):
        gmf.download(p, yes=True)
    assert not (comfy / "models" / "vae" / "tiny_vae.safetensors").exists()
    assert not list(comfy.rglob("*.part"))


def test_bigger_than_listed_is_stopped(server, comfy):
    db = make_db(server)
    db.models[0].files[1].size_bytes = len(BODY_B) - 10
    with pytest.raises(DownloadError, match="大きい"):
        gmf.download(plan(db, comfy), yes=True)
    assert not (comfy / "models" / "vae" / "tiny_vae.safetensors").exists()


# --- 途中で切れる・続きから ------------------------------------------------------------------------


def test_cut_connection_resumes_with_range(server, comfy):
    _Handler.cut_after = MIB + 123
    db = make_db(server)
    res = gmf.download(plan(db, comfy), yes=True, sleep=lambda s: None)
    a = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors"
    assert a.read_bytes() == BODY_A
    ranges = [s.get("range") for s in _gets("tiny_unet")]
    # 続きは、書けた分(切れた最後の断片は捨てることがある)の次から
    assert ranges[0] is None and ranges[1].startswith("bytes=")
    assert 0 < int(ranges[1][6:-1]) <= MIB + 123
    assert res.resumed == ["diffusion_models/sub/tiny_unet.safetensors"]


def test_interrupt_keeps_part_and_next_run_resumes(server, comfy):
    db = make_db(server)

    def stop(ev):
        if ev.phase == "download" and ev.file_bytes >= MIB:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        gmf.download(plan(db, comfy), yes=True, progress=stop)
    part = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors.part"
    have = part.stat().st_size
    assert 0 < have < len(BODY_A)
    p2 = plan(db, comfy)
    assert p2.files[0].status == "resume" and p2.files[0].part_bytes == have
    assert p2.download_bytes == len(BODY_A) - have + len(BODY_B)
    _Handler.seen = []
    res = gmf.download(p2, yes=True)
    assert (comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors").read_bytes() == BODY_A
    assert _gets("tiny_unet")[0]["range"] == f"bytes={have}-"
    assert res.resumed and not part.exists()


def test_server_without_range_restarts(server, comfy):
    _Handler.range_ok = False
    db = make_db(server)
    part = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(BODY_A[: 2 * MIB])
    res = gmf.download(plan(db, comfy), yes=True)
    assert (comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors").read_bytes() == BODY_A
    assert res.restarted == ["diffusion_models/sub/tiny_unet.safetensors"] and not res.resumed


def test_corrupt_part_fails_sha_and_is_deleted(server, comfy):
    db = make_db(server)
    part = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"\0" * MIB)  # 中身の違う .part から続けても、最後の sha256 で見つける
    with pytest.raises(ChecksumMismatch):
        gmf.download(plan(db, comfy), yes=True)
    assert not part.exists()
    gmf.download(plan(db, comfy), yes=True)  # 消したので次は最初から
    assert (comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors").read_bytes() == BODY_A


def test_retry_after_is_honored(server, comfy):
    _Handler.status_once = [(429, {"Retry-After": "7"}), (503, {"Retry-After": "2"})]
    slept: list[float] = []
    gmf.download(plan(make_db(server), comfy), yes=True, sleep=slept.append)
    assert slept[:2] == [7.0, 2.0]


def test_401_is_explained(server, comfy):
    _Handler.status_once = [(401, {})]
    with pytest.raises(DownloadRefused, match="ログイン"):
        gmf.download(plan(make_db(server), comfy), yes=True)


# --- 空き・上限 ---------------------------------------------------------------------------------


def test_disk_insufficient(server, comfy, monkeypatch):
    db = make_db(server)
    monkeypatch.setattr(downloader, "_free_bytes", lambda p: 1024**3)  # 1 GiB: 残り + 1 GiB に足りない
    p = plan(db, comfy)
    assert not p.ok and any("空きが足りません" in b for b in p.blocked)
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert _Handler.seen == [] and not any(comfy.rglob("*.safetensors*"))


def test_disk_rechecked_before_start(server, comfy, monkeypatch):
    p = plan(make_db(server), comfy)
    assert p.ok
    monkeypatch.setattr(downloader, "_free_bytes", lambda p: 10)
    with pytest.raises(DiskFull):
        gmf.download(p, yes=True)
    assert _Handler.seen == []


def test_budget(server, comfy):
    p = plan(make_db(server), comfy, budget_gb=1 / 1024)  # 1 MiB
    assert any("--budget" in b for b in p.blocked)
    assert plan(make_db(server), comfy, budget_gb=1).ok


# --- 落とさないもの -----------------------------------------------------------------------------


def test_gated_refused(server, comfy):
    db = make_db(server)
    db.models[0].files[0].gated = True
    p = plan(db, comfy)
    assert any("gated" in b and "ログイン情報" in b for b in p.blocked)
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert _Handler.seen == []


def test_uncensored_needs_explicit_content(server, comfy):
    db = make_db(server, content="uncensored")
    p = plan(db, comfy)
    assert any("無検閲" in b for b in p.blocked)
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert plan(db, comfy, content="all").ok and plan(db, comfy, content="uncensored").ok
    with pytest.raises(downloader.ModelNotFound):  # 名前の一部では sfw の中から探す(info と同じ)
        gmf.plan_download("Tiny Te", db=db, system_info=pc(), comfyui_dir=comfy)


def test_unknown_license_needs_flag(server, comfy):
    db = make_db(server)
    db.models[0].files[1].license = None
    p = plan(db, comfy)
    assert any("ライセンスが分からない" in b for b in p.blocked)
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    p2 = plan(db, comfy, include_unknown_license=True)
    assert p2.ok and any("ライセンスが分からない" in w for w in p2.warnings)
    gmf.download(p2, yes=True)


def test_not_allowlisted_license_is_warned(server, comfy):
    db = make_db(server)
    db.models[0].files[0].license = "cc-by-nc-4.0"
    p = plan(db, comfy)
    assert p.ok and not p.files[0].license_allowed
    assert any("許可リスト" in w and "cc-by-nc-4.0" in w for w in p.warnings)


@pytest.mark.parametrize(
    "directory,name",
    [
        ("vae", "../evil.safetensors"),
        ("vae", "a/../../evil.safetensors"),
        ("vae", "/abs/evil.safetensors"),
        ("vae", "C:/evil.safetensors"),
        ("vae", "C:evil.safetensors"),
        ("vae", "a\\..\\..\\evil.safetensors"),
        ("vae", "CON"),
        ("vae", "nul.safetensors"),
        ("vae", "evil.safetensors."),
        ("vae", "evil.safetensors:stream"),
        ("..", "evil.safetensors"),
        ("../..", "evil.safetensors"),
        ("C:", "evil.safetensors"),
        ("/tmp", "evil.safetensors"),
        # 予約名・末尾の点や空白はフォルダ名でも(#31 の確認の指摘 7)
        ("con", "x.safetensors"),
        ("nul", "x.safetensors"),
        ("COM1", "x.safetensors"),
        ("vae", "COM\u00b9"),
        ("vae", "LPT\u00b2.bin"),
        ("CONIN$", "x.safetensors"),
        ("vae", "conout$"),
        ("vae", "nul .txt"),
        ("models.", "x.safetensors"),
        ("vae", "x\u202etxt.safetensors"),  # 右から左への上書き
        ("vae", "x.safetensors.part"),  # 落とすときの一時ファイルと重なる
        ("vae", "x.lock"),
    ],
)
def test_path_attacks_refused(server, comfy, directory, name):
    assert safe_relpath(directory, name) is None
    db = make_db(server, files=[_file(server, name, BODY_B, directory)])
    p = plan(db, comfy)
    assert p.files[0].status == "unavailable" and not p.ok
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert _Handler.seen == []


def test_safe_relpath_allows_comfyui_subfolders():
    assert safe_relpath("text_encoders", "split/a.gguf") == ("text_encoders", "split", "a.gguf")
    assert safe_relpath(None, "x.safetensors") == ("other", "x.safetensors")


def test_symlink_escape_refused(server, comfy, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = comfy / "models" / "vae"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("シンボリックリンクを作れない")
        # Windows で権限が無ければジャンクション(管理者でなくても作れる。同じく外へ出る)
        import subprocess

        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if r.returncode != 0:
            pytest.skip("シンボリックリンクもジャンクションも作れない")
    p = plan(make_db(server), comfy)
    assert p.files[1].status == "unavailable" and "外" in (p.files[1].reason or "")
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert list(outside.iterdir()) == []


def test_redirect_to_other_host_refused(server, comfy):
    db = make_db(server, files=[_file(server, "tiny_vae.safetensors", BODY_B, "vae", path="evil")])
    with pytest.raises(HostNotAllowed, match="localhost"):
        gmf.download(plan(db, comfy), yes=True)
    assert [s["path"] for s in _Handler.seen] == ["/evil/tiny_vae.safetensors"]  # 転送先には送らない
    assert not any(comfy.rglob("tiny_vae*"))


def test_host_policy_default():
    pol = downloader.HOST_POLICY  # 既定の約束(この試験は server を使わないので差し替えていない)
    ok = [
        "https://huggingface.co/a/b/resolve/main/x.safetensors",
        "https://cdn-lfs.hf.co/repos/x",
        "https://cdn-lfs-us-1.hf.co/x",
        "https://cas-bridge.xethub.hf.co/xet-bridge-us/x",
        "https://objects.githubusercontent.com/x",
        "https://release-assets.githubusercontent.com/x",
    ]
    bad = [
        "http://huggingface.co/a",  # https だけ
        "https://huggingface.co.evil.com/a",
        "https://evilhf.co/a",
        "https://huggingface.co@evil.com/a",
        "https://user:pw@huggingface.co/a",
        "https://huggingface.co:8443/a",
        "https://civitai.com/api/download/models/1",
        "file:///etc/passwd",
        "https://raw.githubusercontent.com/x",
    ]
    for u in ok:
        assert pol.allowed(u), u
    for u in bad:
        assert not pol.allowed(u), u
    assert pol.allowed("https://huggingface.co/x", start=True)
    assert not pol.allowed("https://cdn-lfs.hf.co/x", start=True)  # 最初の URL は一覧のホストだけ
    assert pol.schemes == ("https",) and not pol.any_port


def test_civitai_url_is_unavailable(comfy):
    db = make_db("https://civitai.com/api/download/models")
    p = plan(db, comfy)
    assert all(f.status == "unavailable" for f in p.files) and not p.ok


# --- 同意 ---------------------------------------------------------------------------------------


def test_consent_declined_and_missing(server, comfy):
    p = plan(make_db(server), comfy)
    seen = []
    with pytest.raises(DownloadDeclined):
        gmf.download(p, confirm=lambda pl: seen.append(pl) or False)
    assert seen == [p] and _Handler.seen == []
    with pytest.raises(DownloadRefused, match="同意"):
        gmf.download(p)
    assert _Handler.seen == [] and not any(comfy.rglob("*.safetensors*"))
    gmf.download(p, confirm=lambda pl: True)
    assert (comfy / "models" / "vae" / "tiny_vae.safetensors").exists()


def test_existing_different_file_not_overwritten(server, comfy):
    dest = comfy / "models" / "vae" / "tiny_vae.safetensors"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * len(BODY_B))  # 同じ大きさで中身が違う
    p = plan(make_db(server), comfy)
    assert p.files[1].status == "conflict" and not p.ok
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert dest.read_bytes() == b"x" * len(BODY_B)


# --- CLI ----------------------------------------------------------------------------------------


@pytest.fixture
def db_file(server, tmp_path) -> Path:
    path = tmp_path / "db.json"
    make_db(server).save(path)
    return path


HW = ["--vram", "8", "--ram", "32"]


def _tree(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*"))


def test_cli_list_makes_no_request_and_writes_nothing(db_file, comfy, monkeypatch, capsys):
    from requests.adapters import HTTPAdapter

    def no_net(*a, **k):
        raise AssertionError("--list で通信した")

    monkeypatch.setattr(HTTPAdapter, "send", no_net)
    before = _tree(comfy)
    assert (
        cli.main(["download", "test_tiny", "--list", "--db", str(db_file), "--comfyui-dir", str(comfy), *HW])
        == 0
    )
    out = capsys.readouterr().out
    assert "diffusion_models/sub/tiny_unet.safetensors" in out and "ライセンス: mit" in out
    assert "https://example.invalid/LICENSE" in out and "空き" in out
    # 幅 100 に収める(1 つで長いパスだけの行は、途中で切らずにはみ出してよい。#76)
    assert not overlong_lines(out.splitlines(), 100)
    assert (
        cli.main(
            ["download", "test_tiny", "--list", "--json", "--db", str(db_file), "--comfyui-dir", str(comfy)]
        )
        == 0
    )
    env = json.loads(capsys.readouterr().out)
    assert env["listed"] is True and env["result"] is None and env["plan"]["ok"] is True
    assert _tree(comfy) == before and _Handler.seen == []


def test_cli_json_contract(db_file, comfy, capsys):
    args = ["download", "test_tiny", "--db", str(db_file), "--comfyui-dir", str(comfy), "--json", *HW]
    assert cli.main(args) == 1  # --json で落とすなら --yes が要る
    assert "--yes" in json.loads(capsys.readouterr().out)["error"]
    assert cli.main([*args, "--yes"]) == 0
    env = json.loads(capsys.readouterr().out)
    assert env["command"] == "download" and env["listed"] is False
    assert {"genmodelfit", "system", "database", "plan", "result"} <= set(env)
    plan_keys = {
        "model_id",
        "name",
        "model_name",
        "precision",
        "fit_level",
        "layout",
        "root",
        "files",
        "total_bytes",
        "download_bytes",
        "disk_free_bytes",
        "disk_needed_bytes",
        "blocked",
        "warnings",
        "alternatives",
        "ok",
    }
    assert plan_keys <= set(env["plan"])
    f0 = env["plan"]["files"][0]
    for k in (
        "name",
        "relpath",
        "dest",
        "url",
        "sha256",
        "size_bytes",
        "directory",
        "license",
        "license_url",
    ):
        assert k in f0
    for k in ("license_allowed", "gated", "status", "part_bytes", "remaining_bytes", "reason"):
        assert k in f0
    r = env["result"]
    assert r["downloaded"] == [f["relpath"] for f in env["plan"]["files"]] and r["bytes_downloaded"] > 0
    assert {"resumed", "restarted", "skipped", "seconds", "files"} <= set(r)


def test_cli_blocked_json_error(server, tmp_path, comfy, capsys):
    db = make_db(server, content="uncensored")
    path = tmp_path / "db2.json"
    db.save(path)
    assert (
        cli.main(["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), "--json", "--yes"])
        == 1
    )
    env = json.loads(capsys.readouterr().out)
    assert "無検閲" in env["error"] and env["plan"]["ok"] is False and env["result"] is None


def test_cli_asks_for_consent(db_file, comfy, monkeypatch, capsys):
    args = ["download", "test_tiny", "--db", str(db_file), "--comfyui-dir", str(comfy), *HW]
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.main(args) == 1
    out = capsys.readouterr().out
    assert "同意して落としますか" in out and "ライセンス: mit" in out
    assert _Handler.seen == []
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(args) == 0
    assert "sha256 はすべて一覧と一致" in capsys.readouterr().out
    assert (comfy / "models" / "vae" / "tiny_vae.safetensors").exists()


def test_cli_unknown_model(db_file, capsys):
    assert cli.main(["download", "no_such_model", "--db", str(db_file), "--list", *HW]) == 1
    assert "見つかりません" in capsys.readouterr().err


# --- ライブラリは何も出さない ------------------------------------------------------------------


def test_library_prints_nothing(server, comfy, capfd):
    db = make_db(server)
    p = gmf.plan_download("test_tiny", db=db, system_info=pc(), comfyui_dir=comfy)
    gmf.download(p, yes=True, progress=lambda e: None)
    gmf.download(gmf.plan_download("test_tiny", db=db, system_info=pc(), comfyui_dir=comfy), yes=True)
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_plan_on_bundled_list_is_offline(monkeypatch):
    """同梱の一覧で計画を作る(通信しない)。名前で選ぶと、逃がさずに載る中で一番精度の高いもの。"""
    from requests.adapters import HTTPAdapter

    monkeypatch.setattr(
        HTTPAdapter, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("通信した"))
    )
    # 本物のディスクの空きを見ない(空きが 20 GB 前後の PC だと bf16 の計画が disk で止まり、PC によって落ちる)
    monkeypatch.setattr(downloader, "_free_bytes", lambda p: 1024**4)
    db = gmf.load_db(use_cache=False)
    big = gmf.plan_download("Z-Image-Turbo", db=db, system_info=pc(24), output_dir="unused-dir")
    assert big.model_id == "image_z_image_turbo" and big.precision == "bf16" and big.ok
    small = gmf.plan_download(
        "Z-Image-Turbo", db=db, system_info=pc(24), output_dir="unused-dir", budget_gb=15
    )
    assert small.model_id == "image_z_image_turbo_int8"
    prec = gmf.plan_download(
        "image_z_image_turbo", db=db, system_info=pc(24), output_dir="x", precision="int8"
    )
    assert prec.model_id == "image_z_image_turbo_int8"
    with pytest.raises(downloader.ModelNotFound, match="精度"):
        gmf.plan_download("Z-Image-Turbo", db=db, system_info=pc(24), output_dir="x", precision="fp4")
    assert not Path("unused-dir").exists()


def test_requests_exceptions_are_caught_in_loop():
    # 接続の失敗の種類(落とすときにやり直すもの)が requests にあること
    assert issubclass(requests.exceptions.ChunkedEncodingError, requests.RequestException)


# --- 実際の Hugging Face から(手動。GENMODELFIT_NETWORK_TESTS=1) -----------------------------------

E2E_MODEL = (
    "utility_birefnet_remove_background"  # 約 424 MiB、MIT、ComfyUI 公式テンプレート(background_removal)
)


@pytest.mark.network
def test_network_download(tmp_path):
    comfy = tmp_path / "ComfyUI"
    (comfy / "models").mkdir(parents=True)
    p = gmf.plan_download(E2E_MODEL, comfyui_dir=comfy, db=gmf.load_db(use_cache=False), system_info=pc(8))
    assert p.ok, p.blocked

    def stop(ev):  # 途中で 1 回止めて、続きから落とす
        if ev.phase == "download" and ev.file_bytes >= 64 * MIB and not stop.done:
            stop.done = True
            raise KeyboardInterrupt

    stop.done = False
    with pytest.raises(KeyboardInterrupt):
        gmf.download(p, yes=True, progress=stop)
    p2 = gmf.plan_download(E2E_MODEL, comfyui_dir=comfy, db=gmf.load_db(use_cache=False), system_info=pc(8))
    assert p2.files[0].status == "resume"
    res = gmf.download(p2, yes=True)
    dest = comfy / "models" / "background_removal" / "birefnet.safetensors"
    try:
        assert res.resumed == ["background_removal/birefnet.safetensors"]
        h = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert h == p.files[0].sha256
    finally:
        dest.unlink(missing_ok=True)


# --- 確認の指摘(#31 の Security Checker)の回帰 ---------------------------------------------------

BS = chr(92)
EVIL_HOPS = [
    "https://evil.com" + BS + ".hf.co/x",  # urlsplit は *.hf.co、urllib3 は evil.com と読む
    "//evil.com" + BS + ".huggingface.co/x",  # 相対の転送先(解決すると上と同じ)
    "https://evil.com" + BS + "@huggingface.co/x",
    "https://huggingface.co" + BS + "@evil.com/",
    "https://evil.com%2f.hf.co/x",
    "https://evil.com%00.hf.co/x",
    "https://evil.com\t.hf.co/x",
    "https://evil.com#.hf.co",
    "https://evil.com?.hf.co",
    "https://huggingface.co@evil.com/x",
    "https://huggingface.co.evil.com/x",
    "//evil.com/x",
    "http://huggingface.co/x",
    "https://huggingface.co:8443/x",
    "https://huggingfacе.co/x",  # キリル文字の е
    "https://ｈf.co/x",
    "https://evil.com;.hf.co/x",  # DNS の名前として正しくない(英数字とハイフンだけ)
    "https://evil.com$.hf.co/x",
    "https://" + "a" * 64 + ".hf.co/x",  # 1 つの区切りが 63 文字を超える
]


class _RedirectAdapter(requests.adapters.HTTPAdapter):
    """huggingface.co の最初の応答だけを手元で作る(302 で loc へ)。ほかはそのまま本物の接続へ。"""

    def __init__(self, loc: str):
        super().__init__()
        self.loc = loc

    def send(self, request, **kw):
        import io

        from requests.structures import CaseInsensitiveDict

        r = requests.Response()
        r.status_code, r.url, r.request = 302, request.url, request
        # 本物の応答の見出しは latin-1 で読まれる(http.client と同じ形で渡す)
        loc = self.loc.encode("utf-8").decode("latin-1")
        r.headers = CaseInsensitiveDict({"Location": loc, "Content-Length": "0"})
        r.raw = io.BytesIO(b"")
        return r


@pytest.fixture
def connections(monkeypatch):
    """接続の層(urllib3 の create_connection)で、つなごうとした先をすべて記録し、つながせない。"""
    import urllib3.util.connection as uc

    seen: list = []

    def fake(address, *a, **k):
        seen.append(address)
        raise OSError("試験: つながせない")

    monkeypatch.setattr(uc, "create_connection", fake)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    return seen


_REAL_MAKE_SESSION = downloader.make_session


def _evil_session(loc: str) -> requests.Session:
    s = _REAL_MAKE_SESSION()
    s.mount("https://huggingface.co/", _RedirectAdapter(loc))
    return s


@pytest.mark.parametrize("loc", EVIL_HOPS)
def test_redirect_never_connects_to_other_host(connections, loc):
    with pytest.raises(HostNotAllowed):
        downloader._open(
            _evil_session(loc), "https://huggingface.co/a/resolve/main/f", {}, downloader.HOST_POLICY, 5
        )
    assert connections == []  # 許していないホストへは、つなごうとすらしない


def test_redirect_to_cdn_connects_only_there(connections):
    """対照: 許すホスト(Xet の CDN)への転送はそこへつなぎに行く(接続の記録が効いていること)。"""
    s = _evil_session("https://cas-bridge.xethub.hf.co/x")
    with pytest.raises(requests.ConnectionError):
        downloader._open(s, "https://huggingface.co/a/resolve/main/f", {}, downloader.HOST_POLICY, 5)
    assert [c[0] for c in connections] == ["cas-bridge.xethub.hf.co"]


def test_download_via_evil_redirect_writes_nothing(connections, monkeypatch, comfy):
    db = make_db("https://huggingface.co/example/tiny/resolve/main")
    p = plan(db, comfy)
    assert p.ok
    monkeypatch.setattr(
        downloader, "make_session", lambda: _evil_session("https://evil.com" + BS + ".hf.co/x")
    )
    with pytest.raises(HostNotAllowed):
        gmf.download(p, yes=True)
    assert connections == [] and not any(comfy.rglob("*.safetensors*"))


@pytest.mark.parametrize(
    "url", ["https://huggingface.co/x", "https://cdn-lfs-us-1.hf.co/x", "https://hf.co/x"]
)
def test_url_host_agrees_with_urllib3(url):
    import urllib3

    _scheme, host, _port = downloader.url_host(url)
    assert host == urllib3.util.parse_url(requests.Request("GET", url).prepare().url).host


def test_truncated_200_without_range_terminates(server, comfy, monkeypatch):
    """Range に応じず、毎回途中で切る相手: 最初からのやり直しに上限があり、止まる。"""
    _Handler.range_ok = False
    orig = _Handler.do_GET

    def do_get(self):
        name = self.path.rsplit("/", 1)[-1]
        if name != "tiny_unet.safetensors":
            return orig(self)
        type(self).seen.append({"path": self.path})
        data = type(self).files[name]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data[: MIB + 5])
        self.wfile.flush()
        self.close_connection = True

    monkeypatch.setattr(_Handler, "do_GET", do_get)
    with pytest.raises(DownloadError, match="Range"):
        gmf.download(plan(make_db(server), comfy), yes=True, sleep=lambda s: None)
    assert len(_gets("tiny_unet")) <= downloader.MAX_RESTARTS + 2


def test_unverified_present_is_hashed_before_skip(server, comfy):
    """verify_existing=False の計画で present でも、download はその場で sha256 を計る
    (違えば上書きもしない)。"""
    dest = comfy / "models" / "vae" / "tiny_vae.safetensors"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * len(BODY_B))  # 同じ大きさで中身が違う
    p = gmf.plan_download(
        "test_tiny", db=make_db(server), system_info=pc(), comfyui_dir=comfy, verify_existing=False
    )
    assert p.files[1].status == "present" and not p.files[1].verified
    with pytest.raises(DownloadRefused, match="sha256"):
        gmf.download(p, yes=True)
    assert dest.read_bytes() == b"x" * len(BODY_B)


def test_id_plus_precision_measures_existing(server, comfy):
    """ID と精度を指定して候補が 2 つ以上でも、選んだワークフローの既にあるファイルは sha256 まで計る。"""
    import copy

    db = make_db(server)
    m2 = copy.deepcopy(db.models[0])
    m2.id = "test_tiny_b"
    db = Database(models=[db.models[0], m2])
    dest = comfy / "models" / "vae" / "tiny_vae.safetensors"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"x" * len(BODY_B))
    p = gmf.plan_download("test_tiny", precision="fp16", db=db, system_info=pc(), comfyui_dir=comfy)
    assert len(p.alternatives) == 1
    assert p.files[1].status == "conflict" and not p.ok


def test_file_replaced_after_plan_is_not_trusted(server, comfy):
    db = make_db(server)
    gmf.download(plan(db, comfy), yes=True)
    p = plan(db, comfy)
    assert [f.verified for f in p.files] == [True, True]
    dest = comfy / "models" / "vae" / "tiny_vae.safetensors"
    dest.write_bytes(b"y" * len(BODY_B))  # 計画のあとで差し替えられた
    with pytest.raises(DownloadRefused, match="sha256"):
        gmf.download(p, yes=True)
    assert dest.read_bytes()[:1] == b"y"


def test_concurrent_download_same_file_is_locked(server, comfy):
    db = make_db(server)
    lock = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()} 0\n", encoding="ascii")  # 動いているプロセス(自分)の印
    with pytest.raises(DownloadError, match="別の download"):
        gmf.download(plan(db, comfy), yes=True)
    assert lock.exists()
    lock.write_text("999999999 0\n", encoding="ascii")  # もう無いプロセスの印は片付けて続ける
    gmf.download(plan(db, comfy), yes=True)
    assert not list(comfy.rglob("*.lock"))
    assert (comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors").read_bytes() == BODY_A


def test_two_threads_same_destination(server, comfy):
    db = make_db(server)
    p1, p2 = plan(db, comfy), plan(db, comfy)
    out: dict[str, object] = {}

    def run(k, p):
        try:
            out[k] = gmf.download(p, yes=True, sleep=lambda s: None).downloaded
        except DownloadError as e:
            out[k] = e

    ts = [threading.Thread(target=run, args=(k, p)) for k, p in (("a", p1), ("b", p2))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert (comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors").read_bytes() == BODY_A
    assert not list(comfy.rglob("*.part")) and not list(comfy.rglob("*.lock"))
    errors = [v for v in out.values() if isinstance(v, DownloadError)]
    assert all("別の download" in str(e) or "上書き" in str(e) for e in errors), errors


def test_lock_removed_even_while_another_download_reads_it(server, comfy, monkeypatch):
    """Windows では、別の download が .lock を読んでいる間は消せない(PermissionError)。
    持ち主は少し待ってやり直し、.lock を残さない(#85。2 つのスレッドの試験がまれに落ちた順の再現)。"""
    p = plan(make_db(server), comfy)
    real_unlink = Path.unlink
    busy = {"n": 3}

    def unlink(self, *a, **kw):
        if self.name.endswith(".lock") and busy["n"] > 0:
            busy["n"] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    gmf.download(p, yes=True, sleep=lambda s: None)
    assert busy["n"] == 0
    assert not list(comfy.rglob("*.lock")) and not list(comfy.rglob("*.part"))


def test_file_placed_while_waiting_for_lock_is_skipped(server, comfy, monkeypatch):
    """鍵を待つ間に別の download が同じファイルを置き終えたら、落とさずに飛ばし .part を残さない
    (2 つのスレッドの試験がまれに落ちた順を、決まった順で再現する)。"""
    p = plan(make_db(server), comfy)
    dest = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors"
    real_lock = downloader._file_lock

    def lock_then_place(path, name):
        dest.write_bytes(BODY_A)  # 鍵を取る直前に、別の download が置き終えた
        return real_lock(path, name)

    monkeypatch.setattr(downloader, "_file_lock", lock_then_place)
    r = gmf.download(p, yes=True, sleep=lambda s: None)
    assert r.skipped == [p.files[0].relpath] and p.files[0].relpath not in r.downloaded
    assert dest.read_bytes() == BODY_A
    assert not list(comfy.rglob("*.part")) and not list(comfy.rglob("*.lock"))


def test_file_placed_during_transfer_leaves_no_part(server, comfy, monkeypatch):
    """落としている間に同じ名前のファイルが置かれたら、上書きせず、落とした .part も残さない。"""
    p = plan(make_db(server), comfy)
    dest = comfy / "models" / "diffusion_models" / "sub" / "tiny_unet.safetensors"

    def emit(ev):
        if getattr(ev, "phase", None) == "verify" and not dest.exists():
            dest.write_bytes(b"other")

    with pytest.raises(DownloadRefused, match="上書き"):
        gmf.download(p, yes=True, sleep=lambda s: None, progress=emit)
    assert dest.read_bytes() == b"other"
    assert not list(comfy.rglob("*.part")) and not list(comfy.rglob("*.lock"))


EXT = "\\\\?\\"  # Windows の拡張パスの頭


@pytest.mark.skipif(os.name != "nt", reason="Windows の拡張パスの試験")
def test_inside_ignores_extended_paths_of_files_being_deleted(tmp_path, monkeypatch):
    """別のスレッドが消している途中(削除待ち)の .lock・.part を resolve() すると、Python が拡張パス
    (頭に EXT が付いたもの)のまま返す。それを「置き場所の外」と誤って断っていた(#41。
    test_two_threads_same_destination が負荷のもとで 60 回に 7〜8 回落ちた原因)。最後の名前は解決しない。"""
    root = tmp_path / "models"
    (root / "sub").mkdir(parents=True)
    lock = root / "sub" / "x.safetensors.lock"
    orig = Path.resolve

    def resolve(self, strict=False):
        r = orig(self, strict)
        return Path(EXT + str(r)) if self.name.endswith((".lock", ".part")) else r

    monkeypatch.setattr(Path, "resolve", resolve)
    assert downloader._inside(lock, root)
    assert downloader._inside(root / "sub" / "x.safetensors.part", root)
    assert not downloader._inside(tmp_path / "other" / "x.lock", root)
    assert not downloader._inside(root / ".." / "x.safetensors", root)
    assert downloader._real(Path(EXT + "C:\\a\\b")) == Path("C:\\a\\b")
    assert downloader._real(Path(EXT + "UNC\\srv\\share\\a")) == Path("\\\\srv\\share\\a")


def test_inside_still_resolves_links_at_the_end(tmp_path):
    """最後の名前がリンクなら、その先を見る(置き場所の外を指すリンクに書かない)。"""
    root = tmp_path / "models"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    link = root / "x.safetensors"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("シンボリックリンクを作れない環境")
    assert not downloader._inside(link, root)


def test_crafted_plan_outside_creates_nothing(server, comfy, tmp_path):
    p = plan(make_db(server), comfy)
    outside = tmp_path / "OUTSIDE" / "deep"
    p.files[1].dest = str(outside / "x.safetensors")
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert not (tmp_path / "OUTSIDE").exists()


def test_os_error_becomes_download_error(server, comfy, monkeypatch, tmp_path, capsys):
    def no_space(*a, **k):
        raise OSError(28, "No space left on device")

    path = tmp_path / "db.json"
    make_db(server).save(path)
    monkeypatch.setattr(downloader.os, "replace", no_space)
    with pytest.raises(DownloadError, match="No space"):
        gmf.download(plan(make_db(server), comfy), yes=True)
    args = ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), "--json", "--yes", *HW]
    assert cli.main(args) == 1
    env = json.loads(capsys.readouterr().out)
    assert "No space" in env["error"] and env["result"] is None


def test_other_folder_warned_with_comfyui(server, comfy):
    db = make_db(server, files=[_file(server, "tiny_vae.safetensors", BODY_B, None)])
    p = plan(db, comfy)
    assert p.files[0].relpath == "other/tiny_vae.safetensors"
    assert any("models/other" in w for w in p.warnings)
    assert any("models/other" in w for w in p.to_dict()["warnings"])  # JSON にも


def test_consent_when_nothing_to_download(db_file, comfy, monkeypatch, capsys):
    base = ["download", "test_tiny", "--db", str(db_file), "--comfyui-dir", str(comfy), *HW]
    assert cli.main([*base, "--yes"]) == 0
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(base) == 0
    out = capsys.readouterr().out
    assert "落とすものは無い" in out and "要る量" not in out and "落とすものはありません" in out


# --- パスに空白のある URL(再確認の指摘。hf:Meina/MeinaHentai の「meinaHentai V3 - baked VAE」) ---


def test_space_in_path_downloads(server, comfy):
    _Handler.files["tiny vae - baked.safetensors"] = BODY_B
    db = make_db(server, files=[_file(server, "tiny vae - baked.safetensors", BODY_B, "vae")])
    p = plan(db, comfy)
    assert p.ok and p.files[0].status == "download"
    gmf.download(p, yes=True)
    assert (comfy / "models" / "vae" / "tiny vae - baked.safetensors").read_bytes() == BODY_B
    assert _gets("tiny%20vae%20-%20baked")  # requests と同じく % に符号化して送った


def test_bundled_url_with_space_is_not_refused_as_host():
    db = gmf.load_db(use_cache=False)
    m = db.get("hf:Meina/MeinaHentai")
    if m is None:
        pytest.skip("同梱の一覧にない")
    f = next(x for x in m.files if " " in (x.url or ""))
    assert downloader.HOST_POLICY.allowed(f.url, start=True)
    p = gmf.plan_download(m.id, db=db, system_info=pc(), output_dir="unused-dir", content="all")
    assert all(pf.status != "unavailable" for pf in p.files), [pf.reason for pf in p.files]
    assert not Path("unused-dir").exists()


@pytest.mark.parametrize(
    "url,why",
    [
        ("https://evil.com" + BS + ".hf.co/x", "URL の形が不正"),
        ("https://evil.com .hf.co/x", "URL の形が不正"),
        ("https://huggingface.co/a" + BS + "b", "URL の形が不正"),
        ("https://huggingface.co/a\tb", "URL の形が不正"),
        ("https://civitai.com/api/download/models/1", "許していない取得先"),
        ("http://huggingface.co/x", "許していない取得先"),
    ],
)
def test_refusal_reasons_are_distinct(comfy, url, why):
    f = ModelFile(
        name="x.safetensors", url=url, sha256="0" * 64, size_bytes=10, directory="vae", license="mit"
    )
    m = ModelEntry(id="t_reason", name="Reason", modality="image", files=[f], template=Template(name="t"))
    p = gmf.plan_download("t_reason", db=Database(models=[m]), system_info=pc(), comfyui_dir=comfy)
    assert p.files[0].status == "unavailable" and why in (p.files[0].reason or "")


def test_stale_lock_retire_keeps_fresh_lock(tmp_path, monkeypatch):
    """残骸と判断したあと、改名までの間に別のプロセスが新しい .lock を作っていたら、それを消さずに戻す。"""
    lock = tmp_path / "x.safetensors.lock"
    lock.write_text(f"{os.getpid()} 0\n", encoding="ascii")  # 実は生きている印
    assert downloader._retire_stale_lock(lock) is False
    assert lock.read_text(encoding="ascii").startswith(str(os.getpid()))
    assert [x.name for x in tmp_path.iterdir()] == ["x.safetensors.lock"]  # 墓標は残らない
    lock.write_text("999999999 0\n", encoding="ascii")  # 本当の残骸
    assert downloader._retire_stale_lock(lock) is True and list(tmp_path.iterdir()) == []


# --- 止めたときの案内・置き場所・確かめる場所・環境変数(#75) ----------------------------------------

HF = "https://huggingface.co"
PAGE = "https://example.invalid/tiny"
ERR_JA = "エラー: 落とせません(上を見てください)"


def _cli_db(tmp_path: Path, *, content: str = "sfw", unknown: bool = False, gated: bool = False) -> Path:
    """通信しない試験用の一覧(--list か、止まって落とさない実行だけに使う)。"""
    db = make_db(HF, content=content)
    db.models[0].homepage = PAGE
    if unknown:
        db.models[0].files[1].license = None
        db.models[0].files[1].license_url = None
    if gated:
        db.models[0].files[0].gated = True
    path = tmp_path / "guide_db.json"
    db.save(path)
    return path


def _run(args: list[str], capsys) -> tuple[int, str, str]:
    code = cli.main(args)
    out, err = capsys.readouterr()
    return code, out, err


def _nows(text: str) -> str:
    """空白・改行を除いた文(折り返しの位置によらず比べる)。"""
    return re.sub(r"\s+", "", text)


def _cmd_lines(out: str) -> list[str]:
    return [
        x.strip() for x in out.splitlines() if x.strip().startswith(("genmodelfit download", "python -m"))
    ]


def _expected(db: Path, comfy: Path, *flags: str) -> str:
    q = cli.shell_quote
    parts = ["genmodelfit", "download", "test_tiny", "--comfyui-dir", q(str(comfy)), *flags]
    return " ".join([*parts, "--db", q(str(db)), *HW])


@pytest.mark.parametrize(
    ("content", "unknown", "flags"),
    [
        ("uncensored", False, ["--uncensored"]),
        ("sfw", True, ["--any-license"]),
        ("uncensored", True, ["--uncensored", "--any-license"]),
    ],
    ids=["uncensored", "unknown-license", "both"],
)
def test_blocked_prints_one_command_with_all_flags(tmp_path, comfy, capsys, content, unknown, flags):
    """端末でない実行(試験は端末でない)は今までどおり止め、指定を全部足したコマンドを 1 行出す。"""
    db = _cli_db(tmp_path, content=content, unknown=unknown)
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(db), *HW]
    code, out, err = _run(args, capsys)
    lines = out.splitlines()
    assert "落とすなら(端末で実行すれば、指定の代わりに y/N で尋ねます):" in lines
    assert _cmd_lines(out) == [_expected(db, comfy, *flags)]  # 1 行に全部(--list・--yes は付けない)
    assert ("--uncensored:" in out) == ("--uncensored" in flags)
    assert ("--any-license:" in out) == ("--any-license" in flags)
    reasons = [x for x in lines if "落とせません:" in x]
    assert len(reasons) == len(set(reasons)) == len(flags)  # 理由は 1 回ずつ
    assert code == 1 and err.strip() == ERR_JA  # stderr に短い 1 行だけ(理由を繰り返さない)
    assert not any(comfy.rglob("*.safetensors*"))


@pytest.mark.parametrize(
    ("content", "unknown", "flags"),
    [
        ("uncensored", False, ["--uncensored"]),
        ("sfw", True, ["--any-license"]),
        ("uncensored", True, ["--uncensored", "--any-license"]),
    ],
    ids=["uncensored", "unknown-license", "both"],
)
def test_list_shows_what_will_be_asked(tmp_path, comfy, capsys, monkeypatch, content, unknown, flags):
    """端末の --list は「落とせません」ではなく、尋ねることを 1 行と、尋ねずに落とすコマンド(--yes 付き)
    を出す。"""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    db = _cli_db(tmp_path, content=content, unknown=unknown)
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(db), *HW, "--list"]
    code, out, err = _run(args, capsys)
    assert code == 0 and err == ""
    consent = [x for x in out.splitlines() if x.startswith("  同意:")]
    assert (
        "落とせません" not in out and len(consent) == 1 and _nows("落とす前に y/N で尋ねます") in _nows(out)
    )
    assert ("無検閲" in out) == ("--uncensored" in flags)
    assert (_nows("ライセンスの記載が無い: tiny_vae.safetensors") in _nows(out)) == ("--any-license" in flags)
    assert "ライセンスが分からないファイルを含みます" not in out  # 同意の行にまとめる(注意を重ねない)
    assert "尋ねずに落とすなら(スクリプトなど):" in out.splitlines()
    assert _cmd_lines(out) == [_expected(db, comfy, *flags, "--yes")]  # 尋ねないので --yes も要る
    _, out, _ = _run([*args, "--json"], capsys)  # JSON は止める理由をそのまま(アプリは指定で決める)
    plan_json = json.loads(out)["plan"]
    assert plan_json["ok"] is False and set(plan_json["blocked_codes"]) <= {"uncensored", "unknown_license"}
    # 実行しても尋ねないとき(--yes・端末でない)の --list は「落とせません」と指定のコマンド
    _, out, _ = _run([*args, "--yes"], capsys)
    assert "落とせません" in out and "同意:" not in out
    assert "落とすなら(--yes を外せば y/N で尋ねます。または下の指定を足す):" in out.splitlines()
    assert _cmd_lines(out) == [_expected(db, comfy, *flags)]
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    _, out, _ = _run(args, capsys)
    assert "落とせません" in out and "落とすなら(端末で実行すれば、指定の代わりに y/N で尋ねます):" in out


def test_blocked_command_keeps_other_args_and_quotes(tmp_path, capsys):
    comfy = tmp_path / "Comfy UI dir"
    (comfy / "models").mkdir(parents=True)
    db = _cli_db(tmp_path, unknown=True)
    args = ["download", "Tiny Test", "--comfyui-dir", str(comfy), "--db", str(db), "--budget", "30.123456789"]
    args += ["--precision", "fp16", "--list", "--lang", "en", "--vram", "7.75", "--ram", "32"]
    code, out, _ = _run(args, capsys)
    assert code == 0
    (cmd,) = _cmd_lines(out)
    # 名前で指定しても、計画のワークフローの ID で書く(同じものになる)。数は丸めない
    assert cmd.startswith("genmodelfit download test_tiny --budget 30.123456789 --comfyui-dir ")
    assert f"--comfyui-dir {cli.shell_quote(str(comfy))} --any-license" in cmd
    assert "--vram 7.75 --ram 32 " in cmd and cmd.endswith("--lang en")
    assert "--list" not in cmd and "--yes" not in cmd and "--json" not in cmd and "--content" not in cmd
    assert "--precision" not in cmd  # ID で精度も決まる
    # 利用者が付けた --content uncensored はそのまま(all に置き換えない)
    db2 = _cli_db(tmp_path, content="uncensored", unknown=True)
    args = ["download", "test_tiny", "--db", str(db2), "--content", "uncensored", "--output-dir", str(comfy)]
    _, out, _ = _run([*args, "--list"], capsys)
    (cmd,) = _cmd_lines(out)
    assert "--content uncensored --any-license" in cmd and "--uncensored" not in cmd
    assert f"--output-dir {cli.shell_quote(str(comfy))}" in cmd


def test_command_uses_the_program_form(tmp_path, comfy, capsys, monkeypatch):
    db = _cli_db(tmp_path, unknown=True)
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(db), "--list"]
    monkeypatch.setattr("sys.argv", [str(Path(downloader.__file__).with_name("__main__.py"))])
    (cmd,) = _cmd_lines(_run(args, capsys)[1])
    assert cmd.startswith("python -m genmodelfit download test_tiny ")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "Scripts" / "genmodelfit.exe")])
    (cmd,) = _cmd_lines(_run(args, capsys)[1])
    assert cmd.startswith("genmodelfit download test_tiny ")


@pytest.mark.parametrize("windows", [True, False], ids=["powershell", "posix"])
def test_shell_quote(windows):
    q = cli.shell_quote
    for safe in ("test_tiny", "fp16", "/opt/ComfyUI", "hf:o/r", "2.5", "C:/ComfyUI"):
        assert q(safe, windows) == safe
    # ' で囲めば、どちらのシェルでも $・`・\・;・&・空白をそのまま渡す
    assert q("Tiny Test", windows) == "'Tiny Test'"
    assert q("C:\\ComfyUI", windows) == "'C:\\ComfyUI'"
    assert q("D:\\models\\", windows) == "'D:\\models\\'"  # 末尾の \ も壊れない
    assert q("$HOME/x", windows) == "'$HOME/x'" and q("a`b", windows) == "'a`b'"
    assert q("a;b", windows) == "'a;b'" and q("a&b", windows) == "'a&b'"
    assert q("mit,apache-2.0", windows) == "'mit,apache-2.0'"  # PowerShell は , で配列にする
    assert q("", windows) == "''" and q('a"b', windows) == "'a\"b'"
    # ' だけはシェルで形が違う
    assert q("it's", windows) == ("'it''s'" if windows else "'it'\\''s'")


def test_shell_quote_round_trips_in_posix_sh():
    import shlex

    for arg in ["Tiny Test", "D:\\models\\", "$HOME", "a`b", "a;b&c", "it's", 'a"b', ""]:
        assert shlex.split(cli.shell_quote(arg, windows=False)) == [arg]


def test_shell_quote_follows_the_os(monkeypatch):
    monkeypatch.setattr(cli, "_is_windows", lambda: True)
    assert cli.shell_quote("it's") == "'it''s'"
    monkeypatch.setattr(cli, "_is_windows", lambda: False)
    assert cli.shell_quote("it's") == "'it'\\''s'"


@pytest.mark.parametrize("kind", ["gated", "unavailable"])
def test_unliftable_block_prints_no_command_and_no_consent_promise(tmp_path, comfy, capsys, kind):
    db = make_db(HF, content="uncensored")
    db.models[0].homepage = PAGE
    if kind == "gated":
        db.models[0].files[0].gated = True
    else:
        db.models[0].files[1].url = "https://civitai.com/api/download/models/1"
        db.models[0].files[1].license = None
        db.models[0].files[1].license_url = None
    path = tmp_path / "db.json"
    db.save(path)
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(path), *HW]
    code, out, err = _run(args, capsys)
    assert code == 1 and err.strip() == ERR_JA
    assert "落とすなら:" not in out.splitlines() and _cmd_lines(out) == []
    assert "このワークフローは genmodelfit では落とせません" in out
    assert "同意の指定" not in out and "--uncensored:" not in out  # 指定を足せば落とせるような言い方をしない
    if kind == "gated":
        assert "利用申請し" in out and f"{HF}/x/tiny_unet.safetensors" not in out
    else:  # 自分で落とせるよう、取得できないファイルの URL と確かめる場所を出す
        want = f"tiny_vae.safetensors: https://civitai.com/api/download/models/1(確かめる場所: {PAGE})"
        assert _nows(want) in _nows(out)


def test_unliftable_block_mentions_a_clean_alternative(tmp_path, comfy, capsys):
    bad = _file(HF, "tiny_unet.safetensors", BODY_A, "diffusion_models")
    bad.url = "https://civitai.com/api/download/models/1"
    db = make_db(HF, files=[bad])
    db.models[0].precision = "bf16"
    alt = ModelEntry(
        id="test_tiny_fp8",
        name="Tiny Test",
        modality="image",
        tasks=["t2i"],
        files=[_file(HF, "tiny_unet_fp8.safetensors", BODY_B, "diffusion_models")],
        template=Template(name="test_tiny_fp8", username="ComfyUI"),
        precision="fp8",
        license="mit",
    )
    db.models.append(alt)
    path = tmp_path / "db.json"
    db.save(path)
    args = ["download", "Tiny Test", "--comfyui-dir", str(comfy), "--db", str(path), *HW]
    p = gmf.plan_download("Tiny Test", db=db, system_info=pc(), comfyui_dir=comfy)
    assert p.model_id == "test_tiny"  # 精度の高いほう(取得できないファイルは選び方に入らない)
    assert [a["id"] for a in p.alternatives if a["ok"]] == ["test_tiny_fp8"]
    _, out, _ = _run(args, capsys)
    assert "同じモデルの別のワークフローにはこの問題がありません: test_tiny_fp8" in out
    assert "genmodelfit download test_tiny_fp8 --list" in out


@pytest.mark.parametrize("kind", ["disk", "budget"])
def test_lever_block_names_the_lever_then_consent(tmp_path, comfy, capsys, monkeypatch, kind):
    db = _cli_db(tmp_path, content="uncensored")
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(db), *HW]
    if kind == "disk":
        monkeypatch.setattr(downloader, "_free_bytes", lambda p: 1024**3)
    else:
        args += ["--budget", "0.001"]
    code, out, err = _run(args, capsys)
    assert code == 1 and err.strip() == ERR_JA and _cmd_lines(out) == []
    assert "同意の指定では外れません。先に:" in out and "どの指定でも" not in out
    assert ("ディスクを" in out) == (kind == "disk") and ("--budget を上げる" in out) == (kind == "budget")
    assert "そのうえで、同意の指定 --uncensored も足す" in out and "--uncensored: " in out


def test_blocked_codes_follow_blocked(comfy, monkeypatch):
    db = make_db(HF, content="uncensored")
    db.models[0].files[1].license = None
    db.models[0].files[0].gated = True
    monkeypatch.setattr(downloader, "_free_bytes", lambda p: 1024**3)
    p = plan(db, comfy)
    assert p.blocked_codes == ["uncensored", "gated", "unknown_license", "disk"]
    assert len(p.blocked) == len(p.blocked_codes)
    assert set(p.blocked_codes) <= set(downloader.BLOCK_CODES)
    assert p.to_dict()["blocked_codes"] == p.blocked_codes
    monkeypatch.undo()
    assert plan(make_db(HF), comfy).blocked_codes == []


def test_blocked_guide_in_english(tmp_path, comfy, capsys):
    db = _cli_db(tmp_path, content="uncensored", unknown=True)
    args = ["download", "test_tiny", "--comfyui-dir", str(comfy), "--db", str(db), *HW, "--lang", "en"]
    code, out, err = _run(args, capsys)
    assert code == 1 and err.strip() == "error: cannot download (see above)"
    assert "To download anyway (run it in a terminal to be asked y/N instead of adding flags):" in out
    assert "cannot download:" in out
    assert _expected(db, comfy, "--uncensored", "--any-license") + " --lang en" in out
    assert "--uncensored: you choose to download an uncensored model" in out
    assert "no license stated" in out and f"check:   {PAGE}" in out
    assert not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", out + err)


def test_other_folder_explained_in_every_layout(server, comfy, tmp_path, capsys):
    files = [_file(server, "tiny_vae.safetensors", BODY_B, None)]
    for kw in ({"comfyui_dir": comfy}, {"output_dir": tmp_path / "out"}):
        p = gmf.plan_download("test_tiny", db=make_db(server, files=files), system_info=pc(), **kw)
        assert p.files[0].relpath == "other/tiny_vae.safetensors" and p.files[0].directory_basis is None
        w = next(w for w in p.warnings if "models/other" in w)
        assert "読まない" in w and "移して" in w and "checkpoints" in w and "tiny_vae.safetensors" in w
    path = tmp_path / "other_db.json"
    make_db(server, files=files).save(path)
    _, out, _ = _run(
        ["download", "test_tiny", "--list", "--db", str(path), "--comfyui-dir", str(comfy)], capsys
    )
    assert "other/tiny_vae.safetensors" in out and out.count("models/other") >= 1
    assert "置き場所: 分からない" not in out  # パスと warnings で足りる(ファイルごとの行は出さない)


def _with_refs(files: list[ModelFile], ref_files: list[ModelFile], username: str = "ComfyUI") -> Database:
    hf = ModelEntry(id="hf:o/r", name="Hf Model", modality="image", source="huggingface", files=files)
    ref = ModelEntry(id="ref_tpl", name="Ref", modality="image", files=ref_files)
    ref.template = Template(name="ref_tpl", username=username)
    return Database(models=[hf, ref])


def _ref(directory: str, params: int, name: str = "sd_xl_base_1.0.safetensors") -> ModelFile:
    return ModelFile(name=name, directory=directory, header_params=params)


def test_directory_inferred_from_official_template_params(server, comfy, capsys, tmp_path):
    _Handler.files["mix_v1.safetensors"] = BODY_A
    f = _file(server, "mix_v1.safetensors", BODY_A, None, header_params=3_468_852_345)  # SDXL の inpainting
    db = _with_refs([f], [_ref("checkpoints", 3_468_837_945)])
    p = gmf.plan_download("hf:o/r", db=db, system_info=pc(), comfyui_dir=comfy)
    pf = p.files[0]
    assert pf.directory == "checkpoints" and pf.relpath == "checkpoints/mix_v1.safetensors"
    assert pf.directory_basis == {
        "kind": "header_params",
        "template": "ref_tpl",
        "file": "sd_xl_base_1.0.safetensors",
        "params": 3_468_852_345,
        "reference_params": 3_468_837_945,
    }
    assert p.to_dict()["files"][0]["directory_basis"] == pf.directory_basis
    assert not any("models/other" in w for w in p.warnings)
    path = tmp_path / "ref_db.json"
    db.save(path)
    args = ["download", "hf:o/r", "--list", "--db", str(path), "--comfyui-dir", str(comfy), *HW]
    _, out, _ = _run(args, capsys)
    want = "置き場所: checkpoints(一覧に無いので推定: ヘッダのパラメータ数 3,468,852,345 が公式テンプレート"
    assert _nows(want + " ref_tpl の sd_xl_base_1.0.safetensors と同じ)") in _nows(out)
    gmf.download(p, yes=True)  # 決めた置き場所に落ちる
    assert (comfy / "models" / "checkpoints" / "mix_v1.safetensors").read_bytes() == BODY_A


def test_directory_aliases_count_as_one_folder_and_gguf_needs_nodes(comfy):
    f = _file(HF, "mix-Q8_0.gguf", BODY_A, None, header_params=9_078_581_248)
    refs = [
        _ref("unet", 9_078_581_248, "a.safetensors"),
        _ref("diffusion_models", 9_078_581_250, "b.safetensors"),
    ]
    p = gmf.plan_download("hf:o/r", db=_with_refs([f], refs), system_info=pc(), comfyui_dir=comfy)
    pf = p.files[0]
    assert pf.directory == "diffusion_models" and pf.directory_basis["needs"] == "ComfyUI-GGUF"
    assert any("ComfyUI-GGUF" in w and "mix-Q8_0.gguf" in w for w in p.warnings)
    st = _file(HF, "mix.safetensors", BODY_A, None, header_params=9_078_581_248)
    pf2 = gmf.plan_download("hf:o/r", db=_with_refs([st], refs), system_info=pc(), comfyui_dir=comfy).files[0]
    assert pf2.directory == "diffusion_models" and "needs" not in pf2.directory_basis


@pytest.mark.parametrize(
    ("params", "refs", "username", "role"),
    [
        (1_066_261_786, [("checkpoints", 3_468_837_945)], "ComfyUI", "main"),  # 一致しない(SD1.5 の数)
        (
            3_468_837_945,
            [("checkpoints", 3_468_837_945), ("diffusion_models", 3_468_837_950)],
            "ComfyUI",
            "main",
        ),
        (
            3_468_837_945,
            [("checkpoints", 3_468_837_945)],
            "someone",
            "main",
        ),  # 利用者のテンプレートは手本にしない
        (3_468_837_945, [("checkpoints", 3_468_837_945)], "ComfyUI", "vae"),  # 本体だけ
        (None, [("checkpoints", 3_468_837_945)], "ComfyUI", "main"),  # パラメータ数が無い
    ],
    ids=["no-match", "two-folders", "community", "not-main", "no-params"],
)
def test_directory_stays_other_without_solid_evidence(comfy, params, refs, username, role):
    f = _file(HF, "mix.safetensors", BODY_A, None, header_params=params, role=role)
    db = _with_refs([f], [_ref(d, n, f"ref{i}.safetensors") for i, (d, n) in enumerate(refs)], username)
    p = gmf.plan_download("hf:o/r", db=db, system_info=pc(), comfyui_dir=comfy)
    assert p.files[0].relpath == "other/mix.safetensors" and p.files[0].directory_basis is None
    assert any("models/other" in w for w in p.warnings)


def test_listed_directory_is_never_replaced(comfy):
    f = _file(HF, "mix.safetensors", BODY_A, "loras", header_params=3_468_837_945)
    db = _with_refs([f], [_ref("checkpoints", 3_468_837_945)])
    pf = gmf.plan_download("hf:o/r", db=db, system_info=pc(), comfyui_dir=comfy).files[0]
    assert pf.relpath == "loras/mix.safetensors" and pf.directory_basis is None


def test_check_url_fallbacks():
    f = ModelFile(name="x.safetensors", repo_id="up/stream")
    hf = ModelEntry(id="hf:own/repo", name="A", modality="image", homepage="https://example.invalid/a")
    assert downloader.check_url(hf, f) == "https://huggingface.co/own/repo"
    tpl = ModelEntry(id="tpl", name="B", modality="image", homepage="https://example.invalid/tpl")
    assert downloader.check_url(tpl, f) == "https://example.invalid/tpl"
    insecure = ModelEntry(id="tpl3", name="D", modality="image", homepage="http://example.invalid/tpl")
    assert downloader.check_url(insecure, f) == "https://huggingface.co/up/stream"  # https のページだけ
    bare = ModelEntry(id="tpl2", name="C", modality="image")
    assert downloader.check_url(bare, f) == "https://huggingface.co/up/stream"
    assert downloader.check_url(bare, ModelFile(name="x")) is None


def test_check_url_in_plan_and_json(tmp_path, comfy, capsys):
    db = _cli_db(tmp_path, unknown=True)
    base = ["download", "test_tiny", "--list", "--db", str(db), "--comfyui-dir", str(comfy), *HW]
    _, out, _ = _run([*base, "--json"], capsys)
    files = json.loads(out)["plan"]["files"]
    assert files[0]["license_url"] and files[0]["check_url"] is None
    assert files[1]["license_url"] is None and files[1]["check_url"] == PAGE
    _, out, _ = _run(base, capsys)
    assert "ライセンスの記載がありません" in out and f"確かめる場所: {PAGE}" in out


def test_check_url_of_the_files_repository_is_labelled(tmp_path, comfy, capsys):
    db = make_db(HF)
    f = db.models[0].files[1]
    f.license, f.license_url = None, None
    f.url = "https://huggingface.co/Comfy-Org/split/resolve/main/tiny_vae.safetensors"
    f.repo_id = "Comfy-Org/split"
    path = tmp_path / "db.json"
    db.save(path)
    args = ["download", "test_tiny", "--list", "--db", str(path), "--comfyui-dir", str(comfy), "--lang", "en"]
    _, out, _ = _run(args, capsys)
    want = "check (the file's repository; may not be the original publisher): " + f"{HF}/Comfy-Org/split"
    assert _nows(want) in _nows(out)


@pytest.mark.parametrize("unknown", [False, True], ids=["all-urls", "no-license-url"])
def test_consent_question_matches_shown_urls(tmp_path, comfy, capsys, monkeypatch, unknown):
    db = _cli_db(tmp_path, unknown=unknown)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    args = [
        "download",
        "test_tiny",
        "--db",
        str(db),
        "--comfyui-dir",
        str(comfy),
        *HW,
        "--include-unknown-license",
    ]
    code, out, _ = _run(args, capsys)
    assert code == 1 and "同意して落としますか" in out
    assert ("(URL)に従って" in out) is not unknown  # 出ていない URL を指す言い方をしない
    assert ("確かめる場所で条件を自分で確かめたうえで" in out) is unknown
    _, out, _ = _run([*args, "--lang", "en"], capsys)
    assert ("terms (URLs)" in out) is not unknown
    assert ("after checking the pages shown above" in out) is unknown


def test_comfyui_dir_env_precedence(tmp_path, comfy, capsys, monkeypatch):
    db = _cli_db(tmp_path)
    base = ["download", "test_tiny", "--list", "--json", "--db", str(db), *HW]

    def plan_json(extra: list[str]) -> dict:
        code, out, _ = _run([*base, *extra], capsys)
        assert code == 0
        return json.loads(out)["plan"]

    monkeypatch.setenv(cli.COMFYUI_DIR_ENV, str(comfy))
    p = plan_json([])  # 環境変数が --comfyui-dir の既定
    assert p["layout"] == "comfyui" and p["root"] == str(comfy / "models")
    other = tmp_path / "ComfyUI2"
    (other / "models").mkdir(parents=True)
    assert plan_json(["--comfyui-dir", str(other)])["root"] == str(other / "models")  # フラグが勝つ
    p = plan_json(["--output-dir", str(tmp_path / "out")])  # --output-dir も環境変数に勝つ
    assert p["layout"] == "output" and p["root"] == str(tmp_path / "out")
    _, out, _ = _run(["download", "test_tiny", "--list", "--db", str(db), *HW], capsys)
    assert f"決め方:環境変数{cli.COMFYUI_DIR_ENV}" in _nows(out)  # どれで決めたかを毎回出す(#111)
    code, out, _ = _run([*base], capsys)
    assert json.loads(out)["destination_from"] == "env"  # どれで決めたか(#111)

    monkeypatch.setenv(cli.COMFYUI_DIR_ENV, str(tmp_path / "not-comfy"))  # フラグと同じく確かめる
    code, out, err = _run(base, capsys)
    assert code == 1 and "ComfyUI のフォルダに見えません" in out + err and cli.COMFYUI_DIR_ENV in out + err
    assert plan_json(["--comfyui-dir", str(comfy)])["root"] == str(comfy / "models")  # フラグがあれば使わない
    assert plan_json(["--output-dir", str(tmp_path / "out")])["layout"] == "output"


def test_list_without_a_place_shows_undecided(tmp_path, capsys):
    """置き場所が決まらないとき(#111): genmodelfit のフォルダに黙って落とさない。
    --list は「未定」と決め方を出す(仮のフォルダの root・dest・空きは出さない)。"""
    db = _cli_db(tmp_path)
    code, out, _ = _run(["download", "test_tiny", "--list", "--db", str(db), *HW], capsys)
    flat = _nows(out)
    assert code == 0 and "未定(ComfyUIのフォルダが見つかりません)" in flat
    assert "genmodelfitconfigsetcomfyui-dir" in flat and "--comfyui-dir" in out
    assert "127.0.0.1" in out  # 落とすときは起動中の ComfyUI にも尋ねる
    assert "genmodelfit-undecided" not in out
    assert "extra_model_paths.yaml" not in out
    code, out, _ = _run(["download", "test_tiny", "--list", "--json", "--db", str(db), *HW], capsys)
    env = json.loads(out)
    assert code == 0 and env["destination_from"] is None and env["destination_candidates"] == []
    assert env["plan"]["root"] == "" and all(f["dest"] == "" for f in env["plan"]["files"])
    assert env["plan"]["disk_free_bytes"] is None and "disk" not in env["plan"]["blocked_codes"]
    with i18n.forced("en"):
        _, out, _ = _run(["download", "test_tiny", "--list", "--db", str(db), *HW], capsys)
    assert "not decided (no ComfyUI folder was found)" in " ".join(out.split())


def test_library_warnings_do_not_name_the_cli_env_var(comfy, tmp_path):
    p = gmf.plan_download("test_tiny", db=make_db(HF), system_info=pc(), output_dir=tmp_path / "o")
    assert any("extra_model_paths.yaml" in w for w in p.warnings)
    assert not any("GENMODELFIT_COMFYUI_DIR" in w for w in p.warnings)


# --- 端末では無検閲・ライセンス不明を y/N の質問で尋ねる(#75。オーナーの依頼) ---------------------------


def _consent_db(server: str, tmp_path: Path, *, content: str, unknown: bool) -> Path:
    db = make_db(server, content=content)
    db.models[0].homepage = PAGE
    if unknown:
        db.models[0].files[1].license = None
        db.models[0].files[1].license_url = None
    path = tmp_path / "consent_db.json"
    db.save(path)
    return path


QUESTION = {
    ("uncensored", False, "ja"): "このモデルは無検閲(成人向けを含みうる)です。",
    (
        "sfw",
        True,
        "ja",
    ): "このモデルはライセンスの記載が無いファイルを含みます(上の確かめる場所で条件を確かめてください)。",
    (
        "uncensored",
        True,
        "ja",
    ): "このモデルは無検閲(成人向けを含みうる)で、ライセンスの記載が無いファイルを含みます"
    "(上の確かめる場所で条件を確かめてください)。",
    ("uncensored", False, "en"): "This model is uncensored (may include adult content).",
    (
        "sfw",
        True,
        "en",
    ): "This model has files with no stated license (check the pages above).",
    (
        "uncensored",
        True,
        "en",
    ): "This model is uncensored (may include adult content) and has files with no stated"
    " license (check the pages above).",
}


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize(
    ("content", "unknown"),
    [("uncensored", False), ("sfw", True), ("uncensored", True)],
    ids=["unc", "lic", "both"],
)
def test_interactive_question_names_exactly_the_items(
    server, tmp_path, comfy, capsys, monkeypatch, content, unknown, lang
):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "n")
    path = _consent_db(server, tmp_path, content=content, unknown=unknown)
    args = ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW, "--lang", lang]
    code, out, _ = _run(args, capsys)
    assert code == 1 and len(asked) == 1 and asked[0].startswith("[y/N/c]")  # 止めずに尋ねた。既定は No
    assert _nows(QUESTION[(content, unknown, lang)]) in _nows(out)
    careful = {
        "ja": "ライセンスの原文の URL が無いファイルは、上の確かめる場所で条件を自分で確かめたうえで",
        "en": "for files without a license URL, after checking the pages shown above yourself",
    }[lang]
    assert (_nows(careful) in _nows(out)) is unknown  # 記載の無いファイルがあれば、自分で確かめる言い方
    assert ("(URL)" in out or "(URLs)" in out) is not unknown
    other = [v for k, v in QUESTION.items() if k[2] == lang and k[:2] != (content, unknown)]
    assert not any(_nows(v) in _nows(out) for v in other)
    assert "落とせません" not in out and "cannot download" not in out
    assert _Handler.seen == [] and not any(comfy.rglob("*.safetensors*"))


def test_interactive_question_without_items_is_the_usual_one(server, tmp_path, comfy, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    path = _consent_db(server, tmp_path, content="sfw", unknown=False)
    code, out, _ = _run(
        ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW], capsys
    )
    assert code == 1 and "同意して落としますか" in out and "このモデルは" not in out


@pytest.mark.parametrize("answer", ["y", "yes"])
def test_interactive_yes_downloads_without_flags(server, tmp_path, comfy, capsys, monkeypatch, answer):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    code, out, _ = _run(
        ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW], capsys
    )
    assert code == 0 and "sha256 はすべて一覧と一致" in out  # 1 回の y で両方に同意(落とすのは手元のサーバ)
    assert (comfy / "models" / "vae" / "tiny_vae.safetensors").read_bytes() == BODY_B


@pytest.mark.parametrize("answer", ["n", "", "no"])
def test_interactive_no_or_empty_downloads_nothing(server, tmp_path, comfy, capsys, monkeypatch, answer):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    code, _, err = _run(
        ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW], capsys
    )
    assert code == 1 and "落としませんでした" in err
    assert _Handler.seen == [] and not any(comfy.rglob("*.safetensors*"))


@pytest.mark.parametrize("extra", [[], ["--yes"], ["--json", "--yes"]], ids=["no-terminal", "yes", "json"])
def test_consent_flags_still_needed_without_the_question(server, tmp_path, comfy, capsys, monkeypatch, extra):
    """端末でない・--yes・--json では、今までどおり --uncensored / --any-license が要る(尋ねられないため)。"""
    monkeypatch.setattr(cli, "_interactive", lambda: bool(extra))  # [] は端末でない実行

    def no_question(prompt=""):
        raise AssertionError("尋ねてはいけない")

    monkeypatch.setattr("builtins.input", no_question)
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    args = ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW, *extra]
    code, out, _ = _run(args, capsys)
    assert code == 1 and _Handler.seen == []
    if "--json" in extra:
        assert "無検閲" in json.loads(out)["error"]
    else:
        assert "--uncensored --any-license" in out
    code, _, _ = _run([*args, "--uncensored", "--any-license", "--yes"], capsys)
    assert code == 0 and (comfy / "models" / "vae" / "tiny_vae.safetensors").exists()


def test_interactive_unliftable_block_is_not_asked(tmp_path, comfy, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def no_question(prompt=""):
        raise AssertionError("尋ねてはいけない")

    monkeypatch.setattr("builtins.input", no_question)
    db = _cli_db(tmp_path, content="uncensored", gated=True)
    code, out, _ = _run(["download", "test_tiny", "--db", str(db), "--comfyui-dir", str(comfy), *HW], capsys)
    assert code == 1 and "このワークフローは genmodelfit では落とせません" in out


def test_library_still_needs_explicit_consent(server, comfy):
    """ライブラリは変えない: content / include_unknown_license を渡さなければ止める。"""
    db = make_db(server, content="uncensored")
    db.models[0].files[1].license = None
    p = plan(db, comfy)
    assert set(p.blocked_codes) == {"uncensored", "unknown_license"}
    with pytest.raises(DownloadRefused):
        gmf.download(p, yes=True)
    assert plan(db, comfy, content="all", include_unknown_license=True).ok


def test_short_flags_parse_and_conflict(capsys):
    ns = cli.build_parser().parse_args(["download", "x", "--uncensored", "--any-license"])
    assert ns.uncensored and ns.include_unknown_license
    assert (
        cli.build_parser().parse_args(["download", "x", "--include-unknown-license"]).include_unknown_license
    )
    for cmd in (
        ["download", "x", "--list"],
        ["recommend"],
        ["fit"],
        ["search", "x"],
        ["info", "x"],
        ["types"],
    ):
        with pytest.raises(SystemExit) as e:
            cli.main([*cmd, "--uncensored", "--content", "sfw"])
        assert e.value.code == 2 and "--uncensored" in capsys.readouterr().err


# --- レビューの指摘(#75): 略した同意の指定・端末の判定・質問の途中の EOF / Ctrl-C・sha256 は 1 回 ---


@pytest.mark.parametrize(
    "abbrev",
    [["--u"], ["--unc"], ["--an"], ["--any"], ["--include-unk"], ["--cont", "all"], ["--con=all"]],
)
def test_consent_flags_cannot_be_abbreviated(capsys, abbrev):
    for cmd in (["download", "x", "--list"], ["recommend"], ["fit"]):
        with pytest.raises(SystemExit) as e:
            cli.main([*cmd, *abbrev])
        assert e.value.code == 2
        err = capsys.readouterr().err
        assert "略さずに" in err or "full" in err


def test_full_consent_flags_and_lang_prefix_still_work(tmp_path, comfy, capsys):
    db = _cli_db(tmp_path, content="uncensored", unknown=True)
    args = ["download", "test_tiny", "--db", str(db), "--comfyui-dir", str(comfy), *HW, "--list"]
    assert _run([*args, "--uncensored", "--any-license"], capsys)[0] == 0
    assert _run([*args, "--content=all", "--include-unknown-license"], capsys)[0] == 0
    code, out, _ = _run([*args, "--lan", "en"], capsys)  # --lang は略してよい(#73)
    assert code == 0 and "cannot download" in out


def test_uncensored_with_content_uncensored_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["recommend", "--uncensored", "--content", "uncensored"])
    assert e.value.code == 2 and "--uncensored" in capsys.readouterr().err


class _Stream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        if isinstance(self._tty, Exception):
            raise self._tty
        return self._tty


@pytest.mark.parametrize(
    ("stdin", "stdout", "want"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
        (ValueError("closed"), True, False),
        (True, OSError("bad handle"), False),
    ],
    ids=["both", "stdin-only", "stdout-only", "neither", "stdin-raises", "stdout-raises"],
)
def test_interactive_detection(monkeypatch, stdin, stdout, want):
    monkeypatch.setattr("sys.stdin", _Stream(stdin))
    monkeypatch.setattr("sys.stdout", _Stream(stdout))
    assert cli._interactive() is want


def test_eof_at_the_consent_question_downloads_nothing(server, tmp_path, comfy, capsys, monkeypatch):
    """入力が無い(パイプ・Windows の NUL で isatty が True になる場合)なら、同意しない。"""
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    code, _, err = _run(
        ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW], capsys
    )
    assert code == 1 and "落としませんでした" in err and _Handler.seen == []


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_ctrl_c_at_the_consent_question(server, tmp_path, comfy, capsys, monkeypatch, lang):
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    args = ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW, "--lang", lang]
    code, _, err = _run(args, capsys)
    assert code == 130 and ("中断しました" if lang == "ja" else "Interrupted") in err
    assert "Traceback" not in err and _Handler.seen == [] and not any(comfy.rglob("*.safetensors*"))


def test_replan_keeps_the_workflow_chosen_with_precision(server, tmp_path, comfy, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    db = make_db(server, content="uncensored")
    other = ModelEntry(
        id="test_tiny_bf16",
        name="Tiny Test",
        modality="image",
        tasks=["t2i"],
        files=[_file(server, "tiny_unet_bf16.safetensors", BODY_B, "diffusion_models")],
        template=Template(name="test_tiny_bf16", username="ComfyUI"),
        precision="bf16",
        license="mit",
        content="uncensored",
    )
    db.models.append(other)
    path = tmp_path / "db.json"
    db.save(path)
    args = [
        "download",
        "test_tiny",
        "--precision",
        "fp16",
        "--db",
        str(path),
        "--comfyui-dir",
        str(comfy),
        *HW,
    ]
    code, out, _ = _run(args, capsys)
    assert code == 1 and "[test_tiny]" in out and "精度 fp16" in out and "tiny_unet_bf16" not in out
    assert "このモデルは無検閲" in out


def test_existing_files_are_hashed_once_when_asking(server, tmp_path, comfy, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    path = _consent_db(server, tmp_path, content="uncensored", unknown=True)
    vae = comfy / "models" / "vae" / "tiny_vae.safetensors"
    vae.parent.mkdir(parents=True)
    vae.write_bytes(BODY_B)
    calls: list[str] = []
    real = downloader._hash_file
    monkeypatch.setattr(
        downloader, "_hash_file", lambda p, progress=None: calls.append(p.name) or real(p, progress)
    )
    code, out, _ = _run(
        ["download", "test_tiny", "--db", str(path), "--comfyui-dir", str(comfy), *HW], capsys
    )
    assert code == 1 and "既にある" in out
    assert calls == ["tiny_vae.safetensors"]  # 計画し直しても計り直さない


def test_library_texts_do_not_name_cli_flags(comfy):
    db = make_db(HF, content="uncensored")
    db.models[0].files[1].license = None
    p = plan(db, comfy)
    assert not any("--uncensored" in b or "--any-license" in b or "端末" in b for b in p.blocked)
    assert any('content="all"' in b for b in p.blocked) and any(
        "include_unknown_license=True" in b for b in p.blocked
    )
    with pytest.raises(downloader.ModelNotFound) as e:
        gmf.plan_download("Tiny Te", db=db, system_info=pc(), comfyui_dir=comfy)
    assert 'content="all"' in str(e.value) and "--uncensored" not in str(e.value)


def test_cli_translates_library_texts(tmp_path, comfy, capsys):
    db = _cli_db(tmp_path, content="uncensored", unknown=True)
    code, out, _ = _run(
        ["download", "test_tiny", "--db", str(db), "--comfyui-dir", str(comfy), *HW, "--json", "--yes"],
        capsys,
    )
    err = json.loads(out)["error"]
    assert code == 1 and "--uncensored" in err and "--any-license" in err and 'content="all"' not in err
    code, _, err = _run(
        ["download", "Tiny Te", "--db", str(db), "--comfyui-dir", str(comfy), "--list"], capsys
    )
    assert code == 1 and "--uncensored" in err and 'content="all"' not in err


# --- #124 L6: 消えたファイル・書けない置き場所 -------------------------------------------------------------


def test_L6a_missing_file_on_the_server_is_a_clear_error(server, comfy):
    _Handler.files = {"tiny_vae.safetensors": BODY_B}  # 本体は消えた(404)
    p = plan(make_db(server), comfy)
    with pytest.raises(DownloadError) as e:
        gmf.download(p, yes=True)
    assert "404" in str(e.value)


def test_L6d_place_that_cannot_be_written_is_a_clear_error(server, comfy, monkeypatch):
    p = plan(make_db(server), comfy)
    real_open = Path.open

    def deny(self, mode="r", *a, **k):
        if self.name.endswith(".part") and mode != "rb":
            raise PermissionError(13, "read-only", str(self))  # 読み取り専用・OneDrive の置き場所など
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(DownloadError) as e:
        gmf.download(p, yes=True)
    assert "PermissionError" in str(e.value) or "read-only" in str(e.value) or "書け" in str(e.value)
    assert not any(comfy.rglob("*.safetensors"))


def test_L6b_no_range(server, comfy):
    test_server_without_range_restarts(server, comfy)  # Range に応じないサーバーは最初から落とし直す


def test_L6c_stale_lock(tmp_path, monkeypatch):
    test_stale_lock_retire_keeps_fresh_lock(tmp_path, monkeypatch)  # 前に落ちた .lock は古いものとして扱う
