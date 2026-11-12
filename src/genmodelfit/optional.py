# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""テンプレートの既定の値で読み込まれない部品(任意の部品。#81、docs/accuracy.md「ファイルの数え方」)。

公式テンプレート(画面用の形式の JSON)のつながりを、ComfyUI が実行するときと同じ向き(出力のノードから入力へ)に
たどり、届かないファイルを分ける。ComfyUI(GPL-3.0)のコードは import しない。動きは v0.43.0(426f5ee)の
コードを事実として読んだもの(行番号はそのコミットのもの):

- 実行するのは出力のノード(`OUTPUT_NODE`)から届くノードだけ。出力のノードは、出力がつながっていても
  全部実行する(execution.py:1183-1185)。lazy の入力は要求されるまでたどらない
  (comfy_execution/graph.py:153-175)
- If/Else Switch(`ComfySwitchNode`)は選んだ側だけを要求する(comfy_extras/nodes_logic.py:146-150)。
  Soft Switch(`ComfySoftSwitchNode`)は、選んだ側がつながっていなければもう一方を要求する
  (nodes_logic.py:178-188)
- 検証は lazy の枝もたどる(execution.py:946-969 で、つながった入力をすべて再帰で検証し、一覧に無い
  ファイル名は「Value not in list」で止める: execution.py:1061-1085)。→ 選ばれない枝のファイルも
  ディスクには要る
- 無効(mode 2)・素通し(mode 4)のノードは、画面(フロントエンド)が API の prompt に入れない。
  サブグラフの実体は、一番上の階層の実体の mode だけが中身に効く(入れ子の実体の mode は中身に効かない)

分け方(`ModelFile.optional`):
- `inactive`: どの出力のノードからも(lazy の枝を含めても)届かないノードにしかないファイル(無効・素通しの
  ノード、出力につながらないノード)。prompt に入らず検証もされないので、VRAM・メインメモリ・ディスク・
  download のどれにも数えない
- `switch_off`: 値の決まったスイッチの選ばれない枝にしかないファイル。実行はされないので VRAM・
  メインメモリには数えないが、検証でたどられるのでディスク・download には数える
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INACTIVE = "inactive"
SWITCH_OFF = "switch_off"
OPTIONAL_KINDS = (INACTIVE, SWITCH_OFF)

