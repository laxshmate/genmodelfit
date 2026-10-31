# Command reference

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
| fit level (`Status`) | How much of the memory a model needs on this PC (VRAM on the GPU, RAM on the CPU): `perfect` up to 60%, `good` up to 85%, `marginal` up to 98%, `too_tight` above 98% (does not run), `unknown` / `?` not judged (the size is not known). Some run modes cap the level: [README, "Fit levels"](../README.md#fit-levels). |
| runnable | `perfect`, `good` or `marginal` (what `fit` shows by default). |
| offload | Keeping part of the weights in RAM instead of VRAM and moving them over while running (`GPU+RAM`). It lets a model run on a smaller GPU, but slowly. "Without offloading" means `GPU` or `GPU~`. |
| run mode (`Mode`) | `GPU` everything on the GPU; `GPU~` the parts are moved onto the GPU one after another; `GPU+RAM` part of the weights is offloaded to RAM (slow); `CPU` (slow); `-` does not run. [README, "Columns"](../README.md#columns) |
| Score | 0–100, the memory headroom (100 up to 70% use). Not speed or quality. |
| model / workflow | A workflow is one ComfyUI template (or Hugging Face entry) with an id such as `image_z_image_turbo_int8`. A model is the weights (family + size) that several workflows share; they differ only in precision or use. Tables show one row per model (`--all-workflows`: one per workflow). `info` and `download` accept either. |
| use (`--task`) | What a workflow does inside its type: `gen`, `edit`, `i2v`, ... (`genmodelfit types` lists them). |
| the list (`db.json`) | The model list that genmodelfit judges. A copy comes with the package; `update --from-release` stores a newer signed one in the cache, which is then used instead (`--no-cache`: the bundled one; `--db PATH`: a file). |
| HEAD check | An anonymous HTTP HEAD request to each download URL, recorded in the list. Checks older than 30 days (`--max-head-age-days`) count as expired: those models are held until you run `genmodelfit update --from-release`. `system` shows the date. |
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
| `--version` |  | show program's version number and exit |
| `--lang` | `{en,ja}` | display language (default: $GENMODELFIT_LANG, then lang in `genmodelfit config`, else en) |

## Options shared by commands

These options mean the same in every command that takes them. The command sections list only their own options and
link here.

| Option | Value | Default | Description | Commands |
|---|---|---|---|---|
| `--json` |  |  | output JSON | every command |
| `--vram` | `GB` | detected on this PC | judge with this much GPU VRAM in GB (GiB), e.g. 8 (0: no GPU); to judge another PC | `system`, `recommend`, `fit`, `search`, `info`, `download` |
| `--ram` | `GB` | detected on this PC | judge with this much system RAM in GB (GiB), e.g. 32; to judge another PC | `system`, `recommend`, `fit`, `search`, `info`, `download` |
| `--db` | `PATH` | the list fetched by update, else the bundled list | use this model list file (db.json) | `system`, `recommend`, `fit`, `search`, `types`, `info`, `download`, `measure`, `gate`, `bench` |
| `--no-cache` |  |  | use the bundled list, not the one fetched by update | `system`, `recommend`, `fit`, `search`, `types`, `info`, `download`, `measure`, `share`, `gate`, `review list`, `bench` |
| `--license-allowlist` | `LIST\|FILE` | the allowlist in [gate.md](gate.md) | licenses allowed for auto-recommendation ("mit,apache-2.0", or a file with one per line). Default: [gate.md](gate.md). An empty string ("") is the same as not giving it | `system`, `recommend`, `fit`, `search`, `info`, `update`, `download`, `gate` |
| `--comfyui-version` | `VER` | the latest release recorded in the list | the ComfyUI version in use; apps should always pass theirs (default: the latest release recorded by update) | `system`, `recommend`, `fit`, `search`, `info`, `update`, `download`, `gate` |
| `--max-head-age-days` | `DAYS` | `30` | trust HEAD checks for this many days (default 30; none for no limit) | `system`, `recommend`, `fit`, `search`, `info`, `update`, `download`, `gate` |
| `--allow-uncensored-auto` |  |  | allow uncensored models in automatic recommendations (Verified); not a display filter and not download consent | `system`, `recommend`, `fit`, `search`, `info`, `update`, `download`, `gate` |
| `--modality`, `-m` | `TYPE[,TYPE]` |  | filter by type (comma-separated): image, video, music, sfx, tts, 3d | `recommend`, `fit`, `search` |
| `--content` | `{all,sfw,uncensored}` | `sfw` | filter by the content mark: sfw (default) / uncensored (only uncensored) / all (both; same as `--uncensored`). Only all can be combined with `--uncensored`. Write it in full | `recommend`, `fit`, `search`, `info` |
| `--uncensored` |  |  | include uncensored models (same as `--content all`) | `recommend`, `fit`, `search`, `types`, `info` |
| `--width` | `PX` | by type: image 1024, video 832 | judge images / videos at this width in pixels; settings left out use the defaults by type | `recommend`, `fit`, `search`, `info` |
| `--height` | `PX` | by type: image 1024, video 480 | judge images / videos at this height in pixels | `recommend`, `fit`, `search`, `info` |
| `--frames` | `N` | by type: video 81 | judge videos at this number of frames | `recommend`, `fit`, `search`, `info` |
| `--length` | `SEC` | by type: music 30 s, sfx 10 s, tts 10 s | judge music / sound effects / speech at this length in seconds | `recommend`, `fit`, `search`, `info` |
| `--precision` | `PREC` | the precision in the list | estimate as if the weights were converted to this precision (fp32, bf16, fp16, fp8, int8, q8, q6, q5, nf4, q4; only when the original precision is known). Not the same as download `--precision`, which picks the workflow to download | `recommend`, `fit`, `search`, `info` |
| `--limits` |  |  | also show the largest resolution / frames / length that is good on this PC's GPU without offloading (limits in JSON) | `recommend`, `fit`, `search` |
| `--license` | `LIST` |  | only models whose files all have one of these licenses (e.g. apache-2.0,mit; unknown never matches) | `recommend`, `fit`, `search` |
| `--any-license`, `--include-unknown-license` |  |  | also show models with files of unknown license (hidden by default; info `<ID>` always shows them; old form `--include-unknown-license`) | `recommend`, `fit`, `search` |
| `--task` | `USE[,USE]` |  | filter by use (gen / edit / i2v / lipsync / motion / voice / upscale / tool, or task codes such as t2i, i2i, inpaint; see `genmodelfit types`) | `recommend`, `fit`, `search` |
| `--all-workflows` |  |  | one row per workflow instead of grouping the same model (differing only in precision or use) | `recommend`, `fit`, `search` |

## Preconditions of the commands that judge this PC

The preconditions of `system`, `recommend`, `fit`, `search` and `info`:

- Nothing to prepare.
- The GPU and RAM are detected; `--vram` / `--ram` override them.
- GPU: NVIDIA via nvidia-smi; otherwise AMD / Intel / Apple via WMI (Windows), lspci + sysfs (Linux) or system_profiler (macOS).
- The bundled model list is used unless `genmodelfit update` stored a newer one (`--no-cache`).

## If `genmodelfit` is not found

- pipx and uv tool put `genmodelfit` on PATH after a one-time `pipx ensurepath` / `uv tool update-shell`; then open a
  new terminal.
- After `pip install`, run `python -m genmodelfit …` with the same Python you installed it into (on Windows also
  `py -m genmodelfit …`). This does not reach pipx or uv tool installs, which have their own environments.
- pip may print *"WARNING: The script genmodelfit.exe is installed in '…\Scripts' which is not on PATH"*. This is
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
| `VRAM` | GiB needed in that mode; `m` = measured (by the maintainer, on this PC, or the median of other users' measurements of the same GPU; number of sources: `info`, JSON `sample_count`) |
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
| Config folder (`config.json`) | `%LOCALAPPDATA%\genmodelfit` | `~/Library/Application Support/genmodelfit` | `~/.config/genmodelfit` (or `$XDG_CONFIG_HOME/genmodelfit`) |
| Cache folder | `%LOCALAPPDATA%\genmodelfit\Cache` | `~/Library/Caches/genmodelfit` | `~/.cache/genmodelfit` (or `$XDG_CACHE_HOME/genmodelfit`) |

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

- [`genmodelfit system`](#genmodelfit-system) — show this PC's GPU, memory and free disk, and how fresh the model list is
- [`genmodelfit recommend`](#genmodelfit-recommend) — recommend models, best first
- [`genmodelfit fit`](#genmodelfit-fit) — judge the models that run, as a table (`--all` for all)
- [`genmodelfit search`](#genmodelfit-search) — search by a part of the id or name and judge the matches for this PC
- [`genmodelfit types`](#genmodelfit-types) — the big types, the uses inside each (names for `--task`), and counts
- [`genmodelfit info`](#genmodelfit-info) — details of one model
- [`genmodelfit update`](#genmodelfit-update) — get a newer model list (users: `--from-release`; without options it rebuilds from upstream)
- [`genmodelfit download`](#genmodelfit-download) — download the files of a model (consent, sha256, resume, ComfyUI folders)
- [`genmodelfit measure`](#genmodelfit-measure) — pick a model worth measuring and measure it on ComfyUI (started if needed; nothing is sent)
- [`genmodelfit share`](#genmodelfit-share) — measure installed models for a few minutes and send them, with your unsent measurements, as a GitHub pull request (`--issue`: send with the issue form)
- [`genmodelfit config`](#genmodelfit-config) — show or change settings (download's ComfyUI folder, display language)
  - [`genmodelfit config show`](#genmodelfit-config-show) — current settings and where they come from (the default when no subcommand is given)
  - [`genmodelfit config set`](#genmodelfit-config-set) — save a value
  - [`genmodelfit config unset`](#genmodelfit-config-unset) — remove a value
  - [`genmodelfit config path`](#genmodelfit-config-path) — where the config file is
- [`genmodelfit gate`](#genmodelfit-gate) — summary of which models can be auto-recommended
- [`genmodelfit diff`](#genmodelfit-diff) — differences between two model lists (db.json): added / removed, licenses, auto-recommendation, HEAD failures
- [`genmodelfit review`](#genmodelfit-review) — list, approve or reject models waiting for review (new entries, license changes, ...; for maintainers)
  - [`genmodelfit review list`](#genmodelfit-review-list) — models waiting for review and why
  - [`genmodelfit review approve`](#genmodelfit-review-approve) — approve with evidence (primary-source URLs); rewrites the list file
  - [`genmodelfit review reject`](#genmodelfit-review-reject) — remove from the list and record it so that update does not add it again
- [`genmodelfit bench`](#genmodelfit-bench) — for maintainers: measure on a running ComfyUI (usually: measure)
- [`genmodelfit contrib`](#genmodelfit-contrib) — for maintainers: make, validate and aggregate measurement submissions (to send one: measure and share)
  - [`genmodelfit contrib make`](#genmodelfit-contrib-make) — make a submission JSON from a result saved by bench
  - [`genmodelfit contrib show`](#genmodelfit-contrib-show) — show what will be shared and the privacy checklist
  - [`genmodelfit contrib share`](#genmodelfit-contrib-share) — the same as `genmodelfit share --issue` (kept for older scripts)
  - [`genmodelfit contrib validate`](#genmodelfit-contrib-validate) — check shape, values, personal information and duplicates (also used in CI)
  - [`genmodelfit contrib intake`](#genmodelfit-contrib-intake) — check a measurement issue on GitHub and write the files for its PR (used by measurement-intake.yml)
  - [`genmodelfit contrib aggregate`](#genmodelfit-contrib-aggregate) — aggregate by model, settings and GPU, and check the accuracy

## `genmodelfit system`

show this PC's GPU, memory and free disk, and how fresh the model list is

**Usage**

```text
genmodelfit system [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                   [--license-allowlist LIST|FILE] [--comfyui-version VER]
                   [--max-head-age-days DAYS] [--allow-uncensored-auto] [--disk-path PATH]
                   [--all-gpus]
```

Show this PC (GPU, RAM, free disk) and whether the model list in use is still fresh (Data: source, HEAD check dates, expired / valid until, auto-recommendable count).

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--disk-path` | `PATH` | your home folder | the folder whose disk's free space is shown |
| `--all-gpus` |  |  | list all display adapters (GPUs), not only NVIDIA (takes about 1 s on Windows; not used for judging) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- [The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also when the list has expired: see Data / database.status.expired; run `genmodelfit update --from-release` to renew it) |
| 1 | Error (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit system --json
genmodelfit system --comfyui-version 0.40.0 --json
```

JSON output (`--json`): [schema.md, "System"](schema.md#system), [schema.md, "Data status"](schema.md#data-status)

## `genmodelfit recommend`

recommend models, best first

**Usage**

```text
genmodelfit recommend [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                      [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                      [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                      [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                      [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                      [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                      [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                      [--auto-only] [--include-tools]
```

Recommend generation models for this PC, best first.

- Without `-m` / `--task`: one section per type (image, video, music, sfx, tts, 3d), top `-n` (default 3) each.
- With `-m` or `--task`: one table (default 10).
- One row per model; the precision and workflow that fit best are chosen (`--all-workflows`: all).
- Order: models that run, then Perfect/Good, then Marginal (faster mode first), official templates, popularity (rank among the same type and source), Score, id (`--json` puts auto-recommendable ones, gate: auto, before the levels).
- Score 0-100 is the memory headroom (100 up to 70% use; not speed).
- Score rules: [scoring.md](scoring.md).
- Uncensored models and utility tools are hidden unless asked.
- The output is a table; add `--json` for apps.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--limit`, `-n` | `N` | 3 per type (10 with `-m` / `--task`) | number of rows to show (1 or more) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` | `marginal` | show only this fit level or better (recommend default: marginal; fit / search: all) |
| `--fit` | `{perfect,good,marginal,runnable,all}` |  | fit levels to show: perfect / good / marginal (that level only), runnable (all that run; fit default), all (too_tight and unknown too). Not with `--min-fit` |
| `--auto-only` |  |  | only auto-recommendable models |
| `--include-tools` |  |  | include utility tools such as depth, pose, background removal and upscaling (default: generation models only) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--modality`, `--content`, `--uncensored`, `--width`, `--height`, `--frames`, `--length`, `--precision`, `--limits`, `--license`, `--any-license`, `--task`, `--all-workflows`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- [The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit recommend --json
genmodelfit recommend -m image --task edit -n 5 --min-fit good --json
genmodelfit recommend -m video,music --auto-only --comfyui-version 0.40.0 --json
genmodelfit recommend --license apache-2.0,mit --vram 8 --ram 32 --json
```

JSON output (`--json`): [schema.md, "recommend / fit / search"](schema.md#recommend--fit--search)

One item per model (same weights family + size); `--all-workflows` gives one per workflow.

**Examples**

```sh
genmodelfit recommend                            # top 3 per type
genmodelfit recommend -m image --task edit -n 5  # top 5 image editing models
genmodelfit recommend --uncensored               # include uncensored models (default is sfw)
genmodelfit recommend --vram 12 --ram 32         # judge for another PC
genmodelfit recommend -m video --json            # JSON for apps
```

## `genmodelfit fit`

judge the models that run, as a table (`--all` for all)

**Usage**

```text
genmodelfit fit [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                [--sort {type,score,vram,disk,name}] [--all] [--perfect] [--include-tools]
```

- Judge the models for this PC and show them in one table (not cut to the top few like recommend).
- By default only the models that run (`--fit runnable`: perfect, good, marginal), without utility tools and models with files of unknown license.
- `--all` also shows too_tight and unknown ones, utility tools and unknown licenses; uncensored models need `--uncensored` (with or without `--all`).
- Order: type, then fit level, official templates, popularity, Score (`--sort` changes it).

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--limit`, `-n` | `N` | `all` | number of rows to show (1 or more) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` |  | show only this fit level or better (recommend default: marginal; fit / search: all) |
| `--fit` | `{perfect,good,marginal,runnable,all}` | runnable (all with `--all`) | fit levels to show: perfect / good / marginal (that level only), runnable (all that run; fit default), all (too_tight and unknown too). Not with `--min-fit` |
| `--sort` | `{type,score,vram,disk,name}` | `type` | order: type (default; type -> fit level -> official -> popularity -> Score), score (across types; level -> Score), vram / disk (smallest first), name |
| `--all` |  |  | show everything (too tight, unknown, utility tools, unknown license; uncensored needs `--uncensored`) |
| `--perfect`, `-p` |  |  | perfect only (same as `--fit perfect`) |
| `--include-tools` |  |  | include utility tools such as depth, pose, background removal and upscaling (default: generation models only) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--modality`, `--content`, `--uncensored`, `--width`, `--height`, `--frames`, `--length`, `--precision`, `--limits`, `--license`, `--any-license`, `--task`, `--all-workflows`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- [The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit fit --json
genmodelfit fit -m video --task i2v --min-fit good -n 20
genmodelfit fit --uncensored --all-workflows --vram 12 --ram 32 --json
```

JSON output (`--json`): [schema.md, "recommend / fit / search"](schema.md#recommend--fit--search)

One item per model (same weights family + size); `--all-workflows` gives one per workflow.

**Examples**

```sh
genmodelfit fit                                         # every model that runs on this PC
genmodelfit fit --sort score --fit good                 # good only, ranked across types
genmodelfit fit --all                                   # everything, including too tight models and utility tools
genmodelfit fit -m video --task i2v --vram 12 --ram 32  # image to video on another PC
genmodelfit fit --uncensored --json                     # JSON, uncensored included (default is sfw)
```

## `genmodelfit search`

search by a part of the id or name and judge the matches for this PC

**Usage**

```text
genmodelfit search [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                   [--modality TYPE[,TYPE]] [--content {all,sfw,uncensored}] [--uncensored]
                   [--width PX] [--height PX] [--frames N] [--length SEC] [--precision PREC]
                   [--limit N] [--limits] [--min-fit {perfect,good,marginal,too_tight}]
                   [--license LIST] [--fit {perfect,good,marginal,runnable,all}] [--any-license]
                   [--task USE[,USE]] [--all-workflows] [--license-allowlist LIST|FILE]
                   [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto]
                   [--sort {type,score,vram,disk,name}] [--all] [--perfect] query
```

- Search models by a part of the id or name (case-insensitive) and judge them for this PC.
- `--content` applies (default sfw).
- With no match the list is empty and the exit code is 0.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--limit`, `-n` | `N` | `all` | number of rows to show (1 or more) |
| `--min-fit` | `{perfect,good,marginal,too_tight}` |  | show only this fit level or better (recommend default: marginal; fit / search: all) |
| `--fit` | `{perfect,good,marginal,runnable,all}` | every level | fit levels to show: perfect / good / marginal (that level only), runnable (all that run; fit default), all (too_tight and unknown too). Not with `--min-fit` |
| `--sort` | `{type,score,vram,disk,name}` | `type` | order: type (default; type -> fit level -> official -> popularity -> Score), score (across types; level -> Score), vram / disk (smallest first), name |
| `--all` |  |  | show everything (too tight, unknown, utility tools, unknown license; uncensored needs `--uncensored`) |
| `--perfect`, `-p` |  |  | perfect only (same as `--fit perfect`) |
| `query` |  |  | search word (part of the id or name; case-insensitive) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--modality`, `--content`, `--uncensored`, `--width`, `--height`, `--frames`, `--length`, `--precision`, `--limits`, `--license`, `--any-license`, `--task`, `--all-workflows`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- [The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also when nothing matches: empty models / "no match") |
| 1 | Error: blank query (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit search flux --json
genmodelfit search wan -m video --min-fit good
genmodelfit search sdxl --uncensored --json
```

JSON output (`--json`): [schema.md, "recommend / fit / search"](schema.md#recommend--fit--search)

One item per model (same weights family + size); `--all-workflows` gives one per workflow.

**Examples**

```sh
genmodelfit search flux                         # models whose name contains flux
genmodelfit search wan -m video --min-fit good  # video models that are good or better
```

## `genmodelfit types`

the big types, the uses inside each (names for `--task`), and counts

**Usage**

```text
genmodelfit types [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                  [--content {all,sfw,uncensored}] [--uncensored]
```

List the big types (image, video, music, sfx, tts, 3d) and the uses inside each type, with counts. The use names are what `--task` accepts.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--content` | `{all,sfw,uncensored}` | `sfw` | which content marks to count: sfw (default) / uncensored (only uncensored) / all (both; same as `--uncensored`). Write it in full |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--db`, `--no-cache`, `--uncensored`

**Preconditions**

- Nothing to prepare.
- The PC is not judged.
- The bundled model list is used unless `genmodelfit update` stored a newer one (`--no-cache`).

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit types
genmodelfit types --json
```

JSON output (`--json`): [schema.md, "Uses"](schema.md#uses)

`--vram`, `--ram` are also accepted but have no effect in this command (kept for older scripts).

## `genmodelfit info`

details of one model

**Usage**

```text
genmodelfit info [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                 [--content {all,sfw,uncensored}] [--uncensored] [--width PX] [--height PX]
                 [--frames N] [--length SEC] [--precision PREC] [--license-allowlist LIST|FILE]
                 [--comfyui-version VER] [--max-head-age-days DAYS] [--allow-uncensored-auto] model
```

- Show one model: files, licenses, estimate, limits on this PC, the gate checks, and the other workflows that use the same model.
- An exact id always matches (uncensored included).
- When the id does not match, a part of the id or name is searched within `--content`; several matches are an error that lists the candidates.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `model` |  |  | model name (the Model column), workflow id, or a part of either |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--content`, `--uncensored`, `--width`, `--height`, `--frames`, `--length`, `--precision`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- [The same as for the other commands that judge this PC](#preconditions-of-the-commands-that-judge-this-pc)

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: no such model or several matches (candidates listed); other errors (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit info image_z_image_turbo_int8 --json
genmodelfit info sdxlturbo --vram 8 --ram 32
```

JSON output (`--json`): [schema.md, "info"](schema.md#info)

On error: { error, candidates? }.

**Examples**

```sh
genmodelfit info "Z-Image-Turbo"                  # by model name
genmodelfit info image_z_image_turbo_int8 --json  # by workflow id, as JSON
```

`--modality`, `--limit`, `--limits` are also accepted but have no effect in this command (kept for older scripts).

## `genmodelfit update`

get a newer model list (users: `--from-release`; without options it rebuilds from upstream)

**Usage**

```text
genmodelfit update [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                   [--license-allowlist LIST|FILE] [--comfyui-version VER]
                   [--max-head-age-days DAYS] [--allow-uncensored-auto] [--offline-sources DIR]
                   [--skip-hf] [--skip-head] [--heads-only] [--fields-only] [--inspect-only]
                   [--offline] [--output PATH] [--from-release] [--force] [--allow-partial]
                   [--rate-limit-budget SEC]
```

- Get a newer model list.
- Users: run `genmodelfit update --from-release`.
- `--from-release` takes in the signed latest list published by this project (only db.json and db.json.sig are downloaded) and checks the signature; after that, the other commands use it instead of the bundled list (`--no-cache` goes back to the bundled one).
- Without options, update rebuilds the whole list from upstream (ComfyUI templates, the Hugging Face Hub, a HEAD request to every download URL).
- Rebuilding is for maintainers: it is slow (many requests; Hugging Face rate limits make it wait, see `--rate-limit-budget`).
- Maintainers can also redo only the HEAD checks (`--heads-only`), re-apply only the app fields and the hand-added models (`--fields-only`), or re-read only the file headers (`--inspect-only`).
- The policy options (`--comfyui-version`, `--max-head-age-days`, ...) only change the auto-recommendable count printed at the end.
- `--db` / `--no-cache` choose the list that `--heads-only`, `--fields-only` and `--inspect-only` start from.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` | the list fetched by update, else the bundled list | the list file (db.json) to start from (used only with `--heads-only`, `--fields-only` or `--inspect-only`) |
| `--no-cache` |  |  | start from the bundled list, not the one fetched by update (used only with `--heads-only`, `--fields-only` or `--inspect-only`) |
| `--offline-sources` | `DIR` |  | (for tests) files to use instead of upstream |
| `--skip-hf` |  |  | skip Hugging Face |
| `--skip-head` |  |  | skip the HEAD checks of URLs |
| `--heads-only` |  |  | keep the current list; redo only the URL HEAD checks and the latest ComfyUI release check |
| `--fields-only` |  |  | keep the current list; re-apply only the app fields (languages, runtime, description) and the hand-added models (data/manual.json). Only the model cards (for languages) and the hand-added models' files are fetched (`--offline`: no network, use the stored card languages) |
| `--inspect-only` |  |  | keep the current list; re-read only the weight file headers (precision), the templates' sampler settings and loader precision, and the repository precision (Hub API, config.json), and recheck repositories that returned 401 (Range requests; weights are not downloaded; results are cached) |
| `--offline` |  |  | with `--inspect-only` / `--fields-only`: no network; use only the cached headers and repository precision |
| `--output` | `PATH` | db.json in the cache | where to write the list (default: db.json in the cache) |
| `--from-release` |  |  | take in the signed list published by this project (nothing changes if the signature does not match). Writes to `--output` if given |
| `--force` |  |  | (`--from-release`) take it in even if it is older than the list taken in before |
| `--allow-partial` |  |  | write the list even if some sources (templates, Hugging Face) failed (sources.partial becomes true) |
| `--rate-limit-budget` | `SEC` | `900` | the most time to wait in total for Hugging Face rate limits (429), in seconds (default 900); stops when exceeded |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- Network access.

**Side effects**

- Network: GET / HEAD only.
- update reads the ComfyUI workflow templates and releases (GitHub), the Hugging Face Hub API and the model download URLs (HEAD, no download of weights); `--from-release` downloads only db.json and db.json.sig from this project's GitHub release.
- Nothing about this PC is sent (User-Agent: `genmodelfit/<version>`; no `~/.netrc` credentials).
- Writes the list to the cache (or `--output`).
- The cache is left untouched on failure.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error (network, bad signature, older list, ...; with `--json` the error is in "error") |
| 2 | Usage error |

`--vram`, `--ram` are also accepted but have no effect in this command (kept for older scripts).

## `genmodelfit download`

download the files of a model (consent, sha256, resume, ComfyUI folders)

**Usage**

```text
genmodelfit download [-h] [--lang {en,ja}] [--json] [--vram GB] [--ram GB] [--db PATH] [--no-cache]
                     [--license-allowlist LIST|FILE] [--comfyui-version VER]
                     [--max-head-age-days DAYS] [--allow-uncensored-auto] [--precision PREC]
                     [--budget GB] [--with-optional] [--list] [--output-dir DIR | --comfyui-dir DIR]
                     [--yes] [--uncensored] [--any-license] [--content {all,sfw,uncensored}] model
```

Download the files of a model for this PC (all files of one workflow), checking sha256.

- MODEL is a model name (as in the tables), a workflow id, or a part of either (like info).
- For a model name, the workflow is chosen like recommend: the highest precision that runs on this PC without offloading (good or better on the GPU); when none does, the best fit level, then the faster run mode, then the higher precision.
- The plan says which rule chose it.
- `--precision` downloads the workflow of that precision instead.
- `--budget` prefers the best workflow whose download stays under the limit; when none does, the download is refused.
- Files go to `<ComfyUI>/models/<folder>/<file>`, where the folder comes from the template (diffusion_models, text_encoders, vae, loras, checkpoints, ...).
- The place is the first of: `--comfyui-dir` (for a folder outside ComfyUI, `--output-dir`, with the same order of priority), the environment variable GENMODELFIT_COMFYUI_DIR, comfyui-dir saved with `genmodelfit config`, or a ComfyUI found automatically (the current folder, a ComfyUI running on 127.0.0.1 port 8188 or 8000, the ComfyUI Desktop and comfy-cli settings, usual places such as C:/ComfyUI).
- The plan always says which of these decided the place.
- When no place or several places are found, a terminal lets you pick one or type a path, while with `--yes`, `--json` or without a terminal nothing is downloaded (exit 1).
- `--list` does not ask a running ComfyUI, so it may show the place as "not decided" although the real download would find one (pass `--comfyui-dir` to see the exact plan).
- An is_default section in the ComfyUI folder's extra_model_paths.yaml is honored.
- Files whose ComfyUI folder is unknown go to other/; ComfyUI does not read other/, so move them to the right folder (diffusion_models, ...) before use.
- Before downloading, the files, each license and its URL (or the page to check when no license is stated), the total size and the free disk are shown and you are asked [y/N/c] (c changes the place; `--yes` skips the question).
- The first time a place found automatically is used, you are asked whether to save it for next time ([Y/n]; `genmodelfit config` shows or removes it).
- Only a ComfyUI folder can be saved: an `--output-dir` place must be given each time.
- Uncensored models and files with an unknown license need your consent: in a terminal the question names them (y/N); with `--yes`, `--json` or without a terminal pass `--uncensored` and/or `--any-license` (old forms: `--content all`, `--include-unknown-license`; consent flags must be written in full).
- When only the consent is missing, the plan ends with one command that adds it (genmodelfit never adds it by itself).
- Refused (no option lifts these): gated files (no login is used), too little disk (needs the download + 1 GB), names that would land outside the folder, hosts other than Hugging Face / GitHub.
- To use the files: reload ComfyUI in the browser if it was open, then open, from ComfyUI's template list, the template titled as in the first line of info (the id in [ ] is the template's file name).
- Models whose id starts with hf: have no ComfyUI template: their Hugging Face page (`https://huggingface.co/<owner>/<repo>`) says how to use them.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `model` |  |  | model name (the Model column), workflow id, or a part of either |
| `--precision` | `PREC` | chosen like recommend (see above) | download the workflow of the same model that has this precision (e.g. fp8 / bf16 / int8 / gguf-q8_0; the Prec column and info show which exist, and an unknown one is an error that lists them). Not the same as `--precision` of recommend etc. (which converts the estimate) |
| `--budget` | `GB` |  | the most to download in GB (files already there are not counted); the best workflow within it is chosen, and when none fits the download is refused |
| `--with-optional` |  |  | also download parts the template does not use by default (only on muted or bypassed nodes, etc.; #81) |
| `--list` |  |  | only list the files and the total (no download, no network) |
| `--output-dir` | `DIR` | none (decided as for `--comfyui-dir`) | a destination outside ComfyUI; files go to `<DIR>/<folder>/<file>` (folder: diffusion_models, vae, ...; the same layout as ComfyUI's models folder, so passing that models folder itself also works) |
| `--comfyui-dir` | `DIR` | GENMODELFIT_COMFYUI_DIR, then config, then found automatically | the ComfyUI folder; files go to `<DIR>/models/<folder>/<file>` (default: $GENMODELFIT_COMFYUI_DIR, then comfyui-dir in `genmodelfit config`, then found automatically) |
| `--yes`, `-y` |  |  | skip the consent question (required to download with `--json`) |
| `--uncensored` |  |  | also download uncensored models (old form `--content all`) |
| `--any-license`, `--include-unknown-license` |  |  | also download models with files of unknown license (check the terms yourself; old form `--include-unknown-license`) |
| `--content` | `{all,sfw,uncensored}` | `sfw` | which content marks the name search looks at: sfw (default) / uncensored (only uncensored) / all (both). uncensored and all also give consent to download uncensored models (`--uncensored` is the same as all; only all can be combined with `--uncensored`) |

- Cannot be used together: `--output-dir`, `--comfyui-dir`
- consent (asked y/N in a terminal; pass these with `--yes`, `--json` or no terminal; write them in full): `--uncensored`, `--any-license`, `--content`
- Also takes the [shared options](#options-shared-by-commands): `--json`, `--vram`, `--ram`, `--db`, `--no-cache`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- Network access to huggingface.co (or github.com) for the download.
- `--list` needs none.

**Side effects**

- Network: GET of the listed URLs only (https; every redirect must stay on Hugging Face / GitHub hosts).
- Nothing about this PC is sent (User-Agent: `genmodelfit/<version>`; no `~/.netrc` credentials, no tokens).
- Writes only inside the destination folder: `<file>.part` while downloading (kept on Ctrl+C or failure; the next run resumes it with HTTP Range), renamed to `<file>` after the sha256 matches.
- A sha256 mismatch deletes the file.
- Existing files whose sha256 matches are skipped; other existing files are never overwritten.
- When the place is not given (no option, environment variable or config), a ComfyUI running on this PC is asked where its folders are (GET http://127.0.0.1:8188 and :8000 /internal/folder_paths, 2 s timeout).
- Answering Y to "next time" writes comfyui-dir to the config file (`genmodelfit config path`).
- `--list`: no network access (a running ComfyUI is not asked) and nothing is written.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also `--list`, even when the plan is blocked: see "blocked") |
| 1 | Error: no such model / several matches, refused (see above), no consent, declined, network, sha256 mismatch (with `--json` the error is in "error") |
| 2 | Usage error |
| 130 | Interrupted (Ctrl+C); run again to resume |

**Examples for scripts and apps**

```sh
genmodelfit download "Z-Image-Turbo" --list --json
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI --yes --json
genmodelfit download image_z_image_turbo_int8 --output-dir D:/models --budget 15 --yes
```

JSON output (`--json`): [schema.md, "download"](schema.md#download)

**Examples**

```sh
genmodelfit download "Z-Image-Turbo" --list                    # 1. first see the files, licenses and sizes (downloads nothing)
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI  # 2. download into your ComfyUI folder (asks first)
genmodelfit download MODEL --uncensored --any-license --yes    # download without the question, also uncensored / unknown-license (check the terms yourself)
```

## `genmodelfit measure`

pick a model worth measuring and measure it on ComfyUI (started if needed; nothing is sent)

**Usage**

```text
genmodelfit measure [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] [--url URL]
                    [--comfyui-dir DIR] [--no-start] [--start-timeout SEC] [--yes]
                    [--delete-downloaded] [--free] [--resolution WxH] [--frames N] [--length SEC]
                    [--limit N] [--timeout SEC] [model]
```

- Measure a model on ComfyUI (started if needed) and save the result on this PC (nothing is sent).
- Without MODEL it lists models worth measuring: the ones that fit this PC's GPU (good or better on the GPU) and are not yet measured on this PC at the template's settings (other people's measurements of the same GPU do not remove a model: two agreeing sources let measurements set the level), with a known license, those already installed first, then the shorter time, then the smaller download; choose one by number (q quits).
- Models that need nodes the running ComfyUI lacks are left out.
- Once a model has that measurement, the list offers runs at other settings that help check the estimate's formula (#116): image resolution 512/768/1024/1536 px (text to image), video frames 17/33/49/81 at the template's size (text or image to video), music and sound length 10/30/60/120 s (text to audio); each only when it fits the GPU at that setting (good or better) and this PC has not measured it at that setting, at most 2 per model.
- Runs over about 300 s are not listed.
- Each run is one generation (a few minutes); running measure again picks the next one.
- A model or setting already measured on this PC comes back, after the others, when the ComfyUI version differs from the one it was measured with (the line says "remeasure: ComfyUI 0.19 → 0.20"; the version of a ComfyUI that is not running yet is read from its folder); with the same version it is not listed.
- MODEL is a model name, a workflow id or a part of either, chosen like download (only workflows with a ComfyUI template); `--resolution`, `--frames` and `--length` change the settings like bench.
- Before measuring one screen shows the model and precision, the settings (as in the template, or the ones changed), which ComfyUI and how it was found, whether the files are installed, a rough time and that nothing is sent, then asks [Y/n] (`--yes` skips it).
- The time is a rough guide: the median of measurements of the same model, else a rough value per type, scaled by the ratio of pixels, frames or length when the settings are changed.
- When the template cannot take a requested setting, or the ComfyUI lacks nodes it needs, it stops before downloading or generating.
- A model that is not installed is downloaded first, with the same questions and consent rules as download.
- At the end (also after a failure or Ctrl+C) it lists what it downloaded with the total size and asks [y/N] whether to delete it: only files this run newly downloaded, never ones that were already there; `--yes` or no terminal keeps them, `--delete-downloaded` deletes without asking.
- A ComfyUI started by genmodelfit is freed (/free: models unloaded, cached results dropped) before the run; yours only with `--free`.
- Afterwards it shows the result and where it saved it, and one line pointing to `genmodelfit share`, which sends it (it never asks whether to send).
- A run that could not be measured is saved as a failure record that share sends.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `model` |  |  | model name (the Model column), workflow id, or a part of either (omitted: list models worth measuring and choose one) |
| `--url` | `URL` | a ComfyUI running on this PC (127.0.0.1 ports 8188 and 8000) | URL of the ComfyUI to measure on (e.g. http://127.0.0.1:8188) |
| `--comfyui-dir` | `DIR` | decided like download | your ComfyUI folder (to see whether the model is installed, and where to download it) |
| `--no-start` |  |  | do not start the ComfyUI that was found when none is running (like GENMODELFIT_COMFYUI_START=0) |
| `--start-timeout` | `SEC` | `300` | the most time to wait for a started ComfyUI to be ready (seconds; default 300) |
| `--yes`, `-y` |  |  | do not ask before measuring (a model that is not installed is downloaded under the same rules as download `--yes`) |
| `--delete-downloaded` |  |  | after measuring, delete without asking the files this run downloaded to measure (only new ones) |
| `--free` |  |  | free the ComfyUI's models and cached results with /free before measuring (for your own running ComfyUI; a ComfyUI started by genmodelfit is always freed) |
| `--resolution` | `WxH` |  | measure at this width and height (e.g. 1536x1536; like bench; omitted: as in the template) |
| `--frames` | `N` |  | measure at this number of video frames (like bench; omitted: as in the template) |
| `--length` | `SEC` |  | measure at this audio length in seconds (like bench; omitted: as in the template) |
| `--limit`, `-n` | `N` |  | how many suggestions to list without a model (default 10) |
| `--timeout` | `SEC` | `1800` | the most time to wait (seconds > 0; default 1800) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--db`, `--no-cache`

**Preconditions**

- A GPU on this PC and a ComfyUI on it: `--url`, or one running on this PC (127.0.0.1 port 8188 or 8000).
- When none is running but a ComfyUI folder is found (like download), it is started in the background (portable: its python_embeded; manual or comfy-cli: its .venv/venv; Desktop: its .venv and the app's ComfyUI; on 127.0.0.1 and a free port, no browser), used, and stopped after measuring; the [Y/n] question covers it (`--no-start` or GENMODELFIT_COMFYUI_START=0 turns this off; `--start-timeout`, default 300 s).
- Otherwise it says how to start ComfyUI and exits 1. The GPU must be free: when other programs use more than 1.5 GiB (or 20%) of its memory, nothing is measured (on AMD / Intel / Apple GPUs, where nvidia-smi is missing, this is read from ComfyUI's /system_stats where possible, and the result's note says what could not be checked).
- Only one measure or share measures at a time.
- A started ComfyUI is stopped also on a failure, Ctrl+C or when the terminal is closed.

**Side effects**

- Network: asks 127.0.0.1 ports 8188 and 8000 whether ComfyUI runs and where its folders are (without `--url`), the ComfyUI it measures on (the node check and one job), the official template and its input images from GitHub when that ComfyUI lacks them, and the model files when it downloads them.
- Nothing about this PC is sent anywhere else.
- Writes the result to `<cache>/measurements/` (used when this PC is judged) and a submission draft (or a failure record) to `<cache>/submissions/` (what `genmodelfit share` sends).
- A started ComfyUI writes its output to `<cache>/comfyui-<date>.log`; `<cache>/measure.lock` while measuring.
- Deletes the model files this run downloaded only on y or `--delete-downloaded` (only inside the destination).

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also: nothing chosen, nothing to suggest, answered n to "Measure it?" or to the download, a result that only cannot be sent) |
| 1 | Error: no ComfyUI found, no GPU, the GPU is busy, another measure is running, the model has no template, nodes are missing, it was not downloaded, the run failed (out of memory, another error, or Ctrl+C during the run), no terminal and no `--yes` |
| 2 | Usage error |
| 130 | Ctrl+C at a question |

**Examples for scripts and apps**

```sh
genmodelfit measure --json
genmodelfit measure image_z_image_turbo_int8 --yes --json
genmodelfit measure "Z-Image-Turbo" --url http://127.0.0.1:8188
genmodelfit measure image_sdxl_simple --resolution 1536x1536 --yes --json
```

JSON output (`--json`): [schema.md, "measure"](schema.md#measure)

**Examples**

```sh
genmodelfit measure                              # pick a model worth measuring and measure it (sends nothing; genmodelfit share sends it)
genmodelfit measure "Z-Image-Turbo"              # measure a model given by name
genmodelfit measure --url http://127.0.0.1:8000  # measure on this ComfyUI (no search)
```

## `genmodelfit share`

measure installed models for a few minutes and send them, with your unsent measurements, as a GitHub pull request (`--issue`: send with the issue form)

**Usage**

```text
genmodelfit share [-h] [--lang {en,ja}] [--json] [--yes] [--download] [--delete-downloaded]
                  [--any-license] [--no-measure] [--budget SEC] [--dry-run] [--issue] [--print-url]
                  [--logout] [--all] [--mark-sent] [--url URL] [--comfyui-dir DIR] [--no-start]
                  [--start-timeout SEC] [--timeout SEC] [--db PATH] [--no-cache] [file]
```

- Measure for a few minutes and send the measurements as a GitHub pull request.
- Without FILE it finds ComfyUI like measure (`--url`, else one running on this PC, else it starts the one it finds and stops it afterwards), picks the suggestions of measure whose files are installed and that are not yet measured on this PC (results saved on this PC count, sent or not; other people's measurements do not; ones measured with another ComfyUI version come back after the others, as in measure) and whose nodes the ComfyUI has, until their rough times add up to `--budget` (300 s), and shows the plan with the unsent measurements and the total time (starting ComfyUI about 45 s, measuring, and downloading as an example at 100 Mbps).
- It asks once [Y/n], then measures one by one, freeing the ComfyUI before each run (a failure does not stop the rest; Ctrl+C goes on to sending what was measured).
- Models that are not installed are downloaded only with `--download` or y to one [y/N] question (asked first when nothing installed can be measured, else after measuring), with download's questions and consent rules; `--yes` alone never downloads.
- Then it shows every file to send (the whole content and the privacy checks) and asks how to send it: 1) a pull request (sign in to GitHub) 2) issues (no sign-in; the form opens in your browser, one issue per file) q) not now.
- `--yes`, `--json` or no terminal send a pull request; `--issue` `--yes` sends issues (the URLs are printed without a terminal).
- Before measuring it only checks a token it already has (GITHUB_TOKEN or GH_TOKEN, else the saved sign-in) and stops at once if it does not work; the device login in your browser (scope public_repo; saved for next time; `--logout` removes it) happens only when you choose a pull request.
- While it waits for the device login, keys work: r opens the page again, n gets a new code, q sends the same files as issues; an expired code asks whether to sign in again.
- With `--yes` and no terminal, a missing token stops it before measuring.
- The pull request comes from a fork of the repository: a branch from the merge base of the upstream main and the fork's main with the files under `data/contrib/<GPU>/` (titled "measurements: `<GPU>` (`<count>`)"), or your open share pull request for that GPU; files already there are skipped.
- If the pull request cannot be made, a terminal offers issues for the same files.
- Results are saved before sending and are never measured again: `genmodelfit share --no-measure` (or `--issue`) sends them later.
- Sent files are marked on this PC (`--all` includes them).
- At the end (also after a failure or Ctrl+C) it lists the model files it downloaded with the total size and asks [y/N] whether to delete them: only files this run newly downloaded and its leftover .part files, never ones that were already there; `--yes` or no terminal keeps them, and the flag `--delete-downloaded` deletes without asking.
- What could not be measured goes into one failure record file per run (names and numbers only, never error text).
- Without any ComfyUI it sends only the unsent measurements; with none it says how to start ComfyUI and exits 1 without asking.
- FILE sends that file only.
- share `--issue` without `--yes` is the older way: choose an unsent measurement and open its issue form.

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `file` |  |  | the submission JSON to send (omitted: measure, then send all unsent measurements) |
| `--yes`, `-y` |  |  | do not ask before measuring and sending (does not agree to downloading models) |
| `--download` |  |  | if time is left, also download and measure suggestions that are not installed (same rules as download; no question) |
| `--delete-downloaded` |  |  | at the end, delete without asking the files this run downloaded to measure (only new ones) |
| `--any-license` |  |  | with `--download` or without a terminal, also download models whose license is unknown (as in download; cannot be abbreviated) |
| `--no-measure` |  |  | do not measure; send only the unsent measurements |
| `--budget` | `SEC` | `300` | the most time to spend measuring (sum of the rough time guides; seconds) |
| `--dry-run` |  |  | only show the plan and what would be sent (no measuring, no sign-in, no network access to GitHub; it asks 127.0.0.1 whether ComfyUI runs) |
| `--issue` |  |  | send with the GitHub issue form instead, without signing in (with `--yes` after measuring; without it the older way) |
| `--print-url` |  |  | with `--issue`: do not open a browser; print only the URL of the form (for scripts) |
| `--logout` |  |  | remove the saved GitHub sign-in (token) |
| `--all` |  |  | also include measurements already marked as sent |
| `--mark-sent` |  |  | with `--issue` `--json`, also mark it as sent (for apps that open the form themselves; by default `--json` only previews) |
| `--url` | `URL` | a ComfyUI running on this PC (127.0.0.1 ports 8188 and 8000) | URL of the ComfyUI to measure on (like measure) |
| `--comfyui-dir` | `DIR` | decided like download | your ComfyUI folder (to see whether models are installed, and where to download; like measure) |
| `--no-start` |  |  | do not start the ComfyUI that was found when none is running (like GENMODELFIT_COMFYUI_START=0) |
| `--start-timeout` | `SEC` | `300` | the most time to wait for a started ComfyUI to be ready (seconds; default 300) |
| `--timeout` | `SEC` | `1800` | the most time to wait for one run (seconds; default 1800) |
| `--db` | `PATH` | choosing: the list fetched by update, else the bundled list; checks: the bundled list | use this model list file (db.json) (to choose what to measure and to check before sending) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--no-cache`

**Preconditions**

- A GitHub account.
- To measure: a GPU and ComfyUI (as for measure).

**Side effects**

- Network: like measure while measuring and downloading.
- GitHub (github.com and api.github.com): before measuring only a check of a token you already have (GET /user); the sign-in, fork, branch, files and pull request only after you agree to send.
- `--dry-run` only asks 127.0.0.1 whether ComfyUI runs; `--issue` makes no network access to GitHub.
- Writes measurements and failure records like measure, the sent marks to `<cache>/submissions_shared.json` and the sign-in token to `<config>/github_token` (readable only by you).
- Deletes the model files this run downloaded only on y or `--delete-downloaded` (only inside the destination).

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also: answered n, nothing to measure or send, results that only cannot be sent) |
| 1 | Error: cannot sign in, no ComfyUI and nothing to send, a measurement failed, a file was refused (privacy check NG or check errors), GitHub refused the upload (the results stay saved), `--json` or no terminal without `--yes` |
| 2 | Usage error |
| 130 | Ctrl+C (at a question; while measuring it first goes on to sending what was measured) |

**Examples for scripts and apps**

```sh
genmodelfit share --dry-run --json
genmodelfit share --no-measure --yes --json
genmodelfit share --issue --yes
genmodelfit share --issue <cache>/submissions/<GPU>/<date>-<sha8>.json --print-url
```

`GITHUB_TOKEN=<token>` `genmodelfit share --yes --json`

JSON output (`--json`): [schema.md, "share"](schema.md#share)

`--issue`: { listed, submissions? | file, ok, refused, checklist, report, share, opened, copied, marked_shared }

**Examples**

```sh
genmodelfit share               # measure for a few minutes and send a pull request
genmodelfit share --dry-run     # only show the plan and what would be sent
genmodelfit share --no-measure  # send only the unsent measurements
genmodelfit share --issue       # send with the issue form, no sign-in
```

## `genmodelfit config`

show or change settings (download's ComfyUI folder, display language)

**Usage**

```text
genmodelfit config [-h] [--lang {en,ja}] [--json] {show,set,unset,path} ...
```

Show or change the settings of the genmodelfit command line (the Python API never reads them).

Keys:

```text
comfyui-dir  the ComfyUI folder download uses (after --comfyui-dir / --output-dir and
             GENMODELFIT_COMFYUI_DIR). `set` checks that the folder looks like ComfyUI
             (4 of main.py, comfy, nodes.py, comfy_extras, comfy_api, or a ComfyUI Desktop
             folder: models, input, user, output, custom_nodes). A folder outside ComfyUI
             (download --output-dir) cannot be saved: pass --output-dir each time.
lang         the display language, en or ja (--lang and GENMODELFIT_LANG win).
```

Without an action, shows each setting and where it comes from (option, environment variable, config file, default).

- Also takes the [shared options](#options-shared-by-commands): `--json`

**Preconditions**

- None.

**Side effects**

- No network access.
- set / unset write the config file (config.json in the user config folder of genmodelfit; GENMODELFIT_CONFIG_DIR changes the folder).
- show and path write nothing.
- A broken file is not read (a warning is shown) and set / unset do not overwrite it.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: unknown key, invalid value, broken or unwritable file |
| 2 | Usage error |

**Examples for scripts and apps**

```sh
genmodelfit config --json
genmodelfit config set comfyui-dir D:/ComfyUI
genmodelfit config unset comfyui-dir
genmodelfit config path
```

JSON output (`--json`): [schema.md, "config"](schema.md#config)

**Subcommands**: `genmodelfit config show`, `genmodelfit config set`, `genmodelfit config unset`, `genmodelfit config path`

### `genmodelfit config show`

current settings and where they come from (the default when no subcommand is given)

**Usage**

```text
genmodelfit config show [-h] [--lang {en,ja}] [--json]
```

This command has no options of its own.

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit config set`

save a value

**Usage**

```text
genmodelfit config set [-h] [--lang {en,ja}] [--json] key value
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `key` |  |  | comfyui-dir or lang |
| `value` |  |  | for comfyui-dir: the path of the ComfyUI folder; for lang: en or ja |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit config unset`

remove a value

**Usage**

```text
genmodelfit config unset [-h] [--lang {en,ja}] [--json] key
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `key` |  |  | comfyui-dir or lang |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit config path`

where the config file is

**Usage**

```text
genmodelfit config path [-h] [--lang {en,ja}] [--json]
```

This command has no options of its own.

- Also takes the [shared options](#options-shared-by-commands): `--json`

## `genmodelfit gate`

summary of which models can be auto-recommended

**Usage**

```text
genmodelfit gate [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache]
                 [--license-allowlist LIST|FILE] [--comfyui-version VER] [--max-head-age-days DAYS]
                 [--allow-uncensored-auto] [--modality {image,video,music,sfx,tts,3d}]
                 [--only-auto | --only-held]
```

Count how many models in the list can be recommended automatically (Verified, "auto" in JSON) and why the others are held. The checks are the same as in info (one model) and in the "auto" / held_reasons fields of recommend `--json`.

- How to read the output: the first line is the ComfyUI version the checks used, and the second the number of models, how many are auto and how many held.
- The table has one row per check: "failing" is how many models fail that check, "first-failing" how many models fail it as their first failed check (the check to fix first).
- `--only-auto` / `--only-held` also list the model ids (held ones with the first reason).
- The checks and the allowlist: [gate.md](gate.md)

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--modality`, `-m` | `{image,video,music,sfx,tts,3d}` |  | count only this type (one type) |
| `--only-auto` |  |  | also list the ids of auto-recommendable models |
| `--only-held` |  |  | also list the ids of held models and why |

- Cannot be used together: `--only-auto`, `--only-held`
- Also takes the [shared options](#options-shared-by-commands): `--json`, `--db`, `--no-cache`, `--license-allowlist`, `--comfyui-version`, `--max-head-age-days`, `--allow-uncensored-auto`

**Preconditions**

- Nothing to prepare.
- The PC is not judged.
- The bundled model list is used unless `genmodelfit update` stored a newer one (`--no-cache`).

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: an empty `--license-allowlist` ("," or an empty file) (e.g. a broken `--db` file; with `--json` the error is in "error") |
| 2 | Usage error (unknown option or value) |

**Examples for scripts and apps**

```sh
genmodelfit gate
genmodelfit gate -m video --only-held
genmodelfit gate --comfyui-version 0.40.0 --json
```

JSON output (`--json`): [schema.md, "gate"](schema.md#gate)

`--vram`, `--ram` are also accepted but have no effect in this command (kept for older scripts).

## `genmodelfit diff`

differences between two model lists (db.json): added / removed, licenses, auto-recommendation, HEAD failures

**Usage**

```text
genmodelfit diff [-h] [--lang {en,ja}] [--json] [--all] [--comfyui-version VER] [--limit N] [old]
                 [new]
```

- Compare two model lists (db.json) and show what changed for users.
- Shown are added / removed models and, for each changed model (name (type) — id), the changes below.
- Changes of a model: added / removed / changed files (with their license and source), license (the permissive side is marked with !), newly uncensored, runtime / use, auto-recommendation, prompt / speech languages, size, precision, review status, and the names of any other changed fields ("other changes").
- Changed models are in three sections, each cut by `--limit` separately: files / license / uncensored / runtime and use first, then auto-recommendation changes (ids always listed), then license provenance, languages, size, review marks and the rest; within a section the most important change comes first, so `--limit` never hides it; the same low-importance change on many models (e.g. a review mark added to all) is one line with a count.
- Every changed field is shown somewhere (or counted for `--all`).
- The raw JSON is compared, so unknown keys count (as in merge-check).

- With no arguments, compares the bundled list with the list fetched by `update` (the cache).
- With one argument, compares the bundled list with that file.
- With two, compares the two files.
- `--all` adds the maintenance view (the content of the weekly PR): all failed HEAD checks (not only new ones), 401 repos, the changed numbers in `sources`, models where only popularity numbers, dates or HEAD results changed, and the ids of collapsed lines.
- With `--all` there is no line limit unless `-n` is given (without `--all` the default is `-n` 20 per section).

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `old` |  |  | the old list. Omitted: compare the bundled list with the fetched list (the update cache). With one file: compare the bundled list with that file |
| `new` |  |  | the new list |
| `--all` |  |  | also show the maintenance view (older HEAD failures, 401 repositories, sources numbers, popularity-only changes) |
| `--comfyui-version` | `VER` |  | ComfyUI version used to compare auto-recommendation (default: recorded in the new list) |
| `--limit`, `-n` | `N` | 20 (all with `--all`) | max lines per section (default 20; all with `--all`) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

**Preconditions**

- No arguments: a fetched list in the cache (run `genmodelfit update --from-release` first; without it, a short how-to is printed and the exit code is 0).
- With arguments: one or two db.json files.
- A warning is printed when the new list is older (generated_at) than the old one.

**Side effects**

- None - read-only.
- No network access.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (also when there are differences, and when there is no fetched list yet) |
| 1 | Error (a file cannot be read or has another schema; with `--json` the error is in "error") |
| 2 | Usage error |

**Examples for scripts and apps**

```sh
genmodelfit diff                          # bundled list vs the fetched list
genmodelfit diff new.json                 # bundled list vs new.json
genmodelfit diff old.json new.json --json
genmodelfit diff old.json new.json --all  # maintenance view
```

The auto-recommendation is compared with one policy for both lists: the ComfyUI version recorded in the new list (or `--comfyui-version`) and no HEAD age limit (HEAD failures still count).

JSON output (`--json`): [schema.md, "diff"](schema.md#diff)

With no fetched list: the same keys, empty, plus "note".

## `genmodelfit review`

list, approve or reject models waiting for review (new entries, license changes, ...; for maintainers)

**Usage**

```text
genmodelfit review [-h] [--lang {en,ja}] {list,approve,reject} ...
```

- List, approve or reject models waiting for review (for maintainers).
- update marks an entry pending (review.status = pending) when it differs from the previous list (new / license_permissive / need_decreased / uncensored_new / files_changed).
- Pending entries are not auto-recommended until approved (gate check: reviewed).

```text
list     [--json] [--all] [--db PATH]          entries waiting for review and why
approve  <id> --evidence URL [--evidence URL…] [--note TEXT] [--by NAME] --db PATH
         refused without evidence; rewrites the list file in place
reject   <id> --note TEXT --db PATH [--rejected FILE] [--hide-name]
         removes the entry and records it in review_rejected.txt (update does not add it again)
```

**Side effects**

- No network access.
- list is read-only; approve / reject rewrite the `--db` file (reject also appends to the rejected file).

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error (no such id, no evidence, not pending, unreadable `--db`; with `--json` the error is in "error") |
| 2 | Usage error |

**Examples for scripts and apps**

```sh
genmodelfit review list --json --db src/genmodelfit/data/db.json
genmodelfit review approve hf:org/model --db src/genmodelfit/data/db.json \
    --evidence https://huggingface.co/org/model/blob/main/LICENSE
genmodelfit review reject hf:org/model --db src/genmodelfit/data/db.json --note "non-commercial only"
```

Details: [schema.md, "review"](schema.md#review), [release.md, "weekly skill"](release.md#weekly-skill-genmodelfit-weekly-42)

**Subcommands**: `genmodelfit review list`, `genmodelfit review approve`, `genmodelfit review reject`

### `genmodelfit review list`

models waiting for review and why

**Usage**

```text
genmodelfit review list [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] [--all]
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list file (default: the cache, else the bundled list) |
| `--all` |  |  | also show approved models |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--no-cache`

### `genmodelfit review approve`

approve with evidence (primary-source URLs); rewrites the list file

**Usage**

```text
genmodelfit review approve [-h] [--lang {en,ja}] [--evidence URL] [--note NOTE] [--by BY] --db PATH
                           [--json] id
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `id` |  |  | model id |
| `--evidence` | `URL` |  | evidence URL (https; one or more; repeat to add) |
| `--note` | `NOTE` |  | a note on what was checked |
| `--by` | `BY` | `review` | who or what checked it (decided_by; default review) |
| `--db` | `PATH` |  | the list file to rewrite (e.g. src/genmodelfit/data/db.json) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit review reject`

remove from the list and record it so that update does not add it again

**Usage**

```text
genmodelfit review reject [-h] [--lang {en,ja}] --note NOTE --db PATH [--rejected PATH]
                          [--hide-name] [--json] id
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `id` |  |  | model id |
| `--note` | `NOTE` |  | why it is removed |
| `--db` | `PATH` |  | the list file to rewrite (e.g. src/genmodelfit/data/db.json) |
| `--rejected` | `PATH` |  | the record file (default: review_rejected.txt in the same folder as `--db`) |
| `--hide-name` |  |  | record only the sha256, not the id (when removing for safety reasons; like the existing exclusions) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

## `genmodelfit bench`

for maintainers: measure on a running ComfyUI (usually: measure)

**Usage**

```text
genmodelfit bench [-h] [--lang {en,ja}] [--json] [--db PATH] [--no-cache] --url URL
                  [--workflow PATH] [--width PX] [--height PX] [--frames N] [--length SEC]
                  [--steps N] [--output PATH] [--save-failed] [--save-nonstandard]
                  [--resolution WxH] [--set NAME=VALUE] [--free] [--timeout SEC] [--force]
                  [--source SOURCE] [--note NOTE] [--submission PATH] [--share] template
```

- Measure one model on a ComfyUI that is running, and save the result (peak VRAM, RAM, time).
- TEMPLATE is the exact workflow id of a ComfyUI template, such as image_z_image_turbo_int8 (the id in [ ] on the first line of info).
- Model names and parts of ids are not accepted, and hf: models have no template (pass an API-format workflow with `--workflow` instead).
- Your saved results are used when this PC is judged.
- bench is for maintainers: `genmodelfit measure` does the same without the template id (it suggests models and finds ComfyUI).
- `--share` prepares a submission and opens GitHub's measurement form in your browser: sign in to GitHub (an account is needed) and press "Create" (like `genmodelfit share --issue`).

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `template` |  |  | the exact workflow id of a ComfyUI template (the id in [ ] on the first line of info) |
| `--url` | `URL` |  | URL of the ComfyUI to measure on (e.g. http://127.0.0.1:8188; no default) |
| `--workflow` | `PATH` |  | workflow in API format (default: built from the template) |
| `--width` | `PX` | the template's value | width in pixels (omitted: as in the template) |
| `--height` | `PX` | the template's value | height in pixels (omitted: as in the template) |
| `--frames` | `N` | the template's value | number of video frames (omitted: as in the template) |
| `--length` | `SEC` | the template's value | audio length in seconds (omitted: as in the template) |
| `--steps` | `N` | the template's value | number of sampler steps (omitted: as in the template) |
| `--output` | `PATH` | measurements/ in the cache | where to write the result JSON (failed and non-standard results are written here too) |
| `--save-failed` |  |  | also save failed results (OOM etc.) to the cache (default: show only) |
| `--save-nonstandard` |  |  | also save results under non-standard conditions (`--workflow`, `--set`, partial runs, ...) to the cache |
| `--resolution` | `WxH` |  | width and height (e.g. 768x768); same as `--width`/`--height` |
| `--set` | `NAME=VALUE` |  | change an input by name (e.g. octree_resolution=192) |
| `--free` |  |  | free that ComfyUI's models with /free before starting (for a ComfyUI only you use) |
| `--timeout` | `SEC` | `1800` | the most time to wait (seconds > 0; default 1800) |
| `--force` |  |  | measure even when the GPU is in use or its free memory cannot be checked |
| `--source` | `SOURCE` |  | who measured it where (goes into source) |
| `--note` | `NOTE` |  | text to add to the result's note |
| `--submission` | `PATH` |  | also write a submission JSON (contrib_schema 3). For a folder: `<folder>/<GPU>/<date>-<sha8>.json` (pass your fork's data/contrib to open a PR as is). Nothing is sent ([CONTRIBUTING.md](../CONTRIBUTING.md)) |
| `--share` |  |  | write the submission JSON (to the cache's submissions folder without `--submission`), show it with the checks, and open GitHub's measurement form (like `genmodelfit share --issue`; without a terminal it prints the URL). Sending needs a GitHub account; genmodelfit itself sends nothing ([CONTRIBUTING.md](../CONTRIBUTING.md)) |

- Also takes the [shared options](#options-shared-by-commands): `--json`, `--db`, `--no-cache`

**Preconditions**

- A ComfyUI running at `--url` with all the model files of TEMPLATE in its models folder (use `genmodelfit download` first).
- The GPU must be free: when another program uses it, or its free memory cannot be checked (nvidia-smi), bench refuses unless `--force`.
- Measure on a ComfyUI only you use; `--free` makes bench unload that ComfyUI's models first.

**Side effects**

- Network: only the ComfyUI at `--url` (it sends one job and reads the results), plus the official template file from GitHub when that ComfyUI does not have it.
- Nothing about this PC is sent elsewhere; `--share` only opens your browser at the form (or prints its URL).
- Writes the result JSON to `<cache>/measurements/` (or `--output`).
- Failed results and results under non-standard conditions are only shown unless `--save-failed` / `--save-nonstandard` / `--output`.
- `--submission` / `--share` also write a submission JSON.
- Settings you leave out (`--width`, `--height`, `--frames`, `--length`, `--steps`) stay as in the template.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: ComfyUI cannot be reached, the GPU is busy (without `--force`), the template or a file is missing, the run failed (out of memory or another error), timeout, Ctrl+C (our job is cancelled first) |
| 2 | Usage error |

**Examples for scripts and apps**

```sh
genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188
genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188 --share
genmodelfit bench video_wan2_2_5B_ti2v --url http://127.0.0.1:8188 --resolution 640x640 --json
```

JSON output (`--json`): [schema.md, "bench"](schema.md#bench)

`--vram`, `--ram` are also accepted but have no effect in this command (kept for older scripts).

## `genmodelfit contrib`

for maintainers: make, validate and aggregate measurement submissions (to send one: measure and share)

**Usage**

```text
genmodelfit contrib [-h] [--lang {en,ja}] {make,show,share,validate,intake,aggregate} ...
```

Make, preview, validate and aggregate measurement submissions (contrib_schema 3; for maintainers. To send a measurement use `genmodelfit measure` and `genmodelfit share`). genmodelfit sends nothing (data is shared only when you press "Create" in the GitHub form or open a Pull Request yourself; [CONTRIBUTING.md](../CONTRIBUTING.md)).

```text
make      --from <bench result>  → data/contrib/<GPU>/<date>-<sha8>.json
show      <file>                 what will be shared, and the privacy checklist (for consent)
share     [file]                 the same as genmodelfit share --issue (kept for older scripts)
intake    <event.json> --out DIR check a measurement issue on GitHub and write the files for its PR
                                 (used by measurement-intake.yml)
validate  <files|dirs>...        check shape, value ranges, personal information, duplicates, file names
aggregate [dir] [--output F] [--report] [--threshold 25] [--fail-on-breach]
          group by model, settings and GPU, drop outliers by median and MAD, check the calibration error
aggregate [dir] --check
          count the submissions not in the bundled aggregate yet (it is rebuilt for each release)
```

**Side effects**

- No network access.
- make writes the submission files; aggregate writes only with `--output`; intake writes to `--out`.
- share is `genmodelfit share --issue` (opens a browser in a terminal; with `--json`, `--print-url` or without a terminal it only prints the URL; writes the sent marks).

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Success (validate: every file passed; show: nothing to fix) |
| 1 | Error (an unreadable file, an unknown shape, ...) |
| 1 | validate: a file failed the mechanical checks, or has a warning with `--strict` (notices for the maintainer who merges never fail) |
| 1 | show: something to fix |
| 1 | share: a privacy check is NG or the checks have errors (cannot share) |
| 1 | aggregate: no such folder, or an empty result with `--output` (unless `--allow-empty`) |
| 1 | aggregate `--check`: submissions not in the bundled aggregate yet, or re-aggregating it differs |
| 2 | Usage error |
| 3 | aggregate `--report` `--fail-on-breach`: an error exceeded the threshold |

**Examples for scripts and apps**

```sh
genmodelfit bench image_sdxl_simple --url http://127.0.0.1:8188 --submission data/contrib/
genmodelfit contrib show data/contrib/<GPU>/<date>-<sha8>.json
genmodelfit contrib share data/contrib/<GPU>/<date>-<sha8>.json --json
genmodelfit contrib validate data/contrib --json
genmodelfit contrib aggregate --report --json --fail-on-breach
```

Details: [schema.md, "contrib"](schema.md#contrib)

**Subcommands**: `genmodelfit contrib make`, `genmodelfit contrib show`, `genmodelfit contrib share`, `genmodelfit contrib validate`, `genmodelfit contrib intake`, `genmodelfit contrib aggregate`

### `genmodelfit contrib make`

make a submission JSON from a result saved by bench

**Usage**

```text
genmodelfit contrib make [-h] [--lang {en,ja}] [--json] [--db PATH] --from FILE [--output PATH]
                         [--no-pc-info]
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `--from` | `FILE` |  | the bench result JSON |
| `--output` | `PATH` | `data/contrib` | where to write (for a folder: `<GPU>/<date>-<sha8>.json`; default data/contrib) |
| `--no-pc-info` |  |  | do not include this PC's OS family, RAM and GPU generation (when ComfyUI ran on another PC) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit contrib show`

show what will be shared and the privacy checklist

**Usage**

```text
genmodelfit contrib show [-h] [--lang {en,ja}] [--json] [--db PATH] file
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `file` |  |  | the submission JSON |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit contrib share`

the same as `genmodelfit share --issue` (kept for older scripts)

**Usage**

```text
genmodelfit contrib share [-h] [--lang {en,ja}] [--json] [--db PATH] [--print-url] [--all]
                          [--mark-sent] [file]
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `file` |  |  | the submission JSON to send (omitted: list unsent measurements and choose one) |
| `--print-url` |  |  | do not open a browser; print only the URL of the form (for scripts) |
| `--all` |  |  | also list measurements already marked as sent |
| `--mark-sent` |  |  | with `--json`, also mark it as sent |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit contrib validate`

check shape, values, personal information and duplicates (also used in CI)

**Usage**

```text
genmodelfit contrib validate [-h] [--lang {en,ja}] [--json] [--db PATH] [--strict]
                             [--no-filename-check] [--markdown] FILE|DIR [FILE|DIR ...]
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `FILE\|DIR` |  |  | submission JSON files (for a folder: every \*.json under it) |
| `--strict` |  |  | treat warnings as failures too |
| `--no-filename-check` |  |  | do not check the file name (`<GPU>/<date>-<sha8>.json`) |
| `--markdown` |  |  | output a Markdown table (for the CI summary) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit contrib intake`

check a measurement issue on GitHub and write the files for its PR (used by measurement-intake.yml)

**Usage**

```text
genmodelfit contrib intake [-h] [--lang {en,ja}] [--json] [--db PATH] [--existing DIR] --out DIR
                           EVENT_JSON
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `EVENT_JSON` |  |  | the issues event file |
| `--existing` | `DIR` | `data/contrib` | the folder searched for the same submission (default data/contrib) |
| `--out` | `DIR` |  | the folder to write the result files to |

- Also takes the [shared options](#options-shared-by-commands): `--json`

### `genmodelfit contrib aggregate`

aggregate by model, settings and GPU, and check the accuracy

**Usage**

```text
genmodelfit contrib aggregate [-h] [--lang {en,ja}] [--json] [--db PATH] [--output FILE] [--report]
                              [--threshold PCT] [--fail-on-breach] [--allow-empty]
                              [--allow-file-sources] [--check] [dir]
```

**Options and arguments**

| Option / argument | Value | Default | Description |
|---|---|---|---|
| `--db` | `PATH` |  | the list used to check model ids (default: the bundled list; the cache is not used) |
| `dir` |  | `data/contrib` | the submissions folder (default data/contrib) |
| `--output` | `FILE` |  | write the result (to bundle it: src/genmodelfit/data/measurements_community.json) |
| `--report` |  |  | redo the calibration and show the error table per type and the threshold check |
| `--threshold` | `PCT` | `25.0` | error threshold (%, default 25) |
| `--fail-on-breach` |  |  | with `--report`, exit with code 3 when the threshold is exceeded |
| `--allow-empty` |  |  | write to `--output` even if the result is empty (by default an empty result does not overwrite) |
| `--allow-file-sources` |  |  | write to `--output` even without git history (sources counted per file). For tests and local experiments only; never for a release |
| `--check` |  |  | write nothing; count the submissions not in the bundled aggregate yet (exit code 1 if any) |

- Also takes the [shared options](#options-shared-by-commands): `--json`

## About this page

The command sections are generated from the program's argument parser by `tools/cli_reference.py`, so they always
match the program. Do not edit them by hand; change the help texts in `src/genmodelfit/cli.py` and run
`python tools/cli_reference.py` (`tests/test_commands_doc.py` fails when this page is out of date).

The environment variables are read in `src/genmodelfit/i18n.py` (`GENMODELFIT_LANG`), `cli.py` (`GENMODELFIT_COMFYUI_DIR`), `comfyfind.py` (`GENMODELFIT_COMFYUI_SEARCH`), `comfystart.py` (`GENMODELFIT_COMFYUI_START`), `config.py`
(`GENMODELFIT_CONFIG_DIR`), `db.py` (`GENMODELFIT_CACHE_DIR`), `signing.py` (`GENMODELFIT_SIGNING_KEY`) and `github.py`
(`GITHUB_TOKEN`, `GH_TOKEN`, `GENMODELFIT_GH_CLIENT_ID`).
