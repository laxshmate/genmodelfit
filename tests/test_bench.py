"""bench と workflow の試験。ComfyUI は偽の HTTP サーバーで置き換える(実物は要らない)。"""

from __future__ import annotations

import itertools
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import psutil
import pytest

from genmodelfit import bench, cli
from genmodelfit.bench import BenchError, BenchOptions, ComfyClient, offload_from_logs
from genmodelfit.db import cache_dir, load_measurements
from genmodelfit.estimate import pick_measurement
from genmodelfit.models import Measurement, ModelEntry, Settings
from genmodelfit.workflow import (
    WorkflowError,
    apply_settings,
    check_inputs,
    effective_settings,
    is_api_format,
    map_widgets,
    reachable_nodes,
    size_input,
    ui_to_api,
    upload_inputs,
)

GIB = 1024**3


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GENMODELFIT_CACHE_DIR", str(tmp_path / "cache"))
    # 実機の nvidia-smi・プロセス探しには触らない(偽の GPU が 1 枚あり、空いていることにする)
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: None)
    monkeypatch.setattr(bench, "smi_gpus", lambda: [dict(GPU0)])
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (1024.0, 0.0))
    monkeypatch.setattr(bench, "find_server_process", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)


GPU0 = {"index": 0, "uuid": "GPU-aaaa", "name": "NVIDIA GeForce RTX 3070", "driver": "566.03"}
_REAL_FIND_SERVER_PROCESS = bench.find_server_process


# --- /object_info の抜粋 ---------------------------------------------------------------------------


def _w(typ, **opts):
    return [typ, opts] if opts else [typ]


OBJECT_INFO: dict[str, Any] = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["model.safetensors"]]}},
        "input_order": {"required": ["ckpt_name"]},
        "output_node": False,
    },
    "UNETLoader": {
        "input": {"required": {"unet_name": [["unet.safetensors"]], "weight_dtype": [["default", "fp8"]]}},
        "input_order": {"required": ["unet_name", "weight_dtype"]},
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": _w("STRING", multiline=True), "clip": _w("CLIP")}},
        "input_order": {"required": ["text", "clip"]},
    },
    "EmptyLatentImage": {
        "input": {"required": {"width": _w("INT"), "height": _w("INT"), "batch_size": _w("INT")}},
        "input_order": {"required": ["width", "height", "batch_size"]},
    },
    "EmptyHunyuanLatentVideo": {
        "input": {
            "required": {
                "width": _w("INT"),
                "height": _w("INT"),
                "length": _w("INT"),
                "batch_size": _w("INT"),
            }
        },
    },
    "EmptyAceStep1.5LatentAudio": {
        "input": {"required": {"seconds": _w("FLOAT"), "batch_size": _w("INT")}},
    },
    "KSampler": {
        "input": {
            "required": {
                "model": _w("MODEL"),
                "seed": _w("INT", control_after_generate=True),
                "steps": _w("INT"),
                "cfg": _w("FLOAT"),
                "sampler_name": [["euler", "dpmpp_2m"]],
                "scheduler": [["normal", "karras"]],
                "positive": _w("CONDITIONING"),
                "negative": _w("CONDITIONING"),
                "latent_image": _w("LATENT"),
                "denoise": _w("FLOAT"),
            }
        },
        "input_order": {
            "required": [
                "model",
                "seed",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "positive",
                "negative",
                "latent_image",
                "denoise",
            ]
        },
    },
    "VAEDecode": {"input": {"required": {"samples": _w("LATENT"), "vae": _w("VAE")}}},
    "SaveImage": {
        "input": {"required": {"images": _w("IMAGE"), "filename_prefix": _w("STRING")}},
        "output_node": True,
    },
    "LoadImage": {"input": {"required": {"image": [["a.png"], {"image_upload": True}]}}},
    "SaveAudioAdvanced": {
        "input": {
            "required": {
                "audio": _w("AUDIO"),
                "filename_prefix": _w("STRING"),
                "format": [
                    "COMFY_DYNAMICCOMBO_V3",
                    {
                        "options": [
                            {"key": "flac", "inputs": {"required": {}}},
                            {
                                "key": "mp3",
                                "inputs": {"required": {"quality": ["COMBO", {"options": ["V0"]}]}},
                            },
                        ]
                    },
                ],
            }
        },
        "output_node": True,
    },
}


def node(nid, ntype, widgets=None, inputs=None, outputs=None, mode=0, **extra):
    return {
        "id": nid,
        "type": ntype,
        "mode": mode,
        "widgets_values": widgets if widgets is not None else [],
        "inputs": inputs or [],
        "outputs": outputs or [],
        **extra,
    }


def sdxl_ui() -> dict[str, Any]:
    """画面用の形式の小さな SDXL(Note と Reroute と素通しのノード入り)。"""
    return {
        "nodes": [
            node(1, "CheckpointLoaderSimple", ["model.safetensors"]),
            node(2, "CLIPTextEncode", ["a cat"], [{"name": "clip", "type": "CLIP", "link": 2}]),
            node(3, "CLIPTextEncode", ["blurry"], [{"name": "clip", "type": "CLIP", "link": 3}]),
            node(4, "EmptyLatentImage", [1024, 1024, 1]),
            node(
                5,
                "KSampler",
                [42, "randomize", 25, 7, "dpmpp_2m", "karras", 1],
                [
                    {"name": "model", "type": "MODEL", "link": 9},
                    {"name": "positive", "type": "CONDITIONING", "link": 5},
                    {"name": "negative", "type": "CONDITIONING", "link": 6},
                    {"name": "latent_image", "type": "LATENT", "link": 7},
                ],
            ),
            node(6, "VAEDecode", [], [{"name": "samples", "link": 8}, {"name": "vae", "link": 10}]),
            node(7, "SaveImage", ["sdxl"], [{"name": "images", "link": 11}]),
            node(8, "MarkdownNote", ["# hello"]),
            node(9, "Reroute", [], [{"name": "", "type": "*", "link": 1}], [{"type": "MODEL"}]),
            node(
                10,
                "UNETLoader",
                ["unet.safetensors", "default"],
                [{"name": "model", "type": "MODEL", "link": None}],
                [{"type": "MODEL"}],
                mode=4,
            ),
        ],
        "links": [
            [1, 1, 0, 9, 0, "MODEL"],
            [2, 1, 1, 2, 0, "CLIP"],
            [3, 1, 1, 3, 0, "CLIP"],
            [5, 2, 0, 5, 1, "CONDITIONING"],
            [6, 3, 0, 5, 2, "CONDITIONING"],
            [7, 4, 0, 5, 3, "LATENT"],
            [8, 5, 0, 6, 0, "LATENT"],
            [9, 9, 0, 5, 0, "MODEL"],
            [10, 1, 2, 6, 1, "VAE"],
            [11, 6, 0, 7, 0, "IMAGE"],
        ],
    }


def subgraph_ui() -> dict[str, Any]:
    """Z-Image のテンプレートと同じ形のサブグラフ(幅・高さ・ステップを外に出したもの)。"""
    sg_id = "f2fdebf6-0000"
    inner = [
        node(
            28,
            "UNETLoader",
            ["unet.safetensors", "default"],
            [{"name": "unet_name", "widget": {}, "link": 73}],
        ),
        node(
            27,
            "CLIPTextEncode",
            ["inside prompt"],
            [{"name": "clip", "link": None}, {"name": "text", "link": 34}],
        ),
        node(
            13,
            "EmptyLatentImage",
            [1024, 1024, 1],
            [{"name": "width", "widget": {}, "link": 35}, {"name": "height", "widget": {}, "link": 36}],
        ),
        node(
            3,
            "KSampler",
            [7, "fixed", 8, 1, "euler", "normal", 1],
            [
                {"name": "model", "link": 13},
                {"name": "positive", "link": 30},
                {"name": "negative", "link": 30},
                {"name": "latent_image", "link": 17},
                {"name": "steps", "widget": {}, "link": 72},
            ],
        ),
        node(8, "VAEDecode", [], [{"name": "samples", "link": 14}, {"name": "vae", "link": None}]),
    ]

    def lk(i, o, os_, t, ts):
        return {"id": i, "origin_id": o, "origin_slot": os_, "target_id": t, "target_slot": ts, "type": "*"}

    sg = {
        "id": sg_id,
        "inputs": [{"name": n, "linkIds": [lid]} for n, lid in [("text", 34), ("width", 35), ("height", 36)]]
        + [{"name": "steps", "linkIds": [72]}, {"name": "unet_name", "linkIds": [73]}],
        "outputs": [{"name": "IMAGE", "linkIds": [16]}],
        "nodes": inner,
        "links": [
            lk(13, 28, 0, 3, 0),
            lk(30, 27, 0, 3, 1),
            lk(17, 13, 0, 3, 3),
            lk(14, 3, 0, 8, 0),
            lk(16, 8, 0, -20, 0),
            lk(34, -10, 0, 27, 1),
            lk(35, -10, 1, 13, 0),
            lk(36, -10, 2, 13, 1),
            lk(72, -10, 3, 3, 4),
            lk(73, -10, 4, 28, 0),
        ],
    }
    outer = node(
        57,
        sg_id,
        [],
        [{"name": "text", "widget": {"name": "text"}, "link": 100}],
        [{"name": "IMAGE", "links": [62]}],
        properties={"proxyWidgets": [["13", "width"], ["13", "height"], ["3", "steps"]]},
    )
    prim = node(99, "PrimitiveNode", ["from primitive", "fixed"], [], [{"type": "STRING", "links": [100]}])
    save = node(9, "SaveImage", ["z"], [{"name": "images", "link": 62}])
    return {
        "nodes": [save, outer, prim],
        "links": [[62, 57, 0, 9, 0, "IMAGE"], [100, 99, 0, 57, 0, "STRING"]],
        "definitions": {"subgraphs": [sg]},
    }


