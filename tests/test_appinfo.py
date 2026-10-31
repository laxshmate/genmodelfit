"""アプリ向けの項目(languages / runtime / description)と手で足すモデル(#36)。

ネットワークには触らない。HF のカードは偽のクライアントで差し替える。
"""

from __future__ import annotations

import json
import re
from dataclasses import fields

import pytest

from genmodelfit import appinfo, cli
from genmodelfit import db as dbmod
from genmodelfit.db import DatabaseError, check_entry, load, parse_db
from genmodelfit.estimate import select_files
from genmodelfit.hf import TIER_USE_RESTRICTED, RepoInfo, license_tier
from genmodelfit.models import ModelEntry, ModelFile

NEW_KEYS = {"languages", "runtime", "description", "card_languages"}
# アプリがカタログの代わりにこの一覧を使う 12 件(AI生成キットの catalog.json の gmf_id。手のモデルは manual:…)
APP_12 = [
    "image_z_image_turbo_int8",
    "image_z_image_turbo",
    "image_sdxl_simple",
    "video_wan2_2_5B_ti2v",
    "text_to_video_wan",
    "audio_ace_step_1_5_checkpoint",
    "hf:stabilityai/stable-audio-3-small-sfx",
    "3d_hunyuan3d_image_to_model",
    "3d_hunyuan3d-v2.1",
    "manual:tsukuyomi-chan-piper-plus",
    "manual:irodori-tts-v4.1-small",
    "manual:stable-audio-3-small-music",
]


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


def _entry(files, *, tasks=("t2i",), modality="image", family=None, source="comfyui-template", id="x"):
    e = ModelEntry(id=id, name=id, modality=modality, tasks=list(tasks), source=source, comfy_family=family)
    e.files = [
        ModelFile(name=n, role=r, directory="text_encoders" if r == "text_encoder" else None)
        for n, r in files
    ]
    return e


# --- 項目の形・読み込み・古い版との互換 -------------------------------------------------------------


def test_new_fields_default_and_roundtrip():
    e = ModelEntry(id="a", name="a", modality="image")
    assert e.languages == {} and e.runtime is None and e.description == {}
    e.languages = {"prompt": ["en"], "speech": []}
    e.runtime, e.description = "comfyui", {"ja": "あ", "en": "a"}
    d = json.loads(json.dumps(e.to_dict()))
    back = ModelEntry.from_dict(d)
    assert (back.languages, back.runtime, back.description) == (e.languages, "comfyui", e.description)
    # 古い一覧(項目が無い・null)も読める
    old = ModelEntry.from_dict(
        {"id": "a", "name": "a", "modality": "image", "languages": None, "description": None}
    )
    assert old.languages == {} and old.description == {}


def test_check_entry_validates_new_fields():
    base = {"id": "a", "name": "a", "modality": "image", "content": "sfw"}
    check_entry(
        {
            **base,
            "languages": {"prompt": ["en"], "speech": []},
            "runtime": "piper",
            "description": {"ja": "x"},
        },
        0,
    )
    check_entry({**base, "languages": None, "runtime": None, "description": None}, 0)
    for bad in (
        {"languages": ["en"]},
        {"languages": {"prompt": "en"}},
        {"languages": {"prompt": [1]}},
        {"runtime": 3},
        {"description": "text"},
        {"description": {"ja": 1}},
    ):
        with pytest.raises(DatabaseError):
            check_entry({**base, **bad}, 0)


def test_old_readers_read_the_bundled_list(monkeypatch):
    """0.3.0 までの読み手(知らないキーを捨てる from_dict と、知っているキーだけ確かめる check_entry)で、
    新しい一覧を読んでも壊れない。新しい項目を知らない版を、フィールドと型の表から新しい項目を除いて真似る。"""
    data = json.loads(dbmod.bundled_text("db.json"))
    assert data["schema_version"] == 1  # 上げていない(古い版が拒むため)
    known = {f.name for f in fields(ModelEntry)} - NEW_KEYS
    old_types = {k: v for k, v in dbmod._ENTRY_TYPES.items() if k not in NEW_KEYS}
    assert set(old_types) < set(dbmod._ENTRY_TYPES)
    n_manual = 0
    for i, m in enumerate(data["models"]):
        dbmod._check_types(m, old_types, f"models[{i}]")  # 古い check_entry
        old = ModelEntry(**{k: v for k, v in m.items() if k in known and k not in ("files", "template")})
        assert not hasattr(old, "nonexistent")
        assert old.id and old.source in ("comfyui-template", "huggingface", "manual")
        n_manual += m["source"] == "manual"
    assert n_manual == len(json.loads(dbmod.bundled_text("manual.json"))["models"])
    # 新しい読み手も同じ一覧を読める(知らないキーを捨てる仕組みは ModelFile・Template にも)
    assert len(parse_db(data, "path").models) == len(data["models"])


def test_old_style_entry_without_new_keys_still_loads():
    data = json.loads(dbmod.bundled_text("db.json"))
    for m in data["models"]:
        for k in NEW_KEYS:
            m.pop(k, None)
    db = parse_db(data, "path")
    assert all(e.languages == {} and e.runtime is None and e.description == {} for e in db.models)


# --- 言語 ------------------------------------------------------------------------------------------


