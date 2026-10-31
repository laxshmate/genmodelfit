"""`--json` の形が docs/schema.md と合うか(組み込み側は別プロセスで呼んで stdout を読む)。

キーの組は docs/schema.md から写した。形を変えたら両方を直す。
"""

from __future__ import annotations

import json

import pytest
from clihelp import bundled_uncensored, run_cli

from genmodelfit import load_db
from genmodelfit.groups import build_index

HW = ["--vram", "8", "--ram", "32", "--no-cache"]

ENVELOPE = {"genmodelfit", "command"}
SYSTEM = {
    "os",
    "cpu",
    "cpu_cores",
    "ram_total_gb",
    "ram_available_gb",
    "gpus",
    "best_gpu",
    "disk_free_gb",
    "overrides",
    "swap_total_gb",
    "swap_max_gb",
    "swap_auto",
    "commit_limit_gb",
    "commit_used_gb",
    "other_gpus",
    "other_gpus_checked",
    "notes",
    "platform_verified",  # #108。--vram で仮定した PC は false
    "platform_verified_by",
}
GPU = {
    "name",
    "vendor",
    "vram_total_gb",
    "vram_free_gb",
    "driver",
    "backend",
    "vram_used_gb",
    "compute_capability",
    "compute_capability_source",
    "arch",
    "torch_cuda",
    "compute_dtype",
    "bf16",
    "fp8",
    "notes",
    "memory",  # 0.7.0(#22。後から足した項目)
    "gfx_target",
    "carveout_gb",
    "pci_id",  # 0.9.0(PCI の型番 ID。同じ GPU の照合)
    "vram_overridden",  # 0.14.1(--vram で量だけ上書きしたか。#150)
}
DATABASE = {"origin", "generated_at", "models", "measurements"}
FIT = {
    "id",
    "name",
    "model_name",
    "modality",
    "tasks",
    "content",
    "source",
    "runtime",
    "languages",
    "description",
    "fit_level",
    "fit_label",
    "run_mode",
    "run_mode_label",
    "level_cap",
    "score",
    "score_components",
    "popularity",  # #95: 人気の順位(並びの鍵。Score には入れない)
    "memory_kind",
    "memory_required_gb",
    "memory_available_gb",
    "utilization_pct",
    "gpu_memory_required_gb",
    "ram_required_gb",
    "disk_size_gb",
    "total_size_gb",
    "license",
    "licenses",
    "auto",
    "community",
    "held_reasons",
    "estimate_confidence",
    "notes",
    "files_count",
    "use",
    "precision",
    "precision_source",
    "group_key",
    "variants",
    "workflows",
    "estimate",
    "gate",
}
SCORE_COMPONENTS = {"memory_fit"}  # #95: Score = 収まり(docs/scoring.md)
ESTIMATE = {
    "vram_gb",
    "vram_sequential_gb",
    "vram_offload_gb",
    "ram_gb",
    "disk_gb",
    "basis",
    "basis_detail",  # #34(後から足した項目)
    "measured_on",
    "fit",
    "run_mode",
    "reasons",
    "settings",
    "limits",
    "vram_free_short",
    "unknown_size",
    "fit_from",  # 0.7.0(#26。後から足した項目)
    "measured_peak_vram_gb",
    "measured_level_vram_gb",  # #95: 段階に使った量
    "sample_count",  # 2026-10-22: 見積もりに使った実測の件数
    "vram_resident_gb",  # #102: 全部を載せたまま仕上げまで動かすときの量
    "optional_parts",  # #81: テンプレートの既定の値で読み込まれない部品
    "vram_gb_all_options",
    "fit_all_options",
}
LIMITS = {"level", "at_cap"}  # max_side_px / max_frames / max_seconds は種類で変わる
GATE = {"auto", "held_by", "comfyui_version", "checks"}
CHECK_IDS = [
    "license",
    "gated",
    "official_workflow",
    "custom_nodes",
    "comfyui_version",
    "url_reachable",
    "sha256",
    "content",
    "size_known",
    "reviewed",
]
# runtime が ComfyUI 以外のモデル(手のモデルの piper・irodori-tts)は、ComfyUI の 3 条件の代わりに app_runtime
APP_RUNTIME_CHECK_IDS = ["license", "gated", "app_runtime", *CHECK_IDS[5:]]
FIT_FILTERS = {
    "modality",
    "content",
    "min_fit",
    "fit",
    "license",
    "include_unknown_license",
    "task",
    "all_workflows",
    "limit",
}
RECOMMEND_FILTERS = FIT_FILTERS | {"per_type", "auto_only", "include_tools"}
LISTING_FILTERS = FIT_FILTERS | {"sort", "all"}
VARIANT = {"precision", "workflow_id", "memory_required_gb", "fit_level", "auto", "disk_size_gb"}
WORKFLOW = {
    "id",
    "name",
    "tasks",
    "use",
    "precision",
    "precision_source",
    "fit_level",
    "memory_required_gb",
    "disk_size_gb",
    "auto",
    "matched",
}
GROUP = {"modality", "matched", "shown"}
SEARCH_FILTERS = LISTING_FILTERS | {"query"}
LIST_KEYS = ENVELOPE | {"system", "database", "filters", "matched", "hidden", "groups", "models"}
HIDDEN_KEYS = {
    "modality",
    "uncensored",
    "sfw",
    "tools",
    "license",
    "license_unknown",
    "size_unknown",
    "too_tight",
    "below_min_fit",
    "other_fit",
    "held",
    "task",
    "same_model",
}
MODEL_ENTRY = {
    "id",
    "name",
    "modality",
    "tasks",
    "source",
    "files",
    "template",
    "params_b",
    "precision",
    "license",
    "license_url",
    "base_model",
    "content",
    "content_evidence",
    "discovered_from",
    "updated",
    "homepage",
    "downloads",
    "downloads_all_time",
    "likes",
    "total_size_bytes",
    "license_name",
    "license_evidence",
    "files_complete",
    "comfy_family",
    "comfy_vae",
    "size_floor_bytes",
    "sampler_stages",
    "workflow_precision",
    "precision_source",
    "languages",
    "runtime",
    "description",
    "card_languages",
    "review",
}
MODEL_FILE = {
    "name",
    "url",
    "sha256",
    "size_bytes",
    "directory",
    "repo_id",
    "license",
    "license_name",
    "gated",
    "role",
    "license_from",
    "repo_license",
    "license_url",
    "license_path",
    "license_evidence",
    "head_status",
    "head_checked_at",
    "header_precision",
    "header_params",
    "repo_precision",
    "repo_precision_source",
    "optional",  # #81: 任意の部品(inactive / switch_off)
    "optional_replaces",
}
TEMPLATE = {
    "name",
    "title",
    "category",
    "min_comfyui_version",
    "open_source",
    "requires_custom_nodes",
    "usage",
    "date",
    "size_bytes",
    "username",
}
MEASUREMENT = {
    "model_id",
    "gpu_name",
    "gpu_vram_gb",
    "peak_vram_gb",
    "peak_ram_gb",
    "seconds",
    "settings",
    "driver",
    "comfyui_version",
    "offload",
    "source",
    "date",
    "note",
    "outcome",
    "standard",
    "vram_metric",
    "origin",  # 以下 #34(ほかの利用者の実測のまとめ)
    "n_submissions",
    "consistent",
    "n_used",  # 以下 2026-10-22(llmfit に合わせた。件数)
    "n_oom",
    "gpu_backend",  # 0.9.0(測った GPU の backend)
    "gpu_pci_id",  # 0.9.0(測った GPU の PCI の型番 ID)
    "files_fp",  # #90(測ったときの一覧のファイルの指紋)
    "settings_override",  # #116(テンプレートの設定を変えて測ったか)
    "gpu_known",  # #90(以下はほかの利用者のまとめの行)
    "n_sources",
    "source_ids",  # #95: 出どころの ID(count_sources が和集合で数える)
    "n_error",
    "runs_evidence",
}
MEASUREMENT_SETTINGS = {"width", "height", "frames", "seconds", "steps", "precision", "batch"}


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("cache")


