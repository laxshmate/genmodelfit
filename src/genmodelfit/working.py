# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""段ごとの作業用メモリ(#100。docs/accuracy.md「式」)。

ComfyUI の作者が各モデルの作業用メモリとして見込む量(一次資料)を、段ごとに出す:

- S(生成の段): model_base.py の memory_required(SDPA の経路の式。1 回分。CFG の 2 倍は入れない)
- V(仕上げの段): sd.py の VAE ごとの memory_used_decode / memory_used_encode(1 枚分)と、
  VRAM によらないタイルの値 V_tile(段階に使うのは min(V, V_tile))
- T(文章の段): 作業用メモリを見込む 4 つのテキストエンコーダ(sd.py の memory_estimation_function)だけ

ComfyUI(GPL-3.0)のコードは import しない・写さない。数(係数)と式の形だけを事実として使い、出どころを
`ファイル:行@コミット`(families.COMFYUI_COMMIT。0.40.0 と違う所は書く)で残す。表の 1 行が 1 つの鍵。

当てられないもの(系統・大きさ・VAE の式が分からないもの、3D、読み上げ)は stage_memory が None を返し、
見積もりはモデル全体を今までの種類ごとの式(estimate.activation_gb)で出す(段ごとに混ぜない)。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from .i18n import tr

GIB = 1024**3
MIB = 1024**2
C = "426f5ee"  # families.COMFYUI_COMMIT の短い形(出どころの表記)

# 大きさの入力の無いテンプレート・既定の大きさ(estimate.DEFAULT_SETTINGS と同じ値。循環 import を避けて持つ)
_DEFAULT_PIXELS = {"image": (1024, 1024), "video": (832, 480)}
_DEFAULT_FRAMES = 81


# --- 潜在の形 ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Latent:
    """潜在の形の決まり。kind は 2d(画像)・3d(動画)・1d(音声)。"""

    key: str
    kind: str
    spatial: int = 8  # 画素 → 潜在の空間の圧縮(2d・3d)
    temporal: int = 4  # 動画のコマ → 潜在のコマの圧縮(3d。(F - 1) // t + 1)
    channels: int = 16  # 潜在のチャンネル(詰めた形で渡す系統の面積に入る)
    rate: float = 0.0  # 1d: 潜在のコマ数 / 秒
    source: str = ""


LATENTS: dict[str, Latent] = {
    x.key: x
    for x in (
        Latent("img8", "2d", 8, source=f"sd.py:510@{C}(KL の downscale_ratio 8)"),
        Latent("img16", "2d", 16, channels=128, source=f"sd.py:690-695@{C}(Flux2 の VAE は 2 倍して 16)"),
        Latent("pix", "2d", 1, channels=3, source=f"sd.py:928@{C}(ピクセル空間の VAE、downscale_ratio 1)"),
        Latent("wan21", "3d", 8, 4, 16, source=f"sd.py:872-873@{C}(Wan 2.1 の VAE、(4, 8, 8))"),
        Latent("wan22", "3d", 16, 4, 48, source=f"sd.py:859-860@{C}(Wan 2.2 の VAE、(4, 16, 16))"),
        Latent("ltxv", "3d", 32, 8, 128, source=f"sd.py:782-783@{C}(LTX の VAE、(8, 32, 32))"),
        Latent("hv", "3d", 8, 4, 16, source=f"sd.py:819-820@{C}(HunyuanVideo の VAE、(4, 8, 8))"),
        Latent("hv15", "3d", 16, 4, 32, source=f"sd.py:791-792@{C}(HunyuanVideo 1.5 の VAE、(4, 16, 16))"),
        Latent("cog", "3d", 8, 4, 16, source=f"sd.py:805-806@{C}(CogVideoX の VAE、(4, 8, 8))"),
        # MiniMax H3: コマ 17k+5 ↔ 潜在 5k+2(sd.py:1051)、空間 16。動画と音声を NestedTensor で渡す
        Latent("h3", "3d", 16, 0, 24, source=f"sd.py:1048-1052@{C}・nodes_minimax_h3.py:91@{C}"),
        Latent("ace15", "1d", rate=48000 / 1920, source=f"nodes_ace.py:105@{C}(秒 × 48000 / 1920)"),
        Latent("sa1", "1d", rate=44100 / 2048, source=f"nodes_audio.py:33@{C}(秒 × 44100 / 2048)"),
        Latent("yue2", "1d", rate=25.0, source=f"text_encoders/yue2.py:21@{C}(FRAMES_PER_SECOND 25)"),
        # ACE-Step 1: 潜在 [B, 8, 16, L]、L = int(秒 × 44100 / 512 / 8)。面積は 16 × L
        Latent("ace", "1d", rate=44100 / 512 / 8, source=f"nodes_ace.py:82@{C}"),
    )
}

