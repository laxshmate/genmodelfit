# Downloading models (`genmodelfit download`)

**English** · [日本語](download.ja.md)

The detailed rules of step 5 ("Download it.") in "2. Procedure" of the [README](../README.md).
For CLI users and for app developers using the Python API.
The JSON shape is in [schema.md](schema.md) (section "download").
The implementation is `src/genmodelfit/downloader.py`; `tests/test_download.py` checks these promises.

## Command line

```
genmodelfit download "Z-Image-Turbo" --list                 # what would be downloaded (no network, writes nothing)
genmodelfit download "Z-Image-Turbo"                        # into the ComfyUI found on this PC (asks first)
genmodelfit download "Z-Image-Turbo" --comfyui-dir C:\ComfyUI
genmodelfit config set comfyui-dir D:\ComfyUI_windows_portable\ComfyUI   # use this ComfyUI from now on
genmodelfit download image_z_image_turbo_int8 --output-dir D:\models --budget 15 --yes
genmodelfit download MODEL --uncensored --any-license --yes   # without the question (scripts)
```

- **Which files**: all files of one workflow.
  - A model name (as in the tables) picks the workflow and precision like `recommend`: the highest precision that runs on this PC without offloading (good or better on the GPU); when none does, the best fit level, then the faster run mode, then the higher precision. The plan says which rule chose it.
  - A workflow id is used as is.
  - `--precision int8` picks a precision.
  - `--budget 15` (GB) caps the download (files already there are not counted): the best workflow within it is chosen, and when none fits the download is refused.
