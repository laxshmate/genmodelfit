# Contributing measurements

**English** · [日本語](CONTRIBUTING.ja.md)

genmodelfit prefers a value measured on a real PC (a measurement) over its estimate. The more GPUs are measured, the more accurate the estimates get.
This page is for anyone who wants to send the result of `genmodelfit bench` as a pull request. It also lists the rules for other changes.
For terms, see [README.md](README.md).

## Contents

- [1. Measure](#1-measure)
- [2. Create the submission JSON](#2-create-the-submission-json)
- [3. Check the contents](#3-check-the-contents)
- [4. Send a pull request](#4-send-a-pull-request)
- [Other changes](#other-changes)
- [License](#license)

In short: install `genmodelfit>=0.9.1` (or `pip install -e .` in your clone, so that local checks match CI), run `genmodelfit bench` against your own ComfyUI in a clone of your fork, check the file with `genmodelfit contrib show`, and open a pull request that adds only that file.
**genmodelfit itself never sends anything**; sharing happens only through the pull request you open.

## 1. Measure

1. Start ComfyUI (the one you normally use is fine). Measure **when no other generation is running**.
   - bench does not measure while the queue has jobs (it checks twice: before starting and just before sending).
   - It also stops when GPU utilization (averaged over about 2 seconds) is 30% or more.
   - Utilization is read with `nvidia-smi` on NVIDIA, `amd-smi` or `rocm-smi` (Linux) on AMD, `xpu-smi` (Linux) on Intel, Windows performance counters for AMD and Intel on Windows, and `ioreg` on Apple.
   - If the GPU's free state cannot be checked, bench stops instead of continuing silently (`--force` lets you measure anyway). Examples: no `nvidia-smi` on NVIDIA, several GPUs with the same name so it is unclear which one, or ComfyUI on another PC.
   - If a non-NVIDIA PC has no tool to read utilization, bench does not stop. It only checks that the ComfyUI queue is empty and that no app other than ComfyUI uses more than half of the GPU memory, and leaves a warning and a `note`. Close other apps (games, video export, and so on) before measuring.
   - **Measurements on AMD, Intel and Apple GPUs are especially welcome.** They have no measurements yet, so their levels are capped at Good. Measurements of the same configuration let us tighten the estimates.
2. Put the model files the template uses into ComfyUI's `models` folder.
   - If files are missing, bench shows which ones.
   - `genmodelfit download <template name> --comfyui-dir <ComfyUI folder>` downloads the files in the list into ComfyUI's models folder (checked with sha256). It does not download files whose license is unknown, or gated files.
3. Measure:

```powershell
pip install -U "genmodelfit>=0.9.1"
genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188 --resolution 768x768 --steps 6
genmodelfit bench text_to_video_wan --url http://127.0.0.1:8188 --resolution 512x512 --frames 49 --steps 20
genmodelfit bench audio_ace_step_1_5_checkpoint --url http://127.0.0.1:8188 --length 20 --steps 8
genmodelfit bench 3d_hunyuan3d_image_to_model --url http://127.0.0.1:8188 --steps 15
```

Always pass `--url` (there is no default URL, so you cannot send to another ComfyUI by mistake).

Template and settings:

- `<template>` is the name of an official ComfyUI template (`genmodelfit info <name>` shows the ID in the list).
- The workflow is built from the template ComfyUI has, or else from the official template on GitHub.
- To measure your own workflow, save it with "Export (API)" in ComfyUI and pass the JSON with `--workflow`.
- Change settings with `--resolution WIDTHxHEIGHT` (or `--width` and `--height`), `--frames`, `--length SECONDS` (audio) and `--steps`.
- Change other inputs with `--set INPUT_NAME=VALUE` (every input with that name changes).
- A result is recorded as "non-standard" (`standard: false`) and is not used for estimates in two cases: you changed anything other than size, length, steps and seed with `--set`, or you used `--workflow`.
- For `--set`, only numeric values are kept in `note`. For strings only the name is kept, since a string may be a prompt.
- Measuring with the same settings as an existing measurement (`src/genmodelfit/data/measurements.json`) makes comparison easier.

Model loading and caching:

- By default bench does not send `/free`, so it does not unload models other people loaded.
- If models are already loaded, their share can end up in the result. bench then shows a warning and leaves the amount before the start in `note`.
- **If the ComfyUI is only yours, add `--free`.** It unloads models and clears the cache of earlier results, then measures from loading.
- If you send the same content again without `--free`, ComfyUI returns the earlier result and runs nothing, so bench reports that it could not measure.
- If only some nodes reused earlier results, the memory for those nodes was not measured, so the result is "non-standard".
- The result is also "non-standard" if other jobs ran in ComfyUI during the measurement, or if the run took 10 seconds or more to start after sending.

Timeout and files:

- On timeout (`--timeout SECONDS`, default 1800) or Ctrl+C, bench cancels only its own prompt. It interrupts it with the prompt_id if running, or removes it from the queue if waiting. It does not stop other people's jobs.
- For templates that need an input image (such as image to 3D), bench uploads the official template's image to ComfyUI. It never overwrites: if a different image has the same name, ComfyUI gives a new name.
- Output of save nodes goes to `genmodelfit_bench/` in ComfyUI's output folder. Delete it if you do not need it.

### What is measured

| Field | How it is measured |
|---|---|
| `peak_vram_gb` | Reads `/system_stats` every 0.2 seconds during generation and takes the maximum of the amount torch allocated (`torch_vram_total`). This is the ComfyUI process's share and does not include the CUDA context (about 0.3 to 0.5 GB). If `torch_vram_total` is unavailable, the increase over the whole GPU is used instead and a warning is left. |
| "GPU-wide increase" in `note` | The maximum total GPU usage (`nvidia-smi`; the tool above for non-NVIDIA, about every 1 second) minus the value before the start. Changes by other apps are included. Not written if there is no tool. |
| `peak_ram_gb` | Maximum resident memory of the ComfyUI process (and its child processes) listening on the port. Only processes whose command line contains `main.py` or `comfy` are counted. Empty if ComfyUI is on another PC or the process cannot be identified. |
| `seconds` | From sending the workflow to its end (including model loading). The run time ComfyUI counted goes into `note`. |
| `offload` | Read from the ComfyUI log: `dynamic` (dynamic VRAM) / `partial` (only part was put on the GPU) / `lowvram` (a startup option such as `--lowvram`) / `none` (everything fit) / `oom` (failed from out of memory). Empty if unknown. |
| `gpu_vram_gb` | Total VRAM from `/system_stats` in GiB, rounded to 2 decimals (about 8.0 for a nominal 8 GB GPU). Old bundled rows hold the nominal value (such as `16`). |
| `outcome` | `ok` (ran to the end) / `oom` (failed from out of memory) / `error` (failed for another reason) |
| `standard` | Whether the official template was kept as is (only size, length, steps and seed changed), no other job got mixed in, and all nodes were executed. |

Out-of-memory (OOM) data is welcome too: it is important data saying "this GPU cannot run it". Always send it with `outcome: "oom"` unchanged.

- bench only shows a failed result and does not save it to the cache (cached measurements are used for estimates on that PC right away).
- To save it, add `--save-failed` (to the cache) or `--output PATH`.
- "Non-standard" results are the same: save them with `--save-nonstandard` or `--output`.
- Estimates use only measurements with `outcome` `ok` and `standard` `true`.

## 2. Create the submission JSON

Clone your fork (GitHub Fork) and run bench with `--submission` in that folder. Running `pip install -e .` in the clone lets you run `contrib validate` with the same main version as CI.

```powershell
git clone https://github.com/<your-name>/genmodelfit
cd genmodelfit
pip install -e .
genmodelfit bench image_sdxl_simple --url http://127.0.0.1:8188 --resolution 832x832 --submission data/contrib/
```

- The file is a submission file (`contrib_schema` 1). If you pass a folder to `--submission`, the file is written as `data/contrib/<GPU>/<date>-<sha8>.json`. Example: `data/contrib/rtx-3070/2026-10-21-1a2b3c4d.json`, where `sha8` is the first 8 hex digits of the content's sha256. If you pass a file name, it is written under that name.
- Pass the folder with a trailing `/` (`data/contrib/`). On a first submission the folder does not exist yet, and without the `/` a file named `data/contrib` is created.
- You can also create it from a result you measured and saved earlier (`<cache>/measurements/....json` or a file from `--output`): `genmodelfit contrib make --from <bench result> --output data/contrib/`
- If ComfyUI was on another PC, add `--no-pc-info` to leave out this PC's OS, memory and GPU generation.
- Submit failed (OOM) results as they are. Results under non-standard conditions are accepted as records but not used in aggregation.

### What goes in and what does not

| Goes in | Example |
|---|---|
| GPU name, total VRAM, generation, compute capability, driver version (generation and compute capability only on NVIDIA) | `NVIDIA GeForce RTX 3070`, `8.0`, `ampere`, `8.6`, `566.03` |
| GPU vendor and PyTorch backend (from the 0.9.0 bench) | `amd` and `rocm`, `intel` and `xpu`, `apple` and `mps`, `nvidia` and `cuda` |
| GPU PCI model ID (0.9.0; only for AMD and Intel with ComfyUI on the same PC; a model number that does not identify the unit or the slot) | `1002:744c` (Radeon RX 7900 XTX; the same on any PC with that model) |
| Apple GPU chip name (0.9.0; only names matching the system_profiler form) | `Apple M2 Pro` |
| ComfyUI version, OS family (no version or build), main memory (rounded to an integer GiB) | `0.40.0`, `windows`, `32` |
| Model ID, settings, result, peak VRAM / memory, time, how it was loaded, how it was measured | `image_sdxl_simple`, `832x832`, `ok`, `6.9` ... |
| Date (UTC date only) | `2026-10-21` |
| Note (`note`: bench's description of how it measured) | A sentence containing something that looks like a path, URL, PC name, user name, email, UUID, IP or time is removed as a whole |

**What does not go in**: PC name, user name, folder paths, GPU UUID, PCI slot address (bus number; different from the model ID), serial numbers, IP / MAC addresses, CPU name, time, the text given to bench's `--source`, and prompts.
Fields other than the defined ones are rejected by the CI check.
However, the automatic check looks only at the shape of the text. It cannot spot a name written in ordinary words in `note` (such as "measured on Taro Yamada's PC") or a path rewritten with base64 and the like. Read the file yourself in step 3.

## 3. Check the contents

Always do this before sending.

```powershell
genmodelfit contrib show data/contrib/rtx-3070/2026-10-21-1a2b3c4d.json
```

This prints everything in the file in readable form, and prints the result of a privacy checklist as `[ok]` / `[NG]`. The checklist looks for paths, PC name, user name, UUID, PCI, IP, URL, email, time and unknown fields, and also checks that this PC's name and user name are not in the file.

- If there is an `[NG]`, fix it before sending. If you edit the file by hand, the `sha8` in its name changes, and `contrib validate` tells you the correct name.
- **Read the `note` yourself.** The automatic check does not understand what a sentence means. You may delete any sentence you are unsure about.
- `genmodelfit contrib validate data/contrib` runs the same check as CI on your PC.
- Only mechanical checks (shape, value range, personal information, duplicates, file name) make a submission fail (the same approach as llmfit; 2026-10-22).
- Notices are shown but do not fail. They are: a value more than 40% off the estimate by the formula; a value smaller than the lower bound (weights + fixed part) although everything was put on the GPU (`offload: none`); a value that changes the level on a PC with the same GPU (checked with the submission's VRAM and with variants of the same name that have different VRAM, each with 16 / 32 / 64 GB of memory); a model not in the list; and an unknown GPU.
- Notices are shown above the job summary, and the person who merges the PR reads them. If there is a reason, write it in the PR description (you may be asked to measure again).

## 4. Send a pull request

1. Add only `data/contrib/<GPU>/<date>-<sha8>.json`.
   - Do not change other files (put code changes in a separate PR).
   - The `Contrib` workflow fails a PR that changes files outside data/contrib.
2. Create a branch, commit, and push to your fork:

   ```powershell
   git switch -c contrib/<GPU>-<model>
   git add data/contrib/<GPU>/<date>-<sha8>.json
   git commit -m "contrib: <GPU> <model>"
   git push -u origin HEAD
   ```

   Then create a pull request on GitHub against main of laxshmate/genmodelfit.
   - It helps if the description lists the ComfyUI startup options (such as `--lowvram`; only those you changed).
   - A personal name or email address is not needed.
   - For a first-time contributor, CI may not run until a maintainer allows the workflow.
3. CI (the `Contrib` workflow) runs `genmodelfit contrib validate` to check shape, value range, personal information, duplicates and file name, and writes the result to the job summary (Summary).
   - It also writes the result of `contrib aggregate --report` to the summary.
   - The check runs with genmodelfit from the PR's target (main).
   - The merge is the maintainer's review (there is no separate mechanism where a person approves each submission).

**genmodelfit does not send measurements.** The only thing shared is the content of the pull request you open on your own GitHub (there is no feature that creates a PR automatically). Once merged, the submission is published under MPL-2.0 (see [License](#license) below).

### After it is accepted

- The maintainer groups submissions with the same model, settings and GPU using `genmodelfit contrib aggregate --output src/genmodelfit/data/measurements_community.json` (outliers are removed with the median and MAD).
- Only **PCs with the same GPU as the measured one** use the result. Judgments for PCs with other GPUs do not change (0.9.1; the same approach as llmfit). "Same GPU" means:
  - the PCI model ID is the same (when both have it), otherwise the normalized name is the same (a generic name is not counted as a match), and
  - the ratio of total VRAM is 0.97 or more. An 8 GB and a 16 GB RTX 4060 Ti are treated as different GPUs.
- On such a PC, **from 1 submission** the basis of the estimate for that configuration becomes `measured_community` (other users' measurements), and the count (`sample_count`) is shown.
- The value is used in both directions, lowering or raising the required amount. A value for everything on the GPU is never lowered below weights + fixed part, and never raised above the GPU's total.
- A measurement of the same GPU and the same settings also becomes grounds for deciding the level. For GPUs other than NVIDIA, the Good cap is lifted if that GPU has a measurement. See [docs/accuracy.md](docs/accuracy.md) for details.
- For a submission PR, the `Contrib` workflow shows the result of `contrib aggregate --report` in the summary. The calibration table (calibration.json, [docs/accuracy.md](docs/accuracy.md)) is rebuilt by a maintainer with `python -m genmodelfit.calibrate` when importing measurements.

### Maintainer measurements (measurements.json)

The bundled `src/genmodelfit/data/measurements.json` holds the measurements the maintainer made. Its fields are in the [Measurement](docs/schema.md#measurement) section of docs/schema.md, and the submission format is in the [contrib](docs/schema.md#contrib) section. Send users' measurements to data/contrib (do not edit measurements.json directly).

## Other changes

Code changes are welcome too.

- When adding a dependency, use only MIT / Apache-2.0 / BSD ones (apps that embed this tool distribute without publishing their source).
- Do not import ComfyUI code (GPL-3.0); talk to it over HTTP only.
- Put the same 3-line MPL-2.0 notice as in other files at the top of every new `.py` file.
- Before sending, run `python tools/ci_local.py` to pass the same checks as CI on your PC (tests on Python 3.10 to 3.13, ruff, and the package). You only need [uv](https://docs.astral.sh/uv/). On Windows, `--linux` also runs it inside WSL.
- For PRs from forks, GitHub's CI runs as well.

## License

The code of genmodelfit and the bundled data (db.json, measurements, coefficients) are under [MPL-2.0](https://github.com/laxshmate/genmodelfit/blob/main/LICENSE).

- Fixes, measurements and other data you send are accepted **as released under MPL-2.0**, like the rest of the project (inbound = outbound).
- By opening a pull request you agree to this.
- There is no CLA (a document assigning copyright or granting extra permission).
- Only submit work you wrote yourself or are allowed to release under MPL-2.0.

