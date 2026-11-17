# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""表示の言語(#73・#76): 決め方の順(既定は en)と、en で日本語の文字が 1 つも出ないこと、ja で英語だけの文が
出ないこと(表の列の名前・段階の名前・Mode の印・ID・コマンドは両方の言語で同じ)。折り返しの決まり(#76)も見る。

一覧に保存された自由記述(review.note・license_evidence・description.ja・実測の source / note)は訳さないので、
JSON ではその項目を外してから、表では一覧にある文そのものを外してから調べる。
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pytest
from clihelp import overlong_lines, run_cli
from rich.cells import cell_len

import genmodelfit as gmf
from genmodelfit import cli, i18n
from genmodelfit.models import MODALITIES

# 日本語の文字(かな・漢字・全角)と和文の約物(U+3000–303F)
JA = re.compile(r"[　-〿぀-ヿ一-鿿＀-￯]")
HW = ["--vram", "8", "--ram", "32", "--no-cache"]
FIXTURES = Path(__file__).parent / "fixtures"
DIFF_PAIR = [str(FIXTURES / "diff" / "0.6.0-0.7.0" / name) for name in ("old.json", "new.json")]


@pytest.fixture
def no_pin(monkeypatch):
    """conftest の ja の固定(環境変数)を外す。"""
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)


# --- 決め方の順 -----------------------------------------------------------------


def test_default_is_en_even_on_a_japanese_os(no_pin, monkeypatch):
    """既定はいつも英語。OS の言語(LANG・LC_ALL など)は見ない(2026-10-25 にオーナーが決めた)。"""
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "ja_JP.UTF-8")
    assert gmf.get_language() == "en"


def test_set_language_beats_default(no_pin):
    gmf.set_language("ja")
    assert gmf.get_language() == "ja"
    gmf.set_language(None)  # 取り消すと既定の en に戻る
    assert gmf.get_language() == "en"


def test_env_beats_set_language(no_pin, monkeypatch):
    gmf.set_language("en")
    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    assert gmf.get_language() == "ja"
    monkeypatch.setenv(i18n.ENV_VAR, "xx")  # 知らない値は無視して次へ
    assert gmf.get_language() == "en"


def test_cli_flag_beats_env(monkeypatch, capsys):
    monkeypatch.setenv(i18n.ENV_VAR, "ja")
    gmf.set_language("ja")
    assert cli.main(["types", "--no-cache", "--lang", "en"]) == 0
    assert not JA.search(capsys.readouterr().out)
    assert gmf.get_language() == "ja"  # main が終われば --lang は残らない


def test_cli_flag_does_not_leak_to_other_threads(no_pin):
    """--lang の固定は ContextVar。ほかのスレッドのライブラリの呼び出しには効かない。"""
    import threading

    seen: list[str] = []
    with i18n.forced("ja"):
        th = threading.Thread(target=lambda: seen.append(gmf.get_language()))
        th.start()
        th.join()
        assert gmf.get_language() == "ja"
    assert seen == ["en"]


def test_set_language_rejects_unknown():
    with pytest.raises(ValueError):
        gmf.set_language("fr")
    with pytest.raises(ValueError):
        gmf.set_language("ja_JP")


def test_tr_and_pick(no_pin):
    gmf.set_language("ja")
    assert i18n.tr("日本語", "English") == "日本語"
    assert i18n.pick({"en": "E", "ja": "J"}) == "J"
    gmf.set_language("en")
    assert i18n.tr("日本語", "English") == "English"
    assert i18n.tr("日本語", "English").ja == "日本語"  # 保存する文のために日本語も持つ
    assert i18n.pick({"en": "E", "ja": "J"}) == "E"
    assert i18n.pick({"ja": "J"}) == "J"  # 無ければもう片方


def test_text_survives_copy_and_pickle(no_pin):
    import copy
    import pickle

    gmf.set_language("en")
    t = i18n.tr("日本語", "English")
    for c in (copy.deepcopy(t), pickle.loads(pickle.dumps(t))):
        assert c == "English" and c.ja == "日本語"


def test_env_reaches_child_process(tmp_path):
    r = run_cli(["types", "--no-cache"], tmp_path, GENMODELFIT_LANG="en")
    assert r.returncode == 0
    assert not JA.search(r.stdout.decode("utf-8"))