- **Where**: each file goes to the ComfyUI folder the template names (`models/diffusion_models`, `text_encoders`, `vae`, `loras`, `checkpoints`, ...). Subfolders in the file name are kept. Which ComfyUI is decided as in [Choosing the place](#choosing-the-place) below; `--output-dir` uses the same layout for a folder outside ComfyUI.
  - Files whose ComfyUI folder is unknown (many Hugging Face models) go to `other/`. ComfyUI does not read `models/other`: the plan says so, and you move them (to `checkpoints`, `diffusion_models`, `loras`, ... as the model page says) before use.
  - For a file without a folder in the list (from any source) the folder is inferred only from one piece of evidence in the bundled list: the main file's header parameter count matches a file of an official ComfyUI template within 0.001%, and all matching files are in one folder (`unet` and `diffusion_models`, `clip` and `text_encoders` count as one; e.g. an SDXL all-in-one file has the same count as `sd_xl_base_1.0.safetensors`, so `checkpoints`). The plan shows the basis (`directory_basis` in JSON). Names and tags are not used to guess. A GGUF file placed in `diffusion_models` / `text_encoders` this way needs the ComfyUI-GGUF custom nodes; the plan says so.
  - Since #75 the main files of 12 Hugging Face models go to `checkpoints/` or `diffusion_models/` instead of `other/`. If you downloaded one of them with 0.9.2 into `other/`, it is downloaded again into the new folder; move the file there yourself first to skip that (the sha256 is checked again).
- **Consent first**: before anything is downloaded you see:
  - every file with its license and the license URL. A file with no license URL says "no license stated" and shows the page to check (the Hugging Face page of the model, or the page stored in the list); the consent question then asks you to check those pages yourself;
  - licenses that are not on the allowlist, marked (commercial use without use restrictions; see [gate.md](gate.md); change it with `--license-allowlist`);
  - the total size and the free disk.
  - Then you are asked `[y/N/c]` (default No; `c` changes the place, see [Before downloading](#before-downloading)). `--yes` skips the question (required with `--json`).
  - Uncensored models and files with no stated license: in a terminal (stdin and stdout are terminals, no `--yes` / `--json`) they do not stop the plan; they are shown, and the question names them ("This model is uncensored (may include adult content) and has files with no stated license (check the pages above). Download anyway, ...?"). One `y` covers everything named. Without a terminal, with `--yes` or `--json` you must pass `--uncensored` and / or `--any-license` (old forms `--content all`, `--include-unknown-license`). `--list` shows what a real run would do: in a terminal (no `--yes`), one `consent:` line with what will be asked and the command for scripts (with `--uncensored` / `--any-license` and `--yes`); otherwise "cannot download" and the flags command. Consent flags must be written in full (`--u` or `--an` is a usage error, exit 2), and `--uncensored` cannot be combined with `--content sfw|uncensored`. The Python API is unchanged: pass `content=` / `include_unknown_license=` yourself.
  - Read the terms of each file before you use the model.
- **Refused**: if any file matches, nothing of that workflow is downloaded. It never downloads only part of it.
  - Each reason is shown once. When only `--uncensored` and / or `--any-license` are missing (no terminal or `--yes`), the plan ends with "To download anyway (run it in a terminal to be asked y/N instead of adding flags):" (with `--yes`: "drop --yes to be asked y/N instead, or add the flags below") and one command that adds all of them (keeping your other options), with one line per flag on what you agree to. It never adds them by itself. For gated files, files genmodelfit cannot download (their URLs are listed so you can get them yourself) and workflows without files it says genmodelfit cannot download the workflow, and names another workflow of the same model without that problem when there is one. For disk space, `--budget` and a different file with the same name it names the lever (free space or `--output-dir`, `--budget`, move the file), then the consent flags if they are also missing. In both cases no command is shown. The command uses the workflow id of the plan and the program form you ran (`genmodelfit` or `python -m genmodelfit`); arguments are put in single quotes when needed. A short `error:` line goes to stderr (exit code 1).
  - Gated files. genmodelfit never handles logins or tokens: request access on the model page and download them yourself.
  - Uncensored models, unless you agree (y in a terminal, or `--uncensored` / `--content all|uncensored`).
  - Files whose license is unknown, unless you agree (y in a terminal, or `--any-license`).
  - Files without a sha256 or size in the list.
  - Hosts other than Hugging Face / GitHub.
- **Safety**:
  - Every file is checked with the sha256 in the list while it is written. A mismatch deletes it and fails.
  - A download goes to `<file>.part` first. After Ctrl+C or a network error, run the same command again and it resumes with an HTTP Range request. If the server ignores Range it starts over, at most 3 times.
  - Files already there are hashed again right before they are skipped. A different file with the same name is never overwritten.
  - A `<file>.lock` next to the file stops two downloads of the same file at once. A lock left by a process that is gone is cleaned up.
  - It does not start unless the free disk is the download + 1 GB.
  - Nothing is written outside the destination. These are refused: names with `..`, absolute paths, drive letters, `\`, reserved Windows names (also as folder names), control / bidi characters, and symbolic links / junctions that lead outside.
- **Network**:
  - Only the URLs in the list are used (checked anonymously with HEAD by `update`; `download` itself does not require a HEAD result), https only.
  - The first URL must be on `huggingface.co` or `github.com`.
  - Every redirect must stay on these hosts (https, port 443 only): `huggingface.co`, `*.huggingface.co`, `hf.co`, `*.hf.co` (CDN, Xet), `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`.
  - A URL that could be read as two different hosts is refused before any connection: `\`, spaces, control characters, `%` or `@` in the host, non-ASCII hosts. The host is read the way requests / urllib3 read it.
  - No `~/.netrc` credentials, no tokens, nothing about this PC (User-Agent and Range only).
  - On HTTP 429 / 500 / 502 / 503 / 504 it waits for `Retry-After` (or backs off) and retries. It stops after 4 failures in a row without progress.
- **Progress** (on stderr): in a terminal, one line for everything and one per file with a bar, size, speed and time
  left, redrawn in place; while the sha256 is checked the file says "verifying". Without a terminal it prints a plain
  line when a file starts and every 10% of the whole. Nothing with `--json`.

## Choosing the place

The first of these that is set decides the place. The plan always says which one (`destination_from` in JSON).

1. `--comfyui-dir <ComfyUI>` or `--output-dir <folder>`. `--comfyui-dir` must look like a ComfyUI folder (`models` or `main.py`); `--output-dir` is any folder outside ComfyUI, with the same layout.
2. The environment variable `GENMODELFIT_COMFYUI_DIR` (checked like the flag).
3. `comfyui-dir` saved with [`genmodelfit config`](#genmodelfit-config). If that folder is gone, it is an error that says how to remove the setting.
4. Found automatically. Every place found is shown on the confirmation screen:
   1. The current folder, when 4 of `main.py`, `comfy/`, `nodes.py`, `comfy_extras/`, `comfy_api/` are there (the same test as comfy-cli). When `measure` / `share` would start the ComfyUI found here, which runs that folder's python, they first show the folder and the python and ask y/N (without a terminal or with `--json` it is not started; a folder given with `--comfyui-dir` is not asked about).
   2. A ComfyUI running on this PC: `GET http://127.0.0.1:8188/internal/folder_paths` and the same on port 8000 (the ComfyUI and ComfyUI Desktop defaults), 2 s timeout, no redirects, no proxy. ComfyUI says this endpoint is for its own front end only and may change, so any missing or odd answer is skipped silently. The ComfyUI folder is the parent of `custom_nodes` (else of `models`). Asked only when downloading, and by `measure` / `share` to find the ComfyUI they measure on: `--list` and the other commands never connect.
   3. The ComfyUI Desktop settings: `basePath` in `config.json` of its user data folder (Windows `%APPDATA%\ComfyUI`, macOS `~/Library/Application Support/ComfyUI`, Linux `~/.config/ComfyUI`), and its `extra_models_config.yaml` there. comfy-cli: `default_workspace` / `recent_workspace` in its `config.ini` and its default workspace (`~/Documents/comfy/ComfyUI` on Windows / macOS, `~/comfy/ComfyUI` on Linux). The newer "Comfy Desktop" is not looked for: its folders are not in the source we checked.
   4. Usual places, one level only: `ComfyUI` and `ComfyUI_windows_portable\ComfyUI` directly under each fixed drive (`C:\`, `D:\`, ... on Windows), Documents, Desktop, Downloads and the home folder.

   A place counts only if it looks like ComfyUI (4 of the 5 names above, or a ComfyUI Desktop folder with `models`, `input`, `user`, `output`, `custom_nodes`). The same folder found twice is listed once.
   With the environment variable `GENMODELFIT_COMFYUI_SEARCH=0`, step 4 is skipped entirely (for embedding apps, scripts and tests; if nothing else decides, "Not decided" below applies).
- **`is_default`**: when the ComfyUI folder's `extra_model_paths.yaml` (and, for ComfyUI Desktop, its `extra_models_config.yaml`) has a section with `is_default: true`, files go there, as ComfyUI-Manager does: `<base_path>/<download_model_base>` when the section has `download_model_base`, else the common parent when each folder key is `<parent>/<key>`. A section laid out differently (e.g. A1111), a missing folder, or a file genmodelfit cannot read (only the plain `name: value` and `|` forms are read, without extra dependencies) is not used, with a warning. A folder the section does not list is warned about (ComfyUI does not read it from there).
- **Not decided** (nothing found, or 2 or more found): in a terminal you choose a number or type the path of a ComfyUI folder. With `--yes`, `--json` or without a terminal nothing is downloaded: exit code 1, with the candidates and how to decide (`--comfyui-dir`, or `genmodelfit config set comfyui-dir`). `--list` downloads nothing, so it still shows the plan, with the place "not decided" and the candidates.
- **Changed in 0.11.0**: genmodelfit no longer downloads silently into its own folder, which ComfyUI does not read. Only `--output-dir` puts files outside ComfyUI.

## Before downloading

In a terminal, the plan ends with the place and how it was found, the files with sizes and licenses, the total and the free disk, then:

```
  to:        D:\ComfyUI_windows_portable\ComfyUI\models  (ComfyUI models folder: <folder>/<file>; found by: the running ComfyUI (127.0.0.1:8188))
  files (3; GB = GiB):
    - diffusion_models/z_image_turbo_int8_convrot.safetensors  5.78 GB
  ...
  total:     11.34 GB; to download 11.34 GB (already there 0.00 GB)
Do you agree to use the files above under their licenses and terms (URLs), and download them?
[y/N/c] (c = change the place)
```

- `c` shows the places found (or asks for a path); after you choose, the plan is shown again and you are asked again. An empty answer keeps the place.
- The first time you download into a place found automatically (step 4, or a path you typed), `y` is followed by "Download here next time too? [Y/n]". `Y` saves it as `comfyui-dir`.
- The consent questions for uncensored models and files with no stated license are the same as before (they are part of the same question).
- Parts the template does not use by default that sit only on muted or bypassed nodes, or on nodes that reach no output (`ModelFile.optional` is `inactive`; 0.12.0, #81), are not downloaded; a notice names them (`skipped_optional` in the plan). To enable those nodes, pass `--with-optional` (Python: `with_optional=True`). Parts behind a switch that is off (`switch_off`) are downloaded, because ComfyUI's validation before running also checks the files on the unselected branch (it stops with "Value not in list" if one is missing). How parts are classed: [sources.md](sources.md) "Optional parts".

## genmodelfit config

```
genmodelfit config                          # current settings, and where each comes from (option, environment variable, config file, default)
genmodelfit config set comfyui-dir D:\ComfyUI
genmodelfit config set lang ja              # default display language (--lang and GENMODELFIT_LANG win)
genmodelfit config unset comfyui-dir
genmodelfit config path                     # where the config file is
```

- All of them take `--json` ([schema.md](schema.md) "config").
- The file is `config.json` in genmodelfit's user config folder (platformdirs: Windows `%LOCALAPPDATA%\genmodelfit`, macOS `~/Library/Application Support/genmodelfit`, Linux `~/.config/genmodelfit`). The environment variable `GENMODELFIT_CONFIG_DIR` changes the folder.
- Only the command line reads it. The Python library never does.
- `set comfyui-dir` saves the folder only if it looks like ComfyUI (4 of 5, or a ComfyUI Desktop folder). Unknown keys are refused. A broken `config.json` is not read (a warning is shown, nothing fails) and is not overwritten.

## Python

```python
import genmodelfit as gmf

plan = gmf.plan_download("Z-Image-Turbo", comfyui_dir=r"C:\ComfyUI")  # no network, writes nothing
for f in plan.files:  # status: download / resume / present / conflict / unavailable
    print(f.relpath, f.size_bytes, f.license, f.license_url, f.status)
print(plan.blocked, plan.warnings, plan.download_bytes, plan.disk_free_bytes)


def ask(plan) -> bool:  # show licenses, URLs, total and free disk; True to go on
    ...


def show(p):  # gmf.DownloadProgress
    print(p.phase, p.name, p.done_bytes, p.total_bytes)


result = gmf.download(plan, confirm=ask, progress=show)
# or gmf.download("Z-Image-Turbo", yes=True, comfyui_dir=...) when the app already asked
```

- Without `comfyui_dir` / `output_dir` the library still uses genmodelfit's data folder, which ComfyUI does not read; pass `comfyui_dir`.
- The library prints nothing.
- `gmf.download` raises `gmf.DownloadRefused` (blocked, no consent) and `gmf.DownloadError` (network, sha256 mismatch, disk).
- It lets `KeyboardInterrupt` through and keeps the `.part` file for the next call.
