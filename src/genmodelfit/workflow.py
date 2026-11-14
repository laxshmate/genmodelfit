# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ComfyUI のワークフローを扱う(bench 用)。

- 公式テンプレート(画面用の形式)を、サーバーの `/object_info` を見て API 形式に直す
- 設定(幅・高さ・フレーム数・音声の長さ・ステップ数)を入力の名前で書き換える
- 実際に使う設定を API 形式のワークフローから読み取る
- サンプラーごとに使う本体の組を、画面用の形式のつながりから読み取る(update が見積もりのために使う)

ComfyUI のコードは import しない(GPL のため)。ここでは JSON だけを扱う。
"""

from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .i18n import tr
from .models import Settings

TEMPLATES_RAW = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main"

# 画面だけのノード(サーバーには無い)。Note は捨て、PrimitiveNode / Reroute は値・つながりに置き換える
UI_ONLY_SKIP = {"Note", "MarkdownNote", "Label (rgthree)"}
PRIMITIVE = "PrimitiveNode"
REROUTE = "Reroute"
# seed の後ろに画面だけの「生成後の動き」の値が入る
CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
# ノードの mode: 0=通常、2=無効(mute)、4=素通し(bypass)
MODE_NEVER = 2
MODE_BYPASS = 4
SUBGRAPH_IN = -10
SUBGRAPH_OUT = -20


# モデルのファイルとみなす拡張子(選択肢の値がこれで終わるなら「モデルのファイルが無い」と出す)
MODEL_FILE_EXT = re.compile(r"\.(safetensors|sft|ckpt|pt|pth|bin|gguf|onnx)$", re.I)


class WorkflowError(ValueError):
    """ワークフローを変換・実行できない理由(利用者に見せる文)。足りないノードで止めたときは、その名前を
    missing_nodes に持つ(#132。share の失敗の記録の材料)。"""

    missing_nodes: list[str] | None = None


_URL_PART_BAD = re.compile(r"[/\\?#%\x00-\x1f\x7f]|\.\.|^\.|^\s|\s$")


def _url_part(part: str) -> str:
    """公式テンプレートの URL に入れる名前(1 つのファイル名だけ)。`/`・`..`・`?` などで別のファイルを
    取りに行かないよう、形を確かめて符号化する。"""
    if not part or _URL_PART_BAD.search(part):
        raise WorkflowError(
            tr(
                f"公式テンプレートの名前として使えません: {part!r}",
                f"not usable as an official template name: {part!r}",
            )
        )
    return quote(part)


def template_url(name: str) -> str:
    return f"{TEMPLATES_RAW}/templates/{_url_part(name)}.json"


def template_input_url(filename: str) -> str:
    return f"{TEMPLATES_RAW}/input/{_url_part(filename)}"


def is_api_format(data: Any) -> bool:
    """API 形式({"<id>": {"class_type": ..., "inputs": {...}}})か。"""
    return (
        isinstance(data, dict)
        and bool(data)
        and "nodes" not in data
        and all(isinstance(v, dict) and "class_type" in v for v in data.values())
    )


# --- 画面用の形式 → API 形式 ----------------------------------------------------------------------


@dataclass
class _Input:
    name: str
    type: str | None
    widget: bool
    src: tuple[str, int] | None  # つながっている元(ノード ID, 出力の番号)


@dataclass
class _Node:
    id: str
    type: str
    mode: int
    widgets_values: Any
    inputs: list[_Input]
    output_types: list[str | None]
    title: str | None = None
    widget_overrides: dict[str, Any] = field(default_factory=dict)  # サブグラフの外から渡された値


def _links_map(links: Any) -> dict[int, tuple[int, int, int, int]]:
    """リンクを {id: (元, 元の出力, 先, 先の入力)} にする。トップは配列、サブグラフは辞書の形。"""
    out: dict[int, tuple[int, int, int, int]] = {}
    for link in links or []:
        if isinstance(link, dict):
            out[link["id"]] = (link["origin_id"], link["origin_slot"], link["target_id"], link["target_slot"])
        elif isinstance(link, list) and len(link) >= 5:
            out[link[0]] = (link[1], link[2], link[3], link[4])
    return out


def _flatten(
    nodes: list[dict[str, Any]],
    links: dict[int, tuple[int, int, int, int]],
    defs: dict[str, dict[str, Any]],
    prefix: str,
    out: list[_Node],
    alias: dict[tuple[str, int], tuple[str, int] | None],
    depth: int = 0,
) -> None:
    """サブグラフを展開して平らなノードの並びにする。中のノードの ID は「外の ID:中の ID」。"""
    if depth > 16:
        raise WorkflowError(tr("サブグラフの入れ子が深すぎます", "subgraphs are nested too deeply"))

    def src_of(link_id: int | None) -> tuple[str, int] | None:
        if link_id is None or link_id not in links:
            return None
        origin, slot, _, _ = links[link_id]
        return (f"{prefix}{origin}", slot)

    for n in nodes:
        nid = f"{prefix}{n['id']}"
        ntype = str(n.get("type"))
        mode = int(n.get("mode") or 0)
        if ntype in defs and mode not in (MODE_NEVER, MODE_BYPASS):
            sg = defs[ntype]
            inner = f"{nid}:"
            sub_links = _links_map(sg.get("links"))
            outer_inputs = {i.get("name"): i for i in n.get("inputs") or []}
            for k, sin in enumerate(sg.get("inputs") or []):
                oi = outer_inputs.get(sin.get("name"))
                alias[(f"{inner}{SUBGRAPH_IN}", k)] = src_of(oi.get("link")) if oi else None
            for k, sout in enumerate(sg.get("outputs") or []):
                for lid in sout.get("linkIds") or []:
                    if lid in sub_links:
                        origin, slot, _, _ = sub_links[lid]
                        alias[(nid, k)] = (f"{inner}{origin}", slot)
            child_start = len(out)
            _flatten(sg.get("nodes") or [], sub_links, defs, inner, out, alias, depth + 1)
            _apply_proxy_widgets(n, sg, sub_links, inner, out[child_start:])
            continue
        if ntype in defs:
            raise WorkflowError(
                tr(
                    f"無効・素通しのサブグラフには対応していません(ノード {nid})",
                    f"muted or bypassed subgraphs are not supported (node {nid})",
                )
            )
        inputs = [
            _Input(
                name=str(i.get("name")),
                type=i.get("type"),
                widget=bool(i.get("widget")),
                src=src_of(i.get("link")),
            )
            for i in n.get("inputs") or []
        ]
        outputs = [o.get("type") for o in n.get("outputs") or []]
        out.append(
            _Node(
                id=nid,
                type=ntype,
                mode=mode,
                widgets_values=n.get("widgets_values"),
                inputs=inputs,
                output_types=outputs,
                title=n.get("title"),
            )
        )


def _apply_proxy_widgets(
    outer: dict[str, Any],
    sg: dict[str, Any],
    sub_links: dict[int, tuple[int, int, int, int]],
    inner: str,
    children: list[_Node],
) -> None:
    """サブグラフの外に出した入力欄(proxyWidgets)の値を、中のノードへ戻す。"""
    proxies = (outer.get("properties") or {}).get("proxyWidgets") or []
    values = outer.get("widgets_values")
    if not proxies or not isinstance(values, list) or not values:
        return
    by_id = {c.id: c for c in children}
    for proxy, value in zip(proxies, values, strict=False):
        if not (isinstance(proxy, list) and len(proxy) == 2):
            continue
        node_id, widget = str(proxy[0]), str(proxy[1])
        if widget == "control_after_generate":
            continue
        if node_id == "-1":  # サブグラフの入力そのもの → つながる先の入力欄すべて
            names = [i.get("name") for i in sg.get("inputs") or []]
            if widget not in names:
                continue
            k = names.index(widget)
            for origin, slot, target, tslot in sub_links.values():
                if origin == SUBGRAPH_IN and slot == k and f"{inner}{target}" in by_id:
                    t = by_id[f"{inner}{target}"]
                    if tslot < len(t.inputs):
                        t.widget_overrides[t.inputs[tslot].name] = value
        elif f"{inner}{node_id}" in by_id:
            by_id[f"{inner}{node_id}"].widget_overrides[widget] = value


def _input_specs(info: dict[str, Any]) -> list[tuple[str, Any, dict[str, Any]]]:
    """/object_info の 1 ノード分から、入力を (名前, 型, 設定) の順の並びにする。"""
    sections = info.get("input") or {}
    order = info.get("input_order") or {}
    out: list[tuple[str, Any, dict[str, Any]]] = []
    for sec in ("required", "optional"):
        specs = sections.get(sec) or {}
        names = order.get(sec) or list(specs)
        for name in names:
            spec = specs.get(name)
            if not spec:
                continue
            typ = spec[0]
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            out.append((name, typ, opts))
    return out


def _is_widget(typ: Any, opts: dict[str, Any]) -> bool:
    if opts.get("forceInput"):
        return False
    return isinstance(typ, list) or typ in WIDGET_TYPES or typ == DYNAMIC_COMBO


def combo_options(typ: Any, opts: dict[str, Any]) -> list[Any] | None:
    if isinstance(typ, list):
        return typ
    if typ == "COMBO" and isinstance(opts.get("options"), list):
        return opts["options"]
    return None


def map_widgets(info: dict[str, Any], values: Any) -> dict[str, Any]:
    """widgets_values(画面の入力欄の値の並び)を入力の名前に割り当てる。

    seed / noise_seed の後ろの「生成後の動き」(fixed / randomize など)は飛ばす。
    """
    specs = _input_specs(info)
    if isinstance(values, dict):  # 一部のノードは名前つきで持つ
        names = {s[0] for s in specs}
        return {k: v for k, v in values.items() if k in names}
    if not isinstance(values, list):
        return {}
    out: dict[str, Any] = {}
    idx = 0

    def take(specs_: list[tuple[str, Any, dict[str, Any]]], prefix: str) -> None:
        nonlocal idx
        for name, typ, opts in specs_:
            if not _is_widget(typ, opts):
                continue
            if idx >= len(values):
                return
            val = values[idx]
            idx += 1
            out[prefix + name] = val
            if (
                (opts.get("control_after_generate") or name in ("seed", "noise_seed"))
                and idx < len(values)
                and isinstance(values[idx], str)
                and values[idx] in CONTROL_VALUES
            ):
                idx += 1
            if typ == DYNAMIC_COMBO:
                for opt in opts.get("options") or []:
                    if isinstance(opt, dict) and opt.get("key") == val:
                        take(_input_specs({"input": opt.get("inputs") or {}}), f"{prefix}{name}.")

    take(specs, "")
    return out


@dataclass
class Conversion:
    workflow: dict[str, dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


def _flat_graph(ui: dict[str, Any]) -> tuple[list[_Node], dict[tuple[str, int], tuple[str, int] | None]]:
    if not isinstance(ui, dict) or not isinstance(ui.get("nodes"), list):
        raise WorkflowError(
            tr("ワークフローの形が違います(nodes がありません)", "unexpected workflow shape (no nodes)")
        )
    defs = {
        str(sg.get("id")): sg
        for sg in ((ui.get("definitions") or {}).get("subgraphs") or [])
        if isinstance(sg, dict) and sg.get("id")
    }
    flat: list[_Node] = []
    alias: dict[tuple[str, int], tuple[str, int] | None] = {}
    _flatten(ui["nodes"], _links_map(ui.get("links")), defs, "", flat, alias)
    return flat, alias


# 本体(MODEL)を受け取って生成するノード。KSampler / KSamplerAdvanced / SamplerCustom / CFGGuider など
_SAMPLER = re.compile(r"sampler|guider", re.IGNORECASE)


def sampler_stages(ui: dict[str, Any]) -> list[list[str]]:
    """サンプラー(と CFGGuider などの guider)ごとに、MODEL の入力をさかのぼって読み込むモデルのファイル名を
    集める(UNETLoader の本体・LoraLoaderModelOnly の LoRA・ModelPatchLoader など)。

    返すのは、ファイルの組が違うサンプラーごとの組(出てきた順。同じ組は 1 回。ファイルの無い組は除く)。
    無効(mute)のノードは使わず、素通し(bypass)のノードは飛ばしてその先をたどる。ComfyUI はサンプラーを
    1 つずつ動かし、次のサンプラーの本体を載せるとき前の本体を降ろす(estimate.EXPERT_SOURCE)。
    読めない形は WorkflowError。"""
    flat, alias = _flat_graph(ui)
    by_id = {n.id: n for n in flat}

    def resolve(src: tuple[str, int] | None, depth: int = 0) -> str | None:
        """つながりの元のノード ID(サブグラフの出入り口・Reroute・素通しを越える)。"""
        if src is None or depth > 64:
            return None
        if src in alias:
            return resolve(alias[src], depth + 1)
        node = by_id.get(src[0])
        if node is None or node.mode == MODE_NEVER or node.type == PRIMITIVE:
            return None
        if node.type == REROUTE:
            return resolve(node.inputs[0].src if node.inputs else None, depth + 1)
        if node.mode == MODE_BYPASS:
            want = node.output_types[src[1]] if src[1] < len(node.output_types) else None
            for i in node.inputs:
                if i.src is not None and i.type == want:
                    return resolve(i.src, depth + 1)
            return None
        return node.id

    def files_of(node: _Node) -> set[str]:
        values = node.widgets_values
        vals = list(values.values()) if isinstance(values, dict) else list(values or [])
        vals += list(node.widget_overrides.values())
        return {
            v.replace("\\", "/").rsplit("/", 1)[-1]
            for v in vals
            if isinstance(v, str) and MODEL_FILE_EXT.search(v)
        }

    out: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for n in flat:
        if n.mode in (MODE_NEVER, MODE_BYPASS) or not _SAMPLER.search(n.type):
            continue
        starts = [i.src for i in n.inputs if i.type == "MODEL" and i.src is not None]
        if not starts:
            continue
        found: set[str] = set()
        visited: set[str] = set()
        stack = [resolve(s) for s in starts]
        while stack:
            nid = stack.pop()
            if nid is None or nid in visited:
                continue
            visited.add(nid)
            node = by_id[nid]
            found |= files_of(node)
            stack += [resolve(i.src) for i in node.inputs if i.type == "MODEL" and i.src is not None]
        key = frozenset(found)
        if found and key not in seen:
            seen.add(key)
            out.append(sorted(found))
    return out


def ui_to_api(ui: dict[str, Any], object_info: dict[str, Any]) -> Conversion:
    """画面用の形式(テンプレート)を API 形式に直す。直せないときは WorkflowError。"""
    try:
        return _ui_to_api(ui, object_info)
    except WorkflowError:
        raise
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, RecursionError) as e:
        raise WorkflowError(
            tr(
                f"ワークフローを API 形式に直せませんでした(想定していない形です: {type(e).__name__})。"
                "ComfyUI の「API 形式で書き出し」で保存した JSON を --workflow で渡してください",
                f"could not convert the workflow to the API format (unexpected shape: {type(e).__name__});"
                ' pass a JSON saved with ComfyUI\'s "Export (API)" via --workflow',
            )
        ) from e


def _runs(n: _Node) -> bool:
    """API 形式に入る(実行される)ノードか。画面だけのもの・止めた・素通しのノードは入らない。"""
    if n.type in UI_ONLY_SKIP or n.type in (PRIMITIVE, REROUTE):
        return False
    return n.mode not in (MODE_NEVER, MODE_BYPASS)


def missing_nodes(ui: dict[str, Any], object_info: dict[str, Any]) -> list[str]:
    """画面用のワークフローが使い、ComfyUI(/object_info)に無いノードの種類(並べたもの)。
    bench(ui_to_api)と、share・measure の前の確かめ(#124)が同じこれを使う。"""
    if not isinstance(object_info, dict):
        return []  # 壊れた /object_info では確かめない(L11b)
    try:
        flat, _alias = _flat_graph(ui)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return []
    return sorted({n.type for n in flat if _runs(n) and n.type not in object_info})


def missing_nodes_message(names: list[str], *, workflow_hint: bool = True) -> str:
    """足りないノードの文。workflow_hint が偽なら --workflow の案内を付けない(measure・share には無い)。"""
    joined = ", ".join(names)
    if not workflow_hint:
        return tr(
            f"ComfyUI に無いノードを使っています: {joined}"
            "(ComfyUI が古いか、カスタムノードが要ります。ComfyUI を更新すると測れます)",
            f"uses nodes that ComfyUI does not have: {joined}"
            " (ComfyUI is old or custom nodes are needed; update ComfyUI to measure it)",
        )
    return tr(
        f"ComfyUI に無いノードを使っています: {joined}"
        "(ComfyUI が古いか、カスタムノードが要ります。"
        "ComfyUI を更新するか --workflow で API 形式を渡してください)",
        f"uses nodes that ComfyUI does not have: {joined}"
        " (ComfyUI is old or custom nodes are needed;"
        " update ComfyUI or pass the API format via --workflow)",
    )


def _ui_to_api(ui: dict[str, Any], object_info: dict[str, Any]) -> Conversion:
    missing = missing_nodes(ui, object_info)
    if missing:
        err = WorkflowError(missing_nodes_message(missing))
        err.missing_nodes = missing  # 失敗の記録の材料(#132。ノードの名前だけ)
        raise err
    flat, alias = _flat_graph(ui)
    by_id = {n.id: n for n in flat}
    warnings: list[str] = []

    def resolve(src: tuple[str, int] | None, depth: int = 0) -> tuple[str, Any] | None:
        """つながりの元をたどる。("node", [id, slot]) / ("value", 値) / None(元が無い)。"""
        if src is None or depth > 64:
            return None
        if src in alias:
            return resolve(alias[src], depth + 1)
        node = by_id.get(src[0])
        if node is None:
            return None
        if node.type == PRIMITIVE:
            wv = node.widgets_values
            return ("value", wv[0]) if isinstance(wv, list) and wv else None
        if node.type == REROUTE:
            return resolve(node.inputs[0].src if node.inputs else None, depth + 1)
        if node.mode == MODE_NEVER:
            return None
        if node.mode == MODE_BYPASS:  # 素通し: 同じ型の入力をそのまま出す
            want = node.output_types[src[1]] if src[1] < len(node.output_types) else None
            # 画面(ComfyUI の frontend)と同じく、出力と同じ番号の入力を先に見て、だめなら最初の同じ型
            if src[1] < len(node.inputs):
                same = node.inputs[src[1]]
                if same.src is not None and same.type == want:
                    return resolve(same.src, depth + 1)
            for i in node.inputs:
                if i.src is not None and i.type == want:
                    return resolve(i.src, depth + 1)
            return None
        return ("node", [node.id, src[1]])

    api: dict[str, dict[str, Any]] = {}
    for n in flat:
        if not _runs(n):
            continue
        info = object_info[n.type]
        inputs = map_widgets(info, n.widgets_values)
        inputs.update(n.widget_overrides)
        for i in n.inputs:
            if i.src is None:
                continue
            r = resolve(i.src)
            if r is None:
                if not i.widget:
                    warnings.append(
                        tr(
                            f"ノード {n.id}({n.type})の入力 {i.name} の元がありません",
                            f"node {n.id} ({n.type}): input {i.name} has no source",
                        )
                    )
                continue
            inputs[i.name] = r[1]
        # テンプレートが作られた後に足された必須の入力欄は、値が入っていない。画面(frontend)と同じく
        # 部品の定義の既定値で埋める(例: ComfyUI 0.40 の MoGeInference の refine_steps)
        required = (info.get("input") or {}).get("required") or {}
        for name, spec in required.items():
            if name in inputs or not isinstance(spec, list) or not spec:
                continue
            typ, opts = spec[0], (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {})
            if _is_widget(typ, opts) and "default" in opts:
                inputs[name] = opts["default"]
                warnings.append(
                    tr(
                        f"ノード {n.id}({n.type})の入力 {name} はテンプレートに値が無いので"
                        f"既定値 {opts['default']!r} を使った",
                        f"node {n.id} ({n.type}): input {name} has no value in the template;"
                        f" used the default {opts['default']!r}",
                    )
                )
        api[n.id] = {"class_type": n.type, "inputs": inputs, "_meta": {"title": n.title or n.type}}
    if not any(object_info.get(v["class_type"], {}).get("output_node") for v in api.values()):
        raise WorkflowError(
            tr(
                "保存・出力のノードがありません(実行しても何も動きません)",
                "no save or output node (running it would do nothing)",
            )
        )
    return Conversion(workflow=api, warnings=warnings)


# --- 確認 -------------------------------------------------------------------------------------


def output_nodes(api: dict[str, dict[str, Any]], object_info: dict[str, Any]) -> set[str]:
    """保存・出力のノード(/object_info の output_node)。"""
    return {nid for nid, n in api.items() if (object_info.get(n.get("class_type")) or {}).get("output_node")}


def reachable_nodes(api: dict[str, dict[str, Any]], object_info: dict[str, Any]) -> set[str]:
    """出力のノードから、つながりを逆にたどって届くノード(実際に実行されるもの)。

    出力のノードが分からないときは全部のノードを返す。
    """
    todo = list(output_nodes(api, object_info))
    if not todo:
        return set(api)
    seen: set[str] = set()
    while todo:
        nid = todo.pop()
        if nid in seen or nid not in api:
            continue
        seen.add(nid)
        for v in (api[nid].get("inputs") or {}).values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)):
                todo.append(str(v[0]))
    return seen


