# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# ruff: noqa: E501  (前置きの文章は表の 1 行が長い)

"""全コマンド・全オプションの一覧(docs/commands.md・docs/commands.ja.md)を、パーサから作る(#111)。

`genmodelfit.cli.build_parser()` を言語ごとに(CLI の --lang と同じ i18n.forced + argparse の文の差し替えで)
作り直し、サブコマンドを数えて Markdown にする。コマンドの説明(--help の文)は節の名前(PRECONDITIONS: など)で
分け、箇条書き・表・コードの囲みにする。JSON の鍵の並びは docs/schema.md の節へのリンクに置き換える。
コードと文書がずれないよう、tests/test_commands_doc.py が「いま作った出力 = 置いてあるファイル」を検査する。

    python tools/cli_reference.py          # 2 つのファイルを書く
    python tools/cli_reference.py --check  # 違えば 1 で終わる(何も書かない)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TARGETS = {"en": DOCS / "commands.md", "ja": DOCS / "commands.ja.md"}

# 手で書く前置き。{...} の印は作った本文・コードの値に置き換える。
# 環境変数の表は、読んでいる場所(src の中の名前)を持つ。tests/test_commands_doc.py が src の GENMODELFIT_* と照らす
INTRO = {
    "en": """# Command reference

**English** · [日本語](commands.ja.md)

Every command and option of `genmodelfit` on one page: what each command does, its options, what it reads and
writes, and its exit codes. The same text is printed by `genmodelfit <command> --help`.

## Common tasks

