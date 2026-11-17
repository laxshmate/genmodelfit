"""入れてすぐ数回たたく人の使い方(ヘルプ・誤った ID・パイプ・端末の幅・起動の仕方)。"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from clihelp import bundled_uncensored, child_env, console_script, overlong_lines, run_cli
from frozenlist import FROZEN_DB

from genmodelfit import __version__, cli

SUBCOMMANDS = [
    "system",
    "recommend",
    "fit",
    "search",
    "types",
    "info",
    "update",
    "gate",
    "bench",
    "download",
    "measure",
    "share",
    "diff",
    "contrib",
    "review",
    "config",
]
HW = ["--vram", "8", "--ram", "32", "--no-cache"]

HEADER = [
    "Type",
    "Status",
    "Score",
    "Model",
    "Use",
    "Prec",
    "VRAM",
    "Mem%",
    "Mode",
    "Disk",
    "License",
]


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("cache")


def test_subcommand_list_is_complete():
    p = cli.build_parser()
    sub = next(a for a in p._actions if a.dest == "command")
    assert sorted(sub.choices) == sorted(SUBCOMMANDS)


# 使い方の行の頭(argparse の「usage: 」。日本語は「使い方: 」。#76)
USAGE = {"en": "usage: ", "ja": "使い方: "}


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_no_args_prints_full_help(cache, lang):
    r = run_cli([], cache, GENMODELFIT_LANG=lang)
    assert r.returncode == 0 and r.stderr == b""
    out = r.stdout.decode("utf-8")
    assert out.startswith(USAGE[lang] + "genmodelfit")
    for name in SUBCOMMANDS:
        assert f"    {name} " in out, name  # 一覧に説明つきで並ぶ


@pytest.mark.parametrize("lang", ["en", "ja"])
@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_subcommand_help(cache, name, lang):
    r = run_cli([name, "--help"], cache, GENMODELFIT_LANG=lang)
    assert r.returncode == 0 and r.stderr == b""
    assert r.stdout.decode("utf-8").startswith(f"{USAGE[lang]}genmodelfit {name}")


def test_version(cache):
    r = run_cli(["--version"], cache)
    assert r.returncode == 0 and r.stdout.decode("utf-8").strip() == f"genmodelfit {__version__}"


def test_unknown_id_goes_to_stderr_in_utf8(cache):
    r = run_cli(["info", "no_such_model_xyz", *HW], cache)
    assert r.returncode == 1 and r.stdout == b""
    err = r.stderr.decode("utf-8")  # 厳密に UTF-8(cp932 のままだとここで落ちるか化ける)
    assert "モデルが見つかりません" in err and "no_such_model_xyz" in err


def test_usage_error_exits_2(cache):
    r = run_cli(["recommend", "--content", "nsfw"], cache)
    assert r.returncode == 2 and r.stdout == b""
    assert "--content" in r.stderr.decode("utf-8")


@pytest.mark.parametrize("args", [["fit", *HW, "--content", "all"], ["fit", *HW, "--json"]])
def test_closed_pipe_does_not_traceback(cache, args):
    """`| head -1` のように 1 行だけ読んで閉じられても、落ちた跡を出さない。"""
    p = subprocess.Popen(
        [sys.executable, "-m", "genmodelfit", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(cache),
    )
    assert p.stdout is not None and p.stderr is not None
    assert p.stdout.readline()
    p.stdout.close()
    err = p.stderr.read().decode("utf-8", "replace")
    code = p.wait(timeout=120)
    assert "Traceback" not in err and "Exception ignored" not in err, err
    assert code == 0, err


TABLE_COMMANDS = [
    ["recommend", *HW],
    ["recommend", *HW, "--content", "all", "--include-tools", "-n", "100"],
    ["fit", *HW, "--content", "all"],
    ["fit", "--vram", "0", "--ram", "16", "--no-cache"],
    ["search", "flux", *HW, "--content", "all"],
    ["search", "no_such_model_xyz", *HW],
    ["recommend", *HW, "-m", "image", "--task", "edit"],
    ["recommend", "--vram", "1", "--ram", "16", "--no-cache", "--all-workflows"],
    ["types", "--no-cache"],
    ["info", "image_z_image_turbo", *HW],
    ["gate", "--no-cache", "--only-held"],
    ["system", "--vram", "8", "--ram", "32"],
    ["info", "image_sdxl_simple", *HW],
    # 理由の文が長い。項目は凍らせた一覧から読む(毎週の更新で同梱の一覧から消えても同じ入力で。#197)
    ["info", "hf:Heartsync/NSFW-Uncensored", *HW, "--db", str(FROZEN_DB)],
    ["info", "templates_purz_animatediff_simple_weighted_ipadapters_looping_animation", *HW],  # 一番長い ID
    ["info", "template_rob_split_stack_qwen_multi_wan22", *HW],  # ファイルが一番多い(11)
    ["download", "Z-Image-Turbo", "--list", *HW],
    ["download", "template_rob_split_stack_qwen_multi_wan22", "--list", *HW],
]
# 文だけのコマンド(幅を見ない)。短い行しか出さない
_PLAIN = ("gate", "system")


@pytest.mark.parametrize("args", TABLE_COMMANDS, ids=lambda a: " ".join(a[:2]))
@pytest.mark.parametrize("columns", [100, 60, 250])
def test_width(cache, args, columns):
    """表と info は端末の幅(最大 100 桁)に収める。全角は 2 桁で数える。"""
    r = run_cli(args, cache, COLUMNS=str(columns))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    lines = r.stdout.decode("utf-8").splitlines()
    assert lines
    limit = 100 if args[0] in _PLAIN else min(columns, 100)
    # 1 つで幅より長い英数字の並び(ID・ファイル名・URL)は途中で折り返さず、その行だけはみ出す(#76)
    assert not overlong_lines(lines, limit)  # 箇条書きの「- 」は語に数えない


def test_fit_says_uncensored_hidden(cache):
    out = run_cli(["fit", *HW, "--lang", "en"], cache).stdout.decode("utf-8")
    # 同梱の無検閲の件数(毎週の更新で変わるので一覧から数える。#19)。
    # ライセンスの分からないものも既定では隠す(#28)。
    # 同じモデルの別のワークフローは「same model」に数える
    assert (
        "All models: " in out
        and re.search(
            rf"\(\d+ hidden: uncensored {bundled_uncensored()}, tools \d+, "
            r"license unknown \d+, too tight \d+",
            out,
        )
        and " more workflows merged into" in re.sub(r"\s+", " ", out)  # 見出しは幅 100 で折り返す
    )
    assert "add --uncensored" in out and "--any-license" in out
    every = ["--content", "all", "--all-workflows", "--all"]
    out = run_cli(["fit", *HW, *every, "--lang", "en"], cache).stdout.decode("utf-8")
    assert "hidden" not in out
    out = run_cli(["fit", *HW, "-m", "3d", "--lang", "en"], cache).stdout.decode("utf-8")
    assert "uncensored" not in out and "other types" in out  # 3D には無検閲が無い。ほかの種類は隠した数に出す
    # 日本語(#76)
    out = run_cli(["fit", *HW], cache).stdout.decode("utf-8")
    flat = re.sub(r"\s+", "", out)
    assert "隠した" in out and f"無検閲{bundled_uncensored()}" in flat and "モデルにまとめた" in flat
    assert "出すなら --uncensored" in out and "--any-license" in out


def test_pc_line_and_sections(cache):
    out = run_cli(["recommend", *HW, "-n", "3", "--lang", "en"], cache).stdout.decode("utf-8").splitlines()
    assert out[0] == "PC: assumed 8.0 GiB VRAM (--vram), RAM 32.0 GiB (--ram)"
    assert (
        out[1].startswith("Recommended: ") and f"hidden: uncensored {bundled_uncensored()}, tools " in out[1]
    )
    heads = [x for x in out if x.startswith("== ")]
    assert [h.split()[1].rstrip(":") for h in heads] == ["image", "video", "music", "sfx", "tts", "3d"]
    # 日本語: 見出しは「== 画像 (image): …」(-m に書く名前も見せる。#76)
    ja = run_cli(["recommend", *HW, "-n", "3"], cache).stdout.decode("utf-8").splitlines()
    assert ja[0] == "PC: VRAM 8.0 GiB と仮定(--vram)、メモリ 32.0 GiB(--ram)"
    assert ja[1].startswith("おすすめ: ") and f"無検閲 {bundled_uncensored()}・補助ツール " in ja[1]
    heads = [x for x in ja if x.startswith("== ")]
    assert [re.search(r" \((\w+)\): ", h).group(1) for h in heads] == [
        "image",
        "video",
        "music",
        "sfx",
        "tts",
        "3d",
    ]
    assert heads[0].startswith("== 画像 (image): ") and "(続きは -m image)" in heads[0]
    header = next(x for x in out if x.startswith("Type"))
    assert header.split()[:4] == ["Type", "Status", "Score", "Model"]  # 節の中も同じ列の順(0.1.1)
    # -m を付けると 1 つの表
    out = run_cli(["recommend", *HW, "-m", "image,video"], cache).stdout.decode("utf-8").splitlines()
    assert not [x for x in out if x.startswith("== ")]
    assert next(x for x in out if x.startswith("Type")).split()[:4] == ["Type", "Status", "Score", "Model"]


def test_cpu_only_sections_say_when_nothing_runs(cache):
    out = run_cli(["recommend", "--vram", "0", "--ram", "16", "--no-cache"], cache).stdout.decode("utf-8")
    assert "== 3D (3d): この条件でこの PC で動くモデルはありません" in out
    assert re.search(r"== 読み上げ \(tts\): \d+ 件中 上位 3 件", out)
    # #39: SD 系と Stable Audio は GPU なしでも CPU で動く(marginal)。
    # #22: 使える GPU が無いことを 1 行で知らせる
    assert "== 画像 (image): " in out and "SDXL1.0 Base" in out
    assert "この PC には使える GPU がありません: CPU で動くモデルだけを出します" in out
    en = run_cli(["recommend", "--vram", "0", "--ram", "16", "--no-cache", "--lang", "en"], cache)
    out_en = en.stdout.decode("utf-8")
    assert "== 3d: no model runs on this PC with these filters" in out_en and "== tts: top 3 of " in out_en
    assert "This PC has no usable GPU: only models that run on CPU are shown" in out_en
    assert all(len(line) <= 100 for line in out.splitlines())


def test_search(cache):
    r = run_cli(["search", "FLUX", *HW], cache)
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0 and r.stderr == b""
    assert "「FLUX」の検索: " in out and "Flux.1 Schnell" in out and "隠した" in out
    # 合わなくても終了コード 0。stdout に「該当なし」(英語は no match)
    r = run_cli(["search", "no_such_model_xyz", *HW], cache)
    assert r.returncode == 0 and "該当なし" in r.stdout.decode("utf-8")
    r = run_cli(["search", "no_such_model_xyz", *HW, "--lang", "en"], cache)
    assert r.returncode == 0 and "(no match)" in r.stdout.decode("utf-8")
    r = run_cli(["search", "  ", *HW], cache)
    assert r.returncode == 1 and "空" in r.stderr.decode("utf-8")


def test_info_partial_match(cache):
    """語の一部で探す。一覧は凍らせた一覧(合う項目が毎週の更新で変わらないように。#197)。"""
    hw = [*HW, "--db", str(FROZEN_DB)]
    # ID がそのまま合えば、無検閲でも出す
    r = run_cli(["info", "hf:Heartsync/NSFW-Uncensored", *hw], cache)
    assert r.returncode == 0 and "内容: uncensored" in r.stdout.decode("utf-8")
    # 一部の一致が 1 件だけなら、それを出す
    r = run_cli(["info", "sdxlturbo", *hw], cache)
    assert r.returncode == 0 and "[sdxlturbo_example]" in r.stdout.decode("utf-8")
    # 2 件以上は先頭を黙って選ばない。候補は stderr、終了コードは 1。既定(sfw)では無検閲を候補に出さない
    r = run_cli(["info", "sdxl", *hw], cache)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 1 and r.stdout == b""
    assert "ID で指定してください" in err and "image_sdxl_simple" in err
    assert "lustify" not in err.lower() and "nova-furry" not in err
    r = run_cli(["info", "sdxl", *hw, "--content", "all"], cache)
    assert r.returncode == 1 and "lustify" in r.stderr.decode("utf-8").lower()
    # 無検閲にしか合わないときは、--uncensored を案内する
    r = run_cli(["info", "Qwen-Image-2.1", *hw], cache)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 1 and r.stdout == b"" and "--uncensored" in err


def test_python_m(cache):
    r = run_cli(["recommend", *HW, "-n", "3"], cache)
    assert r.returncode == 0 and "おすすめ: " in r.stdout.decode("utf-8")


def test_console_script(cache):
    exe = console_script()
    if exe is None:
        pytest.skip("genmodelfit のコマンドが venv に無い(pip install -e . で入る)")
    env = child_env(cache)
    args = [exe, "recommend", *HW, "-n", "3"]
    r = subprocess.run(args, capture_output=True, env=env, timeout=120, check=False)
    assert r.returncode == 0 and "おすすめ: " in r.stdout.decode("utf-8")
    r = subprocess.run([exe, "--version"], capture_output=True, env=env, timeout=60, check=False)
    assert r.stdout.decode("utf-8").strip() == f"genmodelfit {__version__}"


def test_info_hint_names_the_hidden_content_and_strips_spaces():
    """--content で外れた候補の案内は、実際に外れた内容を書く。前後の空白は無視する。"""
    from genmodelfit.cli import CliError, resolve_model
    from genmodelfit.db import load

    db = load(use_cache=False)
    try:
        resolve_model(db, "sdxl_simple", "uncensored")
    except CliError as e:
        assert "sfw" in str(e) and "無検閲" not in str(e)
    else:
        raise AssertionError("uncensored では sfw の候補を出さない")
    assert resolve_model(db, "  image_sdxl_simple ").id == "image_sdxl_simple"


# ヘルプの節の名前(日本語は日本語だけで書く。#76)
AGENT_SECTIONS = {
    "en": ("PRECONDITIONS:", "SIDE EFFECTS:", "EXIT CODES:", "AGENT USAGE:"),
    "ja": ("前提:", "副作用:", "終了コード:", "使い方の例(エージェント・アプリ向け):"),
}


@pytest.mark.parametrize("lang", ["en", "ja"])
@pytest.mark.parametrize("name", ["recommend", "fit", "search", "info"])
def test_help_has_agent_sections(cache, name, lang):
    """llmfit と同じく、ヘルプに前提・副作用・終了コード・使い方の例・JSON の項目を書く。"""
    out = run_cli([name, "--help"], cache, COLUMNS="100", GENMODELFIT_LANG=lang).stdout.decode("utf-8")
    for section in (*AGENT_SECTIONS[lang], "docs/schema.md"):
        assert section in out, (name, section)
    assert f"genmodelfit {name} " in out.split(AGENT_SECTIONS[lang][3])[1]
    if name != "info":
        assert "score_components" in out and "held_reasons" in out
