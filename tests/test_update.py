"""genmodelfit update のテスト。ネットワークには触らない(tests/fixtures/update の記録を DirFetcher で読む)。

フィクスチャは実際の取得を記録して小さく削ったもの。`example-org/*` と `image_*_example` の名前のものだけは
試験用に作った架空のデータ(除外・失敗の経路を通すため)。
"""

import json
import os
import re
from pathlib import Path

import pytest

from genmodelfit import cli, safety
from genmodelfit.db import load
from genmodelfit.fetch import DirFetcher, FetchError, NetworkError
from genmodelfit.hf import (
    HfClient,
    RepoInfo,
    choose_bases,
    guess_params_b,
    guess_precision,
    license_rank,
    parse_hf_url,
    select_weight_files,
    strictest,
)
from genmodelfit.models import ModelEntry, Template
from genmodelfit.popularity import HUGGING_FACE, TEMPLATE, source_count
from genmodelfit.update import (
    LICENSE_POLICY,
    UpstreamFormatError,
    audio_modality,
    build_database,
    skip_reason,
    template_modality,
    validate_index,
    workflow_models,
)

FIX = Path(__file__).parent / "fixtures" / "update"


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def built():
    return build_database(DirFetcher(FIX), head=False, carry_over=False)


# --- index.json --------------------------------------------------------------------------------


def test_validate_index_rejects_changed_shapes():
    with pytest.raises(UpstreamFormatError, match="list"):
        validate_index({"templates": []})
    with pytest.raises(UpstreamFormatError, match="templates"):
        validate_index([{"title": "Image"}])
    with pytest.raises(UpstreamFormatError, match="name"):
        validate_index([{"title": "Image", "templates": [{"title": "no name"}]}])
    assert validate_index([{"title": "Image", "templates": [{"name": "a"}]}])


def test_local_filter():
    assert skip_reason("Image", {"name": "api_x", "size": 1}) == "api"
    assert skip_reason("Image", {"name": "x", "openSource": False, "size": 1}) == "not_open_source"
    assert skip_reason("Image", {"name": "x", "size": 0}) == "no_size"
    assert skip_reason("LLM", {"name": "x", "size": 1}) == "category"
    # openSource が無いのはローカル(False だけを外す)
    assert skip_reason("Image", {"name": "x", "size": 1}) is None


def test_modality_mapping():
    assert template_modality("Video Tools", {"name": "v"}) == "video"
    assert template_modality("3D Model", {"name": "d"}) == "3d"
    assert audio_modality({"name": "audio-chatterbox_tts", "title": "Chatterbox TTS"}) == "tts"
    assert audio_modality({"name": "a", "title": "Foley", "description": "sound effects for video"}) == "sfx"
    sa3 = {"name": "a", "title": "Stable Audio 3", "description": "Generate music, SFX, or instruments"}
    assert audio_modality(sa3) == "music"
    assert (
        template_modality("Audio", {"name": "audio_stable_audio_example", "title": "Stable Audio"}) == "sfx"
    )
    other = {"name": "c", "tags": ["Image Edit"], "io": {"outputs": [{"mediaType": "video"}]}}
    assert template_modality("Character & Fashion", other) == "video"
    assert template_modality("Brand & Design", {"name": "b", "tags": ["Game", "3D"]}) == "image"


def test_subgraph_models_are_collected():
    wf = json.loads((FIX / "templates" / "image_z_image_turbo_int8.json").read_text(encoding="utf-8"))
    assert not any((n.get("properties") or {}).get("models") for n in wf["nodes"]), "本体はサブグラフの中だけ"
    names = {m["name"] for m in workflow_models(wf)}
    assert {
        "z_image_turbo_int8_convrot.safetensors",
        "qwen_3_4b_fp8_mixed.safetensors",
        "ae.safetensors",
    } <= names
    dup = {"nodes": [{"properties": {"models": [{"name": "a", "url": "u"}, {"name": "a2", "url": "u"}]}}]}
    assert len(workflow_models(dup)) == 1, "URL で重複を除く"
    with pytest.raises(UpstreamFormatError):
        workflow_models({"no_nodes": True})