# ComfyUI v0.43.0(426f5ee)の出力のノード: `OUTPUT_NODE = True`(と、それを受け継ぐクラス)か
# `is_output_node=True` のノード(nodes.py・comfy_extras・comfy_api_nodes・
# custom_nodes/websocket_image_save.py)。
# 試験用のノード(DCTestNode)は除いた。ComfyUI の版を上げたら読み直す(families.COMFYUI_COMMIT と同じ版)
OUTPUT_NODE_TYPES = frozenset(
    {
        "CLIPSave",
        "CheckpointSave",
        "CreateBoundingBoxes",
        "ImageCompare",
        "ImageCompositor",
        "LoopProgress",
        "LoopResult",
        "LoraSave",
        "LossGraphNode",
        "MaskPreview",
        "MeshyAnimateModelNode",
        "MeshyImageToModelNode",
        "MeshyMultiImageToModelNode",
        "MeshyRefineNode",
        "MeshyRigModelNode",
        "MeshyTextToModelNode",
        "MeshyTextureMultiViewNode",
        "MeshyTextureNode",
        "ModelSave",
        "Preview3D",
        "Preview3DAdvanced",
        "PreviewAny",
        "PreviewAudio",
        "PreviewGaussianSplat",
        "PreviewImage",
        "PreviewPointCloud",
        "SAM3_TrackPreview",
        "Save3DAdvanced",
        "SaveAnimatedPNG",
        "SaveAnimatedWEBP",
        "SaveAudio",
        "SaveAudioAdvanced",
        "SaveAudioMP3",
        "SaveAudioOpus",
        "SaveConditioning",
        "SaveGLB",
        "SaveGaussianSplat",
        "SaveImage",
        "SaveImageAdvanced",
        "SaveImageDataSetToFolder",
        "SaveImageTextDataSetToFolder",
        "SaveImageWebsocket",
        "SaveLatent",
        "SaveLoRA",
        "SavePointCloud",
        "SaveSVGNode",
        "SaveText",
        "SaveTrainingDataset",
        "SaveVideo",
        "SaveWEBM",
        "TencentImageToModelNode",
        "TencentTextToModelNode",
        "TripoConversionNode",
        "TripoImageToModelNode",
        "TripoImageToModelNodeV2",
        "TripoMeshCompleteNode",
        "TripoMultiviewToModelNode",
        "TripoRetargetNode",
        "TripoRetopologyNode",
        "TripoRigNode",
        "TripoSegmentNode",
        "TripoSmartSegmentNode",
        "TripoTextToModelNode",
        "TripoTextToModelNodeV2",
        "TripoTextureNode",
        "TripoTextureNodeV2",
        "VAESave",
        "VideoCrop",
        "VideoTrim",
    }
)
# 画面だけのノード(実行されない。出力につながらなくても出発点にしない)
FRONT_ONLY = frozenset({"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "Label (rgthree)"})
# つながりの線を使わずに値を渡す拡張のノード。これがあるとつながりから分からないので、何も分けない(安全側)
VIRTUAL_LINKS = frozenset(
    {
        "SetNode",
        "GetNode",
        "Anything Everywhere",
        "Anything Everywhere3",
        "Anything Everywhere?",
        "Prompts Everywhere",
    }
)
SWITCH = "ComfySwitchNode"
SOFT_SWITCH = "ComfySoftSwitchNode"
MODE_NEVER = 2
MODE_BYPASS = 4
SUBGRAPH_IN = -10
SUBGRAPH_OUT = -20
WIDGET_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})
MODEL_FILE = re.compile(r"\.(safetensors|sft|ckpt|pt|pth|bin|gguf|onnx)$", re.IGNORECASE)
_MAX_DEPTH = 16
_UNKNOWN = object()


@dataclass
class OptionalFile:
    """任意の部品の 1 ファイル。replaces は、その部品のスイッチを切り替えると読み込まなくなる既定の部品の名前
    (空なら足すだけ)。"""

    kind: str
    replaces: list[str] = field(default_factory=list)


def base_name(name: str) -> str:
    """比べるためのファイル名(フォルダを除き、小文字)。"""
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()