# --- 変換 --------------------------------------------------------------------------------------


def test_map_widgets_skips_control_after_generate():
    got = map_widgets(OBJECT_INFO["KSampler"], [42, "randomize", 25, 7, "dpmpp_2m", "karras", 1])
    assert got == {
        "seed": 42,
        "steps": 25,
        "cfg": 7,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 1,
    }
    # 「生成後の動き」が無い古い形でもずれない
    got = map_widgets(OBJECT_INFO["KSampler"], [42, 25, 7, "euler", "normal", 1])
    assert got["steps"] == 25 and got["denoise"] == 1


def test_map_widgets_dynamic_combo_and_extra_values():
    got = map_widgets(OBJECT_INFO["SaveAudioAdvanced"], ["audio/x", "mp3", "V0"])
    assert got == {"filename_prefix": "audio/x", "format": "mp3", "format.quality": "V0"}
    # LoadImage の後ろの「upload」の値は捨てる
    assert map_widgets(OBJECT_INFO["LoadImage"], ["a.png", "image"]) == {"image": "a.png"}


def test_ui_to_api_resolves_links_reroute_bypass_and_notes():
    conv = ui_to_api(sdxl_ui(), OBJECT_INFO)
    wf = conv.workflow
    assert set(wf) == {"1", "2", "3", "4", "5", "6", "7"}  # Note・Reroute・素通しのノードは消える
    assert wf["5"]["inputs"]["model"] == ["1", 0]  # Reroute の先をたどる
    assert wf["5"]["inputs"]["seed"] == 42 and wf["5"]["inputs"]["steps"] == 25
    assert wf["6"]["inputs"]["vae"] == ["1", 2]
    assert wf["2"]["inputs"] == {"text": "a cat", "clip": ["1", 1]}
    assert is_api_format(wf) and not is_api_format(sdxl_ui())


def test_ui_to_api_flattens_subgraph_and_primitive():
    wf = ui_to_api(subgraph_ui(), OBJECT_INFO).workflow
    assert wf["9"]["inputs"]["images"] == ["57:8", 0]  # サブグラフの出力 → 中のノード
    assert wf["57:3"]["inputs"]["latent_image"] == ["57:13", 0]
    assert wf["57:27"]["inputs"]["text"] == "from primitive"  # 外の PrimitiveNode の値が中まで届く
    assert wf["57:13"]["inputs"]["width"] == 1024  # 外につながっていない入力は中の値
    assert wf["57:3"]["inputs"]["steps"] == 8


def test_ui_to_api_proxy_widget_values():
    ui = subgraph_ui()
    ui["nodes"][1]["widgets_values"] = [640, 480, 4]
    wf = ui_to_api(ui, OBJECT_INFO).workflow
    assert (wf["57:13"]["inputs"]["width"], wf["57:13"]["inputs"]["height"]) == (640, 480)
    assert wf["57:3"]["inputs"]["steps"] == 4


def test_ui_to_api_rejects_unknown_nodes_and_no_output():
    ui = sdxl_ui()
    ui["nodes"].append(node(50, "SomeCustomNode", [1]))
    with pytest.raises(WorkflowError, match="SomeCustomNode"):
        ui_to_api(ui, OBJECT_INFO)
    ui = sdxl_ui()
    ui["nodes"] = [n for n in ui["nodes"] if n["type"] != "SaveImage"]
    with pytest.raises(WorkflowError, match="出力"):
        ui_to_api(ui, OBJECT_INFO)
    with pytest.raises(WorkflowError):
        ui_to_api({"foo": 1}, OBJECT_INFO)


def test_check_inputs_reports_missing_model_files():
    wf = ui_to_api(sdxl_ui(), OBJECT_INFO).workflow
    assert check_inputs(wf, OBJECT_INFO) == []
    wf["1"]["inputs"]["ckpt_name"] = "missing.safetensors"
    (msg,) = check_inputs(wf, OBJECT_INFO)
    assert "モデルのファイル" in msg and "missing.safetensors" in msg


# --- 設定の書き換え -----------------------------------------------------------------------------


def test_apply_settings_image_video_audio():
    wf = ui_to_api(sdxl_ui(), OBJECT_INFO).workflow
    new, notes = apply_settings(wf, width=832, height=640, steps=15)
    assert new["4"]["inputs"]["width"] == 832 and new["4"]["inputs"]["height"] == 640
    assert new["5"]["inputs"]["steps"] == 15 and wf["5"]["inputs"]["steps"] == 25  # 元は変えない
    assert notes == []
    s = effective_settings(new, precision="fp16")
    assert (s.width, s.height, s.steps, s.frames, s.seconds, s.precision) == (
        832,
        640,
        15,
        None,
        None,
        "fp16",
    )

    video = {
        "1": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": 832, "height": 480, "length": 33}},
        "2": {"class_type": "KSampler", "inputs": {"steps": 30, "latent_image": ["1", 0]}},
    }
    new, _ = apply_settings(video, width=512, height=512, frames=49, steps=20)
    s = effective_settings(new)
    assert (s.width, s.height, s.frames, s.steps) == (512, 512, 49, 20)

    audio = {
        "1": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": 120.0, "batch_size": 1}},
        "2": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {"duration": 120.0, "seed": 1}},
    }
    new, notes = apply_settings(audio, seconds=20, width=512)
    assert new["1"]["inputs"]["seconds"] == 20.0 and new["2"]["inputs"]["duration"] == 20.0
    assert any("幅" in n for n in notes)  # 音声には幅が無い
    assert effective_settings(new).seconds == 20.0


def test_apply_settings_linked_input_is_reported():
    wf = {"1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["9", 0], "height": 512}}}
    new, notes = apply_settings(wf, width=768)
    assert new["1"]["inputs"]["width"] == ["9", 0]
    assert any("他のノード" in n for n in notes)


def test_size_from_primitive_int_is_read_and_set():
    """大きさが PrimitiveInt から来るテンプレート(FLUX.2 9B の文章から画像など)も、
    大きさを読み、書き換えられる。"""
    wf = {
        "1": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": ["7", 0], "height": ["8", 0]}},
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "8": {"class_type": "PrimitiveInt", "inputs": {"value": 768}},
    }
    assert size_input(wf)
    s = effective_settings(wf)
    assert (s.width, s.height) == (1024, 768)
    new, notes = apply_settings(wf, width=512, height=512)
    assert notes == []
    # 値のもと(PrimitiveInt の値)を書く。大きさのノードはつながりのまま(同じ値を使うノードもそろう。#187)
    assert new["7"]["inputs"]["value"] == 512 and new["8"]["inputs"]["value"] == 512
    assert new["1"]["inputs"]["width"] == ["7", 0]
    assert wf["7"]["inputs"]["value"] == 1024  # 元は変えない
    assert (effective_settings(new).width, effective_settings(new).height) == (512, 512)


def _set_and_read(wf, w, h):
    new, notes = apply_settings(wf, width=w, height=h)
    s = effective_settings(new)
    return new, notes, (s.width, s.height)


def test_size_through_a_math_node_is_the_user_number():
    """#187: 利用者の数(PrimitiveInt)を式で丸める・半分にする形(Ideogram 4・LTX 2.5 など)。大きさはその数で、
    書くのもその PrimitiveInt(式はそのまま残り、ComfyUI が計算する)。値が ResolutionSelector から来る
    PrimitiveInt も同じ。"""
    wf = {
        "1": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": ["5", 1], "height": ["6", 1]}},
        "5": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a/2", "values.a": ["7", 0]}},
        "6": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a/2", "values.a": ["8", 0]}},
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": ["9", 0]}},
        "8": {"class_type": "PrimitiveInt", "inputs": {"value": ["9", 1]}},
        "9": {
            "class_type": "ResolutionSelector",
            "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 1},
        },
    }
    assert size_input(wf)
    assert (effective_settings(wf).width, effective_settings(wf).height) == (1024, 1024)
    new, notes, got = _set_and_read(wf, 768, 512)
    assert notes == [] and got == (768, 512)
    assert new["7"]["inputs"]["value"] == 768 and new["8"]["inputs"]["value"] == 512
    assert new["1"]["inputs"]["width"] == ["5", 1]  # 式は残る(a/2 が抜けない)
    # 入力が 2 つつながった式は分からない
    two = {
        **wf,
        "5": {"class_type": "ComfyMathExpression", "inputs": {"values.a": ["7", 0], "values.b": ["8", 0]}},
    }
    assert effective_settings(two).width is None


def test_resolution_selector_through_a_math_node_is_not_replaced():
    """式を通った ResolutionSelector の値は、大きさのノードの入力を置き換えると途中の計算が抜けるので
    変えない。"""
    wf = {
        "1": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": ["5", 1], "height": ["6", 1]}},
        "5": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a/2", "values.a": ["9", 0]}},
        "6": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a/2", "values.a": ["9", 1]}},
        "9": {
            "class_type": "ResolutionSelector",
            "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 1},
        },
    }
    assert (effective_settings(wf).width, effective_settings(wf).height) == (1024, 1024)
    _, notes, got = _set_and_read(wf, 768, 512)
    assert notes and "ResolutionSelector" in notes[0] and got == (1024, 1024)


def test_size_from_an_image_resized_to_fixed_dimensions():
    """#187: 入力の画像を決まった幅×高さに縮め(ResizeImageMaskNode の scale dimensions)、その大きさを
    GetImageSize で受ける形(Capybara・LTX 2 の画像から動画など)。縮める幅・高さを書く。"""
    wf = {
        "1": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ["4", 0], "height": ["4", 1], "batch_size": 1},
        },
        "4": {"class_type": "GetImageSize", "inputs": {"image": ["3", 0]}},
        "3": {
            "class_type": "ResizeImageMaskNode",
            "inputs": {
                "input": ["2", 0],
                "resize_type": "scale dimensions",
                "resize_type.width": 1024,
                "resize_type.height": 1024,
            },
        },
        "2": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
    }
    assert size_input(wf)
    new, notes, got = _set_and_read(wf, 768, 512)
    assert notes == [] and got == (768, 512)
    assert new["3"]["inputs"]["resize_type.width"] == 768 and new["4"] == wf["4"]
    # 総画素数・長辺で縮めるだけ(入力の画像の縦横比のまま)なら、大きさの入力ではない
    wf["3"]["inputs"] = {"input": ["2", 0], "resize_type": "scale total pixels", "resize_type.megapixels": 1}
    assert not size_input(wf)
    # 入力の画像そのものの大きさも、大きさの入力ではない
    wf["4"]["inputs"]["image"] = ["2", 0]
    assert not size_input(wf)