# --- 一覧を作る ---------------------------------------------------------------------------------


def test_build_from_fixtures(built):
    ids = {e.id for e in built.models}
    st = built.sources
    assert "image_z_image_turbo_int8" in ids and "audio_ace_step_1_5_checkpoint" in ids
    assert not any(i.startswith("api_") for i in ids)
    assert "image_closed_weights_example" not in ids and "llm_qwen3_text_gen" not in ids
    assert "audio-chatterbox_tts" not in ids  # size 0
    assert st["comfyui-templates"]["skipped"] == {"api": 1, "category": 1, "no_size": 1, "not_open_source": 1}
    assert st["comfyui-templates"]["failed"] == 1
    assert any("image_missing_json_example" in w for w in st["warnings"])
    assert built.get("templates-wan2_1_infinitetalk_music").modality == "video"
    assert built.get("audio_stable_audio_example").modality == "sfx"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", built.generated_at)


def test_files_filled_from_hf(built):
    e = built.get("image_z_image_turbo_int8")
    assert e.tasks == ["t2i"] and e.precision == "int8"
    main = next(f for f in e.files if f.role == "main")
    assert main.size_bytes and len(main.sha256) == 64 and main.gated is False
    assert {f.role for f in e.files} == {"main", "text_encoder", "vae"}
    assert e.total_size_bytes == sum(f.size_bytes for f in e.files)


def test_repackaged_license_follows_base_model(built):
    e = built.get("image_z_image_turbo_int8")
    main = next(f for f in e.files if f.role == "main")
    assert main.repo_id == "Comfy-Org/z_image_turbo"
    assert main.license_from == "Tongyi-MAI/Z-Image-Turbo" and main.license == "apache-2.0"
    assert e.license == "apache-2.0" and e.license_url.startswith("https://huggingface.co/")
    h3d = built.get("3d_hunyuan3d_image_to_model")
    assert h3d.license == "other" and h3d.files[0].license_name == "tencent-hunyuan-community"


def test_license_strictest_rule():
    assert license_rank(None) > license_rank("other") > license_rank("openrail++") > license_rank("mit")
    assert strictest(["mit", "apache-2.0", "creativeml-openrail-m"]) == "creativeml-openrail-m"
    assert strictest(["mit", "other", "openrail"]) == "other"
    assert strictest(["mit", None, "other"]) is None
    assert strictest([]) is None
    # 段階は名前の順ではない: 非商用 > other > 用途の制限 > 表示 > 寛容
    assert strictest(["cc-by-nc-4.0", "openrail++"]) == "cc-by-nc-4.0"
    assert strictest(["openrail++", "cc-by-nc-sa-4.0", "other"]) == "cc-by-nc-sa-4.0"
    assert strictest(["cc-by-4.0", "mit"]) == "cc-by-4.0"
    assert strictest(["llama3.1", "apache-2.0"]) == "llama3.1"
    # other は license_name も見る(非商用の独自ライセンスは、条件付きの独自ライセンスより厳しい)
    nc = ("other", "flux-1-dev-non-commercial-license")
    assert strictest([("other", "tencent-hunyuan-community"), nc]) == nc
    assert (
        strictest([("other", "qwen-research"), ("other", "krea-2-community-license")])[1] == "qwen-research"
    )


def test_choose_bases():
    bases = ["Wan-AI/Wan2.1-T2V-1.3B", "Wan-AI/Wan2.1-I2V-14B-720P", "Wan-AI/Wan2.1-VACE-14B"]
    assert choose_bases(bases, "split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors") == [bases[0]]
    assert choose_bases(bases, "umt5_xxl_fp8_e4m3fn_scaled.safetensors") == []  # 分からなければ不明
    assert choose_bases(["only/one"], "anything.safetensors") == ["only/one"]
    # 大きさの語(14B)だけの重なりでは選ばない(Kijai の CausVid の LoRA を VACE と取り違えていた)
    vace = ["Wan-AI/Wan2.1-VACE-14B", "Wan-AI/Wan2.1-VACE-1.3B"]
    assert choose_bases(vace, "Wan21_CausVid_14B_T2V_lora_rank32.safetensors") == []


