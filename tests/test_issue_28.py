"""0.1.1(Issue #28): Wan 2.2 14B の載せ替え・精度の選び方・ライセンス不明の扱い・判定できないものの表示・
利用者のテンプレートの並び・ファイルの見出しからの精度・OS の表示。
"""

from __future__ import annotations

import itertools
import json
import re
import struct
from pathlib import Path

import pytest
from clihelp import run_cli

import genmodelfit as gmf
from genmodelfit import cli, load_db
from genmodelfit.estimate import estimate, expert_sets
from genmodelfit.groups import file_precision, model_precision
from genmodelfit.hardware import os_name
from genmodelfit.headers import (
    HeaderCache,
    HeaderError,
    inspect_files,
    parse_gguf_header,
    parse_safetensors_header,
    read_header,
)
from genmodelfit.models import FIT_UNKNOWN, Gpu, ModelEntry, ModelFile, SystemInfo, fit_rank
from genmodelfit.recommend import (
    license_unknown,
    list_key,
    mode_label,
    recommend,
    runs_clean,
    select,
)
from genmodelfit.table import build_table, make_console
from genmodelfit.update import set_completeness, set_sampler_stages
from genmodelfit.workflow import sampler_stages

FIXTURES = Path(__file__).parent / "fixtures"
WAN22_14B = (
    "video_wan2_2_14B_i2v",
    "video_wan2_2_14B_t2v",
    "video_wan2_2_14B_flf2v",
    "video_wan2_2_14B_fun_control",
    "video_wan2_2_14B_fun_camera",
    "video_wan2_2_14B_fun_inpaint",
)


@pytest.fixture(scope="module")
def db():
    return load_db(use_cache=False)


def pc(vram: float, ram: float = 96) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="t", cpu="t", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


# --- 1. Wan 2.2 14B: 2 つの本体は順に使う ---------------------------------------------------------