def test_size_from_a_half_scaled_empty_image():
    """#187: LTX 2 の文章から動画: 空の画像(EmptyImage)を ImageScaleBy で半分にして潜在の大きさにし、
    後で 2 倍に上げる。大きさは空の画像の幅・高さ(出力の大きさ)。"""
    wf = {
        "1": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {"width": ["4", 0], "height": ["4", 1], "length": 97},
        },
        "4": {"class_type": "GetImageSize", "inputs": {"image": ["3", 0]}},
        "3": {"class_type": "ImageScaleBy", "inputs": {"image": ["2", 0], "scale_by": 0.5}},
        "2": {"class_type": "EmptyImage", "inputs": {"width": 1280, "height": 720, "batch_size": 1}},
    }
    assert (effective_settings(wf).width, effective_settings(wf).height) == (1280, 720)
    new, notes, got = _set_and_read(wf, 768, 512)
    assert notes == [] and got == (768, 512) and new["3"] == wf["3"]


def test_only_known_expression_shapes_pass_the_size():
    """式は、利用者の数をそのまま・倍数に丸める・半分にする形だけを大きさとして通す。2 倍にするなど、
    出力の大きさが数と違う形は読まない(違う大きさを記録しないため)。ImageScaleBy も 0.5・1 だけ。"""
    from genmodelfit.workflow import _size_expr

    for e in ("a/2", "a & -8", "floor(a/32)*32", "(a // 32) * 32", "max(16, floor(a / 16) * 16)"):
        assert _size_expr(e, "a"), e
    assert _size_expr("max(((w + 15) // 16) * 16, 256)", "w")
    for e in ("a*2", "floor(a/32)*16", "a+1", "a/3", "b/2"):
        assert not _size_expr(e, "a"), e
    wf = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["5", 1], "height": ["6", 1]}},
        "5": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a*2", "values.a": ["7", 0]}},
        "6": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a", "values.a": ["8", 0]}},
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": 512}},
        "8": {"class_type": "PrimitiveInt", "inputs": {"value": 512}},
    }
    assert not size_input(wf)
    scaled = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["4", 0], "height": ["4", 1]}},
        "4": {"class_type": "GetImageSize", "inputs": {"image": ["3", 0]}},
        "3": {"class_type": "ImageScaleBy", "inputs": {"image": ["2", 0], "scale_by": 2.0}},
        "2": {"class_type": "EmptyImage", "inputs": {"width": 640, "height": 640}},
    }
    from genmodelfit.workflow import _size_value

    assert _size_value(scaled, "1", "width") is None  # 空の画像そのものは大きさのノードとして別に数える


def test_size_number_used_for_something_else_is_not_written():
    """大きさのもとの PrimitiveInt がコマ数などにも使われていれば、書くとほかの設定まで変わるので書かない。"""
    wf = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["7", 0], "height": ["8", 0]}},
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "8": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "9": {"class_type": "SomeVideoNode", "inputs": {"length": ["7", 0]}},
    }
    new, notes, got = _set_and_read(wf, 768, 512)
    assert notes and "9" in notes[0] and new["7"]["inputs"]["value"] == 1024
    assert got == (1024, 512)  # 高さだけは書ける(bench は頼んだ設定と違うので生成の前に止まる)
    # 0(ImageScale の「縦横比から自動」)は大きさにしない
    zero = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["4", 0], "height": ["4", 1]}},
        "4": {"class_type": "GetImageSize", "inputs": {"image": ["3", 0]}},
        "3": {"class_type": "ImageScale", "inputs": {"image": ["2", 0], "width": 0, "height": 512}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
    }
    assert effective_settings(zero).width is None


def test_width_and_height_from_one_number_cannot_differ():
    wf = {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["7", 0], "height": ["7", 0]}},
        "7": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
    }
    new, notes, got = _set_and_read(wf, 768, 512)
    assert notes and new["7"]["inputs"]["value"] == 1024
    new, notes, got = _set_and_read(wf, 768, 768)
    assert notes == [] and got == (768, 768)


def test_size_from_resolution_selector_follows_comfyui():
    """ResolutionSelector の幅・高さは ComfyUI と同じ計算(メガ画素 × 1024²、縦横比、multiple の倍数)。"""

    def wf(aspect, mp, multiple=8):
        return {
            "1": {"class_type": "EmptySD3LatentImage", "inputs": {"width": ["9", 0], "height": ["9", 1]}},
            "9": {
                "class_type": "ResolutionSelector",
                "inputs": {"aspect_ratio": aspect, "megapixels": mp, "multiple": multiple},
            },
        }

    s = effective_settings(wf("1:1 (Square)", 1))
    assert (s.width, s.height) == (1024, 1024)
    # 16:9・1 MP: scale = √(1048576 / 144) ≈ 85.33 → 1365.3 / 8 → 171 × 8 = 1368、768
    s = effective_settings(wf("16:9 (Widescreen)", 1.0))
    assert (s.width, s.height) == (1368, 768)
    new, notes = apply_settings(wf("1:1 (Square)", 1), width=768, height=768)
    assert notes == [] and (effective_settings(new).width, effective_settings(new).height) == (768, 768)
    # 同じ大きさを画像の縮小のノードも使うなら、書き換えない(動く大きさが混ざる。bench は生成の前に止まる)
    shared = wf("1:1 (Square)", 1)
    shared["5"] = {"class_type": "ImageScaleToMaxDimension", "inputs": {"largest_size": ["9", 0]}}
    new, notes = apply_settings(shared, width=768, height=768)
    assert new["1"]["inputs"]["width"] == ["9", 0] and any("ノード 5" in n for n in notes)
    assert effective_settings(new).width == 1024
    # 値が inf・nan でも落ちない
    assert effective_settings(wf("1:1 (Square)", float("inf"))).width is None
    # 縦横比が他のノードから来るなど、値が分からないときは今までどおり大きさの入力なし
    unknown = wf("1:1 (Square)", 1)
    unknown["9"]["inputs"]["megapixels"] = ["3", 0]
    assert not size_input(unknown) and effective_settings(unknown).width is None


def test_offload_from_logs():
    assert offload_from_logs(["loaded completely; 7000 MB usable"]) == "none"
    assert offload_from_logs(["loaded completely", "loaded partially; 3000 MB loaded"]) == "partial"
    assert offload_from_logs(["nothing"]) is None


def test_offload_from_logs_debug_lines_are_not_lowvram():
    """#102: --verbose DEBUG の「lowvram: loaded module regularly」は全部載った読み込みの行
    (lowvram ではない)。「Unloaded partially … lowvram patches」は partial。
    lowvram は --lowvram / --novram で起動したときだけ。"""
    debug = ["lowvram: loaded module regularly diffusion_model.x Linear", "loaded completely; 9000 MB usable"]
    assert offload_from_logs(debug) == "none"
    assert offload_from_logs(["Unloaded partially: 100 MB freed, lowvram patches: 0"]) == "partial"
    assert offload_from_logs(["Set vram state to: LOW_VRAM"]) == "lowvram"
    assert offload_from_logs(["Set vram state to: NO_VRAM"]) == "lowvram"
    assert offload_from_logs(["Set vram state to: NORMAL_VRAM", "loaded completely"]) == "none"


# --- 偽の ComfyUI ----------------------------------------------------------------------------------