def check_inputs(api: dict[str, dict[str, Any]], object_info: dict[str, Any]) -> list[str]:
    """選択肢の入力(モデルのファイル名など)がサーバーにあるか。足りないものを文で返す。

    画像の読み込み(image_upload)は bench がアップロードするので見ない。
    """
    problems: list[str] = []
    for nid, node in api.items():
        info = object_info.get(node["class_type"]) or {}
        for name, typ, opts in _input_specs(info):
            if opts.get("image_upload") or opts.get("audio_upload") or opts.get("video_upload"):
                continue
            choices = combo_options(typ, opts)
            val = node["inputs"].get(name)
            if choices is None or val is None or isinstance(val, list):
                continue
            if val not in choices:
                is_file = isinstance(val, str) and MODEL_FILE_EXT.search(val)
                problems.append(
                    tr(
                        f"{'モデルのファイル' if is_file else '選択肢'}がサーバーにありません: "
                        f"ノード {nid}({node['class_type']})の {name}={val}",
                        f"{'model file' if is_file else 'choice'} not on the server: "
                        f"node {nid} ({node['class_type']}) {name}={val}",
                    )
                )
    return problems


def upload_inputs(api: dict[str, dict[str, Any]], object_info: dict[str, Any]) -> list[tuple[str, str, str]]:
    """アップロードが要る入力(ノード ID, 入力名, ファイル名)のうち、サーバーに無いもの。"""
    out: list[tuple[str, str, str]] = []
    for nid, node in api.items():
        info = object_info.get(node["class_type"]) or {}
        for name, typ, opts in _input_specs(info):
            if not opts.get("image_upload"):
                continue
            val = node["inputs"].get(name)
            choices = combo_options(typ, opts) or []
            if isinstance(val, str) and val and val not in choices:
                out.append((nid, name, val))
    return out