def test_sampler_stages_from_the_template_graph():
    wf = json.loads((FIXTURES / "video_wan2_2_14B_i2v.json").read_text(encoding="utf-8"))
    assert sampler_stages(wf) == [
        [
            "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        ],
        [
            "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        ],
    ]
    # 本体が 1 つのテンプレートは 1 組
    one = json.loads((FIXTURES / "text_to_video_wan.json").read_text(encoding="utf-8"))
    assert sampler_stages(one) == [["wan2.1_t2v_1.3B_fp16.safetensors"]]


def test_set_sampler_stages_keeps_only_two_or_more_main_sets(db):
    wf = json.loads((FIXTURES / "video_wan2_2_14B_i2v.json").read_text(encoding="utf-8"))
    e = ModelEntry.from_dict(db.get("video_wan2_2_14B_i2v").to_dict())
    set_sampler_stages(e, sampler_stages(wf))
    assert len(e.sampler_stages) == 2
    one = ModelEntry.from_dict(db.get("text_to_video_wan").to_dict())
    set_sampler_stages(one, [["wan2.1_t2v_1.3B_fp16.safetensors"]])
    assert one.sampler_stages == []
    set_sampler_stages(one, None)
    assert one.sampler_stages is None


@pytest.mark.parametrize("mid", WAN22_14B)
def test_wan22_14b_sequential_counts_one_expert(db, mid):
    m = db.get(mid)
    found = expert_sets(m)
    assert found is not None and len(found[0]) == 2
    e = estimate(m, pc(24), measurements=db.measurements)
    main = max(f.size_bytes for f in m.files if f.directory == "diffusion_models") / 1024**3
    # 全部載せは 2 つとも、載せ替えは 1 つ分(本体 1 つ + その LoRA + 作業用メモリ)
    assert e.vram_gb - e.vram_sequential_gb > main * 0.95
    assert e.vram_sequential_gb < 2 * main
    assert any("サンプラーごとに 2 組を順に使う" in r for r in e.reasons)


def test_wan22_14b_i2v_on_24_and_16_gb(db):
    """24 GB では部品を順に載せ替えれば GPU に載る(0.1.0 は 35.5 GB で GPU+RAM だった)。載せ替えは 91% で
    余裕が無い(marginal)ので、メインメモリに余裕があれば逃がして good、無ければ GPU~ の marginal(#108)。"""
    m = db.get("video_wan2_2_14B_i2v")
    e24 = estimate(m, pc(24), measurements=db.measurements)
    assert 19 < e24.vram_sequential_gb < 23
    assert (e24.fit, e24.run_mode) == ("good", "offload")
    tight = estimate(m, pc(24, 32), measurements=db.measurements)  # 逃がすとメモリ 38 GB > 32 GB
    assert (tight.fit, tight.run_mode) == ("marginal", "gpu")
    e16 = estimate(m, pc(16, 64), measurements=db.measurements)
    assert e16.run_mode == "offload" and e16.fit == "good"  # 0.1.0 は too_tight(逃がしても 18.1 GB)


def test_graph_and_name_rule_agree_for_wan22(db):
    wf = json.loads((FIXTURES / "video_wan2_2_14B_i2v.json").read_text(encoding="utf-8"))
    by_name = ModelEntry.from_dict(db.get("video_wan2_2_14B_i2v").to_dict())
    by_name.sampler_stages = None
    by_graph = ModelEntry.from_dict(by_name.to_dict())
    set_sampler_stages(by_graph, sampler_stages(wf))
    a = estimate(by_name, pc(24))
    b = estimate(by_graph, pc(24))
    assert a.vram_sequential_gb == b.vram_sequential_gb and a.vram_gb == b.vram_gb


def test_single_model_workflow_is_not_split(db):
    m = db.get("video_wan2_2_14B_s2v")  # 本体 1 つ(LoRA に high_noise の名前があっても分けない)
    assert expert_sets(m) is None
    e = estimate(m, pc(24))
    assert not any("サンプラーごと" in r for r in e.reasons)


def test_other_pipeline_in_the_graph_is_its_own_stage():
    """1 つのワークフローで Z-Image の後に Wan 2.2 を動かすもの: 3 組(Z-Image は Wan と同時に載らない)。"""
    f = [
        ModelFile(name="z.safetensors", directory="diffusion_models", size_bytes=12 * 1024**3),
        ModelFile(name="w_high_noise.safetensors", directory="diffusion_models", size_bytes=14 * 1024**3),
        ModelFile(name="w_low_noise.safetensors", directory="diffusion_models", size_bytes=14 * 1024**3),
    ]
    m = ModelEntry(id="x", name="x", modality="video", files=f, files_complete=True)
    m.sampler_stages = [["z.safetensors"], ["w_high_noise.safetensors"], ["w_low_noise.safetensors"]]
    e = estimate(m, pc(24))
    assert e.vram_sequential_gb < 14 + 8  # 一番大きい組(14 GB)+ 固定分・作業用
    m.sampler_stages = None  # 名前の決まりだけなら Z-Image は両方の組に入る(安全側)
    assert estimate(m, pc(24)).vram_sequential_gb > 14 + 12


# --- 2. 精度の選び方 -------------------------------------------------------------------------------


def _row(db, vram, key_id, **kw):
    from genmodelfit.groups import index_for

    key = index_for(db).key_of[key_id]
    sel = select(db, pc(vram), include_unknown_license=True, **kw)
    return next(f for f in sel.fits if f.group_key == key)


def test_z_image_turbo_picks_bf16_on_24_gb(db):
    row = _row(db, 24, "image_z_image_turbo")
    assert row.model.id == "image_z_image_turbo" and row.model.precision == "bf16"
    assert row.estimate.fit == "good" and row.run_mode_label == "GPU~"
    # int8(perfect)の点数を行の点数にする(逃がさずに載るものは 1 つの段階)
    int8 = next(v for v in row.variants if v["precision"] == "int8")
    assert int8["fit_level"] == "perfect"


def test_z_image_turbo_picks_int8_when_bf16_needs_offload(db):
    row = _row(db, 8, "image_z_image_turbo")
    assert row.model.precision == "int8"


def test_precision_choice_falls_back_to_level_without_clean_variant(db):
    """逃がさないと載らないときは、今までどおり段階 → 逃がさない載せ方 → 精度。"""
    rows = select(db, pc(4), include_unknown_license=True).fits
    for f in rows:
        if not runs_clean(f.estimate):
            others = [v for v in f.variants if v["workflow_id"] != f.model.id]
            assert all(fit_rank(v["fit_level"]) >= fit_rank(f.estimate.fit) for v in others), f.model.id


def test_rows_never_leave_the_clean_tier(db):
    """VRAM を増やしても、逃がさずに載る行は逃がさずに載ったまま、その精度は下がらない。
    段階は perfect → good になることがある(高い精度に替わったとき。docs/schema.md)。
    Score は下がることがある(#95: GPU+RAM → GPU~ に替わると使用率が上がる。docs/scoring.md「Score」)。"""
    from genmodelfit.groups import precision_rank

    for ram in (32, 96):
        prev: dict[str, tuple] = {}
        for v in (0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 80):
            rows = {
                f.group_key: f
                for f in select(db, pc(v, ram), content="all", include_unknown_license=True).fits
            }
            for k, f in rows.items():
                if k not in prev:
                    continue
                clean, rank, level = prev[k]
                if clean:
                    assert runs_clean(f.estimate), (v, ram, k)
                    assert precision_rank(f.model.precision) <= rank, (v, ram, k)
                else:
                    assert fit_rank(f.estimate.fit) <= level, (v, ram, k)
            prev = {
                k: (
                    runs_clean(f.estimate),
                    precision_rank(f.model.precision),
                    fit_rank(f.estimate.fit),
                )
                for k, f in rows.items()
            }


# --- 3. ライセンス不明は既定で隠す ------------------------------------------------------------------


def test_unknown_license_hidden_by_default(db):
    unknown = [m for m in db.models if m.content == "sfw" and license_unknown(m)]
    assert unknown  # 同梱の一覧にまだある(#28 の別の作業で減らす)
    sel = select(db, pc(24))
    assert not any(license_unknown(f.model) for f in sel.fits)
    assert sel.hidden["license_unknown"] >= 1
    assert sel.matched + sum(sel.hidden.values()) == len(db.models)
    shown = select(db, pc(24), include_unknown_license=True)
    assert any(license_unknown(f.model) for f in shown.fits)
    assert "license_unknown" not in shown.hidden


def test_unknown_license_api_and_cli(db, capsys):
    mid = next(m.id for m in db.models if m.content == "sfw" and license_unknown(m))
    sys = pc(24)
    assert not any(license_unknown(f.model) for f in gmf.recommend(system_info=sys, db=db, limit=None))
    assert any(
        license_unknown(f.model)
        for f in gmf.recommend(system_info=sys, db=db, limit=None, include_unknown_license=True)
    )
    hits = gmf.search(mid, system_info=sys, db=db, all_workflows=True)
    assert hits == []
    assert gmf.search(mid, system_info=sys, db=db, all_workflows=True, include_unknown_license=True)
    hw = ["--vram", "24", "--ram", "96", "--no-cache", "--json"]
    assert cli.main(["fit", *hw]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["filters"]["include_unknown_license"] is False and out["hidden"]["license_unknown"] >= 1
    assert all(None not in m["licenses"] for m in out["models"])
    assert cli.main(["fit", *hw, "--include-unknown-license"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["filters"]["include_unknown_license"] is True and "license_unknown" not in out["hidden"]
    assert cli.main(["info", mid, *hw]) == 0  # ID を正確に指定すれば出す
    assert json.loads(capsys.readouterr().out)["model"]["id"] == mid


def test_text_mode_says_how_to_show_unknown_licenses(tmp_path):
    out = run_cli(["fit", "--vram", "24", "--ram", "96", "--no-cache"], tmp_path / "c").stdout.decode("utf-8")
    assert "ライセンス不明" in out and "--any-license" in out


# --- 4. 判定できないものは「?」 --------------------------------------------------------------------


def _tts_gguf_unknown() -> ModelEntry:
    return ModelEntry(
        id="hf:x/unknown",
        name="x",
        modality="image",
        source="huggingface",
        files=[ModelFile(name="m-Q8_0.gguf", role="main", size_bytes=10**9, license="mit")],
        files_complete=False,
        license="mit",
    )


def test_unknown_size_is_unknown_not_too_tight(db):
    m = _tts_gguf_unknown()
    e = estimate(m, pc(24))
    assert e.unknown_size and e.fit == FIT_UNKNOWN and e.run_mode == "unknown"  # 0.2.0 から none と分けた
    from genmodelfit.recommend import judge

    f = judge(m, pc(24), db)
    d = f.to_dict()
    assert d["fit_level"] == "unknown" and d["fit_label"] == "?" and d["run_mode_label"] == "?"
    con = make_console(width=100)
    with con.capture() as cap:
        con.print(build_table([f], system=pc(24)))
    text = cap.get()
    assert "?" in text and "Too tight" not in text


def test_unknown_size_listed_last_in_fit_and_hidden_in_recommend(db, monkeypatch):
    from genmodelfit.db import Database

    m = _tts_gguf_unknown()
    small = Database(models=[*db.models[:40], m], measurements=db.measurements, sources=db.sources)
    sel = select(small, pc(24), include_unknown_license=True, group=False)
    # 動く → 判定できない → 動かない(表・--json・ライブラリで同じ。#95)
    i = [f.model.id for f in sel.fits].index(m.id)
    assert all(f.estimate.fit in ("perfect", "good", "marginal") for f in sel.fits[:i])
    assert all(f.estimate.fit == "too_tight" for f in sel.fits[i + 1 :])
    assert all(list_key(a) <= list_key(b) for a, b in zip(sel.fits, sel.fits[1:], strict=False))
    assert m.id not in [
        f.model.id for f in recommend(small, pc(24), limit=None, include_unknown_license=True)
    ]
    narrowed = select(small, pc(24), include_unknown_license=True, group=False, min_fit="marginal")
    assert narrowed.hidden.get("size_unknown") == 1


@pytest.mark.parametrize(
    "mid",
    [
        "hf:pnnbao-ump/VieNeu-TTS-v3-Turbo",
        "hf:Serveurperso/Qwen3-TTS-GGUF",
        "hf:drbaph/Higgs-Audio-v3-Studio",
        "hf:Jmica/Drama-3-GGUF",
    ],
)
def test_tts_gguf_entries_are_judged(db, mid):
    """読み上げの GGUF は llama.cpp 系の一式で、テキストエンコーダ・VAE を持たない。本体の GGUF だけでも
    大きさが分かるので判定する(0.1.0 は「一式が欠けている」として判定できず Too tight と出ていた)。"""
    m = db.get(mid)
    if m is None:
        pytest.skip("同梱の一覧に無い")
    assert m.files_complete is True
    e = estimate(m, pc(24), measurements=db.measurements)
    assert not e.unknown_size and e.fit != FIT_UNKNOWN


def test_image_gguf_without_encoder_is_still_incomplete():
    e = ModelEntry(
        id="hf:x/flux-gguf",
        name="x",
        modality="image",
        source="huggingface",
        files=[ModelFile(name="flux-Q4_K_S.gguf", role="main", size_bytes=7 * 10**9)],
    )
    set_completeness(e)
    assert e.files_complete is False
    e.modality = "tts"
    set_completeness(e)
    assert e.files_complete is True


# --- 5. 利用者のテンプレートの並び ------------------------------------------------------------------


def test_community_comes_after_official_only_within_the_same_level(db):
    from genmodelfit.groups import is_community

    for v in (8, 24):
        fits = select(db, pc(v), content="all", include_unknown_license=True).fits
        keys = [list_key(f) for f in fits]
        assert keys == sorted(keys)
        for a, b in itertools.pairwise(fits):
            same_mode = mode_label(a.estimate, a.system) == mode_label(b.estimate, b.system)
            if a.auto == b.auto and a.estimate.fit == b.estimate.fit and same_mode and is_community(a.model):
                assert is_community(b.model), (a.model.id, b.model.id)
        # 段階をまたいで最後に回さない: 利用者のテンプレートの good は、公式の marginal より前
        comm = [i for i, f in enumerate(fits) if is_community(f.model) and f.estimate.fit == "good"]
        offi = [i for i, f in enumerate(fits) if not is_community(f.model) and f.estimate.fit == "marginal"]
        for i in comm:
            assert all(i < j for j in offi if fits[j].auto == fits[i].auto), v


# --- 6. ファイルの見出しから精度を読む --------------------------------------------------------------


def _safetensors(tensors: dict) -> bytes:
    head = json.dumps({"__metadata__": {"format": "pt"}, **tensors}).encode()
    return struct.pack("<Q", len(head)) + head + b"\0" * 64


def test_parse_safetensors_header():
    raw = _safetensors(
        {
            "a.weight": {"dtype": "F8_E4M3", "shape": [1024, 1024], "data_offsets": [0, 1]},
            "a.scale": {"dtype": "F32", "shape": [1], "data_offsets": [1, 5]},
            "norm": {"dtype": "BF16", "shape": [1024], "data_offsets": [5, 7]},
        }
    )
    n = struct.unpack("<Q", raw[:8])[0]
    info = parse_safetensors_header(raw[8 : 8 + n])
    assert info.precision == "fp8" and info.params == 1024 * 1024 + 1 + 1024
    with pytest.raises(HeaderError):
        parse_safetensors_header(b"not json")


def _gguf(ftype: int | None, tensors: list[tuple[str, list[int], int]]) -> bytes:
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    kv = [(s("general.architecture"), 8, s("flux"))]
    if ftype is not None:
        kv.append((s("general.file_type"), 4, struct.pack("<I", ftype)))
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", len(tensors)) + struct.pack("<Q", len(kv))
    for k, t, v in kv:
        out += k + struct.pack("<I", t) + v
    for name, dims, ttype in tensors:
        out += s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<I", ttype) + struct.pack("<Q", 0)
    return out


def test_parse_gguf_header():
    raw = _gguf(15, [("w", [4096, 4096], 12), ("b", [4096], 0)])
    info = parse_gguf_header(raw)
    assert info.precision == "gguf-q4_k_m" and info.params == 4096 * 4096 + 4096
    no_ftype = parse_gguf_header(_gguf(None, [("w", [4096, 4096], 8), ("b", [4096], 0)]))
    assert no_ftype.precision == "gguf-q8_0"


class FakeRange:
    """Range 要求の代わり。読んだ範囲を記録する(重み本体まで読まないことを確かめる)。"""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, url: str, start: int, end: int) -> bytes:
        self.calls.append((url, start, end))
        return self.files[url][start : end + 1]


def test_read_header_reads_only_the_header():
    st = _safetensors({"w": {"dtype": "BF16", "shape": [10, 10], "data_offsets": [0, 200]}})
    gg = _gguf(7, [("w", [8, 8], 8)]) + b"\0" * (3 * 1024 * 1024)
    fake = FakeRange(
        {
            "https://huggingface.co/a/b/resolve/main/m.safetensors": st,
            "https://huggingface.co/a/b/resolve/main/m.gguf": gg,
        }
    )
    assert read_header("https://huggingface.co/a/b/resolve/main/m.safetensors", fake).precision == "bf16"
    n = struct.unpack("<Q", st[:8])[0]
    assert fake.calls == [(fake.calls[0][0], 0, 7), (fake.calls[0][0], 8, 8 + n - 1)]
    assert read_header("https://huggingface.co/a/b/resolve/main/m.gguf", fake).precision == "gguf-q8_0"
    assert max(end for _u, _s, end in fake.calls) < 1024 * 1024  # GGUF も先頭 1 MiB だけ


def test_inspect_files_uses_the_cache_offline(tmp_path):
    url = "https://huggingface.co/a/b/resolve/main/model.safetensors"
    st = _safetensors({"w": {"dtype": "F8_E4M3", "shape": [100, 100], "data_offsets": [0, 1]}})
    e = ModelEntry(
        id="hf:a/b",
        name="b",
        modality="image",
        source="huggingface",
        files=[ModelFile(name="model.safetensors", url=url, role="main", size_bytes=10**9, sha256="ab")],
    )
    assert model_precision(e) is None
    cache = HeaderCache(tmp_path / "file_headers.json")
    fake = FakeRange({url: st})
    stats = inspect_files([e], fake, cache, delay=0)
    cache.save()
    assert stats.fetched == 1 and e.files[0].header_precision == "fp8" and model_precision(e) == "fp8"
    e2 = ModelEntry.from_dict(e.to_dict())
    e2.files[0].header_precision = None
    again = inspect_files([e2], None, HeaderCache(tmp_path / "file_headers.json"), offline=True)
    assert again.from_cache == 1 and again.fetched == 0 and e2.files[0].header_precision == "fp8"
    e2.files[0].sha256 = "changed"  # 中身が変わったら使わない
    e2.files[0].header_precision = None
    miss = inspect_files([e2], None, HeaderCache(tmp_path / "file_headers.json"), offline=True)
    assert miss.skipped_offline == 1 and e2.files[0].header_precision is None


@pytest.mark.parametrize(
    ("name", "header", "want"),
    [
        ("model.safetensors", "bf16", "bf16"),
        ("x_fp16.safetensors", "bf16", "bf16"),  # 名前より中身
        ("x_nvfp4.safetensors", None, "nvfp4"),
        ("x_w4a8.safetensors", "int8", "w4a8"),  # 見出しでは分からない形式は名前
        ("x-Q4_K_S.gguf", "gguf-q4_k", "gguf-q4_k_s"),
        ("x.gguf", "gguf-q8_0", "gguf-q8_0"),
        ("x.safetensors", None, None),
    ],
)
def test_file_precision(name, header, want):
    assert file_precision(ModelFile(name=name, header_precision=header)) == want


def test_bundled_precision_unknown_went_down(db):
    """同梱の一覧の精度の不明は、ファイルの見出しを読んで 0.1.0 の 97 件(無検閲を含む)から減らした。"""
    assert sum(1 for m in db.models if m.precision is None) < 97
    assert (
        db.sources.get("file_headers", {}).get("fetched", 0)
        + db.sources.get("file_headers", {}).get("from_cache", 0)
        > 0
    )


# --- 7. OS の表示 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "release", "version", "want"),
    [
        ("Windows", "10", "10.0.22631", "Windows 11"),  # Python 3.10 / 3.11 の Windows 11
        ("Windows", "11", "10.0.26100", "Windows 11"),
        ("Windows", "10", "10.0.19045", "Windows 10"),
        ("Windows", "10", "", "Windows 10"),
        ("Linux", "6.8.0", "#1 SMP", "Linux 6.8.0"),
    ],
)
def test_os_name(system, release, version, want):
    assert os_name(system, release, version) == want


# --- 8. fit の一覧(利用者の指定: 1 つの表・Type が左端・既定は動くものだけ) --------------------------

HW24 = ["--vram", "24", "--ram", "96", "--no-cache", "--json"]
LEVEL_ORDER = ["perfect", "good", "marginal", "unknown", "too_tight"]
TYPES = ["image", "video", "music", "sfx", "tts", "3d"]


def _fit_json(capsys, *args: str) -> dict:
    assert cli.main(["fit", *HW24, *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_fit_default_is_runnable_only_and_counts_hidden(capsys):
    d = _fit_json(capsys)
    assert d["filters"]["fit"] == "runnable" and d["filters"]["sort"] == "type"
    assert d["models"] and {m["fit_level"] for m in d["models"]} <= {"perfect", "good", "marginal"}
    assert all(m["content"] == "sfw" and None not in m["licenses"] for m in d["models"])
    assert {"uncensored", "tools", "license_unknown"} <= set(d["hidden"])
    every = _fit_json(capsys, "--all")
    assert every["filters"]["fit"] == "all" and every["filters"]["include_tools"] is True
    assert {"tools", "license_unknown", "too_tight", "size_unknown"}.isdisjoint(every["hidden"])
    flows = _fit_json(capsys, "--all", "--all-workflows", "--content", "all")  # 行にまとめない
    assert {"too_tight", "unknown"} <= {m["fit_level"] for m in flows["models"]}
    assert every["matched"] > d["matched"]


def test_fit_order_is_type_then_level_then_score(capsys):
    d = _fit_json(capsys, "--all")
    keys = [(TYPES.index(m["modality"]), LEVEL_ORDER.index(m["fit_level"])) for m in d["models"]]
    assert keys == sorted(keys)
    for a, b in zip(d["models"], d["models"][1:], strict=False):
        same = a["modality"] == b["modality"] and a["fit_level"] == b["fit_level"]
        if same and a["community"] == b["community"]:  # 同じ段階・公式の中は 人気 → Score(#95)
            assert (-a["popularity"], -a["score"]) <= (-b["popularity"], -b["score"])
    # 自動のおすすめを先にしない(自動のおすすめかは info で分かる)
    autos = [m["auto"] for m in d["models"] if m["modality"] == "image" and m["fit_level"] == "good"]
    assert autos != sorted(autos, reverse=True) or len(set(autos)) == 1


def test_fit_sort_options(capsys):
    score = _fit_json(capsys, "--sort", "score")["models"]
    # 動く → 判定できない → 動かない、段階(逃がさずに載る / Marginal)→ Score → 人気(#95)
    runs = {"unknown": 1, "too_tight": 2}
    keys = [
        (
            runs.get(m["fit_level"], 0),
            0 if m["fit_level"] in ("perfect", "good") and m["run_mode"] == "gpu" else 1,
            -m["score"],
            -m["popularity"],
        )
        for m in score
    ]
    assert keys == sorted(keys) and len({m["modality"] for m in score[:10]}) > 1  # 種類をまたいだ順位
    vram = _fit_json(capsys, "--sort", "vram")["models"]
    needs = [m["memory_required_gb"] for m in vram]
    assert needs == sorted(needs)
    names = [m["model_name"].lower() for m in _fit_json(capsys, "--sort", "name")["models"]]
    assert names == sorted(names)


def test_fit_level_filter(capsys):
    good = _fit_json(capsys, "--fit", "good")
    assert good["models"] and {m["fit_level"] for m in good["models"]} == {"good"}
    assert good["hidden"]["other_fit"] > 0
    perfect = _fit_json(capsys, "--perfect")
    assert {m["fit_level"] for m in perfect["models"]} == {"perfect"}
    with pytest.raises(SystemExit) as ex:
        cli.main(["fit", *HW24, "--fit", "good", "--min-fit", "good"])
    assert ex.value.code == 2  # 使い方の誤り
    capsys.readouterr()
    old = _fit_json(capsys, "--min-fit", "good")  # --min-fit も使える(その段階以上)
    assert {m["fit_level"] for m in old["models"]} == {"perfect", "good"} and old["filters"]["fit"] is None


def test_fit_table_columns(tmp_path):
    out = run_cli(["fit", "--vram", "24", "--ram", "96", "--no-cache"], tmp_path / "c", COLUMNS="100")
    lines = out.stdout.decode("utf-8").splitlines()
    header = next(x for x in lines if x.startswith("Type"))
    assert header.split()[:5] == ["Type", "Status", "Score", "Model", "Use"]
    assert not [x for x in lines if x.startswith("== ")]  # 1 つの表
    assert all(len(x) <= 100 for x in lines)


def test_fit_list_api_matches_cli(db, capsys):
    d = _fit_json(capsys)
    api = gmf.fit_list(system_info=pc(24), db=db)
    assert [f.model.id for f in api] == [m["id"] for m in d["models"]]
    every = gmf.fit_list(system_info=pc(24), db=db, show_all=True)
    assert len(every) > len(api)
    with pytest.raises(ValueError):
        gmf.fit_list(system_info=pc(24), db=db, sort="speed")
    with pytest.raises(ValueError):
        gmf.fit_list(system_info=pc(24), db=db, min_fit="good")  # fit の既定 runnable と両方
    assert gmf.fit_list(system_info=pc(24), db=db, fit=None, min_fit="good")


# --- 9. レビューの指摘(0.1.1 の Checker) -----------------------------------------------------------


def test_recommend_keeps_z_image_turbo_on_top_after_precision_upgrade(db):
    """代表を bf16(good・GPU~)に替えても、逃がさずに載るものは perfect と 1 つの段階として点数で並べる
    (int8 の perfect だったときの順位から下げない)。"""
    rows = recommend(db, pc(24), modality="image", limit=6)
    assert rows[0].model.id == "image_z_image_turbo" and rows[0].estimate.fit == "good"
    clean = [f for f in rows if runs_clean(f.estimate) and f.auto]
    # 逃がさずに載るものは 1 つの段階として並べる(段階の中は公式 → 人気 → Score。recommend.list_key)
    assert all(f.estimate.fit in ("perfect", "good") for f in clean)
    assert [list_key(f) for f in rows] == sorted(list_key(f) for f in rows)


def test_level_cap_in_json_and_legend(capsys):
    d = _fit_json(capsys, "-m", "image")
    caps = {m["run_mode_label"]: m["level_cap"] for m in d["models"]}
    assert caps.get("GPU") is None and caps.get("GPU~") == "sequential"
    every = _fit_json(capsys, "--all", "-m", "video")
    assert {m["level_cap"] for m in every["models"] if m["run_mode_label"] == "GPU+RAM"} == {"offload"}
    assert cli.main(["fit", "--vram", "24", "--ram", "96", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "GPU~ と GPU+RAM は Good まで、CPU は Marginal まで" in out
    assert all(len(x) <= 100 for x in out.splitlines())
    assert cli.main(["fit", "--vram", "24", "--ram", "96", "--no-cache", "--lang", "en"]) == 0
    assert "GPU~ and GPU+RAM at most Good; CPU at most Marginal" in capsys.readouterr().out


def test_fit_and_min_fit_together_is_a_usage_error(tmp_path):
    for extra in (["--fit", "good"], ["--perfect"]):
        r = run_cli(["fit", "--vram", "8", "--no-cache", "--min-fit", "good", *extra], tmp_path / "c")
        assert r.returncode == 2 and b"--min-fit" in r.stderr


def test_other_fit_label_is_humanized(tmp_path):
    out = run_cli(["fit", "--vram", "24", "--ram", "96", "--no-cache", "--fit", "good"], tmp_path / "c")
    text = re.sub(r"\s+", "", out.stdout.decode("utf-8"))
    assert "ほかの段階(--fit)" in text and "other_fit" not in text
    out = run_cli(
        ["fit", "--vram", "24", "--ram", "96", "--no-cache", "--fit", "good", "--lang", "en"], tmp_path / "c"
    )
    text = re.sub(r"\s+", " ", out.stdout.decode("utf-8"))
    assert "other levels (--fit)" in text and "other_fit" not in text


@pytest.mark.parametrize("ram", ["96", "64"])
def test_info_and_table_use_the_same_percentage(db, capsys, ram):
    # 96 GB: VRAM のほうがきつい。64 GB: 逃がす載せ方でメインメモリのほうがきつい(#122)
    hw = ["--vram", "24", "--ram", ram, "--no-cache"]
    assert cli.main(["info", "video_wan2_2_14B_i2v", *hw]) == 0
    info = capsys.readouterr().out
    assert cli.main(["fit", *hw, "--json", "--all-workflows", "-m", "video"]) == 0
    d = json.loads(capsys.readouterr().out)
    row = next(m for m in d["models"] if m["id"] == "video_wan2_2_14B_i2v")
    assert row["run_mode"] == "offload"
    assert f"({round(row['utilization_pct'])}%)" in " ".join(info.split())
    if ram == "64":
        assert re.search(r"36\.1 / 64\.0 GB\s?[((]56%\)", " ".join(info.split()))


@pytest.mark.parametrize(("vram", "ram"), [(8, 32), (12, 32), (16, 32), (24, 64)])
def test_offload_note_percentage_is_the_table_percentage(db, vram, ram):
    """逃がす行の注意の % は、表の Mem%(段階を決めた、VRAM とメインメモリのきついほう)と同じ(#122)。"""
    from genmodelfit.recommend import usage_ratio

    system = pc(vram, ram)
    seen = 0
    for m in db.models:
        e = estimate(m, system)
        if e.run_mode != "offload":
            continue
        seen += 1
        pct = round(round(100 * usage_ratio(e, system), 1))
        assert any(f"{pct}%)" in r and "good" in r for r in e.reasons), (m.id, pct, e.reasons)
    assert seen


@pytest.mark.parametrize(
    "tensors",
    [
        {"w": {"dtype": "F16", "shape": [-1, 4]}},
        {"w": {"dtype": "F16", "shape": ["a"]}},
        {"w": {"dtype": "F16", "shape": 7}},
        {"w": {"dtype": 3, "shape": [1]}},
        {"w": {"dtype": "F16", "shape": [1.5]}},
    ],
)
def test_malformed_safetensors_shapes_are_header_errors(tensors):
    with pytest.raises(HeaderError):
        parse_safetensors_header(json.dumps(tensors).encode())


def test_malformed_gguf_is_a_header_error():
    raw = _gguf(15, [("w", [4, 4], 12)])
    with pytest.raises(HeaderError):
        parse_gguf_header(b"XXXX" + raw[4:])
    bad_kv = raw[:24] + struct.pack("<Q", 3) + b"abc" + struct.pack("<I", 99)  # 知らない値の型
    with pytest.raises(HeaderError):
        parse_gguf_header(bad_kv + b"\0" * 64)