def test_child_process_defaults_to_en(tmp_path):
    """環境変数も --lang も無ければ、日本語の OS(LANG=ja_JP)でも英語。"""
    import subprocess
    import sys

    from clihelp import child_env

    env = child_env(tmp_path)
    env.pop(i18n.ENV_VAR, None)
    env["LANG"] = "ja_JP.UTF-8"
    r = subprocess.run(
        [sys.executable, "-m", "genmodelfit", "types", "--no-cache"],
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0
    assert not JA.search(r.stdout.decode("utf-8"))


# --- 一覧に保存する文は言語によらず日本語 ---------------------------------------------


def test_stored_fetch_errors_are_japanese_in_en(no_pin):
    """update が一覧(sources.warnings)に残す取得の失敗の文は、表示の言語が en でも日本語(#73)。"""
    from genmodelfit import update
    from genmodelfit.fetch import FetchError

    gmf.set_language("en")

    class _Broken:
        def get_json(self, url):
            raise FetchError(url, i18n.tr("JSON ではありません", "not JSON"))

    _info, warn = update.latest_comfyui(_Broken())
    assert warn is not None and "JSON ではありません" in warn and not re.search("not JSON", warn)
    e = FetchError("https://example.invalid/x", i18n.tr("JSON ではありません", "not JSON"))
    assert str(e).startswith("not JSON") and i18n.stored_message(e).startswith("JSON ではありません")


def test_worker_threads_follow_the_caller_language(no_pin):
    """HfClient.map の仕事は、呼んだ側の言語の固定(weekly の ja など)を引き継ぐ。"""
    from genmodelfit.hf import HfClient

    client = HfClient(object(), workers=4)
    with i18n.forced("ja"):
        got = client.map(lambda _: gmf.get_language(), range(8))
    assert got == ["ja"] * 8


# --- en: 日本語の文字が出ない -----------------------------------------------------


def _stored_free_text() -> list[str]:
    """一覧・実測に保存された自由記述(訳さないもの)。長いものから外す。"""
    texts: set[str] = set()
    db = gmf.load_db()
    for m in db.models:
        d = m.to_dict()
        if isinstance(d.get("review"), dict) and d["review"].get("note"):
            texts.add(d["review"]["note"])
        texts.update(d.get("license_evidence") or [])
        for f in d.get("files") or []:
            texts.update(f.get("license_evidence") or [])
        if isinstance(d.get("description"), dict) and d["description"].get("ja"):
            texts.add(d["description"]["ja"])
    for ms in db.measurements:
        for key in ("source", "note"):
            v = getattr(ms, key, None)
            if v:
                texts.add(v)
    for f in (FIXTURES / "contrib").rglob("*.json"):  # 提出の自由記述(試験用の提出の note など)
        sub = json.loads(f.read_text(encoding="utf-8"))
        for row in [sub, sub.get("measurement") or {}, *(sub.get("measurements") or [])]:
            for key in ("source", "note"):
                if isinstance(row.get(key), str):
                    texts.add(row[key])
    return sorted(texts, key=len, reverse=True)


FREE_TEXT = _stored_free_text()


def _strip_json(obj):
    """JSON から訳さない項目を外す: 保存された自由記述(一覧にある文そのもの)、description.ja、
    label_ja(types の日本語のラベル。docs/schema.md「Uses」でキーの名前が日本語のラベルと決まっている)。"""
    free = set(FREE_TEXT)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "label_ja" or (isinstance(v, str) and v in free):
                continue
            if k == "description" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk != "ja"}
            out[k] = _strip_json(v)
        return out
    if isinstance(obj, list):
        return [_strip_json(v) for v in obj if not (isinstance(v, str) and v in free)]
    return obj


def _run(capsys, args: list[str]) -> tuple[int, str]:
    try:
        code = cli.main([*args, "--lang", "en"])
    except SystemExit as e:  # --help・使い方の誤り
        code = e.code if isinstance(e.code, int) else 1
    out = capsys.readouterr()
    return code, out.out + out.err


def _assert_no_japanese(text: str, label: str) -> None:
    hits = [line for line in text.splitlines() if JA.search(line)]
    assert not hits, f"{label}: {hits[:5]}"