# LTX-2 の音声の潜在: [B, 8, 秒 × 25, 16](16000 / 160 / 4 = 25 /秒、mel 64 / 4 = 16、z 8)。動画は 25 コマ/秒
LTX_AUDIO = (
    8,
    16,
    25.0,
    25.0,
    f"audio_vae.py:182-218@{C}・causal_audio_autoencoder.py:835-836@{C}・nodes_lt.py:702@{C}",
)
# MiniMax H3 の音声の潜在: [B, 32, 2, 秒 × 40](32000 / 800)。動画は 24 コマ/秒
H3_AUDIO = (32 * 2, 40.0, 24.0, f"sd.py:1080-1083@{C}・nodes_minimax_h3.py:105@{C}")


# --- 系統ごとの生成の段の係数 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Work:
    """生成の段の係数(supported_models.py の memory_usage_factor)と、潜在・VAE・文章の段の決まり。"""

    key: str
    factor: float
    latent: str  # LATENTS の鍵
    vae: str  # 既定の VAE(VAE_WORK の鍵)
    vaes: tuple[str, ...]  # この潜在で使える VAE(一覧の comfy_vae がこれに無ければ既定を使う)
    source: str
    packed: bool = False  # 潜在を詰めた形で渡す(面積にチャンネルを含める。samplers.py:1281-1283)
    extra_vaes: tuple[str, ...] = ()  # 一緒に仕上げる VAE(LTX-2 の音声など)
    te: str | None = None  # 文章の段の作業用メモリ(TE_WORK の鍵)
    ref: bool = False  # 参照の潜在(ref_latents)を memory_required に足す系統