class FakeComfy:
    """必要な API だけを持つ偽の ComfyUI。behavior で動きを変える。"""

    def __init__(self, behavior: str = "ok", torch_before: int = 0):
        self.behavior = behavior
        self.submitted: list[dict[str, Any]] = []
        self.freed = 0
        self.interrupts: list[Any] = []  # /interrupt の本文
        self.deleted: list[str] = []
        self.uploads: list[bytes] = []  # /upload/image の本文(multipart のまま)
        self.calls: list[str] = []  # 受けた要求の順(「メソッド パス」)
        self.polls = 0
        self.torch = torch_before
        # /system_stats の GPU(NVIDIA 以外の試験で変える)と、ComfyUI 以外のアプリが使う VRAM
        self.device = ("cuda:0 NVIDIA GeForce RTX 3070 : cudaMallocAsync", "cuda")
        self.pytorch_version = "2.9.0+cu128"
        self.other_apps = GIB
        self.history_calls = 0
        self.submit_time = 0.0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()

    @property
    def mutations(self) -> list[str]:
        return [c for c in self.calls if c.startswith("POST")]

    def _queue(self) -> dict[str, Any]:
        running: list[Any] = []
        pending: list[Any] = []
        b = self.behavior
        if b == "busy":
            running = [[0, "other", {}]]
        if self.submitted:
            if b == "timeout":
                running = [[0, "pid-1", {}]]
            elif b == "timeout_other":  # 他の人の仕事が実行中で、自分のは待っている
                running, pending = [[0, "other", {}]], [[1, "pid-1", {}]]
            elif b == "pending_mine":
                pending = [[1, "pid-1", {}]]
            elif b == "interleave":
                running = [[0, "other", {}]]
        return {"queue_running": running, "queue_pending": pending}

    def _history(self) -> dict[str, Any]:
        self.history_calls += 1
        if self.behavior in ("timeout", "timeout_other", "pending_mine") or self.history_calls < 3:
            return {}
        t = self.submit_time * 1000 + (60_000 if self.behavior == "late_start" else 100)
        msgs: list[Any] = [["execution_start", {"timestamp": t}]]
        if self.behavior in ("cached", "partial_cached"):
            nodes = list(self.submitted[-1])
            if self.behavior == "partial_cached":
                nodes = [n for n in nodes if self.submitted[-1][n]["class_type"] == "CLIPTextEncode"]
            msgs.append(["execution_cached", {"nodes": nodes, "timestamp": t}])
        if self.behavior in ("error", "oom"):
            exc = "torch.OutOfMemoryError" if self.behavior == "oom" else "ValueError"
            msgs.append(
                [
                    "execution_error",
                    {
                        "timestamp": t + 2000,
                        "node_type": "KSampler",
                        "exception_type": exc,
                        "exception_message": "boom",
                    },
                ]
            )
            st = {"status_str": "error", "completed": False, "messages": msgs}
        else:
            msgs.append(["execution_success", {"timestamp": t + 2500}])
            st = {"status_str": "success", "completed": True, "messages": msgs}
        return {"pid-1": {"status": st, "outputs": {}}}

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, obj: Any) -> None:
                body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _raw(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                p = self.path
                fake.calls.append(f"GET {p}")
                if p == "/system_stats":
                    fake.polls += 1
                    if fake.submitted and fake.behavior not in ("timeout", "timeout_other", "pending_mine"):
                        fake.torch = 5 * GIB  # 実行中は 5 GB
                    dev = {
                        "name": fake.device[0],
                        "type": fake.device[1],
                        "vram_total": 8 * GIB - 100 * 1024**2,
                        "vram_free": 8 * GIB - fake.torch - 100 * 1024**2 - fake.other_apps,
                        "torch_vram_total": fake.torch,
                    }
                    system = {"comfyui_version": "0.40.0", "pytorch_version": fake.pytorch_version}
                    return self._send(200, {"system": system, "devices": [dev]})
                if p == "/object_info":
                    return self._send(200, OBJECT_INFO)
                if p == "/queue":
                    return self._send(200, fake._queue())
                if p.startswith("/templates/"):
                    return self._send(200, sdxl_ui()) if "image_sdxl" in p else self._send(404, {})
                if p.startswith("/history/"):
                    return self._send(200, fake._history())
                if p == "/internal/logs/raw":
                    # ComfyUI の LogInterceptor と同じ形(t はその PC の時刻の ISO の文字、m は書いた文字。
                    # tqdm の進み具合の行も入る。#132 の時間の内訳の材料)
                    lines = [{"t": "1", "m": "start"}]
                    if fake.submitted:

                        def at(dt: float) -> str:
                            return datetime.fromtimestamp(fake.submit_time + dt).isoformat()

                        lines += [
                            {"t": at(0.2), "m": "got prompt"},
                            {"t": at(0.3), "m": "loaded completely; 7000 MB usable"},
                            # ほかの処理の進み具合(説明付き・ステップ数が違う)は時間の内訳に使わない
                            {"t": at(0.4), "m": "Fetching 3 files: 100%|###| 3/3 [25:00:00<00:00, 0.01s/it]"},
                            {"t": at(0.45), "m": "\r100%|##########| 7/7 [00:09<00:00,  0.8it/s]"},
                            {"t": at(0.5), "m": "\r  0%|          | 0/25 [00:00<?, ?it/s]"},
                            {"t": at(1.0), "m": "\r 48%|#####     | 12/25 [00:00<00:00, 24.0it/s]"},
                            {"t": at(1.6), "m": "\r100%|##########| 25/25 [00:01<00:00, 24.5it/s]"},
                        ]
                    return self._send(200, {"entries": lines})
                if p.startswith("/tplinput/"):
                    return self._send(200, b"\x89PNG fake")
                return self._send(404, {})

            def do_POST(self):
                p = self.path
                fake.calls.append(f"POST {p}")
                raw = self._raw()
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = None
                if p == "/free":
                    fake.freed += 1
                    fake.torch = 0
                    return self._send(200, {})
                if p == "/prompt":
                    fake.submitted.append(body["prompt"])
                    fake.submit_time = time.time()
                    if fake.behavior == "reject":
                        return self._send(
                            400,
                            {
                                "error": {"type": "prompt_outputs_failed_validation", "message": "bad"},
                                "node_errors": {
                                    "5": {"class_type": "KSampler", "errors": [{"message": "Value too big"}]}
                                },
                            },
                        )
                    return self._send(200, {"prompt_id": "pid-1", "number": 1, "node_errors": {}})
                if p == "/interrupt":
                    fake.interrupts.append(body)
                    return self._send(200, {})
                if p == "/queue":
                    fake.deleted += body.get("delete", [])
                    return self._send(200, {})
                if p == "/upload/image":
                    fake.uploads.append(raw)
                    return self._send(200, {"name": "uploaded.png"})
                return self._send(404, {})

        return H


def _opts(url: str, **kw) -> BenchOptions:
    return BenchOptions(model_id="image_sdxl_simple", url=url, interval=0.01, **kw)


def _quiet(msg: str) -> None:
    pass


def test_bench_measures_with_fake_server():
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, width=832, height=832, steps=15, free_before=True), log=_quiet)
    m = res.measurement
    assert fake.freed == 1  # --free のときだけ開始前に解放
    assert m.gpu_name == "NVIDIA GeForce RTX 3070" and m.driver == "566.03"
    assert m.gpu_vram_gb == pytest.approx(7.9, abs=0.01)  # 丸めずに GiB で持つ
    assert m.peak_vram_gb == pytest.approx(5.0, abs=0.01)
    assert m.comfyui_version == "0.40.0" and m.offload == "none"
    assert m.outcome == "ok" and m.standard is True
    assert (m.settings.width, m.settings.height, m.settings.steps) == (832, 832, 15)
    assert m.seconds is not None and m.date and "/free" in m.note and "2.5 秒" in m.note
    assert res.workflow_source == "server-template"
    sent = fake.submitted[0]
    assert sent["4"]["inputs"]["width"] == 832 and sent["7"]["inputs"]["filename_prefix"].startswith(
        "genmodelfit"
    )


def test_bench_does_not_free_by_default_and_warns_about_preloaded():
    with FakeComfy(torch_before=3 * GIB) as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert fake.freed == 0 and "POST /free" not in fake.calls
    assert any("読み込み済み" in w and "--free" in w for w in res.warnings)
    assert "/free はしていない" in res.measurement.note


def test_bench_refuses_busy_queue_before_any_write():
    with FakeComfy("busy") as fake, pytest.raises(BenchError, match="キュー"):
        bench.run(_opts(fake.url, free_before=True), log=_quiet)
    assert fake.mutations == []  # 他の人の仕事がある間は何も書き込まない(/free もアップロードも)


def _upload_workflow(tmp_path: Path) -> Path:
    wf = ui_to_api(sdxl_ui(), OBJECT_INFO).workflow
    wf["20"] = {"class_type": "LoadImage", "inputs": {"image": "input_x.png"}}
    p = tmp_path / "api.json"
    p.write_text(json.dumps(wf), encoding="utf-8")
    return p


def test_bench_refuses_busy_gpu_before_any_write(tmp_path, monkeypatch):
    p = _upload_workflow(tmp_path)
    monkeypatch.setattr(bench, "gpu_used_now", lambda gpu_id: (4096.0, 90.0))
    with FakeComfy() as fake:
        monkeypatch.setattr(bench, "template_input_url", lambda name: fake.url + "/tplinput/" + name)
        with pytest.raises(BenchError, match="使用率 90%"):
            bench.run(_opts(fake.url, workflow_path=str(p), free_before=True), log=_quiet)
    assert fake.mutations == [] and fake.uploads == []


def test_bench_gpu_busy_is_averaged_over_samples(monkeypatch):
    seq = iter([(1.0, 100.0), (1.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 0.0)])
    seen: list[str] = []

    def fake_used(gpu_id):
        seen.append(gpu_id)
        return next(seq)

    monkeypatch.setattr(bench, "gpu_used_now", fake_used)
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url), log=_quiet)  # 一瞬の 100% だけでは断らない
    assert res.measurement.outcome == "ok"
    assert seen == ["GPU-aaaa"] * 5  # 5 回読む。GPU は UUID で指す


def test_bench_requires_force_when_gpu_cannot_be_checked(monkeypatch):
    monkeypatch.setattr(bench, "smi_gpus", lambda: [])  # nvidia-smi が無い
    with FakeComfy() as fake:
        with pytest.raises(BenchError, match=r"nvidia-smi.*--force"):
            bench.run(_opts(fake.url), log=_quiet)
        assert fake.mutations == []
        res = bench.run(_opts(fake.url, force=True), log=_quiet)
    assert any("--force" in w for w in res.warnings) and "確かめていない" in res.measurement.note
    assert res.measurement.driver is None