TABLE_CASES = [
    ["system", *HW],
    ["recommend", *HW],
    ["recommend", "-m", "image", *HW],
    ["recommend", "--content", "all", "--task", "edit", *HW],
    ["fit", "-m", "image", "-n", "10", *HW],
    ["fit", "--all", "--content", "all", *HW],
    ["search", "flux", *HW],
    ["search", "zzzz-no-such-model", *HW],
    ["types", "--no-cache"],
    ["info", "Z-Image-Turbo", *HW],
    ["info", "image_sdxl_simple", *HW],
    ["info", "no-such-model-xyz", *HW],
    ["download", "x", "--list", *HW],
    ["download", "Z-Image-Turbo", "--list", *HW],
    ["gate", "--no-cache"],
    ["gate", "--no-cache", "--only-held"],
    ["review", "list", "--no-cache"],
    ["diff"],
    ["diff", *DIFF_PAIR],
    ["contrib", "validate", str(FIXTURES / "contrib")],
    ["contrib", "aggregate", str(FIXTURES / "contrib")],
    [
        "diff",
        str(FIXTURES / "diff" / "0.4.0-0.7.0" / "old.json"),
        str(FIXTURES / "diff" / "0.4.0-0.7.0" / "new.json"),
    ],
    ["contrib", "show", str(FIXTURES / "contrib" / "rtx-3070" / "2026-10-20-5bedb2da.json")],
    ["contrib", "aggregate", str(FIXTURES / "contrib"), "--report"],
    ["recommend", "--min-fit", "good", "--fit", "good", *HW],  # 使い方の誤り
    ["recommend", "--limit", "0", *HW],  # 型の誤り
]


@pytest.mark.parametrize("args", TABLE_CASES, ids=lambda a: " ".join(a[:3]))
def test_en_table_has_no_japanese(capsys, monkeypatch, tmp_path, args):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    _code, text = _run(capsys, args)
    for t in FREE_TEXT:
        text = text.replace(t, "")
    _assert_no_japanese(text, " ".join(args))


JSON_CASES = [
    ["system", *HW],
    ["recommend", "--content", "all", "--all-workflows", "--limits", *HW],
    ["fit", "--all", "--content", "all", *HW],
    ["search", "wan", *HW],
    ["types", "--no-cache"],
    ["info", "Z-Image-Turbo", *HW],
    ["info", "x", *HW],  # 候補が複数
    ["info", "no-such-model-xyz", *HW],
    ["download", "x", "--list", *HW],
    ["download", "Z-Image-Turbo", "--list", *HW],
    ["gate", "--no-cache"],
    ["review", "list", "--no-cache", "--all"],
    ["diff", *DIFF_PAIR],
    ["contrib", "validate", str(FIXTURES / "contrib")],
]


@pytest.mark.parametrize("args", JSON_CASES, ids=lambda a: " ".join(a[:3]))
def test_en_json_has_no_japanese(capsys, monkeypatch, tmp_path, args):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    _code, text = _run(capsys, [*args, "--json"])
    data = json.loads(text)
    _assert_no_japanese(json.dumps(_strip_json(data), ensure_ascii=False, indent=1), " ".join(args))


HELP_CASES = [
    [],
    ["system"],
    ["recommend"],
    ["fit"],
    ["search"],
    ["types"],
    ["info"],
    ["update"],
    ["download"],
    ["gate"],
    ["diff"],
    ["review"],
    ["review", "list"],
    ["review", "approve"],
    ["review", "reject"],
    ["bench"],
    ["measure"],
    ["share"],
    ["contrib"],
    ["contrib", "share"],
    ["contrib", "intake"],
    ["contrib", "make"],
    ["contrib", "show"],
    ["contrib", "validate"],
    ["contrib", "aggregate"],
]


@pytest.mark.parametrize("args", HELP_CASES, ids=lambda a: " ".join(a) or "top")
def test_en_help_has_no_japanese(capsys, args):
    code, text = _run(capsys, [*args, "--help"])
    assert code == 0 and text.startswith("usage: genmodelfit")
    _assert_no_japanese(text, " ".join([*args, "--help"]))


def test_en_library_texts(no_pin):
    """Python から: set_language("en") で理由・注意・gate の detail が英語になる。"""
    gmf.set_language("en")
    s = gmf.system(vram_gb=8, ram_gb=32)
    for f in gmf.recommend(system_info=s, content="all", min_fit="too_tight", limit=None, all_workflows=True):
        d = f.to_dict()
        texts = [*d.get("notes", []), *(d.get("held_reasons") or [])]
        texts += [c["detail"] for c in (d.get("gate") or {}).get("checks", [])]
        texts += list((d.get("estimate") or {}).get("reasons", []))
        for t in texts:
            assert not JA.search(t), (f.model.id, t)


