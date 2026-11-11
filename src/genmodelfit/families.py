# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ComfyUI のモデルの系統(model family)と、その系統が使える計算の精度(docs/hardware.md「精度」)。

ComfyUI(GPL-3.0)のコードは import しない。comfy/supported_models.py の `supported_inference_dtypes` と、
comfy/sd.py の VAE の `working_dtypes` を事実として読み、系統ごとに「fp16 で計算できるか」だけを表にした
(コミット COMFYUI_COMMIT。行番号はそのコミットのもの)。

- 本体: fp16 を持たない系統(`[bfloat16, float32]`)は、bf16 の計算を持たない GPU(Turing・Volta・Pascal など)で
  ComfyUI の unet_manual_cast が float32 を選ぶ(計算は fp32、作業用メモリは 2 倍)
- VAE: `working_dtypes` に fp16 が無い VAE は、bf16 を持たない GPU で vae_dtype が float32 を選ぶ
  (VAE の重みは 2 倍)

一覧の各項目の系統は `family_of`(本体のファイル名 → ID・テンプレート名の順に照らす)で決め、
update が `ModelEntry.comfy_family` / `comfy_vae` に入れる。分からないものは None(見積もりは変えない)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMFYUI_COMMIT = "426f5eea52a7ece1dd599fff4cb6cd1f352ddd24"  # v0.43.0(2026-10-26)。0.40.0 と違う所は書く


@dataclass(frozen=True)
class Family:
    key: str
    comfy_class: str  # supported_models.py のクラス
    fp16: bool  # supported_inference_dtypes に float16 があるか
    source: str  # 根拠の行(supported_models.py:行 など)
    vae: str | None = None  # 既定の VAE(VAES の鍵)。VAE のファイル名で分かればそちらを使う
    note: str | None = None


# 系統 → 本体の精度(supported_models.py。BASE の既定は [float16, bfloat16, float32]:
# supported_models_base.py:47)
FAMILIES: dict[str, Family] = {
    f.key: f
    for f in (
        Family("sd", "SD15 / SD20 / SDXL / SVD など", True, "supported_models_base.py:47(既定)", "kl"),
        Family("sd3", "SD3", True, "supported_models_base.py:47(既定)", "kl"),
        Family("flux", "Flux / FluxSchnell", True, "supported_models.py:754", "kl"),
        Family("flux_fill", "FluxInpaint(in_channels 96 = Fill)", False, "supported_models.py:784", "kl"),
        Family("flux2", "Flux2", True, "supported_models.py:813", "flux2"),
        Family("flux3", "Flux3", False, "supported_models.py:856", "flux2"),
        Family("chroma", "Chroma / ChromaRadiance", True, "supported_models.py:1821", "kl"),
        Family("longcat", "LongCatImage", True, "supported_models.py:2462", "kl"),
        Family("krea2", "Krea2", True, "supported_models.py:2024", "wan"),
        Family("lens", "Lens", False, "supported_models.py:883", "flux2"),
        Family("mochi", "GenmoMochi", False, "supported_models.py:925"),
        Family("ltxv", "LTXV / LTXAV", False, "supported_models.py:953", "ltxv"),
        Family("minimax_h3", "MiniMaxH3", False, "supported_models.py:1001", "minimax_h3"),
        Family("hunyuan_video", "HunyuanVideo", False, "supported_models.py:1028", "hunyuan_video"),
        Family("hunyuan_video15", "HunyuanVideo15", True, "supported_models.py:2270", "hunyuan_video15"),
        Family("hunyuan_image21", "HunyuanImage21", False, "supported_models.py:2232"),
        Family("cosmos", "CosmosT2V / CosmosPredict2", True, "supported_models.py:1101"),
        Family("anima", "Anima", True, "supported_models.py:1172", "wan"),
        Family("lumina2", "Lumina2(NewBie も)", False, "supported_models.py:1214", "kl"),
        Family(
            "zimage",
            "ZImage",
            False,
            "supported_models.py:1241",
            "kl",
            "重みの値によっては ComfyUI が fp16 も許す"
            "(allow_fp16: model_detection.py:624、supported_models.py:1245)",
        ),
        Family("ming_image", "MingImage", False, "supported_models.py:1241(ZImage を継ぐ)"),
        Family("pixeldit", "PixelDiTT2I", False, "supported_models.py:1299", "pixel"),
        Family(
            "wan",
            "WAN21_* / WAN22_*(HuMo・Vace・S2V・Animate・SCAIL など)",
            True,
            "supported_models.py:1367",
            "wan",
        ),
        Family("trellis2", "Trellis2(Pixal3D も)", False, "supported_models.py:1542", "trellis2"),
        Family("hunyuan3d", "Hunyuan3Dv2 / 2_1", True, "supported_models_base.py:47(既定)", "hunyuan3d"),
        Family("triposplat", "TripoSplat", True, "supported_models.py:1686"),
        Family("hidream", "HiDream", False, "supported_models.py:1711", "kl"),
        Family("hidream_o1", "HiDreamO1", False, "supported_models.py:1736", "pixel"),
        Family("seedvr2", "SeedVR2", True, "supported_models.py:1854", "seedvr2"),
        Family("ace", "ACEStep", False, "supported_models.py:1904", "ace"),
        Family("ace15", "ACEStep15", False, "supported_models.py:2379", "oobleck"),
        Family(
            "omnigen2",
            "Omnigen2 / Boogu",
            True,
            "supported_models.py:1931・1939(extended_fp16_support のとき float16 を先頭に足す)",
            "kl",
        ),
        Family("ideogram4", "Ideogram4", False, "supported_models.py:1995", "flux2"),
        Family("moss_image", "MOSSImage", False, "supported_models.py:2053", "flux2"),
        Family("mage_flow", "MageFlow", False, "supported_models.py:2114", "mage"),
        Family("qwen_image", "QwenImage", False, "supported_models.py:2143", "wan"),
        Family("qwen_image21", "QwenImage21", False, "supported_models.py:2173", "qwen21"),
        Family("joyimage", "JoyImage", False, "supported_models.py:2205", "wan"),
        Family("kandinsky5", "Kandinsky5", False, "supported_models.py:2323"),
        Family("yue2", "YuE2", False, "supported_models.py:2405", "yue2"),
        Family("minimax_music3", "MiniMaxMusic3", True, "supported_models.py:2429"),
        Family("ernie", "ErnieImage", False, "supported_models.py:2524", "flux2"),
        Family("cogvideox", "CogVideoX(VOID も)", True, "supported_models.py:2605", "cogvideox"),
        Family("stable_audio", "StableAudio / StableAudio3", True, "supported_models_base.py:47(既定)"),
        Family("sam3", "SAM3", True, "supported_models.py:2541"),
        Family("depth_anything3", "DepthAnything3", True, "supported_models.py:2500"),
    )
}


