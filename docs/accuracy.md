# Estimate accuracy

**English** · [日本語](accuracy.ja.md)

This page explains how genmodelfit's VRAM and memory estimates are built, which measurements they are fitted to, and how far they can be trusted.
It is for people who read the estimates, change the numbers, or submit measurements.

As of 2026-10-28, 0.12.0 (how other users' measurements are handled: 2026-10-22; the working-memory formula became per-stage on 2026-10-27: #100).
The coefficients are in `src/genmodelfit/data/calibration.json`; rebuild them with `python -m genmodelfit.calibrate`
(`--dry-run` prints the tables without writing). The tables in "Results (leave-one-out)" come from that command's output.
**All units are GiB (2^30 bytes).** "GB" in the tables and the CLI also means GiB.

## Contents

- [Summary](#summary)
- [Formulas](#formulas)
  - [Per-stage working memory (#100)](#per-stage-working-memory-100)
  - [Tiles](#tiles)
  - [How families are mapped](#how-families-are-mapped)
  - [Models without a per-stage formula](#models-without-a-per-stage-formula)
  - [Main memory (RAM)](#main-memory-ram)
  - [Models of unknown size](#models-of-unknown-size)
- [How files are counted (agreement with #2)](#how-files-are-counted-agreement-with-2)
- [How measurements are used](#how-measurements-are-used)
- [Fitting](#fitting)
- [Results (leave-one-out)](#results-leave-one-out)
- [Known limits](#known-limits)
- [Other users' measurements (#34, 2026-10-21; handling aligned with llmfit on 2026-10-22)](#other-users-measurements-34-2026-10-21-handling-aligned-with-llmfit-on-2026-10-22)
  - [Bench on non-NVIDIA GPUs (0.9.0)](#bench-on-non-nvidia-gpus-090)
- [Cross-check (measurements and public figures used neither for fitting nor for levels)](#cross-check-measurements-and-public-figures-used-neither-for-fitting-nor-for-levels)

## Summary

- **Since #100 (2026-10-27) the working memory is ComfyUI's authors' own estimate (the formulas in the code), per stage** ("Formulas" below).
  The only coefficient still fitted to measurements is the one for 3D among the models without a per-stage formula (`act_3d_gb`).
- Against the 4 unpinned RTX 5080 measurements of the maintainer (peaks on a GPU with room), the differences are Z-Image int8 +5.1%, SDXL +12.5%,
  Wan 2.1 1.3B −14.7%, ACE-Step 1.5 +17.8% ("Results" below; the ACE-Step measurement is below the weights floor and says nothing about the working memory). The formula is not fitted to them, so this is a cross-check, not a fit.
  - 4 measurements, one kind of GPU and dynamic-VRAM peaks, so it does not show that the target is met.
  - For Wan 2.1 1.3B the measurement is 1.96 GB above the formula (VAE at its tile value). It is a peak on a GPU with room, where dynamic VRAM
    kept the parts loaded and decoded the VAE without tiles (with the untiled VAE the formula gives 14.04 GB, +5.6%).
    This positive shift is added to the all-on-GPU value only ("How measurements are used" 1 below).
- The 2 3D measurements miss by +6.4% and −4.6% in leave-one-out (fit on all but one, then predict the one left out).
- **The 3 pinned measurements (Z-Image bf16, Wan2.2 5B × 2) carry no information.**
  - The weights alone already exceed the measurement, so "prediction ≥ measurement" holds by construction and cannot fail.
- As a baseline, predicting from "the sum of file sizes only" misses by 22.8% on average over the 6 unpinned measurements.
- **The swapped-in-turn (`vram_sequential_gb`) and offload (`vram_offload_gb`) values and main memory (`ram_gb`) have no measurements and are not verified.**
  A bench that measures per-stage peaks is needed (future work).
- The RTX 3070 sweep (2026-10-27) is not used as evidence: the GPU was pinned at its limit and other work used it in some runs
  ("Cross-check" below; to be measured again).

## Formulas

ComfyUI runs "read the text (text encoder) → generate (main model) → finish (VAE)" in turn.
So the weights are split by stage and, together with the per-stage working memory (#100, 2026-10-27; see "Per-stage working memory (#100)" below),
three quantities are computed (`estimate.py`, `working.py`).

| quantity | formula | meaning |
|---|---|---|
| `vram_gb` (all on GPU) | fixed + weights of all parts + max(S, V_eff, T) | all weights stay loaded, plus the working memory of the heaviest stage |
| `vram_resident_gb` (kept loaded) | fixed + weights of all parts + max(S, V, T) (V untiled) | used only to decide whether all-on-GPU can be chosen (it is not chosen above 98%; "Tiles" below, #102) |
| `vram_sequential_gb` (swapped in turn) | fixed + max(text-stage weights + T, denoise-stage weights + S, decode-stage weights + V_eff, other) | parts are unloaded between stages. A bit slower but usable |
| `vram_offload_gb` (offload) | fixed + max(weights of each stage × 0.4 + working memory of that stage) | runs with part of the weights left in main memory (lowvram / dynamic VRAM). Slow |
| `ram_gb` (running on the GPU) | 3.0 + weights of the largest stage | for all on GPU and swapped in turn (see "Main memory (RAM)" below) |
| `ram_gb` (offload, or CPU) | 3.0 + weights of all parts | the offloaded weights stay in main memory |

- S, V_eff and T are the working memory of the denoise, decode and text stages ("Per-stage working memory (#100)" below).
- Models that cannot be mapped per stage ("Models without a per-stage formula" below) use the earlier formula for the whole model
  (the working memory from the per-modality coefficients, the same amount in the denoise and decode stages, 0 in the text stage). Stages are never mixed.
- The fixed part stays at 1.472 GiB fitted in 0.10 (not refitted; see "Fitting" below).
  It is close to ComfyUI's own reserve of 1.2 to 1.5 GiB (`minimum_inference_memory` = 0.8 GiB + `EXTRA_RESERVED_VRAM`: 0.6 GiB on Windows (0.7 on GPUs over 15 GB),
  0.4 elsewhere; model_management.py:877-891@426f5ee).
  With dynamic VRAM (below) this reserve means something else (weights are loaded and evicted on demand, so it no longer guards free space).
- The floor of #95 (weights + fixed part) is the value with S = V = T = 0; its definition is unchanged.

**How weights are assigned to a role.** The first rule that matches decides; if none matches, the file is the main model.

1. ComfyUI's location (`directory`)
2. An explicit `role`
3. The HF folder name
   - `unet` / `transformer` → main model
   - `text_encoder*` / `tokenizer*` → encoder
   - `vae*` → VAE
   - `image_encoder` → image encoder
   - `controlnet` → same stage as the main model
4. A known file name
   - Text encoders: `umt5` / `t5xxl` / `clip_l` / `clip_g` / `qwen_2.5_vl` / `qwen_3_4b` / `gemma` / `llama_3` and so on
   - VAE: `vae`

Notes:

- Short words such as `qwen` are not used for matching (`qwen_image_fp8_e4m3fn.safetensors` is the main model).
- Parts that sit on the GPU together with the main model (`controlnet`, `model_patches`, `loras`, and so on) go into the denoise stage.
- Audio encoders (`audio_encoders`) run before generation, so they go into the reading stage.

**All-in-one `checkpoints` in a single file:**

- Split by the breakdown counted from the tensor sizes in the safetensors header (SDXL / ACE-Step 1.5 / Hunyuan3D 2.0 / 2.1; read on 2026-10-20).
- These are split as main model 75%, encoders 20%, VAE 5% (these shares are not verified):
  - All-in-one files with an unknown breakdown.
  - Files with no per-file size, only a template size.

### Per-stage working memory (#100)

**Basis**: the amount ComfyUI's authors expect each model to use as working memory (the formulas in ComfyUI's code; a primary source).
ComfyUI's code (GPL-3.0) is neither imported nor copied. Only the numbers and the shape of the formulas are kept in the tables of
`src/genmodelfit/working.py`, with sources as `file:line@426f5ee` (v0.43.0), one key per row. Differences from 0.40.0 (the product's engine) are noted.

- Since ComfyUI 0.40 the default on NVIDIA (and AMD with ROCm 7.14 or later) is dynamic VRAM (comfy-aimdo), and ComfyUI's expected amount
  does not decide how the model is placed (weights are loaded and evicted on demand; main.py, model_patcher.py, model_management.py).
  So this does not claim "the same formula as ComfyUI predicts how it runs". It only claims "the amount ComfyUI's authors expect".
  Predicting how it runs is meaningful only for the older behaviour (`--disable-dynamic-vram`, torch below 2.8, ROCm below 7.14, Intel, Apple).
- **No correction factor is applied.** The image VAE figure is about 1.8× the measurement (the RTX 3070 sweep), but with so few points it is kept as the safe side.

**S (denoise stage)**: `S = batch × area × 2 bytes × 0.01 × factor MiB` (the SDPA-path formula, model_base.py:424-446@426f5ee).

- 1× (one pass). ComfyUI batches cond and uncond together only when `memory_required × 1.5 < free`, otherwise it runs them one by one
  (samplers.py:273-284). What it needs to run is 1× (`minimum_memory_required`, sampler_helpers.py:178).
- Area = the number of latent elements without channels (image (H/s)·(W/s), video latent frames × (H/s) × (W/s), audio latent frames).
- Families that pass packed latents (LTXAV of LTX-2 / 2.3 and MiniMax H3; NestedTensor of video and audio) also count the channels and
  the audio latent in the area (samplers.py:1281-1283, utils.py:1445-1453).
- For editing uses (`i2i`, `relight`), families whose memory estimate includes reference latents (`ref_latents`: Flux, Flux2, Qwen Image,
  Z-Image, Lumina2 and others; `memory_usage_factor_conds` in model_base.py) add the area of one input image.
- The factor is `memory_usage_factor` in supported_models.py ("How families are mapped" below).
- **The SDPA formula for every vendor.** Apple (MPS), CPU and unsupported AMD GPUs use split (sub-quadratic) attention, but ComfyUI itself says its
  split-path figure (× 0.15) is "too aggressive" for an implementation that splits to fit the free memory (model_base.py:444), so it is not used.
  On those GPUs the reasons only say "the working memory can be larger" (the level is not changed).
  AMD uses SDPA on gfx90a, 942, 950, 1100, 1101, 1150, 1151, 1170, 1171, and gfx1200, 1201 with ROCm 7 or later
  (model_management.py:527-532, `estimate.AMD_SDPA_TARGETS`).
- Doubled on GPUs that compute the denoise stage in fp32 (docs/hardware, "Precision").

**V (decode stage)**: each VAE's `memory_used_decode` / `memory_used_encode` (sd.py).

- For one image (ComfyUI splits the batch by free ÷ V; sd.py:1282-1283). The batch size is not applied.
- Uses that turn an input image, video or audio into latents (`i2i`, `i2v`, `v2v`, `inpaint` and so on) also count encoding:
  V = max(decode, encode). The encoded input is the whole video for whole-video uses (`v2v`, `control`, `inpaint` and so on) and for Wan
  (which encodes all frames even for image to video: nodes_wan.py:48, 54), otherwise one image.
- VAE dtype: VAEs without fp16 in working_dtypes (KL, Flux2 (0.40), LTX, Mage) use 4 bytes (2×) on GPUs without bf16.
  float32-only VAEs (such as the LTX-2 audio VAE) use 4 bytes on every GPU.
- On AMD the KL VAE (also the Flux2 VAE) is × 2.73 (`VAE_KL_MEM_RATIO`, sd.py:503-506). The reasons say so.
- **The Flux2 VAE is × 4** (the KL formula on the 16× latent, × 4). 0.40.0 always multiplies by 4 (sd.py:693@0.40.0);
  426f5ee uses × 1 on GPUs that can use the fused norm (sd.py:698). The product's engine is 0.40, so × 4 is used and the reasons say so.
- The VAE of each family: "How families are mapped" below. LTX-2 / 2.3 also take the larger of the audio VAE (float32, sd.py:991-992),
  and MiniMax H3 the larger of its audio VAE (sd.py:1089-1096).

**T (text stage)**: only text encoders for which ComfyUI estimates working memory (`memory_estimation_function`, sd.py:469-474). 0 for the rest.

| family | formula (bf16 GPU; doubled without bf16) | source |
|---|---|---|
| ACE-Step 1.5 LM | 5 × seconds tokens × 0.4375 MiB (0.5625 for the 4B LM) × 0.5 | text_encoders/ace15.py:243-246, 323-326, 384-391 |
| LTX-2 / 2.3 Gemma | at least 642 tokens × 6 MiB × 0.5 ≈ 1.88 GiB | text_encoders/lt.py:246-256 |
| YuE2 | with the song-stage length L = seconds × 25 + 2: (2 × 28 × 8 × 128 × L + L² + L × 34816) × 2 bytes | text_encoders/yue2.py:129-137 |

- The template's default token counts are unknown, so the minimum is used (the YuE2 score stage, 8192 tokens by default, is not included; future work).
- Ming's image encoder (text_encoders/ming_image.py:332) also has an estimate, but Ming has no VAE formula and cannot be mapped (earlier formula).

### Tiles

The V put into the amounts used (all on GPU, swapped in turn, offload) is **V_eff = min(V, V_tile)**. Before decoding, ComfyUI tries
to free V (sd.py:1278-1280@426f5ee); if it does not fit, other models are unloaded, and if an OOM still occurs the VAE is split into tiles
(sd.py:1269-1357, 1422-1474). V_tile is the value of the **smallest tile, independent of VRAM** (adding VRAM never lowers the level).

Whether the all-on-GPU mode can be chosen is decided with the **untiled V** (#102, 2026-10-28): if the kept-loaded amount
`vram_resident_gb` = fixed + weights of all parts + max(S, V, T) exceeds 98%, ComfyUI unloads the model before decoding, so all-on-GPU
is not chosen and the row becomes swapped in turn (up to Good) or offload. On 21 runs on an RTX 5080 16GB this predicted whether ComfyUI
unloaded part of the models in 20 (12 with V_eff; "Cross-check" below). The amounts used do not take V because the working memory
actually used is 0.5 to 1.0 times V, which would worsen the peak prediction (V is ComfyUI's estimate for deciding how to load). A
measurement shift d is added only when negative (a measurement with a positive shift already contains the VAE peak). Rows whose level
was decided by measurements do not use it. A VAE whose estimate is reliable (`comfy_decode_estimate_is_reliable`, e.g. SeedVR) is
tiled from the start without waiting for an OOM (sd.py:1286-1291@426f5ee); the others behave as above.

**VAEs that count the untiled V (#148, 2026-10-31)**: only for the Oobleck VAE of ACE-Step 1.5 and Stable Audio Open,
the V put into the amounts used is the **untiled V** (`working.VaeWork.untiled`).

- Explanation: the longer the song, the more working memory the decode (VAE) needs. The formula now grows the same way.
- Evidence: V = 1000 × L × 2048 × 2 bytes (sd.py:735@426f5ee), L = round(seconds × 48000 / 1920) (nodes_ace.py:105;
  the ComfyUI node defaults to 120 s: nodes_ace.py:95). ComfyUI first decodes without tiles (sd.py:1278-1311) and
  splits into tiles only on a CUDA OOM (sd.py:1312-1326, model_management.py:389-395).
  The measured working memory is 0.81 to 0.98 times V at 30 s and longer, and 0.81 to 0.90 times at the most trustworthy 60 and 120 s ("ACE-Step 1.5 song length" under "Cross-check" below; 10 s points miss the peak and are not used).
  For the image KL VAE it is 0.3 to 0.65 times, so V would worsen the prediction ("Cross-check", #102, below).
- The old formula stayed at about 1 GB because it used the tile value V_tile (256 latent frames).
- Tile fallback: when the untiled estimate does not run or is tight, the tiled estimate (min(V, V_tile)) is judged too.
  Its level is capped at **Marginal**, and the better one is taken (untiled when equal). ComfyUI retries in tiles after
  an OOM, and NVIDIA on Windows spills into shared memory by default (the RTX 3070 run at 120 s). Both are slow. The
  reasons get one sentence. In that case the VRAM in the table is the tiled estimate, and the sentence says
  "ComfyUI first tries X GB untiled". So the table VRAM can go down when the length goes up, and it differs
  by GPU (e.g. the all-on-GPU value of ACE-Step 1.5 without measurements is 16.5 GB at 60 s and 11.8 GB at
  120 s (tiled) on 8 / 12 GB, and 22.2 GB at 120 s (untiled) on 16 GB). The level never goes up with the length
  (test `test_longer_songs_never_raise_the_level`).
- Neither estimate drops when VRAM is added, so the better of the two does not drop either.
- The CPU has no fallback. A failed allocation on the CPU is not a CUDA OOM, so ComfyUI stops without tiling
  (model_management.py:380, 389-395).
- No coefficient is fitted. No factor is applied to match the measurements (0.8 to 1.0 times V). From 30 s up the
  formula is a little above the measurements (the safe side). Short songs around 10 s have V equal to V_tile, and
  their measurements are not reliable ("Cross-check" below).
- The author measurement of `audio_ace_step_1_5_checkpoint` (20 s, 10.8 GB) is below the weights + fixed floor, so
  the working memory is not shifted (#151; "How measurements are used" 1 below). At #148 it lowered the working memory
  by 1.91 GB at every length, and its level could be better than `audio_ace_step_1_5_split`, which has the same
  contents (Good vs Marginal at 60 s on 8 GB). Since #151 the two levels are the same.
- Fairness: only the 8 models with the Oobleck VAE change (6 ACE-Step 1.5, 2 Stable Audio Open). No level of any
  other model changes at VRAM 2 to 80 GB (0.5 GB steps), main memory 16 / 32 / 64 GB, or on AMD.
  Even at the default length, V at 30 s (2.86 GB) is above the tile value (0.98 GB) for ACE-Step 1.5, so the level
  changes at these VRAM sizes (for any main memory and vendor; Stable Audio Open's default 10 s does not change):
  - `audio_ace_step_1_5_split`: Good → Marginal at 4 to 5 GB, Perfect → Good at 20 to 22.5 GB
  - `audio_ace_step_1_5_checkpoint`: Perfect → Good at 18.5 to 19.5 GB
  - `audio_ace_step_1_5_split_4b`: Perfect → Good at 27 to 30 GB
  - the 3 XL models (base, sft, turbo): Perfect → Good at 35 to 38 GB
  Stable Audio Open uses the same VAE code and formula, so it is treated the same (this model cannot make songs longer
  than 47 s).
- Left out: ACE-Step 1 (music_dcae VAE) and YuE2 have no measurements. The Wan VAE also measures 0.74 to 0.98 times V,
  but it is also used by images such as Qwen Image, and treating it the same would lower the level in 36 places across
  14 models at the default settings (Perfect → Good 27, Good → Marginal 9). To be decided in a separate issue.

Levels of ACE-Step 1.5 (`audio_ace_step_1_5_split`, no measurements; NVIDIA, 32 GB main memory):

| length | 8 GB | 12 GB | 16 GB | 24 GB |
|---|---|---|---|---|
| 10 s | Good | Good | Good | Perfect |
| 30 s | Good | Good | Good | Perfect |
| 60 s | Good → **Marginal** | Good | Good | Perfect → **Good** |
| 120 s | Good → **Marginal** | Good → **Marginal** | Good | Perfect → **Good** |
| 240 s | Good → **Marginal** | Good → **Marginal** | Good → **Marginal** | Good → **Marginal** |

| shape | decode tile | encode tile |
|---|---|---|
| image (2D) | the largest of latent 64×64, 32×128, 128×32 (up to the image's latent size; sd.py:1182-1194). A 512 px square at 8× compression, 1024 px at 16× (Flux2, Qwen Image 2.1, Mage) | the largest of pixels 512×512, 256×1024, 1024×256 (sd.py:1214-1225) |
| video (3D) | 2 latent frames in time (1 for a single frame), a `256 / compression` square in space (up to the latent size; sd.py:1335-1357) | a 256-pixel square in space, all frames (sd.py:1462-1470) |
| audio (1D) | 256 latent frames (sd.py:1196); the same for ACE-Step 1 | 256 × 2048 samples (1/16 for ACE-Step 1; sd.py:1227-1235) |
| VAEs that tile themselves (MiniMax H3) | a 256-pixel square (sd.py:1327-1331, 1335) | a 256-pixel square |

- ComfyUI's 3D budget `min(V, total × 0.8)` depends on VRAM, so the smallest tile is used for the level.
- When V does not fit the GPU (fixed + decode-stage weights + V > VRAM), the reasons say "ComfyUI splits the VAE into tiles (slower)"
  (the V_eff level is not changed). On Windows, NVIDIA may spill into shared memory (sysmem fallback) and only slow down instead of failing with OOM.
- **V rarely changes the level**: judging the bundled list on 6, 8, 12, 16 and 24 GB NVIDIA GPUs with V and V_tile set to 0
  changes the level in 13 of the 1,625 cases (0.11.0; 4 MOSS Image, 3 audio, 2 Qwen Image 2.1, 2 SDXL workflows, 2 VOID).
  The kept-loaded amount (#102) changes 16 of the 1,625 cases at 8 to 32 GB, and lowers the level in 2 (the two Kandinsky 5 entries at
  32 GB, Perfect → Good). The others stay Good and only switch from GPU to GPU~.
- So SDXL's all-on-GPU value is the same from 512 px to about 2000 px (the heaviest stage is the tiled VAE, 1.06 GB; the denoise stage
  is 0.26 GB even at 1024 px). Above that the denoise stage takes over and it grows.
  For most models the weights or the denoise stage dominate. V matters mainly for whether all-on-GPU can be chosen (the kept-loaded amount) and through the "split into tiles (slower)" note when V exceeds VRAM.

### How families are mapped

The row of `working.py` comes from `comfy_family` / `comfy_vae` (`families.py`; docs/hardware, "Family table") and the main model's size.

- **The size is the main file's `header_params`** (element count from the safetensors / GGUF header; else `params_b` of the list), in bins
  (dim and hidden_size are not read; no cache or schema change, and the values are fixed):
  - Wan: below 2.5B → dim 1536, below 7B → 3072 (Wan 2.2 5B), otherwise → 5120. Factor = dim / 2222 (supported_models.py:1374).
    VACE × 1.2 (:1456), WanDancer 1.8 (:1590)
  - Flux2: below 6B → hidden 3072 (Klein 4B), below 12B → 4096 (Klein 9B), otherwise → 6144 (dev 32B). Factor = 3.1 × 4 × hidden / 2604 (:820)
  - LTX: 14B or more → LTXAV (LTX-2 / 2.3; factor 0.077, :980), 10B or more → LTXV 13B (11 = 5.5 × 4096 / 2048, :960), otherwise → LTXV (5.5)
  - SD: an (all-in-one) main model of 3.3B or more → SDXL (0.8, :219), below → SD1.5, SD2, SDXL refiner (all 1.0, :69, 116, 180)
- Other factors: SD3 1.6, Flux 3.1, Chroma 3.2, LongCat 2.5, HiDream 2.0 (default), Lumina2 1.4, Z-Image 2.8,
  Omnigen2 1.95 (Boogu 2.15), Kandinsky5 1.25, Krea2 2.2, Qwen Image 1.8, JoyImage 1.8, Anima 0.95, Lens 4.0,
  Ideogram4 11.6, MOSS-Image 14.6, ERNIE 10.0, MageFlow 6.5, Qwen Image 2.1 6.0, HiDream O1 0.033, PixelDiT 0.04,
  HunyuanVideo 1.8, HunyuanVideo 1.5 4.0, CogVideoX 2.0 (default), MiniMax H3 0.114, ACE-Step 1.5 4.7, ACE-Step 1 0.5,
  Stable Audio Open 2.0 (default), YuE2 4.0 (source lines in `working.WORKS`).
- VAEs (split out in #100): `wan22` (Wan 2.2 5B, 16× compression), `qwen21` (Qwen Image 2.1, 16× compression), `flux2` (the Flux2 VAE × 4),
  `oobleck` (ACE-Step 1.5, Stable Audio Open), `yue2`, `pixel` (pixel space). The Qwen Image VAE is the Wan 2.1 VAE.
  When the list's `comfy_vae` does not fit the row's latent (another part's VAE, or a key of an older list), the row's default VAE is used.
- Temporal compression: 4 for Wan, HunyuanVideo and CogVideoX, 8 for LTX, 17k+5 frames ↔ 5k+2 for MiniMax H3 (sd.py:1051).
  Pixel-space families (HiDream O1, PixelDiT) are 1 in space too.

### Models without a per-stage formula

These use **the earlier per-modality formula for the whole model** (ComfyUI's default factor 2.0 is not used: it would overestimate
non-diffusion tools such as SAM3, Depth Anything and frame interpolation).

<!-- fallback-count: 86 -->
86 of the 329 entries in the bundled list:

- 22 text to speech, 10 3D (the `act_3d_gb` formula; ComfyUI's 3D estimate has a different shape)
- 27 with an unknown family (13 image, 13 video, 1 music)
- 20 with a family but no formula: SeedVR2 4, Stable Audio 3 4 (the list does not tell which of the two VAE configurations it uses),
  SD video (SVD, AnimateDiff) 3, Ming 2 (no VAE formula), Qwen Image video workflows 2, Depth Anything 3 2, SAM3 2, MiniMax Music 1
- 7 with an unknown size: Flux3 Klein 4 (no header and no `params_b`), Wan 2 (same), SD 1

Mapping more families is future work ("Known limits" below).

**Working memory (models without a per-stage formula):**

- Image = coefficient × megapixels × batch size
- Video = coefficient × megapixels × (frames / 49)^0.5 × batch size
- Audio = coefficient × minutes
- 3D = constant
- The coefficients stay at the values fitted in 0.10 (from #100 only 3D is refitted; see "Fitting" below).
- The video exponent 0.5 is not fitted (there is only one measurement). The value itself has no solid basis.

**Precision setting** (`Settings.precision`, CLI `--precision`):

- Only the main model's weights are converted (for example bf16 → fp8 halves them).
- Differences in case and spelling (`BF16`, `float16`, `gguf-Q4_K_M`) are normalized before comparing.
- The original precision is `ModelEntry.precision`; if missing, it is read from the main file name (`bf16` / `fp8` / `int8`, …).
- If it cannot be told, no conversion is done.

**Fit levels** (since 2026-10-20, following "Fit analysis" in llmfit's docs/how-it-works.md):

- The level comes from how much of the memory the mode uses (VRAM on the GPU, main memory on CPU):
  - ≤ 60%: Perfect
  - ≤ 85%: Good
  - ≤ 98%: Marginal
  - above that: Too tight
- Caps by mode:
  - All on GPU: no cap.
  - Swapped in turn (GPU~) and offload (GPU+RAM): up to Good (offload was up to Marginal until 0.10).
  - CPU: generation up to Marginal, speech built for real-time CPU use and one-pass tools up to Good (0.11.0, #108).
  - No cap by GPU vendor (until 0.10, non-NVIDIA GPUs and DGX Spark were capped at Good).
  - The full rules are in [scoring.md](scoring.md) "Level caps".
- The mode with the best level is taken (no offloading on a tie). The main-memory check ("Main memory (RAM)" below) comes after that.
- If the size is unknown, no level is given (`unknown`, shown as `?`; "Models of unknown size" below).
- Before, it was "Perfect if all-on-GPU is within 90% of VRAM, Good if within VRAM".
- Effect on the bundled list (all 341 entries of the list at the time the levels changed, before the HF lower bound was raised; the bundled list of 0.9.1 (2026-10-21) has 329):
  - 8 GB / 32 GB: Perfect 44 → 23, Good 22 → 32, Marginal 87 → 97, Too tight 188 → 189.
  - 16 GB / 32 GB: Perfect 95 → 66, Good 67 → 76, Marginal 107 → 124, Too tight 72 → 75.
- At the VRAM that AI生成キット recommends (8 GB), Z-Image Turbo int8 would use 94% swapped in turn, so since 0.11.0 (#108)
  part of the weights is offloaded and it is Good (GPU+RAM, slow; Marginal swapped in turn before).
  Wan 2.1 1.3B is the same (97% swapped in turn → Good offloaded). tests/test_estimate.py.
- If offload still does not fit on the GPU, the result is compared with the no-GPU judgment (Marginal for small types that run on CPU), and the better one is taken.
  - Before, adding a small GPU could turn a CPU-capable model into "does not run", so adding VRAM lowered the level (#17).

### Main memory (RAM)

- When running on the GPU (all on GPU, swapped in turn), main memory is **fixed 3.0 + the weights of the largest stage**.
  - This rests on an **assumption**: ComfyUI reads safetensors by memory mapping (mmap) and sends the parts to the GPU one at a time.
    So the OS can drop the pages of a part once it is used.
  - There is no measurement.
- For offload and CPU, the offloaded weights stay in main memory, so it is fixed + all parts (the safe side).
- When memory is short:
  - Up to **1.5 times** the installed memory: Marginal. The shortfall uses virtual memory (the page file), which is slow. The reason says so.
  - Above that: Too tight.
- How 1.5 was chosen:
  - On Windows, the default (system-managed) page file grows to about the installed memory or more.
  - But weights in the page file are extremely slow, so it was kept to half of that.
  - **1.5 is a judgment call and has not been measured.**
- On a PC with a fixed page file size (fixed size, no page file, Linux swap), the limit is "installed memory + that size" (but at most 1.5 times) (#21; see "Memory and virtual memory" in [hardware.md](hardware.md)).
- Before, it was "3 GB + all weights, Too tight if short".
  - That disagreed with the 16 GB minimum memory in AI生成キット's catalog.
  - On a 16 GB PC, Wan2.2 5B (16.9 GiB of weights) was Too tight regardless of the GPU.
  - Now none of the 8 models is Too tight because of memory on 16 GB (or a laptop that shows 15.3 GiB) (checked by `tests/test_estimate.py`).

### Models of unknown size

When both of these hold, the weights are not treated as 0 and the model is not said to fit:

- At least one file has no size (None, 0 or negative).
- There is also no template size and no parameter count × precision.

The output is:

- `fit = "unknown"`, `run_mode = "unknown"`, `basis = "estimated"`, `unknown_size = true` (`fit` was `too_tight` before 0.1.1, `run_mode` was `none` before 0.2.0)
- The reason starts with 「大きさが分からないため判定できません」 ("size unknown, cannot judge").
- The model is left out of `recommend` (it still shows in `fit --fit all`, `search` and `info`).
- VRAM and memory values are null in JSON (the Python attributes stay 0.0), and `limits` is null.
- Without a GPU it is also not treated as "small, so it runs on CPU".

## How files are counted (agreement with #2)

The list from #2 holds only one version per part (one precision, `.safetensors` where possible, either the single file or the diffusers layout).
Even so, to avoid overcounting when a Hugging Face repository listing is passed as is,
the estimator also counts by these rules (`estimate.select_files`):

1. **Files that are not counted:**
   - Files that are not weights (`.json`, `.txt`, `.md`, images, …).
   - Formats ComfyUI does not use (`onnx/`, `flax`, `openvino`, `safety_checker/`, `.onnx`, `.msgpack`, `.h5`, …).
2. **Count only one set per part.** A part is decided as follows:
   - ComfyUI folder + name (without precision, format and split number)
     - `t5xxl_fp16` and `t5xxl_fp8_e4m3fn` are the same part.
     - `clip_l` and `t5xxl` are different parts.
   - HF diffusers folder
     - `model` / `pytorch_model` / `diffusion_pytorch_model` inside are one part.
     - In a VAE folder, `vae` and `vae_1_0` and similar count as one.
   - Main model outside folders (same name, or one name starts with the other)
     - `sd_xl_base_1.0` and `sd_xl_base_1.0_0.9vae` are the same part.
     - Main models with different names, such as a high-noise and a low-noise one, are different parts.
3. **How one set is chosen:**
   - Format: `.safetensors` > `.gguf` > `.bin` / `.ckpt` / `.pt(h)`
   - Then precision: fp16 / bf16 > no precision written > fp8 / int8 / q8 > q6 > q5 > q4 / nf4 > fp32
   - If still equal, the larger one (the safe side).
   - Split files (`-00001-of-00003`) are all counted.
4. **When both a single file and a diffusers layout exist,** the single file is counted.
   - If the single file is known to be all-in-one (SDXL and others in the breakdown table), all diffusers folders are dropped.
   - If not known, only the diffusers folders whose role (main model, VAE) also exists as a file outside the folders are dropped.
     - This is so the encoder is not missed when the single file holds only the main model (for example FLUX's `flux1-dev.safetensors`).
     - If a file is all-in-one but not in the breakdown table, the result comes out larger (the safe side).
   - **The request said "count only the single file", but that would miss the encoder (about 9 GiB of T5 for FLUX) when the single file holds only the main model, so it was done this way.**
5. If files were dropped, the reason is shown in `reasons`. `disk_gb` is the sum of the counted files.
6. **Optional parts** (#81, 0.12.0: parts that the official template does not load with its default values; how they are
   found is in [sources.md](sources.md) "Optional parts"):
   - `inactive` (parts only on muted or bypassed nodes, or on nodes that reach no output node) are counted in none of
     VRAM, main memory, `disk_gb` and `download`.
   - `switch_off` (parts only on the unselected branch of a switch with a fixed value: a prompt enhancer, a speed-up LoRA,
     …) are not counted in VRAM or main memory. ComfyUI's validation also checks the files on the unselected branch, so
     they are counted in `disk_gb` and `download`.
   - The upper bound with every switch turned on (`vram_gb_all_options`, `fit_all_options`) is also given, and one
     sentence goes into the reasons only when the level would change ([scoring.md](scoring.md) "Parts switched off by
     default").
   - Measurements were taken with the default values, so the shift ("How measurements are used" below) is decided with the
     parts loaded by default.

## How measurements are used

The RTX 5080 measurements were taken with ComfyUI's dynamic VRAM, which **uses more when more is free**.
13.2 GB on a 16 GB card (Z-Image int8) is not a lower bound of what the model needs. So:

1. A measurement is used as the all-on-GPU value (`vram_gb`).
   - The working memory is shifted by d = "measurement − formula value". The formula value is the per-stage formula at the measurement's settings (VAE at its tile value V_eff).
   - **A negative shift** is added to the working memory of each stage of all three amounts and clipped at 0 per stage (#100),
     so no amount goes below weights + fixed part (zero working memory; the floor of #95).
   - **A positive shift goes to the all-on-GPU value only** (for models with and without a per-stage formula). An unpinned measurement above
     the formula is a peak on a GPU with room, where dynamic VRAM kept the parts loaded and decoded the VAE without tiles
     (Wan 2.1 1.3B's +1.96 GB is nearly the gap between the untiled V of 3.42 GB and the tiled V of 0.27 GB);
     this does not happen when swapping in turn or offloading.
   - **A shift from a measurement pinned at the GPU limit** (used only as a lower bound) goes to all three.
     Which one applies depends on the measurement, so it is monotonic in the VRAM of the PC being judged.
   - v3's "the same shift for all three" (`max(a + d, b + d) = max(a, b) + d`) holds only when the shift is an error of the fixed part.
     Gaps with other causes (dynamic VRAM using free space, no tiling) differ by stage, hence the rules above.
   - **When a measurement (the maintainer's or other users') is below the floor (weights + fixed part), the working memory is not shifted** (#151, 2026-10-31).
     A measurement below the floor is a record of running with part of the weights offloaded to main memory and says nothing about the size of the working memory
     (the same reason it does not set the level; [scoring.md](scoring.md) "When measurements set the level"). At every setting the value is the formula,
     `basis` is estimated (`basis_detail` is also estimated), and the reasons say "it carries no working-memory information and the value is the formula".
     - This reverses the #100 decision (raise to the floor and, within 0.1 GB, keep `basis: "measured"`; `MEASURED_FLOOR_TOLERANCE`).
       Raising to the floor made the shift (measurement − formula) negative by the gap to the floor, and that shift applied **to every length and resolution**
       (from the ACE-Step 1.5 checkpoint's 20 s, 10.80 GB (floor 10.82 GB), −1.91 GB at every length; its level was better than the split version with the same contents).
       Working-memory-only measurements (tests/test_issue_148.py) put the working memory at 20 s at 1.5 to 1.9 GB, so a full load should be 12.3 to 12.7 GB.
       10.80 GB is a record with part offloaded, not measurement noise.
     - A measurement without that information does not push out one with it: if the chosen measurement (`estimate.pick_measurement`) is below the floor, the next candidate is used (the reasons say so),
       and the value is the formula only when every candidate is below the floor. A measurement below the floor still supplies the main-memory peak (`peak_ram_gb`) at the same settings.
     - Pinned and lower-bound-only measurements ("at least") and measurements above the GPU total (capped) are unchanged.
   - On a small GPU, even if all-on-GPU does not fit, the result **falls to swapped in turn or offload**.
     Example: on 8 GB, Z-Image int8 needs 13.2 GB all on GPU, but runs at 7.5 GB swapped in turn. Use is 94% (Marginal), so since 0.11.0 part of the weights is offloaded instead and the level is Good (GPU+RAM).
   - A measured value larger than the GPU does not by itself make a model Too tight.
2. If the measurement is 90% or more of the GPU's capacity (or offload is lowvram or similar), it counts as "pinned at the limit".
   - **It is used only as a lower bound** (all on GPU = max(formula, measurement)).
   - This applies to Z-Image bf16 (15.0 / 16) and the 2 Wan2.2 5B measurements (15.8 / 15.0).
3. `basis: "measured"` only when the settings (resolution, frames, length, **batch size**, precision) equal the measurement.
   - Precision spelling is normalized. If the measurement has no precision recorded, it is compared as the model's original precision.
   - Different settings give an estimate shifted by the measurement, with `basis: "estimated"` (the reason says 「…の実測で補正した推定」, "estimate corrected by the measurement of …").
   - If you change the precision, the measurement is not used.
4. When no settings are given (changed in 0.11.0; [scoring.md](scoring.md) "Settings used for judging"):
   - Every model, with or without measurements, is judged at the default for its type (image 1024px, video 832x480 and 81 frames, music 30 seconds, …).
   - A measurement's settings are used only to work out the shift of the working memory. The maintainer's image, video and music measurements (AI生成キット's "fast" quality:
     768–832px, video at 512–640px, music of 20 seconds) do not match the defaults, so at the defaults they are `calibrated` (no `m` in the table).
   - Up to 0.10 a model with a measurement was judged at the measured settings (so only the models the maintainer measured were compared at smaller settings).
   - To judge at the measured settings, give `--width` and the like (apps: `judge_model(..., settings=...)`).
   - A template without a size input (depth estimation, background removal and the like, which work from an input image) has no size in its judging settings (#90).
     - Which templates have no size input is decided only by a hand table the maintainer checked (`estimate.NO_SIZE_INPUT`; the evidence is a bench record or the template's nodes), never by other users' submissions (so that one submission cannot change the judgment of a model with a size input on every PC).
     - A new template is added when its first bench record is reviewed (the bench writes a width and height only when the template has a size input; a size-less submission for a model not in the table gets the `settings_missing` warning in its PR, which tells the maintainer).
     - A size-less measurement then has the same settings as a judgment without settings. With `--width` and the like, things work as before.
     - The working memory is still estimated at the default size for the type (image 1024px and so on).
5. Without a GPU (`--vram 0` and so on), GPU measurements are not used: `basis: "estimated"`, `measured_on: null`.
6. These measurements are used neither for estimates nor for refitting (written by the bench in #5):
   - `outcome` is not `ok` (OOM, error).
   - `standard: false` (custom workflows or extra overrides).
7. **Working-memory-only measurements** (#100): measurements with `vram_metric: torch_reserved` and `offload: dynamic`
   (taken by bench with dynamic VRAM) exclude the weights (dynamic VRAM loads and evicts the weights on demand).
   - Until per-stage peaks can be measured, they **neither set the level nor shift the estimate; they are only cross-checked** (below; `estimate.working_only`).
   - The 9 bundled RTX 5080 rows (`vram_metric` null) are total amounts as before.
   - The same for other users' submissions (bench results): they are aggregated and kept, but do not move estimates ([schema.md](schema.md), "contrib").
8. Measured values are taken as GiB (`unit` in `measurements.json`).
   - AI生成キット's records do not state the unit.
   - It is an **assumption** from the fact that ComfyUI and nvidia-smi count in MiB.

## Fitting

- **Since #100 only `act_3d_gb` (3D working memory) is fitted**, on the 2 3D measurements.
  - The working memory of models with a per-stage formula is ComfyUI's estimate and has no coefficient (it is not fitted to measurements).
  - The image, video and audio coefficients of models without a per-stage formula (`act_image_gb_per_mp` / `act_video_gb_per_mp` / `act_audio_gb_per_min`)
    and the fixed part `overhead_gb` are frozen at the values fitted to the 9 measurements in 0.10 (`calibrate.FROZEN`; `frozen` in calibration.json).
  - The fixed part will be refitted after the RTX 3070 is measured again ("Cross-check" below).
- Fixed: the weight factor (1.0), the offload share (0.4), the video exponent (0.5), the main-memory fixed part (3.0), the memory factor (1.5).
- Only the bundled measurements are used (`measurements.json`, `measurements_community.json`). This PC's bench results in the cache are excluded
  and their count is printed (calibration.json is the same whichever PC builds it; #100).
- What is minimized (over the 2 3D measurements):
  - **The sum of squared relative errors** (a pinned measurement counts only when the prediction is below it: a one-sided square)
  - A weak penalty on the relative distance from the prior values (the placeholders from #1), weight 1e-4
- Accuracy is measured by leave-one-out. Rows that are not fitted (all but 3D) have the same all-fit and LOO predictions (no measurement is used in the formula).
- The target check (`target_met`) is made only for modalities with at least 2 unpinned measurements.

**Relation to the human guidelines (stated honestly):**

- The minimum / recommended VRAM of the AI生成キット catalog is used neither in the formula nor in the coefficients.
- But **the offload share 0.4 is a hyperparameter chosen with the guidelines in view.**
  - It cannot be set from measurements, so it was chosen from the range that agrees with both "SDXL runs on 4GB" and "bf16 Z-Image does not run on 4GB".
  - That range was the maintainer's view with the formula before #100 and has not been measured again with the per-stage formula.
- "bf16 Z-Image does not run on 4GB" is not in the catalog; it is the maintainer's judgment.
- `tests/test_estimate.py` compares against the guidelines. Those results are not fed back into the fit.

Fitted result (2026-10-27, n = 9, of which 3 pinned; only the 2 3D measurements are fitted):

| coefficient | value | prior | range |
|---|---:|---:|---|
| overhead_gb | 1.472 (frozen) | — | — |
| act_image_gb_per_mp | 0.2284 (frozen) | — | — |
| act_video_gb_per_mp | 9.8704 (frozen) | — | — |
| act_audio_gb_per_min | 0.0455 (frozen) | — | — |
| act_3d_gb | 0.7839 | 2.0 | 0 to 4 |

- The 4 frozen values were fitted to the 9 measurements in 0.10 (the fixed part close to the top of its range, 1.5). **Do not read a physical meaning into each coefficient.**

## Results (leave-one-out)

| Model | Settings | Measured GB | Fit-on-all prediction GB | LOO prediction GB | LOO error | Weights-only error |
|---|---|---:|---:|---:|---:|---:|
| image_z_image_turbo_int8 | 768x768 | 13.2 | 13.87 | 13.87 | +5.1% | -14.1% |
| image_z_image_turbo | 768x768 | 15.0 | 21.80 | 21.80 | pinned (weights alone exceed the measurement, no information) | +28.5% |
| image_sdxl_simple | 832x832 | 8.0 | 9.00 | 9.00 | +12.5% | -19.2% |
| text_to_video_wan | 512x512, 49 frames | 13.3 | 11.34 | 11.34 | -14.7% | -31.2% |
| video_wan2_2_5B_ti2v | 640x640, 73 frames | 15.8 | 19.35 | 19.35 | pinned (weights alone exceed the measurement, no information) | +7.0% |
| video_wan2_2_5B_ti2v | 640x640, 73 frames, text to video | 15.0 | 19.35 | 19.35 | pinned (weights alone exceed the measurement, no information) | +12.7% |
| audio_ace_step_1_5_checkpoint | 20 s | 10.8 | 12.73 | 12.73 | +17.8% | -13.5% |
| 3d_hunyuan3d_image_to_model | - | 6.7 | 6.85 | 7.13 | +6.4% | -31.5% |
| 3d_hunyuan3d-v2.1 | - | 9.4 | 9.12 | 8.97 | -4.6% | -27.0% |

4 could be evaluated (3d 2, image 2); maximum LOO error 12.5% (mean 7.2%). Inside the ±20% target.
music and video have at most 1 unpinned measurement and cannot be evaluated (target_met is null).
All 3 of the 3 pinned measurements are exceeded by the weights alone and carry no information. For reference: LOO mean absolute error over all 6 unpinned measurements 10.2%, maximum 17.8% (weights only: mean 22.8%)

Cross-check (measured differently, so not used for fitting; predicted with the coefficients fitted on all):

| Model | GPU | Source | Settings | Compared | Measured GiB | Estimated GiB | Error |
|---|---|---|---|---|---:|---:|---:|
| utility_birefnet_remove_background | NVIDIA GeForce RTX 3070 | maintainer | - | working memory (measurement without weights) | 1.25 | 0.24 | -81% |
| 3d_moge_perspective_to_mesh | NVIDIA GeForce RTX 3070 | maintainer | - | working memory (measurement without weights) | 0.72 | 0.78 | +9% |

How to read it:

- Rows other than 3D are not fitted, so "fit on all" and "LOO" are the same: they are cross-check errors.
- For the 2 image rows (Z-Image int8 +5.1%, SDXL +12.5%) the heaviest stage is ComfyUI's VAE figure (tile value 1.06 GB), which leans high
  (the VAE figure is about 1.8× the measurement; "Per-stage working memory (#100)" above).
- Wan 2.1 1.3B at −14.7% is the gap between the peak on a GPU with room (VAE not tiled) and the formula, as in "Summary" above.
- ACE-Step 1.5 at +17.8%: the heaviest stage is the untiled V of its VAE (Oobleck), 1.9 GB at 20 s (#148; "Tiles" above).
  The measured 10.8 GB is below the weights + fixed floor and says nothing about the working memory ("Cross-check" below).
- The 2 cross-check rows are working-memory-only measurements (#100), so they are compared with the working-memory estimate (the formula for models without a per-stage formula).
  BiRefNet at −81% shows that the image coefficient (0.23 GB per megapixel), fitted to diffusion models, is too small for tools.

## Known limits

- **Only one kind of GPU**: the 9 total-amount measurements used for fitting and cross-checking are all RTX 5080 16GB with dynamic VRAM.
  - The swapped-in-turn and offload values on small GPUs are not verified.
- **Dynamic VRAM**: it uses more when more is free, so a measurement is "what that GPU used", not "what is needed".
  Since #100 a positive shift (+1.96 GB for Wan 2.1 1.3B) is added to the all-on-GPU value only ("How measurements are used" 1 above).
- **Pinned measurements**: the all-on-GPU values of Z-Image bf16 and Wan2.2 5B come from the formula only and are not bounded from above by measurements.
- **Wan aspect ratio**: the video measurements record only the long side (512px / 640px), assumed square.
- **Unknown dates**: the measurements have no date or ComfyUI version (date in measurements.json is empty).
- **Units**: measurements are assumed to be GiB ("How measurements are used" 8 above).
- **Main memory**: neither the formula nor the 1.5× factor is checked by measurement ("Main memory (RAM)" above).
- **Non-NVIDIA GPUs** (0.7.0, #22): there are 0 measurements on AMD, Intel or Apple.
  - The per-stage working memory keeps the SDPA formula (GPUs with split attention only get a note). AMD multiplies the KL VAE by 2.73.
  - No measurement has run on this kind of GPU yet. Before 0.11.0 the level was capped at Good (2026-10-22); 0.11.0 (#108) dropped the cap and shows this by not giving the verified-kind mark (✓).
  - The share of memory treated as GPU memory is a guideline from primary sources, not measured ([hardware.md](hardware.md), "Shared memory").
- **CPU-capable families** (#39): the CPU main memory of SD-family and Stable Audio models comes from a formula derived from ComfyUI's code, not measured.
  - The formula widens the main model and VAE to fp32 and doubles the working memory (the heaviest stage × 2 with the per-stage formula).
- **Known misses** (against the human guidelines, recorded as strict xfail in `tests/test_estimate.py`):
  - No misses now: all 8 models are not Too tight at the guideline's minimum VRAM and run on the GPU at the recommended VRAM.
  - The Wan2.2 5B miss (Too tight on 8GB before #100) is fixed by the per-stage formula (ComfyUI's Wan 2.2 VAE estimate).
  - At the app's settings (512px, 49 frames), Wan 2.1 1.3B is Good offloaded on 6GB and on 8GB (swapped in turn it would use 97% on 8GB; Marginal before 0.11.0).
- **Future work** (#100):
  1. Text-stage working memory ComfyUI does not estimate: image tokens of Qwen2.5-VL, Flux2's Mistral, the YuE2 score stage (8192 tokens),
     the KV of the ACE-Step 1.5 LM, and so on
  2. Measure per-stage peaks with bench (split at stage boundaries), so that working-memory-only measurements ("How measurements are used" 7 above) can be used
  3. Reduce the models without a per-stage formula (86): the Flux3 size, the Stable Audio 3 VAE, the SeedVR2 and Ming VAEs, and so on
  4. Measure the RTX 3070 again ("Cross-check" below), and Wan2.1 at 832x480, 81 frames
  5. The share of weights kept on the GPU when offloading (now 0.4). ComfyUI on NVIDIA uses `MIN_WEIGHT_MEMORY_RATIO = 0` (model_management.py:458-460@426f5ee).
     A likely reason why reports of Wan 14B fp8 running on 12 GB disagree
  6. Measurements at the edges of the levels: Klein 9B and Flux3 Klein on 6 GB, Z-Image bf16 on 16 GB (swapped-in-turn use 0.864), and so on
  7. What the CPU working memory (doubled in main memory) really contains
- **Models with an unknown all-in-one breakdown** (such as the HF models that #2 adds) are split by general shares, so the swapped-in-turn and offload values can be far off.
- **Role guessing**: a file with neither a folder nor a location gets its role from the list of known names.
  A text encoder not in the list is counted as the main model (the all-on-GPU value does not change, but the swapped-in-turn and offload values come out larger).

## Other users' measurements (#34, 2026-10-21; handling aligned with llmfit on 2026-10-22)

Users add submission JSON files through a Pull Request to `data/contrib/<GPU>/<date>-<sha8>.json` (since 0.13.0 `genmodelfit share` does this; also `genmodelfit bench --submission`)
(steps: [CONTRIBUTING.md](../CONTRIBUTING.md); format: "contrib" in [schema.md](schema.md)).
**As of 2026-10-31 (0.14.0), 15 submissions from 2 sources have been accepted**: the maintainer's 4 (#126, RTX 3070,
SDXL at four settings) and a contributor's 11 (#141, RTX 5080). 4 of them probably missed the peak and are left out
(`contrib_nonstandard.json`, #131), so the bundled `src/genmodelfit/data/measurements_community.json` has 11 rows, one
submission each. None is consistent yet (that needs 2 or more sources for the same GPU, model and settings), so none
enters the calibration.
The rules below describe how submissions are handled.

**Files measured (#90)**: other users' submissions (contrib_schema 2 and 3) and this PC's bench measurements carry `files_fp`, the fingerprint of the list's files when measured; when a weekly list update sees upstream files change (the fingerprint differs), they are no longer used. The maintainer's measurements (`measurements.json`) have no fingerprint, so they keep being used when files change (the maintainer re-measures them; the difference is that other users' rows silently stop being used while the maintainer's keep being used). `genmodelfit contrib aggregate --check` lists the bundled rows that are no longer used.

**Maintainer's decision of 2026-10-22:** follow the same approach as llmfit (main, read on 2026-10-22).

- How llmfit does it:
  - It only checks submissions (the `llmfit bench --share` PR) mechanically in CI.
    - Checked: schema, path and name, size, time, value limits, min ≤ avg ≤ max.
    - There is no signature and no human-review mechanism; merging is the maintainer's review.
  - It groups only measurements with the same CPU name and GPU name, takes the median, and keeps a count (sample_count).
  - It uses even one measurement, and overrides the estimate in both directions.
  - Priority: your own measurement > other users' measurements on the same hardware > estimate.
  - For correcting other models, it clamps the median of measurement ÷ estimate to [0.05, 3.0].
- The rules of 2026-10-21 were dropped.
  - Content: use only to raise the need, at most +15%, only with 2 or more submissions within 15% spread, only submissions a person checked per sha8.
  - Reason: they were hard for the maintainer to follow, and even with measurements they could not lift the then "up to Good" cap for non-NVIDIA GPUs (dropped by #108 in 0.11.0).

**How they are grouped** (`genmodelfit contrib aggregate`):

- Submissions are grouped by what is the same in:
  - model
  - GPU (the PCI model ID if there is one, else the name normalized with `gpu_slug`)
  - GPU VRAM (rounded to 1 GB)
  - how it was measured (`vram_metric`)
  - settings (resolution, frames, length, steps, precision, batch size)
- Non-standard submissions (`standard: false`) are not used.
- The maximum VRAM of runs that finished (ok) is used.
  - With 3 or more, values with |x − median| / (1.4826 × MAD) > 3.5 are dropped as outliers (the floor of MAD is 2% of the median).
  - The median of the rest is the value.
- The count (`n_used`) and the number of OOM runs in the same setup (`n_oom`) are kept.
- A group mixing ok and OOM uses the median of the ok runs, and the OOM count is shown in the reason.
- A group with only OOM becomes one row with `outcome: oom` (not used for estimates).

**When they are used:** only measurements on **the same GPU as this PC** are used for this PC's need and level.

- Same as llmfit (0.9.0). The judgment for a different GPU or a PC without a GPU is the same with or without other users' measurements.
- `estimate.same_gpu` decides whether it is the same GPU.
  - If both have a PCI model ID (`1002:744c`), the ID is used.
  - Otherwise the name normalized by `estimate.gpu_key` is used (dropping (R), (TM), ™, ®, case, whitespace and the words NVIDIA / GeForce).
- **In addition, the VRAM must be close** (0.9.1; `estimate.same_vram`):
  - The ratio of the measured GPU's VRAM to this PC's total (smaller ÷ larger) is 0.95 or more (0.11.0; 0.97 in 0.9.1–0.10).
  - Because GPUs with the same name and the same model ID can have different VRAM (RTX 4060 Ti 8 / 16 GB, RTX 3060 8 / 12 GB, RX 7900 XT / XTX / GRE with PCI ID `744c`).
  - 0.95 allows for how the OS and driver count the total of the same card (`MEASURED_VRAM_TOLERANCE`). Linux CUDA reports the total minus a reservation: 5.80 GiB for a 6 GB card (5934 MiB, an RTX 2060 report on the NVIDIA developer forum), 7.79 for 8 GB, 9.78 for 10 GB, 10.76 for 11 GB, 11.77 for 12 GB, 23.69 for 24 GB; Windows reports 6.0, 7.99, 15.92 and so on. For 6 GB, 5.80 ÷ 6.0 = 0.967 missed 0.97, so Linux and Windows owners of the same card were different GPUs; hence 0.95.
  - The ratio of versions with different VRAM is 0.83 or less (RTX 3080 10 / 12 GB, for example), so there is room between them.
  - Integrated GPUs, APUs and Apple (shared / unified memory, whose total is set by the PC's main memory) are not compared.
- The check of other users' measurements (`fit_change` in `contrib`) is also done for a PC with that VRAM, for GPUs whose nominal VRAM the name table knows.
  - Covered: Intel Arc, and NVIDIA's `estimate.NVIDIA_VRAM_VARIANTS` (RTX 2060, 3050, 3060, 3080, 4060 Ti, 5060 Ti).
  - Confirmed on NVIDIA's official product pages; the source is in the constant's comment.
  - Laptop GPUs and the GTX 16 series are not in the table: they have no VRAM variants, or the names differ.
- Generic names such as "AMD Radeon(TM) Graphics" and "Intel(R) Arc(TM) Graphics" are not matched by name, because GPUs of different generations share them.
- The maintainer's bundled measurements shift the values on every PC (as before). Only the same GPU uses them to set the level (0.11.0; "Level" below).
- An ok row shifts the values **from one measurement**. `consistent` (2 or more sources, within 15% spread, no OOM) is the condition for entering a refit of the coefficients, and also one of the conditions for setting the level (0.11.0).

**Direction:** a measurement is used to **lower or raise** the need. It counts the same as the maintainer's measurements. Only the physical range limits it (`estimate._prepare`).

- Lower bound: the all-on-GPU value is not lowered below **weights + fixed part** (working memory 0).
  - A smaller measurement means some weights were held in main memory (dynamic VRAM, partial) or the measurement was wrong. It carries no working-memory information.
  - **Such a measurement does not shift the working memory** (neither at the same settings nor in the value extended to other settings; #151, 2026-10-31). The value is the formula, `basis` is estimated
    (`basis_detail` is also estimated). The reasons say "it carries no working-memory information and the value is the formula".
  - This reverses the 2026-10-22 decision (raise to the lower bound; `basis_detail` calibrated, reason 「下限の値にした」). Raising to the lower bound made
    a negative shift equal to the gap between the lower bound and the formula's full load, and it spread to other settings. It is the same treatment as the maintainer's measurements ("How measurements are used" 1 above).
  - The condition that validate used to flag for review (`offload: none` and smaller than weights + fixed part) is now a condition for not using the measurement in the estimate.
- Upper bound: the measured GPU's total × 1.02 + 0.25 GiB (the same as validate's "impossible value").
  - A larger value is clamped to this bound (total × 1.02 + 0.25 GiB) (`estimate._prepare`).
  - The reason reads 「GPU の総量を超えるので、上限(総量 × 1.02 + 0.25 GB)までにした」 ("it exceeds the GPU total, so it was limited to the bound (total × 1.02 + 0.25 GB)").
- The peak main memory (`peak_ram_gb`) is limited to the submission's main memory × 1.05 + 1 GiB (clamped when read).
- A measurement pinned at the GPU limit (90% or more of the total, lowvram) is used only as "at least", like the maintainer's measurements.
- When extending to an estimate with different settings (calibrated), measurement ÷ formula is clamped to **[0.6, 1.4]** (the counterpart of llmfit's [0.05, 3.0]).
  - The range is the same as the width at which validate reports "off the formula" (±40%).
  - The maintainer's measurement ÷ formula is 0.64–1.03 (pinned Z-Image bf16 and Wan2.2 5B: 0.64–0.72; the others: 0.98–1.03).
    VRAM is closer to the formula than speed (the quantity llmfit corrects).
  - On the low side, the lower bound of weights + fixed part usually acts first (the maintainer's lower bound ÷ formula is 0.73–1.0).
- The default settings used for judging are not changed to the settings of other users' measurements (a submission with small settings does not shrink the default settings themselves).
- The maintainer's and this PC's `torch_reserved` measurements (no CUDA context included; the 2 RTX 3070 ones) are used only as "at least", as before.
- Other users' `torch_reserved` measurements may be lowered down to the lower bound (weights + fixed part), because every bench submission is torch_reserved and could otherwise never be used to lower the need. Those below the lower bound do not shift, as in "Lower bound" above.

**How a measurement is picked** (`estimate.pick_measurement`): only measurements on the same GPU are candidates for other users' measurements. The order is:

1. Same settings
2. **A measurement on the same GPU as this PC**
3. The maintainer's or this PC's measurement
4. Other users' measurement
5. Not pinned
6. Larger GPU
7. Larger value

GPU names are compared after normalizing the presence of `NVIDIA` / `GeForce`, case and symbols (`estimate.gpu_key` = `contrib.gpu_slug`).

**Level** (#26, "decide the level by measurement", `estimate.measured_level`; the conditions changed in 0.11.0, full rules in [scoring.md](scoring.md) "When measurements set the level"):

- Only successful measurements on **the same GPU** as this PC (`estimate.same_gpu`) with the same settings are a basis for the level. The same rule for the maintainer's and other users' measurements (not extended to similar GPUs; up to 0.10 the maintainer's counted on any same-vendor GPU with at least the measured VRAM).
- The level is set only when **two or more independent sources** agree. The maintainer's rows are one source together, however many (the maintainer alone never sets the level); other users' rows count their `n_sources` (one per person who sent submissions, however many issues or pull requests; #113); they are added up. With one source, the values are only shifted.
- Agree: no OOM record (no `outcome: oom`, `n_oom` or `runs_evidence: false` for the same setup), each aggregated row agrees inside (`consistent`), and the spread across rows is within 15% of the median.
- **The amount used for the level = max(largest peak + context, weights + fixed part)**, the same value as the table's VRAM (`measured_level_vram_gb`). It uses the same physical floor as the values so that a fake small value cannot lift the level.
  - If this amount does not fit this PC's VRAM, the level is not set (for example, Wan2.2 5B's weights + fixed part is about 18.4 GB, which does not fit the RTX 5080's 15.9 GB).
  - Perfect needs 60% or less. A pinned measurement is not a basis for Perfect (up to Good).
- A `torch_reserved` value gets 0.5 GiB added for the context before the band is decided, and it can also be a basis for Perfect (0.9.0).
- Measurements only raise the level and never lower it (non-NVIDIA GPUs too since 0.11.0; #108 dropped their "up to Good" cap).

**Basis levels** (`Estimate.basis_detail`) and the number of sources (`Estimate.sample_count`; the number of submissions until 0.10):

- `basis` stays two-valued (measured / estimated) for backward compatibility.
- `sample_count` was only added to the JSON.
- How they show:
  - `info`: `measured n=3`
  - the mark on the table's VRAM: `m` (the same as the maintainer's; 0.11.0 dropped `c`, #108)
  - the reason: "measurements by other users (3 source(s); median 7.02 GB)" (「ほかの利用者の実測(出どころ 3。中央値 7.02 GB)」)

| level | basis | meaning |
|---|---|---|
| `measured` | measured | a measurement with the same settings (the maintainer's bundled measurements, this PC's saved bench results and other users' measurements (from one) are not told apart; 0.11.0 merged measured_community) |
| `calibrated` | estimated | an estimate shifted by a measurement with different settings (the maintainer's or other users'), or a same-settings value clamped by the lower or upper bound |
| `estimated` | estimated | a formula-only estimate (no measurement, precision changed, or no GPU) |

**Validation** (`contrib validate`, contrib.yml in CI): **only mechanical checks fail.**

- Failing checks:
  - Format, value ranges, personal information, duplicates, file name, size.
  - Value ranges: GPU VRAM at most 256 GiB, peak memory at most main memory × 1.05 + 1 GiB, no NaN, infinity or negative numbers (0.9.0).
  - A GPU name and `system.gpu.vendor` that name different vendors.
  - A `pci_id` with the wrong format or vendor.
  - A name that does not match the GPU in `hardware.PCI_DEVICE_NAMES` (an ID not in the table is a notice).
- Shown only as a "notice" above the job summary:
  - More than 40% off the formula (`deviation`).
  - All on GPU (`offload: none`) but smaller than weights + fixed part (`below_weights`).
  - The level changes on a PC with the same GPU as the submission (name, model ID, vendor; VRAM from the submission's `vram_gb`) × 16 / 32 / 64 GB of memory (`fit_change`; it also reports a level going up on the same GPU).
  - A model not in the list.
  - A GPU not in the table.
- The mechanism where a person checks each sha8 (`--allow-review`, `data/contrib/REVIEWED`) was removed.
- The safety mechanisms of contrib.yml remain:
  - It runs on pull_request.
  - Validation uses the code of the PR's target branch.
  - A PR that changes anything outside data/contrib fails.
  - Characters from a submission are not written to the log.

**Remaining risk:**

- A tampered submission, once merged, moves the estimates and levels as it is.
  - Examples found in the review of 2026-10-21: a submission with small values lowers the need by up to 76%; a +39% submission lowers a level.
  - Inside the lower and upper bounds, these are not prevented.
- What prevents them is **the merger's review.** For that, notices and the table 「ほかの利用者の実測で変わる段階」 ("levels that change with other users' measurements") of `contrib aggregate --report` are shown.
- One submission decides the value of its setup (outliers can be dropped only with 3 or more).

**Feeding calibration** (`python -m genmodelfit.calibrate`):

- Only consistent (`consistent`) measurements of other users are used. A single measurement is not enough, because the coefficients affect every model.
- If the way of measuring is the same as the coefficient fit (`calibrate.CALIBRATION_METRIC`; currently the unrecorded way of the 9 RTX 5080 measurements), they enter the fit and leave-one-out.
- Bench submissions are `torch_reserved`, so for now they enter only the **cross-check** (the table above: the error when predicted with the coefficients fitted on all data).
- Once a few `torch_reserved` measurements per type exist, the fixed part will be split by way of measuring and refitted (until then they are not mixed).

**Weekly check** (`genmodelfit contrib aggregate --report`, called by the weekly automatic PR of #19):

- It re-aggregates data/contrib as of that moment, redoes the calibration, and tabulates the LOO error per type and the cross-check error.
- It warns when a threshold is exceeded (default 25%, `--threshold`; `--fail-on-breach` gives exit code 3).
- What enters the judgment:
  - The LOO error of types that can be evaluated (2 or more unpinned measurements).
  - The cross-check error of other users' measurements.
- The maintainer's cross-check rows (the 2 RTX 3070 rows below) are known misses, so they are shown in the table but not counted in the judgment.

**Inflation:** if the same person submits the same setup many times, the count of submissions is inflated.

- The submissions themselves carry no information that identifies a person. Since 0.12.0 (#113) the sources are
  counted by GitHub account (the account a pull request came from, or the author of a measurement issue), so one
  account counts as one source however many it sends; a different account still counts as someone else
  ([scoring.md](scoring.md)).
- The same content (hash) is rejected as a duplicate, but a re-measured different value cannot be rejected.
- The person reviewing the PR checks whether the same setup appears several times in the same PR.

### Bench on non-NVIDIA GPUs (0.9.0)

Non-NVIDIA GPUs (AMD, Intel, Apple) have 0 measurements, so they get no verified-kind mark (✓) (the level rules are the same; [scoring.md](scoring.md) "Verified GPU kinds").
To make measurements easier to collect, the bench's "is the GPU idle?" check also uses each vendor's tools (`gpustat.py`).

- AMD on Linux: amd-smi → rocm-smi
- Intel on Linux: xpu-smi
- AMD and Intel on Windows: performance counters
- Apple: ioreg

If the usage can be read, it refuses at 30% usage, as on NVIDIA.

When no tool exists or the usage cannot be read (for example Linux without rocm-smi), it measures without asking for `--force`.

- It checks only these two things:
  - The ComfyUI queue is empty.
  - GPU memory used by anything other than ComfyUI is at most half of the total (not checked for Apple's unified memory).
- It leaves 「使用率は確かめていない」 ("usage was not checked") in warnings and note.
- If anything other than ComfyUI uses more than half of GPU memory, it stops unless `--force` is given.

**`standard` stays true.** The reasons:

- `standard` means "the official template as is, no other work mixed in ComfyUI, and every node ran", and looks only at what happens in ComfyUI's queue.
  The queue is checked before the start, just before sending, and during the measurement.
- The value used for estimates (`peak_vram_gb`) is what the ComfyUI process's torch allocated, and it hardly changes even if other apps use the GPU
  (what changes is the time, and the note about the GPU-wide increase).
- A measurement on NVIDIA taken with `--force` and no check has not changed `standard` either (kept the same).

Other points:

- A run where contention with other apps caused a memory shortage and offloading or failure shows in `offload` and `outcome`, so it can be told apart there.
- When ComfyUI runs on another PC, `--force` is needed, as on NVIDIA (this PC's tools can check nothing).
- The measurement keeps `gpu_backend` (cuda / rocm / xpu / mps).
- The submission holds `system.gpu.vendor`, `backend`, and, for AMD and Intel, the PCI model ID (`pci_id`).
- If xpu-smi shows 2 or more Intel GPUs, it cannot tell which one is ComfyUI's, so the usage is treated as unreadable.

With Apple's MPS, ComfyUI's GPU name is `mps`.

- If ComfyUI runs on the same PC, the bench uses this PC's chip name read with system_profiler as `gpu_name` of the measurement and the submission.
  - Example: `Apple M2 Pro`. Only names matching the form of `hardware.APPLE_CHIP_NAME`; serial numbers and the like are not included.
- The judgment compares with this PC's GPU name (the same system_profiler chip name), so a measurement of the same chip is a basis for the level.
- With ComfyUI on another PC, or if the chip name cannot be read, it stays `mps` (with a warning).
- An `mps` measurement is used only as vendor apple, and never counts as a measurement of the same GPU.
- `contrib validate` limits GPU names of vendor apple to a chip name or `mps`.

## Cross-check (measurements and public figures used neither for fitting nor for levels)

### The maintainer's RTX 3070 8GB measurements (bench, 2026-10-19 UTC)

The 2 bundled rows (BiRefNet, MoGe) are torch reservations measured by bench with dynamic VRAM (`vram_metric: torch_reserved`,
`offload: dynamic`): working memory only, without the weights (#100; "How measurements are used" 7 above).
`python -m genmodelfit.calibrate` builds the cross-check below the table in "Results" (compared with the working-memory estimate).

- Both are models without a per-stage formula (tools, 3D), so they are compared with the per-modality coefficient formula.
- The miss (BiRefNet −81%) is because the image coefficient was fitted to diffusion models and does not represent a tool's working memory.

### RTX 3070 sweep (2026-10-27, **not used as evidence**)

SDXL (512, 768, 1024 px), Wan 2.1 1.3B (512 px, 17 / 49 / 81 frames) and ACE-Step 1.5 (10, 30, 60 s) were measured.
**Not used as evidence of accuracy**: the whole GPU was pinned at its limit (7.2 to 7.5 / 8 GB), and other work used 1.6 to 6.6 GB before the runs.
SDXL at 1024 px started at 60% prior utilization (against the shared-machine rule). To be measured again
(only when the GPU is idle, keeping ComfyUI's log of switching the VAE to tiles; the bench's pre-run check will also be stricter).

| Model | Settings | Measured GiB (torch_reserved, dynamic VRAM) | Per-stage formula max(S, V_eff, T) | Reference: untiled V |
|---|---|---:|---:|---:|
| image_sdxl_simple | 512 px | 0.22 | 1.06 | 1.06 |
| image_sdxl_simple | 768 px | 1.31 | 1.06 | 2.39 |
| image_sdxl_simple | 1024 px | 2.28 | 1.06 | 4.25 |
| text_to_video_wan | 512 px, 17 frames | 2.69 | 0.28 | 3.42 |
| text_to_video_wan | 512 px, 49 frames | 3.06 | 0.72 | 3.42 |
| text_to_video_wan | 512 px, 81 frames | 2.94 | 1.16 | 3.42 |
| audio_ace_step_1_5_checkpoint | 10 s | 1.00 | 0.95 | 0.95 |
| audio_ace_step_1_5_checkpoint | 30 s | 2.72 | 0.98 | 2.86 |
| audio_ace_step_1_5_checkpoint | 60 s | 4.94 | 0.98 | 5.72 |

### RTX 5080 16GB measurements by another user (#102, 2026-10-27, **used neither for fitting nor for levels**)

Measured by another user with the Issue #102 script (ComfyUI 0.43.0, Windows 11). The same models were run at several
settings, with the default dynamic VRAM (phase 1) and with `--disable-dynamic-vram` (phase 2). Before each run the GPU
already used 1.8 to 1.9 GB (ComfyUI idling and the Windows desktop). They did not come through the contribution
(contrib) path, so they are not in the measurement list.

**Phase 1: working memory** (torch_reserved; with dynamic VRAM the weights are not included)

| Model | Settings | Measured GiB | max(S, V_eff, T) | Untiled V |
|---|---|---:|---:|---:|
| image_sdxl_simple | 512 / 768 / 1024 / 1536px | 0.22 / 1.41 / 2.75 / 6.09 | 1.06 / 1.06 / 1.06 / 1.06 | 1.06 / 2.39 / 4.25 / 9.57 |
| text_to_video_wan | 832×480, 17 / 33 / 49 frames | 3.88 / 4.50 / 4.28 | 0.42 / 0.76 / 1.10 | 5.21 |
| text_to_video_wan | 512px, 49 frames | 2.78 | 0.72 | 3.42 |
| video_wan2_2_5B_ti2v | 832×480, 49 / 81 frames | 5.81 / 5.25 | 0.98 / 0.98 | 5.95 |
| audio_ace_step_1_5_checkpoint | 10 / 30 / 60 / 120 s | 0.44 / 2.81 / 5.16 / 9.78 | 0.95 / 0.98 / 0.98 / 0.98 | 0.95 / 2.86 / 5.72 / 11.44 |
| image_z_image_turbo_int8 | 768 / 1024 / 1536px | 0.84 / 1.28 / 5.00 | 1.06 / 1.06 / 2.02 | 2.39 / 4.25 / 9.57 |

- The decode (VAE) working memory is 0.5 to 1.0 times the untiled V (in runs where V exceeds 1 GB). ComfyUI's log shows
  no run where the VAE was split into tiles (phase 1 or 2). Tiling is the fallback after an OOM in the regular decode
  (sd.py:1314@426f5ee) and is not used while the VRAM suffices. The RTX 3070 sweep (above) points the same way.

**Phase 2: could ComfyUI keep everything loaded?** ("loaded partially" in the log = part was unloaded)

| Run | Part unloaded | Usable GiB | All on GPU (V_eff) | Everything kept loaded (V) |
|---|---|---:|---:|---:|
| image_sdxl_simple 512 / 768 / 1024px | no | 13.96 | 9.00 / 9.00 / 9.00 | 9.00 / 10.33 / 12.19 |
| image_sdxl_simple 1536px | **yes** | 14.03 | 9.00 | 17.50 |
| audio_ace_step_1_5_checkpoint 10 / 30 s | no | 13.93 / 13.99 | 11.77 / 11.79 | 11.77 / 13.68 |
| audio_ace_step_1_5_checkpoint 60 / 120 s | **yes** | 13.93 | 11.79 / 11.79 | 16.54 / 22.26 |
| text_to_video_wan 512px, 49 frames | no | 13.98 | 11.34 | 14.04 |
| text_to_video_wan 832×480, 17 / 33 / 49 / 81 frames | **yes** | 13.88 to 14.04 | 11.05 to 12.39 | 15.83 |
| image_z_image_turbo_int8 768 / 1024 / 1536px | **yes** | 13.90 to 13.98 | 13.87 / 13.87 / 14.82 | 15.20 / 17.06 / 22.38 |
| video_wan2_2_5B_ti2v, 3 runs | **yes** | 13.91 | 19.35 to 20.37 | 24.32 to 31.80 |
| utility_moge_depth_estimation, 3d_hunyuan3d_image_to_model | no | 14.24 / 14.03 | 2.33 / 6.85 | (no per-stage formula) |

- Whether "everything kept loaded (V)" exceeds the usable amount predicts "part unloaded" in 20 of 21 runs (the miss is
  text_to_video_wan 512px, 49 frames, by 0.06 GB). "All on GPU (V_eff)" predicts only 12. So the all-on-GPU mode is now
  chosen only when "everything kept loaded" fits (#102; "Tiles" above).
- The usage prediction (the "Results" table above) keeps V_eff (since #148 the Oobleck VAE alone uses the untiled V; "Tiles" above). The working memory actually used is smaller than V, and
  using V would worsen the largest LOO error from 12.5% to 34.3% (V is ComfyUI's estimate for deciding how to load, not
  the amount used).
- The judgement compares with 98% of the total (15.6 GB) instead of the usable amount. Counted at 98% it still predicts 20 of
  21 runs (the miss is image_z_image_turbo_int8 768px: kept loaded 15.20 GB fits in 98%, yet part was unloaded).
- bench had marked the 8 runs without "part unloaded" as `offload: lowvram`. The script starts ComfyUI with `--verbose DEBUG`, and
  bench matched "lowvram: loaded module regularly" (model_patcher.py:1102@426f5ee), which is logged when a model loads fully (so
  a full-load measurement was taken for one pinned at the limit). bench now marks `lowvram` only when ComfyUI was started with
  `--lowvram` / `--novram` (#102).
- In phase 1, two runs (text_to_video_wan 832×480 81 frames, video_wan2_2_5B_ti2v 1280×704 81 frames) could not be
  measured because ComfyUI stopped responding (it stalls after aimdo logs `budget_deficit`; the same run stalled twice in a
  row). Phase 2 measured them without stalling (13.41 / 12.78 GiB). This is ComfyUI's dynamic VRAM behaviour and
  genmodelfit cannot fix it. If bench stalls, restart ComfyUI with `--disable-dynamic-vram` and measure again
  ([CONTRIBUTING.md "1. Measure"](../CONTRIBUTING.md#1-measure)).

### Measurement variance (#131)

On the same PC with the same settings, bench's peak VRAM (`torch_reserved`, dynamic VRAM) differed from run to run.
SDXL at 1024 px was usually 2.28 GB and sometimes 1.28 GB. In the RTX 5080 submissions ACE-Step 30 s (0.47 GB) was below 10 s (1.22 GB).
Measured again on 2026-10-31 on an RTX 3070 8GB with ComfyUI 0.19.3 (only while the GPU was idle; `/free` before every run).

**Cause: the working-memory peak is short, and reading every 0.2 s misses it in some runs.**

- The peak comes at the end of the final VAE decode and disappears when ComfyUI returns torch's free memory after the run (soft_empty_cache).
- Read about 60 times a second, the peak lasted about 0.19 s for SDXL 1024 px, about 0.08 s for 768 px, 0.02 to 0.05 s for ACE-Step 10 s,
  0.2 to 0.3 s for ACE-Step 30 s and about 0.45 s for SDXL 1536 px.
- 1.28 GB is the value that lasts about 0.03 s just before the peak (2.28 GB). A run that misses the peak records it (or 1.19 GB) as the maximum.
- Loaded models, first or later run and `/free` do not explain it (the peak had the same shape in 8 runs with ComfyUI started
  afresh each time and in 10 runs on one ComfyUI).

From the peak shapes (recorded about 60 times a second), the value read every 0.2 s was replayed 2,000 times each with different start times:

| Run | 0.2 s apart (up to 0.13.2) | 0.015 s apart (0.14.0) |
|---|---|---|
| SDXL 1024 px (4 recordings) | 2.28 GB in 94 to 100%, 1.28 / 1.19 GB in 0 to 6% | 2.28 GB in 100% |
| ACE-Step 10 s (3 recordings) | spread over 0.41 to 1.31 GB (the true peak in 15 to 28%) | the true peak in 99 to 100% |
| ACE-Step 30 s (3 recordings) | one value per recording | the same |

**What changed (0.14.0)**

- bench reads `/system_stats` 0.015 s apart (`bench.POLL_INTERVAL`). With the time spent waiting for the answer, that is about every 0.03 s in practice (30 to 36 reads a second). The resident memory is still read every 0.2 s.
  Reading this often did not change the step or decode times (SDXL 0.4 s/step, ACE-Step 0.125 s/step).
- A peak too short to be read 3 times (the third-largest value is more than 0.1 GiB and more than 10% below the maximum)
  is not a standard measurement (`standard: false`; used neither for the cross-check nor for levels). A peak read 3 times
  lasted at least twice the polling interval, so every run reads it at least once.
  - The peak value is shown in the result (the display and `--json`), but `measure` and `share` neither save nor send
    non-standard results. `bench` saves it only with `--save-nonstandard` or `--output`.
  - Only runs that finished (`outcome: ok`) are checked. Out-of-memory results and failure records are kept and sent as before.
  - `measure` and `share` remember a run that was non-standard only because of a brief spike as "tried" in `brief_peaks.json` in the cache (not sent).
    The same model and settings on the same GPU with the same list files are not suggested again (so a model whose peak is always brief, like ACE-Step 10 s, is not measured over and over).
    **Limitation**: such runs cannot be used for the cross-check or for levels. For now they are not suggested again even when this PC's ComfyUI version changes
    (the marker keeps the ComfyUI version, so once re-measuring on a new ComfyUI version exists (#147) it can treat them like other measurements).
    Deleting the marker (`brief_peaks.json`) makes them suggested again.
- 4 submissions measured every 0.2 s that probably missed the peak (RTX 3070 SDXL 512 px, RTX 5080 SDXL 512 px,
  ACE-Step 10 s and 30 s) are listed with reasons in the bundled `contrib_nonstandard.json`, and `contrib aggregate` does not use their values
  (the submission files are not edited, because their sha256 would change). The RTX 3070 SDXL 512 px row (0.31 GB) is gone from the bundled summary.
  The RTX 5080 submissions are not in the summary yet (they are left out when the next release adds them).
  Refitting (`python -m genmodelfit.calibrate`) does not change the coefficients or LOO (the summary row was not consistent, so it was not in the cross-check either).

Measurements after the change (RTX 3070, ComfyUI 0.19.3):

| Run | Runs | Peak VRAM | Reads at the peak | Standard |
|---|---:|---|---|---|
| SDXL 1024 px | 15 (including 2 with `measure`) | all 2.28 GB | 5 to 7 | yes |
| SDXL 512 px | 3 | all 0.59 GB | 2 | no (brief spike; the 0.31 GB in the bundled summary missed the peak) |
| SDXL 768 px | 6 | all 1.34 GB | 3 to 4 | yes |
| SDXL 1536 px | 3 | all 4.97 GB | 9 to 13 | yes |
| ACE-Step 30 s | 4 | 2.34 / 2.34 / 2.34 / 2.72 GB | 4 to 9 | yes |
| ACE-Step 10 s | 8 (including 1 with `measure`) | 1.09 to 1.31 GB | 1 to 2 | no (brief spike) |

- Before the change (every 0.2 s), runs on the same PC: SDXL 1024 px gave 2.28 GB in all 23 runs (1.28 GB is rare, as the replay says).
  ACE-Step 10 s varied: 0.59 / 1.06 / 0.66 GB.
- ACE-Step 30 s at 2.72 GB is a real value: the peak lasted 0.28 s and was read 9 times. It is a run-to-run difference on ComfyUI's side (about 0.4 GB), which no way of reading removes.
- The ACE-Step 10 s peak is very short and this way of reading cannot catch it every time. The values in the sweep above (10 s 1.00 GB) and in #102 (10 s 0.44 GB)
  are probably low values that missed the peak.
- The RTX 5080 submission of ACE-Step 30 s (0.47 GB) is probably the same: on a faster GPU the peak is even shorter (the decode took 1.04 s),
  and reading every 0.2 s probably did not read it once. Measuring again with 0.14.0 will tell.

### ACE-Step 1.5 song length (#148, **used neither for fitting nor for levels**)

Working-memory-only measurements (torch_reserved, dynamic VRAM) against the Oobleck VAE's V (untiled) and V_tile. In GiB.

| length | V | V_tile | RTX 3070 8 GB (issue #148, ComfyUI 0.19.3) | RTX 5080 16 GB (#102) | RTX 5080 16 GB (PR #141) |
|---|---:|---:|---:|---:|---:|
| 10 s | 0.95 | 0.95 | 0.66 (not used) | 0.44 (not used) | 1.22 (not used) |
| 30 s | 2.86 | 0.98 | 2.34 (re-measured: 2.34, 2.72) | 2.81 | 0.47 (not used) |
| 60 s | 5.72 | 0.98 | 5.00 | 5.16 | - |
| 120 s | 11.44 | 0.98 | 9.31 (above 8 GB, spilled into shared memory) | 9.78 | 9.50 |

- From 30 s up, measured ÷ V is 0.81 to 0.98. It grows in proportion to the length, which V_tile (constant) cannot explain.
- The 30 s run of PR #141 (0.47 GB) started with the previous run's allocation still held (torch 0.47 GB), so it is
  not used (#131: measurements fluctuate).
- **No 10 s point is used** (not even to check the formula). The #131 investigation found that the working-memory peak lasts only 0.02 to 0.05 s at 10 s and 0.2 to 0.3 s at 30 s, so bench, which reads every 0.2 s, missed it. Re-measured on the RTX 3070 with fast reads, 10 s gives 1.09 to 1.31 GB, but it is too brief to read reliably. The 60 s and 120 s peaks last longer and are the most trustworthy.
- Re-measured, 30 s still gives 2.34 GB (sometimes 2.72 GB, a real difference between runs).
- At 10 s V and V_tile are equal, so #148 does not change the value.
- The ACE-Step 1.5 author measurement (20 s, 10.8 GB) is below the weights + fixed floor (dynamic VRAM kept some
  weights in main memory), so it says nothing about the working memory. The +17.8% in "Results" above comes from
  adding V at 20 s (1.9 GB) (+9.2% before #148).

### Public figures (not used for fitting)

| Model | Public figure | genmodelfit (per-stage formula, all on GPU) | Difference |
|---|---|---:|---|
| HunyuanVideo 720×1280, 129 frames | 60 GB (README of Tencent-Hunyuan/HunyuanVideo, "GPU Peak Memory"; one 80 GB GPU) | 51.2 GiB | −15% (73 GiB with the untiled V) |
| HunyuanVideo 544×960, 129 frames | 45 GB (same) | 44.0 GiB | −2% (56 GiB with the untiled V) |
| CogVideoX-5B | diffusers BF16 from 5 GB (with sequential CPU offload, VAE tiling and slicing; about 3× without them), 15 GB with multiple GPUs, SAT BF16 26 GB (README of THUDM/CogVideo) | VOID (two CogVideoX 5B models) at 720×480, 49 frames: all on GPU 41.8, swapped in turn 14.6, offload 10.0 GiB | not the same setup, so not comparable (reference only) |

- These are values from implementations other than ComfyUI and other precisions, so they are not used for fitting.