def test_file_license_is_unknown_when_upstream_has_none():
    # 本家にライセンスの記載が無いとき、再配布側の表記は使わない(確かめられないので不明)
    class F:
        def get_json(self, url):
            if "upstream" in url:
                return {"id": "x/upstream", "cardData": {}}
            raise FetchError(url, "404", 404)

    c = HfClient(F())
    info = RepoInfo(repo_id="Comfy-Org/thing", license="apache-2.0", base_models=["x/upstream"])
    res = c.resolve_file(info, "a.safetensors")
    assert res.license is None and res.evidence[0].startswith("license_unverified")


def test_uncensored_from_hf_search(built):
    unc = [e for e in built.models if e.content == "uncensored"]
    assert {e.id for e in unc} >= {"hf:Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1"}
    e = built.get("hf:Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1")
    assert e.source == "huggingface" and e.modality == "image"
    assert "hf-tag:not-for-all-audiences" in e.content_evidence and "name:uncensored" in e.content_evidence
    assert e.discovered_from and all(
        u.startswith("https://huggingface.co/api/models?") for u in e.discovered_from
    )
    assert e.downloads and e.files and e.files[0].size_bytes
    gguf = built.get("hf:AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF")
    assert gguf is not None and len([f for f in gguf.files if f.role == "main"]) == 1
    assert gguf.precision.startswith("gguf-")
    # LoRA は一覧に入れない
    assert built.get("hf:lopi999/Wan2.2-I2V_General-NSFW-LoRA") is None
    tts = built.get("hf:hexgrad/Kokoro-82M")
    assert tts and tts.modality == "tts" and tts.content == "sfw"


def test_exclusion_only_counted(built):
    ids = {e.id for e in built.models}
    assert "hf:example-org/loli-style-xl" not in ids  # 名前で外れる
    assert "hf:example-org/plain-nsfw-xl" not in ids  # モデルカードで外れる
    assert built.sources["excluded"] == 2
    assert "example-org" not in json.dumps(built.sources), "外したものの名前は残さない"


def test_template_marked_uncensored_by_name():
    from genmodelfit.update import mark_template_content

    e = ModelEntry(id="image_x_uncensored", name="X Uncensored", modality="image")
    mark_template_content(e, {"tags": []}, HfClient(DirFetcher(FIX)))
    assert e.content == "uncensored" and e.content_evidence == ["name:uncensored"]


def test_measured_entries_carried_over(tmp_path):
    prev = load(use_cache=False)  # 同梱の一覧(実測のある ID を全部持つ)
    db = build_database(DirFetcher(FIX), head=False, previous=prev)
    ids = {e.id for e in db.models}
    assert all(m.model_id in ids for m in db.measurements or prev.measurements)


# --- 選び方・推し方 ---------------------------------------------------------------------------------


def _info(files, **kw):
    return RepoInfo(repo_id=kw.pop("repo_id", "o/r"), files={p: (s, None) for p, s in files.items()}, **kw)


def test_select_weight_files_diffusers_prefers_fp16():
    info = _info(
        {
            "model_index.json": 1,
            "unet/diffusion_pytorch_model.safetensors": 5_000,
            "unet/diffusion_pytorch_model.fp16.safetensors": 2_500,
            "text_encoder/model.safetensors": 400,
            "vae/diffusion_pytorch_model.safetensors": 300,
            "safety_checker/model.safetensors": 1_000,
        }
    )
    got = dict(select_weight_files(info))
    assert got == {
        "unet/diffusion_pytorch_model.fp16.safetensors": "main",
        "text_encoder/model.safetensors": "text_encoder",
        "vae/diffusion_pytorch_model.safetensors": "vae",
    }