class _Ctx:
    """1 つの階層(一番上か、サブグラフの実体 1 つ)。top_mode は、一番上の階層の実体の mode
    (中身すべてに効く)。"""

    def __init__(self, graph: dict[str, Any], path: tuple, parent: _Ctx | None, inst: Any, top_mode: int):
        self.nodes = {n.get("id"): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
        self.links = _links(graph.get("links"))
        self.graph, self.path, self.parent, self.inst, self.top_mode = graph, path, parent, inst, top_mode


def _links(raw: Any) -> dict[Any, tuple[Any, Any, Any, Any]]:
    out: dict[Any, tuple[Any, Any, Any, Any]] = {}
    for link in raw or []:
        if isinstance(link, dict) and "id" in link:
            out[link["id"]] = (
                link.get("origin_id"),
                link.get("origin_slot"),
                link.get("target_id"),
                link.get("target_slot"),
            )
        elif isinstance(link, list) and len(link) >= 5:
            out[link[0]] = (link[1], link[2], link[3], link[4])
    return out


def _mode(n: dict[str, Any]) -> int:
    try:
        return int(n.get("mode") or 0)
    except (TypeError, ValueError):
        return 0


def _input_link(n: dict[str, Any], name: str) -> Any:
    for i in n.get("inputs") or []:
        if isinstance(i, dict) and i.get("name") == name:
            return i.get("link")
    return None


def _outputs_connected(n: dict[str, Any]) -> bool:
    return any(isinstance(o, dict) and o.get("links") for o in n.get("outputs") or [])


def _node_files(n: dict[str, Any]) -> set[str]:
    """ノードに書いてあるファイル(properties.models の名前と、入力欄の値のうちモデルのファイル名)。"""
    out: set[str] = set()
    props = n.get("properties")
    if isinstance(props, dict):
        for m in props.get("models") or []:
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                out.add(base_name(m["name"]))
    wv = n.get("widgets_values")
    values = list(wv.values()) if isinstance(wv, dict) else (wv if isinstance(wv, list) else [])
    out |= {base_name(v) for v in values if isinstance(v, str) and MODEL_FILE.search(v)}
    return out


class _Analysis:
    def __init__(self, wf: dict[str, Any]):
        defs = wf.get("definitions")
        subs = defs.get("subgraphs") if isinstance(defs, dict) else None
        self.subs = {sg.get("id"): sg for sg in subs or [] if isinstance(sg, dict) and sg.get("id")}
        self.flat: dict[tuple, tuple[_Ctx, dict[str, Any], int]] = {}
        self.ctxs: dict[tuple, _Ctx] = {}
        self._expand(_Ctx(wf, (), None, None, 0), 0)

    def _expand(self, ctx: _Ctx, depth: int) -> None:
        if depth > _MAX_DEPTH:
            raise ValueError("subgraphs are nested too deeply")
        self.ctxs[ctx.path] = ctx
        for nid, n in ctx.nodes.items():
            mode = _mode(n)
            if n.get("type") in self.subs:
                # 一番上の階層の実体の mode だけが中身に効く(入れ子の実体の mode は中身に効かない)
                top = mode if ctx.path == () and mode in (MODE_NEVER, MODE_BYPASS) else ctx.top_mode
                self._expand(_Ctx(self.subs[n["type"]], (*ctx.path, nid), ctx, n, top), depth + 1)
                continue
            if ctx.top_mode == MODE_NEVER or mode == MODE_NEVER:
                eff = MODE_NEVER
            elif ctx.top_mode == MODE_BYPASS or mode == MODE_BYPASS:
                eff = MODE_BYPASS
            else:
                eff = 0
            self.flat[(*ctx.path, nid)] = (ctx, n, eff)

    # --- つながりをさかのぼる ---------------------------------------------------------------------

    def resolve(self, ctx: _Ctx, lid: Any, depth: int = 0) -> list[tuple]:
        """入力のリンクの元(実行されるノードの鍵)。無効のノード・値だけのノードは []、素通しのノードは
        その入力の元。サブグラフの出口が見つからないときは ("ALL", 実体の道) で中の全部を表す。"""
        if lid is None or lid not in ctx.links or depth > 500:
            return []
        origin, slot = ctx.links[lid][:2]
        if origin == SUBGRAPH_IN:
            if ctx.inst is None or ctx.parent is None:
                return []
            sgin = ctx.graph.get("inputs") or []
            if not isinstance(slot, int) or not 0 <= slot < len(sgin):
                return []
            return self.resolve(ctx.parent, _input_link(ctx.inst, sgin[slot].get("name")), depth + 1)
        n = ctx.nodes.get(origin)
        if n is None:
            return []
        if n.get("type") in self.subs:
            child = self.ctxs[(*ctx.path, origin)]
            if child.top_mode == MODE_NEVER:
                return []
            exits = [k for k, (_o, _s, t, ts) in child.links.items() if t == SUBGRAPH_OUT and ts == slot]
            if not exits:
                return [("ALL", child.path)]  # 壊れたサブグラフ: 中の全部が使われるとみなす(安全側)
            out: list[tuple] = []
            for k in exits:
                out += self.resolve(child, k, depth + 1)
            return out
        key = (*ctx.path, origin)
        _c, _n, eff = self.flat[key]
        if n.get("type") == "Reroute":
            first = (n.get("inputs") or [{}])[0]
            return self.resolve(ctx, first.get("link") if isinstance(first, dict) else None, depth + 1)
        if n.get("type") == "PrimitiveNode" or eff == MODE_NEVER:
            return []
        if eff == MODE_BYPASS:
            out = []
            for i in n.get("inputs") or []:
                if isinstance(i, dict):
                    out += self.resolve(ctx, i.get("link"), depth + 1)
            return out
        return [key]

    def connected(self, ctx: _Ctx, lid: Any, depth: int = 0) -> bool:
        """入力に値が来るか(ComfyUI の MISSING でないか)。つながっていない・無効のノード(prompt に入らない)に
        つながるときは False。値だけのノード(Primitive)・サブグラフの実体の入力欄の値は True。"""
        if lid is None or lid not in ctx.links or depth > 200:
            return False
        origin, slot = ctx.links[lid][:2]
        if origin == SUBGRAPH_IN:
            if ctx.inst is None or ctx.parent is None:
                return False
            sgin = ctx.graph.get("inputs") or []
            if not isinstance(slot, int) or not 0 <= slot < len(sgin):
                return False
            outer = _input_link(ctx.inst, sgin[slot].get("name"))
            if outer is not None:
                return self.connected(ctx.parent, outer, depth + 1)
            return str(sgin[slot].get("type")).upper() in WIDGET_TYPES  # 実体の入力欄の値
        n = ctx.nodes.get(origin)
        if n is None:
            return False
        if n.get("type") in self.subs:
            return self.ctxs[(*ctx.path, origin)].top_mode != MODE_NEVER
        _c, _n, eff = self.flat[(*ctx.path, origin)]
        if n.get("type") == "Reroute":
            first = (n.get("inputs") or [{}])[0]
            return self.connected(ctx, first.get("link") if isinstance(first, dict) else None, depth + 1)
        if n.get("type") == "PrimitiveNode":
            return True
        if eff == MODE_NEVER:
            return False
        if eff == MODE_BYPASS:
            return any(
                self.connected(ctx, i.get("link"), depth + 1)
                for i in n.get("inputs") or []
                if isinstance(i, dict)
            )
        return True

    def const(self, ctx: _Ctx, lid: Any, depth: int = 0) -> Any:
        """入力欄の値(つながりの元が実体の入力欄・Primitive の値なら、その値)。分からなければ _UNKNOWN。"""
        if lid is None or lid not in ctx.links or depth > 50:
            return _UNKNOWN
        origin, slot = ctx.links[lid][:2]
        if origin == SUBGRAPH_IN:
            if ctx.inst is None or ctx.parent is None:
                return _UNKNOWN
            sgin = ctx.graph.get("inputs") or []
            if not isinstance(slot, int) or not 0 <= slot < len(sgin):
                return _UNKNOWN
            name = sgin[slot].get("name")
            outer = _input_link(ctx.inst, name)
            if outer is not None:
                return self.const(ctx.parent, outer, depth + 1)
            return _instance_widget(ctx.inst, sgin, slot, name)
        n = ctx.nodes.get(origin)
        if n is None or n.get("type") in self.subs:
            return _UNKNOWN
        if n.get("type") == "Reroute":
            first = (n.get("inputs") or [{}])[0]
            return self.const(ctx, first.get("link") if isinstance(first, dict) else None, depth + 1)
        wv = n.get("widgets_values")
        if str(n.get("type")).startswith("Primitive") and isinstance(wv, list) and wv and _mode(n) == 0:
            return wv[0]
        return _UNKNOWN

    def switch_value(self, ctx: _Ctx, n: dict[str, Any]) -> Any:
        lid = _input_link(n, "switch")
        if lid is not None:
            return self.const(ctx, lid)
        wv = n.get("widgets_values")
        if isinstance(wv, dict):
            return wv.get("switch", _UNKNOWN)
        return wv[0] if isinstance(wv, list) and wv else _UNKNOWN

    def files_used(self, ctx: _Ctx, n: dict[str, Any]) -> set[str]:
        """実行するノードが読むファイル。入力欄の値がサブグラフの外(実体の入力欄・proxyWidgets)から来るものも含める。"""
        out = _node_files(n)
        for i in n.get("inputs") or []:
            if isinstance(i, dict) and i.get("widget") and i.get("link") is not None:
                v = self.const(ctx, i.get("link"))
                if isinstance(v, str) and MODEL_FILE.search(v):
                    out.add(base_name(v))
        inst = ctx.inst
        if isinstance(inst, dict):
            proxies = (inst.get("properties") or {}).get("proxyWidgets") or []
            values = inst.get("widgets_values")
            if isinstance(values, list):
                for p, v in zip(proxies, values, strict=False):
                    if (
                        isinstance(p, list)
                        and len(p) == 2
                        and str(p[0]) == str(n.get("id"))
                        and isinstance(v, str)
                        and MODEL_FILE.search(v)
                    ):
                        out.add(base_name(v))
        return out

    # --- たどる ---------------------------------------------------------------------------------

    def starts(self) -> list[tuple]:
        """出発点: 出力のノード(型の表)と、出力がどこにもつながっていないノード(表に無い拡張の出力のノードの
        代わり。ファイルを読むノード・画面だけのノードは除く)。どの階層でも探す。"""
        out = []
        for k, (_ctx, n, eff) in self.flat.items():
            if eff != 0:
                continue
            t = n.get("type")
            if t in OUTPUT_NODE_TYPES or (
                t not in FRONT_ONLY and not _outputs_connected(n) and not _node_files(n)
            ):
                out.append(k)
        return out

    def walk(
        self, starts: list[tuple], lazy: bool, flip: tuple | None = None, ran: set[tuple] | None = None
    ) -> tuple[set[str], list[tuple]]:
        """出発点から届くノードの読むファイルと、値の決まったスイッチの鍵。lazy=True は実行(選ばれない枝を
        たどらない)、False は検証(全部たどる)。flip のスイッチは値を逆にする。ran を渡すと、実行するノードの
        鍵を入れる。"""
        seen: set[tuple] = set()
        got: set[str] = set()
        switches: list[tuple] = []
        stack = list(starts)
        while stack:
            k = stack.pop()
            if k and k[0] == "ALL":
                pre = k[1]
                stack += [kk for kk, (_c, _n, e) in self.flat.items() if kk[: len(pre)] == pre and e == 0]
                continue
            if k in seen:
                continue
            seen.add(k)
            ctx, n, eff = self.flat[k]
            if eff != 0:
                continue
            if ran is not None:
                ran.add(k)
            got |= self.files_used(ctx, n)
            names: set[str] | None = None
            if lazy and n.get("type") in (SWITCH, SOFT_SWITCH):
                v = self.switch_value(ctx, n)
                if isinstance(v, bool):
                    switches.append(k)
                    if k == flip:
                        v = not v
                    sel, other = ("on_true", "on_false") if v else ("on_false", "on_true")
                    if n.get("type") == SOFT_SWITCH and not self.connected(ctx, _input_link(n, sel)):
                        sel = other  # 選んだ側に値が来ない(つながっていない・prompt に入らない)ならもう一方
                    names = {"switch", sel}
            for i in n.get("inputs") or []:
                if not isinstance(i, dict) or (names is not None and i.get("name") not in names):
                    continue
                stack += self.resolve(ctx, i.get("link"))
        return got, switches


def _instance_widget(inst: dict[str, Any], sgin: list[dict[str, Any]], slot: int, name: Any) -> Any:
    """サブグラフの実体の入力欄の値。proxyWidgets があれば ["-1", 名前] の位置、無ければ入力欄の型の
    入力の並び。"""
    values = inst.get("widgets_values")
    if not isinstance(values, list):
        return _UNKNOWN
    proxies = (inst.get("properties") or {}).get("proxyWidgets")
    if proxies:
        for p, v in zip(proxies, values, strict=False):
            if isinstance(p, list) and len(p) == 2 and str(p[0]) == "-1" and p[1] == name:
                return v
        return _UNKNOWN
    widx = [k for k, x in enumerate(sgin) if str(x.get("type")).upper() in WIDGET_TYPES]
    if len(values) == len(sgin):
        return values[slot]
    if len(values) == len(widx) and slot in widx:
        return values[widx.index(slot)]
    return _UNKNOWN  # 並びが合わない(「生成後の動き」の値が混じるなど): 分からない(両方たどる)


def optional_files(wf: Any) -> dict[str, OptionalFile]:
    """テンプレートの既定の値で読み込まれないファイル(base_name → 分け方)。分けられないとき(出発点が無い・
    つながりを使わない拡張がある・形が違う)は空(全部を数える。安全側)。"""
    if not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list):
        return {}
    try:
        a = _Analysis(wf)
    except (ValueError, RecursionError, TypeError, KeyError):
        return {}
    if any(n.get("type") in VIRTUAL_LINKS for _c, n, _e in a.flat.values()):
        return {}
    starts = a.starts()
    if not starts:
        return {}
    try:
        referenced: set[str] = set()
        for _c, n, _e in a.flat.values():
            referenced |= _node_files(n)
        run, switches = a.walk(starts, lazy=True)
        checked, _ = a.walk(starts, lazy=False)
        out: dict[str, OptionalFile] = {}
        for name in sorted(referenced - checked):
            out[name] = OptionalFile(INACTIVE)
        for name in sorted(referenced - run - set(out)):
            out[name] = OptionalFile(SWITCH_OFF)
        # スイッチを 1 つずつ逆にして、増える部品(この部品)と減る部品(代わりに読まなくなる既定の部品)を見る
        for k in switches:
            flipped, _ = a.walk(starts, lazy=True, flip=k)
            removed = sorted(run - flipped)
            for name in flipped - run:
                o = out.get(name)
                if o is not None and o.kind == SWITCH_OFF and not o.replaces:
                    o.replaces = list(removed)
    except (RecursionError, TypeError, KeyError):
        return {}
    return out


