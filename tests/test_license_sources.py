"""出どころの表(license_sources.py、#28)の検査。

- 実在するファイル名(同梱の一覧に出てくるもの)で、表が当てるライセンスを人が確かめた値と比べる
- 表に無いファイルには何も当てない(置き場所が違う・HF 以外・すでに決まっている・確かめられない部品)
- 寛容なライセンスは、根拠の文にそのライセンスが書いてある行からしか出ない
- 同梱の一覧に当てて、新しく auto になる項目が、人が確かめた一覧の中にだけあること
"""

from __future__ import annotations

import copy

import pytest

from genmodelfit import license_sources as ls
from genmodelfit.db import load
from genmodelfit.gate import GatePolicy, evaluate_offline, resolve_file_license
from genmodelfit.hf import (
    PERMISSIVE,
    TIER_PERMISSIVE,
    RepoInfo,
    Resolved,
    is_repackager,
    license_tier,
    lineage_for,
    parse_hf_url,
)
from genmodelfit.models import ModelEntry, ModelFile
from genmodelfit.update import apply_license

NC = "cc-by-nc-4.0"
HUNYUAN = ("other", "tencent-hunyuan-community")
KREA = ("other", "krea-2-community-license")

# (置き場所, ファイル名, ライセンス, license_name)。2026-10-20 に一次資料で確かめた値(根拠は表の evidence)
W22 = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
W21 = "Comfy-Org/Wan_2.1_ComfyUI_repackaged"
_DM = "split_files/diffusion_models/"
QE = "Comfy-Org/Qwen-Image-Edit_ComfyUI"