# --- ja: 日本語だけ(#76) ----------------------------------------------------------


def test_ja_text_is_japanese(capsys):
    assert cli.main(["recommend", *HW]) == 0
    out = capsys.readouterr().out
    # 見出しは日本語の種類の名前と、-m に書く名前(1 行に 2 つの言語の文を並べない)
    assert "== 画像 (image): " in out and "(続きは -m image)" in out and "top " not in out
    assert cli.main(["types", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "  gen     文章から作る(3D は画像から)" in out and "generate from text" not in out
    assert cli.main(["info", "no-such-model-xyz", *HW]) == 1
    assert capsys.readouterr().err.startswith("エラー: モデルが見つかりません")


# 英語の語(大文字で始まってもよい。記号・数字を含まない。前後の ( ) , . ; : は許す)
_EN_WORD = re.compile(r"^[\"'(]*[A-Za-z][A-Za-z']+[,.;:)\"']*$")
# 機械の値の印: ID・ファイル名・パス・URL・オプション・引数の並び・数を含む語({ } [ ] _ / \ < > = $ 数字)
_MACHINE_TOKEN = re.compile(r"[{}\[\]_/\<>=$|~+@#%0-9]|^-|\w\.\w")
# 環境変数の例・JSON の鍵の並びの頭(段落ごと機械の行)
_MACHINE_HEAD = ("$env:", "export ", "JSON (--json):")
_COMMAND_HEAD = ("genmodelfit ", "python -m genmodelfit")
_OPTION = re.compile(r"-{1,2}[A-Za-z]")
_WORD_PUNCT = "\"'(),.;:)"
_TITLE_ID = re.compile(r"\s\[[\w:./-]+\]$")
# 日本語の出力でも英語のまま出すもの(README「Language」): 段階の名前・動かし方の印・列の名前・用語・固有名詞
_KEEP_ENGLISH = frozenset(
    re.findall(
        r"\w+",
        "gib mib kib tib gb mb perfect good marginal too tight verified gpu cpu ram vram ssd hdd gen edit "
        "json comfyui hugging face windows linux "
        "macos nvidia amd intel apple torch pytorch cuda rocm xpu mps",
    )
)


def english_only_lines(text: str) -> list[str]:
    """ja の出力のうち、日本語の無い英語の文の行(#76)。

    外すもの: 日本語を含む行、表の列の名前と行(先頭が Type か種類の名前)、contrib validate の ok の行、
    「題名  [ID]」の見出し、使い方の段落(使い方: から空行まで)、
    環境変数の例・JSON の鍵の並びの段落(JSON (--json): から空行まで)。コマンドの行は「#」の後の説明だけ、
    オプションの行(--help の一覧)は名前の後の説明だけを見る。
    行ごとは外さない。ID・ファイル名・パス・URL・オプション・数のような語(_MACHINE_TOKEN)と、英語のまま出す
    決まりの語(_KEEP_ENGLISH)・全部大文字の語だけを取り除き、残った英語の語が 2 つ以上あれば英語の文とみなす
    (「Mode: GPU all on GPU, …」「error: Not Found」「Note: image_x needs a license check …」を見つける)。"""
    bad = []
    skip_block = False  # 使い方・JSON の鍵の段落(空行まで)
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("使い方:", "usage:", *_MACHINE_HEAD)):
            skip_block = True
        if skip_block:
            skip_block = bool(s)
            continue
        if not s or JA.search(s):
            continue
        head = s.split()
        # 表の列の名前の行・contrib validate の結果の行(結果の語 ok / notice と GPU の名前が入る)
        if head[0] in ("Type", "ok", "notice"):
            continue
        if head[0] in MODALITIES and not (len(head) > 1 and _EN_WORD.match(head[1])):  # 表の行(種類 ● …)
            continue
        if _TITLE_ID.search(s):  # info の見出し(テンプレートの題名は一覧のデータ): 「題名  [ID]」
            continue
        if s.startswith(_COMMAND_HEAD):  # コマンドの例: 説明(# の後)だけ
            s = s.split("#", 1)[1] if "#" in s else ""
        elif _OPTION.match(s):  # --help のオプションの行: 名前の後(2 つ以上の空白の後)の説明だけ
            parts = re.split(r"\s{2,}", s, maxsplit=1)
            s = parts[1] if len(parts) > 1 else ""
        words = [
            w
            for w in s.split()
            if not _MACHINE_TOKEN.search(w)
            and _EN_WORD.match(w)
            and w.strip(_WORD_PUNCT).lower() not in _KEEP_ENGLISH
            and not w.strip(_WORD_PUNCT).isupper()
        ]
        if len(words) >= 2:
            bad.append(line)
    return bad


def test_english_only_lines_finds_old_mixed_text():
    """判定の式そのものの確かめ: 0.9.2 の日本語の出力に混ざっていた英語の行を見つけ、今の日本語の行は通す。"""
    old = """PC: assumed 8.0 GiB VRAM (--vram), RAM 32.0 GiB (--ram)
Recommended: 12 of 80 (224 hidden: uncensored 26, tools 34, license unknown 28, too tight 136; 25
Order: runs, Verified, Perfect/Good, Marginal, Score. Score 0-100. VRAM: GiB needed, m = measured.
Mode: GPU all on GPU, GPU~ parts swapped in turn, GPU+RAM offload (slow), CPU (slow), - no run.
  model:    Z-Image-Turbo  (6 workflows use these weights)
  -h, --help            show this help message and exit
Right-size generation models to your PC."""
    # 以前は行ごと外れて見逃した英語の行(ID・{ } [ ] _ を含む行、先頭が種類名の行)
    missed = [
        "error: Not Found",
        "Order: Type, Status, Score (--sort).",
        "Note: image_sdxl_simple needs a license check before download",
        "image models are hidden because the list is old",
        "  Note: image_sdxl_simple needs a license check before download {--any-license}",
    ]
    assert english_only_lines("\n".join(missed)) == missed
    # 日本語の行・ID・ファイル名・オプションだけの行は通す
    ok = [
        "注意: image_sdxl_simple はライセンスの確認が要ります",
        "  - image_z_image_turbo  (gen, bf16, marginal, 6.2 GB)",
        "  diffusion_models/sub/tiny_unet.safetensors  https://example.invalid/LICENSE --any-license",
    ]
    assert english_only_lines("\n".join(ok)) == []
    assert len(english_only_lines(old)) == 6  # PC: assumed … は語が 1 つ(assumed)だけなので数えない
    new = """Type  Status      Score Model                    Use     Prec  VRAM Mem% Mode    License
image ● Marginal     54 Z-Image-Turbo            gen+5   int8  7.6m  96% GPU~    apache-2.0 yes
  genmodelfit download hf:x/y --uncensored --any-license --vram 8 --ram 32 --lang ja
  JSON (--json): { genmodelfit, command, system: {...}, database: {...}, filters: {...},
  modality, tasks, use, content, source, runtime, languages, description, fit_level, fit_label,
  --modality TYPE[,TYPE], -m TYPE[,TYPE]
    - image_z_image_turbo  (gen, bf16, marginal, 6.2 GB)
OS:       Windows 11
使い方: genmodelfit recommend [-h] [--lang {en,ja}] [--json]
                              [--no-cache] [--modality TYPE[,TYPE]]
並び: 動く → Verified → Perfect/Good → Marginal → Score。Score は 0〜100。"""
    assert english_only_lines(new) == []


JA_ONLY_CASES = [
    ["system", *HW],
    ["recommend", *HW],
    ["recommend", "-n", "2", *HW],
    ["recommend", "--vram", "0", "--ram", "16", "--no-cache"],
    ["fit", *HW],
    ["fit", "--all", "--content", "all", "--sort", "vram", *HW],
    ["search", "flux", *HW],
    ["search", "zzzz-no-such-model", *HW],
    ["types", "--no-cache"],
    ["info", "image_z_image_turbo_int8", *HW],
    ["info", "image_sdxl_simple", "--limits", *HW],
    ["info", "no-such-model-xyz", *HW],
    ["download", "image_z_image_turbo_int8", "--list", *HW],
    ["download", "image_sdxl_simple", "--list", "--budget", "1", *HW],
    ["gate", "--no-cache", "--only-held"],
    ["review", "list", "--no-cache"],
    ["contrib", "show", str(FIXTURES / "contrib" / "rtx-3070" / "2026-10-20-5bedb2da.json")],
    ["contrib", "validate", str(FIXTURES / "contrib")],
    ["recommend", "--min-fit", "good", "--fit", "good", *HW],  # 使い方の誤り
    ["recommend", "--limit", "0", *HW],  # 型の誤り(argparse の文)
    ["recommend", "--bogus", *HW],  # 知らない引数(argparse の文)
    *([*args, "--help"] for args in HELP_CASES),
]


@pytest.mark.parametrize("args", JA_ONLY_CASES, ids=lambda a: " ".join(a[:3]) or "top")
def test_ja_has_no_english_only_sentences(capsys, monkeypatch, tmp_path, args):
    """ja の出力に、日本語の無い英語の文の行が無い(#76)。列の名前・段階の名前・ID・コマンドは英語のまま。"""
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "100")
    with contextlib.suppress(SystemExit):  # --help・使い方の誤り
        cli.main([*args, "--lang", "ja"])
    out = capsys.readouterr()
    text = out.out + out.err
    for t in FREE_TEXT:  # 一覧に保存された自由記述(訳さない)
        text = text.replace(t, "")
    assert text.strip()
    assert english_only_lines(text) == [], " ".join(args)


