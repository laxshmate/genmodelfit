# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""テンプレートの既定の値で読み込まれない部品(任意の部品。#81、docs/scoring.md「既定で切ってある部品」、
docs/sources.md「任意の部品」、docs/accuracy.md「ファイルの数え方」)。

つながりの規則はフィクスチャ(小さな画面用の JSON)で、値は同梱の一覧で確かめる。ネットにも GPU にも
触らない。"""

import copy
import importlib

import pytest

import genmodelfit
from genmodelfit import appinfo, i18n
from genmodelfit.db import DatabaseError, check_entry, load
from genmodelfit.models import ModelEntry, ModelFile
from genmodelfit.optional import INACTIVE, SWITCH_OFF, optional_files
from genmodelfit.update import set_optional_files

E = importlib.import_module("genmodelfit.estimate")  # genmodelfit.estimate は関数の名前でもある
LEVELS = ("perfect", "good", "marginal", "too_tight")


# --- フィクスチャを作る道具 --------------------------------------------------------------------------


def node(nid, typ, inputs=(), outputs=(), files=(), mode=0, widgets=None):
    """inputs: (名前, リンク ID | None)、outputs: 出力ごとのリンク ID の並び。"""
    n = {
        "id": nid,
        "type": typ,
        "mode": mode,
        "inputs": [{"name": name, "link": link} for name, link in inputs],
        "outputs": [{"name": f"out{k}", "links": list(ls) or None} for k, ls in enumerate(outputs)],
        "properties": {"models": [{"name": f} for f in files]} if files else {},
    }
    if widgets is not None:
        n["widgets_values"] = widgets
    return n


def link(lid, origin, slot, target, tslot):
    return [lid, origin, slot, target, tslot, "*"]


def sub_link(lid, origin, slot, target, tslot):
    return {"id": lid, "origin_id": origin, "origin_slot": slot, "target_id": target, "target_slot": tslot}


def classes(wf) -> dict[str, str]:
    return {k: v.kind for k, v in optional_files(wf).items()}


def switch_graph(switch_type="ComfySwitchNode", value=False, preview=False, on_false_link=True):
    """上の階層: 実体 10(中身 S)→ SaveImage 20。S の中: CLIPLoader 1(pe.safetensors)→ TextGenerate 2 →
    スイッチ 3 の on_true、on_false はサブグラフの入力 0(prompt)、スイッチの値は実体の入力欄。preview なら
    TextGenerate の出力に PreviewAny 4(出力のノード)もつなぐ。"""
    inner_nodes = [
        node(1, "CLIPLoader", outputs=[[101]], files=["pe.safetensors"]),
        node(2, "TextGenerate", inputs=[("clip", 101)], outputs=[[102, 104] if preview else [102]]),
        node(
            3,
            switch_type,
            inputs=[("switch", 105), ("on_false", 103 if on_false_link else None), ("on_true", 102)],
            outputs=[[106]],
        ),
    ]
    links = [
        sub_link(101, 1, 0, 2, 0),
        sub_link(102, 2, 0, 3, 2),
        sub_link(105, -10, 1, 3, 0),
        sub_link(106, 3, 0, -20, 0),
    ]
    if on_false_link:
        links.append(sub_link(103, -10, 0, 3, 1))
    if preview:
        inner_nodes.append(node(4, "PreviewAny", inputs=[("source", 104)]))
        links.append(sub_link(104, 2, 0, 4, 0))
    sg = {
        "id": "S",
        "inputs": [{"name": "prompt", "type": "STRING"}, {"name": "switch", "type": "BOOLEAN"}],
        "outputs": [{"name": "text", "type": "STRING", "linkIds": [106]}],
        "nodes": inner_nodes,
        "links": links,
    }
    inst = node(
        10, "S", inputs=[("prompt", None), ("switch", None)], outputs=[[201]], widgets=["a cat", value]
    )
    top = [
        inst,
        node(11, "UNETLoader", outputs=[[202]], files=["main.safetensors"]),
        node(12, "KSampler", inputs=[("model", 202), ("positive", 201)], outputs=[[203]]),
        node(20, "SaveImage", inputs=[("images", 203)]),
    ]
    return {
        "nodes": top,
        "links": [link(201, 10, 0, 12, 1), link(202, 11, 0, 12, 0), link(203, 12, 0, 20, 0)],
        "definitions": {"subgraphs": [sg]},
    }


# --- つながりの規則 ---------------------------------------------------------------------------------


def test_unselected_switch_branch_is_switch_off():
    assert classes(switch_graph()) == {"pe.safetensors": SWITCH_OFF}


def test_selected_branch_is_counted():
    assert classes(switch_graph(value=True)) == {}


def test_preview_any_inside_subgraph_on_a_lazy_branch_counts_as_reachable():
    # ComfyUI は出力のノードを全部実行する(execution.py:1183-1185@426f5ee)。
    # PreviewAny が TextGenerate を要求する
    assert classes(switch_graph(preview=True)) == {}


def test_switch_value_not_fixed_follows_both_sides():
    wf = switch_graph()
    wf["nodes"][0]["widgets_values"] = ["a cat", "maybe"]  # 真偽でない値 = 分からない
    assert classes(wf) == {}


def test_soft_switch_with_selected_side_unconnected_uses_the_other_side():
    # switch=False で on_false がつながっていない: Soft Switch は on_true を要求する(nodes_logic.py:178-188)
    assert classes(switch_graph("ComfySoftSwitchNode", on_false_link=False)) == {}
    # If/Else Switch は要求しない(何も返さない)
    assert classes(switch_graph("ComfySwitchNode", on_false_link=False)) == {"pe.safetensors": SWITCH_OFF}
    # 選んだ側がつながっていれば Soft Switch も選んだ側だけ
    assert classes(switch_graph("ComfySoftSwitchNode")) == {"pe.safetensors": SWITCH_OFF}


def _nested(outer_mode: int, inner_mode: int):
    """上の階層の実体 30(中身 O、mode outer_mode)の中に、入れ子の実体 31(中身 I、mode inner_mode)。
    I の中: CheckpointLoaderSimple 1(ckpt.safetensors)→ VAEDecode 2 → SaveImage 3。"""
    inner = {
        "id": "I",
        "inputs": [],
        "outputs": [],
        "nodes": [
            node(1, "CheckpointLoaderSimple", outputs=[[1]], files=["ckpt.safetensors"]),
            node(2, "VAEDecode", inputs=[("vae", 1)], outputs=[[2]]),
            node(3, "SaveImage", inputs=[("images", 2)]),
        ],
        "links": [sub_link(1, 1, 0, 2, 0), sub_link(2, 2, 0, 3, 0)],
    }
    outer = {"id": "O", "inputs": [], "outputs": [], "nodes": [node(31, "I", mode=inner_mode)], "links": []}
    return {
        "nodes": [
            node(30, "O", mode=outer_mode),
            node(40, "UNETLoader", outputs=[[1]], files=["main.safetensors"]),
            node(41, "SaveLatent", inputs=[("samples", 1)]),
        ],
        "links": [link(1, 40, 0, 41, 0)],
        "definitions": {"subgraphs": [outer, inner]},
    }


def test_nested_bypassed_instance_contents_are_active():
    # 画面は一番上の階層の実体の mode だけで中身を外す。入れ子の実体の素通しは中身に効かない
    assert classes(_nested(0, 4)) == {}
    assert classes(_nested(0, 2)) == {}
    # 一番上の実体が無効・素通しなら中身はすべて外れる
    assert classes(_nested(4, 0)) == {"ckpt.safetensors": INACTIVE}
    assert classes(_nested(2, 0)) == {"ckpt.safetensors": INACTIVE}


def _chain(lora_mode: int, orphan: bool = False):
    nodes = [
        node(1, "UNETLoader", outputs=[[1]], files=["main.safetensors"]),
        node(
            2,
            "LoraLoaderModelOnly",
            inputs=[("model", 1)],
            outputs=[[2]],
            files=["lora.safetensors"],
            mode=lora_mode,
        ),
        node(3, "KSampler", inputs=[("model", 2)], outputs=[[3]]),
        node(4, "SaveImage", inputs=[("images", 3)]),
    ]
    if orphan:
        nodes.append(node(5, "CLIPVisionLoader", outputs=[[]], files=["clip_vision.safetensors"]))
    return {
        "nodes": nodes,
        "links": [link(1, 1, 0, 2, 0), link(2, 2, 0, 3, 0), link(3, 3, 0, 4, 0)],
    }


def test_bypassed_node_own_files_are_not_counted_but_its_inputs_are():
    assert classes(_chain(4)) == {"lora.safetensors": INACTIVE}
    assert classes(_chain(0)) == {}


def test_muted_node_cuts_the_chain():
    assert classes(_chain(2)) == {"lora.safetensors": INACTIVE, "main.safetensors": INACTIVE}


def test_unconnected_loader_is_inactive():
    assert classes(_chain(0, orphan=True)) == {"clip_vision.safetensors": INACTIVE}


def test_unconnected_custom_node_is_a_starting_point():
    # 表に無い拡張の出力のノード(VHS_VideoCombine など)の代わりに、出力がつながらないノードから始める
    wf = {
        "nodes": [
            node(1, "UNETLoader", outputs=[[1]], files=["main.safetensors"]),
            node(2, "VHS_VideoCombine", inputs=[("images", 1)], outputs=[[]]),
        ],
        "links": [link(1, 1, 0, 2, 0)],
    }
    assert classes(wf) == {}


def test_broken_subgraph_exit_counts_everything_inside():
    wf = switch_graph()
    sg = wf["definitions"]["subgraphs"][0]
    sg["links"] = [x for x in sg["links"] if x["target_id"] != -20]  # 出口のリンクが無い
    assert classes(wf) == {}


def test_no_starting_point_or_virtual_links_split_nothing():
    assert optional_files({"nodes": [node(1, "UNETLoader", outputs=[[1]], files=["a.safetensors"])]}) == {}
    wf = _chain(4)
    wf["nodes"].append(node(9, "GetNode", outputs=[[]]))
    assert optional_files(wf) == {}
    assert optional_files({"nodes": "x"}) == {}
    assert optional_files(None) == {}


def test_replacing_switch_records_the_default_part():
    """switch=False で本体 A、True で本体 B を選ぶ: B は switch_off で、切り替えると A を読まなくなる。"""
    wf = {
        "nodes": [
            node(1, "UNETLoader", outputs=[[1]], files=["a.safetensors"]),
            node(2, "UNETLoader", outputs=[[2]], files=["b.safetensors"]),
            node(
                3,
                "ComfySwitchNode",
                inputs=[("switch", None), ("on_false", 1), ("on_true", 2)],
                outputs=[[3]],
                widgets=[False],
            ),
            node(4, "KSampler", inputs=[("model", 3)], outputs=[[4]]),
            node(5, "SaveImage", inputs=[("images", 4)]),
        ],
        "links": [link(1, 1, 0, 3, 1), link(2, 2, 0, 3, 2), link(3, 3, 0, 4, 0), link(4, 4, 0, 5, 0)],
    }
    got = optional_files(wf)
    assert got["b.safetensors"].kind == SWITCH_OFF and got["b.safetensors"].replaces == ["a.safetensors"]
    assert classes(switch_graph()) == {"pe.safetensors": SWITCH_OFF}
    assert optional_files(switch_graph())["pe.safetensors"].replaces == []


def test_set_optional_files_rebuilds_each_time():
    e = ModelEntry(
        id="t",
        name="t",
        modality="image",
        files=[ModelFile(name="main.safetensors"), ModelFile(name="sub/pe.safetensors", optional="inactive")],
    )
    set_optional_files(e, switch_graph())
    assert [f.optional for f in e.files] == [None, SWITCH_OFF]
    assert e.files[1].optional_replaces == []
    set_optional_files(e, switch_graph(value=True))
    assert [f.optional for f in e.files] == [None, None]
    assert e.files[1].optional_replaces is None


def test_db_types_of_the_new_keys():
    base = {
        "id": "x",
        "name": "x",
        "modality": "image",
        "content": "sfw",
        "files": [{"name": "a", "optional": "switch_off"}],
    }
    check_entry(base, 0)
    bad = copy.deepcopy(base)
    bad["files"][0]["optional_replaces"] = [1]
    with pytest.raises(DatabaseError):
        check_entry(bad, 0)
    bad["files"][0]["optional_replaces"] = None
    bad["files"][0]["optional"] = 3
    with pytest.raises(DatabaseError):
        check_entry(bad, 0)


# --- 同梱の一覧の値 ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db():
    return load(use_cache=False)


def rtx3070(vram=8.0, ram=32, comfyui_version="0.15.0"):
    """既定は動的 VRAM の無い ComfyUI(逃がすときは重みの 0.4 を GPU に残す今までの式)。#81 の数は
    その式で決めたもの。0.15 から画像は動的 VRAM の式で逃がす量が小さく、8 GB では任意の部品の有無で
    段階が変わらない(docs/accuracy.md「動的 VRAM で逃がすとき」)。"""
    s = genmodelfit.system_from(
        [{"name": "NVIDIA GeForce RTX 3070", "vram_gb": vram}], ram_gb=ram, gpu_usable=True, windows=True
    )
    s.comfyui_version = comfyui_version
    return s


def stripped(e):
    """任意の部品の印を外した写し(今までの数え方)。"""
    e2 = copy.deepcopy(e)
    for f in e2.files:
        f.optional = None
        f.optional_replaces = None
    return e2


def _est(db, mid, system, entry=None):
    e = entry or db.get(mid)
    return E.estimate(e, system, None, db.measurements_for(mid))


@pytest.mark.parametrize(
    ("mid", "offload"),
    [
        ("image_qwen_image_2_1_t2i", 4.96),
        ("image_qwen_image_2_1_turbo_t2i", 4.96),
        ("image_qwen_image_2_1_image_edit", 5.14),
        ("image_qwen_image_2_1_turbo_image_edit", 5.14),
    ],
)
def test_issue_81_qwen_image_2_1_on_8gb(db, mid, offload):
    e = db.get(mid)
    pe = [f for f in e.files if "_pe_" in f.name]
    assert len(pe) == 1 and pe[0].optional == SWITCH_OFF
    est = _est(db, mid, rtx3070())
    assert (est.fit, est.run_mode) == ("good", "offload")
    assert est.vram_offload_gb == pytest.approx(offload, abs=0.01)
    # 指示の書き直しの部品は検証で要るのでディスクには数える
    assert est.disk_gb == pytest.approx(sum(f.size_bytes for f in e.files) / E.GIB, abs=0.01)
    # 上限(今までの値)と、段階が変わるので 1 文
    old = _est(db, mid, rtx3070(), stripped(e))
    assert (old.fit, old.vram_offload_gb) == ("too_tight", pytest.approx(8.48, abs=0.01))
    assert est.fit_all_options == "too_tight"
    assert est.vram_gb_all_options == pytest.approx(old.vram_offload_gb, abs=0.01)
    assert [p["kind"] for p in est.optional_parts] == ["add"]
    assert any(pe[0].name in r and "8.5 GB" in r for r in est.reasons)


def test_note_only_when_the_level_changes(db):
    est = _est(db, "image_qwen_image_2_1_t2i", rtx3070(24.0, 64))
    assert est.fit_all_options == est.fit
    assert not any(p["name"] in r for p in est.optional_parts for r in est.reasons)


def test_replace_note_for_pixal3d_trellis2(db):
    with i18n.forced("ja"):
        est = _est(db, "3d_pixal3d_trellis2_image_to_model", rtx3070(6.0))
    (p,) = est.optional_parts
    assert p["kind"] == "replace" and p["replaces"] == ["pixal3d_int8_convrot.safetensors"]
    assert est.fit != est.fit_all_options
    assert any("pixal3d_int8_convrot.safetensors の代わりに読み込みます" in r for r in est.reasons)


def test_flux2_klein_inactive_part(db):
    e = db.get("image_flux2_klein_text_to_image")
    off = [f for f in e.files if f.optional]
    assert [f.optional for f in off] == [INACTIVE]
    est = _est(db, e.id, rtx3070())
    assert est.fit == "good"
    assert _est(db, e.id, rtx3070(), stripped(e)).fit == "too_tight"
    # 無効のノードにしかない部品はディスクにも数えない・上限の値は出さない
    assert est.disk_gb == pytest.approx(
        sum(f.size_bytes for f in e.files if not f.optional) / E.GIB, abs=0.01
    )
    assert est.vram_gb_all_options is None and est.fit_all_options is None
    assert est.optional_parts[0]["optional"] == INACTIVE


@pytest.mark.parametrize("mid", ["video_wan2_2_5B_fun_control", "video_wan2_2_5B_ti2v"])
def test_issue_112_wan22_5b_on_8gb(db, mid):
    est = _est(db, mid, rtx3070())
    assert (est.fit, est.run_mode) == ("good", "offload")
    assert est.vram_offload_gb == pytest.approx(6.08, abs=0.02)


def test_levels_only_go_up_and_stay_monotonic(db):
    """任意の部品のあるモデル × NVIDIA 6〜24 GB × メインメモリ 32 / 64 GB: 段階は今までより下がらず、
    VRAM を増やして下がらない。"""
    models = [e for e in db.models if any(f.optional for f in e.files)]
    assert len(models) >= 50
    for e in models:
        for ram in (32, 64):
            prev = None
            for vram in (6, 8, 12, 16, 24):
                s = genmodelfit.system_from(
                    [{"name": "Test GPU", "vendor": "nvidia", "vram_gb": float(vram)}],
                    ram_gb=ram,
                    gpu_usable=True,
                )
                new = _est(db, e.id, s)
                old = _est(db, e.id, s, stripped(e))
                if new.fit in LEVELS and old.fit in LEVELS:
                    assert LEVELS.index(new.fit) <= LEVELS.index(old.fit), (e.id, vram, ram)
                    assert new.basis_detail == old.basis_detail, (e.id, vram, ram)
                if new.fit in LEVELS:
                    assert prev is None or LEVELS.index(new.fit) <= prev, (e.id, vram, ram)
                    prev = LEVELS.index(new.fit)


def test_download_skips_inactive_parts(db, tmp_path):
    from genmodelfit.models import SystemInfo

    pc = SystemInfo(os="test", cpu="test", cpu_cores=8, ram_total_gb=64, ram_available_gb=64)
    e = db.get("image_flux2_klein_text_to_image")
    inactive = [f.name for f in e.files if f.optional == INACTIVE]
    plan = genmodelfit.plan_download(e.id, db=db, system_info=pc, output_dir=tmp_path, verify_existing=False)
    assert plan.skipped_optional == inactive
    assert not set(inactive) & {f.name for f in plan.files}
    assert any(inactive[0] in w for w in plan.warnings)
    full = genmodelfit.plan_download(
        e.id, db=db, system_info=pc, output_dir=tmp_path, verify_existing=False, with_optional=True
    )
    assert full.skipped_optional == [] and set(inactive) <= {f.name for f in full.files}
    # スイッチで切ってある部品は検証で要るので落とす
    q = db.get("image_qwen_image_2_1_t2i")
    qp = genmodelfit.plan_download(q.id, db=db, system_info=pc, output_dir=tmp_path, verify_existing=False)
    assert qp.skipped_optional == [] and {f.name for f in q.files} == {f.name for f in qp.files}


def test_language_material_ignores_optional_parts(db):
    # 指示の書き直しの部品(qwen3.5)は表に当たるが、既定で読み込まないので材料にしない
    e = db.get("image_qwen_image_2_1_t2i")
    assert appinfo.prompt_kind(e) is None
    assert appinfo.prompt_kind(stripped(e)) == "multi"
    assert e.languages == {"prompt": ["en"]}
    assert db.get("image_qwen_image_2_1_turbo_t2i").languages == {"prompt": ["en"]}
    assert db.get("image_qwen_image_2_1_turbo_image_edit").languages == {"prompt": ["en"]}
    # 編集だけ、作者の実機で日本語の指示を確かめた(Issue #81)。zh はカードに根拠が無い
    assert db.get("image_qwen_image_2_1_image_edit").languages == {"prompt": ["en", "ja"]}


def test_bundled_marks_match_the_count(db):
    n = sum(1 for e in db.models if any(f.optional for f in e.files))
    assert db.sources["sampler_stages"]["optional_parts"] == n
    for e in db.models:
        for f in e.files:
            assert f.optional in (None, INACTIVE, SWITCH_OFF)
            if f.optional != SWITCH_OFF:
                assert f.optional_replaces is None
            if e.source != "comfyui-template":
                assert f.optional is None
