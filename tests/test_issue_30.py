"""0.2.0(Issue #30): 「?」(分からない)と「-」(当てはまらない)の使い分け・精度をワークフローと
リポジトリ(Hub API・config.json)から読む・精度の出どころ・401 のリポジトリの記録。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genmodelfit import cli, load_db
from genmodelfit.fetch import FetchError
from genmodelfit.groups import model_precision_source, precision_label
from genmodelfit.models import (
    MODALITIES,
    RUN_UNKNOWN,
    Estimate,
    Gpu,
    ModelEntry,
    ModelFile,
    SystemInfo,
    Template,
)
from genmodelfit.recommend import mode_label
from genmodelfit.repo_precision import (
    RepoPrecisionCache,
    config_dtype,
    dominant_dtype,
    inspect_repos,
    recheck_unauthorized,
)
from genmodelfit.table import COLUMN_WIDTH, distinct_names
from genmodelfit.update import set_precision, set_workflow_precision, unauthorized_repos
from genmodelfit.workflow import loader_precision

ROOT = Path(__file__).resolve().parents[1]
HF = "https://huggingface.co"


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))


def pc(vram: float, ram: float = 32.0) -> SystemInfo:
    gpus = [Gpu(name="Test GPU", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


def _node(nid: int, typ: str, values, mode: int = 0) -> dict:
    return {"id": nid, "type": typ, "mode": mode, "widgets_values": values, "inputs": [], "outputs": []}


def _wf(*nodes) -> dict:
    return {"nodes": list(nodes), "links": []}


# --- ? と - --------------------------------------------------------------------------------------


def test_precision_label_unknown_is_question_mark():
    assert precision_label(None) == "?" and precision_label("bf16") == "bf16"


def _est(**kw) -> Estimate:
    base = dict(vram_gb=1.0, vram_offload_gb=1.0, ram_gb=1.0, disk_gb=1.0, basis="estimated", fit="good")
    base.update(kw)
    return Estimate(run_mode=kw.pop("run_mode", "gpu"), **{k: v for k, v in base.items() if k != "run_mode"})


def test_mode_label_question_for_unknown_dash_for_no_run():
    unknown = _est(fit="unknown", run_mode=RUN_UNKNOWN, unknown_size=True, vram_gb=0.0, ram_gb=0.0)
    no_run = _est(fit="too_tight", run_mode="none")
    assert mode_label(unknown) == "?" and mode_label(no_run) == "-"


def test_unknown_size_estimate_json_has_nulls():
    e = _est(
        fit="unknown", run_mode=RUN_UNKNOWN, unknown_size=True, vram_gb=0.0, vram_offload_gb=0.0, ram_gb=0.0
    )
    d = e.to_dict()
    assert d["vram_gb"] is None and d["vram_offload_gb"] is None and d["ram_gb"] is None
    assert e.vram_gb == 0.0  # Python の属性は計算のため 0.0 のまま


def test_fit_table_uses_question_mark_for_unknown_precision(capsys):
    db = load_db(use_cache=False)
    unknown = [m for m in db.models if m.precision is None and m.content == "sfw"]
    assert unknown, "精度の分からない項目が無くなったら、この試験は別の項目で見る"
    code = cli.main(["fit", "--vram", "24", "--ram", "64", "--all", "--all-workflows", "--sort", "name"])
    out = capsys.readouterr().out
    assert code == 0
    assert "? = 分からない(精度・ライセンス・大きさ。判定していない)、- = 当てはまらない" in out
    # 行は Model の列の文字で、あいまいさ無く探す(ID の先頭だけで探すと、頭の同じ別の項目の行に当たる。#197)。
    # --all は既定(sfw)の全部、--all-workflows はワークフローごとに 1 行。表の名前は表と同じ省略で作る
    lines = out.splitlines()
    k = next(i for i, ln in enumerate(lines) if ln.startswith("Type ") and " Model " in ln and " Prec " in ln)
    header = lines[k]
    rows = []
    for ln in lines[k + 1 :]:  # 表の行は種類(Type)で始まる。その後ろは凡例
        if ln.split(" ", 1)[0] not in MODALITIES:
            break
        rows.append(ln)
    shown = [m.id for m in db.models if m.content == "sfw"]
    assert len(rows) == len(shown)
    x, y, prec = header.index(" Model ") + 1, header.index(" Use ") + 1, header.index(" Prec ") + 1
    cells = distinct_names(shown, y - x - 1)
    by_cell: dict[str, list[str]] = {}
    for r in rows:
        by_cell.setdefault(r[x:y].strip(), []).append(r)
    assert set(by_cell) == set(cells.values())  # 表の名前は 1 行に 1 つ(重ならない)
    for m in unknown:
        (row,) = by_cell[cells[m.id]]
        assert row[prec : prec + COLUMN_WIDTH["Prec"]].strip() == "?", row  # Prec が ?(前は -)
    assert all(len(line) <= 100 for line in out.splitlines())


def test_json_unknown_size_run_mode(capsys):
    from genmodelfit.recommend import judge

    db = load_db(use_cache=False)
    m = ModelEntry(
        id="hf:x/unknown",
        name="x",
        modality="image",
        source="huggingface",
        files=[ModelFile(name="m-Q8_0.gguf", role="main", size_bytes=10**9, license="mit")],
        files_complete=False,
        license="mit",
    )
    d = judge(m, pc(24, 64), db).to_dict()
    assert d["fit_level"] == "unknown" and d["run_mode"] == "unknown" and d["run_mode_label"] == "?"
    assert d["memory_required_gb"] is None and d["ram_required_gb"] is None
    assert d["memory_available_gb"] == 24.0 and d["memory_kind"] == "vram"  # ある量は分かる
    assert "precision_source" in d and all("precision_source" in w for w in d["workflows"])
    assert d["estimate"]["vram_gb"] is None and d["estimate"]["ram_gb"] is None
    assert cli.main(["fit", "--json", "--vram", "8", "--ram", "16", "--all", "--fit", "all"]) == 0
    for row in json.loads(capsys.readouterr().out)["models"]:
        if row["run_mode"] == "none":  # 動かない: 当てはまらない
            assert row["run_mode_label"] == "-" and row["fit_level"] == "too_tight"


# --- 精度の出どころ --------------------------------------------------------------------------------


def _entry(files: list[ModelFile], **kw) -> ModelEntry:
    return ModelEntry(id="t", name="T", modality="image", files=files, template=Template(name="t"), **kw)


def test_precision_source_order_header_wins():
    f = ModelFile(name="x.safetensors", role="main", size_bytes=10, header_precision="bf16")
    f.repo_precision, f.repo_precision_source = "fp32", "hf_api"
    e = _entry([f], workflow_precision="fp8")
    set_precision(e)
    assert (e.precision, e.precision_source) == ("bf16", "header")


@pytest.mark.parametrize(
    ("name", "repo", "wf", "want"),
    [
        ("model_fp16.safetensors", ("fp32", "hf_api"), "fp8", ("fp16", "filename")),
        ("model.safetensors", ("fp32", "hf_api"), "fp8", ("fp32", "hf_api")),
        ("model.pth", ("bf16", "config"), "fp8", ("bf16", "config")),
        ("model.safetensors", (None, None), "fp8", ("fp8", "workflow")),
        ("model.safetensors", (None, None), None, (None, None)),
    ],
)
def test_precision_source_fallbacks(name, repo, wf, want):
    f = ModelFile(name=name, role="main", size_bytes=10)
    f.repo_precision, f.repo_precision_source = repo
    assert model_precision_source(_entry([f], workflow_precision=wf)) == want


def test_fileless_template_uses_workflow():
    assert model_precision_source(_entry([], workflow_precision="fp16")) == ("fp16", "workflow")


# --- ワークフローから --------------------------------------------------------------------------------


def test_loader_precision_unet_weight_dtype():
    wf = _wf(_node(1, "UNETLoader", ["flux-2-klein-9b.safetensors", "fp8_e4m3fn"]))
    assert loader_precision(wf, {"flux-2-klein-9b.safetensors"}) == "fp8"
    wf = _wf(_node(1, "UNETLoader", ["flux-2-klein-9b.safetensors", "default"]))
    assert loader_precision(wf, {"flux-2-klein-9b.safetensors"}) is None  # default は指定なし


def test_loader_precision_wanvideo_and_custom_nodes():
    # 量子化の指定 → fp8。指定なしならファイル名、ファイル名に無ければ読み込む精度の指定
    q = ["Wan2_2-T2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors", "fp16_fast", "fp8_e4m3fn_scaled", "offload"]
    assert loader_precision(_wf(_node(1, "WanVideoModelLoader", q))) == "fp8"
    plain = ["Wan21-14B-SCAIL-preview_comfy_bf16.safetensors", "fp16_fast", "disabled", "offload_device"]
    assert loader_precision(_wf(_node(1, "WanVideoModelLoader", plain))) == "bf16"
    gimm = _node(1, "DownloadAndLoadGIMMVFIModel", ["gimmvfi_r.safetensors", "fp32"])
    assert loader_precision(_wf(gimm)) == "fp32"
    assert loader_precision(_wf(_node(1, "DownloadAndLoadLivePortraitModels", ["auto", "human"]))) is None


def test_loader_precision_skips_muted_and_non_main_loaders():
    wf = _wf(
        _node(1, "UNETLoader", ["a_fp8_e4m3fn.safetensors", "default"], mode=2),  # 無効
        _node(2, "LoraLoaderModelOnly", ["lora_bf16.safetensors", 1.0]),  # LoRA は本体ではない
        _node(3, "CLIPLoader", ["umt5_xxl_fp16.safetensors", "wan", "default"]),
    )
    assert loader_precision(wf) is None
    # 本体のファイル名が分かっていれば、それを読み込むノードだけを見る
    wf = _wf(
        _node(1, "CheckpointLoaderSimple", ["other_fp16.safetensors"]),
        _node(2, "UNETLoader", ["main.safetensors", "fp8_e4m3fn"]),
    )
    assert loader_precision(wf, {"main.safetensors"}) == "fp8"
    assert loader_precision(wf, {"missing.safetensors"}) is None


def test_set_workflow_precision_never_overrides_header():
    f = ModelFile(name="m.safetensors", role="main", size_bytes=10, header_precision="bf16")
    e = _entry([f])
    set_workflow_precision(e, _wf(_node(1, "UNETLoader", ["m.safetensors", "fp8_e4m3fn"])))
    assert e.workflow_precision == "fp8" and (e.precision, e.precision_source) == ("bf16", "header")


# --- リポジトリから(Hub API・config.json) --------------------------------------------------------


def test_dominant_dtype_and_config_dtype():
    assert dominant_dtype({"BF16": 9_000, "F32": 100}) == "bf16"
    assert dominant_dtype({"BF16": 5_000, "F8_E4M3": 5_000}) is None  # 混ざっていれば決めない
    assert dominant_dtype({"U8": 100}) is None and dominant_dtype(None) is None
    assert config_dtype({"torch_dtype": "float32"}) == "fp32"
    assert config_dtype({"dtype": "bfloat16"}) == "bf16"
    assert config_dtype({"torch_dtype": None}) is None and config_dtype([]) is None


class FakeHub:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def get_json(self, url: str):
        self.calls.append(url)
        v = self.routes.get(url)
        if isinstance(v, int):
            raise FetchError(url, f"HTTP {v}", v)
        if v is None:
            raise FetchError(url, "HTTP 404", 404)
        return v


def _hf_entry(repo: str, path: str) -> ModelEntry:
    f = ModelFile(name=path, url=f"{HF}/{repo}/resolve/main/{path}", role="main", size_bytes=10, repo_id=repo)
    return ModelEntry(id=f"hf:{repo}", name=repo, modality="tts", source="huggingface", files=[f])


def test_inspect_repos_api_config_401_and_cache(tmp_path):
    gated = _hf_entry("org/gated", "model.safetensors")
    gated.files[0].size_bytes = 40  # F32 × 10 要素
    mixed = _hf_entry("org/mixed", "model.safetensors")
    pth = _hf_entry("org/pth", "sub/model.pth")
    private = _hf_entry("org/private", "model.safetensors")
    named = _hf_entry("org/named", "model_fp16.safetensors")  # 名前で分かるものは問い合わせない
    hub = FakeHub(
        {
            f"{HF}/api/models/org/gated": {"gated": "auto", "safetensors": {"parameters": {"F32": 10}}},
            # 集計の大きさ(10 億 × 2 バイト)が本体(10 バイト)と合わない → 使わない
            f"{HF}/api/models/org/mixed": {"safetensors": {"parameters": {"BF16": 10**9}}},
            f"{HF}/api/models/org/pth": {"siblings": [{"rfilename": "sub/config.json"}]},
            f"{HF}/org/pth/resolve/main/sub/config.json": {"torch_dtype": "bfloat16"},
            f"{HF}/api/models/org/private": 401,
        }
    )
    cache = RepoPrecisionCache(tmp_path / "repo_precision.json")
    stats = inspect_repos([gated, pth, private, named, mixed], hub.get_json, cache, delay=0)
    for e in (gated, pth, private, named, mixed):
        set_precision(e)
    assert mixed.precision is None and stats.size_mismatch == ["org/mixed"]
    assert (gated.precision, gated.precision_source) == ("fp32", "hf_api")
    assert (pth.precision, pth.precision_source) == ("bf16", "config")
    assert private.precision is None and stats.unauthorized == ["org/private"]
    assert (named.precision, named.precision_source) == ("fp16", "filename")
    assert not any("org/named" in c for c in hub.calls)
    assert (stats.files, stats.hf_api, stats.config, stats.unknown) == (4, 1, 1, 2)
    cache.save()
    # オフラインはキャッシュだけ(読めたものは同じ答え、401 は分からないまま)
    again = [_hf_entry("org/gated", "model.safetensors"), _hf_entry("org/pth", "sub/model.pth")]
    again[0].files[0].size_bytes = 40
    s2 = inspect_repos(again, None, RepoPrecisionCache(tmp_path / "repo_precision.json"), offline=True)
    assert [f.files[0].repo_precision for f in again] == ["fp32", "bf16"] and s2.fetched == 0


def test_inspect_repos_does_not_fetch_config_of_gated_repo(tmp_path):
    e = _hf_entry("org/g", "model.bin")
    hub = FakeHub({f"{HF}/api/models/org/g": {"gated": "manual", "siblings": [{"rfilename": "config.json"}]}})
    inspect_repos([e], hub.get_json, RepoPrecisionCache(None), delay=0)
    assert hub.calls == [f"{HF}/api/models/org/g"] and e.files[0].repo_precision is None


def test_recheck_unauthorized_and_old_failures():
    hub = FakeHub({f"{HF}/api/models/a/open": {}, f"{HF}/api/models/a/closed": 401})
    hub.routes[f"{HF}/api/models/google/gemma-3-12b"] = 401
    r = recheck_unauthorized(["a/closed", "a/open", "google/gemma-3-12b"], hub.get_json, delay=0)
    assert r["unauthorized"] == ["a/closed", "google/gemma-3-12b"] and r["readable"] == ["a/open"]
    assert r["private_or_missing"] == ["a/closed"] and r["missing"] == ["google/gemma-3-12b"]
    old = {"failures": ["x/y: HTTP 401: https://…", "z/w: HTTP 404: https://…"]}
    assert unauthorized_repos(old) == ["x/y"]
    assert unauthorized_repos({"unauthorized": ["b/c"], "failures": old["failures"]}) == ["b/c"]


# --- 同梱の一覧 -------------------------------------------------------------------------------------


def test_bundled_precision_sources_are_consistent():
    db = load_db(use_cache=False)
    for m in db.models:
        prec, src = model_precision_source(m)
        assert (m.precision, m.precision_source) == (prec, src), m.id
    assert isinstance(db.sources["huggingface"].get("unauthorized"), list)


def test_license_requests_doc_lists_unknown_upstreams():
    text = (ROOT / "docs" / "license_requests.md").read_text(encoding="utf-8")
    for repo in (
        "circlestone-labs/Anima-Official-LoRAs",
        "jingheya/lotus-depth-d-v1-1",
        "teemosliang/SDPose-Wholebody",
        "lightx2v/Minimax-h3-Turbo",
        "microsoft/Mage-Flow",
        "microsoft/Lens",
    ):
        assert repo in text, repo