def call(args: list[str], cache, code: int = 0) -> dict:
    r = run_cli([*args, "--json"], cache)
    assert r.returncode == code, r.stderr.decode("utf-8", "replace")
    assert r.stderr == b""  # 注意や途中経過も出さない
    text = r.stdout.decode("utf-8")  # 厳密に UTF-8
    d = json.loads(text)  # stdout は JSON だけ(前後に何も混ざらない)
    assert isinstance(d, dict) and set(d) >= ENVELOPE
    return d


def check_system(d: dict) -> None:
    s = d["system"]
    assert set(s) == SYSTEM
    for g in s["gpus"]:
        assert set(g) == GPU
    assert s["best_gpu"] is None or set(s["best_gpu"]) == GPU
    assert "vram" in s["overrides"] and "ram" in s["overrides"]
    assert s["best_gpu"]["vram_total_gb"] == 8 and s["ram_total_gb"] == 32
    assert s["platform_verified"] is False and s["platform_verified_by"] is None  # GPU が仮定(#108)


def check_fit(item: dict) -> None:
    assert set(item) == FIT
    check_estimate(item["estimate"])
    check_gate(item["gate"])
    e, g = item["estimate"], item["gate"]
    assert item["fit_level"] == e["fit"] and item["run_mode"] == e["run_mode"]
    assert item["fit_label"] == {"perfect": "Perfect", "good": "Good", "marginal": "Marginal"}.get(
        item["fit_level"], "Too tight"
    )
    assert item["run_mode_label"] in ("GPU", "GPU~", "GPU+RAM", "CPU", "-", "?")
    assert item["estimate_confidence"] == e["basis"] and item["notes"] == e["reasons"]
    assert item["auto"] == g["auto"] and item["held_reasons"] == g["held_by"]
    assert set(item["score_components"]) == SCORE_COMPONENTS
    assert 0 <= item["score"] <= 100 and item["score"] == item["score_components"]["memory_fit"]
    assert 0 <= item["popularity"] <= 100
    if item["fit_level"] in ("perfect", "good", "marginal"):
        assert item["score"] >= 37.5
    else:
        assert item["score"] == 0
    assert item["memory_kind"] in ("vram", "ram")
    if item["utilization_pct"] is not None and item["memory_available_gb"]:
        # 一番きつい資源の使用率(GPU+RAM・メモリ不足の行はメインメモリのほうが大きいことがある)
        pct = 100 * item["memory_required_gb"] / item["memory_available_gb"]
        assert item["utilization_pct"] >= pct - 0.2  # 動かし方ごとの値は tests/test_scoring.py
    assert isinstance(item["licenses"], list) and item["files_count"] >= 0
    assert item["ram_required_gb"] == (None if e["unknown_size"] else e["ram_gb"])
    assert item["use"] in ("gen", "edit", "i2v", "lipsync", "motion", "voice", "upscale", "tool")
    assert item["group_key"].startswith(f"{item['modality']}:{item['content']}:")
    assert item["variants"] and all(set(v) == VARIANT for v in item["variants"])
    assert item["id"] in {v["workflow_id"] for v in item["variants"]}  # 選んだ精度も入る
    assert all(set(w) == WORKFLOW and w["id"] != item["id"] for w in item["workflows"])