def test_select_weight_files_variants():
    gguf = _info({"m-Q8_0.gguf": 800, "m-Q4_K_M.gguf": 400, "m-Q2_K.gguf": 200, "mmproj-f16.gguf": 100})
    assert select_weight_files(gguf) == [("m-Q8_0.gguf", "main")]
    st = _info(
        {"x_fp32.safetensors": 8, "x_fp16.safetensors": 4, "x_fp8.safetensors": 2, "lora.safetensors": 1}
    )
    assert select_weight_files(st) == [("x_fp16.safetensors", "main")]
    shards = _info({"model-00001-of-00002.safetensors": 5, "model-00002-of-00002.safetensors": 5})
    assert [p for p, _ in select_weight_files(shards)] == [
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    ]
    assert select_weight_files(_info({"README.md": 1})) == []
    both = _info(
        {
            "model_index.json": 1,
            "unet/diffusion_pytorch_model.safetensors": 5,
            "vae/diffusion_pytorch_model.safetensors": 1,
            "my_model_v2.safetensors": 6,
            "my_model_v2.ckpt": 6,
        }
    )
    assert select_weight_files(both) == [("my_model_v2.safetensors", "main")], "単一ファイルを優先"
    bundle = _info({f"{n}-GGUF/{n}-q8_0.gguf": 100 for n in ("alpha", "beta", "gamma", "delta")})
    assert select_weight_files(bundle) == [], "詰め合わせは選ばない"
    mixed = _info(
        {"m/m-Q8_0.gguf": 100, "other-model/text_encoder_q4.gguf": 50, "text_encoders/te-Q8_0.gguf": 40},
        repo_id="o/m",
    )
    assert dict(select_weight_files(mixed)) == {
        "m/m-Q8_0.gguf": "main",
        "text_encoders/te-Q8_0.gguf": "text_encoder",
    }


def test_guess_precision_and_params():
    assert guess_precision("wan2.1_t2v_1.3B_fp16.safetensors") == "fp16"
    assert guess_precision("z_image_turbo_int8_convrot.safetensors") == "int8"
    assert guess_precision("m-Q4_K_M.gguf") == "gguf-q4_k_m"
    assert guess_params_b("wan2.1_t2v_1.3B_fp16.safetensors") == 1.3
    assert guess_params_b("video_wan2_2_14B_t2v") == 14.0
    assert guess_params_b("sdxl_base.safetensors") is None


def test_parse_hf_url():
    u = "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"
    assert parse_hf_url(u) == ("Comfy-Org/z_image_turbo", "main", "split_files/vae/ae.safetensors")
    assert parse_hf_url("https://civitai.com/api/download/models/1") is None


def test_popularity_uses_downloads_all_time_for_hf():
    """人気の数(#95): HF のモデルは全期間のダウンロード数(30 日の数ではない。÷100 で目盛りを直さない)、
    テンプレートがあればテンプレートの使用数(HF の数は混ぜない)。"""
    hf = ModelEntry(
        id="hf:a/b", name="b", modality="image", source="huggingface", downloads=100, downloads_all_time=5000
    )
    assert source_count([hf]) == (HUGGING_FACE, 5000)
    t = ModelEntry(id="t", name="t", modality="image", template=Template(name="t", usage=0), downloads=10**9)
    assert source_count([t]) == (TEMPLATE, 0)
    assert source_count([t, hf]) == (TEMPLATE, 0)


# --- 安全 ---------------------------------------------------------------------------------------


def test_safety_word_boundaries():
    assert safety.blocked_terms(["Loli-XL"], uncensored=False) == ["loli"]
    assert safety.blocked_terms(["shota_v2"], uncensored=False) == ["shota"]
    assert safety.blocked_terms(["minority-report-nsfw", "kidney"], uncensored=True) == []
    assert safety.blocked_terms(["childrens-book-illustration"], uncensored=False) == []
    assert safety.blocked_terms(["teen-nsfw"], uncensored=True) == ["teen"]
    assert safety.blocked_terms(["teen-nsfw"], uncensored=False) == []