# --- 設定の書き換え -----------------------------------------------------------------------------

_SIZE_SKIP = re.compile(r"^(Save|Load|Preview)")


def _literal(node: dict[str, Any], key: str) -> bool:
    v = node["inputs"].get(key)
    return key in node["inputs"] and not isinstance(v, list)


def _size_nodes(api: dict[str, dict[str, Any]]) -> list[str]:
    """幅・高さを持つノード(空の潜在画像・動画の潜在など)。"""
    return [
        nid
        for nid, n in api.items()
        if "width" in n["inputs"] and "height" in n["inputs"] and not _SIZE_SKIP.match(n["class_type"])
    ]


def _ratio(label: Any) -> tuple[float, float] | None:
    """ResolutionSelector の縦横比の選択肢(「16:9 (Widescreen)」など)の先頭の 幅:高さ。"""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", str(label))
    return (float(m.group(1)), float(m.group(2))) if m and float(m.group(1)) and float(m.group(2)) else None


def _resolution_selector(node: dict[str, Any]) -> tuple[int, int] | None:
    """ResolutionSelector の幅・高さ(ComfyUI の計算: 総画素 = メガ画素 × 1024²、縦横比で分け、
    multiple の倍数に丸める。comfy_extras/nodes_resolution.py:80-86@6e8488e)。
    入力が他のノードから来るなら None。"""
    inp = node["inputs"]
    if not all(_literal(node, k) for k in ("aspect_ratio", "megapixels")):
        return None
    ratio = _ratio(inp["aspect_ratio"])
    multiple = inp.get("multiple", 8)
    try:
        mp, mult = float(inp["megapixels"]), int(multiple)
    except (TypeError, ValueError):
        return None
    if ratio is None or not math.isfinite(mp) or mp <= 0 or mult <= 0:
        return None
    scale = math.sqrt(mp * 1024 * 1024 / (ratio[0] * ratio[1]))
    return round(ratio[0] * scale / mult) * mult, round(ratio[1] * scale / mult) * mult