@dataclass(frozen=True)
class Vae:
    key: str
    fp16: bool  # working_dtypes に float16 があるか
    source: str


@dataclass(frozen=True)
class CpuRun:
    """GPU なしでも ComfyUI の CPU の動き(`--cpu`、GPU の無い PC では自動)で動くとした系統(#39)。"""

    family: str
    modalities: tuple[str, ...]  # この種類のときだけ(sd の系統の SVD = 動画は入れない)
    evidence: str  # 根拠(一次資料と、推定であること)
    speed: str | None = None  # 遅さの目安(実測があれば理由に出す)。今はどれも実測が無いので None
    exclude: str | None = None  # ID・名前がこれに当たるものは除く(CPU で確かめていない大きな版)

    def evidence_text(self) -> str:
        return shown(self.evidence) or ""

    def speed_text(self) -> str | None:
        return shown(self.speed)


# CPU で動く系統。重みの大きさの一律の上限(estimate.CPU_MAX_WEIGHTS_GB)より大きくても、メインメモリが足りれば
# CPU で動くとする(段階は estimate.cpu_top: 生成は marginal まで。#108)。
# ComfyUI は CPU では本体と VAE を fp32 で持つ(model_management.py の
# should_use_fp16 / should_use_bf16 が CPU では False: 426f5ee の 1912-1913・1979-1980 行。
# unet_dtype・vae_dtype が float32)。
# テキストエンコーダは fp16 のまま(text_encoder_dtype の 1261 行)。見積もりは estimate._judge_cpu
CPU_FAMILIES: dict[str, CpuRun] = {
    r.family: r
    for r in (
        CpuRun(
            "sd",
            ("image",),
            "ComfyUI は GPU が無ければ CPU で動く(README の Windows Portable「running on your CPU only」、"
            "comfy/cli_args.py の --cpu)。SD 1.5 / 2.x / SDXL は ComfyUI の組み込みの系統で、"
            "CUDA 専用の部品を使わない(推定。CPU での実測なし)",
            exclude=r"supir",  # SUPIR(SDXL を使う高解像度化)は独自のノードで、CPU で動くか確かめていない
        ),
        CpuRun(
            "stable_audio",
            ("music", "sfx"),
            "ComfyUI は GPU が無ければ CPU で動き、Stable Audio は組み込みの StableAudio の系統"
            "(推定。CPU での実測なし。"
            "AI生成キットの記録に「効果音(GPU/CPU)」があるが、モデル・時間の記録は無い)",
            exclude=r"medium",  # Stable Audio 3 Medium は大きく、CPU で動くか確かめていない
        ),
    )
}