CASES = [
    # 上書きの行(override。自動の決め方が寛容な側に誤る・違う元をたどるもの。0.1.1 のレビュー)
    (
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "split_files/loras/chronoedit_distill_lora.safetensors",
        "other",
        "nvidia-open-model-license",
    ),
    (
        "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "split_files/model_patches/wan2.1_infiniteTalk_multi_fp16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "split_files/diffusion_models/wan2.1_i2v_480p_14B_fp16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Kijai/WanVideo_comfy_fp8_scaled",
        "I2V/Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors",
        "apache-2.0",
        None,
    ),
    ("Kijai/WanVideo_comfy", "Wan21_CausVid_14B_T2V_lora_rank32.safetensors", NC, None),
    ("Kijai/WanVideo_comfy", "Wan21_CausVid_14B_T2V_lora_rank32_v2.safetensors", NC, None),
    ("Kijai/WanVideo_comfy", "Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors", NC, None),
    (
        "Kijai/WanVideo_comfy",
        "Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Kijai/WanVideo_comfy",
        "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Comfy-Org/Wan-Animate-2",
        "loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Kijai/WanVideo_comfy",
        "Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Kijai/WanVideo_comfy",
        "LoRAs/Wan22_relight/WanAnimate_relight_lora_fp16.safetensors",
        "apache-2.0",
        None,
    ),
    (
        "Kijai/WanVideo_comfy_fp8_scaled",
        "WanMove/Wan21-WanMove_fp8_scaled_e4m3fn_KJ.safetensors",
        "apache-2.0",
        None,
    ),
    ("Comfy-Org/Wan-Animate-2", "wan_animate_2_int8_convrot.safetensors", "apache-2.0", None),
    ("Comfy-Org/Wan-Animate-2", "wan_animate_2_distill_int8_convrot.safetensors", "apache-2.0", None),
    (
        "Comfy-Org/MiniMax-Music-3",
        "minimax_music3_dav.safetensors",
        "other",
        "minimax-music3-community-license",
    ),
    (
        "Comfy-Org/MiniMax-Music-3",
        "minimax_music3_dit_fp16.safetensors",
        "other",
        "minimax-music3-community-license",
    ),
    (
        "Comfy-Org/MiniMax-Music-3",
        "minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
        "other",
        "minimax-music3-community-license",
    ),
    ("Comfy-Org/HunyuanVideo_repackaged", "hunyuan_video_t2v_720p_bf16.safetensors", *HUNYUAN),
    ("Comfy-Org/HunyuanVideo_repackaged", "hunyuan_video_vae_bf16.safetensors", *HUNYUAN),
    ("Kijai/HunyuanVideo_comfy", "hunyuan_video_vae_bf16.safetensors", *HUNYUAN),
    ("Comfy-Org/NewBie-image-Exp0.1_repackaged", "gemma_3_4b_it_bf16.safetensors", "gemma", None),
    ("Comfy-Org/NewBie-image-Exp0.1_repackaged", "jina_clip_v2_bf16.safetensors", NC, None),
    (
        "Kijai/LTXV2_comfy",
        "ltx-2-19b-distilled_transformer_only_bf16.safetensors",
        "other",
        "ltx-2-community-license-agreement",
    ),
    (
        "Kijai/LTXV2_comfy",
        "ltx-2-19b-embeddings_connector_distill_bf16.safetensors",
        "other",
        "ltx-2-community-license-agreement",
    ),
    ("Comfy-Org/ltx-2", "gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors", "gemma", None),
    ("Comfy-Org/HunyuanImage_2.1_ComfyUI", "qwen_2.5_vl_7b.safetensors", "apache-2.0", None),
    ("circlestone-labs/Anima", "qwen_3_06b_base.safetensors", "apache-2.0", None),
    (
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "chrono_edit_14B_fp16.safetensors",
        "other",
        "nvidia-open-model-license",
    ),
    ("Comfy-Org/ERNIE-Image", "ministral-3-3b.safetensors", "apache-2.0", None),
    (
        "ByteZSzn/Flux.2-Turbo-ComfyUI",
        "Flux_2-Turbo-LoRA_comfyui.safetensors",
        "other",
        "flux-dev-non-commercial-license",
    ),
    ("Comfy-Org/flux2-dev", "mistral_3_small_flux2_bf16.safetensors", "apache-2.0", None),
    ("Comfy-Org/flux2-dev", "mistral_3_small_flux2_fp8.safetensors", "apache-2.0", None),
    ("Comfy-Org/flux2-klein-9B", "qwen_3_8b_fp8mixed.safetensors", "apache-2.0", None),
    ("Comfy-Org/vae-text-encorder-for-flux-klein-9b", "qwen_3_8b.safetensors", "apache-2.0", None),
    (
        "black-forest-labs/flux-3-image-klein-comfy",
        "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors",
        "other",
        "flux-kommunity-license",
    ),
    ("Comfy-Org/MOSS-Image", "qwen3.5_9b_w4a8_gptq.safetensors", "apache-2.0", None),
    ("Kijai/SUPIR_pruned", "SUPIR-v0Q_fp16.safetensors", "other", "supir-non-commercial"),
    ("Comfy-Org/stable-audio-3", "t5gemma_b_b_ul2.safetensors", "gemma", None),
    ("OpenMOSS-Team/MOSS-Audio-Tokenizer", "model-00001-of-00002.safetensors", "apache-2.0", None),
    ("ResembleAI/chatterbox", "s3gen.pt", "mit", None),
    ("ResembleAI/chatterbox", "ve.pt", "mit", None),
    ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "speech_tokenizer/model.safetensors", "apache-2.0", None),
    ("openbmb/VoxCPM2", "audiovae.pth", "apache-2.0", None),
    ("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "llm.pt", "apache-2.0", None),
    (
        "k2-fsa/OmniVoice",
        "audio_tokenizer/model.safetensors",
        "other",
        "boson-higgs-audio-2-community-license",
    ),
    ("Serveurperso/Qwen3-TTS-GGUF", "qwen-tokenizer-12hz-Q8_0.gguf", "apache-2.0", None),
    ("audio-cpp/AuK-Base-and-Flash-GGUF", "qwen2.5-omni-3b-q8_0.gguf", "other", "qwen-research"),
    (W22, f"{_DM}wan2.2_fun_control_high_noise_14B_fp8_scaled.safetensors", "apache-2.0", None),
    (W22, "split_files/diffusion_models/wan2.2_fun_control_5B_bf16.safetensors", "apache-2.0", None),
    (W22, f"{_DM}wan2.2_fun_inpaint_low_noise_14B_bf16.safetensors", "apache-2.0", None),
    (W22, "split_files/diffusion_models/wan2.2_fun_inpaint_5B_bf16.safetensors", "apache-2.0", None),
    (W21, "split_files/diffusion_models/wan2.1_fun_control_1.3B_bf16.safetensors", "apache-2.0", None),
    (W21, "split_files/diffusion_models/wan2.1_fun_camera_v1.1_1.3B_bf16.safetensors", "apache-2.0", None),
    ("Comfy-Org/Qwen-Image_ComfyUI", f"{_DM}qwen_image_fp8_e4m3fn.safetensors", "apache-2.0", None),
    ("Comfy-Org/Qwen-Image-Edit_ComfyUI", f"{_DM}qwen_image_edit_fp8_e4m3fn.safetensors", "apache-2.0", None),
    (
        "Comfy-Org/Qwen-Image-Edit_ComfyUI",
        f"{_DM}qwen_image_edit_2509_fp8_e4m3fn.safetensors",
        "apache-2.0",
        None,
    ),
    ("Comfy-Org/ERNIE-Image", "diffusion_models/ernie-image.safetensors", "apache-2.0", None),
    ("Comfy-Org/LongCat-Image", f"{_DM}longcat_image_bf16.safetensors", "apache-2.0", None),
    ("Kijai/WanVideo_comfy", "Wan2_1-I2V-ATI-14B_fp8_e4m3fn.safetensors", "apache-2.0", None),
    ("Comfy-Org/void-model", "vae/cogvideox_vae.safetensors", "apache-2.0", None),
    ("Comfy-Org/HuMo_ComfyUI", "whisper_large_v3_fp16.safetensors", "apache-2.0", None),
    ("Comfy-Org/PixelDiT", "gemma_2_2b_it_elm_bf16.safetensors", "gemma", None),
    ("Comfy-Org/PixelDiT", "pid_flux1_1024_to_4096_4step_bf16.safetensors", "other", "nsclv1"),
    ("Comfy-Org/Wan-Dancer", "wan2.2_dancer_14b_global_fp8_scaled.safetensors", "apache-2.0", None),
    ("Comfy-Org/Wan-Dancer", "wan2.2_dancer_14b_local_fp8_scaled.safetensors", "apache-2.0", None),
    ("Comfy-Org/Krea-2", "krea2_style_reference.safetensors", *KREA),
    ("Comfy-Org/Krea-2", "krea2_darkbrush.safetensors", *KREA),
    ("Comfy-Org/Krea-2", "qwen3vl_4b_fp8_scaled.safetensors", *KREA),
    ("k2-fsa/OmniVoice", "model.safetensors", "other", "cc-by-nc"),
    (
        "circlestone-labs/Anima-Official-LoRAs",
        "anima-turbo-lora-v0.2.safetensors",
        "other",
        "circlestone-labs-non-commercial-v1.0",
    ),
]

