# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).
変更の記録。書き方は Keep a Changelog にならう。

## [0.14.0] - Unreleased

### Added

- `share` and `measure` offer to delete what they downloaded to measure (#145): at the end (also after a failure or
  Ctrl+C) they list the files this run downloaded from the start (finished files and leftover `.part`), with the
  total size, and ask `[y/N]` (Enter keeps them). Files that were already there are never listed or deleted, nor is
  a `.part` from an earlier run or the file finished from it; a file several of the run's models use is listed
  once; only files inside the destination folder that are still the same file (device, inode, size, modification
  time checked again right before deleting) are deleted, so another run's files are not; folders this run made are
  removed when they end up empty. `--yes` or no terminal keeps them; the new flag `--delete-downloaded` deletes them
  without asking. An error is printed before the question. The library records what a download put in place with
  `download(..., record=DownloadRecord())` (nothing is printed).
  (測るために落としたものを、終わったあとで消すかを尋ねる。前から入っていたものは消さない)

### Changed

- `share` says why when there is nothing to send, and what to do next (#146): already measured on this PC (with the
  count), nodes missing in this ComfyUI (update ComfyUI), not-installed models declined or not downloaded
  (`share --download`), nothing fits `--budget`, the models folder unknown (`--comfyui-dir`), runs that failed or
  cannot be sent, Ctrl+C, failure records already sent, and no unsent measurements (`share --no-measure --all` to send
  the sent ones again). `--json` adds `reasons` (`{code, text}`) and `next_step` next to `reason`, also after measuring.
  (「送るものはありません」に理由と次にできることを出す)

## [0.13.2] - 2026-10-29

### Changed

- **README rewritten** (EN and JA, same order): quick start and install, what it does, usage (6 steps), reading the
  results (example, fit levels, columns, hidden models, accuracy), help by measuring, privacy, apps, reference,
  license. Details moved to docs: the display language, "If `genmodelfit` is not found" and every table column to
  docs/commands, the longer Python example to docs/schema "Python API (stable)"; non-NVIDIA GPUs, scoring, download and `share` steps link to docs/hardware,
  docs/scoring, docs/download and CONTRIBUTING, which already held them. The Japanese text uses shorter sentences and
  consistent terms; the heading "精度" is now "見積もりの確かさ" (it clashed with the `Prec` column).
  (README を作り直した。英日とも同じ並びにし、細かい話は docs に移した)
- README images: the top GIF re-recorded with 0.13.1 (`tools/demo_gif.py`; simulated GPU and memory, 1.6 / 1.8 MB
  instead of 8.8 MB), and two diagrams, fit levels and the `share` flow (`tools/diagrams.py`; the 60 / 85 / 98% bands
  and the caps per run mode are taken from the code). PNGs and GIFs in docs/images, EN and JA.
- One list of what `share` sends and never sends in README "Privacy" (it was in two places that differed): adds the
  compute capability, the PCI model ID for AMD / Intel (not the slot), the model-file fingerprint and the 0.13.0
  additions.

### Fixed

- README and `genmodelfit --help`: `python -m genmodelfit` works with the Python that pip installed into; it does not
  reach pipx or uv tool installs (run `pipx ensurepath` / `uv tool update-shell` and open a new terminal).
- README: `share` does not by itself make a GPU "verified"; the ✓ needs runs on two or more GPU models of that kind.
  "Other measurements" now says which: runs without ComfyUI's dynamic VRAM (AMD, Intel, Apple, or NVIDIA with it
  turned off). The Verified line is quoted as `info` prints it. `config show` is listed; `measure` suggests only models
  with a known license; narrow terminals drop `Disk`, then `Mem%`; `uvx genmodelfit@latest` explains why (uvx may
  keep an older version). "Ctrl-C" is written "Ctrl+C" everywhere.
- `--help`: `contrib` and `bench --submission` write `contrib_schema` 3 (not 2); `share --dry-run` makes no network
  access to GitHub but asks 127.0.0.1 whether ComfyUI runs (it said "no network").

## [0.13.1] - 2026-10-29

### Fixed

- **`measure` and `share` measure on AMD, Intel and Apple GPUs again** (0.13.0 wrongly stopped them with "For now
  only NVIDIA GPUs are measured"; measurements from these GPUs are the ones most needed, since none has a ✓ yet).
  Only "no usable GPU" stops now. The 0.13.0 checks degrade on these GPUs instead of refusing: the nvidia-smi check
  before starting ComfyUI is skipped; after freeing, other programs' VRAM is checked from ComfyUI's `/system_stats`
  on AMD (ROCm), and on Intel (XPU) and Apple (MPS), where `/system_stats` cannot show it, the measurement's `note`
  says it was not checked; the watcher for new GPU processes during a run (nvidia-smi only) is not used and the
  `note` says so. The busy check before measuring stays bench's per-vendor one (0.9.0). Starting ComfyUI is the same
  for every vendor (the portable `run_amd_gpu.bat` / `run_intel_gpu.bat` pass the same arguments as
  `run_nvidia_gpu.bat`).
  (measure/share が NVIDIA 以外の GPU(AMD・Intel・Apple)でまた測れるようにした(0.13.0 で誤って止めていた))

### Changed

- README: a short section says that models with an unknown license and uncensored models are hidden by default, and
  that `--any-license` / `--uncensored` show them (README「ライセンス不明・無検閲のモデルは、既定で出しません」).
- **Docs: README, CONTRIBUTING, docs and the `--help` texts match the 0.13.0 behaviour** (documentation only; no
  change in what the commands do). `measure` / `share` now say: "not yet measured on this PC" (other people's
  measurements of the same GPU do not remove suggestions; two agreeing sources let measurements set the level);
  ComfyUI is found and started if needed (portable / venv / comfy-cli / Desktop), freed before each run, stopped
  afterwards (also on Ctrl+C or when the terminal is closed), and checked for missing nodes; only one run measures at
  a time (`measure.lock`); the device-login keys (`r` / `n` / `q`) and the expiry retry; the progress display; the
  0.13.0 additions to what is sent (estimate, time breakdown, spill mark, list version, failure records) and what is
  never sent (error text, interrupted or disturbed runs, missing model files); the network used (an existing token is
  checked with api.github.com before measuring; `--dry-run` only asks 127.0.0.1). Exit codes of `measure` / `share`
  (declining a download, results that only cannot be sent, Ctrl+C 130), the cache contents in docs/commands, the
  issue form texts, docs/README, docs/release and docs/accuracy (sources counted by GitHub account) were corrected.

## [0.13.0] - 2026-10-29

### Changed

- **`share` measures and sends a GitHub pull request** (#124; after llmfit's `bench --share`). Without arguments it
  finds ComfyUI like `measure`, picks the installed suggestions of `measure` until their rough times add up to 300 s
  (`--budget`), shows the plan with the unsent measurements and asks once, checks a GitHub token you already have,
  measures one by one, shows every file with the privacy checks and asks how to send it. For a pull request it signs
  in (`GITHUB_TOKEN` / `GH_TOKEN`, else the saved sign-in, else GitHub's device login with scope `public_repo`; the
  token is saved with its expiry in `<config>/github_token`, readable only by you, and never printed; `--logout`
  removes it), forks the project, makes a branch from the merge base of the project's `main` and your fork's `main`
  (your fork's `main` is not changed), puts the files under `data/contrib/<GPU>/` and opens a pull request titled
  `measurements: <GPU> (<count>)`, or adds them to your open `share` pull request for that GPU. Files already there are
  skipped; a retry after a failed attempt still opens the pull request, and files are marked as sent only when a pull
  request has them or they are already on `main`. Models that are not installed are downloaded only with `--download`
  or `y` to one `[y/N]` question (asked first when nothing installed can be measured, else after measuring), with
  `download`'s questions and consent rules; `--yes` alone never downloads. New options: `--yes`, `--download`,
  `--any-license`, `--no-measure`, `--budget`, `--dry-run` (no network to GitHub), `--issue`, `--logout`, `--url`,
  `--comfyui-dir`, `--timeout`, `--no-start`, `--start-timeout`, `--no-cache`. The issue form is kept as
  `share --issue` (no sign-in); `contrib share` and `bench --share` keep using it, and `share --json` without `--yes`
  still lists the unsent measurements (`share FILE --json` still previews) for apps. New module `github.py`.
  The plan shows the total time (starting ComfyUI about 45 s, measuring, and downloading as an example at 100 Mbps).
  After showing the files it asks how to send them: 1) pull request, 2) issues (no sign-in; one form per file),
  q) not now; `--issue --yes` measures and sends issues. Before measuring only an existing token is checked; the device
  login happens when a pull request is chosen. Results are saved before sending and never measured again: models
  with a result saved on this PC (sent or not) are not planned, and after q or a failed pull request (a terminal then
  offers issues) `share --no-measure` sends them.

### Added