# 大きさの値をたどる深さの上限(つながりが輪になっていても止まる)
_SIZE_DEPTH = 8
# 入力の画像を決まった幅×高さに縮めるノード(ResizeImageMaskNode の scale dimensions)の入力の名前
_RESIZE_DIMS = {0: "resize_type.width", 1: "resize_type.height"}
# 大きさとして通す式の形(空白を除き、変数は X)。利用者の数をそのまま・倍数に丸める・半分にする(LTX は
# 後で 2 倍に上げる)だけ。公式テンプレートにある形(2026-11-14)。ほかの形は大きさを読まない(#187)
_SIZE_EXPRS = [
    re.compile(p)
    for p in (
        r"X",
        r"X/2",
        r"X&-(\d+)",
        r"floor\(X/(\d+)\)\*\1",
        r"floor\(X/(\d+)\)\*\1/2",
        r"\(X//(\d+)\)\*\1",
        r"max\((\d+),floor\(X/(\d+)\)\*\2\)",
        r"max\((\d+),\(int\(X\)//(\d+)\)\*\2\)",
        r"max\(\(\(X\+(\d+)\)//(\d+)\)\*\2,(\d+)\)",
    )
]
# 大きさとして通す ImageScaleBy の倍率(LTX 2 の半分で作って 2 倍に上げる形)
_SIZE_SCALES = (0.5, 1)
# 大きさのもとの PrimitiveInt を使ってよい入力(これ以外にも使われていれば書き換えない)
_SIZE_KEYS = {"width", "height", "resize_type.width", "resize_type.height", "target_width", "target_height"}


