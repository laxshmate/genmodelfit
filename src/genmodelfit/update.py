# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`genmodelfit update`: モデルの一覧を上流から作り直す(llmfit の update に当たる)。

データ源(詳しくは docs/sources.md):
  1. ComfyUI 公式テンプレート(MIT)の index.json と、テンプレートごとの JSON の `properties.models`
  2. Hugging Face Hub API(ライセンス・gated・大きさ・sha256。再配布は base_model をたどる)
  3. Hugging Face の検索(無検閲のモデル、テンプレートに無い読み上げ・効果音のモデル)

上流の形が変わったら黙って空にせず UpstreamFormatError で止める。ネットにつながらないときは前のキャッシュを
そのまま残して失敗する(recommend はキャッシュか同梱のデータで動き続ける)。
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import appinfo, review, safety
from ._version import __version__
from .db import Database, DatabaseError, bundled_text, cached_db_path, parse_db
from .fetch import RATE_LIMIT_BUDGET, DirFetcher, Fetcher, FetchError, HttpFetcher, NetworkError
from .groups import model_precision_source
from .hf import (
    UNVERIFIED,
    HfClient,
    RepoInfo,
    Resolved,
    choose_bases,
    families_for,
    guess_params_b,
    guess_precision,
    is_repackager,
    license_tier,
    obligation_rank,
    parse_hf_url,
    search_url,
    select_weight_files,
    upstream_candidates,
)
from .i18n import ja_of, stored_message, tr
from .license_sources import enforce_lineage, source_for
from .models import ModelEntry, ModelFile, Template
from .tasks import tool_task_from_title

RAW = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates"
INDEX_URL = f"{RAW}/index.json"
INDEX_PAGE = "https://github.com/Comfy-Org/workflow_templates/blob/main/templates/index.json"
# 自動のおすすめの判定(gate)で、テンプレートの minComfyUIVersion と比べる版。認証なしで 1 回だけ取る
COMFYUI_RELEASE_URL = "https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest"
COMFYUI_RELEASES_PAGE = "https://github.com/Comfy-Org/ComfyUI/releases"

# テンプレートの失敗がこの割合を超えたら、上流の形が変わったとみなして止める
MAX_FAIL_RATIO = 0.30
# ライセンスの決め方の版。前の一覧から値を引き継ぐ(一部が取れなかったとき)のは、同じ版で作った一覧からだけ
# (古い決め方の寛容すぎる値を持ち込まないため)。決め方を変えたら上げる
LICENSE_POLICY = 5  # 5: 表の上書きの行・元を読めない系統を不明に戻す・同じ段階は義務の多いもの(0.1.1)
# 一式がそろっていないとみなす割合(ファイルの合計 / index の size)
COMPLETE_RATIO = 0.5
# 本体が GGUF だけのとき、テキストエンコーダ・VAE が別に要るとみなす種類(拡散モデル)。#28
GGUF_SPLIT_MODALITIES = ("image", "video", "music", "sfx", "3d")
# sources.counts.license_tier の名前(hf.license_tier の段階)
_TIER_NAMES = {
    6: "unknown",
    5: "non_commercial",
    4: "other",
    3: "use_restricted",
    2: "attribution",
    1: "permissive",
}

# --- テンプレートの分類 ---------------------------------------------------------------------------

CATEGORY_MODALITY = {
    "Image": "image",
    "Image Tools": "image",
    "Video": "video",
    "Video Tools": "video",
    "3D Model": "3d",
    "Audio": "audio",  # 音楽・効果音・読み上げに分ける(audio_modality)
}
SKIP_CATEGORIES = {"LLM", "Node Basics", "Vector"}

# 名前・タグから決められないものだけ手で決める(理由は docs/sources.md)
MODALITY_OVERRIDES = {
    # Stable Audio Open 1.0。モデルカードの用途が効果音・環境音(field recordings)中心
    "audio_stable_audio_example": "sfx",
}

# テンプレートのタグ → tasks(docs/sources.md に同じ表)
TAG_TASKS = {
    "text to image": "t2i",
    "image edit": "i2i",
    "inpainting": "inpaint",
    "outpainting": "outpaint",
    "controlnet": "control",
    "image upscale": "upscale",
    "remove background": "matting",
    "relight": "relight",
    "style reference": "i2i",
    "layer decompose": "i2i",
    "depth estimation": "estimate",
    "normal estimation": "estimate",
    "albedo estimation": "estimate",
    "element segmentation": "estimate",
    "image to video": "i2v",
    "text to video": "t2v",
    "video edit": "v2v",
    "video to video": "v2v",
    "flf2v": "flf2v",
    "reference to video": "ref2v",
    "audio to video": "a2v",
    "lip sync": "lipsync",
    "motion control": "motion",
    "character replacement": "v2v",
    "frame interpolation": "interp",
    "video upscale": "upscale",
    "image to 3d": "i23d",
    "text to 3d": "t23d",
    "text to audio": "t2a",
    "text to music": "t2a",
    "music cover": "a2a",
    "audio editing": "a2a",
    "voice cloning": "vc",
    "text to speech": "tts",
}

# HF の pipeline_tag → (modality, task)
PIPELINES = {
    "text-to-image": ("image", "t2i"),
    "image-to-image": ("image", "i2i"),
    "text-to-video": ("video", "t2v"),
    "image-to-video": ("video", "i2v"),
    "text-to-speech": ("tts", "tts"),
    "text-to-audio": ("audio", "t2a"),
    "image-to-3d": ("3d", "i23d"),
    "text-to-3d": ("3d", "t23d"),
}

DIR_ROLE = {
    "diffusion_models": "main",
    "unet": "main",
    "checkpoints": "main",
    "text_encoders": "text_encoder",
    "clip": "text_encoder",
    "vae": "vae",
    "clip_vision": "clip_vision",
    "loras": "lora",
}

_TTS = re.compile(r"\b(tts|text to speech|speech|voice clon\w*)\b", re.IGNORECASE)
_SFX = re.compile(r"\b(sound effects?|sfx|foley)\b", re.IGNORECASE)
_MUSIC = re.compile(r"\b(music|song|songs|instrumentals?)\b", re.IGNORECASE)
_UNCENSORED = re.compile(
    r"(?<![a-z0-9])(uncensored|nsfw|abliterated|heretic|unfiltered)(?![a-z0-9])", re.IGNORECASE
)
_NFAA = "not-for-all-audiences"


class UpdateError(RuntimeError):
    pass


