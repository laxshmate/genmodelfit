# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ファイルの出どころを人がたどって確かめた表(#28)。自動の決め方(hf.HfClient.resolve_file)で不明になったファイルにだけ当てる。

再配布のリポジトリ(Comfy-Org・Kijai など)は base_model を書いていないか、ファイルと合わない base_model を
書いていることが多く、自動の決め方では不明になる。ここには、元のモデルの**一次資料**(本家の HF のカード
(cardData)・LICENSE・公式の GitHub のリポジトリの LICENSE と README・公式のコード)を読んで、
「この置き場所のこのファイルは、この元のモデルの重み」と確かめられたものだけを載せる(docs/sources.md の
「出どころの表」)。

決まり(寛容の側に間違えない):
- 行は「置き場所のリポジトリ」と「ファイル名」の組で当てる。同じ名前でも別のリポジトリのファイルには当てない
- 元のライセンスは、一次資料にそう書いてあるものだけ。寛容なライセンス(apache-2.0・mit など)は、根拠の文に
  そのライセンスの名前が出てくる行だけ(tests/test_license_sources.py が検査する)
- 元が分かっても、元の元(base_model)より寛容な表記しか無いもの・重みのライセンスの記載が無いもの・本家を
  読めないものは載せない(不明のまま)
- 独自のライセンス(LTX-2・MiniMax・NVIDIA など)は `other` と名前で持つ(許可リストには無いので auto には
  ならないが、「不明」より正確)
- 本家を確かめられない部品(hf.UNVERIFIED_COMPONENTS)・元のライセンスを読めない系統(hf.LINEAGES)の名前の
  ファイルには、表に行があっても当てない(二重の守り)

- 上書きの行(override=True): 自動の決め方が、元のカードの cardData(寛容な表記)だけを見て本文の規約
  (ChronoEdit の「Governing Terms: NVIDIA Open Model License」など)を見落とす・違う元をたどるものは、
  決まっていても表で上書きする(寛容の側の誤りを直す・出どころを直す。0.1.1 のレビュー)
- 元のライセンスを読めない系統(hf.LINEAGES)のファイルは、自動で決まっていても不明に戻す(enforce_lineage。
  gate も同じ判定で不明にするので、一覧のデータと gate の答えをそろえる)

`update` は自動で決めたあと不明のファイルと上書きの行にこの表を当てる(`update.apply_license`)。
同梱の一覧には、ネットに触らずに `apply_license_sources(db)` で当てられる
(`python -m genmodelfit.license_sources` は、
当てたときに不明・auto が何件変わるかを出す。一覧のファイルは書き換えない)。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .hf import (
    HF,
    UPSTREAM_GATED,
    Resolved,
    license_label,
    lineage_for,
    parse_hf_url,
    unverified_component_for,
)
from .i18n import tr

if TYPE_CHECKING:
    from .db import Database
    from .models import ModelEntry, ModelFile

CHECKED = "2026-10-20"  # 表の一次資料を読んだ日
EVIDENCE_KEY = "license_source"  # license_evidence の印("license_source:<行の名前>")
# 表を当て直しても残す根拠(手で足した部品をそのモデルが使う根拠。appinfo.add_components。#40)
COMPONENT_EVIDENCE = "manual_component"


@dataclass(frozen=True)
class LicenseSource:
    """置き場所のリポジトリのファイル → 元のモデルのライセンス(一次資料で確かめたもの)。"""

    key: str
    repos: tuple[str, ...]  # 置き場所(HF の repo_id)。この中のファイルにだけ当てる
    pattern: re.Pattern[str]  # ファイル名(パスの最後)
    license: str
    license_name: str | None
    source: str  # license_from に入れる元(HF の repo_id。HF に無ければ GitHub の owner/repo)
    url: str  # license_url(ライセンスの記載・原文の場所)
    evidence: str  # 一次資料の URL と、そこに書いてあること
    via: tuple[str, ...] = ()  # 置き場所と元の間にたどったもの(license_path に入れる)
    gated: bool = False  # 元が利用申請(規約への同意)を求める
    checked: str = CHECKED
    # True: 自動で決まっていても上書きする(自動の決め方が寛容な側に誤る・違う元をたどると確かめたもの)
    override: bool = False

    def matches(self, repo_id: str | None, filename: str) -> bool:
        base = filename.rsplit("/", 1)[-1]
        return repo_id in self.repos and self.pattern.fullmatch(base) is not None

    def resolved(self, repo_id: str) -> Resolved:
        ev = [f"{EVIDENCE_KEY}:{self.key}", f"{self.evidence}({self.checked} に確認)"]
        if self.gated:
            ev.append(f"{UPSTREAM_GATED}: {self.source}")
        return Resolved(
            self.license,
            self.license_name,
            self.source,
            self.url,
            [repo_id, *self.via, self.source],
            ev,
        )


def _s(
    key: str,
    repos: str | Iterable[str],
    pat: str,
    lic: str,
    name: str | None,
    source: str,
    evidence: str,
    *,
    url: str | None = None,
    via: Iterable[str] = (),
    gated: bool = False,
    override: bool = False,
    checked: str = CHECKED,
) -> LicenseSource:
    rs = (repos,) if isinstance(repos, str) else tuple(repos)
    return LicenseSource(
        key,
        rs,
        re.compile(pat, re.IGNORECASE),
        lic,
        name,
        source,
        url or f"{HF}/{source}",
        evidence,
        tuple(via),
        gated,
        checked,
        override=override,
    )


_K = "Kijai/WanVideo_comfy"
_KREADME = f"{HF}/{_K}/blob/main/README.md"
_KLX2V = f"{HF}/{_K}/blob/main/Lightx2v/README.md"