def test_normalize_languages():
    n = appinfo.normalize_languages
    assert n(["en", "ja", "zh-CN", "ZH", "jpn", "multilingual", "und", "x", 3]) == ["en", "ja", "zh"]
    assert n("ja") == ["ja"] and n("eng") == ["en"]
    assert n(None) == [] and n({"a": 1}) == [] and n([]) == []


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ([("umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoder")], "multi"),
        ([("qwen_3_4b.safetensors", "text_encoder")], "multi"),
        ([("qwen3.5_2b_bf16.safetensors", "text_encoder")], "multi"),
        ([("qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoder")], "multi"),
        ([("text_encoders/Qwen2.5-VL-7B-Instruct-q4_0.gguf", "text_encoder")], "multi"),
        ([("clip_l.safetensors", "text_encoder"), ("t5xxl_fp16.safetensors", "text_encoder")], "en"),
        # 多言語が 1 つでもあれば多言語(Kandinsky 5: CLIP + Qwen2.5-VL)
        ([("clip_l.safetensors", "text_encoder"), ("qwen_2.5_vl_7b.safetensors", "text_encoder")], "multi"),
        # 表に無い部品(Llama・Gemma・Qwen3-VL)が混じれば決めない = 当て推量しない
        (
            [
                ("clip_l.safetensors", "text_encoder"),
                ("llama_3.1_8b_instruct_fp8_scaled.safetensors", "text_encoder"),
            ],
            None,
        ),
        ([("gemma_3_12B_it.safetensors", "text_encoder")], None),
        ([("qwen3vl_8b_fp8_scaled.safetensors", "text_encoder")], None),
        ([("qwen_0.6b_ace15.safetensors", "text_encoder")], None),
        ([("mistral_3_small_flux2_bf16.safetensors", "text_encoder")], None),
        ([("model.safetensors", "main")], None),  # 部品も系統も無い
    ],
)
def test_prompt_kind_from_text_encoder_table(files, expected):
    e = _entry(files)
    assert appinfo.prompt_kind(e) == expected
    # カードが無いときに表だけで付けるのは en だけ(多言語の部品でも ja は付けない)
    assert appinfo.prompt_from_tables(e) == (["en"] if expected else [])


def test_prompt_table_family_and_task_gates():
    assert appinfo.prompt_from_tables(_entry([("sd_xl_base_1.0.safetensors", "main")], family="sd")) == ["en"]
    assert appinfo.prompt_from_tables(_entry([("x.safetensors", "main")], family="flux")) == []
    # 指示の文を取らない作業(画像から 3D・拡大)には表を当てない
    enc = [("umt5_xxl.safetensors", "text_encoder")]
    assert appinfo.prompt_from_tables(_entry(enc, tasks=("i23d",), modality="3d")) == []
    assert appinfo.prompt_from_tables(_entry(enc, tasks=("upscale",))) == []
    # 読み上げは prompt を持たない(speech で表す)
    assert appinfo.prompt_from_tables(_entry(enc, tasks=("tts",), modality="tts")) == []


class _FakeClient:
    def __init__(self, langs):
        self.langs = langs

    def cached_repo(self, repo_id, revision="main"):
        v = self.langs.get(repo_id)
        return None if v is None else RepoInfo(repo_id=repo_id, card={"language": v})


def test_apply_precedence_override_card_table():
    enc = [("qwen_3_4b.safetensors", "text_encoder")]
    e = _entry([*enc, ("m.safetensors", "main")], id="m1")
    e.files[1].repo_id = "owner/m"
    # 表だけ(カードが無い)なら en だけ。多言語の部品でも ja は付けない
    appinfo.apply_app_fields([e], None, table={})
    assert e.languages == {"prompt": ["en"]} and e.card_languages is None
    # カードが言語を書いていれば、それが上限(Z-Image のカードは en だけ = 部品が Qwen3 でも ja を付けない)
    e2 = _entry([*enc, ("m.safetensors", "main")], id="m2")
    e2.files[1].repo_id = "owner/m"
    appinfo.apply_app_fields([e2], _FakeClient({"owner/m": ["en"]}), table={})
    assert e2.languages == {"prompt": ["en"]} and e2.card_languages == ["en"]
    appinfo.apply_app_fields([e2], _FakeClient({"owner/m": ["en", "zh"]}), table={})
    assert e2.languages == {"prompt": ["en", "zh"]}
    # 英語だけの部品(CLIP・T5)なら、カードの言語のうち en だけ残す
    e5 = _entry([("clip_l.safetensors", "text_encoder"), ("m.safetensors", "main")], id="m5")
    e5.files[1].repo_id = "owner/m"
    appinfo.apply_app_fields([e5], _FakeClient({"owner/m": ["en", "ja", "zh"]}), table={})
    assert e5.languages == {"prompt": ["en"]}
    # 表の無いモデルはカードのまま(zh を足さない・ja を付けない)
    e3 = _entry([("m.safetensors", "main")], id="m3")
    e3.files[0].repo_id = "owner/m"
    appinfo.apply_app_fields([e3], _FakeClient({"owner/m": ["en", "zh"]}), table={})
    assert e3.languages == {"prompt": ["en", "zh"]}
    # 手の表が一番強い。[] は「無い」として残す
    appinfo.apply_app_fields(
        [e3],
        _FakeClient({"owner/m": ["en", "zh"]}),
        table={"m3": {"languages": {"prompt": ["en"], "speech": []}}},
    )
    assert e3.languages == {"prompt": ["en"], "speech": []}
    # 読み上げはカードの言語が speech
    t = _entry(
        [("model.safetensors", "main")], tasks=("tts",), modality="tts", source="huggingface", id="hf:o/tts"
    )
    appinfo.apply_app_fields([t], _FakeClient({"o/tts": ["ja", "eng"]}), table={})
    assert t.languages == {"speech": ["ja", "en"]} and t.runtime is None
    # 何も決まらなければ空(キーを付けない)
    none = _entry([("m.safetensors", "main")], id="m4")
    appinfo.apply_app_fields([none], None, table={})
    assert none.languages == {}


def test_card_languages_are_kept_for_offline_reapplying():
    """カードは読めたときに保存し、通信しない当て直し(client なし)や、カードを読めなかった回でも使う。"""
    e = _entry([("umt5_xxl.safetensors", "text_encoder"), ("m.safetensors", "main")], id="w")
    e.files[1].repo_id = "owner/m"
    appinfo.apply_app_fields([e], _FakeClient({"owner/m": ["en", "zh"]}), table={})
    assert e.card_languages == ["en", "zh"]
    appinfo.apply_app_fields([e], None, table={})
    assert e.languages == {"prompt": ["en", "zh"]}
    appinfo.apply_app_fields([e], _FakeClient({}), table={})  # 読めなかった = 前の値
    assert e.card_languages == ["en", "zh"] and e.languages == {"prompt": ["en", "zh"]}
    # 読めたがカードに言語が無い = [](表だけで決める)
    appinfo.apply_app_fields([e], _FakeClient({"owner/m": []}), table={})
    assert e.card_languages == [] and e.languages == {"prompt": ["en"]}
    # 保存した値は db.json に出て、読み戻せる
    back = ModelEntry.from_dict(json.loads(json.dumps(e.to_dict())))
    assert back.card_languages == []