def _size_expr(expr: Any, var: str) -> bool:
    """式が、利用者の数 var を大きさとして通す形(_SIZE_EXPRS)か。"""
    if not isinstance(expr, str) or not re.fullmatch(r"[A-Za-z_]\w*", var):
        return False
    s = re.sub(r"\s+", "", expr)
    s = re.sub(rf"(?<![A-Za-z_0-9]){re.escape(var)}(?![A-Za-z_0-9])", "X", s)
    return any(p.fullmatch(s) for p in _SIZE_EXPRS)


@dataclass(frozen=True)
class _Root:
    """大きさの値のもと。kind が set なら node の key に書けばその値が変わる(直接の値・PrimitiveInt の値・
    縮めるノードの幅と高さ)。rs は ResolutionSelector が大きさのノードに直接渡す値(node の出力 index)で、
    書き換える根は無い(大きさのノードの入力を置き換える)。fixed は、式・ImageScaleBy を通った先の
    ResolutionSelector で、大きさのノードの入力を置き換えると途中の計算が抜けるので書き換えない。"""

    kind: str
    node: str
    key: str | int


def _root(api: dict[str, dict[str, Any]], v: Any, depth: int = 0) -> tuple[Any, _Root | None]:
    """つながり v(["ノード", 出力の番号])が運ぶ大きさの値と、そのもと(_Root)。分からなければ (None, None)。
    たどるもの: PrimitiveInt(値が別のノードから来ればその先)、ResolutionSelector(ComfyUI と同じ計算)、
    入力が 1 つだけつながった ComfyMathExpression(利用者の数を丸める・半分にして後で 2 倍に上げるなど。
    値はつながった数のまま。式は ComfyUI が実行時に計算する)、GetImageSize(幅・高さ)の画像が
    ResizeImageMaskNode の scale dimensions で決まった幅×高さに縮めたものなら、その幅・高さ。
    入力の画像そのものの大きさ(LoadImage など)は分からない(大きさの入力ではない)。#187"""
    if depth > _SIZE_DEPTH or not (isinstance(v, list) and len(v) == 2):
        return None, None
    nid, out = str(v[0]), v[1]
    src = api.get(nid)
    if src is None:
        return None, None
    ct, inp = src["class_type"], src["inputs"]
    if ct == "PrimitiveInt":
        if _literal(src, "value"):
            return _positive(inp["value"], _Root("set", nid, "value"))
        # 値が別のノード(ResolutionSelector など)から来ても、書くのはこの PrimitiveInt
        # (利用者が数を打つのと同じ。後ろの式はそのまま効く)
        value, _ = _root(api, inp.get("value"), depth + 1)
        return (None, None) if value is None else (value, _Root("set", nid, "value"))
    if ct == "ResolutionSelector" and out in (0, 1):
        wh = _resolution_selector(src)
        return (None, None) if wh is None else (wh[out], _Root("rs", nid, out))
    if ct == "ComfyMathExpression":
        links = [(k, x) for k, x in inp.items() if k.startswith("values.") and isinstance(x, list)]
        if len(links) != 1 or not _size_expr(inp.get("expression"), links[0][0].removeprefix("values.")):
            return None, None
        return _through(*_root(api, links[0][1], depth + 1))
    if ct == "GetImageSize" and out in (0, 1):
        return _through(*_image_size(api, inp.get("image"), out, depth + 1))
    if ct == "ComfySwitchNode":
        # 切り替えは、既定の値で選ばれる側だけをたどる(ComfyUI は選んだ側だけを要求する。
        # comfy_extras/nodes_logic.py:146-150。#187 の Mage Flow: 既定は入力の画像の大きさ)
        on = _switch_value(api, inp.get("switch"), depth + 1)
        if on is None:
            return None, None
        return _through(*_root(api, inp.get("on_true" if on else "on_false"), depth + 1))
    return None, None