# 表(docs/sources.md の「出どころの表」と同じ。行を足したら tests/test_license_sources.py の名前の表にも足す)
SOURCES: tuple[LicenseSource, ...] = (
    # --- Kijai/WanVideo_comfy(ファイル名の頭で元をたどる) ---------------------------------------
    _s(
        "causvid-14b-lora",
        _K,
        r"Wan21_CausVid_14B_T2V_lora_rank\d+(_v1_5_no_first_block|_v2)?\.safetensors",
        "cc-by-nc-4.0",
        None,
        "lightx2v/Wan2.1-T2V-14B-CausVid",
        f"{_KREADME} に「CausVid LoRAs are experimental extractions from the CausVid finetunes」と"
        f"「CausVid 14B: {HF}/lightx2v/Wan2.1-T2V-14B-CausVid」。その cardData の license は cc-by-nc-4.0",
    ),
    _s(
        "causvid-1.3b-lora",
        _K,
        r"Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank\d+\.safetensors",
        "cc-by-nc-4.0",
        None,
        "tianweiy/CausVid",
        f"{_KREADME} に「CausVid 1.3B: {HF}/tianweiy/CausVid」と CausVid の LoRA は CausVid の微調整からの"
        "抽出とある。tianweiy/CausVid の cardData の license は cc-by-nc-4.0",
    ),
    _s(
        "lightx2v-t2v-14b-lora",
        _K,
        r"Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank\d+\.safetensors",
        "apache-2.0",
        None,
        "lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill",
        f"{_KREADME} に「CFG and Step distill 14B: {HF}/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill」。"
        "その cardData の license は apache-2.0、LICENSE.txt は Apache License 2.0 の本文。"
        "元の Wan2.1-T2V-14B も apache-2.0",
        url=f"{HF}/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill/blob/main/LICENSE.txt",
    ),
    _s(
        "lightx2v-i2v-480p-lora",
        (_K, "Comfy-Org/Wan-Animate-2"),
        r"lightx2v_I2V_14B_480p_cfg_step_distill_rank\d+_bf16\.safetensors",
        "apache-2.0",
        None,
        "lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v",
        f"{_KLX2V} に「Extracted from: {HF}/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v"
        "/tree/main/distill_models」。その cardData の license は apache-2.0(base_model は "
        "Wan-AI/Wan2.1-I2V-14B-480P、apache-2.0)、LICENSE.txt は Apache License 2.0 の本文。"
        "Comfy-Org/Wan-Animate-2 の同じ名前のファイルは Kijai のものと sha256 が同じ(85c4a61c…)",
        url=f"{HF}/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v/blob/main/LICENSE.txt",
    ),
    _s(
        "lightx2v-t2v-14b-v2-lora",
        _K,
        r"lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank\d+_bf16\.safetensors",
        "apache-2.0",
        None,
        "lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v",
        f"{_KLX2V} に「Extracted from: {HF}/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v"
        "/blob/main/distill_models/distill_model.safetensors」。その cardData の license は apache-2.0"
        "(base_model は Wan-AI/Wan2.1-T2V-14B、apache-2.0)、LICENSE.txt は Apache License 2.0 の本文",
        url=f"{HF}/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill-Lightx2v/blob/main/LICENSE.txt",
    ),
    _s(
        "wan2.2-animate-relight-lora",
        _K,
        r"WanAnimate_relight_lora_fp16(_resized_from_128_to_dynamic_22)?\.safetensors",
        "apache-2.0",
        None,
        "Wan-AI/Wan2.2-Animate-14B",
        f"{HF}/Wan-AI/Wan2.2-Animate-14B の relighting_lora/adapter_model.safetensors と、safetensors の"
        "ヘッダで 960 個の重みの形がすべて同じ(名前を PEFT から ComfyUI の形に、F32 を F16 にしたもの)。"
        "Wan-AI/Wan2.2-Animate-14B の cardData の license は apache-2.0",
    ),
    _s(
        "wan-move-14b",
        "Kijai/WanVideo_comfy_fp8_scaled",
        r"Wan21-WanMove_fp8_scaled_e4m3fn_KJ\.safetensors",
        "apache-2.0",
        None,
        "Ruihang/Wan-Move-14B-480P",
        "公式の https://github.com/ali-vilab/Wan-Move の README が ComfyUI 用にこの Kijai のフォルダを挙げ、"
        f"重みは {HF}/Ruihang/Wan-Move-14B-480P。そのカードは license: apache-2.0 と「The models in this "
        "repository are licensed under the Apache 2.0 License」(Wan2.1 の上に作ったもの)",
    ),
    # --- Comfy-Org の配り直し(README の「Original model repository」から) ----------------------------
    _s(
        "wan-animate-2",
        "Comfy-Org/Wan-Animate-2",
        r"wan_animate_2(_distill)?_(bf16|fp16|fp8_scaled|int8_convrot)\.safetensors",
        "apache-2.0",
        None,
        "Wan-AI/Wan2.2-Animate-2-14B",
        f"{HF}/Comfy-Org/Wan-Animate-2 の README「Original model repository: "
        f"{HF}/Wan-AI/Wan2.2-Animate-2-14B」。"
        "元のカードは license: apache-2.0 と「This project is licensed under the Apache License 2.0」"
        "(wan_animate_2_bf16 と wan_animate_2_bf16_distillation の重みを置いている)",
    ),
    _s(
        "minimax-music3",
        "Comfy-Org/MiniMax-Music-3",
        r"minimax_music3_(dav|dit_fp16|dit_bf16|text_encoder\w*)\.safetensors",
        "other",
        "minimax-music3-community-license",
        "MiniMaxAI/MiniMax-Music3",
        f"{HF}/Comfy-Org/MiniMax-Music-3 の README「Original model repository: "
        f"{HF}/MiniMaxAI/MiniMax-Music3」。"
        "元の LICENSE は「MiniMax-Music3 COMMUNITY LICENSE」(UI への表示、年 2,000 万ドルを超える売上は"
        "書面の許可、利用方針の遵守)",
        url=f"{HF}/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE",
    ),
    _s(
        "hunyuan-video",
        ("Comfy-Org/HunyuanVideo_repackaged", "Kijai/HunyuanVideo_comfy"),
        r"hunyuan_video_(t2v_720p_(bf16|fp8_e4m3fn)|vae_(bf16|fp32))\.safetensors",
        "other",
        "tencent-hunyuan-community",
        "tencent/HunyuanVideo",
        "Comfy-Org/HunyuanVideo_repackaged の README「Original model repository: "
        f"{HF}/tencent/HunyuanVideo」、"
        f"Kijai/HunyuanVideo_comfy の README「Safetensors and fp8 version of HunhuanVideo models: "
        f"{HF}/tencent/HunyuanVideo」。元の cardData は other(tencent-hunyuan-community)",
        url=f"{HF}/tencent/HunyuanVideo/blob/main/LICENSE",
    ),
    _s(
        "newbie-gemma-3-4b-it",
        "Comfy-Org/NewBie-image-Exp0.1_repackaged",
        r"gemma_3_4b_it_(bf16|fp16|fp8_scaled)\.safetensors",
        "gemma",
        None,
        "google/gemma-3-4b-it",
        f"元の {HF}/NewBie-AI/NewBie-image-Exp0.1 のカード「We use Gemma3-4B-it as the primary text encoder」"
        f"と {HF}/google/gemma-3-4b-it へのリンク。google/gemma-3-4b-it は license: gemma(gated: manual)。"
        "Gemma 利用規約は派生(Model Derivatives)にもかかる",
        via=("NewBie-AI/NewBie-image-Exp0.1",),
        gated=True,
    ),
    _s(
        "newbie-jina-clip-v2",
        "Comfy-Org/NewBie-image-Exp0.1_repackaged",
        r"jina_clip_v2_(bf16|fp16|fp32)\.safetensors",
        "cc-by-nc-4.0",
        None,
        "jinaai/jina-clip-v2",
        f"元の {HF}/NewBie-AI/NewBie-image-Exp0.1 のカード「We also extract pooled text features from "
        f"Jina CLIP v2」と {HF}/jinaai/jina-clip-v2 へのリンク。jinaai/jina-clip-v2 の cardData は "
        "cc-by-nc-4.0",
        via=("NewBie-AI/NewBie-image-Exp0.1",),
    ),
    _s(
        "ltx-2-kijai",
        "Kijai/LTXV2_comfy",
        r"ltx-2-19b-(distilled|dev)_transformer_only_(bf16|fp8\w*)\.safetensors"
        r"|ltx-2-19b-embeddings_connector_(distill|dev)_bf16\.safetensors",
        "other",
        "ltx-2-community-license-agreement",
        "Lightricks/LTX-2",
        f"{HF}/Kijai/LTXV2_comfy の cardData は other(ltx-2-community-license-agreement)、README に"
        "「Dev and distill embeddings_connector weights are extracted from their respective original "
        "models」。"
        f"元の {HF}/Lightricks/LTX-2(ltx-2-19b-distilled / -dev を置く)の cardData も同じ",
        url="https://github.com/Lightricks/LTX-2/blob/main/LICENSE",
    ),
    _s(
        "gemma-3-12b-abliterated-lora",
        "Comfy-Org/ltx-2",
        r"gemma-3-12b-it-abliterated_lora_rank\d+_(bf16|fp16)\.safetensors",
        "gemma",
        None,
        "mlabonne/gemma-3-12b-it-abliterated",
        f"{HF}/Comfy-Org/ltx-2 の cardData の base_model に mlabonne/gemma-3-12b-it-abliterated と "
        "google/gemma-3-12b-it。mlabonne/gemma-3-12b-it-abliterated は license: gemma(元は "
        "google/gemma-3-12b-it)。Gemma 利用規約は派生(Model Derivatives)にもかかる",
        url="https://ai.google.dev/gemma/terms",
    ),
    _s(
        "hunyuanimage-2.1-qwen2.5-vl-7b",
        "Comfy-Org/HunyuanImage_2.1_ComfyUI",
        r"qwen_2\.5_vl_7b(_bf16|_fp16)?\.safetensors",
        "apache-2.0",
        None,
        "Qwen/Qwen2.5-VL-7B-Instruct",
        "公式の https://github.com/Tencent-Hunyuan/HunyuanImage-2.1/blob/master/ckpts/checkpoints-download.md"
        "「use an open-source alternative, such as Qwen2.5-VL-7B-Instruct」「hf download "
        "Qwen/Qwen2.5-VL-7B-Instruct --local-dir ./ckpts/text_encoder/llm」。"
        f"{HF}/Qwen/Qwen2.5-VL-7B-Instruct "
        "の license は apache-2.0。qwen_2.5_vl_7b.safetensors の sha256(cfafd739…、16,584,415,576 バイト)は "
        f"{HF}/Comfy-Org/Qwen-Image_ComfyUI の split_files/text_encoders/qwen_2.5_vl_7b.safetensors"
        "(Qwen-Image の文章の読み取り = Qwen2.5-VL-7B-Instruct)と同じ(2026-10-20 に HF の API で確認)",
        via=("tencent/HunyuanImage-2.1",),
    ),
    _s(
        "anima-qwen3-0.6b-base",
        "circlestone-labs/Anima",
        r"qwen_3_06b_base\.safetensors",
        "apache-2.0",
        None,
        "Qwen/Qwen3-0.6B-Base",
        f"sha256 が {HF}/Qwen/Qwen3-0.6B-Base の model.safetensors と同じ(cd2a5120…、1,192,135,096 バイト)。"
        "Qwen/Qwen3-0.6B-Base の cardData の license は apache-2.0",
    ),
    _s(
        "chronoedit-14b",
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        r"chrono_edit_14B_(fp16|bf16|fp8_scaled)\.safetensors",
        "other",
        "nvidia-open-model-license",
        "nvidia/ChronoEdit-14B-Diffusers",
        f"{HF}/Comfy-Org/Wan_2.2_ComfyUI_Repackaged の README が元に nvidia/ChronoEdit-14B-Diffusers "
        "を挙げる。"
        "元のカードは cardData が apache-2.0 だが、本文に「Governing Terms: Use of this model is governed by "
        "the NVIDIA Open Model License Agreement. Additional Information: Apache License Version 2.0」"
        "(apache-2.0 だけではないので、厳しいほうの NVIDIA Open Model License で持つ)",
        url="https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/",
        override=True,
    ),
    _s(
        "chronoedit-distill-lora",
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        r"chronoedit_distill_lora\.safetensors",
        "other",
        "nvidia-open-model-license",
        "nvidia/ChronoEdit-14B-Diffusers",
        f"sha256 が {HF}/nvidia/ChronoEdit-14B-Diffusers の lora/chronoedit_distill_lora.safetensors と同じ"
        "(0af09f4b…、375,905,424 バイト)。そのカードは cardData が apache-2.0 だが、本文に「Governing Terms: "
        "Use of this model is governed by the NVIDIA Open Model License Agreement. Additional Information: "
        "Apache License Version 2.0」(自動の決め方は cardData だけを見て apache-2.0 にしていた。上書きする)",
        url="https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/",
        override=True,
    ),
    _s(
        "ernie-image-ministral-3-3b",
        "Comfy-Org/ERNIE-Image",
        r"ministral-3-3b\.safetensors",
        "apache-2.0",
        None,
        "baidu/ERNIE-Image",
        f"{HF}/Comfy-Org/ERNIE-Image の README が元に baidu/ERNIE-Image を挙げる。baidu/ERNIE-Image(cardData "
        "apache-2.0)の text_encoder/config.json は Mistral3Model・ministral3(26 層・hidden 3072 = "
        "Ministral 3 "
        f"3B)。元の {HF}/mistralai/Ministral-3-3B-Base-2512・-Instruct-2512・-Reasoning-2512 の license は"
        "どれも apache-2.0",
        via=(),
    ),
    _s(
        "flux2-dev-turbo-lora-fal",
        ("ByteZSzn/Flux.2-Turbo-ComfyUI", "Comfy-Org/flux2-dev"),
        r"Flux_2-Turbo-LoRA_comfyui\.safetensors",
        "other",
        "flux-dev-non-commercial-license",
        "fal/FLUX.2-dev-Turbo",
        f"sha256 が {HF}/fal/FLUX.2-dev-Turbo の comfy/Flux_2-Turbo-LoRA_comfyui.safetensors と同じ"
        "(011487390b…)。fal/FLUX.2-dev-Turbo のカードは other(flux-dev-non-commercial-license)と"
        "「This model inherits the FLUX [dev] Non-Commercial License from the base model」",
        url=f"{HF}/black-forest-labs/FLUX.2-dev/blob/main/LICENSE.txt",
    ),
    _s(
        "flux2-dev-mistral-small-3.2",
        "Comfy-Org/flux2-dev",
        r"mistral_3_small_flux2_(bf16|fp8|fp4_mixed)\.safetensors",
        "apache-2.0",
        None,
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "FLUX.2 の公式のコード https://github.com/black-forest-labs/flux2/blob/50fe5162777813d869182b139e83b10743caef15"
        "/src/flux2/text_encoder.py の Mistral3SmallEmbedder が model_spec="
        '"mistralai/Mistral-Small-3.2-24B-Instruct-2506" を from_pretrained で読む(手を加えない)。'
        f"{HF}/mistralai/Mistral-Small-3.2-24B-Instruct-2506 の license は apache-2.0(ComfyUI のファイルは"
        "使う層だけに減らしたもの)。black-forest-labs/FLUX.2-dev の "
        "text_encoder/model-0000{1..10}-of-00010 の sha256 は、"
        "mistralai/Mistral-Small-3.2-24B-Instruct-2506 の model-0000{1..10}-of-00010 と 10 個とも同じ"
        "(91831c2c…・8ffe8070…・… 116ef7ae…。2026-10-20 に HF の API で確認)",
        via=("black-forest-labs/FLUX.2-dev",),
    ),
    _s(
        "flux2-klein-9b-qwen3-8b",
        ("Comfy-Org/flux2-klein-9B", "Comfy-Org/vae-text-encorder-for-flux-klein-9b"),
        r"qwen_3_8b(_fp8mixed|_fp4mixed|_bf16)?\.safetensors",
        "apache-2.0",
        None,
        "Qwen/Qwen3-8B",
        "FLUX.2 の公式のコード https://github.com/black-forest-labs/flux2/blob/50fe5162777813d869182b139e83b10743caef15"
        '/src/flux2/util.py の flux.2-klein-9b が load_qwen3_embedder(variant="8B")、text_encoder.py が '
        "Qwen/Qwen3-8B-FP8 を from_pretrained で読む(手を加えない)。qwen_3_8b.safetensors の大きさ "
        "16,381,517,176 バイトは Qwen/Qwen3-8B の 5 つの分割の合計 16,381,516,776 バイト + ヘッダ。"
        f"{HF}/Qwen/Qwen3-8B・Qwen/Qwen3-8B-FP8 の license は apache-2.0",
        via=("black-forest-labs/FLUX.2-klein-9B",),
    ),
    _s(
        "flux3-klein-prompt-upsampler",
        "black-forest-labs/flux-3-image-klein-comfy",
        r"qwen3\.5_9b_flux3_prompt_upsampler_\w+\.safetensors",
        "other",
        "flux-kommunity-license",
        "black-forest-labs/flux-3-image-klein-comfy",
        f"本家 Black Forest Labs 自身のリポジトリ {HF}/black-forest-labs/flux-3-image-klein-comfy の中の"
        "ファイルで、その cardData は other(flux-kommunity-license、LICENSE.md)。元の Qwen3.5-9B "
        "(apache-2.0)より厳しいので、リポジトリのライセンスで持つ",
        url=f"{HF}/black-forest-labs/flux-3-image-klein-comfy/blob/main/LICENSE.md",
    ),
    _s(
        "moss-image-qwen3.5-9b",
        "Comfy-Org/MOSS-Image",
        r"qwen3\.5_9b_(bf16|int8_convrot|w4a8_gptq)\.safetensors",
        "apache-2.0",
        None,
        "OpenMOSS-Team/MOSS-Image-Base",
        f"{HF}/Comfy-Org/MOSS-Image の cardData の base_model は OpenMOSS-Team/MOSS-Image-Base・-SFT。"
        "MOSS-Image-Base(cardData apache-2.0、LICENSE は Apache License 2.0 の本文)の text_encoder は "
        "Qwen3_5Model(32 層・hidden 4096 = Qwen3.5-9B)。"
        f"{HF}/Qwen/Qwen3.5-9B の license も apache-2.0",
        url=f"{HF}/OpenMOSS-Team/MOSS-Image-Base/blob/main/LICENSE",
        via=("Qwen/Qwen3.5-9B",),
    ),
    _s(
        "supir",
        "Kijai/SUPIR_pruned",
        r"SUPIR-v0[QF]_(fp16|bf16|fp32)\.safetensors",
        "other",
        "supir-non-commercial",
        "Fanghua-Yu/SUPIR",
        "SUPIR の公式 https://github.com/Fanghua-Yu/SUPIR の README「Non-Commercial Use Only Declaration: "
        'The SUPIR ("Software") is made available for use, reproduction, and distribution strictly for '
        "non-commercial purposes」。SUPIR-v0Q / v0F はその公式の重み(Kijai/SUPIR_pruned "
        "はそれを減らしたもの)",
        url="https://github.com/Fanghua-Yu/SUPIR#non-commercial-use-only-declaration",
    ),
    _s(
        "stable-audio-3-t5gemma",
        "Comfy-Org/stable-audio-3",
        r"t5gemma_b_b_ul2(_bf16|_fp16)?\.safetensors",
        "gemma",
        None,
        "google/t5gemma-b-b-ul2",
        "Stability AI の公式 https://github.com/Stability-AI/stable-audio-tools/blob/main/docs/conditioning.md"
        f"「This uses a T5Gemma ({HF}/google/t5gemma-b-b-ul2) encoder」。google/t5gemma-b-b-ul2 は "
        "license: gemma(gated: manual)",
        url="https://ai.google.dev/gemma/terms",
        gated=True,
    ),
    # --- 読み上げの部品(HF の収集では入らない。data/manual.json の components。#142) ---
    _s(
        "moss-audio-tokenizer",
        "OpenMOSS-Team/MOSS-Audio-Tokenizer",
        r"model-0000[12]-of-00002\.safetensors",
        "apache-2.0",
        None,
        "OpenMOSS-Team/MOSS-Audio-Tokenizer",
        f"{HF}/OpenMOSS-Team/MOSS-Audio-Tokenizer のカードは license: apache-2.0"
        "(MOSS-TTS v1.5 の processor が既定で読む音声トークナイザ)",
        checked="2026-10-31",
    ),
    _s(
        "chatterbox-parts",
        "ResembleAI/chatterbox",
        r"(ve|s3gen|conds)\.pt",
        "mit",
        None,
        "ResembleAI/chatterbox",
        f"{HF}/ResembleAI/chatterbox のカードは license: mit(同じリポジトリの ve.pt・s3gen.pt・conds.pt。"
        "公式の src/chatterbox/mtl_tts.py が読む)",
        checked="2026-10-31",
    ),
    _s(
        "qwen3-tts-speech-tokenizer",
        ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
        r"model\.safetensors",
        "apache-2.0",
        None,
        "Qwen/Qwen3-TTS-Tokenizer-12Hz",
        f"speech_tokenizer/model.safetensors(sha256 836b7b35…)は {HF}/Qwen/Qwen3-TTS-Tokenizer-12Hz の "
        "model.safetensors と同じ。Qwen/Qwen3-TTS-Tokenizer-12Hz のカードは license: apache-2.0"
        "(置き場所の Qwen3-TTS-12Hz-*-CustomVoice も license: apache-2.0)",
        checked="2026-10-31",
    ),
    _s(
        "voxcpm2-audiovae",
        "openbmb/VoxCPM2",
        r"audiovae\.pth",
        "apache-2.0",
        None,
        "openbmb/VoxCPM2",
        f"{HF}/openbmb/VoxCPM2 のカードは license: apache-2.0(同じリポジトリの audiovae.pth。公式の "
        "src/voxcpm/model/voxcpm2.py が読む)",
        checked="2026-10-31",
    ),
    _s(
        "cogvideox-vae",
        "Comfy-Org/void-model",
        r"cogvideox_vae\.safetensors",
        "apache-2.0",
        None,
        "zai-org/CogVideoX-2b",
        f"sha256 が {HF}/alibaba-pai/CogVideoX-Fun-V1.5-5b-InP の vae/diffusion_pytorch_model.safetensors と"
        "同じ(bd47d57a…)。その README「CogVideoX-2B 模型 (包括其对应的Transformers模块，VAE模块) "
        "根据 Apache 2.0 "
        f"协议许可证发布」、元の {HF}/zai-org/CogVideoX-2b のカード「The CogVideoX-2B model (including its "
        "corresponding Transformers module and VAE module) is released under the Apache 2.0 License」"
        "(CogVideoX-2b と -5b の VAE は sha256 が同じ a410e48d…)",
        via=("alibaba-pai/CogVideoX-Fun-V1.5-5b-InP",),
    ),
    _s(
        "whisper-large-v3",
        "Comfy-Org/HuMo_ComfyUI",
        r"whisper_large_v3_fp16\.safetensors",
        "apache-2.0",
        None,
        "openai/whisper-large-v3",
        f"sha256 が {HF}/openai/whisper-large-v3 の model.safetensors と同じ(a8e94b85…)。"
        "openai/whisper-large-v3 の cardData の license は apache-2.0",
    ),
    _s(
        "pixeldit-gemma-2-2b-it",
        "Comfy-Org/PixelDiT",
        r"gemma_2_2b_it_elm_(bf16|fp16|fp8_scaled)\.safetensors",
        "gemma",
        None,
        "google/gemma-2-2b-it",
        f"元の {HF}/nvidia/PixelDiT-1300M-1024px のカード「Text encoder: Gemma-2-2B-IT」。"
        "google/gemma-2-2b-it "
        "は license: gemma(gated: manual)。Gemma 利用規約は派生(Model Derivatives)にもかかる",
        url="https://ai.google.dev/gemma/terms",
        via=("nvidia/PixelDiT-1300M-1024px",),
        gated=True,
    ),
    _s(
        "nvidia-pid",
        "Comfy-Org/PixelDiT",
        r"pid_flux1_\w+\.safetensors",
        "other",
        "nsclv1",
        "nvidia/PiD",
        f"{HF}/Comfy-Org/PixelDiT の cardData の base_model に nvidia/PiD。nvidia/PiD のカード「This "
        "model is "
        "released under the NSCLv1 License. The work and any derivative works may only be used for "
        "non-commercial (research or evaluation) purposes」(NSCLv1 の原文へのリンクは、カードが "
        f"{HF}/nvidia/PixelDiT-1300M-1024px/blob/main/LICENSE を指す)",
        url=f"{HF}/nvidia/PiD#licenseterms-of-use",
    ),
    _s(
        "infinitetalk-comfy",
        "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        r"wan2\.1_infiniteTalk_(multi|single)_fp16\.safetensors",
        "apache-2.0",
        None,
        "MeiGen-AI/InfiniteTalk",
        f"{HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged の README が元に {HF}/MeiGen-AI/InfiniteTalk を挙げ、"
        "model_patches/wan2.1_infiniteTalk_multi_fp16・_single_fp16 を置く。"
        "MeiGen-AI/InfiniteTalk のカードは "
        "license: apache-2.0 と「The models in this repository are licensed under the Apache 2.0 License」"
        "(自動の決め方は alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera をたどっていた。出どころを直す)",
        override=True,
    ),
    _s(
        "wan2.1-i2v-14b-480p",
        ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "Kijai/WanVideo_comfy_fp8_scaled"),
        r"(wan2\.1_i2v_480p_14B_(bf16|fp16|fp8_e4m3fn|fp8_scaled)"
        r"|Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ)\.safetensors",
        "apache-2.0",
        None,
        "Wan-AI/Wan2.1-I2V-14B-480P",
        f"{HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged の README が元に Wan-AI/Wan2.1-I2V-14B-480P を挙げ、"
        "wan2.1_i2v_480p_14B_* を置く。Kijai/WanVideo_comfy_fp8_scaled の I2V/Wan2_1-I2V-14B-480p_…_KJ は"
        "名前が同じモデルで、safetensors の見出しに img_emb(画像の入力)があり vace の層が無い(カードの "
        f"base_model の VACE ではない)。{HF}/Wan-AI/Wan2.1-I2V-14B-480P のカードは license: apache-2.0 と"
        "「The models in this repository are licensed under the Apache 2.0 License」"
        "(自動の決め方は 720P・VACE をたどっていた。出どころを直す)",
        url=f"{HF}/Wan-AI/Wan2.1-I2V-14B-480P/blob/main/LICENSE.txt",
        override=True,
    ),
    _s(
        "wan-dancer-14b",
        "Comfy-Org/Wan-Dancer",
        r"wan2\.2_dancer_14b_(global|local)_(fp8_scaled|bf16|fp16)\.safetensors",
        "apache-2.0",
        None,
        "Wan-AI/Wan-Dancer-14B",
        "公式の https://github.com/Wan-Video/Wan-Dancer の README の「HF Model」が "
        f"{HF}/Wan-AI/Wan-Dancer-14B(global_model・local_model を置く)。そのカードは license: apache-2.0 と"
        "「This project is licensed under the Apache 2.0 License」(Comfy-Org/Wan-Dancer の base_model "
        "Wan-AI/Wan2.2-Dancer-14B は匿名で読めない)",
    ),
    _s(
        "krea-2-style-reference-lora",
        "Comfy-Org/Krea-2",
        r"krea2_style_reference\.safetensors",
        "other",
        "krea-2-community-license",
        "ostris/krea2_turbo_style_reference",
        f"sha256 が {HF}/ostris/krea2_turbo_style_reference の krea2_style_reference.safetensors と同じ"
        "(f50df5a9…)。そのカードは other(krea-2-community-license、base_model krea/Krea-2-Turbo)",
        url=f"{HF}/krea/Krea-2-Turbo/blob/main/LICENSE.pdf",
    ),
    _s(
        "krea-2-darkbrush-lora",
        "Comfy-Org/Krea-2",
        r"krea2_darkbrush\.safetensors",
        "other",
        "krea-2-community-license",
        "krea/Krea-2-LoRA-darkbrush",
        f"{HF}/Comfy-Org/Krea-2 の README の「Original LoRA repositories」に krea/Krea-2-LoRA-darkbrush。"
        "その darkbrush.safetensors と大きさ(469,291,992 バイト)・重みの名前・形・型が同じ。"
        "krea/Krea-2-LoRA-darkbrush のカードは other(krea-2-community-license)"
        "(原文はそのリポジトリの LICENSE.pdf。カードの license_link は impressionist の LICENSE.pdf を指す)",
        url=f"{HF}/krea/Krea-2-LoRA-darkbrush/blob/main/LICENSE.pdf",
    ),
    _s(
        "krea-2-qwen3-vl-4b",
        "Comfy-Org/Krea-2",
        r"qwen3vl_4b_(bf16|fp8_scaled|int8_convrot)\.safetensors",
        "other",
        "krea-2-community-license",
        "krea/Krea-2-Turbo",
        f"{HF}/Comfy-Org/Krea-2 の元 {HF}/krea/Krea-2-Turbo(cardData other(krea-2-community-license))が"
        "text_encoder/model.safetensors(8,875,715,136 バイト。ComfyUI の bf16 は 8,875,719,384 バイト)を"
        "自分のライセンスで配る。元の Qwen3-VL(apache-2.0)より厳しいので Krea 2 のライセンスで持つ",
        url=f"{HF}/krea/Krea-2-Turbo/blob/main/LICENSE.pdf",
    ),
    _s(
        "omnivoice",
        "k2-fsa/OmniVoice",
        r"model\.safetensors",
        "other",
        "cc-by-nc",
        "k2-fsa/OmniVoice",
        "公式の https://github.com/k2-fsa/OmniVoice の README「Our code is released under the Apache 2.0 "
        "License. The pre-trained model is licensed under the CC-BY-NC due to constraints from its "
        "training data (e.g., Emilia)」(版の記載なし。HF のカードにはライセンスの記載が無い)",
        url="https://github.com/k2-fsa/OmniVoice#license",
    ),
    # --- circlestone-labs(Anima の公式 LoRA。#30 の確認で LICENSE.md があると分かった) ------------
    _s(
        "anima-official-loras",
        "circlestone-labs/Anima-Official-LoRAs",
        r"anima-[\w.-]+\.safetensors",
        "other",
        "circlestone-labs-non-commercial-v1.0",
        "circlestone-labs/Anima-Official-LoRAs",
        f"{HF}/circlestone-labs/Anima-Official-LoRAs/blob/main/LICENSE.md"
        "(2026-05-26 のコミット c221e431c8 で追加)"
        "の見出し「CircleStone Labs Non-Commercial License v1.0」、2.b「You may only access, use, "
        "Distribute, or create Derivatives of the CircleStone Model or Derivatives "
        "for Non-Commercial Purposes」。"
        "カードのメタデータ(license:)は空",
        url=f"{HF}/circlestone-labs/Anima-Official-LoRAs/blob/main/LICENSE.md",
    ),
)