def test_narrowing_the_encoder_table_takes_effect(monkeypatch):
    """前の languages を引き継がない: 部品の表を狭めて当て直すと反映される。"""
    e = _entry([("umt5_xxl.safetensors", "text_encoder"), ("m.safetensors", "main")], id="wan")
    e.card_languages = ["en", "ja", "zh"]
    appinfo.apply_app_fields([e], None, table={})
    assert e.languages == {"prompt": ["en", "ja", "zh"]}
    narrowed = tuple((pat, "en" if pat == "umt5" else kind, src) for pat, kind, src in appinfo.TEXT_ENCODERS)
    monkeypatch.setattr(appinfo, "TEXT_ENCODERS", narrowed)
    appinfo.apply_app_fields([e], None, table={})
    assert e.languages == {"prompt": ["en"]}
    # カードの無いモデルも、表から外せば決めない(空)
    f = _entry([("umt5_xxl.safetensors", "text_encoder")], id="f")
    appinfo.apply_app_fields([f], None, table={})
    assert f.languages == {"prompt": ["en"]}
    monkeypatch.setattr(appinfo, "TEXT_ENCODERS", ())
    appinfo.apply_app_fields([f], None, table={})
    assert f.languages == {}


def test_rows_removed_from_the_hand_table_disappear():
    e = _entry([("m.safetensors", "main")], id="m", source="huggingface")
    row = {"m": {"languages": {"prompt": ["en", "ja"]}, "runtime": "piper", "description": {"ja": "説明"}}}
    want = ({"prompt": ["en", "ja"]}, "piper", {"ja": "説明"})
    appinfo.apply_app_fields([e], None, table=row)
    assert (e.languages, e.runtime, e.description) == want
    appinfo.apply_app_fields([e], None, table=row)  # 冪等
    assert (e.languages, e.runtime, e.description) == want
    appinfo.apply_app_fields([e], None, table={})
    assert (e.languages, e.runtime, e.description) == ({}, None, {})
    # 手のモデルの説明・言語・動かし方は manual.json のもの(手の表に無くても消さない)
    m = next(x for x in appinfo.manual_entries() if x.runtime == "piper")
    before = (dict(m.languages), m.runtime, dict(m.description))
    appinfo.apply_app_fields([m], None, table={})
    assert (m.languages, m.runtime, m.description) == before


def test_runtime_rule():
    assert appinfo.runtime_of(_entry([], source="comfyui-template")) == "comfyui"
    hf_img = _entry([], source="huggingface", family="sd")
    assert appinfo.runtime_of(hf_img) == "comfyui"
    assert appinfo.runtime_of(_entry([], source="huggingface")) is None  # 系統に当たらない
    assert appinfo.runtime_of(_entry([], source="huggingface", modality="tts", family="sd")) is None
    # 手のモデルの書いた値は消さない
    m = _entry([], source="manual")
    m.runtime = "piper"
    appinfo.apply_app_fields([m], None, table={})
    assert m.runtime == "piper"


# --- 手で足すモデル ----------------------------------------------------------------------------------


def test_manual_entries_are_complete():
    ms = {e.id: e for e in appinfo.manual_entries()}
    assert set(ms) == {
        "manual:tsukuyomi-chan-piper-plus",
        "manual:irodori-tts-v4.1-small",
        "manual:stable-audio-3-small-music",
        "manual:supertonic-3",
    }
    for e in ms.values():
        assert e.source == "manual" and e.template is None and e.files_complete is True
        assert e.description.get("ja") and e.description.get("en")
        assert e.license is not None and e.license_url
        for f in e.files:
            assert f.url.startswith("https://huggingface.co/")
            # main か、版を固定した 40 桁の版(Supertonic 3。#127)
            assert re.search(r"/resolve/(main|[0-9a-f]{40})/", f.url), f.url
            assert re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size_bytes > 0 and f.directory
            assert f.license and f.license_from and f.license_url and f.gated is False
            assert f.repo_id == "/".join(f.url.split("/")[3:5])
    piper = ms["manual:tsukuyomi-chan-piper-plus"]
    assert (piper.runtime, piper.modality, piper.languages) == ("piper", "tts", {"speech": ["ja", "en"]})
    assert (piper.license, piper.license_name) == ("other", "tsukuyomi-chan-corpus")
    assert piper.license_url == "https://tyc.rei-yumesaki.net/material/corpus/#terms3"
    iro = ms["manual:irodori-tts-v4.1-small"]
    assert (iro.runtime, iro.modality, iro.languages, iro.license, iro.license_name) == (
        "irodori-tts",
        "tts",
        {"speech": ["ja"]},
        "other",
        "mit-with-ethical-restrictions",
    )
    # MIT だがカードの Ethical Restrictions があるので、寛容の段に入れない(用途の制限付き)
    assert license_tier(iro.license, iro.license_name) == TIER_USE_RESTRICTED
    assert iro.license_url == "https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small"  # 本体のもの
    sa = ms["manual:stable-audio-3-small-music"]
    assert (sa.runtime, sa.modality, sa.comfy_family) == ("comfyui", "music", "stable_audio")
    assert (sa.license, sa.license_name) == ("other", "stable-audio-community")  # gemma より本体が厳しい側
    assert {f.license for f in sa.files} == {"other", "gemma"}
    assert sa.languages == {"prompt": ["en"], "speech": []}
    # Supertonic 3(#127): 16 個を版 3cadd1ee… に固定。OpenRAIL-M は用途の制限付きの段
    st = ms["manual:supertonic-3"]
    assert (st.runtime, st.modality) == ("supertonic", "tts")
    assert (st.license, st.license_name) == ("openrail", "openrail-m")  # 名前は LICENSE の本文から
    assert license_tier(st.license, st.license_name) == TIER_USE_RESTRICTED
    assert len(st.files) == 16 and st.total_size_bytes == 401_276_744
    assert all("/resolve/3cadd1ee6394adea1bd021217a0e650ede09a323/" in f.url for f in st.files)
    assert [f.name for f in st.files if f.role == "main"] == ["onnx/vector_estimator.onnx"]
    assert (st.precision, st.precision_source) == ("fp32", "header")
    assert len(st.languages["speech"]) == 31 and "ja" in st.languages["speech"]
    assert st.license_url.endswith("/blob/3cadd1ee6394adea1bd021217a0e650ede09a323/LICENSE")


