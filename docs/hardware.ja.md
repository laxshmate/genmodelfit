# ハードウェアの検出と判定(#21)

[English](hardware.md) · **日本語**

この PC で何を調べ、どう判定に使うかを説明します。
GPU・メモリ・ドライバの検出と、判定の根拠にした一次資料を知りたい開発者・コントリビューター向けです。
実装は `src/genmodelfit/hardware.py`(検出)と `src/genmodelfit/estimate.py`(`gpu_precision`・`ram_limit`)です。
出力の形は [schema.ja.md](schema.ja.md)「System」にあります。

一次資料は 2026-10-20 に読んだ(版・コミットは各節に書く)。
llmfit の `llmfit-core/src/hardware.rs`(コミット bffd1b6、MIT)は考え方だけを参考にし、コードは写していない。

## 目次

- [何を調べるか](#何を調べるか)
- [nvidia-smi](#nvidia-smi)
- [世代と PyTorch・ドライバ](#世代と-pytorchドライバ)
- [精度(ComfyUI が選ぶ dtype と VRAM)](#精度comfyui-が選ぶ-dtype-と-vram)
- [NVIDIA 以外の GPU(0.7.0、#22・#23)](#nvidia-以外の-gpu0702223)
- [CPU で動く系統(0.7.0、#39)](#cpu-で動く系統07039)
- [同じ GPU で成功した実測(0.7.0、#26)](#同じ-gpu-で成功した実測07026)
- [すべての環境(0.11.0、#101)](#すべての環境0110101)
- [メモリと仮想メモリ](#メモリと仮想メモリ)
- [画面などが使う VRAM(今の空き)](#画面などが使う-vram今の空き)
- [llmfit との突き合わせ](#llmfit-との突き合わせ)
- [時間](#時間)
- [試験](#試験)

## 何を調べるか

| 項目 | どこから | 判定に使うか |
|---|---|---|
| NVIDIA の GPU(名前・VRAM の総量・空き・使用量・ドライバ・compute capability) | `nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used,driver_version,compute_cap,mig.mode.current --format=csv,noheader,nounits`(0.11.0 で MIG の状態を足した) | 使う(backend が `cuda` のものの最大の 1 枚) |
| NVIDIA 以外も含めた画面の部品 | Windows: PowerShell の `Get-CimInstance Win32_VideoController` + レジストリの `HardwareInformation.qwMemorySize`。Linux: `lspci -Dnn`(AMD の VRAM・GTT は sysfs の `mem_info_vram_total`・`mem_info_gtt_total`)。macOS: `system_profiler SPDisplaysDataType -json` | NVIDIA の GPU が使えないときだけ、AMD・Intel・Apple を対応表で使う(0.7.0、下の「NVIDIA 以外の GPU」)。PCI の型番 ID(`pci_id`)も取る(0.9.0。同じ GPU の実測の照合と、bench の提出に使う) |
| 物理メモリ | psutil(読めなければ OS の読み方。Linux はコンテナ(cgroup)の上限があればそれ) | 使う |
| 仮想メモリ | Windows: `GlobalMemoryStatusEx`(ctypes)とレジストリ `Memory Management\PagingFiles`。Linux: `/proc/swaps`(zram を除く)。macOS: 自動で伸びる扱い | 使う(足りないときの上限) |
| ディスクの空き | `shutil.disk_usage` | 表示だけ |

調べないもの・残さないもの: PC 名・ユーザ名・GPU の UUID・PCI の差し場所(bus id)・PNPDeviceID の後半(個体・差し場所の ID)。

- WMI の PNPDeviceID は、型番を表す前半(`PCI\VEN_xxxx&DEV_xxxx&SUBSYS_…&REV_…`)をレジストリとの突き合わせに使う。
- そのうち VEN・DEV の部分だけを PCI の型番 ID(`pci_id`。`1002:744c` の形)として出力に残す(0.9.0。同じ GPU の実測の照合に使う)。
- SUBSYS・REV と後半(個体・差し場所の ID)は残さない(`tests/test_hardware.py::test_system_json_has_no_private_values`)。
- Linux も `lspci -nn` の `[1002:744c]` から同じ形で残す。

取った情報は手元で使うだけで、どこにも送らない。

## nvidia-smi

- 場所: PATH → `%SystemRoot%\System32\nvidia-smi.exe`(今の Windows のドライバ)→ `%ProgramFiles%\NVIDIA Corporation\NVSMI\nvidia-smi.exe`(古いドライバ)→ Linux の `/usr/bin`・WSL の `/usr/lib/wsl/lib`・`/usr/local/bin`。
- 時間制限 10 秒(普段は 0.1〜0.5 秒)。Windows では窓を出さない(`CREATE_NO_WINDOW`。GUI のアプリから呼ばれるため)。
- 出力の読み方:
  - UTF-8 として読み、読めない字は置き換える。
  - `, ` で区切り、項目の数が合わない行は飛ばす(エラーの文言・空行・壊れた行)。
  - `[N/A]`・`[Not Supported]`・`N/A`・空は「分からない」。
  - 空きが分からず総量と使用量があれば 総量 − 使用量。
  - 単位付き(`8192 MiB`)・カンマの小数点(`8,6`)も読む。
- VRAM の総量が返らない GPU(メモリを CPU と共有するもの)は判定に使わない(backend `none`、理由付き)。ただし DGX Spark(GB10)は統合メモリの GPU として使う(0.11.0。下の「DGX Spark(GB10)」)。
- `compute_cap` を知らない古い nvidia-smi は「Field "compute_cap" is not a valid field to query.」で終了コード 2 になる(開発用の PC で存在しない項目を聞いて確かめた。どのドライバの版で入ったかは一次資料で確かめられなかった)。
  - そのときは `compute_cap` を除いて聞き直し、GPU 名の表(`hardware._NAME_CC`)から推す。
  - 迷う名前(ノート用の GTX 960M など、同じ型番で世代が混ざるもの)は推さない。
  - 表は NVIDIA の一覧(https://developer.nvidia.com/cuda-gpus 、https://developer.nvidia.com/cuda-legacy-gpus)に合わせた。
  - 例: 「NVIDIA TITAN X」は Pascal(6.1)、「GeForce GTX TITAN X」は Maxwell(5.2)、Quadro K620 / K1200 / K2200 は Maxwell(5.0)、ノート用の RTX 2050 は Ampere(8.6)。
  - 業務用の Ada は「RTX 4000 Ada Generation」のほか、「RTX 4000 SFF Ada Generation」「RTX 2000E Ada Generation」の書き方も Ada(8.9)にする(NVIDIA の製品ページ https://www.nvidia.com/en-us/design-visualization/rtx-4000-sff/ 、2000E は NVIDIA の製品の正規の販売元 PNY のページ https://www.pny.com/en-tw/rtx-2000e-ada-generation 。2026-10-27)。
  - 名前から推した値は `compute_capability_source: "name"`。
- NVIDIA の GPU は PCI の型番 ID を読まない(nvidia-smi の GPU の `pci_id` は空)。同じ GPU かは、そろえた名前と VRAM の比で比べる(`estimate.same_gpu`。型番 ID が両方にあるときだけ ID で比べる)。画面の部品(`other_gpus`)には WMI・lspci の型番 ID が出るが、NVIDIA の判定には使わない(`tests/test_nvidia_identity.py`)。
- 終了コードが 0 でなくても、読める行は使う(1 枚だけ「Unable to determine the device handle … Unknown Error」で読めないときなど)。読めなかった枚数を notes に書く(エラーの文言は PCI の差し場所を含むので写さない)。
- 複数の GPU: ComfyUI は 1 枚で動くので、判定に使えるもの(backend `cuda`)のうち VRAM が最大の 1 枚を使う。
  - ComfyUI は既定で CUDA の 0 番(`get_torch_device` → `torch.cuda.current_device()`)を使う。
  - CUDA の番号の付け方の既定は `CUDA_DEVICE_ORDER=FASTEST_FIRST`(速い順。nvidia-smi の PCI の順とは違うことがある)。
  - どれが 0 番かは nvidia-smi からは確かに決められない。違う型番の GPU が複数あるときは「ComfyUI を `--cuda-device` で最大の GPU に向ける」と notes に書く。
- `--vram` で上書きしたとき:
  - 判定に使える NVIDIA の GPU があれば、その世代・ドライバ・精度のまま VRAM だけを変える(この PC の GPU の VRAM が違ったら、の判定)。
  - 無い・使えない世代なら、世代の分からない仮の GPU にする(精度の直しは無し)。
  - `--ram` は搭載量だけを変え、この PC のページファイル・コミットは使わない(別の PC の想定なので)。

## 世代と PyTorch・ドライバ

### PyTorch の CUDA 版のビルドが対応する compute capability

PyTorch の配布物は機械語(SASS)だけを入れる(`.ci/manywheel/build_env_setup.py` v2.14.1 の `_ptx_arches`:「Release/RC wheels ship SASS-only」)。

- 機械語は「同じ major で minor が同じか大きい GPU」でだけ動く(CUDA Programming Guide 1.3.4.1 Binary Compatibility: https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/cuda-platform.html)。
- 例: 8.6 向けは 8.6・8.9 で動き、8.0 や 9.0 では動かない。
- Jetson の 5.3・6.2・7.2・8.7 などは別扱い(PyTorch の `DEVICE_REQUIREMENT`)。CUDA 13.2 以降の 8.7 は下の「GPU の登録の表と公式の一覧」。

| ビルド | PyTorch の版 | x86_64 の compute capability | 出典 |
|---|---|---|---|
| cu132 | 2.12〜2.14 | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.14.1 `torch/cuda/__init__.py` `PYTORCH_RELEASES_CODE_CC`、`.ci/pytorch/windows/build_env_setup.py` |
| cu130 | 2.9〜2.14 | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | 同上 |
| cu129 | 2.8〜2.13(Windows は 2.8・2.9) | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.13.0 `PYTORCH_RELEASES_CODE_CC`、`.ci/pytorch/windows/internal/cuda_config.bat` |
| cu128 | 2.7〜2.11(aarch64 は 2.7・2.9〜2.11) | 7.5, 8.0, 8.6, 9.0, 10.0, 12.0 | v2.11.0 `.ci/manywheel/build_cuda.sh`・`cuda_config.bat` |
| cu126 | 2.6〜2.14 | 5.0, 6.0, 6.1(Windows), 7.0, 7.5, 8.0, 8.6, 9.0 | v2.14.1 同上 |

各版の CUDA 版は `.github/scripts/generate_binary_build_matrix.py` の `CUDA_ARCHES` にある。

- 2.7: 11.8 / 12.6 / 12.8
- 2.8: 12.6 / 12.8 / 12.9
- 2.9: 12.6 / 12.8 / 13.0
- 2.10・2.11: 12.6 / 12.8 / 12.9 / 13.0
- 2.12: 12.6 / 13.0 / 13.2
- 2.13: 12.6 / 12.9 / 13.0 / 13.2
- 2.14.1: 12.6 / 13.0 / 13.2

cu128・cu129 は今の版(2.14)には無い。
ただ、配布先に残っていて、ComfyUI の同梱版や AI生成キットのように版を固定して使う側があるので数える(ドライバ 580 未満の Blackwell が動く道)。

### CUDA の最低ドライバ

NVIDIA CUDA Toolkit 13.4 Update 2 のリリースノート(https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html)の表 3「CUDA Toolkit and Minimum Required Driver Version for CUDA Minor Version Compatibility」を使う。

- 13.x は 580 以上。12.x は 525 以上(表 3 の 12.x の欄は 525 以上 580 未満。それより新しいドライバでも動く)。
- 表 3 は OS で分けていないので、そのまま使う(表 4 の 527.41 / 525.60.13 は CUDA 12.0 GA の同梱ドライバの版で、最低の条件ではない)。
- PyTorch の配布物は機械語だけで PTX の JIT を使わないので、マイナー版の互換の範囲で動くとみなした。

| ビルド | Windows・Linux |
|---|---|
| cu126 / cu128 / cu129 | 525 以上 |
| cu130 / cu132 | 580 以上 |

世代ごとの最低(`hardware.ARCH_MIN_DRIVER`):

- Blackwell(10.x・12.x)の機械語が入るのは CUDA 12.8 から(PyTorch の cu128 以降。上の表)。
- 表 4 の CUDA 12.8 GA は Linux 570.26 / Windows 570.65。570 未満では CPU として判定する(それより古いドライバは Blackwell を知らないので、nvidia-smi に出ること自体がまれ)。
- ほかの世代はビルドの条件と同じ。

WSL の nvidia-smi は Windows のドライバの版を返すので、Windows の表で見る。

### 判定

| 世代 | compute capability | 動くビルド | ドライバの条件 | 判定 |
|---|---|---|---|---|
| Fermi 以前・Kepler | 〜3.7 | なし | — | **CPU**(ドライバを更新しても動かない) |
| Maxwell | 5.0, 5.2 | cu126 | 525 | GPU |
| Pascal | 6.0, 6.1 | cu126 | 同上 | GPU |
| Volta | 7.0 | cu126 | 同上 | GPU |
| Turing | 7.5 | cu126〜cu132 | 同上(cu13x は 580) | GPU |
| Ampere | 8.0, 8.6 | cu126〜cu132 | 同上 | GPU |
| Ada | 8.9 | cu126〜cu132(8.6 の機械語) | 同上 | GPU |
| Hopper | 9.0 | cu126〜cu132 | 同上 | GPU |
| Blackwell | 10.x, 12.x | cu128〜cu132 | 570(世代の最低)/ 580(cu13x) | GPU |
| それより新しい | 13.x〜 | なし | — | **GPU**(断らず「表より新しい世代。最新の PyTorch を使う」と注意。0.10 まで CPU) |

- 動くビルドが 1 つも無い世代、またはドライバがどのビルドにも足りないときは、backend `none` にして CPU として判定する。理由は `Gpu.notes` と `SystemInfo.notes` に書く(「ドライバを更新すれば GPU で動く」かどうかも書く)。
- 世代が分からない(nvidia-smi も名前の表も分からない)・ドライバの版が読めないときは断らない(分からないと書く)。
- `torch_cuda` は動くビルドの一覧(新しい順)で、参考の情報。判定には使っていない。
- AI生成キットの知見(Kepler は CPU、Maxwell・Pascal・Volta は cu126、Quadro K620/K1200/K2200 は Maxwell)と一致する。

## 精度(ComfyUI が選ぶ dtype と VRAM)

ComfyUI(コミット 426f5ee、v0.43.0。2026-10-27 に読み直した)の `comfy/model_management.py` と `comfy/sd.py` を読んだ。

- GPL-3.0 なので import しない。名前の一覧だけを事実として写した(`hardware._COMFY_FP16_PASCAL`・`_COMFY_FP16_BROKEN`)。
- 以下は、起動の引数を付けない既定の動き。

**fp16・bf16・fp8 を使うか**:

- `should_use_fp16`:
  - major ≥ 8 は使う。
  - major < 6(Maxwell 以前)は使わない。
  - 名前に 1080 / 1070 / 1060 / 1050 / titan x / p3000 … / p6 / p4 を含む GPU(Pascal)は **Windows だけ**使う(Linux は fp32 のほうが速いとして使わない)。
  - それ以外の major < 7(GT 1030 など)は使わない。
  - 1660 / 1650 / 1630 / T500 / T550 / T600 / MX550 / MX450 / CMP 30HX / T2000 / T1000 / T1200 を名前に含む GPU は「fp16 が壊れている」として使わない。
  - それ以外(Volta・Turing の RTX)は使う。
- `should_use_bf16`: major ≥ 8。
- `supports_fp8_compute`: 8.9 以上(Windows は torch 2.4 以上)。

**本体の重み**(`unet_dtype` model_management.py:1136〜1187 / `unet_manual_cast` 1189〜1211): 系統ごとの `supported_inference_dtypes`(下の表)の中から選ぶ。

- fp16 を持つ系統(SDXL・Flux・Wan など)は、bf16 を持たない GPU でも fp16 で載せて fp16 で計算する(bf16 の重みも同じ 2 バイト)。
- **fp16 を持たない系統**(`[bfloat16, float32]`: LTX・Qwen Image・Z-Image・HunyuanVideo・ACE-Step など)は、bf16 を持たない GPU(Pascal・Volta・Turing)では `unet_manual_cast` が float32 を返す(計算は fp32、作業用メモリは 2 倍)。
  - 重みは `should_use_bf16(manual_cast=True)` が決める。
  - fp32 にすると `total × 0.88 − 最小の作業用メモリ` に収まらないときだけ bf16 のまま載せる。
  - 収まるなら fp32(2 倍)で載せる。
- 半精度を使わない GPU(Maxwell など)も、fp32 にしても収まるときだけ fp32 に広げる。収まらなければ 2 バイトのまま計算だけ fp32。
- fp8 の重みは、fp8 の計算を持たない GPU では、fp16 に広げても収まれば広げる。収まらなければ fp8 のまま(計算のたびに変換)。
- fp8_scaled・int8_convrot など量子化の設定を持つ重みは量子化のまま(`quant_config`。sd.py:2277)。

**テキストエンコーダ**: 既定は fp16。ファイルの精度が 2 バイト以下ならその精度のまま載せる(`pick_weight_dtype`)。

**VAE**(`vae_dtype` model_management.py:1290):

- VAE ごとの `working_dtypes`(sd.py。既定は `[bfloat16, float32]`: sd.py:518)の中から選ぶ。fp16 は `should_use_fp16`、bf16 は `should_use_bf16` のときに選び、どちらも無ければ fp32。
- 2D の KL VAE(SD・SDXL・SD3・Flux の ae。sd.py:677〜712 の既定の枝)・LTX(784)・Mage(629)は fp16 を持たない。bf16 を持たない GPU では fp32 になる。
- Flux2 の VAE は、0.40.0 では KL の既定のまま fp16 を持たない(426f5ee では fp16 を足した: sd.py:693)。製品のエンジンが 0.40 なので持たない側で見る。
- Wan(853・865・881)・HunyuanVideo(795・828)・CogVideoX(812)・Oobleck(742)などは fp16 を持つ。
- 仕上げで VRAM が足りなければ、ComfyUI はタイルに分けて続ける(遅い)。

**作業用メモリ**: ComfyUI 自身の見積もり(`model_base.memory_required`、VAE の `memory_used_decode`)が、計算の精度の大きさ(`dtype_size`)を掛けている。fp32 で計算すると 2 倍。

見積もりへの効かせ方(`estimate.gpu_precision`。コードで確かなところだけ):

| GPU | 例 | Gpu.compute_dtype | 系統・VAE | 見積もりの直し |
|---|---|---|---|---|
| 半精度を使わない | Maxwell 全部、GT 1030、Linux の GTX 1080、GTX 1660、T1000 | `fp32` | どれでも | 作業用メモリ × 2、VAE の重み × 2(VAE のファイルが fp32、または VAE がどの GPU でも fp32 なら × 1) |
| fp16 は使うが bf16 を持たない | Windows の Pascal(1080 など)、Volta、Turing の RTX | `fp16` | fp16 を持たない系統 | 作業用メモリ × 2 |
| 同上 | 同上 | `fp16` | fp16 を持たない VAE | VAE の重み × 2(#100 から仕上げの段の作業用メモリも × 2) |
| 同上 | 同上 | `fp16` | fp16 を持つ系統 | 変えない(bf16 の重みには「fp16 で動かす(VRAM は同じ)」と書く) |
| 同上 | 同上 | `fp16` | 系統が分からない | 変えない。「系統が分からないので入れていない」と書く |
| fp8 の計算を持たない | Ada より前 | — | fp8 の重み | 変えない。「余裕があれば fp16 に広げる・無ければ遅い」と書く |
| 上のどれでもない | Ampere・Ada・Hopper・Blackwell | `fp16` | どれでも | 変えない |

**fp32 で保存された本体は 2 バイトで数える**(0.15.1)。`unet_dtype` は、系統が fp16 を持ち `should_use_fp16` なら fp16、持たなくても `should_use_bf16` なら bf16 を返す(model_management.py:1170-1176@6e8488e)。`should_use_fp16` は NVIDIA の compute capability 8 以上・ROCm・MPS で VRAM の量によらず真(:1963-2000@6e8488e)なので、基準(半精度の NVIDIA の GPU)では本体の重みを 0.5 倍にする。半精度を使わない GPU と、bf16 を持たない GPU で fp16 を持たない系統を動かすときは、ファイルの大きさのまま(安全側。`GpuPrecision.main_factor`)。CPU は fp32 で持つので、メインメモリはファイルの大きさ。ComfyUI の系統が分からないモデルは換算しない。文章の読み取りの部品も ComfyUI は fp16 で載せる(`text_encoder_dtype`)が、ファイルの精度が名前で分からないことが多いので換算していない。

**それ以外で本体の重みは変えない**(fp32 に広げる側)。理由:

- fp32 にしても収まるときだけ fp32 で載せる、という動きは、VRAM が多いほど使う量が増える。見積もりに入れると VRAM を増やして段階が下がる。
- 重みはファイルの 2 バイトで数え、ComfyUI が広げるのは収まるときだけなので、動くかどうかの判定は変わらない。
- 広げたときは作業用メモリの分が足りなくなり、ComfyUI が重みの一部を逃がして遅くなることがある。そう理由に書く。

作業用メモリ × 2・VAE × 2 は、GPU の世代と系統だけで決まり VRAM の量によらない。
そのため、世代を固定して VRAM・RAM・ページファイルを増やしても段階は下がらない(`test_fit_monotonic_in_vram_per_arch`)。
fp32 で計算する場合は、その GPU の実測でない限り `basis` は `estimated` にする(実測は半精度の GPU のもの)。

### 系統の表(`src/genmodelfit/families.py`、ComfyUI コミット 426f5ee の行)

本体(`comfy/supported_models.py` の `supported_inference_dtypes`。書いていないクラスは `supported_models_base.py:47` の `[float16, bfloat16, float32]`):

| 系統(鍵) | ComfyUI のクラス | fp16 | 行 |
|---|---|---|---|
| sd / sd3 / hunyuan3d / stable_audio | SD15・SDXL・SVD・SD3・Hunyuan3Dv2・StableAudio など | ○ | base:47 |
| flux | Flux / FluxSchnell(Ovis も Flux として読む: model_detection.py:363) | ○ | 754 |
| flux_fill | FluxInpaint(in_channels 96 = Fill・OneReward) | × | 784 |
| flux2 / flux3 | Flux2 / Flux3 | ○ / × | 813 / 856 |
| chroma / longcat / krea2 / anima / cosmos | Chroma / LongCatImage / Krea2 / Anima / Cosmos | ○ | 1821 / 2462 / 2024 / 1172 / 1101 |
| lens / mochi | Lens / GenmoMochi | × | 883 / 925 |
| ltxv | LTXV / LTXAV(LTX-Video・LTX-2.x) | × | 953 |
| minimax_h3 | MiniMaxH3(FastVideo FastH3 も) | × | 1001 |
| hunyuan_video / hunyuan_image21 | HunyuanVideo / HunyuanImage21 | × | 1028 / 2232 |
| hunyuan_video15 | HunyuanVideo15 | ○ | 2270 |
| lumina2 / zimage / ming_image / pixeldit | Lumina2(NewBie: model_detection.py:611)/ ZImage / MingImage / PixelDiTT2I | × | 1214 / 1241 / 1241 / 1299 |
| wan | WAN21_* / WAN22_*(HuMo・VACE・S2V・Animate・SCAIL・CausalAR など) | ○ | 1367 |
| trellis2 | Trellis2(Pixal3D も: model_detection.py:125) | × | 1542 |
| triposplat / seedvr2 / sam3 / depth_anything3 / minimax_music3 / cogvideox | 同名 | ○ | 1686 / 1854 / 2541 / 2500 / 2429 / 2605 |
| hidream / hidream_o1 | HiDream / HiDreamO1 | × | 1711 / 1736 |
| ace / ace15 / yue2 | ACEStep / ACEStep15 / YuE2 | × | 1904 / 2379 / 2405 |
| omnigen2 | Omnigen2 / Boogu(`extended_fp16_support` のとき float16 を先頭に足す: 1939) | ○ | 1931 |
| ideogram4 / moss_image / mage_flow | Ideogram4 / MOSSImage / MageFlow | × | 1995 / 2053 / 2114 |
| qwen_image / qwen_image21 / joyimage | QwenImage(FireRed・Marigold v2 も)/ QwenImage21 / JoyImage | × | 2143 / 2173 / 2205 |
| kandinsky5 / ernie | Kandinsky5 / ErnieImage | × | 2323 / 2524 |

Z-Image は、重みの値で `allow_fp16` が立つと fp16 も使う(model_detection.py:624、supported_models.py:1245)。
ただ、ファイルを読まないと分からないので、fp16 を持たない側(安全側)で見て、理由に書く。

VAE(`comfy/sd.py` の `working_dtypes`。○ は fp16 を持つ、× は持たない):

- `kl`(既定 518。2D の KL)×
- `flux2`(Flux2 の VAE。0.40.0 は既定のまま: 515・693@0.40.0)×
- `ltxv`(784)×
- `ltx_diffusion`(647)×
- `mage`(629)×
- `pixel`(ピクセル空間。925〜933、既定のまま)×
- `wan`(881。Wan 2.1・Qwen Image)○
- `wan22`(865。Wan 2.2、16 倍圧縮)○
- `qwen21`(853。Qwen Image 2.1、16 倍圧縮)○
- `oobleck`(742。ACE-Step 1.5・Stable Audio Open)○
- `yue2`(742・744〜747)○
- `sa3_large` / `sa3_small`(1029。Stable Audio 3 の大きい・小さい設定)○
- `dav`(542。MiniMax Music 3。どの GPU でも float32 なので GPU による違いが無い)○
- `hunyuan_video`(828)○
- `hunyuan_video15`(795)○
- `hunyuan3d`(906)○
- `trellis2`(596・601)○
- `seedvr2`(612)○
- `cogvideox`(812)○
- `minimax_h3`(1053)○
- `ace`(921)○
- `fp32`(542・951・1003・1087。どの GPU でも fp32)

一覧の各項目の系統と VAE の決め方:

- 系統は `families.family_of` で決める。本体のファイル名 → ID・テンプレート名の順に照らす。部品の置き場所のリポジトリ名は別のモデルの部品が混ざるので見ない。
  - どちらでも決まらない画像の本体は、見出しの要素数(`header_params`)が SD の指紋(`families.SD_PARAM_PRINTS`)に合えば SD にする(0.15.1)。
    指紋は公式のファイルの見出しで数えた要素数で、SD1.5 の全部入り 1,066,247,386(v1-5-pruned-emaonly)・UNet 859,520,964、
    SDXL の全部入り 3,468,837,945(sd_xl_base_1.0)・UNet 2,567,463,684(2026-11-11)。幅は ±20,000
    (inpaint の conv_in の 5 チャンネル分 14,400 を含み、ほかの形は数千万以上違う)。本体のファイルがすべて指紋に合うときだけ
- VAE は VAE のファイル名(無ければ系統の既定)で決める。
- `update` が `ModelEntry.comfy_family` / `comfy_vae` に入れる(`--heads-only` でも決め直す)。

0.9.1 の同梱の一覧(2026-10-21、329 件)では、278 件に系統が付き、51 件は分からない。
分からない 51 件の内訳: 読み上げ 22 件・ComfyUI に組み込みでない補助ツールなど 19 件・Capybara 4 件・HF の 6 件(ComfyUI のどのクラスで読むかを確かめられなかったもの)。
分からないものは見積もりを変えず、bf16 を持たない GPU でだけ理由に書く。

## NVIDIA 以外の GPU(0.7.0、#22・#23)

NVIDIA の GPU(nvidia-smi で確かめた `cuda`)が使えないときだけ、画面の部品の一覧から AMD・Intel・Apple の GPU を下の対応表で判定に使う(`hardware.classify_adapter`)。

- **NVIDIA の判定は変えない**。NVIDIA の GPU が使えれば、ほかの GPU は `--all-gpus` で一覧に出すだけ。
- 動くかどうかは下の対応表(一次資料)で決める。
- **段階は NVIDIA の専用 VRAM の GPU と同じ基準で決める**(0.11.0、#108。上限なし)。0.11.0 より前は、この種類の GPU で動いた実測がまだ無いことを理由に Good までにしていた(#101・#103)。今は代わりに、この種類の GPU で 2 機種以上で動いた記録があれば、表の PC の行に「✓ 確認済みの種類」を出す([scoring.ja.md](scoring.ja.md)「確認済みの GPU の種類」)。理由(notes)には「段階は NVIDIA の専用 VRAM の GPU と同じ基準で決めた(<会社>。この種類の GPU で動いた実測があるかは、表の PC の行の ✓ 確認済みの種類で分かる)」と書く(`hardware.unverified_kind_text`)。
- 同じ GPU・同じ設定の実測(ほかの利用者の実測も。[accuracy.ja.md](accuracy.ja.md)「ほかの利用者の実測」)は、段階を上げるだけで下げない(下の「同じ GPU で成功した実測」)。
- NVIDIA の GPU での実測は、作業用メモリをずらす補正にだけ使い(`basis_detail: calibrated`)、`basis` は `estimated`。
- 対応しない組み合わせは、理由(`Adapter.reason`)を付けて CPU として判定する(下の「断る理由」)。

### 断る理由

NVIDIA 以外の GPU の扱いは 3 つに分かれる(0.11.0、#108)。

| 扱い | どれ | 理由の文 |
|---|---|---|
| 使う(未確認。印なし) | 下の対応表で動く組み合わせ。DGX Spark(GB10)も | 「段階は NVIDIA の専用 VRAM の GPU と同じ基準で決めた …」。2 機種以上の実測が集まれば ✓ 確認済みの種類 |
| 対応の根拠が無いので断る(CPU として判定) | AMD がまだ対応を出していない世代: gfx1170・gfx1171・gfx1250(TheRock で Release Ready でない)。RDNA 1 以前・Windows 10 など、ComfyUI の案内の外のもの | 「… Release Ready でない(Build Passing・Sanity Tested まで。PyTorch が動く根拠が無い)ので、CPU として判定した」 |
| 動いても CPU より速い見込みが無いので CPU として判定 | 2 CU の内蔵 GPU: Radeon 610M(gfx1036。Raphael・Granite Ridge・Dragon Range・Fire Range・Mendocino)・Radeon 820M(Ryzen AI 5 330) | 「… 画面を出すための 2 CU の小さな内蔵 GPU で、動いても生成では CPU より速い見込みが無いので、CPU として判定した」 |

- CU の数の出典: AMD の製品ページの「Graphics Core Count」(Ryzen 9 7940HX の Radeon 610M は 2、Ryzen AI 5 330 の Radeon 820M は 2(https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-5-330.html 、2026-10-28 に確かめた)。同じ石の 840M は 4、860M は 8)。
- 820M は gfx(gfx1152・1153)では 840M・860M と分けられないので、名前で決める(`gputables.AMD_DISPLAY_ONLY_NAMES`: 名前の「820M」と、名前が「AMD Radeon(TM) Graphics」だけのときの CPU の名前「Ryzen AI 5 330」)。新しい pci.ids の lspci の名前「Radeon 820M / 840M」(1902)は、CPU の名前が Ryzen AI 5 330(820M だけの CPU)なら 820M として CPU にし、それ以外は 820M か 840M か決まらないので使う側にする。
- 0.11.0 より前は 820M を使っていた(gfx1153 の RDNA 3.5 として)。

### 対応表(一次資料は 2026-10-21 に読んだ)

| 会社・部品 | OS | 判定 | 一次資料 |
|---|---|---|---|
| AMD Radeon RDNA 4(RX 9070 / 9060、gfx1200・1201) | Windows 11・Linux | **GPU**(`rocm`) | ComfyUI README「AMD GPUs (Windows, ROCm 10.0)」(Comfy-Org/ComfyUI 426f5ee: 「supported architectures include RDNA 2, RDNA 3, RDNA 3.5, and RDNA 4」「Use Windows 11」)、ROCm/TheRock `SUPPORTED_GPUS.md`・`RELEASES.md`(4639f91、「GFX target lookup table」・Release Ready) |
| AMD RDNA 3.5 の APU(Ryzen AI Max = gfx1151、Ryzen AI 9 HX 37x = gfx1150、Ryzen AI 7 350 = gfx1152、Radeon 8065S = gfx1151。名前が「Radeon 820M / 840M」のものは gfx1153(公式の資料どうしで食い違う。下)) | Windows 11・Linux | **GPU**(`rocm`、共有メモリ) | 同上 |
| AMD Radeon RDNA 3(RX 7900 = gfx1100、7800 / 7700 = gfx1101、7600・7500・7400・7300・PRO W7400 = gfx1102)、Radeon 780M / 760M / 740M・Ryzen Z1 Extreme / Z1(gfx1103) | Windows 11・Linux | **GPU**(`rocm`) | 同上。Linux の pytorch.org の ROCm 版(ComfyUI の Linux の案内 `--index-url …/rocm7.2`)も入れる |
| AMD Radeon RDNA 2(RX 6900〜6800 = gfx1030、6700 = gfx1031、6600 = gfx1032、6500 / 6400 / 6300・Pro W6400 / W6300 = gfx1034)、Radeon 680M / 660M(gfx1035)、Van Gogh・Sephiroth(Steam Deck・OLED 版。Windows の名前は AMD Custom GPU 0405。8 CU、gfx1033。共有メモリ) | Windows 11・Linux | **GPU**(`rocm`。bf16 なし) | 同上。pytorch.org の ROCm 版は gfx1030 だけ(pytorch/pytorch v2.14.1 `.ci/docker/manywheel/build.sh` の `PYTORCH_ROCM_ARCH`)なので、ほかは AMD の multi-arch 版(TheRock)を使うと notes に書く |
| AMD Instinct(MI100〜MI355X、gfx908・90a・942・950) | Linux | **GPU**(`rocm`) | pytorch.org の ROCm 版の `PYTORCH_ROCM_ARCH`、TheRock の Instinct - Linux の表 |
| AMD の 2 CU の内蔵 GPU(Raphael・Granite Ridge = Ryzen 7000 / 9000 のデスクトップ、Dragon Range・Fire Range = 7x40HX / 7x45HX / 9x5xHX、Mendocino、610M = gfx1036。Radeon 820M = Ryzen AI 5 330(0.11.0)) | — | CPU | 世代は対象だが、画面を出すための小さな GPU で、生成では CPU より速い見込みが無い(判断。実測なし。上の「断る理由」) |
| AMD の gfx1170・gfx1171(RDNA 4 系の APU)・gfx1250(CDNA 5) | — | CPU | TheRock で Release Ready でない(下の「GPU の登録の表と公式の一覧」) |
| AMD RDNA 1(RX 5700 / 5500)・GCN(Vega・Polaris 以前) | — | CPU | ComfyUI の案内が RDNA 2 以降(TheRock は RDNA 1 も Release Ready にしているが、ComfyUI に合わせた) |
| AMD(どれでも) | Windows 10・版が分からない | CPU | ComfyUI の案内「Use Windows 11」(分からなければ使わない側) |
| AMD | macOS | CPU | Apple の MPS 版は Apple silicon だけ(下) |
| Intel Arc A シリーズ(Alchemist: A770 / A750 / A580 / A380 / A310、Pro A) | Windows 11・Linux | **GPU**(`xpu`) | PyTorch 2.14「Getting Started on Intel GPU」(https://docs.pytorch.org/docs/2.14/notes/get_start_xpu.html)の Client GPUs の表(「Windows 11 & Ubuntu 24.04/26.04 & WSL2」)、ComfyUI README「Intel GPUs (Windows and Linux)」 |
| Intel Arc B シリーズ(Battlemage: B580 / B570、Pro B70 / B65 / B60 / B50) | Windows 11・Linux | **GPU**(`xpu`) | 同上 |
| Intel Core Ultra の Arc Graphics(Meteor Lake-H、Arrow Lake-H の 140T / 130T、Lunar Lake の 140V / 130V、Panther Lake の B390 など) | Windows 11・Linux | **GPU**(`xpu`、共有メモリ) | 同上(Panther Lake は Ubuntu 26.04) |
| Intel Data Center GPU Max(Ponte Vecchio) | Linux | **GPU**(`xpu`) | PyTorch 2.14 の Intel Data Center GPU の表(RHEL 9.2・SLES 15 SP5・Ubuntu Server 22.04)。Windows・WSL2 は CPU |
| Intel UHD・Iris Xe・「Intel(R) Graphics」(Arrow Lake-S など、型番 ID が Arc の内蔵 GPU でないもの)・HD Graphics | — | CPU | 上の表に入らない。型番 ID が Core Ultra の Arc Graphics(Meteor Lake 7d55・Arrow Lake-H 7d51・Lunar Lake 64a0)なら「Intel(R) Graphics」でも GPU(下の「GPU の登録の表と公式の一覧」) |
| Intel(どれでも) | Windows 10・版が分からない | CPU | 上の表は Windows 11 |
| Apple silicon(M1〜M4 など) | macOS 14 以降 | **GPU**(`mps`、統合メモリ) | Apple「Accelerated PyTorch training on Mac」(https://developer.apple.com/metal/pytorch/ : 「Mac computer with Apple silicon」「macOS 14.0 or later」)、ComfyUI README「Apple Mac silicon」 |
| Apple silicon | macOS 13 以前・版が分からない | CPU | 同上 |

GPU の名前から世代を決める表:

- `gputables.AMD_NAME_RULES`(製品名と lspci の開発名 Navi 31 など)と `INTEL_XPU_RULES`。
- 名前が「AMD Radeon(TM) Graphics」だけの内蔵 GPU は、CPU の名前(`gputables.AMD_CPU_IGPU_RULES`: Ryzen AI Max → gfx1151、Ryzen 7 8845HS → gfx1103 など)で決める。決まらなければ CPU(世代が分からない)。
- Dragon Range(Ryzen 7x40HX・7x45HX)・Fire Range(9x50HX・9x55HX)は 2 CU の gfx1036 なので CPU。
- ノート用の Radeon は同じ番号でも石が違う: RX 6800M・6850M XT は gfx1031、RX 6800S は gfx1032、RX 6550M は gfx1034、RX 7700S は gfx1102。

0.9.0 で足した名前(#56。一次資料は 2026-10-22 に読んだ):

- **Ryzen Z1 Extreme・Z1**(携帯機。WMI の名前は「AMD Radeon(TM) Graphics」なので CPU の名前で決める):
  - AMD の製品ページ(https://www.amd.com/en/products/processors/handhelds/ryzen-z-series/z1-series/z1-extreme.html ・ https://www.amd.com/en/products/processors/handhelds/ryzen-z-series/z1-series/z1.html)は Graphics Core Count 12 / 4。
  - Z1 Extreme は Phoenix1(780M と同じ 12 CU)、Z1 は Zen 4 2 個 + Zen 4c 4 個の Phoenix2(740M と同じ 4 CU)とみなす(石の対応は推定。AMD は書いていない)。
  - どちらも gfx1103(Linux の amdkfd `kfd_device.c` が GC 11.0.1・11.0.4 を gfx_target_version 110003 にする)。
  - 740M(4 CU)を GPU にしているので Z1 も GPU。2 CU の 610M だけが CPU。
- **Dragon Range の 7940HX・7840HX**(型番の下 2 桁が 40):
  - AMD の製品ページ(例 https://www.amd.com/en/products/processors/laptop/ryzen/7000-series/amd-ryzen-9-7940hx.html)で Radeon 610M・2 CU。
  - 以前は名前の表が 45HX だけで「gfx が分からない」理由になっていた。理由の文は「2 CU の小さな内蔵 GPU」にした。
- **デスクトップの RX 6300**:
  - pci.ids の「7424 Navi 24 [Radeon RX 6300]」で Navi 24(gfx1034)。TheRock の SUPPORTED_GPUS.md は gfx1034 を Release Ready にしている。
  - 2 GB の VRAM なので、動くモデルはほぼ無い(判定は VRAM の量に任せる)。
- **Linux で pci.ids が古いとき**:
  - lspci -nn は「Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:7551]」と出す(pciutils `lib/names.c` の format_name_pair)。
  - 名前を「AMD Device 7551」として残し、`hardware.PCI_DEVICE_NAMES`(`gputables.PCI_DEVICE_RULES`)で石・製品の名前に引き直す。この表は pci.ids 2026.10.22(pciutils/pciids 86e7077e4b79。2026-10-27 に 2026.10.27 で確かめ直した)から、判定に関わる AMD・Intel の ID だけを写したもの。
  - 理由に「型番の ID から … とみなした(update-pciids で新しくすると製品名が出る)」と書く。
  - 表に無い ID は `update-pciids` の案内を付けて CPU(AMD・Intel とも)。
  - 型番の ID は製品の種類だけを表し、個体・差し場所は表さないので名前に残す。
- **Linux の Intel の外付け GPU の VRAM**:
  - xe・i915 のドライバは、amdgpu の `mem_info_vram_total` に当たる sysfs を出さない(torvalds/linux の `drivers/gpu/drm/xe/xe_tile_sysfs.c`・`xe_device_sysfs.c`・`i915_sysfs.c`・Documentation/ABI を読んだ。量は DRM の ioctl か debugfs(root)でだけ読める)。
  - そこで名前の表の**公称**の量を使い、理由に「名前から公称の … GB(量の違う版があれば少ないほう)」と書く(`Adapter.vram_source: "name"`)。
  - ノート用の公称は Intel の表(https://www.intel.com/content/www/us/en/support/articles/000091109.html): A770M 16 GB・A730M 12 GB・A550M 8 GB・A370M 4 GB・A350M 4 GB。
  - A770 は 8 GB 版と 16 GB 版があるので 8 GB。
  - A570M・A530M・Pro の M は Intel の資料で確かめられなかったので値を持たず、Linux では CPU のまま。

### GPU の登録の表と公式の一覧(0.11.0、#103)

登録が要るのは、OS が世代を教えないものだけ: Windows の AMD(名前 → gfx)、Intel(XPU の対象)、NVIDIA の古いドライバ(名前 → 世代。抜けても断らない)、PyTorch の CUDA 版の表(版ごと)。NVIDIA(nvidia-smi が世代を返す)・Linux の AMD(amdkfd)・Apple(チップ名の形)は登録が要らない。

- 表は `src/genmodelfit/gputables.py` にまとめ、1 行を `Rule`(鍵・値・出典・確かめた日・足した版・注意)にした。出典の URL と読んだ日は `SOURCES` に 1 回だけ書く。
- 全部の行と、当たる公式の製品名・判定・出典は [gpu-inventory.ja.md](gpu-inventory.ja.md)(`tools/gpu_inventory.py` が作る。試験が一致を見る)。
- 公式の一覧(2026-10-27 に取った)は `tests/fixtures/gpu_official` に固定し、「公式の製品はどれかの行に当たり、gfx・compute capability が一致する」を試験にした(`tests/test_gpu_tables.py`。ネットワークに触らない)。
- 新しい GPU は毎週の更新の「GPU 表の見張り」が知らせる([release.ja.md](release.ja.md)「毎週の一覧の更新」)。表は自動で書き換えない。

**AMD の Windows の対象**(出典を両方書く):

- AMD 公式の Windows の互換表(ROCm 7.2.1。Radeon: https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html 、Ryzen: …/compatibilityryz/windows/windows_compatibility.html)は gfx1100・1101・1200・1201(Radeon)と gfx1150・1151(Ryzen)だけ。
- genmodelfit は ComfyUI の案内(RDNA 2〜4)と TheRock の `SUPPORTED_GPUS.md` の Windows の表の **Release Ready**(RDNA 2・gfx1102・gfx1103・gfx1152・gfx1153 も入る)に従う。ComfyUI の AMD 版 portable と AMD の multi-arch 版 PyTorch は TheRock の版なので、こちらに合わせた。AMD 公式の表に無い gfx は、AMD の公式の保証の外にある。
- TheRock で Release Ready でない gfx(gfx1170・gfx1171 は Build Passing だけ、gfx1250 は Sanity Tested まで)は CPU(`gputables.AMD_UNVERIFIED`)。TheRock は「Sanity Tested が付くまで、pip で入っても動くとは限らない」と書いている。Release Ready になれば見張りが知らせるので外す。

**#103 で直した抜けと誤り**(出典は gpu-inventory の各行):

- AMD: Radeon 8065S(Ryzen AI Max+ PRO 495、ROCm の hw specs で gfx1151)、RX 7500 / 7400 / 7300・PRO W7400(pci.ids の 749f・7499「Navi 33」= gfx1102)、Steam Machine(pci.ids 7481「Navi 33」)、Radeon Pro W6900X・Pro V520(gfx1030・gfx1011)、Instinct MI25・MI6・MI8(対象外の世代。表示のため)、Ryzen AI 4xx・PRO の CPU 名(内蔵 GPU の名前が汎用のとき)、Ryzen 9 270(TheRock の RELEASES.md で gfx1103)。
- 新しい pci.ids で製品名が汎用(「Navi 33 [Radeon Graphics]」「Battlemage G31 [Intel Graphics]」)になる型番 ID は、古い pci.ids では使えて新しい pci.ids では断る、という逆転があった。amdkfd が無いときは、汎用の名前でも型番 ID の表を先に引くようにした(理由に「製品名が汎用なので、型番の ID から … とみなした」)。
- Intel: Arc Pro B65 / B70(Battlemage G31、e222・e223。公称の VRAM は確かめられなかったので持たない)、Data Center GPU Max(Ponte Vecchio。PyTorch の表で Linux だけなので、Windows・WSL2 は CPU)。
- **Windows の「Intel(R) Graphics」と Core Ultra の Arc**: Intel はメモリが 1 チャンネルのとき、同じ石の Arc Graphics をデバイス マネージャーで「Intel Graphics」と表示する(Intel support 000102144「Intel Arc Graphics Appears As Intel Graphics in Device Manager and Task Manager」https://www.intel.com/content/www/us/en/support/articles/000102144/graphics.html 。解決は 2 チャンネルのメモリ。2026-10-27 に読んだ)。汎用の名前のときに型番 ID の表を引く決まりは Windows の WMI にも効くので、型番 ID が Meteor Lake 7d55・Arrow Lake-H 7d51・Lunar Lake 64a0 なら「Intel(R) Graphics」でも XPU として使う(Linux は前から型番 ID で使っていた)。理由に「1 チャンネルでは 2 チャンネルより遅い」と書く。型番 ID が表に無い「Intel(R) Graphics」(Arrow Lake-S など)は今までどおり CPU。
- **820M の gfx**: TheRock の RELEASES.md は gfx1153、ROCm の hw specs は Ryzen AI 5 330 の Radeon 820M を gfx1152、pci.ids は別の石(1902「Krackan2 [Radeon 820M / 840M]」)に 820M と 840M を載せる。名前だけでは決まらないので、ROCm の wheel を選ぶ TheRock に合わせて gfx1153 にし、理由に食い違いの注意を出す。どちらも RDNA 3.5。Linux は amdkfd の値が先。0.11.0(#108)から、820M だけの名前(と、CPU の名前が Ryzen AI 5 330 の「Radeon 820M / 840M」)は 2 CU として CPU と判定する(上の「断る理由」)。
- **Jetson Orin(8.7)と CUDA 13.2 以降**: PyTorch v2.14.1 は CUDA 13.2 以降のビルドで 8.0 / 8.6 の機械語を 8.7 でも動くとする(`DEVICE_REQUIREMENT_POST_JETSON_SBSA_UNIFICATION`)。genmodelfit は Jetson を判定に使わない(JetPack 専用の版・統合メモリ。下の「すべての環境」)ので、表(`SOLO_CC`)は変えない。
- cu129 の版の範囲: Windows の wheel は 2.8・2.9 だけ(2.10 以降は Linux だけ)。cu128 の aarch64 は 2.7・2.9〜2.11(2.8 は無い)。案内の文言だけで、判定は変わらない。
- 古いドライバで世代が分からないとき(compute_cap を返さず名前の表にも無い)、「ドライバを更新すれば GPU で動く」と言い切っていた。Kepler・Fermi は新しいドライバも PyTorch も無いので、「Maxwell 以降なら更新すれば動く。Kepler・Fermi 以前は更新しても動かない」と書く。

### 精度(ComfyUI 426f5ee の `comfy/model_management.py`)

- AMD(`torch.version.hip`):
  - `should_use_fp16` は常に True。
  - `should_use_bf16` は RDNA 2 以前(`AMD_RDNA2_AND_OLDER_ARCH`)で False。ただし `manual_cast=True`(fp16 を持たない系統の計算の精度を選ぶとき)は True を返す。
  - そこで RDNA 2 では、fp16 を持たない系統でも作業用メモリを 2 倍にしない(VAE は fp32 で 2 倍)。
- Intel(XPU): `should_use_fp16` は `has_fp16`、`should_use_bf16` は `torch.xpu.is_bf16_supported()`。対象の Arc はどちらも持つ。
- Apple(MPS): `should_use_fp16` は True、`should_use_bf16` は macOS 14 以降で True。
- fp8 の計算(`supports_fp8_compute`)はどれも持たないとして扱う(fp8 の重みは注意だけ)。

### 共有メモリ(内蔵 GPU・APU・Apple silicon)

- **Apple silicon(`memory: unified`)**:
  - ComfyUI は MPS では搭載量の全部を GPU のメモリとして数える(`get_total_memory` が CPU と MPS で `virtual_memory_total`、`vram_state` は SHARED: model_management.py)。部品をメインメモリへ逃がす(lowvram)動きは使わない(SHARED では `lowvram_model_memory` を求めない)。
  - macOS は、GPU が性能を落とさずに使える量(Metal の `recommendedMaxWorkingSetSize`。llmfit bffd1b6 が使う値)を搭載量より小さくする。
  - そこで搭載量の **3/4**(`hardware.UNIFIED_MEMORY_SHARE`。判断で決めた値で、実測していない)を GPU のメモリとみなす。
  - GPU に載せる量 + 固定分(Python・ComfyUI、3 GB)を、搭載量とも比べる(超えたら仮想メモリで遅い = Marginal)。
- **内蔵 GPU・APU(`memory: shared`)**:
  - 使える量 = BIOS で取り分けた VRAM(Windows はレジストリの qwMemorySize、Linux は sysfs の `mem_info_vram_total`)+ メインメモリから使える分。
  - Linux の amdgpu は sysfs の `mem_info_gtt_total`(GTT)を読む。
  - 読めなければ、見えているメインメモリの **1/2**(`hardware.SHARED_MEMORY_SHARE`)とする。
  - 根拠(Linux): TTM が GPU に貸すページの上限が「total system memory の約 50%」(torvalds/linux a804c57 `drivers/gpu/drm/ttm/ttm_device.c:88〜92`。amdgpu の `gttsize` の既定は「-1 = Use value specified by TTM」: https://docs.kernel.org/gpu/amdgpu/module-parameters.html)。
  - 根拠(Windows): 共有 GPU メモリも物理メモリの約半分(タスク マネージャーの「共有 GPU メモリ」。WDDM の説明: https://learn.microsoft.com/ja-jp/archive/blogs/pigscanfly/graphics-memory-reporting-in-vista 。古い文書なので目安)。
- どちらも、逃がす先が同じメモリなので `GPU+RAM`(逃がす)は使わない(全部載せか、部品を順に載せ替えるか)。
- メインメモリの判定: GPU に載せる分もメインメモリから使う。
  - 要る量 = GPU に載せないときの量(固定分 + いちばん大きい段の部品)+ GPU に載せる量のうち BIOS で取り分けた VRAM(`Gpu.carveout_gb`。搭載量に入らない)を超える分。
  - 全部載せと順に載せ替えのそれぞれで確かめ、良いほうを取る。
  - 例: Radeon 780M・メモリ 16 GB で、GPU 8.3 GB + メインメモリ 9.8 GB のモデルは足りない。
  - 専用の VRAM の GPU があれば、内蔵 GPU より先に使う。
- メインメモリを増やすと GPU が使える量も増えるだけなので、段階は下がらない(`tests/test_vendors.py`)。

### 検出

- 既定では、次の両方に当てはまるときだけ調べる。
  - 判定に使える NVIDIA の GPU が無い(見つからない・Kepler・古いドライバ)。
  - `--vram` で上書きしていない。
- これで NVIDIA のドライバが入っていない NVIDIA の GPU も分かる。
- NVIDIA の GPU があるときは調べない(PowerShell の起動に約 1 秒かかるため)。
- `genmodelfit system --all-gpus` で必ず調べる(このときも NVIDIA が使えれば NVIDIA で判定する)。
- レジストリには取り外した GPU の項目も残る(下の突き合わせで除く)。
- Windows: `powershell.exe -NoProfile -NonInteractive -Command` で `Get-CimInstance Win32_VideoController` の Name・AdapterRAM・PNPDeviceID・ConfigManagerErrorCode・DriverVersion を JSON で取る(出力は UTF-8 に指定。時間制限 8 秒)。
  - PNPDeviceID が `PCI\` で始まらないもの(仮想の画面・リモートデスクトップの画面)は捨てる。
  - OS の版はビルド番号(22000 以上が Windows 11)。
  - `AdapterRAM` は uint32(Microsoft の Win32_VideoController の説明: https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-videocontroller)なので、4 GiB 以上は 4293918720 前後で頭打ちになる。この値以上は使わない。
  - VRAM はレジストリ `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\00xx` の `HardwareInformation.qwMemorySize`(REG_QWORD。8 GB の GPU なら 8589934592)を使う。
    - Microsoft の文書(https://learn.microsoft.com/en-us/windows-hardware/drivers/display/registering-hardware-information)にあるのは `HardwareInformation.MemorySize`(ULONG、MB と書いてあるが実際のドライバはバイトで書く。llmfit の場合分けと同じ)だけ。qwMemorySize はドライバの慣習。
    - 64 MiB 未満は単位の違いとみなして使わない。
  - レジストリの項目の突き合わせ:
    - `MatchingDeviceId` が WMI の部品の型番の ID の先頭に当たるものに当てる。
    - 無ければ、`DriverDesc` の名前(™・® を除いて比べる)が同じものに当てる。
    - 当たる項目が複数あって値が違う(取り外した同名の GPU が残っている)ときは使わない。
  - NVIDIA の部品(VEN_10DE)があり nvidia-smi で判定できなければ、名前によらず「NVIDIA のドライバが入っていない・動いていない」と知らせる(0.11.0。以前は英語の名前「Microsoft Basic Display Adapter」のときだけ)。
  - ConfigManagerErrorCode が 0 でなければ、22・28・43 は理由を分けて、ほかは「デバイスマネージャーで問題がある状態」と知らせる(下の「すべての環境」)。
- Linux: `lspci -Dnn` のクラス 03xx(VGA・3D・Display)を読む。
  - 名前は括弧の中の製品名(`Navi 31 [Radeon RX 7900 XT/7900 XTX/…]` の括弧の中)。
  - AMD は `/sys/bus/pci/devices/<slot>/mem_info_vram_total`(VRAM)と `mem_info_gtt_total`(GTT)を読む。
  - 検出では rocm-smi を使わない(rocm-smi の VRAM・GTT は同じ sysfs の値で、入っていない PC も多い)。bench の使用率の確かめでは使う([accuracy.ja.md](accuracy.ja.md)「NVIDIA 以外の GPU の bench」)。
  - Intel の外付け GPU は sysfs に VRAM の量が無いので、名前の表の公称の量を使う(上)。
  - 型番の分からない部品は「AMD Device 7551」の名前にする。
- macOS: `system_profiler SPDisplaysDataType -json` の `sppci_model`(「Apple M2 Pro」)と `spdisplays_vendor`。
  - 統合メモリの量は psutil の搭載量(macOS では `sysctl hw.memsize` と同じ値)。
  - OS の版は `platform.mac_ver()`。
- PowerShell・lspci・system_profiler が無い・失敗した・時間切れは、空のまま「画面の部品の一覧を読めなかった」と知らせる(0.11.0。Linux で lspci が無ければ sysfs を読む)。

### 表示

- 表の先頭の行:
  - 使える GPU があれば「PC: AMD Radeon RX 7900 XTX 24.0 GiB VRAM (ROCm, estimated), RAM …」。
  - Apple は「PC: Apple M2 Pro (unified memory; 24.0 GiB for the GPU, MPS, estimated), RAM …」。
  - 内蔵 GPU は「(integrated; 16.1 GiB shared with RAM, XPU, estimated)」。
  - 使える GPU が無ければ、見つけた GPU とその扱いを出す(「PC: Intel(R) UHD Graphics 770 (integrated; not usable for generation → judged as CPU), RAM 15.7 GiB …」)。次の行に「This PC has no usable GPU: only models that run on CPU are shown (N tools hidden: --include-tools)」。
- Mode の列: 動かないもののうち、使える GPU が無いから動かないものは `GPU req`、メインメモリが足りないものは `RAM`(JSON の `run_mode_label` は `-` のまま)。

### ほかのツールとの突き合わせ(2026-10-21)

表が厳しすぎ・緩すぎにならないかを、ほかのツールの対応表と比べた。
基準は「ComfyUI + PyTorch で動くか」なので、ComfyUI の README に合わせている。

- **Windows 10**: GPU を使えるとする資料は見つからなかった。
  - AMD HIP SDK・ROCm on Radeon/Ryzen・PyTorch XPU は Windows 11 だけ。Ollama・LM Studio・llmfit は記載なし。
  - Windows 10 の版と分かれば、表の先頭の行に `needs Windows 11 for generation` と出す(`Adapter.needs`)。
- **DirectML・ZLUDA・Vulkan は使わない**:
  - ComfyUI は DirectML に「barely works, is very slow, has not been updated in over 1 year」と警告し(comfy/model_management.py)、README からも外した。
  - Microsoft も DirectML を maintenance mode とした(https://github.com/microsoft/DirectML)。
  - SD.Next は DirectML を削除し、ZLUDA を「unofficial and currently limited」とした(https://vladmandic.github.io/sdnext-docs/ZLUDA/)。
  - Fooocus や stable-diffusion-webui-amdgpu は DirectML で古い AMD を動かすが、遅い(Fooocus の README で約 3 倍)。
- **AMD 公式の Windows 版 PyTorch の表より広い**:
  - ROCm on Radeon/Ryzen の Windows の表は gfx1200/1201/1100/1101 と gfx1150/1151 だけ(https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html)。
  - ComfyUI の README は RDNA 2〜4 とする。
  - TheRock(ComfyUI が使う ROCm)は RDNA 2・780M(gfx1103)も Windows でリリースの対象にしている(https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md)。
  - RX 6000 系と 780M は ComfyUI に合わせて使う(段階は同じ基準。この種類の GPU で動いた実測は、まだ無い)。
- **RDNA 1・Vega・Polaris**: llmfit は名前と VRAM が取れれば GPU 扱い、Ollama・llama.cpp は Vulkan で動かせる。ただ ComfyUI の README の対象外なので CPU。
- **2 CU の内蔵 GPU(gfx1036)**: TheRock は対象にしているが、生成で CPU より速い見込みが無いので CPU(対応の有無ではなく性能の判断。理由の文もそう書く)。
- **Van Gogh(Steam Deck、gfx1033)**:
  - 0.8.0 までは gfx1036 と一緒に「2 CU の小さな内蔵 GPU」として CPU にしていた。
  - Valve の仕様(https://www.steamdeck.com/en/tech 「GPU: 8 RDNA 2 CUs, 1.6GHz (1.6 TFlops FP32)」)で 8 CU ある。
  - TheRock の SUPPORTED_GPUS.md(2026-10-22 に読んだ main)は gfx1033 を Linux・Windows とも Release Ready にしている。ComfyUI の README は「supported architectures include RDNA 2」。
  - そこで 680M(12 CU)・660M(6 CU)と同じく GPU(共有メモリ)にした。
  - pytorch.org の ROCm 版には入っていないので TheRock の版を使う、と notes に出る。

## CPU で動く系統(0.7.0、#39)

GPU が無い(または GPU に載らない)とき、CPU で動くとみなすのは次のもの。

- これまでの「重み 2 GB 以下の画像・音声・読み上げ」。
- 加えて、`families.CPU_FAMILIES` の系統(下の表)。

| 系統 | 種類 | 根拠 | 除くもの |
|---|---|---|---|
| `sd`(SD 1.5・SD 2.x・SDXL・SDXL Turbo など) | 画像 | ComfyUI の組み込みの系統で CUDA 専用の部品を使わない(推定。CPU での実測なし) | SUPIR(独自のノード。確かめていない) |
| `stable_audio`(Stable Audio Open 1.0・Stable Audio 3 Small) | 音楽・効果音 | 組み込みの StableAudio の系統(推定。CPU での実測なし。AI生成キットの記録に「効果音(GPU/CPU)」があるが、モデル・時間の記録は無い) | Stable Audio 3 Medium(確かめていない) |

ComfyUI の CPU での動き:

- GPU が無ければ CPU で動く(`--cpu`「To use the CPU for everything (slow)」: comfy/cli_args.py)。
- CPU では本体と VAE を fp32 で持つ(`should_use_fp16`・`should_use_bf16` が CPU では False: model_management.py:1912・1979。テキストエンコーダは fp16 のまま: 1261)。

必要なメインメモリ = 固定分 + 全部品 + (2 バイトの本体なら本体をもう 1 つ分)+ VAE をもう 1 つ分 + 作業用メモリ × 2。

- 搭載量に収まれば、メインメモリの使用率の帯(60 / 85 / 98%)で決め、生成は Marginal まで(CPU、遅い)。CPU で実時間に動く設計の読み上げ・1 回の計算の道具は Good まで(0.11.0、#108。[scoring.ja.md](scoring.ja.md)「CPU で動かすとき」)。
- 仮想メモリの範囲なら Marginal(遅い)。
- 超えたら Too tight。
- CPU で動くこと自体が推定なので、理由に「CPU は推定・実測なし」と書く(AI生成キットのカタログの `cpu_ok` は設計の印で、実測ではない)。

## 同じ GPU で成功した実測(0.7.0、#26)

次の条件がすべてそろうと、段階は実測で決める(`Estimate.fit_from: measurement`。0.11.0 で条件を変えた。決まりの全体は [scoring.ja.md](scoring.ja.md)「実測で段階を決める条件」)。

- 同じ設定・標準のワークフローで成功(`outcome: ok`)した実測がある。
- その GPU が、この PC の GPU と**同じ GPU**である(`estimate.same_gpu`: 型番 ID か、そろえた名前。VRAM の比が 0.95 以上)。作者の実測もほかの利用者の実測も同じ規則(0.10 までは、作者の実測は同じ会社で VRAM が実測の GPU 以上ならどの GPU にも効いた)。
- 別々の出どころが 2 つ以上あり(作者の実測は何行あっても合わせて 1 つ、ほかの利用者はまとめの `n_sources`)、そろっている(OOM の記録が無く、値の幅が中央値の 15% 以内)。
- 段階に使う量 = max(一番大きいピーク + 文脈の分, 重み + 固定分)が、この PC の VRAM に収まる。

段階の決め方:

- 段階に使う量 ÷ この PC の VRAM の帯(60 / 85 / 98%)で決める。少なくとも Good。
- 上限に張り付いた実測(動的 VRAM が空きを使い切った)があれば Perfect の根拠にしない(Good まで)。
- `torch_reserved` の実測(bench。CUDA / HIP の文脈を含まない)は、文脈の分 0.5 GiB(`estimate.TORCH_RESERVED_CONTEXT_GB`)を足した値で帯を決め、Perfect の根拠にもする(0.9.0)。以前は Perfect にしなかったので、bench の提出しか無い NVIDIA 以外の GPU は実測があっても Good 止まりだった。

実測の GPU の会社の決め方:

- 実測の `gpu_backend` があればそれで決める。
- 無いときだけ GPU 名で決める(Apple の MPS は GPU 名が "mps" になる)。

ほかの利用者の実測(#34)の使い方:

- この PC と同じ GPU のものだけ使う(型番 ID か、そろえた名前。汎用の名前は除く。`estimate.same_gpu`)。2026-10-22 に決めた。似た GPU には広げない。0.9.0 から、必要量にも同じ GPU のものだけ。
- さらに、実測の GPU の VRAM とこの PC の総量の比(小さい ÷ 大きい)が 0.95 以上のときだけ使う(0.9.1。0.11.0 で 0.97 から 0.95 に。Linux の CUDA は 6 GB のカードを 5.80 GiB と報告し、Windows の 6.0 との比が 0.967 になるため。`estimate.same_vram`)。同じ名前でも VRAM の違う版がある(RTX 4060 Ti の 8 / 16 GB など)。内蔵 GPU・APU・Apple は比べない。

そのほか:

- 精度の直しで作業用メモリを倍にした GPU では使わない。
- メインメモリの判定はそのまま行う。
- 理由の先頭に「この PC と同じ GPU(…)・同じ設定で、別々の N つの出どころの実測がそろって最後まで動いた …(段階は実測で決めた)」と書く。表の VRAM は段階に使った量(m)。
- 実測は見積もりより良いときだけ段階に使う(下げない)。NVIDIA 以外の GPU も同じ(0.11.0 で、実測で Perfect を Good に下げる分岐を外した)。
- 実測が別の GPU のもの・出どころが 1 つだけ・そろっていないときは「段階は見積もりで決めた(…)」と理由を書く。
- 例: Wan 2.2 5B(作者の RTX 5080 16 GB の実測 15.8 / 15.0 GB の 2 行。合わせて 1 つの出どころで、動的 VRAM で重みの一部を逃がして動いた記録)は、640x640・73 コマを指定しても、重み + 固定分(約 18.4 GB)が 15.9 GB に収まらないので、0.11.0 から段階は見積もり(今の段ごとの式では部品を順に載せ替えて Good / GPU~。0.7.0〜0.10 は Good / GPU)。RTX 3060 12 GB では逃がして Good / offload。

## すべての環境(0.11.0、#101)

検出は、どの環境でも例外を出さずに終わり、分からないときは断らず、何が分からないかを notes に書く。
環境 41 通り(外のコマンド・レジストリ・WMI・lspci・sysfs・system_profiler の出力を差し替えて再現したもの)を洗い出して直した。
出典は 2026-10-27 に読んだ。

### 環境の一覧

| 環境 | genmodelfit がすること | 出典(引いた節) |
|---|---|---|
| psutil がメモリを読めない | Windows は `GlobalMemoryStatusEx`、Linux は `/proc/meminfo`、macOS は `sysctl -n hw.memsize` で読む。どれも読めなければ量は「分からない」(`Memory.total` が None。出力の `ram_total_gb` は 0 = 不明)とし、「--ram で指定する」と notes に書く。見積もりは 0 を「分からない」として扱い、メインメモリでは断らない。内蔵 GPU・APU・Apple は、メインメモリの分が分からないので、取り分けた VRAM・GTT が読めなければ理由を書いて CPU で判定する | psutil の失敗は OS ごとに違う(実装の判断) |
| MemAvailable の無い古いカーネル | 空き = MemFree + Cached | Linux `Documentation/filesystems/proc.rst`「MemAvailable」 |
| コンテナ(cgroup v2・v1) | `/proc/self/cgroup` の自分の cgroup(v2 は `0::/path`、v1 は `N:memory:/path`)から根へたどり、上限(`memory.max`・`memory.limit_in_bytes`)の一番小さいものを搭載量に、空きは 上限 − 使用量(`memory.current`・`memory.usage_in_bytes`)。`max` は上限なし。名前空間の中ではパスが `/` なので根だけを読む | https://docs.kernel.org/admin-guide/cgroup-v2.html 「memory.max」「memory.current」、https://docs.kernel.org/admin-guide/cgroup-v1/memory.html 「memory.limit_in_bytes」 |
| PyTorch の表より新しい compute capability(13.0 など) | 断らない(`cuda` のまま)。「表より新しい世代。最新の PyTorch を使う」と注意。断るのは表より古い世代だけ | 「世代が分からないときは断らない」決まりと同じ扱い |
| Linux の aarch64 / ppc64le / riscv64 など | `_machine()` は知らない種類を x86_64 にしない。表(`TORCH_CUDA_BUILDS` の鍵)に無ければ「この機械向けの PyTorch の CUDA 版は無い」で CPU | 上の「PyTorch の CUDA 版のビルドが対応する compute capability」 |
| Windows の Arm64 + NVIDIA | 表は (OS, 機械) で引く(`torch_platform`)。Windows の Arm64 向けの CUDA 版は無いので CPU | https://download.pytorch.org/whl/ の各 cu の索引(`win_arm64` の CUDA 版が無い) |
| 32 ビットの Python | 注意だけ(「PyTorch は 64 ビットの Python にしか無い」) | https://download.pytorch.org/whl/ の各 cu の索引(32 ビットの win32・i686 の wheel が無い) |
| MIG が有効(`mig.mode.current` が Enabled) | nvidia-smi の VRAM は GPU 全体なので、「区画の大きさを --vram で指定する」と注意(値は変えない。実機で確かめていない) | NVIDIA「Multi-Instance GPU User Guide」 https://docs.nvidia.com/datacenter/tesla/mig-user-guide/ (区画ごとにメモリが分かれる) |
| `mig.mode.current` を知らない nvidia-smi | エラーの文がその項目を名指ししたときだけ、その項目を外して聞き直す(`QUERY_CC`)。compute_cap も知らなければ今までどおり `QUERY_OLD` | 知らない項目が 1 つあると問い合わせ全体が失敗する(この PC で存在しない項目を聞いて確かめた) |
| DGX Spark(GB10) | 下の「DGX Spark(GB10)」 | 同節 |
| Jetson(Orin・Thor) | 対象外(CPU)。「Jetson は PyTorch が JetPack 専用の版(jp6 の cu126 など)で表の CUDA 版と別系統、メモリの扱いも確かめていない」と書く。この Issue の範囲外 | Jetson AI Lab の ComfyUI の手順 https://www.jetson-ai-lab.com/tutorial_comfyui_flux.html (別の Issue で扱う) |
| GH200 / GB200 | この Issue の範囲外(nvidia-smi が返す HBM の量でこれまでどおり判定する) | — |
| ドライバの無い NVIDIA(Windows の「Microsoft 基本ディスプレイ アダプター」、Linux の nouveau) | 名前(言語で変わる)に頼らず、型番の会社 ID が NVIDIA(`10de`)の部品で「nvidia-smi で確かめられないので CPU。NVIDIA のドライバを入れる」と知らせる | — |
| デバイス マネージャーのエラー(WMI の ConfigManagerErrorCode) | 22 は「無効にしてある」、28 は「ドライバが入っていない」、43 は「問題を報告して Windows が止めた」、ほかは「問題がある状態」。22・28・43 の AMD・Intel の部品は、この理由で判定に使わない(WSL2 から読んだ部品も同じ。`Adapter.config_error`) | Microsoft「Device Manager error messages」 https://learn.microsoft.com/en-us/windows-hardware/drivers/install/device-manager-error-messages (Code 22・28・43) |
| PowerShell・lspci・system_profiler が無い・失敗・時間切れ | 「画面の部品の一覧を読めなかった」と注意(以前は黙っていた) | — |
| lspci が無い Linux(最小のコンテナ・Flatpak・Snap など) | `/sys/bus/pci/devices/*/{class,vendor,device}` を読み(class が 0x03xxxx のもの)、名前は「AMD Device 744c」の形にして型番 ID の表(`PCI_DEVICE_NAMES`)で引く。差し場所は形を確かめて読むのに使うだけで、残さない・出さない。読めないファイル(SELinux など)は None | https://docs.kernel.org/PCI/sysfs-pci.html 「Accessing PCI device resources through sysfs」 |
| 仮想マシン・管理用(BMC)の画面(QEMU `1234`・virtio `1af4`・VMware `15ad`・VirtualBox `80ee`・Hyper-V `1414`・Cirrus `1013`・QEMU(Red Hat)`1b36`・ASPEED `1a03`・Matrox `102b`) | 「計算に使う GPU ではない(仮想マシン・管理用の画面)」で CPU。型番 ID の読み替え(pci.ids が古いときの案内)は AMD・Intel だけ | pci.ids の会社名(pciutils/pciids) |
| Linux の AMD(amdgpu) | amdkfd の `/sys/class/kfd/kfd/topology/nodes/*/properties` の `gfx_target_version`(10 進で 主×10000 + 下位×100 + stepping、stepping は 16 進 1 文字: 110000 → gfx1100、90010 → gfx90a。0 は CPU のノード)を、ノードの `vendor_id`・`device_id` で部品に当て、名前より先に使う。表に無い gfx10.3 以降は「新しい世代(推定)」として断らない | torvalds/linux `drivers/gpu/drm/amd/amdkfd/kfd_topology.c`(`gfx_target_version`)・`kfd_device.c` |
| Windows の表に無い新しい AMD・Intel(Arc) | CPU のまま、理由に「名前の表に無い GPU(新しい GPU?)。--vram で VRAM を指定すれば判定できる。名前の表は毎週の更新で足す」 | — |
| macOS の Apple 以外の GPU(Intel Mac の AMD・Intel) | 世代を引く前に「macOS の Apple 以外の GPU は PyTorch の MPS 版の対象ではない」 | Apple「Accelerated PyTorch training on Mac」 https://developer.apple.com/metal/pytorch/ (「Mac computer with Apple silicon」「macOS 14.0 or later」)、ComfyUI README「Apple Mac silicon」 |
| macOS の仮想マシン(「Apple Paravirtual device」) | Apple のチップ名(`APPLE_CHIP_NAME`)の形でない Apple の画面は CPU | PyTorch #111576「[MPS] Skip virtualized devices」 https://github.com/pytorch/pytorch/pull/111576 (この部品では最初の確保が失敗する) |
| macOS の版が 10.16 と出る(古い SDK で作った Python) | `SYSTEM_VERSION_COMPAT=0 sw_vers -productVersion` で読み直す。読み直せなければ版は分からない(MPS は使わない側) | pypa/packaging #513 https://github.com/pypa/packaging/pull/513 (「SYSTEM_VERSION_COMPAT=0」で sw_vers を読む) |
| WSL2 の AMD・Intel | 下の「WSL2 の AMD・Intel」 | 同節 |
| 時間切れのコマンド | POSIX はプロセスの組(`start_new_session`)ごと止め、さらに 1 秒だけ待って返す。Windows は下の「既知の限界」 | Python `subprocess.Popen`「start_new_session」 |

### DGX Spark(GB10)

- 判定に使う条件(すべて): nvidia-smi の名前が `NVIDIA GB10` と**完全に一致**(部分一致・GB20・nvgpu は入れない)、memory.total が `[N/A]` / `Not Supported`、Linux の aarch64。compute_cap 12.1 は補強で、無くても外さない。ASUS GX10・Dell・MSI などの GB10 機も同じ名前で拾える。
- メモリ(`memory: shared`):
  - 総量は `/proc/meminfo` の MemTotal(段階は総量で決める決まり)。割り引かない(Apple の 3/4 は Metal の目安の代わりで、GB10 に当てる根拠が無い)。メインメモリと共有で、二重に数えない(`carveout_gb` 0。逃がす動かし方は無い)。
  - 空きは NVIDIA の known-issues の例どおり MemAvailable + SwapFree(HugePages を使っていれば空きの HugePages × 大きさ、swap は数えない)で、今の空きの注意(`vram_free_short`)だけに使う。
- 段階は NVIDIA の専用 VRAM の GPU と同じ基準で決める(0.11.0、#108。上限なし。Perfect は速さの保証ではなく、帯域の狭い DGX Spark でも使用率で決める)。この種類(`nvidia/shared`)の実測は同梱の一覧に無いので、✓ 確認済みの種類は付かない。理由に「GB10 は統合メモリ。GPU だけの上限は NVIDIA の文書に無い」と書く。
- PyTorch の表: compute capability 12.1 は今の `_code_runs`(同じ主番号で下位が小さい機械語は動く)で sm_120 の機械語が動くと判定する(新しい規則は要らない)。cu128 の aarch64(PyTorch 2.7〜2.11)を表に足した。
- 注意: 「sm_121 では CUTLASS・xformers の fmha のカーネルが失敗した報告があり、直ったかは確かめていない」。
- 名前が GB10 でない、VRAM を返さない NVIDIA の GPU は、理由(Jetson は上の表、ほかは「共有メモリの NVIDIA の GPU は ComfyUI の公式の手順を確かめた GB10 だけ」)を書いて CPU。

出典:

- NVIDIA「ComfyUI on DGX Spark」 https://github.com/NVIDIA/dgx-spark-playbooks (`nvidia/comfy-ui`。cu130 の PyTorch、ComfyUI v0.28.2)
- PyTorch のフォーラム(NVIDIA の ptrblck) https://discuss.pytorch.org/t/223744 「CUDA 12.8 以上の ARM のバイナリは DGX Spark に対応、sm_121 は sm_120 とバイナリ互換」
- NVIDIA DGX Spark「Known Issues」 https://docs.nvidia.com/dgx/dgx-spark/known-issues.html 「nvidia-smi reports "Memory-Usage: Not Supported"」(「iGPUs do not have dedicated framebuffer memory」)と「Guidance for reporting memory resources with unified memory architecture」(「the cudaMemGetInfo API does not account for memory that could potentially be reclaimed from SWAP」。例は MemAvailable・SwapFree・HugePages_Total を読む)
- PyTorch の cu128 の aarch64: v2.11.0 `.ci/manywheel/build_cuda.sh`(12.8 は `7.5;8.0;8.6;9.0;10.0;12.0`、aarch64 は 8.0 未満と 8.6 を除く)、https://download.pytorch.org/whl/cu128/torch/ (2.7.0〜2.11.0 の `manylinux_2_28_aarch64`)
- ComfyUI は統合 GPU を通常の CUDA デバイスとして扱い、pin だけ無効にする(`comfy/model_management.py` の `is_integrated_gpu`)

### WSL2 の AMD・Intel

3 段で判定する(NVIDIA は WSL でも nvidia-smi で今までどおり):

1. **WSL2 か**: `/dev/dxg`(GPU の準仮想化の口)があり、カーネルの版に `microsoft` がある。続けて、相互運用が使えるかを `/proc/sys/fs/binfmt_misc/WSLInterop`(または `WSLInterop-late`)の有無で確かめる(プロセスを起こさない)。`/.dockerenv` か `/run/.containerenv` があれば「WSL の上のコンテナ。Windows 側の GPU は読めない」。
2. **Windows 側の GPU を読む**: 相互運用で `powershell.exe`(PATH に無ければ `/etc/wsl.conf` の `[automount] root`(既定 `/mnt/`)の下の `c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`)を `-NoProfile -NonInteractive`・時間切れ 8 秒で呼ぶ。台本(`hardware._PS_WSL`)は WMI の Name・AdapterRAM・PNPDeviceID・ConfigManagerErrorCode・DriverVersion と、レジストリの画面の部品のクラスキー(`0###`)の DriverDesc・MatchingDeviceId・`HardwareInformation.qwMemorySize`・`MemorySize`(REG_BINARY はバイト列から変換)だけを読み、UTF-8 の JSON で返す。PNPDeviceID は Windows と同じく後半(差し場所)を捨てる。失敗(相互運用が無効・時間切れ)は例外にせず注意にする。
3. **WSL の中の準備**: AMD は `rocminfo`(PATH か `/opt/rocm/bin`)が通り、GPU のエージェントの `Name: gfx…` が読めること(一番強い証拠)。読むのは行の頭で照合した `Name: gfx…`・`Marketing Name:`・`Device Type:` だけで、UUID などほかの行は読まない・notes・ログ・例外に出さない。Intel は `clinfo -l` に Intel の GPU の Device が出ること。

使う条件(段階は NVIDIA と同じ基準。この種類の GPU で動いた実測は、まだ無い):

- AMD: AMD の WSL の対応表(ROCm 7.2.1)の製品名と完全に一致するもの(`hardware.WSL_AMD_SUPPORTED`: RX 9070 / 9070 XT / 9070 GRE、AI PRO R9700 / R9600D、RX 9060 / 9060 XT、RX 7900 XTX / XT / GRE、PRO W7900(Dual Slot)/ W7800(48GB)/ W7700、RX 7800 XT、RX 7700 / 7700 XT)。APU は公式の表に無い(librocdxg の README だけで、リポジトリは deprecated)ので使わない。
- Intel: Arc A・B、Core Ultra の Arc Graphics(`gputables.INTEL_XPU_RULES`)で、Windows のドライバが 32.0.101.8801 以上(3 つ目と 4 つ目の欄をつないだ 7 桁で比べる。`int(f"{p3}{p4:04d}") >= 1018801`)。版が読めなければ断らず注意。内蔵の Arc Graphics は WSL のメモリ(Windows の搭載量以下)の半分を共有の分とみなす(少なめの見積もり)。
- そろわないときは CPU として、何が足りないかを書く(例「Windows 側に AMD Radeon RX 7900 XTX があるが、WSL の中の rocminfo で GPU が見えない。Windows に AMD Software: Adrenalin Edition for WSL2 を、WSL の中に ROCm の WSL 版を入れる」「AMD の WSL の対応表(ROCm 7.2.1)に無い」)。
- VRAM は Windows 側のレジストリの量(WMI の AdapterRAM は 4 GB で頭打ち)。WSL の中では空きが約 3 GB 少ないという報告(二次)があるので注意に書く(値は変えない)。
- WSL2 の lspci・sysfs に見える Microsoft の部品(`1414:008e` Basic Render Driver)は計算の GPU ではないので一覧から外す。
- 使える NVIDIA の GPU があるときは、`--all-gpus` でも Windows 側(powershell.exe・rocminfo・clinfo)は読まない。
- 使わないもの: amd-smi(WSL では機能が限られ、ROCm CLI でも GPU 無しと出る)、xpu-smi(WSL での動きを確かめていない)、`HSA_OVERRIDE_GFX_VERSION`(非公式)。

出典:

- AMD「WSL compatibility」(ROCm 7.2.1) https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/wsl/wsl_compatibility.html 「Supported AMD Radeon™ Hardware」(Adrenalin Edition 26.1.1 for WSL2)、https://github.com/ROCm/librocdxg
- Intel「PyTorch Prerequisites for Intel GPUs」2.14 https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu/2-14.html 「Install for WSL2」(「The /dev/dxg must exist, and clinfo -l must list an Intel platform with at least one device」)と「Install for Windows」(「version 32.0.101.8801 … or higher」)
- Intel「Understanding the Intel Graphics Driver Version Number」 https://www.intel.com/content/www/us/en/support/articles/000005654/graphics.html (「The seven digit build number is created by combining the numbers from the last two sections」)
- Microsoft「wsl.conf」 https://learn.microsoft.com/en-us/windows/wsl/wsl-config 「[automount] root」「[interop] enabled」

### GPU 名の形

ほかの利用者の提出(contrib)の GPU 名は、名前の表(#90 の `nvidia_name_known`)でなく形で見る(`hardware.nvidia_name_well_formed`)。

- 断る(`gpu_known: false`。記録として残し、判定には使わない)のは、名前の代わりの値(空・`n/a`・`none`・`null`・`nil`・`unknown`・`-`、nvidia-smi の `[Not Supported]`・`[Unknown Error]`・`ERR!`)と汎用の名前(`NVIDIA Graphics Device`、型番 ID だけの `NVIDIA Corporation Device 2684` など。`estimate.GENERIC_GPU_KEYS`・`generic_gpu_name`)だけ。llmfit も名前は大文字小文字を無視した完全一致で比べ、断るのは代わりの値だけ(llmfit `share.rs`・`benchmarks.rs` の `hardware_payload_matches`)。
- 照合(`estimate.same_gpu`)はもともと、そろえた名前の完全一致と VRAM の比 0.95 以上を求める。作った名前・知らない名前は、同じ文字列を返す PC にしか当たらない。∴ 名前の表は照合の安全をほとんど増やさず、逆に実在の名前(TITAN X (Pascal)・vGPU・A800 40GB Active・CMP 30HX など)を断っていた。
- 形(大文字小文字を区別しない。語は 10 個まで): `NAME := [NVIDIA] BRAND* CORE SUFFIX*`
  - BRAND: GeForce, Quadro, Tesla, GRID, TITAN, RTX, PRO, GTX, GTS, GT, MX, CMP, NVS, FX, DGX
  - CORE はちょうど 1 つ: `PREFIX? \d{1,4}[A-Z]{0,3}` に `(-(SXM\d?|PCIE|DGXS|FHHL|Ada|\d{1,3}GB|\d{1,3}[ABCQE]|\d{1,3}))*` を許す。PREFIX は NVIDIA の系列の頭だけ(A・B・H・L・T・P・V・K・M・MX・GB・GH・GA・GP・GV・GK・GM、vGPU の RTX6000-24Q の RTX)。「TY1985」「KEN1990」「tom99」は型番にならない。TITAN の直後だけ `V|X|Xp|Z|Black|RTX` も。型番の語の無い初代の「GeForce GTX TITAN」も通す
  - SUFFIX: Ti, SUPER, D, v2, Ultra, GTX, GTS, GT, GX2, OEM, Laptop, GPU, Max-Q, with, Design, Workstation, Server, Edition, Generation, Ada, Blackwell, Embedded, SFF, Active, PCIe, NVL, `SXM\d?`, `HBM\d+e?`, `\d{1,4}(GB|MB)`、括弧の世代名(`(Pascal)` など)、A, AC。数字だけの語(「RTX 4090 A 1985」の 1985)と同じ語の繰り返し(「Ti Ti」「8GB 8GB」)は通さない(v2 の「数字だけの語は 1 個まで」から締めた。確認の指摘)
- 形に合わない名前は誤りではなく確認 `gpu_name_form`(メンテナが確かめる。人名などは本人が `contrib show` で見る。今の個人の情報の点検が受け止める)。形に合うが表(`_NAME_CC`)に無い名前は確認 `unknown_gpu`(判定には使う)。新しい系列の語は語の表に 1 語足す(製品ごとではない)。
- 名前の公称の VRAM(`estimate.NVIDIA_NOMINAL_VRAM`)に合わない量は誤りから確認 `gpu_vram_nominal` に下げた(改造された 4090 48 GB などがあるため。別の VRAM の PC に混ざらないことは VRAM の比 0.95 が守る)。
- `_NAME_CC`(名前 → compute capability)は古いドライバのときの世代の推定と、提出の compute capability の食い違いの点検だけに使う。
- 提出の名前の文字に、括弧の世代名(`(Pascal)` など、世代の名前だけ)を許した。
- 試験: 実在の名前(実在の GPU の一覧から集めたもの。Tesla V100-DGXS-32GB・P106-100・RTX6000-Ada-48Q・MX450 など)は通り、作った名前(電話番号・イニシャル + 年・名前 + 数字・「ABC123 XYZ99」)は通らない(`tests/test_contrib_robust.py`)。一覧のうち `NVIDIA Graphics Device`(汎用)・`Orin (nvgpu)`・`NVIDIA Thor`(Jetson の語は BRAND に無い)・人名入りの名前は通らない。

### 既知の限界

- Windows で時間切れのコマンドは子だけを止める(孫のプロセスは残りうる)。読む口は閉じず(閉じると孫が終わるまで止まる)、1 秒だけ待って返す。
- WSL の中の VRAM の空き(約 3 GB 少ない報告)は値に入れていない。
- MIG の区画の大きさは自動では読まない(`--vram` で指定する)。
- GB10 はこの種類の GPU で動いた実測がまだ無い(✓ 確認済みの種類は付かない)。GPU だけで使える上限の数字は NVIDIA の文書に無い。
- Jetson・GH200 / GB200 の統合メモリは扱わない(別の Issue)。
- WSL2 の内蔵 GPU(Intel の Arc Graphics)の共有の分は、WSL のメモリから少なめに見積もる。

## メモリと仮想メモリ

- 搭載量・空きは psutil。
  - Linux でコンテナ(cgroup v2 の `memory.max`、v1 の `memory.limit_in_bytes`)の上限が搭載量より小さければ、上限を搭載量として使う。0.11.0 から、自分の cgroup(`/proc/self/cgroup`)から根へたどって一番小さい上限を使い、空きは 上限 − 使用量(下の「すべての環境」)。
  - psutil が失敗したら OS の読み方(GlobalMemoryStatusEx・/proc/meminfo・sysctl)で読み、読めなければ「--ram で指定する」と書く。
  - WSL の中では WSL に割り当てた分(`.wslconfig`)になる(そう書く)。
- Windows のコミット:
  - `GlobalMemoryStatusEx` の `ullTotalPageFile` は「今のコミットの上限(システムかこのプロセスの小さいほう)」、`ullAvailPageFile` はその空き(https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex)。
  - `commit_limit_gb`・`commit_used_gb` に出す。
  - 今のページファイルの大きさは 上限 − 物理メモリ(`swap_total_gb`)。
- ページファイルの設定: レジストリ `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management` の `PagingFiles`(REG_MULTI_SZ)。
  - `?:\pagefile.sys` はすべてのドライブで自動。
  - `C:\pagefile.sys 0 0`・大きさの無い行はシステム管理。
  - `C:\pagefile.sys 1024 4096` は最小・最大 MB。
  - 空はページファイル無し。
  - この書式は Microsoft の文書では確かめられず、実機の値と一般に知られた書式による。
  - システム管理のページファイルは、最大で「RAM の 3 倍か 4 GB の大きいほう(ボリュームの 1/8 まで)」まで伸びる(https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows)。
- Linux のスワップ: `/proc/swaps` の合計。zram(メモリを圧縮して使うもの)は ComfyUI の `get_disk_swap_total` と同じく数えない。

判定(`estimate.ram_limit`): メインメモリが搭載量より多く要るとき、

| 仮想メモリ | 動くとみなす上限 | 段階 |
|---|---|---|
| 大きさが決まっている(固定のページファイル・無し・Linux のスワップ) | 搭載量 + 最大の大きさ(ただし搭載量 × 1.5 まで) | 上限まで Marginal、超えたら Too tight |
| システム管理で伸びる・分からない・`--ram` で上書きした | 搭載量 × 1.5(これまでどおり) | 同上 |

- 1.5 倍は「重みをページファイルに置くと極端に遅い」ための判断の値で、実測していない([accuracy.ja.md](accuracy.ja.md))。
- ページファイルの大きさは搭載量に比べて増えるだけなので、搭載量を増やして段階が下がることはない(`test_fit_monotonic_in_ram_with_fixed_pagefile`)。

## 画面などが使う VRAM(今の空き)

- nvidia-smi の `memory.used`・`memory.free` を `vram_used_gb`・`vram_free_gb` に出す(画面・ブラウザなどのほかのプログラムも VRAM を使う)。
- **段階は総量で決める**(使う VRAM はほかのアプリを閉じれば空くため)。
  - 判定した動かし方で要る VRAM が今の空きより多いときは、見積もりの `vram_free_short: true` と「今の空き VRAM は … ほかのアプリを閉じれば動く」の理由で知らせる。
- 目安の予約: ComfyUI は Windows で 600 MB(VRAM が 15 GB を超える GPU は 700 MB)、それ以外で 400 MB を、ほかのアプリのために空けておく(`EXTRA_RESERVED_VRAM`)。
  - 見積もりの固定分(`overhead_gb`、実測で合わせた値)がこれと CUDA の文脈などを含む([accuracy.ja.md](accuracy.ja.md))。
- llmfit との違い:
  - llmfit(bffd1b6 の `gpu_fit_pool_gb`)は、空きが分かれば `min(空き, 総量)` で段階を決める。
  - こちらは、画像・動画の生成は 1 回ずつ動かすもので、ほかのアプリを閉じれば済むため、段階は総量で決めて注意だけ出す。

## llmfit との突き合わせ

| llmfit の場合分け | こちら |
|---|---|
| nvidia-smi の `addressing_mode`(ATS)で CPU と共有するメモリの GPU(Grace Blackwell・Tegra)を見分け、RAM を VRAM とみなす | VRAM が返らない NVIDIA の GPU は判定に使わない。ただし名前が `NVIDIA GB10` の DGX Spark だけは、ComfyUI の公式の手順があるので MemTotal を共有のメモリとして使う(0.11.0)。Jetson・GH200 / GB200 は範囲外 |
| Apple silicon は Metal の `recommendedMaxWorkingSetSize` を GPU のメモリにする | Python から Metal を呼ばない(依存を増やさない)ので、搭載量の 3/4 とみなす(上の「共有メモリ」) |
| nvidia-smi が VRAM を返さないとき GPU 名から推す | 推さない(VRAM を推して「載る」と言わない) |
| 同じ型番の複数枚をまとめて VRAM を足す(llama.cpp などは分けて載せられる) | 足さない(ComfyUI は 1 枚で動く)。最大の 1 枚 |
| Linux で nvidia-smi が無いとき sysfs・lspci で NVIDIA の GPU を見つける | 判定には使わない(CUDA が動くか分からない)。`--all-gpus` などの一覧に出すだけ |
| AMD(rocm-smi・sysfs)・Intel(lspci)・Apple(system_profiler)を判定に使う | 0.7.0 から、ComfyUI + PyTorch が動く組み合わせ(対応表)だけ使う。llama.cpp の Vulkan のようにどの GPU でも動くわけではないので、Vulkan では数えない |
| WMI の AdapterRAM の頭打ちをレジストリの qwMemorySize で直す(issue #830)、DriverDesc で突き合わせ、曖昧なら使わない | 同じ考え方。加えて MatchingDeviceId(型番の ID)で先に突き合わせ、PCI でない部品を捨てる |
| 内蔵 GPU(Intel UHD など)を外付けがあれば落とす | 専用の VRAM の GPU を内蔵 GPU より先に使う。UHD・Iris Xe は対応表に無いので使わない |
| AMD の APU の UMA の取り分を搭載量に足し戻す | 取り分けた VRAM + GTT(または見えている RAM の半分)を GPU のメモリにする |
| `available_ram_gb` が 0 のときの代わりの読み方(macOS) | psutil の値のまま |
| compute capability・ドライバ・PyTorch のビルドの対応 | llmfit には無い。こちらで足した(上の「世代と PyTorch・ドライバ」) |
| ページファイル・スワップ | llmfit には無い。こちらで足した(上の「メモリと仮想メモリ」) |

## 時間

開発用の PC(Windows 11、NVIDIA の GPU、ほかの作業で負荷あり)で 2026-10-20 に測った中央値:

| 調べ方 | 時間 |
|---|---|
| 以前の `detect()`(nvidia-smi 1 回) | 約 0.1〜0.25 秒 |
| 今の `detect()`(既定。NVIDIA の GPU があれば nvidia-smi 1 回 + ctypes + レジストリ) | 約 0.15〜0.4 秒(nvidia-smi のばらつきの範囲。ctypes・レジストリは 1 ms 未満) |
| `detect(other_gpus=True)` / `system --all-gpus` | 約 1.0〜1.2 秒(PowerShell の起動が約 1 秒) |

nvidia-smi だけを 15 回ずつ交互に動かすと、項目を増やした問い合わせと以前の問い合わせの差は測れなかった(どちらも 0.1〜0.25 秒)。

## 試験

- `tests/test_hardware.py`・`tests/test_vendors.py`(AMD・Intel・Apple・APU・CPU で動く系統・#26)・`tests/test_detect_everywhere.py`(すべての環境・GB10・WSL2。#101)と `tests/fixtures/hardware/`。
- bench が NVIDIA 以外の GPU の使用率・使用中のメモリを読む道具(`gpustat.py`)は、`tests/test_gpustat.py`(公式の文書・ソースの出力例と、開発用の PC の typeperf の実際の出力)と `tests/test_bench_vendors.py`。
- 既定の試験は本物の nvidia-smi・PowerShell・lspci・レジストリを呼ばない(`tests/conftest.py` の `_no_real_hardware` が `hardware._run`・`_reg_display_entries`・`_reg_paging_files`、0.11.0 から sysfs の部品の一覧・amdkfd・WSL2 の状態も塞ぐ)。
- フィクスチャ:
  - 開発用の PC の nvidia-smi・WMI・レジストリの実際の出力(個体の ID は 0 に置き換えた)。
  - 複数の GPU、compute_cap の無い古い版、`[N/A]`、壊れた出力、ドライバが動いていない・GPU が無いときの出力。
  - AMD だけ・Intel + NVIDIA・ドライバの無い NVIDIA の WMI、取り外した GPU が残るレジストリ、lspci。
- 確かめること:
  - 世代ごと(Kepler・Maxwell・Pascal・Volta・Turing・Ampere・Ada・Hopper・Blackwell)の判定。
  - 世代を固定して VRAM・RAM を増やしても段階が下がらないこと。