# 表に当たらない(不明のまま)ことを確かめたもの。(置き場所, ファイル名)。理由は注に書く
NOT_COVERED = [
    # 本家 microsoft/Mage-Flow・microsoft/Lens は匿名で読めない(401)。
    # Lens の GitHub は MIT だが重みの記載が無い
    ("Comfy-Org/Mage-Flow", "mage_flow_int8_convrot.safetensors"),
    ("Comfy-Org/Lens", "lens_bf16.safetensors"),
    # Lens のテキストエンコーダは microsoft/Lens の text_encoder から読む(gpt-oss そのままとは確かめられない)
    ("Comfy-Org/Lens", "gpt_oss_20b_nvfp4.safetensors"),
    # 本家を確かめられない部品・元を読めない系統
    ("Comfy-Org/SDPose", "rt_detr_v4-x-hgnet_fp16.safetensors"),
    ("Comfy-Org/SDPose", "sdpose_wholebody_fp16.safetensors"),
    ("Comfy-Org/lotus", "lotus-depth-d-v1-1.safetensors"),
    ("Comfy-Org/void-model", "void_pass1.safetensors"),
    # ByteZSzn の v2 は fal の LoRA と sha256 が違い、ByteZSzn にライセンスの記載が無い
    ("Comfy-Org/flux2-dev", "Flux2TurboComfyv2.safetensors"),
    # Mistral の FLUX.2 [dev] 用でも、置き場所が違えば当てない
    ("Comfy-Org/flux2-klein-9B", "mistral_3_small_flux2_bf16.safetensors"),
    # lightx2v の GitHub は Apache-2.0 だが、重みは MiniMax-H3(独自のライセンス)の派生
    ("lightx2v/Minimax-h3-Turbo", "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"),
    # HunyuanVideo のテキストエンコーダ(xtuner/llava-llama-3-8b にライセンスの記載が無い)
    ("Comfy-Org/HunyuanVideo_repackaged", "llava_llama3_fp8_scaled.safetensors"),
    # FILM の重みは GitHub の外(Google Drive)で、重みのライセンスの記載が無い
    ("Comfy-Org/frame_interpolation", "film_net_fp16.safetensors"),
    # 同じ名前でも、表に無い置き場所
    ("someone/WanVideo_copy", "Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors"),
    ("someone/Anima", "qwen_3_06b_base.safetensors"),
]


