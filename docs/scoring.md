# Fairness, order and Score

**English** · [日本語](scoring.ja.md)

The rules for how the `recommend` / `fit` / `search` tables, `--json` and the Python functions use measurements, order the models and set the Score (0.11.0, Issue #95; the level caps and verified GPU kinds are #108).
The memory formula and its coefficients are in [accuracy.md](accuracy.md), the level bands (60 / 85 / 98% use) in [schema.md](schema.md) "Estimate", and whether a model may be recommended automatically in [gate.md](gate.md); this page does not change them.
The model is [llmfit](https://github.com/AlexsJones/llmfit) (2f35825: `llmfit-core/src/fit.rs`, `analysis.rs`, `benchmarks.rs`); where genmodelfit differs, the reason is given.

## Contents

- [Four checks](#four-checks)
- [Overall flow](#overall-flow)
- [Settings used for judging](#settings-used-for-judging)
- [Parts switched off by default](#parts-switched-off-by-default)
- [Level caps](#level-caps)
- [Verified GPU kinds](#verified-gpu-kinds)
- [When measurements set the level](#when-measurements-set-the-level)
- [Shifting values with measurements](#shifting-values-with-measurements)
- [Measurements of templates without a size input](#measurements-of-templates-without-a-size-input)
- [Score](#score)
- [Popularity](#popularity)
- [Order](#order)
- [One row per model: the representative](#one-row-per-model-the-representative)
- [Differences from llmfit](#differences-from-llmfit)
- [Tests](#tests)
- [Not decided yet](#not-decided-yet)

## Four checks

An item goes into the verdict, the score or the order only if it passes all four checks. Anything that fails goes into a mark, a note or a filter instead.

| Check | Condition |
|---|---|
| Explainable | It can be stated in one sentence in the README, and users can check it from the numbers in the table |
| Evidenced | Backed by primary sources, measurements, or llmfit's code and history. Constants without evidence are called "chosen values" and kept to a minimum |
| Fair | The author's PC, whether the author measured a model, the author's app and the date of the run do not change other users' levels, scores or order. Same list, same PC and same options give the same result for everyone |
| Testable | Inputs and expected values can be fixed in an automated test |

## Overall flow

1. PC: VRAM, GPU vendor and generation, system RAM ([hardware.md](hardware.md))
2. Settings: the per-type defaults or the user's options ("Settings used for judging" below)
3. Estimate: the VRAM and RAM each run mode needs ([accuracy.md](accuracy.md)); measurements shift the values
4. Level: pick the run mode (all on GPU only when everything, including the VAE decode memory, can stay loaded; [accuracy.md "Tiles"](accuracy.md#tiles)) and set Perfect / Good / Marginal / too tight from the use. Measurements set the level only when two or more independent sources agree on the same GPU
5. One row per model, then the order, then the Score

## Settings used for judging

**In one sentence**: without options, every model is judged at the same size for its type.

- Settings not given are filled with the per-type defaults (`estimate.DEFAULT_SETTINGS`) whether or not the model has measurements: image 1024x1024, video 832x480 with 81 frames, music 30 s, sound effects and speech 10 s, 3D no size.
- A measurement's settings are used only to work out how far to shift the working memory.
- Up to 0.10, a model with measurements was judged at the measurement's settings when no options were given (the author's app's "fast" quality: Z-Image 768px, SDXL 832px, Wan2.1 512x512 with 49 frames, Wan2.2 5B 640x640 with 73 frames, ACE-Step 20 s). Only the models the author measured were compared at smaller settings, which is not fair, so this was dropped.
- So most of the author's measurements (image, video, music) no longer match the default settings: their `basis` becomes `estimated` (`basis_detail: calibrated`) and the table's VRAM loses the `m`. The 3D and utility measurements match the settings and keep the `m`. The author plans to measure again at the defaults ("Not decided yet").
- An app (such as AI生成キット) that wants its own settings passes them per model: `judge_model(..., settings=...)` or `--width` and so on. No API for a per-type settings table is added (the app's settings differ per model, which a table cannot express).
- Evidence: llmfit also compares every model under the same assumption (context length).
- Test: `tests/test_scoring.py::test_a_settings_do_not_depend_on_measurements`.

## Parts switched off by default

**In one sentence**: parts that the official template does not load with its default values (parts only on muted or bypassed
nodes, on nodes that reach no output node, or on the unselected branch of a switch with a fixed value) are not counted in VRAM
or main memory, and one sentence in the reasons says so only when turning on the switched-off parts would change the level
(0.12.0, #81).

- There are two classes (how they are found: [sources.md](sources.md) "Optional parts"; how they are counted:
  [accuracy.md](accuracy.md) "How files are counted"):
  - `inactive` (muted, bypassed, or reaching no output): not in the prompt and not validated, so counted in none of VRAM, main
    memory, disk and `download`.
  - `switch_off` (the unselected branch of a switch: a prompt enhancer, a speed-up LoRA, …): not executed, so not counted in
    VRAM or main memory. ComfyUI's validation also checks the files on the unselected branch
    (execution.py:946-969, 1061-1085@426f5ee), so they are counted in disk and `download`.
- The upper bound with every switch turned on is also given (`estimate.vram_gb_all_options` and `fit_all_options` in JSON;
  the same as the old value). Only when the level would change does it go into the reasons. Example (RTX 3070 8 GB): "Parts
  switched off by default: qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors (8.8 GB). With all of them on, about
  8.5 GB; on this PC: too_tight". For a switch that swaps parts (Pixal3D / Trellis2) it says "switching loads another part
  (trellis_2_int8_convrot.safetensors, … GB) instead of pixal3d_int8_convrot.safetensors" (it is not an amount added). The list
  of parts is `estimate.optional_parts`.
- #81: Qwen-Image 2.1 (4 entries) added the prompt enhancer part that is off by default (8.8 GB) to the text stage, so even
  with offloading it needed 8.48 GB (106%) on 8 GB: too_tight. Without it, offloading needs 4.96 GB (text to image) /
  5.14 GB (edit): Good. This matches the user's real machine (RTX 3070 8 GB, finished with peaks of 7.1–7.6 GB). The share of
  weights kept on the GPU when offloading (0.4) is unchanged.
- #112 (Wan 2.2 5B too_tight on 8 GB) was already fixed in 0.11.0 by the per-stage working memory (#100) (6.08 GB with
  offloading, Good). It is not changed here; a test keeps it.
- Four checks: the explanation is the sentence above. Basis: ComfyUI's lazy execution
  (comfy_execution/graph.py:153-175, comfy_extras/nodes_logic.py:146-150, 178-188@426f5ee) and the default values of the
  templates the authors published. Fair: decided by the upstream JSON only, independent of the author's PC, measurements, app
  needs or the date of the run (if upstream changes a default, the weekly rebuild follows). Tests: the table below.
- Impact (the bundled list, 329 entries × NVIDIA 6 / 8 / 12 / 16 / 24 GB × main memory 32 / 64 GB, default settings, bundled
  measurements): the level changes in 90 cells of 34 models, and only upwards (0 cells go down, 0 cases where more VRAM lowers
  the level, 0 cells where `basis_detail` changes). Per cell (in parentheses, how many rose from too_tight): 6/32 7(6),
  6/64 7(6), 8/32 5(5), 8/64 5(5), 12/32 12(8), 12/64 12(8), 16/32 11(5), 16/64 11(6), 24/32 12(8), 24/64 8(4). On
  8 GB / 32 GB the ones that rise are the 4 Qwen-Image 2.1 entries and FLUX.2 Klein text to image (all too_tight → Good). The
  upper-bound sentence appears in 53 cells.

## Level caps

**In one sentence**: the level comes from the use of the memory the run mode needs (60 / 85 / 98%), and slow run modes are capped (GPU~ and GPU+RAM at Good, generation on the CPU at Marginal); there is no cap by GPU vendor (#108).

What the levels mean (same as the table legend and the README):

| Level | Meaning |
|---|---|
| Perfect | Everything fits on the GPU with 40% or more of the memory to spare. **Not a speed guarantee** (low-bandwidth machines such as DGX Spark are judged by use as well) |
| Good | 15% or more to spare (swapping parts or offloading is Good when there is room) |
| Marginal | Runs, but the memory is tight (85–98%), or it generates on the CPU (very slow) |
| Does not run | Above 98% (no allocator slack; it does not load in practice). **Exception**: a row whose level comes from measurements can be Good even above 98% (the runs finished; "When measurements set the level" 6) |

Caps per run mode (`estimate.gpu_choice`, `cpu_top`):

| Run mode | Cap | Why |
|---|---|---|
| GPU (all on the GPU) | none | |
| GPU~ (parts swapped in turn) | Good | The parts are loaded again for each stage. Perfect means "everything stays loaded with room to spare" (the same idea as llmfit's "Perfect has room and runs on the GPU") |
| GPU+RAM (part offloaded to system RAM) | Good | Slow, so never Perfect, but Good when there is room (as in llmfit). In the diffusers comparison (PR #11682) offloading slowed Wan by 1.14–1.5× (model-level and group offloading) and FLUX by 3.3–10.4× |
| CPU: CPU-realtime speech (listed) and one-pass tools | Good | "Running on the CPU" below |
| CPU: image, video, music, sound effect and 3D generation | Marginal | "Running on the CPU" below |
| GPU, GPU~, GPU+RAM with the decode (VAE) split into tiles (only the Oobleck VAE, which counts the untiled V; #148) | Marginal | ComfyUI retries in tiles after an OOM, and NVIDIA on Windows spills into shared memory. Both are slow ([accuracy.md "Tiles"](accuracy.md#tiles)) |

### When the decode is split into tiles (#148)

**In one sentence**: the Marginal cap for an untiled decode that does not fit applies only to the Oobleck VAE (ACE-Step 1.5, Stable Audio Open).

- For the Oobleck VAE the measurements are 0.8 to 1.0 times the untiled V (30 s and longer), so V goes into the
  amounts used. Only when V does not fit is the tiled estimate judged, capped at Marginal (GPU+RAM rows too).
- Other VAEs keep the old behaviour: the amounts use the tile value min(V, V_tile), and when V does not fit only a
  note is shown (the level is not changed). For the image KL VAE the measurements are 0.3 to 0.65 times V, so V
  would worsen the prediction. The Wan VAE is left for a separate issue.
- The table VRAM is then the tiled estimate, so it can go down as the length goes up. The level does not go up
  ([accuracy.md "Tiles"](accuracy.md#tiles)).

### When offloading is chosen

**In one sentence**: offloading is chosen only when it raises the level (on a tie the model stays on the GPU).

- If all-on-GPU or swapping parts is Good or better, nothing is offloaded. A row switches to offloading only when all-on-GPU and swapping parts both exceed 98%, or when they are at 85–98% (Marginal, close to OOM) and offloading makes it Good. The reason for the latter reads like "On the GPU alone it is tight at 91% (…), so part of the weights is offloaded to main memory (slow; …)". ComfyUI on NVIDIA also offloads weights with Dynamic VRAM in that situation.
- The level of an offloaded row is the band of the tighter of the offloaded VRAM use and RAM needed ÷ system RAM (the same as the table's Mem%), capped at Good. So it is often set by the amount of system RAM. The offloaded share is a chosen value (40% of the weights stay on the GPU; `Coefficients.offload_fraction`). Above 98% of system RAM the page-file check applies as before (Marginal or does not run).
- llmfit offloads only when all-on-GPU exceeds 98%. genmodelfit does not, because of the promise that more VRAM never lowers the level ([schema.md](schema.md)): with that rule a Good offloaded row would become a Marginal swapped row with slightly more VRAM (136 models in the bundled list, NVIDIA 4–32 GB).
- GPUs other than NVIDIA lack ComfyUI's Dynamic VRAM (comfy-aimdo; NVIDIA on Windows and Linux only per the ComfyUI blog "Dynamic VRAM in ComfyUI"; WSL is not planned), so the reasons for their offloaded rows say "slower still". The level is not changed.
- The limits (the largest resolution and length that are Good on this PC; `limits`) count only rows that are Good or better on the GPU without offloading.
- Impact (0.11.0; bundled list, 32 GB system RAM (64 GB for 24 GB)): rows that switched from GPU / GPU~ Marginal to GPU+RAM Good: 8 at 6 GB, 6 at 8 GB, 18 at 12 GB, 19 at 16 GB, 49 at 24 GB. A GPU Perfect or Good row never switches to offloading (test `test_gpu_perfect_or_good_rows_never_switch_to_offload`).

### Running on the CPU

**In one sentence**: on the CPU only, speech built to run in real time on a CPU and one-pass tools go up to Good, and generation that runs dozens of steps up to Marginal.

The level is the band of system RAM use, capped as follows (`estimate.cpu_top`):

| Type / use | Cap | Examples |
|---|---|---|
| Speech built to run in real time on a CPU (`estimate.CPU_REALTIME_TTS`; table below) | Good | MeloTTS, Piper, VieNeu-TTS, Supertonic |
| All uses are one-pass tools (`tool`, `estimate`, `matting`, `interp`, `segment`, `separate`) | Good | background removal (BiRefNet), depth, pose, segmentation, audio separation |
| Everything else (image, video, music, sound effect and 3D generation; speech whose authors do not say it runs in real time on a CPU; upscaling `upscale`) | Marginal | SDXL, Stable Audio, Fun-CosyVoice3, Chatterbox, VibeVoice, Qwen3-TTS, Kokoro, HiFi-GAN |

Speech built to run in real time on a CPU (the authors' own statements; also shown in the reasons):

| Speech | Matched by | Authors' statement |
|---|---|---|
| MeloTTS | `hf:myshell-ai/MeloTTS-*` | model card: "Fast enough for CPU real-time inference" |
| Piper | run with `piper` | README (tag 2023.11.14-2, github.com/rhasspy/piper/blob/2023.11.14-2/README.md): "A fast, local neural text to speech system that sounds great and is optimized for the Raspberry Pi 4." |
| VieNeu-TTS | `hf:pnnbao-ump/VieNeu-TTS*` | model card: "on CPU everything runs on ONNX Runtime", "RTF < 1, one stream" |
| Supertonic | run with `supertonic` | model card (revision 3cadd1ee): "Supertonic 3 runs fast on CPU, even compared with larger baselines measured on A100 GPU, and uses substantially less memory. It does not require a GPU"; README (commit 1e9799e9, github.com/supertone-inc/supertonic/blob/1e9799e964ea4c0dad7cde993b65c3c813a7b373/README.md): "Watch Supertonic running on a Raspberry Pi, demonstrating on-device, real-time text-to-speech synthesis" |

Speech whose authors do not say it runs in real time on or suits a CPU (Kokoro, HiFi-GAN and others) is not listed and is capped at Marginal like generation.

- Evidence: diffusion generation computes the whole model at every step (20–50 steps). In the Hugging Face blog "Accelerating Stable Diffusion Inference on Intel CPUs", 20 steps of SD 1.5 take 32.3 s on an r7iz.metal-16xl (Sapphire Rapids) in fp32 before optimization. Speech whose authors do not say it runs in real time on a CPU is capped at Marginal (many of them, such as Fun-CosyVoice3, Chatterbox, VibeVoice and Qwen3-TTS, run a language model, flow matching or diffusion for tokens or steps dozens to hundreds of times; Kokoro and HiFi-GAN are smaller, but their authors give no CPU figure). One-pass tools run no steps.
- The upscalers in the list are diffusion families, so upscaling is capped at Marginal like generation (SeedVR2 is one-step diffusion but runs a 3B / 7B DiT at high resolution; SUPIR and Z-Image run dozens of steps).
- llmfit caps the CPU at Good (LLM generation is bound by memory bandwidth and is usable on a CPU). Diffusion generation is bound by compute, so it is separated.
- On a PC with a GPU too, the CPU judgement is used when the GPU judgement is worse (so adding VRAM never lowers the level; on a tie the GPU wins).
- Impact: on CPU-only PCs (32 / 64 GB), 11 rows went from Marginal to Good (3 speech, 8 tools).

## Verified GPU kinds

**In one sentence**: if this kind of GPU has records of running on two or more GPU models, "✓ verified GPU kind" is shown (it is not used for the level).

- Kind = vendor × memory form (dedicated VRAM / shared with system RAM). For example `nvidia/dedicated`, `amd/dedicated`, `amd/shared` (integrated GPUs), `apple/shared`, `nvidia/shared` (DGX Spark). Measurements do not record the memory form, so it comes from the GPU name (GB10 and Apple are shared; AMD and Intel use the integrated flag of the name tables).
- Measurements used: the bundled ones (the author's `measurements.json` and the aggregated rows from other users; this PC's saved bench results are not included) that ran to the end (`outcome: ok`) as standard measurements, without offloading weights to the CPU side (`offload` lowvram, novram or cpu). Aggregated rows must be on a GPU in the name table and not have `runs_evidence: false`. Working-memory-only values (bench with dynamic VRAM) count too: they are records of running to the end on the GPU.
- What is counted is **GPU models** (`estimate.kind_models`): two measurements are on the same model when `same_gpu`'s rule matches (the PCI device ID when both have one, else the normalized name; for dedicated VRAM also a VRAM ratio of 0.95 or more). The author's, this PC's and other users' measurements are treated the same. One model counts once however many measurements or people. Generic names without a device ID do not identify a model and are not counted.
- Measurements with a generic name ("AMD Radeon(TM) Graphics" and the like) are taken as integrated GPUs: by the device ID's table name when there is one, otherwise as the shared-memory kind.
- Only the bundled measurements are used (as in calibrate: the same inputs give the same answer on everyone's PC). With `--vram` it is false, because the assumed GPU is not the measured one.
- Today (0.11.0) only `nvidia/dedicated` is verified (two models: RTX 5080 and RTX 3070). Non-NVIDIA GPUs and DGX Spark have no mark, and their levels follow the same rules.
- Where it shows: after the GPU on the PC line of the tables (`recommend` / `fit` / `search`), on the level line of `info`, and in `system`. Nothing is shown when the kind is not verified. The table legend explains the mark in one line. With `--vram` the GPU is assumed, so nothing is shown.
- JSON: `system.platform_verified` (true / false) and `system.platform_verified_by` (`{"kind", "models"}` when verified, with `models` the names of the GPU models that ran; otherwise null). The library has `gmf.platform_verification(system)`.
- It is not part of automatic recommendation (gate). The gate does not depend on the PC (model information only), and adding it would make automatic recommendations 0 on AMD, Apple and others. Apps decide for themselves.
- Before 0.11.0, non-NVIDIA GPUs and DGX Spark were capped at Good because no measurement had run on their kind of GPU. That cap could only be lifted by submissions from other users, so it became a mark instead (llmfit also shows confidence with marks, not levels). Measurements on the same GPU only raise the level and never lower it (the #95 branch that lowered Perfect to Good from measurements was removed).
- Impact: Good → Perfect for 65 rows on AMD 16 GB, 46 on Apple 16 GB, 87 on Apple 32 GB and 317 on DGX Spark. The level distribution on AMD 16 GB (Perfect 65, Good 117, Marginal 96, does not run 47) is close to NVIDIA 16 GB (69, 113, 96, 47).

## When measurements set the level

**In one sentence**: if two or more independent sources ran to the end on the same GPU as yours with the same settings and agree, the level comes from them (and the amount used for the level is the table's VRAM).

`estimate.measured_level`. The level comes from measurements (`Estimate.fit_from: measurement`) only when all of the following hold, and only when it is better than the estimated level.

1. **Same GPU**: the same rule for the author's measurements and other users' (`estimate.same_gpu`).
   - The PCI device IDs match when both have one; otherwise the normalized names match (generic names never match).
   - For dedicated-VRAM GPUs, the VRAM ratio (smaller ÷ larger) must also be 0.95 or more (`MEASURED_VRAM_TOLERANCE`; "VRAM ratio for the same GPU" below).
   - Up to 0.10, the author's measurements counted on any GPU of the same vendor with at least 0.97 × the measured VRAM (for example, Wan2.2 5B measured on an RTX 5080 was Good / GPU on an RTX 4060 Ti 16 GB). That favoured the author and was dropped.
2. **Same settings**: resolution, frames, length, batch and precision match. Same vendor (`gpu_backend`, else the GPU name). Measured with the current list's files (`files_fp`) on a GPU whose name is not a generic name or placeholder (`gpu_known`; 0.11.0 dropped the name table).
3. **Two or more independent sources** (`MIN_LEVEL_SOURCES`):
   - The author's measurements (`measurements.json`; this PC's saved bench results are treated the same): **however many rows there are for the same model, GPU and settings, together they are one source** (`estimate.count_sources`). The author alone never sets the level.
   - Other users' measurements: one per **group of submissions connected by the same pull request or the same person** (#113; `contrib.submission_sources`): one pull request is at most one source, and one GitHub account is one source however many files, pull requests or issues it sends. The person is a **GitHub account**: for a measurement issue, the issue's author (the bot writes `Submitted-by: github:<account>` and `Submitted-by-id: <the account's numeric id>` in its commit, and the numeric id, which does not change when the account is renamed, is what is counted; older bot commits without the id count by the account name. These lines are trusted only when the merge commit is `Merge pull request #N from laxshmate/measurement/issue-M`, the bot's own branch in this repository, and the commit is the bot's); for a pull request, the account the pull request came from, read from the first line GitHub writes in the merge commit (`Merge pull request #N from <account>/<branch>`; git gives only the name here). The commit authors' e-mail addresses are not used for this, since anyone can write any address in a commit. Measurement pull requests are merged with "Create a merge commit" only; anything else (a commit directly on main, squash or rebase) falls back to the commit author's e-mail address, which is weaker. **An account counts once, but pull requests from a different account, from an organization the person owns, or (for pull requests) after a rename count as different people**, so the maintainer checks the author of each pull request or issue when merging (the bot's pull request says how many submissions the same account already sent for that GPU and model, and has a checklist item for it). Aggregated rows carry the IDs of the sources they used (`source_ids`: the first 12 characters of the sha256 of the group's smallest key, so no address or account name is published, or `file:<first 12 of the content sha256>`), and sources are counted as the **union of the IDs** across rows. One person's submissions split into separate rows (with and without a GPU device ID, or by measuring method `vram_metric`) are still one. Older rows without IDs are not added up; only their largest `n_sources` is used.
   - The author and other users are added up for the same GPU and settings (the author + one submission = 2).
   - With only one source, the level is not set and the values are only shifted ("Shifting values with measurements" below). The reasons say "only one source".
4. **They agree** (`CONSISTENT_REL`; the same definition as `consistent` in `contrib aggregate`):
   - No OOM record: no row with `outcome: oom` for the same setup, no aggregated row with `n_oom`, and none with `runs_evidence: false` (OOM sources at least as many as ok sources).
   - Each aggregated row agrees inside (rows with two or more sources have `consistent: true`).
   - Across rows, the spread (max − min) of the values (with the context added) is within 15% of the median. Aggregated rows are compared by their median.
5. **Amount used for the level** = max(largest agreeing peak + context, weights + fixed part).
   - Context: `torch_reserved` measurements leave out the CUDA / HIP context, so 0.5 GiB is added (`TORCH_RESERVED_CONTEXT_GB`).
   - Weights + fixed part: full load with zero working memory (the same physical floor as for the values).
   - **If peak + context is below weights + fixed part, the level is not set.** A peak below the full-load floor is a record of running with part of the weights kept in system RAM, not evidence that everything fits on the GPU (the same reasoning as Wan2.2 5B below; the values are still shifted). The reasons say it is below the full-load floor.
   - This amount is what the table's VRAM and Mem% show, and JSON's `estimate.measured_level_vram_gb` (and `memory_required_gb`). The raw peak is `estimate.measured_peak_vram_gb`.
   - If the amount exceeds this PC's VRAM, the level is not set.
6. **Level** = the band of this amount ÷ VRAM, at least Good (the runs finished). If any agreeing measurement hit the GPU limit (dynamic VRAM used up the free space), at most Good. The run mode is GPU.

`Estimate.sample_count` is the number of independent sources behind the value (1 for the author's measurements however many rows, `n_sources` for other users, the sum of agreeing sources when measurements set the level). Up to 0.10 it was the number of submissions (`n_used`).

Examples (bundled measurements):
- **Example: Wan2.2 5B on an RTX 5080 16 GB** (with 640x640, 73 frames; the author's two rows, 15.8 / 15.0 GB, are one source together). Weights + fixed part is about 18.4 GB, which cannot all fit in 15.9 GB. The measurement ran to the end with ComfyUI's dynamic VRAM keeping part of the weights in system RAM (offload: dynamic, a value at the GPU limit). So it is a record of "runs by offloading", and measurements do not set the level (with the current per-stage formula it is Good, GPU~, swapping parts). Even with agreeing submissions from other users, the floor of the amount (weights + fixed part) exceeds the VRAM, so measurements never make it a GPU level. From 0.7.0 to 0.10 it was Good, GPU (#26).
- SDXL on an RTX 5080 (with 832px): only one author row, so no level from measurements. One agreeing submission from another user on the same GPU and settings would set it.

The four checks:
- Explainable: the sentence above.
- Evidenced: llmfit carries measurements over only to PCs with the same configuration (`benchmarks.rs:546-558`) and never changes the level from them. genmodelfit uses "ran to the end on the same GPU with the same settings" as stronger evidence than the formula for the level (#26, 2026-10-22), so it asks for more than llmfit: two or more sources.
- Fair: one fake submission, one person submitting many times, or the author alone do not change the level. A fake small value cannot lower the amount below weights + fixed part (for example, "ran in 1.0 GB" for a Flux that needs 22 GB does not make it Perfect on an 8 GB RTX 3070).
- Testable ("Tests").
- Of the 2026-10-22 decision "other users' measurements are used from one submission", the part that sets the level changed (values are still used from one submission).

**With `--vram`**: `--vram` keeps the real GPU's name, so once the VRAM ratio drops below 0.95 it is no longer the same GPU and measurements no longer set the level (for example `--vram 17.5` on an RTX 5080 PC). That is a judgement for a GPU with a different amount, so this is correct. The [schema.md](schema.md) promise "more VRAM never lowers the level" carries this exception.

**VRAM ratio for the same GPU** (0.97 → 0.95 in 0.11.0; `estimate.MEASURED_VRAM_TOLERANCE`)

- **In one sentence**: the same card counts as the same GPU whatever the OS, and variants with another amount (8 GB and 16 GB, for example) count as different GPUs.
- Evidence: Linux CUDA reports the total minus a reservation (5934 MiB = 5.80 GiB for a 6 GB card; an RTX 2060 report on the NVIDIA developer forum). Linux totals are 5.80 / 7.79 / 9.78 / 10.76 / 11.77 / 23.69 GiB; Windows reports 6.0 / 7.99 / 15.92 and so on. For 6 GB, 5.80 ÷ 6.0 = 0.967 missed 0.97. Variants with another amount differ by a ratio of 0.83 or less (RTX 4060 Ti 8 / 16 GB 0.5, RTX 3060 8 / 12 GB 0.67, RTX 2060 6 / 12 GB 0.5, RTX 3080 10 / 12 GB 0.83), well below 0.95.
- Fair: the same value for everyone's measurements; Linux and Windows owners of the same card share each other's measurements.
- Test: `tests/test_scoring.py::test_same_vram_ratio_linux_and_windows` (Linux 5.80 matches Windows 6.0; the 8 GB and 16 GB variants never match).

**Non-NVIDIA GPUs**: the same conditions. Since 0.11.0 there is no level cap by GPU vendor ("Verified GPU kinds" above), and measurements only raise the level ([hardware.md](hardware.md)).

## Shifting values with measurements

**In one sentence**: for a model with measurements, the formula's working memory is shifted to match them, on any GPU.

- Unchanged ([accuracy.md](accuracy.md) "How measurements are used"). Other users' measurements are used from one submission on PCs with the same GPU.
- Only the models the author measured get more accurate values, and the shift can change the run mode or level (for example HY 3D 2.0 (`3d_hunyuan3d_image_to_model`) on an RTX 4060 8 GB is Good / GPU (6.7 GB) with the measurement and Good / GPU~ (full load 6.85 GB) without). This is accepted as the other side of making values accurate; the table's `m` marks values that come from measurements (the author's and other users' measurements are not told apart by the mark; #108 dropped `c`).
- Since #100 a negative shift is clipped at 0 per stage (never below weights + fixed part), a positive shift goes to the all-on-GPU value only, and a shift from a measurement pinned at the GPU limit goes to all three ([accuracy.md](accuracy.md) "How measurements are used" 1).
- Evidence: llmfit also corrects estimates with measurements (one ratio from a machine's measurements for every model; genmodelfit shifts per model, by about −1 to +2 GB with the per-stage formula of #100).
- Test: `tests/test_scoring.py::test_d_values_shift_by_at_most_the_delta` (the difference with and without measurements is within the shift).

## Measurements of templates without a size input

**In one sentence**: a model whose size comes from the input image is judged without a size.

- Added in #90 (`estimate.NO_SIZE_INPUT`, a table the maintainer checked). The judging settings have no size, so measurements that bench writes without a size (BiRefNet, MoGe) match and are used.
- Test: `tests/test_scoring.py::test_g_measurements_without_size_are_used`.

## Score

**In one sentence**: the Score is this PC's memory headroom: 100 while the tightest resource of the run mode is at most 70% used, lower above that (speed, quality and popularity are not in it).

### Formula

```
r = use (table below; unrounded)
memory_fit = 100                                     (r ≤ 0.70)
           = 100 × exp(−0.5 × ((r − 0.70) / 0.20)²)    (r > 0.70)
Rows that run (Perfect / Good / Marginal) never go below 37.5
Rows that do not run or are not judged are 0
Score = memory_fit rounded to one decimal (Python's round); the table shows an integer
```

r per run mode (the table's Mem% and JSON's `utilization_pct` are this r):

| Run mode | r |
|---|---|
| GPU | full-load VRAM ÷ VRAM |
| GPU~ | largest VRAM when the parts are swapped in turn ÷ VRAM |
| GPU+RAM | max(offloaded VRAM ÷ VRAM, RAM needed ÷ system RAM) |
| CPU | RAM needed ÷ system RAM |
| Level set by measurements | amount used for the level ÷ VRAM |
| Any run mode short of system RAM | max(the value above, RAM needed ÷ system RAM) |

- Examples: r = 0.60 → 100.0, 0.80 → 88.2, 0.85 → 75.5, 0.90 → 60.7, 0.98 → 37.5.
- The constants 0.70 and 0.20 are llmfit's `fit_score` (`fit.rs:2335-2349`). llmfit PR #648 made the curve one-sided (free space is not a loss) and PR #100 removed the cliff. The input r is, as in llmfit, "the share of the tightest resource of the run mode".
- The floor 37.5 is the value at r = 0.98 (the edge of Marginal). It covers rows that run although r exceeds 0.98: levels set by measurements, and rows short of system RAM that run with the page file (not only CPU but also GPU, GPU~ and GPU+RAM; r is then the RAM share and Mem% can be 110–130%; the level is Marginal).
- Relation to levels: Perfect (GPU, r ≤ 0.60) is always 100. Good (GPU) is 100 to 75.5, Marginal (GPU) 75.5 to 37.5. GPU~ and GPU+RAM are capped at Good and generation on the CPU at Marginal, so a high Score does not mean fast.
- The Score barely matters for the order (levels and popularity come first). Its job is to show the memory headroom as one number. `--sort score` orders by it.
- With one row per model, the Score is **the representative's value** (it matches the table's Mem%). The "highest score among the members" of 0.10 was dropped.
- JSON: `score` (= memory_fit) and `score_components = {"memory_fit": …}`. The keys up to 0.10 (`fit`, `headroom`, `readiness`, `basis`, `popularity`) were removed (no reuse of a name with a new meaning).

### The promise changed

Up to 0.10 the promise was "more VRAM or RAM never lowers the Score". The new Score can drop when more VRAM switches the representative to a heavier precision or run mode (for example Z-Image Turbo (bf16): 100 on GPU~ at 25 GB, 78.7 all on GPU at 26 GB; llmfit behaves the same). The promise now covers levels only (with the `--vram` exception above). A test checks that more RAM never lowers the Score.

### Left out

| Candidate | Verdict | Reason |
|---|---|---|
| Level points (45% in 0.10) | Removed | The order already uses the level first, so it would count twice. llmfit's Score has no level either |
| Popularity (10% in 0.10) | An order key | Mixing it in needs weights (chosen values) and blurs what the score means (fit or popularity) ("Popularity" below) |
| Readiness (20% in 0.10) | Removed | It is the condition for the author's app to recommend automatically, unrelated to the fit to the PC. Handled by the `--json` order and `--auto-only` |
| Measured or not (10% in 0.10) | Removed | Models the author measured ranked higher in everyone's table. Used only for shifting values and the marks |
| Precision penalty | Not added | The order (16-bit ≳ 8-bit > 4-bit) is supported by the FLUX.1 paper (SVDQuant arXiv 2411.05007), point values are not. Only the rule that picks the higher precision within a model ("One row per model") |
| Size | Not added | No source relates size and quality across different generation model families |
| Recency | Not added | The list only has template creation dates. It changes with the run date (a weakness of llmfit) |
| Family reputation | Not added | A subjective table (llmfit's has no source either) |
| Speed (seconds) | Not now | No formula or calibration yet ("Not decided yet") |

## Popularity

**In one sentence**: popularity is the rank (0–100) of how much a model is used, counted from the same place within the same type, and it is only an order key (not in the Score).

`popularity.py`; JSON `popularity`.

- The unit is the model (a row: all workflows of the same model; `--all-workflows` rows carry the model's value). The count is the largest among the members. Community templates count too (they use the same model).
- Two sources, never mixed because their numbers mean different things:
  - ComfyUI templates: `usage` in index.json (described upstream as "Usage count"; period and unit are not published: Comfy-Org/workflow_templates `templates/index.schema.json:144`, `scripts/sync/sync_data.py:1252`).
  - Hugging Face: all-time downloads (`downloads_all_time`). Manually added models (manual) with a Hugging Face repository join this source.
  - A model with members in both (for example Stable Audio Open for sound effects) uses the template count.
- Rank = the percentile (mid-rank) among all models of the same type and source in the list used for judging (bundled or after `update`; before any filters, uncensored included):
  `100 × (models with a smaller count + models with the same count including itself ÷ 2) ÷ models in the pool`
  - Example: five models with counts 10, 20, 20, 40, 0 → 30 / 60 / 60 / 90 / 0.
- Models without a count or with 0: 0 (the bottom). They join the pool as count 0.
- Manually added models (manual; 3 today) have no count, so their rank is 0 (the bottom). No count is fetched over the network. Only the author's own additions are disadvantaged, so fairness holds.
- Sources with fewer than 5 models in a type: models with a count get 50 (rank differences would not mean much).
- Comparing template ranks and Hugging Face ranks with the same key is a chosen value ("treat the top of each source alike").
- Up to 0.10 it was 10% of the Score and Hugging Face downloads (30 days) were divided by 100 to match the template counts. That conversion was dropped.
- The four checks: explainable / evidence partial (the meaning of the counts is not published, but usage counts are more objective than llmfit's reputation table) / fairness partial (checked below) / testable.

**Bias check**: since most users have ordinary PCs, lighter models might become more popular. In the bundled list (2026-10-27), the Spearman correlation between the full-load VRAM (default settings) and the usage count of template models is −0.05 for image (78 models), +0.22 for video (63), +0.48 for music (10) and +0.59 for 3D (9). No "lighter is more popular" bias shows.

The weekly update (`python -m genmodelfit.weekly`) computes the same correlation for types with 20 or more template models ("人気のかたよりの確認" in the PR body) and adds a warning when it falls below −0.3 (a signal to consider dropping popularity as an order key; `popularity.vram_bias`). The warning also goes into the result's `warnings` and makes `merge-check` not safe (`popularity_bias:<type>`), so that week is not merged automatically and the owner reviews it. Music, 3D and speech have too few models for this check.

## Order

### The `recommend` table

Keys in order:
1. Runs → not judged → does not run
2. Level: fits without offloading (Perfect / Good with GPU or GPU~) → Good with GPU+RAM or CPU → Marginal
3. Within Marginal only: run mode GPU → GPU~ → GPU+RAM → CPU (fastest first). Not used among rows that fit without offloading (so that the 24 GB Z-Image row whose representative is bf16 Good / GPU~ does not sink, #28)
4. Official templates → community templates
5. Popularity rank, highest first
6. Score, highest first
7. ID (fixes ties independently of the input order; llmfit uses the name)

Why (difference from llmfit): llmfit only puts models that do not run last and otherwise orders by Score. genmodelfit looks at the level first. The level is the answer to "does it run" and matters more than the Score (memory headroom) and popularity (#84).

### `fit` / `search`

- Default (`--sort type`): type → level (Perfect / Good / Marginal / not judged / does not run) → official → popularity → Score → ID
- `--sort score`: key 1 → level (fits without offloading / Marginal) → Score → popularity → ID
- `--sort vram`, `disk`, `name`: key 1, then that value (unchanged)

### `--json` and the library

- After key 1, models that may be recommended automatically (`auto: true`) come first, because the app shows recommendations without a person checking. The conditions (such as the license allowlist) can be changed with `GatePolicy` / `--license-allowlist`.
- Key 1 (runs → not judged → does not run) now matches the table (up to 0.10 the library put "does not run" before "not judged").
- The table (`recommend` text output) does not use automatic recommendation for the order, so the date (link-check age) and the allowlist do not change the table's order or the Score.

## One row per model: the representative

Unchanged ([schema.md](schema.md) "Models"): use order → among entries that fit without offloading, the highest precision → otherwise the best level → on the same level, the run mode without offloading (`recommend.variant_key`). The precision order agrees with "16-bit ≳ 8-bit > 4-bit".

For #108 (offloaded rows up to Good) the representative changes were counted (bundled list; NVIDIA 6 / 8 / 12 / 16 / 24 GB, AMD 16 GB, Apple 16 / 32 GB, DGX Spark, CPU-only 32 / 64 GB). Four models changed (Z-Image at 8 GB: bf16 Marginal → int8 Good; FLUX.1 schnell at 12 GB: bf16 Marginal → fp8 Good; Wan 2.2 14B at 24 GB: i2v GPU~ Marginal → flf2v Good; SCAIL at 24 GB: fp16 Marginal → int8 Good; each to a better level among offloaded rows), and a bf16 offloaded row beating an int8 GPU row never happened. A Good offloaded row placed above a GPU Marginal row also never happened on these PCs (a GPU Marginal row becomes an offloaded row when offloading makes it Good). Nothing looked unnatural, so the selection is unchanged.

## Differences from llmfit

| Point | llmfit | genmodelfit | Reason |
|---|---|---|---|
| Score contents | quality, speed, fit, context length | fit only | There is no evidenced way to measure quality and speed for generation models yet; added once there is |
| Order | does not run last → Score | runs → level → … → popularity → Score | "Order" |
| Caps for slow run modes | offload and CPU up to Good | GPU~ and GPU+RAM up to Good; on the CPU, speech and tools up to Good, generation up to Marginal | "Level caps". Diffusion generation is bound by compute and very slow on a CPU (LLM generation is bound by memory bandwidth) |
| When offloading is chosen | when all-on-GPU exceeds 98% | when offloading raises the level | The promise that more VRAM never lowers the level ("When offloading is chosen") |
| GPU kind | same levels, confidence as a mark | same levels, ✓ for verified kinds | "Verified GPU kinds" |
| Measurements and levels | never change the level | set the level when two or more independent sources agree on the same GPU and settings | "When measurements set the level" |
| Same hardware | CPU name + GPU name | GPU device ID or name + VRAM ratio | The CPU is not used for judging |
| Weights not loaded for inference | speculative-decoding draft heads (EAGLE, …) are not counted in the main size and are kept out of the ranking (`SanitizationReason::SpecDecodeDraft` in `llmfit-core/src/models.rs`); no note on the amount if they were used | parts the template does not load with its default values are not counted in VRAM or memory, and one sentence says so only when turning the switch on would change the level | "Parts switched off by default". In generation templates one switch adds a part, so the amount with it on is needed too |

## Tests

`tests/test_scoring.py` (and existing tests whose expectations were updated).

| What | Test |
|---|---|
| memory_fit fixed values (r = 0.50–0.98), no cliff, floor for rows that run, 0 for rows that do not | `test_memory_fit_fixed_values`, `test_memory_fit_shape_floor_and_not_runnable` |
| Each row of the r table (including GPU+RAM with RAM tighter and RAM short) | `test_usage_ratio_per_run_mode` |
| The Score dropping with more VRAM; the level does not | `test_score_can_drop_with_more_vram`, `tests/test_invariants.py` |
| Popularity rank example, small pools, sources not mixed, manual | `test_popularity_percentile_example`, `test_popularity_sources_are_not_mixed`, `test_popularity_is_per_model_and_in_json` |
| Bias check (Spearman) | `test_spearman_and_bias_check`, `tests/test_weekly.py` |
| Each order key (level, Marginal run mode, official, popularity, Score, ID, automatic recommendation), `--sort score` | `test_recommend_order_keys`, `test_fit_sort_type_and_score` |
| (a) Settings do not depend on measurements | `test_a_settings_do_not_depend_on_measurements` |
| (b) No level from measurements on other GPUs (RTX 4060 Ti 16 GB, RTX 4090) | `test_b_no_measured_level_on_other_gpus` |
| (c) Same GPU and settings, two agreeing sources set the level | `test_c_measured_level_on_the_same_gpu` |
| (d) Value differences within the shift | `test_d_values_shift_by_at_most_the_delta` |
| (e) The run date does not change the table order or Score | `test_e_run_date_does_not_change_table_order_or_score` |
| (f) The allowlist does not change the table order or Score | `test_f_allowlist_does_not_change_table_order_or_score` |
| (g) Measurements without a size are used | `test_g_measurements_without_size_are_used` |
| A fake 1.0 GB Flux is not Perfect; one person's four files do not set the level; the author + one submission do; two author rows are one source | `test_fake_small_submission_does_not_make_flux_perfect`, `test_one_source_with_many_files_does_not_set_the_level`, `test_author_one_and_community_one_agree`, `test_author_rows_are_one_source` |
| Offloading up to Good, only when it raises the level; GPU Perfect / Good rows never switch; system RAM counts; limits use GPU rows only (#108) | `tests/test_levels.py` (`test_offload_is_capped_at_good_and_chosen_only_when_it_raises_the_level` and others) |
| CPU caps (speech and tools Good, generation Marginal); the RAM band | `tests/test_levels.py::test_cpu_only_caps`, `test_cpu_top_table`, `test_cpu_level_uses_the_ram_band` |
| Parts switched off by default: counted when reached from an output node in the type table (inside a subgraph, on a lazy branch); a Soft Switch whose selected side is unconnected uses the other side; the contents of a nested bypassed instance are used; parts on nodes that reach no output are not downloaded either; a bypassed node's own files are not counted; the #81 values (4.96 / 5.14 GB, Good on 8 GB); FLUX.2 Klein; #112 (Wan 2.2 5B Good on 8 GB); the upper-bound sentence; no level goes down | `tests/test_optional.py` |
| #148: the Oobleck VAE's working memory grows with song length; the tile fallback is capped at Marginal; no fallback on the CPU; other VAEs unchanged; adding VRAM never lowers the level | `tests/test_issue_148.py` |
| Verified kinds (true for NVIDIA dedicated VRAM, false for AMD, Apple, GB10; one model with many measurements is not verified, two models are, whatever the origin); ✓ only when verified; the gate ignores the kind; AMD and NVIDIA distributions are close; more VRAM or RAM never lowers the level | `tests/test_levels.py` |

`SCHEMA_VERSION` (the shape version of the bundled and released db.json) is unchanged. The meaning of the output changed, so the package is 0.11.0 ([CHANGELOG.md](../CHANGELOG.md)).

## Not decided yet

1. Measure the author's models again at the default settings (image 1024x1024, video 832x480 with 81 frames, music 30 s). Those get their `m` back.
2. A speed (seconds) estimate: GPU compute × size × resolution and frames × steps, calibrated with measurements (a separate spec).
3. Quality measurements: differences per precision with the same seed (LPIPS etc.) with bench. A precision penalty comes after that.
4. Measurements of offloading speed (compare times with `--disable-dynamic-vram` + lowvram in the #102 measurements), to check the Good cap for offloaded rows.
5. The popularity bias check for music, 3D and speech (once there are more models).
6. Record in bench and contrib measurements which optional parts were loaded (a switch turned on, a muted node
   enabled). For now measurements are treated as taken with the template's default values, and the shift is decided
   with the parts loaded by default. A measurement taken with a switch on would make the working memory look larger.