_KL = ("kl",)
_WAN = ("wan",)
_F2 = ("flux2",)
SM = "supported_models.py"
# 系統の鍵 → 行。大きさ・名前で分かれる系統(sd・wan・flux2・ltxv・omnigen2・kandinsky5・stable_audio)は
# work_of で行を選ぶ
WORKS: dict[str, Work] = {
    w.key: w
    for w in (
        Work("sd15", 1.0, "img8", "kl", _KL, f"{SM}:69・116・180@{C}(SD15・SD20・SDXLRefiner)"),
        Work("sdxl", 0.8, "img8", "kl", _KL, f"{SM}:219@{C}"),
        Work("sd3", 1.6, "img8", "kl", _KL, f"{SM}:567@{C}"),
        Work("flux", 3.1, "img8", "kl", _KL, f"{SM}:752@{C}(FluxInpaint も継ぐ)", ref=True),
        Work("chroma", 3.2, "img8", "kl", _KL, f"{SM}:1819@{C}", ref=True),
        Work("longcat", 2.5, "img8", "kl", _KL, f"{SM}:2460@{C}", ref=True),
        Work(
            "hidream",
            2.0,
            "img8",
            "kl",
            _KL,
            f"supported_models_base.py:49@{C}(既定。{SM}:1706 は TODO で無効)",
        ),
        Work("lumina2", 1.4, "img8", "kl", _KL, f"{SM}:1209@{C}", ref=True),
        Work("zimage", 2.8, "img8", "kl", _KL, f"{SM}:1239@{C}", ref=True),
        Work("omnigen2", 1.95, "img8", "kl", _KL, f"{SM}:1926@{C}", ref=True),
        Work("boogu", 2.15, "img8", "kl", _KL, f"{SM}:1960@{C}", ref=True),
        Work("k5_image", 1.25, "img8", "kl", _KL, f"{SM}:2350@{C}"),
        Work("krea2", 2.2, "img8", "wan", _WAN, f"{SM}:2020@{C}", ref=True),
        Work("qwen_image", 1.8, "img8", "wan", _WAN, f"{SM}:2138@{C}", ref=True),
        Work("joyimage", 1.8, "img8", "wan", _WAN, f"{SM}:2196@{C}", ref=True),
        Work("anima", 0.95, "img8", "wan", _WAN, f"{SM}:1184@{C}(model_channels 2048 / 2048 × 0.95)"),
        Work("lens", 4.0, "img16", "flux2", _F2, f"{SM}:881@{C}"),
        Work("ideogram4", 11.6, "img16", "flux2", _F2, f"{SM}:1981@{C}"),
        Work("moss_image", 14.6, "img16", "flux2", _F2, f"{SM}:2048@{C}", ref=True),
        Work("ernie", 10.0, "img16", "flux2", _F2, f"{SM}:2519@{C}"),
        Work("mage_flow", 6.5, "img16", "mage", ("mage",), f"{SM}:2109@{C}", ref=True),
        Work("qwen_image21", 6.0, "img16", "qwen21", ("qwen21",), f"{SM}:2168@{C}", ref=True),
        Work("hidream_o1", 0.033, "pix", "pixel", ("pixel",), f"{SM}:1734@{C}"),
        Work("pixeldit", 0.04, "pix", "pixel", ("pixel",), f"{SM}:1298@{C}"),
        # Flux2: 3.1 × (2 × 2) × hidden_size / 2604(supported_models.py:820)。
        # hidden_size は本体の大きさの区分から
        Work(
            "flux2_4b", 3.1 * 4 * 3072 / 2604, "img16", "flux2", _F2, f"{SM}:820@{C}(hidden 3072)", ref=True
        ),
        Work(
            "flux2_9b", 3.1 * 4 * 4096 / 2604, "img16", "flux2", _F2, f"{SM}:820@{C}(hidden 4096)", ref=True
        ),
        Work(
            "flux2_32b", 3.1 * 4 * 6144 / 2604, "img16", "flux2", _F2, f"{SM}:820@{C}(hidden 6144)", ref=True
        ),
        # Wan: dim / 2222(supported_models.py:1374)。dim は本体の大きさの区分から。VACE は × 1.2(:1456)
        Work("wan_1.3b", 1536 / 2222, "wan21", "wan", ("wan", "wan22"), f"{SM}:1374@{C}(dim 1536)"),
        Work("wan_5b", 3072 / 2222, "wan22", "wan22", ("wan22",), f"{SM}:1374@{C}(dim 3072)"),
        Work("wan_14b", 5120 / 2222, "wan21", "wan", ("wan", "wan22"), f"{SM}:1374@{C}(dim 5120)"),
        Work(
            "wan_vace_1.3b", 1.2 * 1536 / 2222, "wan21", "wan", _WAN, f"{SM}:1374・1456@{C}(dim 1536 × 1.2)"
        ),
        Work("wan_vace_14b", 1.2 * 5120 / 2222, "wan21", "wan", _WAN, f"{SM}:1374・1456@{C}(dim 5120 × 1.2)"),
        Work("wan_dancer", 1.8, "wan21", "wan", _WAN, f"{SM}:1590@{C}(WAN22_WanDancer)"),
        # LTX: LTXV 5.5 × cross_attention_dim / 2048(:960。13B は 4096 で 11)、LTXAV 0.077(:980)
        Work(
            "ltxv", 5.5, "ltxv", "ltxv", ("ltxv", "ltx_diffusion"), f"{SM}:960@{C}(cross_attention_dim 2048)"
        ),
        Work(
            "ltxv_13b",
            11.0,
            "ltxv",
            "ltxv",
            ("ltxv", "ltx_diffusion"),
            f"{SM}:960@{C}(cross_attention_dim 4096)",
        ),
        Work(
            "ltxav",
            0.077,
            "ltxv",
            "ltxv",
            ("ltxv", "ltx_diffusion"),
            f"{SM}:980@{C}",
            packed=True,
            extra_vaes=("ltx_audio",),
            te="gemma",
        ),
        Work("hunyuan_video", 1.8, "hv", "hunyuan_video", ("hunyuan_video",), f"{SM}:1026@{C}"),
        Work("hunyuan_video15", 4.0, "hv15", "hunyuan_video15", ("hunyuan_video15",), f"{SM}:2269@{C}"),
        Work("k5_video", 1.25, "hv", "hunyuan_video", ("hunyuan_video",), f"{SM}:2321@{C}"),
        Work("cogvideox", 2.0, "cog", "cogvideox", ("cogvideox",), f"supported_models_base.py:49@{C}(既定)"),
        Work(
            "minimax_h3",
            0.114,
            "h3",
            "minimax_h3",
            ("minimax_h3",),
            f"{SM}:999@{C}",
            packed=True,
            extra_vaes=("h3_audio",),
        ),
        Work("ace15", 4.7, "ace15", "oobleck", ("oobleck",), f"{SM}:2377@{C}", te="ace15_lm"),
        Work("ace", 0.5, "ace", "ace", ("ace",), f"{SM}:1902@{C}"),
        Work("stable_audio", 2.0, "sa1", "oobleck", ("oobleck",), f"supported_models_base.py:49@{C}(既定)"),
        Work("yue2", 4.0, "yue2", "yue2", ("yue2",), f"{SM}:2407@{C}", te="yue2"),
    )
}

# 本体の大きさ(header_params。無ければ params_b)の区分。docs/accuracy.md「系統の当て方」
WAN_BINS = ((2.5e9, "wan_1.3b"), (7e9, "wan_5b"), (math.inf, "wan_14b"))
FLUX2_BINS = ((6e9, "flux2_4b"), (12e9, "flux2_9b"), (math.inf, "flux2_32b"))
# LTX: LTX-2 / 2.3(19B・22B。音声の付いた LTXAV)・LTXV 13B・ほか(2B)
LTX_BINS = ((10e9, "ltxv"), (14e9, "ltxv_13b"), (math.inf, "ltxav"))
# SD: 本体(全部入り)が 3.3B 以上なら SDXL(UNet 2.6B + CLIP 0.8B)、未満は SD1.5・SD2・SDXL refiner(どれも 1.0)
SDXL_MIN_PARAMS = 3.3e9