class UpstreamFormatError(UpdateError):
    """上流の形が想定と違う(黙って空の一覧を作らないため)。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.ja_message = ja_of(message)  # 一覧(sources.warnings)に残す文は日本語(#73)


def validate_index(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not data:
        raise UpstreamFormatError(
            tr(
                "index.json の形が変わりました: 一覧(list)ではありません",
                "index.json changed shape: not a list",
            )
        )
    for i, cat in enumerate(data):
        if not isinstance(cat, dict) or not isinstance(cat.get("templates"), list):
            raise UpstreamFormatError(
                tr(
                    f"index.json の形が変わりました: {i} 番目の分類に templates がありません",
                    f"index.json changed shape: category {i} has no templates",
                )
            )
        for j, t in enumerate(cat["templates"]):
            if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
                raise UpstreamFormatError(
                    tr(
                        f"index.json の形が変わりました: 「{cat.get('title')}」の "
                        f"{j} 番目に name がありません",
                        f'index.json changed shape: item {j} of "{cat.get("title")}" has no name',
                    )
                )
    return data


def skip_reason(category: str | None, t: dict[str, Any]) -> str | None:
    """ローカルで動くテンプレートでなければ理由を返す。

    openSource が無いものはローカル扱い(False だけを外す)。
    """
    if t["name"].startswith("api_"):
        return "api"
    if t.get("openSource") is False:
        return "not_open_source"
    if not t.get("size"):
        return "no_size"
    if category in SKIP_CATEGORIES:
        return "category"
    return None


def audio_modality(t: dict[str, Any]) -> str:
    short = " ".join([t.get("name", "").replace("_", " "), t.get("title") or "", *(t.get("tags") or [])])
    if _TTS.search(short):
        return "tts"
    text = short + " " + (t.get("description") or "")
    if _SFX.search(text) and not _MUSIC.search(text):
        return "sfx"
    return "music"


def template_modality(category: str | None, t: dict[str, Any]) -> str | None:
    if t["name"] in MODALITY_OVERRIDES:
        return MODALITY_OVERRIDES[t["name"]]
    m = CATEGORY_MODALITY.get(category or "")
    if m == "audio":
        return audio_modality(t)
    if m:
        return m
    # Character & Fashion など: 出力の種類 → タグの順に見る
    outs = {o.get("mediaType") for o in (t.get("io") or {}).get("outputs", []) if isinstance(o, dict)}
    for kind, mod in (("video", "video"), ("audio", "music"), ("3d", "3d"), ("image", "image")):
        if kind in outs:
            return mod
    tags = [x.lower() for x in t.get("tags") or []]
    if any(x in ("image to 3d", "text to 3d") for x in tags):
        return "3d"
    if any("video" in x for x in tags):
        return "video"
    return "image"


# 道具の分類(Image Tools / Video Tools)で、当てはまるタグが無いときの task(生成ではない処理)
TOOL_CATEGORIES = {"Image Tools", "Video Tools"}


def template_tasks(t: dict[str, Any], modality: str, category: str | None = None) -> list[str]:
    # 深度・線画・ポーズの地図を作る前処理や音の分離は、タグ(controlnet など)によらず補助ツール
    tool = tool_task_from_title(t.get("title"), category)
    if tool:
        return [tool]
    out: list[str] = []
    for tag in t.get("tags") or []:
        task = TAG_TASKS.get(str(tag).lower())
        if task and task not in out:
            out.append(task)
    if not out:
        if category in TOOL_CATEGORIES:
            return ["tool"]  # Depth・Pose Map など表に無いタグの道具を、t2i / t2v と言わない
        out = [{"image": "t2i", "video": "t2v", "3d": "i23d", "tts": "tts"}.get(modality, "t2a")]
    return out


def workflow_models(wf: Any) -> list[dict[str, Any]]:
    """ワークフローの全ノード(サブグラフの中も)の properties.models を、URL で重複を除いて返す。"""
    if not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list):
        raise UpstreamFormatError(
            tr("テンプレートの JSON に nodes がありません", "the template JSON has no nodes")
        )
    nodes = list(wf["nodes"])
    defs = wf.get("definitions")
    if isinstance(defs, dict):
        for sg in defs.get("subgraphs") or []:
            if isinstance(sg, dict) and isinstance(sg.get("nodes"), list):
                nodes += sg["nodes"]
    found = list(wf.get("models") or [])  # 古い形: 一番上の models
    for n in nodes:
        props = n.get("properties") if isinstance(n, dict) else None
        if isinstance(props, dict) and isinstance(props.get("models"), list):
            found += props["models"]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in found:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        key = m.get("url") or m["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


# 上流のテンプレートの誤り(ファイル名と URL・hash が食い違う)を直す表: (名前, 上流の URL) → (URL, sha256)。
# 直した値は匿名の HEAD の x-linked-etag で確かめたもの
UPSTREAM_FILE_FIXES = {
    # image_moss_image_t2i_bboxes: 名前は sft なのに URL・hash が base のもの(2026-10-20 に HEAD で確認)
    (
        "moss_image_sft_int8_convrot.safetensors",
        "https://huggingface.co/Comfy-Org/MOSS-Image/resolve/main/diffusion_models/moss_image_base_int8_convrot.safetensors",
    ): (
        "https://huggingface.co/Comfy-Org/MOSS-Image/resolve/main/diffusion_models/moss_image_sft_int8_convrot.safetensors",
        "b4d1475bc7eeffd7b809e82ea13a1fba1fb85c4e18365af3813722faa5c0fe4f",
    ),
}


def model_file(m: dict[str, Any]) -> ModelFile:
    directory = m.get("directory")
    h = m.get("hash")
    sha = h.lower() if isinstance(h, str) and str(m.get("hash_type", "")).lower() == "sha256" else None
    url = m.get("url")
    fix = UPSTREAM_FILE_FIXES.get((m["name"], url))
    if fix:
        url, sha = fix
    parsed = parse_hf_url(url)
    return ModelFile(
        name=m["name"],
        url=url,
        sha256=sha,
        directory=directory,
        repo_id=parsed[0] if parsed else None,
        role=DIR_ROLE.get(directory or "", "other"),
    )


def template_entry(
    category: str, t: dict[str, Any], modality: str, models: list[dict[str, Any]]
) -> ModelEntry:
    files = [model_file(m) for m in models]
    main = next((f for f in files if f.role == "main"), files[0] if files else None)
    e = ModelEntry(
        id=t["name"],
        name=t.get("title") or t["name"],
        modality=modality,
        tasks=template_tasks(t, modality, category),
        source="comfyui-template",
        files=files,
        template=Template(
            name=t["name"],
            title=t.get("title"),
            category=category,
            min_comfyui_version=t.get("minComfyUIVersion"),
            open_source=t.get("openSource"),
            requires_custom_nodes=list(t.get("requiresCustomNodes") or []),
            usage=t.get("usage"),
            date=t.get("date"),
            size_bytes=t.get("size") or None,
            username=t.get("username") or None,
        ),
        params_b=guess_params_b(main.name if main else None, t.get("title"), t["name"]),
        discovered_from=[INDEX_PAGE],
        updated=t.get("date"),
    )
    set_precision(e)  # 大きさが分かったら(enrich_files・inspect_headers)決め直す
    return e


# --- HF でファイルを埋める --------------------------------------------------------------------------


def _hf_keys(entries: list[ModelEntry]) -> set[tuple[str, str]]:
    keys = set()
    for e in entries:
        for f in e.files:
            p = parse_hf_url(f.url)
            if p:
                keys.add((p[0], p[1]))
    return keys


def enrich_files(entries: list[ModelEntry], client: HfClient, fetch: Fetcher, *, head: bool) -> None:
    """ファイルに大きさ・sha256・ライセンス・gated を入れ、一式のライセンスと一式のそろい具合を決める。"""
    keys = _hf_keys(entries)
    client.prefetch(keys)
    # ライセンスの道(再配布の本家と、base_model を MAX_HOPS 段まで)も先にまとめて取る
    chain: set[str] = set()
    for e in entries:
        for f in e.files:
            p = parse_hf_url(f.url)
            info = client.repo(p[0], p[1]) if p else None
            if info is None:
                continue
            if is_repackager(info.repo_id):
                chain |= set(choose_bases(upstream_candidates(info), p[2]))
            else:
                chain |= set(info.base_models)
    client.prefetch_chains(chain)

    missing: list[ModelFile] = []
    for e in entries:
        for f in e.files:
            p = parse_hf_url(f.url)
            if not p:
                host = urlsplit(f.url).netloc if f.url else "URL なし"
                set_file_license(
                    f,
                    Resolved(None, path=[], evidence=[f"{UNVERIFIED}: Hugging Face 以外のファイル({host})"]),
                )
                continue
            info = client.repo(p[0], p[1])
            if info is None:
                fail = client.failed(p[0])
                why = f"{p[0]} を読めません" + (f"(HTTP {fail.status})" if fail and fail.status else "")
                set_file_license(
                    f, Resolved(None, source=p[0], path=[p[0]], evidence=[f"{UNVERIFIED}: {why}"])
                )
                continue
            size, sha = info.files.get(p[2], (None, None))
            f.size_bytes = size or f.size_bytes
            f.sha256 = f.sha256 or sha
            f.gated = info.gated
            apply_license(f, info, client, p[2], url_repo=p[0])
            if f.size_bytes is None:
                missing.append(f)
    if head and missing:
        # API の一覧に無いファイル(別の版など)だけ HEAD で大きさを見る。HF 以外(Civitai など)には送らない
        def _head(f: ModelFile) -> None:
            with contextlib.suppress(FetchError):
                f.size_bytes = fetch.head_size(f.url)

        with ThreadPoolExecutor(client.workers) as ex:
            list(ex.map(_head, missing))
    for e in entries:
        set_entry_license(e)
        set_completeness(e)
        set_precision(e)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_heads(
    entries: list[ModelEntry], fetch: Fetcher, *, workers: int = 8, checked_at: str | None = None
) -> dict[str, Any]:
    """すべてのファイルの URL に匿名の HEAD を送り(転送は追わない)、応答コードを記録する
    (gate の url_reachable)。

    送るのは Hugging Face の URL だけ(Civitai などには送らない。docs/sources.md)。同じ URL は 1 回だけ。
    同時に 8 本まで。429 は HttpFetcher が Retry-After を守って待つ。つながらなかったものは None(未確認)。
    """
    when = checked_at or _now()
    urls = sorted({f.url for e in entries for f in e.files if f.url and parse_hf_url(f.url)})

    def _one(url: str) -> int | None:
        try:
            return fetch.head_status(url)
        except FetchError:
            return None

    with ThreadPoolExecutor(max(1, min(8, workers))) as ex:
        result = dict(zip(urls, ex.map(_one, urls), strict=True))
    skipped: Counter[str] = Counter()
    for e in entries:
        for f in e.files:
            if f.url in result:
                f.head_status = result[f.url]
                f.head_checked_at = when if result[f.url] is not None else None
            else:
                f.head_status, f.head_checked_at = None, None
                if f.url:
                    skipped[f.url.split("/")[2] if "://" in f.url else "?"] += 1
    status = Counter(str(v) for v in result.values() if v is not None)
    return {
        "checked_at": when,
        "urls": len(urls),
        "status": dict(sorted(status.items())),
        "unchecked": sum(1 for v in result.values() if v is None),
        "not_sent": dict(sorted(skipped.items())),  # HEAD を送らなかったファイル(ホストごと、のべ数)
    }


def latest_comfyui(fetch: Fetcher) -> tuple[dict[str, Any], str | None]:
    """ComfyUI の最新のリリースの版(tag)。取れなければ版は None と警告。"""
    info: dict[str, Any] = {"latest_release": None, "url": COMFYUI_RELEASES_PAGE, "checked_at": _now()}
    try:
        data = fetch.get_json(COMFYUI_RELEASE_URL)
    except FetchError as e:
        return info, f"ComfyUI の最新の版を取得できません: {stored_message(e)}"  # 一覧に残す文は日本語
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not tag:
        return info, "ComfyUI の最新の版を読めません(tag_name がありません)"
    info["latest_release"] = tag
    if isinstance(data.get("html_url"), str):
        info["url"] = data["html_url"]
    return info, None


def set_file_license(f: ModelFile, res: Resolved) -> None:
    f.license, f.license_name = res.license, res.license_name
    f.license_from = res.source
    f.license_url = res.url
    f.license_path = list(res.path)
    f.license_evidence = list(res.evidence)


def apply_license(
    f: ModelFile,
    info: RepoInfo,
    client: HfClient,
    path: str,
    *,
    discovered_uncensored: bool = False,
    url_repo: str | None = None,
) -> Resolved:
    """url_repo: ファイルの URL に書かれたリポジトリ。HF でリポジトリの名前が変わると、API は新しい名前
    (info.repo_id)を返す。出どころの表はファイルの URL の名前で当てる(license_sources.apply_to_file と
    同じ。当て直したときに同じ結果になるように。#19 の毎週の更新で見つかった)。"""
    res = client.resolve_file(info, path, discovered_uncensored=discovered_uncensored)
    # 自動で決められなかったもの(と、表が上書きと決めたもの)は、人が一次資料で出どころを確かめた表で決める
    # (license_sources.py)
    repo = url_repo or info.repo_id
    row = source_for(repo, path)
    if row is not None and (res.license is None or row.override):
        res = row.resolved(repo)
    set_file_license(f, res)
    enforce_lineage(f)  # 元を読めない系統は不明に戻す(gate と同じ)
    f.license_url = res.url
    if res.source and res.source != info.repo_id:
        f.repo_license = info.license if info.license != "other" else (info.license_name or "other")
    return res


def set_entry_license(e: ModelEntry) -> None:
    """一式のライセンスは、すべてのファイル(LoRA・アップスケーラ・clip_vision なども)の中で一番厳しいもの。
    ファイルが無ければ不明。原文の場所(license_url)は、同じライセンスの本体のファイル(大きいもの)が
    あればそのもの(同じ段階のファイルが複数あるとき、名前の順で部品(VAE・テキストエンコーダ)の URL を
    出していた。#144)。"""
    if not e.files:
        e.license, e.license_name, e.license_url = None, None, None
        e.license_evidence = [f"{UNVERIFIED}: 使うファイルが分かりません"]
        return
    worst = max(
        e.files,
        key=lambda f: (
            license_tier(f.license, f.license_name),
            obligation_rank(f.license),  # 同じ段階なら義務の多いもの(apache-2.0 を mit より先に)
            f.license or "",
            f.name,
        ),
    )
    e.license, e.license_name = worst.license, worst.license_name
    e.license_url = worst.license_url if worst.license else None
    same_main = [
        f
        for f in e.files
        if f.role == "main" and f.license_url and (f.license, f.license_name) == (e.license, e.license_name)
    ]
    if e.license and same_main:
        e.license_url = min(same_main, key=lambda f: (-(f.size_bytes or 0), f.name)).license_url
    ev: list[str] = []
    for f in e.files:
        for x in f.license_evidence:
            if x.startswith(UNVERIFIED) and x not in ev:
                ev.append(f"{x}({f.name.rsplit('/', 1)[-1]})")
    e.license_evidence = ev[:10]
    mains = [f for f in e.files if f.role == "main" and f.license_from]
    if mains and e.base_model is None and mains[0].repo_id and mains[0].license_from != mains[0].repo_id:
        e.base_model = mains[0].license_from


def set_precision(e: ModelEntry) -> None:
    """精度は本体のファイルの見出し(読めていれば)→ 名前 → 置き場所のリポジトリ(Hub API・config.json)→
    テンプレートの読み込みノードの順に決め、出どころを precision_source に残す(groups.model_precision_source。
    表のモデルのまとめ方と同じ本体を見る)。ファイルの大きさが分かってから呼ぶ(本体が複数なら大きいほうを見るため)。"""
    e.precision, e.precision_source = model_precision_source(e)


def set_sampler_stages(e: ModelEntry, stages: list[list[str]] | None) -> None:
    """テンプレートのつながりから読んだサンプラーごとの組(workflow.sampler_stages)のうち、この項目の本体を
    含むものを sampler_stages に入れる。本体の組が 2 つ以上でなければ [](1 組)。None は読めなかった。"""
    from .estimate import _base_lower, _role_of

    if stages is None:
        e.sampler_stages = None
        return
    mains = {_base_lower(f.name) for f in e.files if _role_of(f) == "main"}
    rows = [st for st in stages if {_base_lower(n) for n in st} & mains]
    kinds = {frozenset(_base_lower(n) for n in st) & mains for st in rows}
    e.sampler_stages = [list(st) for st in rows] if len(kinds) >= 2 else []


def set_workflow_precision(e: ModelEntry, wf: Any) -> None:
    """テンプレートの本体の読み込みノードから推した精度(workflow.loader_precision)を
    workflow_precision に入れ、精度を決め直す。
    本体のファイルがあれば、そのファイルを読み込むノードだけを見る。"""
    from .groups import main_files
    from .workflow import loader_precision

    names = {f.name.replace("\\", "/").rsplit("/", 1)[-1].lower() for f in main_files(e)}
    e.workflow_precision = loader_precision(wf, names or None) if isinstance(wf, dict) else None
    set_precision(e)


def set_optional_files(e: ModelEntry, wf: Any) -> None:
    """テンプレートの既定の値で読み込まれないファイルに optional(inactive / switch_off)と、スイッチで
    入れ替わる既定の部品(optional_replaces)を付ける(optional.optional_files。#81)。当てるたびに作り直す
    (前の値は引き継がない)。つながりから分けられないときは付けない(全部を数える)。"""
    from .optional import SWITCH_OFF, base_name, optional_files

    found = optional_files(wf) if isinstance(wf, dict) else {}
    names = {base_name(f.name): f.name for f in e.files}
    for f in e.files:
        o = found.get(base_name(f.name))
        f.optional = o.kind if o else None
        f.optional_replaces = (
            [names[r] for r in o.replaces if r in names] if o and o.kind == SWITCH_OFF else None
        )


def _stages_of(wf: Any) -> list[list[str]] | None:
    from .workflow import WorkflowError, sampler_stages

    try:
        return sampler_stages(wf)
    except (WorkflowError, TypeError, ValueError, KeyError, IndexError, AttributeError, RecursionError):
        return None


def set_completeness(e: ModelEntry) -> None:
    """files_complete と size_floor_bytes(docs/schema.md)。

    False: 本体(role main。diffusion_models / unet / checkpoints)が無い、ファイルの合計が index の size の
    半分未満、または本体が GGUF だけでテキストエンコーダ・VAE が無い(HF のリポジトリの画像・動画・3D。
    拡散モデルの GGUF は本体だけを配る。読み上げなどの音声の GGUF は llama.cpp 系の実行環境向けの
    一式で、テキストエンコーダ・VAE という部品を持たないので当てはめない。#28)。
    False のとき、テンプレートの index の size を size_floor_bytes に入れる(見積もりが小さく見ないように)。
    """
    reasons = []
    if not any(f.role == "main" for f in e.files):
        reasons.append("no_main")
    total = sum(f.size_bytes or 0 for f in e.files)
    index_size = e.template.size_bytes if e.template else None
    if index_size and total < index_size * COMPLETE_RATIO:
        reasons.append("small_vs_index")
    mains = [f for f in e.files if f.role == "main"]
    if (
        e.source == "huggingface"
        and e.modality in GGUF_SPLIT_MODALITIES
        and mains
        and all(f.name.lower().endswith(".gguf") for f in mains)
        and not any(f.role in ("text_encoder", "vae") for f in e.files)
    ):
        reasons.append("gguf_without_encoder")
    e.files_complete = not reasons
    e.size_floor_bytes = index_size if reasons and index_size else None


def mark_template_content(e: ModelEntry, t: dict[str, Any], client: HfClient) -> None:
    for word in sorted(
        {m.group(1).lower() for m in _UNCENSORED.finditer(" ".join([e.id, e.name, *t.get("tags", [])]))}
    ):
        e.content_evidence.append(f"name:{word}")
    for f in e.files:
        p = parse_hf_url(f.url)
        info = client.repo(p[0], p[1]) if p else None
        if info and _NFAA in info.tags:
            ev = f"hf-tag:{_NFAA} ({info.repo_id})"
            if ev not in e.content_evidence:
                e.content_evidence.append(ev)
    if e.content_evidence:
        e.content = "uncensored"


# --- HF の検索から足す ----------------------------------------------------------------------------

# 無検閲: pipeline_tag ごとに「not-for-all-audiences のタグ」と「名前の語」で探す
UNCENSORED_PIPELINES = tuple(PIPELINES)
UNCENSORED_SEARCH_WORDS = ("uncensored", "nsfw", "abliterated", "heretic", "unfiltered")
# テンプレートで足りない種類を HF の人気順で補う(読み上げはローカルのテンプレートが無い。効果音は 1 件だけ)
SFW_QUERIES = (
    ("tts", {"pipeline_tag": "text-to-speech"}),
    ("sfx", {"pipeline_tag": "text-to-audio", "search": "sfx"}),
    ("sfx", {"pipeline_tag": "text-to-audio", "search": "foley"}),
    ("sfx", {"pipeline_tag": "text-to-audio", "search": "sound"}),
    ("sfx", {"pipeline_tag": "text-to-audio", "search": "audioldm"}),
    ("sfx", {"pipeline_tag": "text-to-audio", "search": "stable-audio-open"}),
)
CAP_UNCENSORED = 30  # 種類ごとの上限(一覧を大きくしすぎない)
CAP_SFW = {"tts": 20, "sfx": 10}
# 一覧に入れる下限(llmfit の scripts/scrape_hf_models.py にならう。docs/sources.md)。全期間のダウンロード数
MIN_DOWNLOADS = 10_000
# 新しく伸びているもの(trendingScore・likes30d の順で拾ったもの)は、全期間 1,000 以上で入れる
MIN_TRENDING_DOWNLOADS = 1_000
TRENDING_SORTS = ("trendingScore", "likes30d")
# 検索の結果に含める項目(全期間のダウンロード数は既定では返らない)
SEARCH_EXPAND = ("downloads", "downloadsAllTime", "likes", "tags", "pipeline_tag", "trendingScore")
# 伸びているもの(trendingScore・likes30d の検索だけで拾ったもの)に低い下限を使うのは、
# 本当に伸びているときだけ: trendingScore がこれ以上で、直近 30 日のダウンロード数が
# MIN_RECENT_DOWNLOADS 以上。それ以外は全期間 MIN_DOWNLOADS 以上
# (#19。古くて使われていないものが likes30d の順で拾われていた)
MIN_TRENDING_SCORE = 0.5
MIN_RECENT_DOWNLOADS = 100
# 名前に merge が入るものも混ぜ合わせ(タグが付いていないものがある。「mix」は入れない)
_MERGE_NAME = re.compile(r"(?<![a-z])(merge|mergekit)(?![a-z])", re.IGNORECASE)
# 量子化・形式違いの写しの名前の末尾(取り除いて同じ名前なら写し。例: Wai-NSFW-Illustrious-v140-Q8-GGUF)
_QUANT_SUFFIX = re.compile(
    r"[-_. ](gguf|ggml|q\d(_\d|_k(_[sml])?|_0|_1)?|k_[sml]|i?q\d_?xxs|fp8(_e4m3fn|_e5m2)?|e4m3fn"
    r"|fp16|bf16|fp32|nf4|int8|int4|w4a16|w8a8|diffusers|safetensors|quantized|quant)$",
    re.IGNORECASE,
)
# 試験用・作りかけのリポジトリの名前(llmfit と同じ)
_TEST_STUB = re.compile(r"tiny-random|tiny-dummy|-random-init|ci-random-|(^|/)test-", re.IGNORECASE)
_MERGE_TAGS = frozenset({"merge", "mergekit"})
_ADAPTER_TAGS = frozenset({"lora", "peft", "adapter", "qlora"})
MIN_MAIN_BYTES = 30_000_000  # 本体がこれより小さいものは、差分(LoRA など)か中身の無いリポジトリとみなす
MAX_PER_OWNER = 2
_ADAPTER = re.compile(r"(?<![a-z])(lora|loras|lycoris|locon|adapter|ip-?adapter|embedding)s?(?![a-z])", re.I)
_COMPONENT = re.compile(
    r"text[-_ ]?encoder|(?<![a-z])(vl|clip|vae|captioner|tagger|llm|gptq|awq|mlx)(?![a-z])"
    r"|qwen\d*(\.\d+)?[-_]?vl",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    repo_id: str
    modality: str
    task: str
    downloads: int
    likes: int
    tags: list[str]
    uncensored: bool
    downloads_all_time: int = 0  # 全期間(検索の結果に無ければ downloads)
    trending_only: bool = True  # trendingScore・likes30d の検索だけで拾った
    all_time_known: bool = False  # 検索の結果に downloadsAllTime があった(無ければ直近 30 日の数で代える)
    trending_score: float = 0.0  # 検索の結果の trendingScore(一番大きいもの)
    evidence: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


def hf_audio_modality(repo_id: str, tags: list[str]) -> str:
    text = repo_id.replace("-", " ").replace("_", " ") + " " + " ".join(tags)
    if _TTS.search(text):
        return "tts"
    if _SFX.search(text):
        return "sfx"
    if _MUSIC.search(text):
        return "music"
    if re.search(r"mmaudio|audioldm|stable.audio.open", text, re.I):
        return "sfx"  # 効果音が主な用途のモデル(docs/sources.md)
    return "music"


def _candidate(item: dict[str, Any], url: str, modality_hint: str | None) -> Candidate | None:
    pipe = item.get("pipeline_tag")
    if pipe not in PIPELINES:
        return None
    modality, task = PIPELINES[pipe]
    tags = [t for t in item.get("tags") or [] if isinstance(t, str)]
    if modality == "audio":
        modality = hf_audio_modality(item["id"], tags)
    if modality_hint and modality != modality_hint:
        return None
    ev = []
    if _NFAA in tags:
        ev.append(f"hf-tag:{_NFAA}")
    ev += [f"name:{w}" for w in sorted({m.group(1).lower() for m in _UNCENSORED.finditer(item["id"])})]
    return Candidate(
        repo_id=item["id"],
        modality=modality,
        task=task,
        downloads=int(item.get("downloads") or 0),
        downloads_all_time=int(item.get("downloadsAllTime") or item.get("downloads") or 0),
        trending_only="sort=downloads" not in url,
        all_time_known=item.get("downloadsAllTime") is not None,
        likes=int(item.get("likes") or 0),
        trending_score=float(item.get("trendingScore") or 0.0),
        tags=tags,
        uncensored=bool(ev),
        evidence=ev,
        queries=[url],
    )


def download_floor(c: Candidate) -> int:
    """全期間のダウンロード数の下限。伸びているもの(trending・likes30d の検索だけで拾い、trendingScore が
    MIN_TRENDING_SCORE 以上で、直近 30 日も MIN_RECENT_DOWNLOADS 以上)だけ MIN_TRENDING_DOWNLOADS。"""
    if c.trending_only and c.trending_score >= MIN_TRENDING_SCORE and c.downloads >= MIN_RECENT_DOWNLOADS:
        return MIN_TRENDING_DOWNLOADS
    return MIN_DOWNLOADS


def copy_name(repo_id: str) -> str:
    """写しを見分ける名前: リポジトリ名(持ち主を除く)から量子化・形式の末尾(-GGUF・-Q8_0・_K_M・-fp8・
    _diffusers など)を取り、英数字だけにする。同じなら同じモデルの写し(ダウンロード数の多いほうを残す)。"""
    name = repo_id.split("/", 1)[-1].lower()
    while True:
        cut = _QUANT_SUFFIX.sub("", name)
        if cut == name or not cut:
            break
        name = cut
    return re.sub(r"[^a-z0-9]", "", name)


def discover_hf(
    client: HfClient, *, progress: Callable[[str], None], stats: dict[str, Any]
) -> list[ModelEntry]:
    queries: list[tuple[str, str | None]] = []  # (url, modality_hint)
    # 新しく伸びているもの: 同じ絞り込みを trendingScore・likes30d の順でも見る
    # (無検閲は not-for-all-audiences の
    # タグだけ)。先に並べる(ダウンロード数の順の検索にも出たら、あとで trending_only を外す)
    for sort in TRENDING_SORTS:
        for pipe in UNCENSORED_PIPELINES:
            queries.append((search_url(filter=_NFAA, pipeline_tag=pipe, sort=sort), None))
        for mod, params in SFW_QUERIES:
            queries.append((search_url(**params, sort=sort), mod))
    for pipe in UNCENSORED_PIPELINES:
        queries.append((search_url(filter=_NFAA, pipeline_tag=pipe), None))
        for w in UNCENSORED_SEARCH_WORDS:
            queries.append((search_url(search=w, pipeline_tag=pipe), None))
    for mod, params in SFW_QUERIES:
        queries.append((search_url(**params), mod))
    queries = [(f"{u}&" + "&".join(f"expand[]={x}" for x in SEARCH_EXPAND), h) for u, h in queries]

    def _run(q: tuple[str, str | None]) -> tuple[str, str | None, list[dict[str, Any]] | FetchError]:
        try:
            return q[0], q[1], client.search(q[0])
        except FetchError as e:
            return q[0], q[1], e

    cands: dict[str, Candidate] = {}
    warnings: list[str] = []
    network_fail = 0
    for url, hint, res in client.map(_run, queries):
        if isinstance(res, FetchError):
            warnings.append(f"HF の検索に失敗: {stored_message(res)}")  # 一覧(sources)に残す文は日本語のまま
            network_fail += isinstance(res, NetworkError)
            continue
        for item in res:
            c = _candidate(item, url, hint)
            if c is None:
                continue
            if hint is None and not c.uncensored:
                continue  # 無検閲の検索で拾ったが根拠が無いもの
            old = cands.get(c.repo_id)
            if old:
                old.queries.append(url)
                old.evidence = sorted(set(old.evidence) | set(c.evidence))
                old.uncensored = old.uncensored or c.uncensored
                old.trending_only = old.trending_only and c.trending_only
                old.downloads_all_time = max(old.downloads_all_time, c.downloads_all_time)
                old.all_time_known = old.all_time_known or c.all_time_known
                old.trending_score = max(old.trending_score, c.trending_score)
                old.downloads = max(old.downloads, c.downloads)
            else:
                cands[c.repo_id] = c
    if network_fail and network_fail == len(queries):
        raise NetworkError(
            queries[0][0],
            tr("Hugging Face の検索につながりません", "cannot connect to the Hugging Face search"),
        )

    skipped: Counter[str] = Counter()
    excluded = 0
    pool: list[Candidate] = []
    for c in sorted(cands.values(), key=lambda c: (-c.downloads, c.repo_id)):
        if safety.is_blocked([c.repo_id, *c.tags], uncensored=c.uncensored):
            excluded += 1
            continue
        tags = {t.lower() for t in c.tags}
        if c.downloads_all_time < download_floor(c):
            skipped["skip_downloads"] += 1
        elif c.uncensored and c.downloads < MIN_RECENT_DOWNLOADS:
            skipped["uncensored_inactive"] += 1  # 無検閲は直近 30 日も使われているものだけ(#19)
        elif _TEST_STUB.search(c.repo_id):
            skipped["test_stub"] += 1
        elif tags & _MERGE_TAGS or _MERGE_NAME.search(c.repo_id.split("/", 1)[-1]):
            skipped["merge"] += 1
        elif _ADAPTER.search(c.repo_id) or tags & _ADAPTER_TAGS:
            skipped["adapter"] += 1
        elif _COMPONENT.search(c.repo_id):
            skipped["component"] += 1
        else:
            pool.append(c)

    # 写しの重複(同じリポジトリ名)は一番使われているものだけ。持ち主ごとに種類あたり 2 件まで
    seen_names: set[str] = set()
    per_owner: Counter[tuple[str, str, bool]] = Counter()
    picked: dict[tuple[str, bool], list[Candidate]] = {}
    for c in pool:
        base = copy_name(c.repo_id)
        owner = c.repo_id.split("/", 1)[0].lower()
        if base in seen_names:
            skipped["duplicate"] += 1
            continue
        if per_owner[(owner, c.modality, c.uncensored)] >= MAX_PER_OWNER:
            skipped["owner_cap"] += 1
            continue
        seen_names.add(base)
        per_owner[(owner, c.modality, c.uncensored)] += 1
        picked.setdefault((c.modality, c.uncensored), []).append(c)

    def cap(key: tuple[str, bool]) -> int:
        return CAP_UNCENSORED if key[1] else CAP_SFW.get(key[0], 0)

    shortlist = [c for key, cs in picked.items() for c in cs[: cap(key) * 2]]
    progress(
        tr(
            f"Hugging Face: 候補 {len(shortlist)} 件を確かめています",
            f"Hugging Face: checking {len(shortlist)} candidates",
        )
    )
    client.prefetch((c.repo_id, "main") for c in shortlist)
    client.prefetch_chains(
        b for c in shortlist for b in ((client.repo(c.repo_id) or RepoInfo(repo_id="")).base_models)
    )
    client.map(client.readme, [c.repo_id for c in shortlist])

    out: list[ModelEntry] = []
    count: Counter[tuple[str, bool]] = Counter()
    for c in shortlist:
        key = (c.modality, c.uncensored)
        if count[key] >= cap(key):
            skipped["over_cap"] += 1
            continue
        info = client.repo(c.repo_id)
        if info is None:
            skipped["fetch_failed"] += 1
            continue
        if safety.is_blocked(
            [c.repo_id, *info.tags],
            json.dumps(info.card, ensure_ascii=False) + "\n" + client.readme(c.repo_id),
            uncensored=c.uncensored,
        ):
            excluded += 1
            continue
        entry = hf_entry(c, info, client)
        if entry is None:
            skipped["no_weights"] += 1
            continue
        # 同じ重みの写し(持ち主違い・名前違い)は、ダウンロード数の多い先のものだけ(shortlist は多い順)
        if is_duplicate(entry, out):
            skipped["duplicate_weights"] += 1
            continue
        count[key] += 1
        out.append(entry)

    stats.update(
        {
            "queries": len(queries),
            "candidates": len(cands),
            "added": len(out),
            "skipped": dict(sorted(skipped.items())),
            "excluded": excluded,
            "failed_queries": len(warnings),
            # 全期間のダウンロード数が返らず、直近 30 日の数で下限を見た候補の数
            "all_time_missing": sum(1 for c in cands.values() if not c.all_time_known),
            "warnings": warnings,
        }
    )
    return out


def hf_entry(c: Candidate, info: RepoInfo, client: HfClient | None = None) -> ModelEntry | None:
    """HF のリポジトリ 1 つを一覧の 1 件にする。ライセンスはファイルごとに確かめる(hf.HfClient.resolve_file:
    base_model の道・系統の表。無検閲化したもので元が分からなければ不明)。"""
    chosen = select_weight_files(info)
    main_bytes = sum(info.files[p][0] or 0 for p, r in chosen if r == "main")
    if main_bytes < MIN_MAIN_BYTES:
        return None
    files = []
    for path, role in chosen:
        size, sha = info.files.get(path, (None, None))
        f = ModelFile(
            name=path,
            url=f"https://huggingface.co/{info.repo_id}/resolve/main/{path}",
            sha256=sha,
            size_bytes=size,
            repo_id=info.repo_id,
            gated=info.gated,
            role=role,
        )
        if client is not None:
            apply_license(f, info, client, path, discovered_uncensored=c.uncensored)
        else:
            set_file_license(
                f,
                Resolved(None, source=info.repo_id, path=[info.repo_id], evidence=[f"{UNVERIFIED}: 未確認"]),
            )
        files.append(f)
    main = next((f for f in files if f.role == "main"), files[0])
    e = ModelEntry(
        id=f"hf:{info.repo_id}",
        name=info.repo_id.split("/", 1)[1],
        modality=c.modality,
        tasks=[c.task],
        source="huggingface",
        files=files,
        params_b=guess_params_b(main.name, info.repo_id.split("/", 1)[1]),
        precision=guess_precision(main.name),
        base_model=info.base_models[0] if info.base_models else None,
        content="uncensored" if c.uncensored else "sfw",
        content_evidence=list(c.evidence),
        discovered_from=sorted(set(c.queries)),
        updated=(info.last_modified or "")[:10] or None,
        homepage=info.url,
        downloads=info.downloads if info.downloads is not None else c.downloads,
        downloads_all_time=c.downloads_all_time or None,
        likes=info.likes if info.likes is not None else c.likes,
    )
    set_entry_license(e)
    set_completeness(e)
    set_precision(e)
    return e


def lineage(e: ModelEntry) -> set[str]:
    """元のモデルの手がかり(たどった base_model と、名前で当たった系統)。写しの判定に使う。"""
    out: set[str] = set()
    for f in e.files:
        out |= {p for p in f.license_path if p != f.repo_id}
        out |= {x.split(":", 1)[1] for x in f.license_evidence if x.startswith("family:")}
    if e.base_model:
        out.add(e.base_model)
    # ライセンスが不明でも系統は名前で分かる(リポジトリ名・ファイル名)
    out |= {f.key for f in families_for([e.id, *(f.name for f in e.files)])}
    return out


def is_duplicate(e: ModelEntry, kept: list[ModelEntry]) -> bool:
    """同じ重みの写し: sha256 の組が同じ、または元のモデルが重なり合計の大きさが ±1% 以内。"""
    shas = {f.sha256 for f in e.files if f.sha256}
    size = e.total_size_bytes or 0
    lin = lineage(e)
    for k in kept:
        if k.modality != e.modality:
            continue
        kshas = {f.sha256 for f in k.files if f.sha256}
        if shas and shas == kshas:
            return True
        ksize = k.total_size_bytes or 0
        if lin & lineage(k) and size and ksize and abs(size - ksize) <= 0.01 * max(size, ksize):
            return True
    return False


# --- 全体 ----------------------------------------------------------------------------------------


_log = logging.getLogger("genmodelfit")


def _silent(msg: str) -> None:
    """進み具合の既定の行き先。ライブラリとしては何も表示せず、logging に渡すだけ。"""
    _log.info(msg)


def _previous_db() -> Database | None:
    """前の一覧: キャッシュ(前回の update の結果)があればそれ、無ければ同梱の一覧。"""
    cp = cached_db_path()
    if cp.exists():
        # 読めない・断られる型を含む古いキャッシュは、読むだけのコマンド(db.load)と同じく使わず同梱へ戻る
        # (update は新しい一覧でキャッシュを上書きするので、案内なしで進めてよい)
        with contextlib.suppress(OSError, ValueError, DatabaseError):
            return parse_db(json.loads(cp.read_text(encoding="utf-8")), "cache")
    try:
        return parse_db(json.loads(bundled_text("db.json")), "bundled")
    except (OSError, ValueError):
        return None


def size_check(e: ModelEntry) -> dict[str, Any] | None:
    """index の size と、ファイルの大きさの合計を比べる(docs/sources.md の表)。"""
    if not e.template or not e.template.size_bytes or not e.files:
        return None
    sizes = [f.size_bytes for f in e.files]
    if any(s is None for s in sizes):
        return None
    total = sum(sizes)  # type: ignore[arg-type]
    return {
        "id": e.id,
        "index_size": e.template.size_bytes,
        "files_total": total,
        "ratio": round(e.template.size_bytes / total, 3) if total else None,
    }


def count_entries(entries: list[ModelEntry]) -> dict[str, Any]:
    """sources.counts(docs/schema.md)。"""
    return {
        "models": len(entries),
        "modality": dict(sorted(Counter(e.modality for e in entries).items())),
        "content": dict(sorted(Counter(e.content for e in entries).items())),
        "source": dict(sorted(Counter(e.source for e in entries).items())),
        "license": dict(sorted(Counter(e.license or "null" for e in entries).items())),
        "license_tier": dict(
            sorted(Counter(_TIER_NAMES[license_tier(e.license, e.license_name)] for e in entries).items())
        ),
        "files_complete_false": sum(1 for e in entries if e.files_complete is False),
    }


def build_database(
    fetch: Fetcher | None = None,
    *,
    hf: bool = True,
    hf_search: bool = True,
    head: bool = True,
    workers: int = 8,
    progress: Callable[[str], None] | None = None,
    previous: Database | None = None,
    carry_over: bool = True,
    inspect: bool | None = None,
) -> Database:
    """上流から一覧を作る。書き出しはしない(run_update が書く)。

    inspect: 本体のファイルの見出しを読んで精度を決める(inspect_headers。既定は head と同じ。
        キャッシュにあるものは読み直さない)。

    fetch: 取得の仕方(既定は HttpFetcher)。試験では DirFetcher などを渡す。
    previous: 前の一覧(既定はキャッシュ、無ければ同梱の一覧)。実測のあるモデルが上流から消えたときと、
        一部が取れなかったとき(sources.partial)の、取れなかった分を引き継ぐ。後者は同じ LICENSE_POLICY で
        作った一覧からだけ(古い決め方の寛容すぎるライセンスを持ち込まない)。
    carry_over: False なら引き継がない(試験用)。
    """
    fetch = fetch or HttpFetcher()
    say = progress or _silent
    stats: dict[str, Any] = {}
    warnings: list[str] = []
    inspect = head if inspect is None else inspect

    say(
        tr(
            "ComfyUI 公式テンプレートの一覧を取得しています",
            "fetching the list of official ComfyUI templates",
        )
    )
    index = validate_index(fetch.get_json(INDEX_URL))
    total = sum(len(c["templates"]) for c in index)
    reasons: Counter[str] = Counter()
    chosen: list[tuple[str, dict[str, Any], str]] = []
    seen: set[str] = set()
    for cat in index:
        title = cat.get("title")
        for t in cat["templates"]:
            r = skip_reason(title, t)
            if r is None and t["name"] in seen:
                r = "duplicate"
            if r:
                reasons[r] += 1
                continue
            seen.add(t["name"])
            chosen.append((title, t, template_modality(title, t) or "image"))

    say(
        tr(
            f"テンプレート {len(chosen)} 件の JSON を取得しています",
            f"fetching the JSON of {len(chosen)} templates",
        )
    )
    client = HfClient(fetch, workers=workers)

    def _load(item: tuple[str, dict[str, Any], str]) -> tuple[Any, tuple[str, bool, str] | None]:
        try:
            wf = fetch.get_json(f"{RAW}/{item[1]['name']}.json")
            return (workflow_models(wf), _stages_of(wf), wf), None
        except (FetchError, UpstreamFormatError) as e:
            name = item[1]["name"]
            return None, (f"{name}: {e}", isinstance(e, NetworkError), f"{name}: {stored_message(e)}")

    entries: list[ModelEntry] = []
    tmpl_by_id: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    failed_ja: list[str] = []  # 一覧(sources.warnings)に残す文(日本語)
    failed_net: list[bool] = []
    failed_names: list[str] = []
    for item, (loaded, err) in zip(chosen, client.map(_load, chosen), strict=False):
        if err:
            failed.append(err[0])
            failed_net.append(err[1])
            failed_ja.append(err[2])
            failed_names.append(item[1]["name"])
            continue
        models, stages, wf = loaded
        e = template_entry(item[0], item[1], item[2], models)
        set_sampler_stages(e, stages)
        set_workflow_precision(e, wf)
        set_optional_files(e, wf)
        entries.append(e)
        tmpl_by_id[e.id] = item[1]
    if chosen and len(failed) / len(chosen) > MAX_FAIL_RATIO:
        if all(failed_net):
            raise NetworkError(
                RAW, tr("テンプレートの JSON を取得できません", "cannot fetch the template JSON")
            )
        raise UpstreamFormatError(
            tr(
                f"テンプレートの JSON の {len(failed)}/{len(chosen)} 件が読めません"
                "(上流の形が変わった可能性)。"
                f"例: {failed[0]}",
                f"cannot read {len(failed)}/{len(chosen)} template JSON files"
                " (the upstream format may have changed)."
                f" Example: {failed[0]}",
            )
        )
    warnings += [f"テンプレートを読めません: {f}" for f in failed_ja]

    excluded = 0
    kept: list[ModelEntry] = []
    for e in entries:
        t = tmpl_by_id[e.id]
        names = [e.id, e.name, *(t.get("tags") or [])]
        if safety.is_blocked(
            names, t.get("description") or "", uncensored=bool(_UNCENSORED.search(" ".join(names)))
        ):
            excluded += 1
            continue
        kept.append(e)
    entries = kept

    stats["comfyui-templates"] = {
        "url": INDEX_URL,
        "license": "MIT",
        "templates_total": total,
        "local": len(chosen),
        "skipped": dict(sorted(reasons.items())),
        "loaded": len(entries),
        "failed": len(failed),
    }

    if hf:
        say(
            tr(
                f"Hugging Face: {len(_hf_keys(entries))} リポジトリの情報を取得しています",
                f"Hugging Face: fetching information on {len(_hf_keys(entries))} repositories",
            )
        )
        enrich_files(entries, client, fetch, head=head)
        net = [f for f in client.failures if f.network]
        repos = len(_hf_keys(entries))
        if repos and len(net) / repos > MAX_FAIL_RATIO:
            raise NetworkError(
                "https://huggingface.co/api/models",
                tr("Hugging Face につながりません", "cannot connect to Hugging Face"),
            )
    for e in entries:
        mark_template_content(e, tmpl_by_id[e.id], client)

    hf_stats: dict[str, Any] = {}
    if hf and hf_search:
        say(
            tr(
                "Hugging Face: 無検閲のモデルと、読み上げ・効果音のモデルを探しています",
                "Hugging Face: looking for uncensored models and text-to-speech / sound-effect models",
            )
        )
        found = discover_hf(client, progress=say, stats=hf_stats)
        ids = {e.id for e in entries}
        entries += [e for e in found if e.id not in ids]
        excluded += hf_stats.get("excluded", 0)
        warnings += hf_stats.pop("warnings", [])

    prev = (previous if previous is not None else _previous_db()) if carry_over else None

    # 一部が取れなかった(テンプレートの JSON・HF の一時的な失敗・429 の待ち時間の上限・検索の失敗)
    transient = {f.repo_id for f in client.failures if f.transient}
    affected = {
        e.id
        for e in entries
        if any(f.repo_id in transient or set(f.license_path) & transient for f in e.files)
    }
    carried = 0
    if (
        prev is not None
        and prev.sources.get("license_policy") == LICENSE_POLICY
        and (affected or failed_names)
    ):
        prev_by_id = {m.id: m for m in prev.models}
        for i, e in enumerate(entries):
            if e.id in affected and e.id in prev_by_id:
                entries[i] = prev_by_id[e.id]
                carried += 1
                warnings.append(f"一部を取得できなかったため前の一覧の値を使いました: {e.id}")
        have = {e.id for e in entries}
        for name in failed_names:
            if name in prev_by_id and name not in have:
                entries.append(prev_by_id[name])
                carried += 1
                warnings.append(f"テンプレートを読めなかったため前の一覧から引き継ぎました: {name}")
    # 手で足すモデル(data/manual.json。#36)。ライセンスは上流のカードと照らし、HEAD・見出しの確認は
    # ほかのモデルと同じ道に乗せる
    manual = appinfo.manual_entries()
    entries = appinfo.merge_manual(entries, manual)
    stats["manual"] = {"models": len(manual), "ids": sorted(e.id for e in manual)}
    if hf:
        warnings += appinfo.verify_manual_licenses(entries, client)
    if inspect:
        say(
            tr(
                "本体のファイルの見出しを読んでいます(精度。重み本体は落としません)",
                "reading the headers of the weight files (precision; the weights are not downloaded)",
            )
        )
        stats["file_headers"] = inspect_headers(entries, fetch, progress=say)
        say(
            tr(
                "精度の分からない本体のリポジトリの情報(Hub API・config.json)を読んでいます",
                "reading repository information (Hub API, config.json) for weights of unknown precision",
            )
        )
        stats["repo_precision"] = inspect_repo_precision(entries, fetch)
    from .families import assign

    for e in entries:  # ComfyUI の系統(引き継いだ項目も、今の表で決め直す)
        assign(e)
    # アプリ向けの項目(言語・動かし方・説明。#36)。手の表 > HF のカード > 文字を読む部品の表。
    # カードの言語は前の一覧から引き継ぎ、読めたカードの分だけ上書きする(読めない回に揺れない。#42)
    carried_langs = appinfo.carry_card_languages(entries, prev.models) if prev is not None else 0
    stats["app_fields"] = appinfo.apply_app_fields(entries, client if hf else None)
    stats["app_fields"]["card_languages_carried"] = carried_langs
    budget = bool(getattr(fetch, "budget_exhausted", False))
    stats["partial"] = bool(failed or transient or hf_stats.get("failed_queries") or budget)
    stats["partial_counts"] = {
        "templates_failed": len(failed),
        "hf_repos_failed_transient": len(transient),
        "entries_affected": len(affected),
        "hf_search_failed": int(hf_stats.get("failed_queries", 0)),
        "rate_limit_budget_exhausted": budget,
        "carried_over": carried,
    }
    stats["license_policy"] = LICENSE_POLICY

    # 実測のあるモデルは、上流から消えても前の一覧から引き継ぐ(実測が宙に浮かないように)
    if prev is not None:
        from .db import load_measurements

        ids = {e.id for e in entries}
        measured = {m.model_id for m in load_measurements()}
        for old in prev.models:
            if old.id in measured and old.id not in ids:
                entries.append(old)
                warnings.append(f"上流に無いため前の一覧から引き継ぎました: {old.id}")

    if hf:
        stats["huggingface"] = {
            "api": "https://huggingface.co/api/models/{repo_id}?blobs=true",
            "repos": len({k for k in client._repos}),
            "failed": len(client.failures),
            "rate_limited": getattr(fetch, "rate_limited", 0),
            "rate_limit_waited_s": round(float(getattr(fetch, "rate_limit_waited", 0.0)), 1),
            "failures": [f"{f.repo_id}: {f.message}" for f in client.failures[:20]],
            # API が 401 のリポジトリ(非公開・消えた。ライセンスは不明)。
            # 毎週の更新の PR で増減が見えるように(#30)
            "unauthorized": sorted({f.repo_id for f in client.failures if f.status == 401}),
        }
        from .repo_precision import split_unauthorized

        # 401 のうち、無いと確かめたもの(匿名では非公開と見分けられない。repo_precision.KNOWN_MISSING)
        stats["huggingface"]["unauthorized_missing"] = split_unauthorized(
            stats["huggingface"]["unauthorized"]
        )[1]
    if hf_stats:
        stats["huggingface-search"] = hf_stats
    stats["civitai"] = "使っていません(利用規約。docs/sources.md)"
    if hf and head:
        say(
            tr(
                "ファイルの URL に匿名の HEAD を送っています(自動のおすすめの判定に使う)",
                "sending anonymous HEAD requests to the file URLs (used for auto-recommendation)",
            )
        )
        stats["head_check"] = check_heads(entries, fetch, workers=workers)
    say(tr("ComfyUI の最新の版を確かめています", "checking the latest ComfyUI version"))
    stats["comfyui"], warn = latest_comfyui(fetch)
    if warn:
        warnings.append(warn)
    checks = [c for c in (size_check(e) for e in entries) if c]
    stats["size_check"] = {
        "templates": len(checks),
        "equal": sum(1 for c in checks if c["index_size"] == c["files_total"]),
        "within_10pct": sum(1 for c in checks if c["ratio"] and 0.9 <= c["ratio"] <= 1.1),
    }
    # 確認の印(#42): 外した項目を除き、前の一覧と比べて pending を付ける(前の一覧が無ければ全部 new)
    entries, stats["review"] = review.finish(entries, prev.models if prev is not None else None)
    entries.sort(key=lambda e: (e.source != "comfyui-template", e.modality, e.id))
    stats["counts"] = count_entries(entries)
    stats["excluded"] = excluded
    stats["warnings"] = warnings
    stats["generator"] = f"genmodelfit {__version__}"
    if not entries:
        raise UpstreamFormatError(
            tr(
                "一覧が空になりました(上流の形が変わった可能性)",
                "the list came out empty (the upstream format may have changed)",
            )
        )
    return Database(
        models=entries,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        sources=stats,
        origin="path",
    )


# --- 書き出しまで(表示は cli.py) ----------------------------------------------------------------


class UpdateFailed(UpdateError):
    """更新をやめた(前の一覧はそのまま)。文はそのまま利用者に見せる。"""


def make_fetcher(offline_sources: str | None = None, rate_limit_budget: float | None = None) -> Fetcher:
    if offline_sources:
        return DirFetcher(offline_sources)
    budget = RATE_LIMIT_BUDGET if rate_limit_budget is None else rate_limit_budget
    return HttpFetcher(rate_limit_budget=budget)


def update_database(
    fetch: Fetcher,
    out_path: Path,
    *,
    hf: bool = True,
    head: bool = True,
    allow_partial: bool = False,
    progress: Callable[[str], None] | None = None,
    previous: Database | None = None,
) -> Database:
    """上流から一覧を作り、out_path に書く。やめたときは UpdateFailed(何も書かない)。

    previous: 引き継ぎに使う前の一覧(build_database と同じ。既定はキャッシュ、無ければ同梱)。
    毎週の更新(weekly.py)は main の一覧を渡す。
    """
    try:
        db = build_database(fetch, hf=hf, head=head, progress=progress, previous=previous)
    except NetworkError as e:
        raise UpdateFailed(
            tr(
                f"ネットワークにつながらないため更新できませんでした(前の一覧はそのまま使えます): {e}",
                f"could not update: no network connection (the previous list still works): {e}",
            )
        ) from e
    except (UpdateError, FetchError) as e:
        raise UpdateFailed(
            tr(
                f"更新できませんでした(前の一覧はそのまま使えます): {e}",
                f"could not update (the previous list still works): {e}",
            )
        ) from e
    if db.sources.get("partial") and not allow_partial:
        pc = db.sources["partial_counts"]
        detail = ", ".join(f"{k}={v}" for k, v in pc.items() if v)
        exhausted = bool(pc["rate_limit_budget_exhausted"])
        hint = "時間をおいてやり直すか、" if exhausted else ""
        hint_en = (
            "Try again later, or add --allow-partial if the missing parts are acceptable."
            if exhausted
            else "Add --allow-partial if the missing parts are acceptable."
        )
        raise UpdateFailed(
            tr(
                f"一部を取得できなかったため書き出しませんでした({detail})。前の一覧はそのまま使えます。"
                f"{hint}欠けたままでよければ --allow-partial を付けてください",
                f"Not written because some parts could not be fetched ({detail});"
                f" the previous list still works. {hint_en}",
            )
        )
    db.save(out_path)
    return db


def inspect_headers(
    entries: list[ModelEntry],
    fetch: Fetcher | None,
    *,
    offline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """本体のファイルの見出し(safetensors の JSON・GGUF の頭)を Range 要求で読み、精度を決め直す
    (headers.inspect_files → set_precision)。読んだ結果はキャッシュ(<cache>/file_headers.json)に残し、
    次からは読まない。offline=True、または fetch が Range 要求を持たない(試験の DirFetcher)ときは
    キャッシュだけを使う。返す値は sources["file_headers"] に入れる集計。"""
    from .db import cache_dir
    from .headers import CACHE_FILE, HeaderCache, inspect_files

    cache = HeaderCache(cache_dir() / CACHE_FILE)
    get_range = None if offline else getattr(fetch, "get_range", None)
    stats = inspect_files(entries, get_range, cache, offline=get_range is None, progress=progress)
    if stats.fetched:
        cache.save()
    for e in entries:
        set_precision(e)
    out = stats.to_dict()
    out["checked_at"] = _now()
    out["precision_unknown"] = sum(1 for e in entries if e.precision is None)
    return out


def inspect_repo_precision(
    entries: list[ModelEntry],
    fetch: Fetcher | None,
    *,
    offline: bool = False,
) -> dict[str, Any]:
    """見出しも名前も精度を示さない本体について、置き場所のリポジトリ(Hub API の safetensors の dtype、
    config.json の torch_dtype)から精度を読み、精度を決め直す(repo_precision.inspect_repos → set_precision)。
    inspect_headers の後に呼ぶ。結果は <cache>/repo_precision.json に残す。offline=True ならキャッシュだけ。
    返す値は sources["repo_precision"] に入れる集計(precision_unknown・precision_source の内訳を含む)。"""
    from .db import cache_dir
    from .repo_precision import CACHE_FILE, RepoPrecisionCache, inspect_repos

    cache = RepoPrecisionCache(cache_dir() / CACHE_FILE)
    get_json = None if offline or fetch is None else fetch.get_json
    stats = inspect_repos(entries, get_json, cache, offline=get_json is None)
    if stats.fetched:
        cache.save()
    for e in entries:
        set_precision(e)
    out = stats.to_dict()
    out["checked_at"] = _now()
    out["precision_unknown"] = sum(1 for e in entries if e.precision is None)
    out["precision_source"] = dict(sorted(Counter(e.precision_source or "unknown" for e in entries).items()))
    return out


def refresh_templates(
    entries: list[ModelEntry], fetch: Fetcher, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """公式テンプレートの JSON を読み直し、サンプラーごとの本体の組(sampler_stages)と、
    本体の読み込みノードから推す精度(workflow_precision)と、任意の部品(ModelFile.optional。#81)だけを
    決め直す。"""
    say = progress or _silent
    tmpl = [e for e in entries if e.source == "comfyui-template"]
    say(
        tr(
            f"テンプレート {len(tmpl)} 件の JSON からサンプラーごとの本体の組を読んでいます",
            f"reading the weight sets per sampler from the JSON of {len(tmpl)} templates",
        )
    )
    failed = 0
    for e in tmpl:
        try:
            wf = fetch.get_json(f"{RAW}/{e.id}.json")
        except FetchError:
            failed += 1
            continue
        set_sampler_stages(e, _stages_of(wf))
        set_workflow_precision(e, wf)
        set_optional_files(e, wf)
    multi = sum(1 for e in tmpl if e.sampler_stages)
    from_wf = sum(1 for e in tmpl if e.workflow_precision)
    return {
        "templates": len(tmpl),
        "failed": failed,
        "multi_stage": multi,
        "workflow_precision": from_wf,
        "optional_parts": sum(1 for e in tmpl if any(f.optional for f in e.files)),
        "checked_at": _now(),
    }


def inspect_database(
    fetch: Fetcher | None,
    out_path: Path,
    *,
    db_path: str | None = None,
    use_cache: bool = True,
    offline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Database:
    """今の一覧をそのまま使い、ファイルの中身から分かることだけを決め直す(update --inspect-only):
    本体のファイルの見出しの精度(inspect_headers)と、テンプレートのサンプラーごとの本体の組
    (refresh_templates。offline のときは読まない)。ほかの値は変えない。"""
    from .db import DatabaseError
    from .db import load as load_db

    say = progress or _silent
    try:
        db = load_db(db_path, use_cache=use_cache)
    except (DatabaseError, OSError, ValueError) as e:
        raise UpdateFailed(tr(f"一覧を読めません: {e}", f"cannot read the list: {e}")) from e
    before = copy.deepcopy(db.models)  # 確認の印を付けるために比べる元(精度が変わると必要量も変わる)
    if not offline and fetch is not None:
        db.sources["sampler_stages"] = refresh_templates(db.models, fetch, say)
    say(
        tr(
            "本体のファイルの見出しを読んでいます(精度。重み本体は落としません)",
            "reading the headers of the weight files (precision; the weights are not downloaded)",
        )
    )
    db.sources["file_headers"] = inspect_headers(db.models, fetch, offline=offline, progress=say)
    say(
        tr(
            "精度の分からない本体のリポジトリの情報(Hub API・config.json)を読んでいます",
            "reading repository information (Hub API, config.json) for weights of unknown precision",
        )
    )
    db.sources["repo_precision"] = inspect_repo_precision(db.models, fetch, offline=offline)
    hf = db.sources.get("huggingface")
    if not offline and fetch is not None and isinstance(hf, dict):
        # 前に API が 401 だったリポジトリ(非公開)が公開されたかを確かめ直す(毎週の更新の差分に出る。#30)
        from .repo_precision import recheck_unauthorized

        say(
            tr(
                "前に読めなかった(401)リポジトリを確かめ直しています",
                "rechecking repositories that could not be read before (401)",
            )
        )
        hf["unauthorized_recheck"] = recheck_unauthorized(unauthorized_repos(hf), fetch.get_json)
        hf["unauthorized"] = list(hf["unauthorized_recheck"]["unauthorized"])
        hf["unauthorized_missing"] = list(hf["unauthorized_recheck"]["missing"])
    _finish_reviews(db, before)
    db.save(out_path)
    return db


def _finish_reviews(db: Database, before: list[ModelEntry]) -> None:
    """一覧の一部だけをやり直したとき(--inspect-only・--heads-only・--fields-only)の確認の印(#42):
    外した項目を除き、やり直す前と比べて pending を付ける(新しく足した手のモデル・下がった精度など)。"""
    db.models, db.sources["review"] = review.finish(db.models, before)
    db.sources["counts"] = count_entries(db.models)


def unauthorized_repos(hf_stats: dict[str, Any]) -> list[str]:
    """sources["huggingface"] の、API が 401 のリポジトリ(unauthorized。古い一覧では failures の
    「<repo>: HTTP 401: …」から読む)。"""
    if isinstance(hf_stats.get("unauthorized"), list):
        return sorted({r for r in hf_stats["unauthorized"] if isinstance(r, str)})
    out = set()
    for line in hf_stats.get("failures") or []:
        if isinstance(line, str):
            repo, _, rest = line.partition(": ")
            if rest.startswith("HTTP 401") and "/" in repo:
                out.add(repo)
    return sorted(out)


def refresh_heads(
    fetch: Fetcher,
    out_path: Path,
    *,
    db_path: str | None = None,
    use_cache: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Database:
    """今の一覧(db_path / キャッシュ / 同梱)をそのまま使い、HEAD と ComfyUI の版の確認だけをやり直す。

    結果は sources["head_check"] と sources["comfyui"] に入る。やめたときは UpdateFailed。
    """
    from .db import DatabaseError
    from .db import load as load_db

    say = progress or _silent
    try:
        db = load_db(db_path, use_cache=use_cache)
    except (DatabaseError, OSError, ValueError) as e:
        raise UpdateFailed(tr(f"一覧を読めません: {e}", f"cannot read the list: {e}")) from e
    before = copy.deepcopy(db.models)
    db.models = appinfo.merge_manual(db.models)
    say(
        tr(
            f"{len(db.models)} 件のファイルの URL に匿名の HEAD を送っています",
            f"sending anonymous HEAD requests to the file URLs of {len(db.models)} entries",
        )
    )
    stats = check_heads(db.models, fetch)
    if stats["urls"] and stats["unchecked"] == stats["urls"]:
        raise UpdateFailed(
            tr(
                "HEAD がすべて失敗しました(ネットワークを確かめてください。一覧はそのままです)",
                "every HEAD request failed (check the network; the list is unchanged)",
            )
        )
    db.sources["head_check"] = stats
    prev_comfy = db.sources.get("comfyui") if isinstance(db.sources.get("comfyui"), dict) else None
    db.sources["comfyui"], warn = latest_comfyui(fetch)
    if warn and prev_comfy and prev_comfy.get("latest_release"):
        # 取れなかったときは前に記録した版を残す(None で上書きすると gate の版の条件が全部保留になる)
        db.sources["comfyui"] = prev_comfy
        warn = f"{warn}(前に記録した {prev_comfy['latest_release']} を残しました)"
    warnings = [w for w in db.sources.get("warnings", []) if not w.startswith("ComfyUI の最新の版")]
    db.sources["warnings"] = warnings + ([warn] if warn else [])
    from .families import assign

    for m in db.models:  # ComfyUI の系統も今の表で決め直す(古い一覧に無い項目を足す)
        assign(m)
    db.sources["app_fields"] = appinfo.apply_app_fields(db.models)
    _finish_reviews(db, before)
    db.save(out_path)
    return db


def refresh_app_fields(
    fetch: Fetcher | None,
    out_path: Path,
    *,
    db_path: str | None = None,
    use_cache: bool = True,
    offline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Database:
    """今の一覧をそのまま使い、アプリ向けの項目(languages / runtime / description)と手のモデルだけを
    当て直す(update --fields-only)。既存のモデルの判定に関わる項目
    (ファイル・ライセンス・精度・HEAD)は、手で足す部品(data/manual.json の components。#40)を除いて変えない。

    - 手の表(data/overrides.json)・文字を読む部品の表・HF のカードの言語から、項目を作り直す。
      offline でなく fetch があれば、各モデルのカード(本体の置き場所とその元。一覧の判定には使わない)を
      読み直して card_languages を更新する。offline なら前の一覧に保存した card_languages を使う
    - 手のモデル(data/manual.json)を足す。offline でなく fetch があれば、そのファイルだけ HEAD・見出しの精度・
      上流のカードとのライセンスの照合をする(ほかのモデルには送らない)
    - 手で足す部品を、その ID の既存の項目のファイルに足す(ライセンスは license_sources の行。
      一式のライセンス・大きさも決め直す)。offline でなく fetch があれば、まだ HEAD で確かめていない
      部品のファイルにだけ HEAD を送る
    """
    from .db import DatabaseError
    from .db import load as load_db

    say = progress or _silent
    try:
        db = load_db(db_path, use_cache=use_cache)
    except (DatabaseError, OSError, ValueError) as e:
        raise UpdateFailed(tr(f"一覧を読めません: {e}", f"cannot read the list: {e}")) from e
    before = copy.deepcopy(db.models)
    manual = appinfo.manual_entries()
    if not offline and fetch is not None:
        say(
            tr(
                f"手のモデル {len(manual)} 件のライセンスを上流のカードと照らしています",
                f"checking the licenses of {len(manual)} hand-added models against the upstream cards",
            )
        )
        db.sources["warnings"] = [
            w for w in db.sources.get("warnings", []) if not w.startswith("手のモデル ")
        ] + appinfo.verify_manual_licenses(manual, HfClient(fetch))
        say(
            tr(
                "手のモデルのファイルの URL に匿名の HEAD を送り、見出しを読んでいます",
                "sending anonymous HEAD requests to the hand-added models' file URLs"
                " and reading their headers",
            )
        )
        check_heads(manual, fetch)
        inspect_headers(manual, fetch, progress=say)
        inspect_repo_precision(manual, fetch)
    db.models = appinfo.merge_manual(db.models, manual)
    db.sources["manual"] = {"models": len(manual), "ids": sorted(e.id for e in manual)}
    # 手で足した部品(#40)のうち、まだ HEAD で確かめていないファイルだけ確かめる(ほかのファイルには送らない。
    # 部品は本体でないので見出しは読まない)
    comps = set(appinfo.components())
    fresh = [
        ModelEntry(
            id=m.id, name=m.name, modality=m.modality, files=[f for f in m.files if f.head_checked_at is None]
        )
        for m in db.models
        if m.id in comps
    ]
    fresh = [m for m in fresh if m.files]
    if fresh and not offline and fetch is not None:
        say(
            tr(
                "手で足した部品のファイルの URL に匿名の HEAD を送っています",
                "sending anonymous HEAD requests to the file URLs of the hand-added parts",
            )
        )
        check_heads(fresh, fetch)
    client = None
    if not offline and fetch is not None:
        client = HfClient(fetch)
        repos = sorted({r for m in db.models if m.source != "manual" for r in appinfo.card_repos(m)})
        say(
            tr(
                f"{len(repos)} 件のリポジトリのカードを読み、言語を確かめています",
                f"reading the cards of {len(repos)} repositories to check the languages",
            )
        )
        client.prefetch((r, "main") for r in repos)
    db.sources["app_fields"] = appinfo.apply_app_fields(db.models, client)
    _finish_reviews(db, before)
    db.save(out_path)
    return db
