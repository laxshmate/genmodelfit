"""実在するモデルで、gate の結果を人が確かめたテスト(Issue #3 の受け入れ基準)。

同梱の一覧(src/genmodelfit/data/db.json。HEAD の結果つき)を読む。ライセンスは一覧の値ではなく、
2026-10-20 に各モデルカード(HF の cardData / LICENSE)を人が読んで確かめたものを根拠に、期待する結果を書いた。
一覧の値がカードと違ったものは、その旨を注に書いた(#13 のレビューで直ったものを含む)。

期待する結果の書き方: auto=True なら保留の理由は無い。False なら held_by の先頭(表の held:<id>)と、
落ちるべき条件の全部を書く。
"""

from __future__ import annotations

import pytest
from frozenlist import frozen_db

from genmodelfit.db import load
from genmodelfit.gate import evaluate_offline


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


# (モデル ID, auto, 先頭の保留理由, 落ちるべき条件の全部)
CASES = [
    # Z-Image Turbo: apache-2.0 https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
    #   テキストエンコーダ Qwen3-4B: apache-2.0 https://huggingface.co/Qwen/Qwen3-4B
    ("image_z_image_turbo", True, None, set()),
    # FLUX.1 [schnell]: apache-2.0 https://huggingface.co/black-forest-labs/FLUX.1-schnell
    #   (ファイルは 1 つにまとめた checkpoint)
    ("flux_schnell", True, None, set()),
    # Wan2.1 T2V 1.3B: apache-2.0 https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B
    #   UMT5-XXL: apache-2.0 https://huggingface.co/google/umt5-xxl
    ("text_to_video_wan", True, None, set()),
    # Wan2.2 TI2V 5B: apache-2.0 https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B
    ("video_wan2_2_5B_ti2v", True, None, set()),
    # Wan2.2 T2V A14B: apache-2.0 https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B
    #   4 ステップの LoRA: apache-2.0 https://huggingface.co/lightx2v/Wan2.2-Lightning
    #   Wan2.1 VAE: apache-2.0 https://huggingface.co/Wan-AI/Wan2.1-T2V-14B
    ("video_wan2_2_14B_t2v", True, None, set()),
    # ACE-Step 1.5: mit https://huggingface.co/ACE-Step/Ace-Step1.5
    ("audio_ace_step_1_5_checkpoint", True, None, set()),
    # FLUX.1 [dev]: other / flux-1-dev-non-commercial-license https://huggingface.co/black-forest-labs/FLUX.1-dev
    ("flux_dev_full_text_to_image", False, "license", {"license"}),
    # SDXL base / refiner: openrail++(使い方の制限)
    #   https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
    #   https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0
    ("sdxl_simple_example", False, "license", {"license"}),
    # Hunyuan3D 2.0: other / tencent-hunyuan-community(地域・利用者数の条件)
    #   https://huggingface.co/tencent/Hunyuan3D-2mv
    ("3d_hunyuan3d_image_to_model", False, "license", {"license"}),
    # Stable Audio Open 1.0: other / stable-audio-community(売上の基準)
    #   https://huggingface.co/stabilityai/stable-audio-open-1.0
    ("audio_stable_audio_example", False, "license", {"license"}),
    # HiDream-I1 Full: 本体は mit https://huggingface.co/HiDream-ai/HiDream-I1-Full だが、
    #   テキストエンコーダ llama_3.1_8b_instruct は Llama 3.1 Community License
    #   https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct
    #   (#13 の修正前の一覧では再配布 Comfy-Org/HiDream-I1_ComfyUI の表記 mit だった。今は llama3.1)
    ("hidream_i1_full", False, "license", {"license"}),
    # MiniMax Music 3: 再配布 https://huggingface.co/Comfy-Org/MiniMax-Music-3 の表記は apache-2.0 だが、
    #   本家 https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE は
    #   MiniMax-Music3 COMMUNITY LICENSE
    #   (商用の画面に名前の表示、年の売上 2,000 万ドル超は許可が要る、AUP)
    #   (#13 の修正前の一覧では再配布側の apache-2.0 だった。今は本家に記載が無いため null)
    ("audio_minimax_music_3", False, "license", {"license"}),
    # Qwen-Image 2512(apache-2.0 https://huggingface.co/Qwen/Qwen-Image-2512)と
    #   360 度の LoRA(mit https://huggingface.co/ProGamerGov/qwen-360-diffusion)。ライセンスは通るが
    #   テンプレートがカスタムノード(comfyui_essentials・seedvr2_videoupscaler)を要る
    ("templates_text_prompt_to_360hdr.app", False, "custom_nodes", {"custom_nodes"}),
    # HF の無検閲のモデル。カードの表記は mit https://huggingface.co/Heartsync/NSFW-Uncensored だが、
    #   構成(unet + text_encoder_2)は SDXL で base_model の記載が無い。SDXL の派生なら openrail++ のはずで、
    #   カードの表記は確かめられない(#13 の一覧は license=null・license_unverified)。
    #   ライセンスでも、公式テンプレートでないことでも、無検閲でも保留
    (
        "hf:Heartsync/NSFW-Uncensored",
        False,
        "license",
        {"license", "official_workflow", "custom_nodes", "comfyui_version", "content"},
    ),
    # --- #14 のレビューで見つかった、auto を間違えていたもの(回帰のテスト) ---
    # DINOv3: other / dinov3-license、gated(manual)
    #   https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m
    #   Comfy-Org/Pixal3D の dino_v3_L_naf_fp32 に本家 TencentARC/Pixal3D の mit が付いていた
    ("3d_pixal3d_multi_views", False, "license", {"license", "gated"}),
    ("3d_pixal3d_trellis2_image_to_model", False, "license", {"license", "gated"}),
    #   VAST-AI/TripoSplat(表記 mit)の dino_v3_vit_h
    ("3d_triposplat_image_to_gaussian_splat", False, "license", {"license", "gated"}),
    # SDPose: カードは mit https://huggingface.co/teemosliang/SDPose-Wholebody だが
    #   「U-Net backbone initialized with Stable Diffusion v2 weights」。同じリポジトリの rt_detr_v4 は
    #   RT-DETRv4(https://github.com/RT-DETRs/RT-DETRv4。重みのライセンスの記載なし)
    ("utility_sdpose_ood_image_to_pose", False, "license", {"license"}),
    ("utility_sdpose_multi_person", False, "license", {"license"}),
    ("utility_sdpose_multi_person_video", False, "license", {"license"}),
    ("utility_sdpose_ood_video_to_pose_map", False, "license", {"license"}),
    # Lotus: カードは apache-2.0 https://huggingface.co/jingheya/lotus-depth-d-v1-1 だが、
    #   https://github.com/EnVision-Research/Lotus は stabilityai/stable-diffusion-2-base から学習する
    ("image_lotus_depth_v1_1", False, "license", {"license"}),
    #   Qwen-Image の InstantX ControlNet(apache-2.0)の前処理に Lotus を使う
    ("image_qwen_image_instantx_controlnet", False, "license", {"license"}),
    # NetaYume Lumina: 一式のファイルはテキストエンコーダ Gemma 2 2B を含む(Gemma 利用規約)
    #   https://huggingface.co/duongve/NetaYume-Lumina-Image-2.0 のカード
    #   「Text Encoder: Pre-trained Gemma-2-2b」
    ("image_netayume_lumina_t2i", False, "license", {"license"}),
    # --- 間違えやすい形で、正しく auto のもの(人が確かめた) ---
    # 置き場所と別の系統の部品を含むが、部品の本家を確かめたもの: JoyAI-Image-Edit
    #   本体 apache-2.0 https://huggingface.co/jdopensource/JoyAI-Image-Edit-Plus-Diffusers 、
    #   テキストエンコーダは Qwen3-VL-8B を元にしたもの(Qwen3-VL は apache-2.0
    #   https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)、VAE は Wan2.1(apache-2.0)
    ("image_joyai_image_edit", True, None, set()),
    # 名前は SD の Marigold だが、v2 は Qwen-Image-Edit-2509 から作った apache-2.0
    #   https://huggingface.co/huawei-bayerlab/marigold-v2-0
    #   (base_model: Qwen/Qwen-Image-Edit-2509、apache-2.0)
    ("image_marigold_v2_depth_estimation", True, None, set()),
    # Image Tools: SeedVR2 3B のアップスケール。apache-2.0 https://huggingface.co/ByteDance-Seed/SeedVR2-7B
    #   (カード「SeedVR and SeedVR2 are licensed under the Apache 2.0」)
    ("utility_seedvr2_3b_int8_upscale_image", True, None, set()),
]