def test_match_gpu_identical_names_uses_process_uuid(monkeypatch):
    a = dict(GPU0)
    b = dict(GPU0, index=1, uuid="GPU-bbbb")

    class P:
        pid = 4242

        def children(self, recursive=False):
            return []

    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [(4242, "GPU-bbbb"), (7, "GPU-aaaa")])
    gpu, why = bench.match_gpu(GPU0["name"], [a, b], P(), local=True)
    assert gpu is b and why is None
    monkeypatch.setattr(bench, "smi_compute_apps", lambda: [])  # Windows の WDDM などで分からない
    gpu, why = bench.match_gpu(GPU0["name"], [a, b], P(), local=True)
    assert gpu is None and "2 枚" in why
    gpu, why = bench.match_gpu(GPU0["name"], [a], None, local=False)
    assert gpu is None and "別の PC" in why
    gpu, why = bench.match_gpu("NVIDIA GeForce RTX 4090", [a], None, local=True)
    assert gpu is None and "見つかりません" in why


def test_bench_rechecks_queue_right_before_submit(monkeypatch):
    with FakeComfy() as fake:
        real_free = ComfyClient.free

        def free_then_someone_queues(self):
            real_free(self)
            fake.behavior = "busy"  # 解放のあいだに他の人が仕事を入れた

        monkeypatch.setattr(ComfyClient, "free", free_then_someone_queues)
        with pytest.raises(BenchError, match="送る直前"):
            bench.run(_opts(fake.url, free_before=True), log=_quiet)
    assert fake.submitted == []


def test_bench_execution_error_is_reported_not_raised():
    with FakeComfy("error") as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert res.failed and "ValueError" in res.failed
    assert res.measurement.outcome == "error" and res.measurement.offload != "oom"
    with FakeComfy("reject") as fake, pytest.raises(BenchError, match="Value too big"):
        bench.run(_opts(fake.url), log=_quiet)


def test_bench_oom_is_marked():
    with FakeComfy("oom") as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert res.failed and "OutOfMemory" in res.failed
    m = res.measurement
    assert m.outcome == "oom" and m.offload == "oom" and "メモリ不足" in m.note


def test_bench_all_cached_is_refused():
    with FakeComfy("cached") as fake, pytest.raises(BenchError, match="前の結果"):
        bench.run(_opts(fake.url), log=_quiet)
    assert fake.freed == 0


def test_bench_partial_cache_is_not_standard():
    with FakeComfy("partial_cached") as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    m = res.measurement
    assert m.outcome == "ok" and m.standard is False and "一部だけ実行" in m.note
    assert any("前の結果" in w for w in res.warnings)


def test_bench_other_work_during_run_is_not_standard():
    with FakeComfy("interleave") as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert res.measurement.standard is False and "他の仕事" in res.measurement.note


def test_bench_late_start_is_not_standard():
    with FakeComfy("late_start") as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert res.measurement.standard is False and "実行が始まるまで" in res.measurement.note


def test_bench_timeout_interrupts_only_own_prompt():
    with FakeComfy("timeout") as fake, pytest.raises(BenchError, match="中止"):
        bench.run(_opts(fake.url, timeout=0.01), log=_quiet)
    assert fake.interrupts == [{"prompt_id": "pid-1"}] and fake.deleted == []


def test_bench_timeout_while_other_runs_deletes_own_pending_only():
    with FakeComfy("timeout_other") as fake, pytest.raises(BenchError, match="中止"):
        bench.run(_opts(fake.url, timeout=0.01), log=_quiet)
    assert fake.interrupts == []  # 実行中の他の人の仕事は止めない
    assert fake.deleted == ["pid-1"]


def test_bench_ctrl_c_removes_own_pending_prompt():
    class Client(ComfyClient):
        def history(self, prompt_id):
            raise KeyboardInterrupt

    with FakeComfy("pending_mine") as fake, pytest.raises(BenchError, match="中断"):
        bench.run(_opts(fake.url), client=Client(fake.url), log=_quiet)
    assert fake.deleted == ["pid-1"] and fake.interrupts == []


def test_bench_rejects_non_positive_timeout():
    with pytest.raises(BenchError, match="0 より大きい"):
        bench.run(_opts("http://127.0.0.1:9", timeout=0), log=_quiet)


def test_bench_missing_model_and_unknown_template(monkeypatch):
    with FakeComfy() as fake:
        monkeypatch.setattr(bench, "template_url", lambda name: fake.url + "/nothing")  # GitHub に行かない
        with pytest.raises(BenchError, match="見つかりません"):
            bench.run(BenchOptions(model_id="no_such_template_xyz", url=fake.url), log=_quiet)


def test_bench_api_workflow_file_and_set_are_not_standard(tmp_path):
    wf = ui_to_api(sdxl_ui(), OBJECT_INFO).workflow
    wf["1"]["inputs"]["ckpt_name"] = "missing.safetensors"
    p = tmp_path / "api.json"
    p.write_text(json.dumps(wf), encoding="utf-8")
    with FakeComfy() as fake:
        with pytest.raises(BenchError, match=r"missing\.safetensors"):
            bench.run(_opts(fake.url, workflow_path=str(p)), log=_quiet)
        assert fake.mutations == []
        wf["1"]["inputs"]["ckpt_name"] = "model.safetensors"
        p.write_text(json.dumps(wf), encoding="utf-8")
        res = bench.run(_opts(fake.url, workflow_path=str(p), sets={"cfg": 3.5}), log=_quiet)
    assert res.workflow_source == "file:api" and fake.submitted[-1]["5"]["inputs"]["cfg"] == 3.5
    m = res.measurement
    assert "cfg=3.5" in m.note and m.standard is False and "--workflow" in m.note


def test_bench_set_strings_are_not_recorded():
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, sets={"text": "my secret prompt", "seed": 5}), log=_quiet)
    m = res.measurement
    assert fake.submitted[-1]["2"]["inputs"]["text"] == "my secret prompt"
    assert "my secret prompt" not in m.note and "text(値は記録しない)" in m.note
    assert "seed=5" in m.note
    assert m.standard is False  # text は大きさ・ステップ数ではない
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, sets={"seed": 7, "steps": 3}), log=_quiet)
    assert res.measurement.standard is True  # seed・steps だけなら標準のまま


def test_bench_uploads_without_overwrite_after_checks(tmp_path, monkeypatch):
    p = _upload_workflow(tmp_path)
    with FakeComfy() as fake:
        monkeypatch.setattr(bench, "template_input_url", lambda name: fake.url + "/tplinput/" + name)
        res = bench.run(_opts(fake.url, workflow_path=str(p), force=True), log=_quiet)
    assert len(fake.uploads) == 1 and b'name="overwrite"' not in fake.uploads[0]
    assert fake.submitted[-1]["20"]["inputs"]["image"] == "uploaded.png"
    assert any("アップロード" in w for w in res.warnings)
    # 読むだけの確認(キュー・object_info)はアップロードより前
    assert fake.calls.index("POST /upload/image") > fake.calls.index("GET /object_info")


def test_cli_bench_json_saves_measurement(tmp_path, capsys):
    out = tmp_path / "m.json"
    with FakeComfy() as fake:
        code = cli.main(
            [
                "bench",
                "image_sdxl_simple",
                "--url",
                fake.url,
                "--resolution",
                "640x480",
                "--json",
                "--output",
                str(out),
            ]
        )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["command"] == "bench" and data["measurement"]["settings"]["width"] == 640
    assert data["measurement"]["outcome"] == "ok" and data["measurement"]["standard"] is True
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["measurements"][0]["model_id"] == "image_sdxl_simple"
    text = out.read_text(encoding="utf-8")
    assert str(Path.home()) not in text and "\\Users\\" not in text  # パスを入れない


def test_cli_bench_default_path_is_read_back(capsys):
    with FakeComfy() as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url]) == 0
    assert "最大 VRAM" in capsys.readouterr().out
    ms = [
        m
        for m in load_measurements()
        if m.gpu_name == "NVIDIA GeForce RTX 3070" and m.source.startswith("genmodelfit")
    ]
    # 同梱にも RTX 3070 の実測(2026-10-19)があるので、キャッシュに保存した分を探す
    assert any(m.model_id == "image_sdxl_simple" for m in ms)  # キャッシュに保存した実測が推定に使われる


def _cached_bench_files() -> list[Path]:
    d = cache_dir() / "measurements"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_cli_bench_oom_is_not_cached_by_default(tmp_path, capsys):
    with FakeComfy("oom") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["saved_to"] is None and "--save-failed" in data["not_saved"]
    assert data["measurement"]["outcome"] == "oom"
    assert _cached_bench_files() == []
    with FakeComfy("oom") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--save-failed"]) == 1
    (f,) = _cached_bench_files()
    assert json.loads(f.read_text(encoding="utf-8"))["measurements"][0]["outcome"] == "oom"
    out = tmp_path / "oom.json"
    with FakeComfy("oom") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--output", str(out)]) == 1
    assert out.is_file()


def test_cli_bench_nonstandard_is_not_cached_by_default(capsys):
    with FakeComfy("partial_cached") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url]) == 0
    assert "--save-nonstandard" in capsys.readouterr().out
    assert _cached_bench_files() == []
    with FakeComfy("partial_cached") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--save-nonstandard"]) == 0
    assert len(_cached_bench_files()) == 1