def source_for(repo_id: str | None, filename: str) -> LicenseSource | None:
    """置き場所とファイル名に当たる行。本家を確かめられない部品・元を読めない系統の名前なら None。"""
    base = filename.rsplit("/", 1)[-1]
    if unverified_component_for(base) or lineage_for([base]):
        return None
    return next((s for s in SOURCES if s.matches(repo_id, base)), None)


def _file_repo(f: ModelFile) -> tuple[str, str] | None:
    p = parse_hf_url(f.url)
    if p:
        return p[0], p[2]
    return None


def apply_to_file(f: ModelFile) -> LicenseSource | None:
    """ライセンスが不明(None)のファイルに表を当てる。上書きの行(override)と、前にこの表で決めたファイルは
    決まっていても当て直す。
    当てた行(当てなければ・すでに同じ値なら None)。"""
    loc = _file_repo(f)
    if loc is None:
        return None
    repo_id, path = loc
    row = source_for(repo_id, path)
    # 表で決めたファイル(license_evidence に license_source:<行>)は、表の行を直したら当て直す
    from_table = f"{EVIDENCE_KEY}:{row.key}" in f.license_evidence if row is not None else False
    if row is None or (f.license is not None and not row.override and not from_table):
        return None
    res = row.resolved(repo_id)
    keep = [x for x in f.license_evidence if x.startswith(f"{COMPONENT_EVIDENCE}:")]
    if (f.license, f.license_name, f.license_from, f.license_url, f.license_path, f.license_evidence) == (
        res.license,
        res.license_name,
        res.source,
        res.url,
        list(res.path),
        [*res.evidence, *keep],
    ):
        return None  # 当て済み(冪等)
    from .update import set_file_license  # update がこのモジュールを import するので、ここで読む

    set_file_license(f, res)
    f.license_evidence += keep
    return row