def test_merge_manual_replaces_and_keeps_order():
    old = ModelEntry(id="manual:irodori-tts-v4.1-small", name="old", modality="tts", source="manual")
    other = ModelEntry(id="a_template", name="t", modality="image", source="comfyui-template")
    hf = ModelEntry(id="hf:o/r", name="r", modality="image", source="huggingface")
    merged = appinfo.merge_manual([hf, old, other])
    ids = [e.id for e in merged]
    assert ids[0] == "a_template" and ids.count("manual:irodori-tts-v4.1-small") == 1
    assert next(e for e in merged if e.id == "manual:irodori-tts-v4.1-small").name != "old"
    assert [(e.source != "comfyui-template", e.modality, e.id) for e in merged] == sorted(
        (e.source != "comfyui-template", e.modality, e.id) for e in merged
    )


def test_merge_manual_drops_hf_duplicates_of_the_same_repo():
    """HF の収集が手のモデルと同じリポジトリを拾っても、一覧に 2 件出さない。
    部品・元のモデルの hf: は残す。"""
    dup = ModelEntry(
        id="hf:stabilityai/stable-audio-3-small-music", name="d", modality="music", source="huggingface"
    )
    keep = [
        ModelEntry(
            id="hf:stabilityai/stable-audio-3-small-sfx", name="s", modality="sfx", source="huggingface"
        ),
        ModelEntry(id="hf:google/t5gemma-b-b-ul2", name="g", modality="other", source="huggingface"),
        ModelEntry(id="hf:Aratako/Irodori-TTS-v4-Small", name="v4", modality="tts", source="huggingface"),
    ]
    iro = ModelEntry(id="hf:Aratako/Irodori-TTS-v4.1-Small", name="i", modality="tts", source="huggingface")
    merged = {e.id for e in appinfo.merge_manual([dup, iro, *keep])}
    assert "hf:stabilityai/stable-audio-3-small-music" not in merged
    assert "hf:Aratako/Irodori-TTS-v4.1-Small" not in merged
    assert {e.id for e in keep} <= merged and "manual:stable-audio-3-small-music" in merged


class _CardClient:
    def __init__(self, cards):
        self.cards = cards

    def repo(self, repo_id, revision="main"):
        c = self.cards.get(repo_id)
        return None if c is None else RepoInfo(repo_id=repo_id, license=c[0], license_name=c[1])


def test_verify_manual_licenses_takes_the_stricter_on_mismatch():
    ms = appinfo.manual_entries()
    cards = {
        "ayousanz/piper-plus-tsukuyomi-chan": ("other", "tsukuyomi-chan-corpus"),
        "Aratako/Irodori-TTS-v4.1-Small": ("mit", None),
        "Aratako/Semantic-DACVAE-Japanese-32dim": ("mit", None),
        "stabilityai/stable-audio-3-small-music": ("other", "stable-audio-community"),
        # カードは license_name なし。書いた openrail-m は本文で細かくしたもの(警告しない。#127)
        "Supertone/supertonic-3": ("openrail", None),
    }  # t5gemma は読めない(gated)ものとして None
    warns = appinfo.verify_manual_licenses(ms, _CardClient(cards))
    assert warns == [
        "手のモデル manual:stable-audio-3-small-music: "
        "google/t5gemma-b-b-ul2 のカードを読めず、ライセンスを照合できません"
    ]
    # カードが書いた値より厳しければ、厳しいほうにする(寛容側に間違えない)
    ms = appinfo.manual_entries()
    cards["Aratako/Irodori-TTS-v4.1-Small"] = ("cc-by-nc-4.0", None)
    warns = appinfo.verify_manual_licenses(ms, _CardClient(cards))
    iro = next(e for e in ms if e.id == "manual:irodori-tts-v4.1-small")
    assert iro.files[0].license == "cc-by-nc-4.0" and iro.license == "cc-by-nc-4.0"
    assert any(
        "manual:irodori-tts-v4.1-small" in w and "model.safetensors" in w and "カードと違います" in w
        for w in warns
    )
    assert any(x.startswith("manual_mismatch") for x in iro.files[0].license_evidence)
    # カードのほうが寛容でも、書いた値(厳しい側)を残す。本文の制限を読んで厳しくしたもの(Irodori の
    # cardData は mit)は警告もしない(最初の照合の警告が t5gemma だけなのがそれ)
    ms = appinfo.manual_entries()
    cards.update(
        {"Aratako/Irodori-TTS-v4.1-Small": ("mit", None), "ayousanz/piper-plus-tsukuyomi-chan": ("mit", None)}
    )
    appinfo.verify_manual_licenses(ms, _CardClient(cards))
    piper = next(e for e in ms if e.id == "manual:tsukuyomi-chan-piper-plus")
    assert piper.license == "other" and piper.license_name == "tsukuyomi-chan-corpus"


def test_estimator_counts_onnx_only_for_own_runtime():
    piper = next(e for e in appinfo.manual_entries() if e.runtime == "piper")
    chosen, _ = select_files(piper)
    assert [f.name for f in chosen] == ["tsukuyomi-chan-6lang-fp16.onnx"]
    for rt in (None, "comfyui"):  # これまでと同じ数え方
        piper.runtime = rt
        assert select_files(piper)[0] == []


# --- 同梱の一覧 --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundled():
    return load(use_cache=False)


def test_bundled_has_the_app_12_with_description_and_languages(bundled):
    for mid in APP_12:
        m = bundled.get(mid)
        assert m is not None, mid
        assert m.description.get("ja") and m.description.get("en"), mid
        assert m.languages, mid  # 画像から 3D の prompt は [] = 指示の文を取らない
        assert m.runtime, mid
    by = {m.id: m for m in bundled.models}
    # 実機で確かめた注意書き(カタログの notes)と矛盾しない
    assert by["image_sdxl_simple"].languages == {"prompt": ["en"]}  # 英語のみ
    for mid in ("video_wan2_2_5B_ti2v", "text_to_video_wan"):  # 日本語・英語・中国語
        assert {"ja", "en", "zh"} <= set(by[mid].languages["prompt"])
    for mid in ("image_z_image_turbo_int8", "image_z_image_turbo"):
        assert "ja" in by[mid].languages["prompt"] and "en" in by[mid].languages["prompt"]
    assert "ja" in by["audio_ace_step_1_5_checkpoint"].languages["speech"]
    assert by["manual:irodori-tts-v4.1-small"].languages == {"speech": ["ja"]}  # 日本語専用
    assert by["manual:stable-audio-3-small-music"].languages["speech"] == []  # 歌は入らない
    assert by["3d_hunyuan3d_image_to_model"].languages == {"prompt": []}


