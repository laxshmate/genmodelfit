# Hardware detection and judging (#21)

**English** · [日本語](hardware.ja.md)

What genmodelfit checks on this PC, and how each item feeds the judgment.
For developers and contributors who want to know how GPUs, memory and drivers are detected, and which primary sources the rules rest on.
The implementation is `src/genmodelfit/hardware.py` (detection) and `src/genmodelfit/estimate.py` (`gpu_precision`, `ram_limit`).
The output shape is in [schema.md](schema.md), section "System".

The primary sources were read on 2026-10-20 (versions and commits are given in each section).
llmfit's `llmfit-core/src/hardware.rs` (commit bffd1b6, MIT) was used for ideas only; no code was copied.

## Contents

- [What it checks](#what-it-checks)
- [nvidia-smi](#nvidia-smi)
- [Generations, PyTorch and drivers](#generations-pytorch-and-drivers)
- [Precision (dtype and VRAM chosen by ComfyUI)](#precision-dtype-and-vram-chosen-by-comfyui)
- [GPUs other than NVIDIA (0.7.0, #22, #23)](#gpus-other-than-nvidia-070-22-23)
- [Families that run on CPU (0.7.0, #39)](#families-that-run-on-cpu-070-39)
- [Measurements that succeeded on the same GPU (0.7.0, #26)](#measurements-that-succeeded-on-the-same-gpu-070-26)
- [Every environment (0.11.0, #101)](#every-environment-0110-101)
- [Memory and virtual memory](#memory-and-virtual-memory)
- [VRAM used by the display etc. (current free)](#vram-used-by-the-display-etc-current-free)
- [Comparison with llmfit](#comparison-with-llmfit)
- [Timing](#timing)
- [Tests](#tests)

## What it checks

| Item | Source | Used for judging? |
|---|---|---|
| NVIDIA GPU (name, total VRAM, free, used, driver, compute capability) | `nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used,driver_version,compute_cap,mig.mode.current --format=csv,noheader,nounits` (the MIG state was added in 0.11.0) | Yes (the one with the most VRAM among those with backend `cuda`) |
| Display adapters, including non-NVIDIA | Windows: PowerShell `Get-CimInstance Win32_VideoController` + registry `HardwareInformation.qwMemorySize`. Linux: `lspci -Dnn` (AMD VRAM / GTT from sysfs `mem_info_vram_total` / `mem_info_gtt_total`). macOS: `system_profiler SPDisplaysDataType -json` | Only when no NVIDIA GPU is usable: AMD, Intel and Apple are judged with the support table (0.7.0; see "GPUs other than NVIDIA" below). The PCI model ID (`pci_id`) is also read (0.9.0; used to match measurements of the same GPU and for bench submissions) |
| Physical memory | psutil (if it fails, the OS's own reading; on Linux, the container (cgroup) limit if there is one) | Yes |
| Virtual memory | Windows: `GlobalMemoryStatusEx` (ctypes) and registry `Memory Management\PagingFiles`. Linux: `/proc/swaps` (zram excluded). macOS: treated as growing automatically | Yes (the upper limit when memory is short) |
| Free disk | `shutil.disk_usage` | Display only |

Not read and not kept: PC name, user name, GPU UUID, PCI slot (bus id), the latter part of PNPDeviceID (instance / slot IDs).

- From the WMI PNPDeviceID, the first part that names the model (`PCI\VEN_xxxx&DEV_xxxx&SUBSYS_...&REV_...`) is used to match registry entries.
- Only its VEN and DEV parts are kept in the output as the PCI model ID (`pci_id`, in the form `1002:744c`) (0.9.0; used to match measurements of the same GPU).
- SUBSYS, REV and the latter part (instance / slot IDs) are not kept (`tests/test_hardware.py::test_system_json_has_no_private_values`).
- On Linux the same form is kept from `[1002:744c]` in `lspci -nn`.

What is read is used locally and never sent anywhere.

## nvidia-smi

- Location: PATH, then `%SystemRoot%\System32\nvidia-smi.exe` (current Windows drivers), then `%ProgramFiles%\NVIDIA Corporation\NVSMI\nvidia-smi.exe` (old drivers), then `/usr/bin` on Linux, `/usr/lib/wsl/lib` on WSL, `/usr/local/bin`.
- Time limit 10 seconds (normally 0.1 to 0.5 seconds). On Windows no window is shown (`CREATE_NO_WINDOW`, because GUI apps call it).
- How the output is read:
  - Read as UTF-8; unreadable characters are replaced.
  - Split on `, `; lines with the wrong number of fields are skipped (error messages, blank lines, broken lines).
  - `[N/A]`, `[Not Supported]`, `N/A` and empty mean "unknown".
  - If free is unknown but total and used are known, free = total - used.
  - Values with units (`8192 MiB`) and comma decimals (`8,6`) are also read.
- A GPU that returns no total VRAM (one that shares memory with the CPU) is not used for judging (backend `none`, with a reason). DGX Spark (GB10) is the exception and is used as a unified-memory GPU (0.11.0; "DGX Spark (GB10)" below).
- An old nvidia-smi that does not know `compute_cap` exits with code 2 and "Field "compute_cap" is not a valid field to query." (confirmed on a development PC by asking for a field that does not exist; the driver version that introduced it could not be confirmed in primary sources).
  - It then asks again without `compute_cap` and infers it from the GPU name table (`hardware._NAME_CC`).
  - Ambiguous names (for example the laptop GTX 960M, where one model name mixes generations) are not inferred.
  - The table follows NVIDIA's lists (https://developer.nvidia.com/cuda-gpus, https://developer.nvidia.com/cuda-legacy-gpus).
  - Examples: "NVIDIA TITAN X" is Pascal (6.1), "GeForce GTX TITAN X" is Maxwell (5.2), Quadro K620 / K1200 / K2200 are Maxwell (5.0), the laptop RTX 2050 is Ampere (8.6).
  - Workstation Ada cards: besides "RTX 4000 Ada Generation", the spellings "RTX 4000 SFF Ada Generation" and "RTX 2000E Ada Generation" are also Ada (8.9) (NVIDIA product page https://www.nvidia.com/en-us/design-visualization/rtx-4000-sff/ ; for the 2000E, the page of NVIDIA's board partner PNY https://www.pny.com/en-tw/rtx-2000e-ada-generation ; 2026-10-27).
  - A value inferred from the name is marked `compute_capability_source: "name"`.
- NVIDIA GPUs are not identified by PCI device ID (`pci_id` of a GPU from nvidia-smi is empty). Whether two GPUs are the same is decided by the normalized name and the VRAM ratio (`estimate.same_gpu`; the ID is compared only when both sides have one). The display adapters (`other_gpus`) show the WMI / lspci device ID, but it is not used to judge NVIDIA GPUs (`tests/test_nvidia_identity.py`).
- Even if the exit code is not 0, readable lines are used (for example when one GPU cannot be read: "Unable to determine the device handle ... Unknown Error"). The number of GPUs that could not be read is written to notes (the error text contains the PCI slot, so it is not copied).
- Multiple GPUs: ComfyUI runs on one GPU, so the one with the most VRAM among the usable ones (backend `cuda`) is used.
  - ComfyUI uses CUDA device 0 by default (`get_torch_device` -> `torch.cuda.current_device()`).
  - CUDA numbering defaults to `CUDA_DEVICE_ORDER=FASTEST_FIRST` (fastest first; this can differ from the PCI order of nvidia-smi).
  - nvidia-smi cannot tell for sure which one is device 0. With GPUs of different models, notes says "point ComfyUI at the largest GPU with `--cuda-device`".
- When `--vram` overrides:
  - If there is a usable NVIDIA GPU, only its VRAM changes; its generation, driver and precision are kept (the judgment "if this PC's GPU had a different VRAM").
  - If there is none, or its generation is not usable, a placeholder GPU of unknown generation is used (no precision adjustment).
  - `--ram` changes only the installed amount and does not use this PC's page file or commit (it assumes another PC).

## Generations, PyTorch and drivers

### Compute capabilities supported by PyTorch CUDA builds

PyTorch ships machine code (SASS) only (`_ptx_arches` in `.ci/manywheel/build_env_setup.py` v2.14.1: "Release/RC wheels ship SASS-only").

- Machine code runs only on GPUs with the same major and a minor equal to or larger (CUDA Programming Guide 1.3.4.1 Binary Compatibility: https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/cuda-platform.html).
- Example: code for 8.6 runs on 8.6 and 8.9, but not on 8.0 or 9.0.
- Jetson 5.3, 6.2, 7.2, 8.7 and similar are handled separately (`DEVICE_REQUIREMENT` in PyTorch). For 8.7 with CUDA 13.2 or later, see "GPU registration tables and the official lists" below.

| Build | PyTorch versions | x86_64 compute capabilities | Source |
|---|---|---|---|
| cu132 | 2.12 to 2.14 | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.14.1 `torch/cuda/__init__.py` `PYTORCH_RELEASES_CODE_CC`, `.ci/pytorch/windows/build_env_setup.py` |
| cu130 | 2.9 to 2.14 | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | same |
| cu129 | 2.8 to 2.13 (Windows: 2.8, 2.9) | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.13.0 `PYTORCH_RELEASES_CODE_CC`, `.ci/pytorch/windows/internal/cuda_config.bat` |
| cu128 | 2.7 to 2.11 (aarch64: 2.7, 2.9 to 2.11) | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.11.0 `.ci/manywheel/build_cuda.sh`, `cuda_config.bat` |
| cu126 | 2.6 to 2.14 | 5.0, 6.0, 6.1 (Windows), 7.0, 7.5, 8.0, 8.6, 9.0 | v2.14.1, same as above |

The CUDA versions of each PyTorch version are in `CUDA_ARCHES` of `.github/scripts/generate_binary_build_matrix.py`:

- 2.7: 11.8 / 12.6 / 12.8
- 2.8: 12.6 / 12.8 / 12.9
- 2.9: 12.6 / 12.8 / 13.0
- 2.10, 2.11: 12.6 / 12.8 / 12.9 / 13.0
- 2.12: 12.6 / 13.0 / 13.2
- 2.13: 12.6 / 12.9 / 13.0 / 13.2
- 2.14.1: 12.6 / 13.0 / 13.2

cu128 and cu129 are not in the current version (2.14).
They are still counted, because they remain on the download site and some users pin versions, such as ComfyUI's bundled build and AI生成キット (a way for Blackwell to run with a driver older than 580).

### Minimum CUDA driver

This uses Table 3 "CUDA Toolkit and Minimum Required Driver Version for CUDA Minor Version Compatibility" in the NVIDIA CUDA Toolkit 13.4 Update 2 release notes (https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html).

- 13.x needs 580 or newer. 12.x needs 525 or newer (Table 3 gives 525 up to below 580 for 12.x; newer drivers also work).
- Table 3 does not split by OS, so it is used as is (Table 4's 527.41 / 525.60.13 are the drivers bundled with CUDA 12.0 GA, not a minimum requirement).
- PyTorch ships machine code only and does not use PTX JIT, so it is assumed to run within minor-version compatibility.

| Build | Windows and Linux |
|---|---|
| cu126 / cu128 / cu129 | 525 or newer |
| cu130 / cu132 | 580 or newer |

Minimum per generation (`hardware.ARCH_MIN_DRIVER`):

- Machine code for Blackwell (10.x, 12.x) is included from CUDA 12.8 (PyTorch cu128 and later; see the table above).
- CUDA 12.8 GA in Table 4 is Linux 570.26 / Windows 570.65. Below 570 the PC is judged as CPU (older drivers do not know Blackwell, so it rarely even shows up in nvidia-smi).
- Other generations follow the build requirements.

nvidia-smi on WSL returns the Windows driver version, so read it against the Windows column.

### Verdict

| Generation | Compute capability | Builds that run | Driver requirement | Verdict |
|---|---|---|---|---|
| Fermi and older, Kepler | up to 3.7 | none | — | **CPU** (a driver update does not help) |
| Maxwell | 5.0, 5.2 | cu126 | 525 | GPU |
| Pascal | 6.0, 6.1 | cu126 | same | GPU |
| Volta | 7.0 | cu126 | same | GPU |
| Turing | 7.5 | cu126 to cu132 | same (580 for cu13x) | GPU |
| Ampere | 8.0, 8.6 | cu126 to cu132 | same | GPU |
| Ada | 8.9 | cu126 to cu132 (8.6 machine code) | same | GPU |
| Hopper | 9.0 | cu126 to cu132 | same | GPU |
| Blackwell | 10.x, 12.x | cu128 to cu132 | 570 (generation minimum) / 580 (cu13x) | GPU |
| Newer than that | 13.x and up | none | — | **GPU** (not refused; the note says "newer than the table; use the latest PyTorch". CPU until 0.10) |

- If a generation has no build that runs, or the driver is too old for every build, the backend is set to `none` and the PC is judged as CPU. The reason goes to `Gpu.notes` and `SystemInfo.notes`, including whether "updating the driver makes it run on GPU".
- If the generation is unknown (nvidia-smi and the name table both fail) or the driver version cannot be read, it does not refuse; it says it does not know.
- `torch_cuda` is the list of builds that run (newest first). It is for reference and is not used for judging.
- It agrees with what AI生成キット learned (Kepler is CPU; Maxwell, Pascal and Volta use cu126; Quadro K620/K1200/K2200 are Maxwell).

## Precision (dtype and VRAM chosen by ComfyUI)

This section is based on `comfy/model_management.py` and `comfy/sd.py` of ComfyUI (commit 426f5ee, v0.43.0; re-read on 2026-10-27).

- It is GPL-3.0, so nothing is imported. Only lists of names were copied, as facts (`hardware._COMFY_FP16_PASCAL`, `_COMFY_FP16_BROKEN`).
- Everything below is the default behavior, without launch arguments.

**Whether fp16, bf16 and fp8 are used**:

- `should_use_fp16`:
  - Used when major >= 8.
  - Not used when major < 6 (Maxwell and older).
  - GPUs whose name contains 1080 / 1070 / 1060 / 1050 / titan x / p3000 ... / p6 / p4 (Pascal) use it **on Windows only** (on Linux it is not used because fp32 is faster).
  - Other GPUs with major < 7 (such as GT 1030) do not use it.
  - GPUs whose name contains 1660 / 1650 / 1630 / T500 / T550 / T600 / MX550 / MX450 / CMP 30HX / T2000 / T1000 / T1200 are treated as "fp16 is broken" and do not use it.
  - Everything else (Volta, Turing RTX) uses it.
- `should_use_bf16`: major >= 8.
- `supports_fp8_compute`: 8.9 or newer (on Windows, torch 2.4 or newer).

**Main model weights** (`unet_dtype` model_management.py:1136 to 1187 / `unet_manual_cast` 1189 to 1211): chosen from each family's `supported_inference_dtypes` (table below).

- Families that have fp16 (SDXL, Flux, Wan and others) are loaded in fp16 and computed in fp16 even on GPUs without bf16 (bf16 weights are also 2 bytes).
- **Families without fp16** (`[bfloat16, float32]`: LTX, Qwen Image, Z-Image, HunyuanVideo, ACE-Step and others): on GPUs without bf16 (Pascal, Volta, Turing), `unet_manual_cast` returns float32 (computation in fp32, working memory doubles).
  - The weights are decided by `should_use_bf16(manual_cast=True)`.
  - They stay in bf16 only if fp32 would not fit in `total x 0.88 - minimum working memory`.
  - If it fits, they are loaded in fp32 (double size).
- GPUs that use no half precision (such as Maxwell) also widen to fp32, but only if it fits. If not, the weights stay at 2 bytes and only the computation is fp32.
- fp8 weights on a GPU without fp8 compute: widened to fp16 if that fits; if not, they stay fp8 (converted at every computation).
- Weights with a quantization config, such as fp8_scaled and int8_convrot, stay quantized (`quant_config`, sd.py:2277).

**Text encoder**: fp16 by default. If the file's precision is 2 bytes or less, it is loaded as is (`pick_weight_dtype`).

**VAE** (`vae_dtype` model_management.py:1290):

- Chosen from each VAE's `working_dtypes` (sd.py; default `[bfloat16, float32]`: sd.py:518). fp16 is chosen when `should_use_fp16` holds, bf16 when `should_use_bf16` holds, and fp32 if neither does.
- The 2D KL VAEs (SD, SDXL, SD3, Flux's ae; the default branch at sd.py:677 to 712), LTX (784) and Mage (629) have no fp16. On GPUs without bf16 they are fp32.
- The Flux2 VAE keeps the KL default without fp16 in 0.40.0 (426f5ee adds fp16: sd.py:693). The product's engine is 0.40, so it is treated as having no fp16.
- Wan (853, 865, 881), HunyuanVideo (795, 828), CogVideoX (812), Oobleck (742) and others have fp16.
- If VRAM runs short at the final decoding, ComfyUI continues in tiles (slow).

**Working memory**: ComfyUI's own estimates (`model_base.memory_required`, and the VAE's `memory_used_decode`) multiply by the size of the computation precision (`dtype_size`). Computing in fp32 doubles it.

How this is applied to the estimate (`estimate.gpu_precision`; only what the code establishes):

| GPU | Examples | Gpu.compute_dtype | Family / VAE | Adjustment to the estimate |
|---|---|---|---|---|
| No half precision | All Maxwell, GT 1030, GTX 1080 on Linux, GTX 1660, T1000 | `fp32` | any | working memory x 2, VAE weights x 2 (x 1 if the VAE file is fp32, or the VAE is fp32 on every GPU) |
| Uses fp16 but has no bf16 | Pascal on Windows (1080 etc.), Volta, Turing RTX | `fp16` | family without fp16 | working memory x 2 |
| same | same | `fp16` | VAE without fp16 | VAE weights x 2 (since #100 also the decode-stage working memory x 2) |
| same | same | `fp16` | family with fp16 | no change (for bf16 weights, the reason says "runs in fp16 (same VRAM)") |
| same | same | `fp16` | family unknown | no change; the reason says "not applied because the family is unknown" |
| No fp8 compute | before Ada | — | fp8 weights | no change; the reason says "widened to fp16 if there is room, slow if not" |
| None of the above | Ampere, Ada, Hopper, Blackwell | `fp16` | any | no change |

**The main model weights are not changed.** Reasons:

- The behavior "load in fp32 only if it fits" uses more memory the more VRAM there is. Putting it in the estimate would increase VRAM and lower the level.
- Weights are counted at the file's 2 bytes, and ComfyUI widens only when it fits, so whether the model runs does not change.
- When it does widen, the working memory can run short, and ComfyUI may offload part of the weights and get slower. The reason says so.

Working memory x 2 and VAE x 2 depend only on the GPU generation and family, not on the amount of VRAM.
So with the generation fixed, adding VRAM, RAM or page file never lowers the level (`test_fit_monotonic_in_vram_per_arch`).
When computing in fp32, `basis` is `estimated` unless the measurement is from that GPU (measurements are from half-precision GPUs).

### Family table (`src/genmodelfit/families.py`, lines of ComfyUI commit 426f5ee)

Main model (`supported_inference_dtypes` in `comfy/supported_models.py`; classes without one use `[float16, bfloat16, float32]` from `supported_models_base.py:47`):

| Family (key) | ComfyUI class | fp16 | Line |
|---|---|---|---|
| sd / sd3 / hunyuan3d / stable_audio | SD15, SDXL, SVD, SD3, Hunyuan3Dv2, StableAudio, etc. | yes | base:47 |
| flux | Flux / FluxSchnell (Ovis is also read as Flux: model_detection.py:363) | yes | 754 |
| flux_fill | FluxInpaint (in_channels 96 = Fill, OneReward) | no | 784 |
| flux2 / flux3 | Flux2 / Flux3 | yes / no | 813 / 856 |
| chroma / longcat / krea2 / anima / cosmos | Chroma / LongCatImage / Krea2 / Anima / Cosmos | yes | 1821 / 2462 / 2024 / 1172 / 1101 |
| lens / mochi | Lens / GenmoMochi | no | 883 / 925 |
| ltxv | LTXV / LTXAV (LTX-Video, LTX-2.x) | no | 953 |
| minimax_h3 | MiniMaxH3 (also FastVideo FastH3) | no | 1001 |
| hunyuan_video / hunyuan_image21 | HunyuanVideo / HunyuanImage21 | no | 1028 / 2232 |
| hunyuan_video15 | HunyuanVideo15 | yes | 2270 |
| lumina2 / zimage / ming_image / pixeldit | Lumina2 (NewBie: model_detection.py:611) / ZImage / MingImage / PixelDiTT2I | no | 1214 / 1241 / 1241 / 1299 |
| wan | WAN21_* / WAN22_* (HuMo, VACE, S2V, Animate, SCAIL, CausalAR, etc.) | yes | 1367 |
| trellis2 | Trellis2 (also Pixal3D: model_detection.py:125) | no | 1542 |
| triposplat / seedvr2 / sam3 / depth_anything3 / minimax_music3 / cogvideox | same names | yes | 1686 / 1854 / 2541 / 2500 / 2429 / 2605 |
| hidream / hidream_o1 | HiDream / HiDreamO1 | no | 1711 / 1736 |
| ace / ace15 / yue2 | ACEStep / ACEStep15 / YuE2 | no | 1904 / 2379 / 2405 |
| omnigen2 | Omnigen2 / Boogu (with `extended_fp16_support`, float16 is added at the front: 1939) | yes | 1931 |
| ideogram4 / moss_image / mage_flow | Ideogram4 / MOSSImage / MageFlow | no | 1995 / 2053 / 2114 |
| qwen_image / qwen_image21 / joyimage | QwenImage (also FireRed, Marigold v2) / QwenImage21 / JoyImage | no | 2143 / 2173 / 2205 |
| kandinsky5 / ernie | Kandinsky5 / ErnieImage | no | 2323 / 2524 |

Z-Image also uses fp16 when `allow_fp16` is set by the weight values (model_detection.py:624, supported_models.py:1245).
That cannot be known without reading the file, so it is treated as having no fp16 (the safe side), and the reason says so.

VAE (`working_dtypes` in `comfy/sd.py`; "yes" has fp16, "no" does not):

- `kl` (default, 518; 2D KL): no
- `flux2` (the Flux2 VAE; 0.40.0 keeps the default: 515, 693@0.40.0): no
- `ltxv` (784): no
- `ltx_diffusion` (647): no
- `mage` (629): no
- `pixel` (pixel space; 925 to 933, the default): no
- `wan` (881; Wan 2.1, Qwen Image): yes
- `wan22` (865; Wan 2.2, 16× compression): yes
- `qwen21` (853; Qwen Image 2.1, 16× compression): yes
- `oobleck` (742; ACE-Step 1.5, Stable Audio Open): yes
- `yue2` (742, 744 to 747): yes
- `sa3_large` / `sa3_small` (1029; the large and small Stable Audio 3 configurations): yes
- `dav` (542; MiniMax Music 3; float32 on every GPU, so no difference between GPUs): yes
- `hunyuan_video` (828): yes
- `hunyuan_video15` (795): yes
- `hunyuan3d` (906): yes
- `trellis2` (596, 601): yes
- `seedvr2` (612): yes
- `cogvideox` (812): yes
- `minimax_h3` (1053): yes
- `ace` (921): yes
- `fp32` (542, 951, 1003, 1087): fp32 on every GPU

How the family and VAE of each list entry are decided:

- The family comes from `families.family_of`. It matches the main model's file name first, then the ID and template name. The repository name of a component's location is not used, because it mixes in components of other models.
  - An image main model that neither decides is SD when its header element count (`header_params`) matches an SD fingerprint (`families.SD_PARAM_PRINTS`; 0.15.1).
    The fingerprints are element counts from the headers of the official files: SD1.5 all-in-one 1,066,247,386 (v1-5-pruned-emaonly) and UNet 859,520,964,
    SDXL all-in-one 3,468,837,945 (sd_xl_base_1.0) and UNet 2,567,463,684 (2026-11-11). The tolerance is ±20,000
    (it covers the 14,400 of the 5 extra inpainting conv_in channels; other shapes differ by tens of millions or more). Only when every main file matches
- The VAE is decided by the VAE file name (or the family's default if there is none).
- `update` stores them in `ModelEntry.comfy_family` / `comfy_vae` (`--heads-only` also decides them again).

In the bundled list of 0.9.1 (2026-10-21, 329 entries), 278 have a family and 51 are unknown.
The 51 unknown: 22 TTS, 19 helper tools and others that are not built into ComfyUI, 4 Capybara, and 6 from HF (those for which the ComfyUI class could not be confirmed).
Unknown entries do not change the estimate, and the reason mentions it only on GPUs without bf16.

## GPUs other than NVIDIA (0.7.0, #22, #23)

Only when no NVIDIA GPU is usable (`cuda`, confirmed by nvidia-smi), AMD, Intel and Apple GPUs from the list of display adapters are judged with the support table below (`hardware.classify_adapter`).

- **The NVIDIA judgment does not change.** If an NVIDIA GPU is usable, other GPUs are only listed with `--all-gpus`.
- Whether it runs is decided by the support table below (primary sources).
- **The level uses the same rules as NVIDIA GPUs with dedicated VRAM** (0.11.0, #108; no cap). Before 0.11.0 it was capped at Good because no measurement had run on this kind of GPU (#101, #103). Instead, when this kind of GPU has records of running on two or more GPU models, the PC line of the tables shows "✓ verified GPU kind" ([scoring.md](scoring.md) "Verified GPU kinds"). The notes say "The level uses the same rules as NVIDIA GPUs with dedicated VRAM (<vendor>; whether this kind of GPU has run in measurements is shown by the ✓ verified GPU kind mark on the PC line of the tables)" (`hardware.unverified_kind_text`).
- Measurements on the same GPU with the same settings (other users' too; see "Other users' measurements" in [accuracy.md](accuracy.md#other-users-measurements-34-2026-10-21-handling-aligned-with-llmfit-on-2026-10-22)) only raise the level and never lower it ("Measurements that succeeded on the same GPU" below).
- Measurements on NVIDIA GPUs are used only to shift the working memory (`basis_detail: calibrated`); `basis` stays `estimated`.
- Combinations that are not supported are judged as CPU, with a reason (`Adapter.reason`; "Reasons for refusing" below).

### Reasons for refusing

Non-NVIDIA GPUs fall into three groups (0.11.0, #108).

| Treatment | Which | Reason text |
|---|---|---|
| Used (not verified; no mark) | Combinations that run per the support table below; DGX Spark (GB10) too | "The level uses the same rules as NVIDIA GPUs with dedicated VRAM …". Once two or more GPU models have measurements: ✓ verified GPU kind |
| Refused for lack of support evidence (judged as CPU) | Generations AMD does not support yet: gfx1170, gfx1171, gfx1250 (not Release Ready in TheRock). Also anything outside ComfyUI's instructions (RDNA 1 and older, Windows 10, …) | "… is not Release Ready … (Build Passing / Sanity Tested only), so there is no evidence that PyTorch runs, so it is judged as CPU" |
| Judged as CPU because it is not expected to beat the CPU | 2-CU integrated GPUs: Radeon 610M (gfx1036; Raphael, Granite Ridge, Dragon Range, Fire Range, Mendocino) and Radeon 820M (Ryzen AI 5 330) | "… is a small 2-CU integrated GPU for the display; even if it runs, it is not expected to generate faster than the CPU, so it is judged as CPU" |

- Sources for the CU counts: "Graphics Core Count" on AMD's product pages (Radeon 610M of the Ryzen 9 7940HX: 2; Radeon 820M of the Ryzen AI 5 330: 2 (https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-5-330.html , checked 2026-10-28); the 840M on the same die has 4, the 860M 8).
- The 820M cannot be told apart from the 840M and 860M by gfx (gfx1152, 1153), so it is decided by name (`gputables.AMD_DISPLAY_ONLY_NAMES`: "820M" in the name, and the CPU name "Ryzen AI 5 330" when the GPU name is only "AMD Radeon(TM) Graphics"). The newer pci.ids lspci name "Radeon 820M / 840M" (1902) is taken as the 820M (CPU) when the CPU name is the Ryzen AI 5 330 (which only has the 820M); otherwise it does not say which, so it stays on the used side.
- Before 0.11.0 the 820M was used (as RDNA 3.5 gfx1153).

### Support table (primary sources read on 2026-10-21)

| Vendor / part | OS | Verdict | Primary sources |
|---|---|---|---|
| AMD Radeon RDNA 4 (RX 9070 / 9060, gfx1200, 1201) | Windows 11, Linux | **GPU** (`rocm`) | ComfyUI README "AMD GPUs (Windows, ROCm 10.0)" (Comfy-Org/ComfyUI 426f5ee: "supported architectures include RDNA 2, RDNA 3, RDNA 3.5, and RDNA 4", "Use Windows 11"), ROCm/TheRock `SUPPORTED_GPUS.md`, `RELEASES.md` (4639f91, "GFX target lookup table", Release Ready) |
| AMD RDNA 3.5 APUs (Ryzen AI Max = gfx1151, Ryzen AI 9 HX 37x = gfx1150, Ryzen AI 7 350 = gfx1152, Radeon 8065S = gfx1151; names "Radeon 820M / 840M" get gfx1153 (official sources disagree; below)) | Windows 11, Linux | **GPU** (`rocm`, shared memory) | same |
| AMD Radeon RDNA 3 (RX 7900 = gfx1100, 7800 / 7700 = gfx1101, 7600, 7500, 7400, 7300, PRO W7400 = gfx1102), Radeon 780M / 760M / 740M, Ryzen Z1 Extreme / Z1 (gfx1103) | Windows 11, Linux | **GPU** (`rocm`) | same. On Linux, the ROCm build from pytorch.org is also installed (ComfyUI's Linux instructions use `--index-url .../rocm7.2`) |
| AMD Radeon RDNA 2 (RX 6900 to 6800 = gfx1030, 6700 = gfx1031, 6600 = gfx1032, 6500 / 6400 / 6300, Pro W6400 / W6300 = gfx1034), Radeon 680M / 660M (gfx1035), Van Gogh, Sephiroth (Steam Deck and OLED model; Windows name AMD Custom GPU 0405; 8 CU, gfx1033; shared memory) | Windows 11, Linux | **GPU** (`rocm`; no bf16) | same. The ROCm build on pytorch.org covers gfx1030 only (`PYTORCH_ROCM_ARCH` in pytorch/pytorch v2.14.1 `.ci/docker/manywheel/build.sh`), so notes says to use AMD's multi-arch build (TheRock) for the others |
| AMD Instinct (MI100 to MI355X, gfx908, 90a, 942, 950) | Linux | **GPU** (`rocm`) | `PYTORCH_ROCM_ARCH` of the pytorch.org ROCm build, TheRock's Instinct - Linux table |
| AMD 2-CU integrated GPUs (Raphael, Granite Ridge = Ryzen 7000 / 9000 desktop; Dragon Range, Fire Range = 7x40HX / 7x45HX / 9x5xHX; Mendocino; 610M = gfx1036; Radeon 820M = Ryzen AI 5 330 (0.11.0)) | — | CPU | The generation is covered, but these are small GPUs for driving a display and are not expected to beat the CPU for generation (a judgment; no measurement; "Reasons for refusing" above) |
| AMD gfx1170, gfx1171 (RDNA 4 APUs), gfx1250 (CDNA 5) | — | CPU | Not Release Ready in TheRock ("GPU registration tables and the official lists" below) |
| AMD RDNA 1 (RX 5700 / 5500), GCN (Vega, Polaris and older) | — | CPU | ComfyUI's instructions start at RDNA 2 (TheRock lists RDNA 1 as Release Ready too, but we follow ComfyUI) |
| AMD (any) | Windows 10, or version unknown | CPU | ComfyUI's instructions: "Use Windows 11" (unknown goes to the not-used side) |
| AMD | macOS | CPU | Apple's MPS build is for Apple silicon only (below) |
| Intel Arc A series (Alchemist: A770 / A750 / A580 / A380 / A310, Pro A) | Windows 11, Linux | **GPU** (`xpu`) | PyTorch 2.14 "Getting Started on Intel GPU" (https://docs.pytorch.org/docs/2.14/notes/get_start_xpu.html), Client GPUs table ("Windows 11 & Ubuntu 24.04/26.04 & WSL2"); ComfyUI README "Intel GPUs (Windows and Linux)" |
| Intel Arc B series (Battlemage: B580 / B570, Pro B70 / B65 / B60 / B50) | Windows 11, Linux | **GPU** (`xpu`) | same |
| Intel Core Ultra Arc Graphics (Meteor Lake-H, Arrow Lake-H 140T / 130T, Lunar Lake 140V / 130V, Panther Lake B390, etc.) | Windows 11, Linux | **GPU** (`xpu`, shared memory) | same (Panther Lake: Ubuntu 26.04) |
| Intel Data Center GPU Max (Ponte Vecchio) | Linux | **GPU** (`xpu`) | PyTorch 2.14's Intel Data Center GPU table (RHEL 9.2, SLES 15 SP5, Ubuntu Server 22.04). Windows and WSL2 are CPU |
| Intel UHD, Iris Xe, "Intel(R) Graphics" (Arrow Lake-S etc., when the device ID is not a Core Ultra Arc iGPU), HD Graphics | — | CPU | not in the table above. With the device ID of a Core Ultra Arc Graphics (Meteor Lake 7d55, Arrow Lake-H 7d51, Lunar Lake 64a0), "Intel(R) Graphics" is a GPU ("GPU registration tables and the official lists" below) |
| Intel (any) | Windows 10, or version unknown | CPU | the table above is for Windows 11 |
| Apple silicon (M1 to M4 etc.) | macOS 14 or newer | **GPU** (`mps`, unified memory) | Apple "Accelerated PyTorch training on Mac" (https://developer.apple.com/metal/pytorch/: "Mac computer with Apple silicon", "macOS 14.0 or later"); ComfyUI README "Apple Mac silicon" |
| Apple silicon | macOS 13 or older, or version unknown | CPU | same |

Tables that map a GPU name to a generation:

- `gputables.AMD_NAME_RULES` (product names and lspci development names such as Navi 31) and `INTEL_XPU_RULES`.
- An integrated GPU whose name is only "AMD Radeon(TM) Graphics" is decided by the CPU name (`gputables.AMD_CPU_IGPU_RULES`: Ryzen AI Max -> gfx1151, Ryzen 7 8845HS -> gfx1103, and so on). If that does not decide it, it is CPU (generation unknown).
- Dragon Range (Ryzen 7x40HX, 7x45HX) and Fire Range (9x50HX, 9x55HX) are 2-CU gfx1036, so CPU.
- Laptop Radeons with the same number can be different chips: RX 6800M and 6850M XT are gfx1031, RX 6800S is gfx1032, RX 6550M is gfx1034, RX 7700S is gfx1102.

Names added in 0.9.0 (#56; primary sources read on 2026-10-22):

- **Ryzen Z1 Extreme, Z1** (handhelds; the WMI name is "AMD Radeon(TM) Graphics", so they are decided by the CPU name):
  - AMD's product pages (https://www.amd.com/en/products/processors/handhelds/ryzen-z-series/z1-series/z1-extreme.html and https://www.amd.com/en/products/processors/handhelds/ryzen-z-series/z1-series/z1.html) list Graphics Core Count 12 / 4.
  - Z1 Extreme is taken as Phoenix1 (12 CU, same as 780M). Z1 is taken as Phoenix2 with 2 Zen 4 + 4 Zen 4c cores (4 CU, same as 740M). The chip mapping is an estimate; AMD does not state it.
  - Both are gfx1103 (Linux amdkfd `kfd_device.c` maps GC 11.0.1 and 11.0.4 to gfx_target_version 110003).
  - 740M (4 CU) is treated as GPU, so Z1 is GPU too. Only the 2-CU 610M is CPU.
- **Dragon Range 7940HX, 7840HX** (last two digits of the model number are 40):
  - AMD's product page (for example https://www.amd.com/en/products/processors/laptop/ryzen/7000-series/amd-ryzen-9-7940hx.html) lists Radeon 610M, 2 CU.
  - Before, the name table had only 45HX, and the reason was "gfx unknown". The reason now says "a small 2-CU integrated GPU".
- **Desktop RX 6300**:
  - pci.ids "7424 Navi 24 [Radeon RX 6300]" is Navi 24 (gfx1034). TheRock's SUPPORTED_GPUS.md lists gfx1034 as Release Ready.
  - It has 2 GB of VRAM, so almost no model runs (the amount of VRAM decides the verdict).
- **Linux with an old pci.ids**:
  - lspci -nn prints "Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:7551]" (pciutils `lib/names.c`, format_name_pair).
  - The name is kept as "AMD Device 7551" and mapped back to a chip / product name with `hardware.PCI_DEVICE_NAMES` (`gputables.PCI_DEVICE_RULES`). This table copies only the AMD and Intel IDs that matter for judging, from pci.ids 2026.10.22 (pciutils/pciids 86e7077e4b79; rechecked against 2026.10.27 on 2026-10-27).
  - The reason says "taken as ... from the model ID (update pci.ids with update-pciids to get the product name)".
  - An ID not in the table is CPU (for both AMD and Intel), with a pointer to `update-pciids`.
  - A model ID shows only the kind of product, not the individual unit or slot, so it is kept in the name.
- **VRAM of Intel discrete GPUs on Linux**:
  - The xe and i915 drivers have no sysfs file equivalent to amdgpu's `mem_info_vram_total` (read from `drivers/gpu/drm/xe/xe_tile_sysfs.c`, `xe_device_sysfs.c`, `i915_sysfs.c` and Documentation/ABI in torvalds/linux; the amount can be read only through a DRM ioctl or debugfs (root)).
  - So the **nominal** amount in the name table is used, and the reason says "nominal ... GB from the name (the smaller one if versions differ)" (`Adapter.vram_source: "name"`).
  - Nominal values for laptops come from Intel's table (https://www.intel.com/content/www/us/en/support/articles/000091109.html): A770M 16 GB, A730M 12 GB, A550M 8 GB, A370M 4 GB, A350M 4 GB.
  - A770 has 8 GB and 16 GB versions, so 8 GB.
  - A570M, A530M and the Pro M models could not be confirmed in Intel's documents, so they have no value and stay CPU on Linux.

### GPU registration tables and the official lists (0.11.0, #103)

Only what the OS does not tell us needs registering: AMD on Windows (name → gfx), Intel (XPU coverage), NVIDIA with an old driver (name → generation; a miss does not refuse), and the PyTorch CUDA build table (per version). NVIDIA (nvidia-smi returns the generation), AMD on Linux (amdkfd) and Apple (the chip-name pattern) need no registration.

- The tables live in `src/genmodelfit/gputables.py`; each row is a `Rule` (key, value, source, date verified, version added, note). Source URLs and read dates are written once in `SOURCES`.
- Every row, the official product names it matches, the verdict and the source are listed in [gpu-inventory.md](gpu-inventory.md) (made by `tools/gpu_inventory.py`; a test checks it matches).
- The official lists (fetched 2026-10-27) are fixed under `tests/fixtures/gpu_official`, and a test checks that every official product matches a row with the same gfx / compute capability (`tests/test_gpu_tables.py`; no network).
- New GPUs are reported by the "GPU table watch" in the weekly update ([release.md](release.md), "Weekly list update"). The tables are never rewritten automatically.

**AMD on Windows** (both sources are given):

- AMD's official Windows compatibility tables (ROCm 7.2.1; Radeon: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html , Ryzen: …/compatibilityryz/windows/windows_compatibility.html) list only gfx1100, 1101, 1200, 1201 (Radeon) and gfx1150, 1151 (Ryzen).
- genmodelfit follows ComfyUI's instructions (RDNA 2 to 4) and **Release Ready** in the Windows table of TheRock's `SUPPORTED_GPUS.md` (which also includes RDNA 2, gfx1102, gfx1103, gfx1152 and gfx1153). ComfyUI's AMD portable and AMD's multi-arch PyTorch are TheRock builds, so we follow TheRock. A gfx missing from AMD's official table is outside AMD's official guarantee.
- gfx that are not Release Ready in TheRock (gfx1170, gfx1171: Build Passing only; gfx1250: up to Sanity Tested) are judged as CPU (`gputables.AMD_UNVERIFIED`). TheRock says that until Sanity Tested, packages may install but not run. When they become Release Ready, the watch reports it and they are taken off the list.

**Gaps and errors fixed in #103** (sources are on each row of the inventory):

- AMD: Radeon 8065S (Ryzen AI Max+ PRO 495, gfx1151 in ROCm hw specs), RX 7500 / 7400 / 7300 and PRO W7400 (pci.ids 749f, 7499 "Navi 33" = gfx1102), Steam Machine (pci.ids 7481 "Navi 33"), Radeon Pro W6900X and Pro V520 (gfx1030, gfx1011), Instinct MI25, MI6, MI8 (unsupported generations; for display), CPU names of Ryzen AI 4xx and PRO (when the iGPU name is generic), Ryzen 9 270 (gfx1103 in TheRock's RELEASES.md).
- Device IDs whose product name becomes generic in a newer pci.ids ("Navi 33 [Radeon Graphics]", "Battlemage G31 [Intel Graphics]") used to work with an old pci.ids and be refused with a new one. Without amdkfd, the device ID table is now looked up first for generic names too (the reason says "The product name is generic, so it was taken as … from the device ID").
- Intel: Arc Pro B65 / B70 (Battlemage G31, e222, e223; the nominal VRAM could not be confirmed, so none is stored), Data Center GPU Max (Ponte Vecchio; Linux only in PyTorch's table, so Windows and WSL2 are CPU).
- **"Intel(R) Graphics" on Windows and Core Ultra Arc**: with single-channel memory, Intel shows the same silicon's Arc Graphics as "Intel Graphics" in Device Manager (Intel support 000102144, "Intel Arc Graphics Appears As Intel Graphics in Device Manager and Task Manager", https://www.intel.com/content/www/us/en/support/articles/000102144/graphics.html ; the fix is dual-channel memory; read 2026-10-27). The rule that looks up the device ID table for generic names also applies to Windows WMI, so with the device ID of Meteor Lake 7d55, Arrow Lake-H 7d51 or Lunar Lake 64a0, "Intel(R) Graphics" is used as XPU (Linux already used the device ID). The reason says single-channel memory is slower than dual-channel. "Intel(R) Graphics" with a device ID not in the table (Arrow Lake-S etc.) stays CPU.
- **gfx of the 820M**: TheRock's RELEASES.md says gfx1153, ROCm hw specs says gfx1152 for the Radeon 820M of the Ryzen AI 5 330, and pci.ids puts the 820M and 840M on another die (1902 "Krackan2 [Radeon 820M / 840M]"). The name alone does not decide it, so we follow TheRock (which picks the ROCm wheel) with gfx1153 and add a note about the disagreement. Both are RDNA 3.5. On Linux the amdkfd value comes first. Since 0.11.0 (#108), 820M-only names (and "Radeon 820M / 840M" with a Ryzen AI 5 330 CPU) are judged as CPU as a 2-CU GPU ("Reasons for refusing" above).
- **Jetson Orin (8.7) and CUDA 13.2 or later**: PyTorch v2.14.1 says 8.0 / 8.6 code runs on 8.7 in builds for CUDA 13.2 or later (`DEVICE_REQUIREMENT_POST_JETSON_SBSA_UNIFICATION`). genmodelfit does not judge Jetson (JetPack-only builds, unified memory; "Every environment" below), so the table (`SOLO_CC`) is unchanged.
- Version range of cu129: Windows wheels exist only for 2.8 and 2.9 (2.10 and later are Linux only). cu128 for aarch64 is 2.7 and 2.9 to 2.11 (no 2.8). Only the guidance text changes, not the verdict.
- With an old driver and an unknown generation (no compute_cap and not in the name table), the reason used to promise "it runs on the GPU after a driver update". Kepler and Fermi have neither a new driver nor PyTorch, so it now says "on Maxwell or newer it runs after a driver update; Kepler, Fermi and older do not".

### Precision (`comfy/model_management.py` of ComfyUI 426f5ee)

- AMD (`torch.version.hip`):
  - `should_use_fp16` is always True.
  - `should_use_bf16` is False on RDNA 2 and older (`AMD_RDNA2_AND_OLDER_ARCH`). But with `manual_cast=True` (when choosing the computation precision for families without fp16) it returns True.
  - So on RDNA 2, working memory is not doubled even for families without fp16 (the VAE is fp32 and doubles).
- Intel (XPU): `should_use_fp16` is `has_fp16`, `should_use_bf16` is `torch.xpu.is_bf16_supported()`. The supported Arc GPUs have both.
- Apple (MPS): `should_use_fp16` is True, `should_use_bf16` is True on macOS 14 or newer.
- fp8 compute (`supports_fp8_compute`) is treated as absent on all of them (fp8 weights only get a note).

### Shared memory (integrated GPUs, APUs, Apple silicon)

- **Apple silicon (`memory: unified`)**:
  - On MPS, ComfyUI counts all the installed memory as GPU memory (`get_total_memory` is `virtual_memory_total` for both CPU and MPS, and `vram_state` is SHARED: model_management.py). It does not use the behavior of moving components to main memory (lowvram); with SHARED it does not compute `lowvram_model_memory`.
  - macOS makes the amount the GPU can use without slowing down (Metal `recommendedMaxWorkingSetSize`, the value llmfit bffd1b6 uses) smaller than the installed memory.
  - So **3/4** of the installed memory (`hardware.UNIFIED_MEMORY_SHARE`; a value chosen by judgment, not measured) is taken as GPU memory.
  - The amount placed on the GPU plus the fixed part (Python and ComfyUI, 3 GB) is also compared with the installed memory (above it, virtual memory makes it slow = Marginal).
- **Integrated GPUs and APUs (`memory: shared`)**:
  - Usable amount = VRAM carved out in the BIOS (Windows: registry qwMemorySize; Linux: sysfs `mem_info_vram_total`) + the part of main memory the GPU can use.
  - On Linux, amdgpu reads sysfs `mem_info_gtt_total` (GTT).
  - If that cannot be read, **1/2** of the visible main memory is used (`hardware.SHARED_MEMORY_SHARE`).
  - Basis (Linux): the limit of pages TTM lends to the GPU is "about 50% of total system memory" (torvalds/linux a804c57 `drivers/gpu/drm/ttm/ttm_device.c:88 to 92`; the amdgpu `gttsize` default is "-1 = Use value specified by TTM": https://docs.kernel.org/gpu/amdgpu/module-parameters.html).
  - Basis (Windows): shared GPU memory is also about half of physical memory (Task Manager's "Shared GPU memory"; WDDM explanation: https://learn.microsoft.com/ja-jp/archive/blogs/pigscanfly/graphics-memory-reporting-in-vista; an old document, so a rough guide).
- In both cases the place to offload to is the same memory, so `GPU+RAM` (offload) is not used (either everything on the GPU, or components swapped in one after another).
- Main memory check: the part placed on the GPU also uses main memory.
  - Required = the amount when nothing is placed on the GPU (fixed part + the largest stage's components) + the part of the GPU amount that exceeds the BIOS-carved VRAM (`Gpu.carveout_gb`, which is not in the installed memory).
  - It is checked for "everything on the GPU" and for "swap components in one after another", and the better one is taken.
  - Example: Radeon 780M with 16 GB of memory: a model needing 8.3 GB on the GPU + 9.8 GB of main memory is not enough.
  - If there is a GPU with dedicated VRAM, it is used before the integrated GPU.
- Adding main memory only increases what the GPU can use, so the level never drops (`tests/test_vendors.py`).

### Detection

- By default, detection runs only when both of these hold:
  - there is no usable NVIDIA GPU (not found, Kepler, or an old driver);
  - `--vram` is not overriding.
- This also finds an NVIDIA GPU that has no NVIDIA driver installed.
- It does not run when an NVIDIA GPU is present (starting PowerShell takes about 1 second).
- `genmodelfit system --all-gpus` always runs it (even then, if NVIDIA is usable, NVIDIA is used for judging).
- The registry also keeps entries of removed GPUs (they are dropped in the matching step below).
- Windows: `powershell.exe -NoProfile -NonInteractive -Command` gets Name, AdapterRAM, PNPDeviceID, ConfigManagerErrorCode and DriverVersion of `Get-CimInstance Win32_VideoController` as JSON (output set to UTF-8; time limit 8 seconds).
  - Entries whose PNPDeviceID does not start with `PCI\` (virtual displays, Remote Desktop displays) are dropped.
  - The OS version is read from the build number (22000 or higher is Windows 11).
  - `AdapterRAM` is a uint32 (Microsoft's Win32_VideoController description: https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-videocontroller), so it tops out near 4293918720 for 4 GiB and more. Values at or above that are not used.
  - VRAM comes from `HardwareInformation.qwMemorySize` (REG_QWORD; 8589934592 for an 8 GB GPU) in registry `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\00xx`.
    - Microsoft's document (https://learn.microsoft.com/en-us/windows-hardware/drivers/display/registering-hardware-information) has only `HardwareInformation.MemorySize` (ULONG; documented as MB, but real drivers write bytes, which is the same case split as llmfit). qwMemorySize is a driver convention.
    - Values under 64 MiB are taken as a unit mismatch and not used.
  - Matching registry entries:
    - An entry whose `MatchingDeviceId` matches the start of the WMI adapter's model ID.
    - Otherwise, an entry whose `DriverDesc` name is the same (compared without the trademark symbols).
    - If several entries match and their values differ (a removed GPU with the same name remains), none is used.
  - If there is an NVIDIA adapter (VEN_10DE) and nvidia-smi cannot judge it, it reports that the NVIDIA driver is not installed or not running, whatever the name (0.11.0; it used to check only the English name "Microsoft Basic Display Adapter").
  - If ConfigManagerErrorCode is not 0, codes 22, 28 and 43 get their own reasons and others "Device Manager shows a problem" ("Every environment" below).
- Linux: reads class 03xx (VGA, 3D, Display) from `lspci -Dnn`.
  - The name is the product name inside the brackets (inside the brackets of `Navi 31 [Radeon RX 7900 XT/7900 XTX/...]`).
  - For AMD it reads `/sys/bus/pci/devices/<slot>/mem_info_vram_total` (VRAM) and `mem_info_gtt_total` (GTT).
  - Detection does not use rocm-smi (its VRAM and GTT are the same sysfs values, and many PCs do not have it). The bench uses it to check utilization (see "Bench on non-NVIDIA GPUs" in [accuracy.md](accuracy.md#bench-on-non-nvidia-gpus-090)).
  - Intel discrete GPUs have no VRAM amount in sysfs, so the nominal amount from the name table is used (above).
  - A part with an unknown model gets the name "AMD Device 7551".
- macOS: `sppci_model` ("Apple M2 Pro") and `spdisplays_vendor` of `system_profiler SPDisplaysDataType -json`.
  - The unified memory amount is psutil's installed memory (the same value as `sysctl hw.memsize` on macOS).
  - The OS version is `platform.mac_ver()`.
- If PowerShell, lspci or system_profiler is missing, fails or times out, the list stays empty and a note says the display adapter list could not be read (0.11.0; on Linux without lspci, sysfs is read).

### Display

- First line of the table:
  - With a usable GPU: "PC: AMD Radeon RX 7900 XTX 24.0 GiB VRAM (ROCm, estimated), RAM ...".
  - Apple: "PC: Apple M2 Pro (unified memory; 24.0 GiB for the GPU, MPS, estimated), RAM ...".
  - Integrated GPU: "(integrated; 16.1 GiB shared with RAM, XPU, estimated)".
  - With no usable GPU, it shows the GPU it found and how it is treated ("PC: Intel(R) UHD Graphics 770 (integrated; not usable for generation → judged as CPU), RAM 15.7 GiB ..."). The next line is "This PC has no usable GPU: only models that run on CPU are shown (N tools hidden: --include-tools)".
- Mode column: among models that do not run, those that fail because there is no usable GPU show `GPU req`, and those that fail for lack of main memory show `RAM` (the JSON `run_mode_label` stays `-`).

### Cross-check with other tools (2026-10-21)

The table was compared with other tools' support tables, to see if it is too strict or too loose.
The standard is "does it run with ComfyUI + PyTorch", so it follows ComfyUI's README.

- **Windows 10**: no source says the GPU can be used.
  - AMD HIP SDK, ROCm on Radeon/Ryzen and PyTorch XPU are Windows 11 only. Ollama, LM Studio and llmfit say nothing.
  - If the version is known to be Windows 10, the first line of the table shows `needs Windows 11 for generation` (`Adapter.needs`).
- **DirectML, ZLUDA and Vulkan are not used**:
  - ComfyUI warns that DirectML "barely works, is very slow, has not been updated in over 1 year" (comfy/model_management.py) and removed it from the README.
  - Microsoft also put DirectML in maintenance mode (https://github.com/microsoft/DirectML).
  - SD.Next removed DirectML and called ZLUDA "unofficial and currently limited" (https://vladmandic.github.io/sdnext-docs/ZLUDA/).
  - Fooocus and stable-diffusion-webui-amdgpu run old AMD GPUs with DirectML, but slowly (about 3 times slower according to Fooocus's README).
- **Wider than AMD's official Windows PyTorch table**:
  - The Windows table of ROCm on Radeon/Ryzen has only gfx1200/1201/1100/1101 and gfx1150/1151 (https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html).
  - ComfyUI's README says RDNA 2 to 4.
  - TheRock (the ROCm ComfyUI uses) also covers RDNA 2 and 780M (gfx1103) for releases on Windows (https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md).
  - RX 6000 series and 780M are used to match ComfyUI (same level rules; no measurement has run on this kind of GPU yet).
- **RDNA 1, Vega, Polaris**: llmfit treats them as GPU if the name and VRAM can be read, and Ollama / llama.cpp can run them via Vulkan. But they are outside ComfyUI's README, so CPU.
- **2-CU integrated GPUs (gfx1036)**: TheRock covers them, but they are not expected to beat the CPU for generation, so CPU (a performance judgment, not a support judgment; the reason text says so).
- **Van Gogh (Steam Deck, gfx1033)**:
  - Up to 0.8.0 it was CPU, grouped with gfx1036 as a "small 2-CU integrated GPU".
  - Valve's specification (https://www.steamdeck.com/en/tech, "GPU: 8 RDNA 2 CUs, 1.6GHz (1.6 TFlops FP32)") says 8 CU.
  - TheRock's SUPPORTED_GPUS.md (main, read on 2026-10-22) lists gfx1033 as Release Ready on both Linux and Windows. ComfyUI's README says "supported architectures include RDNA 2".
  - So it is now GPU (shared memory), like 680M (12 CU) and 660M (6 CU).
  - It is not in the pytorch.org ROCm build, so notes says to use the TheRock build.

## Families that run on CPU (0.7.0, #39)

When there is no GPU (or the model does not fit on the GPU), these are taken as running on CPU:

- The existing rule: image, audio and TTS models with weights of 2 GB or less.
- In addition, the families in `families.CPU_FAMILIES` (table below).

| Family | Type | Basis | Excluded |
|---|---|---|---|
| `sd` (SD 1.5, SD 2.x, SDXL, SDXL Turbo, etc.) | image | A built-in ComfyUI family that uses no CUDA-only components (an estimate; no measurement on CPU) | SUPIR (its own nodes; not checked) |
| `stable_audio` (Stable Audio Open 1.0, Stable Audio 3 Small) | music, sfx | The built-in StableAudio family (an estimate; no measurement on CPU. AI生成キット's notes list "sound effects (GPU/CPU)", but there is no record of the model or time) | Stable Audio 3 Medium (not checked) |

How ComfyUI behaves on CPU:

- Without a GPU, ComfyUI runs on CPU (`--cpu`, "To use the CPU for everything (slow)": comfy/cli_args.py).
- On CPU it holds the main model and the VAE in fp32 (`should_use_fp16` and `should_use_bf16` are False on CPU: model_management.py:1912, 1979. The text encoder stays fp16: 1261).

Required main memory = fixed part + all components + (one more copy of the main model if it is 2 bytes) + one more copy of the VAE + working memory x 2.

- If it fits in the installed memory: the band of system RAM use (60 / 85 / 98%), with generation capped at Marginal (CPU, slow) and speech built for real-time CPU use and one-pass tools at Good (0.11.0, #108; [scoring.md](scoring.md) "Running on the CPU").
- If it fits within virtual memory: Marginal (slow).
- If it exceeds that: Too tight.
- Running on CPU is itself an estimate, so the reason says "CPU is an estimate, no measurement" (`cpu_ok` in AI生成キット's catalog is a design marker, not a measurement).

## Measurements that succeeded on the same GPU (0.7.0, #26)

The level is decided by measurements (`Estimate.fit_from: measurement`) when all of these hold (the conditions changed in 0.11.0; the full rules are in [scoring.md](scoring.md) "When measurements set the level").

- There is a measurement that succeeded (`outcome: ok`) at the same settings with the standard workflow.
- Its GPU is **the same GPU** as this PC's (`estimate.same_gpu`: the model ID or the normalized name, and a VRAM ratio of 0.95 or more). The same rule for the author's and other users' measurements (up to 0.10 the author's counted on any same-vendor GPU with at least the measured VRAM).
- There are two or more independent sources (the author's rows are one together, however many; other users' rows count their `n_sources`) and they agree (no OOM record, spread within 15% of the median).
- The amount used for the level = max(largest peak + context, weights + fixed part) fits in this PC's VRAM.

How the level is decided:

- By the band of the amount used for the level / this PC's VRAM (60 / 85 / 98%). At least Good.
- If a measurement is stuck at the upper limit (dynamic VRAM used up all the free memory), it is not a basis for Perfect (Good at most).
- A `torch_reserved` measurement (bench; it does not include the CUDA / HIP context) sets the band with 0.5 GiB for the context added (`estimate.TORCH_RESERVED_CONTEXT_GB`), and it is also a basis for Perfect (0.9.0). Before, it was not, so a non-NVIDIA GPU with only bench submissions stopped at Good even with a measurement.

How the vendor of the measured GPU is decided:

- By the measurement's `gpu_backend` if there is one.
- Only if there is none, by the GPU name (Apple's MPS has the GPU name "mps").

How measurements by other users (#34) are used:

- Only those of the same GPU as this PC (the model ID, or the normalized name; generic names are excluded; `estimate.same_gpu`). Decided on 2026-10-22. It is not widened to similar GPUs. From 0.9.0, the required amount also uses only the same GPU.
- In addition, only when the ratio of the measured GPU's VRAM to this PC's total (smaller / larger) is 0.95 or more (0.9.1; 0.95 instead of 0.97 since 0.11.0, because Linux CUDA reports a 6 GB card as 5.80 GiB, 0.967 of Windows' 6.0; `estimate.same_vram`). The same name can come with different VRAM (such as 8 / 16 GB for the RTX 4060 Ti). Integrated GPUs, APUs and Apple are not compared.

Other points:

- They are not used on GPUs where the precision adjustment doubled the working memory.
- The main memory check is done as usual.
- The reason starts with "N independent sources ran to the end on the same GPU as this PC (...) with the same settings and agree ... (level from the measurements)", and the VRAM in the table is the amount used for the level (m).
- If the measurement is from another GPU, has only one source or does not agree, the reason says "the level comes from the estimate (...)".
- Example: Wan 2.2 5B (two author rows on an RTX 5080 16 GB: 15.8 / 15.0 GB; one source together, and a record of running by offloading part of the weights with dynamic VRAM) does not fit with weights + fixed part (about 18.4 GB) in 15.9 GB even at 640x640 with 73 frames, so from 0.11.0 the level comes from the estimate (with the current per-stage formula Good / GPU~, swapping parts; Good / GPU from 0.7.0 to 0.10). On an RTX 3060 12 GB it is Good / offload.
- Measurements are used for the level only when they are better than the estimate (never lower it). The same for non-NVIDIA GPUs (0.11.0 removed the branch that lowered Perfect to Good from measurements).

## Every environment (0.11.0, #101)

Detection finishes without raising in every environment, does not refuse when something is unknown, and writes what is unknown in the notes.
41 environments (reproduced by substituting the output of external commands, the registry, WMI, lspci, sysfs and system_profiler) were listed and fixed.
The sources were read on 2026-10-27.

### List of environments

| Environment | What genmodelfit does | Source (section quoted) |
|---|---|---|
| psutil cannot read the memory | Reads it with `GlobalMemoryStatusEx` on Windows, `/proc/meminfo` on Linux and `sysctl -n hw.memsize` on macOS. If none works, the amount is "unknown" (`Memory.total` is None; `ram_total_gb` in the output is 0 = unknown) and the notes say "give it with --ram". The estimate treats 0 as unknown and does not refuse by main memory. Integrated GPUs, APUs and Apple, whose share of main memory is then unknown, are judged as CPU with a reason unless a reserved VRAM or GTT can be read | psutil fails differently on each OS (implementation decision) |
| Old kernel without MemAvailable | Available = MemFree + Cached | Linux `Documentation/filesystems/proc.rst` "MemAvailable" |
| Containers (cgroup v2 and v1) | From this process's cgroup in `/proc/self/cgroup` (v2 `0::/path`, v1 `N:memory:/path`), walks up to the root and uses the smallest limit (`memory.max`, `memory.limit_in_bytes`) as the installed amount; available = limit − usage (`memory.current`, `memory.usage_in_bytes`). `max` means no limit. Inside a namespace the path is `/`, so only the root is read | https://docs.kernel.org/admin-guide/cgroup-v2.html "memory.max", "memory.current"; https://docs.kernel.org/admin-guide/cgroup-v1/memory.html "memory.limit_in_bytes" |
| A compute capability newer than the PyTorch table (13.0 etc.) | Not refused (stays `cuda`), with the note "newer than the table; use the latest PyTorch". Only generations older than the table are refused | Same as the rule "do not refuse when the generation is unknown" |
| Linux on aarch64 / ppc64le / riscv64 etc. | `_machine()` no longer turns unknown kinds into x86_64. If not in the table (keys of `TORCH_CUDA_BUILDS`), "no CUDA build of PyTorch for this machine" and CPU | "Compute capabilities supported by PyTorch CUDA builds" above |
| Windows on Arm64 + NVIDIA | The table is looked up by (OS, machine) (`torch_platform`). There is no CUDA build for Windows on Arm64, so CPU | The cu indexes of https://download.pytorch.org/whl/ (no `win_arm64` CUDA build) |
| 32-bit Python | A note only ("PyTorch exists only for 64-bit Python") | The cu indexes of https://download.pytorch.org/whl/ (no 32-bit win32 / i686 wheels) |
| MIG enabled (`mig.mode.current` is Enabled) | nvidia-smi reports the whole GPU, so the note says "give the partition size with --vram" (the value is not changed; not verified on real hardware) | NVIDIA "Multi-Instance GPU User Guide" https://docs.nvidia.com/datacenter/tesla/mig-user-guide/ (memory is split per partition) |
| nvidia-smi that does not know `mig.mode.current` | Only when the error names that field, asks again without it (`QUERY_CC`). If compute_cap is unknown too, `QUERY_OLD` as before | One unknown field makes the whole query fail (checked on this PC with a nonexistent field) |
| DGX Spark (GB10) | See "DGX Spark (GB10)" below | Same section |
| Jetson (Orin, Thor) | Out of scope (CPU), with the note "Jetson uses JetPack-only PyTorch builds (jp6 cu126 etc.), separate from the CUDA build table here, and its memory handling is unverified". Outside this issue | Jetson AI Lab's ComfyUI guide https://www.jetson-ai-lab.com/tutorial_comfyui_flux.html (to be handled in another issue) |
| GH200 / GB200 | Outside this issue (judged by the HBM amount nvidia-smi reports, as before) | — |
| NVIDIA without a driver (Windows "Microsoft Basic Display Adapter", Linux nouveau) | Not by the name (which changes with the language) but by the NVIDIA vendor ID (`10de`): "cannot be checked with nvidia-smi, so CPU; install the NVIDIA driver" | — |
| Device Manager errors (WMI ConfigManagerErrorCode) | 22 "is disabled", 28 "has no driver installed", 43 "was stopped by Windows because it reported problems", others "has a problem". AMD and Intel adapters with 22, 28 or 43 are not used for that reason (also when read from WSL2; `Adapter.config_error`) | Microsoft "Device Manager error messages" https://learn.microsoft.com/en-us/windows-hardware/drivers/install/device-manager-error-messages (Code 22, 28, 43) |
| PowerShell, lspci or system_profiler missing, failing or timing out | A note "the display adapter list could not be read" (it used to be silent) | — |
| Linux without lspci (minimal containers, Flatpak, Snap etc.) | Reads `/sys/bus/pci/devices/*/{class,vendor,device}` (class 0x03xxxx), names the device "AMD Device 744c" and looks it up in the device ID table (`PCI_DEVICE_NAMES`). The slot is only used to read (after checking its form) and is never kept or printed. Unreadable files (SELinux etc.) are None | https://docs.kernel.org/PCI/sysfs-pci.html "Accessing PCI device resources through sysfs" |
| Virtual machine and management (BMC) displays (QEMU `1234`, virtio `1af4`, VMware `15ad`, VirtualBox `80ee`, Hyper-V `1414`, Cirrus `1013`, QEMU (Red Hat) `1b36`, ASPEED `1a03`, Matrox `102b`) | "Not a GPU for computing (a virtual machine or management display)" and CPU. Reading the device ID (the old pci.ids hint) is for AMD and Intel only | Vendor names in pci.ids (pciutils/pciids) |
| AMD on Linux (amdgpu) | Matches `gfx_target_version` in amdkfd's `/sys/class/kfd/kfd/topology/nodes/*/properties` (decimal major×10000 + minor×100 + stepping, stepping one hex digit: 110000 → gfx1100, 90010 → gfx90a; 0 is a CPU node) to the adapter by the node's `vendor_id` and `device_id`, and uses it before the name. A gfx10.3-or-later value not in the table is "a newer generation (estimate)" and is not refused | torvalds/linux `drivers/gpu/drm/amd/amdkfd/kfd_topology.c` (`gfx_target_version`), `kfd_device.c` |
| New AMD or Intel (Arc) on Windows not in the name table | Still CPU, with the reason "not in the name table (a new GPU?); give the VRAM with --vram to judge; the name table is extended by the weekly update" | — |
| Non-Apple GPUs on macOS (AMD and Intel in Intel Macs) | Before looking up the generation: "GPUs other than Apple on macOS are not covered by the MPS build of PyTorch" | Apple "Accelerated PyTorch training on Mac" https://developer.apple.com/metal/pytorch/ ("Mac computer with Apple silicon", "macOS 14.0 or later"), ComfyUI README "Apple Mac silicon" |
| macOS virtual machine ("Apple Paravirtual device") | An Apple display whose name is not an Apple chip name (`APPLE_CHIP_NAME`) is CPU | PyTorch #111576 "[MPS] Skip virtualized devices" https://github.com/pytorch/pytorch/pull/111576 (the first allocation fails on this device) |
| macOS reported as 10.16 (Python built with an old SDK) | Reads again with `SYSTEM_VERSION_COMPAT=0 sw_vers -productVersion`. If that fails, the version is unknown (MPS is not used) | pypa/packaging #513 https://github.com/pypa/packaging/pull/513 (reads sw_vers with "SYSTEM_VERSION_COMPAT=0") |
| AMD and Intel in WSL2 | See "AMD and Intel in WSL2" below | Same section |
| Commands that time out | On POSIX the whole process group (`start_new_session`) is killed, then it waits 1 more second and returns. Windows: see "Known limits" below | Python `subprocess.Popen` "start_new_session" |

### DGX Spark (GB10)

- Conditions for using it (all of them): the nvidia-smi name is **exactly** `NVIDIA GB10` (no partial match, no GB20, no nvgpu), memory.total is `[N/A]` / `Not Supported`, and Linux on aarch64. Compute capability 12.1 supports this but is not required. GB10 machines by ASUS (GX10), Dell, MSI and others have the same name.
- Memory (`memory: shared`):
  - The total is MemTotal in `/proc/meminfo` (the rule is that the level comes from the total). It is not discounted (Apple's 3/4 stands in for Metal's guide value and has no basis for GB10). It is shared with main memory and not counted twice (`carveout_gb` 0; there is no offload mode).
  - Free is MemAvailable + SwapFree as in NVIDIA's known-issues example (when HugePages are used, free HugePages × size and no swap), used only for the current-free notice (`vram_free_short`).
- The level uses the same rules as NVIDIA GPUs with dedicated VRAM (0.11.0, #108; no cap; Perfect is not a speed guarantee, and the low-bandwidth DGX Spark is judged by use as well). The bundled list has no measurement of this kind (`nvidia/shared`), so there is no ✓ verified GPU kind. The reason says "GB10 has unified memory; NVIDIA documents no GPU-only limit".
- PyTorch table: the current `_code_runs` (machine code for the same major with a lower minor runs) already judges that sm_120 code runs on compute capability 12.1 (no new rule needed). cu128 for aarch64 (PyTorch 2.7–2.11) was added to the table.
- Note: "CUTLASS / xformers fmha kernels were reported to fail on sm_121 (GB10); whether this is fixed is unverified".
- An NVIDIA GPU that reports no VRAM and is not named GB10 is CPU, with the reason (Jetson: the table above; others: "of the NVIDIA GPUs with shared memory, only GB10, which has an official ComfyUI guide, is used").

Sources:

- NVIDIA "ComfyUI on DGX Spark" https://github.com/NVIDIA/dgx-spark-playbooks (`nvidia/comfy-ui`; cu130 PyTorch, ComfyUI v0.28.2)
- PyTorch forum (NVIDIA's ptrblck) https://discuss.pytorch.org/t/223744 "ARM binaries for CUDA 12.8 and later support DGX Spark; sm_121 is binary compatible with sm_120"
- NVIDIA DGX Spark "Known Issues" https://docs.nvidia.com/dgx/dgx-spark/known-issues.html "nvidia-smi reports "Memory-Usage: Not Supported"" ("iGPUs do not have dedicated framebuffer memory") and "Guidance for reporting memory resources with unified memory architecture" ("the cudaMemGetInfo API does not account for memory that could potentially be reclaimed from SWAP"; the example reads MemAvailable, SwapFree and HugePages_Total)
- cu128 for aarch64: v2.11.0 `.ci/manywheel/build_cuda.sh` (12.8 is `7.5;8.0;8.6;9.0;10.0;12.0`; aarch64 drops below 8.0 and 8.6), https://download.pytorch.org/whl/cu128/torch/ (`manylinux_2_28_aarch64` for 2.7.0–2.11.0)
- ComfyUI treats an integrated GPU as a normal CUDA device and only disables pinning (`is_integrated_gpu` in `comfy/model_management.py`)

### AMD and Intel in WSL2

Three steps (NVIDIA in WSL is judged with nvidia-smi as before):

1. **Is it WSL2**: `/dev/dxg` (the GPU paravirtualization device) exists and the kernel release contains `microsoft`. Then interop is checked by the presence of `/proc/sys/fs/binfmt_misc/WSLInterop` (or `WSLInterop-late`) without starting a process. If `/.dockerenv` or `/run/.containerenv` exists: "a container on WSL; the Windows-side GPUs cannot be read".
2. **Read the Windows-side GPUs**: through interop, `powershell.exe` (if not on PATH, `c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` under `[automount] root` of `/etc/wsl.conf`, default `/mnt/`) is run with `-NoProfile -NonInteractive` and an 8-second timeout. The script (`hardware._PS_WSL`) reads only the WMI Name, AdapterRAM, PNPDeviceID, ConfigManagerErrorCode and DriverVersion, and DriverDesc, MatchingDeviceId, `HardwareInformation.qwMemorySize` and `MemorySize` (REG_BINARY converted from bytes) of the display class keys (`0###`) in the registry, and returns UTF-8 JSON. As on Windows, the second half of PNPDeviceID (the slot) is dropped. Failures (interop disabled, timeout) do not raise; they become notes.
3. **Is WSL ready**: AMD needs `rocminfo` (PATH or `/opt/rocm/bin`) to run and show `Name: gfx…` for a GPU agent (the strongest evidence). Only `Name: gfx…`, `Marketing Name:` and `Device Type:` matched at the start of a line are read; other lines such as the UUID are never read or put in notes, logs or exceptions. Intel needs an Intel GPU device in `clinfo -l`.

Conditions for using it (same level rules as NVIDIA; no measurement has run on this kind of GPU yet):

- AMD: an exact product name in AMD's WSL support table (ROCm 7.2.1) (`hardware.WSL_AMD_SUPPORTED`: RX 9070 / 9070 XT / 9070 GRE, AI PRO R9700 / R9600D, RX 9060 / 9060 XT, RX 7900 XTX / XT / GRE, PRO W7900 (Dual Slot) / W7800 (48GB) / W7700, RX 7800 XT, RX 7700 / 7700 XT). APUs are not in the official table (only in the librocdxg README, and that repository is deprecated), so they are not used.
- Intel: Arc A and B, Arc Graphics of Core Ultra (`gputables.INTEL_XPU_RULES`), with a Windows driver of 32.0.101.8801 or later (compared as the 7-digit number made of the third and fourth fields: `int(f"{p3}{p4:04d}") >= 1018801`). If the version cannot be read, it is not refused (a note). For integrated Arc Graphics, half of the memory inside WSL (at most the Windows amount) is taken as the shared part (a low estimate).
- Otherwise it is CPU, with what is missing (e.g. "AMD Radeon RX 7900 XTX is on the Windows side, but rocminfo inside WSL does not see the GPU. Install AMD Software: Adrenalin Edition for WSL2 on Windows and the WSL build of ROCm inside WSL", "not in AMD's WSL support table (ROCm 7.2.1)").
- VRAM is the Windows-side registry amount (WMI AdapterRAM stops at 4 GB). A (secondary) report says about 3 GB less is free inside WSL, so this is a note (the value is not changed).
- The Microsoft device seen by lspci and sysfs in WSL2 (`1414:008e` Basic Render Driver) is not a compute GPU and is dropped from the list.
- When a usable NVIDIA GPU exists, the Windows side (powershell.exe, rocminfo, clinfo) is not read, even with `--all-gpus`.
- Not used: amd-smi (limited in WSL; even the ROCm CLI reports no GPU), xpu-smi (behaviour in WSL not verified), `HSA_OVERRIDE_GFX_VERSION` (unofficial).

Sources:

- AMD "WSL compatibility" (ROCm 7.2.1) https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/wsl/wsl_compatibility.html "Supported AMD Radeon™ Hardware" (Adrenalin Edition 26.1.1 for WSL2), https://github.com/ROCm/librocdxg
- Intel "PyTorch Prerequisites for Intel GPUs" 2.14 https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu/2-14.html "Install for WSL2" ("The /dev/dxg must exist, and clinfo -l must list an Intel platform with at least one device") and "Install for Windows" ("version 32.0.101.8801 … or higher")
- Intel "Understanding the Intel Graphics Driver Version Number" https://www.intel.com/content/www/us/en/support/articles/000005654/graphics.html ("The seven digit build number is created by combining the numbers from the last two sections")
- Microsoft "wsl.conf" https://learn.microsoft.com/en-us/windows/wsl/wsl-config "[automount] root", "[interop] enabled"

### GPU name form

GPU names in other users' submissions (contrib) are checked by form (`hardware.nvidia_name_well_formed`), not by the name table (#90's `nvidia_name_known`).

- Refused (`gpu_known: false`; kept as a record, never used for judging) are only placeholders (empty, `n/a`, `none`, `null`, `nil`, `unknown`, `-`, and nvidia-smi's `[Not Supported]`, `[Unknown Error]`, `ERR!`) and generic names (`NVIDIA Graphics Device`, a device-ID-only `NVIDIA Corporation Device 2684` etc.; `estimate.GENERIC_GPU_KEYS`, `generic_gpu_name`). llmfit also compares names exactly ignoring case and refuses only placeholders (llmfit `share.rs`, `hardware_payload_matches` in `benchmarks.rs`).
- Matching (`estimate.same_gpu`) already requires an exact normalized name and a VRAM ratio of 0.95 or more. A made-up or unknown name only matches a PC that reports the same string. So the name table hardly added safety to matching, and it refused real names (TITAN X (Pascal), vGPU, A800 40GB Active, CMP 30HX etc.).
- Form (case-insensitive; at most 10 words): `NAME := [NVIDIA] BRAND* CORE SUFFIX*`
  - BRAND: GeForce, Quadro, Tesla, GRID, TITAN, RTX, PRO, GTX, GTS, GT, MX, CMP, NVS, FX, DGX
  - Exactly one CORE: `PREFIX? \d{1,4}[A-Z]{0,3}` with `(-(SXM\d?|PCIE|DGXS|FHHL|Ada|\d{1,3}GB|\d{1,3}[ABCQE]|\d{1,3}))*` allowed. PREFIX is only an NVIDIA series head (A, B, H, L, T, P, V, K, M, MX, GB, GH, GA, GP, GV, GK, GM, and RTX for vGPU types such as RTX6000-24Q), so "TY1985", "KEN1990" and "tom99" are not model words. Right after TITAN also `V|X|Xp|Z|Black|RTX`. The first "GeForce GTX TITAN", which has no model word, also passes
  - SUFFIX: Ti, SUPER, D, v2, Ultra, GTX, GTS, GT, GX2, OEM, Laptop, GPU, Max-Q, with, Design, Workstation, Server, Edition, Generation, Ada, Blackwell, Embedded, SFF, Active, PCIe, NVL, `SXM\d?`, `HBM\d+e?`, `\d{1,4}(GB|MB)`, a generation name in parentheses (`(Pascal)` etc.), A, AC. All-digit words (1985 in "RTX 4090 A 1985") and repeated words ("Ti Ti", "8GB 8GB") do not pass (tightened from v2's "at most one all-digit word" after review)
- A name that does not fit the form is not an error but the notice `gpu_name_form` (a maintainer checks it; personal names are seen by the submitter in `contrib show`, which the existing personal-information check covers). A name that fits but is not in the table (`_NAME_CC`) is the notice `unknown_gpu` (still used for judging). A new series adds one word to the word list (not one entry per product).
- A VRAM amount that does not match the name's nominal VRAM (`estimate.NVIDIA_NOMINAL_VRAM`) was lowered from an error to the notice `gpu_vram_nominal` (modified cards such as a 48 GB 4090 exist; the VRAM ratio of 0.95 keeps them away from PCs with other VRAM).
- `_NAME_CC` (name → compute capability) is used only to guess the generation with old drivers and to check a submission's compute capability.
- The characters allowed in a submitted name now include a generation name in parentheses (`(Pascal)` etc.; generation names only).
- Tests: real names (collected from real GPU lists, such as Tesla V100-DGXS-32GB, P106-100, RTX6000-Ada-48Q and MX450) pass, made-up names (phone numbers, initials + year, name + digits, "ABC123 XYZ99") do not (`tests/test_contrib_robust.py`). In the list, `NVIDIA Graphics Device` (generic), `Orin (nvgpu)`, `NVIDIA Thor` (Jetson words are not BRANDs) and the name with a personal name do not pass.

### Known limits

- On Windows a command that times out has only the child killed (grandchildren may remain). The pipe is not closed (closing it blocks until the grandchild ends); it waits 1 more second and returns.
- The reported ~3 GB less free VRAM inside WSL is not put in the value.
- The MIG partition size is not read automatically (give it with `--vram`).
- No measurement has run on GB10's kind of GPU yet (no ✓ verified GPU kind). NVIDIA documents no number for a GPU-only limit.
- Unified memory of Jetson and GH200 / GB200 is not handled (another issue).
- The shared part of an integrated GPU in WSL2 (Intel Arc Graphics) is estimated low from the memory inside WSL.

## Memory and virtual memory

- Installed and free memory come from psutil.
  - On Linux, if the container limit (cgroup v2 `memory.max`, v1 `memory.limit_in_bytes`) is smaller than the installed memory, the limit is used as the installed memory. From 0.11.0 it walks from this process's cgroup (`/proc/self/cgroup`) up to the root and uses the smallest limit; available = limit − usage ("Every environment" below).
  - If psutil fails, the OS's own reading (GlobalMemoryStatusEx, /proc/meminfo, sysctl) is used; if that fails too, the notes say "give it with --ram".
  - Inside WSL it is the amount assigned to WSL (`.wslconfig`); the output says so.
- Windows commit:
  - `ullTotalPageFile` of `GlobalMemoryStatusEx` is "the current commit limit (the smaller of the system's or this process's)", and `ullAvailPageFile` is its free part (https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex).
  - They are output as `commit_limit_gb` and `commit_used_gb`.
  - The current page file size is limit - physical memory (`swap_total_gb`).
- Page file settings: `PagingFiles` (REG_MULTI_SZ) in registry `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management`.
  - `?:\pagefile.sys` is automatic on all drives.
  - `C:\pagefile.sys 0 0`, or a line with no size, is system managed.
  - `C:\pagefile.sys 1024 4096` is minimum and maximum in MB.
  - Empty means no page file.
  - This format could not be confirmed in Microsoft's documents; it rests on values from a real machine and the commonly known format.
  - A system-managed page file can grow up to "3 times RAM or 4 GB, whichever is larger (up to 1/8 of the volume)" (https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows).
- Linux swap: the total of `/proc/swaps`. zram (compressed memory) is not counted, the same as ComfyUI's `get_disk_swap_total`.

Judging (`estimate.ram_limit`): when more main memory is needed than is installed,

| Virtual memory | Upper limit regarded as running | Level |
|---|---|---|
| Fixed size (fixed page file, none, Linux swap) | installed + maximum size (but at most installed x 1.5) | Marginal up to the limit, Too tight above it |
| System managed and growing, unknown, or overridden with `--ram` | installed x 1.5 (as before) | same |

- 1.5x is a judgment value because "weights placed in the page file are extremely slow". It is not measured ([accuracy.md](accuracy.md)).
- The page file size only grows relative to the installed memory, so adding installed memory never lowers the level (`test_fit_monotonic_in_ram_with_fixed_pagefile`).

## VRAM used by the display etc. (current free)

- `memory.used` and `memory.free` of nvidia-smi are output as `vram_used_gb` and `vram_free_gb` (the display, a browser and other programs also use VRAM).
- **The level is decided by the total** (the VRAM in use is freed by closing other apps).
  - When the VRAM needed by the chosen way of running is more than the current free VRAM, the estimate gets `vram_free_short: true` and the reason "current free VRAM is ... it runs if you close other apps".
- Reserved amount: ComfyUI keeps 600 MB free for other apps on Windows (700 MB for GPUs with more than 15 GB of VRAM) and 400 MB elsewhere (`EXTRA_RESERVED_VRAM`).
  - The fixed part of the estimate (`overhead_gb`, a value fitted to measurements) includes this and the CUDA context ([accuracy.md](accuracy.md)).
- Difference from llmfit:
  - llmfit (`gpu_fit_pool_gb` at bffd1b6) decides the level with `min(free, total)` when free is known.
  - Here, image and video generation runs one job at a time and closing other apps is enough, so the level is decided by the total and only a warning is shown.

## Comparison with llmfit

| llmfit's case split | Here |
|---|---|
| Uses nvidia-smi `addressing_mode` (ATS) to find GPUs that share memory with the CPU (Grace Blackwell, Tegra) and treats RAM as VRAM | An NVIDIA GPU that returns no VRAM is not used for judging, except DGX Spark named `NVIDIA GB10`, which has an official ComfyUI guide and uses MemTotal as shared memory (0.11.0). Jetson and GH200 / GB200 are out of scope |
| For Apple silicon, uses Metal `recommendedMaxWorkingSetSize` as GPU memory | Python does not call Metal (to avoid more dependencies), so 3/4 of the installed memory is assumed (see "Shared memory" above) |
| Infers from the GPU name when nvidia-smi returns no VRAM | Does not infer (it does not guess VRAM and then say "it fits") |
| Adds up the VRAM of several cards of the same model (llama.cpp and others can split a model) | Does not add (ComfyUI runs on one GPU). The one with the most VRAM |
| On Linux without nvidia-smi, finds NVIDIA GPUs through sysfs and lspci | Not used for judging (it is unknown whether CUDA works). Only shown in lists such as `--all-gpus` |
| Uses AMD (rocm-smi, sysfs), Intel (lspci) and Apple (system_profiler) for judging | Since 0.7.0, only combinations where ComfyUI + PyTorch runs (the support table). Unlike Vulkan in llama.cpp, it does not run on every GPU, so Vulkan is not counted |
| Fixes WMI's AdapterRAM cap with the registry qwMemorySize (issue #830), matches by DriverDesc, and does not use it if ambiguous | Same idea. In addition, it matches first by MatchingDeviceId (the model ID) and drops non-PCI adapters |
| Drops integrated GPUs (Intel UHD etc.) when a discrete GPU exists | Uses a GPU with dedicated VRAM before an integrated GPU. UHD and Iris Xe are not in the support table, so they are not used |
| Adds the AMD APU's UMA carve-out back to the installed memory | Uses the carved-out VRAM + GTT (or half of the visible RAM) as GPU memory |
| Alternative reading when `available_ram_gb` is 0 (macOS) | psutil's value as is |
| Mapping of compute capability, driver and PyTorch build | Not in llmfit. Added here (see "Generations, PyTorch and drivers" above) |
| Page file and swap | Not in llmfit. Added here (see "Memory and virtual memory" above) |

## Timing

Medians measured on 2026-10-20 on a development PC (Windows 11, NVIDIA GPU, with load from other work):

| Method | Time |
|---|---|
| Previous `detect()` (one nvidia-smi call) | about 0.1 to 0.25 s |
| Current `detect()` (default; with an NVIDIA GPU, one nvidia-smi call + ctypes + registry) | about 0.15 to 0.4 s (within the spread of nvidia-smi; ctypes and registry take under 1 ms) |
| `detect(other_gpus=True)` / `system --all-gpus` | about 1.0 to 1.2 s (starting PowerShell takes about 1 s) |

Running only nvidia-smi 15 times each, alternating, the query with more fields and the previous query showed no measurable difference (both 0.1 to 0.25 s).

## Tests

- `tests/test_hardware.py`, `tests/test_vendors.py` (AMD, Intel, Apple, APU, families that run on CPU, #26), `tests/test_detect_everywhere.py` (every environment, GB10, WSL2; #101) and `tests/fixtures/hardware/`.
- The tool with which bench reads utilization and used memory of non-NVIDIA GPUs (`gpustat.py`) is tested by `tests/test_gpustat.py` (output examples from official documents and sources, and the actual typeperf output of a development PC) and `tests/test_bench_vendors.py`.
- The default tests do not call the real nvidia-smi, PowerShell, lspci or registry (`_no_real_hardware` in `tests/conftest.py` blocks `hardware._run`, `_reg_display_entries` and `_reg_paging_files`, and from 0.11.0 also the sysfs device list, amdkfd and the WSL2 state).
- Fixtures:
  - Actual output of nvidia-smi, WMI and the registry of a development PC (instance IDs replaced with 0).
  - Multiple GPUs, an old version without compute_cap, `[N/A]`, broken output, output when the driver is not running or there is no GPU.
  - AMD only, Intel + NVIDIA, WMI of an NVIDIA GPU without a driver, a registry that keeps a removed GPU, lspci.
- What is checked:
  - The verdict for each generation (Kepler, Maxwell, Pascal, Volta, Turing, Ampere, Ada, Hopper, Blackwell).
  - That adding VRAM or RAM with the generation fixed never lowers the level.
