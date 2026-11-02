import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

# 同梱の一覧の HEAD は 2026-10-20 に確かめた。gate の「確認が古い」を、
# テストを動かす日によらず決まった結果にする
SNAPSHOT_NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


@pytest.fixture(scope="session", autouse=True)
def _isolated_cache_session(tmp_path_factory):
    """利用者の本物のキャッシュ(この PC の bench の実測・取ってきた一覧)を読まない(#90)。
    module の fixture(同梱の一覧を読む db など)は関数の fixture より先に作られるので、
    まず全体で空のフォルダにする。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
        # この PC の ComfyUI を探さない(子のプロセスにも効く。#111)。探す試験は test_download_place.py が外す
        mp.setenv("GENMODELFIT_COMFYUI_SEARCH", "0")
        # 見つけた ComfyUI を起動しない(#124)。起動の試験は tests/test_comfystart.py が偽の subprocess で外す
        mp.setenv("GENMODELFIT_COMFYUI_START", "0")
        yield


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    """試験ごとに空のキャッシュ(書いたものが次の試験に残らない)。"""
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _no_real_hardware(monkeypatch):
    """既定の試験では、本物の nvidia-smi・PowerShell・lspci・レジストリを呼ばない
    (hardware の外への出口を塞ぐ)。
    出力を使う試験は tests/fixtures/hardware のフィクスチャで差し替える。"""
    from genmodelfit import bench, hardware

    monkeypatch.setattr(hardware, "_run", lambda args, timeout: None)
    # bench は nvidia-smi を自分で動かす(_smi_query・使用率の監視)。本物の GPU の使用率・空きを読まない(#90。
    # 共用の PC で GPU が使われていると、bench を通す試験が止まった)。使う試験は smi_gpus などを差し替える
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    monkeypatch.setattr(hardware, "_reg_display_entries", lambda: None)
    monkeypatch.setattr(hardware, "_reg_paging_files", lambda: None)
    # #101: 本物の sysfs の PCI の部品・amdkfd・WSL2 の状態も読まない(CI の仮想マシンの画面や、
    # WSL で流すときの /dev/dxg で結果が変わらないように)。使う試験は tests/test_detect_everywhere.py
    # のように一時ディレクトリの木(hardware._ROOT)と元の関数で差し替える
    monkeypatch.setattr(hardware, "_sysfs_slots", lambda: [])
    monkeypatch.setattr(hardware, "kfd_gfx_targets", lambda: {})
    monkeypatch.setattr(hardware, "wsl_state", lambda: None)
    # #166: WSL で流すとき、OS の名前(カーネルの microsoft-standard-WSL2)で動的 VRAM の式が外れ、
    # 判定が変わらないようにする(WSL の扱いを見る試験は SystemInfo.os を直に書く)
    real_os_name = hardware.os_name

    def _os_name(system=None, release=None, version=None):
        name = real_os_name(system, release, version)
        if system is None and "microsoft" in name.lower():
            return "Linux"
        return name

    monkeypatch.setattr(hardware, "os_name", _os_name)


@pytest.fixture(autouse=True)
def _no_real_comfyui(monkeypatch, tmp_path):
    """download の置き場所(#111)を、この PC の ComfyUI・設定に左右されないようにする。
    設定ファイルは試験ごとの空のフォルダ、探す場所は無いホームだけ(固定ドライブ・ドキュメントなどを見ない)、
    起動中の ComfyUI(127.0.0.1)には尋ねない。使う試験は tests/test_download_place.py のように差し替える。"""
    import sys

    from genmodelfit import comfyfind, config

    monkeypatch.setenv(config.CONFIG_DIR_ENV, str(tmp_path / "gmf-config"))
    home = tmp_path / "no-home"
    monkeypatch.setattr(
        comfyfind, "current_places", lambda: comfyfind.Places(platform=sys.platform, home=home)
    )
    monkeypatch.setattr(comfyfind, "running_candidates", lambda *a, **k: [])


@pytest.fixture(autouse=True)
def _no_node_check(monkeypatch):
    """share・measure の測る前のノードの確かめ(#124)は、既定では「足りないものは無い」にする
    (--url の偽の宛先に通信しない)。確かめる試験は cli._node_check を差し替える。"""
    from genmodelfit import cli

    monkeypatch.setattr(cli, "_node_check", lambda url: SimpleNamespace(missing=lambda model_id: []))


@pytest.fixture(autouse=True)
def _no_github_login(monkeypatch):
    """share の GitHub のログイン(#124)を、この PC・CI の資格情報に左右されないようにする。
    環境変数のトークン・client id を外す(キャッシュは試験ごとの GENMODELFIT_CONFIG_DIR の下)。"""
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GENMODELFIT_GH_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _pin_language(monkeypatch):
    """表示の言語を ja に固定する(機械の言語に左右されないように。#73)。
    子プロセスで CLI を動かす試験にも効くよう環境変数で渡し、set_language・--lang の状態も毎回戻す。"""
    from genmodelfit import i18n

    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    monkeypatch.setattr(i18n, "_api_language", None)
    with i18n.forced(None):  # --lang・weekly の固定(ContextVar)も毎回外して始め、終われば戻す
        yield


@pytest.fixture(autouse=True)
def _fixed_gate_clock(monkeypatch):
    from genmodelfit import gate

    monkeypatch.setattr(gate, "_now", lambda: SNAPSHOT_NOW)
    monkeypatch.delenv("COLUMNS", raising=False)  # 表の幅を、テストを動かす端末によらず決める
    monkeypatch.delenv("GENMODELFIT_COMFYUI_DIR", raising=False)  # download の既定の置き場所(#75)


def pytest_collection_modifyitems(config, items):
    """network の印のテストは GENMODELFIT_NETWORK_TESTS=1 のときだけ動かす。"""
    if os.environ.get("GENMODELFIT_NETWORK_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="実ネットワークのテスト(GENMODELFIT_NETWORK_TESTS=1 で実行)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