def test_safety_card_disclaimers_are_not_hits():
    card = (
        "Adults only.\nDo not use it to generate:\n\n- Sexual content involving minors\n- Illegal content\n"
    )
    assert safety.blocked_terms([], card, uncensored=True) == []
    assert safety.blocked_terms([], "No underage content. Strictly 18+.", uncensored=True) == []
    # 性的な語と同じ文になければ数えない(作例のプロンプトなど)
    assert safety.blocked_terms([], "A tiny boat with a child passing by.", uncensored=True) == []
    assert safety.blocked_terms([], "Generates nude photos of kids.", uncensored=True) == ["kids"]
    # 説明文の「性的な語 + 未成年の語」は、無検閲の印が無いモデルにも当てる
    assert safety.blocked_terms([], "Generates nude photos of kids.", uncensored=False) == ["kids"]
    assert safety.blocked_terms([], "Trained on lolicon art.", uncensored=False) == ["lolicon"]


# --- 失敗の扱い ---------------------------------------------------------------------------------


class _Down:
    def __init__(self, *a, **k):
        pass

    def get_json(self, url):
        raise NetworkError(url, "つながりません(ConnectionError)")

    get_text = get_json

    def head_size(self, url):
        return None


def _write_cache(path: Path) -> str:
    db = load(use_cache=False)
    db.save(path)
    return path.read_text(encoding="utf-8")