def test_bundled_is_stable_under_reapplying(bundled):
    """update が毎回当てても同じ(手の表と表は、db.json を作り直しても消えない)。"""
    import copy

    models = copy.deepcopy(bundled.models)
    before = [(m.languages, m.runtime, m.description) for m in models]
    appinfo.apply_app_fields(models)
    assert [(m.languages, m.runtime, m.description) for m in models] == before
    # 前の値を全部消して当て直しても、保存したカードの言語・手の表・モデルの表から同じものが出る
    for m in models:
        if m.source != "manual":  # 手のモデルの項目は manual.json に書いてある
            m.languages, m.runtime, m.description = {}, None, {}
    appinfo.apply_app_fields(models)
    assert [(m.languages, m.runtime, m.description) for m in models] == before


def test_ja_comes_only_from_cards_or_the_hand_table(bundled):
    """ja は、カードに書いてあるか、手の表(人が確かめたもの)にあるときだけ。部品の表だけでは付けない。
    カードが言語を書いていれば、それより広くしない。"""
    hand = appinfo.overrides()
    for m in bundled.models:
        if m.source == "manual" or m.id in hand:
            continue
        card = m.card_languages or []
        for k in ("prompt", "speech"):
            langs = m.languages.get(k) or []
            if "ja" in langs:
                assert "ja" in card, m.id
            if card:
                assert set(langs) <= set(card), m.id


def test_languages_that_were_over_attributed_are_fixed(bundled):
    """検証で見つかった付けすぎ(カードが en / en,zh なのに ja)と、確証の無い ja(Kandinsky5・Anima)。"""
    by = {m.id: m for m in bundled.models}
    assert by["audio_stable_audio_3_medium"].languages["prompt"] == ["en"]
    assert "ja" not in by["video_hunyuan_video_1.5_720p_t2v"].languages["prompt"]
    for mid in ("video_kandinsky5_t2v", "image_kandinsky5_t2i", "image_anima_base_v1", "image_z_image"):
        assert "ja" not in by[mid].languages.get("prompt", []), mid
    # カードの誤り(Wan2.2 S2V の es、MeloTTS-English の ko)は手の表で直す
    assert by["video_wan2_2_14B_s2v"].languages == {"prompt": ["en", "zh"]}
    assert by["hf:myshell-ai/MeloTTS-English"].languages == {"speech": ["en"]}


def test_descriptions_do_not_claim_cpu_where_the_verdict_says_no(capsys):
    """説明に「GPU なしでも動く」と書くのは、genmodelfit の判定(--vram 0)が動くと言うものだけ
    (CPU での判定は別の Issue)。"""
    args = ["recommend", "--vram", "0", "--ram", "32", "--json", "--content", "all", "--fit", "all"]
    assert (
        cli.main(
            [*args, "--all-workflows", "--include-tools", "--include-unknown-license", "--limit", "1000"]
        )
        == 0
    )
    for m in json.loads(capsys.readouterr().out)["models"]:
        text = " ".join((m.get("description") or {}).values())
        if "GPU なし" in text or "without a GPU" in text:
            assert m["fit_level"] not in ("too_tight", "?"), m["id"]


def test_manual_models_show_in_recommend_and_info(capsys):
    assert cli.main(["recommend", "-m", "tts", "--vram", "8", "--ram", "16", "--json", "--limit", "50"]) == 0
    d = json.loads(capsys.readouterr().out)
    got = {m["id"]: m for m in d["models"]}
    assert "manual:tsukuyomi-chan-piper-plus" in got and "manual:irodori-tts-v4.1-small" in got
    p = got["manual:tsukuyomi-chan-piper-plus"]
    assert (p["source"], p["runtime"], p["languages"], p["fit_level"]) == (
        "manual",
        "piper",
        {"speech": ["ja", "en"]},
        "perfect",
    )
    assert p["description"]["ja"].startswith("軽くて速い読み上げ")
    assert (
        cli.main(["recommend", "-m", "music", "--vram", "8", "--ram", "16", "--json", "--limit", "50"]) == 0
    )
    assert "manual:stable-audio-3-small-music" in {
        m["id"] for m in json.loads(capsys.readouterr().out)["models"]
    }
    assert cli.main(["info", "manual:irodori-tts-v4.1-small", "--vram", "8", "--ram", "16"]) == 0
    out = capsys.readouterr().out
    assert "動かし方:   irodori-tts" in out and "言語:       読み上げ ja" in out and "出どころ: manual" in out
    assert cli.main(["info", "manual:irodori-tts-v4.1-small", "--json"]) == 0
    m = json.loads(capsys.readouterr().out)["model"]
    assert m["runtime"] == "irodori-tts" and m["languages"] == {"speech": ["ja"]} and m["source"] == "manual"


def test_download_plan_lists_manual_files(tmp_path):
    import genmodelfit as gmf

    plan = gmf.plan_download(
        "manual:tsukuyomi-chan-piper-plus", output_dir=tmp_path, include_unknown_license=False
    )
    names = sorted(f.name for f in plan.files)
    assert names == ["config.json", "tsukuyomi-chan-6lang-fp16.onnx"]