@dataclass
class SwitchBranch:
    """既定の値で実行するスイッチ 1 つ。path はノードの id の道(サブグラフの実体の id から)、value は既定の
    値、starts・stops は値を逆にすると実行し始める・しなくなるノード(道と型)。"""

    path: tuple
    type: str
    title: str | None
    value: bool
    starts: list[tuple[tuple, str]] = field(default_factory=list)
    stops: list[tuple[tuple, str]] = field(default_factory=list)


def switch_branches(wf: Any) -> list[SwitchBranch]:
    """テンプレートの既定の値で、どのスイッチがどちらの枝を通すか(テンプレートの式を当てる前の確かめ用。
    tools/template_branches.py)。分けられないときは空。"""
    if not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list):
        return []
    try:
        a = _Analysis(wf)
        if any(n.get("type") in VIRTUAL_LINKS for _c, n, _e in a.flat.values()):
            return []
        starts = a.starts()
        base: set[tuple] = set()
        _, switches = a.walk(starts, lazy=True, ran=base)
        out: list[SwitchBranch] = []
        for k in switches:
            flipped: set[tuple] = set()
            a.walk(starts, lazy=True, flip=k, ran=flipped)
            ctx, n, _e = a.flat[k]

            def typed(keys: set[tuple]) -> list[tuple[tuple, str]]:
                return sorted(((kk, str(a.flat[kk][1].get("type"))) for kk in keys), key=repr)

            out.append(
                SwitchBranch(
                    k,
                    str(n.get("type")),
                    n.get("title"),
                    bool(a.switch_value(ctx, n)),
                    typed(flipped - base),
                    typed(base - flipped),
                )
            )
    except (ValueError, RecursionError, TypeError, KeyError):
        return []
    return out
