# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`measure` と `share` の材料(#113)。何を測ると役に立つか・入っているか・時間の目安・送っていない実測。
設定を変えた測定の提案(#116。予測式の段ごとの作業用メモリを確かめる材料: 解像度・コマ数・長さ)もここ。

ライブラリの関数は何も表示しない(表示は cli.py)。通信しない(ComfyUI を探すのは cli.py の comfyfind)。
"""

from __future__ import annotations

import contextlib
import json
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, cache_dir
from .i18n import tr
from .models import Measurement, ModelEntry, Settings, SystemInfo

# テンプレートのある(bench で測れる)モデルの出どころ
TEMPLATE_SOURCE = "comfyui-template"
# 提案に出す段階と動かし方(#113「GPU に載り(good 以上)」)
SUGGEST_LEVELS = ("perfect", "good")
SUGGEST_MODE = "gpu"
# 時間の目安(秒)。そのモデルの実測が無いときの種類ごとのおおよその値。読み込みを含む 1 回の生成の長さで、
# 同梱の実測(RTX 5080・3070 で 2〜56 秒)より長めにした。目安であって約束ではない
TYPICAL_SECONDS = {"image": 60, "video": 300, "music": 90, "sfx": 60, "tts": 60, "3d": 120}
DEFAULT_TYPICAL_SECONDS = 120
SUBMISSIONS_DIR = "submissions"  # キャッシュの中の、送る前の提出の置き場所(bench --share と同じ)
SHARED_FILE = "submissions_shared.json"  # 送った印(内容の sha256 → 日付)

# 設定を変えた測定(#116)。#102 の台本の組み合わせ(SDXL 512〜1536 px、Wan 2.1 1.3B 17〜81 コマ、
# ACE-Step 10〜120 秒)にならう。予測式の段ごとの作業用メモリ(画素数・コマ数・長さで変わる部分)を、
# 同じモデル・同じ GPU で設定だけ変えて確かめる材料にする。
# 画像は正方形の辺(px)、動画はコマ数(大きさはテンプレートのまま)、音声は長さ(秒)
VARIANT_PIXELS = (512, 768, 1024, 1536)
VARIANT_FRAMES = (17, 33, 49, 81)
VARIANT_SECONDS = (10, 30, 60, 120)
# 設定を入力で決める用途だけ(画像は文章から作るもの。入力の画像から大きさが決まるもの・道具は変えられない。
# 動画は文章・画像から作るもの。音楽・効果音は文章から作るもの)。テンプレートの中は見ない(通信しないため)
VARIANT_TASKS = {"image": ("t2i",), "video": ("t2v", "i2v"), "music": ("t2a",), "sfx": ("t2a",)}
# 引数なしの一覧で、同じモデル(groups の鍵)に出す設定を変えた測定の数の上限
# (一覧が 1 つのモデルで埋まらないように)
VARIANTS_PER_MODEL = 2
# measure・bench で変えられる設定の項目(--resolution・--frames・--length)。時間の目安の比と
# 「同じ設定の実測があるか」は、指定した項目だけで見る
SETTING_KEYS = ("width", "height", "frames", "seconds")
# 引数なしの一覧に出す時間の目安の上限(秒)。measure は 1 回 1〜5 分(#116 のオーナーの決定)
MAX_SUGGEST_SECONDS = 300
# 動画の設定を変えた測定で、時間の目安が種類ごとのおおよその値(実測が無い)のときに出すコマ数の上限
TYPICAL_VIDEO_FRAMES_MAX = 33


@dataclass
class Installed:
    """モデルのファイルが ComfyUI の models に入っているか。
    state は yes / no / unknown(置き場所が分からない)。"""

    state: str
    missing_bytes: int = 0
    total_bytes: int = 0
    missing_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def installed_state(entry: ModelEntry, roots: list[Path], refs: Any = ()) -> Installed:
    """roots(ComfyUI の models に当たるフォルダ)のどれかに、一覧のファイルが同じ大きさであるか。
    sha256 は計らない(大きさだけ。download が落とすときに計る)。
    roots が空・一覧にファイルが無いなら unknown。"""
    from .downloader import DIRECTORY_ALIASES, infer_directory, safe_relpath

    total = sum(f.size_bytes or 0 for f in entry.files)
    if not roots or not entry.files:  # 一覧にファイルが無いもの(カスタムノードが落とすなど)は分からない
        return Installed("unknown", total, total, len(entry.files))
    reverse = {v: k for k, v in DIRECTORY_ALIASES.items()}
    missing = missing_files = 0
    for f in entry.files:
        directory = f.directory or infer_directory(f, refs)[0]
        names = [directory, DIRECTORY_ALIASES.get(directory or ""), reverse.get(directory or "")]
        found = False
        for d in dict.fromkeys(n for n in names if n):
            rel = safe_relpath(d, f.name)
            if rel is None:
                continue
            for root in roots:
                p = root.joinpath(*rel)
                with contextlib.suppress(OSError):
                    if p.is_file() and (not f.size_bytes or p.stat().st_size == f.size_bytes):
                        found = True
                        break
            if found:
                break
        if not found:
            missing += f.size_bytes or 0
            missing_files += 1
    return Installed("no" if missing_files else "yes", missing, total, missing_files)


def settings_dict(settings: Settings | None) -> dict[str, Any] | None:
    """設定のうち指定した項目(SETTING_KEYS)だけ。何も無ければ None(テンプレートのまま)。"""
    if settings is None:
        return None
    d = {k: getattr(settings, k) for k in SETTING_KEYS if getattr(settings, k)}
    return d or None


def same_variant(m: Settings, want: Settings) -> bool:
    """実測の設定 m が、測りたい設定 want と同じか(want で指定した項目だけ比べる。
    動画の大きさのように指定しない項目はテンプレートのままなので比べない)。"""
    keys = [k for k in SETTING_KEYS if getattr(want, k)]
    return all(getattr(m, k) is not None and float(getattr(m, k)) == float(getattr(want, k)) for k in keys)


def _finished(m: Measurement, entry: ModelEntry) -> bool:
    """そのワークフローを、今の一覧のファイル・標準の条件で最後まで測った実測(ok か oom)か。"""
    from .estimate import files_match

    return (
        m.model_id == entry.id
        and (m.outcome or "ok") in ("ok", "oom")
        and m.standard is not False
        and files_match(m, entry)
    )


_BUNDLED_KEYS: set[str] = set()


def _row_key(m: Measurement) -> str:
    return json.dumps(asdict(m), sort_keys=True, default=str, ensure_ascii=False)


def measured_by_this_pc(measurements: list[Measurement]) -> list[Measurement]:
    """この PC の実測だけ(0.13.0。キャッシュの bench の実測・送る前の提出など)。同梱の実測
    (作者の measurements.json と、ほかの利用者のまとめ measurements_community.json)は除く: ほかの PC の
    実測があっても、この PC で測る意味は無くならない(提案から外さない)。時間の目安・見積もりには使う。"""
    from .db import load_measurements
    from .estimate import is_community

    if not _BUNDLED_KEYS:
        _BUNDLED_KEYS.update(_row_key(m) for m in load_measurements(include_cache=False))
    return [m for m in measurements if not is_community(m) and _row_key(m) not in _BUNDLED_KEYS]


def has_measurement_here(
    entry: ModelEntry, measurements: list[Measurement], gpu: Any, settings: Settings | None = None
) -> bool:
    """この GPU(estimate.same_gpu)で、今の一覧のファイル(files_match)・標準の条件で最後まで測った実測
    (ok か oom)があるか(作者・この PC・ほかの利用者)。作業用メモリだけの実測(estimate.working_only。
    今の NVIDIA の bench)も数える: 同じ GPU でもう一度測っても照合の材料が増えないため。
    settings が None なら、テンプレートの設定のままで測った実測(settings_override の無いもの。記録の無い
    古い行も含む)だけを数える(#116)。settings を渡すと、その設定(same_variant)の実測を、設定を変えたか
    どうかによらず数える。"""
    from .estimate import measured_on_this_gpu

    return any(
        _finished(m, entry)
        and measured_on_this_gpu(m, gpu)
        and (
            not getattr(m, "settings_override", None)
            if settings is None
            else same_variant(m.settings, settings)
        )
        for m in measurements
    )


def measured_here_count(db: Database, system: SystemInfo, *, extra: Any = ()) -> int:
    """この PC の実測(measured_by_this_pc と extra)で、この GPU にテンプレートの設定のまま最後まで測った
    ワークフロー(テンプレートのあるもの)の数(#146。share の「送るものはありません」の理由に出す)。"""
    gpu = system.best_gpu
    if gpu is None:
        return 0
    here = measured_by_this_pc(list(db.measurements)) + list(extra)
    ids: set[str] = set()
    for m in here:
        if m.model_id in ids:
            continue
        entry = db.get(m.model_id)
        if entry is not None and entry.source == TEMPLATE_SOURCE and has_measurement_here(entry, [m], gpu):
            ids.add(m.model_id)
    return len(ids)


def template_settings(entry: ModelEntry, measurements: list[Measurement]) -> Settings | None:
    """テンプレートの設定のまま測った実測(どの GPU でもよい)に記録された設定(bench が読んだ、その
    テンプレートの幅・高さ・コマ数・長さ)。日付の新しいものを使う。無ければ None(分からない)。"""
    rows = [m for m in measurements if _finished(m, entry) and not getattr(m, "settings_override", None)]
    if not rows:
        return None
    return max(rows, key=lambda m: m.date or "").settings


def variants(entry: ModelEntry, measurements: list[Measurement] | None = None) -> list[Settings]:
    """設定を変えた測定の候補(#116)。画像は VARIANT_PIXELS の正方形、動画は VARIANT_FRAMES のコマ数
    (大きさはテンプレートのまま)、音楽・効果音は VARIANT_SECONDS の長さ。設定を入力で決める用途
    (VARIANT_TASKS)だけで、大きさの入力の無いテンプレート(estimate.no_size_input)の画像は無し。
    measurements を渡すと、テンプレートのままの実測(template_settings)にその項目が無いもの(その入力が
    無い。コマ数を batch_size で決める AnimateDiff など)も出さない。分からないときは出し、measure が
    生成の前に確かめて止める(bench の strict_settings)。"""
    from .estimate import no_size_input

    tasks = VARIANT_TASKS.get(entry.modality, ())
    if not any(t in tasks for t in entry.tasks):
        return []
    known = template_settings(entry, measurements or [])
    if entry.modality == "image":
        if no_size_input(entry) or (known is not None and not known.width):
            return []
        return [Settings(width=px, height=px) for px in VARIANT_PIXELS]
    if entry.modality == "video":
        if known is not None and not known.frames:
            return []
        return [Settings(frames=n) for n in VARIANT_FRAMES]
    if known is not None and not known.seconds:
        return []
    return [Settings(seconds=float(sec)) for sec in VARIANT_SECONDS]


def judged_settings(entry: ModelEntry, measurements: list[Measurement], want: Settings) -> Settings:
    """設定を変えた測定を判定する設定(#116)。動画のコマ数だけを変えるときは、大きさをテンプレートの値
    (template_settings。分かるとき)にする。分からなければ種類ごとの既定(832x480)のまま。"""
    if entry.modality != "video" or want.width or want.height:
        return want
    known = template_settings(entry, measurements)
    if known is None or not (known.width and known.height):
        return want
    return Settings(width=known.width, height=known.height, frames=want.frames, seconds=want.seconds)


def bench_args(settings: Settings | None) -> list[str]:
    """設定を bench・measure の引数にしたもの(--resolution WxH / --frames N / --length S)。"""
    d = settings_dict(settings) or {}
    out: list[str] = []
    if d.get("width") and d.get("height"):
        out += ["--resolution", f"{d['width']}x{d['height']}"]
    if d.get("frames"):
        out += ["--frames", str(d["frames"])]
    if d.get("seconds"):
        out += ["--length", f"{d['seconds']:g}"]
    return out


def work_ratio(entry: ModelEntry, want: Settings | None, ref: Settings | None) -> float:
    """設定 want で測る量 ÷ 設定 ref で測った量(#116)。want で指定した項目だけの比の積
    (画像は画素数、動画はコマ数、音声は長さ)。ref に無い値は種類ごとの既定(estimate.settings_for)で埋める。
    ステップ数は want で変えない(テンプレートのまま)ので入れない。want が None なら 1。"""
    from .estimate import settings_for

    if want is None:
        return 1.0
    base = settings_for(entry, ref)
    ratio = 1.0
    for k in SETTING_KEYS:
        w, r = getattr(want, k), getattr(base, k)
        if w and r:
            ratio *= float(w) / float(r)
    return ratio


def time_hint(
    entry: ModelEntry, measurements: list[Measurement], settings: Settings | None = None
) -> tuple[int, str]:
    """測るのにかかる時間の目安(秒, 根拠)。根拠は measured(そのモデルのほかの GPU の実測の中央値)か
    typical(種類ごとのおおよその値 TYPICAL_SECONDS)。10 秒単位に切り上げる。モデルを落とす時間は入れない。
    settings(設定を変えた測定。#116)を渡すと、それぞれの実測の時間を設定の比(work_ratio)で伸び縮みさせて
    から中央値を取る(typical は種類ごとの既定の設定での値として伸び縮みさせる)。読み込みの時間も
    同じ比で動かすので、小さい設定では短めに出る(目安)。"""
    secs = [
        float(m.seconds) * work_ratio(entry, settings, m.settings)
        for m in measurements
        if m.model_id == entry.id and m.seconds and m.seconds > 0 and (m.outcome or "ok") == "ok"
    ]
    if secs:
        value, basis = statistics.median(secs), "measured"
    else:
        typical = float(TYPICAL_SECONDS.get(entry.modality, DEFAULT_TYPICAL_SECONDS))
        value, basis = typical * work_ratio(entry, settings, None), "typical"
    return max(10, int(-(-value // 10) * 10)), basis


@dataclass
class Suggestion:
    """測ると役に立つモデル 1 件(measure の引数なしの一覧)。"""

    model_id: str
    model_name: str
    name: str
    modality: str
    precision: str | None
    fit_level: str
    installed: Installed
    seconds: int
    seconds_basis: str
    group_key: str = ""
    fit: Any = field(default=None, repr=False, compare=False)
    settings: Settings | None = None  # 設定を変えた測定(#116)。None はテンプレートのまま

    @property
    def args(self) -> list[str]:
        """measure・bench に付ける設定の引数(テンプレートのままなら空)。"""
        return bench_args(self.settings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "name": self.name,
            "modality": self.modality,
            "precision": self.precision,
            "fit_level": self.fit_level,
            "installed": self.installed.state,
            "download_bytes": self.installed.missing_bytes,
            "total_bytes": self.installed.total_bytes,
            "seconds": self.seconds,
            "seconds_basis": self.seconds_basis,
            "settings": settings_dict(self.settings),
            "args": self.args,
        }


def _order(s: Suggestion) -> tuple:
    """並び: 入っているもの → 時間が短いもの → 落とす量が少ないもの → 名前。"""
    inst = s.installed
    return (
        inst.state != "yes",
        s.seconds,
        inst.missing_bytes,
        s.model_name.lower(),
        s.model_id,
        s.settings is not None,
        s.args,
    )


def suggestions(
    db: Database,
    system: SystemInfo,
    *,
    roots: list[Path] | None = None,
    content: str = "sfw",
    policy: Any = None,
    variants_per_model: int | None = VARIANTS_PER_MODEL,
    extra: Any = (),
    include_unknown_license: bool = False,
) -> list[Suggestion]:
    """測ると役に立つモデル(#113): テンプレートがあり、この PC の GPU に載り(perfect / good で GPU)、
    この GPU にテンプレートの設定のままの、この PC の実測(measured_by_this_pc と extra)が無いもの。
    同じモデル(groups の鍵)は 1 件(入っているもの、
    無ければ recommend と同じ選び方の一番良いワークフロー)。
    設定を変えた測定(#116。variants)も同じ一覧に混ぜる: この GPU にそのワークフローのテンプレートのままの
    実測があり(先にテンプレートのままを測ってもらう)、その設定(judged_settings)で見積もって GPU に載り、
    この GPU でその設定の実測が無いもの。同じモデル・同じ設定は 1 件(選び方は同じ)で、同じモデルの設定を
    変えた測定は、並べた順に variants_per_model 件まで(None なら全部)。
    時間の目安が MAX_SUGGEST_SECONDS を超えるものは出さない。動画の設定を変えた測定で、時間の目安が
    種類ごとのおおよその値のときは TYPICAL_VIDEO_FRAMES_MAX コマまで。
    並びは入っているもの → 時間 → 落とす量。GPU が無ければ空。"""
    from .downloader import directory_refs
    from .estimate import estimate, load_coefficients
    from .recommend import evaluate, variant_key

    gpu = system.best_gpu
    if gpu is None:
        return []
    roots = roots or []
    refs = directory_refs(db)
    c = load_coefficients()
    # extra: この PC に保存した、まだ一覧に取り込まれていない実測(送る前の提出など。#124)。
    # 「この GPU に実測があるか」だけに足す(測り直さない)
    ms = [*db.measurements, *extra]
    # 「測ってある」はこの PC の実測だけで決める(キャッシュ・送る前の提出)。同梱の作者の実測・ほかの利用者の
    # まとめは時間の目安・見積もりには使うが、提案からは外さない
    here = measured_by_this_pc(list(db.measurements)) + list(extra)
    per_key: dict[tuple, list[Suggestion]] = {}
    for f in evaluate(db, system, content=content, policy=policy, coefficients=c):
        m, e = f.model, f.estimate
        if m.source != TEMPLATE_SOURCE:
            continue
        # ライセンスの分からないものは、一覧の既定と同じく出さない(--any-license で出す。#124)
        if not include_unknown_license and m.license_unknown:
            continue
        rows: list[tuple[Settings | None, str]] = []
        if not has_measurement_here(m, here, gpu):
            if e.fit in SUGGEST_LEVELS and e.run_mode == SUGGEST_MODE:
                rows.append((None, e.fit))
        else:
            for v in variants(m, ms):
                ve = estimate(m, system, judged_settings(m, ms, v), ms, c)
                if (
                    ve.fit in SUGGEST_LEVELS
                    and ve.run_mode == SUGGEST_MODE
                    and not has_measurement_here(m, here, gpu, v)
                ):
                    rows.append((v, ve.fit))
        inst = installed_state(m, roots, refs) if rows else None
        for settings, level in rows:
            secs, basis = time_hint(m, ms, settings)
            if secs > MAX_SUGGEST_SECONDS:
                continue
            if (
                settings is not None
                and m.modality == "video"
                and basis == "typical"
                and (settings.frames or 0) > TYPICAL_VIDEO_FRAMES_MAX
            ):
                continue
            s = Suggestion(
                model_id=m.id,
                model_name=f.model_name or m.name,
                name=m.name,
                modality=m.modality,
                precision=m.precision,
                fit_level=level,
                installed=inst,
                seconds=secs,
                seconds_basis=basis,
                group_key=f.group_key or m.id,
                fit=f,
                settings=settings,
            )
            key = (s.group_key, tuple(s.args))
            per_key.setdefault(key, []).append(s)
    picked = sorted(
        (
            min(group, key=lambda s: (s.installed.state != "yes", variant_key(s.fit), s.model_id))
            for group in per_key.values()
        ),
        key=_order,
    )
    out: list[Suggestion] = []
    count: dict[str, int] = {}
    for s in picked:
        if s.settings is not None and variants_per_model is not None:
            if count.get(s.group_key, 0) >= variants_per_model:
                continue
            count[s.group_key] = count.get(s.group_key, 0) + 1
        out.append(s)
    return out


# share(#124)が 1 回で測る時間の合計の上限(秒。時間の目安 time_hint の合計)。オーナーの決定(2026-10-29)
SHARE_BUDGET_SECONDS = 300


def share_plan(
    sugg: list[Suggestion], budget: float, *, state: str = "yes", skip: Any = None
) -> tuple[list[Suggestion], int]:
    """share が測るもの(#124): 提案(suggestions の並び)のうち、入っているか(installed.state が state)の
    ものを、時間の目安の合計が budget 以内になるように並びの順に取る(入りきらないものは飛ばして次を見る)。
    (取ったもの, 目安の合計)。state="no" で、落として測るものの計画になる。
    skip(提案 → 真なら外す)は、時間に収まる候補にだけ、並びの順に呼ぶ(ComfyUI のノードの確かめ。#124)。"""
    picked: list[Suggestion] = []
    total = 0
    for s in sugg:
        if s.installed is None or s.installed.state != state:
            continue
        if total + s.seconds > budget:
            continue
        if skip is not None and skip(s):
            continue
        picked.append(s)
        total += s.seconds
    return picked, total


def resolve(db: Database, system: SystemInfo, query: str, *, content: str = "sfw", policy: Any = None):
    """measure <名前か ID> → 測るワークフローの Fit。download と同じ探し方(downloader.resolve_candidates)で、
    テンプレートのある仲間だけから、ID がそのまま合えばそれ、名前なら recommend と同じ選び方の一番良いもの。
    合うものが無ければ downloader.ModelNotFound。"""
    from .downloader import ModelNotFound, resolve_candidates
    from .estimate import load_coefficients
    from .recommend import judge, variant_key

    cands, exact = resolve_candidates(db, query, content)
    usable = [m for m in cands if m.source == TEMPLATE_SOURCE]
    if not usable:
        raise ModelNotFound(
            tr(
                f"{cands[0].id} には ComfyUI のテンプレートが無いので、measure では測れません"
                "(Hugging Face のモデルは genmodelfit bench --workflow で自分のワークフローを渡して測る)",
                f"{cands[0].id} has no ComfyUI template, so measure cannot measure it"
                " (measure a Hugging Face model with genmodelfit bench --workflow and your own workflow)",
            )
        )
    c = load_coefficients()
    fits = [judge(m, system, db, coefficients=c, policy=policy) for m in usable]
    if exact:
        return fits[0]
    return min(fits, key=lambda f: (variant_key(f), f.model.id))


# --- 送る前の提出と、送った印 ---------------------------------------------------------------------


def submissions_dir() -> Path:
    return cache_dir() / SUBMISSIONS_DIR


def shared_path() -> Path:
    return cache_dir() / SHARED_FILE


def load_shared() -> dict[str, dict[str, Any]]:
    """送った印(内容の sha256 → {date, model_id})。読めなければ空。"""
    try:
        data = json.loads(shared_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    shared = data.get("shared") if isinstance(data, dict) else None
    return {k: v for k, v in shared.items() if isinstance(v, dict)} if isinstance(shared, dict) else {}


def mark_shared(data: dict[str, Any]) -> str:
    """提出に送った印を付ける(手元のファイルだけ。何も送らない)。内容の sha256 を返す。"""
    from .contrib import content_hash

    sha = content_hash(data)
    marks = load_shared()
    mm = data.get("measurement") if isinstance(data.get("measurement"), dict) else {}
    marks[sha] = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "model_id": mm.get("model_id"),
    }
    p = shared_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"shared": marks}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return sha


@dataclass
class SavedSubmission:
    """キャッシュの submissions にある提出 1 件。"""

    path: Path
    data: dict[str, Any]
    sha256: str
    shared: bool

    def to_dict(self) -> dict[str, Any]:
        mm = self.data.get("measurement") or {}
        gpu = (self.data.get("system") or {}).get("gpu") or {}
        if isinstance(self.data.get("failures"), list):  # 失敗の記録(#132)
            return {
                "file": str(self.path),
                "sha256": self.sha256,
                "shared": self.shared,
                "kind": "failures",
                "date": self.data.get("date"),
                "gpu": gpu.get("name"),
                "count": len(self.data["failures"]),
                "outcomes": sorted(
                    {str(f.get("outcome")) for f in self.data["failures"] if isinstance(f, dict)}
                ),
            }
        return {
            "file": str(self.path),
            "sha256": self.sha256,
            "shared": self.shared,
            "date": self.data.get("date"),
            "model_id": mm.get("model_id"),
            "gpu": gpu.get("name"),
            "outcome": mm.get("outcome"),
            "peak_vram_gb": mm.get("peak_vram_gb"),
            "seconds": mm.get("seconds"),
        }


def saved_submissions(root: Path | None = None, *, include_shared: bool = False) -> list[SavedSubmission]:
    """送る前の提出(<キャッシュ>/submissions/<GPU>/<日付>-<sha8>.json と、失敗の記録の
    <日付>-<sha8>-failures.json。#132)。新しいものから。読めないものは飛ばす。
    include_shared が偽なら送った印のあるものを除く。"""
    from .contrib import content_hash, find_files, read_submission

    root = root or submissions_dir()
    marks = load_shared()
    out: list[tuple[tuple, SavedSubmission]] = []
    for p in find_files(root):
        try:
            data, _raw = read_submission(p)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not (
            isinstance(data.get("measurement"), dict) or isinstance(data.get("failures"), list)
        ):
            continue
        sha = content_hash(data)
        s = SavedSubmission(p, data, sha, sha in marks)
        if s.shared and not include_shared:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append(((str(data.get("date") or ""), mtime, p.as_posix()), s))
    return [s for _, s in sorted(out, key=lambda x: x[0], reverse=True)]


def failure_key(f: dict[str, Any], comfyui_version: Any) -> str:
    """失敗の記録 1 件の鍵(#132): 同じモデル・設定・失敗・足りないノード・エラーの種類・ComfyUI の版なら同じ。
    経過時間・最大 VRAM は入れない(同じ失敗を毎回送らないため)。"""
    if f.get("outcome") == "missing_nodes":
        # 足りないノードは設定・ComfyUI の版によらず同じこと(measure と share で二重に送らない)
        nodes = sorted(str(n) for n in f.get("missing_nodes") or [])
        return json.dumps({"m": f.get("model_id"), "n": nodes, "o": f.get("other_nodes")}, ensure_ascii=False)
    keep = ("model_id", "settings", "outcome", "missing_nodes", "error_type", "node_type")
    return json.dumps(
        {"v": comfyui_version, **{k: f.get(k) for k in keep}}, sort_keys=True, ensure_ascii=False
    )


def saved_failure_keys(root: Path | None = None) -> set[str]:
    """この PC に保存した失敗の記録(送ったものも、まだのものも)の鍵の集まり(#132)。"""
    out: set[str] = set()
    for sub in saved_submissions(root, include_shared=True):
        items = sub.data.get("failures")
        if isinstance(items, list):
            out.update(failure_key(f, sub.data.get("comfyui_version")) for f in items if isinstance(f, dict))
    return out
