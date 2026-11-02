"""モデルのまとめ方(大きさ = 別のモデル、精度・使い方 = 同じモデル)と、用途・補助ツールの見分け方(#17)。"""

from __future__ import annotations

import json
import re
from collections import defaultdict

import pytest
from clihelp import run_cli

from genmodelfit import cli, load_db
from genmodelfit.groups import (
    build_index,
    family_tokens,
    group_key,
    is_community,
    main_files,
    model_precision,
    precision_label,
    precision_rank,
)
from genmodelfit.models import Gpu, ModelEntry, SystemInfo
from genmodelfit.recommend import recommend, select
from genmodelfit.tasks import is_tool, main_use, task_codes, uses_of
from genmodelfit.update import template_tasks


@pytest.fixture(scope="module")
def db():
    return load_db(use_cache=False)


def pc(vram: float, ram: float = 32) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


# 同梱の一覧の本体のファイル名 → 精度などを外した語(目で確かめられるように書き出す)
NAMES = [
    ("z_image_turbo_bf16.safetensors", "image turbo z"),
    ("z_image_turbo_int8_convrot.safetensors", "image turbo z"),
    ("wan2.1_i2v_480p_14B_fp16.safetensors", "14b 2.1 480p i2v wan"),
    ("Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors", "14b 2.1 480p i2v wan"),
    ("wan2.1_t2v_1.3B_fp16.safetensors", "1.3b 2.1 t2v wan"),
    ("wan2.1_t2v_14B_fp8_scaled.safetensors", "14b 2.1 t2v wan"),
    ("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "14b 2.2 i2v wan"),
    ("wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "14b 2.2 i2v wan"),
    ("qwen_image_2512_fp8_e4m3fn.safetensors", "2512 image qwen"),
    ("qwen_image_2512_bf16.safetensors", "2512 image qwen"),
    ("qwen_image_edit_2511_fp8mixed.safetensors", "2511 edit image qwen"),
    ("qwen_image_edit_2511_int8_convrot.safetensors", "2511 edit image qwen"),
    ("flux-2-klein-4b-fp8.safetensors", "2 4b flux klein"),
    ("flux-2-klein-9b-fp8.safetensors", "2 9b flux klein"),
    ("flux-2-klein-9b.safetensors", "2 9b flux klein"),
    ("flux1-dev-fp8.safetensors", "1 dev flux"),
    ("flux1-dev.safetensors", "1 dev flux"),
    ("ace_step_v1_3.5b.safetensors", "1 3.5b acestep"),
    ("acestep_v1.5_turbo.safetensors", "1.5 acestep turbo"),
    ("ace_step_1.5_turbo_aio.safetensors", "1.5 acestep turbo"),
    ("hunyuan3d-dit-v2_fp16.safetensors", "2 dit hunyuan3d"),
    ("hunyuan_3d_v2.1.safetensors", "2.1 3d hunyuan"),
    ("ltx-2.3-22b-dev-fp8.safetensors", "2.3 22b dev ltx"),
    ("ltx-2.3-22b-dev_transformer_only_bf16.safetensors", "2.3 22b dev ltx"),
    ("ltx-2-19b-dev-fp8.safetensors", "19b 2 dev ltx"),
    ("ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "2.5 22b distilled ltx"),
    ("hunyuanvideo1.5_720p_t2v_fp16.safetensors", "1.5 720p hunyuanvideo t2v"),
    ("qwen-image-2.1-Q8_0.gguf", "2.1 image qwen"),
    ("flux-abliterated-v2-Q4_K_M.gguf", "2 abliterated flux"),
    ("Sakura-Iris-3B-Q8_0-4.15GiB.gguf", "3b iris sakura"),
    ("10Eros_v1.5_bf16_w4a8_audioint8.safetensors", "1.5 10eros"),
    ("10Eros_v1.5_nvfp4.safetensors", "1.5 10eros"),
    ("transformer/diffusion_pytorch_model-00001-of-00003.safetensors", "diffusion pytorch"),
]


@pytest.mark.parametrize(("name", "expected"), NAMES)
def test_family_tokens(name, expected):
    assert " ".join(family_tokens(name)) == expected


def _key(db, mid):
    return group_key(db.get(mid))


SAME = [  # 同じモデル(精度か使い方だけが違う)
    ("image_z_image_turbo", "image_z_image_turbo_int8"),
    ("image_to_video_wan", "video_wan2_1_infinitetalk"),  # Wan 2.1 I2V 14B の fp16 と fp8
    ("image_qwen_Image_2512", "templates_text_prompt_to_360hdr.app"),  # Qwen-Image 2512 の fp8 と bf16
    ("image_qwen_image_edit_2511", "image_qwen_image_edit_2511_int8"),
    ("video_wan2_2_14B_i2v", "video_wan2_2_14B_flf2v"),
    ("video_ltx2_3_t2v", "template_ltx2_3_obscura_remova_lora_remove_object_from_video"),
    ("video_wan21_scail2_character_replacement", "video_wan21_scail2_character_replacement_int8"),
    ("image_ideogram4_t2i", "image_ideogram4_t2i_int8"),
    ("video_wan_vace_14B_v2v", "video_wan_vace_inpainting"),  # 1.3B の切り替え用も載っているが本体は 14B
]
DIFFERENT = [  # 大きさ・版・種類が違う
    # 共通の 1080p 超解像のファイルを本体と数えて、sha256 でまとめていた(超解像は本体にしない)
    ("video_hunyuan_video_1.5_720p_t2v", "video_hunyuan_video_1.5_720p_i2v"),
    ("text_to_video_wan", "video_wan2.1_alpha_t2v_14B"),  # Wan 2.1 1.3B と 14B
    ("image_flux2_klein_image_edit_4b_distilled", "image_flux2_klein_image_edit_9b_distilled"),
    ("audio_ace_step_1_t2a_song", "audio_ace_step_1_5_split"),  # ACE-Step v1 3.5B と 1.5
    ("3d_hunyuan3d_image_to_model", "3d_hunyuan3d-v2.1"),
    ("video_ltx2_3_t2v", "video_ltx2_t2v"),  # LTX 2.3 22B と LTX-2 19B
    ("video_ltx2_3_t2v", "video_ltx2_5_t2v"),  # LTX 2.3 と 2.5(どちらも 22B)
    ("image_z_image", "image_z_image_turbo"),  # 素の版と turbo
    ("image_qwen_image", "image_qwen_image_edit"),
    ("video_wan2_2_14B_t2v", "video_wan2_2_14B_i2v"),  # テキストエンコーダ(umt5)は同じでも別のモデル
    ("image_qwen_image_2_1_t2i", "hf:abenzerps/Qwen-Image-2.1-Uncensored-Quantized"),  # 内容(無検閲)が違う
]


@pytest.mark.parametrize(("a", "b"), SAME)
def test_same_model(db, a, b):
    assert _key(db, a) == _key(db, b)


@pytest.mark.parametrize(("a", "b"), DIFFERENT)
def test_different_model(db, a, b):
    assert _key(db, a) != _key(db, b)


def test_text_encoder_is_never_the_key(db):
    for m in db.models:
        assert "umt5" not in group_key(m) and "t5xxl" not in group_key(m), m.id


def test_groups_never_mix_type_or_content(db):
    groups = defaultdict(set)
    for m in db.models:
        groups[group_key(m)].add((m.modality, m.content))
    assert all(len(v) == 1 for v in groups.values())
    assert 200 <= len(groups) < len(db.models)


def test_precision_rank_and_label():
    order = ["bf16", "fp8", "int8", "gguf-q8_0", "gguf-q6_k", "gguf-q5_k_m", "gguf-q4_k_m", "nvfp4"]
    assert [precision_rank(p) for p in order] == sorted(precision_rank(p) for p in order)
    assert precision_rank(None) == precision_rank("fp16") == precision_rank("gguf-bf16") == 0
    assert [precision_label(p) for p in ("gguf-q4_k_m", "fp8", None)] == ["q4", "fp8", "?"]


def test_precision_auto_selection(db):
    """逃がさずに載る(good 以上で GPU / GPU~)ものがあれば、その中で精度の高いもの(#28)。無ければ良い段階が
    先、同じ段階なら逃がさないほう、それも同じなら高い精度。
    8 GB / RAM 64 GB: 動的 VRAM の式(2026-11-02)で bf16 は逃がして good → bf16(RTX 3070 の実測で bf16 が
    int8 より速く、絵はほぼ同じ)。24 GB: int8 は perfect、bf16 は載せ替えて good → bf16(0.1.0 は int8)。
    48 GB: どちらも perfect → bf16。RAM が足りない PC は test_z_image_turbo_ram_short_prefers_int8。"""
    for v, want, other in (
        (8, "image_z_image_turbo", "image_z_image_turbo_int8"),
        (24, "image_z_image_turbo", "image_z_image_turbo_int8"),
    ):
        ids = {f.model.id for f in recommend(db, pc(v, 64), modality="image", limit=None)}
        assert want in ids and other not in ids, v
    big = {f.model.id: f for f in recommend(db, pc(48, 64), modality="image", limit=None)}
    assert "image_z_image_turbo" in big and "image_z_image_turbo_int8" not in big
    f = big["image_z_image_turbo"]
    assert [v["precision"] for v in f.variants] == ["bf16", "int8"]
    assert {w["id"] for w in f.workflows} >= {
        "image_z_image_turbo_int8",
        "image_z_image_turbo_fun_union_controlnet",
    }


def test_all_workflows_and_counts(db):
    grouped = select(db, pc(8), include_tools=False)
    every = select(db, pc(8), include_tools=False, group=False)
    assert len(every.fits) == len(grouped.fits) + grouped.hidden["same_model"]
    for sel in (grouped, every):
        assert sel.matched + sum(sel.hidden.values()) == len(db.models)
    assert len({f.group_key for f in grouped.fits}) == len(grouped.fits)


def test_task_filter_picks_the_matching_workflow(db):
    edit = recommend(db, pc(24, 64), modality="image", task="edit", limit=None)
    assert edit and all(f.use == "edit" for f in edit)
    z = next(f for f in edit if f.group_key == _key(db, "image_z_image_turbo"))
    assert z.model.id == "image_z_image_turbo_fun_union_controlnet"  # 代表は編集のワークフロー
    assert task_codes(["generate"]) == task_codes(["gen"]) and "t2i" in task_codes(["gen"])
    with pytest.raises(ValueError):
        task_codes(["dance"])


# A3: 既定の recommend に出てはいけない補助ツール(深度・線画の前処理、音の分離)
TOOLS = [
    "utility_depth_anything3_image_depth_estimation",
    "utility_depth_anything3_video_depth_estimation",
    "utility_moge_depth_estimation",
    "utility-depthAnything-v2-relative-video",
    "utility-lineart-video",
    "image_lotus_depth_v1_1",
    "audio_melbandroformer_audio_separation",
]


@pytest.mark.parametrize("mid", TOOLS)
def test_utility_tools_are_tools(db, mid):
    m = db.get(mid)
    assert is_tool(m) and uses_of(m) == ["tool"] and main_use(m) == "tool"
    assert set(m.tasks) <= {"estimate", "separate"}  # 同梱の一覧のタグも直した
    # update が同じ規則で作る(タグが controlnet / text to image でも)
    t = m.template
    assert (
        template_tasks({"title": t.title, "tags": ["ControlNet", "Text to Image"]}, m.modality, t.category)
        == m.tasks
    )


@pytest.mark.parametrize("vram", [0, 8, 24])
def test_default_recommend_has_no_tools(db, vram):
    for sel in (
        select(db, pc(vram), include_tools=False, per_modality=3),
        select(db, pc(vram), include_tools=False),
    ):
        ids = {f.model.id for f in sel.fits} | {
            w["id"] for f in sel.fits for w in f.workflows if w["matched"]
        }
        assert not ids & set(TOOLS)
        assert all(not is_tool(f.model) for f in sel.fits)


def test_generative_models_are_not_tools(db):
    for mid in (
        "sd3.5_large_depth",
        "video_ltx2_depth_to_video",
        "flux_depth_lora_example",
        "image_qwen_image_layered",
    ):
        assert not is_tool(db.get(mid)), mid
    assert not is_tool(
        ModelEntry(id="x", name="Depth Map: X", modality="image", tasks=["control"])
    )  # 分類が道具でない


# A2: 壊れた・無い --db


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '{"schema_version": 1, "models": [1]}'])
def test_broken_db_is_a_clean_error(tmp_path, capsys, content):
    p = tmp_path / "db.json"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    assert cli.main(["fit", "--db", str(p), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"genmodelfit", "command", "error"} and out["error"]
    assert cli.main(["recommend", "--db", str(p)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("エラー: ")


def test_broken_db_in_subprocess_has_no_traceback(tmp_path):
    p = tmp_path / "db.json"
    p.write_text("{oops", encoding="utf-8")
    r = run_cli(["recommend", "--db", str(p), "--json"], tmp_path / "cache")
    assert r.returncode == 1 and b"Traceback" not in r.stderr
    assert "error" in json.loads(r.stdout.decode("utf-8"))


# --- 3 回目の指摘(代表の選び方・精度・sha256・表示名・段階の帯)-----------------------------------


def _row(sel, key):
    return next(f for f in sel.fits if f.group_key == key)


def test_representative_prefers_generation_over_tools(db):
    idx = build_index(db.models)
    sel = select(db, pc(8))  # fit と同じ(補助ツールも含む)
    assert _row(sel, idx.key_of["image_qwen_image_2_1_t2i"]).model.id == "image_qwen_image_2_1_t2i"
    assert _row(sel, idx.key_of["image_qwen_image_edit_2509"]).model.id.startswith(("image_qwen", "template"))
    assert not _row(sel, idx.key_of["image_qwen_image_edit_2509"]).model.id.startswith("image_marigold")
    z = _row(sel, idx.key_of["image_z_image_turbo"])
    assert z.use == "gen" and z.model.id == "image_z_image_turbo"  # 8 GB / RAM 32 GB は bf16(動的 VRAM の式)
    # 道具だけのモデルは道具のまま出る
    assert _row(sel, idx.key_of["utility_seedvr2_3b_int8_upscale_image"]).use == "upscale"
    music = recommend(db, pc(12), modality="music", limit=3)
    assert all(f.use == "gen" for f in music)


PRECISION_FIXED = {  # 本体のファイル名(0.1.1 からは読めればファイルの見出し)から読む精度(同梱の一覧も直した)
    "templates_mjm_airt_machIne": "fp8",
    "template_rob_split_stack_qwen_multi_wan22": "bf16",
    "video_ltx2_canny_to_video": "bf16",  # 名前に精度の語が無い。見出しの dtype は BF16(#28)
    "video_ltx2_depth_to_video": "bf16",
    "video_ltx2_pose_to_video": "bf16",
    "utility_void_video_inpainting": "bf16",
    "flux_depth_lora_example": "fp8",
    "templates_hellorob_facegen_skindetail_upscale": "bf16",
}


@pytest.mark.parametrize(("mid", "want"), sorted(PRECISION_FIXED.items()))
def test_precision_comes_from_the_main_file(db, mid, want):
    m = db.get(mid)
    assert model_precision(m) == want and m.precision == want


def test_bundled_precision_matches_update_rule(db):
    assert [m.id for m in db.models if m.precision != model_precision(m)] == []
    assert precision_rank("w4a8") == precision_rank("gguf-q4_k_m") > precision_rank("int8")


def test_same_sha256_is_the_same_model(db):
    idx = build_index(db.models)
    k = idx.key_of
    assert k["hf:stabilityai/stable-audio-open-1.0"] == k["audio_stable_audio_example"]  # 同じファイル
    assert (
        k["audio_ace_step_1_5_split"]
        == k["audio_ace_step_1_5_split_4b"]
        == k["audio_ace_step_1_5_checkpoint"]
    )
    assert k["audio_ace_step_1_5_split"] != k["audio_ace_step_1_t2a_song"]


def test_two_main_files_join_an_existing_model(db):
    """FLUX.2 klein 4B の文章から画像は base と distilled の両方を載せる
    → distilled(base でないほう)のモデルへ寄せる。"""
    k = build_index(db.models).key_of
    assert k["image_flux2_klein_text_to_image"] == k["image_flux2_klein_image_edit_4b_distilled"]
    assert k["image_flux2_klein_text_to_image"] != k["image_flux2_klein_image_edit_4b_base"]
    assert k["image_ideogram4_t2i"] == k["image_ideogram4_t2i_int8"]  # 片方だけのモデルが無ければつないだまま


def test_params_b_only_for_generic_hf_names():
    from genmodelfit.models import ModelFile

    t = ModelEntry(
        id="t",
        name="t",
        modality="music",
        params_b=4,
        files=[ModelFile(name="acestep_v1.5_turbo.safetensors", directory="diffusion_models", size_bytes=10)],
    )
    assert "4b" not in group_key(t)  # 題から推した params_b(言語モデルの大きさのことがある)は使わない
    h1 = ModelEntry(
        id="hf:o/voice",
        name="voice",
        modality="tts",
        params_b=1.0,
        source="huggingface",
        files=[ModelFile(name="model.safetensors", repo_id="o/voice", size_bytes=10)],
    )
    h2 = ModelEntry(
        id="hf:p/voice",
        name="voice",
        modality="tts",
        params_b=3.0,
        source="huggingface",
        files=[ModelFile(name="model.safetensors", repo_id="p/voice", size_bytes=10)],
    )
    assert group_key(h1).endswith("voice 1b") and group_key(h1) != group_key(h2)


def test_moss_sft_is_not_merged_with_base(db):
    k = build_index(db.models).key_of
    assert k["image_moss_image_t2i_bboxes"] == k["image_moss_image_t2i"] != k["image_moss_image_base_t2i"]
    from genmodelfit.update import model_file

    base = "https://huggingface.co/Comfy-Org/MOSS-Image/resolve/main/diffusion_models/moss_image_base_int8_convrot.safetensors"
    f = model_file(
        {
            "name": "moss_image_sft_int8_convrot.safetensors",
            "url": base,
            "directory": "diffusion_models",
            "hash": "f601c1e1161915fb3fea003da0c1782e374dd960fbb3978bc0e4e3fa1f2d8e6f",
            "hash_type": "SHA256",
        }
    )
    assert f.url.endswith("moss_image_sft_int8_convrot.safetensors") and f.sha256.startswith("b4d1475b")


def test_moge_is_a_tool(db):
    for mid in ("3d_moge_perspective_to_mesh", "3d_moge_panorama_to_mesh"):
        assert is_tool(db.get(mid)) and db.get(mid).tasks == ["estimate"]
    top = recommend(db, pc(8), modality="3d", limit=1)
    assert top and top[0].use == "gen" and "moge" not in top[0].model.id


def test_model_names_are_unique_and_readable(db):
    idx = build_index(db.models)
    names = [n.lower() for n in idx.name_of.values()]
    assert len(names) == len(set(names))
    by_id = {mid: idx.name_of[k] for mid, k in idx.key_of.items()}
    assert by_id["image_z_image_turbo_int8"] == "Z-Image-Turbo"
    assert by_id["image_flux2_klein_text_to_image"] == "FLUX.2 [Klein] 4B"
    assert idx.find("z-image-turbo") == idx.key_of["image_z_image_turbo"]


def test_info_accepts_the_model_name(capsys):
    assert cli.main(["info", "z-image-turbo", "--vram", "8", "--ram", "32", "--no-cache", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["model_name"] == "Z-Image-Turbo" and d["model"]["id"] == "image_z_image_turbo"
    assert {w["id"] for w in d["same_model"]} >= {
        "image_z_image_turbo_int8",  # 代表が bf16 になったので、int8 はほかのワークフローの側に出る
        "image_z_image_turbo_fun_union_controlnet",
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"files": "zz"},
        {"files": [{"name": "a", "size_bytes": "big"}]},
        {"tasks": "t2i"},
        {"files": [{"name": 3}]},
        {"params_b": "4b"},
    ],
)
def test_db_types_are_checked(tmp_path, capsys, bad):
    entry = {
        "id": "x",
        "name": "x",
        "modality": "image",
        "content": "sfw",
        "tasks": ["t2i"],
        "files": [],
    } | bad
    p = tmp_path / "db.json"
    p.write_text(json.dumps({"schema_version": 1, "models": [entry]}), encoding="utf-8")
    assert cli.main(["fit", "--db", str(p), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "models[0]" in out["error"]


def test_fit_bands():
    from genmodelfit.estimate import fit_band, gpu_choice

    assert [fit_band(x, 100) for x in (60, 61, 85, 86, 98, 99)] == [
        "perfect",
        "good",
        "good",
        "marginal",
        "marginal",
        "too_tight",
    ]
    assert gpu_choice(5, 5, 2, 10) == ("perfect", "full")  # 50%
    assert gpu_choice(12, 5, 2, 10) == ("good", "seq")  # 載せ替えは 50% でも good まで
    assert gpu_choice(12, 11, 5, 10) == ("good", "offload")  # 逃がすのは good まで(#108)
    # 順に載せ替えが 85〜98%(marginal)で逃がせば good なら逃がす。good で並べば逃がさない(GPU が先)
    assert gpu_choice(12, 9, 5, 10) == ("good", "offload")
    assert gpu_choice(12, 8, 5, 10) == ("good", "seq")
    # 逃がす段階はメインメモリにもよる(ram_ratio。表の Mem% と同じ)。marginal で並べば逃がさない
    assert gpu_choice(12, 9, 5, 10, ram_ratio=0.9) == ("marginal", "seq")
    assert gpu_choice(12, 11, 5, 10, ram_ratio=1.2) == ("marginal", "offload")  # 足りない分は仮想メモリ
    assert gpu_choice(12, 11, 9.9, 10) == ("too_tight", "full")


# --- 4 回目の指摘 -------------------------------------------------------------------------------


def test_super_resolution_is_not_a_main_weight(db):
    m = db.get("video_hunyuan_video_1.5_720p_t2v")
    assert [f.name for f in main_files(m)] == ["hunyuanvideo1.5_720p_t2v_fp16.safetensors"]
    k = build_index(db.models).key_of
    assert k["video_hunyuan_video_1.5_720p_t2v"] != k["video_hunyuan_video_1.5_720p_i2v"]


def test_sha_merge_needs_the_whole_set_of_main_weights():
    from genmodelfit.models import ModelFile

    def entry(i, *files):
        fs = [ModelFile(name=n, directory="diffusion_models", size_bytes=10, sha256=h) for n, h in files]
        return ModelEntry(id=i, name=i, modality="video", files=fs)

    a = entry("a", ("x_low.safetensors", "1" * 64), ("x_high.safetensors", "2" * 64))
    b = entry("b", ("y_low.safetensors", "1" * 64), ("y_high.safetensors", "3" * 64))  # 1 つだけ同じ
    c = entry("c", ("z_low.safetensors", "1" * 64), ("z_high.safetensors", "2" * 64))  # 組が同じ
    d = entry("d", ("w_low.safetensors", "1" * 64), ("w_high.safetensors", None))  # sha256 の無い本体がある
    k = build_index([a, b, c, d]).key_of
    assert k["a"] == k["c"] != k["b"]
    assert k["d"] != k["a"]
    # 本体 1 つが sha256 の無いもの: sha256 のあるほうだけで、同じファイル 1 つの項目とまとめない
    e = entry("e", ("u_low.safetensors", "4" * 64), ("u_high.safetensors", None))
    f = entry("f", ("t_main.safetensors", "4" * 64))
    k = build_index([e, f]).key_of
    assert k["e"] != k["f"]


def test_row_score_is_the_representatives(db):
    """行の Score は代表の値(#95。表の Mem% と合う。前の「仲間の一番高い点」はやめた)。VRAM を増やして
    代表が高い精度へ替わると Score は下がることがある(docs/scoring.md「Score」)。"""
    for v in (8, 16, 24):
        sel = select(db, pc(v))
        every = {f.model.id: f for f in select(db, pc(v), group=False).fits}
        for row in sel.fits:
            rep = every[row.model.id]
            assert row.score == rep.score and row.score_components == rep.score_components, (v, row.model.id)
            assert row.to_dict()["utilization_pct"] == rep.to_dict()["utilization_pct"]


def test_community_template_is_never_the_representative(db):
    k = build_index(db.models).key_of
    assert is_community(db.get("templates_mjm_airt_machIne"))
    assert not is_community(db.get("video_wan2_2_14B_i2v"))
    for v in (8, 24, 48):
        row = next(f for f in select(db, pc(v, 64)).fits if f.group_key == k["video_wan2_2_14B_i2v"])
        assert not is_community(row.model), v
        assert "templates_mjm_airt_machIne" in {w["id"] for w in row.workflows}


def test_community_only_models_are_flagged(db):
    idx = build_index(db.models)
    only = [k for k, ms in idx.members.items() if all(is_community(m) for m in ms)]
    assert only
    rows = {f.group_key: f for f in select(db, pc(48, 64)).fits}
    for k in only:
        if k in rows:
            assert rows[k].to_dict()["community"] is True


def test_display_names_of_utility_titles(db):
    idx = build_index(db.models)
    by_id = {mid: idx.name_of[k] for mid, k in idx.key_of.items()}
    assert by_id["utility_seedvr2_7b_int8_upscale_image"] == "SeedVR2 7B"
    assert by_id["video_wan_vace_inpainting"] == "Wan 2.1 VACE 14B"
    assert by_id["audio_ace_step_1_m2m_editing"] == "ACE Step v1 3.5B"
    assert by_id["hf:TenStrip/LTX2.3-10Eros"] == "LTX2.3-10Eros 1.5"  # 精度・包み方の語は入れない
    assert "F16" not in by_id["wan2.1_flf2v_720_f16"] and "Turbo" in by_id["image_krea2_turbo_t2i"]
    assert precision_label("nvfp4") == "fp4"


def test_w4a8_is_four_bit():
    from genmodelfit.hf import guess_precision

    assert guess_precision("10Eros_v1.5_bf16_w4a8_audioint8.safetensors") == "w4a8"


def test_file_less_templates_join_or_say_files_unknown(db):
    idx = build_index(db.models)
    k = idx.key_of
    # 「Frame Interpolation: FILM」はファイルの一覧が無いが、FILM のモデル 1 つだけに当たるので寄せる
    assert k["utility-frame_interpolation-film"] == k["utility_video_frame_interpolation"]
    assert idx.name_of[k["utility-frame_interpolation-film"]] == "FILM"
    # SeedVR2 は 3B と 7B の 2 つに当たるので寄せず、ファイルが分からないと書く
    assert idx.name_of[k["utility_seedvr2_image_upscale"]] == "SeedVR2 (files unknown)"
    names = list(idx.name_of.values())
    # ID を名前に出さない
    assert not [n for n in names if re.search(r"utility[_-]|templates?[_-]|\[[a-z0-9_.-]+\]$", n)]


def test_name_rules_and_overrides(db):
    from genmodelfit.groups import NAME_OVERRIDES

    idx = build_index(db.models)
    by_id = {mid: idx.name_of[k] for mid, k in idx.key_of.items()}
    assert (
        by_id["3d_moge_perspective_to_mesh"] == "MoGe 2 (3D)"
        and by_id["utility_moge_depth_estimation"] == "MoGe 2 (image)"
    )
    assert by_id["utility_moge3_geometry_estimation"] == "MoGe 3"
    assert {
        by_id["utility_sam3d_body"],
        by_id["utility_image_segment_sam3"],
        by_id["utility_video_segment_sam3"],
    } == {"SAM 3.1 (3D)", "SAM 3.1 (image)", "SAM 3.1 (video)"}
    assert by_id["video_minimax_h3_i2v"] == "MiniMax H3 (first/last frame)"
    assert by_id["video_minimax_h3_r2v"] == "MiniMax H3 (reference)"
    assert (
        by_id["image_moss_image_t2i"] == "MOSS-Image SFT"
        and by_id["hf:hexgrad/Kokoro-82M"] == "Kokoro-82M v1.0"
    )
    assert (
        by_id["video-wan21_scail"] == "Wan 2.1 SCAIL"
        and by_id["image_qwen_image_edit_2511"] == "Qwen Image Edit 2511"
    )
    for mid, name in NAME_OVERRIDES.items():  # 上書きの表の ID が一覧にあり、その名前になっている
        assert by_id[mid] == name, mid
    assert len({n.lower() for n in idx.name_of.values()}) == len(idx.name_of)


def test_z_image_turbo_ram_short_prefers_int8(db):
    """同じ段階なら、メインメモリに収まる版を選ぶ(2026-11-02)。RAM 16 GB では bf16(要るメインメモリ 約 22 GB)
    より int8(約 14 GB)。RAM 32 GB の 8 GB では bf16(実測で速い)。"""
    for v, ram, want in (
        (6, 16, "image_z_image_int8"),  # 両方足りないときは、足りない量の小さい int8
        (8, 16, "image_z_image_turbo_int8"),
        (6, 16, "image_z_image_turbo_int8"),
        (4, 16, "image_z_image_turbo_int8"),
        (8, 32, "image_z_image_turbo"),
    ):
        ids = {f.model.id for f in recommend(db, pc(v, ram), modality="image", limit=None)}
        assert want in ids, (v, ram, ids & {"image_z_image_turbo", "image_z_image_turbo_int8"})