@pytest.mark.parametrize(
    ("mid", "relpaths"),
    [
        ("manual:tsukuyomi-chan-piper-plus", ["tts/config.json", "tts/tsukuyomi-chan-6lang-fp16.onnx"]),
        (
            "manual:irodori-tts-v4.1-small",
            [
                "tts/dacvae/weights.pth",
                "tts/model.safetensors",
                "tts/tokenizer/tokenizer.json",
                "tts/tokenizer/tokenizer_config.json",
            ],
        ),
        (
            "manual:stable-audio-3-small-music",
            [
                "checkpoints/stable_audio_3_small_music.safetensors",
                "text_encoders/t5gemma_b_b_ul2.safetensors",
            ],
        ),
        (
            "manual:supertonic-3",
            sorted(
                [f"tts/onnx/{n}" for n in ("duration_predictor.onnx", "text_encoder.onnx", "tts.json")]
                + [f"tts/onnx/{n}" for n in ("unicode_indexer.json", "vector_estimator.onnx", "vocoder.onnx")]
                + [f"tts/voice_styles/{g}{i}.json" for g in "FM" for i in range(1, 6)]
            ),
        ),
    ],
)
def test_download_plan_of_every_manual_model(tmp_path, mid, relpaths):
    """手の 3 件のファイルがすべて置き場所の中に入る(directory は 1 段、サブフォルダは name に。
    置き場所の外を指す名前として断るファイルが無い)。"""
    import genmodelfit as gmf

    plan = gmf.plan_download(mid, output_dir=tmp_path, include_unknown_license=False)
    assert sorted(f.relpath for f in plan.files) == relpaths
    for f in plan.files:
        assert f.status == "download" and f.reason is None, (f.name, f.status, f.reason)
        assert f.directory and "/" not in f.directory and f.dest


# --- update に載る ------------------------------------------------------------------------------------


def test_fields_only_offline_updates_a_copy(tmp_path):
    data = json.loads(dbmod.bundled_text("db.json"))
    data["models"] = [m for m in data["models"] if m["source"] != "manual"]
    for m in data["models"]:
        for k in NEW_KEYS:
            m.pop(k, None)
    src = tmp_path / "old.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "new.json"
    assert cli.main(["update", "--fields-only", "--offline", "--db", str(src), "--output", str(out)]) == 0
    new = load(out)
    assert len(new.models) == len(data["models"]) + 4
    assert new.sources["manual"]["models"] == 4 and new.sources["counts"]["models"] == len(new.models)
    assert new.sources["app_fields"]["description"] == 13
    old_by = {m["id"]: m for m in data["models"]}
    for m in new.models:  # 既存のモデルの判定に関わる項目は変えない
        if m.source == "manual":
            continue
        d = m.to_dict()
        for k in NEW_KEYS:
            d.pop(k)
        assert d == {**old_by[m.id], "total_size_bytes": d["total_size_bytes"]}, m.id
    assert new.get("image_sdxl_simple").languages == {"prompt": ["en"]}


def test_build_database_includes_manual_and_app_fields():
    from pathlib import Path

    from genmodelfit.fetch import DirFetcher
    from genmodelfit.update import build_database

    built = build_database(
        DirFetcher(Path(__file__).parent / "fixtures" / "update"), head=False, carry_over=False
    )
    ids = {e.id for e in built.models}
    assert {"manual:tsukuyomi-chan-piper-plus", "manual:irodori-tts-v4.1-small"} <= ids
    n = len(appinfo.manual_entries())
    assert built.sources["manual"]["models"] == n and built.sources["counts"]["source"]["manual"] == n
    assert built.sources["app_fields"]["runtime"] >= 3
    z = built.get("image_z_image_turbo_int8")
    assert z.runtime == "comfyui" and "ja" in z.languages["prompt"]
    # 上流のカードを読めない(フィクスチャに無い)ときは、書いた値のまま警告だけ
    assert any(w.startswith("手のモデル ") for w in built.sources["warnings"])
    assert built.get("manual:tsukuyomi-chan-piper-plus").license == "other"


def test_merge_manual_keeps_measured_file_values():
    """通信しない書き直しで、前に確かめた HEAD・見出しの精度を消さない。sha256 が変われば引き継がない。"""
    first = appinfo.manual_entries()
    iro = next(e for e in first if e.runtime == "irodori-tts")
    iro.files[0].head_status, iro.files[0].head_checked_at = 302, "2026-10-21T00:00:00Z"
    iro.files[0].header_precision = "fp32"
    again = appinfo.merge_manual([iro])
    new = next(e for e in again if e.id == iro.id)
    assert new is not iro and new.files[0].head_status == 302 and new.files[0].header_precision == "fp32"
    assert new.precision == "fp32" and new.precision_source == "header"
    iro.files[0].sha256 = "0" * 64
    changed = next(e for e in appinfo.merge_manual([iro]) if e.id == iro.id)
    assert changed.files[0].head_status is None and changed.files[0].header_precision is None


def test_gate_holds_non_comfyui_runtimes_with_its_own_reason():
    """runtime が ComfyUI 以外の手のモデルは、ComfyUI の 3 条件ではなく app_runtime で保留
    (#95 から Score に準備の点は無い)。"""
    from genmodelfit.gate import CHECK_IDS, evaluate_offline

    ms = {e.id: e for e in appinfo.manual_entries()}
    for mid in ("manual:tsukuyomi-chan-piper-plus", "manual:irodori-tts-v4.1-small"):
        held = evaluate_offline(ms[mid]).held_by
        assert "app_runtime" in held, mid
        assert not {"official_workflow", "custom_nodes", "comfyui_version"} & set(held), mid
    sa = evaluate_offline(ms["manual:stable-audio-3-small-music"]).held_by  # comfyui は今までどおり
    assert "official_workflow" in sa and "app_runtime" not in sa
    assert "app_runtime" in CHECK_IDS


def test_license_tier_reads_use_restrictions_in_the_name():
    from genmodelfit.hf import TIER_NONCOMMERCIAL, TIER_OTHER, TIER_PERMISSIVE

    assert license_tier("other", "mit-with-ethical-restrictions") == TIER_USE_RESTRICTED
    assert license_tier("mit", "mit-with-ethical-restrictions") == TIER_USE_RESTRICTED
    assert license_tier("other", "acme-use-restrictions") == TIER_USE_RESTRICTED
    assert license_tier("mit", None) == TIER_PERMISSIVE
    assert license_tier("other", "unethical-restrictionsx") == TIER_OTHER  # 語の途中は当てない
    assert (
        license_tier("other", "flux-non-commercial-ethical-restrictions") == TIER_NONCOMMERCIAL
    )  # 非商用が先


# --- 音楽のカードの言語(#41) -------------------------------------------------------------------------