# VAE → working_dtypes(sd.py。既定は [bfloat16, float32]: sd.py:518。2D の KL VAE は既定のまま)
VAES: dict[str, Vae] = {
    v.key: v
    for v in (
        Vae("kl", False, "sd.py:518(既定。SD・SDXL・SD3・Flux の ae などの 2D の KL)"),
        Vae("ltxv", False, "sd.py:784"),
        Vae("ltx_diffusion", False, "sd.py:647(LTX 2.4 以降の拡散の VAE)"),
        Vae("mage", False, "sd.py:629"),
        Vae("wan", True, "sd.py:881(Wan 2.1。Qwen Image の VAE も同じ)"),
        Vae("wan22", True, "sd.py:865(Wan 2.2。16 倍圧縮)"),
        Vae("qwen21", True, "sd.py:853(Qwen Image 2.1。16 倍圧縮)"),
        Vae(
            "flux2",
            False,
            "sd.py:690-698(Flux2 の VAE。0.40.0 は KL の既定のまま fp16 が無い: sd.py:515・693@0.40.0)",
        ),
        Vae("oobleck", True, "sd.py:742(ACE-Step 1.5・Stable Audio Open の Oobleck)"),
        Vae("yue2", True, "sd.py:742・744-747(YuE2 の Oobleck)"),
        Vae("dav", True, "sd.py:542(MiniMax Music 3 の DAV。どの GPU でも float32)"),
        Vae("sa3_large", True, "sd.py:1029(Stable Audio 3 の大きい設定。transformers 12 段)"),
        Vae("sa3_small", True, "sd.py:1029(Stable Audio 3 の小さい設定。transformers 6 段)"),
        Vae("pixel", False, "sd.py:925-933(ピクセル空間。KL の既定のまま)"),
        Vae("hunyuan_video", True, "sd.py:828"),
        Vae("hunyuan_video15", True, "sd.py:795"),
        Vae("hunyuan3d", True, "sd.py:906"),
        Vae("trellis2", True, "sd.py:596・601"),
        Vae("seedvr2", True, "sd.py:612"),
        Vae("cogvideox", True, "sd.py:812"),
        Vae("minimax_h3", True, "sd.py:1053"),
        Vae("ace", True, "sd.py:921"),
        Vae("fp32", True, "sd.py:542・951・1003・1087(どの GPU でも float32 なので GPU による違いが無い)"),
    )
}