def check_order(models: list[dict], by_type: bool = False) -> None:
    """動く → 判定できない → 動かない → auto → 段階(Marginal の中は動かし方の速い順)→ 公式 → 人気 → Score
    → ID の順(by_type なら種類の順に分けたうえで。docs/scoring.md「並び」)。"""
    order = ["image", "video", "music", "sfx", "tts", "3d"]
    levels = ["perfect", "good", "marginal", "too_tight", "unknown"]
    modes = ["GPU", "GPU~", "GPU+RAM", "CPU"]
    keys = [
        (
            order.index(m["modality"]) if by_type else 0,
            {"unknown": 1, "too_tight": 2}.get(m["fit_level"], 0),
            not m["auto"],
            0
            if m["fit_level"] in ("perfect", "good") and m["run_mode"] == "gpu"
            else levels.index(m["fit_level"]),
            modes.index(m["run_mode_label"]) if m["fit_level"] == "marginal" else 0,
            m["community"],  # 利用者のテンプレートは同じ段階の中で公式の後
            -m["popularity"],
            -m["score"],
            m["id"],
        )
        for m in models
    ]
    assert keys == sorted(keys)


def check_list_order(models: list[dict], sort: str = "type") -> None:
    """fit / search の既定(--sort type): 種類 → 段階(判定できないものは too_tight の前)→ 同じ段階の中で公式
    → 人気 → Score → ID。自動のおすすめを先にしない。"""
    order = ["image", "video", "music", "sfx", "tts", "3d"]
    levels = ["perfect", "good", "marginal", "unknown", "too_tight"]
    keys = [
        (
            order.index(m["modality"]),
            levels.index(m["fit_level"]),
            m["community"],
            -m["popularity"],
            -m["score"],
            m["id"],
        )
        for m in models
    ]
    assert keys == sorted(keys)