| What you want | Command | More |
|---|---|---|
| Install | `pipx install genmodelfit` (or `uv tool install genmodelfit` / `pip install genmodelfit`); to try without installing, `uvx genmodelfit recommend` | [README, "Install"](../README.md#install) |
| `genmodelfit` is "not found" after installing | pipx / uv tool: run `pipx ensurepath` / `uv tool update-shell` once and open a new terminal; pip: `python -m genmodelfit recommend` with the Python you installed it into | [below](#if-genmodelfit-is-not-found) |
| See which models run on this PC | `genmodelfit recommend` | [README, "Quick start"](../README.md#quick-start) |
| Find a model of one type (music here) | `genmodelfit recommend -m music` (or `genmodelfit search <word>`) | [`recommend`](#genmodelfit-recommend), [`types`](#genmodelfit-types) |
| See everything that runs, in one table | `genmodelfit fit` | [`fit`](#genmodelfit-fit) |
| Check one model (files, licenses, estimate) | `genmodelfit info "Z-Image-Turbo"` | [`info`](#genmodelfit-info) |
| Put a model into ComfyUI (first time) | `genmodelfit download "Z-Image-Turbo" --list`, then the same without `--list` | [download.md](download.md) |
| Change where downloads go | `genmodelfit config set comfyui-dir D:/ComfyUI` (once only: `--comfyui-dir D:/ComfyUI`) | [download.md, "genmodelfit config"](download.md#genmodelfit-config) |
| ComfyUI is not found (`download` / `measure`) | add `--comfyui-dir D:/ComfyUI` (or set it once with `genmodelfit config set comfyui-dir D:/ComfyUI`) | [download.md, "Choosing the place"](download.md#choosing-the-place) |
| Get the latest model list | `genmodelfit update --from-release` | [`update`](#genmodelfit-update) |
| Judge another PC | `genmodelfit recommend --vram 12 --ram 32` | [README, "Judge another PC"](../README.md#judge-another-pc) |
| Use it from a script or an app | add `--json`; to download also `--yes` and the place (`--comfyui-dir`, or `genmodelfit config` / `GENMODELFIT_COMFYUI_DIR`): without a decided place nothing is downloaded (exit code 1) | [schema.md](schema.md) |
| Switch the language | `genmodelfit config set lang ja` (once only: `--lang ja`) | [Display language](#display-language) |
| Measure models and send the results | `genmodelfit share` (it finds ComfyUI, starts it if needed, measures for a few minutes and opens a pull request) | [CONTRIBUTING.md, "The easy way"](../CONTRIBUTING.md#the-easy-way-no-git-needed) |

## Terms

| Term | Meaning |
|---|---|
| fit level (`Status`) | How much of the memory a model needs on this PC (VRAM on the GPU, RAM on the CPU): `perfect` up to {perfect}%, `good` up to {good}%, `marginal` up to {marginal}%, `too_tight` above {marginal}% (does not run), `unknown` / `?` not judged (the size is not known). Some run modes cap the level: [README, "Fit levels"](../README.md#fit-levels). |
| runnable | `perfect`, `good` or `marginal` (what `fit` shows by default). |
| offload | Keeping part of the weights in RAM instead of VRAM and moving them over while running (`GPU+RAM`). It lets a model run on a smaller GPU, but slowly. "Without offloading" means `GPU` or `GPU~`. |
| run mode (`Mode`) | `GPU` everything on the GPU; `GPU~` the parts are moved onto the GPU one after another; `GPU+RAM` part of the weights is offloaded to RAM (slow); `CPU` (slow); `-` does not run. [README, "Columns"](../README.md#columns) |
| Score | 0–100, the memory headroom (100 up to 70% use). Not speed or quality. |
| model / workflow | A workflow is one ComfyUI template (or Hugging Face entry) with an id such as `image_z_image_turbo_int8`. A model is the weights (family + size) that several workflows share; they differ only in precision or use. Tables show one row per model (`--all-workflows`: one per workflow). `info` and `download` accept either. |
| use (`--task`) | What a workflow does inside its type: `gen`, `edit`, `i2v`, ... (`genmodelfit types` lists them). |
| the list (`db.json`) | The model list that genmodelfit judges. A copy comes with the package; `update --from-release` stores a newer signed one in the cache, which is then used instead (`--no-cache`: the bundled one; `--db PATH`: a file). |
| HEAD check | An anonymous HTTP HEAD request to each download URL, recorded in the list. Checks older than {head_days} days (`--max-head-age-days`) count as expired: those models are held until you run `genmodelfit update --from-release`. `system` shows the date. |
| Verified / auto / held | Verified (`auto` in JSON) means the model passed every check of the gate (licenses on the allowlist, official workflow, fresh HEAD checks, sha256 known, `sfw`, reviewed, ...), so an app may recommend it without asking a person. The others are held (`held_reasons`). [README, "Verified"](../README.md#verified-auto-recommendation), [gate.md](gate.md) |
| content (`sfw` / `uncensored`) | `uncensored` marks models without a safety filter or meant for adult output. They are hidden unless you pass `--uncensored` (both kinds) or `--content uncensored` (only those). [README, "Unknown-license and uncensored models are hidden by default"](../README.md#unknown-license-and-uncensored-models-are-hidden-by-default) |
| GB | Always GiB (1024³ bytes), also in `--vram`, `--ram` and `--budget`. |

## How to read this page

- Each command section has its purpose, the usage line, what it does, its own options, the options it shares with
  other commands (listed once in [Options shared by commands](#options-shared-by-commands)), what it needs, what it
  reads and writes, its exit codes and examples.
- In the usage line, `[...]` is optional, `A | B` means one of them, and `...` means it can be repeated.
- "Value" is what follows the option (`--vram GB`); an option without a value is a switch. "Default" is what is used
  when the option is left out.
- `-h` / `--help` and `--lang` work with every command and are listed only once (below).
- An option may be shortened to any unambiguous prefix (`--mod` for `--modality`), except the consent options
  `--uncensored`, `--any-license`, `--include-unknown-license` and `--content`, which must be written in full
  (`recommend --unc` is a usage error, exit code 2).
- Some options have the same name but do different things in different commands:
  - `--precision`: in `recommend`, `fit`, `search` and `info` the estimate is converted to that precision; in
    `download` it picks which workflow (which files) to download.
  - `--content`: in the listing commands it filters what is shown; in `download` it decides which models the name
    search looks at, and `uncensored` / `all` also count as consent to download uncensored models.
    `--uncensored` is the same as `--content all`; together with `--content sfw` or `--content uncensored` it is a
    usage error (exit code 2).
  - `--all`: everything in `fit` / `search`; the maintenance view in `diff`; approved entries too in `review list`;
    measurements already sent too in `share` (sent again) / `contrib share` (listed).
  - `--limit` / `-n`: rows in `recommend` / `fit` / `search`; lines per section in `diff`; suggestions listed in
    `measure`.
  - `--output` means different things in `update`, `bench` and `contrib`, and `--force` in `update` and `bench`
    (see each command).
- With `--json` every command prints JSON for programs; the shapes are in [schema.md](schema.md).

## Global options

| Option | Value | Description |
|---|---|---|
{global_rows}

## Options shared by commands

These options mean the same in every command that takes them. The command sections list only their own options and
link here.

| Option | Value | Default | Description | Commands |
|---|---|---|---|---|
{shared_rows}

## Preconditions of the commands that judge this PC

The preconditions of `system`, `recommend`, `fit`, `search` and `info`:

{common_pre}

## If `genmodelfit` is not found

- pipx and uv tool put `genmodelfit` on PATH after a one-time `pipx ensurepath` / `uv tool update-shell`; then open a
  new terminal.
- After `pip install`, run `python -m genmodelfit …` with the same Python you installed it into (on Windows also
  `py -m genmodelfit …`). This does not reach pipx or uv tool installs, which have their own environments.
- pip may print *"WARNING: The script genmodelfit.exe is installed in '…\\Scripts' which is not on PATH"*. This is
  common with python.org Python installed without "Add python.exe to PATH", Microsoft Store Python, a user install
  (`--user`), or a venv that is not activated. Add that folder to PATH, or use `python -m genmodelfit`.
- A terminal opened before PATH changed does not see the change: open a new one.

## Table columns

The columns of `recommend`, `fit` and `search`:

| Column | Meaning |
|---|---|
| `Type` | image, video, music, sfx, tts, 3d |
| `Status` | the fit level ([Terms](#terms)) |
| `Score` | 0–100: memory headroom. 100 while `Mem%` is at most 70%, lower above; not speed, quality or popularity ([scoring.md](scoring.md#score)) |
| `Censored` | `Y` censored / `N` uncensored; only with `--uncensored` (or `--content all` / `uncensored`) |
| `Model` | one row per model (same weights); long names are cut in the middle with `…` |
| `Use` | `gen`, `edit`, `i2v`, … (`genmodelfit types`); the row's own use (the one matching `--task`) first, then the other uses of the model's workflows, without repeats (`gen,edit`; `gen,…` when they do not fit). The workflows: `info`; one row each: `--all-workflows` |
| `Prec` | the best precision that fits (fp16, bf16, fp8, int8, …) |
| `VRAM` | GiB needed in that mode; `m` = measured (by the maintainer, on this PC, or the median of other users' measurements of the same GPU; number of sources: `info`, JSON `sample_count`). When the decode is split into tiles (ACE-Step 1.5 and others), the tiled estimate (it can go down as the length goes up; [accuracy.md "Tiles"](accuracy.md#tiles)) |
| `Mem%` | share of the tightest memory used: VRAM on the GPU, RAM on CPU, the larger of the two for `GPU+RAM`; `RAM` = system RAM is short |
| `Mode` | `GPU` all on GPU, `GPU~` parts swapped in turn, `GPU+RAM` offload (slow), `CPU` (slow), `-` does not run (`GPU req`: needs a GPU, none usable here; `RAM`: not enough system RAM) |
| `Disk` | download size (GiB) |
| `License` | license of the files (`other` = read it yourself) |

`?` = unknown (precision, license, size; not judged on it). `-` = not applicable (does not run, no VRAM on CPU, …).
When the terminal is narrow, columns are dropped in the order given under [Display language](#display-language).

## Display language

Output is in English by default. For Japanese, use one of these:

- `--lang ja` on any command (e.g. `genmodelfit recommend --lang ja`)
- the environment variable `GENMODELFIT_LANG=ja`
- `genmodelfit config set lang ja` (saved; command line only)
- `gmf.set_language("ja")` in Python

The flag wins, then the variable, then the saved setting (command line) or `set_language` (Python). The OS language
is not used. Only the texts change: reasons, notes, errors, table headings and footers, and `--help`. JSON keys, ids,
codes and judgments are the same in both languages. English output contains no Japanese, and Japanese output
contains no English-only sentences (one language per line).

Kept in English in both languages: the table column names (`Type`, `Status`, `Score`, `Model`, `Use`, `Prec`, `VRAM`,
`Mem%`, `Mode`, `Disk`, `License`, `Censored`), the fit level names (`Perfect`, `Good`, `Marginal`, `Too tight`),
the run-mode marks (`GPU`, `GPU~`, `GPU+RAM`, `CPU`), units, and machine-readable values such as ids, license ids and
option names. This keeps the table widths stable and matches what you type for `--fit`, `--sort` and so on; the
Japanese output explains them in Japanese below the table. Type headings show the Japanese type name followed by
the name for `-m` (e.g. `(image)`).

Long lines are wrapped counting East Asian wide characters as two columns. File names, URLs, options and numbers with
units are never split (a single one longer than the width stays whole on its own line), and the basic Japanese
line-breaking rules apply (no line starts with a closing mark or comma, or ends with an opening bracket). When the
terminal is narrow, the table drops columns in this order: `Disk`, `Mem%`, `License`, `Prec`, `Use` (see them with
`info` or `--json`).

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `GENMODELFIT_LANG` | every command | Display language, `en` or `ja`. Beaten only by `--lang`. Values like `ja_JP.UTF-8` are accepted. |
| `GENMODELFIT_COMFYUI_DIR` | `download` | The ComfyUI folder to put files in. Beaten by `--comfyui-dir` / `--output-dir`; beats `genmodelfit config` and the automatic search. |
| `GENMODELFIT_COMFYUI_SEARCH` | `download`, `measure`, `share` | `0`, `off`, `no` or `false` turns the automatic search for ComfyUI off (for apps, scripts and tests). |
| `GENMODELFIT_COMFYUI_START` | `measure`, `share` | `0`, `off`, `no` or `false`: do not start the ComfyUI that was found when none is running (like `--no-start`). |
| `GENMODELFIT_CONFIG_DIR` | `config`, and every command that reads the saved settings | The folder of the config file `config.json` (default: see [Where files are kept](#where-files-are-kept)). |
| `GENMODELFIT_CACHE_DIR` | `update`, `measure`, `share`, `bench`, `contrib`, and every command that reads the cached list | The cache folder (default: see [Where files are kept](#where-files-are-kept)). |
| `GENMODELFIT_SIGNING_KEY` | `python -m genmodelfit.signing sign` only, not the `genmodelfit` command | The private key (PEM) that signs a data release. For the maintainer ([release.md](release.md)). |
| `GITHUB_TOKEN`, `GH_TOKEN` | `share` | A GitHub token used as it is to send the pull request (it needs the `public_repo` scope). Beats the saved sign-in and the device login. |
| `GENMODELFIT_GH_CLIENT_ID` | `share` | The client id of the GitHub OAuth app used for the device login. Empty turns the device login off (only `GITHUB_TOKEN` / `GH_TOKEN`). |

Network access also follows the usual proxy and certificate variables (`HTTPS_PROXY`, `REQUESTS_CA_BUNDLE`, ...);
`~/.netrc` credentials are never sent.

## Settings that can be saved (`genmodelfit config`)

`genmodelfit config set KEY VALUE` saves a setting in `config.json` in the config folder. Only the command line reads
it; the Python functions never do. The keys are `comfyui-dir` (a folder path) and `lang` (`en` or `ja`).
Where several places give the same setting, the first of these wins:

1. the command-line option (`--lang`, `--comfyui-dir`, `--output-dir`)
2. the environment variable (`GENMODELFIT_LANG`, `GENMODELFIT_COMFYUI_DIR`)
3. the saved setting (`genmodelfit config`)
4. the default (`lang`: `en`; the ComfyUI folder: found automatically when exactly one is found)

`genmodelfit config` shows each setting and which of these it came from. Details: [download.md](download.md).

## Where files are kept

| | Windows | macOS | Linux |
|---|---|---|---|
| Config folder (`config.json`) | `%LOCALAPPDATA%\\genmodelfit` | `~/Library/Application Support/genmodelfit` | `~/.config/genmodelfit` (or `$XDG_CONFIG_HOME/genmodelfit`) |
| Cache folder | `%LOCALAPPDATA%\\genmodelfit\\Cache` | `~/Library/Caches/genmodelfit` | `~/.cache/genmodelfit` (or `$XDG_CACHE_HOME/genmodelfit`) |

`genmodelfit config path` prints the config file on your PC. `GENMODELFIT_CONFIG_DIR` and `GENMODELFIT_CACHE_DIR`
move the folders. The config folder also holds the GitHub sign-in of `share` (`github_token`, readable only by you;
`genmodelfit share --logout` removes it). The cache holds the list from `update` (`db.json`), the results of the HEAD and file-header checks,
your `measure` and `bench` results (`measurements/`, also used when this PC is judged), submission drafts
and failure records (`submissions/`, what `share` sends), the marks of what `share` has sent
(`submissions_shared.json`), the output of a ComfyUI that `measure` / `share` started (`comfyui-<date>.log`) and,
while measuring, `measure.lock` (only one `measure` / `share` measures at a time).
Deleting the cache folder is safe: the bundled list is used again (your saved results go with it).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. Running `genmodelfit` with no arguments prints the usage and exits 0. |
| 1 | Failure: no such model, a refusal, a declined question, a network error, an unreadable file, or a check that failed. Each command section says what counts as a failure. |
| 2 | Usage error: an unknown option, a wrong value, a missing argument, an abbreviated consent option, or options that cannot be used together. |
| 3 | Only `contrib aggregate --report --fail-on-breach`: an error exceeded the threshold. |
| 130 | Interrupted with Ctrl+C. `download` returns 130 on every OS and keeps the partly downloaded `.part` files; run the same command again to resume. Other commands stop the way Python does: 130 on Linux and macOS, 3221225786 (0xC000013A; PowerShell's `$LASTEXITCODE` shows -1073741510) on Windows. `bench` and `measure` first cancel their job (and stop a ComfyUI that genmodelfit started) and exit 1; `share` stops measuring, goes on to sending what was measured and then exits 130; `measure` and `share` return 130 when Ctrl+C is pressed at their questions. |

With `--json`, a failure still exits 1 and the message is in the `error` field of the JSON.

## Commands

{toc}

{sections}

## About this page

The command sections are generated from the program's argument parser by `tools/cli_reference.py`, so they always
match the program. Do not edit them by hand; change the help texts in `src/genmodelfit/cli.py` and run
`python tools/cli_reference.py` (`tests/test_commands_doc.py` fails when this page is out of date).

The environment variables are read in `src/genmodelfit/i18n.py` (`GENMODELFIT_LANG`), `cli.py` (`GENMODELFIT_COMFYUI_DIR`), `comfyfind.py` (`GENMODELFIT_COMFYUI_SEARCH`), `comfystart.py` (`GENMODELFIT_COMFYUI_START`), `config.py`
(`GENMODELFIT_CONFIG_DIR`), `db.py` (`GENMODELFIT_CACHE_DIR`), `signing.py` (`GENMODELFIT_SIGNING_KEY`) and `github.py`
(`GITHUB_TOKEN`, `GH_TOKEN`, `GENMODELFIT_GH_CLIENT_ID`).
""",
    "ja": """# コマンド一覧

[English](commands.md) · **日本語**

`genmodelfit` のすべてのコマンドとオプションを 1 ページにまとめたものです。コマンドごとに、何をするか・オプション・
読むものと書くもの・終了コードがあります。同じ文は `genmodelfit <コマンド> --help` でも出ます。

## よくある使い方

| したいこと | コマンド | 詳しく |
|---|---|---|
| 入れる | `pipx install genmodelfit`(か `uv tool install genmodelfit` / `pip install genmodelfit`)。入れずに試すなら `uvx genmodelfit recommend` | [README「入れ方」](../README.ja.md#入れ方) |
| 入れたのに `genmodelfit` が「見つからない」 | pipx・uv tool なら `pipx ensurepath`・`uv tool update-shell` を 1 回実行して端末を開き直す。pip なら、入れたのと同じ Python で `python -m genmodelfit recommend` | [下](#genmodelfit-が見つからないとき) |
| この PC で動くモデルを知る | `genmodelfit recommend` | [README「すぐ試す」](../README.ja.md#すぐ試す) |
| 1 つの種類のモデルを探す(例は音楽) | `genmodelfit recommend -m music`(か `genmodelfit search <語>`) | [`recommend`](#genmodelfit-recommend)・[`types`](#genmodelfit-types) |
| 動くものを全部 1 つの表で見る | `genmodelfit fit` | [`fit`](#genmodelfit-fit) |
| 1 つのモデルを調べる(ファイル・ライセンス・見積もり) | `genmodelfit info "Z-Image-Turbo"` | [`info`](#genmodelfit-info) |
| モデルを ComfyUI に入れる(初めて) | `genmodelfit download "Z-Image-Turbo" --list` で確かめ、`--list` を外して実行 | [download.ja.md](download.ja.md) |
| 落とす場所を変える | `genmodelfit config set comfyui-dir D:/ComfyUI`(1 回だけなら `--comfyui-dir D:/ComfyUI`) | [download.ja.md「genmodelfit config」](download.ja.md#genmodelfit-config) |
| ComfyUI が見つからない(`download`・`measure`) | `--comfyui-dir D:/ComfyUI` を付ける(か `genmodelfit config set comfyui-dir D:/ComfyUI` で一度決める) | [download.ja.md「置き場所の決め方」](download.ja.md#置き場所の決め方) |
| 最新の一覧を取る | `genmodelfit update --from-release` | [`update`](#genmodelfit-update) |
| ほかの PC として判定する | `genmodelfit recommend --vram 12 --ram 32` | [README「ほかの PC で判定する」](../README.ja.md#ほかの-pc-で判定する) |
| スクリプト・アプリから使う | `--json` を付ける。落とすときは `--yes` と置き場所(`--comfyui-dir`、か `genmodelfit config`・`GENMODELFIT_COMFYUI_DIR`)も要る。置き場所が決まらないと落とさない(終了コード 1) | [schema.ja.md](schema.ja.md) |
| 表示の言語を変える | `genmodelfit config set lang ja`(1 回だけなら `--lang ja`) | [表示の言語](#表示の言語) |
| モデルを実測して送る | `genmodelfit share`(ComfyUI を見つけ、無ければ起動し、数分測って PR を開く) | [CONTRIBUTING.ja.md「かんたんな送り方」](../CONTRIBUTING.ja.md#かんたんな送り方git-は要らない) |

## 用語

| 用語 | 意味 |
|---|---|
| 段階(`Status`) | モデルがこの PC のメモリ(GPU なら VRAM、CPU ならメモリ)をどれだけ使うか: `perfect` は {perfect}% まで、`good` は {good}% まで、`marginal` は {marginal}% まで、`too_tight` は {marginal}% を超える(動かない)、`unknown` / `?` は判定していない(大きさが分からない)。動かし方による上限もある: [README「段階」](../README.ja.md#段階)。 |
| runnable | `perfect`・`good`・`marginal` のどれか(`fit` が既定で出すもの)。 |
| 逃がす(offload) | 重みの一部を VRAM ではなくメモリに置き、動かしながら GPU へ移すこと(`GPU+RAM`)。小さい GPU でも動くが遅い。「逃がさずに」は `GPU` か `GPU~` のこと。 |
| 動かし方(`Mode`) | `GPU` 全部 GPU に載せる、`GPU~` 部品を順に GPU へ載せ替える、`GPU+RAM` 重みの一部をメモリへ逃がす(遅い)、`CPU`(遅い)、`-` 動かない。[README「列」](../README.ja.md#列) |
| Score | 0〜100 のメモリの余裕(使用率 70% までは 100)。速さや品質ではない。 |
| モデル / ワークフロー | ワークフローは ComfyUI のテンプレート(か Hugging Face の項目)1 つで、`image_z_image_turbo_int8` のような ID を持つ。モデルは、いくつかのワークフローが共有する重み(系統 + 大きさ)で、ワークフローどうしは精度か使い方だけが違う。表はモデルごとに 1 行(`--all-workflows` ならワークフローごと)。`info` と `download` はどちらでも受ける。 |
| 用途(`--task`) | 種類の中でワークフローがすること: `gen`・`edit`・`i2v` など(`genmodelfit types` が並べる)。 |
| 一覧(`db.json`) | genmodelfit が判定するモデルの一覧。パッケージに 1 つ入っていて、`update --from-release` は署名つきの新しい一覧をキャッシュに置き、それからはそちらを使う(`--no-cache` で同梱、`--db PATH` でファイル)。 |
| HEAD の確認 | ダウンロードの URL のそれぞれに、名乗らずに HTTP の HEAD を送った結果(一覧に記録してある)。{head_days} 日(`--max-head-age-days`)より古い確認は期限切れで、そのモデルは `genmodelfit update --from-release` を実行するまで保留になる。日付は `system` で分かる。 |
| Verified / auto / 保留 | Verified(JSON では `auto`)は、gate の条件(ライセンスが許可リストにある・公式のワークフロー・HEAD の確認が新しい・sha256 が分かる・`sfw`・確認済み など)をすべて満たしたもので、アプリが人に尋ねずにおすすめに出してよい。満たさないものは保留(`held_reasons`)。[README「Verified」](../README.ja.md#verified自動でおすすめに出す条件)・[gate.ja.md](gate.ja.md) |
| 内容(`sfw` / `uncensored`) | `uncensored` は、安全フィルタを外したモデルや成人向けの出力を想定したモデルの印。`--uncensored`(両方)か `--content uncensored`(それだけ)を付けたときだけ出す。[README「ライセンス不明・無検閲のモデルは、既定で出しません」](../README.ja.md#ライセンス不明無検閲のモデルは既定で出しません) |
| GB | いつも GiB(1024³ バイト)。`--vram`・`--ram`・`--budget` も同じ。 |

## このページの読み方

- コマンドの節には、目的・使い方の行・何をするか・そのコマンドだけのオプション・ほかのコマンドと共通のオプション
  ([共通のオプション](#共通のオプション)に 1 回だけ載せる)・前提・読むものと書くもの・終了コード・例があります。
- 使い方の行の `[...]` は省けること、`A | B` はどちらか 1 つ、`...` は繰り返せることを表します。
- 「値」はオプションの後ろに書くもの(`--vram GB`)で、値の無いオプションは付けるだけのスイッチです。「既定」は、
  そのオプションを省いたときに使うものです。
- `-h` / `--help` と `--lang` はどのコマンドでも使えるので、下に 1 回だけ載せます。
- オプションは紛れない長さに略せます(`--modality` を `--mod`)。ただし同意のオプション `--uncensored`・
  `--any-license`・`--include-unknown-license`・`--content` は略さずに書きます(`recommend --unc` は使い方の誤りで、
  終了コード 2)。
- 同じ名前でもコマンドによって働きが違うオプションがあります。
  - `--precision`: `recommend`・`fit`・`search`・`info` では見積もりをその精度に換算する。`download` では落とす
    ワークフロー(ファイル)を選ぶ。
  - `--content`: 一覧を出すコマンドでは表示を絞る。`download` では名前で探すモデルを決め、`uncensored` / `all` は
    無検閲のモデルを落とす同意にもなる。`--uncensored` は `--content all` と同じで、`--content sfw` か
    `--content uncensored` と一緒に使うと使い方の誤り(終了コード 2)。
  - `--all`: `fit` / `search` では全部を出す。`diff` では保守用の内容を足す。`review list` では確認済みも出す。
    `share` では送ったことにした実測も送り直す、`contrib share` では一覧に出す。
  - `--limit` / `-n`: `recommend` / `fit` / `search` では行の数、`diff` では節ごとの行の数、`measure` では出す提案の数。
  - `--output` は `update`・`bench`・`contrib` で、`--force` は `update`・`bench` で、それぞれ意味が違う(各コマンドを見る)。
- `--json` を付けると、どのコマンドもプログラム向けの JSON を出します。形は [schema.ja.md](schema.ja.md) にあります。

## 全体のオプション

| オプション | 値 | 説明 |
|---|---|---|
{global_rows}

## 共通のオプション

次のオプションは、使えるどのコマンドでも同じ意味です。コマンドの節には、そのコマンドだけのオプションを載せ、
ここへリンクします。

| オプション | 値 | 既定 | 説明 | コマンド |
|---|---|---|---|---|
{shared_rows}

## この PC を判定するコマンドの前提

`system`・`recommend`・`fit`・`search`・`info` の前提は次のとおりです。

{common_pre}

## `genmodelfit` が見つからないとき

- pipx と uv tool は、1 回だけ `pipx ensurepath`・`uv tool update-shell` を実行すると `genmodelfit` を PATH に
  通します。そのあと端末を開き直します。
- `pip install` で入れたなら、入れたのと同じ Python で `python -m genmodelfit …` を実行します(Windows では
  `py -m genmodelfit …` も可)。pipx と uv tool は専用の環境に入れるので、この方法では動きません。
- pip が *「WARNING: The script genmodelfit.exe is installed in '…\\Scripts' which is not on PATH」* と出すことが
  あります。python.org の Python を「Add python.exe to PATH」なしで入れた、Microsoft Store の Python、ユーザー単位で
  入れた(`--user`)、仮想環境を有効にしていない、のどれかでよく出ます。その場所を PATH に足すか、
  `python -m genmodelfit` を使います。
- PATH を変える前に開いた端末には反映されないので、新しい端末を開きます。

## 表の列

`recommend`・`fit`・`search` の表の列:

| 列 | 意味 |
|---|---|
| `Type` | image・video・music・sfx・tts・3d |
| `Status` | 段階([用語](#用語)) |
| `Score` | 0〜100。メモリの余裕。`Mem%` が 70% 以下なら 100、超えると下がる。速さ・品質・人気は入れない([scoring.ja.md](scoring.ja.md#score)) |
| `Censored` | `Y` 検閲あり / `N` 無検閲。`--uncensored`(か `--content all` / `uncensored`)のときだけ出る |
| `Model` | 同じ重みを 1 行にまとめたモデル。長い名前は真ん中を `…` で詰める |
| `Use` | `gen`・`edit`・`i2v` など(`genmodelfit types`)。行自身の用途(`--task` で合ったもの)を先頭に、そのモデルのほかのワークフローの用途を重複なく並べる(`gen,edit`。収まらなければ `gen,…`)。ワークフローの一覧は `info`、別々の行は `--all-workflows` |
| `Prec` | 載る中で一番良い精度(fp16・bf16・fp8・int8 など) |
| `VRAM` | その動かし方で要る GiB。`m` = 実測(メンテナ・この PC の実測、または同じ GPU のほかの利用者の実測の中央値。出どころの数は `info`・JSON の `sample_count`)。ACE-Step 1.5 などで仕上げをタイルに分けるときは、タイルの見積もりの値(長さを延ばすと減ることがある。[accuracy.ja.md「タイル」](accuracy.ja.md#タイル)) |
| `Mem%` | 一番きついメモリの使用率。GPU なら VRAM、CPU ならメモリ、`GPU+RAM` は大きいほう。`RAM` = PC のメモリが足りない |
| `Mode` | `GPU` 全部 GPU、`GPU~` 部品を順に載せ替え、`GPU+RAM` メモリへ逃がす(遅い)、`CPU`(遅い)、`-` 動かない(`GPU req` は使える GPU が無いので動かない、`RAM` はメモリ不足) |
| `Disk` | ダウンロードの大きさ(GiB) |
| `License` | ファイルのライセンス(`other` = 自分で原文を読む) |

`?` = 分からない(精度・ライセンス・大きさ。判定に使っていない)。`-` = 当てはまらない(動かない・CPU なので VRAM が
無い など)。端末が狭いときに外す列の順は、[表示の言語](#表示の言語)にあります。

## 表示の言語

出力の既定は英語です。日本語にするには、次のどれかを使います。

- どのコマンドでも `--lang ja` を付ける(例 `genmodelfit recommend --lang ja`)
- 環境変数 `GENMODELFIT_LANG=ja` を置く
- `genmodelfit config set lang ja` で保存する(コマンドラインだけ)
- Python で `gmf.set_language("ja")` を呼ぶ

優先は `--lang`、環境変数、保存した設定(コマンドライン)か `set_language`(Python)の順です。OS の言語は見ません。
変わるのは文だけです: 理由・注意・エラー・表の見出しと説明・`--help`。JSON のキー・ID・コード・判定は、どちらの
言語でも同じです。英語の出力には日本語が出ません。日本語の出力には英語だけの文が出ません(1 行に 2 つの言語の文を
並べない)。

どちらの言語でも英語のまま出すもの: 表の列の名前(`Type`・`Status`・`Score`・`Model`・`Use`・`Prec`・`VRAM`・`Mem%`・
`Mode`・`Disk`・`License`・`Censored`)、段階の名前(`Perfect`・`Good`・`Marginal`・`Too tight`)、動かし方の印
(`GPU`・`GPU~`・`GPU+RAM`・`CPU`)、単位、ID・ライセンスの ID・オプションの名前など機械が読む値。表の幅をそろえ、
`--fit`・`--sort` などに書く名前と同じにするためです。日本語の出力では、表の下の説明がこれらを日本語で説明します。
種類の見出しは `== 画像 (image): …` のように、`-m` に書く名前も出します。

長い行は、全角を 2 桁で数えて折り返します。ファイル名・URL・オプション・単位つきの数の途中では切りません(1 つで
幅より長ければ、その行だけはみ出す)。行の頭に「、。)」を、行の終わりに「(「」を置きません。端末が狭いときは、
表の列を `Disk`・`Mem%`・`License`・`Prec`・`Use` の順に外します(外した列は `info` か `--json` で見る)。

## 環境変数

| 変数 | 使うところ | 内容 |
|---|---|---|
| `GENMODELFIT_LANG` | すべてのコマンド | 表示の言語。`en` か `ja`。`ja_JP.UTF-8` のような書き方も受ける。これに勝つのは `--lang` だけ。 |
| `GENMODELFIT_COMFYUI_DIR` | `download` | ファイルを置く ComfyUI のフォルダ。`--comfyui-dir` / `--output-dir` には負け、`genmodelfit config` と自動で探すのには勝つ。 |
| `GENMODELFIT_COMFYUI_SEARCH` | `download`・`measure`・`share` | `0`・`off`・`no`・`false` で、ComfyUI を自動で探すのを止める(アプリ・スクリプト・試験用)。 |
| `GENMODELFIT_COMFYUI_START` | `measure`・`share` | `0`・`off`・`no`・`false` で、起動中の ComfyUI が無いときに見つけた ComfyUI を起動しない(`--no-start` と同じ)。 |
| `GENMODELFIT_CONFIG_DIR` | `config` と、保存した設定を読むすべてのコマンド | 設定ファイル `config.json` のフォルダ(既定は[ファイルの置き場所](#ファイルの置き場所))。 |
| `GENMODELFIT_CACHE_DIR` | `update`・`measure`・`share`・`bench`・`contrib` と、キャッシュした一覧を読むすべてのコマンド | キャッシュのフォルダ(既定は[ファイルの置き場所](#ファイルの置き場所))。 |
| `GENMODELFIT_SIGNING_KEY` | `python -m genmodelfit.signing sign` だけ(`genmodelfit` コマンドでは使わない) | データのリリースに署名する秘密鍵(PEM)。保守する人向け([release.ja.md](release.ja.md))。 |
| `GITHUB_TOKEN`・`GH_TOKEN` | `share` | PR を送るのにそのまま使う GitHub のトークン(scope `public_repo` が要る)。保存したログインとデバイス認証に勝つ。 |
| `GENMODELFIT_GH_CLIENT_ID` | `share` | デバイス認証に使う GitHub の OAuth アプリの client id。空にするとデバイス認証を使わない(`GITHUB_TOKEN`・`GH_TOKEN` だけ)。 |

通信は、プロキシと証明書の環境変数(`HTTPS_PROXY`・`REQUESTS_CA_BUNDLE` など)にも従います。`~/.netrc` の資格情報は送りません。

## 保存できる設定(`genmodelfit config`)

`genmodelfit config set キー 値` は、設定のフォルダの `config.json` に設定を保存します。読むのはコマンドラインだけで、
Python の関数は読みません。キーは `comfyui-dir`(フォルダのパス)と `lang`(`en` か `ja`)です。
同じ設定を決める場所が複数あるときは、次の上のものが勝ちます。

1. コマンドラインのオプション(`--lang`・`--comfyui-dir`・`--output-dir`)
2. 環境変数(`GENMODELFIT_LANG`・`GENMODELFIT_COMFYUI_DIR`)
3. 保存した設定(`genmodelfit config`)
4. 既定(`lang` は `en`。ComfyUI のフォルダは、ちょうど 1 つ見つかったときだけ自動で使う)

`genmodelfit config` は、各設定とどこから来た値かを出します。詳しくは [download.ja.md](download.ja.md)。

## ファイルの置き場所

| | Windows | macOS | Linux |
|---|---|---|---|
| 設定のフォルダ(`config.json`) | `%LOCALAPPDATA%\\genmodelfit` | `~/Library/Application Support/genmodelfit` | `~/.config/genmodelfit`(か `$XDG_CONFIG_HOME/genmodelfit`) |
| キャッシュのフォルダ | `%LOCALAPPDATA%\\genmodelfit\\Cache` | `~/Library/Caches/genmodelfit` | `~/.cache/genmodelfit`(か `$XDG_CACHE_HOME/genmodelfit`) |

この PC の設定ファイルの場所は `genmodelfit config path` で分かります。`GENMODELFIT_CONFIG_DIR`・`GENMODELFIT_CACHE_DIR`
でフォルダを変えられます。設定のフォルダには `share` の GitHub のログイン(`github_token`。本人だけが読める。
`genmodelfit share --logout` で消す)も入ります。キャッシュには、`update` で取った一覧(`db.json`)、HEAD とファイルの見出しの確認の結果、
`measure`・`bench` の結果(`measurements/`。この PC の判定にも使う)、提出の下書きと失敗の記録(`submissions/`。`share` が送るもの)、
`share` で送った印(`submissions_shared.json`)、`measure`・`share` が起動した ComfyUI の出力(`comfyui-<日付>.log`)、
測るあいだの `measure.lock`(同時に測るのは 1 つの `measure` か `share` だけ)が入ります。
キャッシュのフォルダは消してもかまいません(同梱の一覧に戻ります。保存した結果も消えます)。

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功。引数なしの `genmodelfit` は使い方を出して 0 で終わります。 |
| 1 | 失敗: モデルが無い・断った・聞かれて断った・通信の誤り・読めないファイル・検査に通らなかった、など。何が失敗かは、各コマンドの節に書いてあります。 |
| 2 | 使い方の誤り: 知らないオプション・正しくない値・足りない引数・略した同意のオプション・同時に使えないオプション。 |
| 3 | `contrib aggregate --report --fail-on-breach` だけ: 誤差が閾値を超えた。 |
| 130 | Ctrl+C で止めた。`download` はどの OS でも 130 を返し、途中まで落とした `.part` のファイルを残します。同じコマンドをもう一度実行すれば続きから落とします。ほかのコマンドは Python の決まりどおりに止まります: Linux・macOS では 130、Windows では 3221225786(0xC000013A。PowerShell の `$LASTEXITCODE` では -1073741510)。`bench`・`measure` は送った仕事を取り消して(genmodelfit が起動した ComfyUI も止めて)から 1 で終わります。`share` は測るのをやめ、そこまでのものを送るかに進んでから 130 で終わります。`measure`・`share` の質問で Ctrl+C を押すと 130 です。 |

`--json` のときも失敗は 1 で終わり、文は JSON の `error` にあります。

## コマンド

{toc}

{sections}

## このページについて

コマンドの節は、プログラムの引数の解釈から `tools/cli_reference.py` が作っているので、プログラムといつも一致します。
手で直さず、`src/genmodelfit/cli.py` の説明の文を直して `python tools/cli_reference.py` を実行してください
(このページが古いと `tests/test_commands_doc.py` が落ちます)。

環境変数を読むのは `src/genmodelfit/` の `i18n.py`(`GENMODELFIT_LANG`)・`cli.py`(`GENMODELFIT_COMFYUI_DIR`)・
`comfyfind.py`(`GENMODELFIT_COMFYUI_SEARCH`)・`comfystart.py`(`GENMODELFIT_COMFYUI_START`)・`config.py`(`GENMODELFIT_CONFIG_DIR`)・`db.py`(`GENMODELFIT_CACHE_DIR`)・
`signing.py`(`GENMODELFIT_SIGNING_KEY`)・`github.py`(`GITHUB_TOKEN`・`GH_TOKEN`・`GENMODELFIT_GH_CLIENT_ID`)です。
""",
}

LABELS = {
    "en": {
        "usage": "Usage",
        "options": "Options and arguments",
        "cols": ("Option / argument", "Value", "Default", "Description"),
        "global_cols": ("Option", "Value", "Description"),
        "exclusive": "Cannot be used together",
        "required_one": "Exactly one is required",
        "sub": "Subcommands",
        "none": "This command has no options of its own.",
        "shared": "Also takes the [shared options](#options-shared-by-commands)",
        "pre": "Preconditions",
        "side": "Side effects",
        "exit": "Exit codes",
        "agent": "Examples for scripts and apps",
        "examples": "Examples",
        "json": "JSON output (`--json`)",
        "code": "Code",
        "meaning": "Meaning",
        "group": "Group",
        "every": "every command",
        "common_pre": "[The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)",
    },
    "ja": {
        "usage": "使い方",
        "options": "オプションと引数",
        "cols": ("オプション / 引数", "値", "既定", "説明"),
        "global_cols": ("オプション", "値", "説明"),
        "exclusive": "同時には使えない",
        "required_one": "どれか 1 つが要る",
        "sub": "サブコマンド",
        "none": "このコマンドだけのオプションはありません。",
        "shared": "[共通のオプション](#共通のオプション)も使えます",
        "pre": "前提",
        "side": "副作用",
        "exit": "終了コード",
        "agent": "使い方の例(スクリプト・アプリ向け)",
        "examples": "例",
        "json": "JSON の出力(`--json`)",
        "code": "コード",
        "meaning": "意味",
        "group": "まとまり",
        "every": "すべてのコマンド",
        "common_pre": "[この PC を判定するほかのコマンドと同じ](#この-pc-を判定するコマンドの前提)",
    },
}

# --help の説明の節の名前(cli.py の _HELP_EN / _HELP_JA・EXAMPLES と同じ文字)
HEADERS = {
    "PRECONDITIONS:": "pre",
    "SIDE EFFECTS:": "side",
    "EXIT CODES:": "exit",
    "AGENT USAGE:": "agent",
    "EXAMPLES:": "examples",
    "前提:": "pre",
    "副作用:": "side",
    "終了コード:": "exit",
    "使い方の例(エージェント・アプリ向け):": "agent",
    "例:": "examples",
}

# JSON の形を書いた docs/schema(.ja).md の節(見出しの頭の文字)
SCHEMA_SECTIONS = {
    "system": ["System", "Data status"],
    "recommend": ["recommend / fit / search"],
    "fit": ["recommend / fit / search"],
    "search": ["recommend / fit / search"],
    "types": ["Uses"],
    "info": ["info"],
    "update": ["update"],
    "download": ["download"],
    "config": ["config"],
    "gate": ["gate"],
    "diff": ["diff"],
    "bench": ["bench"],
    "measure": ["measure"],
    "share": ["share"],
}

SKIP_DESTS = {"help", "lang"}
ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf"}  # 文の終わりと見ない略語
SHARED_MIN = 3  # この数以上の(上の段の)コマンドで同じ意味のオプションは「共通のオプション」にまとめる


def _slug(heading: str) -> str:
    """GitHub が見出しに付ける id(tests/test_docs.py の slug と同じ)。"""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").replace("*", "").strip().lower()
    kept = []
    for ch in text:
        if ch in " -_":
            kept.append("-" if ch == " " else ch)
        elif unicodedata.category(ch)[0] in "LNM":
            kept.append(ch)
    return "".join(kept)


def _headings(path: Path) -> list[tuple[str, str]]:
    """(見出しの文字, id)。囲みのコードの中は数えない。同じ id には GitHub と同じく -1, -2 を付ける。"""
    out, seen, fenced = [], {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        base = _slug(m.group(1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append((m.group(1).replace("`", ""), base if n == 0 else f"{base}-{n}"))
    return out


def _doc_target(name: str, lang: str) -> tuple[str, Path] | None:
    """文の中の「name.md」の、このページからのリンク先と、見出しを探すファイル。"""
    suffix = ".ja.md" if lang == "ja" else ".md"
    if (DOCS / f"{name}.md").exists():
        return f"{name}{suffix}", DOCS / f"{name}{suffix}"
    if (ROOT / f"{name}.md").exists():
        return f"../{name}{suffix}", ROOT / f"{name}{suffix}"
    return None


def _find_heading(path: Path, text: str, *, prefix: bool = False) -> tuple[str, str] | None:
    heads = _headings(path)
    want = text.strip().lower()
    for h, anchor in heads:
        if h.strip().lower() == want:
            return h, anchor
    for h, anchor in heads:
        hl = h.strip().lower()
        if (hl.startswith(want) if prefix else want in hl) and want:
            return h, anchor
    return None


def _placeholders() -> list[tuple[str, str]]:
    """この PC の実際の場所を、置き場所の名前に置き換える表(長いものから)。"""
    from genmodelfit import config, db

    pairs = [
        (str(config.config_dir()), "<config folder>"),
        (str(db.cache_dir()), "<cache folder>"),
        (str(Path.home()), "~"),
        (Path.home().as_posix(), "~"),
        (os.environ.get("USERNAME", "") or os.environ.get("USER", ""), "<user>"),
    ]
    return sorted((p for p in pairs if len(p[0]) > 2), key=lambda p: -len(p[0]))


def _clean(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        text = text.replace(old, new)
    return text


# --- 文を Markdown にする -----------------------------------------------------------------------

_COMMANDS: list[str] = []  # build_parser のサブコマンドの名前(_render で入れる)


def _inline_pattern() -> re.Pattern[str]:
    cmds = "|".join(sorted((re.escape(c) for c in _COMMANDS), key=len, reverse=True)) or "x^"
    return re.compile(
        r"(?P<code>`[^`]+`)"
        r"|(?P<doc>(?:docs/)?(?P<docname>[A-Za-z_]+)\.md"
        r"(?P<sec>「[^」]+」| \(\"[^\"]+\"\)| \"[^\"]+\"| \([^()]+\))?)"
        rf"|(?P<cmd>genmodelfit (?:{cmds})(?: (?:show|set|unset|path|list|approve|reject|make|share|validate|aggregate)\b)?"
        r"(?: --?[A-Za-z][\w-]*)*)"
        r"|(?P<angle>[^\s()（）、。,;「」]*<[^>\s]+>[^\s()（）、。,;「」]*|~/[\w./-]+)"
        r"|(?P<opt>(?<![\w`.<-])(?:--[A-Za-z][\w-]*|-[A-Za-z](?![\w-]))"
        r"(?: (?:all|sfw|uncensored|none|runnable|perfect|good|marginal|en|ja)(?![\w-]))?)"
    )


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("<", "&lt;").replace("*", "\\*").replace("~", "\\~")


def _inline(text: str, lang: str) -> str:
    """文の中のオプション・コマンド・<...> の入ったパスをコードにし、docs/X.md をリンクにする。"""
    out, pos = [], 0
    for m in _inline_pattern().finditer(text):
        out.append(_escape(text[pos : m.start()]))
        pos = m.end()
        if m.group("code"):
            out.append(m.group("code"))
        elif m.group("doc"):
            out.append(_doc_link(m, lang))
        elif m.group("angle"):
            token = m.group("angle")
            tail = ""
            while token and token[-1] in ".:":
                token, tail = token[:-1], token[-1] + tail
            out.append(f"`{token}`{_escape(tail)}")
        else:
            out.append(f"`{m.group(0)}`")
    out.append(_escape(text[pos:]))
    return "".join(out)


def _doc_link(m: re.Match[str], lang: str) -> str:
    name, sec = m.group("docname"), m.group("sec") or ""
    target = _doc_target(name, lang)
    if target is None:
        return f"`{m.group('doc')}`"
    href, path = target
    label = href.rsplit("/", 1)[-1]
    want = sec.strip().strip('「」()"').strip('"') if sec else ""
    found = _find_heading(path, want) if want else None
    if found:
        shown = f"{label}「{want}」" if lang == "ja" else f'{label}, "{want}"'
        return f"[{shown}]({href}#{found[1]})"
    return f"[{label}]({href})" + (_escape(sec) if sec else "")


def _join(lines: list[str], lang: str) -> str:
    """折り返した行を 1 つの文にする(日本語は、英数字と隣り合うところだけ空白を入れる)。"""
    text = ""
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        if not text:
            text = line
            continue
        if lang == "en":
            text += " " + line
            continue
        a, b = text[-1], line[0]
        glue = (a.isascii() or b.isascii()) and a not in "、。(「・/(" and b not in "、。)」・)("
        glue = glue or "→" in (a, b)
        text += (" " if glue else "") + line
    return text


def _sentences(text: str, lang: str) -> list[str]:
    """文に分ける(かっこの中では分けない)。"""
    out, buf, depth = [], [], 0
    for i, ch in enumerate(text):
        buf.append(ch)
        if ch in "((「":
            depth += 1
        elif ch in "))」":
            depth = max(0, depth - 1)
        if depth:
            continue
        nxt = text[i + 1 : i + 3]
        end = ch == "。"
        if (
            ch == "."
            and len(nxt) == 2
            and nxt[0] == " "
            and ((nxt[1].isascii() and nxt[1].isalpha()) or nxt[1] in '-`"')
        ):
            word = re.search(r"([\w.]*)\.$", text[: i + 1]).group(1).lower()  # type: ignore[union-attr]
            end = not (word in ABBREVIATIONS or len(word) == 1)
        if end:
            out.append("".join(buf).strip())
            buf = []
    rest = "".join(buf).strip()
    if rest:
        out.append(rest)
    return out


def _prose(lines: list[str], lang: str, *, bullets: bool | None = None) -> list[str]:
    """1 つの段落。文が 3 つ以上(bullets=None のとき)か bullets=True なら箇条書きにする。"""
    text = _join(lines, lang)
    if not text:
        return []
    parts = _sentences(text, lang)
    if bullets or (bullets is None and len(parts) >= 3):
        return [f"- {_inline(p, lang)}" for p in parts] + [""]
    return [_inline(text, lang), ""]


def _fence(text: str, kind: str = "text") -> str:
    return f"```{kind}\n" + text.strip("\n") + "\n```"


def _paragraphs(lines: list[str]) -> list[list[str]]:
    out, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _free_text(lines: list[str], lang: str, pairs) -> list[str]:
    """節の外の文: 字下げの無い行は文、字下げした行の並び(表のようなもの)は囲みのまま。"""
    out: list[str] = []
    for para in _paragraphs(lines):
        run: list[str] = []
        indented = False
        for ln in [*para, None]:
            is_ind = ln is not None and ln.startswith(" ")
            if ln is None or (run and is_ind != indented):
                if run:
                    if indented:
                        out += [_fence(_clean("\n".join(_dedent(run)), pairs)), ""]
                    else:
                        out += _prose([_clean(r, pairs) for r in run], lang)
                run = []
            if ln is not None:
                run.append(ln)
                indented = is_ind
    return out


def _dedent(lines: list[str]) -> list[str]:
    n = min((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), default=0)
    return [ln[n:] for ln in lines]


def _split_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(節の前の行, [(節の種類, 行)])。"""
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        kind = HEADERS.get(line.strip()) if not line.startswith(" ") else None
        if kind:
            sections.append((kind, []))
        elif sections:
            sections[-1][1].append(line)
        else:
            intro.append(line)
    return intro, sections


def _json_end(lines: list[str], k: int) -> int:
    """lines[k] から始まる JSON の鍵の並びの、次の行の番号(かっこが閉じて、次が { で始まらない行まで)。"""
    depth = 0
    while k < len(lines):
        depth += lines[k].count("{") + lines[k].count("[") - lines[k].count("}") - lines[k].count("]")
        k += 1
        if depth <= 0 and not (k < len(lines) and lines[k].strip().startswith("{")):
            break
    return k


def _schema_link(command: str, lang: str) -> str | None:
    keys = SCHEMA_SECTIONS.get(command.split()[0])
    if not keys:
        return None
    name = "schema.ja.md" if lang == "ja" else "schema.md"
    links = []
    for key in keys:
        found = _find_heading(DOCS / name, key, prefix=True)
        if found:
            links.append(
                f"[{name}「{found[0]}」]({name}#{found[1]})"
                if lang == "ja"
                else f'[{name}, "{found[0]}"]({name}#{found[1]})'
            )
    return ("・" if lang == "ja" else ", ").join(links) if links else None


_COMMON_PRE: dict[str, list[str]] = {}  # この PC を判定するコマンドに共通の前提(_render で入れる)


def _bullets(body: list[str], lang: str, pairs) -> list[str]:
    out: list[str] = []
    for para in _paragraphs(body):
        out += _prose([_clean(x, pairs) for x in para], lang, bullets=True)
    return out


def _common_pre(template: str, lang: str, pairs) -> list[str]:
    """cli._COMMON_HELP(_JA) の前提の節を箇条書きにしたもの。"""
    _, sections = _split_sections(template)
    body = next(lines for kind, lines in sections if kind == "pre")
    return _bullets([ln for ln in body if ln.startswith(" ")], lang, pairs)


def _render_help(text: str, command: str, lang: str, pairs) -> list[str]:
    """--help の説明(と例)を Markdown にする。"""
    lab = LABELS[lang]
    intro, sections = _split_sections(text)
    out = _free_text(intro, lang, pairs)
    json_done = False
    for kind, lines in sections:
        body = [ln for ln in lines if ln.startswith(" ")]
        free = [ln for ln in lines if ln.strip() and not ln.startswith(" ")]
        if kind in ("pre", "side"):
            out += [f"**{lab[kind]}**", ""]
            items = _bullets(body, lang, pairs)
            if kind == "pre" and items == _COMMON_PRE.get(lang):
                # この PC を判定するコマンドに共通の前提は、前置きの節に 1 回だけ載せる
                out += [f"- {lab['common_pre']}", ""]
            else:
                out += items
        elif kind == "exit":
            rows: list[list[str]] = []
            for ln in body:
                m = re.match(r"\s*(\d+)\s{2,}(.*)", ln)
                if m:
                    rows.append([m.group(1), m.group(2)])
                elif rows and ln.strip():
                    rows[-1].append(ln.strip())
            out += [f"**{lab['exit']}**", "", f"| {lab['code']} | {lab['meaning']} |", "|---|---|"]
            for code, *texts in rows:
                cell = _inline(_clean(_join(texts, lang), pairs), lang).replace("|", "\\|")
                out.append(f"| {code} | {cell} |")
            out.append("")
        elif kind in ("agent", "examples"):
            code: list[str] = []
            prose: list[list[str]] = [[]]
            k = 0
            body_all = lines
            while k < len(body_all):
                ln = body_all[k]
                s = ln.strip()
                if s.startswith("JSON (--json)"):
                    k = _json_end(body_all, k)
                    link = _schema_link(command, lang)
                    if link and not json_done:
                        prose.append([f"\0{lab['json']}: {link}"])
                        json_done = True
                    prose.append([])
                    continue
                if re.match(r"(Details|詳しくは)[: ]+docs/schema\.md$", s):
                    if not json_done:
                        link = _schema_link(command, lang)
                        if link:
                            prose.append([f"\0{lab['json']}: {link}"])
                            json_done = True
                            prose.append([])
                            k += 1
                            continue
                    k += 1
                    continue
                if (s.startswith("genmodelfit") and ln.startswith(" ")) or (
                    code and code[-1].endswith("\\") and ln.startswith(" ")
                ):
                    code.append(ln)
                elif s:
                    prose[-1].append(ln)
                elif prose[-1]:
                    prose.append([])
                k += 1
            out += [f"**{lab[kind]}**", ""]
            if code:
                out += [_fence(_clean("\n".join(_dedent(code)), pairs), "sh"), ""]
            for para in prose:
                if para and para[0].startswith("\0"):
                    out += [para[0][1:], ""]
                elif para:
                    out += _prose([_clean(x, pairs) for x in para], lang)
            continue
        if free:
            out += _free_text(free, lang, pairs)
    return out


# --- 表 ----------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _value(action: argparse.Action) -> str:
    if action.nargs == 0:
        return ""
    if not action.option_strings:  # 引数は名前が「オプション / 引数」の欄に出る
        return ""
    if action.metavar:
        v = action.metavar if isinstance(action.metavar, str) else " ".join(action.metavar)
    elif action.choices is not None:
        v = "{" + ",".join(str(c) for c in action.choices) + "}"
    else:
        v = action.dest.upper()
    if action.nargs in ("*", "+"):
        v += "..."
    return v


def _default_cell(action: argparse.Action, pairs, lang: str) -> str:
    d = action.default
    if not (d is None or d is False or d is argparse.SUPPRESS or action.nargs == 0 or action.required):
        text = ", ".join(str(x) for x in d) if isinstance(d, list | tuple) else str(d)
        return f"`{_cell(_clean(text, pairs))}`"
    doc = getattr(action, "doc_default", None)
    if doc:
        return (
            _inline(_clean(doc, pairs), lang).replace("|", "\\|")
            if not (doc.isascii() and " " not in doc)
            else f"`{_cell(doc)}`"
        )
    return ""


def _flags(action: argparse.Action) -> str:
    if action.option_strings:
        return ", ".join(f"`{o}`" for o in action.option_strings)
    return f"`{action.metavar or action.dest}`".replace("|", "\\|")


def _help_text(parser: argparse.ArgumentParser, action: argparse.Action, pairs, lang: str) -> str:
    if not action.help:
        return ""
    raw = _clean(" ".join(parser._get_formatter()._expand_help(action).split()), pairs)
    return _inline(raw, lang).replace("|", "\\|")


def _visible(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        a
        for a in parser._actions
        if not isinstance(a, argparse._SubParsersAction)
        and a.help is not argparse.SUPPRESS
        and not (a.dest in SKIP_DESTS and a.option_strings)
    ]


def _key(parser, action, pairs, lang) -> tuple:
    return (
        tuple(action.option_strings),
        _value(action),
        _default_cell(action, pairs, lang),
        _help_text(parser, action, pairs, lang),
    )


def _row(parser, action, pairs, lang, *, with_default: bool = True) -> str:
    value = _cell(_value(action))
    cells = [_flags(action), f"`{value}`" if value else ""]
    if with_default:
        cells.append(_default_cell(action, pairs, lang))
    cells.append(_help_text(parser, action, pairs, lang))
    return "| " + " | ".join(cells) + " |"


def _groups(parser: argparse.ArgumentParser) -> list[tuple[bool, list[str]]]:
    out = []
    for g in parser._mutually_exclusive_groups:
        names = [
            " / ".join(f"`{o}`" for o in a.option_strings) if a.option_strings else f"`{a.dest}`"
            for a in g._group_actions
            if a.help is not argparse.SUPPRESS
        ]
        if len(names) > 1:
            out.append((g.required, names))
    return out


def _titled_groups(parser: argparse.ArgumentParser, lang: str) -> list[str]:
    """add_argument_group で名前を付けたまとまり(download の「同意」)。"""
    out = []
    for g in parser._action_groups[2:]:
        names = [
            f"`{a.option_strings[0]}`"
            for a in g._group_actions
            if a.option_strings and a.help is not argparse.SUPPRESS
        ]
        if g.title and names:
            sep = "・" if lang == "ja" else ", "
            out.append(f"- {_inline(g.title, lang)}: {sep.join(names)}")
    return out


def _children(parser: argparse.ArgumentParser):
    """(名前, 子のパーサ, 一行の説明) を登録の順に。"""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            helps = {c.dest: c.help for c in a._choices_actions}
            return [(n, p, helps.get(n) or "") for n, p in a.choices.items()]
    return []


def _walk(parser, prefix: str):
    for n, p, h in _children(parser):
        yield f"{prefix} {n}".strip(), p, h
        yield from _walk(p, f"{prefix} {n}")


def _shared(top, pairs, lang) -> tuple[dict[tuple, list[str]], dict[tuple, tuple]]:
    """同じ意味(同じ名前・値・既定・説明)のオプションと、それを持つコマンド。"""
    users: dict[tuple, list[str]] = {}
    sample: dict[tuple, tuple] = {}
    for name, p, _ in _walk(top, ""):
        for a in _visible(p):
            if not a.option_strings:
                continue
            k = _key(p, a, pairs, lang)
            users.setdefault(k, []).append(name)
            sample.setdefault(k, (p, a))
    keep = {k: v for k, v in users.items() if len({n.split()[0] for n in v}) >= SHARED_MIN}
    return keep, sample


def _wrap_usage(text: str, width: int = 100) -> str:
    """使い方の行を、Python の版によらず同じ形に折り返す(argparse の折り返しの位置は 3.13 で変わった)。
    1 行につないでから、[ ] と ( ) の外の空白だけで切る。続きの行はコマンド名の後ろにそろえる。"""
    flat = " ".join(text.split())
    tokens: list[str] = []
    cur, depth = "", 0
    for ch in flat:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        if ch == " " and depth == 0:
            if cur:
                tokens.append(cur)
            cur = ""
            continue
        cur += ch
    if cur:
        tokens.append(cur)
    if not tokens:
        return ""
    head, rest = [tokens[0]], tokens[1:]
    while rest and rest[0][:1].isalpha() and rest[0].islower() and not rest[0].isupper():
        head.append(rest.pop(0))
    prefix = " ".join(head)
    indent = " " * (len(prefix) + 1)
    lines, line = [], prefix
    for tok in rest:
        if len(line) + 1 + len(tok) > width and line.strip():
            lines.append(line)
            line = indent + tok
        else:
            line += " " + tok
    lines.append(line)
    return "\n".join(lines)


def _section(
    name: str, parser, purpose: str, depth: int, lang: str, pairs, toc: list[str], shared
) -> list[str]:
    lab = LABELS[lang]
    heading = f"`{name}`"
    line = f"{'  ' * (depth - 2)}- [{heading}](#{_slug(heading)})"
    toc.append(f"{line} — {_inline(_cell(purpose), lang)}" if purpose else line)
    out = [f"{'#' * depth} {heading}", ""]
    if purpose:
        out += [_inline(_clean(purpose, pairs), lang), ""]
    fmt = parser._get_formatter()
    fmt.add_usage(parser.usage, parser._actions, parser._mutually_exclusive_groups, prefix="")
    usage = _wrap_usage(fmt.format_help())
    out += [f"**{lab['usage']}**", "", _fence(_clean(usage, pairs)), ""]
    command = name.removeprefix("genmodelfit ").strip()
    desc = (parser.description or "").strip()
    rendered = _render_help(desc, command, lang, pairs) if desc and _cell(desc) != _cell(purpose) else []
    # 説明の頭(何をするか)を先に、前提・副作用・終了コード・例はオプションの後に
    first_bold = next((i for i, x in enumerate(rendered) if x.startswith("**")), len(rendered))
    out += rendered[:first_bold]
    kids = _children(parser)
    own, common = [], []
    for a in _visible(parser):
        if a.option_strings and _key(parser, a, pairs, lang) in shared:
            common.append(a)
        else:
            own.append(a)
    if own:
        out += [
            f"**{lab['options']}**",
            "",
            "| " + " | ".join(lab["cols"]) + " |",
            "|---|---|---|---|",
            *(_row(parser, a, pairs, lang) for a in own),
            "",
        ]
    elif not kids:
        out += [lab["none"], ""]
    notes = [
        f"- {lab['required_one'] if required else lab['exclusive']}: " + ", ".join(names)
        for required, names in _groups(parser)
    ]
    notes += _titled_groups(parser, lang)
    if common:
        sep = "・" if lang == "ja" else ", "
        notes.append(f"- {lab['shared']}: " + sep.join(f"`{a.option_strings[0]}`" for a in common))
    if notes:
        out += [*notes, ""]
    out += rendered[first_bold:]
    if parser.epilog:
        out += _render_help(parser.epilog, command, lang, pairs)
    if kids:
        out += [f"**{lab['sub']}**: " + ", ".join(f"`{name} {n}`" for n, _, _ in kids), ""]
        for n, p, h in kids:
            out += _section(f"{name} {n}", p, h, depth + 1, lang, pairs, toc, shared)
    return out


def render(lang: str) -> str:
    """言語 lang のページの全文。"""
    saved = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "100"  # 使い方の行の折り返しを、端末によらず同じにする
    try:
        return _render(lang)
    finally:
        if saved is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = saved


def _render(lang: str) -> str:
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from genmodelfit import cli, i18n
    from genmodelfit.estimate import FIT_BANDS
    from genmodelfit.gate import DEFAULT_MAX_HEAD_AGE_DAYS

    pairs = _placeholders()
    _COMMON_PRE[lang] = _common_pre(
        (cli._COMMON_HELP_JA if lang == "ja" else cli._COMMON_HELP)
        .replace("{zero}", "")
        .replace("{one}", ""),
        lang,
        pairs,
    )
    lab = LABELS[lang]
    with i18n.forced(lang), cli._argparse_language():
        top = cli.build_parser()
        _COMMANDS[:] = [n for n, _, _ in _children(top)]
        global_rows = [_row(top, a, pairs, lang, with_default=False) for a in _visible(top)]
        # --lang はサブコマンドの表から外してあるので、ここで足す
        lang_action = next(a for a in top._actions if a.dest == "lang")
        global_rows.append(_row(top, lang_action, pairs, lang, with_default=False))
        shared, sample = _shared(top, pairs, lang)
        sep = "・" if lang == "ja" else ", "
        shared_rows = []
        every = {n for n, p, _ in _walk(top, "") if not _children(p)}  # サブコマンドを持たないもの
        for k, names in shared.items():
            p, a = sample[k]
            row = _row(p, a, pairs, lang)
            used = lab["every"] if every <= set(names) else sep.join(f"`{n}`" for n in names)
            shared_rows.append(f"{row} {used} |")
        toc: list[str] = []
        body: list[str] = []
        for n, p, h in _children(top):
            body += _section(f"genmodelfit {n}", p, h, 2, lang, pairs, toc, shared)
    bands = {name: round(v * 100) for v, name in FIT_BANDS}
    text = INTRO[lang]
    for key, value in {
        "{global_rows}": "\n".join(global_rows),
        "{shared_rows}": "\n".join(shared_rows),
        "{common_pre}": "\n".join(_COMMON_PRE[lang]).strip(),
        "{perfect}": str(bands["perfect"]),
        "{good}": str(bands["good"]),
        "{marginal}": str(bands["marginal"]),
        "{head_days}": str(DEFAULT_MAX_HEAD_AGE_DAYS),
        "{toc}": "\n".join(toc),
        "{sections}": "\n".join(body),
    }.items():
        text = text.replace(key, value)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write docs/commands.md and docs/commands.ja.md from the parser."
    )
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if the files are out of date")
    args = ap.parse_args(argv)
    stale = []
    for lang, path in TARGETS.items():
        text = render(lang)
        if args.check:
            current = path.read_bytes().decode("utf-8") if path.exists() else None
            if current != text:
                stale.append(path.name)
        else:
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print(f"out of date: {', '.join(stale)} (run: python tools/cli_reference.py)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
