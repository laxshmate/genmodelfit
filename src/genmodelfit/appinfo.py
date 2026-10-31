# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""アプリが一覧だけで画面を作るための項目(`languages` / `runtime` / `description`。0.5.0、#36)と、
手で足すモデル(`source: "manual"`)。

- 言語の決め方(`apply_app_fields`。当てるたびに材料から作り直す):
  1) 手の表 `data/overrides.json`(人が一次資料か実機で確かめたもの。書いた種類はこれ)
  2) HF のカードの `language`(取れたものは `card_languages` に保存して持つ)。カードが言語を書いていれば
     それが上限で、文字を読む部品がすべて英語だけのもの(CLIP・T5)なら en だけ残す。読み上げはカードだけ。
     音楽はカードの言語が歌詞のことが多いので、指示は en だけ残し、歌の言語はカードから決めない(#41)
  3) カードが無いときは、文字を読む部品の表(`TEXT_ENCODERS`・`FAMILY_PROMPT`)に当たれば en
     (多言語の部品でも、表だけでは ja を付けない)。
  どれでも決まらなければ空にする(当て推量で ja を付けない)。
  根拠は docs/sources.md「言語・動かし方・説明」
- 手で足すモデルは `data/manual.json`(`manual_entries`)。ライセンスは書いた人が一次資料で確かめた値で、
  update が取れたときだけ上流のカードと照らす(`verify_manual_licenses`。食い違えば厳しいほうに寄せて警告)