def check_counts(d: dict) -> None:
    """matched + hidden の合計 = 調べた項目の数(search 以外は一覧の全部)。"""
    assert set(d["hidden"]) <= HIDDEN_KEYS and all(v > 0 for v in d["hidden"].values())
    assert all(set(g) == GROUP for g in d["groups"])
    assert sum(g["matched"] for g in d["groups"]) == d["matched"]
    assert sum(g["shown"] for g in d["groups"]) == len(d["models"])
    if d["command"] != "search":
        assert d["matched"] + sum(d["hidden"].values()) == d["database"]["models"]


def check_estimate(e: dict) -> None:
    assert set(e) == ESTIMATE
    assert e["basis"] in ("measured", "estimated")
    assert e["basis_detail"] in ("measured", "calibrated", "estimated")
    assert (e["basis"] == "measured") == (e["basis_detail"] == "measured")
    assert e["fit"] in ("perfect", "good", "marginal", "too_tight")
    assert e["run_mode"] in ("gpu", "offload", "cpu", "none", "unknown")
    if e["limits"] is not None:
        assert set(e["limits"]) >= LIMITS
        assert set(e["limits"]) - LIMITS <= {"max_side_px", "max_frames", "max_seconds"}


def check_gate(g: dict) -> None:
    assert set(g) == GATE
    assert [c["id"] for c in g["checks"]] in (CHECK_IDS, APP_RUNTIME_CHECK_IDS)
    assert all(set(c) >= {"id", "passed", "detail"} for c in g["checks"])


def test_system(cache):
    d = call(["system", "--vram", "8", "--ram", "32"], cache)
    assert d["command"] == "system" and set(d) == ENVELOPE | {"system", "database"}
    check_system(d)
    # 一覧の新しさ(Issue #18。docs/schema.md「Data status」)
    assert set(d["database"]) == DATABASE | {"status"}
    assert set(d["database"]["status"]) == {
        "source",
        "generated_at",
        "heads_checked_oldest",
        "heads_checked_newest",
        "max_head_age_days",
        "expired",
        "expires_at",
        "models",
        "auto_count",
        "auto_count_if_fresh",
        "checked_at",
    }


def test_recommend(cache):
    d = call(["recommend", *HW, "-n", "5"], cache)
    assert set(d) == LIST_KEYS
    check_system(d)
    check_counts(d)
    assert set(d["database"]) == DATABASE and d["database"]["origin"] == "bundled"
    assert set(d["filters"]) == RECOMMEND_FILTERS
    # -m も --task も無ければ種類ごとの節: 各 5 件まで、limit は無し
    assert (
        d["filters"]["content"] == "sfw" and d["filters"]["per_type"] == 5 and d["filters"]["limit"] is None
    )
    assert d["filters"]["min_fit"] == "marginal" and d["filters"]["license"] is None
    assert [g["modality"] for g in d["groups"]] == ["image", "video", "music", "sfx", "tts", "3d"]
    assert all(g["shown"] == min(5, g["matched"]) for g in d["groups"])
    assert d["hidden"]["uncensored"] == bundled_uncensored() and d["hidden"]["same_model"]
    for m in d["models"]:
        check_fit(m)
        assert m["fit_level"] != "too_tight" and m["content"] == "sfw"
    check_order(d["models"], by_type=True)
    assert d["models"][0]["auto"]  # 自動でおすすめに出せるものが先
    assert len({m["group_key"] for m in d["models"]}) == len(d["models"])  # 1 行 = 1 モデル
    d = call(["recommend", *HW, "-m", "image,video", "-n", "500", "--content", "all", "--limits"], cache)
    check_counts(d)
    assert d["filters"]["modality"] == ["image", "video"] and d["filters"]["limit"] == 500
    assert d["filters"]["per_type"] is None and "uncensored" not in d["hidden"]
    assert {m["modality"] for m in d["models"]} == {"image", "video"}
    assert any(m["content"] == "uncensored" for m in d["models"])
    assert any(m["estimate"]["limits"] for m in d["models"])
    for m in d["models"]:
        check_fit(m)
    check_order(d["models"])
    assert len(d["models"]) == d["matched"]