def enforce_lineage(f: ModelFile) -> str | None:
    """元のライセンスを読めない系統(hf.LINEAGES)のファイルは、自動で決まっていても不明に戻す
    (gate.resolve_file_license と同じ名前で見る)。戻したら系統の名前。"""
    if f.license is None:
        return None
    from .gate import _lineage_names

    lin = lineage_for(_lineage_names(f))
    if lin is None:
        return None
    from .hf import UNVERIFIED

    f.license = f.license_name = f.license_url = None
    why = f"{UNVERIFIED}: 系統 {lin[0]}: {lin[1]}"
    f.license_evidence = [why, *(e for e in f.license_evidence if e != why)]
    return lin[0]


def apply_license_sources(db: Database | Iterable[ModelEntry]) -> list[tuple[str, str, str]]:
    """一覧(またはモデルの並び)の、ライセンスが不明のファイルに表を当て、一式のライセンスを決め直す。
    ネットには触らない。当てたもの [(モデル ID, ファイル名, 行の名前)] を返す。"""
    from .update import set_entry_license

    entries = db.models if hasattr(db, "models") else list(db)
    changed: list[tuple[str, str, str]] = []
    for e in entries:
        hit = False
        for f in e.files:
            row = apply_to_file(f)
            if row is not None:
                changed.append((e.id, f.name, row.key))
                hit = True
            lin = enforce_lineage(f)
            if lin is not None:
                changed.append((e.id, f.name, f"lineage:{lin}"))
                hit = True
        if hit:
            set_entry_license(e)
    return changed