# 参照の潜在を足す編集の用途(tasks)。ReferenceLatent で入れた ref_latents を memory_required が数える
# (model_base.py:426-436@426f5ee。系統は Work.ref)
REF_TASKS = frozenset({"i2i", "relight"})
# VAE の符号化を使う用途(入力の画像・動画・音声を潜在にする)
ENCODE_TASKS = frozenset(
    {
        "i2i",
        "inpaint",
        "outpaint",
        "relight",
        "control",
        "v2v",
        "i2v",
        "flf2v",
        "ref2v",
        "a2a",
        "motion",
        "upscale",
    }
)
# 符号化する入力が動画全体(コマ数ぶん)の用途。i2v などは画像だけ
# (Wan は全部のコマ: nodes_wan.py:48・54@426f5ee)
VIDEO_INPUT_TASKS = frozenset({"v2v", "control", "inpaint", "outpaint", "motion"})


# --- VAE の作業用メモリ ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """1 枚分の形。潜在(T・h・w または L)と画素(F・H・W または音声のサンプル数 n)。"""

    T: int = 1
    h: int = 1
    w: int = 1
    F: int = 1
    H: int = 1
    W: int = 1
    L: int = 1
    n: int = 1


@dataclass(frozen=True)
class VaeWork:
    """VAE の memory_used_decode / memory_used_encode(要素数。dtype の大きさを掛ける前)と、タイルの決まり。"""

    key: str
    decode: object  # Shape → 要素数
    encode: object | None
    tiling: str  # 2d / 3d / 1d / 1d_extra / own256 / none
    source: str
    fp32_only: bool = False  # working_dtypes が [float32] だけ(どの GPU でも 4 バイト)
    fp16: bool = True  # working_dtypes に float16 がある(bf16 の無い GPU でも 2 バイト)
    kl_ratio: bool = False  # AMD で × 2.73(VAE_KL_MEM_RATIO。sd.py:503-506)
    # タイルにしない V を使う量に入れる VAE(#148。docs/accuracy.md「タイル」)。ComfyUI はまずタイルにせずに
    # 復号し(sd.py:1278-1311)、CUDA の OOM のときだけタイルに分ける(sd.py:1312-1326、
    # model_management.py:389-395)。実測でタイルにしない山が V に沿って増えると確かめた VAE だけ True
    untiled: bool = False