@pytest.mark.parametrize("model_id,auto,first,failing", CASES, ids=[c[0] for c in CASES])
def test_real_model_gate(db, model_id, auto, first, failing):
    now = None
    if model_id.startswith("hf:"):
        # HF の項目は凍らせた一覧から読む(毎週の更新で同梱の一覧から消えても同じ入力で確かめる。#197)。
        # HEAD の確認の古さは凍らせた日で見る(日が経っても HEAD の期限で結果が変わらない)
        db = frozen_db()
        now = db.generated_time
    m = db.get(model_id)
    assert m is not None, f"一覧に無い: {model_id}"
    r = evaluate_offline(m, sources=db.sources, now=now)
    assert r.auto is auto, r.to_dict()
    assert (r.held_by[0] if r.held_by else None) == first, r.held_by
    # ライセンスの判定がカードと合っていることは必ず確かめる。それ以外の条件は、上に書いたものだけが落ちる
    assert set(r.held_by) == failing, r.to_dict()


def test_at_least_ten_real_models():
    assert len(CASES) >= 10
    assert sum(1 for c in CASES if c[1]) >= 4 and sum(1 for c in CASES if not c[1]) >= 4


def test_bundled_db_has_head_results_and_comfyui_version(db):
    assert db.sources.get("comfyui", {}).get("latest_release")
    hf = [f for m in db.models for f in m.files if f.url and "huggingface.co" in f.url]
    assert hf and sum(1 for f in hf if f.head_status is not None) / len(hf) > 0.95
