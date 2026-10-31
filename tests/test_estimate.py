import json

import pytest

from genmodelfit import cli
from genmodelfit.calibrate import build_rows, calibrate
from genmodelfit.db import Database, bundled_text, load
from genmodelfit.estimate import (
    GIB,
    UNKNOWN_SIZE_REASON,
    Coefficients,
    _role_of,
    components,
    estimate,
    is_censored,
    limits_for,
    load_coefficients,
    norm_precision,
)
from genmodelfit.models import Gpu, Measurement, ModelEntry, ModelFile, Settings, SystemInfo, Template
from genmodelfit.recommend import evaluate, recommend


def pc(vram: float, ram: float = 32.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


def f(name: str, gb: float, directory: str | None = None, role: str | None = None) -> ModelFile:
    return ModelFile(name=name, size_bytes=int(gb * GIB), directory=directory, role=role)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


# --- 部品の分け方 -------------------------------------------------------------------------------


def test_split_sdxl_checkpoint(db):
    p = components(db.get("image_sdxl_simple"))
    assert p.denoise == pytest.approx(4.78, abs=0.02)  # UNet(GiB)
    assert p.encode == pytest.approx(1.53, abs=0.02)  # CLIP-L + bigG
    assert p.decode == pytest.approx(0.16, abs=0.01)
    assert p.total == pytest.approx(6938078334 / GIB, abs=0.001)
    assert any("SDXL" in n for n in p.notes)


def test_split_by_directory_and_generic_checkpoint():
    e = ModelEntry(
        id="x",
        name="x",
        modality="image",
        files=[
            f("dit.safetensors", 6, "diffusion_models"),
            f("te.safetensors", 4, "text_encoders"),
            f("ae", 0.3, "vae"),
        ],
    )
    p = components(e)
    assert (p.denoise, p.encode, p.decode) == pytest.approx((6, 4, 0.3))
    unknown = ModelEntry(
        id="y", name="y", modality="image", files=[f("mystery.safetensors", 10, "checkpoints")]
    )
    p = components(unknown)
    assert (p.denoise, p.encode, p.decode) == pytest.approx((7.5, 2.0, 0.5))
    assert any("一般的な割合" in n for n in p.notes)


def test_split_hf_names_without_directory():
    e = ModelEntry(
        id="hf:a/b",
        name="b",
        modality="image",
        source="huggingface",
        files=[
            f("transformer/diffusion_pytorch_model-00001-of-00002.safetensors", 5),
            f("transformer/diffusion_pytorch_model-00002-of-00002.safetensors", 3),
            f("text_encoder/model.safetensors", 2),
            f("vae/diffusion_pytorch_model.safetensors", 0.2),
        ],
    )
    p = components(e)
    assert (p.denoise, p.encode, p.decode) == pytest.approx((8, 2, 0.2))


def test_no_files_falls_back_to_template_size():
    e = ModelEntry(id="t", name="t", modality="image", template=Template(name="t", size_bytes=8 * GIB))
    p = components(e)
    assert p.total == pytest.approx(8.0) and p.denoise == pytest.approx(6.0)
    est = estimate(e, pc(16), coefficients=Coefficients())
    assert est.basis == "estimated" and est.fit in ("perfect", "good")


def test_precision_override_scales_main_only(db):
    e = db.get("image_z_image_turbo")  # 本体 bf16 12.3 GB
    base = estimate(e, pc(16), Settings(width=1024, height=1024), coefficients=Coefficients())
    fp8 = estimate(e, pc(16), Settings(width=1024, height=1024, precision="fp8"), coefficients=Coefficients())
    assert base.vram_gb - fp8.vram_gb == pytest.approx(12309866400 / GIB / 2, abs=0.02)
    assert any("bf16 → fp8" in r for r in fp8.reasons)
    # 精度を変えたら実測(bf16)は使わない
    fp8m = estimate(e, pc(16), Settings(width=768, height=768, precision="fp8"), db.measurements)
    assert fp8m.basis == "estimated"


# --- 段階 ----------------------------------------------------------------------------------------


def big_model() -> ModelEntry:
    return ModelEntry(
        id="big",
        name="big",
        modality="image",
        files=[f("dit.safetensors", 12, "diffusion_models"), f("te.safetensors", 8, "text_encoders")],
    )


def test_levels_full_sequential_offload():
    c = Coefficients()
    e = big_model()
    s = Settings(width=1024, height=1024)
    assert estimate(e, pc(48), s, coefficients=c).fit == "perfect"  # 全部で約 21 GB = 44%
    assert estimate(e, pc(32), s, coefficients=c).fit == "good"  # 66%(60% を超えると good)
    seq = estimate(e, pc(16), s, coefficients=c)  # 全部(21 GB)は載らないが、本体の段(13 GB)は載る
    assert seq.fit == "good" and seq.run_mode == "gpu" and "順に載せ替え" in seq.reasons[0]
    off = estimate(e, pc(8), s, coefficients=c)  # 本体も載らない → 一部を逃がす
    assert off.fit == "good" and off.run_mode == "offload"  # 逃がすのは good まで(#108)
    assert "段階は good まで" in off.reasons[0]
    assert off.vram_offload_gb <= 8 < off.vram_sequential_gb <= off.vram_gb
    assert estimate(e, pc(4), s, coefficients=c).fit == "too_tight"


def test_ram_depends_on_run_mode():
    c = Coefficients()
    e = big_model()  # 本体 12 + エンコーダ 8 GiB
    assert estimate(e, pc(48, ram=64), coefficients=c).fit == "perfect"
    # 全部 GPU に載るなら、メインメモリは固定分 + いちばん大きい部品(3 + 12 = 15)で足りる
    gpu16 = estimate(e, pc(48, ram=16), coefficients=c)
    assert gpu16.fit == "perfect" and gpu16.ram_gb == pytest.approx(15.0)
    # 少し足りない(1.5 倍以内)→ marginal(仮想メモリで遅い)
    soft = estimate(e, pc(48, ram=12), coefficients=c)
    assert soft.fit == "marginal" and soft.run_mode == "gpu" and any("仮想メモリ" in r for r in soft.reasons)
    # 1.5 倍を超える → too_tight
    hard = estimate(e, pc(48, ram=8), coefficients=c)
    assert hard.fit == "too_tight" and "メモリ不足" in hard.reasons[0]
    # 逃がすときは全部品(3 + 20 = 23)。16 GB では足りないが 1.5 倍以内なので marginal のまま
    off = estimate(e, pc(8, ram=16), coefficients=c)
    assert off.fit == "marginal" and off.run_mode == "offload" and off.ram_gb == pytest.approx(23.0)
    assert estimate(e, pc(8, ram=12), coefficients=c).fit == "too_tight"


# --- 実測の扱い ----------------------------------------------------------------------------------


def test_measured_on_16gb_falls_through_on_smaller_gpu(db):
    e = db.get("image_z_image_turbo_int8")  # 16 GB で 13.2 GB(動的 VRAM)。アプリでは 6 GB から動く
    at = Settings(width=768, height=768)  # 実測の設定(#95 から、指定しなければ既定の 1024x1024)
    on8 = estimate(e, pc(8), at, db.measurements)
    assert on8.basis == "measured" and on8.vram_gb == pytest.approx(13.2)
    # 順に載せ替えて 7.6 / 8 GB = 96%: 余裕が無い(marginal)。逃がせば余裕があるので、逃がして good(#108。
    # 逃がすのは全部載せ・順に載せ替えより段階が上がるときだけ。docs/scoring.md「逃がす動かし方を選ぶとき」)
    assert on8.fit == "good" and on8.run_mode == "offload" and on8.vram_sequential_gb <= 8
    assert estimate(e, pc(12), at, db.measurements).fit == "good"  # 64%。載せ替えは good まで
    on6 = estimate(e, pc(6), at, db.measurements)
    assert (on6.fit, on6.run_mode) == ("good", "offload")  # 逃がして good(#108)
    # 指定しなければ既定の設定(1024x1024)で判定し、実測は値をずらすのに使うだけ(#95)
    d = estimate(e, pc(8), None, db.measurements)
    assert d.basis == "estimated" and d.basis_detail == "calibrated" and d.settings["width"] == 1024


def test_censored_measurement_is_a_lower_bound(db):
    m = next(x for x in db.measurements if x.model_id == "image_z_image_turbo")
    assert is_censored(m)  # 16 GB の GPU で 15.0 GB = 上限に張り付き
    at = Settings(width=768, height=768)  # 実測の設定
    e = estimate(db.get("image_z_image_turbo"), pc(16), at, db.measurements)
    assert e.basis == "measured" and e.vram_gb > 15.0
    assert any("張り付いた" in r for r in e.reasons)
    ok = Measurement(model_id="x", gpu_name="g", gpu_vram_gb=16, peak_vram_gb=13.2)
    assert not is_censored(ok)


def test_measurement_corrects_other_settings(db):
    e = db.get("image_sdxl_simple")
    other = estimate(e, pc(16), Settings(width=1024, height=1024), db.measurements)
    assert other.basis == "estimated" and any("実測" in r and "補正" in r for r in other.reasons)
    # 832px の実測 8.0 GB より小さくならない。#100 から全部載せの作業用メモリは一番重い段
    # (SDXL は 512px から 2000px ほどまで VAE のタイルの 1.06 GB が一番重い)なので同じ値
    at = estimate(e, pc(16), Settings(width=832, height=832), db.measurements)
    assert other.vram_gb >= at.vram_gb == 8.0
    assert other.vram_sequential_gb >= at.vram_sequential_gb


# --- 設定と単調さ --------------------------------------------------------------------------------


@pytest.mark.parametrize("coef", [Coefficients(), load_coefficients()])
def test_settings_scaling_is_monotonic(db, coef):
    img, vid, aud = (
        db.get("image_sdxl_simple"),
        db.get("text_to_video_wan"),
        db.get("audio_ace_step_1_5_checkpoint"),
    )

    def v(e, **kw):
        return estimate(e, pc(16), Settings(**kw), db.measurements, coef).vram_gb

    def seq(e, **kw):
        return estimate(e, pc(16), Settings(**kw), db.measurements, coef).vram_sequential_gb

    # 全部載せは増えるか同じ(#100: 一番重い段が VRAM によらないタイルの VAE なら同じ)。生成の段が効く
    # 順に載せ替えの量は増える
    # 作者の SDXL の実測のずらし(−1.0 GB)は段ごとに 0 で切るので、生成の段の作業用メモリが 1 GB を超える
    # 2048px より上で初めて増える
    sides = [v(img, width=px, height=px) for px in (512, 768, 1024, 1536, 4096)]
    assert sides == sorted(sides) and sides[0] < sides[-1]
    sides_seq = [seq(img, width=px, height=px) for px in (512, 768, 1024, 1536, 4096)]
    assert sides_seq == sorted(sides_seq) and sides_seq[0] < sides_seq[-1]
    assert v(img, width=1024, height=1024, batch=1) <= v(img, width=1024, height=1024, batch=16)
    assert seq(img, width=1024, height=1024, batch=1) < seq(img, width=1024, height=1024, batch=16)
    frames = [v(vid, width=512, height=512, frames=n) for n in (17, 49, 81, 121)]
    assert frames == sorted(frames) and frames[0] < frames[-1]
    # 音声: タイルにしない Oobleck の V が曲の長さで増える(#148)
    secs = [v(aud, seconds=t) for t in (10, 30, 60)]
    assert secs == sorted(secs) and secs[0] < secs[-1]
    secs_seq = [seq(aud, seconds=t) for t in (10, 30, 60)]
    assert secs_seq == sorted(secs_seq)
    # 16 GB に入らない長さは、ComfyUI がタイルに分ける見積もり(段階は marginal まで)。表の量はタイルの値
    long = estimate(aud, pc(16), Settings(seconds=600), db.measurements, coef)
    assert long.fit == "marginal" and long.vram_gb <= secs[-1]


def gpu_good(e) -> bool:
    """逃がさずに GPU で good 以上(限界に数える行。#108)。"""
    return e.fit in ("perfect", "good") and e.run_mode == "gpu"


def test_limits_are_the_largest_good_settings(db):
    e = db.get("text_to_video_wan")
    base = Settings(width=512, height=512, frames=49)  # 実測の設定(縦横比を正方形にそろえる)
    lim = limits_for(e, pc(10), base, db.measurements)  # 8 GB では標準の設定でも 97% で good にならない
    assert lim["level"] == "good" and lim["max_side_px"] and lim["max_frames"]
    at = Settings(width=lim["max_side_px"], height=lim["max_side_px"], frames=49)
    over = Settings(width=lim["max_side_px"] + 32, height=lim["max_side_px"] + 32, frames=49)
    assert estimate(e, pc(10), at, db.measurements).fit in ("perfect", "good")
    assert not gpu_good(estimate(e, pc(10), over, db.measurements))  # 限界は逃がさない行だけ(#108)
    # #100: 512px ではコマ数は調べる範囲の上限まで good(段ごとの式では Wan 2.1 の VAE はコマ数で増えない)。
    # 768px で、限界のすぐ上が good でないことを見る
    assert lim["at_cap"] == ["max_frames"]
    wide = Settings(width=768, height=768, frames=49)
    lim768 = limits_for(e, pc(10), wide, db.measurements)
    more = estimate(
        e, pc(10), Settings(width=768, height=768, frames=lim768["max_frames"] + 4), db.measurements
    )
    assert not gpu_good(more)
    # 大きな GPU ほど限界は上がる
    lim16 = limits_for(e, pc(24), base, db.measurements)
    assert lim16["max_side_px"] >= lim["max_side_px"] and lim16["max_frames"] >= lim["max_frames"]
    assert limits_for(db.get("3d_hunyuan3d_image_to_model"), pc(8), None, db.measurements) is None
    big = limits_for(db.get("image_sdxl_simple"), pc(24), None, db.measurements)
    assert big["at_cap"] == ["max_side_px"]


# --- 人が決めた目安(AI生成キットのカタログの最低 / 推奨 VRAM)との突き合わせ ------------------------
# 係数を合わせるのには使っていない(docs/accuracy.md)。最低の VRAM で too_tight にならないこと、
# 推奨の VRAM で good 以上になることを見る。

CURATED = {  # id: (最低, 推奨)
    "image_z_image_turbo_int8": (6, 8),
    "image_z_image_turbo": (12, 16),
    "image_sdxl_simple": (4, 8),
    "video_wan2_2_5B_ti2v": (8, 12),
    "text_to_video_wan": (6, 8),
    "audio_ace_step_1_5_checkpoint": (4, 8),
    "3d_hunyuan3d_image_to_model": (6, 8),
    "3d_hunyuan3d-v2.1": (8, 12),
}
# #100 で Wan 2.2 5B と Wan 2.1 1.3B の外れは直った(strict の xfail を外した)
CURATED_IDS = sorted(CURATED)


def app_settings(db, model_id):
    """カタログの目安の VRAM はアプリの設定(作者の実測の設定)でのもの。#95 から、指定しなければ既定の設定で
    判定するので、比べるときはアプリの設定を渡す。"""
    m = next((x for x in db.measurements if x.model_id == model_id), None)
    return m.settings if m is not None else None


@pytest.mark.parametrize("model_id", CURATED_IDS)
def test_curated_min_is_not_too_tight(db, model_id):
    vmin, _ = CURATED[model_id]
    s = app_settings(db, model_id)
    assert estimate(db.get(model_id), pc(vmin), s, db.measurements).fit != "too_tight"


@pytest.mark.parametrize("model_id", CURATED_IDS)
def test_curated_recommended_runs_on_gpu(db, model_id):
    """推奨の VRAM では GPU に載る(全部か順に載せ替えて 98% 以内)。推奨ぎりぎり(85% 超。Z-Image Turbo int8 は
    8 GB で 94%)なら、逃がしたほうが段階が上がるので逃がして good(#108)。"""
    _, vrec = CURATED[model_id]
    e = estimate(db.get(model_id), pc(vrec), app_settings(db, model_id), db.measurements)
    assert e.fit in ("perfect", "good") and e.run_mode in ("gpu", "offload")
    assert e.run_mode == "gpu" or e.vram_sequential_gb <= 0.98 * vrec


def test_curated_8gb_and_4gb(db):
    on8 = {m.id: estimate(m, pc(8), None, db.measurements).fit for m in db.models}
    for mid in (
        "image_sdxl_simple",
        "image_z_image_turbo_int8",
        "text_to_video_wan",
        "audio_ace_step_1_5_checkpoint",
        "3d_hunyuan3d_image_to_model",
    ):
        assert on8[mid] != "too_tight", mid
    assert estimate(db.get("image_z_image_turbo"), pc(16), None, db.measurements).fit != "too_tight"
    # 4 GB で bf16 の Z-Image(重み 20.7 GB)は、逃がしても生成の段に 6 GB ほど要るので too_tight
    assert estimate(db.get("image_z_image_turbo"), pc(4), None, db.measurements).fit == "too_tight"


# --- 合わせ直しの再現 ----------------------------------------------------------------------------


def test_calibration_is_reproducible():
    committed = json.loads(bundled_text("calibration.json"))
    again = calibrate(date=committed["date"])
    assert again["coefficients"] == committed["coefficients"]
    assert again["summary"] == committed["summary"]
    assert again["n"] == committed["n"] == 9
    assert again["frozen"] == committed["frozen"]  # #100: 合わせ直すのは act_3d_gb だけ
    # 照合の行も同じ(空のキャッシュで作る: この PC の bench の実測を混ぜない。#100 のチェッカーの M2)
    assert again["check"] == committed["check"]
    assert again["loo"] == committed["loo"]
    assert set(again["coefficients"]) - set(again["frozen"]) == {"act_3d_gb"}
    assert load_coefficients().overhead_gb == committed["coefficients"]["overhead_gb"]


def test_cli_info_shows_limits(capsys):
    assert cli.main(["info", "text_to_video_wan", "--vram", "10", "--ram", "32", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "この PC なら逃がさずに GPU で 長辺" in out and "コマまで good" in out
    assert (
        cli.main(
            ["fit", "--json", "--limits", "--vram", "10", "--ram", "32", "--no-cache", "--all-workflows"]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    wan = next(m for m in data["models"] if m["id"] == "text_to_video_wan")
    assert wan["estimate"]["limits"]["max_side_px"] and wan["estimate"]["vram_sequential_gb"]


# --- レビューでの指摘への対応(PR #10) -----------------------------------------------------------


def test_unknown_size_is_not_judged_as_fitting(db):
    c = Coefficients()
    cases = [
        ModelEntry(id="a", name="a", modality="image"),  # ファイルもテンプレートの大きさも無い
        ModelEntry(
            id="b", name="b", modality="tts", files=[ModelFile(name="m.safetensors", size_bytes=None)]
        ),
        ModelEntry(id="c", name="c", modality="image", files=[ModelFile(name="m.safetensors", size_bytes=0)]),
        ModelEntry(
            id="d", name="d", modality="image", files=[ModelFile(name="m.safetensors", size_bytes=-5)]
        ),
        ModelEntry(  # 一部だけ分からない
            id="e",
            name="e",
            modality="image",
            files=[
                f("dit.safetensors", 4, "diffusion_models"),
                ModelFile(name="te.safetensors", directory="text_encoders"),
            ],
        ),
    ]
    for e in cases:
        for vram in (0, 8, 48):  # GPU 無し(CPU)でも「小さい」とみなさない
            est = estimate(e, pc(vram), coefficients=c, with_limits=True)
            assert est.unknown_size and est.fit == "unknown" and est.run_mode == "unknown", (e.id, vram)
            assert est.basis == "estimated" and est.reasons[0] == UNKNOWN_SIZE_REASON
            assert est.limits is None and est.measured_on is None
    # テンプレートの大きさがあればそれを使う(大きさが分かる)
    t = ModelEntry(
        id="t",
        name="t",
        modality="image",
        files=[ModelFile(name="m.safetensors")],
        template=Template(name="t", size_bytes=2 * GIB),
    )
    assert not estimate(t, pc(8), coefficients=c).unknown_size
    # recommend には出さない。fit / info には出す
    d = Database(models=[cases[0], db.get("image_sdxl_simple")])
    assert [x.model.id for x in recommend(d, pc(16), limit=None)] == ["image_sdxl_simple"]
    assert {x.model.id for x in evaluate(d, pc(16))} == {"a", "image_sdxl_simple"}


def hf(*names_gb) -> ModelEntry:
    return ModelEntry(
        id="hf:x/y",
        name="y",
        modality="image",
        source="huggingface",
        files=[ModelFile(name=n, size_bytes=int(g * GIB)) for n, g in names_gb],
    )


def test_hf_listing_counts_one_variant_per_component():
    # stabilityai/stable-diffusion-xl-base-1.0 の並び(主なもの)。全部足すと 80 GiB を超える
    sdxl = hf(
        ("sd_xl_base_1.0.safetensors", 6.46),
        ("sd_xl_base_1.0_0.9vae.safetensors", 6.46),
        ("sd_xl_offset_example-lora_1.0.safetensors", 0.05),
        ("unet/diffusion_pytorch_model.safetensors", 9.6),
        ("unet/diffusion_pytorch_model.fp16.safetensors", 4.8),
        ("unet/openvino_model.bin", 9.6),
        ("unet/diffusion_flax_model.msgpack", 9.6),
        ("text_encoder/model.safetensors", 0.46),
        ("text_encoder/model.fp16.safetensors", 0.23),
        ("text_encoder/pytorch_model.bin", 0.46),
        ("text_encoder_2/model.safetensors", 2.6),
        ("text_encoder_2/model.fp16.safetensors", 1.3),
        ("vae/diffusion_pytorch_model.safetensors", 0.31),
        ("vae/diffusion_pytorch_model.fp16.safetensors", 0.16),
        ("vae_1_0/diffusion_pytorch_model.safetensors", 0.31),
        ("onnx/unet/model.onnx_data", 9.6),
        ("model_index.json", 0.0001),
    )
    p = components(sdxl)
    # 全部入りと分かっている単一ファイル 1 つ + LoRA だけ
    assert p.total == pytest.approx(6.46 + 0.05, abs=0.01)
    assert any("単一ファイルだけ" in n for n in p.notes)
    # diffusers 形式だけ: 部品ごとに fp16 の safetensors を 1 組、分割は全部数える
    diffusers = hf(
        ("transformer/diffusion_pytorch_model-00001-of-00002.safetensors", 5),
        ("transformer/diffusion_pytorch_model-00002-of-00002.safetensors", 3),
        ("transformer/diffusion_pytorch_model.bin", 8),
        ("text_encoder/model.fp16.safetensors", 2),
        ("text_encoder/model.safetensors", 4),
        ("text_encoder/pytorch_model.fp16.bin", 2),
        ("vae/diffusion_pytorch_model.safetensors", 0.2),
        ("vae/diffusion_pytorch_model.ckpt", 0.2),
    )
    p = components(diffusers)
    assert (p.denoise, p.encode, p.decode) == pytest.approx((8, 2, 0.2))
    # 本体だけの単一ファイル + diffusers のエンコーダ(FLUX のような並び): エンコーダは数え落とさない
    flux = hf(
        ("flux1-dev.safetensors", 22.2),
        ("transformer/diffusion_pytorch_model-00001-of-00003.safetensors", 9.3),
        ("transformer/diffusion_pytorch_model-00002-of-00003.safetensors", 9.3),
        ("transformer/diffusion_pytorch_model-00003-of-00003.safetensors", 3.6),
        ("text_encoder/model.safetensors", 0.23),
        ("text_encoder_2/model-00001-of-00002.safetensors", 4.6),
        ("text_encoder_2/model-00002-of-00002.safetensors", 4.2),
        ("ae.safetensors", 0.31),
        ("vae/diffusion_pytorch_model.safetensors", 0.31),
    )
    p = components(flux)
    assert p.denoise == pytest.approx(22.2) and p.encode == pytest.approx(0.23 + 8.8)
    assert p.decode == pytest.approx(0.31)
    # GGUF の量子化が並ぶもの: 1 つだけ(q8 を優先)
    gguf = hf(("flux1-dev-Q4_K_S.gguf", 6.8), ("flux1-dev-Q8_0.gguf", 12.7), ("flux1-dev-Q5_K_M.gguf", 8.4))
    assert components(gguf).total == pytest.approx(12.7)


def test_comfyui_directory_dedupes_same_part_only():
    e = ModelEntry(
        id="x",
        name="x",
        modality="image",
        files=[
            f("flux1-dev.safetensors", 22, "diffusion_models"),
            f("t5xxl_fp16.safetensors", 9.1, "text_encoders"),
            f("t5xxl_fp8_e4m3fn.safetensors", 4.6, "text_encoders"),  # 同じ部品の別精度
            f("clip_l.safetensors", 0.23, "text_encoders"),  # 別の部品
            f("ae.safetensors", 0.31, "vae"),
        ],
    )
    p = components(e)
    assert p.encode == pytest.approx(9.1 + 0.23)
    assert any("別の形式・精度" in n for n in p.notes)
    # 名前の違う本体(高ノイズ用・低ノイズ用)はフォルダの外でも両方数える
    two = hf(("wan2.2_high_noise_14B.safetensors", 13), ("wan2.2_low_noise_14B.safetensors", 13))
    assert components(two).total == pytest.approx(26)
    # ComfyUI 風のフォルダ(text_encoders/)の中は名前ごとに別の部品。同じ名前の別精度は 1 つ
    sd35 = hf(
        ("sd3.5_large.safetensors", 15),
        ("text_encoders/clip_g.safetensors", 1.3),
        ("text_encoders/clip_l.safetensors", 0.23),
        ("text_encoders/t5xxl_fp16.safetensors", 9.1),
        ("text_encoders/t5xxl_fp8_e4m3fn.safetensors", 4.6),
    )
    p = components(sd35)
    assert p.denoise == pytest.approx(15) and p.encode == pytest.approx(1.3 + 0.23 + 9.1)


def test_bundled_seed_sizes_unchanged_by_dedupe(db):
    seed = {
        x.model_id for x in db.measurements
    }  # 実測のある 8 件(テンプレートが別案のファイルを並べる一式は対象外)
    for m in (x for x in db.models if x.id in seed):
        if getattr(m, "files_complete", True) is False:  # 本体の無い一式はテンプレートの大きさを下限に使う
            continue
        if any(not x.size_bytes for x in m.files):  # 大きさが一部しか無い一式はテンプレートの大きさを使う
            continue
        files = [x for x in m.files if x.size_bytes]
        # 全部入りの内訳の割合は丸めてあるので、合計は 0.2% ほどずれる
        assert components(m).total == pytest.approx(sum(x.size_bytes for x in files) / GIB, rel=0.003), m.id


def test_role_inference_uses_directory_and_known_encoder_names():
    assert _role_of(ModelFile(name="qwen_image_fp8_e4m3fn.safetensors")) == "main"
    assert _role_of(ModelFile(name="qwen_2.5_vl_7b_fp8_scaled.safetensors")) == "text_encoder"
    assert _role_of(ModelFile(name="qwen_3_4b.safetensors")) == "text_encoder"
    assert _role_of(ModelFile(name="umt5_xxl_fp8_e4m3fn_scaled.safetensors")) == "text_encoder"
    assert _role_of(ModelFile(name="clip_l.safetensors")) == "text_encoder"
    assert _role_of(ModelFile(name="gemma_3_12B_it.safetensors")) == "text_encoder"
    # 置き場所が名前より先
    assert _role_of(ModelFile(name="qwen_3_4b.safetensors", directory="diffusion_models")) == "main"
    assert _role_of(ModelFile(name="x.safetensors", directory="controlnet")) == "controlnet"
    # HF のフォルダ名
    assert _role_of(ModelFile(name="transformer/model.safetensors")) == "main"
    assert _role_of(ModelFile(name="text_encoder_2/model.safetensors")) == "text_encoder"
    assert _role_of(ModelFile(name="image_encoder/model.safetensors")) == "clip_vision"
    # 本体と同時に載る部品(ControlNet・model_patches・LoRA)は生成の段に入る
    e = ModelEntry(
        id="x",
        name="x",
        modality="image",
        files=[
            f("qwen_image_fp8_e4m3fn.safetensors", 19),
            f("qwen_2.5_vl_7b_fp8_scaled.safetensors", 8.7),
            f("cn.safetensors", 3, "controlnet"),
            f("patch.safetensors", 2, "model_patches"),
            f("style.safetensors", 0.5, "loras"),
            f("qwen_image_vae.safetensors", 0.24),
        ],
    )
    p = components(e)
    assert p.denoise == pytest.approx(19 + 3 + 2 + 0.5) and p.encode == pytest.approx(8.7)
    assert p.decode == pytest.approx(0.24)


def test_precision_is_case_insensitive(db):
    assert norm_precision("BF16") == "bf16" and norm_precision("float16") == "fp16"
    assert norm_precision("GGUF-Q4_K_M") == "q4" and norm_precision(" FP8 ") == "fp8"
    e = db.get("image_z_image_turbo")
    s = Settings(width=1024, height=1024)
    c = Coefficients()
    lower = estimate(e, pc(16), Settings(width=1024, height=1024, precision="fp8"), coefficients=c)
    upper = estimate(e, pc(16), Settings(width=1024, height=1024, precision="FP8"), coefficients=c)
    assert upper.vram_gb == lower.vram_gb < estimate(e, pc(16), s, coefficients=c).vram_gb
    # 元と同じ精度(大文字でも)を指定しても実測はそのまま使う
    same = estimate(e, pc(16), Settings(width=768, height=768, precision="BF16"), db.measurements)
    assert same.basis == "measured"


def test_batch_and_precision_must_match_for_measured(db):
    e = db.get("image_z_image_turbo_int8")
    one = estimate(e, pc(16), Settings(width=768, height=768), db.measurements)
    four = estimate(e, pc(16), Settings(width=768, height=768, batch=4), db.measurements)
    assert one.basis == "measured" and four.basis == "estimated" and four.vram_gb > one.vram_gb
    other = estimate(e, pc(16), Settings(width=768, height=768, precision="fp8"), db.measurements)
    assert other.basis == "estimated"


def test_failed_or_nonstandard_measurements_are_ignored():
    e = ModelEntry(
        id="m12", name="m12", modality="image", files=[f("dit.safetensors", 12, "diffusion_models")]
    )
    s = Settings(width=1024, height=1024)
    oom = Measurement(
        model_id="m12", gpu_name="g", gpu_vram_gb=8, peak_vram_gb=7.6, settings=s, outcome="oom"
    )
    est = estimate(e, pc(8), s, [oom])
    assert est.basis == "estimated" and est.measured_on is None and est == estimate(e, pc(8), s, [])
    custom = Measurement(
        model_id="m12", gpu_name="g", gpu_vram_gb=8, peak_vram_gb=7.6, settings=s, standard=False
    )
    assert estimate(e, pc(8), s, [custom]).basis == "estimated"
    # 重み 12 + 固定分 1.47 より大きい実測(下限より小さい実測は下限で止める。#100)
    ok = Measurement(model_id="m12", gpu_name="g", gpu_vram_gb=16, peak_vram_gb=14.0, settings=s)
    assert estimate(e, pc(16), s, [ok]).basis == "measured"
    # 合わせ直しにも使わない
    rows = build_rows(Database(models=[e], measurements=[oom, custom, ok]))
    assert [r.m.peak_vram_gb for r in rows] == [14.0]


def test_negative_settings_are_ignored_and_cli_rejects_them(db, capsys):
    e = db.get("image_sdxl_simple")
    bad = estimate(e, pc(16), Settings(width=-5, height=0, batch=0), coefficients=Coefficients())
    good = estimate(e, pc(16), None, coefficients=Coefficients())
    assert bad.vram_gb == good.vram_gb and bad.settings["batch"] == 1
    for argv in (
        ["fit", "--width", "0"],
        ["fit", "--height", "-1"],
        ["fit", "--frames", "0"],
        ["fit", "--length", "-3"],
        ["fit", "--vram", "-8"],
        ["fit", "--ram", "0"],
    ):
        with pytest.raises(SystemExit) as ex:
            cli.main([*argv, "--no-cache"])
        assert ex.value.code == 2, argv
    capsys.readouterr()


def test_cpu_judgment_does_not_claim_gpu_measurement(capsys):
    assert cli.main(["info", "image_sdxl_simple", "--json", "--vram", "0", "--ram", "32", "--no-cache"]) == 0
    est = json.loads(capsys.readouterr().out)["estimate"]
    assert est["basis"] == "estimated" and est["measured_on"] is None
    assert any("GPU が無いので" in r for r in est["reasons"])


def test_sizes_are_gib():
    e = ModelEntry(id="x", name="x", modality="image", files=[f("dit.safetensors", 1, "diffusion_models")])
    assert components(e).total == pytest.approx(1.0)
    assert json.loads(bundled_text("measurements.json"))["unit"] == "GiB"


def test_accuracy_summary_only_claims_evaluable_modalities():
    sm = json.loads(bundled_text("calibration.json"))["summary"]
    assert sm["evaluable_modalities"] == ["3d", "image"] and sm["n_evaluable"] == 4
    assert sm["by_modality"]["video"]["target_met"] is None
    assert sm["by_modality"]["music"]["target_met"] is None
    assert sm["censored_trivial"] == sm["n_censored"] == 3  # 張り付きは情報を持たない


# 人が決めた目安: 8 モデルとも最低メモリ 16 GB(AI生成キットのカタログ)。
# メモリ 16 GB を理由に too_tight にしない
@pytest.mark.parametrize("model_id", sorted(CURATED))
@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("ram", [16, 15.3])  # 15.x GiB と出る 16 GB 搭載のノート PC も
def test_curated_ram_16gb_is_never_the_reason_for_too_tight(db, model_id, which, ram):
    vram = CURATED[model_id][which]
    e = estimate(db.get(model_id), pc(vram, ram=ram), None, db.measurements)
    assert not any("メモリ不足" in r for r in e.reasons), e.reasons
    roomy = estimate(db.get(model_id), pc(vram, ram=64), None, db.measurements)
    if roomy.fit != "too_tight":
        assert e.fit != "too_tight"


def test_incomplete_entry_uses_size_floor_not_listed_files():
    """#2 が本体の無い一式に files_complete=False と size_floor_bytes を付ける。小さく見せない。"""
    from genmodelfit.models import ModelEntry, ModelFile, Template

    e = ModelEntry(
        id="inc",
        name="inc",
        modality="video",
        files=[
            ModelFile(
                name="clip_vision_h.safetensors",
                size_bytes=1_300_000_000,
                directory="clip_vision",
                role="clip_vision",
            )
        ],
        template=Template(name="inc", size_bytes=20_400_000_000),
    )
    e.files_complete = False
    e.size_floor_bytes = 20_400_000_000
    from genmodelfit.estimate import estimate as est
    from genmodelfit.models import Gpu, SystemInfo

    pc4 = SystemInfo(
        os="t",
        cpu="t",
        cpu_cores=8,
        ram_total_gb=32,
        ram_available_gb=32,
        gpus=[Gpu(name="g", vendor="nvidia", vram_total_gb=4, backend="cuda")],
    )
    r = est(e, pc4)
    assert r.fit != "perfect" and r.vram_gb > 10
    e.size_floor_bytes = None
    e.template = None
    assert est(e, pc4).unknown_size
