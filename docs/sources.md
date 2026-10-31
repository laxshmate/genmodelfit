# Data sources and how the list is built (`genmodelfit update`)

**English** · [日本語](sources.ja.md)

This page explains which data sources `genmodelfit update` reads and which rules it uses to build the model list.
It covers in detail how licenses, languages, uncensored models and exclusions are decided.
Audience: the maintainer, contributors, and app developers who want to check where the list comes from.

## Contents

- [Data sources](#data-sources)
- [Choosing templates](#choosing-templates)
- [Licenses](#licenses)
- [Language, runtime, description, and manually added models](#language-runtime-description-and-manually-added-models)
- [Uncensored models](#uncensored-models)
- [Exclusions](#exclusions)
- [Civitai: not used](#civitai-not-used)
- [Review marks](#review-marks)
- [When upstream changes shape, the network is down, or part of the data is missing](#when-upstream-changes-shape-the-network-is-down-or-part-of-the-data-is-missing)
- [Is the template `size` the total of all files?](#is-the-template-size-the-total-of-all-files)

`genmodelfit update` rebuilds the list from the data sources below
and writes it to the cache (`db.cached_db_path()`) or to the place given by `--output`.
From Python, use `genmodelfit.update.build_database(fetch=...)` (it does not write anything).
The bundled `src/genmodelfit/data/db.json` is the result of a real `update` run (a fallback for when there is no network).

It holds model **information** only. It neither downloads nor distributes weights.

## Data sources

| Source | URL | Terms | What is used |
|---|---|---|---|
| ComfyUI official template index | `https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/index.json` | MIT | category, name, tags, `openSource`, `size`, `usage`, `date`, `minComfyUIVersion`, `requiresCustomNodes`, `io.outputs` |
| Per-template JSON | `…/templates/<name>.json` | MIT | `properties.models` of every node, including nodes inside subgraphs `definitions.subgraphs[].nodes` (`name`, `url`, `hash`, `hash_type`, `directory`) |
| Hugging Face Hub API | `https://huggingface.co/api/models/{repo_id}?blobs=true` (`/revision/{rev}` if the revision is not main) | HF terms of use (public API, anonymous) | `cardData.license` / `license_name` / `license_link` (else the `license:` tag), `gated`, `siblings[].size` and `lfs.sha256`, `cardData.base_model`, `cardData.language` (`card_languages`; see "Language" below), `downloads`, `likes`, `lastModified`, `tags` |
| Weight header, `config.json`, `safetensors` in `/api/models/<repo>` | the main file itself (Range request), `config.json` in the same folder, Hub API | same | precision (how it is decided: see "Files" below; the weights themselves are not downloaded) |
| Hugging Face search | `https://huggingface.co/api/models?filter=…&pipeline_tag=…&search=…&sort=downloads&direction=-1&limit=100` | same | candidates for uncensored models, and for speech and sound-effect models missing from the templates |
| Model card | `https://huggingface.co/{repo_id}/raw/main/README.md` | same | used only to decide exclusions (not stored) |
| File URL (HEAD) | `https://huggingface.co/…/resolve/…` written in templates etc. | same | only the response code of an anonymous HEAD (redirects are generally not followed; only a 307 from a renamed repository is followed once; see [gate.md](gate.md)) (`files[].head_status`; see [gate.md](gate.md)) |
| Latest ComfyUI release | `https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest` | GitHub public API (no authentication, once) | `tag_name` (`sources.comfyui.latest_release`; used by gate to compare versions) |

Not used: data from sites such as willitrunai.com (CLAUDE.md), and Civitai (see below).

Fetching rules:

- The `User-Agent` is `genmodelfit/<version> (+https://github.com/laxshmate/genmodelfit)`.
- At most 8 requests at a time.
- Timeouts: 10 seconds to connect, 60 seconds to read.
- Retries: connection failures and HTTP 500, 502, 503, 504 are tried up to 4 times in total.

Rate limit:

- The anonymous HF API allows **500 requests per 5 minutes** (response header `ratelimit-policy: "fixed window";"api";q=500;w=300`).
- One update sends 500 to 600 requests to HF.
- On a 429, all threads wait for the `Retry-After` seconds. The count goes to `sources.huggingface.rate_limited` and the seconds waited to `rate_limit_waited_s`.
- So an update can take several minutes.
- One request retries a 429 at most 30 times (`fetch.MAX_RATE_LIMITED`). These are counted separately from the 4 tries above.
- The total waiting time has a cap (default 900 seconds; change it with `--rate-limit-budget`).
- Once the cap is exceeded, later HF requests fail immediately, and the run stops as partially missing (see "When part of the data is missing" below).
- No token is used.

## Choosing templates

Only templates that run locally are taken:

- Names starting with `api_` (templates that call a cloud API) are dropped.
- Templates with `openSource` **false** are dropped. Templates **without** `openSource` count as local (old templates do not write it).
- Templates whose `size` is 0 or missing are dropped. They carry no weights. This also drops templates whose custom node fetches its own weights, such as Chatterbox speech.
- Categories `LLM`, `Node Basics` and `Vector` are dropped because they are not generation models.

The ID in the list is the template name as it is.
A template whose JSON cannot be read is skipped with a warning.
If **more than 30% of the local templates** cannot be read, the run stops, assuming the upstream shape has changed.

### Type (modality)

| Category | modality |
|---|---|
| Image / Image Tools | image |
| Video / Video Tools | video |
| 3D Model | 3d |
| Audio | `tts` / `text to speech` / `speech` / `voice cloning` in the name, title or tags → **tts**. `sound effect(s)` / `sfx` / `foley` in the name, title, tags or description, and no `music` / `song` / `instrumental` → **sfx**. Anything else → **music** |
| Others (Character & Fashion, Brand & Design, Product & Ads) | `io.outputs[].mediaType` (video → video, audio → music, image → image). If absent, the tags (`Image to 3D` / `Text to 3D` → 3d, contains `Video` → video, otherwise image) |

Decided by hand (cannot be decided from name or tags): `audio_stable_audio_example` (Stable Audio Open 1.0) → sfx,
because the model card's use is mainly sound effects and ambient sound (field recordings).

### tasks

Tag → short code.

| Tag | task | Tag | task |
|---|---|---|---|
| Text to Image | t2i | Image to Video | i2v |
| Image Edit / Style Reference / Layer Decompose | i2i | Text to Video | t2v |
| Inpainting | inpaint | Video Edit / Video to Video / Character Replacement | v2v |
| Outpainting | outpaint | FLF2V | flf2v |
| ControlNet | control | Reference to Video | ref2v |
| Image Upscale / Video Upscale | upscale | Audio to Video | a2v |
| Remove Background | matting | Lip Sync | lipsync |
| Relight | relight | Motion Control | motion |
| Depth / Normal / Albedo Estimation, Element Segmentation | estimate | Frame Interpolation | interp |
| Image to 3D | i23d | Text to 3D | t23d |
| Text to Audio / Text to Music | t2a | Music Cover / Audio Editing | a2a |
| Voice Cloning | vc | Text to Speech | tts |

If no tag matches, the default for the type is used (image → t2i, video → t2v, 3d → i23d, tts → tts, audio → t2a).

Tool categories:

- For **Image Tools / Video Tools** (tools), the default is **`tool`**, because they are not generation.
  This avoids calling tools with tags missing from the table, such as `Depth` or `Pose Map`, t2i / t2v.
- Regardless of tags, a tool-category item whose title is a map of depth, normals, line art, pose or shape
  (`Depth Map: …`, `Depth Estimation: …`, `Lineart Map: …`) or a segmentation is **`estimate`**.
  A title about audio separation (`Audio Separation`) is **`separate`**.
  (`tasks.tool_task_from_title`. This applies even if the tag is ControlNet / Text to Image, since these are preprocessing, not generation.)
- Regardless of category, a title starting with MoGe, or about Geometry Estimation, is also **`estimate`**.
  MoGe is monocular shape estimation from a photo. It outputs a mesh but is not generation.

Upstream tag mistakes and inconsistencies (`Depth` and `Depth Estimation` mixed, `Text to Image` attached to a tool, and so on) are not fixed.
They are matched against the table as they are. Only clear names are added to the table.

### Files

All of `properties.models` is included, with duplicate URLs removed. The role comes from `directory`:

- `diffusion_models` / `unet` / `checkpoints` → main
- `text_encoders` / `clip` → text_encoder
- `vae` → vae
- `clip_vision` → clip_vision
- `loras` → lora
- anything else (`latent_upscale_models`, `model_patches`, …) → other

sha256 is the template's `hash` if `hash_type` is `SHA256`; otherwise HF's `lfs.sha256`.
The size is HF's `siblings[].size`.
Only for files missing from the API listing (HF URLs of another revision, etc.), HEAD is used to read `x-linked-size` / `content-length` (skip with `--skip-head`).
No HEAD is sent to Civitai URLs.

**Is the file set complete** (`files_complete`; see [schema.md](schema.md))? It is `false` if any of these holds:

- No main file is written (role main: `diffusion_models` / `unet` / `checkpoints`).
  This covers templates whose custom node fetches its own weights, items like MoGe that put the main file in `geometry_estimation`, and items with 0 files.
- The total file size is **under 50%** of `size` in index.json.
- For an HF repository, the main file is GGUF only and there is no text encoder or VAE (GGUF copies often hold only the main file).

When `false`, the template index `size` goes into **`size_floor_bytes`** (no file is removed).
The estimate uses `max(total of files, size_floor_bytes)` (`ModelEntry.estimate_size_bytes`), so an incomplete set is not estimated too small.
HF items have no index size, so `size_floor_bytes` is null (the automatic recommendation (#3) treats this the same as "size unknown").

Precision is decided in the order below, and the source is kept in `precision_source` (#30; see [schema.md](schema.md), section "Models").
A precision read from a header is never overwritten by a later source.

1. `header`: the header of the main file (safetensors JSON, or the start of a GGUF). Read with a Range request (`headers.py`).
2. `filename`: the main file name (nvfp4 / fp4 / nf4 / fp8 / int8 / int4 / bf16 / fp16 / fp32 / gguf-<quantization>).
3. `hf_api` / `config`: the HF repository that holds the main file (`repo_precision.py`).
   - From `safetensors.parameters` of the Hub API (`/api/models/<repo>`, anonymous): used if one dtype covers 90% or more,
     and the element count × bytes per element is within 5% of the size of this item's main safetensors (adding splits from the same repository).
     This works even for gated repositories (the header gives 401).
   - Otherwise, `torch_dtype` / `dtype` in `config.json` in the same folder as the main file
     (fetched only when it is in the API listing and not gated).
   - Requests are sent one at a time with 0.3 seconds between them, and 429 honors Retry-After. The result goes to `<cache>/repo_precision.json`.
4. `workflow`: the main loader node of the official template (`workflow.loader_precision`).
   - If UNETLoader's `weight_dtype` or WanVideoModelLoader's `quantization` is fp8_*, it is fp8.
   - Otherwise, a precision word in the loaded file name.
   - Otherwise, a precision setting (GIMM-VFI's `fp32`, WanVideo's `base_precision`, and so on).
   - `default` and `auto` say nothing.
   - Loader nodes for LoRA, CLIP and VAE are not looked at.
   - It also works for templates whose custom node fetches its own weights (no file list).

In the list built on 2026-10-20 (the bundled one at that time), items with unknown precision, excluding uncensored ones, went from 25 to 10
(header 3, Hub API 4, workflow 8). The remaining 10:

- 2 Civitai checkpoints (realismByStableYogi: no precision in the name, and not HF, so no header is read).
- 4 templates whose loader node does not state a precision:
  - LivePortrait is `auto`.
  - AnimateDiff by Purz has no main file list and the checkpoint name has no precision.
  - Lineart and OpenPose have preprocessing nodes that fetch weights themselves.
- 3 HF models with .pth / .ckpt: Kokoro and MeloTTS (no dtype in config.json) and tts-hifigan (no config.json). The Hub API safetensors tally covers .safetensors only.
- csm-1b: the Hub API says F32 for 6.21 billion bytes, but the total of the main file's 2 splits is 7.13 billion bytes, so they do not match.

The parameter count (params_b) is guessed from notation such as `14B` or `1.3B` in the file name or title (null if absent).

### Optional parts

Added in 0.12.0 (#81). Files of parts that the template does not load with its default values get `optional` (and
`optional_replaces`) (`optional.optional_files`; rebuilt every time by update and by re-reading the templates; the shape is in
[schema.md](schema.md), section "Models", and the effect on the estimate in [accuracy.md](accuracy.md), "How files are counted").
ComfyUI's code is not imported; the behavior of v0.43.0 (426f5ee) is imitated on the template JSON.

1. **Starting points**: ComfyUI's output nodes (`OUTPUT_NODE`; the type table `optional.OUTPUT_NODE_TYPES` was read from
   nodes.py, comfy_extras and comfy_api_nodes of v0.43.0; ComfyUI runs every output node even when its outputs are connected:
   execution.py:1183-1185) and nodes whose outputs are connected to nothing (a stand-in for output nodes of extensions that are
   not in the table; nodes that read files, notes and Primitive nodes are left out). They are looked for inside every subgraph.
2. **Walking back**: input links are followed backwards from the starting points. A subgraph instance is entered from its exit
   (-20), and the inner entrance (-10) leads out to the instance's input. A broken subgraph whose exit cannot be found counts as
   fully used (the safe side).
3. **Muted and bypassed**: mode 2 (muted) nodes are not followed. A mode 4 (bypassed) node's own files are not counted, but its
   inputs are followed (the same as the frontend leaving them out of the API prompt). For subgraph instances, only the mode of
   an instance at the top level applies to everything inside (the mode of a nested instance does not apply to its contents;
   this matches the frontend).
4. **Switches**: for an If/Else Switch (`ComfySwitchNode`), the unselected side is not followed only when the value of `switch`
   is fixed (the node's widget, the subgraph instance's widget, or a Primitive node) (lazy inputs:
   comfy_execution/graph.py:153-175, comfy_extras/nodes_logic.py:146-150). For a Soft Switch (`ComfySoftSwitchNode`), if the
   selected side is not connected (or is connected to a node that is not in the prompt), the other side is followed
   (nodes_logic.py:178-188). If the value is not fixed, both sides are followed.
5. **Classes**: files not reached by the validation, which also follows lazy branches (execution.py:946-969; a file not in the
   list gives "Value not in list": execution.py:1061-1085), are `inactive`; files reached by the validation but not by
   execution are `switch_off`. If several nodes read the same file, it is not optional when any one of them is used.
6. **Replacement**: each switch is flipped one at a time, and default parts that would no longer be loaded go into
   `optional_replaces` (for example, in Pixal3D / Trellis2 the Trellis2 model replaces the Pixal3D model). A part on a muted node
   counts as replacing the default main model if it has the main role (`kind: replace`), otherwise as an addition
   (`kind: add`).
7. Templates with extension nodes that pass values without link lines (`SetNode` / `GetNode`, `Anything Everywhere`) and
   templates with no starting point are not split (everything is counted).

In the bundled list (278 templates; the classes were built from the JSON at Comfy-Org/workflow_templates 0cc0fba, and the file
sets match the bundled list for every template), 66 have optional parts (22 with `inactive` parts, 44 with `switch_off` parts).
Most are speed-up LoRAs that are off by default; others are Qwen-Image 2.1's prompt enhancer (`qwen3.5_9b_…_pe_*`, 8.8 GB),
LTX-2's distilled model, Wan VACE 14B's 1.3B branch, HunyuanVideo 1.5 720p's 1080p refinement, and FLUX.2 Klein's bypassed
model.

## Licenses

The list is used for automatic recommendations in a closed-source commercial app (AI生成キット).
**Being wrong on the permissive side is the worst outcome.**
So when a license cannot be verified, or notations disagree, the result is `license: null` (unknown), and `license_evidence` keeps `license_unverified: <reason>`.
When the decision rules change, raise `update.LICENSE_POLICY`.

- It is now 5.
- 4 added the source table and the VOID family (#28).
- 5 added, in the 0.1.1 review: override rows in the table, reverting families with unreadable origin to unknown, and ordering within the same tier.

### Tiers

`hf.license_tier`. A file set takes the strictest tier.

| Tier | Examples |
|---|---|
| 6 Unknown | null |
| 5 Non-commercial, research | cc-by-nc*, and names containing non-commercial / nc / research (flux-1-dev-non-commercial-license, qwen-research, sai-nc-community, nsclv1, …) |
| 4 Custom, conditional | other (tencent-hunyuan-community, stable-audio-community, ltx-2-community-license-agreement, fair-ai-public-license, …), the gpl family, and everything not covered below |
| 3 Use restrictions | the openrail family (openrail++, creativeml-openrail-m), llama*, gemma, and names containing `ethical-restrictions` / `use-restrictions` (a permissive license to which the card text adds use restrictions; Irodori-TTS's `mit-with-ethical-restrictions`) |
| 2 Attribution required | cc-by-4.0 and similar, odc-by |
| 1 Permissive | apache-2.0, mit, the bsd family, isc, zlib, unlicense, cc0-1.0, bsl-1.0, … (`hf.PERMISSIVE`) |

For `other`, `license_name` (the custom license's name) separates 4 from 5.
Within the same tier, items are ordered by name (this does not affect results).

### How one file is decided

`hf.HfClient.resolve_file`. Steps in order:

0. **Components whose upstream cannot be verified** (`hf.UNVERIFIED_COMPONENTS`): unknown wherever they are
   (`rt_detr_v4`; see after the component table below).
1. **Component table** (`hf.COMPONENTS`): parts whose upstream is fixed by file name (text encoders, VAEs, …)
   inside a repackaged repository (owner `Comfy-Org` / `comfyanonymous` / `Kijai`) get the upstream license from the table.
   - The repackaging repository's notation and base_model describe the main model, so they are not applied to components
     (HiDream's `llama_3.1_8b_instruct` had been shown as mit).
   - Rows were added only after checking the upstream license with the HF API and confirming the same sha256 in several repackagings (unmodified copies).
     Rows added in #14 were checked against the upstream card.
   - A permissive row is not applied to a same-named file in a repository whose owner is not a repackager (it may be modified).
   - **A non-permissive row, or a row where the upstream requires an application (DINOv3, SAM 3, Llama, Gemma 3, …), is applied in any repository**
     (modifying it does not lighten the terms. VAST-AI/TripoSplat's `dino_v3_vit_h` had been shown as mit).
   - A row that requires an application keeps `upstream_gated: <upstream>` in `license_evidence`, and gate's `gated` puts it on hold.
2. **Repackaged main file**: pick the upstream matching the file from `cardData.base_model` (and `base_model:…` tags).
   - If there is one, use it. If several, take the one sharing the most words with the file name.
     Words are alphanumeric chunks; precision and format words and size/version words like `14b` `720p` `v2` are excluded.
     A tie looks at all of them and takes the strictest.
   - **No overlap with any, or no base_model (and not in the table `hf.REPACKAGED_UPSTREAM`) → unknown** (the repackager's notation is not used).
   - If the upstream is **unreadable (401 / 403 / 404) or has no license entry → unknown**.
3. **base_model chain**: follow `base_model` up to **12 levels** from the upstream (or from the host repository if it is not a repackaging).
   - `base_model` can be one name or a list. The tags `base_model:finetune:` / `quantized:` / `adapter:` / `merge:` are also read.
   - If any link in the chain is unreadable, has no license entry, or goes beyond 12 levels → unknown.
   - **If its own notation is more permissive than the strictest of its origins (base_model) → unknown**
     (a derivative cannot be more permissive than its origin. Example: Quiho's apache-2.0 is more permissive than the origin krea/Krea-2-Turbo's other).
   - If equal or stricter, its own notation is used.
4. **Family table** (`hf.FAMILIES`): when the repository name, file name or the followed base_model names identify the origin family,
   a notation more permissive than the family's license is unknown.
   Example: AtomicChat's Qwen-Image-2.1 lists only Qwen3-VL in base_model, but the name shows Qwen-Image-2.1 (qwen-research).
   A matched family is recorded in `license_evidence` as `family:<name>`.
5. For an **uncensored model found on HF** whose base_model and family are both unknown, the result is unknown
   (an uncensored model derives from an original, so its own notation cannot be verified alone).
   For non-uncensored ones (speech and so on), its own notation is used.

Between 3 and 4, two more checks apply (#14 review):

- **Families whose origin license cannot be read** (`hf.LINEAGES`): if the name (same as in 4) is SDPose, Lotus, Stable Diffusion 2 or VOID, the result is unknown
  (see "Families whose origin license cannot be read" below).
- **Components of another family** (`hf.FOREIGN_MARKERS`): the file name contains a mark of another model
  (dino, sam, siglip, clip, t5, qwen, llama, gemma, mistral, glm, birefnet, depth-anything, moge, detr, flux, wan, trellis, hunyuan, stable-diffusion, lumina, ltx, cogvideo, whisper, wav2vec),
  and that mark does not appear in the host repository's name or in the names on the chain the license was read through.
  Then it passes only if a row of the component table or the family table matches the file name itself.
  For a component-table row, the upstream license is used if it is stricter than or equal to the host's notation. If nothing matches, unknown.
  A repository's notation is not attached to another model's component bundled inside it.

A file gets `license` / `license_name` / `license_url`, `license_from` (the repository the license was read from; for the component table, the table's upstream),
`license_path` (the chain followed: host → base_model … → deciding link), `license_evidence`, and `repo_license`
(the host's notation, when it differs from the deciding one). Files that are not on HF (Civitai and so on) are unknown.

Finally, the **source table** (`license_sources.SOURCES`; see "Source table" below) is applied to files that the rules above left unknown.

- Among its rows, **override rows** (`override=True`) are applied even if the file was decided automatically (0.1.1 review).
- Override rows are only for these cases:
  - Cases where the automatic rules look only at a permissive cardData notation and miss the terms in the body.
    Example: the ChronoEdit LoRA. cardData says apache-2.0, but the body says "Governing Terms: … NVIDIA Open Model License", and its sha256 equals the lora/ of nvidia/ChronoEdit-14B-Diffusers.
  - Cases where the automatic rules follow a different origin: InfiniteTalk → MeiGen-AI/InfiniteTalk, Wan 2.1 I2V 480P → Wan-AI/Wan2.1-I2V-14B-480P.
- A file decided earlier by the table (mark `license_source:<row>`) is re-applied when the row is corrected.
- Files of families whose origin license cannot be read (`hf.LINEAGES`, such as VOID) are reverted to unknown even if decided automatically
  (`license_sources.enforce_lineage`. Same decision as gate, so the list data and gate's answer agree).

The license of a file set (`ModelEntry.license`) is that of the strictest file.
Within the same tier (permissive and so on), the one with more obligations is shown
(apache-2.0 > bsd-3-clause > mit, bsd-2-clause …; `hf.OBLIGATION_RANK`). A set of apache-2.0 and mit had been shown as "mit".
Per-file values are all in the JSON `licenses`.

### Component table

Checked on 2026-10-20. Applied only to files inside repackaged repositories (except where a row says otherwise).

| File name | Upstream | License | sha256 check |
|---|---|---|---|
| `llama_3.1_8b_instruct*` | [meta-llama/Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct) | llama3.1 | (1 place) |
| `t5xxl_{fp16,fp8_e4m3fn_scaled,…}` | [google/t5-v1_1-xxl](https://huggingface.co/google/t5-v1_1-xxl) | apache-2.0 | same in HiDream and flux_text_encoders |
| `umt5_xxl_*`, `umt5-xxl-enc-*` | [google/umt5-xxl](https://huggingface.co/google/umt5-xxl) | apache-2.0 | same in Wan 2.1 and Wan-Animate-2 |
| `clip_l` | openai/clip-vit-large-patch14 (not on HF; [LICENSE on GitHub](https://github.com/openai/CLIP/blob/main/LICENSE)) | mit | |
| `clip_g`, `clip_vision_g` | [laion/CLIP-ViT-bigG-14-laion2B-39B-b160k](https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k) | mit | |
| `clip_vision_h`, `CLIP-ViT-H-14-laion2B-s32B-b79K` | [laion/CLIP-ViT-H-14-laion2B-s32B-b79K](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K) | mit | same in 2 places |
| `sigclip_vision_patch14_384` | [google/siglip-so400m-patch14-384](https://huggingface.co/google/siglip-so400m-patch14-384) | apache-2.0 | same in 2 places |
| `qwen_2.5_vl_7b_fp8_scaled` | [Qwen/Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | apache-2.0 | same in Qwen-Image and HunyuanVideo 1.5 |
| `qwen_2.5_vl_fp16` (OmniGen2; 7.5 GB = 3B) | [Qwen/Qwen2.5-VL-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct) | other (qwen-research) | |
| `qwen_3_4b`, `qwen_3_4b_fp8_mixed` | [Qwen/Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) | apache-2.0 | same in FLUX.2 klein 4B and Z-Image |
| `qwen3vl_8b_fp8_scaled` | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) | apache-2.0 | same in Comfy-Org/Qwen3-VL and Boogu |
| `gemma_3_12B_it*` | [google/gemma-3-12b-it](https://huggingface.co/google/gemma-3-12b-it) | gemma | |
| `wav2vec2_large_english*` | [jonatasgrosman/wav2vec2-large-xlsr-53-english](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english) | apache-2.0 | |
| `ae` (FLUX.1 VAE) | [black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | apache-2.0 | same in HiDream, Lumina 2 and Z-Image |
| `flux2-vae` | [black-forest-labs/FLUX.2-VAE](https://huggingface.co/black-forest-labs/FLUX.2-VAE) | apache-2.0 | same in flux2-dev, ERNIE and Lens |
| `flux2-vae_small` | [black-forest-labs/FLUX.2-small-decoder](https://huggingface.co/black-forest-labs/FLUX.2-small-decoder) | apache-2.0 | |
| `wan_2.1_vae`, `Wan2_1_VAE_*` | [Wan-AI/Wan2.1-T2V-14B](https://huggingface.co/Wan-AI/Wan2.1-T2V-14B) | apache-2.0 | same in 3 places |
| `wan2.2_vae` | [Wan-AI/Wan2.2-TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) | apache-2.0 | |
| `qwen_image_vae` | [Qwen/Qwen-Image](https://huggingface.co/Qwen/Qwen-Image) | apache-2.0 | same in 5 places |
| `RealESRGAN_x4plus*` | xinntao/Real-ESRGAN ([LICENSE on GitHub](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE)) | bsd-3-clause | |
| `dino_v3*`, `dinov3*` (anywhere; application required) | [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) (gated: manual; [original text](https://ai.meta.com/resources/models-and-libraries/dinov3-license)) | other (dinov3-license) | (upstream card) |
| `sam3*` (anywhere; application required) | [facebook/sam3](https://huggingface.co/facebook/sam3) (gated: manual) | other (sam-license) | (upstream card) |
| `depth_anything_v2_vit{b,l,g}` (anywhere) | [depth-anything/Depth-Anything-V2-Large](https://huggingface.co/depth-anything/Depth-Anything-V2-Large) | cc-by-nc-4.0 | (upstream card) |
| `depth_anything_v2_vits` | [depth-anything/Depth-Anything-V2-Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) | apache-2.0 | (upstream card) |
| Lumina 2 family sets (`lumina_2.safetensors`, `…all_in_one`) (anywhere) | contains Gemma 2 2B ([neta-art/Neta-Lumina](https://huggingface.co/neta-art/Neta-Lumina), [NetaYume card](https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0)) | gemma | (card) |
| `dinov2*` | [facebook/dinov2-large](https://huggingface.co/facebook/dinov2-large) | apache-2.0 | (upstream card) |
| `sam_vit_*`, `sam2*_hiera*` | [facebook/sam-vit-huge](https://huggingface.co/facebook/sam-vit-huge), [facebook/sam2.1-hiera-large](https://huggingface.co/facebook/sam2.1-hiera-large) | apache-2.0 | (upstream card) |
| `siglip*` | [google/siglip-so400m-patch14-384](https://huggingface.co/google/siglip-so400m-patch14-384), [google/siglip2-so400m-patch16-512](https://huggingface.co/google/siglip2-so400m-patch16-512) | apache-2.0 | (upstream card) |
| `birefnet` | [ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) | mit | (upstream card) |
| `moge_1_vitl*`, `moge_2_vitl_normal*` | [Ruicheng/moge-vitl](https://huggingface.co/Ruicheng/moge-vitl), [Ruicheng/moge-2-vitl-normal](https://huggingface.co/Ruicheng/moge-2-vitl-normal) | mit | (upstream card) |
| `trellis_2_{shape,texture}_vae*` | [microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) | mit | (upstream card; Pixal3D is built on this) |
| `qwen_{0.6b,1.7b,4b}_ace15` | [ACE-Step/Ace-Step1.5](https://huggingface.co/ACE-Step/Ace-Step1.5) (includes Qwen3-Embedding-0.6B and the 5Hz LM; the originals [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) and [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) are apache-2.0) | mit | (upstream card) |

Components whose upstream cannot be verified (`hf.UNVERIFIED_COMPONENTS`; unknown wherever they are):

| File name | Reason |
|---|---|
| `rt_detr_v4*` | RT-DETRv4 (https://github.com/RT-DETRs/RT-DETRv4). The code is Apache-2.0, but the weights were made by distillation with DINOv3 as the teacher and have no license entry. It sits inside Comfy-Org/SDPose but is not part of SDPose (the Comfy-Org/SDPose README names the upstream separately) |

Not put in the component table (could not be fully verified; in every case the host repository's notation is stricter):

- `qwen3vl_8b_int8_convrot` of Qwen-Image-2.1 (only one copy)
- `mistral_3_small_flux2_*` of FLUX.2 dev
- `t5gemma_b_b_ul2` of Stable Audio 3
- `gemma_3_4b_it_bf16` of NewBie
- `qwen_3_8b*` of FLUX.2 klein 9B
- `qwen3vl_4b_*` of Krea-2 and Mage-Flow
- `qwen_2.5_vl_7b` (bf16) of HunyuanImage 2.1
- `llava_llama3` of HunyuanVideo

Of these, `mistral_3_small_flux2_*`, `t5gemma_b_b_ul2`, `gemma_3_4b_it_*`, `qwen_3_8b*`, Krea-2's `qwen3vl_4b_*` and HunyuanImage 2.1's `qwen_2.5_vl_7b`
are decided in the "Source table" below (matched by the pair of host repository and file name).

### Family table

Matched by name. A notation more permissive than this is unknown.

| Family | Name examples | License | Where checked |
|---|---|---|---|
| flux.1-dev | flux1-dev, flux.1dev, FLUX.1-Krea-dev, flux1-dev-kontext | other (flux-1-dev-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.1-dev |
| flux.1-schnell | flux1-schnell | apache-2.0 | https://huggingface.co/black-forest-labs/FLUX.1-schnell |
| flux.2-dev | flux2_dev | other (flux-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.2-dev |
| flux.2-klein-9b | klein-9b, klein-base-9b | other (flux-non-commercial-license) | https://huggingface.co/black-forest-labs/FLUX.2-klein-9B |
| flux.2-klein-4b | klein-4b | apache-2.0 | https://huggingface.co/black-forest-labs/FLUX.2-klein-4B |
| qwen-image-2.1 | Qwen-Image-2.1, qwen_image_2.1 | other (qwen-research) | https://huggingface.co/Qwen/Qwen-Image-2.1 |
| qwen-image | Qwen-Image, qwen_image_edit, 2512 | apache-2.0 | https://huggingface.co/Qwen/Qwen-Image |
| krea-2 | Krea-2, krea2 | other (krea-2-community-license) | https://huggingface.co/krea/Krea-2-Turbo |
| sdxl | sdxl, sd_xl, stable-diffusion-xl | openrail++ | https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 |
| sd1.5 | sd15, sd-1.5, stable-diffusion-v1-5 | creativeml-openrail-m | https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5 |
| sd3.5 | sd3.5, stable-diffusion-3.5 | other (stabilityai-ai-community) | https://huggingface.co/stabilityai/stable-diffusion-3.5-large |
| illustrious-noobai | Illustrious, NoobAI | other (fair-ai-public-license-1.0-sd) | https://huggingface.co/OnomaAIResearch/Illustrious-xl-early-release-v0, https://huggingface.co/Laxhar/noobai-XL-1.1 |
| hunyuan | HunyuanVideo, Hunyuan3D, HunyuanImage | other (tencent-hunyuan-community) | https://huggingface.co/tencent/HunyuanVideo, tencent/Hunyuan3D-2, tencent/HunyuanVideo-1.5 |
| stable-audio | stable-audio-open, stable_audio_3 | other (stable-audio-community) | https://huggingface.co/stabilityai/stable-audio-open-1.0, stabilityai/stable-audio-3-medium-base |
| ltx-2 | LTX-2, ltx-2.3, LTXV2 | other (ltx-2-community-license-agreement) | https://huggingface.co/Lightricks/LTX-2 |
| ltx-video | LTX-Video, ltxv | other (no name given) | https://huggingface.co/Lightricks/LTX-Video |
| wan | Wan2.1, wan2_2 | apache-2.0 | https://huggingface.co/Wan-AI/Wan2.1-T2V-14B, Wan-AI/Wan2.2-T2V-A14B |
| hidream | HiDream (the main DiT only; text encoders are in the component table) | mit | https://huggingface.co/HiDream-ai/HiDream-I1-Full |
| z-image | Z-Image, z_image | apache-2.0 | https://huggingface.co/Tongyi-MAI/Z-Image-Turbo |
| chroma | Chroma | apache-2.0 | https://huggingface.co/lodestones/Chroma |
| marigold-v1 | marigold (except v2) | openrail++ | https://huggingface.co/prs-eth/marigold-depth-v1-1 (v2, huawei-bayerlab/marigold-v2-0, is made from Qwen-Image-Edit-2509 and is apache-2.0) |
| qwen3-vl | Qwen3-VL, qwen3vl | apache-2.0 | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct, -4B, -2B, -32B |

### Families whose origin license cannot be read

`hf.LINEAGES`. If the name appears, the result is unknown.

| Family | Name examples | Basis |
|---|---|---|
| sdpose | SDPose, sdpose_wholebody | The card of https://huggingface.co/teemosliang/SDPose-Wholebody: "U-Net backbone initialized with Stable Diffusion v2 weights" (notation: mit) |
| lotus | lotus-depth, lotus-normal | The training config of https://github.com/EnVision-Research/Lotus starts from stabilityai/stable-diffusion-2-base (notation: apache-2.0) |
| sd2 | sd2, sd21, stable-diffusion-2 | The originals stabilityai/stable-diffusion-2 and -2-base could not be read anonymously on 2026-10-20 (401). The copies' notation is openrail++ |
| void-model | void_pass1, netflix/void-model | The card of https://huggingface.co/netflix/void-model: "VOID is built on CogVideoX-Fun-V1.5-5b-InP". The LICENSE of the origin https://huggingface.co/alibaba-pai/CogVideoX-Fun-V1.5-5b-InP is The CogVideoX License (5B Transformer; commercial use needs registration, with a cap of 1 million monthly accesses). VOID's notation is apache-2.0 (#28) |

The family table is a lower bound meaning "at least this strict". It is not used to decide a license.
Matching a permissive family does not verify a derivative's notation.
Example: even in the Wan family, the base_model of Comfy-Org/Wan-Dancer (Wan-AI/Wan2.2-Dancer-14B) is unreadable, so it is unknown automatically.
The real upstream Wan-AI/Wan-Dancer-14B was verified in the source table below.

### Source table

`license_sources.SOURCES`. #28, checked on 2026-10-20.

Repackaging repositories (Comfy-Org, Kijai and others) often write no base_model, or one that does not match the file.
Then the rules above give unknown (in the list built on 2026-10-20, the bundled one at that time, 113 of 299 items excluding uncensored ones).
So, for files left unknown, a person traced the origin from **primary sources**, and only what could be verified went into the table.

Tracing rules (never be wrong on the permissive side):

1. Primary sources are only: the original model's HF card (cardData license and body), LICENSE, the official GitHub LICENSE and README, and official code.
   A repackager's README is used only as a clue to "which is the original". The license written there is not used.
   (Example: the Comfy-Org/Lens README says FLUX.2-dev is apache-2.0, but it is actually non-commercial.)
2. "This file in this host repository is that original weight" must be confirmed by one of the following:
   - the sha256 equals the original file
   - the official README or code names the host repository or file
   - the official code loads it from the original repository without modification
   - the safetensors header (read with a Range request) shows the same weight names and shapes as the original
   - the repackager's README names the original and the size matches
3. The original license is taken as written. When the cardData and the body disagree, the stricter one wins
   (ChronoEdit's cardData is apache-2.0, but the body says NVIDIA Open Model License). A custom license is kept as `other` plus a name.
4. A derivative (LoRA, extraction, quantization) must be confirmed not to be more permissive than the license of its origin's origin (base_model).
   The following are not put in the table (they stay unknown):
   - items that have only a notation more permissive than the origin: USO, ObjectRemovalFluxFill, LTX-2.3-Transition, MiniMax-H3 Turbo
   - LoRAs whose host has no license entry: Anima's official LoRA, ByteZSzn's Flux2TurboComfyv2
5. A permissive row needs the basis text to contain that license (apache-2.0 / Apache License 2.0, …). tests/test_license_sources.py checks this.
6. A row is matched by the pair of "host repository" and "file name". It is not applied to a same-named file in another host.
   It is not applied to files whose names are components with unverifiable upstream or families with unreadable origin.

A matched file gets `license_from` (origin), `license_url`, `license_path` (host → … → origin), and `license_evidence` holds
`license_source:<row>` and the basis text (URL, what is written, date checked).
`update` applies it only to files that are unknown after the automatic rules.
For the bundled list, `license_sources.apply_license_sources(db)` applies it without touching the network.
`python -m genmodelfit.license_sources` prints how many unknown / auto items change when applied (it does not rewrite the list).

| Host | File name | License | Origin (primary source) | How verified |
|---|---|---|---|---|
| Kijai/WanVideo_comfy | `Wan21_CausVid_14B_T2V_lora_rank*` | cc-by-nc-4.0 | [lightx2v/Wan2.1-T2V-14B-CausVid](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-CausVid) | Kijai's README: "CausVid LoRAs are experimental extractions from the CausVid finetunes", and the link to the original |
| Kijai/WanVideo_comfy | `Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank*` | cc-by-nc-4.0 | [tianweiy/CausVid](https://huggingface.co/tianweiy/CausVid) | same as above ("CausVid 1.3B") |
| Kijai/WanVideo_comfy | `Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank*` | apache-2.0 | [lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill) (LICENSE.txt is Apache 2.0) | Kijai's README: "CFG and Step distill 14B" |
| Kijai/WanVideo_comfy, Comfy-Org/Wan-Animate-2 | `lightx2v_I2V_14B_480p_cfg_step_distill_rank*_bf16` | apache-2.0 | [lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v](https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v) | Kijai's Lightx2v/README.md: "Extracted from …/distill_models". The Comfy-Org copy has the same sha256 |
| Kijai/WanVideo_comfy | `lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank*_bf16` | apache-2.0 | [lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v](https://huggingface.co/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v) | Kijai's Lightx2v/README.md: "Extracted from …/distill_model.safetensors" |
| Kijai/WanVideo_comfy | `WanAnimate_relight_lora_fp16` | apache-2.0 | relighting_lora of [Wan-AI/Wan2.2-Animate-14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) | the header shows the same shapes for 960 weights (only naming and F32→F16 differ) |
| Kijai/WanVideo_comfy_fp8_scaled | `Wan21-WanMove_fp8_scaled_e4m3fn_KJ` | apache-2.0 | [Ruihang/Wan-Move-14B-480P](https://huggingface.co/Ruihang/Wan-Move-14B-480P) | the README of the official https://github.com/ali-vilab/Wan-Move names this Kijai folder |
| Comfy-Org/Wan-Animate-2 | `wan_animate_2(_distill)_*` | apache-2.0 | [Wan-AI/Wan2.2-Animate-2-14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-2-14B) | the origin in the repackager's README |
| Comfy-Org/MiniMax-Music-3 | `minimax_music3_*` | other (minimax-music3-community-license) | [LICENSE of MiniMaxAI/MiniMax-Music3](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE) (displaying it in the UI, and revenue over 20 million USD per year, are permitted) | the origin in the repackager's README |
| Comfy-Org/HunyuanVideo_repackaged, Kijai/HunyuanVideo_comfy | `hunyuan_video_t2v_720p_*`, `hunyuan_video_vae_*` | other (tencent-hunyuan-community) | [tencent/HunyuanVideo](https://huggingface.co/tencent/HunyuanVideo) | the origin in the repackager's README |
| Comfy-Org/NewBie-image-Exp0.1_repackaged | `gemma_3_4b_it_*` | gemma (the origin requires an application) | [google/gemma-3-4b-it](https://huggingface.co/google/gemma-3-4b-it) | card of the original NewBie-AI/NewBie-image-Exp0.1: "We use Gemma3-4B-it as the primary text encoder" |
| Comfy-Org/NewBie-image-Exp0.1_repackaged | `jina_clip_v2_*` | cc-by-nc-4.0 | [jinaai/jina-clip-v2](https://huggingface.co/jinaai/jina-clip-v2) | same card: "Jina CLIP v2" |
| Kijai/LTXV2_comfy | `ltx-2-19b-*_transformer_only_*`, `ltx-2-19b-embeddings_connector_*` | other (ltx-2-community-license-agreement) | [Lightricks/LTX-2](https://huggingface.co/Lightricks/LTX-2) | Kijai's README: "extracted from their respective original models" |
| Comfy-Org/ltx-2 | `gemma-3-12b-it-abliterated_lora_rank*` | gemma | [mlabonne/gemma-3-12b-it-abliterated](https://huggingface.co/mlabonne/gemma-3-12b-it-abliterated) | base_model in the repackager's cardData |
| Comfy-Org/HunyuanImage_2.1_ComfyUI | `qwen_2.5_vl_7b` | apache-2.0 | [Qwen/Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) | the official https://github.com/Tencent-Hunyuan/HunyuanImage-2.1/blob/master/ckpts/checkpoints-download.md runs `hf download Qwen/Qwen2.5-VL-7B-Instruct` |
| circlestone-labs/Anima | `qwen_3_06b_base` | apache-2.0 | [Qwen/Qwen3-0.6B-Base](https://huggingface.co/Qwen/Qwen3-0.6B-Base) | same sha256 |
| Comfy-Org/Wan_2.2_ComfyUI_Repackaged | `chrono_edit_14B_*`, `chronoedit_distill_lora` | other (nvidia-open-model-license) (override row) | [nvidia/ChronoEdit-14B-Diffusers](https://huggingface.co/nvidia/ChronoEdit-14B-Diffusers) (body: "Governing Terms: … NVIDIA Open Model License Agreement") | the main file: origin in the repackager's README. The LoRA has the same sha256 as the origin's lora/chronoedit_distill_lora.safetensors |
| Comfy-Org/Wan_2.1_ComfyUI_repackaged | `wan2.1_infiniteTalk_{multi,single}_fp16` | apache-2.0 (override row) | [MeiGen-AI/InfiniteTalk](https://huggingface.co/MeiGen-AI/InfiniteTalk) ("The models in this repository are licensed under the Apache 2.0 License") | origin in the repackager's README (the automatic rules followed a different origin) |
| Comfy-Org/Wan_2.1_ComfyUI_repackaged, Kijai/WanVideo_comfy_fp8_scaled | `wan2.1_i2v_480p_14B_*`, `Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ` | apache-2.0 (override row) | [Wan-AI/Wan2.1-I2V-14B-480P](https://huggingface.co/Wan-AI/Wan2.1-I2V-14B-480P) (LICENSE.txt: "licensed under the Apache 2.0 License") | origin in the repackager's README. Kijai's header has img_emb and no VACE layers (the automatic rules followed 720P and VACE) |
| Comfy-Org/ERNIE-Image | `ministral-3-3b` | apache-2.0 | text_encoder of [baidu/ERNIE-Image](https://huggingface.co/baidu/ERNIE-Image) (Ministral 3 3B; mistralai's Ministral-3-3B are all apache-2.0) | origin in the repackager's README, text_encoder/config.json |
| ByteZSzn/Flux.2-Turbo-ComfyUI, Comfy-Org/flux2-dev | `Flux_2-Turbo-LoRA_comfyui` | other (flux-dev-non-commercial-license) | [fal/FLUX.2-dev-Turbo](https://huggingface.co/fal/FLUX.2-dev-Turbo) | same sha256 as the file in fal's comfy/ |
| Comfy-Org/flux2-dev | `mistral_3_small_flux2_*` | apache-2.0 | [mistralai/Mistral-Small-3.2-24B-Instruct-2506](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506) | the official code black-forest-labs/flux2, text_encoder.py (commit 50fe516), loads exactly this |
| Comfy-Org/flux2-klein-9B, Comfy-Org/vae-text-encorder-for-flux-klein-9b | `qwen_3_8b*` | apache-2.0 | [Qwen/Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) | klein-9b in the official code util.py loads Qwen3-8B. The size matches the total of the splits |
| black-forest-labs/flux-3-image-klein-comfy | `qwen3.5_9b_flux3_prompt_upsampler_*` | other (flux-kommunity-license) | cardData of the host (BFL itself) | the original's own repository. Stricter than the origin Qwen3.5 (apache-2.0) |
| Comfy-Org/MOSS-Image | `qwen3.5_9b_*` | apache-2.0 | text_encoder of [OpenMOSS-Team/MOSS-Image-Base](https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base) (Qwen3.5-9B; Qwen/Qwen3.5-9B is also apache-2.0) | base_model of the repackager, text_encoder/config.json |
| Kijai/SUPIR_pruned | `SUPIR-v0[QF]_*` | other (supir-non-commercial) | "Non-Commercial Use Only Declaration" of https://github.com/Fanghua-Yu/SUPIR | the official weight names |
| Comfy-Org/stable-audio-3 | `t5gemma_b_b_ul2` | gemma (the origin requires an application) | [google/t5gemma-b-b-ul2](https://huggingface.co/google/t5gemma-b-b-ul2) | docs/conditioning.md of the official Stability-AI/stable-audio-tools |
| OpenMOSS-Team/MOSS-Audio-Tokenizer | `model-0000[12]-of-00002` | apache-2.0 | the host's own card (`license: apache-2.0`) | MOSS-TTS v1.5's processor loads it by default (0.14.0, #142) |
| ResembleAI/chatterbox | `ve.pt`, `s3gen.pt`, `conds.pt` | mit | the host's own card (`license: mit`) | the official src/chatterbox/mtl_tts.py loads them (0.14.0, #142) |
| Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice, -0.6B-CustomVoice | `speech_tokenizer/model.safetensors` | apache-2.0 | [Qwen/Qwen3-TTS-Tokenizer-12Hz](https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz) (`license: apache-2.0`) | same sha256 (0.14.0, #142) |
| openbmb/VoxCPM2 | `audiovae.pth` | apache-2.0 | the host's own card (`license: apache-2.0`) | the official src/voxcpm/model/voxcpm2.py loads it (0.14.0, #142) |
| FunAudioLLM/Fun-CosyVoice3-0.5B-2512 | `llm.pt`, `flow.pt`, `hift.pt`, `campplus.onnx`, `speech_tokenizer_v3.onnx` | apache-2.0 | the host's own card (`license: apache-2.0`) | the official cosyvoice/cli/cosyvoice.py loads them (0.14.0, #142) |
| k2-fsa/OmniVoice | `audio_tokenizer/model.safetensors` (matched on the whole path) | other (boson-higgs-audio-2-community-license) | [bosonai/higgs-audio-v2-tokenizer](https://huggingface.co/bosonai/higgs-audio-v2-tokenizer) (LICENSE in the same folder: "BOSON HIGGS AUDIO 2 COMMUNITY LICENSE AGREEMENT", based on the Llama 3 license; over 100,000 annual users needs another license) | same sha256 (0.14.0, #142) |
| Serveurperso/Qwen3-TTS-GGUF | `qwen-tokenizer-12hz-*.gguf` | apache-2.0 | [Qwen/Qwen3-TTS-Tokenizer-12Hz](https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz) (`license: apache-2.0`) | the host's card: "Audio codec : Qwen3-TTS-Tokenizer-12Hz (Qwen team), Apache 2.0" (0.14.0, #142) |
| Comfy-Org/void-model | `cogvideox_vae` | apache-2.0 | [zai-org/CogVideoX-2b](https://huggingface.co/zai-org/CogVideoX-2b): "including its corresponding Transformers module and VAE module … Apache 2.0" | same sha256 as the VAE of alibaba-pai/CogVideoX-Fun-V1.5-5b-InP. The 2b and 5b VAEs are the same |
| Comfy-Org/HuMo_ComfyUI | `whisper_large_v3_fp16` | apache-2.0 | [openai/whisper-large-v3](https://huggingface.co/openai/whisper-large-v3) | same sha256 |
| Comfy-Org/PixelDiT | `gemma_2_2b_it_elm_*` | gemma (the origin requires an application) | [google/gemma-2-2b-it](https://huggingface.co/google/gemma-2-2b-it) | card of the original nvidia/PixelDiT-1300M-1024px: "Text encoder: Gemma-2-2B-IT" |
| Comfy-Org/PixelDiT | `pid_flux1_*` | other (nsclv1) | [nvidia/PiD](https://huggingface.co/nvidia/PiD) (non-commercial) | base_model of the repackager |
| Comfy-Org/Wan-Dancer | `wan2.2_dancer_14b_{global,local}_*` | apache-2.0 | [Wan-AI/Wan-Dancer-14B](https://huggingface.co/Wan-AI/Wan-Dancer-14B) | the HF link in the README of the official https://github.com/Wan-Video/Wan-Dancer |
| Comfy-Org/Krea-2 | `krea2_style_reference` | other (krea-2-community-license) | [ostris/krea2_turbo_style_reference](https://huggingface.co/ostris/krea2_turbo_style_reference) | same sha256 |
| Comfy-Org/Krea-2 | `krea2_darkbrush` | other (krea-2-community-license) | [krea/Krea-2-LoRA-darkbrush](https://huggingface.co/krea/Krea-2-LoRA-darkbrush) | origin in the repackager's README. Same size and header |
| Comfy-Org/Krea-2 | `qwen3vl_4b_*` | other (krea-2-community-license) | text_encoder of [krea/Krea-2-Turbo](https://huggingface.co/krea/Krea-2-Turbo) | the origin distributes a text encoder of the same size under its own license (stricter than Qwen3-VL's apache-2.0) |
| k2-fsa/OmniVoice | `model.safetensors` | other (cc-by-nc; no version given) | README of https://github.com/k2-fsa/OmniVoice: "The pre-trained model is licensed under the CC-BY-NC" | the official README |
| circlestone-labs/Anima-Official-LoRAs | `anima-*` (`anima-turbo-lora-v0.2` and so on) | other (circlestone-labs-non-commercial-v1.0) | [LICENSE.md](https://huggingface.co/circlestone-labs/Anima-Official-LoRAs/blob/main/LICENSE.md) (commit c221e431c8 of 2026-05-26) | heading "CircleStone Labs Non-Commercial License v1.0", and 2.b "for Non-Commercial Purposes" only. The card metadata is empty (checked in #30) |

Left unknown because they could not be fully verified:

- Comfy-Org/Mage-Flow: the upstream microsoft/Mage-Flow gives 401, and there is no GitHub.
- Comfy-Org/Lens:
  - microsoft/Lens gives 401.
  - The GitHub is MIT, but it has no entry for the weights.
  - gpt-oss is also read from the text_encoder of microsoft/Lens, so it cannot be called plain openai/gpt-oss-20b.
- FILM's `film_net_fp16`: the weights are outside GitHub, and there is no license entry for the weights.
- HunyuanVideo's `llava_llama3_*`: xtuner/llava-llama-3-8b has no license entry.
- ByteZSzn's `Flux2TurboComfyv2`.
- lightx2v/Minimax-h3-Turbo.
- Kokoro: the original StyleTTS2-LJSpeech has no entry.
- SDPose, Lotus, RT-DETRv4 (families and components above).
- Civitai files.

Drafts of requests to the upstream owners to add a license entry are in [license_requests.md](license_requests.md) (they are sent only after the maintainer confirms).

**Rechecking repositories that cannot be read anonymously (401)** (#30):

- `update` lists the repositories for which the Hub API returned 401 (private or gone) in `sources.huggingface.unauthorized`.
- `update --inspect-only` sends the API again to those repositories and writes the result to `sources.huggingface.unauthorized_recheck`
  (`unauthorized`, `private_or_missing`, `missing`, `readable`, `failed`, `checked_at`).
- The diff of the weekly update PR shows when Mage-Flow, Lens and the like become public. Once readable, they get a license at the next rebuild.
  - The weekly update (#19, #42): a startup script on the maintainer's PC (Task Scheduler) does the rebuild, tests and PR.
  - Only the decisions on pending review are left to the Claude Code skill genmodelfit-weekly.
  - weekly-data.yml is a manual backup. See [release.md](release.md).
- The anonymous API returns 401 both for private repositories and for nonexistent ones (even for the nonexistent name this-org-zzz/does-not-exist-123), so they cannot be told apart anonymously.
- Only names confirmed not to exist are written in `repo_precision.KNOWN_MISSING`, and they are split out as `unauthorized_missing` (`missing` in the recheck).
- As of 2026-10-20:
  - Private or missing: Wan-AI/Wan2.2-Dancer-14B, microsoft/Lens, microsoft/Mage-Flow.
  - Missing: google/gemma-3-12b. Google's public listing has only gemma-3-12b-it, -pt and the QAT versions, so this is a name with -it / -pt forgotten. There is no record of which card's base_model it came from.

Applying the table to the list built on 2026-10-20 (the bundled one at that time):

- Items with unknown license, excluding uncensored ones: 113 → 46 of 299 items (files: 75 → 29).
- auto (when the HEAD expiry is not considered): 83 → 99.
- A person has verified the license of every file in the 16 items that newly become auto
  (`JUSTIFIED_AUTO` in tests/test_license_sources.py; the test fails if an item not listed there becomes auto).

### License of a file set

It is the strictest among **all files** (main file, text encoder, VAE, LoRA, clip_vision, upscaler, ControlNet and so on, everything the template uses).
If no file is written at all, it is unknown.
`license_name` is that of the strictest file, and `license_url` is where that file's license entry is.
`license_evidence` collects the reasons from the files' `license_unverified` (up to 10).

## Language, runtime, description, and manually added models

Added in 0.5.0 (#36).

So that an app can build its screens from the list alone, `ModelEntry` gained the optional fields `languages` / `runtime` / `description`
(shape: [schema.md](schema.md), section "ModelEntry". `schema_version` stays 1. Older versions drop unknown keys, so they can still read it).
`appinfo.apply_app_fields` decides them (update applies it every time. `update --fields-only` redoes only this.
It then re-reads the cards. With `--offline` there is no network, and the `card_languages` saved in the list is used).

### Language

Field `languages`.

It is rebuilt from its materials at every application (previous values are not carried over;
narrowing a table or deleting a row from the hand table is fixed at the next application).
Materials, in priority order:

1. **Hand table** `src/genmodelfit/data/overrides.json` (checked by a person on a real machine or in a primary source).
   The kinds written (`prompt` / `speech`) are used as written.
   Manual models use the values written in `src/genmodelfit/data/manual.json`.
2. **`language` of the HF card** (the card of the host repository of the main file, and of its origin `license_from`; the first one that has a language).
   What was read is kept in the field `card_languages` (`[]` = read but no language, null = not read yet).
   - **If the card states languages, they are the upper bound.**
   - Speech: `speech` = the card's value.
   - Prompt text: if every text-reading component is English only (CLIP, T5), only `en` of the card's languages; otherwise the card's value.
   - **For music, only `en` of the card's languages is used for the prompt, and `speech` (the singing language) is not decided from the card** (0.6.0, #41).
     A music card's `language` is often the language of lyrics, not necessarily the prompt language.
     (The card of https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B lists 17 languages, and "Performance varies by language" is about singing.
     The prompt of the instrumental `audio_ace_step_1_t2a_instrumentals` had also been given 17 languages.) The singing language is written in the hand table.
3. **Only when there is no card (no language written), the text-reader component table**: if the table matches, `["en"]`.
   Parts the template does not load with its default values (["Optional parts"](#optional-parts); 0.12.0, #81) are not used as
   material (the card is also read only for main models loaded by default).
   Even for multilingual components, the table alone does not add `ja`.
   (The languages a component can read differ from the languages of the descriptions the model was trained on.
   Wan, Qwen-Image and HunyuanVideo 1.5 use multilingual components, but their cards say `en` `zh`.
   This also avoids adding `ja` to items such as Kandinsky 5 and Anima that have no material checked in Japanese.)

If nothing decides it, the key is not set (no guessing).
`[]` is for when a person decided "that input or output does not exist"
(image to 3D takes no prompt text; `speech` of music without singing).
The table is not applied to models that have no task that takes prompt text
(`appinfo.PROMPT_TASKS`: `t2i` `i2i` `t2v` `i2v` `v2v` `flf2v` `ref2v` `a2v` `t2a` `inpaint` `outpaint` `control`).
Codes are ISO 639-1 two letters (three-letter codes on cards are converted, such as `eng→en`; `multilingual` and `und` are discarded).

Text-reader component table (`appinfo.TEXT_ENCODERS`; start of the file name; card text checked on 2026-10-21):

| Component file name | Result | Basis |
|---|---|---|
| `umt5*` | multilingual | `language` of the card https://huggingface.co/google/umt5-xxl has `multilingual`, `ja`, `zh`, `en` and over 100 languages |
| `qwen_3_*` | multilingual | https://huggingface.co/Qwen/Qwen3-4B: "Support of 100+ languages and dialects" |
| `qwen3.5*` | multilingual | https://huggingface.co/Qwen/Qwen3.5-9B: "Expanded support to 201 languages and dialects" |
| `qwen_2.5_vl*` `qwen2.5-vl*` | multilingual | the base LLM https://huggingface.co/Qwen/Qwen2.5-7B-Instruct: "over 29 languages, including … Japanese" (the VL card itself has no language description) |
| `clip_l*` `clip_g*` | English only | https://huggingface.co/openai/clip-vit-large-patch14: "not been purposefully trained in or evaluated on any languages other than English" |
| `t5xxl*` `t5-base*` | English only | https://huggingface.co/google/t5-v1_1-xxl (`language: en`, C4) |
| family `sd`, whose main model contains CLIP | English only | same CLIP as above |

"Multilingual" means the card's languages are not narrowed. "English only" means the card's languages are narrowed to `en`.
When there are several components:

- If any one is multilingual, the result is multilingual.
- If all are English-only components, the result is English only.
- If a component not in the table is mixed in (Gemma, Mistral, Llama, Qwen3-VL, gpt-oss and so on; the card has no language description, or it was not checked), it is left undecided (the card is used as is, if there is one).

The hand table `src/genmodelfit/data/overrides.json` has 35 rows (16 for languages and descriptions, and 19 that only set the runtime of HF speech models; see "Runtime". MeloTTS-English has a runtime added to its language row). Of the 16 for languages and descriptions:

9 were checked by the app on a real machine (matched to `notes` of AI生成キット's catalog.json and the real-machine checks):

- Z-Image Turbo (2 rows) and Wan 2.1/2.2: `prompt` is `en` `ja` `zh`.
  The notes: "日本語の指示もある程度理解しますが、英語で書くとより正確" (understands Japanese prompts to some extent, but English is more accurate) and "日本語・英語・中国語の指示を理解します" (understands Japanese, English and Chinese prompts).
  The Z-Image card says only `en`, but a real machine confirmed that Japanese prompts also work through the Qwen3 component.
- SDXL: `en` only ("日本語はほとんど理解しません", almost no Japanese understanding).
- ACE-Step 1.5: `prompt` is `en` ("曲の雰囲気は英語で書くとより正確", the mood of the song is more accurate in English). `speech` is `en` `ja` ("日本語の歌詞にも対応", Japanese lyrics are also supported).
  The card says "50+ languages" with no list, so only the 2 that were checked.
- Stable Audio 3 Small sound effects: `prompt` is `en`, and `speech` is `[]` (no singing).
- Hunyuan3D 2.0/2.1: `prompt` is `[]` (from an image).

The other 2 rows fix errors in the card's `language` (the row's `_note` has the basis; update does not read `_note`):

- Wan2.2 S2V: the card https://huggingface.co/Wan-AI/Wan2.2-S2V-14B says only `es`. Set to `en` `zh`, matching the card of the same Wan2.2 https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B.
- MeloTTS-English: the card https://huggingface.co/myshell-ai/MeloTTS-English says `ko`. Set to `en` because it is an English voice.

One more is the singing language (0.6.0, #41):

- `speech` of ACE-Step v1's `audio_ace_step_1_t2a_song` (with singing) is the 10 languages it sings well: `en` `zh` `ru` `es` `ja` `de` `fr` `pt` `it` `ko`.
- Basis: README of https://github.com/ace-step/ACE-Step, which the card of https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B points to:
  "Supports 19 languages with top 10 well-performing languages including: English, Chinese, Russian, Spanish, Japanese, German, French, Portuguese, Italian, Korean".
- The prompt is `en` by the music rule.
- It is not set for `audio_ace_step_1_t2a_instrumentals` (no singing).

The other 4 rows are Qwen-Image 2.1 (0.12.0, #81):

- With the prompt enhancer part (`qwen3.5_9b_…_pe_*`) made an optional part and left out of the language material, the only
  text-reading component is `qwen3vl_8b`, which is not in the table, so the language would be undecided.
- All 4 have `prompt` `en` written. Basis: the official template's default prompt is English (the template is a primary
  source).
- Only `image_qwen_image_2_1_image_edit` also gets `ja`: the owner confirmed on a real machine (RTX 3070 8GB, the official
  `qwen_image_2.1_int8_convrot`, the same node layout as this template) that a Japanese instruction passed without translation
  edited the image correctly (the 2026-10-26 addition to Issue #81). The Turbo edit and the text-to-image templates were not
  tried in Japanese, so they do not get it.
- `zh` is not added: the Qwen-Image 2.1 card (https://huggingface.co/Qwen/Qwen-Image-2.1 , checked on 2026-10-28) has no
  language description.

The manual models `src/genmodelfit/data/manual.json` (4 rows):

- Tsukuyomi-chan: `speech` is `ja` `en`. The card (https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan) `language` lists 6 languages (ja en zh es fr pt), but the app confirmed only Japanese and English on a real machine, so only those.
- Irodori-TTS: `ja` (`language: ja` on the card).
- Stable Audio 3 Small music: `prompt` is `en`, `speech` is `[]`.
- Supertonic 3: `speech` is the 31 languages of the card (the card's `language` and the "Supported Languages" table in its body agree).

### Runtime

Field `runtime`.

- From an official template: `comfyui`.
- From HF: only items that match a ComfyUI family (`comfy_family`) and are not speech get `comfyui`.
- Speech from HF works differently for each model, so no rule decides it; only what the hand table says (`runtime` in
  `overrides.json`; 0.14.0, #142; checked against the card's usage, `library_name` and the official README, with the basis
  in the row's `_note`; all 20 in the list on 2026-10-31): `cosyvoice` `audio-cpp` `transformers` `qwen-tts` `chatterbox` `qwentts-cpp` `parler-tts` `sglang-omni` `higgs-audio-v3-studio` `kokoro` `omnivoice` `vibevoice` `melotts` `voxcpm` `vieneu` `speechbrain`. Speech not in the hand table is null.
- Manual models use the value written (`piper`, `irodori-tts`, `supertonic`, `comfyui`).
- For a model whose value is neither `comfyui` nor null, the estimate also counts files in formats ComfyUI does not use, such as `.onnx` (`estimate.select_files`; Tsukuyomi-chan's weights are onnx).
- The way other models are counted is unchanged.

### Description

Field `description`.

- Only hand-written ones in `src/genmodelfit/data/overrides.json` and `src/genmodelfit/data/manual.json` (the Japanese is the `summary` of AI生成キット's catalog.json; the English is a translation of it).
- Deleting a row from the hand table also removes the description at the next application.
- Whether a model runs on a PC without a GPU is not written, so it never disagrees with the judgment.
  genmodelfit currently judges SDXL and Stable Audio 3 Small as Too tight at `--vram 0`. The CPU judgment is tracked in https://github.com/laxshmate/genmodelfit/issues/39.

### Manually added models

`source: "manual"`, `src/genmodelfit/data/manual.json`.

These are models that the popularity-order collection misses. The ID is `manual:<name>`.
`files` has `url` `sha256` `size_bytes` `directory` `role`, and per file `license` `license_name` `license_from` `license_url` `license_evidence` (`appinfo.manual_entries`).

update puts them through the same path as other models:

- The file-set license is `set_entry_license` (the strictest).
- `files_complete` and precision (read from headers).
- The ComfyUI family (`families.assign`).
- The HEAD check (`check_heads`).
- It also fetches the card of `license_from`. If it disagrees with the written value, the **stricter** is used and a warning is issued
  (`appinfo.verify_manual_licenses`; if the card cannot be read, the written value is kept and a warning is issued).
- If the written value is a stricter tier than the card, no warning is issued, because it was made stricter by reading the restrictions in the body (Irodori-TTS).
- If `license` equals the card's and the card has no `license_name`, the written `license_name` is taken as a refinement from the body (LICENSE): no warning and no rewrite (0.14.0, #127. Supertonic 3's card says only `openrail` and its LICENSE body is Open RAIL-M, so `openrail` / `openrail-m`; AI生成キット's allow-list checks the name too). The LICENSE URL goes in `license_evidence`.

About paths:

- `directory` is a one-level place (for ComfyUI models, ComfyUI's model folder; for speech, `tts`).
- Subfolders go in `name`, as with other models (Irodori-TTS's `tokenizer/tokenizer.json` and `dacvae/weights.pth`).
- `download` puts files at `<location>/<directory>/<name>` (`tts/tokenizer/tokenizer.json`). `downloader.safe_relpath` does not allow `/` in `directory`, so do not write it there.

Merging with HF collection:

- If the HF collection picks up the same repository as a manual model (the `license_from` and `homepage` of the main file), that `hf:` item is not put in the list (`appinfo.merge_manual`).
- Components such as T5Gemma, and the original model Irodori-TTS v4, are not removed.

Gate:

- Manual models whose `runtime` is not `comfyui` (piper, irodori-tts, supertonic) are put on hold by gate with `app_runtime`, instead of `official_workflow`, `custom_nodes` and `comfyui_version`
  (this list cannot verify how they run. See [gate.md](gate.md)).

Sources of the first 3 (checked on 2026-10-21 with the HF API and the card text. sha256 and size match `lfs` in the Hub API.
For small non-LFS files, the file was actually downloaded and its sha256 computed):

| ID | License | Where checked |
|---|---|---|
| `manual:tsukuyomi-chan-piper-plus` | `other` / `tsukuyomi-chan-corpus` (Tsukuyomi-chan corpus terms of use; not in SPDX) | the card https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan: `license: other`, `license_name: tsukuyomi-chan-corpus`, `license_link: https://tyc.rei-yumesaki.net/material/corpus/#terms3`. It has a credit requirement and prohibited uses of the output audio (the app's `credit` / `conditions`; not held in the schema) |
| `manual:irodori-tts-v4.1-small` | `other` / `mit-with-ethical-restrictions` (the tier with use restrictions. The voice codec is `mit`) | The cardData of https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small is `license: mit` and the body says "released under MIT". But the same card's "Ethical Restrictions" (https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small#ethical-restrictions: do not imitate a real person's voice without consent, do not make audio for deepfakes or disinformation) restricts use, so it does not go in the permissive tier (it also does not match `mit` in gate's allow-list). The voice codec https://huggingface.co/Aratako/Semantic-DACVAE-Japanese-32dim is also `license: mit` (the original facebook/dacvae-watermarked is `apache-2.0`) |
| `manual:stable-audio-3-small-music` | `other` / `stable-audio-community`; the text-reading component `t5gemma_b_b_ul2` is `gemma` | the card https://huggingface.co/stabilityai/stable-audio-3-small-music (`license: other`, `license_name: stable-audio-community`, `gated: auto`). The same repository has `LICENSE_GEMMA.md`. The URL is a copy at the non-gated https://huggingface.co/Comfy-Org/stable-audio-3 (`stable_audio_3_small_music.safetensors`, `t5gemma_b_b_ul2.safetensors`). The T5Gemma entry is the same as `audio_stable_audio_3_medium`, which uses the same file (`stable-audio-3-t5gemma` in `license_sources`) |
| `manual:supertonic-3` (0.14.0, #127; checked on 2026-10-31) | `openrail` / `openrail-m` (BigScience Open RAIL-M; the tier with use restrictions; the name from the LICENSE body) | the card of https://huggingface.co/Supertone/supertonic-3 at revision `3cadd1ee6394` (`license: openrail`; the body says "The accompanying model is released under the OpenRAIL-M License"; the sample code is MIT) and `LICENSE` at the same revision (Attachment A use restrictions: no impersonation or deepfakes without consent, and others). The file URLs are pinned to the revision, `resolve/<revision>/…` (with main, the sha256 would stop matching when the content changes). 16 files, 401,276,744 bytes in total. For the 4 `.onnx` files the first 8 MB were read with Range and every weight was float32, so the header precision `fp32` is written (the card's "about 99M parameters" and the total size also match at 4 bytes each). `config.json` only exists to count downloads and the SDK does not use it, so it is left out. The developer's GitHub (supertone-inc/supertonic) is archived ("This repository is archived. Development and support have ended."), so no updates or fixes will come. The weights are also at supertone-oss-archive/supertonic-3 with the same LFS oids (also written in `license_evidence`) |

Manual models with a Hugging Face repository join the Hugging Face pool of the popularity rank (`popularity`), but they have no all-time download count (`downloads_all_time`), so their rank is 0 (the bottom; [scoring.md](scoring.md) "Popularity").

### Manually added components

`components` in `src/genmodelfit/data/manual.json`, 0.6.0, #40.

The HF collection picks up only the repository's main model (and components in the same repository), so components in a different repository can be missing.
`components` is a table "model ID → files to add". update (rebuild, `--heads-only`, `--fields-only`) applies it every time inside `appinfo.merge_manual`
(a file with the same URL is not added twice; applying it any number of times gives the same result).
**The license is not written in the table. It is decided by a row of the component source table (`license_sources.py`, the "Source table" above)** (A row whose name pattern contains `/` is matched against the whole path in the repository, to tell `model.safetensors` from `audio_tokenizer/model.safetensors` in the same repository; 0.14.0, #142. URLs of parts added from then on are pinned to a revision.)
(no row means unknown, so it never errs on the permissive side).
The reason the model uses that component goes in `evidence`, and it is left at the end of the file's `license_evidence` as `manual_component: <reason>`.
The ID is not changed (this keeps the ID on the user side).

| ID | File added | License | Where checked (2026-10-21) |
|---|---|---|---|
| `hf:stabilityai/stable-audio-3-small-sfx` | `text_encoders/t5gemma_b_b_ul2.safetensors` of https://huggingface.co/Comfy-Org/stable-audio-3 (sha256 `1e1eba25…`, 1,187,264,003 bytes. T5Gemma for ComfyUI) | `gemma` (`stable-audio-3-t5gemma` in `license_sources`) | Hub API of https://huggingface.co/stabilityai/stable-audio-3-small-sfx: it has the files `t5gemma-b-b-ul2/model.safetensors` (sha256 `9b05ea5a…` = same as `model.safetensors` of https://huggingface.co/google/t5gemma-b-b-ul2; google/t5gemma-b-b-ul2 is `license: gemma`) and `LICENSE_GEMMA.md`, and the application text (`extra_gated_prompt`) says "This model also includes components redistributed under the Gemma Terms of Use (https://ai.google.dev/gemma/terms)". Stability AI's https://github.com/Stability-AI/stable-audio-tools/blob/main/docs/conditioning.md: "This uses a T5Gemma (https://huggingface.co/google/t5gemma-b-b-ul2) encoder" |
| `hf:OpenMOSS-Team/MOSS-TTS-v1.5` (0.14.0, #142; this and below checked on 2026-10-31) | `model-00001-of-00002.safetensors` and `model-00002-of-00002.safetensors` of https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer (7,098,461,728 bytes in total; the audio tokenizer) | `apache-2.0` (`moss-audio-tokenizer`) | `from_pretrained` in MOSS-TTS v1.5's `processing_moss_tts.py` (revision cdd3b911) loads `OpenMOSS-Team/MOSS-Audio-Tokenizer` unless told otherwise |
| `hf:ResembleAI/chatterbox` | `s3gen.pt` (1,057,165,844 bytes), `ve.pt` and `conds.pt` in the same repository | `mit` (`chatterbox-parts`) | `from_pretrained` / `from_local` in `src/chatterbox/mtl_tts.py` of https://github.com/resemble-ai/chatterbox (commit 5de7a54a) load them. Using the listed main file `t3_mtl23ls_v3.safetensors` needs `t3_model="v3"` (without it, v2) |
| `hf:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`, `-0.6B-CustomVoice` | `speech_tokenizer/model.safetensors` in the same repository (682,293,092 bytes) | `apache-2.0` (`qwen3-tts-speech-tokenizer`) | `from_pretrained` in `modeling_qwen3_tts.py` of https://github.com/QwenLM/Qwen3-TTS (commit 022e286b) downloads and loads `speech_tokenizer/` (and stops without it) |
| `hf:openbmb/VoxCPM2` | `audiovae.pth` in the same repository (376,951,122 bytes) | `apache-2.0` (`voxcpm2-audiovae`) | `src/voxcpm/model/voxcpm2.py` of https://github.com/OpenBMB/VoxCPM (commit cce58a5b) loads it (and stops without it). The ZipEnhancer denoiser is on ModelScope and the card's example uses `load_denoiser=False`, so it is not added |
| `hf:FunAudioLLM/Fun-CosyVoice3-0.5B-2512` | `llm.pt`, `flow.pt`, `hift.pt`, `speech_tokenizer_v3.onnx`, `campplus.onnx` in the same repository (4,434,743,215 bytes in total; URLs pinned to revision 29e01c4e) | `apache-2.0` (`cosyvoice3-parts`) | `CosyVoice3` in `cosyvoice/cli/cosyvoice.py` of https://github.com/FunAudioLLM/CosyVoice (commit 074ca6dc) loads them. `CosyVoice-BlankEN` is Qwen's config and tokenizer and `llm.pt` overwrites its weights, so the estimate may count the LLM twice and come out high |
| `hf:k2-fsa/OmniVoice` | `audio_tokenizer/model.safetensors` in the same repository (805,665,628 bytes; pinned to revision c5fdb5cc) | `other` / `boson-higgs-audio-2-community-license` (`omnivoice-audio-tokenizer`); the file set stays at the main file's `cc-by-nc` | `omnivoice/models/omnivoice.py` of https://github.com/k2-fsa/OmniVoice (commit 08be0b4c) loads `audio_tokenizer/` |
| `hf:Serveurperso/Qwen3-TTS-GGUF` | `qwen-tokenizer-12hz-Q8_0.gguf` in the same repository (291,150,624 bytes; pinned to revision b7ee2e8c) | `apache-2.0` (`qwentts-cpp-tokenizer`) | the card: "The tokenizer is shared across every talker". The listed talker is Q8_0, so Q8_0 |

The other Stable Audio 3 items (`audio_stable_audio_3_medium`, `_medium_base`, `manual:stable-audio-3-small-music`) already have the same component
(tests/test_appinfo.py checks that every Stable Audio 3 item has a `gemma` T5Gemma).

## Uncensored models

Rule from CLAUDE.md: uncensored models are not removed from the list. They get `content: "uncensored"` and a basis (`content_evidence`).

**Templates**:

- If the ID, title or tags contain `uncensored` / `nsfw` / `abliterated` / `heretic` / `unfiltered`, the evidence is `name:<word>`.
- If the HF repository of a file in use has the tag `not-for-all-audiences`, the evidence is `hf-tag:not-for-all-audiences (<repo>)`.
- As of 2026-10-20, 0 templates match.

**Hugging Face search**:

- For each pipeline_tag (text-to-image, image-to-image, text-to-video, image-to-video, text-to-speech, text-to-audio, image-to-3d, text-to-3d),
  take 100 each in order of downloads, with `filter=not-for-all-audiences` and `search=uncensored|nsfw|abliterated|heretic|unfiltered`.
- To pick up newly rising models, `filter=not-for-all-audiences` and the searches for types the templates do not cover are also run with `sort=trendingScore` and `sort=likes30d`.
- Every search receives the all-time download count with `expand[]=downloadsAllTime`.
  If it is not returned, the count of the last 30 days is used instead, and the number of such candidates is kept in `sources.huggingface-search.all_time_missing`.
- Trending searches are placed first. An item that also appears in a downloads-ordered search is not treated as trending-only (floor: 10,000).
- Only items with evidence (`hf-tag:not-for-all-audiences`, or `name:<word>` in the name) become candidates. They are narrowed in this order:

1. Remove those that match the exclusion check (below) (only the count is kept).
2. Remove those with **fewer than 10,000 all-time downloads** (`skip_downloads`).
   - Exception: items found only by the trendingScore / likes30d searches that are **really rising**
     (trendingScore in the search result at least 0.5, and at least 100 downloads in the last 30 days) are admitted from 1,000.
   - This follows llmfit's `scripts/scrape_hf_models.py` (`min_downloads=10000`, discovery by trendingScore and likes30d) (decided by the user on 2026-10-20).
     The aim is to keep "models nobody uses" out of the list.
   - The rising condition was added on 2026-10-21 (#19). In likes30d order, old unused items (5 in the last 30 days and so on) had been admitted at the 1,000 floor.
   - **Uncensored items also need at least 100 downloads in the last 30 days** (`uncensored_inactive`; #19).
3. Remove test or unfinished names (starting with `tiny-random`, `tiny-dummy`, `-random-init`, `ci-random-`, `test-`) (`test_stub`).
   Remove merges: those with a `merge` / `mergekit` tag, or **with the word merge / mergekit in the repository name** (`merge`; same as llmfit. The name rule was added in #19. "mix" is not included).
4. Remove LoRA, LyCORIS, adapters and embeddings (by name, or the `lora` / `peft` / `adapter` / `qlora` tags).
   They do not run without a main model, and whether they fit is decided by the main model.
5. Remove component-only items (the name contains text-encoder, VL, CLIP, VAE, GPTQ, AWQ, MLX and so on).
6. For copies with the same repository name (different owners), keep only the one with the most downloads.
   Names are compared after stripping trailing quantization/format words
   (`-GGUF`, `-Q8_0`, `_K_M`, `-fp8`, `-nf4`, `-int8`, `_diffusers`, `-Quantized` and so on) and keeping only alphanumerics (`update.copy_name`; #19.
   Example: `Wai-NSFW-Illustrious-v140-Q8-GGUF` is a copy of `waiNSFWIllustrious_v140`).
   At most 2 items per owner per type.
7. Keep at most the **top 30** per type (so the list does not grow too large).
   Fetch repository info and the model card, and remove those that match an exclusion on the card, those whose weights cannot be selected, and those whose main file is under 30 MB (diffs or empty repositories).
8. Remove **copies of the same weights** (with different names): items whose set of selected files' sha256 is the same,
   or whose origin (the followed base_model, the matched family) overlaps and whose total size is within ±1%.
   The one with more downloads is kept (`sources.huggingface-search.skipped.duplicate_weights`).

The license is verified with the rules in "Licenses" above (the author's own notation is not used as is. If an uncensored derivative's origin is unknown, it is unknown).

The ID is `hf:<owner>/<repo>` and `source: "huggingface"`. `discovered_from` holds the search URL where it was found, and `downloads` (last 30 days) / `downloads_all_time` (all time) / `likes` are filled in.
The bundled list was also aligned to this rule on 2026-10-20 (from 64 HF items, remove 16 with fewer than 10,000 all-time and 2 with the merge tag, leaving 46.
Uncensored went from 39 to 24). It was rebuilt with the #19 rules on 2026-10-21 ([release.md](release.md)).
For counts, see `sources.counts` and `sources.huggingface-search` in the list (the bundled list of 2026-10-21T06:20Z has 48 HF items and 26 uncensored).

**User templates**:

- Templates whose `username` in index.json is not `ComfyUI` (32 as of 2026-10-20, such as hellorob and PurzBeats) are kept in `Template.username` and get `community: true`.
- If an official workflow with the same weights exists, they are not made the representative of the entry (they stay in the workflow list).
- A user template with no official counterpart is shown as one model as it is, but ranked after the official ones (see [schema.md](schema.md), "Order of `recommend`").
- For the popularity rank in the `recommend` order, template `usage` and HF all-time downloads are never mixed: each becomes a percentile within the same type and source (0.11.0; the old conversion "HF downloads divided by 100 to match the template scale" was dropped; [scoring.md](scoring.md) "Popularity").

**Types the templates do not cover**:

- Local speech templates: 0 (Chatterbox has `size` 0). Sound effects: 1 (Stable Audio Open). So HF popularity order fills the gap (`content` is `sfw` if there is no evidence).
- Speech: the top 20 of `pipeline_tag=text-to-speech`.
- Sound effects: from `pipeline_tag=text-to-audio` with `search=sfx|foley|sound|audioldm|stable-audio-open`, those judged to be sound effects, the top 10.
  Judged as sound effects means: sound effect / sfx / foley in the name or tags, or the MMAudio, AudioLDM or Stable Audio Open families without the word music.

### Rules for choosing the files to use from an HF repository

`hf.select_weight_files`.

The estimate (#4) uses the total of the files, so **only the one set needed to run** is included (different versions of the same component are not stacked).

1. Only files with a weight extension (`.safetensors` `.gguf` `.ckpt` `.bin` `.pth` `.pt` `.sft`) whose size is known.
   `optimizer` `scheduler` `safety_checker` `feature_extractor` `onnx` `flax` `voices/` `samples/` and so on are excluded.
2. If there is a **single-file main model** at the repository root, only that is used, even if a diffusers layout also exists (the form ComfyUI reads).
3. If there is only a diffusers layout (`model_index.json` exists and no main file at the root), one version per component folder:
   - `transformer` / `transformer_2` / `unet` / `prior` / `decoder` → main
   - `text_encoder*` → text_encoder
   - `vae` → vae
   - `image_encoder` → clip_vision
   - safetensors first. Precision order fp16 → bf16 → the unmarked default. Splits (`-00001-of-00003`) are taken together.
4. Otherwise, split files by file name into roles (main, text encoder, VAE; `mmproj`, tokenizer, codec and LoRA are not used) and choose one version per role:
   - GGUF: Q8_0 → Q4_K_M → Q5_K_M → Q6_K → Q4_K_S → Q5_K_S in this order; if none, the smallest.
   - Otherwise: safetensors first (.bin / .ckpt / .pth only if there is no safetensors); an fp32 version only if there is no other; of the rest, the largest (the complete main model).
   - For the main model, if some file shares words with the repository name, choose from those (avoids bundles of another model).
   - Text encoders and VAEs only from the same folder as the main model, the root, or a folder named for the role (`text_encoders`, `vae` and so on).
   - A repository with no main model at the root, whose main models are spread over 4 or more folders not named for roles, is regarded as an assortment of separate models and is not included.
5. If no main model can be chosen, the item is not put in the list. `precision` comes from the chosen main file's name.

Because rules choose the files, this can go wrong for repositories that bundle several models.

## Exclusions

Content aimed at sexualizing minors is excluded.

`src/genmodelfit/safety.py`. Excluded items are not put in the list. Only the **count** is kept in `sources.excluded` (names are not kept either).

| Kind | Words | Where applied |
|---|---|---|
| STRONG | loli(s), lolicon(s), loli con, lolita(s), shota(s), shotacon(s), shota con, jailbait, preteen(s), pedo, pedophile, pedophilia, paedophile, paedophilia, toddlercon, underage, ロリ, ロリコン, ロリータ, ショタ, ショタコン, 児童ポルノ | ID, name and tags of every model; model card and description |
| CONTEXT | child, children, kid(s), minor(s), teen(s), teenage(r/rs), schoolgirl(s), school girl(s), schoolboy(s), school boy(s), little girl(s) / boy(s), young girl(s) / boy(s), toddler, infant, cub, jk, 幼女, 児童, 女子小学生, 女子中学生, 小学生, 中学生, JC, JS (only in capitals) | Excluded if in the ID, name or tags of an uncensored model. On cards, only when the same sentence has a sexual word (sex, nude, naked, porn, erotic, nsfw, explicit, lewd, hentai, topless, エロ, ヌード, 裸, 性的, アダルト, R18 and so on); this applies also to models without the uncensored mark |
| DATASET | e621 (an image collection known to include categories of sexualized minors in its training data. Decided by the maintainer on 2026-10-21) | Excluded if in the ID, name, tags or model card of an uncensored model (even if the purpose is not about minors, because it may appear in the output) |

- Words are matched at alphanumeric boundaries.
  - `minor` does not match `minority`, `kid` does not match `kidney`, `teen` does not match `fifteen`. `Loli-XL` and `shota_v2` do match.
  - Katakana words match only when not adjacent to katakana (ロリ does not match カロリー or ロリポップ).
  - `js` is easily confused with JavaScript (such as the `transformers.js` tag), so only capital `JS` counts.
- **Negation and prohibition are not counted only when they directly govern the word.**
  - The negation word (no / not / never / without / do not / prohibited and so on) must come within 8 words before it.
    There must be no punctuation (, ; : . and so on) or separator word ("filters / limits / restrictions / censorship / safety" and so on) in between.
  - A prohibition after the word ("… are strictly prohibited", "…は禁止") is treated the same way.
  - Example: "Do not generate sexual content involving minors." is not counted.
    In "Uncensored, no filters: generates nude teen girls.", the "no" only governs "filters", so teen is counted (previously the whole sentence was ignored).
- **A prohibition of generation** (a prohibition word governing a generation verb, such as "do not use … to generate" or "must not create"):
  - It applies to the whole comma-separated list ("illegal content, non-consensual imagery, or sexual content involving minors").
  - Up to 30 words, until the end of the sentence (. ; : ! ?), are not counted.
  - Mentions as the name of a crime ("child sexual abuse material", which appears in safety explanations) are removed from word matches.
  - On 2026-10-20 real data, this fixed the wrongful exclusion of 2 adult-oriented models that had notices in these two forms.
- When a line in **a prohibiting form** (do not / must not / never / prohibited …), such as "Do not use it to generate:", ends with ":", the bullet list that follows (the prohibited list) is also not counted.
  (It does not apply to a negation-only line like "No filters:".)
- For names of official templates (without the uncensored mark), only STRONG words are applied (so that "children's book" and the like are not excluded).

For the count excluded in the run of 2026-10-20, see `sources.excluded` (names are not kept).
Tests (`tests/test_review_fixes.py`) check that these are not excluded: items whose sample prompts contain "child", and correct adult-oriented models whose notices contain "minors".

## Civitai: not used

We read Civitai's terms of use (https://civitai.com/content/tos, lastmod 2026-10-06) and decided not to use it.

- 11.4: automated access is allowed only when using "the public API etc. **with your own valid credentials**, within the limits".
  It presumes a per-user API key. A tool collecting anonymously and distributing is not envisioned.
- 6.1: the license to use the service is "limited to personal, non-commercial use".
- 7: the service's "compilation, information, data" are Civitai's Materials. 11.10 prohibits selling or transferring Materials.

The genmodelfit list is meant to be bundled and distributed by a closed-source commercial app (AI生成キット),
so it cannot be said to be "clearly allowed" to include information collected from Civitai.
The Civitai URLs in templates (5, 6 places in total) are kept as URLs only, as they are. Size and license stay unknown (no HEAD is sent either).

## Review marks

update (rebuild, `--heads-only`, `--fields-only`, `--inspect-only`) compares with the previous list and sets these items to pending review (`pending`):

- new items
- a license change toward the permissive side
- a decrease in required amounts
- newly uncensored
- **file changes** (`files_changed`, 0.9.0):
  - files added or removed
  - a change in URL (the query is also compared; only the part after `#` is dropped) or in `repo_id`
  - a change in size (bigger, smaller, became known, became unknown)
  - a change or disappearance of an sha256 that had a value (an sha256 that is empty before and newly filled is not flagged)

Items removed with `review reject` (`src/genmodelfit/data/review_rejected.txt`) are not put back by a rebuild.
Items pending review are put on hold by gate's `reviewed`, and they do not enter the distributed list (data-latest).
For details, see [schema.md](schema.md), section "review", and [release.md](release.md), section "Weekly skill (genmodelfit-weekly, #42)".

## When upstream changes shape, the network is down, or part of the data is missing

- index.json is not a list, a category has no `templates`, or a template has no `name` → stops with `UpstreamFormatError`, exit code 1. The cache is left as it was.
- A template JSON has no `nodes` or cannot be read → a warning for each (`sources.warnings`). If it goes over 30%, it stops.
- The list becomes empty → it stops.
- No network (index.json cannot be fetched, none of the template JSONs can be fetched, or over 30% of HF cannot be reached)
  → the previous cache is kept and the exit code is 1. `recommend` and others keep working with the cache (or the bundled list if there is none).
- When a model that has a measurement (`measurements.json`) disappears from upstream, it is carried over from the previous list (the cache, or the bundled one if none) with a warning
  (a cache with unreadable or wrongly typed items is not used, and it falls back to the bundled one; 0.9.1, #63).
- **When part of the data is missing**:
  - Triggers: even one template JSON cannot be read; a transient HF failure (cannot connect, 429, 5xx; 401 / 403 / 404 are treated as "unknown" and not counted);
    the 429 waiting cap was exceeded; the HF search failed.
  - It sets `sources.partial: true` and `sources.partial_counts`
    (`templates_failed`, `hf_repos_failed_transient`, `entries_affected`, `hf_search_failed`, `rate_limit_budget_exhausted`, `carried_over`).
  - For an affected entry (a template that could not be read, or one holding files of a failed repository), if the previous list has the same ID, its values are used.
    This applies only when the previous list was built with the same `sources.license_policy` (so overly permissive licenses from an old rule are not carried in).
  - HF models that should have been found by search are not carried over.
- When part is missing, `update` **stops without writing, with exit code 1** (the previous cache is kept).
  If you accept the gaps, add `--allow-partial` (it writes, exit code 0; `sources.partial` is true).

## Is the template `size` the total of all files?

What we found when we compared them.

In the `update` result of 2026-10-20, for **263** templates whose file sizes are all known, we compared `size` in index.json with the total of the files the template uses
(HF `siblings[].size`) (`sources.size_check` at the time. For the current value, see `sources.size_check` in the bundled list).

| Result of comparison | Count |
|---|---:|
| Equal to the byte | 0 |
| Within ±2% | 179 (68%) |
| Within ±10% | 214 (81%) |
| size is 10% or more smaller than the total | 22 |
| size is 10% or more larger than the total | 27 |
| size is a multiple of 0.01 GiB (a hand-rounded value) | 258 |

Extract (the top 10 are ones with measurements plus representatives, the bottom 3 are examples of outliers):

| Template | size in index | Total of files | Ratio (size / total) |
|---|---:|---:|---:|
| image_z_image_turbo_int8 | 12,133,282,611 (11.30 GiB) | 12,168,299,735 (11.33 GiB) | 0.997 |
| image_z_image_turbo | 20,830,591,386 (19.40 GiB) | 20,690,152,836 (19.27 GiB) | 1.007 |
| sdxl_simple_example | 13,013,750,907 (12.12 GiB) | 13,014,060,264 (12.12 GiB) | 1.000 |
| image_sdxl_simple | 6,979,321,856 (6.50 GiB) | 6,938,078,334 (6.46 GiB) | 1.006 |
| flux_schnell | 17,233,556,275 (16.05 GiB) | 17,236,328,572 (16.05 GiB) | 1.000 |
| text_to_video_wan | 9,824,737,690 (9.15 GiB) | 9,828,025,775 (9.15 GiB) | 1.000 |
| video_wan2_2_5B_ti2v | 18,146,236,826 (16.90 GiB) | 18,144,966,705 (16.90 GiB) | 1.000 |
| audio_ace_step_1_5_checkpoint | 10,737,418,240 (10.00 GiB) | 10,025,478,736 (9.34 GiB) | 1.071 |
| audio_stable_audio_example | 5,690,831,667 (5.30 GiB) | 5,745,535,406 (5.35 GiB) | 0.990 |
| 3d_hunyuan3d_image_to_model | 4,928,474,972 (4.59 GiB) | 4,928,151,562 (4.59 GiB) | 1.000 |
| 3d_hunyuan3d-v2.1 | 4,928,474,972 (4.59 GiB) | 7,365,943,290 (6.86 GiB) | 0.669 |
| flux_fill_outpaint_example | 10,372,346,020 (9.66 GiB) | 34,174,211,972 (31.83 GiB) | 0.304 |
| templates_shane_change_any_objects | 10,737,418,240 (10.00 GiB) | 46,495,526,446 (43.30 GiB) | 0.231 |

Conclusions:

- `size` can be taken as **the total of the whole set (all model files the template uses), rounded in GiB and written by hand**
  (most are within ±2%, and almost all are in 0.01 GiB steps). But no item matches to the byte, so it is only a guide.
- Outliers we looked into:
  - A copy of another template's value: `3d_hunyuan3d-v2.1` has the same 4.59 GiB as 2.0, but the 2.1 main model is 6.86 GiB.
  - size too small: `flux_fill_outpaint_example`'s main model FLUX.1 Fill alone (23.8 GB) exceeds the size of 9.66 GiB.
    `templates_shane_change_any_objects` also has a main model of 34.7 GB in fp16 (size is 10 GiB; it may assume a smaller precision).
  - size too large: custom nodes fetch their own weights and the main model is not in `properties.models`
    (`video-wan21_scail` and `templates-wan2_1_infinitetalk_music` list only 1.26 GB of clip_vision).
- Therefore genmodelfit **uses the total of file sizes, and uses `size` only as a fallback when no file is written** (`ModelEntry.total_size_bytes`).
  Templates with no main file written, or with a total under half of size, get `files_complete: false`,
  and the index size goes into `size_floor_bytes` so the estimate is not read too small (see "Files" above).