def test_cli_bench_errors(capsys):
    with FakeComfy("busy") as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--json"]) == 1
    assert "キュー" in json.loads(capsys.readouterr().out)["error"]
    assert cli.main(["bench", "x", "--url", "http://127.0.0.1:9", "--resolution", "big"]) == 1
    assert cli.main(["bench", "x", "--url", "http://127.0.0.1:9", "--timeout", "1"]) == 1  # 接続できない
    assert cli.main(["bench", "x", "--url", "http://127.0.0.1:9", "--timeout", "0"]) == 1
    assert "0 より大きい" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:  # --url は必須(既定の 8188 に送らない)
        cli.main(["bench", "x"])
    assert e.value.code == 2


def test_client_template_404_returns_none():
    with FakeComfy() as fake:
        assert ComfyClient(fake.url).template("nothing") is None


def test_client_does_not_follow_redirects():
    """--url の相手が別の相手(ポート違いも別)へ転送しても追わない(ワークフローを別のホストへ送り直さない。
    RT-004)。同じ相手の中の転送は追う(tests/test_privacy.py の test_bench_client_sends_no_netrc)。"""
    from http.server import BaseHTTPRequestHandler

    hits: list[str] = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    to = f"http://127.0.0.1:{target.server_address[1]}/prompt"

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    src = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (target, src)]
    for t in threads:
        t.start()
    try:
        client = ComfyClient(f"http://127.0.0.1:{src.server_address[1]}")
        with pytest.raises(BenchError, match=r"転送|redirect"):
            client._req("POST", "/prompt", json={"prompt": {}})
    finally:
        for s in (target, src):
            s.shutdown()
            s.server_close()
    assert hits == []


# --- 計測の部品 -------------------------------------------------------------------------------------


class _FakePopen:
    """nvidia-smi -lms の代わり。止められるまで行を出し続ける。"""

    instances: ClassVar[list[_FakePopen]] = []

    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.terminated = threading.Event()
        self.waited = False
        self.killed = False
        self.stdout = self._lines()
        type(self).instances.append(self)

    def _lines(self):
        yield "1000\n"
        while not self.terminated.wait(0.01):
            yield "1500\n"

    def terminate(self):
        self.terminated.set()

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class _StatsClient:
    def system_stats(self):
        return {"devices": [{"torch_vram_total": GIB, "vram_total": 8 * GIB, "vram_free": 6 * GIB}]}


