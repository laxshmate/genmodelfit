"""アプリに組み込んで使うときの約束(公開 API・ネットに出ない・何も表示しない・誤った引数・決まった結果)。"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from frozenlist import frozen_db

import genmodelfit as gmf
from genmodelfit.models import FIT_LEVELS, Gpu, SystemInfo
from genmodelfit.recommend import evaluate


def pc(vram: float, ram: float = 32.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return gmf.load_db(use_cache=False)


def test_all_names_import():
    for name in gmf.__all__:
        assert hasattr(gmf, name), name
    ns: dict = {}
    exec(f"from genmodelfit import {', '.join(gmf.__all__)}", ns)  # 「from … import」でも取れる
    assert all(name in ns for name in gmf.__all__)


def test_recommend_with_constructed_system(db):
    fits = gmf.recommend(system_info=pc(8), db=db, limit=5)
    assert 0 < len(fits) <= 5
    assert all(f.estimate.fit in FIT_LEVELS[:3] for f in fits)
    cpu = gmf.recommend(system_info=pc(0, 16), db=db, limit=None)  # GPU の無い PC
    assert all(f.estimate.run_mode == "cpu" for f in cpu)


def test_api_writes_nothing(db, capfd):
    gmf.recommend(system_info=pc(8), db=db)
    gmf.recommend(system_info=pc(0), db=db, content="all", limit=None)
    evaluate(db, pc(12), content="all", with_limits=True)
    gmf.info("image_sdxl_simple", db=db)
    gmf.load_db(use_cache=False)
    gmf.system(vram_gb=8, ram_gb=32)  # nvidia-smi を呼んでも、その出力を漏らさない
    out, err = capfd.readouterr()
    assert out == "" and err == ""


class _FakeFetcher:
    """HEAD はすべて 200、ComfyUI の版は v0.0.0 を返す(ネットに出ない)。"""

    def get_json(self, url):
        return {"tag_name": "v0.0.0"}

    def get_text(self, url):
        raise AssertionError(url)

    def head_size(self, url):
        return None

    def head_status(self, url):
        return 200


@pytest.fixture
def release_server(tmp_path):
    """壊れた署名つきの一覧を配る手元の HTTP サーバー(記録の行も出さない)。"""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    root = tmp_path / "release"
    root.mkdir()
    (root / "db.json").write_text("{}", encoding="utf-8")
    (root / "db.json.sig").write_text("not-a-signature", encoding="ascii")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_library_functions_write_nothing(tmp_path, capfd, monkeypatch, release_server):
    """更新・取り込み・実測のライブラリ関数も、成功でも失敗でも stdout / stderr に何も出さない(fd で見る)。"""
    from pathlib import Path

    from genmodelfit import bench, remote, update
    from genmodelfit.fetch import DirFetcher

    fix = Path(__file__).parent / "fixtures" / "update"
    db = update.update_database(DirFetcher(fix), tmp_path / "a.json", head=False, allow_partial=True)
    assert db.models and (tmp_path / "a.json").exists()
    db = update.refresh_heads(_FakeFetcher(), tmp_path / "b.json", use_cache=False)
    assert db.sources["comfyui"]["latest_release"] == "v0.0.0"
    monkeypatch.setattr(remote, "RETRY_DELAY", 0)
    with pytest.raises(remote.RemoteError):
        remote.update_from_release(release_server, timeout=5, dest=tmp_path / "c.json")
    assert not (tmp_path / "c.json").exists()
    with pytest.raises(bench.BenchError):
        bench.run(bench.BenchOptions(model_id="image_sdxl_simple", url="http://127.0.0.1:9", timeout=5))
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_info_is_exact_id_only(db):
    assert gmf.info("image_sdxl_simple", db=db).id == "image_sdxl_simple"
    assert gmf.info("sdxl", db=db) is None  # 一部の一致では探さない
    # 無検閲の項目は凍らせた一覧から読む(毎週の更新で同梱の一覧から消えても同じ入力で確かめる。#197)
    assert gmf.info("hf:Heartsync/NSFW-Uncensored", db=frozen_db()).content == "uncensored"


def test_no_network(tmp_path):
    """import と主な関数でネットに出ない(別プロセスで、つなぐ所を全部ふさいでから import する)。"""
    script = textwrap.dedent(
        """
        import socket, sys

        def deny(*a, **k):
            raise AssertionError("network used")

        socket.socket.connect = deny
        socket.socket.connect_ex = deny
        socket.create_connection = deny
        socket.getaddrinfo = deny
        import requests
        requests.Session.request = deny

        import genmodelfit as gmf
        from genmodelfit.models import Gpu, SystemInfo
        from genmodelfit.recommend import evaluate

        pc = SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=32, ram_available_gb=32,
                        gpus=[Gpu(name="T", vendor="nvidia", vram_total_gb=8, backend="cuda")])
        db = gmf.load_db()
        gmf.recommend(system_info=pc)
        gmf.recommend(system_info=pc, db=db, content="all", limit=None)
        evaluate(db, pc, content="all", with_limits=True)
        gmf.info("image_sdxl_simple")
        gmf.search("flux", system_info=pc)
        gmf.data_status()
        gmf.data_status(db, comfyui_version="0.40.0")
        # CLI の読むだけのコマンドも(README「Privacy」。出力は捨てる)
        import contextlib, io
        from genmodelfit import cli
        for argv in (["system", "--json"], ["recommend", "--json"], ["fit", "--json"], ["search", "wan"],
                     ["types"], ["info", "image_sdxl_simple"], ["gate", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()):
                hw = [] if argv[0] == "types" else ["--vram", "8", "--ram", "32"]
                assert cli.main([*argv, *hw]) == 0, argv
        sys.stdout.write("OK")
        """
    )
    from clihelp import child_env

    r = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        env=child_env(tmp_path / "cache"),
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout == b"OK" and r.stderr == b""


@pytest.mark.parametrize(
    ("kwargs", "word"),
    [
        ({"modality": "picture"}, "modality"),
        ({"content": "nsfw"}, "content"),
        ({"content": "SFW"}, "content"),
        ({"min_fit": "great"}, "min_fit"),
        ({"min_fit": "Good"}, "min_fit"),
    ],
)
def test_invalid_arguments_raise_value_error(db, kwargs, word):
    with pytest.raises(ValueError, match=word) as ei:
        gmf.recommend(system_info=pc(8), db=db, **kwargs)
    assert "使えるのは" in str(ei.value)  # 使える値を文に入れる


def test_evaluate_rejects_unknown_content(db):
    with pytest.raises(ValueError, match="content"):
        evaluate(db, pc(8), content="adult")


def test_default_content_is_sfw(db):
    default = gmf.recommend(system_info=pc(24, 64), db=db, limit=None)
    assert default and all(f.model.content == "sfw" for f in default)
    every = gmf.recommend(system_info=pc(24, 64), db=db, limit=None, content="all")
    assert any(f.model.content == "uncensored" for f in every)
    only = gmf.recommend(system_info=pc(24, 64), db=db, limit=None, content="uncensored")
    assert only and all(f.model.content == "uncensored" for f in only)
    assert all(f.model.content == "sfw" for f in evaluate(db, pc(8)))
    # ID を指定すれば無検閲のモデルも引ける
    assert gmf.info(only[0].model.id, db=db) is not None


def test_results_are_deterministic(db):
    a = [f.to_dict() for f in gmf.recommend(system_info=pc(8), db=db, limit=None, content="all")]
    b = [f.to_dict() for f in gmf.recommend(system_info=pc(8), db=db, limit=None, content="all")]
    assert a == b
    c = [f.to_dict() for f in gmf.recommend(system_info=pc(8), db=gmf.load_db(use_cache=False), limit=None)]
    assert c == [x for x in a if x["content"] == "sfw"]


@pytest.mark.parametrize(
    ("cli_args", "kwargs"),
    [
        ([], {"per_modality": 3, "limit": None}),  # -m も --task も無ければ種類ごとに 3 件
        (
            ["-m", "image,video", "--task", "edit,i2v", "--all-workflows"],
            {"modality": ["image", "video"], "task": "edit,i2v", "all_workflows": True},
        ),
        (
            ["-m", "image", "--min-fit", "good", "-n", "5"],
            {"modality": "image", "min_fit": "good", "limit": 5},
        ),
        (
            ["--license", "apache-2.0,mit", "--auto-only", "--include-tools", "--content", "all", "-n", "50"],
            {
                "license": ["apache-2.0", "mit"],
                "auto_only": True,
                "include_tools": True,
                "content": "all",
                "per_modality": 50,
                "limit": None,
            },
        ),
        (["--comfyui-version", "0.3.0", "-m", "video"], {"comfyui_version": "0.3.0", "modality": "video"}),
    ],
)
def test_python_and_cli_give_the_same_models(capsys, db, cli_args, kwargs):
    """アプリは Python からでも CLI の --json からでも同じものを受け取る(Fit.to_dict() == models の 1 件)。"""
    import json

    from genmodelfit import cli

    assert cli.main(["recommend", "--json", "--vram", "8", "--ram", "32", "--no-cache", *cli_args]) == 0
    from_cli = json.loads(capsys.readouterr().out)["models"]
    fits = gmf.recommend(system_info=gmf.system(vram_gb=8, ram_gb=32), db=db, **kwargs)
    assert from_cli and [json.loads(json.dumps(f.to_dict())) for f in fits] == from_cli


def test_python_search_matches_cli(capsys, db):
    import json

    from genmodelfit import cli

    assert cli.main(["search", "wan", "--json", "--vram", "8", "--ram", "32", "--no-cache"]) == 0
    from_cli = json.loads(capsys.readouterr().out)["models"]
    fits = gmf.search("wan", system_info=gmf.system(vram_gb=8, ram_gb=32), db=db)
    assert from_cli and [json.loads(json.dumps(f.to_dict())) for f in fits] == from_cli
    assert gmf.search("no_such_model_xyz", system_info=pc(8), db=db) == []


def test_fit_score_and_auto(db):
    fits = gmf.recommend(system_info=pc(8), db=db, limit=None)
    assert fits[0].auto and fits[0].score == fits[0].to_dict()["score"]
    assert all(set(f.score_components) == {"memory_fit"} for f in fits)  # #95
    # 古い版の ComfyUI を渡すと、新しい版が要るものは自動のおすすめから外れる
    old = gmf.recommend(system_info=pc(8), db=db, limit=None, comfyui_version="0.1.0")
    assert sum(f.auto for f in old) < sum(f.auto for f in fits)


def test_python_argument_forms_and_errors(db):
    """license は文字列(カンマ区切り)でも並びでもよい。limit は 1 以上、空の検索語は誤り。"""
    a = gmf.recommend(system_info=pc(8), db=db, license="mit, apache-2.0", limit=None)
    b = gmf.recommend(system_info=pc(8), db=db, license=["mit", "apache-2.0"], limit=None)
    assert a and [f.to_dict() for f in a] == [f.to_dict() for f in b]
    assert all(set(f.to_dict()["licenses"]) <= {"mit", "apache-2.0"} for f in a)
    for bad in (0, -1, True, 2.5):
        with pytest.raises(ValueError, match="limit"):
            gmf.recommend(system_info=pc(8), db=db, limit=bad)
    with pytest.raises(ValueError, match="license"):
        gmf.recommend(system_info=pc(8), db=db, license=" , ")
    for q in ("", "   "):
        with pytest.raises(ValueError, match="query"):
            gmf.search(q, system_info=pc(8), db=db)
    with pytest.raises(ValueError, match="task"):
        gmf.recommend(system_info=pc(8), db=db, task="dance")
    assert gmf.recommend(system_info=pc(8), db=db, modality="image,music", limit=None)