# --- 折り返し(#76): 英数字の並びの途中で切らない・禁則・全角は 2 桁 ---------------------------


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_download_plan_keeps_file_names_whole(capsys, monkeypatch, tmp_path, lang):
    """長いファイル名を「…safetensor」と「s」に分けない(LazyMix の計画。#76)。
    LazyMix は凍らせた一覧から読む(毎週の更新で同梱の一覧から消えても飛ばさない。#197)。"""
    from frozenlist import FROZEN_DB

    model = "hf:TheImposterImposters/LazyMix-v4.0-inpainting"
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "100")
    args = ["download", model, "--list", *HW, "--db", str(FROZEN_DB), "--lang", lang]
    assert cli.main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    name = "lazymixRealAmateur_v40Inpainting.safetensors"
    text = "\n".join(lines)
    assert text.count(name) == text.count("lazymixRealAmateur") >= 2
    assert not [x for x in lines if x.rstrip().endswith("safetensor") or x.strip() in ("s", "rs")]
    # 幅を超えてよいのは、貼るためのコマンドの行と、1 つの長い並びだけの行
    wide = overlong_lines(lines, 100)
    assert all(x.strip().startswith(("genmodelfit ", "python -m genmodelfit")) for x in wide), wide


@pytest.mark.parametrize("width", [40, 60, 80, 100])
def test_wrap_text_rules(width):
    from genmodelfit.table import wrap_line

    text = (
        "  注意: ライセンスが分からないファイルがあります: some_really_long_file_name.safetensors。"
        "中身の条件を自分で確かめたうえで落とすなら --any-license(端末なら y/N の質問で尋ねます)。"
        "大きさは 3.95 GB、URL は https://huggingface.co/org/repo です「かぎ」(かっこ)"
    )
    lines = wrap_line(text, width)
    assert "".join("".join(lines).split()) == "".join(text.split())  # 空白のほかは何も足さず・落とさない
    for run in (
        "some_really_long_file_name.safetensors。",
        "--any-license",
        "https://huggingface.co/org/repo",
    ):
        assert any(run in x for x in lines), (run, lines)  # 英数字の並びを途中で切らない
    assert any("3.95 GB" in x for x in lines)  # 数と単位を分けない
    for x in lines[1:]:
        assert x.strip()[0] not in "、。,.)」』", x  # 行の頭に置かない文字
    for x in lines[:-1]:
        assert x.rstrip()[-1] not in "(「『", x  # 行の終わりに置かない文字
    assert not overlong_lines(lines, width)  # 全角は 2 桁。幅を超えるのは 1 つの長い ASCII の並びだけの行