def test_offline_keeps_cache(tmp_path, monkeypatch, capsys):
    from genmodelfit import db as dbmod
    from genmodelfit import update

    before = _write_cache(dbmod.cached_db_path())
    monkeypatch.setattr(update, "HttpFetcher", _Down)
    assert cli.main(["update", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "ネットワーク" in out["error"]
    assert dbmod.cached_db_path().read_text(encoding="utf-8") == before
    # recommend はキャッシュで動き続ける
    assert cli.main(["recommend", "--json", "--vram", "8", "--ram", "32", "-n", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["database"]["origin"] == "cache"


def test_upstream_format_error_keeps_cache(tmp_path, capsys):
    from genmodelfit import db as dbmod

    src = tmp_path / "src"
    (src / "templates").mkdir(parents=True)
    (src / "templates" / "index.json").write_text(json.dumps({"categories": []}), encoding="utf-8")
    before = _write_cache(dbmod.cached_db_path())
    assert cli.main(["update", "--offline-sources", str(src)]) == 1
    assert "形が変わりました" in capsys.readouterr().err
    assert dbmod.cached_db_path().read_text(encoding="utf-8") == before


def test_too_many_template_failures(tmp_path):
    src = tmp_path / "src"
    (src / "templates").mkdir(parents=True)
    index = [{"title": "Image", "templates": [{"name": f"t{i}", "size": 1} for i in range(4)]}]
    (src / "templates" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (src / "templates" / "t0.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    with pytest.raises(UpstreamFormatError, match="3/4"):
        build_database(DirFetcher(src), hf=False)


def test_cli_update_writes_output(tmp_path, capsys):
    out = tmp_path / "db.json"
    args = ["update", "--json", "--offline-sources", str(FIX), "--skip-head", "--output", str(out)]
    # フィクスチャはテンプレート 1 件の JSON が無い(一部が欠けた)ので、--allow-partial が無ければ書かない
    assert cli.main(args) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert "--allow-partial" in err and "templates_failed=1" in err
    assert not out.exists()
    assert cli.main([*args, "--allow-partial"]) == 0
    env = json.loads(capsys.readouterr().out)
    assert env["sources"]["partial"] is True and env["sources"]["partial_counts"]["templates_failed"] == 1
    assert env["command"] == "update" and env["output"] == str(out)
    counts = env["sources"]["counts"]
    assert counts["models"] == env["database"]["models"] and counts["content"]["uncensored"] >= 2
    assert env["sources"]["excluded"] == 2
    db = load(out)
    assert db.get("image_z_image_turbo_int8") is not None


@pytest.mark.network
def test_network_update(tmp_path):
    db = build_database()
    counts = db.sources["counts"]
    assert counts["models"] >= 150
    assert set(counts["modality"]) == {"image", "video", "music", "sfx", "tts", "3d"}
    assert counts["content"].get("uncensored", 0) >= 10
    assert os.environ.get("GENMODELFIT_NETWORK_TESTS") == "1"


class _Resp:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def json(self):
        return self._body


def test_http_fetcher_waits_on_429():
    from genmodelfit.fetch import HttpFetcher

    slept: list[float] = []
    now = [0.0]

    def sleep(s):
        slept.append(s)
        now[0] += s

    f = HttpFetcher(sleep=sleep, clock=lambda: now[0])
    replies = [_Resp(429, {"Retry-After": "45"}), _Resp(200, body={"ok": True})]

    class S:
        def request(self, *a, **k):
            return replies.pop(0)

    f._local.session = S()
    assert f.get_json("https://huggingface.co/api/models/x") == {"ok": True}
    assert f.rate_limited == 1 and sum(slept) >= 45
    f._local.session = type("S2", (), {"request": lambda self, *a, **k: _Resp(404)})()
    with pytest.raises(FetchError) as e:
        f.get_json("https://huggingface.co/api/models/y")
    assert e.value.not_found


def test_bundled_snapshot_is_an_update_result():
    db = load(use_cache=False)
    counts = db.sources.get("counts", {})
    assert len(db.models) >= 150 and counts.get("models") == len(db.models)
    assert {m.modality for m in db.models} == {"image", "video", "music", "sfx", "tts", "3d"}
    unc = [m for m in db.models if m.content == "uncensored"]
    assert len(unc) >= 10 and all(m.content_evidence for m in unc)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", db.generated_at)
    hf = [m for m in db.models if m.source == "huggingface"]
    assert hf and all(m.id.startswith("hf:") and m.files and m.discovered_from for m in hf)
    # ライセンスの決め方の版と、一式のそろい具合
    assert db.sources.get("license_policy") == LICENSE_POLICY and db.sources.get("partial") is False
    assert all(m.files_complete is not None for m in db.models)
    assert all(m.size_floor_bytes for m in db.models if m.files_complete is False and m.template)


def test_hf_selection_rules_like_llmfit(built):
    """全期間 10,000 未満・試験用の名前・merge のタグは入れない。
    trendingScore・likes30d だけで拾ったものは 1,000 から。"""
    from genmodelfit.update import MIN_DOWNLOADS, MIN_TRENDING_DOWNLOADS

    assert (MIN_DOWNLOADS, MIN_TRENDING_DOWNLOADS) == (10_000, 1_000)
    skipped = built.sources["huggingface-search"]["skipped"]
    # rare-nsfw-xl(全期間 9,999)、brand-new-voice(trending だけ、900)、both-voice(全期間 5,000。
    # trending の検索にも
    # 出るが、ダウンロード数の順の検索にも出たので下限は 10,000)、old-voice(likes が多く trending の検索に
    # 出るが、trendingScore 0.1・直近 30 日 50 なので下限は 10,000。#19)
    assert skipped["skip_downloads"] == 4
    assert skipped["test_stub"] == 1 and skipped["merge"] == 1 and skipped["adapter"] == 1
    # new-voice(trending だけ、1,500)と meina-like-xl(直近 30 日 2,000・全期間 50,000)は下限を通る
    # (試験のフォルダにリポジトリの情報が無いので、取れなかった数に入る)
    assert skipped["fetch_failed"] == 2
    # #19: 直近 30 日 20 の無検閲(old-nsfw-xl)は入れない。plain-nsfw-xl の量子化の写し(-Q8-GGUF)は重複
    assert skipped["uncensored_inactive"] == 1 and skipped["duplicate"] == 1
    st = built.sources["huggingface-search"]
    assert st["all_time_missing"] >= 1  # downloadsAllTime の無い結果(直近 30 日の数で代えた)を数える
    ids = {e.id for e in built.models}
    assert not {"hf:example-org/tiny-random-nsfw-xl", "hf:example-org/nsfw-merge-xl"} & ids
    k = built.get(
        "hf:hexgrad/Kokoro-82M"
    )  # trending の検索にも出るが、downloads の順でも拾ったので下限は 10,000
    assert k is not None and k.downloads_all_time and k.downloads_all_time >= 10_000


def test_hf_selection_rules_19():
    """#19: 伸びているときだけ低い下限、名前の merge、量子化の写しの名前。"""
    from genmodelfit.update import (
        _MERGE_NAME,
        MIN_DOWNLOADS,
        MIN_TRENDING_DOWNLOADS,
        Candidate,
        copy_name,
        download_floor,
    )

    def cand(**kw):
        base = dict(
            repo_id="a/b", modality="image", task="t2i", downloads=500, likes=0, tags=[], uncensored=False
        )
        return Candidate(**{**base, **kw})

    assert download_floor(cand(trending_score=0.5)) == MIN_TRENDING_DOWNLOADS
    assert download_floor(cand(trending_score=0.4)) == MIN_DOWNLOADS
    assert download_floor(cand(trending_score=2.0, downloads=99)) == MIN_DOWNLOADS
    assert download_floor(cand(trending_score=2.0, trending_only=False)) == MIN_DOWNLOADS
    assert _MERGE_NAME.search("uber-realistic-porn-merge-urpm") and _MERGE_NAME.search("x_mergekit")
    assert not _MERGE_NAME.search("CamelliaMix_NSFW") and not _MERGE_NAME.search("emerged-model")
    assert copy_name("sothmik/Wai-NSFW-Illustrious-v140-Q8-GGUF") == copy_name(
        "Ine007/waiNSFWIllustrious_v140"
    )
    assert copy_name("x/Foo-Q4_K_M-GGUF") == copy_name("y/foo") == copy_name("z/Foo_diffusers") == "foo"
    assert copy_name("x/flux-fp8") == copy_name("x/flux-nf4") == copy_name("x/flux-int8") == "flux"
    assert copy_name("x/Qwen-Image-2.1") != copy_name("x/Qwen-Image-2.2")


def test_uncensored_words_heretic_and_unfiltered():
    """名前の語 heretic・unfiltered も無検閲の印にする(語の区切りで。オーナーの指示 2026-10-21)。"""
    from genmodelfit import update

    def words(text: str) -> set[str]:
        return {m.group(1).lower() for m in update._UNCENSORED.finditer(text)}

    assert words("someone/Qwen-Image-Heretic") == {"heretic"}
    assert words("someone/flux-unfiltered-v2 FLUX Unfiltered") == {"unfiltered"}
    assert words("someone/heretical-model filteredunfilteredx") == set()  # 語の途中は当てない
    assert {"heretic", "unfiltered"} <= set(update.UNCENSORED_SEARCH_WORDS)


def test_entry_license_url_prefers_the_main_file_with_the_same_license():
    """同じライセンスのファイルが複数あるとき、一式の license_url は本体(大きいもの)の場所。名前の順で
    VAE などの部品の場所にしない(#144)。本体より厳しい部品があれば、その部品の場所。"""
    from genmodelfit.models import ModelEntry, ModelFile
    from genmodelfit.update import set_entry_license

    def f(name, role, lic, url, size=1):
        return ModelFile(name=name, role=role, license=lic, license_url=url, size_bytes=size)

    vae = f("wan_2.1_vae.safetensors", "vae", "apache-2.0", "https://example.org/wan21")
    hi = f("wan2.2_fun_control_high_noise_14B.safetensors", "main", "apache-2.0", "https://example.org/hi", 9)
    lo = f("wan2.2_fun_control_low_noise_14B.safetensors", "main", "apache-2.0", "https://example.org/lo", 5)
    e = ModelEntry(id="x", name="x", modality="video", files=[lo, hi, vae])
    set_entry_license(e)
    assert (e.license, e.license_url) == ("apache-2.0", "https://example.org/hi")
    nc = f("lora.safetensors", "lora", "cc-by-nc-4.0", "https://example.org/nc")
    e = ModelEntry(id="y", name="y", modality="video", files=[lo, hi, vae, nc])
    set_entry_license(e)
    assert (e.license, e.license_url) == ("cc-by-nc-4.0", "https://example.org/nc")
