# The gate: when a model may be auto-recommended

**English** · [日本語](gate.ja.md)

The rule behind `auto: true` ("Verified" in `info`): which models may be shown to PC beginners without a human checking.
This page is for app developers who use the list and for contributors who change the rule or the list.

## Contents

- [Usage](#usage)
- [Checks](#checks)
- [Allowlist (default)](#allowlist-default)
- [How file licenses are read](#how-file-licenses-are-read)
  - [Auto mistakes found in review (#14, 2026-10-20)](#auto-mistakes-found-in-review-14-2026-10-20)
- [Checking URLs (`url_reachable`)](#checking-urls-url_reachable)
  - [Check expiry (`max_head_age_days`)](#check-expiry-max_head_age_days)
- [ComfyUI version (`comfyui_version`)](#comfyui-version-comfyui_version)
- [Uncensored models (`content`)](#uncensored-models-content)
- [What can be auto](#what-can-be-auto)

The app (AI生成キット) shows recommended models to PC beginners **without a human checking**.
So the gate is conservative: anything unknown (unknown license, HEAD not checked, unknown version and so on) is held.

- Only models that meet all the checks get `auto: true`.
- A held model still appears in the list, in `recommend` (without `--auto-only`), in `fit` and in `info`, as before.
- **Wrongly marking a model auto is the worst outcome.** When in doubt, hold it.

The implementation is `src/genmodelfit/gate.py`.

- The result is not stored in the list (db.json); it is computed every time.
- The list stores only what `update` recorded: the HEAD results, the latest ComfyUI version and the review mark (`review`).

## Usage

```powershell
genmodelfit gate [--json] [--modality image] [--only-auto | --only-held]   # counts per check
genmodelfit recommend --auto-only --vram 8 --comfyui-version 0.40.0         # only what can be shown automatically
genmodelfit info image_z_image_turbo --comfyui-version 0.40.0               # result and reason per check
# Policy overrides (shared by recommend / fit / info / gate)
--comfyui-version 0.40.0                # the ComfyUI version you use (the app must always pass it; see "ComfyUI version" below)
--license-allowlist mit,apache-2.0      # or a file with one entry per line (text after # is a comment)
--max-head-age-days 30                  # trust HEAD checks up to this many days (default 30; none = no limit)
--allow-uncensored-auto                 # let uncensored models be auto too
```

`--limit` / `-n` of `recommend` / `fit` / `info` / `search` / `diff` must be 1 or more (0 or a negative number is an error, exit code 2).

```python
from genmodelfit.db import load
from genmodelfit.gate import GatePolicy, evaluate_offline

db = load()
policy = GatePolicy(comfyui_version="0.40.0")  # the ComfyUI version the app bundles / installs
r = evaluate_offline(db.get("image_z_image_turbo"), policy, sources=db.sources)
r.auto, r.held_by, r.to_dict()
```

The shape of `to_dict()`:

```json
{"auto": true, "held_by": ["<id of each failed check>"], "comfyui_version": "<version compared>",
 "checks": [{"id": "", "passed": true, "detail": ""}]}
```

- `checks` follows the order of the table below. A ComfyUI model (`runtime` is `comfyui` or null) has 10: every row except `app_runtime`.
- For a model whose `runtime` is neither `comfyui` nor null, one `app_runtime` takes the place of `official_workflow`, `custom_nodes` and `comfyui_version`, so there are 8.
- `held_by[0]` (the first failed check) appears as `held:<id>` in `gate --only-held`; `info` shows every check with [ok] / [NG]. The tables of `recommend` / `fit` no longer have a Verified column (#84).
- `detail` is text for people, in the display language (`en` / `ja`; `--lang`, `GENMODELFIT_LANG`). The wording may change; machines should read `id` and `passed`.

## Checks

| id | Passes when | Example of a hold |
|---|---|---|
| `license` | Every file's license is on the allowlist, including LoRAs, upscalers and image encoders. The license is decided as in "How file licenses are read" below | `other`, openrail family, non-commercial, community licenses, llama / gemma / DINOv3 terms, unknown |
| `gated` | Every file has `gated: false`. A component whose original source asks for an access request (agreeing to terms), such as DINOv3 and SAM 3, is also held | `true`, unknown (`null`), a component whose original source asks for a request |
| `official_workflow` | An official ComfyUI template (`source: comfyui-template`) whose `openSource` is not false | An entry that exists only on Hugging Face |
| `custom_nodes` | The template's `requiresCustomNodes` is empty | Custom nodes are needed, or there is no template |
| `comfyui_version` | The template's `minComfyUIVersion` ≤ the version you use (compared numerically: `0.3.9 < 0.3.10`) | Version unreadable, not stated, or the version you use is unknown |
| `app_runtime` | (Only for models whose `runtime` is neither `comfyui` nor null; it takes the place of `official_workflow`, `custom_nodes` and `comfyui_version`.) Never passes. This list cannot verify the runtime (piper, irodori-tts, supertonic), so the app does (0.5.0, #36) | The models added by hand in `src/genmodelfit/data/manual.json`: Tsukuyomi-chan (つくよみちゃん, runtime `piper`), Irodori-TTS (runtime `irodori-tts`) and Supertonic 3 (runtime `supertonic`) |
| `url_reachable` | Every file's URL has a record of an anonymous HEAD returning 200 / 302 / 307 (a redirect to `/api/resolve-cache`), and that check is newer than `max_head_age_days` (default 30 days) | 401 (gated), 404, no record (unchecked), no URL, an old check, no check time |
| `sha256` | Every file has a 64-digit hexadecimal sha256 | Civitai files and similar |
| `content` | Not uncensored (`content: uncensored`) | Uncensored (can be lifted with `--allow-uncensored-auto`) |
| `size_known` | Every file's size is known and the file list is complete (`files_complete` is not false) | Size unknown, no files listed |
| `reviewed` | The review mark (`review`) is not awaiting review (`pending`). Old lists without the mark (up to 0.5.0) pass. It is always the last check (0.6.0, #42; [schema.md](schema.md), "review") | A new entry added by `update` (rebuild, `--heads-only`, `--fields-only`, `--inspect-only`); a license that moved to the permissive side; a drop in the required amount; newly uncensored; a file change (`files_changed`: a change or loss of a URL (including the query) or sha256, a size increase or decrease, files added or removed, repo_id; 0.9.0; [sources.md](sources.md), "Review marks") — held until confirmed with evidence by `genmodelfit review approve` |

- An entry with no files at all is held on every file-related check (an empty list does not pass).
- A template with no `openSource` entry is treated as a template that runs locally.
  - These are old templates such as SDXL, FLUX.1 schnell and Wan 2.1: 51 entries in the bundled list of 2026-10-21T06:20Z.
  - `update` treats them the same way.
  - `update` does not put API templates (those starting with `api_`) or `openSource: false` into the list.

## Allowlist (default)

`apache-2.0`, `mit`, `bsd-2-clause`, `bsd-3-clause`, `cc0-1.0`, `unlicense`.

How they were chosen: **commercial use is allowed, and there are no usage restrictions (such as a list of banned uses), no revenue or user-count thresholds, no access requests and no attribution duties** (the "1 permissive" tier of `hf.license_tier`).

- Their conditions (keep the copyright notice and license text) apply when you **redistribute** the weights.
- genmodelfit and the apps that use it do not distribute weights. The user's PC fetches them directly from Hugging Face, so nothing is left for the app to comply with.

Not included:

- `cc-by-*` (such as `cc-by-4.0`): in the list's tiering (#13) this is the "2 attribution duty" tier.
  - An app that can show attribution can add it, for example `--license-allowlist apache-2.0,mit,bsd-2-clause,bsd-3-clause,cc0-1.0,unlicense,cc-by-4.0`.
  - The first version of #14 had it in the default. It was removed to match the #13 tiers (the 2026-10-20 list has no cc-by-4.0 file).
- RAIL licenses such as `openrail`, `openrail++`, `creativeml-openrail-m`: they oblige you to pass the use restrictions (Attachment A) on to users. A person must read the terms and put them in the app's terms.
- Non-commercial (`cc-by-nc-*`, `flux-1-dev-non-commercial-license` and so on): cannot be used in an app that is sold.
- Community licenses with revenue or user-count thresholds (`stabilityai-ai-community`, `stable-audio-community`, `tencent-hunyuan-community`, `ltx-2-community-license`, MiniMax's and so on): above the threshold a separate contract is needed, and some add attribution or regional conditions.
- `llama*`, `gemma`, `dinov3-license`: their own terms (user-count thresholds, use restrictions, attribution).
- `other` (a custom license that is none of the above) and unknown: not passed, because nobody has read them.
  - `other` is matched by `license_name`.
  - A license that a person has read and judged fine (for example `mit-and-apache-2.0`) can be let through by adding its name to the allowlist.

## How file licenses are read

The `license` in the list sometimes keeps the notation of the repository where the file lives.

- A repository can contain parts of other models (text encoders, image encoders, detectors and so on), and their real license can differ from the repository's.
  - Example: `dino_v3_vit_h` in VAST-AI/TripoSplat (notation mit) is under the DINOv3 terms.
  - Example: `rt_detr_v4-x-hgnet` in Comfy-Org/SDPose is not SDPose; it is RT-DETRv4.
- A card's notation can also be more permissive than the original model's.
  - Example: the SDPose card says mit, but its U-Net was made from Stable Diffusion 2.

The gate does not rewrite the list. It reads in this order during the judgment, and stops at the first rule that decides:

1. **A file name that is a restricted component** is read under that component's terms (held), whichever repository it is in, taking priority over the list's value:

   | File name | Read as | Source |
   |---|---|---|
   | `llama` / `llava` | llama3.1 | https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct |
   | `gemma` 1 to 3, `t5gemma` | gemma | https://ai.google.dev/gemma/terms. Gemma 4 is Apache-2.0 (https://ai.google.dev/gemma/docs/gemma_4_license), so it is not matched |
   | `dino_v3`, `dinov3` | other (dinov3-license); the original source asks for a request | https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m (gated: manual). The original text https://ai.meta.com/resources/models-and-libraries/dinov3-license requires a copy of the terms and the "Built with DINOv3" notice when distributing, and restricts use |
   | `sam3`, `sam3.1` | other (sam-license); the original source asks for a request | https://huggingface.co/facebook/sam3 (gated: manual) |
   | `depth_anything_v2_vit{b,l,g}` | cc-by-nc-4.0 | https://huggingface.co/depth-anything/Depth-Anything-V2-Large |
   | Lumina 2 family bundles (`lumina_2.safetensors`, `…lumina…all_in_one`, `NetaYume…all_in_one`) | gemma | They include the text encoder Gemma 2 2B (cards: https://huggingface.co/neta-art/Neta-Lumina and https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0) |

   Rows of `hf.COMPONENTS` that are not permissive or that ask for a request (such as qwen-research for Qwen2.5-VL-3B) are treated the same way.
2. **A component whose original source cannot be verified** (`hf.UNVERIFIED_COMPONENTS`) is unknown: `rt_detr_v4`.
   RT-DETRv4's code at https://github.com/RT-DETRs/RT-DETRv4 is Apache-2.0, but the weights were made by distillation with DINOv3 as the teacher, and no weight license is stated.
3. **A lineage whose original license cannot be read** (`hf.LINEAGES`) is unknown.
   The name is looked up in the file name and in the path the license was read through (`license_from`, `license_path`; names of redistribution repositories are excluded):

   | Lineage | Source |
   |---|---|
   | `sdpose` | The card of https://huggingface.co/teemosliang/SDPose-Wholebody: "U-Net backbone initialized with Stable Diffusion v2 weights" (notation mit) |
   | `lotus` (lotus-depth / lotus-normal) | The training config of https://github.com/EnVision-Research/Lotus starts from `stabilityai/stable-diffusion-2-base` (the notation of https://huggingface.co/jingheya/lotus-depth-d-v1-1 is apache-2.0) |
   | `sd2` (sd2, sd21, stable-diffusion-2) | The Stable Diffusion 2 originals (stabilityai/stable-diffusion-2 and -2-base) could not be read anonymously on 2026-10-20 (401). The copy https://huggingface.co/sd2-community/stable-diffusion-2-base is notated openrail++ |
   | `void-model` (void_pass, netflix/void-model) | The card of https://huggingface.co/netflix/void-model: "VOID is built on CogVideoX-Fun-V1.5-5b-InP". The original LICENSE is The CogVideoX License (VOID's notation is apache-2.0; #28) |

   A value more permissive than the family table (`hf.FAMILIES`: SDXL, SD1.5, Marigold v1 (openrail++) and so on) is also unknown.
4. If the license **could only be read from the redistributor's notation** (`license_from` is a redistribution repository), it cannot be verified and is held.
   Components whose origin is known are read under their original source's license:

   | File name | License | Original source |
   |---|---|---|
   | `umt5_xxl…` | apache-2.0 | https://huggingface.co/google/umt5-xxl |
   | `t5xxl…`, `t5-v1_1…`, `t5-base…` | apache-2.0 | https://huggingface.co/google/t5-v1_1-xxl |
   | `clip_l…` | mit | https://github.com/openai/CLIP |
   | `clip_g…` | mit | https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k |
   | `clip_vision_h…` | mit | https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K |
   | `wan_2.1_vae…`, `Wan2_1_VAE…` | apache-2.0 | https://huggingface.co/Wan-AI/Wan2.1-T2V-14B |

5. **A component of another lineage.** The file name carries a mark of another model (`hf.FOREIGN_MARKERS`: dino, sam, siglip, clip, t5, qwen, llama, gemma, mistral, birefnet, depth-anything, moge, detr, flux, wan, trellis, hunyuan, stable-diffusion, lumina, ltx and so on), and that mark does not appear in the name of the hosting repository or of the path the license was read through.
   - Then it passes only when a **row whose original source has been verified** (`hf.COMPONENTS`, `hf.FAMILIES`) matches the file name. For a row of the components table, the license is the original source's.
   - If no row matches, it is unknown.
   - Examples: TripoSplat's `flux2-vae` is the FLUX.2-VAE row of the components table (apache-2.0). Pixal3D's `trellis_2_shape_vae` is microsoft/TRELLIS.2-4B (mit). JoyAI's `qwen3vl_8b_joyimage_edit` is the Qwen3-VL row of the family table (apache-2.0).
6. Otherwise, the list's value (read from the original source).

`update` decides with the same tables (components, unverifiable components, lineages, components of another lineage) and sets what it cannot verify to `null` ([sources.md](sources.md), "Licenses").
This reading in the gate is a second layer of protection, so that a list built with an older rule can still be held at judgment time.

### Auto mistakes found in review (#14, 2026-10-20)

The first version marked 93 entries auto, and the following 10 were mistakes (they were auto while disagreeing with their cards).
Now they are held by both the list rule (update) and the gate. `tests/test_gate_real.py` has regression tests.

| Model | Why it was wrong |
|---|---|
| `3d_pixal3d_multi_views`, `3d_pixal3d_trellis2_image_to_model` | `dino_v3_L_naf_fp32` in Comfy-Org/Pixal3D carried the mit of the original TencentARC/Pixal3D (the DINOv3 terms and a request are required) |
| `3d_triposplat_image_to_gaussian_splat` | `dino_v3_vit_h` in VAST-AI/TripoSplat carried the repository's mit |
| `utility_sdpose_ood_image_to_pose`, `utility_sdpose_multi_person`, `utility_sdpose_multi_person_video`, `utility_sdpose_ood_video_to_pose_map` | The SDPose card says mit, but it was made from Stable Diffusion 2. The `rt_detr_v4` in the same repository also got SDPose's mit |
| `image_lotus_depth_v1_1`, `image_qwen_image_instantx_controlnet` (contains Lotus) | The Lotus card says apache-2.0, but it was trained from stable-diffusion-2-base |
| `image_netayume_lumina_t2i` | The bundle file `NetaYumev35_pretrained_all_in_one` contains the text encoder Gemma 2 2B (found in a re-check after the review) |

## Checking URLs (`url_reachable`)

`genmodelfit update` sends an anonymous HEAD to the Hugging Face URL of every file. It records the response code in `files[].head_status` and the time in `head_checked_at`.

- The same URL is checked only once, up to 8 at a time. 429 is waited out following `Retry-After`.
- Nothing is sent to non-HF hosts such as Civitai (they stay unchecked and held).

Redirects are basically not followed.

- Exception: a **307 whose target is not `/api/resolve-cache/…`** (a renamed repository). A missing file also returns 307, so one HEAD is sent to the target and its response is recorded (`fetch.follow_rename`).
- If that target is another redirect, it stays unchecked (`null`).
- So the only 307 left in the list is a redirect to `/api/resolve-cache`, and the gate treats 200, 302 and 307 as "fetchable".
- The breakdown of responses is in `sources.head_check` of the list.
  - In the bundled list of 2026-10-21T06:20Z, 487 URLs got a HEAD: 302 for 461, 307 for 3, 401 for 23. Nothing is sent to Civitai's 6 locations.
  - The 6 entries that returned 307 in the 2026-10-20 list were renamed repositories, and all their targets returned 302.

Responses actually observed on 2026-10-20:

| File | Response | Redirect target | Recorded |
|---|---|---|---|
| LFS (Xet) files (almost all weights, small safetensors too) | 302 | `https://us.aws.cdn.hf.co/xet-bridge-us/…`, with `x-linked-size` and `x-linked-etag` (sha256) | 302 |
| Small non-LFS files (such as `model_index.json`) | 307 | `/api/resolve-cache/models/…` (relative) | 307 |
| A renamed repository (such as `Comfy-Org/flux2-klein`) | 307 | `/resolve/…` of the new repository (relative) | The target's response (302, 404 and so on) |
| A gated repository | 401 | None | 401 |
| A missing file | 404 | None | 404 |

HEAD is counted in HF's "resolvers" window (the response header `ratelimit-policy: "fixed window";"resolvers";q=3000;w=300`), which is separate from the API's 500 requests per 5 minutes. One update cannot use it up.

### Check expiry (`max_head_age_days`)

Upstream, files disappear and repositories become gated.

- HEAD checks are trusted for **30 days** by default.
- If any file's check is older than that (or has no recorded time), `url_reachable` is held (detail: "check too old" in English, 「確認が古い」 in Japanese).
- Change it with `GatePolicy(max_head_age_days=…)` or `--max-head-age-days N` (`none` = no limit).

**The HEAD check time of the bundled list (`src/genmodelfit/data/db.json`)** is in `database.status` of `genmodelfit system --json` (`heads_checked_oldest`, `expires_at`; [schema.md](schema.md), "Data status").

- After `expires_at`, the bundled list alone has 0 auto entries. For the bundled list of 2026-10-21T06:20Z, that is 2026-11-20T06:20Z.
- An app that uses the list should, regularly (within 30 days), run `genmodelfit update --from-release` (a signed release; recommended) or redo the HEAD checks as follows:

```powershell
genmodelfit update --heads-only [--db PATH] [--output PATH]   # by default reads the cached list and writes the cache
```

If the cache cannot be read or has the wrong type, it reads and writes the bundled list (0.9.1, #63).

## ComfyUI version (`comfyui_version`)

**The app that uses the list must always pass the ComfyUI version it installs or bundles** (`--comfyui-version` / `GatePolicy(comfyui_version=…)`).
Example: AI生成キット installs ComfyUI v0.40.0, so it uses `GatePolicy(comfyui_version="0.40.0")`.
If you do not pass it, a template that needs a newer version than the user's ComfyUI can end up auto.

Defaults when it is not passed:

- Library (`evaluate_offline`): the version recorded in `sources`. If `sources` is not passed either, `comfyui_version` is held.
- CLI: the latest release that `update` recorded in the list (`sources.comfyui.latest_release`).

How `update` records it:

- It fetches `https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest` once, without authentication, and records `tag_name` (`v0.42.1` on 2026-10-20).
- If it cannot fetch, it records `null` and warns.
- If `update --heads-only` cannot fetch, it keeps the previously recorded version and warns.

How versions are compared:

- By numeric parts (missing parts are 0).
- With equal numbers, a prerelease (`0.4.0-rc1`, `0.4.0rc1`, `0.4.0.dev3`) counts as earlier than the release (`0.4.0`).
- This holds for both the required version and the version you use. If you use `0.4.0-rc1`, a template that needs `0.4.0` is held.
- Anything after `+build` is ignored.
- An unreadable mark (`0.4.0-foo`) is held as an unreadable version.

## Uncensored models (`content`)

Automatic recommendations are for beginners, so uncensored models are held by default.

- They are not removed from the list.
- They still appear in lists and filters such as `recommend --content uncensored`, `fit` and `info`, as before.

## What can be auto

Check the counts with `genmodelfit gate --only-auto --comfyui-version <version>`.

- In the bundled list of 2026-10-21T06:20Z: with 0.40.0, 95 entries (image 57, video 29, music 9); with the recorded v0.42.1, 99 entries. 3D, speech and sound effects are 0.
- On 2026-10-20 there were 83. After the review findings were fixed, a person checked the license and its basis for every file of those (a table row of components or families, or the original card). The list is in the PR #14 description.
- Entries added since are in `JUSTIFIED_AUTO` in tests/test_license_sources.py ([sources.md](sources.md), "Source table").

Points that need care among the ones checked:

- `black-forest-labs/FLUX.1-schnell` is gated on HF (`gated: auto`: access is granted as soon as you press agree). Its license is apache-2.0, and the list's files come from places that are not gated (Comfy-Org and others), so it passes. The gating of the original source of components is not checked in general (only DINOv3 and SAM 3, which the components table says need a request).
- The main body of `video_wan_ati` (Kijai/WanVideo_comfy): update picks Wan2.1-VACE as the original source, but the real original is https://huggingface.co/bytedance-research/ATI (apache-2.0; base_model is Wan2.1-I2V-14B-480P). The result is the same, apache-2.0.