# --- 当てたときに何が変わるか(python -m genmodelfit.license_sources) -----------------------------


def report(db: Database, *, include_uncensored: bool = False) -> dict[str, Any]:
    """表を当てる前と後で、ライセンス不明の項目の数と、gate が auto にする項目を比べる(db は書き換えない)。

    HEAD の期限で結果が日によって変わらないよう、gate は max_head_age_days=None で判定する。"""
    import copy

    from .gate import GatePolicy, evaluate_offline

    policy = GatePolicy(max_head_age_days=None)

    def _pick(d: Database) -> list[ModelEntry]:
        return [m for m in d.models if include_uncensored or m.content != "uncensored"]

    def _auto(d: Database) -> set[str]:
        return {m.id for m in _pick(d) if evaluate_offline(m, policy, sources=d.sources).auto}

    after = copy.deepcopy(db)
    applied = apply_license_sources(after)
    before_unknown = sorted(m.id for m in _pick(db) if m.license is None)
    after_unknown = sorted(m.id for m in _pick(after) if m.license is None)
    before_auto, after_auto = _auto(db), _auto(after)
    files_before = {(f.repo_id, f.name) for m in _pick(db) for f in m.files if f.license is None}
    files_after = {(f.repo_id, f.name) for m in _pick(after) for f in m.files if f.license is None}
    by_id = {m.id: m for m in _pick(after)}
    return {
        "entries": len(_pick(db)),
        "unknown_before": len(before_unknown),
        "unknown_after": len(after_unknown),
        "unknown_files_before": len(files_before),
        "unknown_files_after": len(files_after),
        "resolved_entries": sorted(set(before_unknown) - set(after_unknown)),
        "still_unknown": after_unknown,
        "auto_before": len(before_auto),
        "auto_after": len(after_auto),
        "newly_auto": sorted(after_auto - before_auto),
        "no_longer_auto": sorted(before_auto - after_auto),
        "applied": applied,
        "licenses_after": {
            i: license_label(by_id[i].license, by_id[i].license_name)
            for i in sorted(set(before_unknown) - set(after_unknown))
        },
    }