def _url(repo: str, name: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{name}"


def test_cases_are_many():
    assert len(CASES) >= 30


@pytest.mark.parametrize("repo,name,lic,lic_name", CASES, ids=[f"{c[0]}:{c[1]}" for c in CASES])
def test_table_license_for_real_files(repo, name, lic, lic_name):
    row = ls.source_for(repo, name)
    assert row is not None, (repo, name)
    assert (row.license, row.license_name) == (lic, lic_name)
    f = ModelFile(name=name.rsplit("/", 1)[-1], url=_url(repo, name), repo_id=repo)
    assert ls.apply_to_file(f) is row
    assert (f.license, f.license_name) == (lic, lic_name)
    assert f.license_from == row.source and f.license_url == row.url
    assert f.license_path[0] == repo and f.license_path[-1] == row.source
    assert f"{ls.EVIDENCE_KEY}:{row.key}" in f.license_evidence
    assert any(row.checked in e for e in f.license_evidence)


@pytest.mark.parametrize("repo,name", NOT_COVERED, ids=[f"{c[0]}:{c[1]}" for c in NOT_COVERED])
def test_not_covered_stays_unknown(repo, name):
    assert ls.source_for(repo, name) is None
    f = ModelFile(name=name, url=_url(repo, name), repo_id=repo)
    assert ls.apply_to_file(f) is None
    assert f.license is None and f.license_from is None and f.license_evidence == []


def test_never_overwrites_known_or_non_hf():
    repo, name = "Comfy-Org/Wan-Animate-2", "wan_animate_2_int8_convrot.safetensors"
    known = ModelFile(name=name, url=_url(repo, name), repo_id=repo, license="other", license_name="x")
    assert ls.apply_to_file(known) is None and (known.license, known.license_name) == ("other", "x")
    civitai = ModelFile(name=name, url="https://civitai.com/api/download/models/1", repo_id=None)
    assert ls.apply_to_file(civitai) is None and civitai.license is None
    no_url = ModelFile(name=name)
    assert ls.apply_to_file(no_url) is None and no_url.license is None


def test_permissive_only_from_evidence_naming_the_license():
    names = {
        "apache-2.0": ("apache-2.0", "apache license 2.0", "apache 2.0"),
        "mit": ("mit license", "license: mit"),
    }
    for row in ls.SOURCES:
        if license_tier(row.license, row.license_name) != TIER_PERMISSIVE:
            continue
        assert row.license in PERMISSIVE
        text = row.evidence.lower()
        assert any(n in text for n in names[row.license]), row.key


def test_rows_are_well_formed():
    keys = [r.key for r in ls.SOURCES]
    assert len(keys) == len(set(keys))
    for row in ls.SOURCES:
        # 元は再配布のリポジトリにしない
        # (gate が「再配布側の表記だけ」として落とすため。元をたどったことが要る)
        assert not is_repackager(row.source), row.key
        assert row.repos and all("/" in r for r in row.repos), row.key
        assert row.evidence and "http" in row.evidence + row.url, row.key
        assert row.license == "other" or row.license_name is None, row.key
        if row.license == "other":
            assert row.license_name, row.key
    # 表に載っている行が、表に出てくるすべての行を少なくとも 1 つの実例で確かめている
    covered = {ls.source_for(r, n).key for r, n, _, _ in CASES}
    assert covered == set(keys)


def test_lineage_and_unverified_never_match():
    # 元を読めない系統の名前は、表に行があっても当てない(VOID・SDPose・Lotus)
    assert lineage_for(["void_pass1.safetensors"])
    assert lineage_for(["netflix/void-model"])
    assert lineage_for(["cogvideox_vae.safetensors"]) is None


def test_update_uses_table_only_when_unknown():
    repo, path = "Comfy-Org/Wan-Animate-2", "diffusion_models/wan_animate_2_int8_convrot.safetensors"

    class Client:
        def __init__(self, res):
            self.res = res

        def resolve_file(self, info, p, *, discovered_uncensored=False):
            return self.res

    info = RepoInfo(repo_id=repo, license="apache-2.0")
    f = ModelFile(name=path.rsplit("/", 1)[-1], url=_url(repo, path), repo_id=repo)
    unknown = Resolved(None, None, repo, None, [repo], ["license_unverified: x"])
    apply_license(f, info, Client(unknown), path)
    assert f.license == "apache-2.0" and f.license_from == "Wan-AI/Wan2.2-Animate-2-14B"
    # 自動で決まったものは表で上書きしない
    g = ModelFile(name=f.name, url=f.url, repo_id=repo)
    known = Resolved("other", "y", "a/b", None, [repo, "a/b"], [])
    apply_license(g, info, Client(known), path)
    assert (g.license, g.license_name, g.license_from) == ("other", "y", "a/b")


# --- 同梱の一覧に当てる -------------------------------------------------------------------------

# 表を当てて新しく auto になってよい項目(2026-10-20 に全ファイルのライセンスを人が確かめた)。
# ここに無い項目が auto になったら、表の行を見直す(寛容の側に間違えていないか)
JUSTIFIED_AUTO = {
    # baidu/ERNIE-Image・-Turbo(apache-2.0)、ministral-3-3b(表)、FLUX.2 VAE(apache-2.0)
    "image_ernie_image",
    "image_ernie_image_turbo",
    # OpenMOSS-Team/MOSS-Image-Base・-SFT(apache-2.0)、Qwen3.5-9B(表)、FLUX.2 small decoder(apache-2.0)
    "image_moss_image_base_t2i",
    "image_moss_image_base_t2i_bboxes",
    "image_moss_image_t2i",
    "image_moss_image_t2i_bboxes",
    # ByteDance/Bernini-R(apache-2.0)、lightx2v の T2V v2 LoRA(表)、UMT5・Wan 2.1 VAE
    "video_bernini_r_image_editing",
    "video_bernini_r_video_editing",
    # bytedance-research/HuMo(apache-2.0)、whisper-large-v3(表)、lightx2v の I2V LoRA(表)
    "video_humo",
    # htdong/Wan-Alpha(apache-2.0)、Wan2.1-T2V-14B、lightx2v の T2V v2 LoRA(表)
    "video_wan2.1_alpha_t2v_14B",
    # MeiGen-AI/InfiniteTalk(apache-2.0)、chinese-wav2vec2-base(mit)、lightx2v の I2V LoRA(表)
    "video_wan2_1_infinitetalk",
    # Wan-AI/Wan2.2-Animate-2-14B(表)、lightx2v の I2V LoRA(表)、CLIP-ViT-H(mit)
    "video_wan_animate2",
    "video_wan_animate2_distilled",
    # Wan-AI/Wan-Dancer-14B(表)
    "video_wan_dancer",
    # Wan-AI/Wan2.1-VACE-14B(apache-2.0)、lightx2v の I2V LoRA(表)
    "video_wan_vace_flf2v",
    # Ruihang/Wan-Move-14B-480P(表)、lightx2v の I2V LoRA(表)
    "video_wanmove_480p",
}


@pytest.fixture(scope="module")
def bundled():
    return load(use_cache=False)


def test_apply_to_bundled_db_only_touches_unknown_files_in_table(bundled):
    after = copy.deepcopy(bundled)
    applied = ls.apply_license_sources(after)
    for m0, m1 in zip(bundled.models, after.models, strict=True):
        for f0, f1 in zip(m0.files, m1.files, strict=True):
            loc = parse_hf_url(f0.url)
            row = ls.source_for(loc[0], loc[2]) if loc else None
            if f0.license is None and row is not None:
                assert (f1.license, f1.license_name) == (row.license, row.license_name)
            else:
                assert f1 == f0, (m0.id, f0.name)
    # 同梱の一覧には当て済みなので、当て直しても何も変わらない(冪等)
    assert applied == []


def test_reapplying_restores_cleared_licenses(bundled):
    """表が受け持つファイルのライセンスを消してから当て直すと、同梱の一覧と同じ値に戻る。"""
    cleared = copy.deepcopy(bundled)
    n = 0
    for m in cleared.models:
        for f in m.files:
            loc = parse_hf_url(f.url)
            if loc and ls.source_for(loc[0], loc[2]) is not None and f.license is not None:
                f.license = f.license_name = f.license_url = None
                n += 1
    assert n > 0
    ls.apply_license_sources(cleared)
    for m0, m1 in zip(bundled.models, cleared.models, strict=True):
        for f0, f1 in zip(m0.files, m1.files, strict=True):
            assert (f1.license, f1.license_name) == (f0.license, f0.license_name), (m0.id, f0.name)


def _before_table(bundled):
    """表(上書きでない行)で決めたファイルのライセンスを消した一覧(表を当てる前の状態)。上書きの行のファイルは
    当てる前も自動で決まっていたので、そのまま残す。"""
    from genmodelfit.update import set_entry_license

    cleared = copy.deepcopy(bundled)
    for m in cleared.models:
        hit = False
        for f in m.files:
            keys = [e.split(":", 1)[1] for e in f.license_evidence if e.startswith(f"{ls.EVIDENCE_KEY}:")]
            rows = [r for r in ls.SOURCES if r.key in keys]
            if rows and not rows[0].override:
                f.license = f.license_name = f.license_url = None
                hit = True
        if hit:
            set_entry_license(m)
    return cleared


def test_newly_auto_is_exactly_the_justified_list(bundled):
    """表を当てる前(表で決めたファイルを消した一覧)と当てた後で、新しく auto になる項目が JUSTIFIED_AUTO と
    ちょうど同じ(当て済みの一覧どうしを比べると空になって何も確かめないので、当てる前を作り直す)。"""
    r = ls.report(_before_table(bundled))
    assert r["auto_after"] > r["auto_before"]
    assert set(r["newly_auto"]) == JUSTIFIED_AUTO, set(r["newly_auto"]) ^ JUSTIFIED_AUTO
    assert r["no_longer_auto"] == []


def test_override_rows_fix_permissive_errors_and_provenance(bundled):
    """ChronoEdit の LoRA は自動の決め方では apache-2.0(cardData)だが、本文の NVIDIA Open Model License で
    上書きする。InfiniteTalk・Wan 2.1 I2V 480P は出どころを本家に直す。"""
    f = ModelFile(
        name="chronoedit_distill_lora.safetensors",
        url=_url(
            "Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/loras/chronoedit_distill_lora.safetensors"
        ),
        repo_id="Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        license="apache-2.0",
        license_from="nvidia/ChronoEdit-14B-Diffusers",
    )
    row = ls.apply_to_file(f)
    assert row is not None and row.override
    assert (f.license, f.license_name) == ("other", "nvidia-open-model-license")
    assert ls.apply_to_file(f) is None  # 当て済みなら何もしない
    m = bundled.get("image_chrono_edit_14B")
    assert all(x.license == "other" for x in m.files if "chrono" in x.name)
    assert not evaluate_offline(m, GatePolicy(max_head_age_days=None), sources=bundled.sources).auto
    talk = bundled.get("video_wan2_1_infinitetalk")
    froms = {x.name: x.license_from for x in talk.files}
    assert froms["wan2.1_infiniteTalk_multi_fp16.safetensors"] == "MeiGen-AI/InfiniteTalk"
    assert froms["Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors"] == "Wan-AI/Wan2.1-I2V-14B-480P"
    assert talk.base_model == "Wan-AI/Wan2.1-I2V-14B-480P"
    i2v = bundled.get("image_to_video_wan")
    assert {x.license_from for x in i2v.files if "i2v_480p" in x.name} == {"Wan-AI/Wan2.1-I2V-14B-480P"}


def test_update_overrides_when_the_row_says_so():
    repo, path = (
        "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
        "split_files/loras/chronoedit_distill_lora.safetensors",
    )

    class Client:
        def resolve_file(self, info, p, *, discovered_uncensored=False):
            return Resolved("apache-2.0", None, "nvidia/ChronoEdit-14B-Diffusers", None, [repo], [])

    f = ModelFile(name=path.rsplit("/", 1)[-1], url=_url(repo, path), repo_id=repo)
    apply_license(f, RepoInfo(repo_id=repo, license="apache-2.0"), Client(), path)
    assert (f.license, f.license_name) == ("other", "nvidia-open-model-license")


def test_renamed_repo_uses_the_url_name():
    """HF でリポジトリの名前が変わると、API は新しい名前を返す(2026-10 に Comfy-Org/flux2-klein-9B →
    vae-text-encorder-for-flux-klein-9b)。update は表をファイルの URL の名前で当て、
    当て直しても変わらない(#19)。"""
    old, new = "Comfy-Org/flux2-klein-9B", "Comfy-Org/vae-text-encorder-for-flux-klein-9b"
    path = "split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors"

    class Client:
        def resolve_file(self, info, p, *, discovered_uncensored=False):
            return Resolved(None, None, new, None, [new], ["license_unverified: x"])

    f = ModelFile(name=path.rsplit("/", 1)[-1], url=_url(old, path), repo_id=old)
    apply_license(f, RepoInfo(repo_id=new, license="other"), Client(), path, url_repo=old)
    assert f.license == "apache-2.0" and f.license_path[0] == old
    e = ModelEntry(id="x", name="x", modality="image", files=[f])
    assert ls.apply_license_sources([e]) == []  # 当て直しても同じ(冪等)


def test_lineage_files_are_unknown_in_the_data(bundled):
    """元を読めない系統(VOID)は、一覧のデータでも不明(gate と同じ答え)。"""
    m = bundled.get("utility_void_video_inpainting")
    for f in m.files:
        if "void_pass" in f.name or "raft_large" in f.name:
            assert f.license is None and any("void-model" in e for e in f.license_evidence), f.name
    g = ModelFile(name="void_pass1.safetensors", license="apache-2.0", license_from="netflix/void-model")
    assert ls.enforce_lineage(g) == "void-model" and g.license is None


def test_entry_license_prefers_more_obligations_in_the_same_tier(bundled):
    from genmodelfit.hf import strictest
    from genmodelfit.update import set_entry_license

    e = ModelEntry(
        id="x",
        name="x",
        modality="video",
        files=[ModelFile(name="a", license="mit"), ModelFile(name="b", license="apache-2.0")],
    )
    set_entry_license(e)
    assert e.license == "apache-2.0"
    assert strictest(["mit", "apache-2.0", "bsd-3-clause"]) == "apache-2.0"
    assert strictest(["mit", "cc-by-nc-4.0"]) == "cc-by-nc-4.0"  # 段階が先
    mixed = [m for m in bundled.models if {f.license for f in m.files} >= {"apache-2.0", "mit"}]
    assert mixed and all(m.license != "mit" for m in mixed)


def test_report_on_bundled_db(bundled):
    r = ls.report(bundled)
    assert r["unknown_after"] <= r["unknown_before"]
    assert r["unknown_files_after"] <= r["unknown_files_before"]
    assert r["no_longer_auto"] == []
    assert set(r["newly_auto"]) <= JUSTIFIED_AUTO, set(r["newly_auto"]) - JUSTIFIED_AUTO


def test_justified_auto_have_only_verified_files(bundled):
    after = copy.deepcopy(bundled)
    ls.apply_license_sources(after)
    policy = GatePolicy(max_head_age_days=None)
    for mid in JUSTIFIED_AUTO:
        m = after.get(mid)
        assert m is not None, mid
        assert evaluate_offline(m, policy, sources=after.sources).auto, mid
        for f in m.files:
            assert resolve_file_license(f).license in PERMISSIVE, (mid, f.name)


def test_void_model_is_held_by_lineage(bundled):
    m = bundled.get("utility_void_video_inpainting")
    if m is None:
        pytest.skip("一覧に VOID が無い")
    mains = [f for f in m.files if "void_pass" in f.name]
    assert mains and all(resolve_file_license(f).license is None for f in mains)


def test_entries_api_accepts_list():
    f = ModelFile(
        name="ministral-3-3b.safetensors",
        url=_url("Comfy-Org/ERNIE-Image", "text_encoders/ministral-3-3b.safetensors"),
        repo_id="Comfy-Org/ERNIE-Image",
    )
    e = ModelEntry(id="x", name="x", modality="image", files=[f])
    changed = ls.apply_license_sources([e])
    assert changed == [("x", "ministral-3-3b.safetensors", "ernie-image-ministral-3-3b")]
    assert e.license == "apache-2.0"


@pytest.mark.parametrize(
    ("mid", "origin"),
    [
        ("video_wan2_2_14B_fun_control", "alibaba-pai/Wan2.2-Fun-A14B-Control"),
        ("video_wan2_2_14B_fun_camera", "alibaba-pai/Wan2.2-Fun-A14B-Control-Camera"),
        ("video_wan2_2_5B_fun_control", "alibaba-pai/Wan2.2-Fun-5B-Control"),
        ("video_wan2_2_14B_fun_inpaint", "alibaba-pai/Wan2.2-Fun-A14B-InP"),
        ("video_wan2_2_5B_fun_inpaint", "alibaba-pai/Wan2.2-Fun-5B-InP"),
        ("wan2.1_fun_control", "alibaba-pai/Wan2.1-Fun-V1.1-1.3B-Control"),
        ("video_wan2.1_fun_camera_v1.1_1.3B", "alibaba-pai/Wan2.1-Fun-V1.1-1.3B-Control-Camera"),
        ("video_wan2.1_fun_camera_v1.1_14B", "alibaba-pai/Wan2.1-Fun-V1.1-14B-Control-Camera"),
        ("image_qwen_image", "Qwen/Qwen-Image"),
        ("image_qwen_image_edit", "Qwen/Qwen-Image-Edit"),
        ("image_qwen_image_edit_2509", "Qwen/Qwen-Image-Edit-2509"),
        ("image_ernie_image", "baidu/ERNIE-Image"),
        ("image_longcat_text_to_image", "meituan-longcat/LongCat-Image"),
        ("video_wan_ati", "bytedance-research/ATI"),
        ("template-Animation_Trajectory_Control_Wan_ATI", "bytedance-research/ATI"),
    ],
)
def test_main_files_follow_their_own_origin(bundled, mid, origin):
    """Wan の Fun の本体は、再配布の README の元のうち自分の版をたどる(名前の重なりの同点で別の版に
    ならない。#144)。テンプレートの base_model もその元。"""
    m = bundled.get(mid)
    mains = [f for f in m.files if f.role == "main"]
    assert mains and all(f.license_from == origin for f in mains), [f.license_from for f in mains]
    assert m.base_model == origin


def test_apply_rederives_the_template_base_model_from_the_new_origin():
    """表で本体の元が変わったら、テンプレートの base_model(本体の元から決めたもの)も決め直す(作り直しと
    同じ答え。#144)。手で書いた base_model(手のモデル)と HF のカードの base_model は変えない。"""
    path = "split_files/diffusion_models/wan2.2_fun_control_5B_bf16.safetensors"
    old = "alibaba-pai/Wan2.2-Fun-A14B-Control-Camera"

    def entry(source):
        f = ModelFile(
            name=path.rsplit("/", 1)[-1],
            url=_url(W22, path),
            repo_id=W22,
            role="main",
            license="apache-2.0",
            license_from=old,
            license_evidence=["family:wan"],
        )
        return ModelEntry(id="x", name="x", modality="video", source=source, files=[f], base_model=old)

    t = entry("comfyui-template")
    ls.apply_license_sources([t])
    assert t.files[0].license_from == t.base_model == "alibaba-pai/Wan2.2-Fun-5B-Control"
    hf = entry("huggingface")
    ls.apply_license_sources([hf])
    assert hf.files[0].license_from == "alibaba-pai/Wan2.2-Fun-5B-Control" and hf.base_model == old