"""

from __future__ import annotations

import json
import re
from typing import Any

from .db import bundled_text
from .i18n import tr
from .models import ModelEntry

OVERRIDES_FILE = "overrides.json"
MANUAL_FILE = "manual.json"

# 部品の表で分かるのは「部品が読める言語」で、モデルが学習した説明文の言語ではない(Wan・Qwen-Image・
# HunyuanVideo 1.5 は多言語の部品を使うが、カードは en・zh)。そのため表は、カードの言語を狭める
# (英語だけの部品なら en だけ残す)のと、カードが無いときに en を付けるのにだけ使う。表だけで ja は付けない
ENGLISH = ["en"]

# ファイル名(小文字)の正規表現 → (結果, 根拠)。結果 "multi" = 多言語(カードの言語を狭めない)、
# "en" = 英語だけ。
# 上から当てる(umt5 を t5 より先に)。出典は docs/sources.md「言語・動かし方・説明」と同じ
TEXT_ENCODERS: tuple[tuple[str, str, str], ...] = (
    # google/umt5-xxl のカードの language に multilingual と ja・zh・en が並ぶ(mC4 の多言語で事前学習)
    (r"umt5", "multi", "https://huggingface.co/google/umt5-xxl"),
    # Qwen3 の LLM(Qwen/Qwen3-4B のカード「Support of 100+ languages and dialects」)
    (r"^qwen_3_", "multi", "https://huggingface.co/Qwen/Qwen3-4B"),
    # Qwen3.5(Qwen/Qwen3.5-9B のカード「Expanded support to 201 languages and dialects」)
    (r"^qwen3[._]5", "multi", "https://huggingface.co/Qwen/Qwen3.5-9B"),
    # Qwen2.5-VL は Qwen2.5 の LLM が元(Qwen/Qwen2.5-7B-Instruct のカード
    # 「over 29 languages, including … Japanese」)。
    # Qwen3-VL(qwen3vl_*)はカードに言語の記述が無いので表に入れない(空)
    (r"^qwen[_-]?2[._]?5[_-]?vl", "multi", "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct"),
    # OpenAI CLIP のカード「not been purposefully trained in or evaluated on any languages other than
    # English」。clip_g(OpenCLIP bigG)も同じ系統の英語の画像説明
    (r"^clip[_-][lg]\b|^clip[_-][lg][_.]", "en", "https://huggingface.co/openai/clip-vit-large-patch14"),
    # T5 v1.1(t5xxl・t5-base)は C4(英語)で事前学習(カードの language: en)
    (r"^t5[_-]?xxl|^t5[_-]base|^t5-v1", "en", "https://huggingface.co/google/t5-v1_1-xxl"),
)
# 本体に文字を読む部品が入っている系統(チェックポイントに CLIP が入った SD 系)
FAMILY_PROMPT: dict[str, tuple[str, str]] = {
    "sd": ("en", "SD 1.x / 2.x / XL はチェックポイントの中の CLIP(英語の画像説明で学習)で指示を読む"),
}
# 指示(文章)を読む作業。これが無いモデル(画像から 3D など)は prompt を決めない
PROMPT_TASKS = frozenset(
    {"t2i", "i2i", "t2v", "i2v", "v2v", "flf2v", "ref2v", "a2v", "t2a", "inpaint", "outpaint", "control"}
)

_ISO3 = {
    "eng": "en",
    "jpn": "ja",
    "zho": "zh",
    "chi": "zh",
    "kor": "ko",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "por": "pt",
    "ita": "it",
    "rus": "ru",
}


def normalize_languages(value: Any) -> list[str]:
    """HF のカードの language(文字列か並び)を ISO 639-1 の 2 文字にそろえる。multilingual・und・3 文字の
    知らないものは捨てる。順序は保ち、重ねない。"""
    items = [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
    out: list[str] = []
    for x in items:
        if not isinstance(x, str):
            continue
        code = x.strip().lower().replace("_", "-").split("-")[0]
        code = _ISO3.get(code, code)
        if re.fullmatch(r"[a-z]{2}", code) and code not in out:
            out.append(code)
    return out


def card_languages(info: Any) -> list[str]:
    """RepoInfo(HF のカード)の language。無ければ空。"""
    card = getattr(info, "card", None) or {}
    return normalize_languages(card.get("language"))


def uses_prompt(e: ModelEntry) -> bool:
    return e.modality != "tts" and bool(PROMPT_TASKS & set(e.tasks))


def prompt_kind(e: ModelEntry) -> str | None:
    """文字を読む部品の表の結果。多言語の部品が 1 つでもあれば "multi"、すべて英語の部品なら "en"、
    表に無い部品が混じれば None。部品が無ければ系統の表。指示の文を取らないモデルは None。
    テンプレートの既定の値で読み込まない部品(ModelFile.optional。#81)は見ない。"""
    if not uses_prompt(e):
        return None
    kinds: list[str | None] = []
    for f in e.files:
        if f.role != "text_encoder" and f.directory != "text_encoders":
            continue
        if f.optional:  # 既定で読み込まない部品(指示の書き直しなど。#81)は材料にしない
            continue
        name = f.name.rsplit("/", 1)[-1].lower()
        hit = next((k for pat, k, _ in TEXT_ENCODERS if re.search(pat, name)), None)
        kinds.append(hit)
    if "multi" in kinds:
        return "multi"
    if kinds:
        return "en" if all(k == "en" for k in kinds) else None
    fam = FAMILY_PROMPT.get(e.comfy_family or "")
    return "en" if fam and fam[0] == "en" else None


def prompt_from_tables(e: ModelEntry) -> list[str]:
    """カードが無いときに、部品の表だけで決める指示の言語。表に当たれば en(多言語の部品でも ja は付けない)、
    当たらなければ決めない(空)。"""
    return list(ENGLISH) if prompt_kind(e) else []


def runtime_of(e: ModelEntry) -> str | None:
    """動かす仕組み。公式テンプレート由来は comfyui。HF 由来は、ComfyUI の系統に当たる画像・動画・音楽・
    効果音・3D だけ comfyui(読み上げは仕組みがモデルごとに違うので規則では決めない。手の表 overrides.json の
    runtime に書く。#142。docs/sources.md「動かし方」)。"""
    if e.source == "comfyui-template":
        return "comfyui"
    if e.source == "huggingface" and e.modality != "tts" and e.comfy_family:
        return "comfyui"
    return None


_cache: dict[str, Any] = {}


def _bundled_json(name: str) -> Any:
    if name not in _cache:
        _cache[name] = json.loads(bundled_text(name))
    return _cache[name]


def overrides() -> dict[str, dict[str, Any]]:
    """手の表(id → languages / runtime / description)。"""
    return dict(_bundled_json(OVERRIDES_FILE).get("models", {}))


def apply_app_fields(
    entries: list[ModelEntry],
    client: Any = None,
    *,
    table: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """languages / runtime / description を決めて入れる(update と、通信しない書き直しで使う)。

    当てるたびに「カードの言語(card_languages)+ 部品の表 + 手の表」から作り直す(前の languages・runtime・
    description は引き継がない。表を狭めたり手の表から行を消したりすると、次の当て直しで直る)。
    手のモデル(source: manual)は manual.json に書いた値が出発点(merge_manual が毎回作り直す)。

    client: HfClient。あれば取得済みのカードの language を card_languages に保存する(通信はしない)。
    無ければ保存済みの card_languages を使う。返す値は集計(sources["app_fields"])。"""
    table = overrides() if table is None else table
    stats = {"languages_prompt": 0, "languages_speech": 0, "runtime": 0, "description": 0, "from_card": 0}
    for e in entries:
        manual = e.source == "manual"
        if client is not None and not manual:
            got = _card_for_entry(e, client)
            if got is not None:  # 読めなかったら前に保存したものを使う
                e.card_languages = got
        row = table.get(e.id) or {}
        langs = {k: list(v) for k, v in (e.languages or {}).items()} if manual else _derived_languages(e)
        if not manual and e.card_languages:
            stats["from_card"] += 1
        for k, v in (row.get("languages") or {}).items():
            langs[k] = list(v)
        e.languages = {k: langs[k] for k in ("prompt", "speech") if k in langs}
        if row.get("runtime"):
            e.runtime = row["runtime"]
        elif not manual or not e.runtime:
            e.runtime = runtime_of(e)
        if row.get("description"):
            e.description = dict(row["description"])
        elif not manual:
            e.description = {}
        stats["languages_prompt"] += bool(e.languages.get("prompt"))
        stats["languages_speech"] += bool(e.languages.get("speech"))
        stats["runtime"] += e.runtime is not None
        stats["description"] += bool(e.description)
    return stats


def _derived_languages(e: ModelEntry) -> dict[str, list[str]]:
    """カードの言語と部品の表から決める(手の表を当てる前)。

    カードが言語を書いていれば、それが上限(英語だけの部品ならカードの en だけ残す。音楽も en だけ。
    ほかはカードのまま)。
    カードが無ければ部品の表(当たれば en)。読み上げはカードだけ。"""
    card = list(e.card_languages or [])
    if e.modality == "tts":
        return {"speech": card} if card else {}
    if not uses_prompt(e):
        return {}
    kind = prompt_kind(e)
    if not card:
        prompt = prompt_from_tables(e)
    elif e.modality == "music":
        # 音楽のカードの language は歌詞(歌える言語)のことが多く、指示の言語とは限らない(ACE-Step v1 の
        # カードは 17 言語で、歌の入らないワークフローにも付いていた。#41)。指示はカードのうち en だけにし、
        # 歌の言語(speech)もカードからは決めない(歌の入らないワークフローがあるので。手の表で書く)
        prompt = [x for x in card if x in ENGLISH]
    elif kind == "en":
        prompt = [x for x in card if x in ENGLISH]
    else:
        prompt = card
    return {"prompt": prompt} if prompt else {}


def card_repos(e: ModelEntry) -> list[str]:
    """言語を読むカードの置き場所: 本体のファイルの置き場所とその元(license_from)。HF 由来は自分自身が先。
    既定で読み込まない本体(ModelFile.optional。#81)は見ない。"""
    used = [f for f in e.files if not f.optional] or e.files
    mains = [f for f in used if f.role == "main"] or used[:1]
    repos: list[str] = []
    for f in mains:
        for r in (f.repo_id, f.license_from):
            if r and r not in repos:
                repos.append(r)
    if e.source == "huggingface" and e.id.startswith("hf:") and e.id[3:] not in repos:
        repos.insert(0, e.id[3:])
    return repos


def _card_for_entry(e: ModelEntry, client: Any) -> list[str] | None:
    """カードの language。card_repos の順に見て、最初に言語のあったカードのもの。

    途中のカードが読めなかった(401・失敗・まだ取っていない)ら、そこで None にする(前に保存した値を使う)。
    読めなかったカードを飛ばして次のカードを使うと、読めたかどうかで答えが毎回変わるため(#42 の毎週の
    作り直しで、gated の facebook/sam3 が読めない回だけ Wan のカードの zh が付いた)。すべて読めて、どれにも
    言語が無ければ []。読むカードが無ければ None。"""
    repos = card_repos(e)
    if not repos:
        return None  # 読むカードが無い(前の値のまま。無ければ None = まだ読んでいない)
    for r in repos:
        info = client.cached_repo(r)
        if info is None:
            return None
        langs = card_languages(info)
        if langs:
            return langs
    return []


def carry_card_languages(entries: list[ModelEntry], previous: list[ModelEntry]) -> int:
    """作り直した項目に、前の一覧で保存したカードの言語(card_languages)を引き継ぐ(まだ入っていないものだけ)。
    カードが読めなかった回に言語が消えたり変わったりしないように
    (apply_app_fields は読めたときだけ上書きする)。
    返す値は引き継いだ数。"""
    prev = {m.id: m for m in previous}
    n = 0
    for e in entries:
        old = prev.get(e.id)
        if e.card_languages is None and old is not None and old.card_languages is not None:
            e.card_languages = list(old.card_languages)
            n += 1
    return n


# --- 手で足すモデル -----------------------------------------------------------------------------------


def manual_entries(data: dict[str, Any] | None = None) -> list[ModelEntry]:
    """data/manual.json から ModelEntry を作る。ライセンス・大きさ・系統などは update の関数でそろえる。"""
    from .families import assign
    from .update import set_completeness, set_entry_license, set_precision

    data = _bundled_json(MANUAL_FILE) if data is None else data
    out: list[ModelEntry] = []
    for raw in data.get("models", []):
        e = ModelEntry.from_dict({**raw, "source": "manual"})
        for f in e.files:
            if f.repo_id is None and f.url and "huggingface.co/" in f.url:
                parts = f.url.split("/")
                f.repo_id = "/".join(parts[3:5])
        set_entry_license(e)
        set_completeness(e)
        set_precision(e)
        assign(e)
        out.append(e)
    return out


_MEASURED_FILE_FIELDS = (
    "head_status",
    "head_checked_at",
    "header_precision",
    "header_params",
    "repo_precision",
    "repo_precision_source",
)


def manual_repos(m: ModelEntry) -> list[str]:
    """手のモデルそのものの HF のリポジトリ(本体のファイルの license_from と、homepage)。HF の収集が同じ
    リポジトリを拾ったら、その hf: の項目は手のモデルと重なるので一覧に入れない。部品の license_from
    (T5Gemma など)と、元のモデル(Irodori-TTS v4 のような別の版の base_model)は数えない。"""
    out: list[str] = []
    for f in m.files:
        if f.role == "main" and f.license_from and f.license_from not in out:
            out.append(f.license_from)
    hp = (m.homepage or "").split("huggingface.co/", 1)
    if len(hp) == 2:
        r = "/".join(hp[1].strip("/").split("/")[:2])
        if r.count("/") == 1 and r not in out:
            out.append(r)
    return out


def merge_manual(entries: list[ModelEntry], manual: list[ModelEntry] | None = None) -> list[ModelEntry]:
    """手のモデルを一覧に入れる(同じ id は手のものに置き換える。同じリポジトリの hf: の項目は除く)。
    並びは update と同じ。
    置き換える前の同じ URL のファイルに、通信で確かめた値(HEAD・見出しの精度)があれば引き継ぐ
    (通信しない書き直しで消さないため)。"""
    from .update import set_precision

    manual = manual_entries() if manual is None else manual
    old = {e.id: e for e in entries}
    for m in manual:
        prev = old.get(m.id)
        if prev is None:
            continue
        by_url = {f.url: f for f in prev.files if f.url}
        for f in m.files:
            pf = by_url.get(f.url)
            if pf is None or pf.sha256 != f.sha256:
                continue
            for k in _MEASURED_FILE_FIELDS:
                if getattr(f, k) is None and getattr(pf, k) is not None:
                    setattr(f, k, getattr(pf, k))
        set_precision(m)
    ids = {m.id for m in manual} | {f"hf:{r}" for m in manual for r in manual_repos(m)}
    merged = [e for e in entries if e.id not in ids] + manual
    add_components(merged)
    merged.sort(key=lambda e: (e.source != "comfyui-template", e.modality, e.id))
    return merged


# --- 手で足す部品 -------------------------------------------------------------------------------------


def components(data: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """data/manual.json の components(モデルの ID → {evidence, files})。形を確かめる(ValueError)。

    足すファイルは https の url が必須(同じファイルかを URL で見るので、URL の無い行は当てるたびに重なる)。
    1 つのモデルの中で同じ URL は 1 回だけ。"""
    data = _bundled_json(MANUAL_FILE) if data is None else data
    table = data.get("components") or {}
    out = {k: v for k, v in table.items() if not k.startswith("_")}
    for mid, row in out.items():
        if not isinstance(row, dict) or not isinstance(row.get("files"), list) or not row["files"]:
            raise ValueError(
                tr(
                    f"components の {mid}: files(1 つ以上)がありません",
                    f"components {mid}: no files (one or more needed)",
                )
            )
        seen: set[str] = set()
        for raw in row["files"]:
            url = raw.get("url") if isinstance(raw, dict) else None
            if not isinstance(url, str) or not url.startswith("https://") or not raw.get("name"):
                raise ValueError(
                    tr(
                        f"components の {mid}: ファイルに https の url と name が要ります: {raw!r}",
                        f"components {mid}: a file needs an https url and a name: {raw!r}",
                    )
                )
            if url in seen:
                raise ValueError(
                    tr(
                        f"components の {mid}: 同じ url が 2 回あります: {url}",
                        f"components {mid}: the same url appears twice: {url}",
                    )
                )
            seen.add(url)
    return out


def add_components(entries: list[ModelEntry], table: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """HF の収集では入らない部品のファイルを、その ID の項目に足す(#40。Stable Audio 3 の T5Gemma など)。

    ライセンスは書いた値を使わず、部品の出どころの表(license_sources.SOURCES。一次資料で確かめた行)で決める。
    行が無ければ不明(license_unverified)にする(寛容な側に誤らない)。どの部品をそのモデルが使うかの根拠は
    `manual_component: …` として license_evidence に足す。同じ URL のファイルがすでにあれば足さず、
    ライセンスだけ表で当て直す(通信で確かめた HEAD・見出しの値はそのまま。何度当てても同じ結果)。
    足したり当て直したりした項目の ID を返す。"""
    from .hf import UNVERIFIED, Resolved, parse_hf_url
    from .license_sources import COMPONENT_EVIDENCE, source_for
    from .models import ModelFile
    from .update import set_completeness, set_entry_license, set_file_license, set_precision

    table = components() if table is None else components({"components": table})
    touched: list[str] = []
    for e in entries:
        row = table.get(e.id)
        if not row:
            continue
        by_url = {f.url: f for f in e.files if f.url}
        for raw in row.get("files") or []:
            f = by_url.get(raw.get("url"))
            if f is None:
                f = ModelFile.from_dict({k: v for k, v in raw.items() if not k.startswith("_")})
                e.files.append(f)
            loc = parse_hf_url(f.url) if f.url else None
            if loc and f.repo_id is None:
                f.repo_id = loc[0]
            src = source_for(loc[0], loc[2]) if loc else None
            if src is None:
                why = "部品の出どころの表(license_sources)に行がありません"
                set_file_license(f, Resolved(None, path=[], evidence=[f"{UNVERIFIED}: {why}"]))
            else:
                set_file_license(f, src.resolved(loc[0]))
            if row.get("evidence"):
                f.license_evidence.append(f"{COMPONENT_EVIDENCE}: {row['evidence']}")
        set_entry_license(e)
        set_completeness(e)
        set_precision(e)
        touched.append(e.id)
    return touched


def verify_manual_licenses(entries: list[ModelEntry], client: Any) -> list[str]:
    """手のモデルのファイルのライセンスを、上流のカード(license_from)と照らす。読めて食い違えば、厳しいほうを
    使って警告を返す(書いた値がカードより厳しい段階なら、本文の制限を読んだものとしてそのまま。license が
    同じでカードに license_name が無ければ、書いた license_name は本文で細かくしたものとしてそのまま。#127)。
    カードを読めなければ(非公開・401・通信の失敗)書いた値のまま、確かめられなかった旨の警告。"""
    from .hf import license_tier, normalize_license, strictest
    from .update import set_entry_license

    warnings: list[str] = []
    for e in entries:
        if e.source != "manual":
            continue
        for f in e.files:
            if not f.license_from:
                continue
            info = client.repo(f.license_from)
            if info is None:
                warnings.append(
                    f"手のモデル {e.id}: {f.license_from} のカードを読めず、ライセンスを照合できません"
                )
                continue
            card = (normalize_license(info.license), info.license_name)
            mine = (f.license, f.license_name)
            if card[0] is None or card == mine:
                continue
            if card[0] == mine[0] and card[1] is None and mine[1]:
                # 書いた名前は、カードの同じ license を本文で細かくしたもの(Supertonic 3 の openrail →
                # LICENSE の本文が Open RAIL-M なので openrail-m。#127)。食い違いではない
                # (警告も書き換えもしない)
                continue
            if license_tier(*card) < license_tier(*mine):
                # 書いた人がカードの本文の制限(Irodori の Ethical Restrictions など)を読んで、cardData より
                # 厳しくしたもの。そのまま(警告しない)
                continue
            worse = strictest([card, mine])
            f.license, f.license_name = worse
            f.license_evidence.append(
                f"manual_mismatch: 書いた値 {mine} とカード {card} が違うので厳しいほうにした"
            )
            warnings.append(
                f"手のモデル {e.id}: {f.name} のライセンスがカードと違います(書いた値 {mine}、カード {card})"
            )
        set_entry_license(e)
    return warnings