def main() -> None:
    from .db import load

    r = report(load(use_cache=False))
    print(tr(f"無検閲を除く {r['entries']} 項目", f"{r['entries']} entries, uncensored excluded"))
    print(
        tr(
            f"ライセンス不明の項目: {r['unknown_before']} → {r['unknown_after']}",
            f"entries with an unknown license: {r['unknown_before']} -> {r['unknown_after']}",
        )
    )
    print(
        tr(
            f"ライセンス不明のファイル(置き場所・名前): {r['unknown_files_before']} "
            f"→ {r['unknown_files_after']}",
            f"files with an unknown license (repository, name): {r['unknown_files_before']}"
            f" -> {r['unknown_files_after']}",
        )
    )
    print(
        tr(
            f"auto(HEAD の期限なし): {r['auto_before']} → {r['auto_after']}",
            f"auto (no HEAD age limit): {r['auto_before']} -> {r['auto_after']}",
        )
    )
    for i in r["newly_auto"]:
        print(tr(f"  新しく auto: {i}", f"  newly auto: {i}"))
    for i in r["no_longer_auto"]:
        print(tr(f"  auto でなくなった: {i}", f"  no longer auto: {i}"))
    print(tr("ライセンスが決まった項目:", "entries whose license was resolved:"))
    for i, lab in r["licenses_after"].items():
        print(f"  {i}: {lab}")
    rows: dict[str, int] = {}
    for _, _, k in r["applied"]:
        rows[k] = rows.get(k, 0) + 1
    print(tr("当てた行(ファイルののべ数):", "rows applied (file count):"))
    for k, n in sorted(rows.items()):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