def test_wrap_text_long_run_stays_whole_on_its_own_line():
    from genmodelfit.table import wrap_line

    url = "https://huggingface.co/" + "x" * 120
    lines = wrap_line(f"  確かめる場所: {url} を見る", 60)
    assert f"      {url}" in lines or url in [x.strip() for x in lines]
    assert all(url not in x or x.strip() == url for x in lines)


def test_wrap_text_english_breaks_like_before():
    """英語は前と同じ位置で折り返す(後ろに続く空白の分も幅に入れる)。"""
    from genmodelfit.table import wrap_line

    words = "Recommended: 10 of 37 (273 hidden: other types 166, uncensored 21, tools 19, license unknown 12,"
    text = words + " too tight 55; 19 more workflows merged into their models)"
    assert wrap_line(text, 100) == [words, "    too tight 55; 19 more workflows merged into their models)"]


def test_ja_and_en_json_differ_only_in_text(capsys):
    """言語で変わるのは文だけ: ID・段階・点数・並びは同じ。"""
    args = ["recommend", "--content", "all", "--json", *HW]
    cli.main([*args, "--lang", "ja"])
    ja = json.loads(capsys.readouterr().out)
    cli.main([*args, "--lang", "en"])
    en = json.loads(capsys.readouterr().out)
    assert [(m["id"], m["fit_level"], m["score"], m["run_mode"]) for m in ja["models"]] == [
        (m["id"], m["fit_level"], m["score"], m["run_mode"]) for m in en["models"]
    ]
    assert set(ja) == set(en)
    assert [sorted(m) for m in ja["models"]] == [sorted(m) for m in en["models"]]