def test_auto_only_counts_held(cache):
    every = call(["recommend", *HW, "-m", "image", "-n", "500"], cache)
    auto = call(["recommend", *HW, "-m", "image", "-n", "500", "--auto-only"], cache)
    check_counts(auto)
    assert all(m["auto"] for m in auto["models"]) and auto["hidden"]["held"]
    # 保留にしたものは held に数える(まとめる前に外すので、ほかの理由の数と合わせて全部になる)
    assert auto["matched"] < every["matched"]


def test_fit(cache):
    d = call(["fit", *HW, "--fit", "all", "--include-unknown-license", "--include-tools"], cache)
    assert set(d) == LIST_KEYS
    check_counts(d)
    assert set(d["filters"]) == LISTING_FILTERS | {"include_tools"} and d["filters"]["min_fit"] is None
    assert d["filters"]["fit"] == "all" and d["filters"]["sort"] == "type"
    assert d["filters"]["include_unknown_license"] is True
    db = load_db(use_cache=False)
    sfw = [m for m in db.models if m.content == "sfw"]
    idx = build_index(db.models)
    sfw_models = {idx.key_of[m.id] for m in sfw}
    assert d["matched"] == len(d["models"]) == len(sfw_models)  # 1 行 = 1 モデル
    assert {m["model_name"] for m in d["models"]} == {
        idx.name_of[k] for k in sfw_models
    }  # 表示名は重ならない
    assert d["hidden"] == {"uncensored": len(db.models) - len(sfw), "same_model": len(sfw) - d["matched"]}
    for m in d["models"]:
        check_fit(m)
    check_list_order(d["models"])
    every = call(["fit", *HW, "--content", "all", "--all-workflows", "--all"], cache)
    check_counts(every)
    assert len(every["models"]) == every["database"]["models"] and every["hidden"] == {}


def test_task_and_types(cache):
    d = call(["recommend", *HW, "-m", "image", "--task", "edit", "-n", "500"], cache)
    check_counts(d)
    edit = {"i2i", "inpaint", "outpaint", "relight", "control", "v2v", "a2a"}
    assert d["models"] and all(set(m["tasks"]) & edit and m["use"] == "edit" for m in d["models"])
    assert d["hidden"]["task"] and d["filters"]["task"] == ["edit"]
    t = call(["types", "--no-cache"], cache)
    assert [x["modality"] for x in t["types"]] == ["image", "video", "music", "sfx", "tts", "3d"]
    assert all(set(x) == {"modality", "label", "label_ja", "models", "workflows", "uses"} for x in t["types"])
    assert all(x["models"] <= x["workflows"] for x in t["types"])
    assert {u["use"] for u in t["uses"]} == {
        "gen",
        "edit",
        "i2v",
        "lipsync",
        "motion",
        "voice",
        "upscale",
        "tool",
    }
    r = run_cli(["recommend", *HW, "--task", "dance"], cache)
    assert r.returncode == 2 and "task" in r.stderr.decode("utf-8")
    r = run_cli(["recommend", *HW, "-m", "image,picture"], cache)
    assert r.returncode == 2


def test_min_fit_and_license(cache):
    good = call(["fit", *HW, "--min-fit", "good"], cache)
    check_counts(good)
    assert good["models"] and all(m["fit_level"] in ("perfect", "good") for m in good["models"])
    assert good["hidden"]["too_tight"] and good["hidden"]["below_min_fit"]
    tight = call(["recommend", *HW, "--min-fit", "too_tight", "-m", "image", "-n", "500"], cache)
    assert any(m["fit_level"] == "too_tight" for m in tight["models"])
    assert all(m["estimate"]["unknown_size"] is False for m in tight["models"])  # 大きさ不明は出さない
    lic = call(["fit", *HW, "--license", "Apache-2.0, MIT", "--all-workflows"], cache)
    assert lic["filters"]["license"] == ["apache-2.0", "mit"] and lic["hidden"]["license"]
    assert lic["models"] and all(set(m["licenses"]) <= {"apache-2.0", "mit"} for m in lic["models"])
    every = call(["fit", *HW, "--all-workflows", "--include-unknown-license"], cache)
    unknown = [m for m in every["models"] if None in m["licenses"]]
    assert unknown and not {m["id"] for m in unknown} & {m["id"] for m in lic["models"]}  # 不明は合わない
    r = run_cli(["fit", *HW, "--license", " , "], cache)
    assert r.returncode == 2