def _switch_value(api: dict[str, dict[str, Any]], v: Any, depth: int) -> bool | None:
    """切り替えの値(直接の真偽か、つながった PrimitiveBoolean の値)。分からなければ None。"""
    if isinstance(v, bool):
        return v
    if depth > _SIZE_DEPTH or not (isinstance(v, list) and len(v) == 2):
        return None
    src = api.get(str(v[0]))
    if (
        src is None
        or src["class_type"] != "PrimitiveBoolean"
        or not isinstance(src["inputs"].get("value"), bool)
    ):
        return None
    return src["inputs"]["value"]


def _positive(value: Any, root: _Root | None) -> tuple[Any, _Root | None]:
    """正の数だけを大きさにする(0 は ImageScale などで「縦横比から自動」)。"""
    try:
        return (value, root) if value is not None and float(value) > 0 else (None, None)
    except (TypeError, ValueError):
        return None, None


def _through(value: Any, root: _Root | None) -> tuple[Any, _Root | None]:
    """式・画像の大きさを通った先のもと。ResolutionSelector(rs)なら、大きさのノードの入力を置き換えると
    途中の計算(LTX の a/2 など)が抜けるので、書き換えない(fixed)。"""
    if root is not None and root.kind == "rs":
        return value, _Root("fixed", root.node, root.key)
    return value, root


def _image_size(api: dict[str, dict[str, Any]], img: Any, out: int, depth: int) -> tuple[Any, _Root | None]:
    """画像のつながり img の幅(out 0)・高さ(1)の値とそのもと(GetImageSize がたどる先。#187)。
    決まった幅×高さに縮める ResizeImageMaskNode(scale dimensions)、幅と高さを持つノード(EmptyImage・
    ImageScale など)はその幅・高さ。ImageScaleBy はその前の画像の大きさのまま(LTX 2 の、半分で作って
    後で 2 倍に上げる形。式の a/2 と同じく、値は利用者の数)。入力の画像そのもの(LoadImage など)は
    分からない。"""
    if depth > _SIZE_DEPTH or not (isinstance(img, list) and len(img) == 2):
        return None, None
    rid = str(img[0])
    src = api.get(rid)
    if src is None:
        return None, None
    ct, inp = src["class_type"], src["inputs"]
    if ct == "ResizeImageMaskNode":
        if inp.get("resize_type") != "scale dimensions":
            return None, None
        key = _RESIZE_DIMS[out]
    elif ct == "ImageScaleBy":
        if inp.get("scale_by") not in _SIZE_SCALES:
            return None, None
        return _through(*_image_size(api, inp.get("image"), out, depth + 1))
    elif "width" in inp and "height" in inp and not _SIZE_SKIP.match(ct):
        key = ("width", "height")[out]
    else:
        return None, None
    if _literal(src, key):
        return _positive(inp[key], _Root("set", rid, key))
    return _root(api, inp.get(key), depth + 1)


def _size_value(api: dict[str, dict[str, Any]], nid: str, key: str) -> Any:
    """大きさのノードの幅・高さの値。直接の値か、つながりをたどった値(_root。PrimitiveInt・ResolutionSelector・
    利用者の数の式・決まった幅×高さに縮めた入力の画像)。分からなければ None。"""
    v = api[nid]["inputs"].get(key)
    if not isinstance(v, list):
        return v
    return _root(api, v)[0]


def _settable(api: dict[str, dict[str, Any]], nid: str, key: str) -> bool:
    return _size_value(api, nid, key) is not None


def _other_users(api: dict[str, dict[str, Any]], link: list[Any]) -> list[str]:
    """link(ResolutionSelector の出力)を、大きさのノードの幅・高さ以外で使うノード。
    画像の縮小・計算のノードが同じ値を使っていると、大きさのノードだけ書き換えても実際に動く大きさとずれる。"""
    size = set(_size_nodes(api))
    return sorted(
        nid
        for nid, n in api.items()
        for k, v in n["inputs"].items()
        if isinstance(v, list)
        and len(v) == 2
        and str(v[0]) == str(link[0])
        and v[1] == link[1]
        and not (nid in size and k in ("width", "height"))
    )


def _non_size_users(
    api: dict[str, dict[str, Any]], pid: str, depth: int = 0, seen: set[str] | None = None
) -> list[str]:
    """PrimitiveInt pid の値を、大きさの入力(_SIZE_KEYS)・式・PrimitiveInt 以外に使うノード。値をそのまま通す
    切り替え(ComfySwitchNode)は、その先を同じ決まりでたどる(切り替えの先がコマ数などなら、それも数える)。"""
    seen = set() if seen is None else seen
    if pid in seen or depth > _SIZE_DEPTH:
        return []
    seen.add(pid)
    out: set[str] = set()
    for nid, n in api.items():
        for k, v in n["inputs"].items():
            if not (isinstance(v, list) and len(v) == 2 and str(v[0]) == pid) or k in _SIZE_KEYS:
                continue
            ct = n["class_type"]
            if ct in ("ComfyMathExpression", "PrimitiveInt"):
                continue
            if ct == "ComfySwitchNode" and k in ("on_true", "on_false"):
                out.update(_non_size_users(api, nid, depth + 1, seen))
                continue
            out.add(nid)
    return sorted(out)


