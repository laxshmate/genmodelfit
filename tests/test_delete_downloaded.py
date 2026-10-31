# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# ruff: noqa: F811  (fixtures imported from test_download / test_share_pr)

"""share・measure が測るために落としたものを、終わったあとで消すか(#145)。

消す候補は、その実行で新しく落としたファイル(落とし終えたもの・残った .part・.lock)だけで、もとからあった
ファイルは入れない。消すのは置き場所の中だけ。ファイルは tmp_path の中だけに作り、落とすのは手元の
http.server(test_download の server。127.0.0.1)からだけ。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_download import BODY_A, BODY_B, MIB, comfy, db_file, make_db, plan, server  # noqa: F401
from test_share_pr import TOKEN, answers, gh, sugg, this_pc  # noqa: F401
from test_share_pr import plan as share_plan  # noqa: F401

import genmodelfit as gmf
from genmodelfit import cli
from genmodelfit.downloader import DownloadRecord

UNET = Path("diffusion_models") / "sub" / "tiny_unet.safetensors"
VAE = Path("vae") / "tiny_vae.safetensors"


# --- ライブラリ: 記録 -----------------------------------------------------------------------------


def test_record_has_only_new_files_and_delete_keeps_the_old(server, comfy):
    db = make_db(server)
    old = comfy / "models" / VAE
    old.parent.mkdir(parents=True)
    old.write_bytes(BODY_B)  # もとから入っていたファイル
    rec = DownloadRecord()
    res = gmf.download(plan(db, comfy), yes=True, record=rec)
    assert res.skipped == ["vae/tiny_vae.safetensors"]
    new = comfy / "models" / UNET
    assert [p for p, _ in rec.existing()] == [new]  # .lock は終わると消える。もとからのものは入らない
    assert rec.existing()[0][1] == len(BODY_A)
    removed, failed = rec.delete()
    assert removed == [new] and failed == [] and not new.exists()
    assert old.read_bytes() == BODY_B  # もとからのファイルは消さない
    assert rec.delete() == ([], [])  # 2 回目は何もしない


def test_record_keeps_this_runs_part_but_not_an_older_part(server, comfy):
    db = make_db(server)

    def stop(ev):
        if ev.phase == "download" and ev.file_bytes >= MIB:
            raise KeyboardInterrupt

    rec = DownloadRecord()
    with pytest.raises(KeyboardInterrupt):
        gmf.download(plan(db, comfy), yes=True, progress=stop, record=rec)
    part = comfy / "models" / (str(UNET) + ".part")
    assert [p for p, _ in rec.existing()] == [part]  # 途中で止めても、それまでの分が残る
    # 次の実行: 前の実行の .part は入れない(続きから落とし終えたファイルは新しいので入れる)
    rec2 = DownloadRecord()
    with pytest.raises(KeyboardInterrupt):
        gmf.download(plan(db, comfy), yes=True, progress=stop, record=rec2)
    assert rec2.existing() == []
    # 前の実行の .part を続けて落とし終えたファイルも、もとからあったものとして扱う(記録しない。安全な側)
    rec3 = DownloadRecord()
    gmf.download(plan(db, comfy), yes=True, record=rec3)
    assert (comfy / "models" / UNET).read_bytes() == BODY_A
    assert [p for p, _ in rec3.existing()] == [comfy / "models" / VAE]


def test_record_never_has_locks_and_removes_the_folders_it_made(server, comfy):
    db = make_db(server)
    (comfy / "models" / "vae").mkdir(parents=True)  # もとからあるフォルダ
    rec = DownloadRecord()
    gmf.download(plan(db, comfy), yes=True, record=rec)
    assert not any(p.endswith(".lock") for p, _, _ in rec.entries)
    assert {Path(f).name for f, _ in rec.folders} == {"diffusion_models", "sub"}
    keep = comfy / "models" / "diffusion_models" / "keep.txt"
    rec.delete()
    assert not (comfy / "models" / "diffusion_models" / "sub").exists()
    assert (comfy / "models" / "vae").is_dir()  # もとからあるフォルダは消さない
    assert not (comfy / "models" / "diffusion_models").exists()
    # 作ったフォルダでも、ほかのファイルが入れば残す
    rec2 = DownloadRecord()
    gmf.download(plan(db, comfy), yes=True, record=rec2)
    keep.write_text("x")
    rec2.delete()
    assert keep.exists() and not (comfy / "models" / "diffusion_models" / "sub").exists()


def test_a_file_replaced_after_recording_is_not_deleted(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    f = root / "a.safetensors"
    f.write_bytes(b"x" * 10)
    rec = DownloadRecord()
    assert rec.add(f, root)
    f.unlink()
    f.write_bytes(b"y" * 12)  # 別の実行が置き直した
    assert rec.existing() == []
    removed, failed = rec.delete()
    assert removed == [] and failed and failed[0][0] == f and f.read_bytes() == b"y" * 12


def test_record_lists_a_path_once_and_never_outside_the_destination(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    inside = root / "a.safetensors"
    inside.write_bytes(b"x" * 10)
    outside = tmp_path / "outside.safetensors"
    outside.write_bytes(b"y")
    rec = DownloadRecord()
    rec.add(inside, root)
    rec.add(str(inside), str(root))  # 同じファイルを 2 つのモデルが使っても 1 回
    rec.add(outside, root)
    rec.add(root / "gone.safetensors", root)  # もう無いもの
    assert rec.existing() == [(inside, 10)]
    rec.delete()
    assert not inside.exists() and outside.exists()


def test_record_skips_links(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    target = tmp_path / "real.safetensors"
    target.write_bytes(b"z")
    link = root / "link.safetensors"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("シンボリックリンクを作れない")
    rec = DownloadRecord()
    rec.add(link, root)
    assert rec.existing() == []
    rec.delete()
    assert target.exists()


def test_measure_download_records_through_download(server, comfy, db_file, monkeypatch):
    """share・measure の落とし方(_measure_download → download)が記録に足す。"""
    args = cli.build_parser().parse_args(
        ["share", "--yes", "--db", str(db_file), "--comfyui-dir", str(comfy)]
    )
    args._dl_record = DownloadRecord()
    assert cli._measure_download(args, "test_tiny") == 0
    assert sorted(p for p, _ in args._dl_record.existing()) == sorted(
        [comfy / "models" / UNET, comfy / "models" / VAE]
    )


# --- CLI: share ---------------------------------------------------------------------------------


@pytest.fixture
def fake_dl(monkeypatch, tmp_path):
    """落とす処理の偽物: 置き場所(tmp)にファイルを作って記録に足す。もとからあるファイルも 1 つ置く。"""
    root = tmp_path / "ComfyUI" / "models"
    (root / "checkpoints").mkdir(parents=True)
    old = root / "checkpoints" / "old.safetensors"
    old.write_bytes(b"o" * 100)
    made: list[Path] = []

    def fake(args, model_id, **k):
        p = root / "checkpoints" / f"{model_id}.safetensors"
        p.write_bytes(b"n" * 2048)
        made.append(p)
        args._dl_record.add(p, root)
        (root / "checkpoints" / "shared_vae.safetensors").write_bytes(b"v" * 10)
        args._dl_record.add(root / "checkpoints" / "shared_vae.safetensors", root)
        args._dl_record.add(root / "checkpoints" / "shared_vae.safetensors", root)  # 2 つのモデルが使う
        return 0

    monkeypatch.setattr(cli, "_measure_download", fake)
    return {"root": root, "old": old, "made": made}


@pytest.fixture
def dl_only(share_plan):
    share_plan["sugg"] = [sugg("a_installed_short", 60), sugg("b_not_installed", 60, "no", 2 * 1024**3)]
    return share_plan


def test_share_asks_to_delete_what_it_downloaded_default_keeps(
    capsys, monkeypatch, this_pc, dl_only, gh, fake_dl
):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "y", "", "")  # 測る・落とす・送り方 1・消す → Enter(既定は消さない)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert asked[-1] == "消しますか? [y/N] "
    assert "この実行で落としたファイル 2 件" in err and "前から入っていたファイルは入れていません" in err
    assert str(fake_dl["made"][0]) in err and "old.safetensors" not in err
    assert err.count("shared_vae.safetensors") == 1  # 2 つのモデルが使うものも 1 回だけ
    assert "消さずに残しました" in err
    assert fake_dl["made"][0].exists() and fake_dl["old"].exists()


def test_share_deletes_on_yes_and_never_the_old_files(capsys, monkeypatch, this_pc, dl_only, gh, fake_dl):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers(monkeypatch, "", "y", "", "y")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert "消しました: 2 件" in err
    assert not fake_dl["made"][0].exists() and fake_dl["old"].exists()


def test_share_yes_alone_keeps_and_flag_deletes_without_asking(
    capsys, monkeypatch, this_pc, dl_only, gh, fake_dl
):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("尋ねない"))
    assert cli.main(["--lang", "ja", "share", "--yes", "--download", "--url", "http://x:1"]) == 0
    err = capsys.readouterr().err
    assert "は残しました" in err and "--delete-downloaded" in err
    assert fake_dl["made"][0].exists()
    fake_dl["made"][0].unlink()
    assert (
        cli.main(
            ["--lang", "ja", "share", "--yes", "--download", "--delete-downloaded", "--url", "http://x:1"]
        )
        == 0
    )
    assert "消しました: 2 件" in capsys.readouterr().err
    assert not fake_dl["made"][-1].exists() and fake_dl["old"].exists()


def test_share_without_terminal_keeps(capsys, monkeypatch, this_pc, dl_only, gh, fake_dl):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("尋ねない"))
    assert cli.main(["--lang", "en", "share", "--yes", "--download", "--url", "http://x:1"]) == 0
    assert "Kept the 2 file(s)" in capsys.readouterr().err and fake_dl["made"][0].exists()


def test_share_offers_after_ctrl_c(capsys, monkeypatch, this_pc, dl_only, gh, fake_dl):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def stop(*a, **k):
        raise KeyboardInterrupt

    real = cli._share_measure_one
    calls = []

    def measure_one(args, url, x, *rest):
        calls.append(x.model_id)
        if x.model_id == "b_not_installed":
            stop()
        return real(args, url, x, *rest)

    monkeypatch.setattr(cli, "_share_measure_one", measure_one)
    asked = answers(monkeypatch, "", "y", "", "y")  # 測る・落とす・送り方 1・消す
    cli.main(["--lang", "ja", "share", "--url", "http://x:1"])
    err = capsys.readouterr().err
    assert calls == ["a_installed_short", "b_not_installed"]
    assert asked[-1] == "消しますか? [y/N] " and "消しました: 2 件" in err
    assert not fake_dl["made"][0].exists() and fake_dl["old"].exists()


def test_nothing_downloaded_asks_nothing(capsys, monkeypatch, this_pc, share_plan, gh):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "")
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 0
    assert all("消しますか" not in a for a in asked)
    assert "落としたファイル" not in capsys.readouterr().err


def test_share_json_never_asks_or_prints_about_deleting(capsys, monkeypatch, this_pc, share_plan, gh):
    """--json は落とさない(_share_checks)ので、--delete-downloaded を付けても尋ねず、stdout は JSON だけ。"""
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("尋ねない"))
    assert cli.main(["share", "--yes", "--json", "--delete-downloaded", "--url", "http://x:1"]) == 0
    cap = capsys.readouterr()
    json.loads(cap.out)
    assert "落としたファイル" not in cap.err and "消しました" not in cap.err


def test_error_is_printed_before_the_delete_question(capsys, monkeypatch, this_pc, dl_only, gh, fake_dl):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def boom(*a, **k):
        raise cli.CliError("broken")

    monkeypatch.setattr(cli, "_share_send", boom)
    events = []

    def ask(prompt=""):
        events.append("ask:" + prompt)
        return {"消しますか? [y/N] ": "n"}.get(prompt, "y" if "落とし" in prompt else "")

    monkeypatch.setattr("builtins.input", ask)
    assert cli.main(["--lang", "ja", "share", "--url", "http://x:1"]) == 1
    err = capsys.readouterr().err
    assert err.index("エラー: broken") < err.index("消さずに残しました")
    assert events[-1] == "ask:消しますか? [y/N] "


# --- CLI: measure -------------------------------------------------------------------------------


def test_measure_offers_to_delete_the_model_it_downloaded(capsys, monkeypatch, this_pc, tmp_path):
    from types import SimpleNamespace

    from genmodelfit import measure

    root = tmp_path / "ComfyUI" / "models"
    (root / "checkpoints").mkdir(parents=True)
    entry = SimpleNamespace(id="m1", name="m1", files=[])
    monkeypatch.setattr(measure, "resolve", lambda *a, **k: SimpleNamespace(model=entry))
    monkeypatch.setattr(measure, "installed_state", lambda *a, **k: measure.Installed("no", 5, 5, 1))
    monkeypatch.setattr(measure, "time_hint", lambda *a, **k: (60, "typical"))
    monkeypatch.setattr(cli, "_measure_screen", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_node_check", lambda url: SimpleNamespace(missing=lambda mid: []))
    made = root / "checkpoints" / "m1.safetensors"

    def fake_dl(args, model_id, **k):
        made.write_bytes(b"m" * 4096)
        args._dl_record.add(made, root)
        return 0

    def fake_run(*a, **k):
        raise KeyboardInterrupt  # 測る途中の Ctrl+C でも尋ねる

    monkeypatch.setattr(cli, "_measure_download", fake_dl)
    monkeypatch.setattr(cli, "_measure_run", fake_run)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asked = answers(monkeypatch, "", "y")  # 測りますか → はい、消す → y
    with pytest.raises(KeyboardInterrupt):
        cli.main(["--lang", "ja", "measure", "m1", "--url", "http://x:1"])
    assert asked[-1] == "消しますか? [y/N] " and not made.exists()