# 理由・注意に出す上の表の文の英語(#73)。(日本語の文, 英語)。無い文はそのまま出す
# (tests/test_families.py が、日本語を含む文がすべてここにあるかを確かめる)
_EN: dict[str, str] = dict(
    [
        ("SD15 / SD20 / SDXL / SVD など", "SD15 / SD20 / SDXL / SVD etc."),
        ("supported_models_base.py:47(既定)", "supported_models_base.py:47 (default)"),
        ("Lumina2(NewBie も)", "Lumina2 (also NewBie)"),
        (
            "重みの値によっては ComfyUI が fp16 も許す"
            "(allow_fp16: model_detection.py:624、supported_models.py:1245)",
            "Depending on the weight values, ComfyUI also allows fp16"
            " (allow_fp16: model_detection.py:624, supported_models.py:1245)",
        ),
        ("supported_models.py:1241(ZImage を継ぐ)", "supported_models.py:1241 (inherits ZImage)"),
        (
            "WAN21_* / WAN22_*(HuMo・Vace・S2V・Animate・SCAIL など)",
            "WAN21_* / WAN22_* (HuMo, Vace, S2V, Animate, SCAIL etc.)",
        ),
        ("Trellis2(Pixal3D も)", "Trellis2 (also Pixal3D)"),
        (
            "supported_models.py:1931・1939(extended_fp16_support のとき float16 を先頭に足す)",
            "supported_models.py:1931, 1939 (float16 is put first when extended_fp16_support)",
        ),
        ("CogVideoX(VOID も)", "CogVideoX (also VOID)"),
        (
            "ComfyUI は GPU が無ければ CPU で動く(README の Windows Portable「running on your CPU only」、"
            "comfy/cli_args.py の --cpu)。SD 1.5 / 2.x / SDXL は ComfyUI の組み込みの系統で、"
            "CUDA 専用の部品を使わない(推定。CPU での実測なし)",
            "ComfyUI runs on the CPU when there is no GPU (README, Windows Portable:"
            ' "running on your CPU only"; --cpu in comfy/cli_args.py). SD 1.5 / 2.x / SDXL are built-in'
            " ComfyUI families and use no CUDA-only parts (estimated; not measured on CPU)",
        ),
        (
            "ComfyUI は GPU が無ければ CPU で動き、Stable Audio は組み込みの StableAudio の系統"
            "(推定。CPU での実測なし。"
            "AI生成キットの記録に「効果音(GPU/CPU)」があるが、モデル・時間の記録は無い)",
            "ComfyUI runs on the CPU when there is no GPU, and Stable Audio is the built-in StableAudio"
            " family (estimated; not measured on CPU. The records of the local_model_ai_gen_kit app list"
            ' "sound effects (GPU/CPU)" but not the model or time)',
        ),
        (
            "sd.py:518(既定。SD・SDXL・SD3・Flux の ae などの 2D の KL)",
            "sd.py:518 (default; 2D KL VAEs such as SD, SDXL, SD3 and the Flux ae)",
        ),
        ("sd.py:647(LTX 2.4 以降の拡散の VAE)", "sd.py:647 (the diffusion VAE of LTX 2.4 and later)"),
        ("sd.py:881(Wan 2.1。Qwen Image の VAE も同じ)", "sd.py:881 (Wan 2.1; also the Qwen Image VAE)"),
        ("sd.py:865(Wan 2.2。16 倍圧縮)", "sd.py:865 (Wan 2.2; 16× compression)"),
        ("sd.py:853(Qwen Image 2.1。16 倍圧縮)", "sd.py:853 (Qwen Image 2.1; 16× compression)"),
        (
            "sd.py:690-698(Flux2 の VAE。0.40.0 は KL の既定のまま fp16 が無い: sd.py:515・693@0.40.0)",
            "sd.py:690-698 (the Flux2 VAE; 0.40.0 keeps the KL default without fp16: sd.py:515, 693@0.40.0)",
        ),
        (
            "sd.py:742(ACE-Step 1.5・Stable Audio Open の Oobleck)",
            "sd.py:742 (the Oobleck VAE of ACE-Step 1.5 and Stable Audio Open)",
        ),
        ("sd.py:742・744-747(YuE2 の Oobleck)", "sd.py:742, 744-747 (the YuE2 Oobleck VAE)"),
        (
            "sd.py:542(MiniMax Music 3 の DAV。どの GPU でも float32)",
            "sd.py:542 (the MiniMax Music 3 DAV; float32 on every GPU)",
        ),
        (
            "sd.py:1029(Stable Audio 3 の大きい設定。transformers 12 段)",
            "sd.py:1029 (the large Stable Audio 3 configuration; 12 transformer layers)",
        ),
        (
            "sd.py:1029(Stable Audio 3 の小さい設定。transformers 6 段)",
            "sd.py:1029 (the small Stable Audio 3 configuration; 6 transformer layers)",
        ),
        ("sd.py:925-933(ピクセル空間。KL の既定のまま)", "sd.py:925-933 (pixel space; the KL default)"),
        ("sd.py:596・601", "sd.py:596, 601"),
        (
            "sd.py:542・951・1003・1087(どの GPU でも float32 なので GPU による違いが無い)",
            "sd.py:542, 951, 1003, 1087 (float32 on every GPU, so no difference between GPUs)",
        ),
    ]
)


def shown(text: str | None) -> str | None:
    """上の表の文を表示の言語で返す(英語が無ければそのまま)。"""
    from .i18n import tr

    if text is None:
        return None
    return tr(text, _EN.get(text, text))


