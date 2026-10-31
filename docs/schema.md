# JSON schema

**English** · [日本語](schema.ja.md)

This page defines the JSON that genmodelfit prints with `--json` and returns from its Python API, and the format of the list file `db.json`.
It is for app developers who read that output, and for contributors who change the schema.

**Contents**

- [Common envelope](#common-envelope)
- [Side effects](#side-effects)
- [System](#system)
- [Data status](#data-status)
- [recommend / fit / search](#recommend--fit--search)
  - [Models](#models) · [Uses](#uses) · [Fit](#fit-one-item-of-models) · [Score](#score)
- [Estimate](#estimate)
- [Gate object](#gate-object)
- [gate](#gate)
- [diff](#diff)
- [review](#review)
- [info](#info)
  - [ModelEntry](#modelentry) · [Measurement](#measurement)
- [bench](#bench)
- [measure](#measure)
- [share](#share)
- [contrib](#contrib)
- [download](#download)
- [config](#config)
- [Python API (stable)](#python-api-stable)
- [Database file (`db.json`)](#database-file-dbjson)
- [update](#update)

`schema_version`: **1**. The source of truth is `src/genmodelfit/models.py`; this page must match it.
When you change the shape, update `SCHEMA_VERSION` and this page.

**Units**: every `*_gb` value is **GiB** (2^30 bytes): file sizes are bytes / 2^30, VRAM is nvidia-smi MiB / 1024,
RAM is psutil bytes / 2^30. Measurements are assumed to be GiB too (`unit` in `measurements.json`).
The "GB" in CLI output is also GiB.

## Common envelope

Every command with `--json` prints one object:

| key | type | meaning |
|---|---|---|
| `genmodelfit` | str | tool version |
| `command` | str | `system` / `recommend` / `fit` / `search` / `types` / `info` / `update` / `bench` / `measure` / `share` / `gate` / `download` / `diff` / `review` / `contrib` / `config` |
| `system` | System | the PC that was judged (after `--vram` / `--ram` overrides). Not in `types` / `gate` / `update` / `bench` / `share` / `diff` / `review` / `contrib` / `config` |
| `database` | object | `origin` (`bundled` / `cache` / `path`), `generated_at`, `models`, `measurements` (counts). In `system` also `status` (DataStatus, below). Not in `bench` / `share` / `diff` / `contrib` / `config` / `review approve` / `review reject` / `update --from-release` |
| `error` | str | only on failure (exit code 1) |
| `candidates` | object[] | only when `info` matched several models (see info) |

## Side effects

The same as the SIDE EFFECTS of each command's `--help` and [README, "Privacy"](../README.md#privacy):

| command | network | writes |
|---|---|---|
| `system`, `recommend`, `fit`, `search`, `types`, `info`, `gate`, `diff` (and the Python API) | none | none |
| `update` | GET / HEAD to GitHub (ComfyUI templates, releases), the Hugging Face Hub API and model URLs (HEAD; for main weight files also a ranged GET of the file header only, never the weights) | the cache `db.json` (or `--output`), `file_headers.json` in the cache |
| `update --fields-only` | GET of the Hugging Face Hub API for the cards whose `language` decides `languages` (the main files' repos and their `license_from`); for the hand-added models also HEAD, ranged GET of the file header and the upstream cards' API; nothing with `--offline` | the cache `db.json` (or `--output`), `file_headers.json` in the cache |
| `update --inspect-only` | ranged GET of the headers of the main weight files on Hugging Face, GET of the ComfyUI template JSONs; nothing with `--offline` | the cache `db.json` (or `--output`), `file_headers.json` in the cache |
| `update --from-release` | GET `db.json` and `db.json.sig` from this project's `data-latest` release | the cache `db.json` (or `--output`), `release_state.json` |
| `measure` | without `--url`, `GET http://127.0.0.1:8188/internal/folder_paths` and the same on port 8000 (2 s timeout, no redirects, no proxy); then like `bench` on that ComfyUI; when you agree to download a missing model, like `download`. Nothing with only the suggestions list and no ComfyUI running. (0.13.0) When none runs and an install is found, it starts ComfyUI as a separate process on 127.0.0.1 and a free port and asks it `GET /system_stats` until ready, then `GET /object_info` and the template to check the nodes; it stops that process afterwards | like `bench`: the result in `<cache>/measurements/`, the submission draft in `<cache>/submissions/<GPU>/<date>-<sha8>.json`; the model files when downloaded; a failure record in `<cache>/submissions/` when a run fails (0.13.0); a started ComfyUI's output in `<cache>/comfyui-<date>.log`; `<cache>/measure.lock` while measuring |
| `share` | (0.13.0) like `measure` while measuring and downloading. GitHub (https to github.com and api.github.com only): before measuring, `GET /user` with a token you already have (`GITHUB_TOKEN`, `GH_TOKEN` or the saved one) to check it; the device login only when you choose a pull request; after you agree to send, the fork, a branch, the files (Contents API) and the pull request. `--dry-run` (which only asks 127.0.0.1 whether ComfyUI runs) and `--issue` make no network access to GitHub (`--issue` opens the browser at the GitHub form in a terminal; the content is sent only when you press "Create") | like `measure`; `<cache>/submissions_shared.json` (the sent marks); `<config>/github_token` (the sign-in token and its expiry; readable only by you; `--logout` removes it); `--issue` may put the content on the clipboard |
| `bench` | only the ComfyUI `--url` (+ official template files on GitHub if ComfyUI lacks them); `--share` adds no network access (see `share` below) | a measurement JSON in the cache (or `--output`); with `--submission` also the submission JSON; with `--share` and no `--submission`, `<cache>/submissions/<GPU>/<date>-<sha8>.json` |
| `contrib` | none (genmodelfit never sends a submission); `share` is `genmodelfit share --issue` | `make`: the submission JSON; `aggregate --output`: the community file; `intake`: the files in `--out`; `share`: the sent marks; `show` / `validate` / `aggregate --check`: none |
| `review` | none | `list`: none; `approve` / `reject`: the `--db` file in place; `reject` also appends to `review_rejected.txt` (next to `--db` unless `--rejected`) |
| `python -m genmodelfit.weekly merge-check` | none | none |
| `python -m genmodelfit.review publish SRC DST` | none | DST (the list without pending entries; data.yml signs it) |
| `python -m genmodelfit.review apply DECISIONS --db PATH` | none | the `--db` file; rejects also append to `review_rejected.txt` |
| `download` | GET of the model URLs in the list (https; redirects only to Hugging Face / GitHub hosts). When the place is not given (no `--comfyui-dir` / `--output-dir`, environment variable or config), also `GET http://127.0.0.1:8188/internal/folder_paths` and the same on port 8000 (a ComfyUI running on this PC; 2 s timeout, no redirects, no proxy). None with `--list` | the files under the destination (`<file>.part` while downloading); `comfyui-dir` in the config file when you answer Y to "next time"; nothing with `--list` |
| `config` | none | `set` / `unset`: the config file (`config path`); `show` / `path`: none |

No telemetry; nothing about the PC or the user is sent. HTTP uses `fetch.make_session()`: User-Agent
`genmodelfit/<version> (+https://github.com/laxshmate/genmodelfit)`, proxy / CA environment variables honored, `~/.netrc`
credentials never attached (tests/test_privacy.py).

## System

| key | type | meaning |
|---|---|---|
| `os`, `cpu` | str | |
| `cpu_cores` | int | logical threads |
| `ram_total_gb`, `ram_available_gb` | float | GiB. 0 = unknown (0.11.0: memory could not be read; the estimate does not refuse by main memory) |
| `gpus` | Gpu[] | NVIDIA GPUs confirmed by `nvidia-smi`, and (0.7.0, #22) AMD / Intel / Apple GPUs that the support table in [hardware.md](hardware.md) accepts when no NVIDIA GPU is usable |
| `best_gpu` | Gpu \| null | the GPU used for judging: the largest-VRAM GPU whose `backend` is `cuda`; without one, a `rocm` / `xpu` / `mps` GPU (dedicated VRAM first, then the largest). null → judged as CPU |
| `disk_free_gb` | float \| null | free space at the home directory (or `--disk-path`) |
| `overrides` | str[] | `vram` / `ram` when overridden |
| `swap_total_gb` | float \| null | current page file (Windows) / swap (Linux, zram excluded) size |
| `swap_max_gb` | float \| null | how far it can grow: the fixed page file maximum, 0 for none, the Linux swap size. null = unknown or system managed |
| `swap_auto` | bool \| null | true = system-managed page file (grows) / macOS. false = fixed size or none. null = unknown |
| `commit_limit_gb`, `commit_used_gb` | float \| null | Windows commit limit (RAM + page file) and current commit (`GlobalMemoryStatusEx`) |
| `other_gpus` | Adapter[] | display adapters of any vendor. AMD / Intel / Apple ones are classified (`usable`, `reason`); usable ones also appear in `gpus` when no NVIDIA GPU is usable. Empty unless checked |
| `other_gpus_checked` | bool | whether `other_gpus` was looked up: by default only when `nvidia-smi` found no NVIDIA GPU and `--vram` is not given; always with `system --all-gpus` |
| `notes` | str[] | what detection found or could not find (in the display language, `en` / `ja`): why a GPU is judged as CPU, old driver, non-NVIDIA GPUs, page file, WSL / container limits |
| `platform_verified` | bool | (0.11.0, #108; only in the CLI's JSON envelope, not in `SystemInfo.to_dict`) whether the bundled measurements have records of running on two or more GPU models of the kind (vendor × dedicated / shared memory) of the GPU used for judging. The same as "✓ verified GPU kind" on the tables' PC line. false without a GPU or with `--vram`. Not used for levels or `auto` (gate) ([scoring.md](scoring.md) "Verified GPU kinds"). The library has `gmf.platform_verification(system)` |
| `platform_verified_by` | object \| null | the evidence when verified: `{"kind": "nvidia/dedicated", "models": ["NVIDIA GeForce RTX 3070", "NVIDIA GeForce RTX 5080"]}` (kind, names of the GPU models that ran). null when not verified |

With `--ram`, the swap / commit values are null (another PC is assumed). Detection never fails: unknown values are
null and the reason is in `notes`. Host name, user name, GPU UUID, PCI bus id and device instance ids are never
read into the output. Details and sources: [hardware.md](hardware.md).

Gpu:

| key | type | meaning |
|---|---|---|
| `name`, `vendor` | str | `vendor` is `nvidia` / `amd` / `intel` / `apple` |
| `vram_total_gb` | float | 0 when nvidia-smi does not report it (then `backend` is `none`). For `memory` `shared` / `unified`, the memory the GPU is taken to use ([hardware.md](hardware.md), "Shared memory") |
| `vram_free_gb`, `vram_used_gb` | float \| null | free now / used now by other apps (screen, browser, …). null when overridden or not reported |
| `driver` | str \| null | driver version as nvidia-smi prints it |
| `backend` | str | `cuda` / `rocm` (AMD) / `xpu` (Intel) / `mps` (Apple silicon): used for judging. `none`: not used (generation not supported by any PyTorch CUDA build, driver too old for all of them, or VRAM unknown; reason in `notes`). `rocm` / `xpu` / `mps` are new in 0.7.0 (#22) and estimates; levels follow the same rules as NVIDIA (no cap since 0.11.0, #108) |
| `compute_capability` | str \| null | e.g. `"8.6"` |
| `compute_capability_source` | str \| null | `nvidia-smi` / `name` (guessed from the GPU name when nvidia-smi is too old) |
| `arch` | str \| null | `kepler` / `maxwell` / `pascal` / `volta` / `turing` / `ampere` / `ada` / `hopper` / `blackwell` (`fermi` / `unknown`) |
| `torch_cuda` | str[] | PyTorch CUDA builds (`cu132` … `cu126`) that run on this GPU with this driver, newest first. Informational |
| `compute_dtype` | str \| null | what ComfyUI computes in on this GPU by default: `fp16` / `bf16` / `fp32` (fp32 doubles working memory). null when `backend` is `none` or the generation is unknown |
| `bf16`, `fp8` | bool \| null | bf16 / fp8 compute support as ComfyUI sees it (null as above). Without bf16, model families that ComfyUI never runs in fp16 compute in fp32 ([hardware.md](hardware.md)) |
| `notes` | str[] | reasons and cautions for this GPU |
| `memory` | str | (0.7.0) `dedicated` (own VRAM) / `shared` (integrated GPU or APU using system RAM; from 0.11.0 also DGX Spark's GB10, whose total is MemTotal) / `unified` (Apple silicon). Shared and unified GPUs never offload (`GPU+RAM`) |
| `gfx_target` | str \| null | (0.7.0) AMD LLVM target, e.g. `gfx1100` |
| `carveout_gb` | float \| null | (0.7.0) for `memory: shared`, the VRAM the BIOS sets aside (not part of `ram_total_gb`); the RAM check counts only GPU use above it. null otherwise |
| `pci_id` | str \| null | (0.9.0) PCI model ID `vendor:device` (`1002:744c`) for AMD / Intel GPUs (Windows WMI `PNPDeviceID`, Linux `lspci -nn`); null for NVIDIA (nvidia-smi names match everywhere) and Apple. Used to match other users' measurements of the same GPU (`estimate.same_gpu`) |

Adapter (`other_gpus`): `name`, `vendor` (`nvidia` / `amd` / `intel` / `apple` / `unknown`), `vram_gb` (Windows: registry
`HardwareInformation.qwMemorySize`, else WMI `AdapterRAM` below 4 GiB; Linux AMD: sysfs; Intel Arc on Linux: the
smaller model's VRAM from the name; null if unknown), `vram_source` (`registry` / `wmi` / `sysfs` / `name` / null),
`source` (`wmi` / `lspci` / `system_profiler`; 0.11.0 added `sysfs` (Linux without lspci) and `wsl` (Windows-side
adapters read from WSL2); 0.12.0 added `app` (given to `gmf.system_from`, whose `vram_source` is `app` too; #96)). Added in 0.7.0 (#22): `integrated` (bool | null), `usable` (bool | null;
null for NVIDIA, which `nvidia-smi` decides), `reason` (a sentence in the display language: why it is or is not used), `shared_gb`
(Linux amdgpu GTT, sysfs `mem_info_gtt_total`), `needs` (string | null: what would make the GPU usable, currently only
`"Windows 11"` when a known Windows 10 build was the only reason; the table header says `needs Windows 11 for generation`).
Added in 0.9.0: `pci_id` (str | null: PCI model ID `vendor:device` from Windows WMI `PNPDeviceID` or Linux
`lspci -nn`, any vendor; never the bus address).

Added in 0.11.0 (#101; [hardware.md](hardware.md), "Every environment"):
- `driver` (str | null: DriverVersion from Windows WMI; used for the Intel condition in WSL2)
- `gfx_target` (str | null: the AMD generation read from a source more reliable than the name: `gfx_target_version` of the Linux amdkfd, or `Name: gfx…` of rocminfo in WSL2)
- `wsl_runtime` (bool | null: for an adapter read from WSL2, whether WSL is ready (rocminfo for AMD, `clinfo -l` for Intel))
- `config_error` (int | null: WMI ConfigManagerErrorCode when not 0; AMD and Intel adapters with 22, 28 or 43 are not used)

## Data status

Whether the model list in use is still fresh enough for auto-recommendation. There is no separate `status` command:
`genmodelfit system --json` puts it in `database.status`, `update --json` (and `update --from-release --json`) in
`status`, and Python returns it from `gmf.data_status(db=None, *, now=None, comfyui_version=None)` (a frozen dataclass
`DataStatus`; `.to_dict()` gives the same keys). The text output of `system` / `update` shows it as the Data lines.
Network is never touched.

| key | type | meaning |
|---|---|---|
| `source` | str | `bundled` (shipped in the package) / `cache` (written by a local `genmodelfit update`) / `release` (taken in by `update --from-release`: the cache whose `generated_at` equals the one recorded in `release_state.json`) / `path` (`--db`, `db=`) |
| `generated_at` | str \| null | when the list was made (as written in it) |
| `heads_checked_oldest`, `heads_checked_newest` | str \| null | oldest / newest `head_checked_at` over all files (UTC, `Z`) |
| `max_head_age_days` | int \| null | the gate's limit (`--max-head-age-days`, default 30; null = no limit) |
| `expired` | bool | **true when at least one entry lost auto-recommendation only because its HEAD checks are too old** (`auto_count < auto_count_if_fresh`). Apps should then stop showing "new models" from genmodelfit and refresh with `update --from-release` (or `update --heads-only`) |
| `expires_at` | str \| null | the earliest moment an auto entry expires: its oldest HEAD check + `max_head_age_days`. Exactly at this time it is still valid (the gate drops a check when its age is **greater** than the limit) |
| `models` | int | entries in the list (one per workflow) |
| `auto_count` | int | entries that are auto-recommendable now (default gate policy: content `sfw` only; the ComfyUI version is `--comfyui-version` / `comfyui_version`, else the one recorded by update) |
| `auto_count_if_fresh` | int | the same with the HEAD age limit removed |
| `checked_at` | str | the time used for the judgment (UTC) |

The gate's `url_reachable` check itself decides both counts (`gate.evaluate_offline`, run once with the policy and once
with `max_head_age_days=None`), so `expired` and the gate never disagree. The dates of the list in use are in
`genmodelfit system --json` `database.status` (`heads_checked_oldest`, `expires_at`); for the 0.9.1 bundled list the
oldest HEAD check is 2026-10-21T06:20:06Z, so with no update it expires after 2026-11-20T06:20:06Z and `auto_count`
becomes 0.

## recommend / fit / search

The three commands print the same shape (Issue #17, modeled on llmfit's `recommend --json`):

| key | type | meaning |
|---|---|---|
| `filters` | object | values actually used: `modality` (str[] \| null), `content`, `min_fit`, `license` (str[] \| null), `include_unknown_license` (bool), `fit` (str \| null: `--fit`), `task` (str[] \| null: the names given), `all_workflows`, `limit`; `recommend` adds `per_type`, `auto_only`, `include_tools`; `fit` adds `sort`, `all`, `include_tools`; `search` adds `query`, `sort`, `all` |
| `matched` | int | rows that passed every filter, before `limit` / `per_type` (models; workflows with `--all-workflows`) |
| `hidden` | object | why the other entries are not shown: `{reason: count}`, only reasons with a count (below) |
| `groups` | object[] | per type, in the order image, video, music, sfx, tts, 3d (only the `-m` types when given): `{modality, matched, shown}` |
| `models` | Fit[] | best first (order below) |

**One item per model** (below, "Models"): entries that differ only in precision or in workflow (text-to-image, edit,
LoRA, …) are collapsed into one item; the others are listed in `workflows` / `variants`. `--all-workflows`
(`all_workflows=True` in Python) gives one item per list entry as before.

`fit` (0.1.1, Issue #28; Python `gmf.fit_list()`): by default only the models that run (`--fit runnable` = perfect /
good / marginal), without utility tools, unknown-license and uncensored models; `--all` (`show_all=True`) is `--fit all`
plus tools and unknown licenses (uncensored still needs `--uncensored`). `--fit perfect|good|marginal` shows exactly
that level (other runnable levels are counted as `hidden.other_fit`); `--fit` and `--min-fit` cannot be combined
(`--perfect` = `--fit perfect`). `fit` and `search` print **one table** sorted by `--sort` (`sort=`):

| sort | order |
|---|---|
| `type` (default) | type (image, video, music, sfx, tts, 3d) → fit level (perfect → good → marginal → unknown → too_tight) → official before community within the same level → `popularity` desc → `score` desc → `id` |
| `score` | runs (perfect / good / marginal) → unknown → too_tight, then fit level (perfect / good without offload → marginal) → `score` desc → `popularity` desc → `id`: one ranking across types |
| `vram` / `disk` | runs → unknown → too_tight, then `memory_required_gb` / `disk_size_gb` ascending (null last) |
| `name` | runs → unknown → too_tight, then model name |

The fit level always comes before the score (the level is the answer to "does it run"; rules in
[scoring.md](scoring.md) "Order"). Auto-recommendable models are not moved up in `fit` / `search` (`info` shows the checks). JSON `models`
follows the same order; `filters` of `fit` adds `sort`, `all`, `include_tools`, of `search` `query`, `sort`, `all`.
Table columns (all commands): `Type | Status | Score | Censored | Model | Use | Prec | VRAM | Mem% | Mode | Disk | License`;
when the terminal is narrow, the Model column shrinks first (down to 24 characters), then Disk, Mem%, License,
Prec, Use are dropped in that order (Type, Status, Score, Censored and Model never). The **Verified** column (named Auto
up to 0.5.0: JSON `auto`, `yes` or the first failed gate check) was removed from the tables (#84); `info` shows the gate
checks and `gate` counts them. JSON is unchanged (`auto`, `held_by`, `gate`). **Censored** (0.6.0, in place of the `[U] ` name prefix) is `Y` (`content: sfw`) or `N`
(`content: uncensored`), never blank; the column is
shown only when uncensored rows can appear (`--content all|uncensored`; a table built from Python without a content
filter shows it when a row is uncensored) and is omitted with the default `sfw`. The legend then says
`Censored: N = safety filter removed / adult model`. JSON is unchanged (`content`).
`search` shows every fit level by default (`--fit` narrows).

**Unknown and not applicable (`?` and `-`)** (0.2.0, #30). In the table, `info` and the legend, `?` means the value
is **unknown** and `-` means it **does not apply**:

| column / line | `?` (unknown) | `-` (not applicable) |
|---|---|---|
| Status | size unknown, nothing judged (`fit_level: "unknown"`) | — |
| Prec / `info` precision | precision unknown (`precision: null`) | — |
| VRAM | size unknown | runs on the CPU (no VRAM used) |
| Mem% | size unknown | no GPU although one is needed (nothing to divide by) |
| Mode | size unknown (`run_mode: "unknown"`) | does not run (`run_mode: "none"`) |
| Disk / License | size / license unknown (`null`) | — |
| Use / `info` tasks | no task known | — |
| `info` file folder | — | the file has no ComfyUI folder (Hugging Face repo) |

JSON: an unknown **value** is `null` (`precision`, `license`, `memory_required_gb`, `disk_size_gb`, and the
`estimate` VRAM / RAM figures when `unknown_size`); an unknown or not-applicable **state** is a documented enum value
(`fit_level: "unknown"`, `run_mode: "unknown"` vs `"none"`), with the labels `?` / `-` in `fit_label` /
`run_mode_label`.
`memory_available_gb` is this PC's VRAM (RAM for CPU) even when the size is unknown, because that amount is known.

**Breaking in 0.2.0** (JSON, size-unknown models only): `run_mode` `"none"` → `"unknown"`, `run_mode_label` `-` →
`?`, `estimate.vram_gb` / `vram_offload_gb` / `ram_gb` / `vram_sequential_gb` `0.0` → `null`, `memory_available_gb`
`null` → the PC's amount. See CHANGELOG.md, "Breaking (JSON)", for migration. `schema_version` stays 1.

`recommend`: without `-m` and `--task` it shows **one section per type** (image, video, music, sfx, tts, 3d), the top
`-n` (default 3) of each (`filters.per_type`, `limit` null); a type with nothing runnable prints one line in text mode
and has `matched: 0` in `groups`. With `-m` or `--task` it is one list, `-n` default 10. In JSON `models` is ordered by
type, then by the order below. Other defaults: `min_fit` `marginal`, utility tools hidden, models with unknown size never
shown. `fit` shows the runnable levels by default and `search` every level (`--fit` / `--min-fit` to narrow), with no
limit; models with unknown size (`fit_level: "unknown"`, `?`) come after marginal ones of their type (`--fit all`). `search <query>` matches a
part of the id or name (case-insensitive); entries that do not match are not counted in `hidden`. With no match it prints
`models: []` and exits 0 (text mode prints `(no match)` in English, `(no match / 該当なし)` in Japanese); a blank query exits 1.
`content` defaults to **`sfw`**: uncensored models appear only with `--uncensored` (= `--content all`) or `--content uncensored` (only uncensored). `--uncensored` with `--content sfw` is a usage error.

**Unknown licenses are hidden by default** (0.1.1, Issue #28): an entry where any file's license (the entry's license
when it lists no files) is unknown (`null`) is not shown by `recommend` / `fit` / `search` or by `gmf.recommend()` /
`gmf.search()`; it is counted as `hidden.license_unknown`. `--any-license` (old form `--include-unknown-license`; Python
`include_unknown_license=True`) shows them. `info <exact id>` always shows the entry.

`hidden` reasons, each entry counted once under the first reason that applies, in this order:
`modality` (other `-m`), `uncensored` / `sfw` (by `--content`), `tools` (recommend / fit without `--include-tools` / `--all`),
`task` (other uses than `--task`), `license` (`--license`), `license_unknown` (some file's license is unknown; without
`--include-unknown-license`), `size_unknown` (recommend, or any `--min-fit` other than `too_tight`), `too_tight`,
`below_min_fit` (perfect / good / marginal below `--min-fit`), `other_fit` (a runnable level not selected by `--fit`),
`held` (`--auto-only`), `same_model` (collapsed into
another row of the same model;
the text header says "N more workflows merged into their models").
**`matched` + the sum of `hidden` = the number of entries looked at** (every entry of the list; for `search`, the entries
whose id or name matched). Tested in `tests/test_cli_json.py`.

`--license a,b`: every file's license (`ModelFile.license`; the entry's `license` when it lists no files) must be in the
list (case-insensitive). An unknown license (`null`) never matches.

### Order of `recommend`

`gmf.recommend()` and `recommend --json` ([scoring.md](scoring.md) "Order"): runs → not judged (`unknown`) → does
not run (`too_tight`) (up to 0.10 `too_tight` came before `unknown`; 0.11.0 matches the table) → `auto: true` →
**runs without offload** (perfect or good on `GPU` / `GPU~`, one tier, 0.1.1: the precision upgrade
to a `good` bf16 row must not move a model below where its `perfect` int8 row was) → marginal, and within marginal only
the faster run mode first (`GPU` → `GPU~` → `GPU+RAM` → `CPU`, 0.11.0) → official before
community templates (`community`) **within the same tier** → `popularity` (desc, 0.11.0) → `score` (desc) → `id`.
Until 0.1.0 community templates came after every official row of all levels.
The `recommend` **table** (text output) uses the same order without `auto: true` (#84; so the date and the allowlist
never change the table's order), and `-n` cuts that order, so with `-n` the table and `--json` can list
different models.

### Models

Like llmfit: a different **size** is a different model, a different **precision** is the same model (chosen
automatically), and **workflows** are uses of that model. `group_key` is built from the main weights
(`src/genmodelfit/groups.py`):

1. Main files = role `main` (by the estimator's rule: ComfyUI folder → `role` → HF folder → name). Text encoders
   (umt5, …) and VAEs are never the key. With several main files, the largest one and those ≥95% of its size (a pair
   such as Wan 2.2 high / low noise); without any, the largest file that is not a text encoder / VAE / clip_vision;
   without files, the entry itself. Super-resolution, upscaler and refiner files (`sr`, `upscal…`, `refiner` in the
   name) are not main weights when another main file exists (Hunyuan Video 1.5 lists its 1080p SR file in both the
   720p t2v and i2v workflows).
2. From each file name (the repo name instead for generic names such as `model.safetensors`), drop the extension,
   shard numbers, GGUF quant tags (`Q4_K_M`, `Q8_0`, …) and precision / packaging words (`fp16`, `bf16`, `fp8`,
   `e4m3fn`, `scaled`, `mixed`, `int8`, `int4`, `nf4`, `nvfp4`, `w4a8`, `convrot`, `kj`, `comfy`, `transformer`, `only`,
   `pruned`, `aio`, `high`, `low`, `noise`, …). Keep version, size and variant words (`2.1`, `1.5`, `14b`, `1.3b`,
   `turbo`, `dev`, `distilled`, `base`, `edit`). `wan2_1` is read as `wan 2.1`, `v1.5` as `1.5`, `ace_step` as
   `acestep`. If no size word is left, `params_b` is added (`4b`).
3. Words are sorted and joined; the key is `<modality>:<content>:<words>`, so different types or content marks never
   share a model. Examples: `z_image_turbo_bf16` and `z_image_turbo_int8_convrot` → `image:sfw:image turbo z`;
   `wan2.1_t2v_1.3B_fp16` → `… 1.3b 2.1 t2v wan`, `wan2.1_t2v_14B_fp8_scaled` → `… 14b 2.1 t2v wan` (different).
   The test table is `tests/test_groups.py`.

After the per-entry key, the whole list is looked at (`groups.build_index`):

- An entry with two main files of different names (FLUX.2 klein 4B text-to-image loads both `klein-base-4b` and
  `klein-4b`) joins the model of one of them if that model exists on its own: the one without `base`, then by name.
  Otherwise the names stay joined with `+` (Ideogram 4 with its unconditional pair).
- Entries whose **whole set** of main-file sha256s is equal are the same model (the same file under another name or
  repo, e.g. Stable Audio Open 1.0 in the template and on HF). Sharing only some main files is not enough. Upstream records whose file name and URL / hash disagree
  are corrected first (`update.UPSTREAM_FILE_FIXES`, checked with an anonymous HEAD).
- `params_b` is added to the key only for generic HF file names (`model.safetensors`) without a size word; template
  `params_b` comes from the title and can be the language model's size (ACE-Step 1.5 "4B LM").

Precision (`ModelEntry.precision`) is read from the main file used for the key (`groups.model_precision_source`;
`update` uses the same function). The first source that tells wins, and it is kept in `ModelEntry.precision_source`
(#30, 0.2.0):

1. `header`: the file's `header_precision` when it was read. The name wins (`filename`) for packed formats the header
   cannot tell (`w4a8`, `nvfp4`, `mxfp4`, `fp4`, `nf4`, `int4`) and for GGUF files whose name states the quantization;
2. `filename`: the file name, e.g. `w4a8` for the 10Eros W4A8 file;
3. `hf_api` / `config`: `ModelFile.repo_precision`, read from the hosting Hugging Face repo when neither the header nor
   the name tells (`repo_precision.py`): the Hub API's `safetensors.parameters` when one dtype holds ≥ 90 % of the
   parameters **and** element count × bytes per element is within 5 % of the entry's main safetensors files in that
   repo (shards added; a repo with other weights beside the main one does not describe it; safetensors main files only;
   readable for gated repos), else `torch_dtype` / `dtype` of the
   `config.json` in the main file's folder (only when the API lists it and the repo is not gated);
4. `workflow`: `ModelEntry.workflow_precision`, inferred from the official template's main loader node
   (`workflow.loader_precision`): the node that loads the main file (or, for templates without a file list, the
   first of `workflow.MAIN_LOADERS`: `UNETLoader`, `CheckpointLoaderSimple`, `WanVideoModelLoader`, `SeedVR2LoadDiTModel`,
   `DownloadAndLoadGIMMVFIModel`, …). A load-time cast to fp8 (`weight_dtype` / `quantization` = `fp8_*`) → `fp8`; else
   the precision word of the loaded file name; else an explicit precision widget (`fp32`, `bf16`, `fp16`, `fp16_fast`).
   `default` / `auto` / `disabled` say nothing. LoRA, CLIP and VAE loaders are never used.

A precision read from the header is never replaced by a later source. null = unknown (`precision_source` null).

**Display name** (`model_name`, the table's Model column, `groups.display_name`): candidates are each member's
name before `:`, after `:` and both joined, without precision / packaging words (`Int8`, `FP16`, `F16`, `ConvRot`, `KJ`,
`AIO`, …) and use phrases (`Text to Video`, `Depth Estimation`, `Image Upscale`, …). The candidate containing most words
of the key wins (official templates before community templates and HF repos, then the shorter); it is cut after the
last key word it contains, and missing size / version / variant words of the key are added (`ACE Step v1 3.5B`,
`Hunyuan Video 1.5 720p T2V`, `Image Upscale: SeedVR2 7B Int8` → `SeedVR2 7B`). A version added this way gets a `v`
when it follows a number (`Kokoro-82M v1.0`); `Wan2.1` is written `Wan 2.1`. Models whose key words are the same
except the type share one name plus the type (`SAM 3.1 (3D)` / `(image)` / `(video)`; no suffix on the only type
that has official templates). A name without digits gets its one-digit version when a sibling differs only by a
version (`MoGe 2` beside `MoGe 3`). File-less templates join the one model of the same type whose key contains all
words of their model name (`Frame Interpolation: FILM` → `FILM`); otherwise they are their own model named
`<name> (files unknown)`. Remaining clashes get key words (`fl2va` → `first/last frame`), then `#2`. A small table
`groups.NAME_OVERRIDES` (entry id → name; community templates and a few HF repos whose titles carry no model name)
overrides the result; `tests/test_groups.py` checks it. Ids never appear in names. `genmodelfit info <model name>` (case-insensitive) shows the model's best workflow for this PC and the
others.

Which entry is shown for a model (`recommend.variant_key`): among the entries that passed the filters,
0. official before community templates (`community`: the template's `username` is not ComfyUI) — a community
   workflow never stands for a model that has an official one,
1. the use: `gen` → `edit` → `i2v` → `lipsync` → `motion` → `voice` → `upscale` → `tool` (with `--task`, among the
   matching uses) — so a tool or upscale workflow that shares the weights never stands for the model,
2. **entries that run on the GPU without offload** (`fit_level` `perfect` or `good` and `run_mode` `gpu`, i.e. Mode
   `GPU` or `GPU~`; `recommend.runs_clean`) first, and **among them the higher precision first**, then the better
   level — like llmfit, the best quality that fits (0.1.1, Issue #28),
3. otherwise (no entry runs without offload): the better fit level, then no offload (`GPU` / `GPU~` before `GPU+RAM`
   before CPU — same level, faster), then the higher precision,
4. the order above.
Precision order: bf16 / fp16 / fp32 > fp8 > int8 > GGUF Q8 > Q6 > Q5 > Q4 / 4-bit > lower; unknown precision counts as
full precision. So on 24 GB Z-Image Turbo shows bf16 (`good`, `GPU~`) rather than int8 (`perfect`); on 8 GB it shows
int8 (swapped, `marginal`) rather than bf16 (offload, `marginal`); with `--task edit` the representative is an edit
workflow. **Every value on the row, including `score` (and `score_components`), is the shown entry's** (0.11.0, so it
matches the table's Mem%; up to 0.10 the row score was the highest among the members with the same use and tier).
`popularity` is the model's value (the same for all members).
Adding VRAM never moves a row out of the "runs without offload" tier, and the shown
precision only goes up within it (exception: a level set by measurements on the same GPU, see "Score"); the row's
`score` can drop when a heavier precision or run mode takes over (0.11.0), and the row's `fit_level` can go from `perfect` to `good` when a higher precision
becomes runnable (on the bundled list only LTX 2.3 10Eros: nvfp4 `perfect` at 48 GB → bf16 `good` at 64 GB).
Tested in `tests/test_groups.py`.

On the bundled list (329 entries, 2026-10-21) this gives 210 models; 55 of them have more than one entry.

### Uses

The six big types stay the top level. Uses are sub-divisions inside a type, decided from the entry's `tasks`.
`--task` and `genmodelfit types` use them. The labels are what `types` prints (`label`, `label_ja`):

| use | `label` | `label_ja` (Japanese label) | task codes |
|---|---|---|---|
| `gen` (alias `generate`) | generate from text (3D: from an image) | 文章から作る(3D は画像から) | t2i, t2v, t2a, tts, t23d, i23d |
| `edit` | edit / transform / control | 編集・変換・形の指定 | i2i, inpaint, outpaint, relight, control, v2v, a2a |
| `i2v` | image to video | 画像から動画 | i2v, flf2v, ref2v |
| `lipsync` (alias `lip`) | lip sync / audio to video | 口の動き・音から動画 | lipsync, a2v |
| `motion` | motion control | 動きの指定 | motion |
| `voice` | voice conversion | 声の変換 | vc |
| `upscale` | upscale / restore | 高解像度化 | upscale |
| `tool` | utility tool (depth, pose, mask, …) | 補助ツール(深度・ポーズ・切り抜きなど) | tool, estimate, matting, interp, segment, separate |

`--task` takes use names, aliases or task codes, comma-separated (`--task edit`, `--task i2v,lipsync`, `--task t2i`).
An entry matches when one of its tasks is in the set. `use` (JSON) is the matching use, else
the first of tool, upscale, lipsync, motion, voice, edit, gen, i2v that the entry has.
The table's Use column (#84) starts with the row's own `use` (the use that matched `--task`, else the shown entry's),
then the uses of the other workflows of the same model (`workflows`, including ones hidden by the filters) without
repeats, in the order of the table above, e.g. `gen,edit` (`edit,gen` with `--task edit`); with `--all-workflows` it
is the row's `use` only. A list longer than the column (9 characters) is cut after the last use that fits and ends
with `,…` (`gen,…`, `lipsync,…`), so the row's own use is always shown; `info` lists the workflows. Until 0.10.0 it was the row's `use` plus `+N`
(the number of other workflows).
Utility tools (`upscale` and `tool`, plus Image / Video Tools templates whose title is a depth / normal / line-art /
pose / geometry map or a segmentation, any audio separation, and MoGe / geometry estimation even under 3D Model) are hidden by `recommend` unless `--include-tools` or a
tool use is given in `--task`.

`genmodelfit types --json`: `filters` (`content`), `types` (`[{modality, label, label_ja, models, workflows, uses: {use:
workflows}}]`; `models` = number of models as above, `workflows` = list entries; an entry with two uses counts in both),
`uses` (`[{use, label, label_ja, tasks}]`), `aliases`.

### Fit (one item of `models`)

Python `Fit.to_dict()` returns exactly this dict (`gmf.recommend()` / `gmf.fit_list()` / `gmf.search()` give the same list as the CLI's `--json`).

| key | type | meaning |
|---|---|---|
| `id` | str | template name (`image_z_image_turbo_int8`) or `hf:<owner>/<repo>` |
| `name`, `modality`, `tasks` | | modality ∈ `image` `video` `music` `sfx` `tts` `3d`; `tasks` is a list (`t2i`, `i2v`, …) |
| `content` | str | `sfw` or `uncensored` (see [README](../README.md)) |
| `source` | str | `comfyui-template` / `huggingface` / `manual` |
| `runtime` | str \| null | how the model runs: `comfyui` / `piper` / `irodori-tts` / … (`ModelEntry.runtime`, 0.5.0); null = unknown |
| `languages` | object | `{"prompt": [...], "speech": [...]}` ISO 639-1 (`ModelEntry.languages`, 0.5.0); a missing key = unknown, `[]` = none (an image-to-3D model takes no prompt; a music model without vocals has no speech) |
| `description` | object | `{"ja": "...", "en": "..."}` hand-written, only for some models (`ModelEntry.description`, 0.5.0); `{}` = none |
| `fit_level` | str | `perfect` / `good` / `marginal` / `too_tight` (= `estimate.fit`), or `unknown` when the size is unknown and nothing was judged (0.1.1; until 0.1.0 these were `too_tight`) |
| `fit_label` | str | `Perfect` / `Good` / `Marginal` / `Too tight` / `?` |
| `run_mode` | str | `gpu` / `offload` / `cpu` / `none` (does not run) / `unknown` (size unknown, not judged; 0.2.0 — until 0.1.1 these were `none`) (= `estimate.run_mode`) |
| `run_mode_label` | str | `GPU` (all on GPU) / `GPU~` (parts swapped in turn) / `GPU+RAM` (offload) / `CPU` / `-` (does not run) / `?` (size unknown) |
| `level_cap` | str \| null | the run mode caps the level (0.1.1): `sequential` (`GPU~`, at most `good`), `offload` (`GPU+RAM`, at most `good`; `marginal` until 0.10), `cpu` (generation at most `marginal`, speech built for real-time CPU use and one-pass tools at most `good`; 0.11.0, #108); null for all on GPU or not running. So "Good, 54 %, GPU~" is not a contradiction |
| `score` | float | 0–100: the memory headroom ("Score" below) |
| `score_components` | object | `{"memory_fit": …}` (0.11.0; the same value as `score`. The keys up to 0.10, `fit`, `headroom`, `readiness`, `basis`, `popularity`, were removed) |
| `popularity` | float | (0.11.0) popularity rank 0–100: the percentile within the same type and source (template usage / Hugging Face all-time downloads), the model's value. Only an order key, not part of `score` ([scoring.md](scoring.md) "Popularity") |
| `memory_kind` | str | `vram` (judged on the GPU) or `ram` (CPU) |
| `memory_required_gb` | float \| null | the figure the level was judged on: VRAM for all-in / swapping / offload (the table's VRAM column); RAM for CPU. When a GPU is needed but missing: the minimum VRAM. null when the size is unknown |
| `memory_available_gb` | float \| null | this PC's VRAM (or RAM for CPU); `0.0` when a GPU is needed but missing. Also set when the size is unknown (0.2.0; was null) |
| `utilization_pct` | float \| null | the use ratio of the tightest resource of the run mode × 100 (the table's Mem%; the input of `score`). Usually `memory_required_gb / memory_available_gb × 100`; for `GPU+RAM` rows and rows short of RAM, RAM needed ÷ system RAM when that is larger (0.11.0) |
| `gpu_memory_required_gb` | float \| null | (0.7.1, #58) the VRAM the model needs to run on this PC's GPU, also when `run_mode` is `cpu` (where `memory_required_gb` is RAM). Same value as `memory_required_gb` for every GPU run mode and for `too_tight`; for `cpu` it is the same figure `too_tight` uses, the smallest need among the load modes (the GPU+RAM offload figure `estimate.vram_offload_gb`; also for shared / unified memory GPUs, so the meaning does not change between `cpu` and `too_tight`), i.e. "the GPU would need at least this much". null when no GPU is used for judging (CPU-only PC, `gpu_usable=False`, unusable GPU) or the size is unknown. Additive; no other field changes |
| `ram_required_gb` | float \| null | system RAM needed (= `estimate.ram_gb`; null when the size is unknown) |
| `disk_size_gb` | float \| null | download size counted (one variant per part, = `estimate.disk_gb`) |
| `total_size_gb` | float \| null | size of all listed files |
| `license` | str \| null | strictest license of the files (`apache-2.0`, `mit`, `other`, …; null = unknown); within the same tier the one with more obligations (`apache-2.0` before `bsd-3-clause` before `mit`, `hf.OBLIGATION_RANK`) |
| `licenses` | (str \| null)[] | the distinct licenses of the files (null last = some file unknown) |
| `auto` | bool | can be auto-recommended (= `gate.auto`) |
| `held_reasons` | str[] | failed gate checks (= `gate.held_by`) |
| `community` | bool | a community template (the index's `username` is not ComfyUI; [sources.md](sources.md)) |
| `estimate_confidence` | str | `measured` / `estimated` (= `estimate.basis`; the finer level is `estimate.basis_detail`) |
| `notes` | str[] | reasons in the display language (`en` / `ja`; = `estimate.reasons`) |
| `files_count` | int | download files listed |
| `use` | str | the use shown (Uses above) |
| `precision` | str \| null | the chosen entry's precision (`ModelEntry.precision`: `bf16`, `fp8`, `int8`, `gguf-q8_0`, …; null = unknown, shown as `?`, ranked like full precision). Where it came from: `precision_source` (next row) |
| `precision_source` | str \| null | `header` / `filename` / `hf_api` / `config` / `workflow` (`ModelEntry.precision_source`, see "Models"); null = unknown (0.2.0) |
| `group_key` | str | the model this entry belongs to (Models above) |
| `model_name` | str | the model's display name (unique over the list; `info` accepts it) |
| `variants` | object[] | per precision, the best entry that passed the filters: `{precision, workflow_id, memory_required_gb, fit_level, auto, disk_size_gb}`, best precision first (always includes this entry) |
| `workflows` | object[] | **all** other entries of the same model, also those filtered out: `{id, name, tasks, use, precision, precision_source, fit_level, memory_required_gb, disk_size_gb, auto, matched}` (`matched`: passed the current filters), best first |
| `estimate` | Estimate | full estimate (below; `limits` with `--limits`) |
| `gate` | Gate | full gate result (below) |

### Score

The full rules are in [scoring.md](scoring.md) "Score" (changed in 0.11.0).

```
r = use of the tightest resource of the run mode (utilization_pct ÷ 100)
memory_fit = 100                                     (r ≤ 0.70)
           = 100 × exp(−0.5 × ((r − 0.70) / 0.20)²)    (r > 0.70)
rows that run (perfect / good / marginal) never go below 37.5; too_tight and unknown are 0
score = round(memory_fit, 1)
```

| run mode | r |
|---|---|
| `GPU` | full-load VRAM ÷ VRAM |
| `GPU~` | largest VRAM when the parts are swapped in turn ÷ VRAM |
| `GPU+RAM` | max(offloaded VRAM ÷ VRAM, RAM needed ÷ system RAM) |
| `CPU` | RAM needed ÷ system RAM |
| level set by measurements (`fit_from: measurement`) | `estimate.measured_level_vram_gb` ÷ VRAM |
| short of system RAM | max(the value above, RAM needed ÷ system RAM) |

- The constants 0.70 and 0.20 are llmfit's `fit_score`. Examples: r = 0.80 → 88.2, 0.90 → 60.7, 0.98 → 37.5.
- The level, readiness (gate), whether a model was measured and popularity are not in it (popularity is an order key: `popularity`).
- The formula up to 0.10 (`0.45·fit + 0.15·headroom + 0.20·readiness + 0.10·basis + 0.10·popularity`) and its component keys were removed.

**Promise**: more VRAM or RAM never lowers the level, and more RAM never lowers `score`.
More VRAM can lower `score` when the representative switches to a heavier precision or run mode (for example Z-Image
Turbo int8: 100 on GPU~ at 14 GB, 82.3 all on GPU at 16 GB; 0.11.0; llmfit behaves the same). Tested in `tests/test_invariants.py` and
`tests/test_scoring.py`.

**Exception: a level set by measurements on the same GPU** (0.9.1, 0.11.0). Measurements set the level only on the same
GPU as this PC (`estimate.same_gpu`, VRAM ratio 0.95 or more), for the maintainer's measurements and other users' alike. So
raising VRAM past that (changing the amount with `--vram`) stops the measurements from setting the level, and the level
can fall (for example `--vram 17` on an RTX 5080 PC). Intended: it is a judgement for a GPU with another amount.

## Estimate

| key | type | meaning |
|---|---|---|
| `vram_gb` | float | peak VRAM when everything stays on the GPU |
| `vram_sequential_gb` | float \| null | peak VRAM when ComfyUI swaps parts between stages (text encoder → model → VAE). When the workflow runs several diffusion models one sampler after another (Wan 2.2 14B high_noise → low_noise; `ModelEntry.sampler_stages`), the model stage counts only the largest of them (with its LoRAs) |
| `vram_resident_gb` | float \| null | VRAM ComfyUI tries to keep free while everything stays loaded through the decode: overhead + all weights + the largest stage working memory with the **untiled** VAE working memory (#102; [accuracy.md, "Tiles"](accuracy.md#tiles)). Not the amount used (that is `vram_gb`); when it exceeds 98% of VRAM, all-on-GPU is not chosen and the parts are swapped (`GPU~`, level capped at good). Equals `vram_gb` for models without per-stage working memory; ≥ `vram_gb`. Not applied when the level is set by measurements (`fit_from: measurement`). `null` when the size is unknown |
| `vram_offload_gb` | float | peak VRAM when part of the weights stays in RAM (lowvram / dynamic VRAM); several diffusion models run in turn are counted one at a time as above |
| `ram_gb` | float | system RAM needed for the chosen run mode: GPU (all-in / swapping) = 3 + largest stage's weights; offload / CPU = 3 + all weights. Short but within RAM + page file (fixed page file / swap; capped at 1.5× RAM; 1.5× RAM when system managed or unknown) → `marginal` (page file, slow); more → `too_tight` |
| `disk_gb` | float \| null | size of the files counted (one variant per part; see [accuracy.md](accuracy.md)) |
| `basis` | str | **`measured`** (from a real run) or **`estimated`** (formula). Always present. Stays two-valued for compatibility; the finer level is `basis_detail` |
| `basis_detail` | str | (#34) how measured the value is; details below |
| `fit_from` | str | (0.7.0, #26) `estimate` (the level comes from the estimate) or `measurement`; details below |
| `measured_peak_vram_gb` | float \| null | (0.7.0) the largest agreeing peak VRAM when `fit_from` is `measurement` (before the context is added) |
| `measured_level_vram_gb` | float \| null | (0.11.0) the amount used for the level when `fit_from` is `measurement`: max(peak + context, weights + fixed part). The table's VRAM column (with `m` / `c`), Mem% and `memory_required_gb` show this value |
| `sample_count` | int \| null | (2026-10-22, optional; like llmfit) number of independent sources behind the value (0.11.0; was the number of submissions): 1 for the maintainer's / this PC's measurements however many rows, the aggregated row's `n_sources` for other users' (1 for older rows without it), and the agreeing sources counted by the union of the IDs (`source_ids`) when measurements set the level. null when no measurement was used (`basis_detail` `estimated`). `info` prints it as `n=3`; the table's VRAM column marks any measured value with `m`, whoever measured it (0.11.0 dropped `c`; #108) |
| `measured_on` | str \| null | GPU of the measurement when `basis` is `measured` (null when judged without a GPU) |
| `fit` | str | the four levels below, or `unknown` (size unknown, not judged) |
| `run_mode` | str | `gpu` / `offload` / `cpu` / `none` (does not run) / `unknown` (size unknown) |
| `reasons` | str[] | human-readable reasons, in the display language (`en` / `ja`) |
| `settings` | object \| null | settings actually judged (unset items filled with the per-modality defaults whether or not there are measurements; 0.11.0, before that from the measurement when there was one) |
| `limits` | Limits \| null | largest settings that still reach `good` on this PC's GPU without offloading (0.11.0; null on PCs without a GPU). Set by `info`, and by `fit` / `recommend` with `--limits` |
| `vram_free_short` | bool | judged to run on the GPU, but the VRAM free right now (`nvidia-smi` `memory.free`) is below what the chosen run mode needs: close other apps. The level stays based on total VRAM. Always false when the free VRAM is unknown (`--vram`) |
| `optional_parts` | object[] | (0.12.0, #81) parts the official template does not load with its default values (not in the amounts above), each `name`, `size_gb` (null = unknown), `optional` (`inactive` / `switch_off`, as `ModelFile.optional`), `kind` (`replace` = used instead of a default part / `add` = only added) and `replaces` (names of the default parts no longer loaded). `[]` if none. [scoring.md](scoring.md), "Parts switched off by default" |
| `vram_gb_all_options` | float \| null | (0.12.0, #81) the VRAM used for the level with every switched-off part (`switch_off`) turned on (an upper bound; chosen like `gpu_memory_required_gb`). null with no `switch_off` part, no GPU, or an unknown size |
| `fit_all_options` | str \| null | (0.12.0, #81) the level in that case. null with no `switch_off` part. Only when it differs from `fit` does one sentence go into `reasons` |
| `unknown_size` | bool | the weight size is unknown, so nothing was judged: `fit` is `unknown` (shown as `?`, never "Too tight"), `run_mode` `unknown` (0.2.0; was `none`), the VRAM / RAM values are `null` in JSON (`Estimate.to_dict`; the Python attributes stay 0.0), `limits` null, first reason 「大きさが分からないため判定できません」 ("size unknown, cannot judge"). `recommend` drops these; `fit` / `search` list them last |

`basis_detail` (#34), from most to least measured (`models.BASIS_DETAILS`):

1. `measured`: a measurement at the same settings. The maintainer's or this PC's bench measurements and other users' submissions aggregated by `contrib aggregate` (values use **one** submission already) are not told apart. The number of sources is `sample_count`
2. `calibrated`: shifted by a measurement at other settings (also a same-settings value clamped by the bounds below)
3. `estimated`: formula only

0.11.0 (#108) merged `measured_community` into `measured`. Apps that check for `measured_community` should check for `measured` (which source it was stays in `origin` of the `measurements` list of `info --json`; it is never used for marks).

Since 2026-10-22 (like llmfit) community measurements are used in **both directions**: they can lower or raise the needs.
They are bounded only physically:

- The all-in value is never below weights + fixed overhead (zero working memory), and never above the measuring GPU's VRAM × 1.02 + 0.25 GiB.
- A value pinned at the GPU limit is a lower bound.
- Spread to other settings, the ratio measured ÷ formula is clamped to [0.6, 1.4] (`estimate.COMMUNITY_CALIBRATION_RANGE`; llmfit uses [0.05, 3.0] for speed).

Other users' measurements are used only on the same GPU:

- `estimate.same_gpu`: the PCI model ID when both sides have one, else the normalized name.
- Since 0.9.1, the VRAM ratio must also be 0.95 or more (0.97 until 0.10).
- PCs with another GPU are judged as without them.

Other rules:

- At the same settings, such a row comes before the maintainer's / this PC's.
- The maintainer's `torch_reserved` rows stay lower bounds (they only raise).
- Working-memory-only rows (`vram_metric: torch_reserved` with `offload: dynamic`; #100, `estimate.working_only`) are not used at all here: they hold no weights, because dynamic VRAM loads and evicts the weights on demand. Until the per-stage peaks can be measured they are only cross-checked in [accuracy.md](accuracy.md) (not for the shift, not for the level).
- When a bound was applied, the value is not the measurement: `basis` is `estimated` and `basis_detail` is `calibrated`.
- When `basis` is `measured` and the measurement is not capped at the GPU limit, `vram_gb` equals the measured value.
- See [accuracy.md](accuracy.md), "Other users' measurements".

`fit_from` (0.7.0, #26; the conditions changed in 0.11.0) is `estimate` (the level comes from the estimate) or `measurement`.
It is `measurement` when all of these hold (`estimate.measured_level`; details in [scoring.md](scoring.md) "When measurements set the level"):

- Standard measurements at the same settings, from the same vendor, on the **same GPU** as this PC (`estimate.same_gpu`: same PCI model ID, else the same normalized name, and a VRAM ratio of 0.95 or more; generic names never match). The same rule for the maintainer's and other users' measurements (up to 0.10 the maintainer's counted on any same-vendor GPU with at least the measured VRAM).
- **Two or more independent sources**: the maintainer's / this PC's rows are one source together, however many (the maintainer alone never sets the level); other users' rows count the union of their source IDs (`source_ids`; the largest `n_sources` for older rows without IDs); they are added up (`estimate.count_sources`). Measurements whose peak + context is below weights + fixed part never set the level (they ran by offloading weights).
- **They agree**: no OOM record (no `outcome: oom` row, no `n_oom`, no `runs_evidence: false`), each aggregated row agrees inside (`consistent`), and the spread across rows is within 15% of the median.
- The amount used for the level = max(largest agreeing peak + context, weights + fixed part) (`measured_level_vram_gb`). A `torch_reserved` peak gets `estimate.TORCH_RESERVED_CONTEXT_GB` (0.5 GiB) added. If the amount does not fit this PC's VRAM, the level is not set.
- The level is at least Good; Perfect only when the amount is within 60% of this VRAM and no agreeing measurement was pinned at the GPU limit.
- It is used only when it is better than the estimated level.
- The vendor of a measurement is its `gpu_backend` when recorded, else guessed from the GPU name. A measurement from another vendor's GPU only calibrates (`basis: estimated`).
- Measurements only raise the level and never lower it, whatever the GPU vendor. Before 0.11.0, AMD / Intel / Apple GPUs and DGX Spark were capped at Good unless the level came from measurements (#108 replaced the cap with the verified-kind mark `platform_verified`).
- The run mode is then `gpu`, and the reasons say that measurements decided.

When no settings are given, the per-modality defaults are used whether or not there are measurements (image 1024²,
video 832×480 × 81 frames, music 30 s, sfx / tts 10 s; no size for templates without a size input). Up to 0.10 a model
with a measurement was judged at the measured settings (changed in 0.11.0; [scoring.md](scoring.md) "Settings used for judging").
A measurement is used as `basis: "measured"` only when width / height / frames / seconds / batch / precision match
(precision compared case-insensitively; a measurement without precision means the model's own precision) and a GPU is
present; other settings are shifted by it and stay `estimated`. Measurements with `outcome` other than `ok` or with
`standard: false` are ignored. So at the defaults, models whose measurements were taken at other settings (the
maintainer's image, video and music measurements) are `calibrated`; pass `--width` etc. to judge at the measured settings. How measurements and coefficients are used: [accuracy.md](accuracy.md).

Limits (all values are the largest that give `perfect` or `good` on the GPU without offloading; `limits` is null without a GPU; other settings unchanged; `null` = even the
smallest tried value does not reach `good`):

| key | type | meaning |
|---|---|---|
| `level` | str | always `good` |
| `max_side_px` | int \| null | image / video: long side in px, keeping the aspect ratio (searched 256–2048 for image, 256–1280 for video, step 32) |
| `max_frames` | int \| null | video: frames at the judged resolution (4n+1, up to 241) |
| `max_seconds` | int \| null | music / sfx / tts: length in seconds (step 5, up to 600) |
| `at_cap` | str[] | keys that reached the end of the searched range (the real limit is higher) |

Fit levels — by the **utilization of the memory pool of the run mode** (required / available; VRAM on the
GPU, RAM on the CPU), as in llmfit (llmfit's `docs/how-it-works.md`, "Fit analysis"; adopted 2026-10-20):

| utilization | level |
|---|---|
| ≤ 60% | `perfect` |
| ≤ 85% | `good` |
| ≤ 98% | `marginal` |
| > 98% | `too_tight` |

Caps per run mode (0.11.0, #108; [scoring.md](scoring.md) "Level caps"): all on the GPU (`GPU`) none; parts swapped
in turn (`GPU~`) and offload to RAM (`GPU+RAM`) at most `good` (offload was `marginal` until 0.10); on the CPU,
image, video, music, sound effect and 3D generation and speech whose authors do not say it runs in real time on a CPU
at most `marginal`, speech whose authors say it runs in real time on a CPU (`estimate.CPU_REALTIME_TTS`) and one-pass tools (all uses in `tool` /
`estimate` / `matting` / `interp` / `segment` / `separate`) at most `good`, with system RAM as the pool. No cap by GPU
vendor (until 0.10 non-NVIDIA GPUs and DGX Spark were capped at `good`). The run mode with the best level is used
(ties: all-in → swapped → offload), so offloading is chosen only when it raises the level (never when all-in or
swapped is `good` or better). The use of the offload mode is the tighter of the offloaded VRAM use and RAM needed ÷
system RAM. A level set by measurements can be `good` even above 98% (an exception). `perfect` means room to spare,
not speed.
The RAM check is applied afterwards (short but within the page file limit above → at most `marginal`, more →
`too_tight`). On GPUs where ComfyUI computes in fp32 (`compute_dtype: "fp32"`: Maxwell, some Pascal, GTX 16xx …) the
working memory and the VAE weights are doubled before judging ([hardware.md](hardware.md), "Precision"). When the
GPU judgement is worse than the judgement as if there were no GPU (CPU), the CPU one is used (ties: GPU), so adding VRAM
never lowers the level (exception: a level set by
measurements on the same GPU, which applies only at a VRAM ratio of 0.95 or more; see "Score"). Models whose size is unknown
(`unknown_size: true`) get `unknown` instead of a level. Constants: `estimate.FIT_BANDS`.

**Several diffusion models in one workflow** (0.1.1, Issue #28): ComfyUI runs the samplers one
after another, and when the next sampler loads its model, `load_models_gpu` calls `free_memory`, which unloads models
that are loaded but not used now, as much as needed (ComfyUI commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24`:
`comfy/samplers.py:1242` → `comfy/sampler_helpers.py:201` → `comfy/model_management.py:1001`, unloading at `:905` /
`:919`; dynamic-VRAM models are loaded on demand, `:914`). So the swapped (`GPU~`) and offload figures count one
sampler's set at a time: that model + the files on its MODEL input (LoRAs) + the denoise-stage files shared by all
(ControlNet, model patches); the all-in figure and system RAM still count all of them (unloaded models stay in RAM).
The sets come from `ModelEntry.sampler_stages` (read from the template graph by `update`); when that is null (not
checked), from the rule "main files named `*high_noise*` and `*low_noise*`" (LoRAs named high_noise / low_noise go to
their set, others are shared). `estimate.expert_sets`. The official Wan 2.2 14B I2V template itself notes 84% VRAM on an
RTX 4090D 24 GB at 640×640, 81 frames.

## Gate object

Computed every time (never stored in `db.json`). Details and the default license allowlist: [gate.md](gate.md).

| key | type | meaning |
|---|---|---|
| `auto` | bool | true only when every check passed |
| `held_by` | str[] | ids of the failed checks, in the order below (`held_by[0]` is shown as `held:<id>` by `gate --only-held`) |
| `comfyui_version` | str \| null | the ComfyUI version compared against (`--comfyui-version`, else `sources.comfyui.latest_release`) |
| `checks` | Check[] | the 10 checks, in this order: `license`, `gated`, `official_workflow`, `custom_nodes`, `comfyui_version`, `url_reachable`, `sha256`, `content`, `size_known`, `reviewed` (0.6.0, #42: passes unless `review.status` is not `ok`; always last, so `held_by[0]` of the older checks is unchanged). For a model whose `runtime` is neither `comfyui` nor null (0.5.0), the three ComfyUI checks are replaced by one `app_runtime` check that never passes (8 checks) |

Check: `id` (stable, machine-readable), `passed` (bool), `detail` (for people, in the display language `en` / `ja`; wording may change).

## gate

`genmodelfit gate --json`: `policy` (`license_allowlist`, `comfyui_version`, `allow_uncensored`, `max_head_age_days`),
`filters` (`modality`, `only_auto`, `only_held`), `summary` (`models`, `auto`, `held`,
`failing` = models failing each check, `first_failing` = models whose first failed check is that one; both have
a key per check id, `app_runtime` included since 0.5.0, `reviewed` since 0.6.0),
`models` (`{"id", …Gate}`; all, or only auto / held with `--only-auto` / `--only-held`).

## diff

`genmodelfit diff [OLD] [NEW] --json` (Python: `gmf.diff_db(old_db, new_db)`, the same dict without `genmodelfit` /
`command`; #19). Compares two `db.json` files; no network, nothing written. Used for the weekly pull request
(`python -m genmodelfit.weekly`, [release.md](release.md)). No `system` / `database`.
With no arguments the bundled list is compared with the fetched list in the cache (`update` / `update --from-release`);
with one argument the bundled list with that file (#59). The CLI compares the raw JSON (keys this version does not know count, as in `merge-check`).
When there is no fetched list yet, the output has the usual keys with empty values (`old` / `new` / `policy` / `classification` null, `summary` with the usual keys and 0 (`head_unchecked` null), lists empty) plus `note` (says to run `genmodelfit update --from-release`), and the exit code is 0.

| key | meaning |
|---|---|
| `old`, `new` | `generated_at`, `models` (count), `license_policy`, `comfyui_latest_release`, `partial`, `comfyui_checked_at` |
| `policy` | the gate policy applied to **both** lists (GatePolicy.to_dict): `comfyui_version` = `--comfyui-version`, else the version recorded in the new list (else the old one); `max_head_age_days` = null (HEAD age ignored; HEAD status codes still count) |
| `summary` | `models_old`, `models_new`, `added`, `removed`, `license_changed`, `license_permissive_side`, `auto_old`, `auto_new`, `auto_gained`, `auto_lost`, `size_changed`, `precision_changed`, `newly_uncensored`, `head_failures`, `head_failures_new`, `head_unchecked` (new `sources.head_check.unchecked`), `unauthorized_added`, `unauthorized_removed` |
| `added`, `removed` | `{id, name, modality, content, source, license}` (`license` is the label: `mit`, `other(<name>)`, `不明` for unknown) |
| `license_changes` | entries whose `(license, license_name)` changed: `{id, name, old, new, old_tier, new_tier, allowed_old, allowed_new, permissive_side}`. Tiers are `hf.license_tier` (1 permissive … 6 unknown). `permissive_side` = the tier went down (unknown → anything included) or the license newly passes the allowlist |
| `auto_changes` | entries whose auto result differs (added / removed entries too; `old` / `new` is null when absent): `{id, name, old, new, held_by, held_by_old}` |
| `size_changes` | total size changed by ≥ 10 % **and** ≥ 100 MiB (or became known / unknown): `{id, old_bytes, new_bytes, ratio}` |
| `precision_changes` | `{id, old, new, old_source, new_source}` |
| `newly_uncensored` | `content` became `uncensored`, or an added `uncensored` entry: the `added` fields + `evidence` (`content_evidence`), `added` (bool) |
| `head_failures` | URLs in the new list whose HEAD answered other than 200 / 302 / 307: `{url, status, ids, new}`; `new` = that URL did not fail (or was absent) in the old list |
| `unauthorized` | `{added, removed}`: repo ids in `sources.huggingface.unauthorized` (401) that appeared / disappeared |
| `partial_counts` | the new list's `sources.partial_counts` (null if absent) |
| `classification` | 0.6.0 (#42): `{safe, judgement_needed, safe_changes}`; see below |
| `old_label`, `new_label` | #59: what was compared, for display: `同梱の一覧(genmodelfit <version>)` (bundled list), `取ってきた一覧(<cache path>)` (fetched list) or the file path as given; null from `gmf.diff_db` unless passed |
| `field_changes` | #59: `{id: [field, …]}` for every common entry with any change, including the safe ones. Fields are the names of `diff.entry_changes`, the same function the classification uses: top-level keys (`languages`, `license`, `review`, …), `files` (files added / removed / reordered), `files.<key>` (a key of a paired file), `template.<key>`. Files are paired in tiers so that same-named files are not mixed up: same (name, url, sha256, directory, repo_id) → same (directory, name, sha256) → same (directory, name, url) → the rest by (directory, name) in order; a changed name or directory is a removed + added file. A key that is missing on one side and null on the other is not a change |
| `model_changes` | #59: common entries with a change outside the safe list or an auto-recommendation change: `{id, name, modality, items, other}`. `items` has one item per change (shapes below). `other`: the remaining changed field names (shown as "other changes:", or 「その他の変化:」 in Japanese). Every changed field outside the safe list is in `items` or `other` |
| `top_changes` | #59: changed top-level keys of the lists other than `generated_at` / `sources` / `models` (only when the raw JSON is compared: `genmodelfit diff` and `merge-check` do; `gmf.diff_db` without `raw` gives []) |
| `warnings` | #59, CLI only: notes about the comparison: the new list is older (`generated_at`) than the old one (any argument form; with no arguments it also says to run `genmodelfit update --from-release`) |
| `stats` | changed values of `sources`, flattened to dotted keys (`counts.modality.image`, `head_check.status.302`, …): `{key, old, new}`; lists are compared by length; `checked_at` / `url` / `api` / `generator` / `failures` / `errors` are skipped |

Shapes of `items` in `model_changes`:

- `{kind: "value", field, old, new}` for `name` / `modality` / `tasks` / `runtime` / `comfy_family` / `content` / `precision`
- `{kind: "license", old, new}` (labels)
- `{kind: "languages", key, old, new}` (`key` is `prompt` / `speech` / …; null when absent; absent vs empty list is not an item)
- `{kind: "file_added" | "file_removed", name, directory, size_bytes, license, role, source}` (`source` = the HF repo, else the URL host and path)
- `{kind: "files_order"}`
- `{kind: "file_changed", name, field, old, new}` (`field` is `url` / `sha256` / `repo_id` / `directory` / `gated` / `role` of a paired file)
- `{kind: "file_license", name, old, new}`
- `{kind: "license_source", name, field, old, new}` (where the license was checked: the entry's `license_url`, or a file's `license_url` / `license_from` / `license_path` / `repo_license`; `name` is the file, null for the entry)
- `{kind: "size", old, new}` (total bytes, any change)
- `{kind: "review", old, new, reasons, fields}` (`old` / `new` = the status, null when there is no review mark; the new reasons; changed review keys)
- `{kind: "auto", old, new, held_by}` (as in `auto_changes`)

**classification** (0.6.0, #42) splits the changes so that the weekly run can tell whether a week may be merged
without the maintainer (`safe: true`; the runner recomputes it with `python -m genmodelfit.weekly merge-check`) or needs an
evidence pull request for the maintainer. It is an **allowlist**: for every common entry every field and every file field is
compared, and only the changes listed as safe below are safe; anything else, including fields this version does not
know, is judgement-needed.

| key | meaning |
|---|---|
| `safe` | true when `judgement_needed` is empty |
| `judgement_needed` | `[{id, name, reasons}]`, sorted by id (`id` `(database)` for a changed top-level key other than `generated_at` / `sources` / `models`, merge-check only). `reasons` are listed below, in this order |
| `safe_changes` | counts of changes seen in the safe direction: `removed`, `license_stricter`, `size_changed`, `precision_changed` (as in the diff; entries among them can still be judgement-needed), `head_failures_new`, `unauthorized_added`, `unauthorized_removed`, `stats` (changed `sources` values), `auto_gained_head_only`, `auto_lost`, `pending_carried` (the same pending `review` in both lists) |

`reasons` of `judgement_needed`, in this order:

- `new`: added entry
- `license_permissive`: the entry or file license tier went down or newly on the allowlist, or the gate's license check newly passes
- `need_decreased`: a file got smaller, or the estimate's needs went down (`review.review_reasons`)
- `uncensored_new`
- `files_changed`:
  - files added / removed / reordered
  - a file's `name`, `url`, `sha256`, `repo_id` or `directory` changed
  - its `size_bytes` changed in either direction, or became known / unknown (a smaller file also gets `need_decreased`)
  - growth counts since 0.9.0: apps download the listed URL as is, so a bigger file at the same URL is different content
- `use_changed`: `tasks` / `modality`
- `auto_gained`: newly auto-recommendable, unless the old list held it only by `url_reachable` **and** no file's name / url / sha256 changed
- `review_approved`: `review` went from not ok to ok
- `review_pending`: from ok to not ok (`status` must be exactly `ok`, so `"OK"` is not ok; the review's own reasons follow)
- then `other_change:<field>` for every other changed field: `other_change:name`, `other_change:languages`, `other_change:description`, `other_change:license_url`, `other_change:precision`, `other_change:template.<key>`, `other_change:files.<key>`, `other_change:license` for a same-tier license rename, `other_change:review` for any other review change, …

Safe (the allowlist; never in `judgement_needed` by itself): entry `downloads`, `downloads_all_time`, `likes`,
`updated`, `discovered_from` (where it was found; changes with the search order); template `usage`; file `head_status`, `head_checked_at`; a
license moving to a stricter tier (entry or file); removed entries; models dropping out of auto-recommendation;
auto-recommendation regained only because HEAD recovered with the same files; `sources` statistics; a pending `review`
carried over unchanged. Everything else is judgement-needed.

```json
"classification": {
  "safe": false,
  "judgement_needed": [
    {"id": "hf:org/new-model", "name": "New Model", "reasons": ["new"]},
    {"id": "image_example", "name": "Example", "reasons": ["license_permissive", "auto_gained"]}
  ],
  "safe_changes": {"removed": 1, "license_stricter": 0, "size_changed": 2, "precision_changed": 0,
    "head_failures_new": 3, "unauthorized_added": 0, "unauthorized_removed": 1, "stats": 12,
    "auto_gained_head_only": 1, "auto_lost": 2, "pending_carried": 0}
}
```

`python -m genmodelfit.weekly --out DIR` writes `DIR/db.json`, `DIR/diff.json` (the dict above), `DIR/pr_body.md`,
`DIR/review_queue.json` (0.6.0: `{genmodelfit, generated_at, pending: [review list items], classification}`) and
`DIR/result.json`: `genmodelfit`, `ok`, `problems` (str[], why it failed), `base`, `offline`, `run_url`, `partial`,
`status` (DataStatus of the new list), `status_old` (of the base list), `warnings` (str[]), `summary` (as above), `classification` (`{safe, judgement_needed: count}`),
`review_pending` (the review list items), `files` (`db`, `diff`, `pr_body`, `review_queue`), and `applied_to` with
`--apply`. The pull request body has a 「分類」 (classification) section and a 「確認待ち」 (pending review) table (names escaped like every upstream
text). Exit codes are unchanged: a judgement-needed week is still `ok` (exit 0).

`python -m genmodelfit.weekly merge-check --base <main db.json> --head <new db.json> [--json]` recomputes the
classification from the two files (raw JSON, so unknown keys count): `{genmodelfit, command: "merge-check", base, head,
safe, judgement_needed (count), reasons ([{id, name, reasons}]), safe_changes}`. Exit 0 = safe, 3 = not safe,
1 = a file cannot be read (`error` with `--json`), 2 = usage error. No network, nothing written.

## review

Review marks (0.6.0, #42). New or riskier entries go into the list but are **not auto-recommended until someone has
checked them against primary sources**. `ModelEntry.review` is an optional object (`schema_version` stays 1; readers
up to 0.5.0 drop the key):

| key | type | meaning |
|---|---|---|
| `status` | str | `ok` or `pending` (any other value is treated as not ok) |
| `reasons` | str[] | why it was marked: `new`, `license_permissive`, `need_decreased`, `uncensored_new`, `files_changed` (kept after approval: what was checked) |
| `decided_by` | str \| null | who approved (`--by`; the bundled list starts with `baseline-2026-10-21`) |
| `decided_at` | str \| null | `YYYY-MM-DD` (UTC) of the approval |
| `evidence` | str[] | https URLs of the primary sources (license text, model card, …) |
| `note` | str \| null | free text |

**A missing `review` (null) means ok** (lists made before 0.6.0). **Apps should filter with
`entry.review is None or entry.review.status == "ok"`** (Python: `entry.reviewed`); the gate already holds pending
models (`reviewed`; `info` shows it as a failed check).

How `update` sets it (`review.apply_reviews`, comparing with the list it carries over from: the cache or the bundled
list, or the base list in the weekly run):

- an id absent from the previous list → `pending` with `new` (plus `uncensored_new` when uncensored). Without any
  previous list (`carry_over=False`) every entry is `new`
- a common entry → `pending` when its license moved toward permissive (`license_permissive`: `hf.license_tier` went
  down, the set's license is newly on the allowlist, or the gate's `license` check newly passes), its needs went down
  (`need_decreased`: at one of the reference PCs `review.REFERENCE_PCS` = (0 GB VRAM, 16 GB RAM), (6, 16), (8, 16),
  (12, 32), (16, 32), (24, 64) the fit level went up, the size became known, or the full-load VRAM or the RAM went down
  by more than 1 %; estimated only when a need-related field changed), or it newly became `uncensored`
  (`uncensored_new`), or its files changed (`files_changed`: a file added / removed, a file's `url` (only a `#fragment`
  is ignored; the query string counts), `repo_id` or `size_bytes` changed in either direction or became known / unknown (since 0.9.0 growth counts too: the same URL now serves different content), or a `sha256` that had a value differs or went empty; a `sha256` that was
  empty and is now filled in, the first check, does not count. Files are paired as `diff` does). Reasons are added to an entry that was already pending
- otherwise the previous mark is carried over (`ok` with a note when the previous list had none)

`update --heads-only` / `--fields-only` / `--inspect-only` apply the same rule against the list as it was before the
run. `sources.review`: `{ok, pending, marked: {reason: count}, rejected_skipped}`.

The signed list published to `data-latest` (data.yml) leaves out pending entries
(`python -m genmodelfit.review publish`; `sources.review.withheld_pending` counts them): genmodelfit 0.5.x drops the
unknown `review` key and would auto-recommend them. The bundled `db.json` on main keeps them (0.6.0 holds them by the
`reviewed` check).

Rejected entries stay out: `src/genmodelfit/data/review_rejected.txt` (bundled), one line per entry
`<id or sha256:<hex of the id>><TAB><YYYY-MM-DD><TAB><reason>`, `#` comments. `update` drops matching ids
(`rejected_skipped`). With `--hide-name` only the hash is stored (like the safety exclusions, the name is not kept).

Commands (`--db` is required for writing; in a checkout use `--db src/genmodelfit/data/db.json`):

```text
genmodelfit review list [--json] [--all] [--db PATH]
genmodelfit review approve <id> --evidence <https URL> [--evidence …] [--note TEXT] [--by NAME] --db PATH [--json]
genmodelfit review reject <id> --note TEXT --db PATH [--rejected FILE] [--hide-name] [--json]
```

`approve` refuses (exit 1) without at least one evidence URL that is `https` with a host (`https://` alone is refused), for an unknown id, or when the entry is not
pending; it keeps `reasons` and sets `status: ok`, `decided_by`, `decided_at` (today, UTC), `evidence` and `note`.
`reject` requires `--note`, removes the entry, updates `sources.counts` / `sources.review` and appends the line.
`--json` (no `system` / `database`): `approve` prints `{genmodelfit, command: "review", action: "approve", id, db,
review: {status, reasons, decided_by, decided_at, evidence, note}}`; `reject` prints `{genmodelfit, command: "review",
action: "reject", id, key (the id or `sha256:<hex>`), date, note, rejected_file, db}`; a refusal prints
`{genmodelfit, command: "review", error}` and exits 1.
Python: `gmf.review_list(db=None, include_ok=False)`, `gmf.review_approve(db_path, id, evidence, note=, by=)`,
`gmf.review_reject(db_path, id, note, rejected_path=, hide_name=)`; they raise `review.ReviewError` (a `ValueError`)
and print nothing.

`python -m genmodelfit.review apply DECISIONS.json --db PATH [--max-approve 10] [--rejected FILE] [--json]` applies the
weekly decisions (the runner script calls it; Claude only writes the decisions). Input:
`{"decisions": [{"id", "action": "approve"|"reject"|"hold", "evidence": [https URLs with a host], "note": str,
"safety": bool}]}`. Validation is strict and all-or-nothing (exit 2, nothing written): unknown keys at either level,
unknown actions, ids that are not pending, the same id twice, `approve` without evidence or with `safety`, `reject`
without a note, a note over 500 characters or with control / format characters (newlines, NUL, ESC, bidi overrides,
zero-width characters), non-boolean `safety`, more approvals than `--max-approve`. Approvals get
`decided_by: "claude-weekly"`; a reject with `safety: true` stores only the id hash (`--hide-name`). Output (`--json`):
`{applied, db, rejected_file, counts: {approve, reject, hold}, pending_left, results: [{id, action, result, safety,
evidence, note}]}` with every string escaped by `weekly.md` (safe in a pull request body; a safety reject shows only
the start of the hash and no note). Exit 0 applied, 2 invalid decisions, 1 unreadable `--db`.

Items of `review list --json` / `review_queue.json` also have `card_url` (the Hugging Face card: the repo of an `hf:`
entry, else the main file's license source or host repo, else `homepage`) and `files`:
`[{name, repo, path, url, license, license_name, license_from, license_url, role}]` (`repo` / `path` parsed from the
Hugging Face URL). Strings are data (not escaped) in JSON.

Duplicate model ids are refused everywhere (0.6.0, #42): `parse_db` / `load` / `Database.save` raise
`DatabaseError`, and `merge-check` exits 1 for a file with a duplicate id (otherwise `get()` would silently pick one
of two entries).

`review list --json`:

```json
{
  "genmodelfit": "0.12.0",
  "command": "review",
  "database": {"origin": "path", "generated_at": "2026-10-28T03:12:00Z", "models": 331, "measurements": 21},
  "pending": 1,
  "models": [
    {"id": "hf:org/new-model", "name": "New Model", "modality": "image", "content": "sfw",
     "source": "huggingface", "license": "apache-2.0", "license_url": "https://huggingface.co/org/new-model",
     "homepage": "https://huggingface.co/org/new-model", "status": "pending", "reasons": ["new"],
     "decided_by": null, "decided_at": null, "evidence": [], "note": null,
     "card_url": "https://huggingface.co/org/new-model", "files": []}
  ]
}
```

## info

`model` (ModelEntry), `estimate`, `gate`, `measurements` (Measurement[]), `group_key`, `same_model` (the other
entries of the same model, each like a `workflows` item, judged for this PC, best first), `model_name`, `community`
(bool, as in Fit).
`info <query>` also accepts a model's display name (exact, case-insensitive): then the model's best workflow for
this PC is shown.

How `info <query>` picks the model:

- An exact id always matches, whatever its content (uncensored included).
- Otherwise the query is matched as a part of the id or name (case-insensitive), **filtered by `--content`
  (default `sfw`)**. Exactly one match → that model.
- Two or more matches → never picks one: exit code 1. Text mode lists the candidates on stderr; `--json` prints
  the envelope with `error` and `candidates` (`[{"id", "name", "content"}]`, all of them).
- No match (or only uncensored matches under `sfw`) → exit code 1 with `error` (the message suggests `--uncensored`).

Python `gmf.info(model_id)` matches the exact id only and returns `None` otherwise.

### ModelEntry

`id`, `name`, `modality`, `tasks`, `source`, `files` (ModelFile[]), `template` (Template \| null), `params_b`,
`precision`, `license`, `license_url`, `base_model`, `content`, `content_evidence` (str[]: why it is marked
uncensored), `discovered_from` (str[]: where it was found), `updated`, `homepage`, `downloads` / `likes`
(int \| null: Hugging Face counters, set for `source: huggingface`; `downloads` is the last 30 days),
`downloads_all_time` (int \| null: all-time downloads, used for the inclusion floor), `total_size_bytes`,
`license_name` (the name of an `other` license, from the strictest file), `license_evidence` (str[]: why the license is
unknown — entries start with `license_unverified:`), `files_complete`, `size_floor_bytes` (below),
`workflow_precision` (str \| null: precision inferred from the template's main loader node, see "Models" above;
optional, 0.2.0), `precision_source` (str \| null: `header` / `filename` / `hf_api` / `config` / `workflow`, where
`precision` came from; null = unknown or older data; optional, 0.2.0).

**For apps that build their screens from the list alone** (0.5.0, #36; all optional, `schema_version` stays 1 — older
readers drop unknown keys, see "Database file"):

- `languages` (`{"prompt": str[], "speech": str[]}`, ISO 639-1 codes): `prompt` = languages the text prompt works in,
  `speech` = languages spoken or sung in the output (TTS voice, lyrics). A key that is missing means unknown; `[]` means
  the model has none (image-to-3D takes no prompt text, a sound model without vocals has no speech). Rebuilt from its inputs on every
  apply (nothing carries over from the previous list; [sources.md](sources.md), "Language, runtime, description, and manually added models"): the hand table
  `src/genmodelfit/data/overrides.json` (or the entry's own `languages` in `src/genmodelfit/data/manual.json`) wins for the keys it sets; otherwise
  the `language` of the Hugging Face card (saved as `card_languages`) is the upper bound — `speech` is the card's
  list, `prompt` is the card's list, narrowed to `en` when every text encoder is English-only (CLIP / T5) and for
  music (a music card's `language` usually lists lyric languages, so a music entry gets at most `["en"]` from the card
  and no `speech`; 0.6.0, #41); without a
  card, a known text encoder gives `["en"]` (a multilingual encoder alone never adds `ja`). Nothing decides → the key
  is left out (no guessing). Test membership of the language you need.
- `card_languages` (str[] \| null): the Hugging Face card's `language` saved by `update` / `update --fields-only`
  (the material for `languages`, so an offline re-apply gives the same result). `[]` = the card was read and has no
  language, null = not read.
- `runtime` (str \| null): `comfyui` for official templates and for Hugging Face image / video / music / sfx / 3D entries
  whose files match a ComfyUI family; `piper`, `irodori-tts` for the hand-added speech models; null = not decided. A
  runtime other than `comfyui` (and null) makes the estimator count `.onnx` and other non-ComfyUI weight files.
- `description` (`{"ja": str, "en": str}`): a short hand-written description, only for the models that have one.
- `source: "manual"` (id `manual:<slug>`): models added by hand in `src/genmodelfit/data/manual.json` (`template` is null). Their license
  per file was read from the upstream card by the person who added them (`license_evidence` starts with `manual:`); `update`
  compares it with the card of `license_from` and takes the stricter one on a mismatch (with a warning).
- Hand-added components (0.6.0, #40): `components` in `src/genmodelfit/data/manual.json` adds files that the Hugging Face collection
  does not pick up to an entry of another source (first: the T5Gemma text encoder of
  `hf:stabilityai/stable-audio-3-small-sfx`, Gemma Terms of Use). Their license is never written by hand: it comes from
  the per-file source table (`license_sources.py`; no row → unknown), and `license_evidence` ends with
  `manual_component: <why this model uses the file>`. `update` (all modes) applies them on every run.

**License contract**: `license` is the strictest license over **all** files (LoRAs, upscalers and clip_vision included).
`null` means unknown and must be treated as "not allowed". A license is never more permissive than what could be verified
upstream ([sources.md](sources.md), "Licenses").

**Size contract** (`files_complete` / `size_floor_bytes`):

| `files_complete` | meaning | what an estimator must do |
|---|---|---|
| `true` | the listed files make up the whole set | use `total_size_bytes` |
| `false` | the list is known to be incomplete (no main model file, files < 50% of the template's index `size`, or a GGUF main file without text encoder / VAE) | never treat the entry as small: use `max(total_size_bytes, size_floor_bytes)` (`ModelEntry.estimate_size_bytes`); if `size_floor_bytes` is null, treat the size as unknown (not judgeable) |
| `null` | not checked (older data) | treat as `true` |

`size_floor_bytes` is the template's index `size` when `files_complete` is false (null for Hugging Face entries, which
have no index size).

`sampler_stages` (str[][] \| null, optional; 0.1.1): for official templates, the model files each sampler (KSampler,
KSamplerAdvanced, SamplerCustom, CFGGuider, …) loads through its MODEL input (diffusion model, LoRAs, model patches),
one list per distinct set, in graph order (`workflow.sampler_stages`, kept only for sets that contain one of the entry's
main files). Set only when there are two or more different sets of main files (`[]` = one set, `null` = not checked; HF
entries are null). Used by the estimator as above.

`review` (object \| null, optional; 0.6.0, #42): the review mark, see "review". null = ok (older lists).

`comfy_family` / `comfy_vae` (str \| null, optional; added in #21 — older files without them still load): the ComfyUI
model family and VAE kind (keys of `families.FAMILIES` / `families.VAES`), set by `update` from the main / VAE file
names. Used to decide whether ComfyUI computes in fp32 on GPUs without bf16 ([hardware.md](hardware.md), "Family table")
and, since #100, which per-stage working-memory formula applies (`working.py`; [accuracy.md](accuracy.md), "Formulas").
#100 split the VAE kinds `wan22` (Wan 2.2), `qwen21` (Qwen Image 2.1), `flux2` (the Flux2 VAE), `oobleck`
(ACE-Step 1.5, Stable Audio Open), `yue2` and `pixel` (pixel space) out of `wan` / `kl` / null; `schema_version` stays 1
(older lists keep the old keys, and the estimator maps them to the family's VAE).
null = unknown (the estimate is not changed; a reason says so on GPUs without bf16). When null, the estimator derives
them from the file names again.

ModelFile: `name`, `url`, `sha256`, `size_bytes`, `directory` (ComfyUI folder), `repo_id`, `license`,
`license_name`, `gated` (bool \| null = unknown), `role` (`main` / `text_encoder` / `vae` / `clip_vision` / `lora` / `other`),
`license_from` (the repo the license was read from: the upstream repo for repackaged files, the table's upstream for
known components; a repo id, never a path), `repo_license` (the hosting repo's own label when it differs from the
upstream one), `license_url`, `license_path` (str[]: the derivation path that was checked — hosting repo → base_model … →
deciding repo), `license_evidence` (str[]: `license_unverified: …`, `component:<key>`, `family:<key>`, `upstream_gated: <repo>` — the component's upstream requires accepting terms),
`head_status` (int \| null: status of an anonymous HEAD without following redirects, recorded by `update`;
null = not checked), `head_checked_at` (UTC ISO-8601 of that HEAD), `header_precision` (str \| null: precision read
from the file's own header — safetensors: the dtype holding most bytes, `F32`→`fp32`, `F16`→`fp16`, `BF16`→`bf16`,
`F8_*`→`fp8`, `I8`→`int8`, packed 4-bit `U8` → null; GGUF: `general.file_type`, else the commonest tensor type, as
`gguf-q8_0` etc.), `header_params` (int \| null: total elements of all tensors in the file). Both are read by
`update` / `update --inspect-only` for Hugging Face main weight files only (`headers.py`; ranged GET of the first 8 bytes
and the JSON header, or of the first MiB(s) of a GGUF, never the weights), null = not read. Main files are the
`main` role plus what `groups.main_files` treats as the main file (the largest non-encoder / non-VAE file when no
file has the `main` role, e.g. BiRefNet; 0.2.0).
`repo_precision` / `repo_precision_source` (str \| null, optional, 0.2.0): precision read from the hosting repo
(`hf_api` or `config`, see "Models"), set only for the largest main file when its header and name tell nothing.
`optional` (str \| null, optional, 0.12.0, #81): a part the official template does not load with its default values:
`inactive` (only on muted or bypassed nodes, or on nodes that reach no output node; counted in none of VRAM, memory,
`disk_gb` and `download`) or `switch_off` (only on the unselected branch of a switch with a fixed value; not counted in
VRAM or memory, counted in `disk_gb` and `download`). null = loaded by default (or not known: HF and manual models). An
unknown value is treated like null (counted). `optional_replaces` (str[] \| null, optional): for a `switch_off` part, the
file names of the default parts no longer loaded when its switch is flipped (empty or null = only added). How they are
found: [sources.md](sources.md), "Optional parts". Older readers drop both and count everything as before.
The estimator decides a file's part from `directory` first, then `role`, then the Hugging Face folder / known file
names, and counts one variant per part ([accuracy.md](accuracy.md), "How files are counted").

Template: `name`, `title`, `category`, `min_comfyui_version`, `open_source`, `requires_custom_nodes`, `usage`,
`date`, `size_bytes`, `username` (who made it; not `ComfyUI` = community template).

### Measurement

`model_id`, `gpu_name`, `gpu_vram_gb`, `peak_vram_gb`, `peak_ram_gb`, `seconds` (wall time), `settings`
(`width`, `height`, `frames`, `seconds` = audio length, `steps`, `precision`, `batch`), `driver`,
`comfyui_version`, `offload`, `source`, `date`, `note`, `outcome`, `standard`, `vram_metric`, and (#34) `origin`,
`n_submissions`, `consistent`, and (2026-10-22, optional) `n_used`, `n_oom`, `gpu_backend`, `gpu_pci_id`.

| key | type | meaning |
|---|---|---|
| `gpu_vram_gb` | float | total VRAM. bench stores `/system_stats` `vram_total` in GiB rounded to 2 decimals (e.g. `7.99`); older bundled rows hold the nominal size (`16`) |
| `offload` | str \| null | how ComfyUI placed the model: `none` (all on GPU) / `dynamic` (dynamic VRAM) / `partial` (partially loaded) / `lowvram` (`--lowvram` / `--novram`) / `oom` (ran out of memory) |
| `outcome` | str | `ok` (finished) / `oom` (out of memory) / `error` (failed for another reason). Default `"ok"` |
| `vram_metric` | str \| null | how `peak_vram_gb` was taken: `torch_reserved` (bench: peak torch reservation of the ComfyUI process, excluding the CUDA context) or null (not recorded; the bundled RTX 5080 rows). Calibration uses only rows with null (`calibrate.CALIBRATION_METRIC`). #100: `torch_reserved` with `offload: dynamic` excludes the weights (working memory only; `estimate.working_only`): such rows neither shift the estimate nor set the level, and the cross-check compares them with the working-memory estimate. Other `torch_reserved` rows include the weights (a lower bound of the total) |
| `origin` | str \| null | (#34) `community` for rows of `measurements_community.json` (other users' submissions aggregated by `contrib aggregate`); null for the maintainer's bundled rows and this PC's bench results |
| `n_submissions` | int \| null | community rows: submissions in the group (outliers and OOM included). The file also keeps `n_outliers`, `spread_mad_gb`, `spread_rel_pct`, `peak_vram_min_gb` / `peak_vram_max_gb`, `outlier_submissions` and `submissions` (sha8 of each file), which the reader ignores |
| `n_used` | int \| null | community rows: `ok` submissions behind the median (outliers removed). Was `Estimate.sample_count` up to 0.10 (`n_sources` since 0.11.0) |
| `n_oom` | int \| null | community rows: submissions of the same group that ran out of memory. A group with `ok` and `oom` uses the `ok` median and keeps this count (shown in the reasons; such a row never makes Perfect) |
| `consistent` | bool \| null | community rows: at least 2 used submissions whose range is within 15% of the median, and no OOM in the group. Only consistent rows enter the calibration of the coefficients (`calibrate`); estimates use every `ok` row from one submission (2026-10-22) |
| `gpu_backend` | str \| null | (0.9.0) PyTorch backend of the measured GPU as bench saw it: `cuda` (NVIDIA) / `rocm` (AMD; ComfyUI reports `type: cuda`, told apart by `+rocm` in `pytorch_version` or the GPU name) / `xpu` (Intel) / `mps` (Apple). null = not recorded (all bundled rows are NVIDIA). Decides the measurement's vendor for #26 and calibration across vendors (`estimate.measurement_vendor`); the GPU name is used only when it is null (Apple's MPS reports the name `mps`). `contrib aggregate` keeps it on community rows |
| `files_fp` | str \| null | (#90) fingerprint of the model's files in the list when measured (`estimate.files_fingerprint`; see "Rules of the submission pipeline" below). If it differs from the current list's fingerprint, the measurement is not used. null = not recorded (the maintainer's measurements.json, older rows; used as before) |
| `settings_override` | bool \| null | (0.12.0, #116; optional) `true` when bench / measure changed the template's settings (`--width`, `--height`, `--resolution`, `--frames`, `--length`, `--steps`). null / absent = as in the template (older rows without the key too). `measure` uses it to tell whether this PC has measured the template's settings on this GPU |
| `gpu_known` | bool \| null | (#90, #101) community rows: false = the GPU name is a generic name or a placeholder (kept as a record, never used for judging) |
| `n_sources` | int \| null | (#90) community rows: number of independent sources (the people who sent the submissions; #113) behind the value |
| `source_ids` | str[] \| null | (#95) community rows: short IDs of the sources behind the value (first 12 characters of the sha256 of the sender key, or `file:<first 12 of the content sha256>`). When measurements set the level, sources are counted as the union of the IDs across rows (`estimate.count_sources`). null for older rows without them |
| `n_error` | int \| null | (#90) community rows: submissions of the same group that ended in `error` (not used for the value) |
| `runs_evidence` | bool \| null | (#90) community rows: false = OOM sources are at least as many as ok sources (not evidence that it runs; how it is used is the level rule of PR #95) |
| `gpu_pci_id` | str \| null | (0.9.0) PCI model ID `vendor:device` of the measured GPU (`1002:744c`; a model number, never the bus address or a serial). bench writes it for AMD / Intel when ComfyUI runs on this PC (`hardware.local_pci_id`: Windows WMI `PNPDeviceID` VEN/DEV, Linux `lspci -nn`). Community rows are grouped by it when present. `estimate.same_gpu` compares IDs when both sides have one, else the normalized name (`estimate.gpu_key`; generic names such as `AMD Radeon(TM) Graphics` never match); since 0.9.1 the VRAM ratio must also be 0.95 or more (`estimate.same_vram`, `MEASURED_VRAM_TOLERANCE`; 0.97 until 0.10; not for shared / unified memory) |
| `standard` | bool | `true` when the official template was used as is (only width / height / frames / length / steps / seed changed), every reachable node actually ran (no partial cache) and no other job ran in between. Default `true` |

`outcome` and `standard` were added later as optional fields with defaults, so older files without them still read
as `ok` / `true` (`schema_version` stays 1). Estimates use only measurements with `outcome == "ok"` and
`standard != false`; OOM rows are still welcome as data but must keep `outcome: "oom"`.

## bench

`genmodelfit bench <template> --json` prints the envelope (without `system` / `database`) plus:

| key | type | meaning |
|---|---|---|
| `measurement` | Measurement | the result (also saved to `saved_to` when saved) |
| `saved_to` | str \| null | where the JSON was written (`--output`, default `<cache>/measurements/<model>_<gpu>_<UTC>.json`); null when not saved |
| `not_saved` | str | only when not saved: why (failed or non-standard results are not cached unless `--save-failed` / `--save-nonstandard` / `--output`) |
| `workflow_source` | str | `server-template` / `github-template` / `file:api` / `file:ui` |
| `warnings` | str[] | settings that could not be applied, uploaded inputs, … (in the display language, `en` / `ja`) |
| `failed` | str | only when the run failed in ComfyUI (OOM or another execution error; exit code 1). `measurement.outcome` is `oom` / `error` |
| `timing` | object \| null | (0.13.0, #132) the time breakdown, the same keys as `measurement.timing` of a contrib submission (see contrib; unknown values are null) |
| `submission` | object | only with `--submission` or `--share`: `{saved_to, path_in_repo}` (the submission JSON, see contrib); with `--share` also `share` (the `file`, `ok`, `refused`, `checklist`, `report`, `share`, `opened`, `copied`, `marked_shared` of `share --json`; no browser is opened) |

How bench fills Measurement:

- `peak_vram_gb`: max of `/system_stats` `devices[0].torch_vram_total` polled 0.015 s apart
  (`bench.POLL_INTERVAL`; about every 0.03 s with the wait for the answer; 0.2 s apart up to 0.13.2) (the ComfyUI process,
  without the CUDA context). The GPU-wide delta from the pre-run baseline goes to `note`: `nvidia-smi` for NVIDIA;
  for other GPUs (0.9.0, `gpustat.py`) `amd-smi` → `rocm-smi` (AMD, Linux), `xpu-smi` (Intel, Linux), the Windows
  performance counters via `typeperf` (AMD / Intel, Windows) and `ioreg` (Apple). The same tools give the
  pre-run busy check (average utilization ≥ 30% refuses without `--force`). When no tool can be read, bench does
  not stop: it checks that the ComfyUI queue is empty and that apps other than ComfyUI hold at most 50% of the
  device memory (not on Apple's unified memory), then measures, with a warning and a `note` sentence;
  `standard` is not changed ([accuracy.md](accuracy.md)). A remote ComfyUI still needs `--force`.
  - Brief spike (0.14.0, #131): when the third-largest value is more than 0.1 GiB and more than 10% below the
    maximum, the maximum was a spike shorter than the polling interval and varies from run to run. Only runs that
    finished (`outcome: ok`) are checked. The peak is shown in the result, `standard` becomes false and the non-standard conditions in `note` say that the peak was a brief spike,
    with the highest value read at least 3 times (used neither for the cross-check nor for levels). With fewer than 10 reads
    this is not decided. measure and share neither save nor send such a run; they remember it as "tried" in
    `brief_peaks.json` in the cache and do not suggest the same one again (not sent). bench saves it only with
    `--save-nonstandard` or `--output`. Submissions that probably missed the peak with 0.2 s polling are listed in the
    bundled `contrib_nonstandard.json` (content sha256 → reason), and `contrib aggregate` does not use their values. Why: [accuracy.md "Measurement variance (#131)"](accuracy.md#measurement-variance-131).
- `peak_ram_gb`: max resident memory of the process listening on the port (and its children), only when its
  command line contains `main.py` or `comfy`; null when remote or not identified.
- `seconds`: wall time from submit to completion, including model loading. `/free` is sent first only with `--free`;
  without it the pre-run torch allocation is written to `note` and a warning is printed.
- `offload`: read from the ComfyUI log: `dynamic` / `partial` / `lowvram` / `none` / `oom`, or null when unknown.
- `--set` values: only numbers are written to `note`; for other values only the input name is kept.
- The saved file is `{"measurements": [Measurement]}` and holds no paths, user names or prompts.

## measure

`genmodelfit measure [MODEL] --json` (#113) prints the envelope (with `system` and `database`) plus `action`:

- Without MODEL, `action: "suggest"`: `comfyui` `{url, found_by, start?}` (`found_by` is `option` for `--url`, `running` for
  a ComfyUI found on 127.0.0.1 ports 8188 / 8000, null when none was found; `start` (0.13.0) `{kind, comfyui_dir}` when
  none runs and an install would be started: `kind` is `portable`, `venv` or `desktop`; `version` (0.14.0, #147) the
  current ComfyUI version when known, see "Remeasure" below) and `suggestions`
  [{model_id, model_name, name, modality, precision, fit_level, installed, download_bytes, total_bytes, seconds,
  seconds_basis, settings, args, remeasure}]. Here `installed` is only the state string (`yes` / `no` / `unknown`, see
  "Installed" below); with MODEL it is an object. `settings` (0.12.0, #116) is null for a run as in the template, else the changed
  settings (`{width, height}`, `{frames}` or `{seconds}`); `args` is the same as `measure` / `bench` arguments
  (`["--resolution", "1536x1536"]`, empty for the template's settings). Nothing is measured.
- With MODEL (needs `--yes`), `action: "measure"`: `model_id`, `settings` (0.12.0; the settings given with
  `--resolution` / `--frames` / `--length` or chosen from the list, null when as in the template), `comfyui`, `installed`
  `{state, missing_bytes, total_bytes, missing_files}`, `time_hint` `{seconds, basis}`, then the keys of `bench`
  (`measurement`, `workflow_source`, `warnings`, `failed?`, `saved_to`, `not_saved`), `submission`
  `{saved_to, path_in_repo}` (null when no submission was written) and `hint` (the line about `genmodelfit share`, or
  null). On a failure the same keys up to `time_hint` and `error`.

Rules:

- **Suggestions**: models from an official ComfyUI template (`source: comfyui-template`) whose judgment on this PC is
  `perfect` or `good` with run mode `gpu`, with a known license (`--any-license` in `share` includes the others), and
  that this PC has not measured on this GPU at the template's settings yet (0.13.0: only this PC's own results count,
  `measure.measured_by_this_pc`: the cached bench results and the saved submissions, not the bundled author or community
  rows, which still feed the time hints and the estimates; `measure.has_measurement_here`: same GPU by `estimate.same_gpu`, same files by `files_match`, standard, outcome `ok`
  or `oom`, no `settings_override` (0.12.0; rows without the key count); a working-memory-only measurement counts too,
  since measuring it again on the same GPU adds nothing). One per model (`groups` key): the
  installed workflow, else the one `recommend` would pick. Order: installed first, then `seconds`, then
  `download_bytes`, then the name. Default 10 (`--limit`).
- **Remeasure** (0.14.0, #147; `measure.stale_version`): a workflow (or a run at other settings) that this PC has
  already measured on this GPU is offered again when every one of those runs recorded a ComfyUI version and none
  of them is the current one (an update can change the memory and time used). `remeasure` is then
  `{from, to}` (the version of the newest run, the current one; null for the others), the line says "ComfyUI 0.19 →
  0.20 で測り直し" ("remeasure: ComfyUI 0.19 → 0.20"), and these rows come after every row never measured (the same
  order inside), also within one model. With the same version, an unknown current version, or any run without a recorded
  version (it may have been the same version), nothing is offered again (one account counts once however often it sends the same thing). The current
  version comes from the running ComfyUI's `/system_stats` (`system.comfyui_version`; with `--dry-run` only from one
  on this PC), or, before an install is started, from the text of `comfyui_version.py` (`__version__ = "x.y.z"`), else
  the `[project]` `version` of `pyproject.toml`, next to its `main.py` (`comfystart.installed_version`; read as text,
  never imported, so the plan is decided before starting). genmodelfit has no separate version of its measuring
  method: `vram_metric` stays `torch_reserved` for every result of this PC, so only the ComfyUI version is compared.
- **Runs at other settings** (0.12.0, #116; `measure.variants`), mixed into the same list and order, to check the
  formula's per-stage working memory: text-to-image workflows (`t2i`, not in `estimate.NO_SIZE_INPUT`) at 512 / 768 /
  1024 / 1536 px square (`VARIANT_PIXELS`), text / image-to-video (`t2v`, `i2v`) at 17 / 33 / 49 / 81 frames
  (`VARIANT_FRAMES`; size as in the template), text-to-audio music and sfx (`t2a`) at 10 / 30 / 60 / 120 s
  (`VARIANT_SECONDS`), after the #102 plan. They are offered only for a workflow that this PC has already measured on
  this GPU at the template's settings (so that run is collected first; measuring a run at other settings does not
  hide the template's row), and not for an item the template has no input for: when a measurement at the template's
  settings (`measure.template_settings`, any GPU) records no width, frames or length, there is no variant for it
  (AnimateDiff, whose frames come from `batch_size`). One is listed when its estimate at that setting is `perfect` /
  `good` on `gpu` and this PC has not measured it at that setting on this GPU (`has_measurement_here` with `settings`: the given
  items must match, other items are not compared, with or without `settings_override`; so the template's own value,
  such as 1024 px, is not offered again). Video runs are judged at the template's width and height from that
  measurement (`measure.judged_settings`), else at the per-type default 832x480. One per model and setting (chosen
  like above), at most `measure.VARIANTS_PER_MODEL` (2) per model after ordering, so measuring one shows the next.
- **Time limit**: rows whose time is over `measure.MAX_SUGGEST_SECONDS` (300 s) are not listed (`measure` is one run
  of 1–5 minutes). Video runs at other settings whose time is the per-type rough value (`basis: typical`) are offered
  only up to `measure.TYPICAL_VIDEO_FRAMES_MAX` (33) frames.
- **Settings that cannot be set**: with settings (from `--resolution` / `--frames` / `--length` or the list), measure
  runs bench with `strict_settings`: if the workflow after `apply_settings` does not carry the requested values
  (`workflow.effective_settings`; the input is missing or comes from another node), it stops before generating with
  an error and nothing is measured. If the recorded settings still differ after the run, `warnings` says so.
- **Installed** (`installed.state`): `yes` when every file of the workflow exists with the listed size under one of the
  models folders (`--comfyui-dir`, else the folder decided like `download` without asking a ComfyUI, plus the folder a
  running ComfyUI reported); `no` otherwise (`missing_bytes` is what `download` would fetch); `unknown` when no folder
  was found or the list has no files for it. sha256 is not computed (download checks it).
- **Time** (`seconds`, rounded up to 10 s): the median `seconds` of `ok` measurements of the same workflow on any GPU
  (`basis: measured`), else a rough value per type (`basis: typical`; `measure.TYPICAL_SECONDS`: image 60, video 300,
  music 90, sfx 60, tts 60, 3d 120). A rough guide only; downloading is not included. With changed settings each
  measurement's time is scaled by the ratio of the changed items (pixels, frames or length; `measure.work_ratio`,
  missing values taken as the per-type defaults) before the median, and the typical value is scaled from the per-type
  default settings. Steps are not changed, so not scaled; loading is scaled too, so small settings come out short.
- **MODEL** is resolved like `download` (`downloader.resolve_candidates`), keeping only workflows with a template; an
  exact id is used as is, a name picks the workflow `recommend` would pick.
- The submission draft goes to `<cache>/submissions/<GPU>/<date>-<sha8>.json` (only for standard results with outcome
  `ok` or `oom`), where `share` lists it. The result goes to `<cache>/measurements/` like `bench`.
- **Starting ComfyUI** (0.13.0, #124; `comfystart.py`): when no ComfyUI runs (and no `--url`), the install found
  like `download` (`--comfyui-dir`, `GENMODELFIT_COMFYUI_DIR`, `genmodelfit config`, the usual places) is started as a
  separate process: portable `<root>/python_embeded/python.exe -s <root>/ComfyUI/main.py`, a manual or comfy-cli
  install with its `.venv` / `venv` python (no venv: not started; another python is never guessed), ComfyUI Desktop
  with `<basePath>/.venv` and the app's `resources/ComfyUI/main.py` and the Desktop's directory arguments (only when
  both are found; the app itself is never launched). Always `--listen 127.0.0.1 --port <free port> --disable-auto-launch`;
  `GITHUB_TOKEN` / `GH_TOKEN` are removed from its environment. It waits for `GET /system_stats` (`--start-timeout`,
  default 300 s) and stops only that process tree afterwards (also on a failure or Ctrl+C; Windows `taskkill /T /F`,
  POSIX the process group). The "Measure it?" question covers it. `--no-start` or `GENMODELFIT_COMFYUI_START=0` turns
  it off. JSON: `comfyui.start` before, `comfyui.started` `{kind, comfyui_dir, url, log}` once started.
- **Never left running, never two at once** (0.13.0): on Windows the started ComfyUI is put in a Job Object with
  `KILL_ON_JOB_CLOSE`, so closing the terminal or killing genmodelfit stops it too; on Linux it also gets
  `PR_SET_PDEATHSIG`; on POSIX a hang-up (closing the terminal) is handled like Ctrl+C. Limit: on macOS a
  genmodelfit killed with SIGKILL cannot stop it. Only one `measure` / `share` measures at a time
  (`<cache>/measure.lock` with the process id; a lock whose process is gone is taken over). When a ComfyUI already
  answers on 127.0.0.1:8188 / 8000 but its folders cannot be read, no second one is started (`--url` it instead).
  The folder a running server reports is used for downloads only when it exists and is not a system folder.
- **Missing nodes** (0.13.0): once a ComfyUI is reachable, `/object_info` is read once and each template is checked
  (`workflow.missing_nodes`, the same check as `bench`; `bench.NodeCheck`). The list hides suggestions whose template
  uses nodes that ComfyUI lacks; a named model stops before downloading or measuring, with "Next:" saying to update
  ComfyUI (or install the custom nodes).
- Exit codes: 0 success, nothing chosen, nothing to suggest, answered n to "Measure it?" or declined the download, a
  result that only cannot be sent / 1 no ComfyUI, no GPU, busy GPU, another `measure` / `share` measuring, no template,
  missing nodes, not installed with `--json`, no terminal without `--yes`, ComfyUI could not be started, a failed run
  (also Ctrl+C during the run) / 2 usage error / 130 Ctrl+C at a question. A failure in text output ends with a "Next:"
  line saying what to do.

## share

`genmodelfit share` (0.13.0, #124) measures and sends a GitHub pull request; `share --issue` is the older way with
the issue form (#113). `contrib share` is `share --issue` with `command: "contrib"` and `action: "share"`.

### Measure and send (`share`, `share --no-measure`, `share FILE`)

`--json` needs `--yes` (except with `--dry-run` and `--logout`). The envelope has no `system` / `database` and:

| Key | Type | Meaning |
|---|---|---|
| `action` | str | `send` (`logout` for `--logout`, with `removed`) |
| `no_measure` | bool | `--no-measure` or FILE |
| `comfyui` | object | as in `measure` (`url`, `found_by`, `start?`, `started?`, `version?`) |
| `budget_seconds` | float | `--budget` (default 300, `measure.SHARE_BUDGET_SECONDS`) |
| `plan` | Suggestion[] | the installed suggestions to measure (the items of `measure`'s `suggestions`) |
| `download_plan` | Suggestion[] | the suggestions that are not installed and fit the time left |
| `skipped` | object[] | `{model_id, name, missing_nodes}`: left out because the ComfyUI lacks nodes |
| `pending` | object[] | the unsent measurements before measuring (the items of `share --issue --json`'s `submissions`) |
| `measured` | object[] | `{model_id, settings, outcome, peak_vram_gb, seconds, submission}` |
| `failed` | object[] | `{model_id, settings, error}` (`model_id` null when ComfyUI could not be started) |
| `interrupted` / `downloaded` | bool | stopped with Ctrl+C / downloaded the `download_plan` |
| `download_hint` | object | `{count, bytes}` when models were not downloaded because nothing could ask (`--yes`, `--json`, no terminal) |
| `reason` / `reasons` / `next_step` | str / object[] / str | only when there is nothing to send (0.14.0, #146): one sentence; `{code, text}` per reason, `code` one of `no_measure`, `measured_here` (the installed models that fit are already measured on this PC; only when this PC has such runs), `no_installed` (no installed model that fits; otherwise), `installed_unknown` (the models folder is unknown), `missing_nodes` (update ComfyUI), `budget` (nothing installed fits the time limit, or nothing at all when none is installed), `download_declined` / `not_downloaded` (`share --download`), `failed`, `unsendable`, `interrupted`, `failures_sent` (the same failure records were already sent), `no_unsent` (always last; mentions `share --no-measure --all` when sent files exist); and one line saying what to do next |
| `files` | object[] | `{file, path_in_repo, ok, refused}` for every file checked |
| `prs` | object[] | `{url, reused, branch, fork, uploaded, skipped, on_main}` per GPU |
| `marked_shared` | int | files marked as sent |
| `sent` | bool | a pull request was made or updated |
| `github` | object | `{login, source}` (`source`: `env:GITHUB_TOKEN`, `env:GH_TOKEN`, `cache` or `device`); never the token |
| `method` / `issues` | str / object[] | `issue` and the `share --issue --json` objects per file, when sent as issues |
| `hint` | str | with a failed pull request: how to send the saved results later (`share --no-measure`) |
| `dry_run` | bool | only with `--dry-run` |
| `error` | str | on a failure |

Rules:

- **ComfyUI** is found, started and checked like `measure` (above). Without one (and no unsent measurement) it shows
  the plan and how to start ComfyUI and exits 1 without asking or signing in; with unsent measurements it goes on to
  send those.
- **Plan**: `measure.suggestions` with `installed.state == "yes"`, in order, while the sum of `seconds` fits the budget
  (`measure.share_plan`; a row that does not fit is skipped and the next is tried). Remeasure rows (0.14.0, #147,
  see measure) come after the rows never measured, so among the installed ones they are taken only with time left
  (installed remeasure rows still come before downloads, which use the time left after the installed ones). When a ComfyUI is reachable,
  suggestions whose template uses nodes it lacks are left out (`skipped`) and the next ones fill the budget
  (`download_plan` too). With a ComfyUI to start, this happens after it starts, and the new plan is shown.
- **Order**: plan with the total time → one `[Y/n]` (covers measuring and starting ComfyUI; `--yes` skips it) → check
  an existing token → start ComfyUI → measure one by one (`_measure_run`, the same as `measure`; a failure does not
  stop the rest; Ctrl+C goes on to sending) → show every file with the privacy checks → choose how to send
  (`1) pull request 2) issues q) not now`, default 1; `--yes` / `--json` / no terminal: pull request;
  `--issue --yes`: issues) → sign in if needed → pull request, or one issue form per file (`_share`, the same as
  `share --issue`; the URLs are printed without a terminal).
- **Total time** on the plan: starting ComfyUI when it will be started (`comfystart.TYPICAL_START_SECONDS`, 45 s), the
  sum of the time hints, and when downloads are planned their measuring time plus the download at 100 Mbps
  (`cli.DOWNLOAD_EXAMPLE_MBPS`; shown as an example, "depends on your connection"). The download question shows the
  same.
- **Freed before each run**: `share` measures several items in one ComfyUI, so before each one it calls `/free`
  (`unload_models`, `free_memory`; ComfyUI unloads the models and clears its cache of node results) like `bench
  --free`; for a ComfyUI that was already running the plan says so ("Before each run the ComfyUI's memory is freed").
  `measure` does it for a ComfyUI it started, and for yours only with `--free` (0.13.0). A run that still reused
  cached results, or any other non-standard condition, is not saved or sent: it is listed under `failed` with the
  reason (nothing is dropped silently).
- **Not sendable** (0.13.0): before a submission is written, the checks of `share` (privacy checklist and
  `validate` errors) run; when it cannot be sent (for example a value outside the checked ranges; since #132 a peak VRAM above the GPU
  total is sent with `spilled`), only this PC's
  measurement is kept and the run is listed under `failed` with the reason. A saved submission that cannot be sent
  is listed once and renamed `<name>.unsendable` (not offered again; `retired` in the JSON); `share` does not fail
  for it when everything else was sent.
- **GPUs and VRAM**: any GPU that is judged (backend `cuda` / `rocm` / `xpu` / `mps`; `SystemInfo.best_gpu`) is
  measured; only "no usable GPU" stops (0.13.1; 0.13.0 wrongly measured on NVIDIA only). `measure.suggestions` leaves
  out models with an unknown license unless `--any-license`. Measuring is refused when other programs use more than
  1.5 GiB or 20% of the VRAM (`bench.others_vram_limit`): on NVIDIA checked with nvidia-smi before starting ComfyUI
  and from `/system_stats` after freeing; on AMD (ROCm) only from `/system_stats` after freeing (ComfyUI reports the
  whole GPU's free memory there). Intel (XPU) and Apple (MPS) do not show other programs in `/system_stats`, so this
  is not checked and the `note` says so (`bench.OTHERS_VRAM_UNCHECKED`). On NVIDIA a new compute-only GPU process
  during a run (type C in nvidia-smi's process table; C+G / G apps such as browsers are ignored) makes the result
  non-standard; other vendors have no such process table, so it is not watched and the `note` says so
  (`bench.GPU_APPS_UNCHECKED`). The busy check before measuring is bench's per-vendor one
  ([accuracy.md](accuracy.md), "Bench on non-NVIDIA GPUs").
- **Never measured twice**: the saved submissions on this PC (`<cache>/submissions/`, sent or not) count as
  measurements for the suggestions (`measure.suggestions(extra=...)`), so a later `share` sends them instead of
  measuring again. Results are saved before sending; after `q`, a failed sign-in or a failed pull request they stay
  unsent and `share --no-measure` (or `--issue`) sends them. A failed pull request in a terminal offers issues for the
  same files.
- **Downloads**: never with `--yes` or `--json` alone. With `--download`, or `y` to one `[y/N]` question (the models,
  total size and time): asked before signing in and before starting ComfyUI when no installed suggestion can be used,
  after starting when the installed ones all lacked nodes, else after measuring. Downloading is `download` with its
  own questions and consent rules (`--any-license` is passed on; suggestions are never uncensored). Without a
  question, `download_hint` says that `--download` would download them.
- **Sign-in**: before measuring only an existing token is checked (`GITHUB_TOKEN`, `GH_TOKEN`, else the saved one;
  a token that does not work stops it; without one, `--yes` / `--json` / no terminal stop it before measuring unless
  `--issue`). The sign-in itself happens when a pull request is chosen: `GITHUB_TOKEN`, `GH_TOKEN`, the saved token
  (`<config>/github_token` `{access_token, expires_at}`; expired or answering 401: deleted), then the device login (client id
  `github.CLIENT_ID`, `GENMODELFIT_GH_CLIENT_ID` overrides it and empty turns it off; scope `public_repo`; only in a
  terminal without `--json`; `slow_down` adds 5 s to the interval; `expired_token` / `access_denied` stop). The
  refresh token is never stored. Before sending, the token is checked again: if it expired, a terminal signs
  in again; otherwise it exits 1 saying the measurements are saved and `share --no-measure` sends them.
- **Pull request**: fork `laxshmate/genmodelfit` (created and waited for when missing), branch
  `genmodelfit-share/<GPU slug>/<sha8 of the paths>` from the merge base of the upstream `main` and the fork's
  `main` (`github.fork_base`, the compare API's `merge_base_commit`; the upstream head when it cannot be compared;
  the fork's `main` is never changed, since `merge-upstream` needs the `workflow` scope), the files at `data/contrib/<GPU>/<date>-<sha8>.json`
  (contrib's `suggested_path`) through the Contents API, then a pull request titled `measurements: <GPU> (<count>)`
  with a table of the files, the genmodelfit version and `<!-- genmodelfit:share -->`. One per GPU. An open pull
  request of the same account whose branch starts with `genmodelfit-share/<GPU slug>/` gets the files instead. Files
  already on the upstream `main` are checked first and not uploaded (marked as sent). A file already on the branch
  is skipped (422). When the pull request fails partway, what was already placed (another GPU's pull request, files
  put on an open pull request or on the branch) is kept and only the other files are offered as issues. A pull request is opened whenever the branch has files not on the upstream
  `main`, also when retrying after a failed attempt. Marked as sent: only the files of a pull request (new or
  reused) and files already on the upstream `main`.
- **Errors**: 403 / 404 on a write: the token lacks the `public_repo` scope (after a fresh fork, the git data is waited
  for first). 403 / 429 with `Retry-After`, `X-RateLimit-Remaining: 0` or "rate limit": waited once when 60 s or
  less, else exit 1 with the time to wait. The open pull requests are read page by page; when they cannot be read
  it stops instead of opening a second pull request.
- `share --json` without `--yes` (and without the flags above) and `share FILE --json` keep the `--issue` output
  (for apps that used it). FILE with `--no-measure` or `--download` is a usage error (2).
- Exit codes: 0 sent, nothing to send, or answered n / 1 cannot sign in, no ComfyUI and nothing to send, a measurement
  failed, a file was refused, GitHub refused, `--json` or no terminal without `--yes` / 2 usage error / 130 Ctrl+C at
  a question, or while measuring (after it went on to sending what was measured). Results that only cannot be sent
  do not make it 1.

### Issue form (`share --issue`)

`genmodelfit share --issue [FILE] --json`:

- Without FILE: `listed: true`, `folder` and `submissions` [{file, sha256, shared, date, model_id, gpu, outcome,
  peak_vram_gb, seconds}] (unsent ones, newest first; with `--all` also those marked as sent). Nothing is opened.
- With FILE: `listed: false`, `file`, `ok`, `refused` (why it cannot be sent), `checklist`, `report`, `share`
  (`{path_in_repo, title, issue_url, issue_url_length, url_limit, form_url}`; null when it cannot be sent),
  `opened`, `copied` (false with `--json`) and `marked_shared`.

Rules:

- The checks are those of `contrib show` (every field, the privacy checklist, the `validate` result; the file name is
  not checked). An NG in the checklist or a `validate` error refuses it (exit code 1); warnings and notices do not.
- `issue_url`: `https://github.com/laxshmate/genmodelfit/issues/new?template=measurement.yml&title=<title>&submission=<content>`
  (the issue form `.github/ISSUE_TEMPLATE/measurement.yml`; `submission` is the id of its JSON box, `contrib.ISSUE_FIELD`),
  with the file content as is (LF line endings), URL-encoded. The title is `measurement: <model_id> on <GPU name>`.
- When the URL would be longer than 8000 characters (`contrib.SHARE_URL_LIMIT`), `issue_url` is null; `form_url` (the same
  form with only the title) is opened, and the content is copied to the clipboard with the OS tool (Windows `clip`,
  macOS `pbcopy`, Linux `wl-copy` / `xclip` / `xsel`; through stdin, no shell). Without one, the text output prints the
  content between two `---` lines (the file's location is never inside them).
- The browser is opened only in a terminal (stdin and stdout are terminals) and without `--json` / `--print-url`; it
  never asks. `--print-url` (only with `--issue`) prints only the URL.
- The measurement is marked as sent (`<cache>/submissions_shared.json`, content sha256 → date) only when the form
  was opened in the browser or its URL was printed with `--print-url`. `--json` only previews and marks nothing
  unless `--mark-sent` is given (for apps that open the form themselves); the text output without a terminal and a
  browser that could not be opened mark nothing either.
- `--print-url` without FILE uses the only unsent measurement; with none or several it exits 1 and asks for FILE
  (the files are listed on stderr).
- No network access; the content is sent only when the user presses "Create" in the form.

## contrib

Measurement submissions from users (#34). genmodelfit **never sends** them on its own: users agree in `share`, which opens a
pull request from their account (#124), press "Create" in the measurement issue form that `share --issue` opens (a
bot turns the issue into a pull request; `contrib intake`, #113), or add the file to
`data/contrib/` of their fork and open a pull request themselves ([CONTRIBUTING.md](../CONTRIBUTING.md)). Code: `src/genmodelfit/contrib.py`.

Submissions measured by bench with dynamic VRAM (`vram_metric: torch_reserved`, `offload: dynamic`) hold only the working memory (#100). They are aggregated and kept, but until the per-stage peaks can be measured they are used only for the cross-check (not for the estimate's shift or the level; see "Measurement" and [accuracy.md](accuracy.md)).

### Submission file (`contrib_schema` 1, 2 and 3)

Written by `genmodelfit share` / `measure`, `genmodelfit bench … --submission PATH` or `genmodelfit contrib make --from <bench result>`. Stored in the
repository as `data/contrib/<gpu-slug>/<date>-<sha8>.json`: `gpu-slug` is `estimate.gpu_key` of the GPU name
(lower case; `NVIDIA`, `GeForce`, `(R)`, `(TM)`, `™`, `®` removed; other symbols → `-`; `rtx-3070`), `sha8` the first 8 hex digits of the sha256 of the canonical JSON (keys sorted,
no spaces, UTF-8), so the name does not depend on indentation and two identical files collide.

```json
{
 "contrib_schema": 2,
 "kind": "genmodelfit-measurement",
 "tool": {"name": "genmodelfit", "version": "0.12.0"},
 "date": "2026-10-20",
 "system": {
  "os_family": "windows",
  "ram_total_gb": 32,
  "gpu": {"name": "NVIDIA GeForce RTX 3070", "vram_gb": 8.0, "arch": "ampere", "compute_capability": "8.6", "driver": "566.03"}
 },
 "comfyui_version": "0.40.0",
 "measurement": {
  "model_id": "image_sdxl_simple",
  "settings": {"width": 832, "height": 832, "steps": 20},
  "outcome": "ok", "standard": true, "offload": "dynamic",
  "peak_vram_gb": 6.9, "peak_ram_gb": 7.4, "seconds": 41.2,
  "vram_metric": "torch_reserved",
  "note": "…"
 }
}
```

| key | type | meaning |
|---|---|---|
| `date` | str | UTC date only (`YYYY-MM-DD`); no time |
| `system.os_family` | str \| null | `windows` / `linux` / `macos`; no version or build. null when ComfyUI ran on another PC |
| `system.ram_total_gb` | int \| null | RAM in GiB rounded to an integer; null when unknown or overridden with `--ram` |
| `system.gpu` | object | the GPU fields; details below |
| `measurement` | object | the Measurement fields that matter (settings without `batch: 1`). `note` is split into sentences (`。`, newline, `;`); every sentence that contains this PC's host / user name / home folder or matches a privacy pattern (below) is dropped whole, and the count of dropped sentences is appended (`contrib.scrub_text`) |

Contents of `system.gpu`:

- `name`
- `vram_gb` (as measured)
- `arch` / `compute_capability` (NVIDIA only: from nvidia-smi on this PC when the names match, else from the GPU name table)
- `driver`
- (0.9.0, optional) `vendor` (`nvidia` / `amd` / `intel` / `apple`) with `backend` (`cuda` / `rocm` / `xpu` / `mps`). Written only when bench recorded the backend (`Measurement.gpu_backend`); both or neither.
- (0.9.0, optional) `pci_id` (PCI model ID `1002:744c`, lower case). Its vendor ID must match `vendor`, and an ID listed in `hardware.PCI_DEVICE_NAMES` must name the same GPU as `name` (AMD by gfx, Intel by generation and nominal VRAM). An unlisted ID is an `unknown_gpu` notice.

Other rules:

- Older files without these stay valid.
- `contrib_schema` 1, 2 and 3 are accepted (#90, #132). 2 is 1 plus `measurement.files_fp` (optional; 64 lowercase hex
  digits), written since 0.11.0. `files_fp` in a schema 1 file is an error. 3 (written since 0.13.0) is described
  below.
- (0.12.0, #116, optional) `measurement.settings_override`: `true` when the template's settings were changed
  (written only then; anything but true / false is an error). `contrib_schema` stays as is, like the optional GPU
  keys of 0.9.0. `contrib aggregate` keeps such submissions in their own rows and marks the row with it.
- The name may contain `(R)`, `(TM)`, `™`, `®`. A name of another vendor than `vendor` is an error. For `apple` only a chip name (`Apple M2 Pro`) or `mps`.
- `vram_gb` above 256 GiB, `peak_ram_gb` above `ram_total_gb` × 1.05 + 1 GiB, and any NaN / infinity / negative number are errors (NaN in the file is refused when reading).

Not written by genmodelfit: host name, user name, paths, GPU UUID, PCI bus id, serial numbers, IP / MAC addresses,
the measurement's free-text `source`, CPU name, exact time. **Limits (honest)**: the checks are patterns, not
understanding. **Not detected**: personal names or other identifying words in plain text (e.g. `山田太郎のPCで測った`,
`on Taro's desktop`, `github some-user`), addresses (`東京都港区…`), encodings other than base64-like runs
and URL encoding, and paths without separators described in words. That is why `contrib show` asks the user to
read the note and a person reviews each pull request.
Free-text fields are kept small: `gpu.name` only letters, digits, space, `.`, `-`, `(R)`, `(TM)`, `™`, `®` and a
size such as `(16GB)` (64 chars), `comfyui_version`
strictly `x.y.z`, `precision` one of the known precisions, `model_id` up to 200 chars of `[A-Za-z0-9_.:/+-]`, other
strings 100 chars, `note` 500 chars (lowered from 2000 in #90; bench truncates longer notes); no control characters, newlines or invisible characters anywhere; unknown keys,
arrays (except a failure file's `failures` and `missing_nodes`) and nesting deeper than 8 are rejected; files over 64 KB and symbolic links are not read.

### Data added in `contrib_schema` 3 (#132)

`genmodelfit share` (and `measure`) write `contrib_schema` 3 since 0.13.0. Besides the measurement it sends what helps
to improve the estimates. Everything below is numbers, fixed words and public names; no message text, no generated
images or sounds, no input other than the template's prompt, and none of host name, user name, GPU UUID, PCI
location, paths or IP addresses (the privacy checklist covers every new field).

| key | type | meaning |
|---|---|---|
| `db` | object | the list used when measuring: `generated_at` (date only, `YYYY-MM-DD`) and `sha256` (first 12 hex digits of the bundled or cached `db.json`; omitted when unknown). Optional |
| `measurement.estimate` | object | genmodelfit's estimate for the measured model and settings at that time, made **without** this measurement or other measurements from the same PC (only the bundled measurements: the author's and the aggregated community rows; those of the same model on the same GPU are left out too, because the author's may come from this very PC — `measurements: bundled-excluding-this-gpu` says so): `fit_level` (`perfect` / `good` / `marginal` / `too_tight`), `run_mode` (`gpu` / `sequential` / `offload` / `cpu` / `none`), `vram_gb` (the VRAM that set the level), `vram_resident_gb`, `work_gb` (the working-memory formula, heaviest stage), `ram_required_gb`, `seconds` (the time guide of `measure`), `basis` (`measured` / `estimated`). Any value may be null; left out when ComfyUI ran on another PC or the size is unknown |
| `measurement.timing` | object | time breakdown in seconds, null when unknown: `queue_s` (sent → started), `load_s` (start → first sampler step: loading and text encoding), `sampling_s` (sum of the sampler runs), `sec_per_step`, `decode_s` (last sampler step → end: VAE decode and saving), `save_s` (always null for now: HTTP cannot tell it apart), `total_s` (same as `seconds`), `cold_load` (true = first load in a ComfyUI that genmodelfit started, false = the same ComfyUI again after `/free`, null = unknown) |
| `measurement.spilled` | true | only for `outcome: ok` runs whose peak VRAM is above the GPU total × 1.02 + 0.25 GB (Windows spilled into shared memory). Written only when true |
| `measurement.spilled_gb` | number | peak VRAM minus the GPU total (GiB), with `spilled` |

How the time breakdown is read (HTTP only, no websocket): the times of `execution_start` / `execution_success`
(or `_error`) in `/history`'s `status.messages`, and the progress lines (tqdm, `20/20 [00:05<00:00, 3.80it/s]`) in
ComfyUI's log (`/internal/logs/raw`). Values that compare the two clocks (`queue_s`, `load_s`, `decode_s`) are only
written when ComfyUI runs on the same PC. Only progress lines without a description in front (not `Fetching 3 files: …`)
whose step count equals a sampler's `steps` in the workflow sent are used; `sampling_s` / `sec_per_step` are null when
none matches or the sum is longer than the run. If the timing or the estimate does not pass the checks, the
measurement is sent without them.

Rules: `db`, `estimate`, `timing`, `spilled` / `spilled_gb` in a schema 1 or 2 file are errors. `spilled` must be
true, only with `outcome: ok`, the peak must be above the total line and at most the GPU total plus the RAM (×1.05 +
1 GiB; the GPU total again when RAM is unknown), and `spilled_gb` must match peak − total within 0.1 GiB. A spilled
submission is accepted but **not used** for the aggregate, the estimates, the levels or `calibrate` (it is material
for "this GPU spills to shared memory (slow)"; how to use it is a later issue).

### Failure records (`kind: genmodelfit-failures`, #132)

One file per `share` run and GPU (and one per failed `measure`, saved as unsent and sent by the next `share`), next to the measurements: `data/contrib/<gpu-slug>/<date>-<sha8>-failures.json`.
`contrib_schema` 3 only. Top keys: `contrib_schema`, `kind`, `tool`, `date`, `system` (the same summary as a
measurement), `comfyui_version`, `db` (optional), `failures` (1–50 records).

```json
{
 "contrib_schema": 3,
 "kind": "genmodelfit-failures",
 "tool": {"name": "genmodelfit", "version": "0.13.0"},
 "date": "2026-10-29",
 "system": {"os_family": "windows", "ram_total_gb": 32, "gpu": {"name": "NVIDIA GeForce RTX 3070", "vram_gb": 8.0, "arch": "ampere", "compute_capability": "8.6", "driver": "566.03", "vendor": "nvidia", "backend": "cuda"}},
 "comfyui_version": "0.40.0",
 "db": {"generated_at": "2026-10-21", "sha256": "0123456789ab"},
 "failures": [
  {"model_id": "image_moge", "outcome": "missing_nodes", "seconds": null, "missing_nodes": ["LoadMoGeModel"], "before_run": true},
  {"model_id": "video_x", "outcome": "timeout", "seconds": 1800.4, "timeout_s": 1800.0},
  {"model_id": "image_y", "outcome": "error", "seconds": 3.0, "error_type": "ValueError", "node_type": "KSampler"}
 ]
}
```

| key | meaning |
|---|---|
| `model_id`, `outcome` | required. `outcome` is `missing_nodes` / `timeout` / `error` / `oom`. Interrupted runs (the user stopped them) and runs disturbed by other programs are **never** sent |
| `settings` | optional, the same keys and ranges as a measurement |
| `seconds` | elapsed seconds, null for problems found before running |
| `timeout_s` | the time limit, only for `timeout` |
| `missing_nodes` | node names ComfyUI lacked (built-in or custom node names, which are public): letters (any script), digits, spaces and `_ . + ( ) \| ' : , [ ] -`, up to 100 characters, no `/` or `\`; only for `missing_nodes` |
| `other_nodes` | how many missing node names did not fit that form or looked like personal information (they are not sent). `missing_nodes` may be empty only when this is 1 or more |
| `before_run` | true when the node check found it before running (nothing ran, so no numbers) |
| `error_type` | for `error` / `oom`: the exception type name (`torch.OutOfMemoryError`) or ComfyUI's `node_errors` type (`value_not_in_list`); `[A-Za-z_][A-Za-z0-9_.]{0,63}`. The message text is never sent (it can hold paths) |
| `node_type` | for `error` / `oom`: the node type that failed, only when it is in the workflow genmodelfit sent |
| `peak_vram_gb`, `files_fp` | optional, as in a measurement |

What becomes a record: a template that needs nodes this ComfyUI lacks (from the node check before measuring in
`share` or `measure`, or when bench stops on them), a timeout after our job started running (not one spent waiting in the queue, while
other work ran, or while the PC slept), ComfyUI refusing the workflow (not `value_not_in_list`, a model file missing on this PC), an error during the run, and an OOM that
could not be sent as a measurement. `measure` saves its record as an unsent file too, and the next `share` sends it. A record that equals one
already saved on this PC (same model, settings, failure, error type and ComfyUI version; for missing nodes only
the model and the node names) is not written again. A record that does not pass the checks is left out alone, and
records are dropped from the end to stay under 64 KB. On receiving, every privacy detector (also phone numbers,
domains and encodings) is applied to all strings in `failures`; free words that fit the name form cannot be told
from node names, so a person still reviews each pull request. Failure records are not used by the estimate
formula, the aggregate or `calibrate`; they are material for what to recommend and which ComfyUI version is needed.

### Rules of the submission pipeline (#90)

Each rule can be explained in one sentence. They are checked with about 60 kinds of crafted submissions
(tests/test_contrib_robust.py).

- **Add only**: a submission PR only adds new files under `data/contrib`; a PR that deletes, modifies or renames an
  existing file fails in CI (contrib.yml) (removing one is a maintainer PR).
- **Layout**: only `data/contrib/<GPU>/<date>-<sha8>.json` and failure records `<date>-<sha8>-failures.json` (#132; lowercase `.json`) can be added; any other file or place
  is the error `layout`, both in CI and in `contrib validate <folder>`.
- **Empty**: a PR or folder without any submission file fails (`contrib validate` exits 1).
- **Unreadable forms**: a UTF-8 BOM is refused with the reason (not stripped), and JSON with the same key twice in
  one object is refused (the later value would silently win).
- **Personal information**: base64-like runs (from 16 characters with `=` padding, else from 20), 32+ hex digits
  (except exactly 40, a git commit, and 64, a sha256), domain names, phone numbers, dotted
  MAC addresses, obfuscated e-mail (`[at]`, アット) and words like 「製品番号」 are errors. Base64, hex, domain and
  phone patterns are not applied to fixed-form fields such as the model id (those are bound by their form and the
  list). Public domains (`contrib.PUBLIC_DOMAINS`) are skipped only when the host name ends with them
  (`nvidia.com.taro.jp` is still caught).
- **Measured files**: bench writes `files_fp`, the fingerprint of the model's files in the list when measured (the
  sha256 of the lowercased file sha256s, sorted and joined with newlines; omitted if any file has no sha256), and a
  measurement whose fingerprint differs from the current list is not used.
- **GPU names** (0.11.0, #101 dropped the name table): only placeholders (empty, `n/a`, `none`, `null`, `unknown`,
  `-`) and generic names (`NVIDIA Graphics Device` etc.) are not used for judging (kept as records with
  `gpu_known: false`; notice `unknown_gpu`). NVIDIA names are checked by form (`hardware.nvidia_name_well_formed`;
  [hardware.md](hardware.md), "GPU name form"): a name that does not fit is not an error but the notice
  `gpu_name_form` (a maintainer checks it), and a name that fits but is not in the table is the notice `unknown_gpu`
  (still used). A made-up name only matches a PC that reports the same string (`estimate.same_gpu`). A generation
  name in parentheses (`(Pascal)` etc.) is allowed in the name.
- **GPU memory**: for NVIDIA GPUs whose VRAM follows from the name (`estimate.NVIDIA_NOMINAL_VRAM`; desktop GeForce
  RTX 20–50 only; other families (laptop, workstation, data center) have no size check), a `vram_gb` outside
  0.95–1.03 times every version's amount is the notice `gpu_vram_nominal` (lowered from an error in 0.11.0, because
  modified cards exist; the VRAM ratio of 0.95 keeps them away from PCs with other VRAM. CUDA on Linux reports a
  6 GB card as 5.80 GiB, so the lower side is wider).
- **Error runs**: `outcome: error` submissions are not used for a row's value and only counted (`n_error`; a group of
  only errors makes no row).
- **Steps**: peak VRAM does not depend on the step count, so neither the aggregation key nor judging's matching uses
  it (a row's `settings.steps` is the most common value, for display).
- **Issues** (#113): a measurement issue is checked by `contrib intake` with the same checks and becomes one PR
  (branch `measurement/issue-<N>`, made by GitHub Actions; the issue author is written in the PR and, as
  `Submitted-by: github:<account>`, in the commit), counted as that account's submission. A PR made with `GITHUB_TOKEN` does not start contrib.yml; the intake already ran
  the same checks.
- **Sources** (#113): submissions connected by the same pull request (merge commit) or the same person count as
  one source (`contrib.submission_origins` / `submission_sources`; the rules are in [scoring.md](scoring.md)): the
  person is a GitHub account (counted once per account; another account, an organization or a renamed account counts
  as someone else, which the maintainer checks): the `Submitted-by-id` numeric id (else, for older bot commits, the
  `Submitted-by` account) only for the intake bot's own branch merged with a merge
  commit, else the pull request's source account from the merge commit's first line (`Merge pull request #N from
  <account>/<branch>`; commit e-mail addresses are never used for it); only a commit that is not such a merge
  (directly on main, squash, rebase) falls back to the author's e-mail address (weaker). The value is the sha256 of the
  group's smallest key, so no address is published. They count as one source whose median is one value, and outliers are judged between sources (`n_sources`; without git
  history or in a shallow clone each submission counts on its own, and the output says so).
- **OOM-heavy groups**: a row whose OOM sources are at least as many as its ok sources has `runs_evidence: false` (not
  evidence that it runs; how it is used is PR #95).
- **Versions**: a submission made by a newer genmodelfit than the checker (`tool_newer`), a ComfyUI older than the
  template needs (`comfyui_old`) or 6+ minor versions newer than the latest the list knows (`comfyui_newer`) gets a
  notice only.
- **Templates without a size input** with `batch` other than 1 get the notice `batch_sizeless` (a recording error of
  0.10.1's bench).

### `contrib make --from FILE [--output PATH] [--no-pc-info] [--json]`

Builds one submission per measurement in the bench result FILE. `--output` is a folder (default
`data/contrib`; files go to `<gpu-slug>/<date>-<sha8>.json` under it) or, for a single measurement, a `.json` file.
`--json`: envelope + `action: "make"`, `submissions` [{saved_to, path_in_repo, submission}] (`submission` is the
file's content). Exit 1 when the bench result cannot be read.

### `contrib show FILE`

Prints every field of the file and a privacy checklist (`known_fields`, `paths`, `host_user` — also this PC's own
host / user name —, `device_ids`, `network`, `date_only`, and `note_reviewed`, which is for the user to tick).
`--json`: envelope + `action: "show"`, `file`, `ok`, `submission`, `checklist` [{id, label, ok (null = for the user
to check), detail}], `report` (below). Exit code 1 when something must be fixed.

### `contrib share [FILE]`

The same as `genmodelfit share --issue` (see "share"; kept for older scripts). The JSON has `command: "contrib"` and
`action: "share"`.

### `contrib intake EVENT_JSON --out DIR [--existing DIR]`

Used by `.github/workflows/measurement-intake.yml` (#113) for issues with the `measurement` label. Reads the issue body,
number and author from the issues event file, takes the first fenced block that starts with `{` (the form's
JSON box, a `json` code block) as data, and runs `contrib.intake_issue`: the `validate` checks (file name not checked), the
privacy checks of `share` without the check for this machine's own host / user name (on GitHub that is the
runner's, not the sender's), at most 64 KiB, and no submission with the same content under `--existing`. It also
counts how many submissions the same account already sent for that GPU and model under `--existing`
(`contrib.submitter_count`; needs git history). It writes
`result.json` (`IntakeResult`: `ok`, `reasons`, `notices`, `path_in_repo`, `sha256`, `title`, …) and `comment.md`
(English and Japanese; reason texts made harmless with `contrib.md_cell`); when accepted also `submission.json` (the
content re-serialized by `contrib.dumps`, not the raw body), `path.txt`, `title.txt`, `commit_msg.txt` and
`pr_body.md` (the issue number and author, that count, notices for the maintainer, `Closes #N`) and
`comment_updated.md` (for an edit after the PR exists); when refused also `comment_pr_closed.md` (for the PR that is
closed). `commit_msg.txt` ends with `Submitted-by: github:<account>` and `Submitted-by-id: <numeric id>` (the issue author's
`user.id`, only when it is digits), which `submission_sources` uses to count one account as one source. Nothing from the body is printed
(only `accepted` / `refused (N reasons)`). Exit 0 whether accepted or refused; 1 when the event cannot be read. No
network access.

### `contrib validate FILE|DIR… [--json] [--strict] [--no-filename-check] [--markdown]`

For each file a report `{path, ok, errors: [{code, message}], warnings: [{code, message}], notices: [{code,
message}], sha256, suggested_path, model_id, gpu_name}`. `--json`: envelope + `action: "validate"`, `ok`, `files`,
`valid`, `invalid`, `notices` (files with notices), `warnings` (count), `reports`. `--markdown`: a table for the CI
job summary, with a line for the maintainer above it when some files have notices.

Like llmfit's `validate_community_benchmarks.py`, **only mechanical checks fail** (2026-10-22): errors below.

| error code | when |
|---|---|
| `read` | not readable, not JSON, larger than 64 KB, nested too deep, or a symbolic link (symlinked folders are not followed) |
| `string` | a string with control characters / newlines / invisible or bidi characters, too long a string or key, an array, or nesting deeper than 8 |
| `schema` | `contrib_schema` not 1, 2 or 3, schema 3 fields in an older file, wrong `kind` / `tool`, missing keys, not objects |
| `unknown_field` | a key outside the schema (anywhere, including `settings`) |
| `privacy` | a value or key (after NFKC normalization, removing zero-width characters and reading `¥` as `\`) contains something listed below |
| `date` | not `YYYY-MM-DD`, or in the future |
| `value` | out of range, see below |
| `settings` | width / height 64–8192, frames 1–2000, seconds 0.1–3600, steps 1–1000, batch 1–64 (integers except seconds) |
| `gpu_inconsistent` | the GPU name's known compute capability or generation differs from the file's; `vendor` and `backend` do not pair up (or only one is given); `compute_capability` / `arch` on a non-NVIDIA `vendor`; a GPU name of another vendor (AMD / Intel / Apple) than `vendor`; a `pci_id` whose vendor ID differs from `vendor` (`10de` when `vendor` is absent) or whose GPU in `hardware.PCI_DEVICE_NAMES` differs from the name; for `apple`, a name other than a chip name or `mps` |
| `filename` | name not `<date>-<sha8>.json` (`<date>-<sha8>-failures.json` for failure records), date or sha8 not matching the content, folder not the GPU slug |
| `value` (strict forms) | `gpu.name` / `comfyui_version` / `precision` / `model_id` / `tool.version` outside the forms above |
| `duplicate` | same content hash as an earlier file in the same run |
| `layout` | (#90) when checking a folder: a file that is not at `<GPU>/<date>-<sha8>.json` (lowercase `.json`) |

`privacy` rejects:

- a drive path (`C:\`, `C:/`)
- any backslash
- two or more path separators between words (`a/b/c`, `Users\name\`)
- `/home/`-like roots
- `//server`
- `~/`
- `%VAR%`
- `$HOME`
- URL-encoded separators (`%2F` `%5C` `%3A`)
- `@` followed by a letter (e-mail, @handle)
- a UUID / GPU UUID
- a PCI address (`0000:01:00.0`, `01:00.0`)
- an IPv4 / IPv6 / MAC address
- a URL (`scheme://`, `www.`)
- a host-like name (`DESKTOP-XXXX`, `*-PC`, `*.local`, the words "host name" / "PC 名" …)
- a time (`14:05`, `2pm`, `14時05分`, ISO date-time)
- a serial number (`SN:`)

Some harmless text is also rejected (e.g. `12:30`, a 4-part version `1.2.3.4`).

`value` rejects out-of-range values:

- any NaN / infinity / negative number
- `vram_gb` ≤ 0 or > 256
- `peak_vram_gb` > GPU VRAM × 1.02 + 0.25, or 0 for `ok`
- `peak_ram_gb` > RAM × 1.05 + 1
- `seconds` ≤ 0, > 86400, or missing for `ok`
- malformed `pci_id`
- unknown `outcome` / `offload` / `vram_metric` / `os_family` / `arch` / `vendor` / `backend`
- malformed `driver` / `comfyui_version` / `compute_capability`

Notices (the file is valid and is aggregated; shown in the CI summary for the maintainer who merges — merging is
the review): `unknown_model` (not in the bundled list), `unknown_gpu` (not in the GPU name table, e.g. AMD / Intel /
Apple or a new NVIDIA, still used; or a generic name or placeholder, not used), `gpu_name_form` (0.11.0: an NVIDIA
name that does not fit the name form; a maintainer checks it for personal names), `gpu_vram_nominal` (0.11.0: VRAM
that is not the name's nominal amount; an error unless a modified card), `batch_sizeless` (a template without a size input, `estimate.NO_SIZE_INPUT`, with `batch` other than 1:
0.10.1's bench recorded MoGe's processing batch as the batch; judging uses batch 1, so it is not a same-settings
measurement; #90), `deviation` (peak VRAM more than 40% away from the formula's all-in estimate for those settings),
`below_weights` (`offload: none` but peak VRAM below the weights + fixed overhead; estimates use that floor for
every submission below it — with dynamic VRAM / partial loading a smaller peak is normal), `fit_change` (this
submission alone would change the level on a PC with the submitted GPU — same name, PCI model ID and vendor, VRAM =
the submission's `vram_gb`, plus the other nominal VRAMs of the same name when known (Intel Arc
`hardware._INTEL_XPU`; NVIDIA `estimate.NVIDIA_VRAM_VARIANTS`, 0.9.1: RTX 2060 / 3050 / 3060 / 3080 / 4060 Ti /
5060 Ti) — × RAM 16 / 32 / 64 GB, at the default and the submitted settings; this includes a level
going up. Other GPUs are never affected since 0.9.0). Up to 0.8.0 these were "review required" and needed a
per-sha8 approval (`--allow-review`, `data/contrib/REVIEWED`); both were removed.

Warnings (accepted, but reported; `--strict` makes them fail): `nonstandard` (kept as a record, never aggregated),
`metric_missing`, `driver_missing`, `comfyui_version_missing`, `ram_not_rounded`, `settings_missing` /
`settings_modality`, `offload_outcome`, `date_old`. `settings_missing` is given for an image / video model without a
width and height when the model is not in the hand table of templates without a size input (`estimate.NO_SIZE_INPUT`;
#90; for a new template the maintainer checks the bench record and adds it to the table).

`--markdown` escapes every cell (`| \` < > [ ] & @`, newlines, and GFM autolinks: `://` and `www.`) so file names
and values cannot inject Markdown, HTML, links or workflow commands into the CI summary. CI appends genmodelfit's
stderr to the summary through the same escaping.

Exit codes: 0 every file passed (warnings and notices allowed) / 1 a file failed, has a warning with `--strict`, or
there is no submission at all (#90) / 2 usage error.

### `contrib aggregate [DIR] [--output FILE] [--report] [--threshold PCT] [--fail-on-breach] [--json]`

`--output` refuses (exit code 1) when any source is counted per file (`file:…`, no git history), unless
`--allow-file-sources` is given (tests and local experiments only, never for a release; #95). `--report` and
`--check` only display and work without git history.

Reads `DIR` (default `data/contrib`; a missing folder is an error), keeps the files that pass `validate` (notices
allowed), and groups standard submissions by model id, the GPU (PCI model ID when present, else
the name normalized by `estimate.gpu_key`: case, spaces / symbols, `(R)` `(TM)` `™` `®`, `NVIDIA` / `GeForce`), GPU VRAM rounded to 1 GB (an RTX 4060 Ti 8 GB and 16 GB are different groups),
`vram_metric`, settings (precision normalized: `BF16` = `bfloat16` = `bf16`; the step count is not part of it, #90) and
the fingerprint of the list's files when measured, `files_fp` (#90). `error` submissions are not used for the value
and are counted in `n_error` (#90). `ok` submissions are reduced to one median per source
(`contrib.submission_sources`: the commit that added the file; per submission without git history or in a shallow
clone), and among those source values, values with |x − median| / (1.4826 × MAD) > 3.5
are outliers (only with 3 or more sources; MAD floored at 2% of the median); the median of the remaining source
values is the value, with `n_used` (submissions used), `n_sources` (sources used), `n_oom`, `n_error`,
`runs_evidence` (whether OOM sources are fewer than ok sources), `gpu_known` and `files_fp` (a group of only OOM runs
becomes one `outcome: oom` row, never used). A group is `consistent`
(enters the calibration) with at least 2 used sources whose range is within 15% of the median and no OOM in the
group. `--json` and the text output say how sources were counted (`sources_from`: `git` / `files`); the bundled
aggregate is made from a clone with history when releasing (docs/release.md "Releasing a version"). Output rows are Measurement records
with `origin: "community"` (see Measurement) in a fixed order, so the same input always gives the same file.
`--output` writes `{unit, source: "community", contrib_schema, date (latest submission date), method, submissions,
measurements}` (an empty result is not written unless `--allow-empty`, so the bundled file is never wiped by mistake).
- `submissions` (#90) lists `{sha256, source}` for each submission given to the aggregate (those that pass the
  checks), in sha256 order. `sha256` is the content hash (`contrib.canonical`, the source of the sha8 in the file
  name); `source` is the source used when aggregating (the commit that added it, or `file:<sha256>` without git
  history).
- `contrib aggregate --check` and the test re-aggregate with these recorded sources, never reading git history (same
  result in a shallow clone or outside git). The first form (plain sha256 strings) is still read (one source per
  submission), but `--check` reports it as not matching (`matches: false`) until it is re-aggregated in the current
  form (the written form differs).
- Older versions skip it as an unknown key (they read only `measurements`).

The bundled copy is `src/genmodelfit/data/measurements_community.json`, which is loaded with the other measurements.
- It is rebuilt when making a release (docs/release.md "Releasing a version"). A submission PR changes only
  data/contrib, so merging it does not put it in yet.
- The test checks that every submission in `submissions` is in data/contrib and that re-aggregating exactly those
  gives the bundled file. Submissions in data/contrib that are not in it yet are allowed.

`--report` re-runs the calibration (`calibrate.calibrate`) on the bundled maintainer rows plus the new community rows
(never the local cache) and returns `report`: `{threshold_pct, n_fit, coefficients, summary, by_modality, loo, check,
breaches, breach, fit_changes}`. `by_modality` is `{<modality>: {loo_n, loo_mean_abs_pct, loo_max_abs_pct, evaluable,
check_n, check_mean_abs_pct, check_max_abs_pct}}`; `breaches` is [{kind (`loo` / `community_check`), model_id,
gpu_name, origin, error_pct}]; `loo` / `check` are the per-row results of `calibrate.calibrate`. Counted against the threshold (default 25%): LOO errors of evaluable modalities and
the check errors of community rows; the maintainer's own check rows (RTX 3070) are shown but not counted.
`fit_changes` [{model_id, gpu_name, vram_gb, ram_gb, before, after}] lists the level changes the community rows cause
on PCs with the measured GPU (name, PCI model ID, vendor, the measured GPU's VRAM and the other nominal VRAMs of
that name, as for the `fit_change` notice) × RAM 16 / 32 / 64 GB; other GPUs
are never affected since 0.9.0 (section "Fit-level changes caused by community data" in the text report).
Text output is Markdown (for the weekly pull request of #19).

`--json`: envelope + `action: "aggregate"`, `dir`, `files`, `valid`, `invalid`, `notices`, `submissions_used`, `groups`,
`consistent`, `written_to`, `community` (the document above), and `report` with `--report`.
Exit codes: 0 / 1 error (no such folder; empty result with `--output` without `--allow-empty`) / 2 usage error /
**3** a breach with `--report --fail-on-breach`.

### `contrib aggregate [DIR] --check [--json]`

Writes nothing; compares the bundled aggregate with `DIR` (default `data/contrib`; a missing folder is an error,
exit code 1) (#90). It cannot be used with `--output` or `--report`. It also reports `file_sources`, the number of
sources in the aggregate counted per file (`file:…`) instead of per git commit; the bundled aggregate must have 0
(#95).
- It lists the valid submissions not in the aggregate yet (submissions merged since the last release).
- It also shows the submissions in the aggregate's `submissions` that are missing from `DIR` (or fail the checks),
  and whether re-aggregating exactly those gives the bundled file.
- It also lists the bundled rows measured with files (`files_fp`) that differ from the current list, which judging
  does not use (`stale`; information only; happens when a weekly list update sees upstream files change).

`--json`: envelope + `action: "aggregate_check"`, `dir`, `listed` (submissions in the aggregate), `valid`, `invalid`
(files that fail the checks), `pending` (paths of submissions not in it yet), `missing` (hashes in the aggregate but
not found), `stale` ([model_id, gpu_name]), `file_sources` (#95), `matches`, `ok` (nothing missing and matches).
Exit codes: 0 = nothing pending and ok / 1 = something pending, sources counted per file, or not ok / 2 usage error.

## download

`genmodelfit download <model> --json` prints the envelope (with `system` and `database`) plus the keys below.
`--list --json` prints the plan only (`listed: true`, `result: null`; no network access, nothing written, exit 0 even
when `plan.ok` is false). Downloading with `--json` needs `--yes`. On failure (exit 1, or 130 after Ctrl+C) `error` is
set, `result` is null and `plan` is the plan (when it was made). A model that is not found or matches several
models gives `error` (and `candidates`) without `plan`. When the place is not decided (nothing or several ComfyUI
folders found, and no question can be asked), `plan` is null and `destination_candidates` lists what was found
([download.md](download.md) "Choosing the place").
Python: `gmf.plan_download(...)` returns `DownloadPlan` (`.to_dict()` is `plan`), `gmf.download(...)` returns
`DownloadResult` (`.to_dict()` is `result`).

| key | type | meaning |
|---|---|---|
| `listed` | bool | true for `--list` (nothing downloaded) |
| `plan` | DownloadPlan | what is (or would be) downloaded |
| `result` | DownloadResult \| null | what happened; null for `--list` and on failure |
| `destination_from` | str \| null | (0.11.0, #111) how the place was decided: `comfyui_dir_option`, `output_dir_option`, `env` (`GENMODELFIT_COMFYUI_DIR`), `config` (`genmodelfit config`), found automatically: `cwd`, `running` (a running ComfyUI), `desktop` (ComfyUI Desktop settings), `comfy_cli`, `common` (usual places), or `typed` (a path typed in the terminal). null = not decided (`--list`, or the error above) |
| `comfyui_dir` | str \| null | (0.11.0) the ComfyUI folder; null for `--output-dir` and when not decided |
| `extra_model_paths` | object \| null | (0.11.0) the `is_default` section used: `file`, `section`, `root` (the place), `folders` (str[] of the folder keys in the section, or null with `download_model_base`); null when none is used |
| `destination_candidates` | object[] | (0.11.0) the ComfyUI folders found automatically: `comfyui_dir`, `from` (as `destination_from`), `detail` (e.g. `127.0.0.1:8188`, the settings file). Empty when the place was given |

DownloadPlan:

| key | type | meaning |
|---|---|---|
| `model_id`, `name` | str | the workflow (list entry) chosen |
| `model_name` | str | the model name shown in the tables |
| `modality`, `content`, `precision` | str / str / str \| null | as in ModelEntry |
| `fit_level`, `run_mode_label`, `auto` | str / str / bool | the judgement on this PC (as in `recommend`) |
| `chosen` | str | why this workflow was chosen (in the display language, `en` / `ja`) |
| `layout` | str | `comfyui` (`--comfyui-dir`: `root` is `<dir>/models`) / `output` (`--output-dir` or the Python default; same layout. The CLI also uses `output` for an `is_default` section or the models folder a running ComfyUI reported) |
| `root` | str | absolute path of the folder files go into (`<root>/<directory>/<name>`); "" when the place is not decided (CLI `--list`) |
| `files` | PlannedFile[] | one per file of the workflow |
| `total_bytes` | int | sum of the file sizes |
| `download_bytes` | int | still to download (minus files already there and `.part` bytes) |
| `present_bytes` | int | files already there with the right sha256 |
| `disk_free_bytes` | int \| null | free space at `root` (or its nearest existing parent) |
| `disk_needed_bytes` | int | `download_bytes` + `margin_bytes` (0 when nothing to download) |
| `margin_bytes` | int | 1 GiB |
| `budget_bytes` | int \| null | `--budget` in bytes |
| `blocked` | str[] | why it will not download (in the display language, `en` / `ja`). Empty = can download |
| `blocked_codes` | str[] | the kind of each `blocked` entry, same order (optional, since #75): `uncensored`, `unknown_license` (these two are lifted by the user's consent: Python `content="all"` / `include_unknown_license=True`; CLI `--uncensored` / `--any-license`, or the y/N question in a terminal. The `blocked` texts of the library name the Python arguments; the CLI shows its own flags), `gated`, `no_files`, `unavailable` (genmodelfit cannot download the workflow at all), `conflict`, `budget`, `disk` (the consent flags do not lift these; move the file / `--output-dir`, raise `--budget`, free disk space) |
| `warnings` | str[] | things to know (licenses not on the allowlist, will not run on this PC, layout hint) |
| `alternatives` | object[] | the other workflows considered: `id`, `precision`, `use`, `fit_level`, `memory_required_gb`, `size_bytes`, `auto`, `ok` (optional, since #75: true when it has no gated, unavailable or conflicting file; uncensored, unknown license, disk and budget are not counted) |
| `license_url` | str \| null | the model's license page |
| `ok` | bool | `blocked` is empty |
| `skipped_optional` | str[] | (0.12.0, #81) file names of optional parts not downloaded (`ModelFile.optional` is `inactive`). Empty with `with_optional=True` (CLI: `--with-optional`) |

PlannedFile:

| key | type | meaning |
|---|---|---|
| `name` | str | file name in the list (may contain ComfyUI subfolders, `/`) |
| `relpath` | str | path under `root` (`<directory>/<name>`; `other/<name>` when the ComfyUI folder is unknown; ComfyUI does not read `other`, move such files before use); "" when refused |
| `dest` | str | absolute destination path; "" when refused or when the place is not decided |
| `url`, `sha256`, `size_bytes`, `role` | | from ModelFile |
| `directory` | str \| null | the ComfyUI folder: from ModelFile, or inferred when the list has none (see `directory_basis`); null = unknown (`other/`) |
| `directory_basis` | object \| null | why `directory` was inferred for a file the list gives no folder (any source; optional, since #75): the main file's header parameter count matches a file of an official ComfyUI template within 0.001%, and all such files are in one folder (`unet` counts as `diffusion_models`, `clip` as `text_encoders`; e.g. an SDXL all-in-one file = `sd_xl_base_1.0.safetensors` → `checkpoints`). Keys: `kind` (`header_params`), `template` (the template id), `file` (its file), `params`, `reference_params`, and `needs` (`ComfyUI-GGUF` for a GGUF file in `diffusion_models` / `text_encoders`). null = from the list, or unknown |
| `license`, `license_name` | str \| null | the license used for the check (gate's conservative reading; null = unknown) |
| `license_url` | str \| null | where the license text is |
| `check_url` | str \| null | when `license_url` is null: the page to check the terms on (optional, since #75): `https://huggingface.co/<owner>/<repo>` for `hf:` ids, else the entry's `homepage` (https only), else the file's Hugging Face repository (for official templates often a Comfy-Org repository, not the original publisher; the CLI labels it so). null when `license_url` is set |
| `license_note` | str \| null | why the license differs from the list's value, or why it could not be confirmed |
| `license_allowed` | bool | on the license allowlist (gate default or `--license-allowlist`) |
| `gated` | bool \| null | access request needed (also when the upstream of a component asks for it) |
| `status` | str | `download` / `resume` (a `.part` exists) / `present` (already there, sha256 matches) / `conflict` (a different file has the name; never overwritten) / `unavailable` (see `reason`) |
| `part_bytes` | int | size of the existing `.part` |
| `remaining_bytes` | int | bytes still to download for this file |
| `reason` | str \| null | why `conflict` / `unavailable` (in the display language, `en` / `ja`) |
| `verified` | bool | for `present`: the sha256 was measured while planning (false with `verify_existing=False`: size only). `download` always measures existing files again before skipping them |

DownloadResult:

| key | type | meaning |
|---|---|---|
| `model_id`, `root` | str | as in the plan |
| `downloaded` | str[] | `relpath` of the files downloaded (sha256 checked) |
| `resumed` | str[] | of those, continued from a `.part` with HTTP Range |
| `restarted` | str[] | of those, started over because the server ignored Range |
| `skipped` | str[] | already there; the sha256 was measured in this run and matched |
| `bytes_downloaded` | int | bytes received in this run |
| `seconds` | float | wall time |
| `files` | object[] | `relpath`, `dest`, `sha256` of every file |

## config

`genmodelfit config [show|set|unset|path] --json` (0.11.0, #111; [download.md](download.md) "genmodelfit config").
The envelope has no `system` / `database`.

| key | type | meaning |
|---|---|---|
| `action` | str | `show` (also with no action) / `set` / `unset` / `path` |
| `path` | str | the config file (it may not exist yet) |
| `settings` | object | `show`: `comfyui-dir` and `lang`, each `{ value, from }`. `from`: `option` (`--lang`), `env` (`GENMODELFIT_COMFYUI_DIR` / `GENMODELFIT_LANG`), `file`, `default` (`comfyui-dir`: value null, download looks for ComfyUI; `lang`: `en`) |
| `warnings` | str[] | `show`: why the file or a value was not used (broken JSON, wrong type) |
| `key`, `value` | str | `set`: what was saved (`comfyui-dir` as an absolute path) |
| `removed` | bool | `unset`: false when the key was not set |

Errors (exit 1: unknown key, invalid value, broken or unwritable file) give `{ genmodelfit, command, error }`.

## Python API (stable)

**Only the names in `genmodelfit.__all__` are the stable API** (tests/test_judge_model.py fixes the new ones). Functions
in submodules (`genmodelfit.recommend.judge`, `genmodelfit.hardware.classify_gpu`, …) are internal and may change in
any release.

A short tour (moved from the README in 0.13.2):

```python
import genmodelfit as gmf

for f in gmf.recommend(modality="image", task="edit", limit=5):
    row = f.to_dict()  # the same dict as one item of `recommend --json`
    # memory_required_gb: what the chosen mode needs (the table's VRAM; RAM on CPU).
    # f.estimate.vram_gb is the all-on-GPU need, which can be larger than the GPU (GPU~ / GPU+RAM rows).
    print(f.model.id, f.model.precision, f.score, f.auto, f.estimate.fit, row["memory_required_gb"])

st = gmf.data_status()  # is the list fresh enough for Verified (auto) models?
if st.expired:
    print("run: genmodelfit update --from-release", st.expires_at)

# reading the list yourself: skip entries still waiting for review.
# on a malformed or wrongly typed list, load_db raises a RuntimeError (catch RuntimeError; the subclass
# genmodelfit.db.DatabaseError is not in gmf.__all__).
ok = [e for e in gmf.load_db().models if e.reviewed]

# one model, with the GPU your app detected (generation and precision are decided here)
fit = gmf.judge_model("image_sdxl_simple", gpu_name="NVIDIA GeForce RTX 3060", vram_gb=12, driver="560.94")
print(fit.to_dict()["fit_level"])

# AMD / Intel GPUs go through the same support tables as detection; refusals say why (other_gpus[].reason)
pc = gmf.system_from(
    [{"name": "AMD Radeon RX 7800 XT", "vendor": "amd", "vram_gb": 16}],
    windows=True,
    windows_build=22631,
    cpu="AMD Ryzen 7 7700X",
)
gmf.gpu_family("Intel(R) Arc(TM) B580 Graphics", "intel")  # {"backend": "xpu", ...} from the name table
db = gmf.load_db(use_cache=False, user_measurements=False)  # bundled list + bundled measurements only

# fetch the signed list with your own session, e.g. the app's CA bundle (network: only this and gmf.download)
db = gmf.update_from_release(session=my_session, dest=data_dir / "db.json", state_dir=data_dir)
```

Judging one model with a GPU the app detected itself (0.6.0, #27). The generation (compute capability) and ComfyUI's
compute precision are decided by genmodelfit from the GPU name and driver; nothing runs nvidia-smi or PowerShell:

```python
import genmodelfit as gmf

fit = gmf.judge_model(
    "hf:stabilityai/stable-audio-3-small-sfx",  # id, or a model name / part of an id (like `info` / `download`)
    gpu_name="NVIDIA GeForce RTX 3060",
    vram_gb=12.0,
    driver="560.94",
    ram_gb=32.0,  # None = this PC's memory (as gmf.system())
    gpu_usable=None,  # None: genmodelfit decides from the driver / generation; True: the app checked it
    db=gmf.load_db(),  # optional; an app may pass its bundled list
)
row = fit.to_dict()  # the same dict as one item of `recommend --json` models ("Fit" above)
row["fit_level"], row["run_mode"], row["memory_required_gb"], row["estimate_confidence"], row["licenses"]
```

| function | returns | notes |
|---|---|---|
| `gmf.system_from(gpus=(), *, ram_gb=None, gpu_usable=None, windows=None, windows_build=None, cpu=None)` | `SystemInfo` | `gpus`: dicts `{"name", "vram_gb", "driver"?, "compute_capability"?, "vendor"? (default nvidia), "pci_id"?}` or `Gpu`. The largest usable NVIDIA GPU is judged. (0.12.0, #96) GPUs with `vendor` `amd` / `intel` / `apple` go through the same support tables as detection (`hardware.md`, "GPUs other than NVIDIA"; `vram_gb` may be omitted for them: Intel discrete GPUs take the nominal VRAM from the name, integrated GPUs a share of `ram_gb`; `pci_id` such as `1002:744c` resolves generic names) and are judged only when no NVIDIA GPU is usable; each one is in `other_gpus` with `usable`, `reason` (why it is or is not used), `integrated` and `needs` (Adapter above, `source: app`). Before 0.12.0 non-NVIDIA GPUs were always treated as CPU. `windows_build`: the Windows build number (22000 or more is Windows 11, which AMD / Intel need; default: this PC's build when it runs Windows, else unknown, which refuses AMD / Intel; `ValueError` when the OS is not Windows). `cpu`: the CPU name (decides the generation of an AMD integrated GPU named only `AMD Radeon(TM) Graphics`; also `SystemInfo.cpu`; default this PC's). For the support tables `windows=True` is Windows, `False` is Linux (macOS on a Mac), `None` this PC's OS. `ram_gb` sets total = available and ignores this PC's page file (like `--ram`). `gpu_usable`: `None` = a GPU that no current PyTorch CUDA build supports, or whose driver is too old, is treated as CPU (reason in `notes`, [hardware.md](hardware.md)); `True` = the caller verified it (generation and precision are still decided here); `False` = CPU (for every vendor; `None` / `True` affect NVIDIA only). `windows` (default: this PC): Windows driver table and Windows ComfyUI precision (Pascal fp16). OS, CPU and free disk are this PC's. Bad GPU dicts raise `ValueError` |
| `gmf.judge_model(model, *, gpu_name=None, vram_gb=None, driver=None, compute_capability=None, ram_gb=None, cpu_only=False, gpu_usable=None, windows=None, gpu_vendor=None, windows_build=None, cpu=None, settings=None, content="sfw", system_info=None, db=None, comfyui_version=None)` | `Fit` | (0.12.0, #96) `gpu_vendor` (`amd` / `intel` / `apple`; default nvidia), `windows_build` and `cpu` are passed to `system_from` (`vram_gb=0` means unknown for non-NVIDIA).  `model`: an exact id is used whatever its content; otherwise the name lookup of `download` (filtered by `content`; several workflows of one model → the representative of the `recommend` row, the others in `fit.workflows`). Not found / several models → `gmf.ModelNotFound` (`.candidates`). PC: `system_info`, else `gpu_name` + `vram_gb` (both, else `ValueError`), else `cpu_only=True` (no GPU), else this PC (`gmf.system()`). `system_info` together with the GPU arguments → `ValueError` |
| `gmf.gpu_family(name, vendor, cpu="")` | `dict \| None` | (0.12.0, #96) looks up an AMD / Intel GPU name in genmodelfit's name tables (the same tables the support check uses), e.g. to choose the PyTorch build: `{"vendor": "amd"\|"intel", "backend": "rocm"\|"xpu", "arch": "gfx1101" (AMD gfx; null for Intel), "family": "RDNA 3" / "Arc B-Series (Battlemage)" ("?" for a gfx outside the table), "integrated": bool, "vram_floor_gb": float \| null (Intel discrete: nominal VRAM from the name, the smaller one if versions differ)}`. `None` when the name is not in the tables (e.g. Intel UHD / Iris, a GPU newer than the table). `cpu` decides an AMD integrated GPU named only `AMD Radeon(TM) Graphics`. Being in the table does not mean it runs on a given OS (RDNA 1 is listed); `system_from` decides that. Other vendors raise `ValueError`. No network |
| `gmf.load_db(path=None, *, use_cache=True, user_measurements=True, source=None)` | `Database` | `path`: a list file; else the cache list (`use_cache`) or the bundled one. (0.12.0, #96) `user_measurements=False` uses only the bundled measurements (the author's `measurements.json` and the community summary), not this PC's `bench` results in the cache, so every PC judges the same. `source`: the origin of `path` (`Database.origin`, the `source` of `gmf.data_status()`): `"path"` (default) or `"release"` for a file your app imported with `update_from_release`; other values, or `source` without `path`, raise `ValueError`. No network |
| `Database.generated_time` / `DataStatus.generated_time` | `datetime \| None` | (0.12.0, #96) `generated_at` as an aware UTC `datetime` (read like the release import: a trailing `Z`, any number of fraction digits); `None` when missing, malformed or without a time zone. Compare two lists with it. Not in `to_dict()` |
| `ModelEntry.license_unknown` / `ModelEntry.is_tool` | `bool` | (0.12.0, #96) the per-entry checks behind the default filters of `recommend` / `fit_list` / `search` (`include_unknown_license=False`, `include_tools=False`): some file (or the whole set when there are no files) has an unknown license; the entry is an auxiliary tool (preprocessing, audio separation, …). Properties like `reviewed`; not in `to_dict()` |
| `gmf.self_check()` | `dict` | (0.12.0, #96) whether the bundled parts are present, e.g. for a smoke test of a frozen app: `{"ok": bool, "version": str, "checks": {"db" \| "measurements" \| "calibration" \| "cryptography" \| "public_keys": {"ok": bool, "detail": str}}}` (`detail`: counts / version, or the error). Never raises, no network, prints nothing |
| `gmf.parse_safetensors_header(header)` | `HeaderInfo` | (0.12.0, #96) the JSON header of a `.safetensors` file (the bytes after the 8-byte little-endian length) → `HeaderInfo(precision, params, dtypes)` (`precision`: the dtype with the most bytes, as in `ModelFile.header_precision`; `params`: element count; `dtypes`: dtype → bytes; `to_dict()`). A malformed header raises `gmf.HeaderError` (a `ValueError`). No network |
| `gmf.platform_verification(system, measurements=None)` | `dict` | (0.11.0, #108) whether the kind of `system`'s GPU is verified: `{"verified": bool, "kind": "nvidia/dedicated" etc. \| None, "models": [names of the GPU models that ran]}` (verified with two or more). Without `measurements`, the bundled measurements (this PC's bench results are not included). The same as `system.platform_verified` in the CLI's JSON. No network |
| `gmf.set_language(lang)` / `gmf.get_language()` | `None` / `str` | the display language of the texts that genmodelfit returns (`estimate.reasons`, `notes`, gate `detail`, error messages, …): `"en"` or `"ja"`; `None` clears it. **The default is English**; the OS language is not used. Order (first wins): the CLI `--lang`, the environment variable `GENMODELFIT_LANG`, `set_language()`, `en`. The CLI `--lang` holds only inside that call (a `contextvars.ContextVar`), so it does not change the language of library calls in other threads. A text is fixed in the language that was active when the object holding it was created (a `Fit` made in `ja` keeps Japanese `notes` after `set_language("en")`). Only the language of those texts changes; keys, ids, codes, scores and judgments do not. Free text stored in the list (`review.note`, `license_evidence`, measurement `source` / `note`) is not translated. Other values raise `ValueError` (#73) |

Fetching the distributed list from Python (0.7.1, #58). Also in `__all__`: `gmf.RemoteError` (a `DatabaseError`):

```python
db = gmf.update_from_release(
    "https://github.com/laxshmate/genmodelfit/releases/download/data-latest",  # default
    timeout=30,
    dest=data_dir / "db.json",  # default: the cache db.json
    force=False,  # True: accept a list older than the one imported before
    session=my_requests_session,  # default None: genmodelfit's own session
    state_dir=data_dir,  # default None: the cache folder
)
```

| parameter | meaning |
|---|---|
| `session` | a `requests.Session` (e.g. with `verify` set to the app's CA bundle, or proxies). All GETs of this call use it. Rules below |
| `state_dir` | where `release_state.json` (the `generated_at` of the list imported before) is read and written. Default: the cache folder. Environment variables and module globals are not changed |

Rules for `session`:

- Its `verify` / `proxies` / other headers are used as they are, and the session is not modified.
- `~/.netrc` credentials are never sent. Every request carries a no-op `auth` (requests reads netrc only when neither the request nor the session has `auth`). Redirects are followed by genmodelfit (`allow_redirects=False` per hop) because requests re-reads netrc on each redirect.
- The fixed `User-Agent` (`genmodelfit/<version> (+https://github.com/laxshmate/genmodelfit)`) is set per request.
- **Limits**: if the session itself has `auth`, it is the caller's choice and is used for the first host only (never for a redirect to another host; an `Authorization` header set on the session is also dropped there).
- Cookies, hooks and adapters of the session are not touched by genmodelfit, but the session does what it normally does: custom non-`Authorization` headers set on it are also sent to redirect targets (requests' default), and `Set-Cookie` of the responses updates its cookie jar.
- An empty `auth` (`None`, `()`, `""`) counts as none.
- `verify` and `proxies` of the session are passed per request, so the caller's CA bundle wins over `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` (requests#3829). Proxy environment variables fill only what the session lacks.
- Redirects are followed up to 10 times with a caller session (requests' default is 30; the session's `max_redirects` is ignored). An invalid redirect URL (e.g. port 99999) is a `RemoteError`.
- Signature check, `MAX_BYTES`, the one retry and the errors (`RemoteError`) are the same as without `session`.

Without `session`, the call uses `fetch.make_session()` as before (the private `remote._http_get` hook still works, but is not
stable API).

Equivalence: with `gpu_usable=True, windows=True, ram_gb=1024` it gives the same `Estimate` as the internal calls the
AI生成キット (AI Gen Kit) used up to genmodelfit 0.5.0 (`hardware.classify_gpu` + `comfy_precision` → `recommend.judge`, then
`recommend.vram_need` = `memory_required_gb` before rounding to 2 places).

**Name shadowing**: `genmodelfit.recommend` is the function above and hides the submodule of the same
name, so `import genmodelfit.recommend as r` binds the function. Use the functions in `__all__`; if a module is
really needed, `importlib.import_module("genmodelfit.recommend")` (internal, not stable).

## Database file (`db.json`)

```json
{"schema_version": 1, "generated_at": "2026-10-20T03:17:00Z", "sources": {…}, "models": [ModelEntry, …]}
```

`generated_at` is always an aware UTC ISO-8601 string ending in `Z`.
`downloads`, `likes`, `license_from`, `repo_license`, and (PR #13 review) `ModelEntry.license_name` /
`license_evidence` / `files_complete` / `size_floor_bytes` and `ModelFile.license_url` / `license_path` /
`license_evidence`, and (#3) `ModelFile.head_status` / `head_checked_at`, and (#30) `ModelEntry.workflow_precision` /
`precision_source` and `ModelFile.repo_precision` / `repo_precision_source`, and (#36, 0.5.0) `ModelEntry.languages` /
`runtime` / `description` / `card_languages` and `source: "manual"`, and (#81, 0.12.0) `ModelFile.optional` /
`optional_replaces` were added without changing `schema_version` (optional fields; older
readers ignore unknown keys: `from_dict` drops them, `check_entry` only checks the keys it knows, and `source` is a free
string there. `tests/test_appinfo.py` reads the bundled list with a copy of the old reader).
Task `tool` was added for Image Tools / Video Tools templates without a known tag.

**Types are checked when a list is read** (0.9.1, #63; `db.check_entry`): every known key of an entry, a file, a
template and a review must have its documented type (null where the tables say `| null`), and the top-level
`generated_at` / `sources` too; otherwise `DatabaseError` and nothing is loaded. Null lists (`content_evidence`,
`discovered_from`, `license_evidence`) and null `languages` / `description` are read as empty. A cache list that
fails this is skipped and the bundled list is used (also as `update`'s previous list). Unknown keys are still ignored.

`sources` (written by `genmodelfit update`, see [sources.md](sources.md)):

| key | meaning |
|---|---|
| `comfyui-templates` | `url` (the templates' `index.json`), `license` (of the template repository, `MIT`), `templates_total`, `local`, `skipped` (by reason: `api` / `not_open_source` / `no_size` / `category`), `loaded`, `failed` |
| `huggingface` | `api` (the Hub API URL pattern), `repos` (API calls), `failed`, `rate_limited` (429 waits), `rate_limit_waited_s`, `failures` (first 20), `unauthorized` (repo ids whose Hub API answered 401 — the anonymous API answers 401 both for private and for missing repos; their licenses stay unknown; 0.2.0), `unauthorized_missing` (the subset confirmed not to exist, `repo_precision.KNOWN_MISSING`). After `update --inspect-only`: `unauthorized_recheck` = `{unauthorized, private_or_missing, missing, readable, failed, checked_at}` (those repos asked again; `readable` ones get their license on the next full `update`) |
| `huggingface-search` | `queries`, `candidates`, `added`, `skipped` (by reason, incl. `duplicate_weights`), `excluded`, `failed_queries`, `all_time_missing` (candidates without an all-time download count) |
| `partial` | bool: some upstream data could not be fetched (then `update` exits 1 unless `--allow-partial`) |
| `partial_counts` | `templates_failed`, `hf_repos_failed_transient`, `entries_affected`, `hf_search_failed`, `rate_limit_budget_exhausted`, `carried_over` |
| `license_policy` | version of the license rules (`update.LICENSE_POLICY`, now 5). Values are carried over from a previous list only when it has the same version |
| `civitai` | why it is not used |
| `size_check` | `templates`, `equal`, `within_10pct` (index `size` vs the sum of the files) |
| `counts` | `models`, `modality`, `content`, `source`, `license` (null = unknown), `license_tier`, `files_complete_false` |
| `head_check` | `checked_at`, `urls` (unique HF URLs sent a HEAD), `status` (count per status code), `unchecked` (no answer), `not_sent` (files on other hosts, per host) |
| `file_headers` | `files`, `urls` (main weight files on HF), `from_cache`, `fetched`, `failed`, `skipped_offline`, `errors` (first 20), `checked_at`, `precision_unknown` (entries whose precision is still null) |
| `sampler_stages` | only after `update --inspect-only`: `templates`, `failed`, `multi_stage` (entries with two or more sampler sets), `workflow_precision` (entries with a precision inferred from the loader node), `optional_parts` (entries with optional parts; 0.12.0, #81), `checked_at` |
| `repo_precision` | `files` (largest main files on HF whose header and name tell no precision), `repos`, `from_cache`, `fetched` (requests), `hf_api`, `config`, `unknown` (files decided by each / by none), `skipped_offline`, `unauthorized` (repos whose API answered 401), `size_mismatch` (repos whose API dtype was not used because the size did not match), `errors` (first 20), `checked_at`, `precision_unknown` (entries whose precision is still null, final), `precision_source` (entries per source, `unknown` = null) |
| `manual` | `models` (number of hand-added models from `src/genmodelfit/data/manual.json`), `ids` (0.5.0) |
| `app_fields` | `languages_prompt`, `languages_speech` (entries with a non-empty list), `runtime`, `description`, `from_card` (entries with a saved Hugging Face card language, `card_languages`) (0.5.0), `card_languages_carried` (entries whose card languages were carried over from the previous list; full `update` only) |
| `comfyui` | `latest_release` (tag of the latest ComfyUI release, e.g. `v0.42.1`; null if it could not be read), `url`, `checked_at` |
| `excluded` | number of models dropped by the exclusion rule (names are not kept) |
| `review` | `{ok, pending, marked: {reason: count}, rejected_skipped}`; the published list adds `withheld_pending` (see "review") |
| `warnings` | str[] |
| `generator` | `genmodelfit <version>` that wrote the list |

## update

`genmodelfit update --json` prints the envelope with `output` (path written) and `sources` (above).
On failure it prints `error` and exits 1; the previous cache is left untouched.
`update --from-release --json` prints a smaller object: `genmodelfit`, `command` (`update`), `models` (**int**: number of
models taken in), `generated_at`, `output`, `status` (DataStatus with `source: release`).
`update --json` (rebuild / `--heads-only` / `--fields-only` / `--inspect-only`) also has `status` (DataStatus of the list written; `source` is `cache` for the
cache, `path` for `--output` elsewhere).
`update --heads-only` reads the current database (`--db`, cache or bundled), redoes only the HEADs and the ComfyUI
version, and writes it back (`--output`, default the cache).
`update --fields-only` reads the current database the same way and rebuilds only the app fields (`languages` /
`runtime` / `description`, from `src/genmodelfit/data/overrides.json`, the tables and the Hugging Face cards) and the hand-added
models (`src/genmodelfit/data/manual.json`): it reads the cards again (Hub API) and, for the hand-added models only, sends HEADs,
reads the file headers and compares the licenses with the upstream cards; with `--offline` it sends nothing and
uses the saved `card_languages`. The other entries' files, licenses, precision and HEAD results are not touched,
except that the hand-added components (`components` in `src/genmodelfit/data/manual.json`, above) are added to their entries (with a
HEAD of the new files only, none with `--offline`).
`update --inspect-only` reads the current database the same way and redoes only what is read from the files
themselves: the main weight files' headers (`header_precision` / `header_params`, then `precision`), the official
templates' `sampler_stages` and `workflow_precision`, the repo precision (`repo_precision`: Hub API / `config.json`,
cached in `<cache>/repo_precision.json`), and it asks the Hub API again for the repos in
`sources.huggingface.unauthorized`; nothing else changes (except the review marks, see "review"). Headers already in `<cache>/file_headers.json` (keyed by URL and
sha256) are not read again; requests are sent one at a time with a short pause and honor `Retry-After`. With
`--offline` it uses only that cache and sends nothing (the templates are then not re-read).

Measurements live in `measurements.json` (`{"unit": "GiB", "unit_note": "…", "measurements": [Measurement, …]}`), bundled plus
`measurements_community.json` (community rows, see contrib) plus `<cache>/measurements/*.json`. NaN / Infinity and
negative values are refused when read (0.9.0).