def test_search(cache):
    d = call(["search", "FLUX", *HW, "--all-workflows"], cache)
    assert d["command"] == "search" and set(d) == LIST_KEYS
    check_counts(d)
    assert set(d["filters"]) == SEARCH_FILTERS and d["filters"]["query"] == "FLUX"
    assert d["models"] and all("flux" in (m["id"] + m["name"]).lower() for m in d["models"])
    assert all(m["content"] == "sfw" for m in d["models"]) and d["hidden"].get("uncensored")
    for m in d["models"]:
        check_fit(m)
    check_list_order(d["models"])
    # 無検閲を出すと、隠していた数だけ増える(ライセンスの分からないものも出して比べる。無検閲には
    # ライセンスの分からないものがあり、--content all でもそれは隠れるため。#19 の毎週の更新で見つかった)
    unk = ["--all-workflows", "--include-unknown-license"]
    sfw = call(["search", "flux", *HW, *unk], cache)
    every = call(["search", "flux", *HW, "--content", "all", *unk], cache)
    assert len(every["models"]) == len(sfw["models"]) + sfw["hidden"]["uncensored"]
    grouped = call(["search", "flux", *HW], cache)
    assert grouped["matched"] + grouped["hidden"]["same_model"] == len(d["models"])
    none = call(["search", "no_such_model_xyz", *HW], cache)  # 0 件でも終了コード 0
    assert none["models"] == [] and none["matched"] == 0 and none["hidden"] == {}


def test_info(cache):
    d = call(["info", "image_sdxl_simple", *HW], cache)
    assert set(d) == ENVELOPE | {
        "system",
        "database",
        "model",
        "estimate",
        "gate",
        "measurements",
        "group_key",
        "model_name",
        "community",
        "same_model",
    }
    assert d["same_model"] and all(set(w) == WORKFLOW for w in d["same_model"])  # SDXL base は 4 項目
    m = d["model"]
    assert set(m) == MODEL_ENTRY
    assert all(set(f) == MODEL_FILE for f in m["files"])
    assert m["template"] is None or set(m["template"]) == TEMPLATE
    check_estimate(d["estimate"])
    check_gate(d["gate"])
    assert d["measurements"], "image_sdxl_simple には実測がある"
    for x in d["measurements"]:
        assert set(x) == MEASUREMENT and set(x["settings"]) == MEASUREMENT_SETTINGS


def test_info_uncensored_by_id(cache):
    fit = call(["fit", *HW, "--content", "uncensored", "-n", "1"], cache)
    mid = fit["models"][0]["id"]
    d = call(["info", mid, *HW], cache)  # 既定の sfw でも、ID を指定すれば出る
    assert d["model"]["id"] == mid and d["model"]["content"] == "uncensored"


def test_gate(cache):
    d = call(["gate", "--no-cache"], cache)
    assert set(d) == ENVELOPE | {"database", "policy", "filters", "summary", "models"}
    policy = {"license_allowlist", "comfyui_version", "allow_uncensored", "max_head_age_days"}
    assert set(d["policy"]) == policy
    assert set(d["filters"]) == {"modality", "only_auto", "only_held"}
    assert set(d["summary"]) == {"models", "auto", "held", "failing", "first_failing"}
    assert len(d["models"]) == d["summary"]["models"] == d["database"]["models"]
    for m in d["models"]:
        assert set(m) == GATE | {"id"}


def test_error_is_json(cache):
    d = call(["info", "no_such_model_xyz", *HW], cache, code=1)
    assert set(d) == ENVELOPE | {"error"} and "no_such_model_xyz" in d["error"]


def test_info_ambiguous_is_error_with_candidates(cache):
    d = call(["info", "sdxl", *HW], cache, code=1)
    assert set(d) == ENVELOPE | {"error", "candidates"}
    ids = [c["id"] for c in d["candidates"]]
    assert "image_sdxl_simple" in ids and len(ids) >= 2
    assert all(set(c) == {"id", "name", "content"} and c["content"] == "sfw" for c in d["candidates"])
    every = call(["info", "sdxl", *HW, "--content", "all"], cache, code=1)
    assert any(c["content"] == "uncensored" for c in every["candidates"])
    # 無検閲にしか合わないものは黙って選ばない
    d = call(["info", "Qwen-Image-2.1", *HW], cache, code=1)
    assert set(d) == ENVELOPE | {"error"} and "--uncensored" in d["error"]
