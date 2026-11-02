import json

import pytest

import genmodelfit as gmf
from genmodelfit import cli
from genmodelfit.db import load
from genmodelfit.estimate import Coefficients, estimate, judge
from genmodelfit.hardware import detect, parse_nvidia_smi
from genmodelfit.models import Gpu, ModelEntry, ModelFile, Settings, SystemInfo


def pc(vram: float, ram: float = 32.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


def entry(sizes_gb: list[float], modality: str = "image") -> ModelEntry:
    files = [ModelFile(name=f"f{i}", size_bytes=int(s * 2**30)) for i, s in enumerate(sizes_gb)]
    return ModelEntry(id="x", name="x", modality=modality, files=files)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


def test_parse_nvidia_smi():
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 3070, 8192, 4199, 3993, 566.03, 8.6\n")
    assert gpus[0].name == "NVIDIA GeForce RTX 3070"
    assert gpus[0].vram_total_gb == 8.0 and gpus[0].vram_free_gb == pytest.approx(4.1, abs=0.01)
    assert gpus[0].compute_capability == "8.6"
    assert parse_nvidia_smi("garbage") == []


def test_detect_overrides():
    s = detect(vram_gb=12, ram_gb=24, gpus=[])
    assert s.vram_gb == 12 and s.ram_total_gb == 24 and s.overrides == ["vram", "ram"]
    assert detect(gpus=[]).vram_gb == 0


def test_bundled_db_and_measurements():
    db = load(use_cache=False)
    assert db.origin == "bundled" and len(db.models) >= 8
    assert len(db.measurements) >= 9
    ids = {m.id for m in db.models}
    assert all(m.model_id in ids for m in db.measurements), "実測は一覧にある ID を指す"


def test_roundtrip():
    db = load(use_cache=False)
    m = db.models[0]
    again = ModelEntry.from_dict(json.loads(json.dumps(m.to_dict())))
    assert again.to_dict() == m.to_dict()


def test_fit_levels_by_vram():
    c = Coefficients()
    e = entry([4.0, 2.0])
    assert estimate(e, pc(24), coefficients=c).fit == "perfect"
    # 動的 VRAM の無い ComfyUI(0.16 より前)では 4 GB でぎりぎりか入らない
    old = pc(4)
    old.comfyui_version = "0.15.0"
    assert estimate(e, old, coefficients=c).fit in ("marginal", "too_tight")
    # 動的 VRAM(画像・NVIDIA。版が分からなければ使えると仮定)では逃がして動く
    assert estimate(e, pc(4), coefficients=c).run_mode == "offload"
    assert estimate(e, pc(1), coefficients=c).fit == "too_tight"
    assert estimate(entry([0.1], "tts"), pc(0), coefficients=c).run_mode == "cpu"
    assert estimate(entry([10.0]), pc(0), coefficients=c).fit == "too_tight"


def test_judge_ram_is_hard_limit():
    fit, _mode, reasons = judge(4, 3, 64, pc(24, ram=16), Coefficients(), entry([4]))
    assert fit == "too_tight" and "メモリ" in reasons[0]


def test_measured_wins():
    db = load(use_cache=False)
    m = db.get("image_z_image_turbo_int8")
    e = estimate(m, pc(16), Settings(width=768, height=768), db.measurements)
    assert e.basis == "measured" and e.vram_gb == pytest.approx(13.2)
    e2 = estimate(m, pc(16), Settings(width=1536, height=1536), db.measurements)
    assert e2.basis == "estimated"


def test_recommend_order_and_filters():
    db = load(use_cache=False)
    fits = gmf.recommend(modality="image", limit=None, system_info=pc(16), db=db)
    assert fits and all(f.model.modality == "image" for f in fits)
    # 自動でおすすめに出せるものが先、その中は 段階 → 公式 → 人気 → Score → ID(docs/scoring.md「並び」)
    from genmodelfit.recommend import list_key

    assert fits == sorted(fits, key=list_key)
    autos = [f.auto for f in fits]
    assert any(autos) and autos == sorted(autos, reverse=True)
    unc = gmf.recommend(content="uncensored", limit=None, system_info=pc(16), db=db)
    assert all(f.model.content == "uncensored" and f.model.content_evidence for f in unc)


def test_cli_json(capsys):
    assert cli.main(["recommend", "--json", "--vram", "8", "--ram", "32", "--no-cache", "-n", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "recommend" and out["system"]["best_gpu"]["vram_total_gb"] == 8
    # -m も --task も無ければ種類ごとに 3 件まで
    assert all(g["shown"] <= 3 for g in out["groups"]) and len(out["models"]) <= 3 * 6
    for m in out["models"]:
        assert m["fit_level"] in ("perfect", "good", "marginal") and m["estimate_confidence"] in (
            "measured",
            "estimated",
        )


def test_cli_system_and_info(capsys):
    assert cli.main(["system", "--json", "--vram", "8"]) == 0
    assert json.loads(capsys.readouterr().out)["system"]["overrides"] == ["vram"]
    assert cli.main(["info", "image_sdxl_simple", "--json", "--vram", "8", "--no-cache"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["model"]["id"] == "image_sdxl_simple" and out["estimate"]["fit"]
    assert cli.main(["info", "no-such-model", "--no-cache"]) == 1


def test_cli_text_output(capsys):
    assert cli.main(["fit", "--vram", "8", "--no-cache"]) == 0
    assert "SDXL" in capsys.readouterr().out


def test_fit_is_not_cut_by_recommend_default(capsys):
    from genmodelfit.cli import build_parser

    p = build_parser()
    assert p.parse_args(["fit"]).limit is None
    assert p.parse_args(["recommend"]).limit is None  # 既定の 10 件は cmd_recommend の中で決める