# 本体のファイル名・テンプレート名・リポジトリ名 → 系統(上から順に試す)
_RULES: tuple[tuple[str, str], ...] = (
    (r"flux3", "flux3"),
    (r"flux[._-]?1[._-]?fill|fill[._-]dev|onereward", "flux_fill"),
    (r"flux[._-]?2|flux2", "flux2"),
    (r"chroma", "chroma"),
    (r"longcat", "longcat"),
    (r"flux|kontext|ovis", "flux"),
    (r"krea[._-]?2", "krea2"),
    (r"(^|[^a-z])lens[_-]", "lens"),
    (r"mochi", "mochi"),
    (r"ltx", "ltxv"),
    (r"minimax[._-]?h3|fasth3", "minimax_h3"),
    (r"minimax[._-]?music", "minimax_music3"),
    (r"hunyuan[._-]?video[._-]?1\.5|hunyuanvideo15", "hunyuan_video15"),
    (r"hunyuan[._-]?image[._-]?2\.1", "hunyuan_image21"),
    (r"hunyuan[._-]?video", "hunyuan_video"),
    (r"hunyuan[._-]?3d", "hunyuan3d"),
    (r"cosmos", "cosmos"),
    (r"anima(?!t)", "anima"),  # animate(Wan Animate)・animation・animatediff と分ける
    (r"lumina|netayume|newbie", "lumina2"),
    (r"z[._-]?image", "zimage"),
    (r"ming[._-]?image", "ming_image"),
    (r"pixeldit", "pixeldit"),
    (r"hidream[._-]?o1", "hidream_o1"),
    (r"hidream", "hidream"),
    (r"seedvr2", "seedvr2"),
    (r"ace[._-]?step[._-]?1\.5|acestep[._-]?v?1\.5|ace[._-]?1\.5", "ace15"),
    (r"ace[._-]?step", "ace"),
    (r"omnigen|boogu", "omnigen2"),
    (r"ideogram|ideo[._-]?4", "ideogram4"),
    (r"moss[._-]?image", "moss_image"),
    (r"mage[._-]?flow", "mage_flow"),
    (r"qwen[._-]?image[._-]?2\.1|image[._-]2\.1[._-]|noct[._-]?q", "qwen_image21"),
    (r"qwen[._-]?image|firered", "qwen_image"),
    (r"joyai|joyimage", "joyimage"),
    (r"kandinsky", "kandinsky5"),
    (r"yue2", "yue2"),
    (r"ernie", "ernie"),
    (r"cogvideo|void[._-]pass", "cogvideox"),
    (r"trellis|pixal3d", "trellis2"),
    (r"triposplat", "triposplat"),
    (r"(^|[^a-z])wan|humo|chrono[._-]?edit|causal[._-]?forcing|scail|infinitetalk|bernini", "wan"),
    (r"sd3|sd[._-]?3\.5", "sd3"),
    (
        r"sd[._-]?xl|sdxl|noobai|lustify|juggernaut|realismbystable|meina|hassaku|svd[._-]|lotus"
        # v1.5 のような版の番号だけでは SD にしない(10Eros・MOSS-TTS が当たっていた)
        r"|(sd|stable[._-]?diffusion)[._-]?v?1[._-]5|v1[._-]5[._-](pruned|emaonly|inpainting)",
        "sd",
    ),
    (r"stable[._-]?audio", "stable_audio"),
    (r"sam3", "sam3"),
    (r"depth[._-]?anything[._-]?3", "depth_anything3"),
)

# VAE のファイル名 → VAE(上から順に試す)
_VAE_RULES: tuple[tuple[str, str], ...] = (
    (r"ltx[._-]?2\.[45].*video[._-]?vae|ltx25.*video[._-]?vae", "ltx_diffusion"),
    (r"ltx", "ltxv"),
    (r"mage[._-]?flow", "mage"),
    (r"hunyuanvideo15|hunyuan[._-]?video[._-]?1\.5", "hunyuan_video15"),
    (r"hunyuan[._-]?video", "hunyuan_video"),
    (r"wan2[._]?2[._-]?vae", "wan22"),
    (r"qwen[._-]?image[._-]?2\.1[._-]?vae", "qwen21"),
    (r"wan|qwen[._-]?image", "wan"),
    (r"flux2[._-]?vae|full[._-]encoder[._-]small[._-]decoder", "flux2"),
    (r"ace[._-]?1\.5[._-]?vae", "oobleck"),
    (r"trellis", "trellis2"),
    (r"seedvr2", "seedvr2"),
    (r"cogvideox", "cogvideox"),
    (r"minimax[._-]?h3", "minimax_h3"),
    (
        r"(^|/)ae\.safetensors|vae[._-]ft[._-]mse|sdxl[._-]?vae",
        "kl",
    ),
)