- **`measure` and `share` start ComfyUI when none is running** (#124; `comfystart.py`): the install found like
  `download` is started as a separate process on 127.0.0.1 and a free port without a browser (portable: its
  `python_embeded`; manual or comfy-cli: its `.venv` / `venv`, never a guessed Python; ComfyUI Desktop: its `.venv`
  and the app's ComfyUI, only when both are found), waited for with `/system_stats` (`--start-timeout`, default 300 s),
  and stopped afterwards, also on a failure or Ctrl+C; a ComfyUI that was already running is never touched. Its output
  goes to `<cache>/comfyui-<date>.log`. `--no-start` or `GENMODELFIT_COMFYUI_START=0` turns it off.
- `measure` prints its screen before the progress of a started ComfyUI also when the output is redirected.
- **Found by testing 50 situations with a real GPU** (#124):
  - A result that cannot be sent (for example peak VRAM above the GPU's total because Windows spilled into shared
    memory) is kept as this PC's measurement but no longer saved as an unsent submission, so `share` does not fail
    on it every time; such files already in the cache are listed once and renamed `*.unsendable`.
  - A running ComfyUI 0.19 was not found: `/internal/folder_paths` returns lists of paths; both shapes are read now
    (this also fixes `download`'s automatic destination).
  - Other programs using VRAM: before starting its own ComfyUI, and after freeing a running one, `share` / `measure`
    refuse to measure when more than 1.5 GiB (or 20% of the GPU) is used by others; a program that starts using the
    GPU during a run makes the result non-standard (not sent).
  - Ctrl+C during a download in `share` stops downloading and measuring (the `.part` files stay) and goes on to
    sending; Ctrl+C while signing in or sending says what was placed and how to resume (`share --no-measure`), exit 130.
  - A ComfyUI that stops during a run is reported with its log; one started by genmodelfit is restarted once.
  - After the share-level "download them?" question (now with the licenses), `download` does not ask again per model.
  - Suggestions leave out models with an unknown license (as the lists do; `--any-license` includes them), and only
    NVIDIA GPUs are measured for now (other GPUs can still send saved results).
  - Clearer reasons when there is nothing to do (`--budget` too small, nothing unsent, everything measured), for
    `--no-start`, timeouts and missing nodes; flag mistakes exit 2; declining the download in `measure` exits 0;
    stdout is flushed before every stderr line (pipes keep the order); each file being sent is shown.
- **Second round of testing** (#124): log lines shown from ComfyUI lose control characters everywhere; anything
  answering on 8188 / 8000 (also 401 / 403) counts as a running ComfyUI (no second one); a run whose only problem is
  results that cannot be sent exits 0, and Ctrl+C exits 130; an unusable cache folder is a clear error; bench's
  reasons follow the display language; next steps are specific (template fetch, rejected workflow, save failure,
  crash) and `--workflow` is not suggested in `measure` / `share`; on shared PCs only a new compute-only process (nvidia-smi
  type C; not browsers or other C+G / G apps) counts as interference; an expired and a broken saved sign-in are
  told apart.
- **More error cases** (#124): a started ComfyUI is stopped also when the terminal is closed or genmodelfit is
  killed (Windows Job Object; Linux parent-death signal; POSIX hang-up); only one measuring run at a time
  (`measure.lock`); no second ComfyUI when one already answers; folders reported by a running server are checked
  before downloading into them; a sign-in-protected ComfyUI, user jobs in its queue, a sleeping PC, a cache that
  cannot be written, unreachable templates, an organization that restricts OAuth apps (use `--issue`) and odd
  nvidia-smi output are explained; several GPUs follow `CUDA_VISIBLE_DEVICES`; log lines lose control characters
  and the live display does not crash on cp932 or 40-column terminals and stays plain with `TERM=dumb`.
- **Progress you can follow** (#124): `share` numbers its steps ("== 手順 2/6: ComfyUI を起動 =="); in a terminal
  the ComfyUI start, each measurement (elapsed against the guide, queued / running / waiting, peak VRAM so far, a
  note when it takes much longer than the guide) and downloads (one bar per file and one for everything, with size,
  speed, time left and a "verifying" phase) are drawn with `rich.progress` on stderr; without a terminal plain lines
  about every 10 s (downloads: when a file starts and every 10%); nothing with `--json`. `measure` uses the same
  displays. bench and comfystart report through callbacks; the libraries still print nothing.
- **Each run starts clean** (#124): `share` frees ComfyUI (`/free`: unloads models and drops cached node results)
  before every run, saying so in the plan when the ComfyUI was already running; `measure` does it for a ComfyUI it
  started, and for yours with the new `--free`. A result measured under non-standard conditions (for example reused
  cached nodes) is listed under "not measured" with the reason instead of disappearing.
- **Missing nodes are checked before measuring** (#124): once ComfyUI is reachable, `share` leaves out suggestions
  whose template uses nodes that ComfyUI lacks ("update ComfyUI to measure them") and fills the time with the next
  ones; `measure` hides them from its list and stops a named model before downloading or measuring. The "Next:" line
  for a missing-node error now says to update ComfyUI (or install the custom nodes) instead of checking that ComfyUI
  runs.
- **`share` sends more of what helps improve the estimates** (#132; `contrib_schema` 3, docs/schema.md "contrib").
  Each measurement also carries `estimate` (genmodelfit's level, run mode, VRAM, working memory, RAM and time guide
  for it, made without that measurement or other measurements from the same PC), `timing` (queue, load, sampling,
  seconds per step, decode, total, first load; read over HTTP from `/history`'s `status.messages` and ComfyUI's log
  progress lines, null when unknown) and `db` (the list's date and the start of its sha256). A result whose peak VRAM
  went over the GPU total (Windows shared memory) is no longer refused: it is sent as `outcome: ok` with
  `spilled: true` and `spilled_gb`. What could not be measured goes into one failure record file per run and GPU
  (`data/contrib/<GPU>/<date>-<sha8>-failures.json`, kind `genmodelfit-failures`): missing nodes (also those left
  out by the node check before measuring), timeouts after the job started, errors and unsendable OOMs, with node
  names, the exception type name or ComfyUI's `node_errors` type and the failing node type only (never the message
  text; interrupted runs and runs disturbed by other programs are never sent). `measure` also saves a failure record when it cannot measure (the next `share` sends it), and `bench --json`
  has `timing`. A run with only failures still sends
  them; a record already saved on this PC is not written again. The screen before sending shows the new fields
  (English and Japanese), and the privacy checklist covers them. `contrib validate`, `contrib intake` (issues) and the
  contrib / measurement-intake workflows accept schema 3 and failure files (schema 1 and 2 are still accepted);
  spilled results and failure records are kept out of `contrib aggregate`, the estimates and `calibrate`. The
  sampler time uses only progress lines whose step count matches the workflow's sampler (others, such as model
  downloads, are ignored), and a timing or estimate that fails the checks is dropped instead of blocking the
  measurement; the estimate also leaves out bundled measurements of the same model on the same GPU. Node names that
  do not fit the safe form are only counted (`other_nodes`), a bad record is left out alone, and
  `value_not_in_list` (a model file missing on this PC) is not a failure record.

### Fixed

- **The device login of `share` no longer waits silently until the code expires** (reported by the owner). While
  waiting, a live line shows the code and the time left (plain lines every 30 s without a terminal), with the
  hint that a mistyped code can be entered again on the same page (valid for 15 minutes). Keys, read without
  waiting (Windows `msvcrt`; POSIX `select` with the terminal in cbreak mode, always restored): `r` opens the
  page again, `n` gets a new code, `q` stops signing in and sends the same files as issues, Ctrl+C stops. An
  expired or denied code asks whether to sign in again (no: issues). Measurements are never lost.
  `github.device_flow` / `login` take an `on_wait` hook (the library still prints nothing).
- **Other people's measurements no longer remove suggestions** from `measure` and `share`: "measured here" now
  means this PC's own results only (its cached bench results and saved submissions). The bundled author
  measurements and the aggregated community rows still feed the time hints and the estimates
  (`measure.measured_by_this_pc`). The tests no longer depend on the bundled community data.
- The bundled `measurements_community.json` is aggregated from `data/contrib` (#126: 4 submissions from 1
  source, the maintainer; RTX 3070, SDXL at four settings; none consistent yet, so the calibration is
  unchanged). README and docs/accuracy (EN/JA) give the new counts.
- `measure` without a model no longer asks for a number when no running ComfyUI was found; it shows the list, then
  how to start ComfyUI, and exits with 1 (#123). Before, it stopped only after a model was chosen.

## [0.12.0] - 2026-10-28

### Added

- **Public API for embedding apps** (#96; docs/schema.md "Python API (stable)"), so that an app no longer calls
  internal functions:
  - `gmf.system_from(..., windows_build=None, cpu=None)`: GPUs with `vendor` `amd` / `intel` / `apple` go through the
    same support tables as detection (`hardware.classify_adapter`); the reason for each is in `other_gpus[].reason`
    (`usable`, `integrated`, `needs`). `vram_gb` may be omitted for them; an optional `pci_id` resolves generic names.
    `gmf.judge_model` takes `gpu_vendor`, `windows_build` and `cpu` and passes them on.
  - `gmf.gpu_family(name, vendor, cpu="")`: the AMD gfx / Intel XPU name tables (`backend`, `arch`, `family`,
    `integrated`, `vram_floor_gb`), or `None` when the name is not in them.
  - `gmf.load_db(..., user_measurements=True, source=None)`: `user_measurements=False` reads only the bundled
    measurements (not this PC's `bench` results); `source="release"` marks a list file the app imported itself.
  - `ModelEntry.license_unknown` / `ModelEntry.is_tool`: the per-entry checks of the default filters.
  - `Database.generated_time` / `DataStatus.generated_time` (`generated_at` as a UTC `datetime`), and
    `gmf.self_check()` (bundled list, measurements, coefficients, `cryptography` and public keys present; never raises).
  - `gmf.parse_safetensors_header`, `gmf.HeaderError`, `gmf.HeaderInfo`.
- **`measure` also suggests runs at other settings** (#116), to check the formula's per-stage working memory without
  a long batch (the #102 plan is now `measure` → `share` → "Create"): text-to-image at 512 / 768 / 1024 / 1536 px,
  text / image-to-video at 17 / 33 / 49 / 81 frames (size as in the template), text-to-audio music / sfx at 10 / 30 /
  60 / 120 s. A run is listed only when its estimate at that setting is good or better on the GPU and this GPU has no
  measurement at that setting (same GPU, same files, same settings); it is mixed into the same list and order
  (installed → time → download), at most 2 per model (`measure.VARIANTS_PER_MODEL`), shown with the arguments
  (`[image_sdxl_simple --resolution 1536x1536]`). The rough time is scaled by the ratio of pixels, frames or length.
  `measure <name>` takes `--resolution WxH`, `--frames N` and `--length S` like `bench`; the screen before measuring
  and the JSON show the settings (`suggestions[].settings` / `args`, `settings`). Each run is still one generation;
  `share` sends the settings as before. New: `measure.variants`, `bench_args`, `settings_dict`, `same_variant`,
  `work_ratio`, `template_settings`, `judged_settings`, `bench.parse_resolution`; `has_measurement_here` and
  `time_hint` take optional `settings`.
  - Runs at other settings are offered only after this GPU has a measurement of the workflow at the template's
    settings, so that run is collected first; the template's own value (e.g. 1024 px) is not offered again.
  - New optional `Measurement.settings_override` (also `measurement.settings_override` in submissions, written only
    when true; `contrib_schema` unchanged): bench / measure set it when the template's settings were changed. Only
    rows without it count as "measured at the template's settings"; `contrib aggregate` keeps them in their own rows.
  - With settings, measure stops before generating when the template cannot take them (`BenchOptions.strict_settings`;
    e.g. frames on an image template or AnimateDiff), and warns if the recorded settings still differ. Variants are
    not offered for an item that a measurement at the template's settings shows the template has no input for.
  - Video runs are judged at the template's size from that measurement (else 832x480). Rows over about 300 s are not
    listed, and video runs whose time is only the per-type rough value go up to 33 frames.

### Changed

- **Parts a template does not load by default are not counted in VRAM / RAM** (#81, #112): `update` follows each official
  template's links the way ComfyUI v0.43.0 runs it (from the output nodes, every subgraph level; If/Else Switch with a
  fixed value only on the selected branch, Soft Switch falls back when that side is unconnected; muted / bypassed nodes
  and the contents of a muted / bypassed top-level subgraph left out) and marks files only reached otherwise:
  `ModelFile.optional` = `inactive` (muted, bypassed or reaching no output: counted nowhere and not downloaded unless
  `download --with-optional` / `with_optional=True`) or `switch_off` (unselected branch: not in VRAM / RAM, but in
  `disk_gb` and downloaded, because ComfyUI validates it), plus `optional_replaces`. New optional keys, so
  `schema_version` stays 1. `Estimate` gains `optional_parts`, `vram_gb_all_options` and `fit_all_options` (the old
  all-in value), with one sentence in the reasons only when turning the switches on would change the level; `info`
  marks optional files; `DownloadPlan` gains `skipped_optional`. Qwen-Image 2.1 (4 entries) on 8 GB: offload 8.48 GB
  too_tight → 4.96 GB (t2i) / 5.14 GB (edit) Good. Wan 2.2 5B on 8 GB was already Good (6.08 GB) from #100 and is now
  kept by a test. Across the bundled list × NVIDIA 6–24 GB × RAM 32 / 64 GB, 90 cells of 34 models change, all upwards.
  The bundled list was re-marked offline from the template JSON (Comfy-Org/workflow_templates 0cc0fba): 66 templates have
  optional parts. Language material now uses only parts loaded by default; Qwen-Image 2.1 gets `prompt: en` from the
  hand table, and `image_qwen_image_2_1_image_edit` also `ja` (confirmed on a real machine). Docs: scoring "Parts
  switched off by default", accuracy "How files are counted", sources "Optional parts".
- **`gmf.system_from` judges AMD / Intel / Apple GPUs** (#96): before, a GPU dict with a non-NVIDIA `vendor` was always
  treated as CPU; now it is classified like a detected adapter and used when no NVIDIA GPU is usable (pass
  `gpu_usable=False` for the old CPU-only result). `vendor` is compared without case (`"NVIDIA"` is NVIDIA).
- **Release notes come from this file**: release.yml takes the tag's section of `CHANGELOG.md` as the GitHub Release
  body (`tools/changelog_notes.py`, instead of `--generate-notes`) and stops before publishing when that section is
  missing, empty or still `Unreleased`. docs/release.md "Releasing a version" replaces the first-release steps.
- Documentation audit: private setup details (paths, key backup location, the maintainer's PC) removed from the docs
  (a test keeps them out), stale levels and versions corrected (8 GB Z-Image Turbo int8 and Wan 2.1 1.3B are Good
  offloaded since 0.11.0), `measure` / `share` added to the JSON envelope, the Japanese "Details" lines of `review` /
  `contrib` restored in docs/commands, a Japanese contact link on the issue page, and `Operating System :: MacOS`.

## [0.11.0] - 2026-10-28

### Added

- **`genmodelfit config`** (#111): `config` (show each setting and where it comes from), `config set comfyui-dir
  <folder>` / `set lang ja`, `config unset <key>`, `config path`, all with `--json`. The file is `config.json` in the
  platformdirs user config folder (`GENMODELFIT_CONFIG_DIR` changes it). Only the command line reads it; the Python
  library never does. `set comfyui-dir` checks that the folder looks like ComfyUI; unknown keys are refused; a broken
  file is ignored with a warning and never overwritten. `lang` is the default display language (`--lang` and
  `GENMODELFIT_LANG` win).
- **`download` finds your ComfyUI** (#111): after `--comfyui-dir` / `--output-dir`, `GENMODELFIT_COMFYUI_DIR` and the
  saved `comfyui-dir`, it looks at the current folder (4 of `main.py`, `comfy`, `nodes.py`, `comfy_extras`,
  `comfy_api`, as comfy-cli does), a ComfyUI running on this PC (`GET /internal/folder_paths` on 127.0.0.1 ports 8188
  and 8000, 2 s; only when downloading, never with `--list`), the ComfyUI Desktop settings (`basePath`) and comfy-cli
  settings / default workspace, and usual places one level deep (fixed drives, Documents, Desktop, Downloads, home;
  also `ComfyUI_windows_portable\ComfyUI`). The plan always says how the place was decided (JSON `destination_from`,
  plus `comfyui_dir`, `extra_model_paths`, `destination_candidates`). An `is_default` section in
  `extra_model_paths.yaml` is honored (read without new dependencies). The question is `[y/N/c]` (`c` changes the
  place), and the first download into a place found automatically asks "Download here next time too? [Y/n]".

- **Measure and send with two commands and one button: `genmodelfit measure` and `genmodelfit share`** (#113).
  `measure` without a model lists models worth measuring: from an official template, `perfect` / `good` on this
  PC's GPU, and no measurement on this GPU yet (one per model; installed ones first, then the shorter time, then the
  smaller download); choose by number. `measure <name or id>` resolves like `download` (template workflows only).
  Before measuring one screen shows the model and precision, the settings (as in the template), which ComfyUI and how
  it was found (`--url`, else a ComfyUI running on 127.0.0.1 ports 8188 / 8000), whether the files are installed (by
  size in the models folders), a rough time (the median of measurements of the same model on other GPUs, else a value
  per type) and that nothing is sent, then asks `[Y/n]` (`--yes`; `--json` needs `--yes`). A missing model is
  downloaded first through `download` (same questions and consent rules); without a running ComfyUI it says how to
  start one. Afterwards it prints the result, where it saved it and one line pointing to `genmodelfit share` (it never
  asks whether to send). `share` lists the unsent measurements in `<cache>/submissions/` (or takes a file), shows
  exactly what will be sent with the privacy checks, and opens the GitHub **issue form** for measurements with the
  content filled in; you press "Create". When the content does not fit in a URL (8000 characters) it is copied to the
  clipboard with the OS tool (clip / pbcopy / wl-copy / xclip / xsel) and the empty form is opened. A shared
  measurement is marked in `<cache>/submissions_shared.json` and not listed again (`--all`). `--print-url` and
  `--json` never open a browser. genmodelfit itself still sends nothing.
- **Measurement issues become pull requests** (#113): `.github/ISSUE_TEMPLATE/measurement.yml` and
  `.github/workflows/measurement-intake.yml`. The workflow runs `genmodelfit contrib intake` on issues with the
  `measurement` label: the body is read from the event file as data only, checked like `contrib validate` plus the
  privacy checks, and on success the bot pushes `data/contrib/<GPU>/<date>-<sha8>.json` to `measurement/issue-<N>`,
  opens a PR (with the issue author and `Closes #N`) and links it; on failure it comments the reasons (English and
  Japanese). It counts as the issue author's submission. Library: `contrib.intake_issue`,
  `contrib.extract_issue_json`.
- **Sharing a measurement without git: `bench … --share` and `contrib share <file>`** (#90; the page changed in
  #113). They run the same checks as `contrib show` and `contrib validate` (all fields, the privacy checklist, the
  mechanical checks) and refuse to share when a privacy check is NG or the checks have errors (warnings and notices do
  not stop it). They now do what `genmodelfit share` does (the issue form; GitHub's "new file" page and its
  "Propose changes" / "Create pull request" steps are no longer used; `share_urls` returns `issue_url` / `form_url`
  instead of `new_file_url` / `paste_url`). `bench --share` without `--submission` writes the submission to
  `<cache>/submissions/<GPU>/<date>-<sha8>.json`. CONTRIBUTING now puts `measure` → `share` → "Create" first; the
  git / fork path stays as the alternative.
- **DGX Spark (GB10) is judged as a unified-memory GPU** (#101). Only when nvidia-smi names it exactly `NVIDIA GB10`,
  reports no VRAM (`[N/A]`) and runs on Linux aarch64: the total is MemTotal (not discounted, not counted twice with
  RAM; `memory: shared`), current free is MemAvailable + SwapFree (or free HugePages), and the level follows the
  same rules as other GPUs (no cap since #108). cu128 for aarch64 was added to the PyTorch table. Jetson and
  GH200 / GB200 stay out of scope (with a reason).
- **AMD and Intel GPUs in WSL2** (#101). With `/dev/dxg` and a `microsoft` kernel, interop and container are checked
  without starting a process, the Windows-side GPUs are read through `powershell.exe` (WMI + registry, as on
  Windows), and WSL readiness through `rocminfo` (only `Name: gfx…`, `Marketing Name:`, `Device Type:` are read; the
  UUID never appears anywhere) or `clinfo -l`. Used only for the products in AMD's WSL table (ROCm 7.2.1) and Intel
  Arc / Core Ultra Arc Graphics with driver 32.0.101.8801 or later; otherwise CPU with what is missing.
- New `Adapter` fields `driver`, `gfx_target`, `wsl_runtime`, `config_error`; `Adapter.source` can be `sysfs` or `wsl` (#101).
- **GPU registration tables with sources, and an inventory page** (#103). The hand-written GPU tables (AMD name →
  gfx, CPU name → iGPU gfx, Intel XPU names, PCI device IDs, NVIDIA name → compute capability for old drivers,
  PyTorch CUDA builds) moved to `gputables.py`; every row is a `Rule` with its source, the date it was checked, the
  version it was added in and a note. `tools/gpu_inventory.py` generates `docs/gpu-inventory.md` / `.ja.md` (every
  row with the official product names it matches, the verdict and the source); a test pins the pages to the
  generator. The official lists (pci.ids, ROCm hw specs and compatibility tables, TheRock, PyTorch's Intel GPU table,
  NVIDIA's CUDA GPU lists, PyTorch's release tables and wheel index, the CUDA release notes, ComfyUI's
  `model_management.py`; fetched 2026-10-27) are fixed under `tests/fixtures/gpu_official`, and a test checks that
  every official product matches a row with the same gfx / compute capability (no network).
- **GPU table watch in the weekly update** (#103; `gpuwatch.py`). `python -m genmodelfit.weekly` reads the same
  official lists (with `fetch`'s session, by header words, with time limits) and adds a "GPU 表の見張り" section to the
  PR body and `gpu_watch.json`: candidates "not in tables" / "differs" and the sources that could not be read. It never
  rewrites the tables, never raises, and does not affect `ok` or the safe automatic merge. `--no-gpu-watch` skips it;
  `python -m genmodelfit.gpuwatch` runs it by hand.

### Changed

- **One GitHub account is one source, one pull request at most one** (#113): other users' submissions are counted as
  groups connected by the same pull request (its merge commit; measurement pull requests are merged with a merge
  commit, never squash or rebase) or the same account. For a measurement issue the person is the issue author's
  numeric account id in the intake bot's `Submitted-by-id:` line (unchanged by renames; older bot commits with only
  `Submitted-by: github:<account>` count by name), trusted only when the merge commit is `Merge pull request #N from laxshmate/measurement/issue-M` (the bot's
  own branch; a fork's branch of the same name is not trusted), else the pull request's source account from the merge commit's
  first line `Merge pull request #N from <account>/<branch>` (commit e-mail addresses are never used, since anyone
  can write them; a fork's fake bot commits count as the fork's owner). Only a commit that is not such a merge
  (directly on main, squash, rebase) falls back to its author's e-mail address. `source_ids` hold the first 12 hex digits of the sha256 of the group's smallest key, so
  no address or account is published. Another account, an organization the person owns, or a pull request after a
  rename counts as someone else; the bot's pull request has a checklist item for the maintainer to check the author.
  New: `contrib.submission_origins`, `contrib.submission_submitters`, `contrib.submitter_count`.
- **`share --json` only previews** (#113): it marks the measurement as sent only with `--mark-sent`; the text output
  without a terminal does not mark it either (opening the browser and `--print-url` do). `share --print-url` without a
  file uses the only unsent measurement. `measure` answered with n exits 0, and a failed measurement ends with a plain
  "Next:" line. The line after measuring says honestly what the measurement is used for (working-memory-only NVIDIA
  results are for cross-checking; out-of-memory results stop measured levels for that setup; every measurement
  takes effect after a maintainer accepts it and the next version is released).
- **Measurement intake is idempotent and follows edits** (#113): it runs on issues opened and on body edits only (not
  on labels or title edits), does nothing when the PR already has the same content, refuses content that another
  issue's PR already has, updates the PR and comments on both pages when the issue is edited to different valid
  content, and closes the PR when the issue is edited to invalid content (reopened when fixed). It is split into a
  read-only job that installs genmodelfit and a write job that only uses git and gh. The privacy check no longer
  treats the runner's host / user name as the sender's.
  It only looks at the bot's own pull requests (not a fork's with the same branch name), reopens only pull requests
  it closed itself (label `measurement-invalid`), closes the issue's pull request when an edit duplicates another
  issue's, and comments the same refusal reasons only once.
- **`contrib` is for maintainers** (#113): the README command table and `--help` list `measure` and `share` for
  users; `review`, `bench` and `contrib` are described as maintenance commands. The commands stay as they were
  (`contrib share` is the same as `share`).

- **`download` no longer falls back to genmodelfit's own folder** (#111), which ComfyUI does not read. When no
  ComfyUI is found or several are, a terminal lets you choose a candidate or type a path; with `--yes`, `--json` or
  without a terminal nothing is downloaded (exit 1, with the candidates and how to decide; JSON `plan: null`).
  `--list` still shows the plan with the place "not decided" (`plan.root` and `files[].dest` are `""`). Only
  `--output-dir` puts files outside ComfyUI. The Python API (`plan_download` / `download`) is unchanged.

The meaning of the output changed (#95), so this is 0.11.0. `SCHEMA_VERSION` (the shape of db.json) is unchanged.
The rules, their one-sentence explanations, evidence and tests are in the new docs/scoring.md (English and Japanese).

- **All-on-GPU is chosen only when everything can stay loaded through the VAE decode** (#102). A new
  `vram_resident_gb` = fixed + weights of all parts + max(S, V, T) with the untiled VAE working memory V (ComfyUI frees V before
  decoding and unloads other models if it does not fit; sd.py:1278-1280@426f5ee). Above 98% the row becomes GPU~ (up to Good) or
  offload. On 21 runs on an RTX 5080 16GB measured by another user this predicted whether ComfyUI unloaded part of the models in 20
  (12 before). The amounts used (`vram_gb` and the bands) and calibration are unchanged. Bundled list × NVIDIA 8–32 GB: 16 of 1,625
  rows switch from GPU to GPU~, 2 of them drop Perfect → Good (Kandinsky 5 at 32 GB).
- docs/accuracy: the #102 measurements (RTX 5080 16GB, both ComfyUI modes) in "Cross-check"; CONTRIBUTING: what to do when
  ComfyUI stops responding with dynamic VRAM.

- **Level caps reviewed** (#108; docs/scoring.md "Level caps", "Verified GPU kinds"):
  - **Offloading (GPU+RAM) goes up to Good** (was Marginal; as in llmfit). Offloading is chosen only when it raises the
    level (ties stay on the GPU), so a GPU Perfect or Good row never switches to offloading; GPU / GPU~ rows at 85–98%
    switch when offloading makes them Good, and the reason says "On the GPU alone it is tight at 9x%, so part of the
    weights is offloaded". (llmfit offloads only above 98%; that would break "more VRAM never lowers the level" for 136
    bundled models.) The level of an offloaded row uses the tighter of the offloaded VRAM use and RAM ÷ system RAM
    (the same as Mem%). Non-NVIDIA GPUs get a note that ComfyUI's Dynamic VRAM (NVIDIA on Windows / Linux only) is
    missing, so offloading is slower still. The limits (`limits`) count only rows that run on the GPU without
    offloading. Rows that switched from GPU / GPU~ Marginal to GPU+RAM Good: 8 at 6 GB, 6 at 8 GB, 18 at 12 GB, 19 at
    16 GB, 49 at 24 GB (bundled list); Marginal → Good overall: 49 / 71 / 68 / 43 / 108 rows.
  - **CPU only**: one-pass tools (depth, pose, segmentation, background removal, audio separation) and speech built to
    run in real time on a CPU (`estimate.CPU_REALTIME_TTS`: MeloTTS, Piper, VieNeu-TTS, each with the authors'
    statement; Kokoro and HiFi-GAN stay Marginal) go up to Good by the band of RAM use; image, video, music, sound effect and 3D generation,
    speech made with a language model, flow matching or diffusion (Fun-CosyVoice3, Chatterbox, VibeVoice, Qwen3-TTS)
    and upscaling stay at most Marginal (diffusion runs dozens of steps; HF's Intel CPU blog: SD 1.5, 20 steps, 32.3 s
    on r7iz.metal-16xl in fp32). 11 rows Marginal → Good on a CPU-only PC. The limits (`limits`) are not given on a
    PC without a GPU, and their text says "Good on the GPU without offloading". When the GPU judgement is worse than the CPU one, the CPU one is used (adding VRAM never lowers the
    level).
  - **No cap by GPU vendor**: AMD / Intel / Apple GPUs and DGX Spark are judged by the same rules as NVIDIA (Good →
    Perfect for 65 rows on AMD 16 GB, 87 on Apple 32 GB, 317 on DGX Spark; the AMD 16 GB distribution is close to
    NVIDIA 16 GB). Measurements only raise the level (the #95 branch that lowered Perfect to Good from measurements on
    non-NVIDIA GPUs was removed).
  - **"✓ verified GPU kind"**: the PC line of the tables (`recommend` / `fit` / `search`), `info` and `system` show it
    when the bundled measurements have records of running on two or more GPU models of the same kind (vendor ×
    dedicated / shared memory; author and community measurements alike); nothing otherwise. Today only NVIDIA with
    dedicated VRAM (RTX 5080, RTX 3070). JSON: new `system.platform_verified`
    and `system.platform_verified_by`; the library: new `gmf.platform_verification(system)`. Not used for the gate
    (`auto`), which stays independent of the PC.
  - **Refusals in three groups**: used without the mark (non-NVIDIA, DGX Spark), refused for lack of support evidence
    (gfx1170 / 1171 / 1250), and judged as CPU because not expected to beat the CPU (2-CU iGPUs). **The Radeon 820M
    (2 CU, AMD's Ryzen AI 5 330 page) is now judged as CPU**, like the 610M; "Radeon 820M / 840M" stays used unless
    the CPU name is the Ryzen AI 5 330.
  - Legend: the level caps line changed; new lines say Perfect is not a speed guarantee, GPU+RAM levels are often set
    by RAM and the offloaded share is assumed (40% of weights on the GPU), a level set by measurements can be Good above
    98% Mem%, and what ✓ means.
- **One mark for measured values** (#108): the table's VRAM shows `m` for any measured value (the `c` for other
  users' measurements is gone), and **`estimate.basis_detail` `measured_community` is merged into `measured`** (apps
  that check `measured_community` should check `measured`; `sample_count` still gives the number of sources, and
  `info --json` still lists each measurement's `origin`). A same-settings value clamped by the bounds is
  `calibrated` (was `measured_community`). `info` prints `n=` for every measured basis.

- **Score is now only the memory headroom (`memory_fit`)** (#95). It uses llmfit's `fit_score` curve (100 while
  r ≤ 0.70, then `100 × exp(−0.5 × ((r − 0.70) / 0.20)²)`), where r is the use of the tightest resource of the run
  mode: full-load VRAM ÷ VRAM (`GPU`), the largest swapped VRAM ÷ VRAM (`GPU~`), max(offloaded VRAM ÷ VRAM, RAM
  needed ÷ RAM) (`GPU+RAM`), RAM needed ÷ RAM (`CPU`), the amount used for the level ÷ VRAM (level set by
  measurements), and max(that, RAM needed ÷ RAM) for rows short of RAM. Rows that run never go below 37.5; rows that
  do not run or are not judged are 0. The level, readiness (gate), "measured or not" and popularity are no longer
  part of it. JSON `score_components` is now `{"memory_fit": …}`; the keys `fit`, `headroom`, `readiness`, `basis`
  and `popularity` were removed. A model row's Score is now the representative's (it was the best score among the
  members). The table's Mem% and JSON `utilization_pct` show r (for `GPU+RAM` and rows short of RAM this can be the
  RAM share). **The Score can now drop when VRAM grows** and a heavier precision or run mode takes over (Z-Image
  Turbo int8: 100 at 14 GB, 82.3 at 16 GB; llmfit behaves the same); the promise "more VRAM never lowers it" now covers
  levels only. More RAM still never lowers the Score.
- **Popularity is an order key, not part of the Score** (#95). New JSON field `popularity` (0–100): the percentile
  (mid-rank) of the model's count among all models of the same type and source in the list (before filters): ComfyUI
  template `usage` or Hugging Face all-time downloads (`downloads_all_time`), never mixed (the 30-day downloads ÷ 100
  conversion is gone). No count → 0; sources with fewer than 5 models in a type → 50 for models with a count. The
  weekly update reports the Spearman correlation between full-load VRAM and template usage for types with 20+
  template models and warns in the PR body below −0.3 (`popularity.vram_bias`).
- **New order** (#95). `recommend`: runs → not judged → does not run → (`--json` and the library: auto-recommendable
  first) → fits without offloading (Perfect / Good on `GPU` / `GPU~`) → Marginal, faster run mode first (`GPU` →
  `GPU~` → `GPU+RAM` → `CPU`) → official templates → popularity → Score → id. `fit` / `search --sort type`: type →
  level → official → popularity → Score → id (the last key was the name). `--sort score`: runs → fits without
  offloading / Marginal → Score → popularity → id. The library and `--json` now put "not judged" before "does not
  run", like the table (it was the other way round).
- **Judging uses the per-type default settings for every model** (#95). Without options, a model with measurements
  was judged at the measurement's settings (the author's app's "fast" quality: Z-Image 768px, SDXL 832px, Wan2.1
  512x512 with 49 frames, Wan2.2 5B 640x640 with 73 frames, ACE-Step 20 s) while all other models used the defaults
  (image 1024x1024, video 832x480 with 81 frames, music 30 s). Now every model uses the defaults; measurement settings
  are only used to compute the shift. Most bundled maintainer measurements (image, video, music) therefore become
  `calibrated` at the defaults and lose the `m` in the table (3D and utility measurements match and keep it). Apps
  that want their own settings pass them per model (`judge_model(..., settings=...)`, `--width` etc.); no new API.
- **Measurements set the level only on the same GPU, and only when two or more independent sources agree** (#95).
  The maintainer's measurements now follow the same rule as other users' (`estimate.same_gpu`; before, they counted on
  any same-vendor GPU with at least the measured VRAM, e.g. Wan2.2 5B measured on an RTX 5080 was Good / GPU on an
  RTX 4060 Ti 16 GB). The maintainer's rows are one source together, however many (the maintainer alone never sets
  the level); an aggregated community row counts its `n_sources` (one per
  pull request); they are added up. They must agree (no OOM record, aggregated rows `consistent`, spread across rows
  within 15% of the median). The amount used for the level is max(largest peak + context, weights + fixed part), the
  same physical floor as the values; the table's VRAM, Mem% and `memory_required_gb` show it (new
  `estimate.measured_level_vram_gb`; `measured_peak_vram_gb` keeps the raw peak). With one source the values are
  still shifted (from one submission) but the level comes from the estimate, and the reasons say why. The same applies
  to lifting the Good cap of AMD / Intel / Apple GPUs. Wan2.2 5B on an RTX 5080 at 640x640 / 73 frames is no longer
  Good / GPU (#26): the maintainer's two rows are one source, and its weights + fixed part (about 18.4 GB) does not
  fit 15.9 GB. The measurement finished by offloading part of the weights with dynamic VRAM, so the right level is
  the offload level (Marginal, GPU+RAM); docs/scoring.md works through this example.
- `contrib aggregate --output` refuses (exit code 1) when sources would be counted per file (no git history),
  unless `--allow-file-sources` (tests and local experiments only); `--check` reports `file_sources`, which must be 0
  for the bundled aggregate (#95).
- Aggregated community rows now carry `source_ids` (short IDs of the sources used), and measured levels count
  sources as the union of the IDs across rows, so one pull request whose submissions land in two rows (with and
  without a GPU device ID, or by measuring method) is still one source (#95). A measurement whose peak + context is
  below weights + fixed part (it ran by offloading weights) no longer sets the level. When the weekly popularity-bias
  check is flagged, the warning also goes into the result's `warnings` and `merge-check` is not safe, so that week is
  not merged automatically. Manually added models have no usage count, so their popularity rank is 0.
- **The same GPU now allows a VRAM ratio of 0.95 (was 0.97)** (#95; `estimate.MEASURED_VRAM_TOLERANCE`). Linux CUDA
  reports the total minus a reservation (a 6 GB card as 5934 MiB = 5.80 GiB, e.g. an RTX 2060 on the NVIDIA developer
  forum; 7.79 for 8 GB, 11.77 for 12 GB, 23.69 for 24 GB) while Windows reports about 6.0, so Linux and Windows owners
  of the same 6 GB card (5.80 / 6.0 = 0.967) never shared measurements. Variants with another amount still never match
  (ratio 0.83 or less: RTX 4060 Ti 8 / 16 GB, RTX 3060 8 / 12 GB, RTX 2060 6 / 12 GB, RTX 3080 10 / 12 GB).
- `Estimate.sample_count` is now the number of independent sources (1 for the maintainer's rows together, `n_sources` for community
  rows, the sum when measurements set the level), not the number of submissions; the reasons say
  "measurements by other users (N source(s); median … GB)".
- The table legend, `--help` and the Python docstrings explain the new Score and order. CONTRIBUTING recommends
  measuring at the default settings, and explains templates without a size input.
- **Submissions are checked more robustly** (#90; about 60 crafted kinds of submissions in
  `tests/test_contrib_robust.py`; each rule is one sentence in docs/schema.md "Rules of the submission pipeline"):
  - A submission PR may only add files: the `Contrib` workflow fails on deleted / modified / renamed files under
    `data/contrib`, on added files not named `data/contrib/<GPU>/<date>-<sha8>.json` (lowercase `.json`), and on PRs
    that add no submission. `contrib validate <folder>` reports misplaced or non-`.json` files as the error `layout` and
    exits 1 when there is no submission. A fixed resubmission is a new file; the maintainer removes the old one.
  - New privacy errors in `note`: base64-like runs, 32+ hex digits, domain names, phone numbers, dotted MAC addresses,
    obfuscated e-mail, 「製品番号」. `note` is limited to 500 characters (was 2000; bench truncates). A UTF-8 BOM and
    duplicate JSON keys are refused with a clear message.
  - `contrib_schema` 2 adds `measurement.files_fp`, the fingerprint of the model's files in the list when measured
    (bench writes it for local measurements and submissions); a measurement whose fingerprint differs from the
    current list is not used. Schema 1 stays valid.
  - NVIDIA GPU names must fully match the name table; other names (e.g. `NVIDIA Graphics Device`) are kept as records
    with `gpu_known: false` and never used for judging. A `vram_gb` that does not match the GPU's nominal VRAM
    (desktop RTX 20–50, from NVIDIA's comparison page) is an error.
  - `contrib aggregate`: `error` runs are counted (`n_error`) instead of becoming OOM rows; the step count is no longer
    part of the key (matching already ignored it); submissions added by the same commit / PR count as one source
    (`n_sources`; outliers are judged between sources; without git history each file counts on its own and the output
    says so); rows whose OOM sources are at least their ok sources get `runs_evidence: false`.
  - Notices for a submission made by a newer genmodelfit, a ComfyUI older than the template needs, or much newer than
    the latest the list knows.
  - Follow-ups after review: the bundled aggregate records each submission's source (`submissions: [{sha256,
    source}]`), so `--check` and the test re-aggregate without git history (shallow clones, ci_local's WSL copy);
    sources follow renames back to the first add; the nominal-VRAM check accepts what CUDA reports on Linux (6 GB as
    5.80 GiB; range 0.95–1.03); the NVIDIA name table strips only known suffixes (Ti, SUPER, D, v2, Laptop GPU,
    with Max-Q Design, Workstation Edition, OEM, a VRAM size the model has, …) once each; node names, 40-digit commits
    and public domains (huggingface.co, github.com, …) no longer trip the privacy check; the paste fallback keeps the
    local path outside the `---` lines; `contrib.yml` compares the merge commit with its first parent and refuses
    links / submodules; `aggregate --check` errors on a missing folder and lists bundled rows whose `files_fp` no
    longer matches.
    Public domains count only when the host name ends with them; 32+ hex digits are caught again except exactly 40
    (git commit) and 64 (sha256); padded base64 is caught from 16 characters; tests never run the real `nvidia-smi`
    from bench.
- **Detection works in every environment** (#101; docs/hardware.md "Every environment"). Memory falls back to
  GlobalMemoryStatusEx / `/proc/meminfo` / `sysctl hw.memsize` when psutil fails (unknown → `ram_total_gb` 0 = unknown, not used to refuse, and "give it with --ram"),
  and MemFree + Cached on old kernels; the container limit walks the process's own cgroup (v2 and v1) to the
  smallest limit, with available = limit − usage. A compute capability newer than the PyTorch table is no longer
  refused (a note). Unknown machines are no longer treated as x86_64, and the table is keyed by (OS, machine)
  (no CUDA build for Windows on Arm64; 32-bit Python is noted). MIG mode is noted. When PowerShell / lspci /
  system_profiler cannot be read there is a note; Linux without lspci reads sysfs. amdkfd's `gfx_target_version`
  is used before the name (newer RDNA values are not refused). Virtual-machine and BMC displays are "not a GPU for
  computing"; device-ID lookup is for AMD and Intel only. Non-Apple GPUs on macOS are out of scope first, and an
  Apple display that is not a chip name (the VM "Apple Paravirtual device") is CPU. macOS reported as 10.16 is
  read again with `sw_vers`. An NVIDIA adapter without a driver is reported by vendor ID, not by its (localized)
  name. Device Manager codes 22 / 28 / 43 get their own reasons, and AMD / Intel adapters with them are not used. New AMD / Intel GPUs not in the Windows name
  table point to `--vram`. Timed-out commands are killed with their process group on POSIX.
- **Submitted GPU names are checked by form, not by a name table** (#101). Only placeholders (empty, n/a, none,
  null, unknown, -) and generic names (`NVIDIA Graphics Device` etc.) give `gpu_known: false`. An NVIDIA name that
  does not fit the form `[NVIDIA] BRAND* CORE SUFFIX*` is the notice `gpu_name_form` (maintainer check), not an
  error; a VRAM that is not the name's nominal amount is the notice `gpu_vram_nominal` instead of the error
  `gpu_inconsistent` (modified cards exist). Generation names in parentheses such as `(Pascal)` are allowed.
  nvidia-smi placeholders (`[Not Supported]`, `[Unknown Error]`, `ERR!`, `nil`) and device-ID-only names are generic.
  `hardware.nvidia_name_known` was replaced by `hardware.nvidia_name_well_formed`; `estimate.nvidia_size_ok` was
  removed.

- **Working memory now uses ComfyUI's own per-stage estimates** (#100; docs/accuracy.md "Formulas"). For each model
  family the working memory of the denoise stage (S: `memory_required` of model_base.py, SDPA path, 1×, packed latents
  and reference latents counted), the decode stage (V: each VAE's `memory_used_decode` / `memory_used_encode`, one
  image, encode counted for image/video/audio inputs) and the text stage (T: only the 4 text encoders with a
  `memory_estimation_function`) comes from the formulas in ComfyUI's code, cited as `file:line@426f5ee` in the new
  `src/genmodelfit/working.py` (no ComfyUI code is imported or copied). All on GPU = fixed + all weights +
  max(S, V_eff, T); swapped in turn = fixed + max(stage weights + that stage's working memory); offload as before
  with the stage values. V_eff = min(V, V_tile) with VRAM-independent tile sizes, so more VRAM still never lowers a
  level; when V does not fit, the reasons say ComfyUI will split the VAE into tiles (slower). Vendor rules: the SDPA
  formula on every vendor (a note only where ComfyUI uses split attention: Apple, CPU, AMD outside the SDPA list),
  KL VAEs × 2.73 on AMD, VAEs without fp16 × 2 on GPUs without bf16, the Flux2 VAE × 4 (ComfyUI 0.40; 0.43 drops it
  with the fused norm). Model sizes come from `header_params` bins (Wan dim, Flux2 hidden size, LTXV / LTXAV, SDXL vs
  SD1.5). 86 of the 329 bundled models (text to speech, 3D, unknown family / size, no VAE formula) keep the earlier
  per-modality formula for the whole model. Level changes against 0.10 on NVIDIA 8 / 16 / 24 GB: 9 better and 12
  worse / 11 and 11 / 4 and 16 (for example Wan 2.2 5B, LTX-2 / 2.3 and HunyuanVideo improve on 8 to 16 GB; Flux.2
  Klein, Flux Kontext and Z-Image bf16 worsen through the × 4 Flux2 VAE or a large denoise factor; on 24 GB MOSS-Image,
  MageFlow, Klein 4B edit and the SDXL base + refiner workflows go from Perfect to Good). Details: docs/accuracy.md.
- **How the measurement shift is applied** (#100): a negative "measurement − formula" is added to the working memory of
  every stage and clipped at 0 per stage, so all on GPU, swapped in turn and offload never go below weights + fixed
  part (also for the author's measurements). A positive shift goes to all on GPU only (an unpinned measurement above
  the formula is a peak on a GPU with room: dynamic VRAM kept the parts loaded and decoded the VAE without tiles), also
  for models without a per-stage formula; a shift from a measurement pinned at the GPU limit goes to all three. A
  measurement (author or other users) within 0.1 GB below the floor stays `measured` with the floor value and a
  reason (rounding or measurement noise; ACE-Step 1.5: 10.80 against 10.82). `calibrate` uses only the bundled
  measurements (this PC's bench results in the cache are excluded and reported). Wan 2.1 1.3B at the app's settings
  (512 px, 49 frames) is Marginal on 6 GB (offloaded) and on 8 GB (swapped in turn, 97%).
- **Measurements of working memory only are cross-checked only** (#100). `vram_metric: torch_reserved` with
  `offload: dynamic` (bench with dynamic VRAM, including other users' submissions) excludes the weights, so such rows
  no longer shift estimates or set levels until per-stage peaks can be measured. `calibrate` compares them with the
  working-memory estimate (new column "Compared"), and `contrib aggregate --report` does not count them as breaches.
- **`calibrate` fits only `act_3d_gb`** (#100). The fixed part and the image / video / audio coefficients are frozen at
  the 0.10 values (`calibrate.FROZEN`, `frozen` in calibration.json; the fixed part waits for new RTX 3070
  measurements). docs/accuracy (English and Japanese) is rewritten: formulas with sources, tiles, family mapping,
  the 86 models without a per-stage formula, the cross-check (RTX 3070 sweep marked as not evidence, HunyuanVideo and
  CogVideoX public figures) and future work.
- **New VAE kinds in the list** (#100): `comfy_vae` splits `wan22`, `qwen21`, `flux2`, `oobleck`, `yue2` and `pixel`
  out of `wan` / `kl` / null (52 bundled entries changed; `SCHEMA_VERSION` unchanged; older lists are mapped to the
  family's VAE).
- **ComfyUI citations unified to commit 426f5ee (v0.43.0)** (#100): families.py, docs/hardware and docs/schema now
  cite line numbers of 426f5ee instead of b1a247c; differences from 0.40.0 are noted.

### Fixed

- **bench no longer marks full loads as `offload: lowvram`** (#102). With ComfyUI started with `--verbose DEBUG`, the debug line
  "lowvram: loaded module regularly" (logged when a model loads fully) matched, so normal measurements were treated as pinned
  at the limit. `lowvram` now means ComfyUI was started with `--lowvram` / `--novram` ("Set vram state to: LOW_VRAM / NO_VRAM").

- **GPUs missing from the tables are now judged** (#103; sources on each row of docs/gpu-inventory.md): Radeon 8065S
  (gfx1151), RX 7500 / 7400 / 7300, PRO W7400 and the Steam Machine GPU (Navi 33, gfx1102), Radeon Pro W6900X,
  Pro V520, the CPU names of Ryzen AI 4xx / PRO and Ryzen 9 270, Intel Arc Pro B65 / B70 and Data Center GPU Max
  (Linux only, as in PyTorch's table). `AMD_ARCH` gained gfx1170, gfx1171, gfx1250 and gfx803.
- **A newer pci.ids no longer turns a usable GPU into CPU** (#103). Device IDs whose product name becomes generic
  ("Navi 33 [Radeon Graphics]", "Battlemage G31 [Intel Graphics]") are looked up in the device ID table when amdkfd
  is not available. This also applies on Windows: a Core Ultra whose Arc Graphics shows as "Intel(R) Graphics"
  because the memory is single-channel (Intel support 000102144) is used as XPU when its device ID is Meteor Lake
  7d55, Arrow Lake-H 7d51 or Lunar Lake 64a0, with a note that single-channel memory is slower. The Radeon 820M
  row now comes before the 840M / 860M row (pci.ids 1902 "Radeon 820M / 840M"), and "Navi10Lite" / "Navi12Lite"
  (console chips) no longer match Navi 10 / 12.
- **gfx that are not Release Ready in TheRock are judged as CPU** (#103): gfx1170 / gfx1171 (Build Passing only) and
  gfx1250 (up to Sanity Tested). Before, they passed as "a newer RDNA" through amdkfd.
- **The Radeon 820M follows TheRock (gfx1153)** with a note that ROCm hw specs says gfx1152 (#103); the CPU-name table
  for the Ryzen AI 5 330 now agrees. The verdict is the same either way.
- **Wording** (#103): an old driver with an unknown generation no longer promises that a driver update makes Kepler or
  Fermi run; the cu129 range notes that Windows wheels exist only for 2.8 and 2.9, and cu128 for aarch64 for 2.7 and
  2.9 to 2.11. AMD's Windows policy (AMD's official table vs TheRock's Release Ready) and why Jetson Orin 8.7 with
  CUDA 13.2 is not used are documented in docs/hardware.md.
- **The reason for the Good cap on non-NVIDIA GPUs and DGX Spark** (#101; the cap itself was removed in #108) now says what it is: no measurement has
  run on this kind of GPU yet (lifted once two or more measurements of the same GPU agree), not "an estimate".
  When one source has measured this GPU with these settings, it says that there are not yet enough measurements to
  set the level (two independent sources) instead (#103).
- Workstation Ada GPUs named "RTX 4000 SFF Ada Generation" or "RTX 2000E Ada Generation" now get the Ada generation
  (8.9) from the name when nvidia-smi does not report it, and "RTX 2000E Ada Generation" is a known GPU name.
  Tests pin how NVIDIA GPUs are matched by name and VRAM, without PCI device IDs (#99).
- **Wan 2.2 5B is no longer Too tight on 8 GB** (#100): with ComfyUI's Wan 2.2 VAE estimate it runs offloaded on 8 GB
  and on the GPU from 12 GB, matching the AI生成キット catalog (the strict xfail of this known miss was removed).
- **Bench submissions with dynamic VRAM no longer trigger spurious deviation notices** (#100): `contrib validate`
  compares working-memory-only submissions with the formula's working memory instead of the full-load value.
- **A fake small measurement, or one person submitting many times, can no longer lift a model's level** (#95). A
  single crafted submission such as "Flux ran in 1.0 GB" made the 22 GB Flux Perfect on an 8 GB RTX 3070 while the
  table still showed 22 GB; four near-identical files from one pull request also set the level. Now the level needs two
  or more independent sources and never uses less than weights + fixed part.
- **Only the models the maintainer measured were compared at smaller settings and could be lifted on other GPUs**
  (#95; see "Changed"): judging settings and the level rule no longer depend on who measured.
- **Measurements of templates without a size input are now used** (#90). Templates that work from an input image
  (depth estimation, background removal, MoGe, BiRefNet, …) have no width / height input, so the bench records no
  size; judging used the image default (1024x1024), so such measurements never matched and were never used (the
  bundled RTX 3070 BiRefNet measurement, and every community one). Templates without a size input are now listed in a
  hand table checked by the maintainer (`estimate.NO_SIZE_INPUT`: BiRefNet background removal and MoGe depth
  estimation, each with its evidence; never decided by community submissions), and when no size is given their
  judging settings carry no size, so a size-less measurement matches. The working memory is still estimated at the
  default size, so the estimated values do not change. `contrib validate` warns `settings_missing` only for models
  not in the table, and gives the notice `batch_sizeless` for a model in the table with `batch` other than 1. The
  bench writes a size only when the template has a size input (`workflow.size_input`). The bench no longer reads a `batch_size` outside the size / latent nodes as the
  batch (MoGe's processing batch of 4 was recorded as `batch: 4`); the bundled RTX 3070 MoGe measurement is corrected
  to `batch: 1` (calibration unchanged). A MoGe submission made by 0.10.1 still says `batch: 4` and does not match
  (measure again, or make it again without `batch`).
- **Merging a valid submission PR no longer breaks main's tests** (#90). The test required the bundled
  `measurements_community.json` to equal the aggregate of all of `data/contrib`, but a submission PR may only change
  `data/contrib` and the aggregate was a later maintainer step, so every merge turned main red. The aggregate is now
  rebuilt when making a release (docs/release.md "Releasing later versions"; community measurements reach users only
  through package releases): `contrib aggregate --output` records the content sha256 of the submissions it used in
  a new top-level `submissions` list (older versions ignore it), and the test checks that every listed submission is
  in `data/contrib` and that re-aggregating exactly those reproduces the file; submissions not yet listed are
  allowed. New `contrib aggregate --check` lists them (exit code 1 if any).
- **Tests no longer read the user's real cache** (#90): `tests/conftest.py` points `GENMODELFIT_CACHE_DIR` at an empty
  folder for the whole session and for each test, so a local bench result no longer breaks the suite. Tests that
  compare community data no longer assume an empty bundled aggregate. `tools/ci_local.py` shows
  `contrib aggregate --check` as information (not a failure).

### Changed

- The README picture is retaken with the current table, in English (`docs/images/recommend.png`) and in Japanese
  (`docs/images/recommend.ja.png`, now used by README.ja.md). `tools/screenshots.py` makes them, so they can be
  retaken whenever the table changes (#89).

## [0.10.1] - 2026-10-26

### Changed

- **The `recommend` / `fit` / `search` tables no longer have a Verified column** (#84). It showed the gate's verdict
  for automatic recommendation (`yes`, or the first failed check such as `license` or `workflow`), which was hard to
  read and to check by hand; llmfit has no such column either. The legend line about it is gone, and the freed width
  goes back to the other columns (with the wider Use column below): at 100 columns without the Censored column (the
  default `sfw`), Model is 2 characters wider (24 → 26) and Disk is shown again; with the Censored column
  (`--content all|uncensored`), License is shown instead of being dropped (Model 31 → 27; Disk and Mem% are still
  dropped).
  `info <id>` still shows each gate check with [ok] / [NG], and `gate` still counts them.
- **The `recommend` table no longer puts Verified models first**: rows are ordered runs → Perfect/Good → Marginal →
  official first → Score → id (the legend says `Order: runs, Perfect/Good, Marginal, Score.`), and `-n` cuts this
  order, so the table may list different models than before. **`recommend --json` and `gmf.recommend()` are
  unchanged** (same order with `auto: true` first, same contents, `auto` / `held_by` / `gate` kept), so with `-n` the
  table and the JSON can list different models (docs/schema.md "Order of `recommend`"); the legend of `recommend`
  says `--json lists auto-recommendable models first (schema.md).` (not with `--auto-only`, where both orders are
  the same). `fit` and `search` never
  sorted by it. The README screenshots (`docs/images/*.png`) still show the old column; there is no tool to render
  them, so they are left unchanged.
- **The table's Use column no longer shows `+N`** (#84, owner's comment): `gen+3` ("the shown use plus N other
  workflows of the same model") was not understood. A row that merges the workflows of one model now lists their uses
  without repeats: first the row's own use (the one that matched `--task`, else the shown workflow's), then the others
  in the order of `genmodelfit types` (`gen`, `edit`, `i2v`, …), e.g. `gen,edit` (`edit,gen` with `--task edit`), or
  just `gen` when they are all the same; with `--all-workflows` it is the row's own use. The column is 9 characters
  wide (was 7, so `gen,edit` and `lipsync,…` fit); a longer list is cut after the last use that fits and ends with
  `,…` (`gen,…`), so the row's own use is always visible. The legend says
  `Use = workflow uses (info, --all-workflows)` instead of `+N = other workflows (--all-workflows)`. JSON (`use`,
  `workflows`) and `info` are unchanged.

### Fixed

- `download` on Windows: when two downloads of the same file overlapped, the `.lock` could be left behind
  (the owner could not delete it while the other one was reading it), and the next download in the same process
  then stopped with "another download is fetching the same file". The owner now retries the delete (#85).

## [0.10.0] - 2026-10-26

### Changed

- **Output is now English by default, in exactly one language** (#73). Japanese needs `--lang ja`,
  `GENMODELFIT_LANG=ja` or `gmf.set_language("ja")`; the OS language is not used. **Behavior change for apps**: an app
  that shows genmodelfit's texts to Japanese users (e.g. AI生成キット) must call `gmf.set_language("ja")` (or set
  `GENMODELFIT_LANG=ja`) to keep Japanese reasons, `notes`, gate `detail` and error messages.
- English output contains no Japanese: table section headers (`== image` instead of `== image (画像)`), `types`,
  `info` labels and descriptions (`description[lang]`, falling back to the other language), estimate reasons and
  `notes`, limits, gate `detail`, errors and warnings, `--help` of every command, and the messages of `download` /
  `update` / `bench` / `contrib` / `review` / `diff`. JSON keys, ids, codes (`fit_level`, `basis`, check ids,
  `vram_free_short`, …), scores, judgments and ordering do not change; only the language of the texts does. Free text
  stored in the list (`review.note`, `license_evidence`, measurement `source` / `note`) is not translated, and text
  that `update` writes into the list stays Japanese whatever the display language.
- **Japanese output (`--lang ja`) is now Japanese only** (#76), one language per line: the lines above and below
  the tables (`PC:`, the heading with hidden counts, the legend and notes), section headers
  (`== 画像 (image): 37 件中 上位 3 件(続きは -m image)`, keeping the name for `-m`), `system`, `info`, `types`,
  the `download` plan, `gate`, `review`, `contrib`, error messages (`エラー:`) and `--help` (descriptions, option help,
  section titles, and argparse's own texts such as `使い方:` and `-h`). Kept in English in both languages: table
  column names, fit level names (`Perfect` / `Good` / `Marginal` / `Too tight`), run-mode marks (`GPU` / `GPU~` /
  `GPU+RAM` / `CPU`), units, ids and other machine-readable values (README "Language"). JSON is unchanged. Tests
  that ran in Japanese and pinned the old mixed text now check the Japanese text (and the English text with
  `--lang en`); a new test fails on any English-only sentence in the Japanese output of the main commands and of
  every `--help`. The README screenshots (`docs/images/*.png`) still show the old mixed output; there is no tool to
  render them, so they are left unchanged for now.
- Line wrapping (tables, `info`, `download`, `diff`, `system` notes, option help) counts East Asian wide characters
  as two columns and never splits file names, URLs, options or numbers with units (one longer than the width stays
  whole on its own line, so it can be copied); Japanese lines do not start with 、。) or end with (「 (#76). English
  lines break where they did before, except that URLs, words with hyphens and numbers with units
  are no longer cut in the middle (as a long path no longer is).
- The Japanese descriptions of Stable Audio 3 Small (music), Irodori-TTS and Tsukuyomi-chan now carry what only the
  English ones said (English prompts; the ethical restrictions on the MIT license; the voice data's own terms).

### Added

- `--lang en|ja` on every command (abbreviations such as `--lan ja` work too), the environment variable
  `GENMODELFIT_LANG`, and `gmf.set_language()` / `gmf.get_language()` (#73). Order: `--lang`, `GENMODELFIT_LANG`,
  `set_language()`, then `en`. The weekly PR body stays Japanese.
- An EXAMPLES section in `genmodelfit --help` and in the `--help` of `recommend`, `fit`, `info`, `search` and
  `download`, in the display language.
- `download` explains what to do next (#75):
  - When it stops, each reason is shown once (the final `error:` line that repeated them is gone; the exit code stays
    1 and `--json` keeps `error`). When only `--content all` / `--include-unknown-license` are missing, it ends with
    "To download anyway:" and one ready-to-run command with all of them added (your other options kept), plus one
    line per flag on what you agree to. It never adds them by itself. For gated files, files it cannot fetch
    (their URLs are listed) and workflows without files it says genmodelfit cannot download the workflow and names a
    clean alternative workflow if there is one; for disk space, `--budget` and same-name files it names the lever.
    No command is shown in those cases. `--list` shows the same guidance; a blocked run writes one short `error:`
    line to stderr. The command uses the plan's workflow id and the program form you ran (`genmodelfit` or
    `python -m genmodelfit`), with single-quoted arguments where needed.
  - Files whose ComfyUI folder is unknown go to `other/` as before, and the plan now says clearly (in every layout)
    that ComfyUI does not read `models/other` and that they must be moved before use. For a main file without a
    folder in the list, the folder is inferred when its header parameter count matches a file of an official template
    in exactly one folder (e.g. SDXL all-in-one files → `checkpoints`); the plan shows the basis, and says when a GGUF
    file needs the ComfyUI-GGUF custom nodes. In the bundled list this places the main file of 12 of the 48 Hugging
    Face models in `checkpoints/` or `diffusion_models/` instead of `other/`: a copy downloaded with 0.9.2 into
    `other/` is downloaded again (move it to the new folder first to avoid that).
  - A file with no license URL says "no license stated" and shows the page to check (the model's Hugging Face page or
    the page in the list), and the consent question no longer refers to URLs that are not shown.
  - The environment variable `GENMODELFIT_COMFYUI_DIR` sets the default for `--comfyui-dir` (`--comfyui-dir` and
    `--output-dir` win over it; checked like the flag). Without either, the plan says the default folder is not
    ComfyUI's and shows how to set it (one hint for this OS, merged with the extra_model_paths.yaml note).
  - `download --help`: examples for `--list` first, then downloading into ComfyUI, and
    `--content all --include-unknown-license`.
  - JSON (optional keys): `plan.blocked_codes`, `alternatives[].ok`, and per file `directory_basis` (an
    object) and `check_url` (https pages only).
  - In a terminal, `download` no longer needs consent flags: uncensored models and files with no stated license are
    shown in the plan and named in the consent question (y/N, default No; one `y` covers them). Without a terminal,
    with `--yes` or `--json` the flags are still required. `--list` shows what will be asked. The Python API is
    unchanged (pass `content=` / `include_unknown_license=`). Gated files are still never downloaded.
- Shorter flags (#75): `--uncensored` (= `--content all`) on `recommend`, `fit`, `search`, `info`, `types` and
  `download`, and `--any-license` (= `--include-unknown-license`) on `recommend`, `fit`, `search` and `download`.
  The old forms still work; `--content uncensored` still means "only uncensored"; `--uncensored` with
  `--content sfw|uncensored` is a usage error. Consent flags (`--uncensored`, `--any-license`,
  `--include-unknown-license`, `--content`) must be written in full: abbreviations such as `--u` are a usage
  error. Hints, notes and `--help` examples now suggest the short forms. JSON is unchanged. The library's
  `blocked` / not-found texts name the Python arguments (`content="all"`, `include_unknown_license=True`); the CLI
  shows its own flags.

### Fixed

- Read-only commands (`system` / `recommend` / `fit` / `info` / `search` / `types`) and `import genmodelfit` no longer
  import `requests`. It is now imported only by the functions that use the network, so a mismatched urllib3 / chardet
  in the user's environment no longer prints `RequestsDependencyWarning` on every run, and start-up is about 60 ms faster
  (#74).
- `download`: when two downloads of the same file run at once, the one that gets the lock after the other has
  finished now checks the placed file (sha256) and skips it, instead of downloading it again, refusing to overwrite
  and leaving a `.part` behind. A download refused because a file appeared at the destination no longer leaves its
  `.part` either (found by a rare failure of the two-thread test in the local CI).

## [0.9.2] - 2026-10-24

### Fixed

- The `--help` of `system` / `recommend` / `fit` / `search` / `info` no longer says the GPU is detected only through nvidia-smi
  (AMD / Intel / Apple are detected since 0.7.0).
- The reason shown when another user's measurement is above the GPU's total now gives the real cap
  (total × 1.02 + 0.25 GB) instead of "the total".

### Documentation

- README, CONTRIBUTING and docs/ brought up to date with 0.9.0 / 0.9.1: other users' measurements apply only to
  the same GPU with VRAM within 3% (so adding VRAM can drop a measured value; noted as the exception to "more VRAM
  never lowers the score"); `gmf.download` is the other Python call that uses the network; PCI model IDs are kept
  in the output; submission checks and fields; `files_changed`; strict type checks; local CI (`tools/ci_local.py`)
  and the weekly update on the maintainer's PC. Counts and dates that change every week now say where to read them.
- README: a screenshot of `genmodelfit recommend` (8 GiB VRAM, 32 GiB RAM; model list as of 2026-10-22), and a
  quick start that shows `uvx genmodelfit recommend` (try it without installing) before the install step.
- Every page in docs/ and CONTRIBUTING now has an English version (`X.md`) and a Japanese version (`X.ja.md`) with
  the same content, instead of pages that were Japanese only or mixed both languages. Each page starts with what it
  covers and who it is for; the longer pages have a table of contents. New index: docs/README.md.
  `tests/test_docs.py` checks that both versions exist, that links and anchors resolve, and that sections cited from
  the code exist.
- The project's "Documentation" link on PyPI now points to the docs index.

## [0.9.1] - 2026-10-22

### Fixed

- **A list with a wrongly typed field is refused when it is read** (#63). `repo_id`, `license_from`, `license_path`,
  `sampler_stages`, `comfy_family` and the other optional fields of an entry, a file and a template (`source`,
  `license_url`, `base_model`, `content_evidence`, `discovered_from`, `updated`, `homepage`, `likes`, `license_evidence`,
  `comfy_vae`, `workflow_precision`, `precision_source`; file: `license_url`, `repo_license`, `license_evidence`,
  `head_status`, `head_checked_at`, `header_precision`, `header_params`, `repo_precision`, `repo_precision_source`;
  template: all fields) are type-checked like `license_name` was in 0.8.0. All 27 distinct versions of the bundled list in the git history (`git log --all -- src/genmodelfit/data/db.json`; the 41 commits that touched `data/` include unchanged copies) still load. Top-level `generated_at` / `sources` are checked too.
- **Other users' measurements are used only for a GPU with a close VRAM.** The same name (or PCI ID) can be a different card:
  RTX 4060 Ti 8 / 16 GB, RTX 3060 8 / 12 GB, RX 7900 XT / XTX / GRE (all `744c`; the 0.9.0 review's N2). A measurement now
  matches only when `min/max` of its VRAM and the PC's total is at least 0.97 (`estimate.same_vram`; not applied to shared /
  unified memory). The fit_change check also tries the nominal VRAMs of NVIDIA GPUs with several sizes
  (`estimate.NVIDIA_VRAM_VARIANTS`, from NVIDIA's product pages). The bundled `measurements_community.json` is empty, so
  bundled judgements do not change. `--vram` that differs from the measured card's VRAM no longer uses that measurement.

## [0.9.0] - 2026-10-22

### Changed — judgements that change

Only the PCs whose GPU #56 now recognizes change with the bundled data. These PCs go from CPU to GPU: Steam Deck
(Van Gogh / Sephiroth), Ryzen Z1 Extreme / Z1, Radeon RX 6300 / Pro W6300 / W6400, Linux PCs whose old pci.ids shows
`AMD Device …` / `Intel Device …` for a known ID, and Linux Intel Arc laptop GPUs (A770M / A730M / A550M / A370M / A350M).
Checked against 0.8.0 over every bundled model × a set of 36 PCs (NVIDIA 4 / 6 / 8 / 12 / 16 / 24 / 48 GB × RAM
16 / 32 / 64, RX 7900 XTX / 7600 / 6800 XT, 780M, Ryzen 9 7945HX, Arc A770 / B580, M2 Pro, CPU 16 / 32 GB, and five
#56 PCs): rows changed only on the five #56 PCs (rows / levels changed: Steam Deck 190 / 82, Ryzen Z1 Extreme 127 / 125,
RX 6300 2 GB 133 / 4, Linux `AMD Device 7551` 291 / 291, Linux Arc A770M 271 / 271); the 31 other PCs are identical.
PCs outside that set were not compared. The bundled RTX 3070
`torch_reserved` measurements change no level (the estimate was already Perfect where they apply), and the bundled
`measurements_community.json` is empty, so the rules for other users' measurements below change nothing today.

- **Non-NVIDIA GPUs that were judged too cautiously** (#56). Sources are in docs/hardware.md「NVIDIA 以外の GPU」.
  - Steam Deck (Van Gogh, gfx1033; also the OLED model's Sephiroth and the Windows driver name `AMD Custom GPU 0405`)
    was CPU as a "2 CU" display-only GPU. It has 8 RDNA 2 CUs (Valve's spec) and TheRock lists gfx1033 as Release
    Ready on Linux and Windows, so it is judged like the 680M / 660M (shared memory, at most Good). Only the 2 CU
    gfx1036 (610M, Raphael, Granite Ridge, Dragon Range) stays CPU.
  - Ryzen Z1 Extreme / Z1 (handhelds, 12 / 4 RDNA 3 CUs per AMD's pages) are judged as gfx1103 APUs like the
    780M / 740M instead of CPU.
  - Dragon Range 7940HX / 7840HX were CPU with the reason "gfx unknown"; they are now named as the 2 CU Radeon 610M
    (still CPU), like the 7945HX.
  - The desktop Radeon RX 6300 (and Pro W6300 / W6400) is Navi 24 (gfx1034).
  - Linux with an old pci.ids: lspci's `Device [1002:7551]` is kept as `AMD Device 7551` and looked up in a PCI ID
    table copied from pci.ids 2026.10.22 (AMD and Intel GPUs that matter for judging); the reason says it was
    resolved from the ID. Unknown IDs get the `update-pciids` hint for Intel too (`Intel Device e20b` used to get none).
  - Linux Intel Arc laptop GPUs (A770M 16 GB, A730M 12 GB, A550M 8 GB, A370M / A350M 4 GB, from Intel's spec table)
    use the nominal VRAM, marked as nominal in the reason, because the xe / i915 drivers expose no VRAM size in sysfs.
    They were CPU before.
- **Other users' measurements (#34) are used like llmfit's** (owner's decision, 2026-10-22), **only on the same GPU**.
  A community measurement moves the needs and the level only of a PC whose GPU is the measured one (same PCI model ID
  when both sides have one, else the same normalized name; generic names such as `AMD Radeon(TM) Graphics` never
  match). PCs with another GPU, or none, are judged exactly as without it. The maintainer's bundled measurements are
  still used on every PC (bundled judgements unchanged). `consistent` groups still enter the coefficient calibration.
  On the same GPU:
  - an `ok` group is used from **one** submission (median of the group; `n_used` / `n_oom` kept). A group with `ok`
    and `oom` runs uses the `ok` median and reports the OOM count;
  - values move the needs **both ways** and `measured_community` scores like `measured`. Physical bounds: the all-in
    value never goes below weights + fixed overhead (a smaller value becomes that floor; `basis` is then
    `estimated`), never above the measuring GPU's VRAM × 1.02 + 0.25 GiB, and a value pinned at the GPU limit is a
    lower bound. `peak_ram_gb` is capped at the submission's RAM × 1.05 + 1 GiB. Spread to other settings
    (`calibrated`), measured ÷ formula is clamped to [0.6, 1.4] (`estimate.COMMUNITY_CALIBRATION_RANGE`; llmfit uses
    [0.05, 3.0]). Removed: "never lower the needs", the +15% cap (`COMMUNITY_RAISE_CAP`), ignoring rows that change the
    level, and the 2-consistent-submissions rule;
  - a successful measurement at the same settings can set the level (#26) and lifts the AMD / Intel / Apple
    "at most Good" cap. Perfect needs a peak within 60% of this VRAM (for `torch_reserved`, after adding the context,
    next item), not pinned at the GPU limit and no OOM report in the group.
- **`torch_reserved` measurements can now make Perfect** (#26 levels from measurements). Every `bench` result and
  submission is `torch_reserved` (the ComfyUI process's torch reservation, without the CUDA / HIP context), and the
  old rule "torch_reserved never makes Perfect" kept AMD / Intel / Apple GPUs at Good even with a measurement of that
  GPU. The level bands (60 / 85 / 98%) and the fit check now use the peak plus `estimate.TORCH_RESERVED_CONTEXT_GB` =
  0.5 GiB (the upper end of bench's "about 0.3–0.5 GB" context; the cautious side). Needs (`vram_gb`) and calibration
  are unchanged.
- **Same-GPU matching** (`estimate.gpu_key`, `estimate.same_gpu`): one normalization for both the PC's GPU name and the
  measured one — case, spaces and symbols, `(R)` / `(TM)` / `™` / `®`, and the words "NVIDIA" / "GeForce" (the same
  card is "NVIDIA GeForce RTX 3070" in nvidia-smi and PyTorch but "GeForce RTX 3070" in lspci; NVIDIA product names do
  not collide without them; "AMD" / "Intel" are kept so `AMD Device 744c` ≠ `Intel Device 744c`). New `Gpu.pci_id` /
  `Adapter.pci_id` (PCI model ID `1002:744c` from Windows WMI `PNPDeviceID` or Linux `lspci -nn`; never the bus
  address) and `Measurement.gpu_pci_id`: when both sides have an ID, the ID decides.
- **The vendor of a measurement comes from its recorded backend** (`gpu_backend` / the submission's
  `system.gpu.backend`) and only falls back to the GPU name when missing. A name `mps` is Apple.
- **`bench` on Apple names the GPU by this Mac's chip** (`Apple M2 Pro`, read with system_profiler) instead of
  ComfyUI's `mps`, when ComfyUI runs on the same Mac. Only a plain chip name (`hardware.APPLE_CHIP_NAME`) is used,
  never serials or other text; otherwise, and for a remote ComfyUI, the name stays `mps` (with a warning) and counts
  only as Apple, never as the same GPU.

### Changed — reviews and the weekly list

- **`update` marks an entry `pending` with `files_changed` when its files changed**: files added / removed, a `url`
  changed (the query string counts; only a `#fragment` is ignored), a `repo_id` changed, a `size_bytes` changed **in
  either direction** or became known / unknown, a `sha256` that had a value changed or went empty. Apps download the
  listed URL as is, so an approved entry no longer gets different files silently. A `sha256` filled in for the first
  time does not count. `diff`'s classification agrees for every single-file change except that first `sha256` and a
  pure reordering (tests/test_review_42.py). Over the bundled lists v0.6.0 → v0.7.0 → v0.8.0 it marks 1 entry
  (stable-audio-3-small-sfx), 0 in the next step.
- **`diff` / `merge-check`: a file that only grew is no longer safe** (`files_changed`; a smaller file gets
  `need_decreased` and `files_changed`). A bigger file at the same URL is different content. Weeks where a Hugging
  Face file was overwritten with a bigger one now go to the owner instead of being merged by the weekly job
  (docs/release.md). No such change happened between v0.6.0, v0.7.0 and v0.8.0.

### Changed — submissions (`contrib`)

- `contrib validate` (and the Contrib workflow) fails only on mechanical checks, like llmfit. `deviation`,
  `below_weights`, `fit_change`, `unknown_model`, `unknown_gpu` are **notices** in the job summary for the maintainer
  who merges; `raise_too_large` is gone.
- The `fit_change` notice now checks PCs with the submitted GPU (name, PCI model ID, vendor; VRAM = the submission's
  `vram_gb`; RAM 16 / 32 / 64), since other GPUs are no longer affected. It shows a level going up on that GPU and the
  AMD / Intel / Apple Good cap being lifted. `contrib aggregate --report`'s fit-level table gains a GPU column.
- New errors: `system.gpu.vram_gb` above 256 GiB (was 512); `peak_ram_gb` above `ram_total_gb` × 1.05 + 1 GiB (now also
  capped when read); any NaN / infinity / negative number; a GPU name of another vendor than `system.gpu.vendor`
  (e.g. an NVIDIA name with `amd`); for `apple`, a name other than a chip name or `mps`; a malformed `pci_id`, one
  whose vendor ID does not match, or one of `hardware.PCI_DEVICE_NAMES` whose GPU differs from the name (AMD: gfx;
  Intel: generation and nominal VRAM; an ID outside that table is an `unknown_gpu` notice). Files with `NaN` / `Infinity` are refused when read, and submissions, community rows
  and bench results are written with `allow_nan=False`.
- GPU names may contain `(R)`, `(TM)`, `™`, `®` and a size such as `(16GB)` (as ComfyUI, PyTorch and Windows print
  them, e.g. `Intel(R) Arc(TM) 140V GPU (16GB)`).
- For GPUs whose nominal VRAM the name table knows (Intel Arc: `hardware._INTEL_XPU`), the `fit_change` check also
  uses a PC with that VRAM (A770 comes as 8 and 16 GB under one name). NVIDIA has no such table yet.
- Submissions carry the optional `system.gpu.pci_id` (AMD / Intel, written by bench when ComfyUI runs on this PC;
  `contrib_schema` stays 1, old files stay valid). `contrib show` lists it; CONTRIBUTING.md lists it under what is
  sent. Community rows are grouped by it when present and keep `gpu_pci_id`.
- The weekly workflow writes "提出 0 件" instead of a failure while `data/contrib` does not exist.

### Compatibility

- `contrib`: `Report.review` / `Report.accepted()` are gone (use `Report.notices`; `Report.ok` is the pass / fail);
  `--allow-review`, `--reviewed` and `data/contrib/REVIEWED` are removed; the JSON count `review` is now `notices`.
  `Measurement.approved` and `Submission.approved` are removed (old files with `approved` still read; the key is
  ignored).
- `estimate.pick_measurement(..., gpu=)` takes the PC's `Gpu` instead of a GPU name.
- `contrib.COMMON_VRAM_GB` is removed (the check PCs use the submitted GPU).
- `xpu-smi` is called with `-d -1` (all GPUs). With two or more Intel GPUs it cannot tell which one ComfyUI uses
  (it reports no total), so bench treats it as unreadable (queue and memory checks only) instead of combining them.

### Changed — bench and other

- **`bench` works on AMD, Intel and Apple GPUs.** The busy check (average utilization ≥ 30% stops) and the GPU-wide
  delta in `note` use `amd-smi` → `rocm-smi` (AMD, Linux), `xpu-smi` (Intel, Linux), the Windows performance counters
  through `typeperf` (AMD / Intel, Windows; checked against nvidia-smi on this PC) and `ioreg` (Apple; no sudo)
  (new `gpustat.py`, stdlib only). When no tool can be read, bench no longer stops: it checks the ComfyUI queue and
  that other apps hold at most half of the GPU memory, then measures with a warning and a note; `standard` stays true
  (docs/accuracy.md). A remote ComfyUI still needs `--force`.
- Measurements record `gpu_backend` (`cuda` / `rocm` / `xpu` / `mps`), and submissions carry the optional
  `system.gpu.vendor` / `backend` (both or neither). `contrib show` lists them. Non-NVIDIA submissions no longer put a
  non-NVIDIA generation into `arch`. `contrib aggregate` keeps `gpu_backend` on community rows.
- The table legend says non-NVIDIA GPUs are at most Good until measured and points to CONTRIBUTING.md; the README
  says the same next to the GPU requirements. The legend's first line fits in 100 columns again (`Verified=yes` →
  `Verified`).

### Added

- `Estimate.sample_count` (optional JSON key, like llmfit): measurements behind the value (community: `n_used`;
  maintainer / this PC: 1; null when estimated). `Measurement.n_used` / `n_oom`. Reasons say
  「ほかの利用者の実測 3 件(中央値 7.02 GB)」, `info` prints `measured_community n=3`, and the table marks community
  values with `c` (legend: `VRAM: c = measured by other users`).
- `Gpu.pci_id`, `Adapter.pci_id`, `Measurement.gpu_pci_id` (optional JSON keys; `schema_version` unchanged).

## [0.8.0] - 2026-10-22

### Changed

- **`diff` is usable by users and no longer drops changes silently** (#59). With no arguments it compares the bundled
  list with the list fetched by `update --from-release` (without one it prints how to and exits 0); with one argument
  the bundled list with that file; with two, the two files as before. The header names both lists. Models are shown as
  `name (type) — id`. The default output lists only user-relevant changes: added / removed, auto-recommendation, and
  per changed model the license (`!` = toward the permissive side), size, precision, prompt / speech languages (before
  → after), added / removed files with their license, newly uncensored, review status, and 「その他の変化」 with the names
  of any other changed field. Only new HEAD failures are listed; old HEAD failures, 401 repos, `sources` numbers and
  popularity / date-only changes are counted and listed with `--all`. 0.6.0 → 0.7.0 used to show only a size change;
  it now shows the three music language changes and the T5Gemma text encoder (Gemma license) of
  stable-audio-3-small-sfx.
- Changed models are listed by importance (files, then license, uncensored, runtime / use, auto-recommendation,
  languages / size / precision, then review marks and other fields), so `--limit` never hides a more important change.
  The same low-importance change on many models is one line with a count (`確認の印が付いた(ok): 329 件`; ids with
  `--all`). The `…ほか N 件` line points to `-n N` / `--all`; `--all` has no line limit unless `-n` is given.
  0.5.0 → 0.7.0 and 0.4.0 → 0.7.0 now show the T5Gemma (Gemma license) file first instead of after 300 review marks.
- `diff` and `weekly merge-check` find changes with one function (`diff.entry_changes`), so they cannot disagree, and
  the CLI compares the raw JSON like merge-check (unknown keys of entries, files and the top level are shown).
  Files are paired in tiers (same name + url + sha256 + directory + repo, then directory + name + sha256, then
  directory + name + url, then by order), so same-named files are not mixed up; added / removed files show their
  source (repo or URL). A changed url / sha256 / repo / directory shows the old → new value. Review marks read
  「確認の印が付いた(ok)」 / 「確認の印が外れた」; a missing vs empty language list is not shown; the type is not repeated
  when the name already ends with it. With no arguments, a warning is shown when the fetched list is older than the
  bundled one; without a fetched list, `--json` has the usual keys (empty) plus `note`.
- The loader rejects a non-string `license_name` (entry or file), which used to crash `diff` with AttributeError.
- Second review of `diff` (#59): before → after values are shortened around the part that differs (a URL changed only in
  the middle, or two sha256 that share a prefix, never look the same); the license provenance (`license_url`,
  `license_from`, `license_path`, `repo_license`) is shown with the license, not as 「その他の変化」; collapsed lines
  of auto-recommendation changes always list the ids (cut by `-n` with 「…ほか N 件(-n M で増やす)」); 「その他の変化」 is
  collapsed per field; a file key that is missing on one side and null on the other is no change; model ids are never
  broken by wrapping (a long id gets its own line); the "older list" warning is shown for every argument form; the
  no-cache `--json` has the usual `summary` keys with 0.
- Changed models are shown in three sections, each cut by `-n` separately (owner's decision, #59): files, license
  values (with 「! 寛容な側」), uncensored, runtime / use; then auto-recommendation changes (ids always listed, so
  0.1.0 → 0.7.1 shows all 16 newly auto-recommended ids with the default `-n 20`); then license provenance
  (not collapsed), languages, size, precision, review marks and other fields.

### Added

- `diff --json`: `old_label`, `new_label`, `field_changes`, `model_changes`, `top_changes`, `warnings` (docs/schema.md「diff」). Existing keys are
  unchanged, and the weekly pull request body is the same.
- Tests for `diff` with the real 0.6.0 / 0.7.0 lists (trimmed by `tools/make_diff_fixture.py`), a test that changes
  every field of `ModelEntry` / `ModelFile` / `Template` / `Review` one at a time and requires it in the output, the
  argument modes, 100-column width and JSON compatibility.

## [0.7.1] - 2026-10-21

JSON changes are additive; `schema_version` stays 1.

### Added

- **`gmf.update_from_release(..., session=None, state_dir=None)`** (Issue #58) is public API (`update_from_release` and
  `RemoteError` are in `__all__`). `session` is a caller's `requests.Session` (e.g. with the app's CA bundle) used for
  every GET of the call; `~/.netrc` is still never sent (a no-op `auth` per request, redirects followed by genmodelfit)
  and the fixed User-Agent is sent per request, without changing the session. `state_dir` is where
  `release_state.json` lives (default: the cache folder). Apps no longer need to patch `remote._http_get` or set
  `GENMODELFIT_CACHE_DIR`.
  The session's `verify` / `proxies` win over `REQUESTS_CA_BUNDLE` and proxy variables; an empty `session.auth` counts as none.
  Redirects are followed up to 10 times; an invalid redirect URL is a `RemoteError`.
- **`gpu_memory_required_gb`** on `Fit.to_dict()` (Issue #58): the VRAM needed to run on this PC's GPU, also when
  `run_mode` is `cpu` (where `memory_required_gb` is RAM). Same as `memory_required_gb` for GPU run modes; null without a GPU.

## [0.7.0] - 2026-10-21

Targets 0.7.0 (`_version.py` is 0.7.0). JSON changes are additive; `schema_version` stays 1.

### Added

- **AMD / Intel / Apple GPUs are judged** (Issues #22, #23) when no NVIDIA GPU is usable, from a support table built
  from primary sources (docs/hardware.md「NVIDIA 以外の GPU」): AMD ROCm for RDNA 2 / 3 / 3.5 / 4 on Windows 11 and Linux
  (and Instinct on Linux), Intel XPU for Arc A / B and Core Ultra Arc Graphics on Windows 11 and Linux, Apple MPS on
  macOS 14+. They are estimates: reasons say `推定(<vendor>・実測なし)` and levels are capped at Good. Integrated GPUs
  and APUs use dedicated VRAM + GTT (or half of RAM), Apple silicon 3/4 of unified memory; they never offload.
  Unsupported GPUs (Intel UHD / Iris Xe, RDNA 1 and older, 2-CU display iGPUs, Windows 10) are judged as CPU with a
  reason. NVIDIA judging is unchanged. macOS detection uses `system_profiler`; Linux reads amdgpu GTT from sysfs.
  NVIDIA 以外の GPU も、一次資料の対応表で動く組み合わせなら推定で判定する(段階は good まで)。
- JSON (additive): `Gpu.backend` gains `rocm` / `xpu` / `mps`; `Gpu.memory` (`dedicated` / `shared` / `unified`),
  `Gpu.gfx_target`, `Gpu.carveout_gb`; `Adapter.integrated`, `usable`, `reason`, `shared_gb`, `needs` and vendor `apple`;
  `Estimate.fit_from` (`estimate` / `measurement`) and `measured_peak_vram_gb`.
- **SD family and Stable Audio run on CPU** (Issue #39): SD 1.5 / 2.x / SDXL image models and Stable Audio Open 1.0 /
  3 Small are Marginal (`CPU`) without a GPU when RAM suffices, counting ComfyUI's fp32 weights and working memory
  on CPU (`families.CPU_FAMILIES`). Running on CPU is itself an estimate (no CPU measurements; reasons say
  `CPU は推定・実測なし`). SUPIR and Stable Audio 3 Medium are left out (not checked).
- Tables: without a usable GPU, the PC line names the detected GPUs and how they are treated (`PC: Intel(R) UHD
  Graphics 770 (integrated; not usable for generation → judged as CPU), RAM …`) and a second line says `This PC has no
  usable GPU: only models that run on CPU are shown (N tools hidden: --include-tools)`. The Mode column shows
  `GPU req` (needs a GPU) and `RAM` (RAM short) for rows that do not run; JSON `run_mode_label` is unchanged.
- Uncensored name words `heretic` and `unfiltered` (`update`: name detection and Hugging Face search words).
- Public functions to judge one model with a GPU the app detected itself (Issue #27): `gmf.judge_model(model, *,
  gpu_name, vram_gb, driver, ram_gb, cpu_only, gpu_usable, windows, settings, content, system_info, db,
  comfyui_version) -> Fit` and `gmf.system_from(gpus, *, ram_gb, gpu_usable, windows) -> SystemInfo`; genmodelfit decides
  the generation and the ComfyUI precision from the GPU name and driver. `vram_gb` / `ram_gb` must be finite and
  not negative, and `cpu_only=True` refuses GPU arguments (`ValueError`). `gmf.ModelNotFound` is exported. Only the names
  in `genmodelfit.__all__` are the stable API (docs/schema.md "Python API (stable)", which also notes that the function
  `genmodelfit.recommend` hides the submodule of the same name). With `gpu_usable=True, windows=True` the result equals
  what apps computed with the internal `recommend.judge` / `vram_need` and `hardware.classify_gpu` / `comfy_precision`.
  アプリが内部の関数を使わずに 1 モデルを判定できる公開の関数。
- Hand-added components (Issue #40): `components` in `data/manual.json` adds files the Hugging Face collection does not
  pick up to an entry, with the license taken from the per-file source table (`license_sources.py`; unknown without a
  row) and `manual_component: …` evidence; every file needs a unique https `url` (`ValueError` otherwise). First: `hf:stabilityai/stable-audio-3-small-sfx` now lists the T5Gemma text
  encoder (`Comfy-Org/stable-audio-3` `text_encoders/t5gemma_b_b_ul2.safetensors`, `gemma`), so its `licenses` are
  `other` (stable-audio-community) and `gemma`, and the size / VRAM estimate includes the 1.11 GiB encoder. The id is
  unchanged. 効果音の Stable Audio 3 に、Gemma 利用規約の T5Gemma の部品を足した。

### Changed

- **A successful measurement on the same GPU sets the level** (Issue #26): with a successful standard measurement at
  the same settings on a GPU of the same vendor whose VRAM is at most this PC's (97% tolerance) and
  whose measured peak fits in this PC's VRAM, the level is at least Good and the run
  mode GPU (Perfect only when the peak is within 60% and not pinned at the GPU limit). Wan 2.2 5B on an RTX 5080 16 GB
  is now Good / GPU (was Marginal / offload with `basis: measured`). Reasons say whether the measurement or the
  estimate decided the level.
  同じ GPU で成功した実測があれば、段階は実測で決める。
- `system`: non-NVIDIA GPUs show their backend and memory kind; display adapters show whether they are usable.
- Music `languages` (Issue #41): a music card's `language` usually lists lyric languages, so music entries now take at
  most `["en"]` for `prompt` from the card and no `speech` (the hand table still sets them). ACE-Step v1
  (`audio_ace_step_1_t2a_instrumentals` / `_song`, 17 languages before) and YuE (`zh`, `en` before) are now `["en"]`.
  The song workflow `audio_ace_step_1_t2a_song` gets `speech` from the hand table: the 10 well-performing lyric
  languages of the ACE-Step README (including `ja`).
  音楽のカードの言語(歌詞)を指示の言語にしない。
- **Read gate `checks` by `id`, not by position**: the number of checks depends on the model (10, or 8 for models whose
  `runtime` is neither `comfyui` nor null: `app_runtime` replaces the three ComfyUI checks; 9 → 7 in 0.5.0, docs/gate.md).
  `checks` の数はモデルで変わるので、並びの位置でなく id で読む。

### Fixed

- `download` no longer refuses a file inside the destination as "outside" when another download of the same file is
  removing its `.lock` / `.part` at that moment (Windows: `Path.resolve()` of a file pending deletion returned the
  `\\?\` extended path). The last path component is no longer resolved unless it is a link; parents still are
  (Issue #41; `test_two_threads_same_destination` failed about once in 100 runs under parallel load and passed
  1,200 of 1,200 after the fix).
  同じファイルを同時に落とすと、負けた側が「置き場所の外」と誤って断ることがあった。

## [0.6.0] - 2026-10-21

### Added

- Review marks (Issue #42): optional `ModelEntry.review` `{status: ok|pending, reasons, decided_by, decided_at,
  evidence, note}`. `update` compares with the list it carries over from and marks `pending` new entries (`new`),
  licenses that moved toward permissive (`license_permissive`), lower needs at reference PCs (`need_decreased`) and newly
  uncensored entries (`uncensored_new`); otherwise the previous mark is carried over. `--heads-only` / `--fields-only` /
  `--inspect-only` apply the same rule. A missing `review` is treated as ok, so older lists still load and apps can
  filter with `entry.review is None or entry.review.status == "ok"` (`entry.reviewed`). The bundled list starts all ok
  (`decided_by: baseline-2026-10-21`). `schema_version` stays 1.
  確認の印。新しい項目などは確かめるまで自動のおすすめに出さない。
- Gate check `reviewed` (always last; readiness weight 0): pending models are not auto-recommended
  (`held:reviewed`, `review` in the Verified column).
- `genmodelfit review list [--json]`, `review approve <id> --evidence URL… [--note] [--by] --db PATH` (refuses without
  an https evidence URL), `review reject <id> --note TEXT --db PATH [--hide-name]` (removes the entry and records it in
  `data/review_rejected.txt`, which `update` honors). Python: `gmf.review_list()`, `gmf.review_approve()`,
  `gmf.review_reject()`.
- `diff` adds `classification` `{safe, judgement_needed: [{id, name, reasons}], safe_changes}` for the weekly run
  (safe-only week → the runner may merge; otherwise an evidence pull request for the owner). It is an allowlist: only
  popularity counts, `updated`, template `usage`, HEAD results, growing files, stricter licenses, removed entries,
  auto-recommendation lost (or regained by HEAD with the same files), `sources` statistics and unchanged pending
  marks are safe; any other field change (`files_changed`, `use_changed`, `other_change:<field>`, unknown fields
  included) needs judgement. The weekly run writes `review_queue.json` and adds 「分類」/「確認待ち」 sections to the pull
  request body; exit codes are unchanged.
- `python -m genmodelfit.weekly merge-check --base A --head B [--json]`: recompute the classification from two files
  (exit 0 safe / 3 not safe / 1 unreadable) for the runner script that decides the merge.
- `python -m genmodelfit.review publish SRC DST`: the list to sign without pending entries.
- `python -m genmodelfit.review apply DECISIONS.json --db PATH [--max-approve 10] [--json]`: apply the weekly
  decisions (`approve` / `reject` / `hold`) returned by Claude, strictly validated and all or nothing (exit 2);
  approvals are `decided_by: claude-weekly`, safety rejects keep only the id hash, and the summary is escaped for
  the pull request body. `review_queue.json` / `review list --json` items add `card_url` and `files`
  (repo, path, license, license_url, …).
- Duplicate model ids are refused (`parse_db` / `load` / `save` raise `DatabaseError`; `merge-check` exits 1).

### Changed

- **Published data (data-latest) leaves out pending entries.** genmodelfit 0.5.x does not know `review` (it drops the
  key), so a pending entry that passes the other checks would be auto-recommended there. data.yml now runs
  `python -m genmodelfit.review publish` before signing; pending entries reach data-latest only after approval.
  The bundled `db.json` keeps them (0.6.0 holds them with the `reviewed` check).
  0.5.x は review を知らないので、確認待ちの項目は data-latest に配らない。
- The weekly update now runs as a Claude Code skill on the owner's PC; `weekly-data.yml` no longer runs on a schedule
  (manual `workflow_dispatch` backup only). See docs/release.md.
- `review approve` requires evidence URLs that are https with a host (`https://` alone is refused).
- Tables: the "Auto" column is now **"Verified"** (values unchanged: `yes` or the first failed check). JSON keeps
  `auto`, `held_by` and the check ids. `info` says `verified (auto-recommend)`.
- Tables: the `[U] ` prefix on uncensored model names is replaced by a **"Censored"** column right after Score
  (`Y` = censored / `N` = uncensored), shown only when uncensored rows can appear (`--content all|uncensored`; from
  Python, when a row is uncensored). Legend: `Censored: N = safety filter removed / adult model`. JSON unchanged.
- README split into English `README.md` (also the PyPI page) and Japanese `README.ja.md`.

## [0.5.0] - 2026-10-21

### Added

- App fields for apps that build their screens from the list alone (Issue #36): optional `ModelEntry.languages`
  (`{"prompt": [...], "speech": [...]}`, ISO 639-1), `runtime` (`comfyui` / `piper` / `irodori-tts`) and `description`
  (`{"ja", "en"}`), also in `recommend` / `fit` / `search` JSON and `info`. They are rebuilt on every apply (nothing
  carries over). Languages: the hand table `data/overrides.json` wins; otherwise the Hugging Face card's `language`
  (saved as `card_languages`) is the upper bound, narrowed to `en` when every text encoder is English-only (CLIP / T5);
  without a card a known text encoder gives `["en"]` — a multilingual encoder alone never adds `ja`. Undecided stays
  empty, see docs/sources.md. `schema_version` stays 1: readers up to 0.4.0 drop the unknown keys (`tests/test_appinfo.py` reads the
  bundled list with a copy of the old reader).
- Hand-added models (`source: "manual"`, `data/manual.json`, ids `manual:<name>`) that `update` merges, HEAD-checks and
  header-inspects like the others and whose per-file licenses it compares with the upstream cards (stricter wins, with a
  warning). First three: Tsukuyomi-chan (piper-plus), Irodori-TTS v4.1 Small, Stable Audio 3 Small (music).
- `update --fields-only [--offline]`: re-apply only the app fields and the hand-added models to the current list
  (re-reads the Hugging Face cards for the languages; `--offline` sends nothing and uses the saved `card_languages`).
  アプリが一覧だけで画面を作れるように、言語・動かし方・説明の項目と、手で足すモデル(最初の 3 件)を足した。
- The estimator counts `.onnx` and other non-ComfyUI weight files for models whose `runtime` is neither `comfyui` nor
  unknown (piper). Other models are counted as before. **Versions up to 0.4.0 do not count them, so with the new list
  they show Tsukuyomi-chan (onnx) as size unknown and hide it** — apps that list it need 0.5.0.
  0.4.0 以前の版ではつくよみちゃん(onnx)が大きさ不明で出ない。
- Gate check `app_runtime` (in place of `official_workflow` / `custom_nodes` / `comfyui_version`, same readiness weight
  40) for models whose `runtime` is neither `comfyui` nor null: always held, the app checks its own runtime.
- License names with `ethical-restrictions` / `use-restrictions` are in the use-restricted tier. Irodori-TTS v4.1 Small
  is `other` / `mit-with-ethical-restrictions` (MIT plus the card's Ethical Restrictions), not permissive.
- `update` drops an `hf:` entry for the same repository as a hand-added model (its main file's `license_from` or
  `homepage`), so a model is not listed twice.

## [0.4.0] - 2026-10-21

### Added

- `genmodelfit diff OLD NEW [--json]` and `gmf.diff_db(old, new)` (Issue #19): what changed between two lists —
  added / removed models, license changes (changes toward the permissive side are marked), auto-recommendation
  changes under one policy for both lists (HEAD age ignored), size / precision changes beyond a threshold, newly
  uncensored models, failed HEAD checks, 401 repos and changed `sources` numbers. JSON: docs/schema.md "diff".
  2 つの一覧の違いをまとめる `diff` を足した。
- Weekly data refresh (Issue #19): `.github/workflows/weekly-data.yml` rebuilds the list every Monday with
  `python -m genmodelfit.weekly` (caches kept between runs), checks it (auto count, the HEAD expiry must be more than
  7 days away, ruff and pytest with the new list) and opens a pull request `automated/weekly-data` whose body is the
  diff. It is never merged automatically; publishing stays in data.yml after the owner merges. A partial rebuild
  fails unless run by hand with `allow_partial` and only HF repos failed transiently. docs/release.md.
  毎週、作り直した一覧を PR にする(自動ではマージしない。配るのはマージした一覧だけ)。

- Measurement submissions from users (Issue #34). `genmodelfit bench … --submission PATH` and
  `genmodelfit contrib make --from <bench result>` write a submission file (`contrib_schema` 1:
  `data/contrib/<gpu>/<date>-<sha8>.json`) with the GPU name / VRAM / generation / compute capability / driver, the ComfyUI
  version, the OS family, RAM rounded to GiB and the results — never host / user names, paths, GPU UUID, PCI address,
  IP, CPU name or exact times (the note is scrubbed). genmodelfit sends nothing; users open the pull request themselves.
  `contrib show` prints exactly what will be shared with a privacy checklist; `contrib validate` checks schema, value
  ranges, privacy patterns, GPU name / generation consistency, file name and duplicates (exit 0 / 1 / 2);
  `.github/workflows/contrib.yml` runs it on pull requests touching `data/contrib/**` (read-only, the tool installed
  from the base commit, results in the job summary). `contrib aggregate` groups submissions by model, settings and GPU
  with median / MAD outlier removal into the new bundled `measurements_community.json` (empty for now), and
  `--report` re-runs the calibration and flags errors above a threshold (default 25%, exit 3 with `--fail-on-breach`)
  for the weekly job of #19.
- `Estimate.basis_detail` (`measured` > `measured_community` > `calibrated` > `estimated`); `basis` stays
  `measured` / `estimated`. Community measurements are used only when at least two consistent submissions agree (and
  no OOM), rank after the maintainer's measurements at the same settings, and can only raise the needs (never lower
  VRAM / RAM, never improve a level, never raise the score: `basis` points 0 like estimates). `validate` marks
  implausible values (more than 40% from the formula, below the weights with `offload: none`), values that would raise
  the estimate by more than 15% or change a level at common VRAM / RAM sizes, unknown models and unknown GPUs as
  review-required; approval is per submission (`--allow-review <sha8>` or the maintainers' `data/contrib/REVIEWED`).
  Unapproved community rows raise needs by at most 15% and are ignored where they would change the level.
  `aggregate --report` lists the level changes caused by community data.
- `torch_reserved` measurements (bench; without the CUDA context) are now lower bounds: below the formula they no
  longer lower the estimate (the bundled RTX 3070 rows for BiRefNet and MoGe now use the formula value).
  `Measurement.origin` / `n_submissions` / `consistent` (optional fields).
- `python -m genmodelfit.calibrate` also writes the check rows (measurements in another `vram_metric`, such as the
  RTX 3070 runs) to `calibration.json` (`check`) and the accuracy table; LOO rows carry `gpu_name` and `origin`.
  Coefficients are unchanged.
  利用者の実測の提出(作る・見る・検査・まとめ・毎週の精度の点検)と、根拠の段階 `basis_detail` を足した。

### Changed

- Hugging Face selection (Issue #19): the 1,000 all-time floor for models found only by the trending / likes30d
  searches now needs a trendingScore of at least 0.5 and at least 100 downloads in the last 30 days (otherwise
  10,000 applies); repo names containing `merge` / `mergekit` are skipped like the tag; quantized / format copies
  are recognized by name (`-GGUF`, `-Q8_0`, `_K_M`, `-fp8`, `-nf4`, `-int8`, `_diffusers`, `-Quantized`, …) and
  only the most downloaded is kept; uncensored models also need 100 downloads in the last 30 days.
  HF の選び方を締めた(伸びている条件・名前の merge・量子化の写し・無検閲は直近 30 日も使われているもの)。
- The bundled list was rebuilt with these rules (326 entries: HF 48, uncensored 26; auto 99 unchanged).
  Added: 6 uncensored HF models that meet the rules and the new official template `video_fasth3_ominiref_r2v`;
  removed: `hf:0xSojalSec/Qwen-Image-2.1-Uncensored-GGUF` (a quantized copy of
  `hf:abenzerps/Qwen-Image-2.1-Uncensored-Quantized`) and `hf:TheImposterImposters/lustifySDXLNSFW_v20-inpainting`
  (the same weights, by sha256, as the more downloaded `hf:andro-flock/LUSTIFY-SDXL-NSFW-checkpoint-v2-0-INPAINTING`).
  同梱の一覧を新しい規則で作り直した。
- Weekly PR body: every upstream string is HTML-escaped and Markdown-neutralized (`weekly.md`), mentions are
  broken, truncation keeps `<details>` balanced; a scheduled run fails when more than 5 entries become uncensored
  or when HF / template / uncensored / auto counts fall below half.

### Fixed

- Exclusion (safety): uncensored models whose ID, name, tags or card mention a dataset known to include a category
  of sexual content involving minors (`DATASET_TERMS`: e621) are excluded, even when that is not their purpose
  (owner decision, 2026-10-21; `hf:hearmeneigh/sd21-e621-rising-v2` and `hf:Laxhar/noobai-XL-1.1` — its card says it was trained on the full Danbooru and e621 datasets — removed from the bundled list).
  学習データに未成年の性的表現の分類を含む画像の集まり(e621)を使う無検閲のモデルは外す(安全側)。

- When a Hugging Face repo was renamed, `update` applied the license-source table with the new name from the API
  while re-applying it uses the name in the file URL, so the two disagreed (`license_path`). Both now use the URL's
  name. Found by the first weekly rebuild (Comfy-Org/flux2-klein-9B → vae-text-encorder-for-flux-klein-9b).
  リポジトリの名前が変わったとき、出どころの表をファイルの URL の名前で当てるようにした。
- Tests no longer pin numbers of the bundled list (uncensored count, HEAD check date) so that the weekly rebuild
  can pass them.


## [0.3.0] - 2026-10-21

### Added

- `genmodelfit download <model>` and `gmf.plan_download()` / `gmf.download()` (Issue #31): download all files of a
  model's workflow, with the precision chosen like `recommend` (`--precision`, `--budget GB`), into a ComfyUI folder by
  each file's template folder (`--comfyui-dir`) or the same layout elsewhere (`--output-dir`). `--list` shows the plan
  without any network access. Before downloading it shows every file's license and URL, the total size and the free
  disk and asks for consent (`--yes` to skip). sha256 is checked while writing (a mismatch deletes the file),
  `.part` files resume with HTTP Range, files already there are skipped, and it needs the download + 1 GB free.
  Gated files, uncensored models (unless `--content all|uncensored`) and unknown licenses (unless
  `--include-unknown-license`) are refused; only https URLs on Hugging Face / GitHub hosts are followed (each
  redirect is checked, and URLs that urlsplit and urllib3 would read as different hosts are refused before
  connecting); nothing is written outside the destination. Existing files are hashed again right before they
  are skipped, restarts against servers that ignore Range are capped, and a `.lock` file stops two downloads of
  the same file. URL paths may contain spaces (they are percent-encoded as requests does); spaces, `%` and `@` are
  refused only in the scheme and host, and refusals say whether the URL is malformed or the host is not allowed.
  JSON: docs/schema.md "download".
  モデルを落とす `download` を足した(同意・sha256・続きから・ComfyUI のフォルダ)。

## [0.2.0] - 2026-10-20

Fewer unknowns in the table and a clear meaning for `?` and `-` (Issue #30). The JSON output changes in a way that
can break apps (below), so this is a minor release under 0.x semver (owner's decision).
表の「?」「-」を減らし、使い分けをそろえた版。JSON の形が変わるので 0.2.0 にした(オーナーの判断)。

### Breaking (JSON)

These only concern models whose weight size is unknown (`estimate.unknown_size: true`, `fit_level: "unknown"`):

- `run_mode` (top level and `estimate.run_mode`) is now **`"unknown"`** (was `"none"`). `"none"` now only means
  "does not run" (`fit_level: "too_tight"`). `run_mode_label` is `"?"` (was `"-"`).
- `estimate.vram_gb`, `estimate.vram_offload_gb`, `estimate.ram_gb` and `estimate.vram_sequential_gb` are **`null`**
  (were `0.0`). The Python `Estimate` attributes stay `0.0`; only `Estimate.to_dict()` / `--json` change.
- `memory_available_gb` is now this PC's VRAM (or RAM) instead of `null` (the available amount is known;
  `memory_required_gb` / `utilization_pct` stay `null`).

Migration / 移行:

- Code that treated `run_mode == "none"` as "cannot use" should also handle `"unknown"` (or check
  `fit_level` / `estimate.unknown_size` first, as before).
- Code that summed or compared `estimate.vram_gb` / `ram_gb` must skip `null` (it was a meaningless `0.0` before).
- `fit_label` / `fit_level` and `schema_version` (1) are unchanged; the db.json fields added below are optional.
大きさが分からないモデルだけ: run_mode は unknown、estimate の VRAM / RAM は null、memory_available_gb は
この PC の量。run_mode の none を「使えない」と見ていたコードは unknown も扱う。

### Changed

- **`?` means unknown, `-` means not applicable**, in the table, `info`, the legend and JSON (`docs/schema.md`,
  「Unknown and not applicable」). Prec of an unknown precision is now `?` (was `-`); Mode / Mem% of a model whose
  size is unknown are `?` (Mode `-` stays for "does not run", VRAM `-` for CPU, Mem% `-` without a GPU). A new
  legend line explains both marks.
  「?」は分からない、「-」は当てはまらない。精度が分からないときは「?」(前は「-」)。
- License table: `circlestone-labs/Anima-Official-LoRAs` has a `LICENSE.md` (CircleStone Labs Non-Commercial License
  v1.0, added 2026-05-26) although its card metadata is empty; the table now sets the LoRAs to
  `other` (`circlestone-labs-non-commercial-v1.0`). The four Anima templates are no longer "license unknown" (still not
  auto-recommended: non-commercial). Bundled list: license unknown 47 → 43 entries (no uncensored), 60 → 56 in all.
  Anima の公式 LoRA は LICENSE.md(非商用)があったので表で決めた。

### Added

- **Precision from more sources**, never overriding a precision read from the file header
  (`ModelEntry.precision_source`: `header` / `filename` / `hf_api` / `config` / `workflow`, optional, schema 1;
  also `precision_source` in `models[]` and `workflows[]` of `recommend` / `fit` / `search --json`):
  - `hf_api`: the Hub API's `safetensors.parameters` when one dtype holds ≥ 90 % **and** element count × bytes is
    within 5 % of the entry's main safetensors size in that repo (readable anonymously even for gated repos whose
    file headers return 401);
  - `config`: `torch_dtype` / `dtype` of the `config.json` next to the main file (only when listed and not gated);
  - `workflow`: the official template's main loader node (`UNETLoader` `weight_dtype`, `WanVideoModelLoader`
    `quantization`, the loaded file name, `precision` widgets, …; `workflow.loader_precision`).
  New optional fields `ModelFile.repo_precision` / `repo_precision_source` and `ModelEntry.workflow_precision`.
  Tool weights whose role is not `main` (BiRefNet, Depth Anything 3) now get their header read too.
  Bundled list (no uncensored): precision unknown 25 → 10 (header 3, Hub API 4, workflow 8; csm-1b stays unknown
  because the Hub API total does not match its files; the rest are listed in docs/sources.md with the reason).
  精度をリポジトリ(Hub API・config.json)とテンプレートの読み込みノードからも読み、出どころを残す。
- `sources.repo_precision` (counts per source, `unauthorized`, `size_mismatch`), `sources.huggingface.unauthorized`
  (repos whose Hub API returns 401 — anonymous 401 is the same for private and missing repos),
  `unauthorized_missing` (the ones confirmed not to exist, `repo_precision.KNOWN_MISSING`: `google/gemma-3-12b`) and,
  after `update --inspect-only`, `unauthorized_recheck` (`unauthorized` / `private_or_missing` / `missing` /
  `readable` / `failed`), so the weekly update (#19) shows when Mage-Flow or Lens become public.
  401 のリポジトリを記録し、`--inspect-only` で確かめ直す。無いと確かめた名前は分ける。
- `docs/license_requests.md`: drafts of polite requests to upstream owners whose weights have no stated license or
  a license that conflicts with their base model (not posted; to be sent only after the owner's review).
  ライセンスの記載の依頼の下書き(まだ出さない)。

## [0.1.1] - 2026-10-20

Fixes from the owner's review (Issue #28).
オーナーのレビューの指摘を直した版。

### Changed

- License tracing (#28): a hand-checked source table (`license_sources.py`) maps files in repackaging repos
  (Comfy-Org, Kijai, …) to their original models, using primary sources only (original model card / LICENSE,
  official GitHub, sha256). `update` applies it to files it could not resolve; `apply_license_sources(db)` applies it
  offline. VOID (netflix/void-model) is now unknown (fine-tuned from a CogVideoX-licensed model). `LICENSE_POLICY` 4.
  ライセンスの出どころの表を足した(再配布のファイル → 元のモデル。一次資料だけで確かめたもの)。
- **Wan 2.2 14B and other workflows that run several diffusion models one sampler after another** (high_noise →
  low_noise): the swapped (`GPU~`) and offload estimates count one model (+ its LoRA) at a time, because ComfyUI
  unloads the unused model before loading the next one (`load_models_gpu` → `free_memory`, ComfyUI commit
  b1a247c, cited in `docs/schema.md`). Wan 2.2 14B I2V at 832×480×81: swapped 35.5 → 21.0 GiB, offload 18.1 → 12.3 GiB.
  On 24 GB it now runs on the GPU with parts swapped (`GPU~`, `marginal` at 87 %) instead of `GPU+RAM`; on 16 GB it is
  `marginal` with offload instead of `too_tight`. The sets come from the template graph (`sampler_stages`, new) or
  from `*high_noise*` / `*low_noise*` file names.
- **Precision choice within a model**: among the variants that run on the GPU without offload (Good or better, `GPU`
  or `GPU~`), the highest precision is shown, like llmfit; only when none does, the better level wins as before.
  Z-Image Turbo on 24 GB now shows bf16 instead of int8. The row's score is taken from the same use and the same tier
  (runs without offload = one tier), so it never drops when VRAM grows.
- **Unknown licenses are hidden by default** from `recommend`, `fit`, `search`, `gmf.recommend()` and `gmf.search()`
  (`hidden.license_unknown`); `--include-unknown-license` / `include_unknown_license=True` shows them; `info <id>`
  still shows them.
- **Models whose size is unknown show `?`** (`fit_level: "unknown"`, `fit_label: "?"`) instead of "Too tight";
  `fit` lists them last, `recommend` still hides them.
- **Community templates** come after official ones only within the same auto group and fit level (they used to come
  after every official row, so a Good community model appeared after Marginal ones).
- TTS GGUF repositories (llama.cpp-style runtimes: VieNeu-TTS v3 Turbo, Qwen3-TTS GGUF, Higgs Audio v3 Studio,
  Drama-3) are no longer marked incomplete for lacking a text encoder / VAE, so they are judged.
- OS shown as `Windows 11` on Python 3.10 / 3.11 too (build number ≥ 22000).
- **`fit` is easier to read** (like llmfit's `fit`): it shows only the models that run (Perfect / Good / Marginal)
  and counts the rest by reason in the header; `--all` shows everything (tools, models that do not run, `?`, unknown
  licenses). One table ordered by type → fit level → Score (no auto-first; see the Auto column). New options for
  `fit` / `search`: `--fit perfect|good|marginal|runnable|all` (`--min-fit` still works, not together with `--fit`),
  `--sort type|score|vram|disk|name`, `--all`; `fit --include-tools`. JSON follows the same order. Python:
  `gmf.fit_list()`, and `fit=` / `sort=` on `gmf.search()`.
- Table columns in every command: `Type | Status | Score | Model | …` (Type leftmost, Score left of the name).
- Review fixes: `recommend` ranks "runs without offload" (Perfect / Good on `GPU` / `GPU~`) as one tier, so Z-Image
  Turbo stays first in image on 24 GB after switching to bf16; JSON `level_cap` (`sequential` / `offload` / `cpu`) and a
  legend line explain why GPU~ is at most Good; `--fit` with `--min-fit` is a usage error (exit 2); info and the table
  round Mem% the same way; malformed file headers are reported as header errors.
- Licenses: rows of the source table can override an automatic result (`override`). ChronoEdit's distill LoRA was
  apache-2.0 from the card's metadata but the card says it is governed by the NVIDIA Open Model License; InfiniteTalk
  and Wan 2.1 I2V 480P now point at their real upstreams. Files of lineages whose license cannot be read (VOID) are
  unknown in the data too (as the gate already judged). An entry mixing apache-2.0 and mit files is shown as
  apache-2.0 (more obligations), not mit. `update.LICENSE_POLICY` 4 → 5.

### Added

- Precision from the files themselves: `update` (and the new `update --inspect-only`, `--offline` for the cache only)
  reads the safetensors JSON header / GGUF header of the main weight files with HTTP Range requests (never the
  weights), caches them in `file_headers.json`, and stores `ModelFile.header_precision` / `header_params`. On the
  bundled list, entries with unknown precision went from 97 to 25 (sfw: 90 → 25; the rest are gated repos, 401, or
  have no main file on Hugging Face).
- `ModelEntry.sampler_stages`, `ModelFile.header_precision`, `ModelFile.header_params` (optional; `schema_version`
  stays 1). `sources.file_headers`, `sources.sampler_stages`.
- README: how to install so that the `genmodelfit` command is found (pipx / uv tool, the pip "not on PATH" warning,
  and `python -m genmodelfit`, which always works). Also in `genmodelfit --help`.

### Data

- Bundled list: HEADs re-checked on 2026-10-20T10:33:07Z (valid until 2026-11-19T10:33:07Z); precision from file
  headers; `sampler_stages` from the 277 official templates (19 with several sampler sets).

## [0.1.0] - 2026-10-20

First public release. / 最初の公開版。

### Added

- Hardware detection (`genmodelfit system`, `gmf.system()`): NVIDIA GPUs via `nvidia-smi`, RAM, free disk;
  `--vram` / `--ram` to judge another PC. Other PCs are judged as CPU-only.
- Bundled model list (`db.json`): 323 entries (image, video, music, sound effects, TTS, 3D) built from the official
  ComfyUI workflow templates and the Hugging Face Hub API, folded into 204 models. Hugging Face models need at least
  10,000 all-time downloads (1,000 for trending ones), like llmfit.
- Memory estimates per component and stage, with swapping in turn, offload to RAM and CPU run modes; every estimate
  says whether it is `measured` or `estimated`. Calibration on real runs (`python -m genmodelfit.calibrate`,
  `docs/accuracy.md`).
- Fit levels by memory utilization (Perfect ≤ 60 %, Good ≤ 85 %, Marginal ≤ 98 %, Too tight), like llmfit.
- `recommend`, `fit`, `search`, `types`, `info` with a readable table and `--json` for apps (`docs/schema.md`):
  one row per model (same weights family and size), best precision that fits, score (0–100), per-type sections,
  filters by type, use (`--task`), license, fit level and content.
- Auto-recommendation gate (`genmodelfit gate`, `auto` / `held_reasons`): license allowlist traced to the upstream,
  gating, official workflow without custom nodes, ComfyUI version, anonymous HEAD within 30 days, sha256, known size,
  content `sfw`.
- Content marks: uncensored models are kept in the list with evidence but hidden by default (`content="sfw"`);
  models aimed at sexual content involving minors are excluded.
- `genmodelfit update` (rebuild from upstream, `--heads-only`) and `update --from-release` (signed list from the
  `data-latest` release, Ed25519, rollback protection).
- `genmodelfit bench`: measure peak VRAM / RAM / time on a running ComfyUI and contribute measurements
  (`CONTRIBUTING.md`).
- Python API: `gmf.recommend()`, `gmf.search()`, `gmf.info()`, `gmf.system()`; library functions never print.
- Data freshness: `gmf.data_status()` (`DataStatus`: source, HEAD check dates, `expired`, `expires_at`,
  `auto_count`) and the Data lines of `genmodelfit system` / `update` (`database.status` in `system --json`).
  It uses the gate's own check, so `expired` tells apps when auto-recommendations have dropped.
- Type information (`py.typed`), Python 3.10–3.13 on Windows and Linux.
- Privacy: read-only commands and the Python API never touch the network; HTTP sends a fixed User-Agent and never
  attaches `~/.netrc` credentials. No telemetry (README "Privacy").

### Changed

- License: MPL-2.0 for the code and the bundled data (was MIT before the first release).
  Attribution for the upstream data is in `THIRD_PARTY_NOTICES`.

[0.13.2]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.13.2
[0.13.1]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.13.1
[0.13.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.13.0
[0.12.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.12.0
[0.11.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.11.0
[0.10.1]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.10.1
[0.10.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.10.0
[0.9.2]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.9.2
[0.9.1]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.9.1
[0.9.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.9.0
[0.8.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.8.0
[0.7.1]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.7.1
[0.7.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.7.0
[0.1.0]: https://github.com/laxshmate/genmodelfit/releases/tag/v0.1.0