def size_input(api: dict[str, dict[str, Any]]) -> bool:
    """テンプレートに大きさ(幅・高さ)の入力があるか: 大きさを決めるノード(_size_nodes)に、値の分かる幅と高さが
    あるか(直接の値、PrimitiveInt、ResolutionSelector。_size_value)。無いテンプレート(入力の画像から作る
    深度推定・背景除去など)では、bench は設定に幅・高さを書かない。
    判定と contrib はその記録で見分ける(estimate.no_size_input。#90)。"""
    return any(_settable(api, nid, "width") and _settable(api, nid, "height") for nid in _size_nodes(api))


FRAME_KEYS = ("length", "frames", "num_frames", "video_frames")
SECONDS_KEYS = ("seconds", "duration", "audio_length")


def apply_settings(
    api: dict[str, dict[str, Any]],
    *,
    width: int | None = None,
    height: int | None = None,
    frames: int | None = None,
    seconds: float | None = None,
    steps: int | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """設定を入力の名前で書き換えた写しと、書き換えられなかった理由を返す。"""
    wf = copy.deepcopy(api)
    notes: list[str] = []

    def set_all(keys: tuple[str, ...], value: Any, nodes: list[str], label: tuple[str, str]) -> None:
        hit = False
        for nid in nodes:
            n = wf[nid]
            for k in keys:
                if k not in n["inputs"]:
                    continue
                if _literal(n, k):
                    n["inputs"][k] = value
                    hit = True
                elif k in ("width", "height") and _settable(api, nid, k):
                    _, root = _root(api, api[nid]["inputs"][k])
                    if root is not None and root.kind == "set":
                        # 値のもと(PrimitiveInt の値・縮めるノードの幅と高さ)を書く。同じ値を使うノード
                        # (式・入力の画像を縮めるノードなど)もそろって変わる(#187)。PrimitiveInt が
                        # 大きさ以外(コマ数など)にも使われていれば、ほかの設定まで変わるので書かない
                        odd = (
                            _non_size_users(api, root.node)
                            if api[root.node]["class_type"] == "PrimitiveInt"
                            else []
                        )
                        if odd:
                            notes.append(
                                tr(
                                    f"ノード {nid} の {k} の値(ノード {root.node})は"
                                    f"ノード {', '.join(odd)} も大きさ以外に使うので"
                                    f" {label[0]} を変えられません",
                                    f"node {nid}: the value of {k} (node {root.node}) is also used by node"
                                    f" {', '.join(odd)} for something other than the size,"
                                    f" so the {label[1]} cannot be changed",
                                )
                            )
                            continue
                        roots[(root.node, root.key)].add((k, value))
                        hit = True
                        continue
                    if root is not None and root.kind == "fixed":
                        notes.append(
                            tr(
                                f"ノード {nid} の {k} は ResolutionSelector から計算を通って来るので"
                                f" {label[0]} を変えられません",
                                f"node {nid}: {k} comes from a ResolutionSelector through a calculation,"
                                f" so the {label[1]} cannot be changed",
                            )
                        )
                        continue
                    # ResolutionSelector から来る大きさ: このノードの入力を値に置き換える
                    # (元のノードは書き換えない。ほかの大きさのノードも同じ回で置き換わる)。
                    # 同じ値をほかのノードも使うなら、動く大きさが混ざるので変えない(bench は生成の前に止まる)
                    others = _other_users(api, api[nid]["inputs"][k])
                    if others:
                        notes.append(
                            tr(
                                f"ノード {nid} の {k} の値はノード {', '.join(others)} も使うので"
                                f" {label[0]} を変えられません",
                                f"node {nid}: the value of {k} is also used by node {', '.join(others)},"
                                f" so the {label[1]} cannot be changed",
                            )
                        )
                        continue
                    n["inputs"][k] = value
                    hit = True
                else:
                    notes.append(
                        tr(
                            f"ノード {nid} の {k} は他のノードから来るので {label[0]} を変えられません",
                            f"node {nid}: {k} comes from another node, so the {label[1]} cannot be changed",
                        )
                    )
        if not hit:
            notes.append(tr(f"{label[0]} を設定できるノードがありません", f"no node can set the {label[1]}"))

    size = _size_nodes(wf)
    # 書く値のもと → (幅か高さか, 値)。幅と高さが同じもとから来て値が違えば、書けない
    roots: dict[tuple[str, str | int], set[tuple[str, int]]] = defaultdict(set)
    if width is not None:
        set_all(("width",), int(width), size, ("幅", "width"))
    if height is not None:
        set_all(("height",), int(height), size, ("高さ", "height"))
    for (rid, rkey), uses in roots.items():
        values = {v for _, v in uses}
        if len(values) > 1:
            notes.append(
                tr(
                    f"幅と高さが同じ値(ノード {rid} の {rkey})から来るので、違う幅と高さにできません",
                    f"the width and height come from the same value (node {rid}, {rkey}),"
                    " so they cannot differ",
                )
            )
            continue
        wf[rid]["inputs"][rkey] = values.pop()
    if frames is not None:
        set_all(FRAME_KEYS, int(frames), size, ("フレーム数", "frame count"))
    if seconds is not None:
        set_all(SECONDS_KEYS, float(seconds), list(wf), ("長さ(秒)", "length (seconds)"))
    if steps is not None:
        set_all(("steps",), int(steps), list(wf), ("ステップ数", "step count"))
    return wf, notes


def set_output_prefix(api: dict[str, dict[str, Any]], prefix: str) -> None:
    """保存ノードの filename_prefix をそろえる(利用者の出力に混ざらないように)。"""
    for n in api.values():
        if n["class_type"].startswith("Save") and isinstance(n["inputs"].get("filename_prefix"), str):
            n["inputs"]["filename_prefix"] = prefix


def _first(api: dict[str, dict[str, Any]], keys: tuple[str, ...], nodes: list[str] | None = None) -> Any:
    for nid in nodes if nodes is not None else list(api):
        n = api[nid]
        for k in keys:
            if _literal(n, k):
                return n["inputs"][k]
    return None


def effective_settings(api: dict[str, dict[str, Any]], precision: str | None = None) -> Settings:
    """API 形式のワークフローから、実際に使う設定を読む。幅・高さは大きさの入力があるとき(size_input)だけ
    書く(無いテンプレートの実測は大きさを持たない。estimate.no_size_input がそれで見分ける)。
    枚数(batch_size)は大きさを決めるノード、無ければ名前に Latent を含むノード(音声の潜在など)から読む
    (MoGe の batch_size のような処理の単位は枚数ではない。#90)。"""
    size = _size_nodes(api)
    latent = [nid for nid in size if "Latent" in api[nid]["class_type"]] or size
    batch_nodes = latent or [nid for nid, n in api.items() if "Latent" in n["class_type"]]
    sized = size_input(api)

    def num(v: Any, cast: type) -> Any:
        try:
            return cast(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    batch = num(_first(api, ("batch_size",), batch_nodes), int) or 1

    def size_of(key: str) -> Any:
        for nid in latent:
            v = _size_value(api, nid, key)
            if v is not None:
                return v
        return None

    return Settings(
        width=num(size_of("width"), int) if sized else None,
        height=num(size_of("height"), int) if sized else None,
        frames=num(_first(api, FRAME_KEYS, latent), int),
        seconds=num(_first(api, SECONDS_KEYS), float),
        steps=num(_first(api, ("steps",)), int),
        precision=precision,
        batch=batch,
    )


# --- 本体の読み込みノードから精度を推す(update が使う。#30) ------------------------------------------

# 本体を読み込むノードの型(優先する順)。公式テンプレートで本体を読むもの。ここに無い型からは推さない
MAIN_LOADERS = (
    "UNETLoader",
    "UnetLoaderGGUF",
    "CheckpointLoaderSimple",
    "CheckpointLoader",
    "ImageOnlyCheckpointLoader",
    "WanVideoModelLoader",
    "SeedVR2LoadDiTModel",
    "MelBandRoFormerModelLoader",
    "DownloadAndLoadGIMMVFIModel",
    "DownloadAndLoadDepthAnythingV2Model",
    "LoadDA3Model",
    "LoadBackgroundRemovalModel",
    "FILM VFI",
    "DownloadAndLoadLivePortraitModels",
)
# 読み込むときに重みをこの形式に変える指定(UNETLoader の weight_dtype、WanVideoModelLoader の quantization)
LOAD_CAST = {
    "fp8_e4m3fn": "fp8",
    "fp8_e4m3fn_fast": "fp8",
    "fp8_e5m2": "fp8",
    "fp8_e4m3fn_scaled": "fp8",
    "fp8_e4m3fn_scaled_fast": "fp8",
    "fp8_e5m2_scaled": "fp8",
}
# 読み込む精度の指定(WanVideoModelLoader の base_precision、GIMM-VFI の precision など)。
# default / auto / disabled は指定なし(ファイルのまま・GPU しだい)なので推さない
LOAD_DTYPE = {
    "fp32": "fp32",
    "float32": "fp32",
    "fp16": "fp16",
    "float16": "fp16",
    "fp16_fast": "fp16",
    "bf16": "bf16",
    "bfloat16": "bf16",
}


def _node_values(node: _Node) -> list[Any]:
    values = node.widgets_values
    vals = list(values.values()) if isinstance(values, dict) else list(values or [])
    return vals + list(node.widget_overrides.values())


def _node_files(node: _Node) -> list[str]:
    return [
        v.replace("\\", "/").rsplit("/", 1)[-1]
        for v in _node_values(node)
        if isinstance(v, str) and MODEL_FILE_EXT.search(v)
    ]


def node_precision(node: _Node) -> str | None:
    """1 つの読み込みノードの指定から精度を推す。

    1. 読み込むときに fp8 に変える指定(weight_dtype / quantization が fp8_*)→ fp8
    2. 読み込むファイル名の精度の語(hf.guess_precision。fp16・bf16・fp8・int8・GGUF の Q8_0 など)
    3. 読み込む精度の指定(base_precision の fp16_fast・precision の fp32 など)
    どれも無ければ None(default・auto だけのノード、精度の語の無いファイル)。"""
    from .hf import guess_precision

    vals = [v.strip().lower() for v in _node_values(node) if isinstance(v, str)]
    for v in vals:
        if v in LOAD_CAST:
            return LOAD_CAST[v]
    for f in _node_files(node):
        p = guess_precision(f)
        if p:
            return p
    for v in vals:
        if v in LOAD_DTYPE:
            return LOAD_DTYPE[v]
    return None


def loader_precision(ui: dict[str, Any], main_names: set[str] | None = None) -> str | None:
    """画面用の形式のテンプレートから、本体の読み込みノード(MAIN_LOADERS)の指定で精度を推す。

    main_names(本体のファイル名。小文字)があれば、そのファイルを読み込むノードを使う。無ければ(ファイルの一覧が
    無いテンプレート)MAIN_LOADERS の順で最初に見つかったノード。無効(mute)・素通し(bypass)のノードは使わない。
    推せないとき・読めない形のときは None。"""
    try:
        flat, _alias = _flat_graph(ui)
    except (WorkflowError, KeyError, TypeError, ValueError, AttributeError):
        return None
    loaders = [n for n in flat if n.type in MAIN_LOADERS and n.mode not in (MODE_NEVER, MODE_BYPASS)]
    if main_names:
        hits = [n for n in loaders if {f.lower() for f in _node_files(n)} & main_names]
        if hits:
            return node_precision(hits[0])
        if any(_node_files(n) for n in loaders):
            return None  # 本体ではないファイルを読むノードから推さない
    loaders.sort(key=lambda n: MAIN_LOADERS.index(n.type))
    return node_precision(loaders[0]) if loaders else None