def test_music_card_languages_only_give_english_prompts():
    """音楽のカードの language は歌詞の言語のことが多い(ACE-Step v1 は 17 言語)。指示は en だけにし、
    歌の言語(speech)はカードからは決めない。効果音・画像はカードのまま。"""
    card = ["en", "zh", "ja", "ko"]
    music = _entry([("m.safetensors", "main")], tasks=("t2a",), modality="music", id="mu")
    music.files[0].repo_id = "owner/m"
    sfx = _entry([("m.safetensors", "main")], tasks=("t2a",), modality="sfx", id="sx")
    sfx.files[0].repo_id = "owner/m"
    image = _entry([("m.safetensors", "main")], id="im")
    image.files[0].repo_id = "owner/m"
    appinfo.apply_app_fields([music, sfx, image], _FakeClient({"owner/m": card}), table={})
    assert music.languages == {"prompt": ["en"]}
    assert sfx.languages == {"prompt": card} and image.languages == {"prompt": card}
    # カードに en が無ければ、音楽の指示は決めない(キーを付けない)
    zh = _entry([("m.safetensors", "main")], tasks=("t2a",), modality="music", id="zh")
    zh.files[0].repo_id = "owner/zh"
    appinfo.apply_app_fields([zh], _FakeClient({"owner/zh": ["zh"]}), table={})
    assert zh.languages == {}


def test_bundled_music_prompts_are_not_lyric_languages(bundled):
    by = {m.id: m for m in bundled.models}
    for mid in ("audio_ace_step_1_t2a_instrumentals", "audio_yue2_text2music"):
        assert by[mid].languages == {"prompt": ["en"]}, mid
    # 歌入りの ACE-Step v1 は、歌える言語を手の表に書いた(README の「top 10 well-performing languages」)
    song = by["audio_ace_step_1_t2a_song"].languages
    assert song["prompt"] == ["en"] and "ja" in song["speech"] and len(song["speech"]) == 10
    hand = appinfo.overrides()
    for m in bundled.models:
        if m.modality == "music" and m.source != "manual" and m.id not in hand:
            assert set(m.languages.get("prompt", [])) <= {"en"}, m.id
            assert "speech" not in m.languages, m.id


# --- 手で足す部品(#40) -----------------------------------------------------------------------------

SFX = "hf:stabilityai/stable-audio-3-small-sfx"
T5GEMMA_URL = (
    "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/text_encoders/t5gemma_b_b_ul2.safetensors"
)


def _sfx_from_hf():
    """HF の収集が作る形(本体 1 つだけ。部品が無い)。"""
    main = ModelFile(
        name="model.safetensors",
        url="https://huggingface.co/stabilityai/stable-audio-3-small-sfx/resolve/main/model.safetensors",
        sha256="ed9cf1b6172f1a8c2921a9560c21109ff3239524563ced9dce6dcdef41e2f515",
        size_bytes=2270384940,
        repo_id="stabilityai/stable-audio-3-small-sfx",
        license="other",
        license_name="stable-audio-community",
        gated=True,
        role="main",
    )
    return ModelEntry(
        id=SFX,
        name="stable-audio-3-small-sfx",
        modality="sfx",
        tasks=["t2a"],
        source="huggingface",
        files=[main],
    )


def test_components_add_t5gemma_with_gemma_terms_from_the_source_table():
    e = _sfx_from_hf()
    other = ModelEntry(id="hf:o/other", name="o", modality="sfx", source="huggingface")
    merged = appinfo.merge_manual([e, other], [])
    sfx = next(m for m in merged if m.id == SFX)
    assert [f.name for f in sfx.files] == ["model.safetensors", "t5gemma_b_b_ul2.safetensors"]
    te = sfx.files[1]
    assert (te.url, te.directory, te.role, te.repo_id) == (
        T5GEMMA_URL,
        "text_encoders",
        "text_encoder",
        "Comfy-Org/stable-audio-3",
    )
    # ライセンスは書いた値でなく、部品の出どころの表(一次資料で確かめた行)のもの
    assert (te.license, te.license_from, te.license_url) == (
        "gemma",
        "google/t5gemma-b-b-ul2",
        "https://ai.google.dev/gemma/terms",
    )
    assert "license_source:stable-audio-3-t5gemma" in te.license_evidence
    assert any(
        x.startswith("manual_component: https://huggingface.co/stabilityai/stable-audio-3-small-sfx")
        for x in te.license_evidence
    )
    # 一式は一番厳しいもの(stable-audio-community)のまま、licenses に両方
    from genmodelfit.recommend import _sum_licenses

    assert (sfx.license, sfx.license_name) == ("other", "stable-audio-community")
    assert set(_sum_licenses(sfx)) == {"other", "gemma"}
    assert sfx.total_size_bytes == 2270384940 + 1187264003
    assert next(m for m in merged if m.id == "hf:o/other").files == []
    # 何度当てても同じ(ファイルを重ねない・根拠を重ねない)。HEAD の結果は残す
    te.head_status, te.head_checked_at = 302, "2026-10-21T00:00:00Z"
    again = appinfo.merge_manual(merged, [])
    sfx2 = next(m for m in again if m.id == SFX)
    assert len(sfx2.files) == 2 and sfx2.files[1].license_evidence == te.license_evidence
    assert sfx2.files[1].head_status == 302


def test_components_without_a_source_row_stay_unknown():
    """部品の出どころの表に行が無ければ、書いた値があってもライセンスは不明(寛容な側に誤らない)。"""
    e = _sfx_from_hf()
    table = {
        SFX: {
            "evidence": "x",
            "files": [
                {
                    "name": "mystery.safetensors",
                    "url": "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/text_encoders/mystery.safetensors",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                    "directory": "text_encoders",
                    "role": "text_encoder",
                    "license": "mit",
                }
            ],
        }
    }
    assert appinfo.add_components([e], table) == [SFX]
    assert e.files[1].license is None
    from genmodelfit.recommend import license_unknown

    assert license_unknown(e)


def test_every_stable_audio_3_entry_has_the_t5gemma_text_encoder(bundled):
    """Stable Audio 3 は文字を読む部品に T5Gemma(Gemma 利用規約)を使う。どの項目もその部品を持つ。"""
    sa3 = [m for m in bundled.models if re.search(r"stable[-_]audio[-_]3", m.id)]
    assert {SFX, "manual:stable-audio-3-small-music", "audio_stable_audio_3_medium"} <= {m.id for m in sa3}
    for m in sa3:
        te = [f for f in m.files if f.name.endswith("t5gemma_b_b_ul2.safetensors")]
        assert te and te[0].license == "gemma" and te[0].role == "text_encoder", m.id