def _h3_dec(s: Shape) -> float:
    # MiniMax H3: (9.5 × min(コマ, 28 + 2) × H × W + 固定) × 1.03
    # (sd.py:1068-1072。28 = (5 + 2) × 4: ldm/minimax/vae.py:451-452)
    up = max(1, (s.T - 2) // 5 * 17 + 5) if s.T > 1 else 1
    fixed = 150e6 if up == 1 else 110e6 if up <= 22 else 270e6
    return (9.5 * min(up, 30) * s.H * s.W + fixed) * 1.03


def _h3_enc(s: Shape) -> float:
    fixed, per = (110e6, 7.0) if s.F == 1 else (1.3e9, 9.5)
    return (per * min(s.F, 17) * s.H * s.W + fixed) * 1.03  # sd.py:1061-1066


VAE_WORK: dict[str, VaeWork] = {
    v.key: v
    for v in (
        VaeWork(
            "kl",
            lambda s: 2178 * s.h * s.w * 64,
            lambda s: 1767 * s.H * s.W,
            "2d",
            f"sd.py:508-509@{C}",
            fp16=False,
            kl_ratio=True,
        ),
        # Flux2 の VAE: KL の式(16 倍の潜在)× 4。0.40.0 は常に × 4(sd.py:693@0.40.0)、426f5ee は fused norm が
        # 使えれば × 1 で fp16 も持つ(sd.py:693・698@426f5ee)。製品のエンジンが 0.40 なので 0.40 の値を使う
        VaeWork(
            "flux2",
            lambda s: 2178 * s.h * s.w * 64 * 4,
            lambda s: 1767 * s.H * s.W,
            "2d",
            f"sd.py:690-698@{C}(× 4 は 0.40.0 の sd.py:693)",
            fp16=False,
            kl_ratio=True,
        ),
        VaeWork(
            "wan",
            lambda s: (2200 if s.T <= 4 else 7000) * s.h * s.w * 64,
            lambda s: (1500 if s.F <= 4 else 6000) * s.H * s.W,
            "3d",
            f"sd.py:882-883@{C}",
        ),
        VaeWork(
            "wan22", lambda s: 8000 * s.h * s.w * 256, lambda s: 3300 * s.H * s.W, "3d", f"sd.py:866-867@{C}"
        ),
        VaeWork(
            "qwen21", lambda s: 900 * s.h * s.w * 256, lambda s: 600 * s.H * s.W, "2d", f"sd.py:854-855@{C}"
        ),
        VaeWork(
            "mage",
            lambda s: 1000 * s.h * s.w * 256,
            lambda s: 400 * s.H * s.W,
            "2d",
            f"sd.py:630-631@{C}",
            fp16=False,
        ),
        VaeWork("pixel", lambda s: s.H * s.W, lambda s: s.H * s.W, "2d", f"sd.py:926-927@{C}", fp16=False),
        VaeWork(
            "ltxv",
            lambda s: 1200 * s.T * s.h * s.w * 512,
            lambda s: 80 * max(s.F, 7) * s.H * s.W,
            "3d",
            f"sd.py:778-779@{C}",
            fp16=False,
        ),
        VaeWork(
            "ltx_diffusion",
            lambda s: 1700 * s.T * s.h * s.w * 512,
            lambda s: 80 * max(s.F, 7) * s.H * s.W,
            "3d",
            f"sd.py:641-642@{C}",
            fp16=False,
        ),
        VaeWork(
            "hunyuan_video",
            lambda s: 2800 * min(8, (s.T - 1) * 4 + 1) * s.h * s.w * 64,
            lambda s: 1400 * min(9, s.F) * s.H * s.W,
            "3d",
            f"sd.py:826-827@{C}",
        ),
        VaeWork(
            "hunyuan_video15",
            lambda s: 3600 * 4 * s.h * s.w * 256,
            lambda s: 1400 * 9 * s.H * s.W,
            "3d",
            f"sd.py:800-801@{C}",
        ),
        VaeWork(
            "cogvideox",
            lambda s: 2800 * max(2, (s.T - 1) * 4 + 1) * s.h * s.w * 64,
            lambda s: 1400 * max(1, s.F) * s.H * s.W,
            "3d",
            f"sd.py:810-811@{C}",
        ),
        VaeWork(
            "minimax_h3", _h3_dec, _h3_enc, "own256", f"sd.py:1061-1075@{C}(256 画素のタイルは自分で切る)"
        ),
        VaeWork(
            "oobleck",
            lambda s: 1000 * s.L * 2048,
            lambda s: 1000 * s.n,
            "1d",
            f"sd.py:734-735@{C}(ACE-Step 1.5・Stable Audio Open の Oobleck)",
            # ACE-Step 1.5 の実測(RTX 3070・RTX 5080)で、作業用メモリが V の 0.8〜1.0 倍(30 秒以上)で
            # 曲の長さに比例(#148)
            untiled=True,
        ),
        VaeWork("yue2", lambda s: 1500 * s.L * 1920, lambda s: 1000 * s.n, "1d", f"sd.py:734・747@{C}"),
        VaeWork(
            "ace", lambda s: 87000 * 16 * s.L, lambda s: 330 * s.n, "1d_extra", f"sd.py:911-912・923@{C}"
        ),
        # 一緒に仕上げる音声の VAE(float32 だけ)
        VaeWork(
            "ltx_audio", lambda s: 87000 * 16 * s.L, None, "none", f"sd.py:991-992・1003@{C}", fp32_only=True
        ),
        VaeWork(
            "h3_audio",
            lambda s: max(42e6, 220 * s.n + 20e6) * 1.03,
            None,
            "none",
            f"sd.py:1087-1096@{C}",
            fp32_only=True,
        ),
    )
}


def _tile_decode(v: VaeWork, kind: str, s: Shape, spatial: int) -> Shape:
    """VRAM によらない最小のタイルの形(復号)。docs/accuracy.md「タイル」。"""
    if v.tiling == "2d":
        # decode_tiled_: 潜在 64×64・32×128・128×32 の 3 回(sd.py:1182-1194)。一番大きい面積のもの
        h, w = max(
            ((min(s.h, a), min(s.w, b)) for a, b in ((64, 64), (32, 128), (128, 32))),
            key=lambda x: x[0] * x[1],
        )
        return Shape(s.T, h, w, s.F, h * spatial, w * spatial, s.L, s.n)
    if v.tiling == "3d":
        # decode_tiled_3d: 空間は 256 / 圧縮、時間は 2 まで縮める(sd.py:1335-1357)
        t = 256 // spatial
        return Shape(1 if s.T == 1 else min(s.T, 2), min(s.h, t), min(s.w, t), s.F, s.H, s.W, s.L, s.n)
    if v.tiling in ("1d", "1d_extra"):
        return Shape(s.T, s.h, s.w, s.F, s.H, s.W, min(s.L, 256), s.n)  # decode_tiled_1d(sd.py:1196)
    if v.tiling == "own256":
        return Shape(s.T, s.h, s.w, s.F, min(s.H, 256), min(s.W, 256), s.L, s.n)  # sd.py:1327-1331・1335
    return s


def _tile_encode(v: VaeWork, s: Shape) -> Shape:
    """VRAM によらない最小のタイルの形(符号化)。"""
    if v.tiling == "2d":
        # encode_tiled_: 画素 512×512・1024×256・256×1024 の 3 回(sd.py:1214-1225)
        H, W = max(
            ((min(s.H, a), min(s.W, b)) for a, b in ((512, 512), (256, 1024), (1024, 256))),
            key=lambda x: x[0] * x[1],
        )
        return Shape(s.T, s.h, s.w, s.F, H, W, s.L, s.n)
    if v.tiling in ("3d", "own256"):
        # encode_tiled_3d・自分で切る VAE: 空間 256 画素、時間はそのまま(sd.py:1462-1470)
        return Shape(s.T, s.h, s.w, s.F, min(s.H, 256), min(s.W, 256), s.L, s.n)
    if v.tiling == "1d":
        return Shape(s.T, s.h, s.w, s.F, s.H, s.W, s.L, min(s.n, 256 * 2048))  # encode_tiled_1d(sd.py:1227)
    if v.tiling == "1d_extra":
        return Shape(s.T, s.h, s.w, s.F, s.H, s.W, s.L, min(s.n, 256 * 2048 // 16))  # sd.py:1233-1235
    return s


# --- 文章の段 ------------------------------------------------------------------------------------


def _te_gib(key: str, seconds: float, names: str) -> float:
    """文章の段の作業用メモリ(GiB。bf16 の GPU の値。bf16 の無い GPU は呼び出し側で 2 倍)。"""
    if key == "ace15_lm":
        # トークン = 5 × 秒(min_tokens。ace15.py:243-246・281)× 0.4375 MiB
        # (4B の LM は 0.5625。ace15.py:323-326)、
        # bf16 なら半分(ace15.py:384-391)
        const = 0.5625 if re.search(r"(^|[^0-9])4b", names) else 0.4375
        return 5 * math.ceil(seconds) * const * 0.5 * MIB / GIB
    if key == "gemma":
        # 最低 642 トークン × 6 MiB、bf16 なら半分(text_encoders/lt.py:246-256)
        return 642 * 3.0 * MIB / GIB
    if key == "yue2":
        # 曲の段: 長さ = 秒 × 25 + 2、KV と前埋め(text_encoders/yue2.py:129-137、ldm/yue2/model.py:14-19、
        # head_dim 128: text_encoders/llama.py:376)。bf16 の 2 バイト
        n = round(seconds * 25) + 2
        cache = 2 * 28 * 8 * 128 * n
        prefill = n * n + n * (6144 * 3 + 2048 * 8)
        return (cache + prefill) * 2 / GIB
    return 0.0


TE_SOURCES = {
    "ace15_lm": f"text_encoders/ace15.py:243-246・323-326・384-391@{C}",
    "gemma": f"text_encoders/lt.py:246-256@{C}",
    "yue2": f"text_encoders/yue2.py:129-137@{C}",
}


# --- 系統の当て方 ---------------------------------------------------------------------------------


def _names(entry) -> str:
    from .groups import main_files

    files = " ".join(f.name.split("/")[-1] for f in main_files(entry))
    tmpl = entry.template.name if entry.template else ""
    return f"{entry.id} {entry.name} {tmpl} {files}".lower()


def main_sizes(entry) -> list[float]:
    """本体のファイルのパラメータ数(safetensors / GGUF の見出し)。無ければ一覧の params_b。
    分からなければ空。"""
    from .groups import main_files

    sizes = [float(f.header_params) for f in main_files(entry) if getattr(f, "header_params", None)]
    if sizes:
        return sizes
    if entry.params_b and entry.params_b > 0:
        return [entry.params_b * 1e9]
    return []


def _bin(size: float, bins) -> str:
    return next(key for limit, key in bins if size < limit)


def work_of(entry, family: str | None) -> Work | None:
    """この項目の生成の段の行。当てられなければ None(モデル全体を今までの式に戻す)。"""
    if family is None:
        return None
    mod = entry.modality
    names = _names(entry)
    sizes = main_sizes(entry)
    key: str | None = None
    if family == "sd":
        if mod != "image" or not sizes:  # SVD・AnimateDiff などの動画と、大きさの分からないものは当てない
            return None
        key = "sd15" if min(sizes) < SDXL_MIN_PARAMS else "sdxl"
    elif family == "wan":
        if "dancer" in names:
            key = "wan_dancer"
        elif not sizes:
            return None
        else:
            key = _bin(max(sizes), WAN_BINS)
            if "vace" in names and key != "wan_5b":
                key = key.replace("wan_", "wan_vace_")
    elif family == "flux2":
        if not sizes:
            return None
        key = _bin(max(sizes), FLUX2_BINS)
    elif family == "ltxv":
        if not sizes:
            return None
        key = _bin(max(sizes), LTX_BINS)
    elif family in ("flux", "flux_fill"):
        key = "flux"
    elif family == "omnigen2":
        key = "boogu" if "boogu" in names else "omnigen2"
    elif family == "kandinsky5":
        key = "k5_video" if mod == "video" else "k5_image"
    elif family == "stable_audio":
        # Stable Audio 3 の VAE は 2 つの設定(sd.py:1006-1030)のどちらか一覧からは分からないので当てない
        if re.search(r"stable[._ -]?audio[._ -]?3", names):
            return None
        key = "stable_audio"
    elif family in WORKS:
        key = family
    if key is None:
        return None
    w = WORKS[key]
    kind = LATENTS[w.latent].kind
    want = {"image": ("2d", "3d"), "video": ("3d",), "music": ("1d",), "sfx": ("1d",)}.get(mod, ())
    if kind not in want or (kind == "3d" and mod == "image" and not key.startswith("wan")):
        return None
    return w


def vae_key(entry, w: Work, comfy_vae: str | None) -> str:
    """仕上げの段の VAE。一覧の comfy_vae がこの潜在で使える VAE ならそれ、違えば
    (別の部品の VAE など)既定。"""
    return comfy_vae if comfy_vae in w.vaes else w.vae


# --- 段ごとの作業用メモリ -------------------------------------------------------------------------


@dataclass
class StageMem:
    """段ごとの作業用メモリ(GiB)。使う量に入れる仕上げの値は V_eff(ふつうは min(V, V_tile))。"""

    S: float
    V: float
    V_tile: float
    T: float
    work: str  # 生成の段の行の鍵
    vae: str  # VAE の鍵
    vae_bytes: int  # VAE の計算の 1 要素のバイト数
    kl_ratio: float  # AMD の倍率(掛けたもの)
    notes: list[str] = field(default_factory=list)
    # タイルにしない V を使う量に入れる(VaeWork.untiled。#148)。False ならタイルの値 min(V, V_tile)
    untiled: bool = False
    # untiled の VAE が入らず、ComfyUI がタイルに分けて動かす(OOM のあと)見積もり(estimate._judge_tiled)
    fallback: bool = False

    @property
    def V_eff(self) -> float:
        return self.V if self.untiled else min(self.V, self.V_tile)

    def tiled(self) -> StageMem | None:
        """タイルに分けて仕上げる見積もり(untiled の VAE で、タイルのほうが小さいときだけ。#148)。"""
        if not self.untiled or self.V_tile >= self.V:
            return None
        return replace(self, untiled=False, fallback=True)

    @property
    def peak(self) -> float:
        """全部載せに足す作業用メモリ(一番重い段)。"""
        return max(self.S, self.V_eff, self.T)

    @property
    def resident_peak(self) -> float:
        """全部を載せたまま仕上げまで動かすときに ComfyUI が空けようとする作業用メモリ(#102。
        docs/accuracy.md「タイル」)。VAE はタイルにしない V で見込む: 復号の前に V を空けようとし
        (sd.py:1278-1280@426f5ee)、タイルは普通の復号が OOM になったときの逃げ道(sd.py:1314@426f5ee)。
        使う量(peak)ではなく、全部載せを選べるかを決める量。"""
        return max(self.S, self.V, self.T)


def _dims(entry, s, lat: Latent, *, image_frames: bool) -> Shape:
    mod = entry.modality
    if lat.kind == "1d":
        sec = s.seconds if s.seconds and s.seconds > 0 else 30.0
        L = max(1, int(sec * lat.rate)) if lat.key == "ace" else max(1, round(sec * lat.rate))
        sr = 48000 if lat.key in ("ace15", "yue2") else 44100
        return Shape(L=L, n=round(sec * sr))
    W, H = s.width, s.height
    if not W or not H:
        W, H = _DEFAULT_PIXELS["video" if mod == "video" else "image"]
    sp = lat.spatial
    h, w = max(1, H // sp), max(1, W // sp)
    if lat.kind == "2d" or image_frames:
        return Shape(1, h, w, 1, H, W)
    F = s.frames if s.frames and s.frames > 0 else _DEFAULT_FRAMES
    if lat.key == "h3":  # 17k+5 コマ ↔ 5k+2(sd.py:1051)
        return Shape(max(1, (F - 5) // 17 * 5 + 2) if F > 1 else 1, h, w, F, H, W)
    T = (F - 1) // lat.temporal + 1
    return Shape(T, h, w, F, H, W)


def stage_memory(
    entry,
    s,
    *,
    act_factor: float = 1.0,
    gpu_fp32: bool = False,
    no_bf16: bool = False,
    amd: bool = False,
) -> StageMem | None:
    """段ごとの作業用メモリ(GiB)。当てられないモデルは None(呼び出し側が今までの式を使う)。

    基本は NVIDIA の半精度(2 バイト)の値。act_factor は生成の段を fp32 で計算する GPU の 2、
    gpu_fp32 は半精度を使わない GPU、no_bf16 は bf16 を持たない GPU
    (VAE は working_dtypes に fp16 が無ければ fp32)、amd は AMD の GPU(KL の VAE を × 2.73)。"""
    from .families import family_of

    if entry.modality in ("3d", "tts"):
        return None
    fam = getattr(entry, "comfy_family", None) or family_of(entry)
    w = work_of(entry, fam)
    if w is None:
        return None
    from .families import vae_of

    stored = getattr(entry, "comfy_vae", None) or vae_of(entry, fam)
    vkey = vae_key(entry, w, stored)
    v = VAE_WORK.get(vkey)
    if v is None:
        return None
    lat = LATENTS[w.latent]
    image_frames = entry.modality == "image" and lat.kind == "3d"
    d = _dims(entry, s, lat, image_frames=image_frames)
    batch = max(1, s.batch or 1)
    tasks = set(entry.tasks or [])
    notes: list[str] = []

    # S: B × 面積 × 2 バイト × 0.01 × 係数 MiB(model_base.py:438-442)。
    # 面積は潜在の要素からチャンネルを除いたもの。
    # 詰めた形(NestedTensor → pack_latents)ではチャンネルも入る(samplers.py:1281-1283、utils.py:1445-1453)
    if lat.kind == "1d":
        area = 16 * d.L if lat.key == "ace" else d.L
    elif w.packed:
        area = lat.channels * d.T * d.h * d.w
        sec = d.F / (LTX_AUDIO[3] if w.key == "ltxav" else H3_AUDIO[2])
        if w.key == "ltxav":
            area += LTX_AUDIO[0] * LTX_AUDIO[1] * round(sec * LTX_AUDIO[2])
        else:
            area += H3_AUDIO[0] * round(sec * H3_AUDIO[1])
    else:
        area = d.T * d.h * d.w
    if w.ref and tasks & REF_TASKS:
        area += d.T * d.h * d.w  # 入力の画像 1 枚を参照の潜在として足す
    S = batch * area * 2 * 0.01 * w.factor * MIB / GIB * act_factor

    # V: 1 枚分。復号と、入力を潜在にする用途なら符号化の大きいほう
    enc_input = bool(tasks & ENCODE_TASKS)
    vae_bytes = 4 if (v.fp32_only or gpu_fp32 or (no_bf16 and not v.fp16)) else 2
    ratio = 2.73 if (amd and v.kl_ratio) else 1.0
    enc_shape = d
    if enc_input and lat.kind == "3d" and not image_frames:
        frames = d.F if (fam == "wan" or tasks & VIDEO_INPUT_TASKS) else 1
        enc_shape = Shape(d.T, d.h, d.w, frames, d.H, d.W)

    def one(vv: VaeWork, shape: Shape, nbytes: int, r: float) -> tuple[float, float]:
        dec = vv.decode(shape)
        dec_t = vv.decode(_tile_decode(vv, lat.kind, shape, lat.spatial))
        enc = enc_t = 0.0
        if enc_input and vv.encode is not None:
            es = enc_shape if vv is v else shape
            enc = vv.encode(es)
            enc_t = vv.encode(_tile_encode(vv, es))
        return max(dec, enc) * nbytes * r / GIB, max(dec_t, enc_t) * nbytes * r / GIB

    V, V_tile = one(v, d, vae_bytes, ratio)
    for extra in w.extra_vaes:
        xv = VAE_WORK[extra]
        if extra == "ltx_audio":
            sec = d.F / LTX_AUDIO[3]
            xs = Shape(L=max(1, round(sec * LTX_AUDIO[2])))
        else:
            sec = d.F / H3_AUDIO[2]
            xs = Shape(n=round(sec * 32000))
        a, at = one(xv, xs, 4, 1.0)
        V, V_tile = max(V, a), max(V_tile, at)
    if vkey == "flux2":
        notes.append(
            tr(
                "Flux2 の VAE の作業用メモリは、ComfyUI 0.40 の見込み(KL の式の 4 倍)で見積もった"
                "(0.43 以降は fused norm が使える GPU では 4 倍しない)",
                "The Flux2 VAE working memory uses ComfyUI 0.40's figure (4 × the KL formula;"
                " from 0.43 it is not multiplied by 4 on GPUs that can use the fused norm)",
            )
        )

    # T: 作業用メモリを見込むテキストエンコーダだけ。bf16 の無い GPU・fp32 の GPU は 2 倍
    T = 0.0
    if w.te:
        sec = s.seconds if s.seconds and s.seconds > 0 else 30.0
        T = _te_gib(w.te, sec, _names(entry)) * (2.0 if (no_bf16 or gpu_fp32) else 1.0)
    return StageMem(S, V, V_tile, T, w.key, vkey, vae_bytes, ratio, notes, untiled=v.untiled)


__all__ = [
    "LATENTS",
    "VAE_WORK",
    "WORKS",
    "StageMem",
    "Work",
    "main_sizes",
    "stage_memory",
    "vae_key",
    "work_of",
]