def _match(rules: tuple[tuple[str, str], ...], text: str) -> str | None:
    for pat, key in rules:
        if re.search(pat, text):
            return key
    return None


def _main_names(entry) -> list[str]:
    from .groups import main_files

    return [f.name.split("/")[-1].lower() for f in main_files(entry)]


# 名前で系統が分からない画像の本体を、見出しの要素数(header_params)で SD と見分ける指紋。
# 1 行 = (公式のファイルの要素数, 説明)。公式のファイルの見出しを 2026-11-11 に数えた
# (stable-diffusion-v1-5/stable-diffusion-v1-5 の v1-5-pruned-emaonly.safetensors、
# stabilityai/stable-diffusion-xl-base-1.0 の sd_xl_base_1.0.safetensors と unet/。UNet だけの数は HF API の
# safetensors.total と同じ)。docs/hardware.md「系統の表」
SD_PARAM_PRINTS: tuple[tuple[int, str], ...] = (
    (1_066_247_386, "SD1.5 の全部入り"),
    (859_520_964, "SD1.5 の UNet"),
    (3_468_837_945, "SDXL の全部入り"),
    (2_567_463_684, "SDXL の UNet"),
)
# 指紋の幅。同じ形の派生(inpaint の conv_in の 5 チャンネル 5 × 320 × 3 × 3 = 14,400、position_ids などの
# 小さな差)を含め、ほかの形(いちばん近い形でも数千万は違う)は含まない
SD_PARAM_TOLERANCE = 20_000


def _sd_by_params(entry) -> bool:
    from .groups import main_files

    if entry.modality != "image":
        return False
    sizes = [f.header_params for f in main_files(entry) if getattr(f, "header_params", None)]
    return bool(sizes) and all(
        any(abs(n - ref) <= SD_PARAM_TOLERANCE for ref, _ in SD_PARAM_PRINTS) for n in sizes
    )


def family_of(entry) -> str | None:
    """一覧の 1 件の系統。本体のファイル名で決まらなければ、ID・テンプレート名で決め、それでも決まらなければ
    画像の本体の見出しの要素数が SD の指紋(SD_PARAM_PRINTS)に合うものを SD にする。"""
    if entry.modality == "tts":  # 読み上げは ComfyUI の組み込みの系統に当たらない(名前の偶然の一致を避ける)
        return None
    names = " ".join(_main_names(entry))
    key = _match(_RULES, names) if names else None
    if key:
        return key
    # 部品の置き場所(Comfy-Org/… のリポジトリ)は別のモデルの部品も混ざるので見ない。
    # HF の項目は ID がリポジトリ
    tmpl = entry.template.name if entry.template else ""
    key = _match(_RULES, f"{entry.id} {tmpl}".lower())
    if key:
        return key
    return "sd" if _sd_by_params(entry) else None


def vae_of(entry, family: str | None = None) -> str | None:
    """VAE の種類。VAE のファイルの名前で決め、無ければ系統の既定(全部入りのチェックポイントなど)。"""
    vaes = [
        f.name.split("/")[-1].lower()
        for f in entry.files
        if (f.role == "vae" or f.directory == "vae") and f.name
    ]
    found = {_match(_VAE_RULES, n) for n in vaes} - {None}
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:  # 複数の VAE(使い分け)は、fp16 を持たないものがあればそれ(安全側)
        return next((k for k in sorted(found) if not VAES[k].fp16), sorted(found)[0])
    fam = FAMILIES.get(family or "")
    return fam.vae if fam and not vaes else None


def assign(entry) -> None:
    """entry.comfy_family と entry.comfy_vae を決めて入れる(update と同梱の一覧の作り直しで使う)。"""
    entry.comfy_family = family_of(entry)
    entry.comfy_vae = vae_of(entry, entry.comfy_family)