# --- 説明は両方の言語で持つ -------------------------------------------------------


def _descriptions():
    data = Path(gmf.__file__).parent / "data"
    for m in json.loads((data / "db.json").read_text(encoding="utf-8"))["models"]:
        yield "db.json", m["id"], m.get("description")
    for m in json.loads((data / "manual.json").read_text(encoding="utf-8"))["models"]:
        yield "manual.json", m["id"], m.get("description")
    for mid, m in json.loads((data / "overrides.json").read_text(encoding="utf-8"))["models"].items():
        yield "overrides.json", mid, m.get("description")


def test_every_description_has_both_languages():
    """description を持つ項目は ja と en の両方が空でない(片方の言語の利用者だけ欠けないように。#73)。"""
    checked = 0
    for where, mid, d in _descriptions():
        if not d:  # 説明なし(None・空の {})
            continue
        checked += 1
        assert isinstance(d, dict), (where, mid)
        assert (d.get("ja") or "").strip() and (d.get("en") or "").strip(), (where, mid, d)
        assert JA.search(d["ja"]) and not JA.search(d["en"]), (where, mid)
    assert checked > 0


def test_overlong_lines_helper_rejects_long_japanese():
    """幅を超えてよいのは ASCII の 1 語だけの行。空白の無い長い日本語の行は許さない(#76)。"""
    assert overlong_lines(["あ" * 60], 100)
    assert overlong_lines(["  - " + "あ" * 60], 100)
    assert overlong_lines(["x" * 120 + "あ"], 100)
    assert overlong_lines(["x" * 60 + " " + "y" * 60], 100)
    assert not overlong_lines(["  - " + "x" * 120], 100)
    assert not overlong_lines(["      " + "x" * 120 + "。"], 100)
    assert not overlong_lines(["あ" * 50], 100)  # 50 字 = 100 桁は収まる


def test_wrap_text_exact_fit_and_angle_placeholder():
    """幅ちょうどの行は次へ送らない(前の版と同じ)。< > で囲んだ日本語の印は切らない(#76)。"""
    from genmodelfit.table import wrap_line, wrap_text

    path = "C:\\" + "x" * 44  # 8 + 4 + 48 = 60 桁
    text = "  to:        " + path + "  (models folder (ComfyUI layout): <folder>/<file>)"
    lines = wrap_line(text, 60)
    assert lines[0] == "  to:        " + path and cell_len(lines[0]) == 60, lines
    mark = "<フォルダ>/<GPU>/<日付>-<sha8>.json"
    for width in (40, 60, 80):
        out = wrap_text("フォルダなら " + mark + "(フォークの data/contrib を渡す)", width)
        assert any(mark in x for x in out), (width, out)


def test_argparse_language_nesting_restores():
    """main を重ねて(同時に)呼んでも、最後に出る呼び出しが argparse を元に戻す。"""
    import argparse

    orig = argparse._
    with cli._argparse_language():
        patched = argparse._
        assert patched is not orig
        with cli._argparse_language():
            assert argparse._ is patched
        assert argparse._ is patched  # 内側が出ても、外側が残っている間は戻さない
    assert argparse._ is orig