def test_bundled_sfx_is_what_merge_manual_rebuilds(bundled):
    """同梱の一覧の効果音の項目は、HF の収集の形に部品を足したもの(update が作り直しても同じ)。"""
    rebuilt = next(m for m in appinfo.merge_manual([_sfx_from_hf()], []) if m.id == SFX)
    have = bundled.get(SFX)
    keys = (
        "name",
        "url",
        "sha256",
        "size_bytes",
        "directory",
        "role",
        "license",
        "license_name",
        "license_from",
        "license_url",
        "license_path",
        "license_evidence",
        "repo_license",
        "gated",
    )
    assert [tuple(getattr(f, k) for k in keys) for f in have.files][1:] == [
        tuple(getattr(f, k) for k in keys) for f in rebuilt.files
    ][1:]


@pytest.mark.parametrize(
    "files",
    [
        [{"name": "a.safetensors", "role": "text_encoder"}],  # url が無い
        [{"name": "a.safetensors", "url": "http://huggingface.co/o/r/resolve/main/a.safetensors"}],
        [{"url": "https://huggingface.co/o/r/resolve/main/a.safetensors"}],  # name が無い
        [
            {"name": "a.safetensors", "url": "https://huggingface.co/o/r/resolve/main/a.safetensors"},
            {"name": "a.safetensors", "url": "https://huggingface.co/o/r/resolve/main/a.safetensors"},
        ],
        [],
    ],
)
def test_components_require_a_unique_https_url(files):
    """URL で同じファイルかを見るので、URL の無い行は当てるたびに重なる。形が崩れた表は読み込みで断る。"""
    with pytest.raises(ValueError, match="components"):
        appinfo.components({"components": {SFX: {"evidence": "x", "files": files}}})
    with pytest.raises(ValueError, match="components"):
        appinfo.add_components([_sfx_from_hf()], {SFX: {"evidence": "x", "files": files}})


def test_bundled_components_are_valid():
    assert SFX in appinfo.components()


def test_verify_keeps_a_license_name_that_refines_the_card():
    """カードの license が同じで license_name が無ければ、書いた license_name(本文で細かくした名前)は
    そのまま。警告も書き換えもしない(毎週の照合で openrail-m が消えないこと。#127)。
    license が違えば今までどおり。"""
    ms = [e for e in appinfo.manual_entries() if e.id == "manual:supertonic-3"]
    card = _CardClient({"Supertone/supertonic-3": ("openrail", None)})
    assert appinfo.verify_manual_licenses(ms, card) == []
    st = ms[0]
    assert {(f.license, f.license_name) for f in st.files} == {("openrail", "openrail-m")}
    assert (st.license, st.license_name) == ("openrail", "openrail-m")
    assert not any(x.startswith("manual_mismatch") for f in st.files for x in f.license_evidence)
    # カードに別の名前があれば食い違い(厳しいほうに寄せて警告)
    ms = [e for e in appinfo.manual_entries() if e.id == "manual:supertonic-3"]
    nc = _CardClient({"Supertone/supertonic-3": ("cc-by-nc-4.0", None)})
    warns = appinfo.verify_manual_licenses(ms, nc)
    assert warns and ms[0].license == "cc-by-nc-4.0"


# --- HF の読み上げの動かし方と部品(#142) ------------------------------------------------------------


def test_every_hf_speech_model_has_a_runtime_from_the_hand_table():
    """HF の読み上げは規則で動かし方を決めず、手の表に書く(カードの使い方で確かめたもの)。同梱の一覧の
    どれにも値があり、手の表の行には根拠(_note)がある。"""
    table = appinfo.overrides()
    db = load(None, use_cache=False)
    speech = [m for m in db.models if m.source == "huggingface" and m.modality == "tts"]
    assert speech
    for m in speech:
        assert m.runtime and m.runtime == table[m.id]["runtime"], m.id
        assert "#142" in table[m.id]["_note"], m.id
    # 規則だけでは決めない(手の表を外すと null)
    one = speech[0]
    appinfo.apply_app_fields([one], table={})
    assert one.runtime is None


@pytest.mark.parametrize(
    ("mid", "names", "lic"),
    [
        (
            "hf:OpenMOSS-Team/MOSS-TTS-v1.5",
            {"model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"},
            "apache-2.0",
        ),
        ("hf:ResembleAI/chatterbox", {"s3gen.pt", "ve.pt", "conds.pt"}, "mit"),
        ("hf:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", {"speech_tokenizer/model.safetensors"}, "apache-2.0"),
        ("hf:Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", {"speech_tokenizer/model.safetensors"}, "apache-2.0"),
        ("hf:openbmb/VoxCPM2", {"audiovae.pth"}, "apache-2.0"),
        (
            "hf:FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            {"llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v3.onnx", "campplus.onnx"},
            "apache-2.0",
        ),
        ("hf:k2-fsa/OmniVoice", {"audio_tokenizer/model.safetensors"}, "other"),
        ("hf:Serveurperso/Qwen3-TTS-GGUF", {"qwen-tokenizer-12hz-Q8_0.gguf"}, "apache-2.0"),
    ],
)
def test_speech_models_carry_the_parts_their_code_loads(mid, names, lic):
    """公式のコードが読む部品(音声トークナイザ・コーデックなど)を files に足し、ライセンスは部品の
    出どころの表、見積もりにも数える(#142)。"""
    from genmodelfit.estimate import select_files

    m = load(None, use_cache=False).get(mid)
    got = {f.name: f for f in m.files}
    assert names <= set(got)
    for n in names:
        f = got[n]
        assert f.license == lic and re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size_bytes > 0
        assert any(x.startswith("license_source:") for x in f.license_evidence)
        assert any(x.startswith("manual_component:") for x in f.license_evidence)
    counted = {f.name for f in select_files(m)[0]}
    assert names <= counted, counted
    # #142 の後から足した部品の URL は版に固定(main だと中身が変わったとき sha256 が合わなくなる)
    if mid in PINNED_PARTS:
        assert all(re.search(r"/resolve/[0-9a-f]{40}/", got[n].url) for n in names), mid


PINNED_PARTS = {
    "hf:FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    "hf:k2-fsa/OmniVoice",
    "hf:Serveurperso/Qwen3-TTS-GGUF",
}