def test_monitor_reaps_nvidia_smi_child(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(bench.subprocess, "Popen", _FakePopen)
    mon = bench.Monitor(_StatsClient(), None, "GPU-aaaa", interval=0.01, use_smi=True)
    mon.start()
    deadline = time.monotonic() + 5
    while mon.peaks.smi_samples < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    peaks = mon.stop()
    (p,) = _FakePopen.instances
    assert p.terminated.is_set() and p.waited  # 止めて、回収まで待つ
    assert p.cmd[-2:] == ["-i", "GPU-aaaa"]
    assert peaks.gpu_used_mib == 1500 and peaks.torch_vram == GIB
    assert all(not t.is_alive() for t in mon._threads)


def test_monitor_does_not_start_smi_after_stop(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(bench.subprocess, "Popen", _FakePopen)
    mon = bench.Monitor(_StatsClient(), None, None, use_smi=True)
    mon._stop.set()  # 読み取りのスレッドが起動する前に stop() が来た
    mon._read_smi()
    assert _FakePopen.instances == []


def test_monitor_reaps_child_when_output_ends(monkeypatch):
    class Ends(_FakePopen):
        def _lines(self):
            yield "800\n"

    Ends.instances = []
    monkeypatch.setattr(bench, "_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(bench.subprocess, "Popen", Ends)
    mon = bench.Monitor(_StatsClient(), None, None, use_smi=True)
    mon._read_smi()  # nvidia-smi が自分で終わっても回収する
    (p,) = Ends.instances
    assert p.waited and mon.peaks.gpu_used_mib == 800


def test_find_server_process_requires_comfy_cmdline(monkeypatch):
    class Conn:
        status = psutil.CONN_LISTEN
        laddr = type("A", (), {"port": 8188})()
        pid = 99

    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return cmdline

    monkeypatch.setattr(bench.psutil, "net_connections", lambda kind: [Conn()])
    monkeypatch.setattr(bench.psutil, "Process", Proc)
    cmdline = ["python.exe", "-s", "ComfyUI\\main.py", "--port", "8188"]
    assert _REAL_FIND_SERVER_PROCESS("http://127.0.0.1:8188").pid == 99
    cmdline = ["C:\\Program Files\\SomeApp\\server.exe"]
    assert _REAL_FIND_SERVER_PROCESS("http://127.0.0.1:8188") is None
    assert _REAL_FIND_SERVER_PROCESS("http://192.168.1.5:8188") is None  # 別の PC


def test_bench_without_comfy_process_leaves_ram_empty():
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url), log=_quiet)
    assert res.measurement.peak_ram_gb is None
    assert any("最大メモリは記録しない" in w for w in res.warnings)


def test_describe_sets_keeps_only_numbers():
    assert bench.describe_sets({"a": 1, "b": 2.5, "c": "x", "d": [1], "e": True}) == (
        "a=1, b=2.5, c(値は記録しない), d(値は記録しない), e(値は記録しない)"
    )


# --- 実測の扱い(見積もり側) ---------------------------------------------------------------------


def test_pick_measurement_skips_failed_and_nonstandard():
    entry = ModelEntry(id="m1", name="m1", modality="image")
    s = Settings(width=768, height=768)

    def meas(peak, **kw):
        return Measurement(
            model_id="m1", gpu_name="g", gpu_vram_gb=8, peak_vram_gb=peak, settings=Settings(768, 768), **kw
        )

    ok = meas(5.0)
    picked = pick_measurement([ok, meas(7.9, outcome="oom"), meas(9.0, standard=False)], entry, s)
    assert picked is ok
    assert pick_measurement([meas(7.9, outcome="oom"), meas(3.0, outcome="error")], entry, s) is None
    # 古い行(outcome・standard が無い)は ok・標準として読む
    old = Measurement.from_dict({"model_id": "m1", "gpu_name": "g", "gpu_vram_gb": 16, "peak_vram_gb": 8})
    assert (old.outcome, old.standard) == ("ok", True)


# --- 実物のテンプレート(Comfy-Org/workflow_templates、MIT)と ComfyUI 0.40.0 の /object_info ---------

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_real_template_z_image_subgraph():
    info = _fixture("object_info_subset.json")["nodes"]
    conv = ui_to_api(_fixture("image_z_image_turbo_int8.json"), info)
    wf = conv.workflow
    assert conv.warnings == []
    types = sorted(n["class_type"] for n in wf.values())
    assert types.count("KSampler") == 1 and "MarkdownNote" not in types
    sampler = next(n for n in wf.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["steps"] == 8 and sampler["inputs"]["sampler_name"] == "res_multistep"
    unet = next(n for n in wf.values() if n["class_type"] == "UNETLoader")
    assert unet["inputs"]["unet_name"] == "z_image_turbo_int8_convrot.safetensors"
    new, notes = apply_settings(wf, width=768, height=768, steps=6)
    s = effective_settings(new)
    assert (s.width, s.height, s.steps, notes) == (768, 768, 6, [])


def test_real_template_ace_step_primitive_and_dynamic_combo():
    info = _fixture("object_info_subset.json")["nodes"]
    wf = ui_to_api(_fixture("audio_ace_step_1_5_checkpoint.json"), info).workflow
    enc = next(n for n in wf.values() if n["class_type"] == "TextEncodeAceStepAudio1.5")
    # PrimitiveNode(120 秒・seed 31)の値が両方のノードに入る
    assert enc["inputs"]["duration"] == 120 and enc["inputs"]["seed"] == 31 and enc["inputs"]["bpm"] == 190
    assert enc["inputs"]["language"] == "en" and enc["inputs"]["top_k"] == 0
    save = next(n for n in wf.values() if n["class_type"] == "SaveAudioAdvanced")
    assert save["inputs"]["format"] == "flac"
    new, _ = apply_settings(wf, seconds=20, steps=8)
    assert effective_settings(new).seconds == 20.0
    latent = next(n for n in new.values() if n["class_type"] == "EmptyAceStep1.5LatentAudio")
    assert latent["inputs"]["seconds"] == 20.0


def test_real_template_sdxl_simple():
    info = _fixture("object_info_subset.json")["nodes"]
    conv = ui_to_api(_fixture("image_sdxl_simple.json"), info)
    wf = conv.workflow
    assert conv.warnings == []
    assert sorted(n["class_type"] for n in wf.values()) == [
        "CLIPTextEncode",
        "CLIPTextEncode",
        "CheckpointLoaderSimple",
        "EmptyLatentImage",
        "KSampler",
        "SaveImage",
        "VAEDecode",
    ]
    assert wf["12"]["inputs"] | {"seed": 0} == {
        "seed": 0,
        "steps": 25,
        "cfg": 7,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 1,
        "model": ["15", 0],
        "positive": ["10", 0],
        "negative": ["11", 0],
        "latent_image": ["13", 0],
    }
    assert wf["14"]["inputs"] == {"samples": ["12", 0], "vae": ["15", 2]}
    assert wf["15"]["inputs"] == {"ckpt_name": "sd_xl_base_1.0.safetensors"}
    new, notes = apply_settings(wf, width=832, height=832, steps=15)
    s = effective_settings(new)
    assert (s.width, s.height, s.steps, notes) == (832, 832, 15, [])
    assert reachable_nodes(wf, info) == set(wf)


def test_real_template_hunyuan3d_image_to_model():
    info = _fixture("object_info_subset.json")["nodes"]
    conv = ui_to_api(_fixture("3d_hunyuan3d_image_to_model.json"), info)
    wf = conv.workflow
    assert conv.warnings == []
    assert "MarkdownNote" not in {n["class_type"] for n in wf.values()}
    assert wf["61"]["inputs"] == {
        "num_chunks": 8000,
        "octree_resolution": 256,
        "samples": ["3", 0],
        "vae": ["54", 2],
    }
    assert wf["80"]["inputs"] == {"clip_vision_output": ["51", 0]}
    assert wf["3"]["inputs"]["negative"] == ["80", 1]  # 2 つめの出力
    assert wf["51"]["inputs"] == {"crop": "none", "clip_vision": ["54", 1], "image": ["56", 0]}
    assert wf["81"]["inputs"] == {"algorithm": "surface net", "threshold": 0.6, "voxel": ["61", 0]}
    # 入力の画像はアップロードが要るものとして挙がる
    assert upload_inputs(wf, info) == [("56", "image", "3d_hunyuan3d_image_to_model_input_image.png")]
    new, _ = apply_settings(wf, steps=15)
    assert new["3"]["inputs"]["steps"] == 15
    assert any("ckpt_name=hunyuan3d-dit-v2_fp16.safetensors" in p for p in check_inputs(wf, info))


def test_real_template_text_to_video_wan():
    info = _fixture("object_info_subset.json")["nodes"]
    conv = ui_to_api(_fixture("text_to_video_wan.json"), info)
    wf = conv.workflow
    assert conv.warnings == []
    assert wf["40"]["inputs"] == {"width": 832, "height": 480, "length": 33, "batch_size": 1}
    assert wf["37"]["inputs"] == {"unet_name": "wan2.1_t2v_1.3B_fp16.safetensors", "weight_dtype": "default"}
    assert wf["48"]["inputs"] == {"shift": 8, "model": ["37", 0]}
    assert wf["49"]["inputs"] == {"fps": 16, "images": ["8", 0]}
    assert wf["50"]["inputs"]["format"] == "auto" and wf["50"]["inputs"]["video"] == ["49", 0]
    assert wf["3"]["inputs"]["steps"] == 30 and wf["3"]["inputs"]["sampler_name"] == "uni_pc"
    new, notes = apply_settings(wf, width=512, height=512, frames=49, steps=20)
    s = effective_settings(new)
    assert (s.width, s.height, s.frames, s.steps, notes) == (512, 512, 49, 20, [])
    problems = check_inputs(wf, info)
    assert any("モデルのファイル" in p and "wan2.1_t2v_1.3B_fp16" in p for p in problems)
    assert not any("モデルのファイル" in p and "sampler_name" in p for p in problems)  # サンプラーは選択肢


# --- 変換の細部 -------------------------------------------------------------------------------------


def test_bypass_prefers_input_with_same_slot():
    # 素通しのノードに同じ型の入力が 2 つ。出力 1 番を使う先には、入力 1 番の元が渡る(画面と同じ)
    ui = {
        "nodes": [
            node(1, "CheckpointLoaderSimple", ["model.safetensors"]),
            node(2, "CLIPTextEncode", ["a"], [{"name": "clip", "type": "CLIP", "link": 1}]),
            node(3, "CLIPTextEncode", ["b"], [{"name": "clip", "type": "CLIP", "link": 2}]),
            node(
                4,
                "PairNode",
                [],
                [
                    {"name": "a", "type": "CONDITIONING", "link": 3},
                    {"name": "b", "type": "CONDITIONING", "link": 4},
                ],
                [{"type": "CONDITIONING"}, {"type": "CONDITIONING"}],
                mode=4,
            ),
            node(5, "EmptyLatentImage", [512, 512, 1]),
            node(
                6,
                "KSampler",
                [1, "fixed", 2, 7, "euler", "normal", 1],
                [
                    {"name": "model", "type": "MODEL", "link": 5},
                    {"name": "positive", "type": "CONDITIONING", "link": 6},
                    {"name": "negative", "type": "CONDITIONING", "link": 7},
                    {"name": "latent_image", "type": "LATENT", "link": 8},
                ],
            ),
            node(7, "VAEDecode", [], [{"name": "samples", "link": 9}, {"name": "vae", "link": 10}]),
            node(8, "SaveImage", ["x"], [{"name": "images", "link": 11}]),
        ],
        "links": [
            [1, 1, 1, 2, 0, "CLIP"],
            [2, 1, 1, 3, 0, "CLIP"],
            [3, 2, 0, 4, 0, "CONDITIONING"],
            [4, 3, 0, 4, 1, "CONDITIONING"],
            [5, 1, 0, 6, 0, "MODEL"],
            [6, 4, 0, 6, 1, "CONDITIONING"],
            [7, 4, 1, 6, 2, "CONDITIONING"],
            [8, 5, 0, 6, 3, "LATENT"],
            [9, 6, 0, 7, 0, "LATENT"],
            [10, 1, 2, 7, 1, "VAE"],
            [11, 7, 0, 8, 0, "IMAGE"],
        ],
    }
    wf = ui_to_api(ui, OBJECT_INFO).workflow
    assert wf["6"]["inputs"]["positive"] == ["2", 0]
    assert wf["6"]["inputs"]["negative"] == ["3", 0]  # 以前は最初の同じ型(2)になっていた


def test_map_widgets_list_and_dict_values_do_not_crash():
    # seed の次の値が配列・辞書でも TypeError(unhashable)にならない
    got = map_widgets(OBJECT_INFO["KSampler"], [42, [1, 2], 25, 7, "euler", "normal", 1])
    assert got["seed"] == 42 and got["steps"] == [1, 2]
    got = map_widgets(OBJECT_INFO["KSampler"], [42, {"k": 1}, 25])
    assert got["seed"] == 42


def _broken_ui() -> dict[str, Any]:
    ui = sdxl_ui()
    ui["nodes"].append({"type": "KSampler"})  # id の無いノード
    return ui


def test_ui_to_api_unexpected_shape_is_workflow_error():
    with pytest.raises(WorkflowError, match="API 形式に直せません"):
        ui_to_api(_broken_ui(), OBJECT_INFO)


def test_cli_bench_reports_converter_errors_in_japanese(tmp_path, capsys):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps(_broken_ui()), encoding="utf-8")
    with FakeComfy() as fake:
        code = cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--workflow", str(p), "--json"])
    assert code == 1
    assert "API 形式に直せません" in json.loads(capsys.readouterr().out)["error"]
    assert fake.mutations == []


def test_bench_records_timing_with_fake_comfyui():
    """時間の内訳(#132): /history の時刻とログの進み具合の行から。HTTP だけ。"""
    with FakeComfy() as fake:
        res = bench.run(_opts(fake.url, free_before=True, cold_load=True), log=_quiet)
    t = res.timing
    assert t["total_s"] is not None and t["cold_load"] is True
    assert t["queue_s"] == pytest.approx(0.1, abs=0.05)
    assert t["sampling_s"] == 1.0 and t["sec_per_step"] == 0.04  # テンプレートの 25 ステップの行だけ
    assert t["load_s"] == pytest.approx(0.4, abs=0.05) and t["decode_s"] == pytest.approx(1.0, abs=0.05)


def _stage_log(t0: float, first_elapsed: str = "00:00") -> list[dict[str, Any]]:
    """t0 + 10 秒にサンプラの最初の行、t0 + 20 秒に最後の行(20 ステップ)。"""

    def at(dt: float) -> str:
        return datetime.fromtimestamp(t0 + dt).isoformat()

    return [
        {"t": at(10), "m": f"\r  0%|          | 0/20 [{first_elapsed}<?, ?it/s]"},
        {"t": at(20), "m": "\r100%|##########| 20/20 [00:10<00:00,  2.0it/s]"},
    ]


def _stage_entry(t0: float) -> dict[str, Any]:
    msgs = [
        ["execution_start", {"timestamp": t0 * 1000}],
        ["execution_success", {"timestamp": (t0 + 30) * 1000}],
    ]
    return {"status": {"messages": msgs}}


def test_stage_peaks_splits_at_the_sampler_boundaries():
    """#100 の宿題 2: torch の確保量を、サンプラの前・最中・後に分ける。境目のまわりはどの段にも入れない。"""
    t0 = 1_700_000_000.0
    # 前は 2 GB、境目の直前(9.95 秒)に 9 GB、最中は 6 GB、後(VAE)は 4 GB に戻ってから 7 GB
    samples = [(t0 + 1, 2 * GIB), (t0 + 9.95, 9 * GIB), (t0 + 12, 6 * GIB), (t0 + 19, 6 * GIB)]
    samples += [(t0 + 21, 4 * GIB), (t0 + 25, 7 * GIB)]
    st = bench.stage_peaks(samples, _stage_entry(t0), _stage_log(t0), local=True, steps={20})
    assert st["sampler_runs"] == 1
    assert st["before_sampling"] == {
        "peak_gb": 2.0,
        "min_gb": 2.0,
        "samples": 1,
    }  # 9.95 秒は境目の 0.1 秒の内
    assert st["sampling"] == {"peak_gb": 6.0, "min_gb": 6.0, "samples": 2}
    assert st["after_sampling"] == {"peak_gb": 7.0, "min_gb": 4.0, "samples": 2}
    # 最初の行の経過が 0 秒でなければ、始まりは 1 秒までずれるので、境目の幅を 1 秒にする
    late = bench.stage_peaks(samples, _stage_entry(t0), _stage_log(t0 + 1, "00:01"), local=True, steps={20})
    assert late["before_sampling"]["samples"] == 1 and late["sampling"]["samples"] == 2


def test_stage_peaks_is_none_without_what_it_needs():
    t0 = 1_700_000_000.0
    samples = [(t0 + 12, 6 * GIB)]
    entry, log = _stage_entry(t0), _stage_log(t0)
    assert bench.stage_peaks(samples, entry, log, local=False, steps={20}) is None  # 時計が別
    assert bench.stage_peaks([], entry, log, local=True, steps={20}) is None
    assert bench.stage_peaks(samples, entry, log, local=True, steps={30}) is None  # 合うサンプラの行が無い
    assert bench.stage_peaks(samples, {}, log, local=True, steps={20}) is None  # 実行の時刻が無い
    # 読めた回の無い段は None
    st = bench.stage_peaks(samples, entry, log, local=True, steps={20})
    assert st["before_sampling"] is None and st["after_sampling"] is None and st["sampling"]["samples"] == 1


def test_cli_bench_json_has_timing(capsys):
    """bench --json に時間の内訳(#132)。"""
    with FakeComfy() as fake:
        assert cli.main(["bench", "image_sdxl_simple", "--url", fake.url, "--json"]) == 0
    out = capsys.readouterr().out
    t = json.loads(out)["timing"]
    assert set(t) == {
        "queue_s",
        "load_s",
        "sampling_s",
        "sec_per_step",
        "decode_s",
        "save_s",
        "total_s",
        "cold_load",
    }
    assert t["sampling_s"] == 1.0 and t["sec_per_step"] == 0.04
    # 段ごとの山(#100 の宿題 2)。偽の ComfyUI はすぐ終わり、ログの時刻(サンプラは 0.5〜1.6 秒)は
    # 先の時刻なので、段に分けた形は返るが、読めた回は無い
    # (段への割り付けは test_stage_peaks_* が合成の値で確かめる)
    st = json.loads(out)["stages"]
    assert st["sampler_runs"] == 1 and set(st) == {
        "sampler_runs",
        "before_sampling",
        "sampling",
        "after_sampling",
    }


# --- 一瞬の山(#131) --------------------------------------------------------------------------------
# RTX 3070 の SDXL 1024px: VAE の復号の山 2.28 GB は約 0.19 秒だけ続き、実行の終わりに消える。
# 0.2 秒ごとに読むと、山の直前の 1.28 GB しか読めない回があった

_REAL_SLEEP = time.sleep


def test_steady_peak_and_brief_peak():
    plateau = [int(x * GIB) for x in [0.5] * 20 + [1.19, 1.28] + [2.28] * 6 + [0.03]]
    assert bench.steady_peak(plateau) == int(2.28 * GIB)
    assert bench.brief_peak(plateau) is None  # 山が読む間隔より長ければ標準のまま
    spike = [int(x * GIB) for x in [0.5] * 20 + [1.19, 1.28, 2.28, 0.03]]
    assert bench.brief_peak(spike) == (int(2.28 * GIB), int(1.19 * GIB))
    # 差が小さい(0.1 GiB 以下・最大値の 10% 以下)なら揺れても照合に効かないので決めない
    small = [int(x * GIB) for x in [0.5] * 20 + [0.55, 0.58, 0.6]]
    assert bench.brief_peak(small) is None
    # 境目: 差が 0.1 GiB を超えても最大値の 10% 以下なら、10% を超えても 0.1 GiB 以下なら、一瞬の山にしない
    base = [int(0.3 * GIB)] * 20
    assert bench.brief_peak(base + [int(1.85 * GIB)] * 3 + [2 * GIB]) is None  # 差 0.15 GiB・7.5%
    assert bench.brief_peak(base + [int(0.42 * GIB)] * 3 + [int(0.5 * GIB)]) is None  # 差 0.08 GiB・16%
    assert bench.brief_peak(base + [int(1.7 * GIB)] * 3 + [2 * GIB]) is not None  # 差 0.3 GiB・15%
    # 読めた回が少なければ決めない(実行が読む間隔の数回分より短い)
    assert bench.brief_peak([0, 5 * GIB]) is None
    assert bench.steady_peak([]) == 0 and bench.steady_peak([3, 1]) == 1


def test_default_poll_interval_is_short():
    # 0.2 秒おきでは約 0.2 秒の山を飛ばす(#131)
    assert BenchOptions(model_id="x", url="u").interval == bench.POLL_INTERVAL <= 0.05
    assert bench.Monitor(_StatsClient(), None, None).interval == bench.POLL_INTERVAL


def test_monitor_reads_ram_less_often_than_vram(monkeypatch):
    class Proc:
        calls = 0

        def memory_info(self):
            Proc.calls += 1
            return type("M", (), {"rss": GIB})()

        def children(self, recursive=False):
            return []

    clock = itertools.count()  # 1 回読むごとに 0.05 秒進む
    mon = bench.Monitor(_StatsClient(), Proc(), None, use_smi=False, ram_interval=0.2)
    mon._stop.wait = lambda t: mon._stop.is_set()  # 待たない
    n = {"i": 0}
    real = mon.sample_once

    def counted(**kw):
        real(**kw)
        n["i"] += 1
        if n["i"] >= 20:
            mon._stop.set()

    mon.sample_once = counted
    with monkeypatch.context() as mp:
        mp.setattr(bench.time, "monotonic", lambda: next(clock) * 0.05)
        mon._poll()
    assert len(mon.torch_values) == 20 and mon.peaks.samples == 20
    assert 4 <= Proc.calls <= 6  # 0.2 秒ごと(20 回 × 0.05 秒 = 1 秒)
    assert mon.peaks.ram == GIB


class _SlowFake(FakeComfy):
    """実行に時間がかかる偽の ComfyUI(読む回数を一瞬の山の判定に足りるだけ増やす)。"""

    def _history(self) -> dict[str, Any]:
        if self.history_calls < 30:
            self.history_calls += 1
            return {}
        return super()._history()


def _peak_run(monkeypatch, spike_reads: set[int], behavior: str = "ok"):
    """実行中の /system_stats の spike_reads 回目(1 から)だけ torch を 7 GB にして測る。"""
    monkeypatch.setattr(bench.time, "sleep", lambda s: _REAL_SLEEP(0.01))
    real = bench.ComfyClient.system_stats
    with _SlowFake(behavior) as fake:
        n = {"run": 0}

        def stats(self):
            d = real(self)
            if fake.submitted:
                n["run"] += 1
                if n["run"] in spike_reads:
                    d["devices"][0]["torch_vram_total"] = 7 * GIB
            return d

        monkeypatch.setattr(bench.ComfyClient, "system_stats", stats)
        res = bench.run(_opts(fake.url, free_before=True), log=_quiet)
    assert n["run"] >= bench.BRIEF_PEAK_MIN_SAMPLES
    return res


def test_brief_peak_is_kept_but_not_standard(monkeypatch):
    res = _peak_run(monkeypatch, {5})  # 1 回だけ読めた山
    m = res.measurement
    assert m.peak_vram_gb == pytest.approx(7.0)  # 値は残す
    assert m.standard is False
    assert "一瞬だけの山(7.00 GB。3 回以上読めた最大は 5.00 GB)" in m.note
    assert any("brief spike" in s or "一瞬" in s for s in res.nonstandard)
    assert any("7.00 GB" in w for w in res.warnings)
    assert res.brief_peak is True


@pytest.mark.parametrize("behavior", ["oom", "error"])
def test_brief_peak_does_not_touch_failed_runs(monkeypatch, behavior):
    # メモリ不足・失敗は今までどおり標準のまま(OOM の提出・失敗の記録を残して送る。#131)
    res = _peak_run(monkeypatch, {5}, behavior)
    m = res.measurement
    assert m.outcome == behavior and m.standard is True and res.brief_peak is False
    assert "一瞬" not in m.note


def test_peak_read_several_times_stays_standard(monkeypatch):
    res = _peak_run(monkeypatch, {5, 6, 7})  # 3 回読めた山 = 読む間隔の 2 倍以上続いた
    m = res.measurement
    assert m.peak_vram_gb == pytest.approx(7.0) and m.standard is True
    assert "一瞬" not in m.note and res.nonstandard == []


def test_size_through_a_switch_follows_its_default_branch():
    """#187: 切り替え(ComfySwitchNode)は既定の値で選ばれる側だけをたどる。Mage Flow は既定(true)で入力の画像の
    大きさ(大きさの入力が無い)、切り替えると手の大きさ(PrimitiveInt)。"""

    def wf(on: bool) -> dict:
        return {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": ["5", 0], "height": ["6", 0]}},
            "5": {
                "class_type": "ComfySwitchNode",
                "inputs": {"switch": ["9", 0], "on_true": ["4", 0], "on_false": ["7", 0]},
            },
            "6": {
                "class_type": "ComfySwitchNode",
                "inputs": {"switch": ["9", 0], "on_true": ["4", 1], "on_false": ["8", 0]},
            },
            "4": {"class_type": "GetImageSize", "inputs": {"image": ["2", 0]}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "in.png"}},
            "7": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
            "8": {"class_type": "PrimitiveInt", "inputs": {"value": 768}},
            "9": {"class_type": "PrimitiveBoolean", "inputs": {"value": on}},
        }

    assert not size_input(wf(True))
    hand = wf(False)
    assert (effective_settings(hand).width, effective_settings(hand).height) == (1024, 768)
    new, notes, got = _set_and_read(hand, 640, 512)
    assert notes == [] and got == (640, 512) and new["7"]["inputs"]["value"] == 640
    # 切り替えの値が分からなければ読まない
    unknown = wf(False)
    unknown["9"] = {"class_type": "SomeNode", "inputs": {}}
    assert not size_input(unknown)
