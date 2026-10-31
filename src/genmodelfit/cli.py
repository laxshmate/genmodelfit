# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""コマンドライン。`--json` を付けると、どのコマンドも決まった形の JSON を出す(docs/schema.md)。

終了コード: 0 成功 / 1 失敗 / 2 使い方の誤り。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import i18n
from ._version import __version__
from .db import Database, DatabaseError
from .db import load as load_db
from .estimate import GIB, estimate, load_coefficients
from .hardware import detect
from .i18n import LANGUAGES, pick, tr
from .models import FIT_LEVELS, MODALITIES, Settings, SystemInfo
from .recommend import CONTENT_FILTERS, DEFAULT_CONTENT, FIT_FILTERS, SORTS, select


class CliError(RuntimeError):
    pass


class UsageError(CliError):
    """使い方の誤り(フラグの組み合わせなど)。終了コード 2(argparse と同じ)。"""


def _positive_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            tr(f"整数を指定してください: {text}", f"must be an integer: {text}")
        ) from None
    if v <= 0:
        raise argparse.ArgumentTypeError(
            tr(f"1 以上を指定してください: {text}", f"must be 1 or more: {text}")
        )
    return v


def _positive_float(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            tr(f"数を指定してください: {text}", f"must be a number: {text}")
        ) from None
    if not v > 0 or v == float("inf"):
        raise argparse.ArgumentTypeError(
            tr(f"0 より大きい値を指定してください: {text}", f"must be greater than 0: {text}")
        )
    return v


def _non_negative_float(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            tr(f"数を指定してください: {text}", f"must be a number: {text}")
        ) from None
    if not v >= 0 or v == float("inf"):
        raise argparse.ArgumentTypeError(
            tr(f"0 以上を指定してください: {text}", f"must be 0 or more: {text}")
        )
    return v


def _out(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _system(args: argparse.Namespace) -> SystemInfo:
    return detect(
        disk_path=getattr(args, "disk_path", None),
        vram_gb=args.vram,
        ram_gb=args.ram,
        other_gpus=True if getattr(args, "all_gpus", False) else None,
    )


def _wrapped(text: str, first: str = "  - ", rest: str = "    ", width: int = 100) -> list[str]:
    """全角を 2 桁で数えて width 桁に折り返す(system の注意の文は長い日本語になる)。
    英数字の並びの途中では切らない(table.wrap_text。#76)。"""
    from .table import wrap_text

    return wrap_text(text, width, first, rest)


def _swap_line(s: SystemInfo) -> str | None:
    """仮想メモリの 1 行(Windows はページファイルとコミット、Linux はスワップ)。分からなければ None。"""
    if s.swap_auto is None and s.swap_total_gb is None and s.commit_limit_gb is None:
        return None
    windows = s.commit_limit_gb is not None or s.os.startswith("Windows")
    label = tr("ページファイル", "Pagefile") if windows else tr("スワップ", "Swap")
    if s.swap_max_gb == 0 and s.swap_auto is False:
        text = tr("なし", "none")
    elif s.swap_auto:
        now = (
            tr(f"今 {s.swap_total_gb:.1f} GB、", f"{s.swap_total_gb:.1f} GB now, ")
            if s.swap_total_gb is not None
            else ""
        )
        text = now + tr("OS が管理(増える)", "system managed (grows)")
    elif s.swap_max_gb is not None:
        text = tr(f"最大 {s.swap_max_gb:.1f} GB", f"up to {s.swap_max_gb:.1f} GB")
    elif s.swap_total_gb is not None:
        text = tr(f"今 {s.swap_total_gb:.1f} GB", f"{s.swap_total_gb:.1f} GB now")
    else:
        text = tr("不明", "unknown")
    if s.commit_limit_gb is not None and s.commit_used_gb is not None:
        text += tr(
            f"。コミット {s.commit_used_gb:.1f} / {s.commit_limit_gb:.1f} GB 使用中",
            f"; commit {s.commit_used_gb:.1f} / {s.commit_limit_gb:.1f} GB in use",
        )
    return f"{label}: {text}"


def _db(args: argparse.Namespace) -> Database:
    return load_db(args.db, use_cache=not args.no_cache)


def _settings(args: argparse.Namespace) -> Settings | None:
    s = Settings(
        width=args.width,
        height=args.height,
        frames=args.frames,
        seconds=args.length,
        precision=getattr(args, "precision", None),
    )
    return s if any(v is not None for v in (s.width, s.height, s.frames, s.seconds, s.precision)) else None


def _envelope(command: str, system: SystemInfo | None, db: Database | None, **payload: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"genmodelfit": __version__, "command": command}
    if system is not None:
        d["system"] = system.to_dict()
        # この PC の GPU の種類が同梱の実測で確かめてあるか(#108。docs/schema.md「system」)。gate には使わない
        from .estimate import platform_verification

        pv = platform_verification(system)
        d["system"]["platform_verified"] = pv["verified"]
        d["system"]["platform_verified_by"] = (
            {"kind": pv["kind"], "models": pv["models"]} if pv["verified"] else None
        )
    if db is not None:
        d["database"] = {
            "origin": db.origin,
            "generated_at": db.generated_at,
            "models": len(db.models),
            "measurements": len(db.measurements),
        }
    d.update(payload)
    return d


def format_limits(lim: dict) -> str:
    """「この PC の GPU なら(逃がさずに)長辺 640px・133 コマまで good」の形にする。限界は逃がさずに GPU で
    good になる設定だけ(#108。逃がす・CPU の行の段階とは別)。"""
    caps = set(lim.get("at_cap") or [])
    keys = [k for k in ("max_side_px", "max_frames", "max_seconds") if k in lim]
    if keys and all(lim[k] is None for k in keys):
        return tr(
            "逃がさずに GPU で Good になる設定は無い(解像度や長さを下げても)",
            "no setting is Good on the GPU without offloading (even at a lower resolution or length)",
        )
    ja = i18n.get_language() == "ja"
    parts = []
    for key, label, unit, label_en, unit_en in (
        ("max_side_px", "長辺 ", "px", "long side ", " px"),
        ("max_frames", "", " コマ", "", " frames"),
        ("max_seconds", "", " 秒", "", " s"),
    ):
        if key not in lim:
            continue
        v = lim[key]
        if not ja:
            name = (label_en or unit_en).strip()
            if v is None:
                parts.append(f"{name}: not Good on the GPU without offloading even when lowered")
            else:
                parts.append(f"{label_en}{v}{unit_en}{' (the highest checked)' if key in caps else ''}")
        elif v is None:
            parts.append(f"{label or unit.strip() + ' '}は下げても逃がさずに GPU で good にならない")
        else:
            parts.append(f"{label}{v}{unit}{'(調べた上限)' if key in caps else ''}")
    if not ja:
        return (
            "Good on this PC's GPU without offloading up to "
            + ", ".join(parts)
            + " (other settings unchanged)"
        )
    return "この PC なら逃がさずに GPU で " + "・".join(parts) + "まで good(ほかの設定はそのまま)"


# --- コマンド ---------------------------------------------------------------------------------


def _data_status(args: argparse.Namespace, db: Database, source: str | None = None):
    from .status import compute

    return compute(db, _policy(args), source=source)


def format_data_status(st) -> list[str]:
    """`system` と `update` の Data の欄(人が読む形)。"""
    if i18n.get_language() == "ja":
        return _format_data_status_ja(st)
    lines = [f"List: {st.source}, generated {st.generated_at or '?'}, {st.models} entries"]
    if st.max_head_age_days is None:
        lines.append(f"HEAD checks: {st.heads_checked_oldest or 'none'} (no age limit)")
    elif st.expired:
        lines.append(
            f"HEAD checks: EXPIRED ({st.heads_checked_oldest or 'none'}, limit {st.max_head_age_days} days)"
            " -> run `genmodelfit update --from-release` (or `update --heads-only`)"
        )
    else:
        lines.append(
            f"HEAD checks: {st.heads_checked_oldest or 'none'}, valid until {st.expires_at or '-'}"
            f" ({st.max_head_age_days} days)"
        )
    lines.append(f"Auto-recommendable: {st.auto_count} of {st.models} (if fresh: {st.auto_count_if_fresh})")
    return lines


# 一覧の出どころ(status.source)の日本語(#76)
_SOURCE_JA = {"bundled": "同梱", "cache": "update で取った一覧", "path": "指定のファイル", "release": "配布"}


def _format_data_status_ja(st) -> list[str]:
    """format_data_status の日本語(#76)。項目は英語と同じ。"""
    src = _SOURCE_JA.get(st.source, st.source)
    lines = [f"一覧: {src}、生成 {st.generated_at or '?'}、{st.models} 件"]
    oldest = st.heads_checked_oldest or tr("なし", "none")
    if st.max_head_age_days is None:
        lines.append(f"HEAD の確認: {oldest}(期限なし)")
    elif st.expired:
        lines.append(
            f"HEAD の確認: 期限切れ({oldest}、期限 {st.max_head_age_days} 日)"
            " → `genmodelfit update --from-release`(か `update --heads-only`)を実行してください"
        )
    else:
        lines.append(f"HEAD の確認: {oldest}、{st.expires_at or '-'} まで有効({st.max_head_age_days} 日)")
    lines.append(
        f"自動のおすすめ: {st.models} 件中 {st.auto_count} 件(確認が新しければ {st.auto_count_if_fresh} 件)"
    )
    return lines


def _print_status_after_update(st) -> None:
    for line in format_data_status(st)[1:]:
        print("  " + line)


def cmd_system(args: argparse.Namespace) -> int:
    s = _system(args)
    db = _db(args)
    st = _data_status(args, db)
    if args.json:
        env = _envelope("system", s, db)
        env["database"]["status"] = st.to_dict()
        _out(env)
        return 0
    print(tr("=== この PC ===", "=== System ==="))
    print(tr(f"OS:       {s.os}", f"OS:   {s.os}"))
    print(tr(f"CPU:      {s.cpu}({s.cpu_cores} スレッド)", f"CPU:  {s.cpu} ({s.cpu_cores} threads)"))
    print(
        tr(
            f"メモリ:   {s.ram_total_gb:.1f} GB(空き {s.ram_available_gb:.1f} GB)",
            f"RAM:  {s.ram_total_gb:.1f} GB (available {s.ram_available_gb:.1f} GB)",
        )
    )
    swap = _swap_line(s)
    if swap:
        print(swap)
    if s.disk_free_gb is not None:
        print(tr(f"ディスク: 空き {s.disk_free_gb:.1f} GB", f"Disk: {s.disk_free_gb:.1f} GB free"))
    if not s.gpus:
        print(tr("GPU:      見つからない(CPU だけ)", "GPU:  none detected (CPU only)"))
    elif s.best_gpu is None:
        print(
            tr(
                "GPU:      使える GPU が無い(CPU だけとして判定。注意を見てください)",
                "GPU:  no usable GPU (judged as CPU only; see notes)",
            )
        )
    for i, g in enumerate(s.gpus, 1):
        mem = []
        if g.vram_free_gb is not None:
            mem.append(tr(f"空き {g.vram_free_gb:.1f} GB", f"{g.vram_free_gb:.1f} GB free"))
        if g.vram_used_gb is not None:
            mem.append(
                tr(
                    f"ほかのアプリが {g.vram_used_gb:.1f} GB 使用中",
                    f"{g.vram_used_gb:.1f} GB used by other apps",
                )
            )
        extra = "".join(tr(f"、{m}", f", {m}") for m in mem)
        print(
            tr(
                f"GPU {i}: {g.name}(VRAM {g.vram_total_gb:.1f} GB{extra})",
                f"GPU {i}: {g.name} ({g.vram_total_gb:.1f} GB VRAM{extra})",
            )
        )
        cc = (
            f"compute capability {g.compute_capability}"
            if g.compute_capability
            else tr("compute capability 不明", "compute capability unknown")
        )
        if g.compute_capability_source == "name":
            cc += tr("(名前から推定)", " (guessed from the name)")
        arch = tr(f"({g.arch})", f" ({g.arch})") if g.arch else ""
        if g is s.best_gpu:
            use = tr("判定に使う", "used for judging")
        elif g.backend == "none":
            use = tr("使わない", "not used")
        else:
            use = tr("一番大きい GPU ではない", "not the largest")
        if g.vendor != "nvidia":  # AMD・Intel・Apple(0.7.0、#22。対応表で決めた推定)
            gfx = tr(f"{g.gfx_target}、", f"{g.gfx_target}, ") if g.gfx_target else ""
            kind = {
                "shared": tr("内蔵・メモリと共有、", "integrated, shared with RAM, "),
                "unified": tr("ユニファイドメモリ、", "unified memory, "),
            }.get(g.memory, "")
            print(
                tr(
                    f"       {gfx}{kind}{g.backend}(推定。実測なし): {use}",
                    f"       {gfx}{kind}{g.backend} (estimated, no measurements): {use}",
                )
            )
            continue
        print(
            tr(
                f"       {cc}{arch}、ドライバ {g.driver or '不明'}、{g.backend}: {use}",
                f"       {cc}{arch}, driver {g.driver or 'unknown'}, {g.backend}: {use}",
            )
        )
        if g.torch_cuda:
            print(
                tr(
                    f"       この PC で動く PyTorch の CUDA 版: {', '.join(g.torch_cuda)}",
                    f"       PyTorch CUDA builds that run here: {', '.join(g.torch_cuda)}",
                )
            )
        if g.compute_dtype:
            if i18n.get_language() == "ja":
                yes = {True: "対応", False: "非対応", None: "不明"}
                print(f"       ComfyUI の精度: {g.compute_dtype}(bf16 {yes[g.bf16]}、fp8 {yes[g.fp8]})")
            else:
                yes = {True: "yes", False: "no", None: "unknown"}
                print(f"       ComfyUI precision: {g.compute_dtype} (bf16 {yes[g.bf16]}, fp8 {yes[g.fp8]})")
    if s.other_gpus_checked:
        print(
            tr(
                "画面の部品(AMD / Intel / Apple は、使える NVIDIA の GPU が無いときだけ使う):",
                "Display adapters (AMD / Intel / Apple are used only when no NVIDIA GPU is usable):",
            )
        )
        if not s.other_gpus:
            print(tr("  (見つからない)", "  (none found)"))
        for a in s.other_gpus:
            vram = f", {a.vram_gb:.1f} GB ({a.vram_source})" if a.vram_gb else ""
            kind = tr("、内蔵", ", integrated") if a.integrated else ""
            state = {
                True: tr(": 使える", ": usable"),
                False: tr(": 使えない → CPU として判定", ": not usable -> judged as CPU"),
            }.get(a.usable, "")
            print("\n".join(_wrapped(f"{a.name} [{a.vendor}{vram}{kind}]{state}")))
    from .estimate import platform_verification

    pv = platform_verification(s)
    if pv["verified"]:  # 確認済みの種類だけ印を出す(#108。docs/scoring.md「確認済みの GPU の種類」)
        print(
            tr(
                f"✓ 確認済みの種類({pv['kind']}。動いた機種: {'、'.join(pv['models'])})",
                f"✓ verified GPU kind ({pv['kind']}; ran on: {', '.join(pv['models'])})",
            )
        )
    if s.overrides:
        print(tr(f"(上書き: {', '.join(s.overrides)})", f"(overridden: {', '.join(s.overrides)})"))
    if s.notes:
        print(tr("注意:", "Notes:"))
        for n in s.notes:
            print("\n".join(_wrapped(n)))
    print(tr("=== 一覧 ===", "=== Data ==="))
    for line in format_data_status(st):
        print(line)
    return 0


def _license_list(text: str) -> list[str]:
    items = [x.strip().lower() for x in text.split(",") if x.strip()]
    if not items:
        raise argparse.ArgumentTypeError(
            tr(
                "ライセンスを 1 つ以上、カンマ区切りで指定してください(例: apache-2.0,mit)",
                "give one or more licenses, comma-separated (e.g. apache-2.0,mit)",
            )
        )
    return items


def _modality_list(text: str) -> list[str]:
    from .tasks import parse_modalities

    try:
        return list(parse_modalities(text) or ())
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _task_list(text: str) -> list[str]:
    from .tasks import task_codes

    items = [x.strip().lower() for x in text.split(",") if x.strip()]
    try:
        task_codes(items)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return items


def _select(args: argparse.Namespace, s: SystemInfo, db: Database, **kw: Any):
    return select(
        db,
        s,
        modality=args.modality,
        content=args.content,
        license=args.license,
        task=args.task,
        include_unknown_license=args.include_unknown_license or getattr(args, "all", False),
        group=not args.all_workflows,
        settings=_settings(args),
        with_limits=args.limits,
        policy=_policy(args),
        **kw,
    )


def _filters(args: argparse.Namespace, min_fit: str | None, limit: int | None) -> dict[str, Any]:
    return {
        "modality": args.modality,
        "content": args.content,
        "min_fit": min_fit,
        "fit": args.fit,
        "license": args.license,
        "include_unknown_license": args.include_unknown_license or getattr(args, "all", False),
        "task": args.task,
        "all_workflows": args.all_workflows,
        "limit": limit,
    }


def _show(
    args, command: str, title: str, s: SystemInfo, db: Database, sel, filters: dict[str, Any], sections=False
) -> int:
    if args.json:
        _out(
            _envelope(
                command,
                s,
                db,
                filters=filters,
                matched=sel.matched,
                hidden=sel.hidden,
                groups=sel.groups,
                models=[f.to_dict() for f in sel.fits],
            )
        )
        return 0
    from .table import print_fit_table

    print_fit_table(
        sel.fits,
        title=title,
        system=s,
        matched=sel.matched,
        hidden=sel.hidden,
        groups=sel.groups if sections else None,
        grouped=not args.all_workflows,
        order=getattr(args, "sort", None) or "auto",
        content=getattr(args, "content", None),
        # --auto-only は表も JSON も auto だけなので、並びの違いを知らせない(#84)
        json_first=command == "recommend" and not getattr(args, "auto_only", False),
    )
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    """-m も --task も無ければ種類ごとの節(各 -n 件、既定 3)。あれば 1 つの表(既定 10 件)。"""
    s, db = _system(args), _db(args)
    sections = not args.modality and not args.task
    per_type = (args.limit or 3) if sections else None
    limit = None if sections else (args.limit if args.limit is not None else 10)
    if args.fit is not None and args.min_fit is not None:
        raise CliError(
            tr("--fit と --min-fit は同時に指定できません", "--fit and --min-fit cannot be used together")
        )
    min_fit = None if args.fit is not None else (args.min_fit or "marginal")
    sel = _select(
        args,
        s,
        db,
        min_fit=min_fit,
        fit=args.fit,
        include_tools=args.include_tools,
        auto_only=args.auto_only,
        drop_unknown_size=True,
        limit=limit,
        per_modality=per_type,
        # 表は自動のおすすめ(Verified)を先にしない(#84)。--json は今までの並び(verified が先)のまま
        verified_first=bool(args.json),
    )
    filters = _filters(args, min_fit, limit) | {
        "per_type": per_type,
        "auto_only": args.auto_only,
        "include_tools": args.include_tools,
    }
    return _show(args, "recommend", tr("おすすめ", "Recommended"), s, db, sel, filters, sections=sections)


def _fit_choice(args: argparse.Namespace, default: str | None) -> tuple[str | None, str | None]:
    """(min_fit, fit)。--perfect は --fit perfect。--all は --fit all。どれも無ければ default。"""
    if args.perfect:
        args.fit = "perfect"
    if getattr(args, "all", False) and args.fit is None and args.min_fit is None:
        args.fit = "all"
    if args.fit is not None and args.min_fit is not None:
        raise CliError(
            tr("--fit と --min-fit は同時に指定できません", "--fit and --min-fit cannot be used together")
        )
    if args.fit is None and args.min_fit is None:
        args.fit = default
    return args.min_fit, args.fit


def cmd_fit(args: argparse.Namespace) -> int:
    """既定は動くもの(perfect / good / marginal)だけ、補助ツール・ライセンス不明・無検閲は隠す。--all で全部。
    並びは種類 → 段階 → 点数(--sort)。自動のおすすめを先にはしない(自動のおすすめかは info で分かる)。"""
    s, db = _system(args), _db(args)
    min_fit, fit = _fit_choice(args, "runnable")
    tools = args.include_tools or args.all
    sel = _select(
        args,
        s,
        db,
        min_fit=min_fit,
        fit=fit,
        limit=args.limit,
        include_tools=tools,
        sort=args.sort,
        auto_first=False,
    )
    filters = _filters(args, min_fit, args.limit) | {
        "sort": args.sort,
        "include_tools": tools,
        "all": args.all,
    }
    return _show(args, "fit", tr("全モデル", "All models"), s, db, sel, filters)


def cmd_search(args: argparse.Namespace) -> int:
    """ID・名前の一部で探す(大文字小文字を区別しない)。0 件でも終了コード 0。空の語は 1。"""
    s, db = _system(args), _db(args)
    query = args.query.strip()
    if not query:
        raise CliError(tr("探す語が空です", "the search word is empty"))
    min_fit, fit = _fit_choice(args, None)
    sel = _select(
        args, s, db, query=query, min_fit=min_fit, fit=fit, limit=args.limit, sort=args.sort, auto_first=False
    )
    filters = {"query": query} | _filters(args, min_fit, args.limit) | {"sort": args.sort, "all": args.all}
    return _show(args, "search", tr(f"「{query}」の検索", f'Search "{query}"'), s, db, sel, filters)


def types_summary(db: Database, content: str = DEFAULT_CONTENT) -> list[dict[str, Any]]:
    """大きな種類ごとの、モデルの数・ワークフローの数・用途ごとのワークフローの数。"""
    from .groups import group_key
    from .tasks import MODALITY_LABEL, USES, uses_of

    out = []
    for mod in MODALITIES:
        entries = [m for m in db.models if m.modality == mod and (content == "all" or m.content == content)]
        uses = {u: sum(1 for m in entries if u in uses_of(m)) for u in USES}
        en, ja = MODALITY_LABEL[mod]
        out.append(
            {
                "modality": mod,
                "label": en,
                "label_ja": ja,
                "models": len({group_key(m) for m in entries}),
                "workflows": len(entries),
                "uses": {u: n for u, n in uses.items() if n},
            }
        )
    return out


def cmd_types(args: argparse.Namespace) -> int:
    from .tasks import USE_ALIASES, USES

    db = _db(args)
    rows = types_summary(db, args.content)
    uses = [
        {"use": u, "label": en, "label_ja": ja, "tasks": list(codes)} for u, (en, ja, codes) in USES.items()
    ]
    if args.json:
        _out(
            _envelope(
                "types",
                None,
                db,
                filters={"content": args.content},
                types=rows,
                uses=uses,
                aliases=USE_ALIASES,
            )
        )
        return 0
    lines = [
        tr(
            f"種類(内容 {args.content}。Models = 重みの数、Workflows = 一覧の項目の数)",
            f"Types (content {args.content}; models = distinct weights, workflows = list entries)",
        )
    ]
    lines.append(f"{'Type':<6}{'Models':>7}{'Workflows':>10}  Uses (workflows)")
    for r in rows:
        used = ", ".join(f"{u} {n}" for u, n in r["uses"].items())
        lines.append(f"{r['modality']:<6}{r['models']:>7}{r['workflows']:>10}  {used}")
    lines.append("")
    lines.append(
        tr(
            "--task に使う用途(下の名前か、t2i / i2i / inpaint などの用途のコード):",
            "Uses for --task (a name below, or a task code such as t2i / i2i / inpaint):",
        )
    )
    for u in uses:
        label = tr(u["label_ja"], u["label"])  # 1 行に 2 つの言語を並べない(#76)
        lines.append(f"  {u['use']:<8}{label}  [{', '.join(u['tasks'])}]")
    lines.append(
        tr("例: ", "Examples: ") + "genmodelfit recommend -m image --task edit / -m video --task i2v,lipsync"
    )
    _emit(lines)
    return 0


class AmbiguousModel(CliError):
    def __init__(self, query: str, candidates: list) -> None:
        super().__init__(
            tr(
                f"「{query}」に合うモデルが {len(candidates)} 件あります。ID で指定してください",
                f'{len(candidates)} models match "{query}". Specify one by its id',
            )
        )
        self.candidates = candidates


def resolve_model(db: Database, query: str, content: str = DEFAULT_CONTENT):
    """ID がそのまま合えば(内容によらず)そのモデル。合わなければ ID・名前の一部で探す。

    一部で探すときは content で絞る(既定 sfw なので無検閲は出ない)。1 件だけならそれ、
    2 件以上なら AmbiguousModel(先頭を黙って選ばない)、0 件なら CliError。
    """
    query = query.strip()
    exact = db.get(query)
    if exact is not None:
        return exact
    q = query.lower()
    hits = [m for m in db.models if q in m.id.lower() or q in m.name.lower()]
    shown = [m for m in hits if content == "all" or m.content == content]
    if len(shown) == 1:
        return shown[0]
    if shown:
        raise AmbiguousModel(query, shown)
    if hits:
        kinds = "・".join(sorted({m.content for m in hits}))
        kinds_en = ", ".join(sorted({m.content for m in hits}))
        raise CliError(
            tr(
                f"モデルが見つかりません: {query}(--content {content} で外れた {kinds} のモデルが "
                f"{len(hits)} 件あります。--uncensored を付けるか ID で指定してください)",
                f"no such model: {query} ({len(hits)} {kinds_en} model(s) are hidden by --content {content};"
                " add --uncensored or specify the id)",
            )
        )
    raise CliError(tr(f"モデルが見つかりません: {query}", f"no such model: {query}"))


def _emit(lines: list[str]) -> None:
    """info の文を幅(COLUMNS か端末、最大 100 桁)に収めて出す。"""
    from .table import output_width, wrap_line

    width = output_width()
    for line in lines:
        for part in wrap_line(line, width):
            print(part)


def _by_model_name(db: Database, s: SystemInfo, args: argparse.Namespace, query: str):
    """モデルの表示名(大文字小文字を区別しない)に合えば、そのモデルの中でこの PC に一番合うワークフロー。"""
    from .groups import index_for
    from .recommend import judge, variant_key

    if db.get(query.strip()) is not None:
        return None  # ID がそのまま合うなら ID を優先
    idx = index_for(db)
    key = idx.find(query)
    if key is None:
        return None
    c = load_coefficients()
    fits = [
        judge(m, s, db, settings=_settings(args), coefficients=c, policy=_policy(args))
        for m in idx.members[key]
    ]
    return min(fits, key=variant_key).model


def cmd_info(args: argparse.Namespace) -> int:
    s, db = _system(args), _db(args)
    m = _by_model_name(db, s, args, args.model) or resolve_model(db, args.model, args.content)
    c = load_coefficients()
    e = estimate(m, s, _settings(args), db.measurements, c, with_limits=True)
    gate = _gate_for(m, args, db)
    key, same = same_model(db, s, m, args)
    from .groups import index_for, is_community

    if args.json:
        _out(
            _envelope(
                "info",
                s,
                db,
                model=m.to_dict(),
                estimate=e.to_dict(),
                gate=gate,
                measurements=[vars(x) | {"settings": vars(x.settings)} for x in db.measurements_for(m.id)],
                group_key=key,
                model_name=index_for(db).name_of.get(key),
                community=is_community(m),
                same_model=same,
            )
        )
        return 0
    from .groups import index_for, is_community
    from .table import platform_verified

    verified = platform_verified(s)
    if i18n.get_language() == "ja":
        _emit(
            _info_lines_ja(
                m, e, gate, same, index_for(db).name_of.get(key, m.name), is_community(m), verified
            )
        )
        return 0
    n = len(same) + 1
    out = [f"{m.name}  [{m.id}]"]
    out.append(
        f"  model:    {index_for(db).name_of.get(key, m.name)}"
        f"  ({n} workflow{'s' if n > 1 else ''} use{'' if n > 1 else 's'} these weights)"
    )
    if is_community(m):
        out.append(f"  community template by {m.template.username} (not made by ComfyUI)")
    out.append(f"  modality: {m.modality}  tasks: {', '.join(m.tasks) or '?'}  content: {m.content}")
    out.append(f"  license:  {m.license or '? (unknown)'}  source: {m.source}")
    if m.runtime:
        out.append(f"  runtime:  {m.runtime}")
    if m.languages:
        out.append(
            "  languages: " + "; ".join(f"{k} {', '.join(v) or '(none)'}" for k, v in m.languages.items())
        )
    about = pick(m.description) if m.description else None
    if about:
        out.append("  about:    " + about)
    prec_from = f" (from {m.precision_source})" if m.precision and m.precision_source else ""
    out.append(f"  precision: {m.precision or '? (unknown)'}{prec_from}")
    counted = f"{e.disk_gb:.2f} GB counted" if e.disk_gb else "? (size unknown)"
    out.append(f"  size:     {counted}, {len(m.files)} file(s) listed (GB = GiB)")
    for f in m.files:
        fs = f"{f.size_bytes / GIB:.2f} GB" if f.size_bytes and f.size_bytes > 0 else "?"
        out.append(
            f"    - {f.name} ({fs}, {f.directory or '-'}, license {f.license or '?'}){_optional_mark(f)}"
        )
    basis = e.basis if e.basis_detail == e.basis else f"{e.basis}: {e.basis_detail}"
    if e.sample_count is not None:
        basis += f" n={e.sample_count}"  # 別々の出どころの数(sample_count。0.11.0 で件数から変えた)
    out.append(
        f"  fit:      {e.fit}  ({basis}{', on ' + e.measured_on if e.measured_on else ''})"
        + ("  ✓ verified GPU kind" if verified else "")
    )
    seq = f" / {e.vram_sequential_gb:.1f} GB swapping parts" if e.vram_sequential_gb is not None else ""
    if e.unknown_size:
        out.append("  VRAM:     ? (unknown: weight sizes are not known)")
    else:
        out.append(
            f"  VRAM:     {e.vram_gb:.1f} GB all-in-GPU{seq} / {e.vram_offload_gb:.1f} GB with offload;"
            f" RAM {e.ram_gb:.1f} GB"
        )
    if e.limits:
        out.append(f"  limits:   {format_limits(e.limits)}")
    out += [f"  - {r}" for r in e.reasons]
    if gate:
        target = gate.get("comfyui_version") or "?"
        out.append(
            f"  verified (auto-recommend): {'yes' if gate.get('auto') else 'held'}  (ComfyUI {target})"
        )
        for chk in gate.get("checks", []):
            mark = "ok" if chk["passed"] else "NG"
            out.append(f"    [{mark}] {chk['id']}: {chk.get('detail', '')}")
    if same:
        k = len(same)
        out.append(f"  same model ({k} other workflow{'s' if k > 1 else ''}; precision / use differ):")
        for w in same:
            need = w["memory_required_gb"]
            # 分からない(大きさ不明)は ?、当てはまらない(GPU を使わない)は -(#30)
            vram = f"{need:.1f} GB" if need is not None else ("?" if w["fit_level"] == "unknown" else "-")
            prec = w["precision"] or "?"
            out.append(f"    - {w['id']}  ({w['use']}, {prec}, {w['fit_level']}, {vram})")
    _emit(out)
    return 0


def _optional_mark(f) -> str:
    """info のファイルの行の、任意の部品の印(#81。VRAM・メモリに数えていないもの)。"""
    from .estimate import optional_kind

    kind = optional_kind(f)
    if kind == "switch_off":
        return tr(
            "  [任意: 既定でスイッチが切ってある。VRAM に数えない]",
            "  [optional: switched off by default; not in VRAM]",
        )
    if kind == "inactive":
        return tr(
            "  [任意: 無効・素通しのノードなど。数えない・落とさない]",
            "  [optional: muted / bypassed node etc.; not counted or downloaded]",
        )
    return ""


def _ja_label(name: str, width: int = 12) -> str:
    """info・download の日本語の見出し(「  モデル:     」)。値の頭を全角の幅でそろえる(#76)。"""
    from rich.cells import cell_len

    text = name + ":"
    return "  " + text + " " * max(1, width - cell_len(text))


def _info_lines_ja(
    m, e, gate, same: list[dict[str, Any]], model_name: str, community: bool, verified: bool = False
) -> list[str]:
    """info の日本語(#76)。英語と同じ項目を同じ順に。機械の値(ID・段階・用途・精度・コード)は訳さない。"""
    L = _ja_label
    out = [f"{m.name}  [{m.id}]"]
    out.append(f"{L('モデル')}{model_name}(この重みを使うワークフロー {len(same) + 1} 件)")
    if community:
        out.append(f"  利用者のテンプレート(作者 {m.template.username}。ComfyUI の公式ではない)")
    out.append(f"{L('種類')}{m.modality}  用途: {', '.join(m.tasks) or '?'}  内容: {m.content}")
    out.append(f"{L('ライセンス')}{m.license or '?(不明)'}  出どころ: {m.source}")
    if m.runtime:
        out.append(f"{L('動かし方')}{m.runtime}")
    if m.languages:
        langs = "; ".join(
            f"{_diff_label(_DIFF_LANG_LABELS, k)} {', '.join(v) or '(なし)'}" for k, v in m.languages.items()
        )
        out.append(f"{L('言語')}{langs}")
    about = pick(m.description) if m.description else None
    if about:
        out.append(f"{L('説明')}{about}")
    prec_from = f"(出どころ: {m.precision_source})" if m.precision and m.precision_source else ""
    out.append(f"{L('精度')}{m.precision or '?(不明)'}{prec_from}")
    counted = f"{e.disk_gb:.2f} GB(数えた分)" if e.disk_gb else "?(大きさ不明)"
    out.append(f"{L('大きさ')}{counted}、一覧のファイル {len(m.files)} 件(GB = GiB)")
    for f in m.files:
        fs = f"{f.size_bytes / GIB:.2f} GB" if f.size_bytes and f.size_bytes > 0 else "?"
        out.append(
            f"    - {f.name}({fs}、{f.directory or '-'}、ライセンス {f.license or '?'}){_optional_mark(f)}"
        )
    basis = e.basis if e.basis_detail == e.basis else f"{e.basis}: {e.basis_detail}"
    if e.sample_count is not None:
        basis += f" n={e.sample_count}"  # 別々の出どころの数(sample_count)
    where = f"、{e.measured_on} で" if e.measured_on else ""
    out.append(f"{L('段階')}{e.fit}({basis}{where})" + ("  ✓ 確認済みの種類" if verified else ""))
    if e.unknown_size:
        out.append(f"{L('VRAM')}?(重みの大きさが分からない)")
    else:
        seq = f" / 部品を載せ替えて {e.vram_sequential_gb:.1f} GB" if e.vram_sequential_gb is not None else ""
        out.append(
            f"{L('VRAM')}全部 GPU に載せて {e.vram_gb:.1f} GB{seq} / 逃がして {e.vram_offload_gb:.1f} GB、"
            f"メモリ {e.ram_gb:.1f} GB"
        )
    if e.limits:
        out.append(f"{L('限界')}{format_limits(e.limits)}")
    out += [f"  - {r}" for r in e.reasons]
    if gate:
        target = gate.get("comfyui_version") or "?"
        out.append(f"  自動のおすすめ(Verified): {'yes' if gate.get('auto') else '保留'}(ComfyUI {target})")
        for chk in gate.get("checks", []):
            mark = "ok" if chk["passed"] else "NG"
            out.append(f"    [{mark}] {chk['id']}: {chk.get('detail', '')}")
    if same:
        out.append(f"  同じモデル(ほかのワークフロー {len(same)} 件。精度・使い方が違う):")
        for w in same:
            need = w["memory_required_gb"]
            vram = f"{need:.1f} GB" if need is not None else ("?" if w["fit_level"] == "unknown" else "-")
            out.append(f"    - {w['id']}  ({w['use']}, {w['precision'] or '?'}, {w['fit_level']}, {vram})")
    return out


def same_model(db: Database, s: SystemInfo, m, args: argparse.Namespace) -> tuple[str, list[dict[str, Any]]]:
    """同じモデル(groups.group_key)のほかのワークフロー。この PC での判定つき、良い順。"""
    from .groups import index_for
    from .recommend import _member_dict, judge, variant_key

    idx = index_for(db)
    key = idx.key_of[m.id]
    c = load_coefficients()
    others = [
        judge(o, s, db, settings=_settings(args), coefficients=c, policy=_policy(args))
        for o in idx.members[key]
        if o.id != m.id
    ]
    others.sort(key=variant_key)
    return key, [_member_dict(o, True) for o in others]


def _progress(args: argparse.Namespace):
    """途中経過を stderr に出す関数(--json のときは出さない)。"""

    def say(msg: str) -> None:
        if not getattr(args, "json", False):
            print(msg, file=sys.stderr, flush=True)

    return say


def cmd_update(args: argparse.Namespace) -> int:
    if getattr(args, "from_release", False):
        return _update_from_release(args)
    from .db import cached_db_path
    from .update import (
        UpdateFailed,
        inspect_database,
        make_fetcher,
        refresh_app_fields,
        refresh_heads,
        update_database,
    )

    out_path = Path(args.output) if getattr(args, "output", None) else cached_db_path()
    say = _progress(args)
    fetch = make_fetcher(getattr(args, "offline_sources", None), getattr(args, "rate_limit_budget", None))
    try:
        if getattr(args, "inspect_only", False):
            db = inspect_database(
                fetch,
                out_path,
                db_path=getattr(args, "db", None),
                use_cache=not getattr(args, "no_cache", False),
                offline=bool(getattr(args, "offline", False)),
                progress=say,
            )
        elif getattr(args, "fields_only", False):
            db = refresh_app_fields(
                fetch,
                out_path,
                db_path=getattr(args, "db", None),
                use_cache=not getattr(args, "no_cache", False),
                offline=bool(getattr(args, "offline", False)),
                progress=say,
            )
        elif getattr(args, "heads_only", False):
            db = refresh_heads(
                fetch,
                out_path,
                db_path=getattr(args, "db", None),
                use_cache=not getattr(args, "no_cache", False),
                progress=say,
            )
        else:
            db = update_database(
                fetch,
                out_path,
                hf=not getattr(args, "skip_hf", False),
                head=not getattr(args, "skip_head", False),
                allow_partial=bool(getattr(args, "allow_partial", False)),
                progress=say,
            )
    except UpdateFailed as e:
        if args.json:
            _out({"genmodelfit": __version__, "command": "update", "error": str(e)})
        else:
            print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
        return 1
    st = _data_status(args, db, source="cache" if out_path == cached_db_path() else "path")
    if args.json:
        db.origin = "path"
        _out(_envelope("update", None, db, output=str(out_path), sources=db.sources, status=st.to_dict()))
        return 0
    _print_status_after_update(st)
    if getattr(args, "inspect_only", False):
        fh = db.sources["file_headers"]
        print(
            tr(
                f"ファイルの見出しの結果を書き出しました: {out_path}",
                f"Wrote the file header results: {out_path}",
            )
        )
        print(
            tr(
                f"  本体のファイル {fh['urls']} 件: キャッシュ {fh['from_cache']}、読んだ {fh['fetched']}、"
                f"読めない {fh['failed']}、未確認(オフライン) {fh['skipped_offline']}",
                f"  {fh['urls']} weight files: cached {fh['from_cache']}, read {fh['fetched']},"
                f" unreadable {fh['failed']}, unchecked (offline) {fh['skipped_offline']}",
            )
        )
        rp = db.sources.get("repo_precision") or {}
        if rp:
            print(
                tr(
                    f"  リポジトリから読んだ精度: Hub API {rp.get('hf_api', 0)}、"
                    f"config.json {rp.get('config', 0)}、分からない {rp.get('unknown', 0)}"
                    f"(調べた本体 {rp.get('files', 0)} 件)",
                    f"  Precision read from repositories: Hub API {rp.get('hf_api', 0)},"
                    f" config.json {rp.get('config', 0)}, unknown {rp.get('unknown', 0)}"
                    f" ({rp.get('files', 0)} weight files checked)",
                )
            )
        unknown = rp.get("precision_unknown", fh["precision_unknown"])
        print(tr(f"  精度が分からない項目: {unknown} 件", f"  Entries with unknown precision: {unknown}"))
        hf = db.sources.get("huggingface") or {}
        missing = set(hf.get("unauthorized_missing") or [])
        unauth = [r for r in hf.get("unauthorized") or [] if r not in missing]
        if unauth:
            print(
                tr(
                    f"  匿名で読めない(401。非公開か無い)リポジトリ: {', '.join(unauth)}",
                    f"  Repositories not readable anonymously (401: private or missing): {', '.join(unauth)}",
                )
            )
        if missing:
            print(
                tr(
                    f"  401 のうち無いと確かめたもの: {', '.join(sorted(missing))}",
                    f"  Of those 401s, confirmed missing: {', '.join(sorted(missing))}",
                )
            )
        return 0
    if getattr(args, "fields_only", False):
        af = db.sources["app_fields"]
        print(tr(f"アプリ向けの項目を書き出しました: {out_path}", f"Wrote the app fields: {out_path}"))
        print(
            tr(
                f"  手のモデル {db.sources['manual']['models']} 件 / 言語(指示) {af['languages_prompt']} 件・"
                f"言語(読み上げ) {af['languages_speech']} 件 / 動かし方 {af['runtime']} 件 / "
                f"説明 {af['description']} 件",
                f"  hand-added models {db.sources['manual']['models']} / languages (prompt)"
                f" {af['languages_prompt']}, languages (speech) {af['languages_speech']} / runtime"
                f" {af['runtime']} / description {af['description']}",
            )
        )
        return 0
    if getattr(args, "heads_only", False):
        stats = db.sources["head_check"]
        print(tr(f"HEAD の結果を書き出しました: {out_path}", f"Wrote the HEAD results: {out_path}"))
        status = ", ".join(f"{k} {v}" for k, v in stats["status"].items())
        print(tr(f"  URL {stats['urls']} 件: ", f"  {stats['urls']} URLs: ") + status)
        latest = db.sources["comfyui"]["latest_release"]
        print(
            tr(
                f"  未確認 {stats['unchecked']} 件 / ComfyUI の最新の版: {latest}",
                f"  unchecked {stats['unchecked']} / latest ComfyUI release: {latest}",
            )
        )
        return 0
    counts = db.sources["counts"]
    mods = ", ".join(f"{k} {v}" for k, v in counts["modality"].items())

    def joined(key: str) -> str:
        return ", ".join(f"{k} {v}" for k, v in counts[key].items())

    print(
        tr(
            f"{counts['models']} 件を書き出しました: {out_path}",
            f"Wrote {counts['models']} entries: {out_path}",
        )
    )
    print(tr(f"  種類: {mods}", f"  types: {mods}"))
    print(tr("  内容: ", "  content: ") + joined("content"))
    print(tr("  出どころ: ", "  source: ") + joined("source"))
    print(tr("  ライセンス: ", "  license: ") + joined("license_tier"))
    print(
        tr(
            f"  一式がそろっていない(files_complete=false): {counts['files_complete_false']} 件",
            f"  incomplete file sets (files_complete=false): {counts['files_complete_false']}",
        )
    )
    print(
        tr(
            f"  除外: {db.sources['excluded']} 件 / 警告: {len(db.sources['warnings'])} 件",
            f"  excluded: {db.sources['excluded']} / warnings: {len(db.sources['warnings'])}",
        )
    )
    if db.sources.get("partial"):
        pc = json.dumps(db.sources["partial_counts"], ensure_ascii=False)
        print(
            tr(
                f"  注意: 一部を取得できないまま書き出しました(--allow-partial): {pc}",
                f"  warning: written with some sources missing (--allow-partial): {pc}",
            )
        )
    return 0


def _update_from_release(args: argparse.Namespace) -> int:
    from .db import cached_db_path
    from .remote import update_from_release

    dest = Path(args.output) if getattr(args, "output", None) else cached_db_path()
    db = update_from_release(dest=dest, force=bool(getattr(args, "force", False)))
    st = _data_status(args, db, source="release")
    if args.json:
        out = {
            "genmodelfit": __version__,
            "command": "update",
            "models": len(db.models),
            "generated_at": db.generated_at,
            "output": str(dest),
            "status": st.to_dict(),
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    else:
        print(
            tr(
                f"一覧を更新しました: {len(db.models)} 件(生成日時 {db.generated_at}) -> {dest}",
                f"Updated the model list: {len(db.models)} entries (generated {db.generated_at}) -> {dest}",
            )
        )
        _print_status_after_update(st)
    return 0


# diff の表示の言葉(#59)。言語は呼んだときに選ぶ(#73)
_DIFF_FIELD_LABELS = {
    "name": ("名前", "name"),
    "modality": ("種類", "type"),
    "tasks": ("用途", "tasks"),
    "runtime": ("動かし方", "runtime"),
    "comfy_family": ("ComfyUI の系統", "ComfyUI family"),
    "content": ("内容", "content"),
    "precision": ("精度", "precision"),
}
_DIFF_LANG_LABELS = {"prompt": ("指示", "prompt"), "speech": ("読み上げ", "speech")}


def _diff_label(table: dict[str, tuple[str, str]], key: str) -> str:
    pair = table.get(key)
    return tr(*pair) if pair else key


# 変わったモデルの並びの大事さ(小さいほど先)。--limit で切るときも、大事な変化を先に出す
_DIFF_RANK = {
    "file_added": 0,
    "file_removed": 0,
    "files_order": 0,
    "file_changed": 0,
    "license": 1,
    "file_license": 1,
    # ライセンスの出どころは、自動のおすすめの変化の後・言語や大きさの前(オーナーの決定、#59)
    "license_source": 4.5,
    "auto": 4,
    "languages": 5,
    "size": 5,
    "review": 6,
}
_DIFF_VALUE_RANK = {"content": 2, "precision": 5}  # ほかの値(名前・種類・用途・動かし方・系統)は 3
_DIFF_OTHER_RANK = 6


def _diff_other_head() -> str:
    return tr("その他の変化: ", "other changes: ")


# この大事さ以下(数が大きい)の同じ行が、この件数以上のモデルにあれば 1 行にまとめる。部品・ライセンス・
# 内容(無検閲)はまとめない(モデルごとに確かめるもの)
_DIFF_COLLAPSE_RANK = 3
_DIFF_COLLAPSE_MIN = 3
# まとめた行の ID は、この件数までならその場に出す(多ければ --all)
_DIFF_COLLAPSE_SHOW_IDS = 8
# まとめない種類(大事さが低くても、モデルごとに出す)
_DIFF_NEVER_COLLAPSE = frozenset({"license_source"})
# まとめても ID を必ず出す種類(分類で判断が要る変化: 自動のおすすめ)。数は -n で切る
_DIFF_COLLAPSE_KEEP_IDS = frozenset({"auto"})
DIFF_DEFAULT_LIMIT = 20


def _diff_sections() -> tuple[tuple[str, tuple[float, float]], ...]:
    """変わったモデルの節: (見出し, 大事さの範囲)"""
    return (
        (
            tr(
                "変わったモデル(部品・ライセンス・内容・動かし方など。大事な変化の順):",
                "Changed models (files, license, content, runtime, ...; most important first):",
            ),
            (0, 4),
        ),
        (tr("自動のおすすめの変化:", "Auto-recommendation changes:"), (4, 4.5)),
        (
            tr(
                "そのほかの変化(ライセンスの出どころ・言語・大きさ・確認の印など):",
                "Other changes (license provenance, languages, size, review marks, ...):",
            ),
            (4.5, 99),
        ),
    )


_DIFF_LIST_MAX = 20  # 既定の表示で並べる数の上限(言語など。--all では全部)


def _diff_val(v: Any, full: bool = True) -> str:
    if v is None or v == []:
        return tr("なし", "none")
    if isinstance(v, list):
        if not full and len(v) > _DIFF_LIST_MAX:
            head = ", ".join(str(x) for x in v[: _DIFF_LIST_MAX - 4])
            rest = len(v) - _DIFF_LIST_MAX + 4
            return tr(f"{head} …ほか {rest}(全 {len(v)})", f"{head} … {rest} more ({len(v)} in all)")
        return ", ".join(str(x) for x in v)
    return str(v)


def _diff_auto(v: Any) -> str:
    return tr("出す", "yes") if v else (tr("なし", "none") if v is None else tr("保留", "held"))


def _diff_text(field: str, v: Any) -> str:
    if v is None:
        return tr("なし", "none")
    if isinstance(v, list):
        return " > ".join(str(x) for x in v)
    text = str(v)
    return text.split("://", 1)[-1] if field.endswith("url") else text


def _diff_pair(field: str, a: Any, b: Any, room: int = 72) -> tuple[str, str]:
    """前と後の値を、違うところが見えるように縮める(前と後が同じ文字列にならない)。

    sha256 は違う桁まで伸ばす。長い URL などは、同じ頭と尻を「…」にして、違うところとその前後を残す。"""
    ta, tb = _diff_text(field, a), _diff_text(field, b)
    if ta == tb:
        return ta, tb
    p = 0
    while p < min(len(ta), len(tb)) and ta[p] == tb[p]:
        p += 1
    if field == "sha256" and a is not None and b is not None:
        n = max(12, p + 4)
        return ta[:n] + ("…" if n < len(ta) else ""), tb[:n] + ("…" if n < len(tb) else "")
    if a is None or b is None or (len(ta) <= room and len(tb) <= room):
        return ta, tb  # 片方が無いときは、もう片方を全部出す
    s = 0
    while s < min(len(ta), len(tb)) - p and ta[-1 - s] == tb[-1 - s]:
        s += 1
    ctx = 12
    start = max(0, p - ctx)

    def cut(t: str) -> str:
        end = len(t) - max(0, s - ctx)
        seg, tail = t[start:end], end < len(t)
        if len(seg) > room:
            seg, tail = seg[:room], True  # 違うところ(先頭から ctx 文字目)は残る
        return ("…" if start else "") + seg + ("…" if tail else "")

    return cut(ta), cut(tb)


def _diff_model(m: dict[str, Any]) -> str:
    """モデルの見出し: 名前 (種類) — ID。名前が「(種類)」で終わるときは種類を重ねない。"""
    return f"{_diff_name(m)} — {m['id']}"


def _diff_name(m: dict[str, Any]) -> str:
    name, kind = str(m.get("name") or "?"), m.get("modality")
    if kind and not name.rstrip().lower().endswith(f"({kind})".lower()):
        name = f"{name} ({kind})"
    return name


def _diff_head(m: dict[str, Any], indent: str, width: int, extra: str = "") -> list[str]:
    """モデルの見出しの行。幅に収まらなければ ID を次の行に分ける(ID の途中では折り返さない)。"""
    from rich.cells import cell_len

    line = f"{indent}{_diff_model(m)}{extra}"
    if cell_len(line) <= width:
        return [line]
    out = [f"{indent}{_diff_name(m)} —", f"{indent}    {m['id']}"]
    if extra:
        if cell_len(out[1] + extra) <= width:
            out[1] += extra
        else:
            out.append(f"{indent}    {extra.strip()}")
    return out


def _diff_ids(ids: list[str], indent: str, width: int) -> list[str]:
    """ID の並びを、ID の途中で切らずに幅に詰める(幅より長い ID は 1 行にはみ出す)。"""
    from rich.cells import cell_len

    out: list[str] = []
    cur = ""
    for k, i in enumerate(ids):
        piece = i + ("," if k < len(ids) - 1 else "")
        cand = f"{cur} {piece}" if cur else f"{indent}{piece}"
        if cur and cell_len(cand) > width:
            out.append(cur)
            cand = f"{indent}{piece}"
        cur = cand
    if cur:
        out.append(cur)
    return out


def _diff_side(tag: str, side: dict[str, Any], label: str | None) -> str:
    head = f"{tag}: {label}  " if label else f"{tag}: "
    tail = tr("  (一部欠け: sources.partial)", "  (partial: sources.partial)") if side["partial"] else ""
    n = tr(f"{side['models']} 件", f"{side['models']} entries")
    return f"{head}{side['generated_at']}  {n}  ComfyUI {side['comfyui_latest_release']}{tail}"


def _diff_item(
    c: dict[str, Any], it: dict[str, Any], lic: dict[str, Any], unc: dict[str, Any], full: bool = True
) -> tuple[int, str]:
    """変わったモデルの 1 つの変化 → (大事さ, 行)。知らない種類も黙って落とさず、先に出す。"""
    kind = it["kind"]
    rank = _DIFF_RANK.get(kind, 0)
    if kind == "value":
        rank = _DIFF_VALUE_RANK.get(it["field"], 3)
        label = _diff_label(_DIFF_FIELD_LABELS, it["field"])
        line = f"{label} {_diff_val(it['old'], full)} → {_diff_val(it['new'], full)}"
        if it["field"] == "content" and it["new"] == "uncensored":
            ev = (unc.get(c["id"]) or {}).get("evidence") or []
            line += tr("  ! 新しく無検閲", "  ! newly uncensored") + (
                tr(f"({ev[0]})", f" ({ev[0]})") if ev else ""
            )
        return rank, line
    if kind == "license":
        permissive = (lic.get(c["id"]) or {}).get("permissive_side")
        mark = (
            tr("  ! 寛容な側(原文で確かめる)", "  ! more permissive (check the original text)")
            if permissive
            else ""
        )
        return rank, tr(
            f"ライセンス {it['old']} → {it['new']}{mark}", f"license {it['old']} → {it['new']}{mark}"
        )
    if kind == "license_source":
        where = tr(f"部品 {it['name']} の ", f"file {it['name']} ") if it.get("name") else ""
        a, b = _diff_pair(it["field"], it["old"], it["new"])
        return rank, tr("ライセンスの出どころ ", "license provenance ") + f"{where}{it['field']} {a} → {b}"
    if kind == "languages":
        what = _diff_label(_DIFF_LANG_LABELS, it["key"])
        vals = f"{_diff_val(it['old'], full)} → {_diff_val(it['new'], full)}"
        return rank, tr(f"言語({what}) {vals}", f"languages ({what}) {vals}")
    if kind in ("file_added", "file_removed"):
        path = f"{it['directory']}/{it['name']}" if it.get("directory") else str(it["name"])
        sign = "+" if kind == "file_added" else "-"
        size = _gib(it["size_bytes"])
        if i18n.get_language() == "en":
            src = f", {it['source']}" if it.get("source") else ""
            return rank, f"file {sign} {path} ({size} GiB, license {it['license']}{src})"
        src = f"、{it['source']}" if it.get("source") else ""
        return rank, f"部品 {sign} {path}({size} GiB、ライセンス {it['license']}{src})"
    if kind == "files_order":
        return rank, tr("部品の並び・形が変わった", "file order / shape changed")
    if kind == "file_changed":
        a, b = _diff_pair(it["field"], it["old"], it["new"])
        return rank, tr(
            f"部品 {it['name']} の {it['field']} {a} → {b}", f"file {it['name']} {it['field']} {a} → {b}"
        )
    if kind == "file_license":
        return rank, tr(
            f"部品 {it['name']} のライセンス {it['old']} → {it['new']}",
            f"file {it['name']} license {it['old']} → {it['new']}",
        )
    if kind == "size":
        a, b = it["old"], it["new"]
        if a and b and _gib(a) == _gib(b):
            return rank, tr(
                f"大きさ {_gib(b)} GiB(わずかな変化 {b - a:+,} バイト)",
                f"size {_gib(b)} GiB (small change {b - a:+,} bytes)",
            )
        return rank, tr(f"大きさ {_gib(a)} → {_gib(b)} GiB", f"size {_gib(a)} → {_gib(b)} GiB")
    if kind == "review":
        old, new = it["old"], it["new"]
        reasons = ", ".join(it["reasons"])
        why = tr(f"(理由: {reasons})", f" (reasons: {reasons})") if it["reasons"] and new != "ok" else ""
        if old is None and new is not None:
            return rank, tr(f"確認の印が付いた({new}){why}", f"review mark added ({new}){why}")
        if old is not None and new is None:
            return rank, tr(f"確認の印が外れた(前は {old})", f"review mark removed (was {old})")
        if old != new:
            return rank, tr(f"確認の印 {old} → {new}{why}", f"review mark {old} → {new}{why}")
        fields = ", ".join(it.get("fields") or []) or "?"
        return rank, tr(
            f"確認の印({new})の中身が変わった: {fields}", f"review mark ({new}) details changed: {fields}"
        )
    if kind == "auto":
        held = ", ".join(it.get("held_by") or [])
        why = tr(f"(保留: {held})", f" (held: {held})") if held else ""
        return rank, tr(
            "自動のおすすめ ", "auto-recommend "
        ) + f"{_diff_auto(it['old'])} → {_diff_auto(it['new'])}{why}"
    return rank, f"{kind}: {json.dumps(it, ensure_ascii=False)}"


def _diff_changed_entries(
    d: dict[str, Any],
    show_all: bool,
    limit: int | None = None,
    width: int = 100,
    ranks: tuple[float, float] | None = None,
) -> list[tuple[tuple[Any, ...], list[str]]]:
    """「変わったモデル」の節の並び: (並べる鍵, 行)。大事な変化のあるモデルが先。

    大事さの低い同じ行(確認の印が付いた など。「その他の変化」は項目ごと)が多くのモデルにあれば 1 行
    (件数)にまとめる。まとめた行も同じ大事さの順に並ぶので、--limit で大事なものが隠れない。
    まとめた行の ID: 自動のおすすめの変化は必ず出す(limit まで)。ほかは少なければその場に、多ければ --all。
    ranks: (下限, 上限) の大事さの行だけを使う(節に分けるとき。None = 全部)。"""
    lic = {c["id"]: c for c in d["license_changes"]}
    unc = {c["id"]: c for c in d["newly_uncensored"]}
    per: list[tuple[dict[str, Any], list[tuple[int, str, str]]]] = []
    for c in d.get("model_changes") or []:
        rows = [(*_diff_item(c, it, lic, unc, show_all), it["kind"]) for it in c["items"]]
        rows += [(_DIFF_OTHER_RANK, _diff_other_head() + f, "other") for f in c["other"]]
        if ranks is not None:
            rows = [r for r in rows if ranks[0] <= r[0] < ranks[1]]
        per.append((c, rows))
    count: dict[tuple[int, str, str], list[str]] = {}
    for c, rows in per:
        for r in dict.fromkeys(rows):
            if r[0] >= _DIFF_COLLAPSE_RANK and r[2] not in _DIFF_NEVER_COLLAPSE:
                count.setdefault(r, []).append(c["id"])
    groups = {r: ids for r, ids in count.items() if len(ids) >= _DIFF_COLLAPSE_MIN}
    out: list[tuple[tuple[Any, ...], list[str]]] = []
    for c, rows in per:
        keep = sorted((r for r in rows if r not in groups), key=lambda r: r[0])
        if not keep:
            continue
        texts = [t for _, t, k in keep if k != "other"]
        others = [t[len(_diff_other_head()) :] for _, t, k in keep if k == "other"]
        if others:
            texts.append(_diff_other_head() + ", ".join(others))
        out.append(((keep[0][0], 0, c["id"]), [*_diff_head(c, "  ", width), *(f"    {t}" for t in texts)]))
    for (rank, text, kind), ids in groups.items():
        lines = [tr(f"  {text}: {len(ids)} 件", f"  {text}: {len(ids)} models")]
        if kind in _DIFF_COLLAPSE_KEEP_IDS:  # 判断が要る変化は ID を隠さない
            shown = ids if limit is None else ids[:limit]
            lines += _diff_ids(shown, "    ", width)
            if len(shown) < len(ids):
                rest = len(ids) - len(shown)
                lines.append(
                    tr(
                        f"    …ほか {rest} 件(-n {len(ids)} で増やす)",
                        f"    … {rest} more (-n {len(ids)} for more)",
                    )
                )
        elif show_all or len(ids) <= _DIFF_COLLAPSE_SHOW_IDS:
            lines += _diff_ids(ids, "    ", width)
        else:
            lines[0] += tr("(ID は --all)", " (ids: --all)")
        out.append(((rank, 1, text), lines))
    out.sort(key=lambda e: e[0])
    return out


def _diff_lines(d: dict[str, Any], limit: int | None, show_all: bool = False, width: int = 100) -> list[str]:
    """`genmodelfit diff` の表示(折り返し前の行)。#59

    既定は利用者に関係のある変化だけ(増えた・消えた・変わったモデルの変化(自動のおすすめを含む)・
    新しい HEAD の失敗)。show_all(--all)は保守用の、前からの HEAD の失敗・401 のリポジトリ・
    sources の数・人気の数などだけの変化と、まとめた行の ID も出す。limit は節ごとの上限(None = 全部)。
    width はモデルの ID を途中で折り返さないための幅(ID は自分の行に分ける)。"""
    s = d["summary"]

    def cut(items: list[Any]) -> list[Any]:
        return items if limit is None else items[:limit]

    def more(items: list[Any]) -> list[str]:
        if limit is None or len(items) <= limit:
            return []
        rest = len(items) - limit
        return [
            tr(
                f"    …ほか {rest} 件(-n {len(items)} か --all で全部。--json でも出ます)",
                f"    … {rest} more (-n {len(items)} or --all for all; also in --json)",
            )
        ]

    changed = d.get("model_changes") or []
    head_new = [h for h in d["head_failures"] if h["new"]]
    lines = [
        _diff_side(tr("前", "old"), d["old"], d.get("old_label")),
        _diff_side(tr("後", "new"), d["new"], d.get("new_label")),
        tr(
            f"増えた {s['added']} / 消えた {s['removed']} / 変わった {len(changed)} / 自動のおすすめ"
            f" {s['auto_old']} → {s['auto_new']}(+{s['auto_gained']} -{s['auto_lost']})"
            f" / HEAD の新しい失敗 {s['head_failures_new']}",
            f"added {s['added']} / removed {s['removed']} / changed {len(changed)} / auto-recommended"
            f" {s['auto_old']} → {s['auto_new']} (+{s['auto_gained']} -{s['auto_lost']})"
            f" / new HEAD failures {s['head_failures_new']}",
        ),
    ]
    lines += [tr(f"注意: {w}", f"warning: {w}") for w in d.get("warnings") or []]
    if show_all:
        lines += [
            tr(
                f"ライセンスの変化 {s['license_changed']}(寛容な側 {s['license_permissive_side']})"
                f" / 大きさ {s['size_changed']} / 精度 {s['precision_changed']}"
                f" / 新しく無検閲 {s['newly_uncensored']}",
                f"license changes {s['license_changed']} (more permissive {s['license_permissive_side']})"
                f" / size {s['size_changed']} / precision {s['precision_changed']}"
                f" / newly uncensored {s['newly_uncensored']}",
            ),
            tr(
                f"HEAD の失敗 {s['head_failures']}(新 {s['head_failures_new']})",
                f"HEAD failures {s['head_failures']} (new {s['head_failures_new']})",
            )
            + f" / 401 +{s['unauthorized_added']} -{s['unauthorized_removed']}",
        ]
    if d.get("top_changes"):
        lines += [
            "",
            tr("一覧の上の段で変わった鍵: ", "Changed top-level keys: ") + ", ".join(d["top_changes"]),
        ]
    auto_new = {c["id"] for c in d["auto_changes"] if c["new"]}
    for key, title in (("added", tr("増えた", "Added")), ("removed", tr("消えた", "Removed"))):
        if d[key]:
            lines += ["", f"{title}:"]
            for m in cut(d[key]):
                auto = (
                    tr("  自動のおすすめに出す", "  auto-recommended")
                    if key == "added" and m["id"] in auto_new
                    else ""
                )
                lines += _diff_head(m, "  ", width, f"  [{m['content']}/{m['license']}]{auto}")
            lines += more(d[key])
    # 変わったモデルは 3 つの節に分け、節ごとに -n で切る(どの節も先頭の大事なものは隠れない)。
    # 並び(オーナーの決定、#59): 部品・ライセンス・内容・動かし方など → 自動のおすすめ →
    # ライセンスの出どころ・言語・大きさ・確認の印など
    entries: list[tuple[tuple[Any, ...], list[str]]] = []
    for title, ranks in _diff_sections():
        part = _diff_changed_entries(d, show_all, limit, width, ranks)
        if part:
            entries += part
            lines += ["", title]
            for _, block in cut(part):
                lines += block
            lines += more(part)
    hf_ = sorted(d["head_failures"], key=lambda h: (not h["new"], h["url"])) if show_all else head_new
    if hf_:
        lines += [
            "",
            tr("HEAD が失敗した URL(! = 新しく):", "URLs whose HEAD check failed (! = new):")
            if show_all
            else tr("HEAD が新しく失敗した URL:", "URLs whose HEAD check newly failed:"),
        ]
        mark = (lambda h: "! " if h["new"] else "  ") if show_all else (lambda h: "")
        lines += [f"  {mark(h)}{h['status']} {h['url']}" for h in cut(hf_)]
        lines += more(hf_)
    big = {c["id"] for c in changed}
    quiet = {i: p for i, p in (d.get("field_changes") or {}).items() if i not in big}
    if show_all:
        ua = d["unauthorized"]
        if ua["added"] or ua["removed"]:
            lines += [
                "",
                tr("401 のリポジトリ: ", "401 repositories: ")
                + f"+{', '.join(ua['added']) or '-'} / -{', '.join(ua['removed']) or '-'}",
            ]
        if quiet:
            q = sorted(quiet.items())
            lines += [
                "",
                tr(
                    "人気の数・更新日・HEAD の結果などだけの変化:",
                    "Changes only in popularity, dates, HEAD results, ...:",
                ),
            ]
            lines += [f"  {i}: {', '.join(p)}" for i, p in cut(q)]
            lines += more(q)
        if d["stats"]:
            lines += ["", tr("sources の数の変化:", "Changed numbers in sources:")]
            lines += [f"  {x['key']}: {x['old']} -> {x['new']}" for x in cut(d["stats"])]
            lines += more(d["stats"])
        return lines
    if not (d["added"] or d["removed"] or entries or head_new or d.get("top_changes")):
        lines += ["", tr("変化なし(利用者に関係のある変化はありません)", "No changes that matter to users")]
    hidden = []
    old_fail = s["head_failures"] - len(head_new)
    if quiet:
        hidden.append(tr(f"人気の数などだけの変化 {len(quiet)} 件", f"{len(quiet)} popularity-only changes"))
    if old_fail:
        hidden.append(tr(f"前からの HEAD の失敗 {old_fail} 件", f"{old_fail} older HEAD failures"))
    if d["stats"]:
        hidden.append(tr(f"sources の数 {len(d['stats'])} 件", f"{len(d['stats'])} sources numbers"))
    if d["unauthorized"]["added"] or d["unauthorized"]["removed"]:
        hidden.append(tr("401 のリポジトリの増減", "401 repository changes"))
    if hidden:
        lines += ["", tr(f"(--all で: {'、'.join(hidden)})", f"(with --all: {', '.join(hidden)})")]
    return lines


def _diff_print(lines: list[str], width: int) -> None:
    """幅に折り返して出す。字下げのあとが 1 語だけの行(ID だけの行)は折り返さない(はみ出してよい)。"""
    from .table import wrap_line

    for line in lines:
        if line.strip() and " " not in line.strip():
            print(line)
            continue
        for part in wrap_line(line, width):
            print(part)


def _gib(b: int | None) -> str:
    return "?" if not b else f"{b / GIB:.2f}"


def _diff_howto() -> str:
    return tr(
        "取ってきた一覧がまだありません({path})。"
        "先に `genmodelfit update --from-release`"
        " で最新の一覧を取ってきてから、もう一度 `genmodelfit diff` を"
        "実行してください(2 つのファイルを比べるときは `genmodelfit diff old.json new.json`)。",
        "No fetched list yet ({path}). Run `genmodelfit update --from-release` to fetch the latest list, then"
        " run `genmodelfit diff` again (to compare two files: `genmodelfit diff old.json new.json`).",
    )


def _diff_older() -> str:
    return tr(
        "後の一覧({new_label}、{new})が前の一覧({old_label}、{old})より古いです。比べる向きが逆かもしれません"
        "(新しいモデルが「消えた」に見えます)。",
        "The new list ({new_label}, {new}) is older than the old list ({old_label}, {old}). The order may be"
        ' reversed (new models would look "removed").',
    )


def _diff_older_cache() -> str:
    return tr(
        "`genmodelfit update --from-release` で取り直してください。",
        " Fetch it again with `genmodelfit update --from-release`.",
    )


def _diff_raw(path: str | Path | None) -> dict[str, Any]:
    """一覧の JSON をそのまま読む(この版が知らない鍵の変化も比べるため。merge-check と同じ)。
    None は同梱の一覧。読めない・形が違うときは load_db が先に DatabaseError を出す。"""
    from .db import bundled_text

    text = bundled_text("db.json") if path is None else Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def _diff_inputs(
    args: argparse.Namespace,
) -> tuple[tuple[Database, dict[str, Any], str], tuple[Database, dict[str, Any], str], list[str]] | str:
    """比べる 2 つの一覧(Database・JSON・説明)と注意。#59

    引数なし = 同梱の一覧 ↔ 取ってきた一覧(update・update --from-release が書くキャッシュ)、
    1 つ = 同梱の一覧 ↔ そのファイル、2 つ = そのまま。取ってきた一覧が無ければ案内の文を返す。
    後の一覧が前の一覧より古い(生成日時)ときは、どの形でも注意を付ける。"""
    from .db import cached_db_path

    def side(path: str | Path | None, label: str) -> tuple[Database, dict[str, Any], str]:
        db = load_db(None, use_cache=False) if path is None else load_db(path)
        return db, _diff_raw(path), label

    bundled = tr(f"同梱の一覧(genmodelfit {__version__})", f"bundled list (genmodelfit {__version__})")
    from_cache = False
    if args.old and args.new:
        old, new = side(args.old, str(args.old)), side(args.new, str(args.new))
    elif args.old:
        old, new = side(None, bundled), side(args.old, str(args.old))
    else:
        cp = cached_db_path()
        if not cp.exists():
            return _diff_howto().format(path=cp)
        old, new = side(None, bundled), side(cp, tr(f"取ってきた一覧({cp})", f"fetched list ({cp})"))
        from_cache = True
    warn = []
    ga, gb = old[0].generated_at, new[0].generated_at
    if ga and gb and gb < ga:
        text = _diff_older().format(old_label=old[2], old=ga, new_label=new[2], new=gb)
        warn.append(text + (_diff_older_cache() if from_cache else ""))
    return old, new, warn


def cmd_diff(args: argparse.Namespace) -> int:
    from .diff import diff_db, empty_diff
    from .table import output_width

    width = output_width()
    got = _diff_inputs(args)
    if isinstance(got, str):  # 取ってきた一覧が無い: 案内だけ(失敗ではない)
        if args.json:
            _out({"genmodelfit": __version__, "command": "diff", **empty_diff(got), "warnings": []})
        else:
            _diff_print([got], width)
        return 0
    (old, raw_old, old_label), (new, raw_new, new_label), warnings = got
    d = diff_db(
        old,
        new,
        comfyui_version=args.comfyui_version,
        old_label=old_label,
        new_label=new_label,
        raw=(raw_old, raw_new),
    )
    d["warnings"] = warnings
    if args.json:
        _out({"genmodelfit": __version__, "command": "diff", **d})
        return 0
    limit = args.limit if args.limit is not None else (None if args.all else DIFF_DEFAULT_LIMIT)
    _diff_print(_diff_lines(d, limit, show_all=args.all, width=width), width)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import BenchError, options_from_args, run, save, save_decision
    from .workflow import WorkflowError

    try:
        result = run(options_from_args(args), log=_progress(args))
    except (BenchError, WorkflowError) as e:
        raise CliError(str(e)) from e
    m = result.measurement
    out_path, not_saved = save_decision(
        m, args.output, getattr(args, "save_failed", False), getattr(args, "save_nonstandard", False)
    )
    if out_path is not None:
        save(m, out_path)
    sub_path, sub_repo = _bench_submission(args, m)
    code = 1 if result.failed else 0
    if args.json:
        extra = {"saved_to": str(out_path) if out_path else None}
        if not_saved:
            extra["not_saved"] = not_saved
        if sub_path is not None:
            extra["submission"] = {"saved_to": str(sub_path), "path_in_repo": sub_repo}
            if args.share:
                share_code, info = _share(sub_path, args, as_json=True)
                extra["submission"]["share"] = info
                code = max(code, share_code)
        _out(_envelope("bench", None, None, **extra, **result.to_dict()))
        return code
    for w in result.warnings:
        print(tr(f"注意: {w}", f"warning: {w}"), file=sys.stderr)
    s = m.settings
    if i18n.get_language() == "en":
        unknown = "unknown"
        parts = [f"{s.width}x{s.height}" if s.width else None, f"{s.frames} frames" if s.frames else None]
        parts += [f"{s.seconds:g} s of audio" if s.seconds else None, f"{s.steps} steps" if s.steps else None]
        print(f"=== bench: {m.model_id} ===")
        print(f"GPU:       {m.gpu_name} ({m.gpu_vram_gb:g} GiB, driver {m.driver or unknown})")
        print(f"ComfyUI:   {m.comfyui_version or unknown}")
        print(f"Settings:  {', '.join(p for p in parts if p) or '(as in the template)'}")
        print(f"Result:    {m.outcome}{'' if m.standard else ' (non-standard conditions)'}")
        print(f"Peak VRAM: {m.peak_vram_gb:.2f} GB (the ComfyUI process)")
        if m.peak_ram_gb is not None:
            print(f"Peak RAM:  {m.peak_ram_gb:.2f} GB")
        print(f"Time:      {m.seconds:g} s (from sending to the end, including model loading)")
        print(f"Offload:   {m.offload or unknown}")
        print(f"Note:      {m.note}")
        if result.failed:
            print(f"Failed:    {result.failed}")
        print(f"Saved to:  {out_path}" if out_path else f"Not saved: {not_saved}")
        if sub_path is not None:
            print(f"Submission: {sub_path}")
            if args.share:
                print()
                return max(code, _share(sub_path, args, as_json=False)[0])
            print(
                f"           send it with genmodelfit share {sub_path}"
                f" (in the repository it becomes {sub_repo})"
            )
        return code
    parts = [f"{s.width}x{s.height}" if s.width else None, f"{s.frames} フレーム" if s.frames else None]
    parts += [f"{s.seconds:g} 秒の音声" if s.seconds else None, f"{s.steps} ステップ" if s.steps else None]
    print(f"=== bench: {m.model_id} ===")
    print(f"GPU:      {m.gpu_name}({m.gpu_vram_gb:g} GiB、ドライバ {m.driver or '不明'})")
    print(f"ComfyUI:  {m.comfyui_version or '不明'}")
    print(f"設定:     {'、'.join(p for p in parts if p) or '(テンプレートのまま)'}")
    print(f"結果:     {m.outcome}{'' if m.standard else '(標準でない条件)'}")
    print(f"最大 VRAM: {m.peak_vram_gb:.2f} GB(ComfyUI のプロセス)")
    if m.peak_ram_gb is not None:
        print(f"最大メモリ: {m.peak_ram_gb:.2f} GB")
    print(f"時間:     {m.seconds:g} 秒(送ってから終わるまで、モデルの読み込み込み)")
    print(f"載せ方:   {m.offload or '不明'}")
    print(f"メモ:     {m.note}")
    if result.failed:
        print(f"失敗:     {result.failed}")
    print(f"保存先:   {out_path}" if out_path else f"保存:     {not_saved}")
    if sub_path is not None:
        print(f"提出用:   {sub_path}")
        if args.share:
            print()
            return max(code, _share(sub_path, args, as_json=False)[0])
        print(f"          送るときは genmodelfit share {sub_path}(リポジトリでは {sub_repo})")
    return code


def _bench_submission(args: argparse.Namespace, m) -> tuple[Path | None, str | None]:
    """bench --submission: 提出用の JSON を書く(#34)。ComfyUI が同じ PC のときだけ、この PC の
    OS の種類・メモリ・GPU の世代を入れる。--share で --submission が無いときは
    <キャッシュ>/submissions/<GPU>/<日付>-<sha8>.json に書く(#90)。"""
    target = getattr(args, "submission", None)
    if not target and getattr(args, "share", False):
        from .db import cache_dir

        target = str(cache_dir() / "submissions") + os.sep
    if not target:
        return None, None
    from .bench import is_local
    from .contrib import build_submission, suggested_path, write_submission

    system = detect() if is_local(args.url) else None
    sub = build_submission(m, system)
    return write_submission(sub, target), suggested_path(sub)


# --- contrib(#34) --------------------------------------------------------------------------------

# 終了コード(contrib)。docs/schema.md「contrib」
EXIT_BREACH = 3  # aggregate --fail-on-breach で、誤差が閾値を超えた


def _contrib_db(args: argparse.Namespace) -> Database:
    """検査・まとめに使う一覧。既定は同梱のもの(CI と手元で同じ結果にする)。"""
    return load_db(getattr(args, "db", None), use_cache=False)


def _shown(v: Any, unit: str = "") -> str:
    return tr("不明", "unknown") if v is None else f"{v}{unit}"


def format_submission(data: dict[str, Any], footer: bool = True) -> list[str]:
    """提出の中身を人が読める形に(contrib show)。ファイルの項目はすべて出す。
    footer が偽なら最後の「どこにも送らない」の文を出さない(share が PR で送るとき。#124)。"""
    from .contrib import suggested_path

    system = data.get("system") or {}
    gpu = system.get("gpu") or {}
    mm = data.get("measurement") or {}
    tool = data.get("tool") or {}
    s = mm.get("settings") or {}
    if isinstance(data.get("failures"), list):
        return _format_failures(data, footer)
    if i18n.get_language() == "en":
        return _format_submission_en(data, footer)
    gpu_bits = [
        f"{_shown(gpu.get('vram_gb'), ' GiB')}",
        f"世代 {_shown(gpu.get('arch'))}",
        f"compute {_shown(gpu.get('compute_capability'))}",
        f"ドライバ {_shown(gpu.get('driver'))}",
        # 会社・backend(0.9.0)。入っているときだけ出す(古い提出には無い)
        *(
            [f"会社 {_shown(gpu.get('vendor'))}", f"backend {_shown(gpu.get('backend'))}"]
            if {"vendor", "backend"} & set(gpu)
            else []
        ),
        # PCI の型番 ID(0.9.0。機種の番号で、個体・差し場所は表さない)。入っているときだけ
        *([f"型番 ID {_shown(gpu.get('pci_id'))}"] if "pci_id" in gpu else []),
    ]
    peak = mm.get("peak_vram_gb")
    return [
        "=== 提出する中身 ===",
        f"置き場所:   {suggested_path(data)}(受け付けたときのリポジトリの中の名前)",
        f"形式:       contrib_schema {data.get('contrib_schema')}"
        f"({tool.get('name')} {tool.get('version')}、{data.get('kind')})",
        f"日付:       {data.get('date')}(UTC の日付だけ。時刻は入れない)",
        f"OS:         {_shown(system.get('os_family'))}(種類だけ。版・ビルドは入れない)",
        f"メモリ:     {_shown(system.get('ram_total_gb'), ' GiB')}(整数に丸めた量)",
        f"GPU:        {_shown(gpu.get('name'))}({'、'.join(gpu_bits)})",
        f"ComfyUI:    {_shown(data.get('comfyui_version'))}",
        f"モデル:     {_shown(mm.get('model_id'))}",
        f"設定:       {', '.join(f'{k}={v}' for k, v in s.items()) or '(テンプレートのまま)'}",
        f"結果:       {_shown(mm.get('outcome'))}{'' if mm.get('standard', True) else '(標準でない条件)'}",
        f"最大 VRAM:  {_shown(peak, ' GiB')}(測り方 {_shown(mm.get('vram_metric'))})",
        f"最大メモリ: {_shown(mm.get('peak_ram_gb'), ' GiB')}",
        f"時間:       {_shown(mm.get('seconds'), ' 秒')}",
        f"載せ方:     {_shown(mm.get('offload'))}",
        *_new_field_lines(data),
        f"メモ:       {mm.get('note') or '(なし)'}",
        *(
            [
                "",
                "これがファイルの中身のすべてです。genmodelfit はこのファイルをどこにも送りません。",
                "共有するのは、あなたが GitHub のフォームで「Create」を押したときだけです(CONTRIBUTING.md)。",
            ]
            if footer
            else []
        ),
    ]


def _format_submission_en(data: dict[str, Any], footer: bool = True) -> list[str]:
    """format_submission の英語(#73)。項目は日本語と同じ。"""
    from .contrib import suggested_path

    system = data.get("system") or {}
    gpu = system.get("gpu") or {}
    mm = data.get("measurement") or {}
    tool = data.get("tool") or {}
    s = mm.get("settings") or {}
    gpu_bits = [
        f"{_shown(gpu.get('vram_gb'), ' GiB')}",
        f"arch {_shown(gpu.get('arch'))}",
        f"compute {_shown(gpu.get('compute_capability'))}",
        f"driver {_shown(gpu.get('driver'))}",
        *(
            [f"vendor {_shown(gpu.get('vendor'))}", f"backend {_shown(gpu.get('backend'))}"]
            if {"vendor", "backend"} & set(gpu)
            else []
        ),
        *([f"PCI model ID {_shown(gpu.get('pci_id'))}"] if "pci_id" in gpu else []),
    ]
    peak = mm.get("peak_vram_gb")
    return [
        "=== What will be shared ===",
        f"Path:       {suggested_path(data)} (its name in the repository once accepted)",
        f"Format:     contrib_schema {data.get('contrib_schema')}"
        f" ({tool.get('name')} {tool.get('version')}, {data.get('kind')})",
        f"Date:       {data.get('date')} (UTC date only, no time)",
        f"OS:         {_shown(system.get('os_family'))} (family only, no version or build)",
        f"RAM:        {_shown(system.get('ram_total_gb'), ' GiB')} (rounded to an integer)",
        f"GPU:        {_shown(gpu.get('name'))} ({', '.join(gpu_bits)})",
        f"ComfyUI:    {_shown(data.get('comfyui_version'))}",
        f"Model:      {_shown(mm.get('model_id'))}",
        f"Settings:   {', '.join(f'{k}={v}' for k, v in s.items()) or '(as in the template)'}",
        "Result:    "
        f" {_shown(mm.get('outcome'))}{'' if mm.get('standard', True) else ' (non-standard conditions)'}",
        f"Peak VRAM:  {_shown(peak, ' GiB')} (metric {_shown(mm.get('vram_metric'))})",
        f"Peak RAM:   {_shown(mm.get('peak_ram_gb'), ' GiB')}",
        f"Time:       {_shown(mm.get('seconds'), ' s')}",
        f"Offload:    {_shown(mm.get('offload'))}",
        *_new_field_lines(data),
        f"Note:       {mm.get('note') or '(none)'}",
        *(
            [
                "",
                "This is everything in the file. genmodelfit does not send this file anywhere.",
                'It is shared only when you press "Create" in the GitHub form (CONTRIBUTING.md).',
            ]
            if footer
            else []
        ),
    ]


def _num_text(v: Any, unit: str = "") -> str:
    return tr("不明", "unknown") if not isinstance(v, (int, float)) or isinstance(v, bool) else f"{v:g}{unit}"


def _db_line(data: dict[str, Any]) -> list[str]:
    db = data.get("db")
    if not isinstance(db, dict):
        return []
    sha = f"sha256 {db.get('sha256')}" if db.get("sha256") else tr("sha256 不明", "sha256 unknown")
    return [
        tr(
            f"一覧の版:   {_shown(db.get('generated_at'))}({sha}。測ったときの一覧)",
            f"List:       {_shown(db.get('generated_at'))} ({sha}; the list when measured)",
        )
    ]


def _new_field_lines(data: dict[str, Any]) -> list[str]:
    """contrib_schema 3 の実測の項目(#132): 一覧の版・測ったときの見積もり・時間の内訳・VRAM を超えた印。
    送る前の画面で、新しく送る項目も見せる(何を送るかが分かるように)。"""
    mm = data.get("measurement") or {}
    out = _db_line(data)
    est = mm.get("estimate")
    if isinstance(est, dict):
        gb = {
            k: _num_text(est.get(k), " GiB")
            for k in ("vram_gb", "vram_resident_gb", "work_gb", "ram_required_gb")
        }
        level, mode, basis = (_shown(est.get(k)) for k in ("fit_level", "run_mode", "basis"))
        out.append(
            tr(
                f"見積もり:   段階 {level}・動かし方 {mode}・VRAM {gb['vram_gb']}(仕上げまで載せたまま"
                f" {gb['vram_resident_gb']}・作業用メモリ {gb['work_gb']})・メモリ {gb['ram_required_gb']}・"
                f"時間 {_num_text(est.get('seconds'), ' 秒')}・根拠 {basis}"
                "(この実測と、同じ GPU・同じモデルの実測を使わずに作った予測)",
                f"Estimate:   level {level}, run {mode}, VRAM {gb['vram_gb']} (kept loaded to the end"
                f" {gb['vram_resident_gb']}, working memory {gb['work_gb']}), RAM {gb['ram_required_gb']},"
                f" time {_num_text(est.get('seconds'), ' s')}, basis {basis}"
                " (the prediction made without this measurement or other measurements of this model on"
                " this GPU)",
            )
        )
    t = mm.get("timing")
    if isinstance(t, dict):
        keys = ("queue_s", "load_s", "sampling_s", "sec_per_step", "decode_s", "save_s", "total_s")
        ja = {k: _num_text(t.get(k), " 秒") for k in keys}
        en = {k: _num_text(t.get(k), " s") for k in keys}
        cold = t.get("cold_load")
        cold_ja = "不明" if cold is None else ("はい" if cold else "いいえ(2 回目)")
        cold_en = "unknown" if cold is None else ("yes" if cold else "no (second time)")
        out.append(
            tr(
                f"時間の内訳: キュー {ja['queue_s']}・読み込み {ja['load_s']}・サンプラ {ja['sampling_s']}"
                f"(1 ステップ {ja['sec_per_step']})・復元と保存 {ja['decode_s']}・保存 {ja['save_s']}・"
                f"合計 {ja['total_s']}・初めての読み込み {cold_ja}",
                f"Timing:     queue {en['queue_s']}, load {en['load_s']}, sampling {en['sampling_s']}"
                f" ({en['sec_per_step']} per step), decode and save {en['decode_s']}, save {en['save_s']},"
                f" total {en['total_s']}, first load {cold_en}",
            )
        )
    if mm.get("spilled") is True:
        over = _num_text(mm.get("spilled_gb"), " GiB")
        out.append(
            tr(
                f"VRAM 超え:  GPU の総量を {over} 超えた(共有メモリに逃げた。見積もり・段階には使わない)",
                f"Spilled:    {over} over the GPU total (went to shared memory; not used for estimates or"
                " levels)",
            )
        )
    return out


def _format_failures(data: dict[str, Any], footer: bool = True) -> list[str]:
    """失敗の記録のファイル(#132)を人が読める形に。ファイルの項目はすべて出す。"""
    from .contrib import suggested_path

    system = data.get("system") or {}
    gpu = system.get("gpu") or {}
    tool = data.get("tool") or {}
    items = data.get("failures") or []
    lines = [
        tr("=== 提出する中身(失敗の記録) ===", "=== What will be shared (failure records) ==="),
        tr(
            f"置き場所:   {suggested_path(data)}(受け付けたときのリポジトリの中の名前)",
            f"Path:       {suggested_path(data)} (its name in the repository once accepted)",
        ),
        tr("形式:       ", "Format:     ")
        + f"contrib_schema {data.get('contrib_schema')} ({tool.get('name')} {tool.get('version')},"
        f" {data.get('kind')})",
        tr(
            f"日付:       {data.get('date')}(UTC の日付だけ。時刻は入れない)",
            f"Date:       {data.get('date')} (UTC date only, no time)",
        ),
        tr(
            f"OS:         {_shown(system.get('os_family'))}(種類だけ。版・ビルドは入れない)",
            f"OS:         {_shown(system.get('os_family'))} (family only, no version or build)",
        ),
        tr(
            f"メモリ:     {_shown(system.get('ram_total_gb'), ' GiB')}(整数に丸めた量)",
            f"RAM:        {_shown(system.get('ram_total_gb'), ' GiB')} (rounded to an integer)",
        ),
        tr("GPU:        ", "GPU:        ")
        + f"{_shown(gpu.get('name'))} ({_shown(gpu.get('vram_gb'), ' GiB')},"
        f" {tr('ドライバ', 'driver')} {_shown(gpu.get('driver'))})",
        f"ComfyUI:    {_shown(data.get('comfyui_version'))}",
        *_db_line(data),
        tr(
            f"失敗の記録: {len(items)} 件(文は入れない。名前と数だけ。見積もりの式には使わない)",
            f"Failures:   {len(items)} (no message text, only names and numbers; not used by the estimate"
            " formula)",
        ),
    ]
    for f in items:
        if not isinstance(f, dict):
            continue
        st = ", ".join(f"{k}={v}" for k, v in (f.get("settings") or {}).items())
        bits = [f"{f.get('model_id')}: {f.get('outcome')}"]
        if st:
            bits.append(st)
        if f.get("missing_nodes"):
            bits.append(tr("足りないノード ", "missing nodes ") + ", ".join(map(str, f["missing_nodes"])))
        if f.get("before_run"):
            bits.append(tr("測る前に分かった", "found before running"))
        for key, ja, en in (("error_type", "種類", "type"), ("node_type", "ノード", "node")):
            if f.get(key):
                bits.append(tr(f"{ja} {f[key]}", f"{en} {f[key]}"))
        if f.get("seconds") is not None:
            bits.append(tr(f"{f['seconds']:g} 秒", f"{f['seconds']:g} s"))
        if f.get("timeout_s") is not None:
            bits.append(tr(f"上限 {f['timeout_s']:g} 秒", f"limit {f['timeout_s']:g} s"))
        if f.get("peak_vram_gb") is not None:
            bits.append(tr(f"最大 VRAM {f['peak_vram_gb']:g} GiB", f"peak VRAM {f['peak_vram_gb']:g} GiB"))
        lines.append("  - " + tr("、", "; ").join(bits))
    if footer:
        lines += [
            "",
            tr(
                "これがファイルの中身のすべてです。genmodelfit はこのファイルをどこにも送りません。",
                "This is everything in the file. genmodelfit does not send this file anywhere.",
            ),
            tr(
                "共有するのは、あなたが GitHub のフォームで「Create」を押したときだけです(CONTRIBUTING.md)。",
                'It is shared only when you press "Create" in the GitHub form (CONTRIBUTING.md).',
            ),
        ]
    return lines


def cmd_contrib_make(args: argparse.Namespace) -> int:
    from .contrib import build_submission, measurements_from_file, suggested_path, write_submission

    try:
        ms = measurements_from_file(args.source)
    except (OSError, ValueError) as e:
        raise CliError(
            tr(
                f"bench の結果を読めません: {args.source}: {e}",
                f"cannot read the bench result: {args.source}: {e}",
            )
        ) from e
    system = None if args.no_pc_info else detect()
    out = []
    target = args.output
    for m in ms:
        sub = build_submission(m, system)
        # 1 件で、ファイル名(.json)を指定したときはそこへ。ほかはフォルダの下に決まった名前で
        dest = target if len(ms) == 1 and target.lower().endswith(".json") else str(Path(target)) + os.sep
        path = write_submission(sub, dest)
        out.append({"saved_to": str(path), "path_in_repo": suggested_path(sub), "submission": sub})
    if args.json:
        _out(_envelope("contrib", None, None, action="make", submissions=out))
        return 0
    for o in out:
        print(
            tr(f"提出用の JSON を書きました: {o['saved_to']}", f"Wrote the submission JSON: {o['saved_to']}")
        )
        print(
            tr(
                f"  PR では {o['path_in_repo']} に置きます。"
                f"送る前に genmodelfit contrib show {o['saved_to']} で確かめてください",
                f"  In the PR it goes to {o['path_in_repo']}."
                f" Check it with genmodelfit contrib show {o['saved_to']} before sharing",
            )
        )
    return 0


def _contrib_inspect(path: str | Path, args: argparse.Namespace) -> tuple[Any, str, list, Any, bool]:
    """contrib show / share の点検: (中身, 文字列, 個人の情報の点検, 検査の結果, 直すところが無いか)。"""
    from .contrib import privacy_checklist, read_submission, validate_submission

    try:
        data, raw = read_submission(path)
    except (OSError, ValueError) as e:
        raise CliError(
            tr(
                f"提出用の JSON を読めません: {path}: {type(e).__name__}",
                f"cannot read the submission JSON: {path}: {type(e).__name__}",
            )
        ) from e
    checks = privacy_checklist(data, raw)
    report = validate_submission(data, _contrib_db(args), path=path, raw=raw, check_filename=False)
    good = report.ok and all(c["ok"] is not False for c in checks)
    return data, raw, checks, report, good


def _print_inspection(data: Any, checks: list, report: Any, good: bool, footer: bool = True) -> None:
    """contrib show の表示(中身のすべて・個人の情報の点検・検査の結果)。share も同じものを出す。"""
    if isinstance(data, dict):
        for line in format_submission(data, footer):
            print(line)
    print()
    print(tr("=== 個人の情報の点検 ===", "=== Privacy checklist ==="))
    for c in checks:
        mark = "[  ]" if c["ok"] is None else ("[ok]" if c["ok"] else "[NG]")
        print(f"  {mark} {c['label']}" + (f": {c['detail']}" if c["detail"] else ""))
    print()
    for e in report.errors:
        print(tr("誤り", "error") + f" {e['code']}: {e['message']}")
    for w in report.warnings:
        print(tr("注意", "warning") + f" {w['code']}: {w['message']}")
    for v in report.notices:
        print(
            tr("確認", "notice")
            + f" {v['code']}: {v['message']}"
            + tr(
                "(失敗にはしません。PR をマージする人が見ます)",
                " (not a failure; the maintainer who merges checks it)",
            )
        )
    nw = len(report.warnings)
    print(
        tr("検査: ", "check: ")
        + ("ok" if good else tr("NG(直してから出してください)", "NG (fix it before submitting)"))
        + (tr(f"(注意 {nw} 件)", f" ({nw} warnings)") if report.warnings else "")
    )


def cmd_contrib_show(args: argparse.Namespace) -> int:
    data, _raw, checks, report, good = _contrib_inspect(args.file, args)
    if args.json:
        _out(
            _envelope(
                "contrib",
                None,
                None,
                action="show",
                file=str(args.file),
                ok=good,
                submission=data,
                checklist=checks,
                report=report.to_dict(),
            )
        )
        return 0 if good else 1
    _print_inspection(data, checks, report, good)
    return 0 if good else 1


def _copy_to_clipboard(text: str) -> bool:
    """文字をクリップボードに写す(#113)。OS の道具だけを使う(新しい依存を足さない): Windows は clip.exe
    (UTF-16 で渡す)、macOS は pbcopy、Linux は wl-copy・xclip・xsel(あるもの)。シェルを通さず、引数に
    中身を入れない(標準入力で渡す)。写せたか。試験では差し替える(本物のクリップボードに触らない)。"""
    import shutil
    import subprocess

    if sys.platform == "win32":
        cands = [(["clip"], ("﻿" + text).encode("utf-16-le"), None)]
    elif sys.platform == "darwin":
        cands = [(["pbcopy"], text.encode("utf-8"), {**os.environ, "LANG": "en_US.UTF-8"})]
    else:
        data = text.encode("utf-8")
        cands = [
            *([(["wl-copy"], data, None)] if os.environ.get("WAYLAND_DISPLAY") else []),
            (["xclip", "-selection", "clipboard"], data, None),
            (["xsel", "--clipboard", "--input"], data, None),
        ]
    for argv, data, env in cands:
        exe = shutil.which(argv[0])
        if not exe:
            continue
        try:
            r = subprocess.run(
                [exe, *argv[1:]],
                input=data,
                capture_output=True,
                timeout=10,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return True
    return False


def _open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:  # ブラウザが無い・開けない環境(URL は出してある)
        return False


def _share(
    path: str | Path,
    args: argparse.Namespace,
    *,
    as_json: bool,
    print_url: bool = False,
    shown: bool = False,
) -> tuple[int, dict[str, Any]]:
    """share・contrib share・bench --share: 点検して、GitHub の実測の Issue のフォーム(中身を入れたもの)を
    ブラウザで開く(#113)。通信しない(開くだけ。送るのは利用者がフォームで「Create」を押したとき)。
    中身が URL に入りきらなければ、クリップボードに写して中身の無いフォームを開く。
    ブラウザを開くのは端末のときだけ(--json・--print-url・端末でないときは URL を出すだけ)。
    URL を渡せたら(開いた・出した)手元で送った印を付ける。(終了コード, JSON に入れる中身)。"""
    from .contrib import share_check, share_urls
    from .measure import mark_shared

    data, raw, checks, report, _good = _contrib_inspect(path, args)
    if isinstance(data, dict):
        why = share_check(checks, report)
    else:
        why = [tr("提出の形ではありません", "not a submission")]
    urls = share_urls(data, raw) if not why else None
    info: dict[str, Any] = {
        "file": str(path),
        "ok": not why,
        "refused": why,
        "checklist": checks,
        "report": report.to_dict(),
        "share": urls,
        "opened": False,
        "copied": False,
        "marked_shared": False,
    }
    if why:
        if not as_json:
            _print_inspection(data, checks, report, False)
            print()
            print(
                tr(
                    "送れません。次を直してから、もう一度 genmodelfit share で確かめてください:",
                    "Cannot share it. Fix the following, then check again with genmodelfit share:",
                ),
                file=sys.stderr,
            )
            for w in why:
                print(f"  - {w}", file=sys.stderr)
        return 1, info
    assert urls is not None and isinstance(data, dict)
    text = raw.replace("\r\n", "\n")
    browse = not as_json and not print_url and _interactive()
    if as_json:
        # --json は見るだけのことがあるので、--mark-sent のときだけ印を付ける
        if getattr(args, "mark_sent", False):
            info["marked_shared"] = bool(mark_shared(data))
        return 0, info
    if print_url:
        print(urls["issue_url"] or urls["form_url"])
        info["marked_shared"] = bool(mark_shared(data))
        return 0, info
    if not shown:  # share がもう見せたときは繰り返さない(#124)
        _print_inspection(data, checks, report, True)
        print()
    print(tr("=== GitHub で送る ===", "=== Share on GitHub ==="))
    if urls["issue_url"]:
        url = urls["issue_url"]
        if browse:
            info["opened"] = _open_browser(url)
        print(
            tr(
                "ブラウザで開いたページ(実測の提出のフォーム。中身は入力済み)で GitHub にログインし、"
                "「Create」を押すと送られます。"
                if info["opened"]
                else "次のページ(実測の提出のフォーム。中身は入力済み)をブラウザで開き、GitHub にログインして"
                "「Create」を押すと送られます:",
                "In the page opened in your browser (the measurement form, already filled in), sign in"
                ' to GitHub and press "Create" to send it.'
                if info["opened"]
                else "Open this page (the measurement form, already filled in) in a browser, sign in"
                ' to GitHub and press "Create" to send it:',
            )
        )
        print(f"  {url}")
    else:
        url = urls["form_url"]
        if browse:
            info["copied"] = _copy_to_clipboard(text)
            info["opened"] = _open_browser(url)
        print(
            tr(
                f"中身が長いので(URL が {urls['issue_url_length']} 字。上限 {urls['url_limit']} 字)、"
                "フォームに貼り付けて送ります。",
                f"The content is long (the URL would be {urls['issue_url_length']} characters; limit"
                f" {urls['url_limit']}), so paste it into the form.",
            )
        )
        print(
            tr(
                "中身はクリップボードに写しました。"
                if info["copied"]
                else "下の 2 つの --- の行の間をそのまま写してください。",
                "The content is on your clipboard."
                if info["copied"]
                else "Copy everything between the two --- lines below.",
            )
        )
        print(
            tr(
                "1. ブラウザで開いたフォームで GitHub にログインする"
                if info["opened"]
                else f"1. 次のフォームをブラウザで開き、GitHub にログインする: {url}",
                "1. Sign in to GitHub in the form opened in your browser"
                if info["opened"]
                else f"1. Open this form in a browser and sign in to GitHub: {url}",
            )
        )
        print(
            tr(
                "2. 「Measurement (JSON)」の欄に貼り付ける",
                '2. Paste it into the "Measurement (JSON)" box',
            )
        )
        print(tr("3. 「Create」を押す", '3. Press "Create"'))
        if not info["copied"]:
            # 写す範囲(--- の行の間)にはファイルの中身だけを置く(パスにはユーザ名が入るので範囲の外に出す)
            print("---")
            print(text, end="" if text.endswith("\n") else "\n")
            print("---")
    print(
        tr(
            "genmodelfit は何も送りません。送られるのは、あなたがフォームで「Create」を押したときだけです。",
            'genmodelfit sends nothing. It is sent only when you press "Create" in the form.',
        )
    )
    if info["opened"]:  # 端末でなく URL を出しただけ(見るだけのこともある)なら印を付けない
        info["marked_shared"] = bool(mark_shared(data))
    return 0, info


def _saved_line(i: int, s) -> str:
    d = s.to_dict()
    if d.get("kind") == "failures":  # 失敗の記録(#132)
        what = tr(f"失敗の記録 {d['count']} 件", f"failure records: {d['count']}")
        bits = [str(d["date"]), what, str(d["gpu"])]
        mark = tr("(送った印あり)", " (marked as sent)") if s.shared else ""
        return f"  {i:>2}. " + "  ".join(bits) + mark
    secs = d["seconds"]
    peak = d["peak_vram_gb"]
    bits = [
        str(d["date"]),
        str(d["model_id"]),
        str(d["gpu"]),
        str(d["outcome"]),
        f"{peak:g} GiB" if isinstance(peak, (int, float)) else "-",
        tr(f"{secs:g} 秒", f"{secs:g} s") if isinstance(secs, (int, float)) else "-",
    ]
    return f"  {i:>2}. " + "  ".join(bits) + (tr("(送った印あり)", " (marked as sent)") if s.shared else "")


def _choose_number(count: int) -> int | None:
    """1〜count の番号を尋ねる(q・空・EOF でやめる → None)。"""
    while True:
        try:
            ans = input(tr(f"番号(1〜{count}。q でやめる): ", f"Number (1-{count}; q to quit): "))
        except EOFError:
            return None
        ans = ans.strip().lower()
        if ans in ("", "q", "quit"):
            return None
        if ans.isdigit() and 1 <= int(ans) <= count:
            return int(ans) - 1
        print(tr("番号で選んでください", "Choose by number"), file=sys.stderr)


def _cmd_share_issue(args: argparse.Namespace, command: str = "share") -> int:
    """share --issue・contrib share(#113): 送っていない実測を選ぶ(または share <file>)→ 中身を見せて
    Issue のフォームを開く(ログインしない・通信しない)。"""
    from .measure import saved_submissions, submissions_dir

    print_url = getattr(args, "print_url", False)
    extra = {"action": "share"} if command == "contrib" else {}
    path = getattr(args, "file", None)
    if not path and print_url and not args.json:
        # --print-url は URL を 1 行だけ出す: 送っていない実測が 1 つだけならそれ、2 つ以上なら選ぶように言う
        saved = saved_submissions(include_shared=getattr(args, "all", False))
        if len(saved) == 1:
            path = saved[0].path
        else:
            for s in saved:
                print(f"  {s.path}", file=sys.stderr)
            raise CliError(
                tr(
                    f"送っていない実測が {len(saved)} 件あります。送るファイルを指定してください:"
                    " genmodelfit share --issue <ファイル> --print-url",
                    f"{len(saved)} unsent measurements. Choose the file to send:"
                    " genmodelfit share --issue <file> --print-url",
                )
                if saved
                else tr(
                    "送っていない実測はありません(genmodelfit measure で測れます)",
                    "No unsent measurements (measure one with genmodelfit measure)",
                )
            )
    if not path:
        saved = saved_submissions(include_shared=getattr(args, "all", False))
        if args.json or print_url or not _interactive():
            if args.json:
                _out(
                    _envelope(
                        command,
                        None,
                        None,
                        **extra,
                        listed=True,
                        folder=str(submissions_dir()),
                        submissions=[s.to_dict() for s in saved],
                    )
                )
                return 0
            for i, s in enumerate(saved, 1):
                print(f"{_saved_line(i, s)}\n      {s.path}")
            print(
                tr(
                    "送るファイルを genmodelfit share --issue <ファイル> で指定してください。"
                    if saved
                    else "送っていない実測はありません(genmodelfit measure で測れます)。",
                    "Give the file to send: genmodelfit share --issue <file>."
                    if saved
                    else "No unsent measurements (measure one with genmodelfit measure).",
                ),
                file=sys.stderr,
            )
            return 0
        if not saved:
            print(
                tr(
                    "送っていない実測はありません。genmodelfit measure で測ると、ここに出ます。",
                    "No unsent measurements. Measure one with genmodelfit measure and it shows up here.",
                )
            )
            return 0
        print(tr("=== 送っていない実測 ===", "=== Unsent measurements ==="))
        for i, s in enumerate(saved, 1):
            print(_saved_line(i, s))
        try:
            k = _choose_number(len(saved))
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        if k is None:
            print(tr("送りませんでした", "Nothing shared"), file=sys.stderr)
            return 0
        path = saved[k].path
        print()
    code, info = _share(path, args, as_json=args.json, print_url=print_url)
    if args.json:
        _out(_envelope(command, None, None, **extra, listed=False, **info))
    return code


# --- share: 測って PR で送る(#124。llmfit の share にならう) -------------------------------------------


DOWNLOAD_EXAMPLE_MBPS = 100  # 落とす時間の例に使う回線の速さ(Mbps)。目安で、約束ではない


def _download_text(nbytes: int) -> str:
    """落とす量と、例の回線での時間(#124): 「落とす量 6.5 GB(100 Mbps で約 9 分。回線しだい)」。"""
    import math

    mins = max(1, math.ceil(nbytes * 8 / (DOWNLOAD_EXAMPLE_MBPS * 1e6) / 60))
    return tr(
        f"落とす量 {nbytes / GIB:.1f} GB({DOWNLOAD_EXAMPLE_MBPS} Mbps で約 {mins} 分。回線しだい)",
        f"{nbytes / GIB:.1f} GB to download (about {mins} min at {DOWNLOAD_EXAMPLE_MBPS} Mbps; depends on"
        " your connection)",
    )


def _download_seconds(nbytes: int) -> float:
    return nbytes * 8 / (DOWNLOAD_EXAMPLE_MBPS * 1e6)


def _share_total_line(start, total: int, dl_plan: list, with_download: bool) -> str:
    """計画の合計の時間の目安(#124): ComfyUI の起動(起動するとき。comfystart.TYPICAL_START_SECONDS)+ 測る
    (時間の目安の合計)+ 落とす(落とすことになっているとき。例の回線の速さで)。"""
    from .comfystart import TYPICAL_START_SECONDS

    secs = float(total)
    ja = [f"測る 約 {total} 秒"]
    en = [f"measuring about {total} s"]
    if start is not None:
        secs += TYPICAL_START_SECONDS
        ja.insert(0, f"ComfyUI の起動 約 {TYPICAL_START_SECONDS} 秒")
        en.insert(0, f"starting ComfyUI about {TYPICAL_START_SECONDS} s")
    if with_download and dl_plan:
        nbytes = sum(x.installed.missing_bytes for x in dl_plan)
        dl_secs = sum(x.seconds for x in dl_plan)
        secs += dl_secs + _download_seconds(nbytes)
        ja.append(f"落としたものを測る 約 {dl_secs} 秒")
        en.append(f"measuring the downloaded ones about {dl_secs} s")
        dl = _download_text(nbytes)  # 表示の言語のもの
        ja.append(dl)
        en.append(dl)
    mins = max(1, round(secs / 60))
    return tr(
        f"合計の目安: 約 {mins} 分(" + "、".join(ja) + ")",
        f"Total (rough): about {mins} min (" + ", ".join(en) + ")",
    )


def _saved_measurements() -> list:
    """この PC に保存した提出(送ったものも、まだのものも)を実測の形にしたもの(#124)。
    測ると役に立つものの提案から、もう測ってある(送る前・送ったがまだ取り込まれていない)ものを外すのに使う(測り直さない)。"""
    from .contrib import to_measurement
    from .measure import saved_submissions

    out = []
    for sub in saved_submissions(include_shared=True):
        if not isinstance(sub.data.get("measurement"), dict):
            continue  # 失敗の記録(#132)は実測でない
        try:
            out.append(to_measurement(sub.data))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _unsent_hint() -> str:
    return tr(
        "測った結果はこの PC に保存してあります。測り直さずに genmodelfit share --no-measure で送れます"
        "(ログインしないなら genmodelfit share --issue)",
        "The measurements are saved on this PC. Send them without measuring again with"
        " genmodelfit share --no-measure (without signing in: genmodelfit share --issue)",
    )


def _share_session():
    """share の通信の session(fetch.make_session。試験で差し替える)。--dry-run・--issue では作らない。"""
    from .fetch import make_session

    return make_session()


def _ask_yes_no(prompt: str, default: bool) -> bool:
    """[Y/n]・[y/N] を尋ねる。空なら default、EOF は「いいえ」。Ctrl+C は KeyboardInterrupt のまま。"""
    try:
        ans = input(prompt)
    except EOFError:
        return False
    a = ans.strip().lower()
    if not a:
        return default
    return a in ("y", "yes")


class _UseIssue(CliError):
    """PR のログインをやめて、同じファイルを Issue(ログインなし)で送る(デバイス認証で q・やり直さない)。"""


class _KeySource:
    """デバイス認証を待つあいだのキー(1 文字。待たずに読む)。with で使い、抜けるときに端末を必ず戻す。
    Windows は msvcrt(kbhit・getwch)、POSIX は端末を cbreak にして select で読む。標準入力が端末でなければ
    何も読まない(read は None)。"""

    def __init__(self) -> None:
        self.enabled = False
        self._fd: int | None = None
        self._old: Any = None

    def __enter__(self) -> _KeySource:
        try:
            if not sys.stdin.isatty():
                return self
        except (AttributeError, ValueError, OSError):
            return self
        if sys.platform == "win32":
            self.enabled = True
            return self
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self.enabled = True
        except (ImportError, OSError, ValueError):
            self._old = None
        return self

    def read(self) -> str | None:
        if not self.enabled:
            return None
        try:
            if sys.platform == "win32":
                import msvcrt

                return msvcrt.getwch().lower() if msvcrt.kbhit() else None
            import select

            ready, _, _ = select.select([sys.stdin], [], [], 0)
            return sys.stdin.read(1).lower() if ready else None
        except (OSError, ValueError):
            return None

    def __exit__(self, *exc: Any) -> None:
        if self._old is not None and self._fd is not None:
            import termios

            with contextlib.suppress(OSError, ValueError):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            self._old = None
        self.enabled = False


def _login_keys() -> _KeySource:
    """待つあいだのキーの読み手(試験で差し替える)。"""
    return _KeySource()


def _login_sleep(seconds: float) -> None:
    """デバイス認証を待つ(試験で差し替える)。"""
    import time

    time.sleep(seconds)


def _login_clock() -> float:
    import time

    return time.monotonic()


def _share_login(args: argparse.Namespace):
    """GitHub の資格情報を決めて確かめる(測る前。#124)。(資格情報, session)。使えなければ CliError。
    トークンは表示しない。デバイス認証のコードと URL は stderr に出し、端末ならブラウザを開く。
    待つあいだはコード・残り時間・キーの 1 行を出し(_LoginView)、キーを待たずに読む(_KeySource): r ブラウザを
    開き直す、n 新しいコード、q ログインをやめて Issue で送る(_UseIssue)、Ctrl+C 止める。コードの期限切れ・
    断られたときは、もう一度ログインするかを尋ね、n なら Issue で送る。測った結果は消さない。"""
    from . import github

    interactive = _interactive() and not args.json

    def on_code(dc) -> None:
        view.close()
        print(
            tr(
                "GitHub にログインします(実測を PR で送るため。権限は public_repo)。",
                "Signing in to GitHub (to send the measurements as a pull request; scope public_repo).",
            ),
            file=sys.stderr,
        )
        print(
            tr(
                f"  1. ブラウザで {dc.verification_uri} を開く",
                f"  1. Open {dc.verification_uri} in a browser",
            ),
            file=sys.stderr,
        )
        print(
            tr(f"  2. コード {dc.user_code} を入れる", f"  2. Enter the code {dc.user_code}"),
            file=sys.stderr,
        )
        print(
            tr(
                "  コードを間違えたら、同じページで入れ直せます(コードは 15 分有効)",
                "  If you mistype the code, enter it again on the same page"
                " (the code is valid for 15 minutes)",
            ),
            file=sys.stderr,
        )
        if _open_browser(dc.verification_uri):
            print(tr("  (ブラウザで開きました)", "  (opened in your browser)"), file=sys.stderr)
        print(
            tr(
                "  キー: r ブラウザを開き直す / n 新しいコード / q やめて Issue で送る / Ctrl+C 止める",
                "  Keys: r open the browser again / n new code / q stop and send as issues / Ctrl+C stop",
            ),
            file=sys.stderr,
            flush=True,
        )

    def on_wait(dc, left: float) -> str | None:
        key = keys.read()
        if key == "r":
            view.close()
            opened = _open_browser(dc.verification_uri)
            print(
                tr(
                    f"  ブラウザで開き直しました: {dc.verification_uri}"
                    if opened
                    else f"  ブラウザを開けません。{dc.verification_uri} を開いてください",
                    f"  Opened again in your browser: {dc.verification_uri}"
                    if opened
                    else f"  Cannot open a browser; open {dc.verification_uri}",
                ),
                file=sys.stderr,
                flush=True,
            )
        elif key == "n":
            view.close()
            return github.WAIT_NEW
        elif key == "q":
            view.close()
            return github.WAIT_STOP
        view.update(dc, left)
        return None

    session = _share_session()
    while True:
        view = _LoginView(enabled=not args.json)
        failed: github.GitHubError | None = None
        try:
            with _login_keys() as keys:
                creds = github.login(
                    session,
                    interactive=interactive,
                    on_code=on_code,
                    log=_progress(args),
                    on_wait=on_wait,
                    sleep=_login_sleep,
                    clock=_login_clock,
                )
        except github.GitHubError as e:
            failed = e
        finally:
            view.close()
        if failed is None:
            break
        if failed.kind == "cancelled":
            raise _UseIssue(tr("ログインをやめました", "Stopped signing in"))
        if failed.kind in ("expired", "denied") and interactive:
            print(tr(f"エラー: {failed}", f"error: {failed}"), file=sys.stderr)
            again = _ask_yes_no(
                tr(
                    "もう一度ログインしますか? [Y/n](n なら Issue で送ります) ",
                    "Sign in again? [Y/n] (n sends them as issues) ",
                ),
                True,
            )
            if again:
                continue
            raise _UseIssue(tr("ログインしませんでした", "Not signed in"))
        raise CliError(str(failed)) from None
    if not args.json:
        print(
            tr(f"GitHub: {creds.login} としてログイン", f"GitHub: signed in as {creds.login}"),
            file=sys.stderr,
        )
    return creds, session


def _share_precheck(args: argparse.Namespace):
    """測る前の資格情報の確かめ(#124)。Issue で送ると決めてある(--issue)なら何もしない。環境変数のトークンか
    保存したトークンがあれば確かめ(環境変数のものが使えなければ止める。保存したものが使えなければ消す)、
    無ければ: 端末なら最後に決める(None)。端末でない・--json で PR で送るなら、ブラウザでログインできないので
    測る前に止める。(資格情報か None, session か None)。"""
    from . import github

    if getattr(args, "issue", False):
        return None, None
    interactive = _interactive() and not args.json
    token_status = github.cached_token_status()
    if github.env_token() is None and token_status in ("expired", "broken"):
        github.clear_cached_token()
        if not args.json:
            print(
                github.token_status_message(token_status)
                + tr("(PR を選んだときにログインします)", " (you sign in if you choose a pull request)"),
                file=sys.stderr,
            )
        token_status = "none"
    has = github.env_token() is not None or token_status == "ok"
    if not has and interactive:
        return None, None
    session = _share_session()
    try:
        creds = github.login(session, interactive=False)  # 確かめるだけ(ログインはしない)
    except github.GitHubError as e:
        if not interactive:
            raise CliError(str(e)) from None
        if e.kind in ("network", "rate_limit", "api"):
            print(
                tr(
                    f"注意: GitHub に繋がりません({e})。"
                    "PR で送るときに失敗するかもしれません。Issue でも送れます",
                    f"warning: cannot reach GitHub ({e}). Sending a pull request may fail; issues work too",
                ),
                file=sys.stderr,
            )
            return None, None
        if e.kind == "invalid_token":
            # 環境変数のトークンが使えない: PR は直さないと送れないが、Issue は選べるので続ける
            print(
                tr(
                    f"注意: {e}。このままでは PR で送れません(最後に Issue を選べます)",
                    f"warning: {e}. Sending a pull request will fail unless it is fixed"
                    " (you can choose issues at the end)",
                ),
                file=sys.stderr,
            )
            return None, None
        if token_status in ("expired", "broken"):
            msg = github.token_status_message(token_status)
        elif token_status == "ok" and not github.token_path().exists():
            msg = tr(
                "保存した GitHub のログインが使えなくなったので消しました",
                "The saved GitHub sign-in no longer works and was removed",
            )
        else:
            msg = None
        if msg:
            print(
                msg + tr("(PR を選んだときにログインします)", " (you sign in if you choose a pull request)"),
                file=sys.stderr,
            )
        return None, None
    if not args.json:
        print(
            tr(f"GitHub: {creds.login} としてログイン", f"GitHub: signed in as {creds.login}"),
            file=sys.stderr,
        )
    return creds, session


def _share_relogin_if_expired(args: argparse.Namespace, creds, session):
    """送る直前にトークンを確かめる(測るあいだに切れることがある)。切れていれば保存したものを消し、端末なら
    ログインし直す。端末でなければ、測った結果は保存してあり --no-measure で送れることを言って止める。"""
    from . import github

    try:
        ok = github.whoami(session, creds.token) is not None
    except github.GitHubError as e:
        raise CliError(str(e)) from None
    if ok:
        return creds, session
    if not creds.source.startswith("env:"):
        github.clear_cached_token()
    if _interactive() and not args.json and not creds.source.startswith("env:"):
        return _share_login(args)
    raise CliError(
        tr(
            "GitHub のログインの期限が切れました。測った結果は保存してあります。"
            "ログインし直してから genmodelfit share --no-measure で送ってください",
            "The GitHub sign-in expired. The measurements are saved; sign in again and send them with"
            " genmodelfit share --no-measure",
        )
    )


def _share_items(paths: list, args: argparse.Namespace) -> list[dict[str, Any]]:
    """送る候補のファイルを点検する(個人の情報の点検と検査。今の share・contrib show と同じ)。"""
    from .contrib import share_check, suggested_path

    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            data, raw, checks, report, _good = _contrib_inspect(p, args)
        except CliError as e:
            out.append({"file": str(p), "ok": False, "refused": [str(e)], "data": None, "path_in_repo": None})
            continue
        if isinstance(data, dict):
            why = share_check(checks, report)
        else:
            why = [tr("提出の形ではありません", "not a submission")]
        out.append(
            {
                "file": str(p),
                "ok": not why,
                "refused": why,
                "data": data,
                "raw": raw,
                "checks": checks,
                "report": report,
                "path_in_repo": suggested_path(data) if isinstance(data, dict) else None,
            }
        )
    return out


def _share_files_json(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"file": it["file"], "path_in_repo": it["path_in_repo"], "ok": it["ok"], "refused": it["refused"]}
        for it in items
    ]


def _share_submit(args: argparse.Namespace, creds, session, items: list[dict[str, Any]]) -> tuple[list, int]:
    """点検を通ったファイルを、GPU ごとに 1 本の PR で送る。送った(か、もうあった)ものに送った印を付ける。
    (PR の結果の一覧, 印を付けた数)。"""
    import hashlib

    from . import github
    from .measure import mark_shared

    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["path_in_repo"].split("/")[2], []).append(it)
    results = []
    marked = 0

    def mark(group, done) -> None:
        nonlocal marked
        for it in group:
            if it["path_in_repo"] in done:
                mark_shared(it["data"])
                marked += 1

    for slug, group in groups.items():
        files = [(it["path_in_repo"], it["raw"].replace("\r\n", "\n")) for it in group]
        key = hashlib.sha256("\n".join(sorted(p for p, _ in files)).encode("utf-8")).hexdigest()[:8]
        gpu = ((group[0]["data"].get("system") or {}).get("gpu") or {}).get("name") or slug
        try:
            res = github.submit(
                session,
                creds,
                files,
                title=github.pr_title(str(gpu), len(files)),
                body=github.pr_body([it["data"] for it in group]),
                # GPU の名前(slug)に / は入らないので、ほかの GPU の PR と紛れない
                branch=f"{github.BRANCH_PREFIX}{slug}/{key}",
                match_prefix=f"{github.BRANCH_PREFIX}{slug}/",
                log=_progress(args),
            )
        except KeyboardInterrupt as e:
            partial = getattr(e, "partial", None)
            touched = {p for r in results for p in r.touched}
            if partial is not None:
                mark(group, set(partial.delivered))
                touched |= set(partial.touched)
                if partial.pr_url:
                    results.append(partial)
            e.results = results  # type: ignore[attr-defined]
            e.touched = touched  # type: ignore[attr-defined]
            raise
        except github.GitHubError as e:
            # 途中まで送れたもの(ほかの GPU の PR・開いている PR に置いたもの)は印を付け、送り直さない
            partial = getattr(e, "partial", None)
            touched = {p for r in results for p in r.touched}
            if partial is not None:
                mark(group, set(partial.delivered))
                touched |= set(partial.touched)
                if partial.pr_url:
                    results.append(partial)
            err = CliError(str(e))
            err.results = results  # type: ignore[attr-defined]
            err.touched = touched  # type: ignore[attr-defined]
            err.marked = marked  # type: ignore[attr-defined]
            raise err from None
        mark(group, set(res.delivered))  # PR がある(か、本家の main にもうある)ものだけに印を付ける
        results.append(res)
    return results, marked


def _print_share_items(items: list[dict[str, Any]]) -> None:
    for it in items:
        print()
        print(f"--- {it['file']}")
        if it["data"] is not None:
            _print_inspection(it["data"], it["checks"], it["report"], it["ok"], footer=False)
    for it in items:
        if not it["ok"]:
            print(tr(f"送れません: {it['file']}", f"Cannot send: {it['file']}"), file=sys.stderr)
            for w in it["refused"]:
                print(f"  - {w}", file=sys.stderr)


def _share_send(
    args: argparse.Namespace,
    paths: list,
    creds,
    session,
    report: dict[str, Any],
    base_code: int = 0,
) -> int:
    """送る中身と点検を見せ、送り方を決めて送る(#124)。端末では「1) PR 2) Issue q) 送らない」を尋ねる
    (--issue なら Issue、--yes・--json・端末でないなら PR)。PR はここでログインする(creds が None なら)。
    PR で送れなければ、端末では同じファイルを Issue で送るかを尋ねる。送れなかったものは保存したまま
    (genmodelfit share --no-measure で測り直さずに送れる)。"""
    from .contrib import REPO

    items = _share_items(paths, args)
    good = [it for it in items if it["ok"]]

    def emit(**more: Any) -> None:
        _out(_envelope("share", None, None, action="send", **report, files=_share_files_json(items), **more))

    if not args.json:
        print()
        _step(args, "show")
        print(tr(f"=== 送る中身({len(items)} 件) ===", f"=== What will be sent ({len(items)}) ==="))
        _print_share_items(items)
    # この PC に保存した提出で送れないもの: 理由を 1 回だけ出し、名前を変えて次から出さない(#124。ずっと
    # 送れないまま毎回失敗しないように)。share <file> で指定したものは今までどおり失敗にする
    saved_dir = _submissions_root()
    retired = []
    for it in items:
        if not it["ok"] and not getattr(args, "file", None) and _under(it["file"], saved_dir):
            dst = _retire_submission(it["file"])
            if dst is not None:
                retired.append(it["file"])
                if not args.json:
                    print(
                        tr(
                            f"送れないので、次から出しません: {dst}",
                            f"It cannot be sent, so it is no longer offered: {dst}",
                        ),
                        file=sys.stderr,
                    )
    if not good:
        if args.json:
            emit(prs=[], marked_shared=0, sent=False, retired=retired)
        elif retired:
            print(tr("送れるものはありません", "Nothing that can be sent"))
        return base_code if retired and len(retired) == len(items) else 1
    code = base_code if len(good) + len(retired) == len(items) else 1
    method = "issue" if getattr(args, "issue", False) else "pr"
    if not getattr(args, "issue", False) and not args.yes and not args.json and _interactive():
        print()
        print(
            tr(
                f"送り先: https://github.com/{REPO}。送ったものは公開されます。",
                f"Where: https://github.com/{REPO}. What you send is public.",
            )
        )
        try:
            method = _ask_method()
        except KeyboardInterrupt:
            print(file=sys.stderr)
            print(_unsent_hint(), file=sys.stderr)
            return 130
        if method is None:
            print(tr("送りませんでした。", "Nothing sent."), file=sys.stderr)
            print(_unsent_hint(), file=sys.stderr)
            return base_code
    _step(args, "send")
    if method == "issue":
        return _share_send_issues(args, good, report, items, code)
    try:
        if creds is None:
            creds, session = _share_login(args)
        else:
            creds, session = _share_relogin_if_expired(args, creds, session)
        results, marked = _share_submit(args, creds, session, good)
    except _UseIssue as e:
        # ログインをやめた(q)・やり直さなかった: 同じファイルを Issue(ログインなし)で送る。
        # 測った結果は保存したまま
        print(tr(f"{e}。Issue で送ります", f"{e}; sending them as issues"), file=sys.stderr)
        return _share_send_issues(args, good, report, items, code)
    except KeyboardInterrupt as e:
        # 送る途中の Ctrl+C: どこまで置いたか・何が残ったか・続け方を言う(#124)
        done_prs = getattr(e, "results", [])
        touched = getattr(e, "touched", set())
        sys.stdout.flush()
        print(file=sys.stderr)
        print(tr("中断しました。", "Interrupted."), file=sys.stderr)
        for r in done_prs:
            if r.pr_url:
                print(tr(f"送れた PR: {r.pr_url}", f"Sent in pull request: {r.pr_url}"), file=sys.stderr)
        if touched:
            print(
                tr(
                    f"PR かブランチに置いたもの: {len(touched)} 件"
                    "(genmodelfit share --no-measure で続けられます)",
                    f"Placed in a pull request or on its branch: {len(touched)}"
                    " (genmodelfit share --no-measure"
                    " continues)",
                ),
                file=sys.stderr,
            )
        print(_unsent_hint(), file=sys.stderr)
        return 130
    except CliError as e:
        done_prs = getattr(e, "results", [])
        touched = getattr(e, "touched", set())
        rest = [it for it in good if it["path_in_repo"] not in touched]
        if args.json:
            emit(
                prs=[r.to_dict() for r in done_prs],
                marked_shared=getattr(e, "marked", 0),
                sent=bool(done_prs),
                error=str(e),
                hint=_unsent_hint(),
            )
            return 1
        print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
        for r in done_prs:
            if r.pr_url:
                print(tr(f"送れた PR: {r.pr_url}", f"Sent in pull request: {r.pr_url}"))
        if len(rest) < len(good):
            print(
                tr(
                    f"{len(good) - len(rest)} 件はもう PR かブランチに置いたので、Issue では送りません"
                    "(genmodelfit share --no-measure で PR を続けられます)",
                    f"{len(good) - len(rest)} file(s) are already in a pull request or on its branch and are"
                    " not sent as issues (genmodelfit share --no-measure continues the pull request)",
                ),
                file=sys.stderr,
            )
        if rest and not args.yes and _interactive():
            try:
                again = _ask_yes_no(
                    tr(
                        "PR で送れませんでした。同じファイルを Issue(ログインなし)で送りますか? [Y/n] ",
                        "Could not send the pull request. Send the same files as issues (no sign-in)? [Y/n] ",
                    ),
                    True,
                )
            except KeyboardInterrupt:
                print(file=sys.stderr)
                again = False
            if again:
                return _share_send_issues(args, rest, report, items, 1)
        print(_unsent_hint(), file=sys.stderr)
        return 1
    if args.json:
        emit(prs=[r.to_dict() for r in results], marked_shared=marked, sent=True, github=creds.to_dict())
        return code
    print()
    print(tr("=== 送りました ===", "=== Sent ==="))
    for r in results:
        if r.pr_url is None:
            print(
                tr(
                    "どのファイルも、もう送ってありました(PR は開いていません)",
                    "All files were already sent (no PR opened)",
                )
            )
            continue
        print(
            tr(
                f"PR: {r.pr_url}" + ("(開いている PR に足しました)" if r.reused else "(新しく開きました)"),
                f"PR: {r.pr_url}" + (" (added to your open PR)" if r.reused else " (opened)"),
            )
        )
        print(
            tr(
                f"  置いたファイル {len(r.uploaded)} 件、もうあったもの {len(r.skipped)} 件",
                f"  {len(r.uploaded)} file(s) added, {len(r.skipped)} already there",
            )
        )
    print(
        tr(
            "検査のワークフローが数分で確かめます。メンテナがマージすると、次の版から見積もりに使われます。",
            "A workflow checks it within minutes; once a maintainer merges it, the next version uses it.",
        )
    )
    return code


def _ask_method() -> str | None:
    """送り方を尋ねる: "pr" / "issue" / None(送らない)。既定は 1(PR)。"""
    while True:
        try:
            ans = input(
                tr(
                    "送り方: 1) PR(GitHub にログイン) 2) Issue(ログインなし。ブラウザでフォームを開く)"
                    " q) 送らない [1]: ",
                    "How to send: 1) pull request (sign in to GitHub) 2) issue (no sign-in; opens the form in"
                    " your browser) q) do not send [1]: ",
                )
            )
        except EOFError:
            return None
        a = ans.strip().lower()
        if a in ("", "1", "pr", "y", "yes"):
            return "pr"
        if a in ("2", "issue"):
            return "issue"
        if a in ("q", "quit", "n", "no"):
            return None
        print(tr("1・2・q で選んでください", "Choose 1, 2 or q"), file=sys.stderr)


def _submissions_root():
    from .measure import submissions_dir

    return submissions_dir()


def _under(path, root) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _retire_submission(path) -> Path | None:
    """送れない提出の名前を <名前>.unsendable に変える(消さない。直せば名前を戻して送れる)。"""
    p = Path(path)
    dst = p.with_name(p.name + ".unsendable")
    try:
        os.replace(p, dst)
    except OSError:
        return None
    return dst


def _share_send_issues(
    args: argparse.Namespace, good: list, report: dict[str, Any], items: list, code: int
) -> int:
    """Issue のフォームで送る(share --issue と同じ _share。1 つのファイルに 1 つの Issue)。端末ならブラウザで
    開き、端末でなければ URL を出す。ログインしない。"""
    print_url = not args.json and not _interactive()
    infos = []
    worst = code
    for it in good:
        c, info = _share(it["file"], args, as_json=args.json, print_url=print_url, shown=True)
        infos.append(info)
        worst = max(worst, c)
    if print_url and any(i.get("marked_shared") for i in infos):
        # URL を出しただけでも送った印を付ける(次の share で PR でも送って二重にしないため)。
        # 実測は消えない
        print(
            tr(
                "上の URL を開いて「Create」を押すと送られます。送った印を付けたので、次の share では"
                "出ません。送らなかったときは genmodelfit share --issue --all で、"
                "測り直さずにもう一度選べます",
                'Open the URL above and press "Create" to send it. It is marked as sent, so the next'
                " share does not list it; if you did not send it, genmodelfit share --issue --all lets"
                " you choose it again without measuring",
            ),
            file=sys.stderr,
        )
    if args.json:
        _out(
            _envelope(
                "share",
                None,
                None,
                action="send",
                **report,
                files=_share_files_json(items),
                method="issue",
                issues=infos,
                prs=[],
                marked_shared=sum(1 for i in infos if i.get("marked_shared")),
                sent=False,
            )
        )
    elif not all(i.get("marked_shared") for i in infos):
        print(_unsent_hint(), file=sys.stderr)
    return worst


def _measure_gpu_stop(s) -> str | None:
    """測れない GPU の理由(#124)。判定に使える GPU(backend が cuda・rocm・xpu・mps。
    models.SystemInfo.best_gpu)が無いときだけ。AMD・Intel・Apple でも測る(0.13.1。bench が会社ごとに
    空きを確かめる: docs/accuracy.md「NVIDIA 以外の GPU の bench」)。"""
    if s.best_gpu is None:
        return tr(
            "この PC に測りに使える GPU が見つかりません(genmodelfit system で確かめてください)",
            "No GPU that can be measured was found on this PC (check with genmodelfit system)",
        )
    return None


def _share_nothing(args: argparse.Namespace, **ctx: Any) -> dict[str, Any]:
    """「送るものはありません」の理由と次にできること(#124・#146)。share が持っている材料だけで決める:
    sugg(提案)・skipped(ノードが足りずに外したもの)・budget・dl_plan(落とす計画)・downloaded・asked
    (落とすかを尋ねたか)・failed・interrupted・measured_here(この PC で測ったワークフローの数を返す関数)。
    {reason(1 つの文), reasons([{code, text}]), next_step(1 行)}。"""
    from .measure import saved_submissions

    sugg = ctx.get("sugg") or []
    skipped = ctx.get("skipped") or []
    budget = float(ctx.get("budget") or 0)
    dl_plan = ctx.get("dl_plan") or []
    failed = ctx.get("failed") or []
    reasons: list[tuple[str, str]] = []
    first: list[str] = []  # 次にできること(先のものほど先に出す)
    later: list[str] = []
    sent_before = bool(saved_submissions(include_shared=True))
    if args.no_measure:
        reasons.append(
            (
                "no_measure",
                tr("測らない指定です(--no-measure)", "--no-measure was given (nothing is measured)"),
            )
        )
        if not sent_before:
            later.append(tr("測ってから送る: genmodelfit share", "Measure and send: genmodelfit share"))
    else:
        missing = {d["model_id"] for d in skipped}
        usable = [x for x in sugg if x.model_id not in missing]
        installed = [x for x in usable if x.installed is not None and x.installed.state == "yes"]
        unknown = [x for x in usable if x.installed is not None and x.installed.state == "unknown"]
        if not any(x.installed is not None and x.installed.state in ("yes", "unknown") for x in sugg):
            # 入っていて測れる提案が無い: この PC で測ったものがあればそれが理由。無ければ、入っていないと言う
            count = ctx.get("measured_here")
            n = count() if callable(count) else 0
            if n:
                reasons.append(
                    (
                        "measured_here",
                        tr(
                            "この PC に入っていて GPU に載るモデルは、この PC でもう測ってあります"
                            f"(この GPU で {n} 件)",
                            "The installed models that fit this GPU are already measured on this PC"
                            f" ({n} on this GPU)",
                        ),
                    )
                )
            else:
                reasons.append(
                    (
                        "no_installed",
                        tr(
                            "この PC に入っていて、GPU に載り測れるモデルがありません",
                            "No installed model that fits this GPU can be measured on this PC",
                        ),
                    )
                )
                if any(x.installed is not None and x.installed.state == "no" for x in usable):
                    later.append(
                        tr(
                            "入っていないモデルを落として測る: genmodelfit share --download",
                            "Download and measure the ones not installed: genmodelfit share --download",
                        )
                    )
        if unknown:
            reasons.append(
                (
                    "installed_unknown",
                    tr(
                        f"{len(unknown)} 件は、ComfyUI の models の場所が分からず、"
                        "入っているか確かめられません",
                        f"{len(unknown)} cannot be checked for installed files (the ComfyUI models folder"
                        " is unknown)",
                    ),
                )
            )
            later.append(
                tr(
                    "ComfyUI の場所を指定する: genmodelfit share --comfyui-dir <ComfyUI のフォルダ>",
                    "Give the ComfyUI folder: genmodelfit share --comfyui-dir <ComfyUI folder>",
                )
            )
        if skipped:
            reasons.append(
                (
                    "missing_nodes",
                    tr(
                        f"{len(skipped)} 件は、この ComfyUI に無いノードが要ります",
                        f"{len(skipped)} need nodes this ComfyUI does not have",
                    ),
                )
            )
            first.append(
                tr(
                    "ComfyUI を更新してから、もう一度 genmodelfit share(無いノードの要るものも測れます)",
                    "Update ComfyUI, then run genmodelfit share again (the ones that need missing nodes can"
                    " then be measured)",
                )
            )
        # 時間の上限: 入っているものがあればその中で、無ければ入っていないものも含めて、どれも収まらないとき
        # (落とすのを断ったうえに、入っているものが上限を超えるなら、両方の理由を出す)
        candidates = installed + unknown or usable
        if candidates and not any(x.seconds <= budget for x in candidates):
            shortest = min(x.seconds for x in candidates)
            reasons.append(
                (
                    "budget",
                    tr(
                        f"時間の上限({budget:g} 秒)に収まるものがありません",
                        f"Nothing fits the time limit ({budget:g} s)",
                    ),
                )
            )
            first.append(
                tr(
                    f"上限を広げる: genmodelfit share --budget {shortest}",
                    f"Widen the limit: genmodelfit share --budget {shortest}",
                )
            )
        if dl_plan and not ctx.get("downloaded"):
            size = sum(x.installed.missing_bytes for x in dl_plan)
            if ctx.get("asked"):
                code = "download_declined"
                text = tr(
                    f"入っていない {len(dl_plan)} 件({size / GIB:.1f} GB)は、落とすのを断りました",
                    f"You declined to download the {len(dl_plan)} that are not installed"
                    f" ({size / GIB:.1f} GB)",
                )
            else:
                code = "not_downloaded"
                text = tr(
                    f"入っていない {len(dl_plan)} 件({size / GIB:.1f} GB)は落としていません"
                    "(--yes・端末でないときは落としません)",
                    f"The {len(dl_plan)} that are not installed ({size / GIB:.1f} GB) were not downloaded"
                    " (--yes or no terminal never downloads)",
                )
            reasons.append((code, text))
            first.append(
                tr(
                    "落として測る: genmodelfit share --download",
                    "Download and measure them: genmodelfit share --download",
                )
            )
        real = [f for f in failed if not f.get("unsendable")]
        unsendable = [f for f in failed if f.get("unsendable")]
        if real:
            reasons.append(
                (
                    "failed",
                    tr(
                        f"測れなかったもの {len(real)} 件(理由は上)",
                        f"{len(real)} could not be measured (see above)",
                    ),
                )
            )
        if unsendable:
            reasons.append(
                (
                    "unsendable",
                    tr(
                        f"{len(unsendable)} 件は測れたが送れません(この PC の実測としては保存しました)",
                        f"{len(unsendable)} were measured but cannot be sent"
                        " (kept as this PC's measurements)",
                    ),
                )
            )
        if ctx.get("interrupted"):
            reasons.append(("interrupted", tr("途中で止めました(Ctrl+C)", "Stopped with Ctrl+C")))
        if getattr(args, "_share_failures_known", False):
            reasons.append(
                (
                    "failures_sent",
                    tr(
                        "測れなかったものの記録は、もう送ってあります(同じものは送り直しません)",
                        "The failure records were already sent (the same ones are not sent again)",
                    ),
                )
            )
        if any(code == "measured_here" for code, _ in reasons):
            later.append(
                tr(
                    "ほかのモデルを測る: genmodelfit measure <名前>",
                    "Measure another model: genmodelfit measure <name>",
                )
            )
    reasons.append(
        (
            "no_unsent",
            tr("送っていない実測はありません", "No unsent measurements")
            + (
                tr(
                    "(送ったものを送り直すなら genmodelfit share --no-measure --all)",
                    " (to send the sent ones again: genmodelfit share --no-measure --all)",
                )
                if sent_before
                else ""
            ),
        )
    )
    if sent_before:
        later.append(
            tr(
                "送ったものを送り直す: genmodelfit share --no-measure --all",
                "Send the sent ones again: genmodelfit share --no-measure --all",
            )
        )
    steps = first + later or [
        tr("測れるものを確かめる: genmodelfit measure", "See what can be measured: genmodelfit measure")
    ]
    summary = (
        tr("送るものはありません。", "Nothing to send. ")
        + tr("、", "; ").join(t for _, t in reasons)
        + tr("。", ".")
    )
    return {
        "reason": summary,
        "reasons": [{"code": c, "text": t} for c, t in reasons],
        "next_step": steps[0],
    }


def _nothing_ctx(args: argparse.Namespace, s, db, sugg: list, skipped: list, budget: float) -> dict[str, Any]:
    """_share_nothing に渡す材料(この PC で測った数は、出すときだけ数える)。"""

    def measured_here() -> int:
        if s is None or db is None:
            return 0
        from .measure import measured_here_count

        return measured_here_count(db, s, extra=_saved_measurements())

    return {"sugg": sugg, "skipped": skipped, "budget": budget, "measured_here": measured_here}


def _print_nothing(nothing: dict[str, Any]) -> None:
    """「送るものはありません」と、理由と次にできること(#146)。"""
    print(tr("送るものはありません", "Nothing to send"))
    print(tr("理由:", "Why:"))
    for r in nothing["reasons"]:
        print(f"  - {r['text']}")
    print(tr(f"次にできること: {nothing['next_step']}", f"Next: {nothing['next_step']}"))


def _share_steps(args: argparse.Namespace, start, plan: list, dl_plan: list, want_dl) -> _Steps:
    """この回で使う手順(#124): GitHub の確認(もうあるトークンを確かめるとき)・ComfyUI を起動・ノードを確かめる
    (起動したとき)・測る・落として測る(落とすと決まっているとき。あとで決まれば足す)・送る中身を見せる・送る。"""
    from . import github

    items: list[tuple[str, str]] = []
    measuring = bool(plan or (dl_plan and want_dl))
    if (
        measuring
        and not getattr(args, "issue", False)
        and (github.env_token() is not None or github.token_path().exists() or args.yes or not _interactive())
    ):
        items.append(("check", tr("GitHub の確認", "Check GitHub")))
    if start is not None and (plan or (dl_plan and want_dl)):
        items.append(("start", tr("ComfyUI を起動", "Start ComfyUI")))
        items.append(("nodes", tr("ノードを確かめる", "Check the nodes")))
    if plan:
        items.append(("measure", tr(f"測る({len(plan)} 件)", f"Measure ({len(plan)})")))
    if dl_plan and want_dl:
        items.append(
            ("download", tr(f"落として測る({len(dl_plan)} 件)", f"Download and measure ({len(dl_plan)})"))
        )
    items.append(("show", tr("送る中身を見せる", "Show what will be sent")))
    items.append(("send", tr("送る", "Send")))
    return _Steps(items, enabled=not args.json)


def _share_screen(
    plan: list,
    dl_plan: list,
    pending: list,
    url,
    found_by,
    budget: float,
    total: int,
    unknown: int,
    start=None,
    skipped: list | None = None,
    asks_download: bool = True,
    downloads: bool = False,
) -> list[str]:
    """share の測る前の画面(measure の「測る前に」と同じ部品: ComfyUI・入っているか・時間の目安)。"""
    lines = [
        tr("=== 測って送る(genmodelfit share) ===", "=== Measure and send (genmodelfit share) ==="),
        tr(
            f"ComfyUI:    {_comfyui_text(url, found_by, start)}",
            f"ComfyUI:    {_comfyui_text(url, found_by, start)}",
        ),
    ]
    if plan:
        lines.append(
            tr(
                f"測るもの(入っているもの。目安の合計 約 {total} 秒 / 上限 {budget:g} 秒):",
                f"To measure (installed; about {total} s in all / limit {budget:g} s):",
            )
        )
        lines += [_suggestion_row(i, x) for i, x in enumerate(plan, 1)]
    else:
        lines.append(
            tr(
                "測るもの: なし(入っていて、時間の上限に収まる提案がありません)",
                "To measure: none (no installed suggestion fits the time limit)",
            )
        )
    if unknown:
        lines.append(
            tr(
                f"入っているか分からない提案が {unknown} 件あります(--comfyui-dir で ComfyUI のフォルダを"
                "指定すると確かめられます)",
                f"{unknown} suggestion(s) could not be checked for files (give your ComfyUI folder with"
                " --comfyui-dir)",
            )
        )
    lines += _skipped_lines(skipped or [])
    if dl_plan:
        dl_text = _download_text(sum(x.installed.missing_bytes for x in dl_plan))
        lines.append(
            tr(
                f"入っていないもの {len(dl_plan)} 件({dl_text})は、落として測ります(--download)",
                f"{len(dl_plan)} not installed ({dl_text}) are downloaded and measured (--download)",
            )
            if downloads
            else tr(
                f"入っていないもの {len(dl_plan)} 件({dl_text})は、測り終えて時間が残れば、"
                "落としてよいかを尋ねます(--download で尋ねずに落とす)",
                f"{len(dl_plan)} not installed ({dl_text}): after measuring, if time is left,"
                " you are asked whether to download them (--download skips the question)",
            )
            if asks_download
            else tr(
                f"入っていないもの {len(dl_plan)} 件({dl_text})は落としません"
                "(--download を付けると、落として測ります)",
                f"{len(dl_plan)} not installed ({dl_text}) are not downloaded (--download"
                " downloads and measures them)",
            )
        )
    if url is not None and (plan or dl_plan):
        # 起動中の(利用者の)ComfyUI で続けて測るので、毎回空ける(起動したものは言わずに空ける)
        lines.append(
            tr(
                "測る前に毎回 ComfyUI のメモリを空けます(読み込み済みのモデルは外れます)",
                "Before each run the ComfyUI's memory is freed (models already loaded are unloaded)",
            )
        )
    if plan or (dl_plan and downloads):
        lines.append(_share_total_line(start, total, dl_plan, downloads))
    lines.append(tr(f"送っていない実測: {len(pending)} 件", f"Unsent measurements: {len(pending)}"))
    lines += [_saved_line(i, x) for i, x in enumerate(pending, 1)]
    lines.append(
        tr(
            "時間は目安です(落とす時間は回線しだい)。測った結果はこの PC に保存し、"
            "送る前に中身を見せて、送り方を尋ねます。",
            "Times are rough guides (downloading depends on your connection). Results are saved on this PC;"
            " before sending, the content is shown and you choose how to send it.",
        )
    )
    return lines


def _node_check(url: str):
    """ComfyUI のノードの確かめ(bench.NodeCheck。試験で差し替える)。"""
    from .bench import NodeCheck

    return NodeCheck(url)


def _share_asks_download(args: argparse.Namespace) -> bool:
    """入っていない提案を落とすかを尋ねるか(端末で、--yes・--json・--download が無いとき)。"""
    return not args.download and not args.yes and not args.json and _interactive()


def _share_replan(sugg: list, budget: float, checker, skipped: list[dict[str, Any]]):
    """測る計画(入っているもの → 時間の残りで入っていないもの)。checker があれば、この ComfyUI に無い
    ノードの要るものを外し(skipped に足す)、その分ほかの提案で埋める。(計画, 目安の合計, 落とす計画)。"""
    from .measure import share_plan

    def skip(x) -> bool:
        if checker is None:
            return False
        miss = checker.missing(x.model_id)
        if miss and all(d["model_id"] != x.model_id for d in skipped):
            skipped.append({"model_id": x.model_id, "name": x.model_name, "missing_nodes": miss})
        return bool(miss)

    plan, total = share_plan(sugg, budget, skip=skip)
    dl_plan, _ = share_plan(sugg, budget - total, state="no", skip=skip)
    return plan, total, dl_plan


def _skipped_lines(skipped: list[dict[str, Any]]) -> list[str]:
    if not skipped:
        return []
    out = [
        tr(
            f"測らないもの {len(skipped)} 件(この ComfyUI に無いノードが要る。ComfyUI を更新すると測れます):",
            f"Not measured: {len(skipped)} (they need nodes this ComfyUI does not have; update ComfyUI to"
            " measure them):",
        )
    ]
    for d in skipped:
        out.append(f"  - {d['name']} [{d['model_id']}]: {', '.join(d['missing_nodes'])}")
    return out


def _share_replan_lines(plan: list, total: int, budget: float, skipped: list) -> list[str]:
    lines = ["", tr("=== 測る計画(ComfyUI を確かめたあと) ===", "=== Plan (after checking ComfyUI) ===")]
    lines += _skipped_lines(skipped)
    if plan:
        lines.append(
            tr(
                f"測るもの(目安の合計 約 {total} 秒 / 上限 {budget:g} 秒):",
                f"To measure (about {total} s in all / limit {budget:g} s):",
            )
        )
        lines += [_suggestion_row(i, x) for i, x in enumerate(plan, 1)]
    else:
        lines.append(tr("測るもの: なし", "To measure: none"))
    return lines


# --- 途中経過の表示(#124。ライブラリは表示しない。ここで rich で描く) ---------------------------------------
# 端末(Console.is_terminal)なら rich.progress で書き直し、端末でない・--json なら決まった時に普通の行を出す。
# 出すのは stderr(stdout は結果・JSON のため)。質問の前には必ず止める(close)。NO_COLOR は rich が守る


class _SafeStream:
    """書けない文字を ? にして書く(cp932 などの古い端末・ファイルで落ちない。L9a)。"""

    def __init__(self, inner):
        self._inner = inner

    def write(self, text):
        try:
            return self._inner.write(text)
        except UnicodeEncodeError:
            enc = getattr(self._inner, "encoding", None) or "ascii"
            return self._inner.write(text.encode(enc, errors="replace").decode(enc, errors="replace"))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _err_console():
    """途中経過を描く rich の Console(stderr)。試験で差し替える。書けない文字は ? にする(L9a)。
    NO_COLOR・TERM=dumb は rich が守る(色を付けない・書き換えない。L9c)。"""
    from rich.console import Console

    return Console(file=_SafeStream(sys.stderr), highlight=False)


def _fit_width(text: str, width: int) -> str:
    """表示の幅(全角は 2 桁。rich の数え方)を width に収める(はみ出すぶんは …)。改行は空白にする。"""
    from rich.cells import cell_len

    text = " ".join(str(text).split())
    if width <= 1 or cell_len(text) <= width:
        return text if width > 1 else ""
    out = ""
    for ch in text:
        if cell_len(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


def _text_column(field: str, ratio: int | None = None):
    from rich.progress import TextColumn
    from rich.table import Column

    return TextColumn(field, table_column=Column(no_wrap=True, overflow="ellipsis", ratio=ratio))


class _View:
    """途中経過の表示のもと。enabled が偽(--json)なら何も出さない。端末でなければ every 秒ごとに 1 行。"""

    every = 10.0

    def __init__(self, enabled: bool = True, console=None, clock=None):
        import time as _time

        self.enabled = enabled
        self.console = console if console is not None else (_err_console() if enabled else None)
        self.live = bool(
            enabled
            and self.console is not None
            and self.console.is_terminal
            and not getattr(self.console, "is_dumb_terminal", False)
        )
        self.clock = clock or _time.monotonic
        self._last_line: float | None = None
        self.progress = None

    def line(self, text: str, *, force: bool = False) -> None:
        """端末でないときの 1 行(every 秒ごと。force なら必ず)。"""
        if not self.enabled or self.live:
            return
        now = self.clock()
        if not force and self._last_line is not None and now - self._last_line < self.every:
            return
        self._last_line = now
        print(text, file=sys.stderr, flush=True)

    def close(self) -> None:
        if self.progress is not None:
            self.progress.stop()
            self.progress = None


class _LoginView(_View):
    """デバイス認証を待つあいだの 1 行(コード・残り時間・キー)。端末でなければ 30 秒ごとに普通の行。"""

    every = 30.0

    def update(self, dc, left: float) -> None:
        mm, ss = divmod(max(0, int(left)), 60)
        text = tr(
            f"ログインを待っています: コード {dc.user_code}・残り {mm:02d}:{ss:02d}"
            "  r ブラウザを開き直す / n 新しいコード / q やめて Issue で送る / Ctrl+C 止める",
            f"Waiting for the sign-in: code {dc.user_code}, {mm:02d}:{ss:02d} left"
            "  r open the browser again / n new code / q stop and send as issues / Ctrl+C stop",
        )
        if self.live:
            if self.progress is None:
                from rich.progress import Progress, SpinnerColumn

                self.progress = Progress(
                    SpinnerColumn(), _text_column("{task.description}"), console=self.console, transient=True
                )
                self.task = self.progress.add_task("", total=None)
                self.progress.start()
            self.progress.update(self.task, description=_fit_width(text, self.console.width - 4))
            self.progress.refresh()
        else:
            self.line(text)


class _StartView(_View):
    """ComfyUI の起動を待つあいだ:
    「ComfyUI を起動しています 23 秒 / 目安 約 45 秒(上限 300 秒)| ログの最後の行」。"""

    def __init__(self, est: int, limit: float, **kw):
        super().__init__(**kw)
        self.est, self.limit = est, limit
        if self.live:
            from rich.progress import Progress, SpinnerColumn

            self.progress = Progress(
                SpinnerColumn(), _text_column("{task.description}"), console=self.console, transient=False
            )
            self.task = self.progress.add_task(self._text(0.0, ""), total=None)
            self.progress.start()

    def _text(self, waited: float, log_line: str) -> str:
        slow = waited > SLOW_FACTOR * self.est
        text = tr(
            f"ComfyUI を起動しています {waited:.0f} 秒"
            + ("(目安より長くかかっています)" if slow else "")
            + f" / 目安 約 {self.est} 秒(上限 {self.limit:g} 秒)",
            f"Starting ComfyUI {waited:.0f} s"
            + (" (taking longer than the guide)" if slow else "")
            + f" / about {self.est} s (limit {self.limit:g} s)",
        )
        return text + (f" | {log_line}" if log_line else "")

    def update(self, waited: float, log_line: str = "") -> None:
        text = self._text(waited, log_line)
        if self.live:
            self.progress.update(self.task, description=_fit_width(text, self.console.width - 4))
            self.progress.refresh()
        else:
            self.line(_fit_width(text, 100))

    def done(self, waited: float) -> None:
        text = tr(f"ComfyUI の準備ができました({waited:.0f} 秒)", f"ComfyUI is ready ({waited:.0f} s)")
        if self.live:
            self.progress.update(self.task, description=text, total=1, completed=1)
        self.close()
        if self.enabled and not self.live:
            self.line(text, force=True)


SLOW_FACTOR = 3.0  # 目安のこの倍を超えたら「目安より長くかかっています」と添える


class _MeasureView(_View):
    """1 件を測るあいだ: 時間の目安を棒にし(超えたら棒を止めて「目安を超えています」)、
    段階・最大 VRAM を出す。"""

    def __init__(self, label: str, est: int, **kw):
        super().__init__(**kw)
        self.label, self.est = label, max(1, int(est or 0))
        self.seen_running = False
        if self.live:
            from rich.progress import BarColumn, Progress, SpinnerColumn

            self.progress = Progress(
                SpinnerColumn(),
                _text_column("{task.description}", ratio=2),
                BarColumn(bar_width=20),
                _text_column("{task.fields[info]}", ratio=3),
                console=self.console,
                transient=False,
            )
            self.task = self.progress.add_task(
                _fit_width(label, max(10, self.console.width // 3)), total=self.est, info=""
            )
            self.progress.start()

    def update(self, info: dict) -> None:
        el = float(info.get("elapsed") or 0.0)
        ph = info.get("phase") or "waiting"
        if ph == "running":
            self.seen_running = True
        name = {
            "queued": tr("キュー待ち", "queued"),
            "running": tr("実行中", "running"),
            "waiting": tr("結果を待つ", "waiting for the result")
            if self.seen_running
            else tr("送った", "sent"),
        }.get(ph, str(ph))
        peak = float(info.get("peak_vram_gb") or 0.0)
        over = el > self.est
        text = tr(
            f"{el:.0f} 秒"
            + ("(目安を超えています)" if over else "")
            + f" / 目安 約 {self.est} 秒 | {name} | 最大 VRAM {peak:.2f} GB",
            f"{el:.0f} s"
            + (" (over the guide)" if over else "")
            + f" / about {self.est} s | {name} | peak VRAM {peak:.2f} GB",
        )
        if el > SLOW_FACTOR * self.est:
            text += tr(
                "。初回はモデルの読み込みで長くなります",
                "; the first run loads the model, which takes longer",
            )
        if self.live:
            if over:
                self.progress.update(self.task, total=None, info=text)  # 棒を止めて「動いている」表示に
            else:
                self.progress.update(self.task, completed=el, info=text)
            self.progress.refresh()
        else:
            self.line(f"  {self.label}: {text}")

    def done(self) -> None:
        if self.live and self.progress is not None:
            self.progress.update(self.task, total=1, completed=1)
        self.close()


def _run_live(args: argparse.Namespace, label: str, est: int):
    """bench の progress を受ける関数と、その表示(close() で止める)。"""
    view = _MeasureView(label, est, enabled=not getattr(args, "json", False))
    return view.update, view


class _DownloadView(_View):
    """download の進み具合(DownloadProgress)。端末なら全体の行とファイルごとの行
    (名前・棒・量・速さ・残り時間)、
    sha256 を確かめるあいだは「確かめています」。端末でなければ、ファイルの始めと全体の 10% ごとに 1 行。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tasks: dict[int, Any] = {}
        self.pct = -1
        self.cur: tuple[int, str] | None = None
        if self.live:
            from rich.progress import (
                BarColumn,
                DownloadColumn,
                Progress,
                TimeRemainingColumn,
                TransferSpeedColumn,
            )

            self.progress = Progress(
                _text_column("{task.description}", ratio=1),
                BarColumn(bar_width=None),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=self.console,
                transient=False,
            )
            self.overall = None
            self.progress.start()

    def _name(self, p) -> str:
        phase = {
            "check": tr("計っています", "checking"),
            "verify": tr("確かめています", "verifying"),
            "restart": tr("最初から", "restarting"),
            "retry": tr("待っています", "waiting"),
        }.get(p.phase, "")
        name = p.name.rsplit("/", 1)[-1]
        head = f"[{p.file_index}/{p.file_count}] " + (f"{phase} " if phase else "")
        room = max(10, (self.console.width if self.live else 100) // 3 - len(head))
        return head + _fit_width(name, room)

    def __call__(self, p) -> None:
        if not self.enabled:
            return
        if self.live:
            self._live(p)
        else:
            self._plain(p)

    def _live(self, p) -> None:
        pr = self.progress
        if p.message:
            pr.console.print(f"  {p.name}: {p.message}")
        if self.overall is None:
            self.overall = pr.add_task(tr("全体", "all"), total=p.total_bytes or None)
        pr.update(self.overall, completed=p.done_bytes)
        t = self.tasks.get(p.file_index)
        if t is None:
            t = self.tasks[p.file_index] = pr.add_task(self._name(p), total=p.file_total or None)
        if p.phase in ("skip", "done"):
            total = p.file_total or p.file_bytes or 1
            pr.update(t, description=self._name(p), total=total, completed=total)
        else:
            pr.update(t, description=self._name(p), completed=p.file_bytes, total=p.file_total or None)
        key = (p.file_index, p.phase)
        if key != self.cur:  # 段階が変わったら(計る→取る→確かめる)すぐ描く
            self.cur = key
            pr.refresh()

    def _plain(self, p) -> None:
        if p.message:
            print(f"  {p.name}: {p.message}", file=sys.stderr, flush=True)
            return
        if p.phase not in ("download", "check", "verify", "done", "skip"):
            return
        pct = int(100 * p.done_bytes / p.total_bytes) if p.total_bytes else 0
        key = (p.file_index, p.phase)
        if key != self.cur or pct // 10 != self.pct // 10:
            self.cur = key
            word = {
                "download": tr("取得", "download"),
                "check": tr("計っています", "checking"),
                "verify": tr("確かめています", "verifying"),
                "done": tr("済み", "done"),
                "skip": tr("もうある", "already there"),
            }[p.phase]
            print(
                f"[{p.file_index}/{p.file_count}] {word} {_fit_width(p.name, 50)}  "
                f"{p.file_bytes / GIB:.2f}/{(p.file_total or 0) / GIB:.2f} GB  "
                + tr(f"(全体 {pct}%)", f"(all {pct}%)"),
                file=sys.stderr,
                flush=True,
            )
        self.pct = pct


class _Steps:
    """share の手順の見出し「== 手順 2/6: ComfyUI を起動 ==」。手順は計画から決め、増えたら言う。"""

    def __init__(self, items: list[tuple[str, str]], *, enabled: bool = True):
        self.items = list(items)  # (鍵, 見出し)
        self.enabled = enabled
        self.done: list[str] = []

    def start(self, key: str, label: str | None = None) -> None:
        keys = [k for k, _ in self.items]
        added = key not in keys
        if added:
            # 次に来る手順(まだ始めていないもの)の前に入れる
            pos = next((i for i, (k, _) in enumerate(self.items) if k not in self.done), len(self.items))
            self.items.insert(pos, (key, label or key))
        if label is not None:
            self.items = [(k, label if k == key else lb) for k, lb in self.items]
        self.done.append(key)
        if not self.enabled:
            return
        i = [k for k, _ in self.items].index(key) + 1
        name = dict(self.items)[key]
        sys.stdout.flush()
        print()
        print(f"== {tr('手順', 'Step')} {i}/{len(self.items)}: {name} ==", flush=True)
        if added:
            print(
                tr(
                    f"(手順が 1 つ増えて {len(self.items)} になりました)",
                    f"(one more step: {len(self.items)} in all)",
                ),
                flush=True,
            )

    def drop(self, key: str) -> None:
        """使わなくなった手順を外す(まだ始めていないものだけ)。"""
        if key not in self.done:
            self.items = [(k, lb) for k, lb in self.items if k != key]


def _step(args: argparse.Namespace, key: str, label: str | None = None) -> None:
    steps = getattr(args, "_steps", None)
    if steps is not None:
        steps.start(key, label)


def _settings_label(settings) -> str:
    """設定の短い言い方(768×768・33 コマ・30 秒)。テンプレートのままなら空。"""
    from .measure import settings_dict

    d = settings_dict(settings) or {}
    bits = []
    if d.get("width") and d.get("height"):
        bits.append(f"{d['width']}×{d['height']}")
    if d.get("frames"):
        bits.append(tr(f"{d['frames']} コマ", f"{d['frames']} frames"))
    if d.get("seconds"):
        bits.append(tr(f"{d['seconds']:g} 秒", f"{d['seconds']:g} s"))
    return " ".join(bits)


def _share_measure_one(
    args: argparse.Namespace,
    url: str,
    x,
    measured: list,
    failed: list,
    pos: tuple[int, int] = (1, 1),
    rest: list | None = None,
    started=None,
) -> None:
    """share の 1 件を測る(measure と同じ _measure_run)。結果は measured か failed に足す。
    pos は (何件目, 全部で何件)、rest はこのあとに測るもの(残りの時間の目安に使う)。"""
    from .measure import settings_dict

    rec = {"model_id": x.model_id, "settings": settings_dict(x.settings)}
    label = " ".join(b for b in (x.model_name, _settings_label(x.settings)) if b)
    what = " ".join([x.model_id, *x.args])
    if not args.json:
        print()
        print(
            tr(
                f"測っています {pos[0]}/{pos[1]}: {label}(目安 約 {x.seconds} 秒)[{what}]",
                f"Measuring {pos[0]}/{pos[1]}: {label} (about {x.seconds} s) [{what}]",
            ),
            flush=True,
        )
    # share は続けて何件も測るので、毎回メモリと前の結果(キャッシュ)を空けてから測る(#124)
    progress, view = _run_live(
        args, tr(f"測る {pos[0]}/{pos[1]}: {label}", f"Measure {pos[0]}/{pos[1]}: {label}"), x.seconds
    )
    _share_cold_load(args, url, x.model_id, started)
    try:
        r = _measure_run(args, url, x.fit.model, x.settings, free=True, progress=progress)
    finally:
        view.done()
    _share_collect(args, r)
    rest = rest or []
    left = (
        tr(
            f" — 残り {len(rest)} 件、あと約 {sum(y.seconds for y in rest)} 秒",
            f" — {len(rest)} left, about {sum(y.seconds for y in rest)} s more",
        )
        if rest
        else ""
    )
    if r["error"] is not None:
        failed.append({**rec, "error": r["error"]})
        crashed = started is not None and started.process.poll() is not None
        if (
            not args.json and not crashed
        ):  # 起動した ComfyUI が止まったなら、そのことはあとで出す(_share_comfy_check)
            sys.stdout.flush()
            print(tr(f"エラー: {r['error']}", f"error: {r['error']}"), file=sys.stderr)
            print(_measure_next_step(r["error"], x.model_id), file=sys.stderr)
            if left:
                print(left.lstrip(" —"), flush=True)
        return
    m = r["measurement"]
    if r["submission"] is None:
        # 測れたが送れない(標準でない条件・失敗・検査に通らない): 黙って落とさず、理由を付けて
        # 「測れなかったもの」に入れる
        res = r["result"]
        if r.get("unsendable"):
            why = tr(
                "測れたが、送れません(この PC の実測としては保存しました): " + " / ".join(r["unsendable"]),
                "measured, but it cannot be sent (kept as this PC's measurement): "
                + " / ".join(r["unsendable"]),
            )
        elif not m.standard:
            why = tr(
                "標準でない条件で測ったので、保存も送りもしません: " + "、".join(res.nonstandard),
                "measured under non-standard conditions, so it is neither saved nor sent: "
                + ", ".join(res.nonstandard),
            )
        else:
            why = tr(
                f"結果が {m.outcome} なので送りません", f"the result is {m.outcome}, so it is not sent"
            ) + (tr(f"({res.failed})", f" ({res.failed})") if res.failed else "")
        failed.append({**rec, "error": why, **({"unsendable": True} if r.get("unsendable") else {})})
        if not args.json:
            for w in res.warnings:
                print(tr(f"注意: {w}", f"warning: {w}"), file=sys.stderr)
            print(tr(f"送れません: {why}", f"Not sent: {why}") + left, flush=True)
        return
    measured.append(
        {
            **rec,
            "outcome": m.outcome,
            "peak_vram_gb": m.peak_vram_gb,
            "seconds": m.seconds,
            "submission": str(r["submission"]) if r["submission"] else None,
        }
    )
    if not args.json:
        for w in r["result"].warnings:
            print(tr(f"注意: {w}", f"warning: {w}"), file=sys.stderr)
        secs = tr(f"、{m.seconds:g} 秒", f", {m.seconds:g} s") if m.seconds is not None else ""
        print(
            tr(
                f"結果: {m.outcome}、最大 VRAM {m.peak_vram_gb:.2f} GB{secs}",
                f"Result: {m.outcome}, peak VRAM {m.peak_vram_gb:.2f} GB{secs}",
            )
            + left,
            flush=True,
        )


def _share_cold_load(args: argparse.Namespace, url: str, model_id: str, started) -> None:
    """これから測るモデルが、この ComfyUI で初めての読み込みか(#132。timing.cold_load)。自分で起動した
    ComfyUI なら初めて測るモデルは true、同じ ComfyUI で 2 回目(/free のあと)は false。利用者が起動していた
    ComfyUI で初めて測るモデルは、前に読み込んだか分からないので None。"""
    key = id(started) if started is not None else url
    st = getattr(args, "_cold_state", None)
    if not isinstance(st, dict) or st.get("key") != key:
        st = {"key": key, "seen": set()}
        args._cold_state = st
    seen = model_id in st["seen"]
    args._cold_load = False if seen else (True if started is not None else None)
    st["seen"].add(model_id)


def _share_collect(args: argparse.Namespace, r: dict[str, Any]) -> None:
    """1 件を測った結果から、失敗の記録と、測った GPU(失敗の記録の system に使う)を覚える(#132)。"""
    rec = r.get("failure")
    if rec:
        if not isinstance(getattr(args, "_share_failures", None), list):
            args._share_failures = []
        args._share_failures.append(rec)
    m = r.get("measurement")
    if m is not None and getattr(m, "gpu_name", None) and getattr(m, "gpu_vram_gb", None):
        args._share_gpu = m
    if r.get("comfyui_version") and not getattr(args, "_share_comfy_version", None):
        args._share_comfy_version = r["comfyui_version"]


def _share_write_failures(
    args: argparse.Namespace, s, url: str | None, checker, skipped: list
) -> Path | None:
    """1 回の share の失敗の記録を、送る前の提出の置き場所に 1 ファイル書く(#132。<GPU>/<日付>-<sha8>-
    failures.json)。材料は測れなかったもの(_share_collect)と、ノードの確かめで外したもの(skipped)。前に
    書いた失敗の記録と同じもの(同じモデル・失敗・ComfyUI の版)は書かない(毎回同じものを送らない)。
    検査・個人の情報の点検に通らなければ書かない。書いた場所(無ければ None)。"""
    from .bench import is_local
    from .contrib import build_failures, db_info, failure_record, write_submission
    from .measure import saved_failure_keys, submissions_dir

    if getattr(args, "_share_failures_written", False):
        return None
    records = list(getattr(args, "_share_failures", None) or [])
    records += [
        failure_record(d["model_id"], "missing_nodes", missing_nodes=d.get("missing_nodes"), before_run=True)
        for d in skipped or []
    ]
    records = [r for r in records if r]
    if not records:
        return None
    m = getattr(args, "_share_gpu", None)
    local = bool(url) and is_local(url)
    system = s if local else None
    if m is not None:
        gpu = {
            "gpu_name": m.gpu_name,
            "gpu_vram_gb": m.gpu_vram_gb,
            "driver": getattr(m, "driver", None),
            "backend": getattr(m, "gpu_backend", None),
            "pci_id": getattr(m, "gpu_pci_id", None),
        }
        version = getattr(m, "comfyui_version", None)
    elif system is not None and system.best_gpu is not None:
        g = system.best_gpu
        gpu = {
            "gpu_name": g.name,
            "gpu_vram_gb": round(g.vram_total_gb, 2),
            "driver": g.driver,
            "backend": g.backend,
            "pci_id": g.pci_id,
        }
        version = None
    else:
        return None  # どの GPU の記録か分からない(ComfyUI が別の PC で、何も測れなかった)
    version = version or getattr(args, "_share_comfy_version", None)
    if not version and checker is not None and hasattr(checker, "comfyui_version"):
        version = checker.comfyui_version()
    data = build_failures(records, system, **gpu, comfyui_version=version, db_info=db_info(_measure_db(args)))
    seen = saved_failure_keys()
    fresh = [f for f in data["failures"] if _failure_key(f, data.get("comfyui_version")) not in seen]
    if not fresh:
        args._share_failures_known = True  # 同じ記録はもう保存・送ってある(#146 の理由)
        return None
    data["failures"] = fresh
    if _submission_refusal(data, args):
        data["failures"] = [f for f in fresh if not _submission_refusal({**data, "failures": [f]}, args)]
        if not data["failures"] or _submission_refusal(data, args):
            return None
    args._share_failures_written = True
    try:
        return write_submission(data, str(submissions_dir()) + os.sep)
    except OSError:
        return None


def _measure_save_failure(
    args: argparse.Namespace, s, url: str | None, run_: dict[str, Any], checker=None
) -> Path | None:
    """measure で測れなかったときの失敗の記録を、送っていない提出として保存する(#132。share と同じ形。
    次の share がいっしょに送る。同じ記録は 2 回書かない)。保存した場所(無ければ None)。"""
    rec = run_.get("failure")
    if not rec:
        return None
    args._share_failures = [rec]
    _share_collect(args, {**run_, "failure": None})
    return _share_write_failures(args, s, url, checker, [])


def _failure_key(f: dict[str, Any], version: Any) -> str:
    from .measure import failure_key

    return failure_key(f, version)


def _share_ask_download(dl_plan: list, *, first: bool) -> bool:
    """入っていない提案を落として測るかを 1 回だけ尋ねる([y/N]。既定は No)。first は、使える入っている
    ものが無く、最初に尋ねるとき。"""
    nbytes = sum(x.installed.missing_bytes for x in dl_plan)
    secs = sum(x.seconds for x in dl_plan)
    mins = max(1, round((secs + _download_seconds(nbytes)) / 60))
    print()
    print(
        tr(
            ("この PC に入っていて測れるモデルはありません。" if first else "時間が残っています。")
            + f"入っていないモデル {len(dl_plan)} 件を落とせば測れます"
            f"({_download_text(nbytes)}、測る時間の目安 約 {secs} 秒。合わせて約 {mins} 分):",
            ("No installed model can be measured on this PC. " if first else "There is time left. ")
            + f"{len(dl_plan)} model(s) that are not installed can be measured after downloading"
            f" ({_download_text(nbytes)}; about {secs} s to measure; about {mins} min in all):",
        )
    )
    for i, x in enumerate(dl_plan, 1):
        print(_suggestion_row(i, x))
        lic = _model_licenses(x)
        if lic:
            print(tr(f"      ライセンス: {lic}", f"      License: {lic}"))
    print(
        tr(
            "y と答えると、この一覧のものだけを落とします"
            "(download と同じ確かめ。モデルごとには尋ね直しません)",
            "Answering y downloads only these (with download's checks; it does not ask again per model)",
        )
    )
    sys.stdout.flush()
    yes = _ask_yes_no(tr("落として測りますか? [y/N] ", "Download and measure them? [y/N] "), False)
    if not yes:
        print(tr("落としません", "Not downloading"), file=sys.stderr)
    return yes


def _model_licenses(x) -> str:
    """提案のモデルのファイルのライセンス(重ならないように並べる)。分からなければ空。"""
    entry = getattr(getattr(x, "fit", None), "model", None)
    files = getattr(entry, "files", None) or []
    names = []
    for f in files:
        lic = getattr(f, "license", None)
        name = (
            tr("不明", "unknown")
            if lic is None
            else (f"other: {f.license_name}" if lic == "other" and getattr(f, "license_name", None) else lic)
        )
        if name not in names:
            names.append(name)
    return ", ".join(names)


def _share_download_and_measure(args: argparse.Namespace, ctx: dict, dl_plan: list, measured, failed) -> None:
    """入っていない提案を、download と同じ流れ(_measure_download。同じ同意・ライセンス・無検閲の決まり)で
    落として測る(#124。同意は呼び出し側が取ってある)。"""
    for i, x in enumerate(dl_plan, 1):
        if not args.json:
            print()
            print(
                tr(
                    f"落とす {i}/{len(dl_plan)}: {x.model_name}",
                    f"Download {i}/{len(dl_plan)}: {x.model_name}",
                )
            )
            sys.stdout.flush()
        code = _measure_download(args, x.model_id, consented=True)
        if code == 130:
            raise KeyboardInterrupt  # 落とす途中の Ctrl+C: 落とすのも測るのも止める(.part は残る)
        if code != 0:
            failed.append(
                {
                    "model_id": x.model_id,
                    "settings": x.to_dict()["settings"],
                    "error": tr("落とすのをやめました", "download declined")
                    if getattr(args, "_download_declined", False)
                    else tr("落とせませんでした", "not downloaded"),
                }
            )
            continue
        _share_measure_one(
            args,
            ctx["url"],
            x,
            measured,
            failed,
            (i, len(dl_plan)),
            dl_plan[i:],
            ctx["holder"][-1] if ctx["holder"] else None,
        )
        if not _share_comfy_check(args, ctx, x, failed):
            break


LOCK_FILE = "measure.lock"  # 測っているあいだの印(キャッシュの中。中身はプロセスの番号)


def _measure_lock():
    """measure・share が同時に測らないようにする(L1c。同じ ComfyUI・同じキャッシュを取り合わない)。
    ほかの genmodelfit が測っていれば、はっきり断る。印を作ったプロセスがもう無ければ(古い印)取り直す。"""
    import psutil

    from .db import cache_dir

    p = cache_dir() / LOCK_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CliError(
            tr(
                f"キャッシュのフォルダを作れません({type(e).__name__}: {p.parent})。"
                "測っても保存できないので測りません"
                "(GENMODELFIT_CACHE_DIR で書ける場所に変えられます)",
                f"Cannot create the cache folder ({type(e).__name__}: {p.parent}). Nothing is measured, "
                "since it"
                " could not be saved (GENMODELFIT_CACHE_DIR changes it to a writable place)",
            )
        ) from None
    for _ in range(3):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            if p.is_dir():
                raise CliError(
                    tr(f"キャッシュの印を作れません({p})", f"Cannot create the lock in the cache ({p})")
                ) from None
            try:
                pid = int(p.read_text(encoding="ascii").strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and psutil.pid_exists(pid):
                raise CliError(
                    tr(
                        f"ほかの genmodelfit(measure か share。プロセス {pid})が測っています。"
                        f"終わってから実行してください(止まっているのに残っているなら {p} を消してください)",
                        f"Another genmodelfit (measure or share, process {pid}) is measuring. Run it after"
                        f" that one finishes (if it is gone but this stays, delete {p})",
                    )
                ) from None
            with contextlib.suppress(OSError):  # 古い印(そのプロセスはもう無い)
                p.unlink()
            continue
        except OSError as e:
            raise CliError(
                tr(
                    f"キャッシュのフォルダに書けません({type(e).__name__}: {p.parent})。"
                    "測っても保存できないので"
                    "測りません(GENMODELFIT_CACHE_DIR で書ける場所に変えられます)",
                    f"Cannot write to the cache folder ({type(e).__name__}: {p.parent}). Nothing is measured,"
                    " since it could not be saved (GENMODELFIT_CACHE_DIR changes it to a writable place)",
                )
            ) from None
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        return p
    return None


def _measure_unlock(p) -> None:
    if p is not None:
        with contextlib.suppress(OSError):
            p.unlink()


def _note_unchecked(args: argparse.Namespace, checker) -> None:
    """ノードを確かめられなかったもの(テンプレートを取れない・回線が無い。L4a)を知らせる(外さずに測る)。"""
    names = list(getattr(checker, "unchecked", []) or [])
    if names and not args.json:
        print(
            tr(
                f"注意: ノードを確かめられなかったもの {len(names)} 件(テンプレートを取れない。"
                f"回線を確かめてください。測るときに分かります): {', '.join(names)}",
                f"warning: the nodes of {len(names)} could not be checked (the template could not be"
                f" fetched; check the connection; measuring will tell): {', '.join(names)}",
            ),
            file=sys.stderr,
        )


def _vram_busy_before_start(gpu=None) -> str | None:
    """自分で ComfyUI を起動する前に、ほかのプログラムが VRAM を多く使っていないか(#124)。
    使っていれば理由。nvidia-smi で読むので NVIDIA の GPU だけ(gpu は判定に使う models.Gpu)。
    AMD・Intel・Apple は起動して /free で空けたあとに bench が ComfyUI の /system_stats で確かめる
    (確かめられなければ note に書く)。"""
    from .bench import others_vram_limit, vram_in_use

    if gpu is not None and (getattr(gpu, "vendor", None) or "nvidia") != "nvidia":
        return None
    v = vram_in_use(getattr(gpu, "name", None))
    if v is None:
        return None
    used, total = v
    limit = others_vram_limit(total)
    if used <= limit:
        return None
    return tr(
        f"ほかのプログラムが VRAM を約 {used / GIB:.1f} GB 使っています(上限 {limit / GIB:.1f} GB)。"
        "閉じてから測ってください(測ると数値が狂います)",
        f"Other programs use about {used / GIB:.1f} GB of VRAM (limit {limit / GIB:.1f} GB)."
        " Close them before"
        " measuring (the numbers would be wrong)",
    )


def _comfy_alive(url: str | None, started) -> bool:
    """ComfyUI がまだ動いているか(自分で起動したものはプロセス、ほかは /system_stats に答えるか)。"""
    from .bench import BenchError, ComfyClient

    if started is not None and started.process.poll() is not None:
        return False
    if not url:
        return False
    try:
        ComfyClient(url, timeout=5).system_stats()
    except BenchError:
        return False
    return True


def _share_comfy_check(args: argparse.Namespace, ctx: dict, x, failed: list) -> bool:
    """1 件を測ったあとで、ComfyUI が止まっていないかを見る(#124)。止まっていたら理由(ログの場所と最後の行)を
    出し、自分で起動したものなら 1 回だけ起動し直して続ける(True)。2 回目・利用者の ComfyUI なら測るのを
    やめる(False。測ったものを送るかに進む)。"""
    from . import comfystart

    if not failed or failed[-1].get("model_id") != x.model_id:
        return True  # 測れた: 調べない(余計な通信をしない)
    started = ctx["holder"][-1] if ctx["holder"] else None
    err = str(failed[-1].get("error") or "")
    exited = started is not None and started.process.poll() is not None
    lost = any(w in err for w in ("接続できません", "Cannot connect", "Cannot talk", "通信できません"))
    if not exited and not (lost and not _comfy_alive(ctx["url"], started)):
        return True  # 止まっていない(測るのに失敗しただけ)
    log = getattr(started, "log", None)
    tail = comfystart.tail(log, 5) if log else []
    msg = tr("ComfyUI が止まりました", "ComfyUI stopped") + (
        tr(f"(ログ: {log})", f" (log: {log})") if log else ""
    )
    failed[-1]["error"] = msg
    if not args.json:
        sys.stdout.flush()
        print(tr(f"エラー: {msg}", f"error: {msg}"), file=sys.stderr)
        for line in tail:
            print(f"  {line}", file=sys.stderr)
    if started is not None and ctx["start"] is not None and ctx["restarts"] == 0:
        ctx["restarts"] += 1
        if not args.json:
            print(tr("ComfyUI を起動し直して続けます", "Starting ComfyUI again to go on"), file=sys.stderr)
        try:
            new = _comfyui_start(args, ctx["start"], ctx["holder"])
        except CliError as e:
            if not args.json:
                print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
        else:
            ctx["url"] = new.url
            return True
    ctx["dead"] = True
    if not args.json:
        print(
            tr(
                "測るのをやめて、測ったものを送るかに進みます",
                "Stopping the measurements; moving on to sending what was measured",
            ),
            file=sys.stderr,
        )
    return False


def _share_dry_run(
    args: argparse.Namespace, paths: list, report: dict[str, Any], screen: list[str] | None = None
) -> int:
    """--dry-run: 計画と送る中身を見せるだけ(測らない・ログインしない・GitHub に通信しない)。"""
    items = _share_items(paths, args)
    if args.json:
        payload = {"action": "send", **report}
        _out(_envelope("share", None, None, **payload, dry_run=True, files=_share_files_json(items)))
        return 0
    if screen:
        _emit(screen)
    _print_share_items(items)
    print()
    print(
        tr(
            "--dry-run: 測っていません・ログインしていません・何も送っていません。",
            "--dry-run: nothing was measured, no sign-in, nothing was sent.",
        )
    )
    return 0


def _share_issue_old(args: argparse.Namespace) -> bool:
    """前からの share --issue(送っていない実測を選んで Issue のフォームを開く。測らない)か。
    --issue --yes は、測ってから Issue で送る(#124)。FILE・--print-url・--json(--yes なし)は前からのまま。"""
    return bool(args.issue) and (not args.yes or bool(args.file) or bool(args.print_url))


def _share_checks(args: argparse.Namespace) -> None:
    """share のフラグの組み合わせ(#124)。"""
    if _share_issue_old(args):
        for on, name in (
            (args.download, "--download"),
            (args.dry_run, "--dry-run"),
            (args.logout, "--logout"),
            (args.no_measure, "--no-measure"),
            (args.budget is not None, "--budget"),
        ):
            if on:
                raise UsageError(
                    tr(f"{name} は --issue といっしょには使えません", f"{name} cannot be used with --issue")
                )
        return
    for on, name in ((args.print_url, "--print-url"), (args.mark_sent, "--mark-sent")):
        if on and not args.issue:
            raise UsageError(
                tr(f"{name} は --issue といっしょのときだけ使えます", f"{name} is only for --issue")
            )
    if args.logout and args.issue:
        raise UsageError(
            tr("--logout は --issue といっしょには使えません", "--logout cannot be used with --issue")
        )
    if args.logout:
        return
    if args.json and args.download:
        raise UsageError(
            tr(
                "--json では落としません(先に genmodelfit download <ID> --yes で落としてください)",
                "--json does not download (download first with genmodelfit download <id> --yes)",
            )
        )
    if not args.yes and not args.dry_run and (args.json or not _interactive()):
        raise UsageError(
            tr(
                "--json・端末でないときは --yes が要ります(測る・送る前に尋ねられないため)",
                "--json or no terminal needs --yes (the questions before measuring and sending cannot be"
                " asked)",
            )
        )


def cmd_share(args: argparse.Namespace) -> int:
    """share。測るために落としたものは、終わったら(Ctrl+C・失敗でも)消すかを尋ねる(#145)。"""
    return _with_delete_offer(args, _cmd_share)


def _cmd_share(args: argparse.Namespace) -> int:
    """share(#124): 入っているモデルを時間の上限まで測り、送っていない実測といっしょに GitHub の PR で送る。
    ComfyUI の見つけ方・入っているかの確かめ・測り方・落とし方は measure と同じ関数を使う。
    --issue は今までの Issue のフォームの形(#113。ログインしない)。share <file> はそのファイルだけを送る。"""
    from . import github
    from .measure import SHARE_BUDGET_SECONDS, saved_submissions, suggestions

    if args.file and (args.no_measure or args.download):
        print(
            tr(
                "genmodelfit share: エラー: --no-measure・--download はファイルといっしょには使えません",
                "genmodelfit share: error: --no-measure and --download cannot be used with a file",
            ),
            file=sys.stderr,
        )
        return 2
    if (
        not args.issue
        and args.json
        and not args.yes
        and not (args.dry_run or args.logout or args.download or args.no_measure or args.budget is not None)
    ):
        # 前からの組み込み(アプリ)のため: --yes の無い share --json は、送っていない実測の一覧、
        # share <file> --json は中身の確かめのまま(測らない・送らない。#113 と同じ)
        return _cmd_share_issue(args)
    _share_checks(args)
    if _share_issue_old(args):
        return _cmd_share_issue(args)
    if args.logout:
        removed = github.clear_cached_token()
        if args.json:
            _out(_envelope("share", None, None, action="logout", removed=removed))
        else:
            print(
                tr("ログインの記録を消しました", "Removed the saved GitHub login")
                if removed
                else tr("消すログインの記録はありません", "No saved GitHub login")
            )
        return 0
    budget = float(args.budget or SHARE_BUDGET_SECONDS)
    if args.file:
        if args.dry_run:
            return _share_dry_run(args, [args.file], {"no_measure": True})
        return _share_send(args, [args.file], None, None, {"no_measure": True})

    s = None
    url = found_by = None
    plan: list = []
    dl_plan: list = []
    total = unknown = 0
    stop: list[str] | None = None
    start = None
    sugg: list = []
    skipped: list[dict[str, Any]] = []
    checker = None
    comfy_stop = False  # 測れない理由が ComfyUI のこと(起動のしかたを案内する)か
    start_why = None
    db_ = None
    if not args.no_measure:
        s, db = _system(args), _db(args)
        db_ = db
        url, found_by, running = _measure_comfyui(args)
        gpu_stop = _measure_gpu_stop(s)
        if gpu_stop:
            stop = [gpu_stop]
        else:
            roots = _measure_roots(args, running)
            # もう測って保存したもの(送る前・送ったが取り込まれる前)は、測り直さない(#124)
            sugg = suggestions(
                db,
                s,
                roots=roots,
                policy=_policy(args),
                extra=_saved_measurements(),
                include_unknown_license=bool(getattr(args, "include_unknown_license", False)),
            )
            if url is not None and not args.dry_run:
                # 起動中の ComfyUI があれば、足りないノードの要るものを先に外す(#124)
                checker = _node_check(url)
            plan, total, dl_plan = _share_replan(sugg, budget, checker, skipped)
            _note_unchecked(args, checker)
            unknown = sum(1 for x in sugg if x.installed is not None and x.installed.state == "unknown")
            start, start_why = _comfyui_start_plan(args, url)
            if url is None and start is None:
                stop = _not_running_lines("", None, start_why)
                comfy_stop = True
    pending = saved_submissions(include_shared=args.all)
    if start is not None:
        args._download_dir = str(start.comfyui_dir)  # 落とす場所は起動する ComfyUI(尋ねない)
    report: dict[str, Any] = {
        "no_measure": bool(args.no_measure),
        "comfyui": _comfy_json(url, found_by, start),
        "budget_seconds": budget,
        "plan": [x.to_dict() for x in plan],
        "download_plan": [x.to_dict() for x in dl_plan],
        "skipped": skipped,
        "pending": [p.to_dict() for p in pending],
    }
    asks_download = _share_asks_download(args)
    screen = _share_screen(
        plan,
        dl_plan,
        pending,
        url,
        found_by,
        budget,
        total,
        unknown,
        start,
        skipped,
        asks_download,
        downloads=bool(args.download),
    )
    if stop is not None and not pending:
        # 測れず、送っていない実測も無い: 計画を見せ、起動の仕方を出して 1(尋ねない・ログインしない。#123)
        if args.json:
            _out(_envelope("share", None, None, action="send", **report, error=stop[0]))
            return 1
        # 測る計画があるときだけ見せる(GPU が無い・ほかのサーバーがいるときは出さない)
        if comfy_stop and plan and not isinstance(start_why, _HeadReason):
            _emit(screen)
        sys.stdout.flush()
        print(tr(f"エラー: {stop[0]}", f"error: {stop[0]}"), file=sys.stderr)
        for line in stop[1:]:
            print(line, file=sys.stderr)
        return 1
    if stop is not None:
        # 測れないが、送っていない実測はある: それだけを送るかに進む
        if not args.json:
            print(tr(f"注意: {stop[0]}", f"warning: {stop[0]}"), file=sys.stderr)
            print(
                tr(
                    f"測らずに、送っていない実測 {len(pending)} 件だけを送るかに進みます"
                    + ("(測るときは ComfyUI を起動してから、もう一度実行してください)" if comfy_stop else ""),
                    f"Not measuring; moving on to sending the {len(pending)} unsent measurement(s)"
                    + (" (to measure, start ComfyUI and run it again)" if comfy_stop else ""),
                ),
                file=sys.stderr,
            )
        plan, dl_plan, report["plan"], report["download_plan"] = [], [], [], []
    if not plan and not dl_plan and skipped and stop is None and not args.dry_run:
        # 測れるものは無いが、ノードが足りないものはある: 失敗の記録だけを送れる(#132)
        report["failures_file"] = _str_or_none(_share_write_failures(args, s, url, checker, skipped))
        pending = saved_submissions(include_shared=args.all)
        report["pending"] = [p.to_dict() for p in pending]
    if not plan and not dl_plan and not pending:
        nothing = _share_nothing(args, **_nothing_ctx(args, s, db_ or None, sugg, skipped, budget))
        if args.json:
            _out(
                _envelope(
                    "share", None, None, action="send", **report, files=[], prs=[], sent=False, **nothing
                )
            )
        else:
            _print_nothing(nothing)
        return 0
    measuring = bool(plan or dl_plan)
    if args.dry_run:
        return _share_dry_run(args, [p.path for p in pending], report, screen if measuring else None)
    if not args.json and measuring:
        _emit(screen)
        sys.stdout.flush()  # 計画を先に(このあとの stderr の途中経過と順が入れ替わらないように)
    # 入っていないものを落とすか: --download なら落とす。
    # 尋ねられない(--yes・--json・端末でない)なら落とさない。
    # 尋ねるのは、使える入っているものが無ければ最初に(ComfyUI を起動する前)、あれば測り終えてから(#124)
    want_dl: bool | None = True if args.download else (None if _share_asks_download(args) else False)
    asked_dl = want_dl is None  # 落とすかを尋ねる(尋ねたか。#146 の理由)
    try:
        if plan and not args.yes:
            go = _ask_yes_no(
                tr(f"{len(plan)} 件を測って送りますか? [Y/n] ", f"Measure {len(plan)} and send? [Y/n] "),
                True,
            )
            if not go:
                print(
                    tr(
                        "何もしませんでした(測らずに送るなら genmodelfit share --no-measure)",
                        "Nothing done (to send without measuring: genmodelfit share --no-measure)",
                    ),
                    file=sys.stderr,
                )
                return 0
        if not plan and dl_plan and want_dl is None:
            want_dl = _share_ask_download(dl_plan, first=True)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    measured: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    interrupted = downloaded = False
    creds = session = None
    args._steps = _share_steps(args, start, plan, dl_plan, want_dl)
    if plan or (dl_plan and want_dl):
        # 測る前は、ある資格情報(環境変数・保存したトークン)を確かめるだけ(使えなければすぐ止める)。
        # ブラウザでのログインは、最後に PR で送ると決めたときだけ(#124)
        if any(k == "check" for k, _ in args._steps.items):
            _step(args, "check")
        creds, session = _share_precheck(args)
        holder: list = []
        lock = _measure_lock()  # 同時に測らない(L1c)
        try:
            if start is not None:
                _step(args, "start")
                try:
                    busy = _vram_busy_before_start(s.best_gpu if s else None)
                    if busy:
                        raise CliError(busy)
                    started = _comfyui_start(args, start, holder)
                except CliError as e:
                    failed.append({"model_id": None, "settings": None, "error": str(e)})
                    plan, dl_plan = [], []
                    if not args.json:
                        print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
                else:
                    url = started.url
                    report["comfyui"]["started"] = started.to_dict()
                    # 起動したので、足りないノードの要るものを外し、時間の残りでほかの提案を足す(#124)
                    _step(args, "nodes")
                    before = [(x.model_id, tuple(x.args)) for x in plan]
                    approved_dl = list(dl_plan)
                    before_dl = [x.model_id for x in dl_plan]
                    checker = _node_check(url)
                    plan, total, dl_plan = _share_replan(sugg, budget, checker, skipped)
                    _note_unchecked(args, checker)
                    # 落とすのは、見せた(同意した)一覧の中だけ。外れたものは理由を言う(足さない)
                    new_ids = {(x.model_id, tuple(x.args)) for x in dl_plan}
                    dl_plan = [x for x in approved_dl if (x.model_id, tuple(x.args)) in new_ids]
                    dropped = [x for x in approved_dl if (x.model_id, tuple(x.args)) not in new_ids]
                    if dropped and not args.json:
                        missing = {d["model_id"] for d in skipped}
                        for x in dropped:
                            why = (
                                tr("ComfyUI に無いノードが要る", "needs nodes this ComfyUI does not have")
                                if x.model_id in missing
                                else tr("時間の上限に収まらない", "does not fit the time limit")
                            )
                            print(
                                tr(
                                    f"落とさないもの: {x.model_name} [{x.model_id}]({why})",
                                    f"Not downloaded: {x.model_name} [{x.model_id}] ({why})",
                                ),
                                file=sys.stderr,
                            )
                    report.update(
                        plan=[x.to_dict() for x in plan],
                        download_plan=[x.to_dict() for x in dl_plan],
                        skipped=skipped,
                    )
                    changed = before != [(x.model_id, tuple(x.args)) for x in plan] or before_dl != [
                        x.model_id for x in dl_plan
                    ]
                    if not args.json and (skipped or changed):
                        _emit(_share_replan_lines(plan, total, budget, skipped))
                        sys.stdout.flush()
                    if not plan and dl_plan and want_dl is None:
                        # 入っているものがどれも使えなかった: いま尋ねる(ComfyUI は起動したまま)
                        want_dl = _share_ask_download(dl_plan, first=True)
            if plan:
                _step(args, "measure", tr(f"測る({len(plan)} 件)", f"Measure ({len(plan)})"))
            else:
                args._steps.drop("measure")
            ctx = {"url": url, "start": start, "holder": holder, "restarts": 0, "dead": False}
            for i, x in enumerate(plan, 1):
                _share_measure_one(
                    args,
                    ctx["url"],
                    x,
                    measured,
                    failed,
                    (i, len(plan)),
                    plan[i:],
                    ctx["holder"][-1] if ctx["holder"] else None,
                )
                if not _share_comfy_check(args, ctx, x, failed):
                    break
            if ctx["dead"]:
                dl_plan = []
            url = ctx["url"]
            if dl_plan and want_dl is None:
                want_dl = _share_ask_download(dl_plan, first=False)
            if dl_plan and want_dl:
                downloaded = True
                _step(
                    args,
                    "download",
                    tr(f"落として測る({len(dl_plan)} 件)", f"Download and measure ({len(dl_plan)})"),
                )
                _share_download_and_measure(args, ctx, dl_plan, measured, failed)
        except KeyboardInterrupt:
            interrupted = True
            print(file=sys.stderr)
            print(
                tr(
                    "止めました。そこまでの実測を送るかに進みます",
                    "Stopped. Moving on to sending what was measured so far",
                ),
                file=sys.stderr,
            )
        finally:
            for st in holder:
                _comfyui_stop(args, st)
            _measure_unlock(lock)
    if failed and not args.json:
        print()
        print(tr(f"測れなかったもの: {len(failed)} 件", f"Not measured: {len(failed)}"))
        for f in failed:
            print(f"  - {f['model_id'] or 'ComfyUI'}: {f['error'].splitlines()[0]}")
    report.update(measured=measured, failed=failed, interrupted=interrupted, downloaded=downloaded)
    if dl_plan and not downloaded and not args.download and not _share_asks_download(args):
        # 尋ねられない(--yes・--json・端末でない)ので落とさなかった: --download で落として測れることを知らせる
        size = sum(x.installed.missing_bytes for x in dl_plan)
        report["download_hint"] = {"count": len(dl_plan), "bytes": size}
        if not args.json:
            print(
                tr(
                    f"入っていないモデル {len(dl_plan)} 件(落とす量 {size / GIB:.1f} GB)は、"
                    "--download を付けると落として測ります(genmodelfit share --download)",
                    f"{len(dl_plan)} model(s) that are not installed ({size / GIB:.1f} GB) can be downloaded"
                    " and measured with --download (genmodelfit share --download)",
                )
            )
    if plan or dl_plan or getattr(args, "_share_failures", None):
        # 失敗の記録(#132): 測れなかったもの(時間切れ・エラー・足りないノード)と、ノードの確かめで外したもの
        written = _share_write_failures(args, s, url, checker, skipped)
        if written is not None:
            report["failures_file"] = str(written)
    # 測れたが送れないだけのもの(オーナーの決まり)は失敗に数えない。Ctrl+C で止めたら 130
    base_code = 130 if interrupted else (1 if any(not f.get("unsendable") for f in failed) else 0)
    pending = saved_submissions(include_shared=args.all)
    if not pending:
        nothing = _share_nothing(
            args,
            **_nothing_ctx(args, s, db_ or None, sugg, skipped, budget),
            dl_plan=dl_plan,
            downloaded=downloaded,
            asked=asked_dl,
            failed=failed,
            interrupted=interrupted,
        )
        if args.json:
            _out(
                _envelope(
                    "share", None, None, action="send", **report, files=[], prs=[], sent=False, **nothing
                )
            )
        else:
            _print_nothing(nothing)
        return base_code
    return _share_send(args, [p.path for p in pending], creds, session, report, base_code)


def _with_delete_offer(args: argparse.Namespace, run) -> int:
    """run(args) を動かし、その中で測るために新しく落としたもの(downloader.DownloadRecord)があれば、
    終わったあとに消すかを扱う(#145)。例外(Ctrl+C・誤り)で止まっても扱う。"""
    from .downloader import DownloadRecord

    record = DownloadRecord()
    args._dl_record = record
    try:
        return run(args)
    except CliError as e:
        if not record or getattr(args, "json", False):
            raise
        # 誤りを先に出してから、消すかを尋ねる(main の表示と同じ形。終了コードも同じ)
        print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
        return 2 if isinstance(e, UsageError) else 1
    finally:
        if record:
            _offer_delete_downloaded(args, record)


def _offer_delete_downloaded(args: argparse.Namespace, record) -> None:
    """この実行で新しく落としたファイル(落とし終えたものと、残った .part・.lock)を消すか(#145)。
    もとからあったファイルは記録に入らない。--delete-downloaded なら尋ねずに消す。端末で --yes・--json が
    無ければ一覧と合計の量を見せて [y/N](既定は消さない)。それ以外(--yes だけ・端末でない)は消さずに
    残したことと消し方を出す。表示は stderr(stdout の結果・JSON に混ぜない)。"""
    files = record.existing()
    if not files:
        return
    total = sum(size for _, size in files)
    size = f"{total / GIB:.2f} GB"
    err = sys.stderr
    if getattr(args, "delete_downloaded", False):
        delete = True
    elif args.yes or getattr(args, "json", False) or not _interactive():
        print(
            tr(
                f"この実行で落としたファイル {len(files)} 件({size})は残しました"
                "(消すなら、次から --delete-downloaded を付ける)",
                f"Kept the {len(files)} file(s) this run downloaded ({size}); add --delete-downloaded next"
                " time to delete them",
            ),
            file=err,
        )
        return
    else:
        sys.stdout.flush()
        print(file=err)
        print(
            tr(
                f"測るためにこの実行で落としたファイル {len(files)} 件(合わせて {size})。"
                "前から入っていたファイルは入れていません:",
                f"Files this run downloaded to measure: {len(files)} ({size} in all). Files that were"
                " already there are not included:",
            ),
            file=err,
        )
        for path, n in files:
            print(f"  - {path}  ({n / GIB:.2f} GB)", file=err)
        err.flush()
        try:
            delete = _ask_yes_no(tr("消しますか? [y/N] ", "Delete them? [y/N] "), False)
        except KeyboardInterrupt:
            print(file=err)
            delete = False
    if not delete:
        print(tr("消さずに残しました", "Kept them"), file=err)
        return
    removed, failed = record.delete()
    freed = sum(size for path, size in files if path in removed)
    print(
        tr(
            f"消しました: {len(removed)} 件({freed / GIB:.2f} GB)",
            f"Deleted: {len(removed)} file(s) ({freed / GIB:.2f} GB)",
        ),
        file=err,
    )
    for path, why in failed:
        print(tr(f"消しませんでした: {path}({why})", f"Not deleted: {path} ({why})"), file=err)


def _str_or_none(p) -> str | None:
    return str(p) if p is not None else None


def cmd_contrib_share(args: argparse.Namespace) -> int:
    # contrib share は前からのスクリプトのため、今までどおり Issue のフォームの形(#124)
    return _cmd_share_issue(args, command="contrib")


def _result_word(r) -> str:
    if not r.ok:
        return "NG"
    return "notice" if r.notices else "ok"


def _validate_markdown(reports: list) -> list[str]:
    """CI のまとめ用の表。提出から来た文字(パス・モデル ID・GPU 名・メッセージ)は、
    すべて md_cell で無害にする。確認(notices)は失敗にしないが、マージする人が見るので表の上に数を出す。"""
    from .contrib import md_cell

    words = [_result_word(r) for r in reports]
    lines = [
        "## genmodelfit contrib validate",
        "",
        tr(
            f"ファイル {len(reports)} 件: ok {words.count('ok')}、確認つきの ok {words.count('notice')}、"
            f"NG {words.count('NG')}",
            f"{len(reports)} files: {words.count('ok')} ok, {words.count('notice')} ok with notices,"
            f" {words.count('NG')} NG",
        ),
        "",
    ]
    if words.count("notice"):
        lines += [
            tr(
                f"**マージする人へ: 確認の項目のある提出が {words.count('notice')} 件あります**"
                "(式から大きく外れる・よくある PC"
                " で段階を変える・一覧に無いモデルなど。失敗にはしていません。"
                "マージすると見積もりに使われます)",
                f"**For the maintainer: {words.count('notice')} submissions have notices**"
                " (far from the formula, changing the fit level on common PCs, a model not in the list, ...;"
                " not failures. Once merged they are used for estimates)",
            ),
            "",
        ]
    lines += [
        tr(
            "| 結果 | ファイル | モデル | GPU | 誤り・確認・注意 |",
            "| Result | File | Model | GPU | Errors / notices / warnings |",
        ),
        "|---|---|---|---|---|",
    ]
    err_w, notice_w, warn_w = tr("誤り", "error"), tr("確認", "notice"), tr("注意", "warning")
    for r, word in zip(reports, words, strict=True):
        msgs = [f"{err_w} {e['code']}: {e['message']}" for e in r.errors]
        msgs += [f"{notice_w} {v['code']}: {v['message']}" for v in r.notices]
        msgs += [f"{warn_w} {w['code']}: {w['message']}" for w in r.warnings]
        text = "<br>".join(md_cell(m) for m in msgs) or "-"
        mark = {"ok": "ok", "notice": tr("ok(**確認**)", "ok (**notice**)"), "NG": "**NG**"}[word]
        lines.append(
            f"| {mark} | {md_cell(r.path)} | {md_cell(r.model_id or '-')} |"
            f" {md_cell(r.gpu_name or '-')} | {text} |"
        )
    return lines


INTAKE_EVENT_MAX_BYTES = (
    1024 * 1024
)  # GitHub の Issue の本文は 65536 字まで。イベントのファイルはそれより大きい


def cmd_contrib_intake(args: argparse.Namespace) -> int:
    """contrib intake(#113。measurement-intake.yml が使う): GitHub の issues のイベントのファイルから Issue の
    本文・番号・書いた人を読み、intake_issue で検査して、結果を --out のフォルダのファイルに書く
    (result.json・comment.md、通れば submission.json・pr_body.md・commit_msg.txt・title.txt・path.txt・
    comment_updated.md、通らなければ comment_pr_closed.md)。
    本文は標準出力に出さない(ワークフローのログにワークフローのコマンドを書かせないため)。通信しない。
    受け付けた・断ったはどちらも 0(結果は result.json の ok)。イベントを読めないときだけ 1。"""
    from .contrib import intake_issue, submitter_count

    try:
        p = Path(args.event)
        if p.stat().st_size > INTAKE_EVENT_MAX_BYTES:
            raise ValueError("too large")
        event = json.loads(p.read_text(encoding="utf-8"))
        issue = event["issue"]
        body = issue.get("body") or ""
        number = int(issue["number"])
        author = str((issue.get("user") or {}).get("login") or "")
        author_id = (issue.get("user") or {}).get("id")  # 数の ID(intake_issue が数字か確かめる)
        if not isinstance(body, str):
            raise ValueError("body")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise CliError(
            tr(
                f"issues のイベントを読めません: {type(e).__name__}",
                f"cannot read the issues event: {type(e).__name__}",
            )
        ) from e
    db = _contrib_db(args)
    # コメントと PR の本文は英語と日本語の両方(Issue を書く人もメンテナもどちらかを読めるように)
    with i18n.forced("en"):
        res = intake_issue(body, author, number, db=db, existing=args.existing, author_id=author_id)
    prior = None
    if res.ok and res.content:
        data = json.loads(res.content)
        prior = submitter_count(
            args.existing,
            author,
            model_id=(data.get("measurement") or {}).get("model_id"),
            gpu_name=((data.get("system") or {}).get("gpu") or {}).get("name"),
            user_id=author_id,
        )
        with i18n.forced("en"):
            res = intake_issue(
                body, author, number, db=db, existing=args.existing, prior=prior, author_id=author_id
            )
    with i18n.forced("ja"):
        ja = intake_issue(
            body, author, number, db=db, existing=args.existing, prior=prior, author_id=author_id
        )
    sep = "\n---\n\n"
    res.comment = f"{res.comment}{sep}{ja.comment}"
    res.comment_updated = f"{res.comment_updated}{sep}{ja.comment_updated}"
    res.comment_pr_closed = f"{res.comment_pr_closed}{sep}{ja.comment_pr_closed}"
    if res.ok:
        res.pr_body = f"{ja.pr_body.replace(f'Closes #{number}', '').rstrip()}\n{sep}{res.pr_body}"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(name: str, text: str) -> None:
        (out / name).write_text(text, encoding="utf-8", newline="\n")

    write("result.json", json.dumps(res.to_dict(), ensure_ascii=False, indent=1) + "\n")
    write("comment.md", res.comment)
    if res.ok and res.content and res.path_in_repo:
        write("comment_updated.md", res.comment_updated)
        write("submission.json", res.content)
        write("pr_body.md", res.pr_body)
        write("commit_msg.txt", res.commit_message)
        write("title.txt", res.title + "\n")
        write("path.txt", res.path_in_repo + "\n")
    else:
        write("comment_pr_closed.md", res.comment_pr_closed)
        write("marker.txt", res.marker + "\n")  # 英語の理由の組の印(同じ理由ならコメントしない)
    if args.json:
        _out(_envelope("contrib", None, None, action="intake", ok=res.ok, reasons=len(res.reasons)))
    else:
        print("accepted" if res.ok else f"refused ({len(res.reasons)} reasons)")
    return 0


def cmd_contrib_validate(args: argparse.Namespace) -> int:
    from .contrib import layout_report, scan_dir, validate_files

    files: list[Path] = []
    misplaced: list[Path] = []
    for f in args.files:
        p = Path(f)
        if p.is_dir():
            ok, ng = scan_dir(p)
            files += ok
            misplaced += ng
        else:
            files.append(p)
    reports = validate_files(files, _contrib_db(args), check_filename=not args.no_filename_check)
    # 置けない場所・形のファイルも誤り(CI の「足したファイルの形」と同じ。#90)
    reports = sorted([*reports, *(layout_report(b) for b in misplaced)], key=lambda r: r.path)
    bad = [r for r in reports if not r.ok or (args.strict and r.warnings)]
    # 提出が 1 件も無いのも失敗(フォルダの指定の間違い・空の PR を通さない。#90)
    code = 1 if bad or not reports else 0
    if args.json:
        _out(
            _envelope(
                "contrib",
                None,
                None,
                action="validate",
                ok=not bad,
                files=len(reports),
                valid=sum(r.ok for r in reports),
                invalid=sum(not r.ok for r in reports),
                notices=sum(1 for r in reports if r.ok and r.notices),
                warnings=sum(len(r.warnings) for r in reports),
                reports=[r.to_dict() for r in reports],
            )
        )
        return code
    if args.markdown:
        for line in _validate_markdown(reports):
            print(line)
        return code
    if not reports:
        print(
            tr(
                "検査する提出がありません(data/contrib のように <GPU>/<日付>-<sha8>.json の入ったフォルダを"
                "渡してください)",
                "no submissions to check"
                " (pass a folder like data/contrib that holds <GPU>/<date>-<sha8>.json)",
            ),
            file=sys.stderr,
        )
    err_w, notice_w, warn_w = tr("誤り", "error"), tr("確認", "notice"), tr("注意", "warning")
    for r in reports:
        what = ", ".join(x for x in (r.model_id, r.gpu_name) if x)
        print(f"{_result_word(r):<6}  {r.path}" + (f"  ({what})" if what else ""))
        for e in r.errors:
            print(f"      {err_w} {e['code']}: {e['message']}")
        for v in r.notices:
            print(f"      {notice_w} {v['code']}: {v['message']}")
        for w in r.warnings:
            print(f"      {warn_w} {w['code']}: {w['message']}")
    words = [_result_word(r) for r in reports]
    print(
        tr(
            f"ファイル {len(reports)} 件: ok {words.count('ok')}、確認つきの ok {words.count('notice')}"
            f"(失敗にはしない。マージする人が見る)、NG {words.count('NG')}",
            f"{len(reports)} files: {words.count('ok')} ok, {words.count('notice')} ok with notices"
            f" (not failures; the maintainer checks them), {words.count('NG')} NG",
        )
    )
    return code


def format_report(rep: dict[str, Any]) -> list[str]:
    """精度の点検の表(Markdown。毎週の自動 PR にそのまま貼れる形)。"""
    from .calibrate import check_table
    from .contrib import CHECK_RAM_GB, md_cell

    thr = rep["threshold_pct"]
    lines = [
        tr("## 見積もりの精度の点検", "## Accuracy check"),
        "",
        tr(
            f"係数を合わせた実測 {rep['n_fit']} 件。閾値: 誤差の絶対値 {thr:g}%"
            "(評価できる種類の leave-one-out と、ほかの利用者の実測の照合で見る)。",
            f"{rep['n_fit']} measurements fitted. Threshold: absolute error {thr:g}%"
            " (leave-one-out for the evaluable types, and the check against community measurements).",
        ),
        "",
        tr(
            "| 種類 | LOO 件数 | LOO 平均 | LOO 最大 | 評価できる | 照合 件数 | 照合 最大 |",
            "| Type | LOO n | LOO mean | LOO max | Evaluable | Check n | Check max |",
        ),
        "|---|---:|---:|---:|---|---:|---:|",
    ]

    def pct(v: Any) -> str:
        return "-" if v is None else f"{v:g}%"

    for mod, b in rep["by_modality"].items():
        lines.append(
            f"| {mod} | {b['loo_n']} | {pct(b['loo_mean_abs_pct'])} | {pct(b['loo_max_abs_pct'])} |"
            f" {tr('はい', 'yes') if b['evaluable'] else tr('いいえ', 'no')} | {b['check_n']} |"
            f" {pct(b['check_max_abs_pct'])} |"
        )
    lines.append("")
    if rep["breach"]:
        lines.append(
            tr(
                f"**警告: 誤差が閾値 {thr:g}% を超えたものがあります**",
                f"**Warning: some errors exceed the {thr:g}% threshold**",
            )
        )
        lines.append("")
        for b in rep["breaches"]:
            gpu_name = md_cell(b.get("gpu_name") or "?")
            lines.append(
                tr(
                    f"- {md_cell(b['model_id'])}({gpu_name}、{b['kind']}、{b.get('origin')}):",
                    f"- {md_cell(b['model_id'])} ({gpu_name}, {b['kind']}, {b.get('origin')}):",
                )
                + f" {b['error_pct']:+g}%"
            )
    else:
        lines.append(
            tr(f"閾値 {thr:g}% を超えたものはありません。", f"Nothing exceeds the {thr:g}% threshold.")
        )
    lines += [
        "",
        tr(
            "### ほかの利用者の実測で変わる段階",
            "### Fit-level changes caused by community data",
        ),
        "",
    ]
    if rep.get("fit_changes"):
        lines += [
            tr("| モデル | GPU | VRAM | メモリ | 前 | 後 |", "| Model | GPU | VRAM | RAM | Before | After |"),
            "|---|---|---:|---:|---|---|",
        ]
        for c in rep["fit_changes"]:
            lines.append(
                f"| {md_cell(c['model_id'])} | {md_cell(c.get('gpu_name') or '?')} | {c['vram_gb']:g} GB |"
                f" {c['ram_gb']:g} GB | {c['before']} | {c['after']} |"
            )
    else:
        ram = ", ".join(f"{v:g}" for v in CHECK_RAM_GB)
        lines.append(
            tr(
                f"ありません(実測と同じ GPU の PC × メモリ {ram}"
                " GB で確かめた。ほかの GPU の PC には使わない)。",
                "None (checked on PCs with the measured GPU ×"
                f" {ram} GB RAM; not used for PCs with other GPUs).",
            )
        )
    if rep.get("check"):
        lines += ["", *check_table(rep["check"])]
    return lines


def _contrib_aggregate_check(args: argparse.Namespace) -> int:
    """aggregate --check: 同梱のまとめに入っていない提出を数える(#90。まとめは版を出すときに作る)。
    提出のフォルダが無ければ誤り(指定の間違いを 0 件として通さない)。今の一覧とファイルの違う行も知らせる。"""
    from .contrib import bundled_status
    from .db import COMMUNITY_FILE, bundled_text

    if args.output or args.report:
        raise CliError(
            tr(
                "--check は --output・--report と一緒に使えません",
                "--check cannot be used with --output / --report",
            )
        )
    if not Path(args.dir).is_dir():
        raise CliError(
            tr(f"提出のフォルダがありません: {args.dir}", f"no such submissions folder: {args.dir}")
        )
    st = bundled_status(args.dir, json.loads(bundled_text(COMMUNITY_FILE)), _contrib_db(args))
    code = 0 if st["ok"] and not st["pending"] and not st["file_sources"] else 1
    if args.json:
        _out(_envelope("contrib", None, None, action="aggregate_check", dir=str(args.dir), **st))
        return code
    if st["file_sources"]:
        print(
            tr(
                f"同梱のまとめに、ファイルごとに数えた出どころが {st['file_sources']} 件ある"
                "(版のまとめは git の履歴で作り直す。docs/release.md)",
                f"The bundled aggregate has {st['file_sources']} source(s) counted per file"
                " (rebuild it from git history for a release; docs/release.md)",
            )
        )
    print(
        tr(
            f"同梱のまとめ: 提出 {st['listed']} 件"
            f"({'それだけをまとめ直すと同じ' if st['matches'] else 'まとめ直すと違う'})",
            f"Bundled aggregate: {st['listed']} submissions"
            + (
                " (re-aggregating them gives the same file)"
                if st["matches"]
                else " (re-aggregating them differs)"
            ),
        )
    )
    for h in st["missing"]:
        print(
            tr(
                f"  {args.dir} に無い(または検査を通らない)提出: {h[:8]}",
                f"  missing from {args.dir} (or failing the checks): {h[:8]}",
            )
        )
    print(
        tr(
            f"{args.dir} の検査を通った提出 {st['valid']} 件のうち、同梱のまとめにまだ入っていないもの"
            f" {len(st['pending'])} 件",
            f"{len(st['pending'])} of the {st['valid']} valid submissions in {args.dir}"
            " are not in the bundled aggregate yet",
        )
    )
    for path in st["pending"]:
        print(f"  {path}")
    if st["stale"]:
        # 一覧のファイルが変わった(上流の差し替え)行は判定に使われない(estimate.files_match)。知らせるだけ
        print(
            tr(
                f"同梱のまとめのうち、測ったときの一覧のファイル(files_fp)が今の一覧と違い、使われない行"
                f" {len(st['stale'])} 件:",
                f"{len(st['stale'])} rows of the bundled aggregate were measured with files (files_fp)"
                " that differ from the current list, so they are not used:",
            )
        )
        for model_id, gpu_name in st["stale"]:
            print(f"  {model_id}  {gpu_name}")
    if st["pending"] or not st["ok"]:
        print(
            tr(
                "版を出すときに genmodelfit contrib aggregate --output"
                " src/genmodelfit/data/measurements_community.json で入れ直します(docs/release.md)",
                "Rebuild it when making a release: genmodelfit contrib aggregate --output"
                " src/genmodelfit/data/measurements_community.json (docs/release.md)",
            )
        )
    return code


def cmd_contrib_aggregate(args: argparse.Namespace) -> int:
    from .contrib import (
        accuracy_report,
        aggregate,
        community_document,
        load_dir,
        md_cell,
        report_db,
        submission_sources,
        write_community,
    )

    if args.check:
        return _contrib_aggregate_check(args)
    if not Path(args.dir).is_dir():
        raise CliError(
            tr(f"提出のフォルダがありません: {args.dir}", f"no such submissions folder: {args.dir}")
        )
    db = _contrib_db(args)
    subs, reports = load_dir(args.dir, db)
    sources, via_git = submission_sources(args.dir, subs)
    rows = aggregate(subs, sources)
    if args.output and not rows and not args.allow_empty:
        raise CliError(
            tr(
                "まとめが空なので書き出しません(同梱のまとめを空で上書きしないため。空にするなら"
                " --allow-empty)",
                "the result is empty, so nothing is written (so as not to overwrite the bundled data with an"
                " empty one; use --allow-empty to write it anyway)",
            )
        )
    file_ids = sorted({s for r in rows for s in r.get("source_ids") or [] if s.startswith("file:")})
    if args.output and file_ids and not args.allow_file_sources:
        # 版を出すまとめは、出どころを git のコミット(PR)で数える。提出のファイルごとに数えると、1 人が何件も
        # 出すと何人分にも数えられ、段階を決められてしまう(#95。docs/release.md)
        raise CliError(
            tr(
                "出どころを git の履歴(浅くない clone)で数えられないので書き出しません"
                f"(ファイルごとの出どころ {len(file_ids)} 件)。版を出すときは main の"
                " 浅くない clone で動かしてください。試験・手元の確かめだけなら --allow-file-sources",
                "sources cannot be counted from git history (a non-shallow clone), so nothing is written"
                f" ({len(file_ids)} per-file source(s)). For a release, run it in a non-shallow"
                " clone of main. For tests or local experiments only, use --allow-file-sources",
            )
        )
    written = str(write_community(rows, args.output, subs, sources)) if args.output else None
    rep = accuracy_report(report_db(db, rows), args.threshold) if args.report else None
    code = EXIT_BREACH if (rep and rep["breach"] and args.fail_on_breach) else 0
    stats = {
        "dir": str(args.dir),
        "files": len(reports),
        "valid": sum(r.ok for r in reports),
        "invalid": sum(not r.ok for r in reports),
        "notices": sum(1 for r in reports if r.ok and r.notices),
        "submissions_used": len(subs),
        "groups": len(rows),
        "consistent": sum(1 for r in rows if r["consistent"]),
        # 出どころ(#90): git = リポジトリに足したコミットごと、files = git の履歴が無いので提出ごと
        "sources_from": "git" if via_git else "files",
    }
    if args.json:
        payload: dict[str, Any] = {"action": "aggregate", **stats, "written_to": written}
        payload["community"] = community_document(rows, subs, sources)
        if rep is not None:
            payload["report"] = rep
        _out(_envelope("contrib", None, None, **payload))
        return code
    print(
        tr(
            f"提出 {stats['files']} 件(検査を通った {stats['valid']}"
            f" 件、通らなかった {stats['invalid']} 件)→ "
            f"{stats['groups']} 組にまとめた(そろった組"
            f" {stats['consistent']}。ok の組は 1 件から見積もりに使う)",
            f"{stats['files']} submissions ({stats['valid']}"
            f" passed the checks, {stats['invalid']} failed) -> "
            f"{stats['groups']} groups ({stats['consistent']} consistent; ok groups are used for estimates"
            " from one submission)",
        )
    )
    if not via_git:
        print(
            tr(
                "git の履歴(浅くない clone)が無いので、出どころは提出ごとに数えた(同じ人の提出も別々)",
                "No git history (a non-shallow clone), so each submission was counted as its own source"
                " (even submissions from the same person)",
            )
        )
    for r in rows:
        if r["outcome"] != "ok":
            mark = tr("oom だけ", "oom only")
        else:
            mark = tr("そろった", "consistent") if r["consistent"] else tr("使う", "used")
        counts = (
            f"n={r['n_submissions']}, used={r['n_used']}, sources={r['n_sources']},"
            f" outliers={r['n_outliers']},"
            f" oom={r['n_oom']}, error={r['n_error']}, "
        )
        spread = tr(f"ばらつき {r['spread_rel_pct']:g}%", f"spread {r['spread_rel_pct']:g}%")
        print(
            f"  {md_cell(r['model_id'])}  {md_cell(r['gpu_name'])}  {r['peak_vram_gb']:g} GiB  "
            + tr(f"({counts}{spread}、{mark})", f"({counts}{spread}, {mark})")
        )
    if written:
        print(tr(f"書き出し: {written}", f"written: {written}"))
    if rep is not None:
        print()
        for line in format_report(rep):
            print(line)
    return code


def cmd_gate(args: argparse.Namespace) -> int:
    from .gate import CHECK_IDS, evaluate_offline, summarize

    db = _db(args)
    policy = _policy(args)
    models = [m for m in db.models if not args.modality or m.modality == args.modality]
    results = [evaluate_offline(m, policy, sources=db.sources) for m in models]
    summary = summarize(results)
    shown = results
    if args.only_auto:
        shown = [r for r in results if r.auto]
    elif args.only_held:
        shown = [r for r in results if not r.auto]
    target = results[0].comfyui_version if results else (policy.comfyui_version or None)
    if args.json:
        _out(
            _envelope(
                "gate",
                None,
                db,
                policy=policy.to_dict() | {"comfyui_version": target},
                filters={"modality": args.modality, "only_auto": args.only_auto, "only_held": args.only_held},
                summary=summary,
                models=[{"id": r.model_id} | r.to_dict() for r in shown],
            )
        )
        return 0
    if i18n.get_language() == "ja":
        print(f"=== 自動のおすすめの条件(ComfyUI {target or '?'}) ===")
        print(f"モデル {summary['models']} 件: 出せる {summary['auto']} / 保留 {summary['held']}")
        print("条件                落ちた数  最初に落ちた数")
    else:
        print(f"=== Auto-recommend gate (ComfyUI {target or '?'}) ===")
        print(f"models {summary['models']}: auto {summary['auto']} / held {summary['held']}")
        print("check               failing  first-failing")
    for cid in CHECK_IDS:
        print(f"  {cid:<18}{summary['failing'][cid]:>8}  {summary['first_failing'][cid]:>13}")
    if args.only_auto or args.only_held:
        print()
        for r in shown:
            state = tr("出せる", "auto") if r.auto else tr("保留: ", "held:") + r.held_by[0]
            print(f"  {r.model_id}  {state}")
    return 0


# --- review(#42) ---------------------------------------------------------------------------------


def _review_error(args: argparse.Namespace, e: Exception) -> int:
    if args.json:
        _out({"genmodelfit": __version__, "command": "review", "error": str(e)})
    else:
        print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
    return 1


def cmd_review_list(args: argparse.Namespace) -> int:
    from .review import review_list

    db = _db(args)
    items = review_list(db, include_ok=args.all)
    if args.json:
        _out(
            _envelope("review", None, db, pending=sum(1 for i in items if i["status"] != "ok"), models=items)
        )
        return 0
    pending = [i for i in items if i["status"] != "ok"]
    print(
        tr(
            f"確認待ち {len(pending)} 件 / 一覧 {len(db.models)} 件({_SOURCE_JA.get(db.origin, db.origin)})",
            f"pending {len(pending)} / {len(db.models)} entries in the list ({db.origin})",
        )
    )
    for i in items:
        why = ", ".join(i["reasons"]) or "-"
        print(f"  [{i['status']}] {i['id']}  ({i['modality']}/{i['content']}/{i['license']})  {why}")
        if i["status"] == "ok" and i["decided_by"]:
            print(f"      {i['decided_by']} {i['decided_at'] or ''}  {' '.join(i['evidence'])}".rstrip())
    return 0


def cmd_review_approve(args: argparse.Namespace) -> int:
    from .review import ReviewError, approve_file

    try:
        m = approve_file(args.db, args.id, args.evidence or [], note=args.note, by=args.by)
    except ReviewError as e:
        return _review_error(args, e)
    r = m.review
    assert r is not None
    if args.json:
        _out(
            {"genmodelfit": __version__, "command": "review", "action": "approve", "id": m.id, "db": args.db}
            | {
                "review": {
                    k: getattr(r, k)
                    for k in ("status", "reasons", "decided_by", "decided_at", "evidence", "note")
                }
            }
        )
        return 0
    reasons = ", ".join(r.reasons) or "-"
    print(
        tr(f"確認済みにしました: {m.id}({reasons})→ {args.db}", f"Approved: {m.id} ({reasons}) -> {args.db}")
    )
    return 0


def cmd_review_reject(args: argparse.Namespace) -> int:
    from .review import ReviewError, reject_file

    try:
        res = reject_file(args.db, args.id, args.note, rejected_path=args.rejected, hide_name=args.hide_name)
    except ReviewError as e:
        return _review_error(args, e)
    if args.json:
        _out({"genmodelfit": __version__, "command": "review", "action": "reject", "id": args.id, **res})
        return 0
    print(
        tr(
            f"一覧から外しました: {args.id} → {res['db']}(外した記録:"
            f" {res['rejected_file']}、鍵 {res['key']})",
            f"Removed from the list: {args.id} -> {res['db']}"
            f" (record: {res['rejected_file']}, key {res['key']})",
        )
    )
    return 0


# --- download(#31) -------------------------------------------------------------------------------


def _gb(n: int | None) -> str:
    return f"{n / GIB:.2f} GB" if n is not None else "?"


# --comfyui-dir の既定(#75。docs/download.ja.md「コマンドライン」)
COMFYUI_DIR_ENV = "GENMODELFIT_COMFYUI_DIR"


def _is_windows() -> bool:
    """案内に出すシェルの形を決める OS(試験で差し替える)。Windows は PowerShell、ほかは POSIX sh。"""
    return os.name == "nt"


def _asks_phrase(asks: list[str]) -> str:
    """尋ねる同意の種類 → 「このモデルは…」に続く文(#75)。"""
    unc, lic = "uncensored" in asks, "unknown_license" in asks
    if unc and lic:
        return tr(
            "無検閲(成人向けを含みうる)で、ライセンスの記載が無いファイルを含みます"
            "(上の確かめる場所で条件を確かめてください)",
            "is uncensored (may include adult content) and has files with no stated license"
            " (check the pages above)",
        )
    if unc:
        return tr("無検閲(成人向けを含みうる)です", "is uncensored (may include adult content)")
    return tr(
        "ライセンスの記載が無いファイルを含みます(上の確かめる場所で条件を確かめてください)",
        "has files with no stated license (check the pages above)",
    )


def _consent_line(plan, asks: list[str]) -> str:
    """端末で尋ねる同意を 1 行にまとめる(#75。無検閲・ライセンスの記載が無いファイル)。"""
    parts_ja, parts_en = [], []
    if "uncensored" in asks:
        parts_ja.append("無検閲(成人向けを含みうる)")
        parts_en.append("uncensored (may include adult content)")
    if "unknown_license" in asks:
        names = ", ".join(f.name for f in plan.files if f.license is None)
        parts_ja.append(f"ライセンスの記載が無い: {names}(上の確かめる場所で確かめる)")
        parts_en.append(f"no stated license: {names} (check the pages above)")
    return tr(
        "  同意:      " + "・".join(parts_ja) + "— 落とす前に y/N で尋ねます",
        "  consent:   " + "; ".join(parts_en) + " — asked y/N before downloading",
    )


def blocked_texts(plan) -> list[str]:
    """止める理由を CLI の言い方で(同意の 2 つは CLI の指定を書く。ほかはライブラリの文のまま。#75)。"""
    out = []
    codes = list(plan.blocked_codes) + [""] * (len(plan.blocked) - len(plan.blocked_codes))
    for text, code in zip(plan.blocked, codes, strict=False):
        if code == "uncensored":
            text = tr(
                "無検閲のモデルです。落とすなら --uncensored(端末なら y/N の質問で尋ねます)",
                "This is an uncensored model. To download it, pass --uncensored (in a terminal you are asked"
                " y/N instead)",
            )
        elif code == "unknown_license":
            names = ", ".join(f.name for f in plan.files if f.license is None)
            text = tr(
                f"ライセンスが分からないファイルがあります: {names}。"
                "中身の条件を自分で確かめたうえで落とすなら --any-license(端末なら y/N の質問で尋ねます)",
                f"Some files have an unknown license: {names}. To download them after checking the terms"
                " yourself, pass --any-license (in a terminal you are asked y/N instead)",
            )
        out.append(text)
    return out


def format_plan(plan, *, consent: bool = False, dest=None, asks: list[str] | None = None) -> list[str]:
    """download --list と同意の画面の文(人が読む形。幅は _emit で 100 桁までに折り返す)。

    dest: 置き場所をどう決めたか(_Destination。None = 知らせない。#111)。
    asks: 端末の質問で尋ねる同意の種類(uncensored / unknown_license。#75)。"""
    from .downloader import UNKNOWN_WARNING_EN, UNKNOWN_WARNING_JA, license_label

    ja = i18n.get_language() == "ja"
    # 見出し(#76。日本語は全角の幅で値の頭をそろえる。英語は前からの形のまま)
    L = (lambda j, e: _ja_label(j, 14)) if ja else (lambda j, e: f"  {e + ':':<10} ")
    out = [f"{plan.model_name}  [{plan.model_id}]"]
    out.append(
        L("ワークフロー", "workflow")
        + tr(
            f"{plan.name}({plan.modality}、{plan.content}、精度 {plan.precision or '?'})",
            f"{plan.name}  ({plan.modality}, {plan.content}, precision {plan.precision or '?'})",
        )
    )
    out.append(
        L("この PC", "this PC")
        + tr(
            f"{plan.fit_level}({plan.run_mode_label})。{plan.chosen}",
            f"{plan.fit_level} ({plan.run_mode_label}); {plan.chosen}",
        )
    )
    out += _destination_lines(plan, dest, L)
    out.append(tr(f"  ファイル({len(plan.files)} 件。GB = GiB):", f"  files ({len(plan.files)}; GB = GiB):"))
    lic_label = tr("        ライセンス: ", "        license: ")
    for f in plan.files:
        state = {
            "download": "",
            "resume": tr(f"  {_gb(f.part_bytes)} から続きを落とす", f"  resume from {_gb(f.part_bytes)}"),
            "present": tr("  既にある", "  already there"),
        }
        mark = state.get(f.status, f"  {f.status.upper()}")
        out.append(f"    - {f.relpath or f.name}  {_gb(f.size_bytes)}{mark}")
        # 置き場所が分からない(other/)ことは、パスと warnings で知らせる
        if f.directory_basis:
            out.append(_basis_text(f))
        lic = license_label(f)
        if f.license is None:
            lic += "" if ja else " (UNKNOWN)"  # 日本語は license_label が「不明」と書く
        elif not f.license_allowed:
            lic += tr("(許可リストに無い: 条件を読む)", " (not on the allowlist: read the terms)")
        if f.license_url:
            out.append(f"{lic_label}{lic}  {f.license_url}")
        else:
            missing = (
                tr("ライセンスの記載がありません", "no license stated")
                if f.license is None
                else tr("一覧にライセンスの原文の URL がありません", "no license URL in the list")
            )
            out.append(f"{lic_label}{lic}{tr('。', '; ')}{missing}")
            if (
                f.check_url
                and not plan.model_id.startswith("hf:")
                and (f.url or "").startswith(f.check_url + "/")
            ):
                # ファイルを置いているリポジトリ(公式テンプレートの Comfy-Org など)。作者とは限らない
                out.append(
                    tr(
                        "        確かめる場所(ファイルの置き場所のリポジトリ。作者とは限らない): "
                        + f.check_url,
                        "        check (the file's repository; may not be the original publisher): "
                        + f.check_url,
                    )
                )
            elif f.check_url:
                out.append(tr(f"        確かめる場所: {f.check_url}", f"        check:   {f.check_url}"))
        if f.license_note:
            out.append(tr(f"        メモ: {f.license_note}", f"        note: {f.license_note}"))
        if f.reason:
            out.append(f"        {f.status}: {f.reason}")
    out.append(
        L("合計", "total")
        + tr(
            f"{_gb(plan.total_bytes)}。落とす量 {_gb(plan.download_bytes)}"
            f"(既にある {_gb(plan.present_bytes)})",
            f"{_gb(plan.total_bytes)}; to download {_gb(plan.download_bytes)}"
            f" (already there {_gb(plan.present_bytes)})",
        )
    )
    free = _gb(plan.disk_free_bytes) if plan.disk_free_bytes is not None else tr("不明", "unknown")
    if plan.download_bytes:
        out.append(
            L("ディスク", "disk")
            + tr(
                f"空き {free}。要る量 {_gb(plan.disk_needed_bytes)}(落とす量 + 1 GB)",
                f"{free} free; needs {_gb(plan.disk_needed_bytes)} (to download + 1 GB)",
            )
        )
    else:
        out.append(
            L("ディスク", "disk")
            + tr(
                f"空き {free}。落とすものは無い(置いてあるファイルの sha256 をもう一度確かめる)",
                f"{free} free; nothing to download (sha256 of the files there is checked again)",
            )
        )
    if plan.budget_bytes is not None:
        out.append(
            L("上限", "budget")
            + tr(f"{_gb(plan.budget_bytes)}(--budget)", f"{_gb(plan.budget_bytes)} (--budget)")
        )
    if plan.license_url:
        out.append(L("利用条件", "terms") + plan.license_url)
    if plan.alternatives:
        alts = ", ".join(
            f"{a['precision'] or '?'}/{a['use']} {a['fit_level']} {_gb(a['size_bytes'])} [{a['id']}]"
            for a in plan.alternatives[:3]
        )
        rest = len(plan.alternatives) - 3
        more = tr(f"(ほかに {rest} 件)", f" (+{rest} more)") if rest > 0 else ""
        out.append(L("ほかの候補", "others") + f"{alts}{more}")
    for w in plan.warnings:
        if asks and w.startswith((UNKNOWN_WARNING_JA, UNKNOWN_WARNING_EN)):
            continue  # 下の「同意」の行にまとめた(ライブラリの warnings はアプリ向けにそのまま)
        out.append(tr(f"  注意: {w}", f"  warning: {w}"))
    if asks:
        out.append(_consent_line(plan, asks))
    out += [tr(f"  落とせません: {b}", f"  cannot download: {b}") for b in blocked_texts(plan)]
    if consent and asks and not plan.blocked:
        out.append("")
        if any(not f.license_url for f in plan.files):
            out.append(
                tr(
                    f"このモデルは{_asks_phrase(asks)}。上のファイルを、"
                    "それぞれのライセンス・利用条件に従って"
                    "使うこと(ライセンスの原文の URL が無いファイルは、上の確かめる場所で条件を自分で確かめた"
                    "うえで)に同意して、それでも落としますか?",
                    f"This model {_asks_phrase(asks)}. Do you agree to use the files above under their"
                    " licenses and terms (for files without a license URL, after checking the pages shown"
                    " above yourself), and download anyway?",
                )
            )
        else:
            out.append(
                tr(
                    f"このモデルは{_asks_phrase(asks)}。上のファイルを、それぞれのライセンス・利用条件(URL)に"
                    "従って使うことに同意して、それでも落としますか?",
                    f"This model {_asks_phrase(asks)}. Do you agree to use the files above under their"
                    " licenses and terms (URLs), and download anyway?",
                )
            )
    elif consent and not plan.blocked and not plan.download_bytes:
        out.append("")
        out.append(
            tr(
                "落とすものはありません(置いてあるファイルの sha256 だけ確かめます)。続けますか?",
                "Nothing to download (only the sha256 of the files there is checked). Continue?",
            )
        )
    elif consent and not plan.blocked and any(not f.license_url for f in plan.files):
        out.append("")
        out.append(
            tr(
                "上のファイルを、それぞれのライセンス・利用条件に従って使うこと(ライセンスの原文の URL が無い"
                "ファイルは、上の確かめる場所で条件を自分で確かめたうえで)に同意して落としますか?",
                "Do you agree to use the files above under their licenses and terms (for files without a"
                " license URL, after checking the pages shown above yourself), and download them?",
            )
        )
    elif consent and not plan.blocked:
        out.append("")
        out.append(
            tr(
                "上のファイルを、それぞれのライセンス・利用条件(URL)に従って使うことに同意して落としますか?",
                "Do you agree to use the files above under their"
                " licenses and terms (URLs), and download them?",
            )
        )
    return out


def _basis_text(f) -> str:
    """推定した置き場所の行(根拠 directory_basis を人が読む文に。#75)。"""
    b = f.directory_basis
    params, ref, tpl = f"{b.get('params', 0):,}", b.get("file", "?"), b.get("template", "?")
    needs = b.get("needs")
    return tr(
        f"        置き場所: {f.directory}(一覧に無いので推定: ヘッダのパラメータ数 {params} が"
        f"公式テンプレート {tpl} の {ref} と同じ"
        + (f"。読むには ComfyUI にカスタムノード {needs} が要る" if needs else "")
        + ")",
        f"        folder:  {f.directory} (not in the list; inferred: header parameter count {params} matches"
        f" {ref} in the official template {tpl}"
        + (f"; ComfyUI needs the {needs} custom nodes to load it" if needs else "")
        + ")",
    )


# --- 置き場所の決め方(#111。docs/download.ja.md「置き場所の決め方」) -----------------------------

# 自動で見つけた(または選ぶ画面で入れた)場所。初めて落とすとき「次からもここに」と尋ねる
_AUTO_SOURCES = ("cwd", "running", "desktop", "comfy_cli", "common", "typed")


def _source_label(source: str | None, detail: str = "") -> str:
    """置き場所の決め方(JSON の destination_from)→ 人が読む名前。"""
    labels = {
        "comfyui_dir_option": ("--comfyui-dir", "--comfyui-dir"),
        "output_dir_option": ("--output-dir", "--output-dir"),
        "env": (f"環境変数 {COMFYUI_DIR_ENV}", f"environment variable {COMFYUI_DIR_ENV}"),
        "config": ("genmodelfit config の comfyui-dir", "comfyui-dir in genmodelfit config"),
        "cwd": ("今いるフォルダ", "the current folder"),
        "running": ("起動中の ComfyUI", "the running ComfyUI"),
        "desktop": ("ComfyUI デスクトップ版の設定", "ComfyUI Desktop settings"),
        "comfy_cli": ("comfy-cli の設定・既定の場所", "comfy-cli settings or default"),
        "common": ("よくある場所", "a usual place"),
        "typed": ("入力した場所", "the folder you typed"),
    }
    ja, en = labels.get(source or "", ("?", "?"))
    if source == "running" and detail:
        ja, en = f"{ja}({detail})", f"{en} ({detail})"
    return tr(ja, en)


@dataclass
class _Destination:
    """download の置き場所をどう決めたか(#111)。source が None なら未定(--list だけで起きる)。"""

    source: str | None
    comfyui_dir: Path | None = None
    output_dir: Path | None = None  # plan_download に渡す(--output-dir、is_default の節、起動中の models)
    detail: str = ""
    section: Any = None  # comfyfind.DefaultSection
    candidates: list = field(default_factory=list)  # comfyfind.Candidate
    notes: list[str] = field(default_factory=list)
    probed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "destination_from": self.source,
            "comfyui_dir": str(self.comfyui_dir) if self.comfyui_dir else None,
            "extra_model_paths": self.section.to_dict() if self.section else None,
            "destination_candidates": [c.to_dict() for c in self.candidates],
        }


def _from_comfyui(
    path: Path,
    source: str,
    *,
    detail: str = "",
    models_dir: Path | None = None,
    extra: list[Path] | None = None,
    notes: list[str] | None = None,
) -> _Destination:
    """ComfyUI のフォルダ → 置き場所。ComfyUI に見えなければ誤り。extra_model_paths.yaml に is_default の節が
    あれば、そこ(ComfyUI-Manager の download_model_base と同じ)。"""
    from . import comfyfind
    from .downloader import DownloadRefused, comfyui_models_dir

    path = Path(os.path.abspath(path.expanduser()))
    try:
        comfyui_models_dir(path)
    except DownloadRefused as e:
        src = {
            "env": tr(
                f"(環境変数 {COMFYUI_DIR_ENV} の値)", f" (from the environment variable {COMFYUI_DIR_ENV})"
            ),
            "config": tr(
                "(genmodelfit config の comfyui-dir の値。genmodelfit config unset comfyui-dir で消せる)",
                " (from comfyui-dir in genmodelfit config; remove it with"
                " genmodelfit config unset comfyui-dir)",
            ),
        }.get(source, "")
        raise CliError(str(e) + src) from e
    files: list[Path] = []
    for p in [path / "extra_model_paths.yaml", *(extra or []), *comfyfind.desktop_extra_configs(path)]:
        if p not in files:
            files.append(p)
    section, ynotes = comfyfind.default_section(files)
    d = _Destination(source, comfyui_dir=path, detail=detail, notes=[*(notes or []), *ynotes])
    if section is not None:
        d.section, d.output_dir = section, section.root
    elif models_dir is not None and models_dir.is_dir():
        d.output_dir = models_dir
    return d


def _from_candidate(
    c, candidates: list, notes: list[str] | None = None, probed: bool = False
) -> _Destination:
    d = _from_comfyui(
        c.path, c.source, detail=c.detail, models_dir=c.models_dir, extra=c.extra_configs, notes=notes
    )
    d.candidates, d.probed = candidates, probed
    return d


def _resolve_destination(args: argparse.Namespace, *, probe: bool) -> _Destination:
    """置き場所(上から順に最初に決まったもの): --comfyui-dir / --output-dir → 環境変数
    GENMODELFIT_COMFYUI_DIR → genmodelfit config の comfyui-dir → 自動で探す(1 つだけ見つかったとき)。
    probe: 起動中の ComfyUI(127.0.0.1)に尋ねる(落とすときだけ。--list は通信しない)。"""
    from . import comfyfind, config

    if args.comfyui_dir:
        return _from_comfyui(Path(args.comfyui_dir), "comfyui_dir_option")
    if args.output_dir:
        out = Path(os.path.abspath(Path(args.output_dir).expanduser()))
        return _Destination("output_dir_option", output_dir=out)
    env_dir = os.environ.get(COMFYUI_DIR_ENV, "").strip()
    if env_dir:
        return _from_comfyui(Path(env_dir), "env")
    values, notes = config.load()
    if values.get("comfyui-dir"):
        return _from_comfyui(Path(values["comfyui-dir"]), "config", notes=notes)
    cands = comfyfind.find_candidates(probe=probe)
    if len(cands) == 1:
        return _from_candidate(cands[0], cands, notes, probe)
    return _Destination(None, candidates=cands, notes=notes, probed=probe)


def _undecided_root() -> Path:
    """未定のまま計画を作るときの、無いフォルダ(何も書かない。計画の root・dest は空にして出す)。"""
    import secrets
    import tempfile

    return Path(tempfile.gettempdir()) / f"genmodelfit-undecided-{secrets.token_hex(8)}"


def _undecided_plan(plan) -> None:
    """未定の計画から、仮のフォルダに関わるもの(root・dest・空き・空きで止める理由)を消す。"""
    from .downloader import LAYOUT_WARNING_EN, LAYOUT_WARNING_JA

    codes = list(plan.blocked_codes) + [""] * (len(plan.blocked) - len(plan.blocked_codes))
    pairs = [(b, c) for b, c in zip(plan.blocked, codes, strict=True) if c != "disk"]
    plan.blocked = [b for b, _ in pairs]
    plan.blocked_codes = [c for _, c in pairs if c]
    plan.root, plan.layout, plan.disk_free_bytes = "", "comfyui", None
    for f in plan.files:
        f.dest = ""
    plan.warnings = [w for w in plan.warnings if w not in (LAYOUT_WARNING_JA, LAYOUT_WARNING_EN)]


def _destination_notes(plan, dest: _Destination) -> None:
    """ComfyUI から決めた置き場所の計画を直す: 「ComfyUI のフォルダではない」の知らせを外し、is_default の節に
    無いフォルダと、置き場所を決めたときの注意を足す。"""
    from .comfyfind import LEGACY
    from .downloader import LAYOUT_WARNING_EN, LAYOUT_WARNING_JA

    if dest.comfyui_dir is not None and dest.output_dir is not None:
        plan.warnings = [w for w in plan.warnings if w not in (LAYOUT_WARNING_JA, LAYOUT_WARNING_EN)]
    sec = dest.section
    if sec is not None and sec.folders is not None:
        missing = sorted(
            {
                f.directory
                for f in plan.files
                if f.directory and LEGACY.get(f.directory, f.directory) not in sec.folders
            }
        )
        if missing:
            plan.warnings.append(
                tr(
                    f"{sec.file} の節 {sec.section} に無いフォルダ({', '.join(missing)})は、"
                    "ComfyUI がここから読みません(節にそのフォルダを足すか、"
                    "--output-dir で ComfyUI の models を指定する)",
                    f"ComfyUI does not read the folders missing from section {sec.section} of {sec.file}"
                    f" ({', '.join(missing)}) here (add them to the section, or pass ComfyUI's models with"
                    " --output-dir)",
                )
            )
    plan.warnings += dest.notes


def _how_to_decide() -> str:
    return tr(
        "--comfyui-dir で ComfyUI のフォルダを渡すか、genmodelfit config set comfyui-dir <フォルダ> で決める"
        "(ComfyUI の外に置くなら --output-dir)",
        "pass the ComfyUI folder with --comfyui-dir, or save it with genmodelfit config set comfyui-dir"
        " <folder> (--output-dir for a place outside ComfyUI)",
    )


def _undecided_why(dest: _Destination) -> str:
    if dest.candidates:
        return tr(
            f"ComfyUI のフォルダが {len(dest.candidates)} つ見つかりました",
            f"{len(dest.candidates)} ComfyUI folders were found",
        )
    return tr("ComfyUI のフォルダが見つかりません", "no ComfyUI folder was found")


def _candidate_lines(dest: _Destination, current: Path | None = None) -> list[str]:
    from .comfyfind import _key

    out = []
    for i, c in enumerate(dest.candidates, 1):
        here = current is not None and _key(c.path) == _key(current)
        mark = tr("  ← 今の置き場所", "  <- current") if here else ""
        out.append(f"    {i}. {c.path}  ({_source_label(c.source, c.detail)}){mark}")
    return out


def _destination_lines(plan, dest: _Destination | None, L) -> list[str]:
    """計画の「置き場所」の行(#111。決め方・見つけ方を毎回出す)。"""
    if dest is not None and dest.source is None:
        why = _undecided_why(dest)
        out = [L("置き場所", "to") + tr(f"未定({why})", f"not decided ({why})")]
        out += _candidate_lines(dest)
        out.append(tr(f"        決め方: {_how_to_decide()}", f"        to decide: {_how_to_decide()}"))
        if not dest.probed:
            out.append(
                tr(
                    "        落とすときは、起動中の ComfyUI(127.0.0.1 の 8188・8000)にも尋ねます。"
                    "端末なら候補から選べます",
                    "        When downloading, a running ComfyUI (127.0.0.1 ports 8188 and 8000) is"
                    " also asked, and in a terminal you can pick from the candidates",
                )
            )
        return out
    if plan.layout == "comfyui":
        where = tr("ComfyUI の models のフォルダ", "ComfyUI models folder")
    elif dest is not None and dest.section is not None:
        where = tr(
            f"extra_model_paths.yaml の is_default の節 {dest.section.section}",
            f"is_default section {dest.section.section} of extra_model_paths.yaml",
        )
    elif dest is not None and dest.comfyui_dir is not None and dest.output_dir is not None:
        where = tr(
            "起動中の ComfyUI が最初に見る models", "the models folder the running ComfyUI reads first"
        )
    else:
        where = tr("models のフォルダ(ComfyUI と同じ並び)", "models folder (ComfyUI layout)")
    how = ""
    if dest is not None and dest.source is not None:
        auto = dest.source in _AUTO_SOURCES and dest.source != "typed"
        label = _source_label(dest.source, dest.detail)
        how = tr(
            f"。{'見つけ方' if auto else '決め方'}: {label}", f"; {'found by' if auto else 'set by'}: {label}"
        )
    out = [
        L("置き場所", "to")
        + tr(f"{plan.root}({where}: <folder>/<file>{how})", f"{plan.root}  ({where}: <folder>/<file>{how})")
    ]
    if dest is not None and dest.comfyui_dir is not None and plan.layout != "comfyui":
        out.append(L("ComfyUI", "ComfyUI") + str(dest.comfyui_dir))
    return out


def _choose_destination(candidates: list, current: Path | None = None) -> _Destination | None:
    """端末で ComfyUI のフォルダを選ぶ(番号か、パスを入れる)。空・EOF・3 回続けて誤りなら None。"""
    from .comfyfind import looks_like_comfyui

    lines = [tr("ComfyUI のフォルダを選んでください:", "Choose the ComfyUI folder:")]
    lines += _candidate_lines(_Destination(None, candidates=candidates), current) or [
        tr("    (ComfyUI のフォルダは見つかりませんでした)", "    (no ComfyUI folder was found)")
    ]
    _emit(lines)
    for _ in range(3):
        try:
            ans = input(
                tr(
                    "番号か ComfyUI のフォルダのパス(空で取りやめ): ",
                    "Number, or path of the ComfyUI folder (empty to cancel): ",
                )
            ).strip()
        except EOFError:
            return None
        if not ans:
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(candidates):
            return _from_candidate(candidates[int(ans) - 1], candidates, probed=True)
        p = Path(ans.strip("\"'")).expanduser()
        if looks_like_comfyui(p):
            d = _from_comfyui(p, "typed")
            d.candidates = candidates
            return d
        print(
            tr(
                f"ComfyUI のフォルダに見えません: {p}(main.py・comfy・nodes.py・comfy_extras・comfy_api の"
                "うち 4 つがあるフォルダ)",
                f"This does not look like a ComfyUI folder: {p} (one with 4 of main.py, comfy, nodes.py,"
                " comfy_extras, comfy_api)",
            ),
            file=sys.stderr,
        )
    return None


def _remember(dest: _Destination) -> None:
    """自動で見つけた・入れた場所に落とすとき、次からもそこに落とすかを尋ねて保存する(既定は Yes)。"""
    from . import config

    if dest.source not in _AUTO_SOURCES or dest.comfyui_dir is None:
        return
    try:
        ans = input(tr("次からもここに落としますか? [Y/n] ", "Download here next time too? [Y/n] "))
    except EOFError:
        return
    if ans.strip().lower() not in ("", "y", "yes"):
        return
    try:
        path = config.set_value("comfyui-dir", str(dest.comfyui_dir))
    except config.ConfigError as e:
        print(tr(f"保存できませんでした: {e}", f"Not saved: {e}"), file=sys.stderr)
        return
    print(
        tr(
            f"保存しました: comfyui-dir = {dest.comfyui_dir}({path}。genmodelfit config で見る・消す)",
            f"Saved: comfyui-dir = {dest.comfyui_dir} ({path}; see or remove it with genmodelfit config)",
        )
    )


# 引用符なしで両方のシェル(PowerShell・POSIX sh)にそのまま渡る文字
_SHELL_SAFE = re.compile(r"[A-Za-z0-9_./:=+%-]+")


def shell_quote(arg: str, windows: bool | None = None) -> str:
    """コマンドの 1 つの引数を、貼り付けて使える形にする(#75)。

    安全な文字だけならそのまま。ほかは ' で囲む(PowerShell も POSIX sh も ' の中は $・`・\\ を展開しない)。
    中に ' があるときだけシェルで形が分かれるので、この OS のシェルの形にする
    (PowerShell は '' と重ねる、POSIX sh は '\\'' で閉じて足して開き直す)。windows=None は _is_windows()。"""
    if arg and _SHELL_SAFE.fullmatch(arg):
        return arg
    if "'" not in arg:
        return f"'{arg}'"
    if _is_windows() if windows is None else windows:
        return "'" + arg.replace("'", "''") + "'"
    return "'" + arg.replace("'", "'\\''") + "'"


def _num(v: float | int) -> str:
    """数の引数を、解釈した値のまま文字にする(丸めない。8.0 は 8)。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return repr(v)


def _prog() -> str:
    """利用者が使った起動のしかた(python -m genmodelfit か genmodelfit)。"""
    argv0 = sys.argv[0] if sys.argv else ""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    by_path = (
        os.path.basename(argv0) == "__main__.py" and os.path.basename(os.path.dirname(argv0)) == "genmodelfit"
    )
    if by_path or getattr(spec, "name", "") == "genmodelfit.__main__":
        return "python -m genmodelfit"
    return "genmodelfit"


def _download_command(args: argparse.Namespace, model_id: str, add: list[str]) -> str:
    """利用者の指定(解釈した引数から。argv の字面ではない)に add の指定を足した download のコマンド(#75)。

    モデルは計画のワークフローの ID で書く(同じワークフローになる。精度も ID で決まるので
    --precision は付けない)。
    --list・--json・--yes は付けない(落とすためのコマンドなので)。環境変数・genmodelfit config で決めた
    ComfyUI のフォルダは付けない(同じ設定で同じ場所になる)。自動で見つけた場所は --comfyui-dir で
    書く(#111)。"""
    parts = ["download", model_id]
    if args.budget is not None:
        parts += ["--budget", _num(args.budget)]
    if getattr(args, "with_optional", False):
        parts.append("--with-optional")
    dest = getattr(args, "_dest", None)
    if args.comfyui_dir:
        parts += ["--comfyui-dir", args.comfyui_dir]
    elif dest is not None and dest.source in _AUTO_SOURCES and dest.comfyui_dir is not None:
        parts += ["--comfyui-dir", str(dest.comfyui_dir)]  # 自動で見つけた場所(スクリプトでも同じ場所に)
    elif args.output_dir:
        parts += ["--output-dir", args.output_dir]
    if "--uncensored" in add or args.content == "all":
        parts.append("--uncensored")
    elif args.content != DEFAULT_CONTENT:
        parts += ["--content", args.content]
    if args.include_unknown_license or "--any-license" in add:
        parts.append("--any-license")
    if "--yes" in add:
        parts.append("--yes")
    for flag, value in (
        ("--db", args.db),
        ("--vram", None if args.vram is None else _num(args.vram)),
        ("--ram", None if args.ram is None else _num(args.ram)),
        ("--license-allowlist", args.license_allowlist),
        ("--comfyui-version", args.comfyui_version),
    ):
        if value is not None:
            parts += [flag, value]
    if args.max_head_age_days != 30:
        days = args.max_head_age_days
        parts += ["--max-head-age-days", "none" if days is None else _num(days)]
    if args.no_cache:
        parts.append("--no-cache")
    if args.allow_uncensored_auto:
        parts.append("--allow-uncensored-auto")
    if getattr(args, "lang", None):
        parts += ["--lang", args.lang]
    return " ".join([_prog(), *(shell_quote(p) for p in parts)])


def _repo_page(url: str | None) -> str | None:
    """Hugging Face のファイルの URL → そのリポジトリのページ(…/resolve/… の前)。ほかは None。"""
    if url and url.startswith("https://huggingface.co/") and "/resolve/" in url:
        return url.split("/resolve/", 1)[0]
    return None


def _consent_flags(codes: list[str]) -> tuple[list[str], list[str]]:
    """同意の種類 → 足す指定と、指定ごとに何に同意するかの文(#75)。"""
    add: list[str] = []
    agree: list[str] = []
    if "uncensored" in codes:
        add.append("--uncensored")
        agree.append(
            tr(
                "  --uncensored: 無検閲(成人向けを含みうる)のモデルを、自分の判断で落とす",
                "  --uncensored: you choose to download an uncensored model (may include adult content)",
            )
        )
    if "unknown_license" in codes:
        add.append("--any-license")
        agree.append(
            tr(
                "  --any-license: ライセンスの記載が無いファイルの条件を、上の確かめる場所で"
                "自分で確かめ、使う責任を自分で負う",
                "  --any-license: you check the terms of the files with no stated license"
                " yourself (pages above) and take responsibility for using them",
            )
        )
    return add, agree


def download_next_steps(
    plan, args: argparse.Namespace, asks: list[str] | None = None
) -> tuple[list[str], str | None, list[str]]:
    """止めたときの次の一手(#75)。(前の文, 1 行のコマンド(無ければ None), 後の文)。

    同意の指定(--uncensored・--any-license)だけで外せるなら、それを全部足したコマンドを 1 つ出し、
    指定ごとに何に同意するかを 1 行ずつ添える。指定では外せない理由(gated・空き・取得先・置き場所の外など)が
    あれば、コマンドは出さずにすることを書く。勝手に指定を足して落とすことはしない。
    asks: 端末なら質問で尋ねる同意の種類(--list で、止めずに尋ねる計画を見せたとき)。尋ねずに落とす
    (--yes・スクリプト)ときのコマンドを出す。"""
    if asks:
        add, agree = _consent_flags(asks)
        cmd = "  " + _download_command(args, plan.model_id, [*add, "--yes"])
        head = tr("尋ねずに落とすなら(スクリプトなど):", "To download without the question (scripts):")
        return [head], cmd, agree
    codes = list(dict.fromkeys(plan.blocked_codes))
    add, agree = _consent_flags(codes)
    fatal = [c for c in codes if c in ("gated", "unavailable", "no_files")]
    lever = [c for c in codes if c in ("disk", "budget", "conflict")]
    if not fatal and not lever:
        if not add:
            return [], None, []
        cmd = "  " + _download_command(args, plan.model_id, add)
        if args.yes:
            head = tr(
                "落とすなら(--yes を外せば y/N で尋ねます。または下の指定を足す):",
                "To download anyway (drop --yes to be asked y/N instead, or add the flags below):",
            )
        else:
            head = tr(
                "落とすなら(端末で実行すれば、指定の代わりに y/N で尋ねます):",
                "To download anyway (run it in a terminal to be asked y/N instead of adding flags):",
            )
        return [head], cmd, agree
    if fatal:
        # gated(ログインしない)・取得できないファイル・ファイル無しは、どの指定でも落とせない。指定を足せば
        # 落とせるような言い方はしない
        head = [
            tr(
                "このワークフローは genmodelfit では落とせません(どの指定でも変わりません):",
                "genmodelfit cannot download this workflow (no option changes this):",
            )
        ]
        if "gated" in fatal:
            pages = sorted({_repo_page(f.url) or f.check_url or "" for f in plan.files if f.gated} - {""})
            head.append(
                tr(
                    "  - gated: モデルのページで利用申請し、そのファイルは自分で落とす"
                    "(genmodelfit はログインしない)",
                    "  - gated: request access on the model page and download those files yourself"
                    " (genmodelfit never logs in)",
                )
                + (": " + ", ".join(pages) if pages else "")
            )
        if "unavailable" in fatal:
            head.append(
                tr(
                    "  - genmodelfit では落とせないファイル(理由は上)。自分で落とすなら:",
                    "  - files genmodelfit cannot download (reasons above). To get them yourself:",
                )
            )
            for f in plan.files:
                if f.status == "unavailable":
                    where = f.url or tr("一覧に URL がありません", "no URL in the list")
                    check = (
                        tr(f"(確かめる場所: {f.check_url})", f" (check: {f.check_url})")
                        if f.check_url
                        else ""
                    )
                    head.append(f"      {f.name}: {where}{check}")
        if "no_files" in fatal:
            head.append(tr("  - 一覧に落とすファイルがありません", "  - the list has no files to download"))
        alt = next((a for a in plan.alternatives if a.get("ok")), None)
        if alt:
            head.append(
                tr(
                    f"  - 同じモデルの別のワークフローにはこの問題がありません: {alt['id']}"
                    f"({alt['precision'] or '?'}、{alt['fit_level']})。見るなら:"
                    f" {_prog()} download {shell_quote(alt['id'])} --list",
                    f"  - another workflow of the same model has no such problem: {alt['id']}"
                    f" ({alt['precision'] or '?'}, {alt['fit_level']}). To look at it:"
                    f" {_prog()} download {shell_quote(alt['id'])} --list",
                )
            )
        return head, None, []
    todo = {
        "disk": tr(
            f"  - 空き: ディスクを {_gb(plan.disk_needed_bytes)} 空けるか、--comfyui-dir / --output-dir で"
            "別のドライブを選ぶ",
            f"  - disk: free up space (needs {_gb(plan.disk_needed_bytes)}) or choose another drive with"
            " --comfyui-dir / --output-dir",
        ),
        "budget": tr(
            "  - 上限: --budget を上げるか外す、または --precision で小さい精度を選ぶ",
            "  - budget: raise or drop --budget, or pick a smaller precision with --precision",
        ),
        "conflict": tr(
            "  - 同じ名前の別のファイル: 移すか、--output-dir で別の置き場所を選ぶ",
            "  - a different file with the same name: move it, or choose another place with --output-dir",
        ),
    }
    head = [tr("同意の指定では外れません。先に:", "The consent flags do not lift these. First:")]
    head += [todo[c] for c in lever]
    if add:
        head.append(
            tr(
                f"  - そのうえで、同意の指定 {' '.join(add)} も足す(下の意味に同意するなら)",
                f"  - then also add the consent flags {' '.join(add)} (if you agree to what they mean below)",
            )
        )
    return head, None, agree


def _emit_next_steps(plan, args: argparse.Namespace, asks: list[str] | None = None) -> None:
    head, command, tail = download_next_steps(plan, args, asks)
    if not (head or command or tail):
        return
    print()
    _emit(head)
    if command:
        print(command)  # コピーして貼れるよう折り返さない
    _emit(tail)


def _interactive() -> bool:
    """端末で y/N を尋ねられるか(標準入力と標準出力がどちらも端末)。試験で差し替える(#75)。

    Windows で入力を NUL から読ませると isatty が True になる。そのときも _ask が EOF を受けて「同意しない」に
    なるので、勝手に落とすことはない(tests/test_download.py の EOF の試験)。"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _ask(lines: list[str]) -> bool:
    """同意を尋ねる(y / yes だけを同意とみなす)。入力が無い(パイプ・EOF)なら同意しない。"""
    _emit(lines)
    try:
        ans = input("[y/N] ")
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes")


def _ask_choice(lines: list[str]) -> str:
    """download の同意を尋ねる。y / yes → "y"、c → "c"(置き場所を変える。#111)、ほか・入力が無い
    (パイプ・EOF)→ "n"。"""
    _emit(lines)
    try:
        ans = input(tr("[y/N/c](c = 置き場所を変える) ", "[y/N/c] (c = change the place) "))
    except EOFError:
        return "n"
    ans = ans.strip().lower()
    if ans in ("y", "yes"):
        return "y"
    return "c" if ans == "c" else "n"


def _undecided_error(args: argparse.Namespace, s, db, dest: _Destination) -> int:
    """置き場所が決まらず、尋ねられない(--yes・--json・端末でない)とき: 落とさずに誤り(#111)。"""
    msg = tr(
        f"置き場所が決まりません({_undecided_why(dest)})。{_how_to_decide()}",
        f"The place to download to is not decided ({_undecided_why(dest)}). To decide, {_how_to_decide()}",
    )
    if args.json:
        _out(_envelope("download", s, db, listed=False, plan=None, result=None, error=msg, **dest.to_json()))
        return 1
    print(tr(f"エラー: {msg}", f"error: {msg}"), file=sys.stderr)
    for line in _candidate_lines(dest):
        print(line, file=sys.stderr)
    return 1


def cmd_download(args: argparse.Namespace) -> int:
    from .downloader import CONSENT_CODES, DownloadError, ModelNotFound, download, plan_download

    if args.json and not args.list and not args.yes:
        raise CliError(
            tr(
                "--json で落とすときは --yes が要ります(同意を尋ねられないため。先に --list で確かめる)",
                "--json needs --yes to download (consent cannot be asked; check with --list first)",
            )
        )
    # 置き場所(#111): --comfyui-dir / --output-dir → 環境変数 → genmodelfit config → 自動で探す。
    # 起動中の ComfyUI に尋ねるのは落とすときだけ(--list は通信しない)
    dest = _resolve_destination(args, probe=not args.list)
    s, db = _system(args), _db(args)
    # 本当に実行したら尋ねるとき(端末で --yes・--json が無い)。--list も同じ条件で、尋ねることを見せる
    can_ask = not args.json and not args.yes and _interactive()

    def make_plan(dest: _Destination):
        if dest.source is None:
            where: dict[str, Any] = {"output_dir": _undecided_root(), "comfyui_dir": None}
        elif dest.output_dir is not None:
            where = {"output_dir": dest.output_dir, "comfyui_dir": None}
        else:
            where = {"output_dir": None, "comfyui_dir": dest.comfyui_dir}
        try:
            plan = plan_download(
                args.model,
                precision=args.precision,
                budget_gb=args.budget,
                content=args.content,
                include_unknown_license=args.include_unknown_license,
                system_info=s,
                db=db,
                policy=_policy(args),
                with_optional=args.with_optional,
                **where,
            )
        except ModelNotFound as e:
            err = CliError(
                str(e).replace('content="all"', "--uncensored")
            )  # ライブラリの言い方を CLI の指定に
            err.candidates = e.candidates or None  # type: ignore[attr-defined]
            raise err from e
        except DownloadError as e:
            raise CliError(str(e)) from e
        # 無検閲・ライセンス不明は、端末で尋ねられるなら止めずに y/N の質問で同意を取る(#75)。--yes・--json・
        # 端末でないときは今までどおり指定(--uncensored・--any-license)が要る。ライブラリの約束は変えない
        asks = [c for c in dict.fromkeys(plan.blocked_codes) if c in CONSENT_CODES]
        if asks and can_ask and set(plan.blocked_codes) <= set(CONSENT_CODES):
            strict = plan
            try:
                plan = plan_download(
                    plan.model_id,  # 同じワークフロー(ID で指定し直す。名前の探し方は変えない)
                    budget_gb=args.budget,
                    content="all" if "uncensored" in asks else args.content,
                    include_unknown_license=True,
                    verify_existing=False,  # sha256 は 1 回目で計った。ファイルの状態は 1 回目のものを使う
                    system_info=s,
                    db=db,
                    policy=_policy(args),
                    with_optional=args.with_optional,
                    **where,
                )
            except DownloadError as e:  # 1 回目と同じ条件なので起きないはず
                raise CliError(str(e)) from e
            if [f.name for f in plan.files] != [
                f.name for f in strict.files
            ]:  # 念のため(同じ ID なので同じはず)
                raise CliError(tr("計画を作り直せませんでした", "could not re-plan the download"))
            plan.files = strict.files
            plan.chosen, plan.alternatives = strict.chosen, strict.alternatives  # 選んだ理由は 1 回目のもの
        else:
            asks = []
        if dest.source is None:
            _undecided_plan(plan)
        _destination_notes(plan, dest)
        args._dest = dest  # 次の一手のコマンドに、自動で見つけた場所を書くため
        return plan, asks

    plan, asks = make_plan(dest)  # 置き場所が未定でも先に作る(モデルが無いなどの誤りを先に出す)
    if dest.source is None and not args.list:
        if not can_ask:
            return _undecided_error(args, s, db, dest)
        try:
            chosen = _choose_destination(dest.candidates)
        except KeyboardInterrupt:  # 選ぶ途中の Ctrl+C: 何も落とさず 130
            print(file=sys.stderr)
            print(
                tr("中断しました(何も落としていません)", "Interrupted (nothing downloaded)"), file=sys.stderr
            )
            return 130
        if chosen is None:
            print(
                tr("落としませんでした(置き場所が決まりません)", "Not downloaded (no place chosen)"),
                file=sys.stderr,
            )
            return 1
        dest = chosen
        plan, asks = make_plan(dest)

    def fail(e: Exception, code: int = 1) -> int:
        if args.json:
            _out(
                _envelope(
                    "download",
                    s,
                    db,
                    listed=False,
                    plan=plan.to_dict(),
                    result=None,
                    error=str(e),
                    **dest.to_json(),
                )
            )
        else:
            print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
        return code

    if args.list:
        if args.json:
            _out(
                _envelope("download", s, db, listed=True, plan=plan.to_dict(), result=None, **dest.to_json())
            )
        else:
            _emit(format_plan(plan, dest=dest, asks=asks))
            _emit_next_steps(plan, args, asks)
        return 0
    while True:
        if plan.blocked:
            if args.json:
                return fail(
                    DownloadError(tr("落とせません: ", "cannot download: ") + " / ".join(blocked_texts(plan)))
                )
            # 理由は計画の中に 1 回だけ出し、最後に次の一手を出す(同じ文を error: で繰り返さない。#75)
            _emit(format_plan(plan, dest=dest))
            _emit_next_steps(plan, args)
            print(
                tr("エラー: 落とせません(上を見てください)", "error: cannot download (see above)"),
                file=sys.stderr,
            )
            return 1
        if args.yes:
            if not args.json:
                _emit(format_plan(plan, dest=dest))
            break
        try:
            answer = _ask_choice(format_plan(plan, consent=True, dest=dest, asks=asks))
            if answer == "c":  # 置き場所を変えて、計画を出し直して聞き直す(#111)
                from .comfyfind import find_candidates

                cands = dest.candidates or find_candidates(probe=True)
                new = _choose_destination(cands, current=dest.comfyui_dir)
                if new is not None:
                    dest = new
                    plan, asks = make_plan(dest)
                continue
            if answer == "y":
                _remember(dest)
        except KeyboardInterrupt:  # 質問の途中の Ctrl+C: 何も落とさず 130
            print(file=sys.stderr)
            print(
                tr("中断しました(何も落としていません)", "Interrupted (nothing downloaded)"), file=sys.stderr
            )
            return 130
        if answer != "y":
            args._declined = True
            print(tr("落としませんでした(同意がありません)", "Not downloaded (no consent)"), file=sys.stderr)
            return 1
        break
    show = None if args.json else _DownloadView()
    try:
        result = download(plan, progress=show, yes=True, record=getattr(args, "_dl_record", None))
    except KeyboardInterrupt:
        if show:
            show.close()
        return fail(
            DownloadError(
                tr(
                    "中断しました。途中までのファイル(.part)は残してあり、"
                    "同じコマンドでもう一度実行すると続きから落とします",
                    "Interrupted. The partial files (.part) are kept; run the same command again to resume",
                )
            ),
            130,
        )
    except DownloadError as e:
        if show:
            show.close()
        return fail(e)
    if show:
        show.close()
    if args.json:
        _out(
            _envelope(
                "download",
                s,
                db,
                listed=False,
                plan=plan.to_dict(),
                result=result.to_dict(),
                **dest.to_json(),
            )
        )
        return 0
    print(
        tr(
            f"落としました: {len(result.downloaded)} 件(続きから {len(result.resumed)} 件)、"
            f"既にあった {len(result.skipped)} 件、{result.bytes_downloaded / GIB:.2f} GB、"
            f"{result.seconds:.0f} 秒。"
            "sha256 はすべて一覧と一致",
            f"Downloaded: {len(result.downloaded)} files ({len(result.resumed)} resumed),"
            f" {len(result.skipped)} already there, {result.bytes_downloaded / GIB:.2f} GB,"
            f" {result.seconds:.0f} s. Every sha256 matches the list",
        )
    )
    print(tr(f"置き場所: {plan.root}", f"Location: {plan.root}"))
    return 0


# --- measure(#113。docs/commands.md「genmodelfit measure」) -------------------------------------------

MEASURE_LIMIT = 10  # 引数なしの measure が出す提案の数(--limit で変える)
_MODALITY_JA = {
    "image": "画像",
    "video": "動画",
    "music": "音楽",
    "sfx": "効果音",
    "tts": "読み上げ",
    "3d": "3D",
}


def _measure_comfyui(args: argparse.Namespace) -> tuple[str | None, str | None, Any]:
    """測りに使う ComfyUI: (URL, 見つけ方 option / running, 起動中の候補)。--url が無ければ、この PC の
    127.0.0.1 の 8188・8000 で起動中の ComfyUI を探す(download と同じ comfyfind。
    GENMODELFIT_COMFYUI_SEARCH=0 で探さない)。見つからなければ (None, None, None)。"""
    if args.url:
        return args.url, "option", None
    from . import comfyfind

    if comfyfind.search_disabled():
        return None, None, None
    running = comfyfind.running_candidates()
    if not running:
        return None, None, None
    c = running[0]
    return f"http://{c.detail}", "running", c


def _measure_roots(args: argparse.Namespace, running: Any) -> list[Path]:
    """モデルのファイルが入っているかを見るフォルダ(ComfyUI の models・extra_model_paths の既定の節・
    起動中の ComfyUI が読む models)。download と同じ決め方で、通信しない(起動中の候補は先に見つけたもの)。"""
    from .downloader import DownloadError, comfyui_models_dir

    roots: list[Path] = []
    dest = None
    try:
        if running is not None and not args.comfyui_dir:
            dest = _from_candidate(running, [running])
        else:
            dest = _resolve_destination(args, probe=False)
    except (CliError, DownloadError, OSError):
        dest = None
    if dest is not None and dest.source is not None:
        if dest.output_dir is not None:
            roots.append(dest.output_dir)
        if dest.comfyui_dir is not None:
            with contextlib.suppress(DownloadError, OSError):
                roots.append(comfyui_models_dir(dest.comfyui_dir))
    if running is not None and running.models_dir is not None:
        roots.append(running.models_dir)
    return list(dict.fromkeys(roots))


def _seconds_text(secs: int, basis: str, scaled: bool = False) -> str:
    why = {
        "measured": (
            "同じモデルのほかの GPU の実測から",
            "from measurements of the same model on other GPUs",
        ),
        "typical": ("種類ごとのおおよその値", "a rough value for the type"),
    }[basis]
    if scaled:  # 設定を変えた測定(#116)
        why = (
            why[0] + "、設定の画素数・コマ数・長さの比で伸び縮みさせた",
            why[1] + ", scaled by the ratio of pixels, frames or length",
        )
    return tr(f"約 {secs} 秒(目安。{why[0]})", f"about {secs} s (rough guide; {why[1]})")


def _installed_text(inst) -> str:
    if inst.state == "yes":
        return tr("入っている", "installed")
    if inst.state == "no":
        return tr(
            f"入っていない(落とす量 {inst.missing_bytes / GIB:.1f} GB)",
            f"not installed ({inst.missing_bytes / GIB:.1f} GB to download)",
        )
    return tr(
        "分からない(ComfyUI のフォルダが見つかりません)",
        "unknown (the ComfyUI folder was not found)",
    )


def _comfyui_start_plan(args: argparse.Namespace, url: str | None) -> tuple[Any, str | None]:
    """起動中の ComfyUI が無いとき、自動で起動する仕方(#124。comfystart)。
    (StartPlan か None, 起動できない理由)。
    見つけ方は download・measure と同じ(--comfyui-dir → 環境変数 → genmodelfit config → 自動で探す)。
    --url・--no-start・GENMODELFIT_COMFYUI_START=0 なら起動しない。通信しない。"""
    from . import comfyfind, comfystart
    from .downloader import DownloadError

    if url is not None:
        return None, None
    found = comfyfind.comfy_port_status()
    port, status = found if found else (None, None)
    if port is not None and status in (401, 403):
        return None, _HeadReason(
            tr(
                f"ポート {port} のサーバー(ComfyUI か、その前のプロキシ)が認証(ログイン)"
                f"を求めています({status})。"
                "2 台目は起動しません。genmodelfit は認証の付いた ComfyUI では測れないので、認証の無い "
                "ComfyUI を"
                " --url http://<ホスト>:<ポート> で指定してください",
                f"The server on port {port} (ComfyUI or a proxy in front of it) asks for authentication "
                f"({status})."
                " A second ComfyUI is not started. genmodelfit cannot measure behind authentication; give a"
                " ComfyUI without it with --url http://<host>:<port>",
            )
        )
    if port is not None and status != 200:
        return None, _HeadReason(
            tr(
                f"ポート {port} で ComfyUI でないものが答えています(HTTP {status})。2 台目は起動しません。"
                "測る ComfyUI を --url http://<ホスト>:<ポート> で指定してください",
                f"Something that is not ComfyUI answers on port {port} (HTTP {status}). No ComfyUI is "
                "started;"
                " give the ComfyUI to measure on with --url http://<host>:<port>",
            )
        )
    if port is not None:
        # フォルダを読めないが、ComfyUI がもう動いている(L3a): 同じフォルダで 2 台目を起動しない
        return None, _HeadReason(
            tr(
                f"ComfyUI がもうポート {port} で動いているようです(フォルダを読めませんでした)。"
                f"2 台目は起動しません。それで測るなら --url http://127.0.0.1:{port} を付けてください",
                f"A ComfyUI already seems to run on port {port} (its folders could not be read). A second "
                "one is"
                f" not started; to measure on it, add --url http://127.0.0.1:{port}",
            )
        )
    if getattr(args, "no_start", False):
        return None, tr(
            "--no-start なので ComfyUI を起動しません(外すと、見つけた ComfyUI を起動して測ります)",
            "--no-start was given, so ComfyUI is not started (without it, the ComfyUI found is started)",
        )
    if comfystart.start_disabled():
        return None, tr(
            f"環境変数 {comfystart.START_ENV} が 0 なので ComfyUI を起動しません",
            f"The environment variable {comfystart.START_ENV} is 0, so ComfyUI is not started",
        )
    try:
        dest = _resolve_destination(args, probe=False)
    except (CliError, DownloadError, OSError):
        return None, None
    d = dest.comfyui_dir
    if d is None:
        return None, None
    places = comfyfind.current_places()
    desktop = any(comfyfind._key(c.path) == comfyfind._key(d) for c in comfyfind.desktop_candidates(places))
    try:
        plan = comfystart.plan_start(
            d, desktop=desktop, user_data=comfyfind.desktop_user_data(places) if desktop else None
        )
    except comfystart.StartError as e:
        return None, str(e)
    return plan, None


def _comfyui_start(args: argparse.Namespace, plan, holder: list):
    """ComfyUI を起動して、準備ができるまで待つ(#124)。起動したものはすぐ holder に入れる(何かを表示する
    より前。呼び出し側の finally が holder のものを止めるので、途中の Ctrl+C でも残らない)。
    途中は stderr に出す(--json では出さない)。起動できなければ CliError(ログの場所と最後の行を添える)。"""
    from . import comfystart

    say = _progress(args)
    try:
        started = comfystart.launch(plan)
    except comfystart.StartError as e:
        raise CliError(str(e)) from None
    holder.append(started)
    say(
        tr(
            f"ComfyUI を起動しています({plan.comfyui_dir}、ポート {started.port}。ログ: {started.log})",
            f"Starting ComfyUI ({plan.comfyui_dir}, port {started.port}; log: {started.log})",
        )
    )
    limit = getattr(args, "start_timeout", None) or comfystart.DEFAULT_START_TIMEOUT
    view = _StartView(comfystart.TYPICAL_START_SECONDS, limit, enabled=not getattr(args, "json", False))
    waited_box = [0.0]

    def progress(waited: float, line: str = "") -> None:
        waited_box[0] = waited
        view.update(waited, line)

    try:
        comfystart.wait_ready(started, timeout=limit, progress=progress)
    except comfystart.StartError as e:
        view.close()
        lines = [str(e), *("  " + x for x in (e.tail or []))]
        raise CliError("\n".join(lines)) from None
    except BaseException:
        view.close()
        raise
    view.done(waited_box[0])
    say(tr(f"ComfyUI: {started.url}", f"ComfyUI: {started.url}"))
    return started


def _comfyui_stop(args: argparse.Namespace, started) -> None:
    """自分で起動した ComfyUI だけを止める(前から起動していたものには触らない)。"""
    from . import comfystart

    if started is None:
        return
    ok = comfystart.stop(started)
    if not getattr(args, "json", False):
        print(
            tr("起動した ComfyUI を止めました", "Stopped the ComfyUI that was started")
            if ok
            else tr(
                f"起動した ComfyUI を止められませんでした"
                f"(プロセス {started.process.pid}。手で止めてください)",
                f"Could not stop the ComfyUI that was started (process {started.process.pid}; stop it by"
                " hand)",
            ),
            file=sys.stderr,
        )


def _comfy_json(url: str | None, found_by: str | None, start) -> dict[str, Any]:
    d: dict[str, Any] = {"url": url, "found_by": found_by}
    if start is not None:
        d["start"] = start.to_dict()
    return d


def _comfyui_text(url: str | None, found_by: str | None, start=None) -> str:
    if url is None and start is not None:
        return tr(
            f"起動します({start.comfyui_dir}。測り終えたら止めます)",
            f"will be started ({start.comfyui_dir}; stopped after measuring)",
        )
    if url is None:
        return tr("見つかりません(起動していない)", "not found (not running)")
    if found_by == "option":
        return tr(f"{url}(--url で指定)", f"{url} (given with --url)")
    return tr(f"{url}(この PC で起動中の ComfyUI を見つけた)", f"{url} (a ComfyUI running on this PC)")


class _HeadReason(str):
    """それだけで足りる「起動しない理由」(ComfyUI がもう動いている など)。起動のしかたの案内を付けない。"""


def _not_running_lines(model_id: str, inst, why: str | None = None) -> list[str]:
    from .comfyfind import PROBE_PORTS, search_disabled

    if isinstance(why, _HeadReason):
        return [str(why)]

    ports = "・".join(str(p) for p in PROBE_PORTS)
    ports_en = " and ".join(str(p) for p in PROBE_PORTS)
    head = (
        tr(
            "ComfyUI を探していません(環境変数 GENMODELFIT_COMFYUI_SEARCH が 0)。",
            "Not looking for ComfyUI (the environment variable GENMODELFIT_COMFYUI_SEARCH is 0).",
        )
        if search_disabled()
        else tr(
            f"起動中の ComfyUI が見つかりません(この PC の 127.0.0.1 のポート {ports} を見ました)。",
            f"No running ComfyUI was found (looked at ports {ports_en} on 127.0.0.1 of this PC).",
        )
    )
    lines = [
        head,
        *([why] if why else []),
        tr(
            "ComfyUI を起動してから、同じコマンドをもう一度実行してください:",
            "Start ComfyUI, then run the same command again:",
        ),
        tr("  - ComfyUI デスクトップ版: アプリを開く", "  - ComfyUI Desktop: open the app"),
        tr(
            "  - ポータブル版: フォルダの run_nvidia_gpu.bat などを実行する",
            "  - Portable: run run_nvidia_gpu.bat (or the one for your GPU) in its folder",
        ),
        tr(
            "  - 手で入れた ComfyUI: そのフォルダで python main.py",
            "  - A manual install: python main.py in its folder",
        ),
        tr(
            "  - 別の PC・別のポートの ComfyUI: --url http://<ホスト>:<ポート> で指定する",
            "  - On another PC or port: give it with --url http://<host>:<port>",
        ),
    ]
    if inst is not None and inst.state == "no":
        lines.append(
            tr(
                f"モデルのファイルも入っていません。先に genmodelfit download {model_id} で落とせます。",
                f"The model files are not installed either; download them first with"
                f" genmodelfit download {model_id}.",
            )
        )
    return lines


def _suggestion_row(i: int, x) -> str:
    """提案の 1 行(measure の一覧と share の計画で同じもの)。"""
    kind = tr(_MODALITY_JA.get(x.modality, x.modality), x.modality)
    prec = f"、{x.precision}" if x.precision else ""
    prec_en = f", {x.precision}" if x.precision else ""
    what = " ".join([x.model_id, *x.args])  # [ ] の中をそのまま measure に渡せる(#116)
    return tr(
        f"  {i:>2}. {x.model_name} [{what}]({kind}{prec})  {_installed_text(x.installed)}  約 {x.seconds} 秒",
        f"  {i:>2}. {x.model_name} [{what}] ({kind}{prec_en})  {_installed_text(x.installed)}"
        f"  about {x.seconds} s",
    )


def _suggestion_lines(sugg: list) -> list[str]:
    from .measure import MAX_SUGGEST_SECONDS as max_secs
    from .measure import VARIANTS_PER_MODEL as per_model

    out = [
        tr(
            "=== 測ると役に立つモデル(この PC でまだ測っていないもの) ===",
            "=== Models worth measuring (not yet measured on this PC) ===",
        )
    ]
    out += [_suggestion_row(i, x) for i, x in enumerate(sugg, 1)]
    out.append(
        tr(
            "時間は目安(同じモデルの実測、無ければ種類ごとのおおよその値。"
            "設定を変えた行は画素数・コマ数・長さの比で伸び縮みさせた値。落とす時間は入れない)。"
            "--resolution・--frames・--length の付いた行は、設定を変えて測ります"
            "(予測式の確かめに使います。この PC でテンプレートのまま測ったモデルだけで、"
            f"1 つのモデルに {per_model} 件まで出します)。約 {max_secs} 秒を超えるものは出しません。"
            "入っていないものは、選ぶと download と同じ確かめをしてから落とします。",
            "Times are rough guides (from measurements of the same model, else a rough value"
            " for the type; rows with other settings are scaled by the ratio of pixels, frames or length;"
            " downloading is not included). Rows with --resolution, --frames or --length measure at those"
            " settings (used to check the estimate's formula; only for models already measured on this PC"
            f" as in the template, at most {per_model} per model). Runs over about {max_secs} s are not"
            " listed. Choosing one that is not installed downloads it first, with the same questions as"
            " download.",
        )
    )
    return out


def _settings_text(settings) -> str:
    """測る前の画面の「設定」(#116)。変えた項目と、ほかはテンプレートのままであること。"""
    from .measure import settings_dict

    d = settings_dict(settings)
    if not d:
        return tr(
            "公式のテンプレートのまま(解像度・コマ数・長さ・ステップ数)",
            "as in the official template (resolution, frames, length, steps)",
        )
    ja: list[str] = []
    en: list[str] = []
    if d.get("width") and d.get("height"):
        ja.append(f"解像度 {d['width']}x{d['height']}")
        en.append(f"resolution {d['width']}x{d['height']}")
    if d.get("frames"):
        ja.append(f"コマ数 {d['frames']}")
        en.append(f"{d['frames']} frames")
    if d.get("seconds"):
        ja.append(f"長さ {d['seconds']:g} 秒")
        en.append(f"length {d['seconds']:g} s")
    return tr(
        "、".join(ja) + "(ほかは公式のテンプレートのまま)",
        ", ".join(en) + " (the rest as in the official template)",
    )


def _measure_settings(args: argparse.Namespace):
    """measure の --resolution・--frames・--length(bench と同じ)。どれも無ければ None。"""
    from .bench import BenchError, parse_resolution
    from .models import Settings

    width = height = None
    if args.resolution:
        try:
            width, height = parse_resolution(args.resolution)
        except BenchError as e:
            raise CliError(str(e)) from e
    if width is None and args.frames is None and args.length is None:
        return None
    return Settings(width=width, height=height, frames=args.frames, seconds=args.length)


def _measure_screen(
    fit, url: str | None, found_by: str | None, inst, secs: int, basis: str, settings=None, start=None
) -> list[str]:
    m = fit.model
    kind = tr(_MODALITY_JA.get(m.modality, m.modality), m.modality)
    prec = m.precision or tr("不明", "unknown")
    return [
        tr("=== 測る前に ===", "=== Before measuring ==="),
        tr(
            f"モデル:     {fit.model_name or m.name} [{m.id}]({kind}、精度 {prec})",
            f"Model:      {fit.model_name or m.name} [{m.id}] ({kind}, precision {prec})",
        ),
        tr(f"設定:       {_settings_text(settings)}", f"Settings:   {_settings_text(settings)}"),
        tr(
            f"ComfyUI:    {_comfyui_text(url, found_by, start)}",
            f"ComfyUI:    {_comfyui_text(url, found_by, start)}",
        ),
        tr(f"ファイル:   {_installed_text(inst)}", f"Files:      {_installed_text(inst)}"),
        tr(
            f"時間:       {_seconds_text(secs, basis, settings is not None)}。"
            "初めはモデルの読み込みで長くなります",
            f"Time:       {_seconds_text(secs, basis, settings is not None)};"
            " the first run takes longer to load the model",
        ),
        tr(
            "送るもの:   何も送りません。結果はこの PC に保存するだけです"
            "(送るのは genmodelfit share で選んだときだけ)",
            "Sending:    nothing. The result is only saved on this PC (it is sent only if you choose it in"
            " genmodelfit share)",
        ),
    ]


def _measure_next_step(msg: str, model_id: str) -> str:
    """measure が測れなかったときの「次にすること」。
    bench の文の --force などは measure に無いので、ここで言い換える。"""
    if "テンプレートを取れません" in msg or "Cannot fetch the template" in msg:
        return tr(
            "次にすること: 回線(プロキシの設定も)を確かめてから、もう一度実行してください"
            "(公式のテンプレートを GitHub から取ります)。",
            "Next: check the connection (and proxy settings), then run it again (the official template is"
            " fetched from GitHub).",
        )
    if "ワークフローを受け付けませんでした" in msg or "rejected the workflow" in msg:
        return tr(
            "次にすること: 上のノードの理由を見てください。ComfyUI "
            "やカスタムノードを更新すると直ることがあります。"
            "直らなければ、この文を添えて GitHub の Issue で知らせてください。",
            "Next: read the node's reason above. Updating ComfyUI or the custom nodes may fix it; if not, "
            "tell"
            " us in a GitHub issue with this message.",
        )
    if "保存できません" in msg or "cannot be saved" in msg:
        return tr(
            "次にすること: キャッシュのフォルダ(GENMODELFIT_CACHE_DIR)が書けて、"
            "ディスクに空きがあるかを確かめてから、"
            "もう一度実行してください。",
            "Next: check that the cache folder (GENMODELFIT_CACHE_DIR) is writable and the disk has room, "
            "then"
            " run it again.",
        )
    if "秒で終わらなかった" in msg or "not finished in" in msg:
        return tr(
            "次にすること: 初めは読み込みで長くかかります。"
            "もう一度実行するか、待つ時間の上限を --timeout で延ばしてください。",
            "Next: the first run takes longer to load. Run it again, or allow more time with --timeout.",
        )
    if "ComfyUI が止まりました" in msg or "ComfyUI stopped" in msg:
        return tr(
            "次にすること: ComfyUI のログを見て、起動し直してから、もう一度実行してください。",
            "Next: read the ComfyUI log, start it again, then run it again.",
        )
    if "--force" in msg:
        return tr(
            "次にすること: ほかの生成・ゲーム・動画の書き出しなどを閉じて GPU を空け、"
            "同じコマンドをもう一度実行してください。GPU の空きを確かめられない PC では、"
            f"genmodelfit bench {model_id} --url <URL> --force で確かめずに測れます。",
            "Next: close other generation jobs, games or video exports to free the GPU, and run the same"
            " command again. On a PC where the GPU cannot be checked, genmodelfit bench"
            f" {model_id} --url <URL> --force measures without checking.",
        )
    if "ファイルがサーバーにありません" in msg or "missing required files" in msg:
        return tr(
            f"次にすること: genmodelfit download {model_id} でファイルを落とし"
            "(ComfyUI が別のフォルダを読んでいるときは --comfyui-dir でそのフォルダを指定)、"
            "ComfyUI を起動し直してから、もう一度実行してください。",
            f"Next: download the files with genmodelfit download {model_id} (use --comfyui-dir for the"
            " folder your ComfyUI reads), restart ComfyUI, then run it again.",
        )
    if "ComfyUI に無いノード" in msg or "nodes that ComfyUI does not have" in msg:
        return tr(
            "次にすること: ComfyUI を新しい版に更新してください(カスタムノードが要るものは、"
            "そのカスタムノードを入れてください)。そのあと、もう一度実行してください。",
            "Next: update ComfyUI to a newer version (or install the custom nodes it needs), then run it"
            " again.",
        )
    if "頼んだ設定" in msg or "requested settings" in msg:
        return tr(
            f"次にすること: このテンプレートでは、その設定を変えられません。設定を付けずに"
            f" genmodelfit measure {model_id} で測るか、"
            "genmodelfit measure の一覧からほかのものを選んでください。",
            f"Next: this template cannot change that setting. Measure it without settings"
            f" (genmodelfit measure {model_id}) or choose another one from the genmodelfit measure list.",
        )
    if "キュー" in msg or "queue" in msg.lower():
        return tr(
            "次にすること: ComfyUI のキューの仕事が終わるのを待ってから、もう一度実行してください。",
            "Next: wait until the jobs in ComfyUI's queue are done, then run it again.",
        )
    if not any(w in msg.lower() for w in ("接続", "通信できません", "connect", "cannot talk")):
        return tr(
            "次にすること: もう一度実行してください。直らなければ、この文を添えて GitHub の Issue "
            "で知らせてください。",
            "Next: run it again. If it still fails, tell us in a GitHub issue with this message.",
        )
    return tr(
        "次にすること: ComfyUI が起動していて、ブラウザで開けることを確かめてから、もう一度実行してください"
        "(ほかの PC・ポートなら --url)。直らなければ、この文を添えて GitHub の Issue で知らせてください。",
        "Next: check that ComfyUI is running and opens in your browser, then run it again (another PC or"
        " port: --url). If it still fails, tell us in a GitHub issue with this message.",
    )


def _share_hint(m) -> str:
    """測り終えたときの 1 行(#113)。送った実測が何に使われるかを、測り方に合わせて正直に書く
    (docs/accuracy.md「実測の使い方」)。作業用メモリだけの実測(今の NVIDIA の動的 VRAM)は照合だけに使う。
    どれも、メンテナが受け入れて次の版が出てから使われる(同梱のまとめは版を出すときに作る)。"""
    from .estimate import working_only

    when = tr(
        "メンテナが受け入れ、次の版が出てから使われます",
        "it takes effect after a maintainer accepts it and the next version is released",
    )
    if m.outcome == "oom":
        return tr(
            "genmodelfit share で送ると、「この GPU では足りない」記録として残り、この設定では実測で段階を"
            f"決めなくなります({when})",
            "Send it with genmodelfit share: it is kept as a record that this GPU was not enough, which stops"
            f" measurements from setting the level for this setup ({when})",
        )
    if working_only(m):
        return tr(
            "genmodelfit share で送ると、見積もりの確かめ(照合)に使われ、精度の向上に役立ちます"
            f"(この測り方の値は、まだ段階を決めるのには使いません。{when})",
            "Send it with genmodelfit share: it is used to cross-check the estimates and helps improve their"
            f" accuracy (values measured this way do not set levels yet; {when})",
        )
    return tr(
        "genmodelfit share で送ると、同じ GPU を使う人の見積もりに使われます"
        f"(別の人の実測とそろうと、段階も実測で決まります。{when})",
        "Send it with genmodelfit share: it is used in the estimates for people with the same GPU (once"
        f" another person's measurement agrees, it also sets the level; {when})",
    )


def _measure_download(args: argparse.Namespace, model_id: str, consented: bool = False) -> int:
    """入っていないモデルを download と同じ流れ(同じ同意の決まり)で落とす。consented は、share の「落として
    測りますか」に y と答えたとき(名前と量を見せて同意を取ってある): download のモデルごとの質問を省く
    (--yes。ライセンス不明・無検閲は download の決まりどおり、指定が無ければ落とさない)。
    利用者が落とすのをやめたら args._download_declined を真にする。"""
    argv = ["--lang", i18n.get_language(), "download", model_id]
    place = args.comfyui_dir or getattr(args, "_download_dir", None)
    if place:
        argv += ["--comfyui-dir", place]
    if args.yes or consented:
        argv.append("--yes")
    if getattr(args, "include_unknown_license", False):  # share --any-license(#124。download と同じ同意)
        argv.append("--any-license")
    if args.db:
        argv += ["--db", args.db]
    if args.no_cache:
        argv.append("--no-cache")
    ns = build_parser().parse_args(argv)
    # _main を通らないので、内容の区分の既定(sfw)をここで入れる。無検閲の同意は足さない
    ns.content = getattr(args, "content", None) or DEFAULT_CONTENT
    ns._dl_record = getattr(args, "_dl_record", None)  # この実行で落としたもの(#145。あとで消すか尋ねる)
    code = cmd_download(ns)
    args._download_declined = bool(getattr(ns, "_declined", False))
    return code


def _measure_run(
    args: argparse.Namespace, url: str, entry, settings, free: bool = False, progress=None
) -> dict[str, Any]:
    """1 つ測って保存する(measure と share で共有する。#124)。free なら測る前に ComfyUI の /free でモデルと
    前の結果(キャッシュ)を外す(続けて測ると前のモデル・前の結果が混ざるため)。bench.run で測り、結果を
    <キャッシュ>/measurements/ に、標準の条件で最後まで測れた(ok か oom)ものは送る用の提出を
    <キャッシュ>/submissions/ に書く。測れなければ error に文を入れて返す(例外にしない)。"""
    from .bench import BenchError, BenchOptions, is_local, run, save, save_decision
    from .contrib import build_submission, db_info, suggested_path, write_submission
    from .estimate import files_fingerprint
    from .measure import same_variant, settings_dict, submissions_dir
    from .workflow import WorkflowError

    out: dict[str, Any] = {
        "error": None,
        "result": None,
        "measurement": None,
        "saved_to": None,
        "not_saved": None,
        "submission": None,
        "path_in_repo": None,
        # 送れる失敗の記録 1 件(#132。contrib.failure_record)。share が集めて 1 ファイルにする
        "failure": None,
    }
    opts = BenchOptions(
        model_id=entry.id,
        url=url,
        timeout=getattr(args, "timeout", None) or 1800.0,
        precision=entry.precision,
        files_fp=files_fingerprint(entry),
        width=settings.width if settings else None,
        height=settings.height if settings else None,
        frames=settings.frames if settings else None,
        seconds=settings.seconds if settings else None,
        strict_settings=settings is not None,  # 書けない設定なら生成の前に止める(#116)
        free_before=free,
        cold_load=getattr(args, "_cold_load", None),  # share が決める(#132)
    )
    try:
        result = run(opts, log=_progress(args), progress=progress)
    except (BenchError, WorkflowError) as e:
        out["error"] = _without_workflow_hint(str(e))
        out["failure"] = _failure_from_error(e, entry, settings, opts.files_fp)
        if out["failure"]:
            out["comfyui_version"] = _comfyui_version_of(url)
        return out
    m = result.measurement
    if settings is not None and not same_variant(m.settings, settings):
        result.warnings.append(
            tr(
                "測った設定が頼んだ設定と違います(実測には測った設定を書きました): "
                f"{settings_dict(m.settings)}",
                f"The measured settings differ from the requested ones (the measured ones were recorded):"
                f" {settings_dict(m.settings)}",
            )
        )
    out_path, not_saved = save_decision(m, None, False, False)
    out["unsendable"] = None
    try:
        if out_path is not None:
            save(m, out_path)
        if m.standard and m.outcome in ("ok", "oom"):
            system = detect() if is_local(url) else None
            sub = build_submission(
                m,
                system,
                # 測ったときの見積もり(この実測とこの PC のほかの実測を使わない)・時間の内訳・一覧の版(#132)
                estimate=_measure_estimate(entry, system, m),
                timing=getattr(result, "timing", None),
                db_info=db_info(_measure_db(args)),
            )
            # 送れない(検査・個人の情報の点検に通らない)なら送る用の提出を作らない(#124)。手元の実測は残す
            why = _submission_refusal(sub, args)
            if why and ({"timing", "estimate"} & set(sub["measurement"])):
                # 見積もり・時間の内訳のおかしな値で実測を送れなくしない(#132): 外してもう一度確かめる
                bare = {
                    **sub,
                    "measurement": {
                        k: v for k, v in sub["measurement"].items() if k not in ("timing", "estimate")
                    },
                }
                if not _submission_refusal(bare, args):
                    sub, why = bare, []
            if why:
                out["unsendable"] = why
                if m.outcome == "oom":  # 実測としては送れないメモリ不足は、失敗の記録にする(#132)
                    out["failure"] = _failure_from_result(result, entry)
            else:
                out["submission"] = write_submission(sub, str(submissions_dir()) + os.sep)
                out["path_in_repo"] = suggested_path(sub)
        elif m.standard and m.outcome == "error":
            out["failure"] = _failure_from_result(result, entry)
    except OSError as e:
        # 測ったのに保存できない(キャッシュが書けない・ディスクが満杯。L5a): はっきり言う
        out["error"] = tr(
            f"測りましたが、保存できません({type(e).__name__}: {getattr(e, 'filename', '') or ''})。"
            "キャッシュのフォルダ(GENMODELFIT_CACHE_DIR で変えられる)とディスクの空きを確かめてください",
            f"Measured, but it cannot be saved ({type(e).__name__}: {getattr(e, 'filename', '') or ''})."
            " Check the cache folder (GENMODELFIT_CACHE_DIR changes it) and the free disk space",
        )
        out.update(result=result, measurement=m)
        return out
    out.update(result=result, measurement=m, saved_to=out_path, not_saved=not_saved)
    return out


def _failure_from_error(e: Exception, entry, settings, files_fp: str | None) -> dict[str, Any] | None:
    """測れなかったときの失敗の記録(#132): 足りないノード(WorkflowError.missing_nodes)・時間切れ・ComfyUI が
    受け付けなかった(BenchError.failure)だけ。ほかの理由(接続できない・ほかのプログラム・中断など)は送らない。
    文は入れない(contrib.failure_record が名前の形だけ残す)。"""
    from .contrib import failure_record

    missing = getattr(e, "missing_nodes", None)
    if missing:
        return failure_record(
            entry.id, "missing_nodes", settings=settings, missing_nodes=list(missing), files_fp=files_fp
        )
    f = getattr(e, "failure", None)
    if not isinstance(f, dict) or not f.get("outcome"):
        return None
    return failure_record(
        entry.id,
        f["outcome"],
        settings=settings,
        seconds=f.get("seconds"),
        timeout_s=f.get("timeout_s"),
        error_type=f.get("error_type"),
        node_type=f.get("node_type"),
        files_fp=files_fp,
    )


def _comfyui_version_of(url: str) -> str | None:
    """ComfyUI の版(/system_stats。失敗の記録に書く。#132)。取れなければ None(通信の誤りでも止めない)。"""
    try:
        checker = _node_check(url)
        get = getattr(checker, "comfyui_version", None)
        return get() if callable(get) else None
    except Exception:
        return None


def _failure_from_result(result, entry) -> dict[str, Any] | None:
    """最後まで動かなかった(error・送れない oom)実測の失敗の記録(#132)。中断(interrupted)は送らない。"""
    from .contrib import failure_record

    m = result.measurement
    kind = getattr(result, "error_kind", None) or {}
    if kind.get("interrupted") or not m.standard:
        return None
    return failure_record(
        entry.id,
        m.outcome,
        settings=m.settings,
        seconds=m.seconds,
        error_type=kind.get("error_type"),
        node_type=kind.get("node_type"),
        peak_vram_gb=m.peak_vram_gb,
        files_fp=getattr(m, "files_fp", None),
    )


_BUNDLED_MEASUREMENTS: list = []


def _measure_estimate(entry, system, m) -> dict[str, Any] | None:
    """測ったときの見積もり(#132)。同梱の実測(作者の実測とほかの利用者のまとめ)だけを使う: この実測と、
    この PC のほかの実測(キャッシュの measurements・送る前の提出)は入れない(入れると照合にならない)。
    同梱の実測からも、同じ GPU・同じモデルのものは除く(作者の PC の実測かもしれない)。"""
    from .contrib import measurement_estimate
    from .db import load_measurements

    try:
        if not _BUNDLED_MEASUREMENTS:
            _BUNDLED_MEASUREMENTS.extend(load_measurements(include_cache=False))
        return measurement_estimate(
            entry, system, m.settings, list(_BUNDLED_MEASUREMENTS), exclude_gpu=m.gpu_name
        )
    except Exception:  # 見積もりが作れなくても、測った結果は送れるようにする
        return None


def _measure_db(args: argparse.Namespace) -> Database | None:
    """測ったときの一覧(一覧の版を提出に書く。#132)。1 回の実行で 1 回だけ読む。読めなければ None。"""
    cached = getattr(args, "_measure_db_cache", None)
    if cached is not None:
        return cached
    try:
        db = load_db(getattr(args, "db", None), use_cache=not getattr(args, "no_cache", False))
    except Exception:
        return None
    with contextlib.suppress(AttributeError):
        args._measure_db_cache = db
    return db


_WORKFLOW_HINTS = (  # measure・share には --workflow が無いので、bench の文の --workflow の案内を言い換える
    ("名前を確かめるか、--workflow で API 形式のワークフローを渡してください", "名前を確かめてください"),
    ("Check the name, or pass an API-format workflow with --workflow", "Check the name"),
    (
        "ComfyUI の input フォルダに置くか、--workflow で別の画像を指定してください",
        "ComfyUI の input フォルダに置いてください",
    ),
    (
        "Put it in ComfyUI's input folder, or give another image with --workflow",
        "Put it in ComfyUI's input folder",
    ),
    ("ComfyUI を更新するか --workflow で API 形式を渡してください", "ComfyUI を更新すると測れます"),
    ("update ComfyUI or pass the API format via --workflow", "update ComfyUI to measure it"),
)


def _without_workflow_hint(msg: str) -> str:
    for a, b in _WORKFLOW_HINTS:
        msg = msg.replace(a, b)
    return msg


def _submission_refusal(sub: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """提出が送れない理由(share と同じ点検: 個人の情報の点検の NG と検査の誤り)。送れるなら空。"""
    from .contrib import dumps, privacy_checklist, share_check, validate_submission

    raw = dumps(sub)
    report = validate_submission(sub, _contrib_db(args), raw=raw, check_filename=False)
    return share_check(privacy_checklist(sub, raw), report)


def cmd_measure(args: argparse.Namespace) -> int:
    """measure。測るために落としたものは、終わったら(Ctrl+C・失敗でも)消すかを尋ねる(#145。share と同じ)。"""
    return _with_delete_offer(args, _cmd_measure)


def _cmd_measure(args: argparse.Namespace) -> int:
    from .downloader import DownloadError, directory_refs
    from .measure import (
        installed_state,
        resolve,
        settings_dict,
        suggestions,
        time_hint,
    )

    settings = _measure_settings(args)
    if settings is not None and not args.model:
        raise CliError(
            tr(
                "--resolution・--frames・--length は、測るモデルといっしょに指定してください"
                "(例: genmodelfit measure image_sdxl_simple --resolution 1536x1536)。"
                "引数なしの一覧には、設定を変えた測定も出ます",
                "Give --resolution, --frames or --length with the model to measure"
                " (e.g. genmodelfit measure image_sdxl_simple --resolution 1536x1536)."
                " The list without arguments also shows measurements at other settings",
            )
        )
    if args.json and args.model and not args.yes:
        raise CliError(
            tr(
                "--json で測るときは --yes が要ります(測る前に尋ねられないため)",
                "--json needs --yes to measure (the question before measuring cannot be asked)",
            )
        )
    if args.timeout is not None and args.timeout <= 0:
        raise CliError(tr("--timeout は 0 より大きい秒数にしてください", "--timeout must be above 0 seconds"))
    s, db = _system(args), _db(args)
    url, found_by, running = _measure_comfyui(args)
    roots = _measure_roots(args, running)
    start, start_why = _comfyui_start_plan(args, url)
    comfy = _comfy_json(url, found_by, start)
    if s.best_gpu is None:
        msg = tr(
            "この PC に測りに使える GPU が見つかりません(measure は GPU で測ります)。"
            "genmodelfit system で、見つかった GPU と使えない理由を確かめてください",
            "No GPU that can be measured was found on this PC (measure measures on a GPU)."
            " Run genmodelfit system to see which GPU was found and why it cannot be used",
        )
        if args.json:
            _out(_envelope("measure", s, db, action="suggest" if not args.model else "measure", error=msg))
            return 1
        raise CliError(msg)
    gpu_stop = _measure_gpu_stop(s)
    if gpu_stop:  # 判定に使える GPU が無い(#124)
        if args.json:
            _out(
                _envelope("measure", s, db, action="suggest" if not args.model else "measure", error=gpu_stop)
            )
            return 1
        raise CliError(gpu_stop)
    if not args.model:
        sugg = suggestions(db, s, roots=roots, policy=_policy(args), extra=_saved_measurements())[
            : args.limit or MEASURE_LIMIT
        ]
        if args.json:
            _out(
                _envelope(
                    "measure", s, db, action="suggest", comfyui=comfy, suggestions=[x.to_dict() for x in sugg]
                )
            )
            return 0
        if not sugg:
            print(
                tr(
                    "測ると役に立つモデルはありません"
                    "(この PC の GPU に載るものは、この PC でもう測ってあります)。"
                    "ほかのモデルを測るなら genmodelfit measure <名前>",
                    "No model to suggest (the ones that fit this PC's GPU are already measured on this"
                    " PC)."
                    " To measure another model: genmodelfit measure <name>",
                )
            )
            return 0
        skipped: list[dict[str, Any]] = []
        if url is not None and _interactive():
            # 起動中の ComfyUI に無いノードの要るものは、選べないように外す(#124)
            checker = _node_check(url)
            keep = []
            for x in sugg:
                miss = checker.missing(x.model_id)
                if miss:
                    skipped.append({"model_id": x.model_id, "name": x.model_name, "missing_nodes": miss})
                else:
                    keep.append(x)
            sugg = keep
        _emit(_suggestion_lines(sugg) + _skipped_lines(skipped))
        if not sugg:
            return 0
        if not _interactive():
            sys.stdout.flush()
            print(
                tr(
                    "測るときは genmodelfit measure <ID>([ ] の中をそのまま。設定の引数があればそれも)",
                    "To measure one: genmodelfit measure <id> (what is in [ ], with the settings if any)",
                ),
                file=sys.stderr,
            )
            return 0
        if url is None and start is None:
            # 選ばせてから止めない(#123)。一覧は見せ、起動の仕方を出して終える
            sys.stdout.flush()
            lines = _not_running_lines("", None, start_why)
            print(tr(f"エラー: {lines[0]}", f"error: {lines[0]}"), file=sys.stderr)
            for line in lines[1:]:
                print(line, file=sys.stderr)
            return 1
        try:
            k = _choose_number(len(sugg))
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        if k is None:
            print(tr("測りませんでした", "Nothing measured"), file=sys.stderr)
            return 0
        fit, settings = sugg[k].fit, sugg[k].settings
        print()
    else:
        try:
            fit = resolve(db, s, args.model, policy=_policy(args))
        except DownloadError as e:
            err = CliError(str(e).replace('content="all"', "--uncensored"))
            err.candidates = getattr(e, "candidates", None) or None  # type: ignore[attr-defined]
            raise err from e
    entry = fit.model
    inst = installed_state(entry, roots, directory_refs(db))
    secs, basis = time_hint(entry, db.measurements, settings)
    plan = {
        "model_id": entry.id,
        "settings": settings_dict(settings),
        "comfyui": comfy,
        "installed": inst.to_dict(),
        "time_hint": {"seconds": secs, "basis": basis},
    }

    def fail(msg: str, lines: list[str] | None = None) -> int:
        if args.json:
            _out(_envelope("measure", s, db, action="measure", **plan, error=msg))
        else:
            sys.stdout.flush()  # 見せた画面のあとに誤りを出す(パイプ・ログで順が入れ替わらないように)
            print(tr(f"エラー: {msg}", f"error: {msg}"), file=sys.stderr)
            for line in lines or []:
                print(line, file=sys.stderr)
        return 1

    if not args.json:
        _emit(_measure_screen(fit, url, found_by, inst, secs, basis, settings, start))
        sys.stdout.flush()  # 画面を先に(このあとの stderr の途中経過と順が入れ替わらないように)
    if url is None and start is None:
        lines = _not_running_lines(entry.id, inst, start_why)
        return fail(lines[0], lines[1:])
    if url is not None:
        # 足りないノードがあれば、落とす・測る前に止める(#124)
        from .workflow import missing_nodes_message

        checker = _node_check(url)
        miss = checker.missing(entry.id)
        if miss:
            from .contrib import failure_record

            # 測る前に分かった足りないノードも、失敗の記録として保存する(#132。share と同じ)
            rec = failure_record(entry.id, "missing_nodes", missing_nodes=list(miss), before_run=True)
            saved = _measure_save_failure(args, s, url, {"failure": rec}, checker)
            if saved is not None:
                plan["failures_file"] = str(saved)
            msg = missing_nodes_message(miss, workflow_hint=False)
            return fail(msg, [_measure_next_step(msg, entry.id)])
    if inst.state == "no" and args.json:
        return fail(
            tr(
                f"モデルのファイルが入っていません。"
                f"先に genmodelfit download {entry.id} --yes で落としてください",
                f"The model files are not installed. Download them first with"
                f" genmodelfit download {entry.id} --yes",
            )
        )
    if not args.yes:
        if not _interactive():
            return fail(
                tr(
                    "端末でないときは --yes が要ります(測る前に尋ねられないため)",
                    "Without a terminal, --yes is needed (the question cannot be asked)",
                )
            )
        if inst.state == "no":
            print(
                tr(
                    "モデルのファイルが入っていないので、先に落とします"
                    "(このあと download と同じ確かめをします)。",
                    "The model files are not installed, so they are downloaded first (with the same questions"
                    " as download).",
                )
            )
        try:
            ans = input(tr("測りますか? [Y/n] ", "Measure it? [Y/n] "))
        except EOFError:
            ans = "n"
        except KeyboardInterrupt:
            print(file=sys.stderr)
            return 130
        if ans.strip().lower() not in ("", "y", "yes"):
            print(tr("測りませんでした", "Nothing measured"), file=sys.stderr)
            return 0  # やめただけで、誤りではない(share の q と同じ)
    if inst.state == "no":
        code = _measure_download(args, entry.id)
        if code != 0:
            if getattr(args, "_download_declined", False):
                print(tr("やめました(落とさないので測りません)", "Stopped (not downloaded, so not measured)"))
                return 0
            return code
        print()
    holder: list = []
    lock = _measure_lock()  # 同時に測らない(L1c)
    try:
        if start is not None:
            busy = _vram_busy_before_start(s.best_gpu)
            if busy:
                return fail(busy)
            try:
                started = _comfyui_start(args, start, holder)
            except CliError as e:
                return fail(str(e))
            url = started.url
            plan["comfyui"]["started"] = started.to_dict()
        # 自分で起動した ComfyUI なら初めての読み込み(#132)。起動していた ComfyUI では分からない
        args._cold_load = True if holder else None
        # 自分で起動した ComfyUI か --free なら、測る前にメモリと前の結果を空ける(#124)
        progress, view = _run_live(args, tr(f"測る: {entry.id}", f"Measure: {entry.id}"), secs)
        try:
            run_ = _measure_run(
                args,
                url,
                entry,
                settings,
                free=bool(holder) or bool(getattr(args, "free", False)),
                progress=progress,
            )
        finally:
            view.done()
    finally:
        for st in holder:
            _comfyui_stop(args, st)
        _measure_unlock(lock)
    failures_file = _measure_save_failure(args, s, url, run_)
    if failures_file is not None:
        plan["failures_file"] = str(failures_file)
        if not args.json:
            print(
                tr(
                    f"失敗の記録を保存しました(次の genmodelfit share でいっしょに送れます): {failures_file}",
                    f"Saved a failure record (the next genmodelfit share can send it): {failures_file}",
                ),
                file=sys.stderr,
            )
    if run_["error"] is not None:
        return fail(run_["error"], [_measure_next_step(run_["error"], entry.id)])
    m, result = run_["measurement"], run_["result"]
    out_path, not_saved = run_["saved_to"], run_["not_saved"]
    sub_path, sub_repo = run_["submission"], run_["path_in_repo"]
    unsendable = run_.get("unsendable")
    hint = _share_hint(m)
    code = 1 if result.failed else 0
    if args.json:
        _out(
            _envelope(
                "measure",
                s,
                db,
                action="measure",
                **plan,
                **result.to_dict(),
                saved_to=str(out_path) if out_path else None,
                not_saved=not_saved,
                submission={"saved_to": str(sub_path), "path_in_repo": sub_repo} if sub_path else None,
                hint=hint if sub_path else None,
            )
        )
        return code
    for w in result.warnings:
        print(tr(f"注意: {w}", f"warning: {w}"), file=sys.stderr)
    print()
    print(tr(f"=== 結果: {m.model_id} ===", f"=== Result: {m.model_id} ==="))
    print(
        tr(
            f"GPU:       {m.gpu_name}({m.gpu_vram_gb:g} GiB)",
            f"GPU:       {m.gpu_name} ({m.gpu_vram_gb:g} GiB)",
        )
    )
    print(tr(f"結果:      {m.outcome}", f"Result:    {m.outcome}"))
    print(tr(f"最大 VRAM: {m.peak_vram_gb:.2f} GB", f"Peak VRAM: {m.peak_vram_gb:.2f} GB"))
    if m.seconds is not None:
        print(tr(f"時間:      {m.seconds:g} 秒", f"Time:      {m.seconds:g} s"))
    if result.failed:
        print(tr(f"失敗:      {result.failed}", f"Failed:    {result.failed}"))
    print(
        tr(f"保存先:    {out_path}", f"Saved to:  {out_path}")
        if out_path
        else tr(f"保存:      {not_saved}", f"Not saved: {not_saved}")
    )
    if sub_path is not None:
        print(tr(f"送る用:    {sub_path}", f"To send:   {sub_path}"))
        print(hint)
    elif unsendable:
        print(
            tr(
                "送れません(この PC の実測としては保存しました): " + " / ".join(unsendable),
                "Cannot be sent (kept as this PC's measurement): " + " / ".join(unsendable),
            )
        )
    return code


# --- genmodelfit config(#111。docs/download.ja.md「genmodelfit config」) --------------------------------


def _config_settings(args: argparse.Namespace, values: dict[str, str]) -> dict[str, dict[str, Any]]:
    """それぞれの設定の今の値と出どころ(option / env / file / default)。"""
    if getattr(args, "lang", None):
        lang = {"value": args.lang, "from": "option"}
    elif i18n.normalize(os.environ.get(i18n.ENV_VAR)) is not None:
        lang = {"value": i18n.normalize(os.environ.get(i18n.ENV_VAR)), "from": "env"}
    elif values.get("lang"):
        lang = {"value": values["lang"], "from": "file"}
    else:
        lang = {"value": "en", "from": "default"}
    env_dir = os.environ.get(COMFYUI_DIR_ENV, "").strip()
    if env_dir:
        comfy = {"value": env_dir, "from": "env"}
    elif values.get("comfyui-dir"):
        comfy = {"value": values["comfyui-dir"], "from": "file"}
    else:
        comfy = {"value": None, "from": "default"}
    return {"comfyui-dir": comfy, "lang": lang}


def _from_label(key: str, src: str) -> str:
    env = COMFYUI_DIR_ENV if key == "comfyui-dir" else i18n.ENV_VAR
    return {
        "option": tr("指定(--lang)", "option (--lang)"),
        "env": tr(f"環境変数 {env}", f"environment variable {env}"),
        "file": tr("設定ファイル", "config file"),
        "default": tr("既定", "default"),
    }[src]


def cmd_config(args: argparse.Namespace) -> int:
    from . import comfyfind, config

    action = args.action or "show"
    path = config.config_path()
    head = {"genmodelfit": __version__, "command": "config", "action": action, "path": str(path)}
    try:
        if action == "path":
            if args.json:
                _out(head)
            else:
                print(path)
            return 0
        if action == "set":
            key, value = args.key, args.value
            config._check_key(key)
            if key == "comfyui-dir":
                p = Path(os.path.abspath(Path(value).expanduser()))
                if not comfyfind.looks_like_comfyui(p):
                    raise CliError(
                        tr(
                            f"ComfyUI のフォルダに見えません: {p}(main.py・comfy・nodes.py・"
                            "comfy_extras・comfy_api のうち 4 つ、または ComfyUI デスクトップ版のフォルダ"
                            "(models・input・user・output・custom_nodes)が要ります)",
                            f"This does not look like a ComfyUI folder: {p} (needs 4 of main.py, comfy,"
                            " nodes.py, comfy_extras, comfy_api, or a ComfyUI Desktop folder: models, input,"
                            " user, output, custom_nodes)",
                        )
                    )
                value = str(p)
            elif value not in LANGUAGES:
                raise CliError(
                    tr(
                        f"lang の値が正しくありません: {value!r}(使えるのは {', '.join(LANGUAGES)})",
                        f"Invalid lang: {value!r} (choose from {', '.join(LANGUAGES)})",
                    )
                )
            written = config.set_value(key, value)
            if args.json:
                _out({**head, "path": str(written), "key": key, "value": value})
            else:
                print(tr(f"保存しました: {key} = {value}({written})", f"Saved: {key} = {value} ({written})"))
            return 0
        if action == "unset":
            written, removed = config.unset(args.key)
            if args.json:
                _out({**head, "path": str(written), "key": args.key, "removed": removed})
            elif removed:
                print(tr(f"消しました: {args.key}({written})", f"Removed: {args.key} ({written})"))
            else:
                print(tr(f"{args.key} は設定されていません", f"{args.key} is not set"))
            return 0
    except config.ConfigError as e:
        raise CliError(str(e)) from e
    values, notes = config.load()
    settings = _config_settings(args, values)
    if args.json:
        _out({**head, "settings": settings, "warnings": notes})
        return 0
    exists = tr("ある", "exists") if path.is_file() else tr("まだ無い", "not created yet")
    lines = [tr(f"設定ファイル: {path}({exists})", f"config file: {path} ({exists})")]
    for key, v in settings.items():
        if key == "comfyui-dir" and v["value"] is None:
            value = tr("(未設定。download が自動で探す)", "(not set; download looks for ComfyUI)")
        else:
            value = str(v["value"])
        lines.append(f"  {key:<12} {value}  ({_from_label(key, v['from'])})")
    lines += [tr(f"注意: {n}", f"warning: {n}") for n in notes]
    lines.append(
        tr(
            "変える: genmodelfit config set comfyui-dir <フォルダ> / set lang ja、"
            "消す: genmodelfit config unset <キー>",
            "change: genmodelfit config set comfyui-dir <folder> / set lang ja;"
            " remove: genmodelfit config unset <key>",
        )
    )
    _emit(lines)
    return 0


# --- 自動でおすすめに出せるか(gate.py) --------------------------------------------------------


def _policy(args: argparse.Namespace):
    cached = getattr(args, "_gate_policy", None)
    if cached is not None:
        return cached
    from .gate import DEFAULT_LICENSE_ALLOWLIST, DEFAULT_MAX_HEAD_AGE_DAYS, GatePolicy, load_allowlist

    allow = DEFAULT_LICENSE_ALLOWLIST
    if getattr(args, "license_allowlist", None):
        allow = load_allowlist(args.license_allowlist)
        if not allow:
            raise CliError(tr("--license-allowlist が空です", "--license-allowlist is empty"))
    policy = GatePolicy(
        license_allowlist=allow,
        comfyui_version=getattr(args, "comfyui_version", None),
        allow_uncensored=bool(getattr(args, "allow_uncensored_auto", False)),
        max_head_age_days=getattr(args, "max_head_age_days", DEFAULT_MAX_HEAD_AGE_DAYS),
    )
    args._gate_policy = policy  # fit では全モデルで使うので、許可リストのファイルを読むのは 1 回だけ
    return policy


def _gate_for(model, args, db: Database | None = None) -> dict[str, Any] | None:
    from .gate import evaluate_offline

    return evaluate_offline(model, _policy(args), sources=db.sources if db else None).to_dict()


# --- 引数 ---------------------------------------------------------------------------------------


def _positive_int(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(tr(f"整数ではありません: {v}", f"not an integer: {v}")) from None
    if n < 1:
        raise argparse.ArgumentTypeError(tr(f"1 以上を指定してください: {v}", f"must be 1 or more: {v}"))
    return n


def _head_age(v: str) -> int | None:
    if v.strip().lower() == "none":
        return None
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(
            tr(f"日数(整数)か none を指定してください: {v}", f"give a number of days (integer) or none: {v}")
        ) from None
    if n < 0:
        raise argparse.ArgumentTypeError(tr(f"0 以上を指定してください: {v}", f"must be 0 or more: {v}"))
    return n


_JSON_FIELDS = """  JSON (--json): { genmodelfit, command, system: {...}, database: {...}, filters: {...},
  matched, hidden: {reason: count}, groups: [{modality, matched, shown}],
  models: [{ id, name, model_name,
  modality, tasks, use, content, source, runtime, languages, description, fit_level, fit_label,
  run_mode, run_mode_label, score, score_components { memory_fit }, popularity, memory_kind,
  memory_required_gb, memory_available_gb, utilization_pct, gpu_memory_required_gb,
  ram_required_gb, disk_size_gb, total_size_gb, license, licenses, auto,
  held_reasons, estimate_confidence, notes,
  files_count, precision, group_key, variants: [{precision, workflow_id, ...}],
  workflows: [{id, name, tasks, use, precision, fit_level, ...}], estimate, gate }] }"""
_JSON_KEYS = _JSON_FIELDS  # JSON の鍵の並び(両方の言語で同じ)
_JSON_FIELDS = (
    _JSON_KEYS
    + """
  One item per model (same weights family + size); --all-workflows gives one per workflow.
  Details: docs/schema.md"""
)

_DETECT_HELP = (
    "  Nothing to prepare. The GPU and RAM are detected; --vram / --ram override them. GPU: NVIDIA\n"
    "  via nvidia-smi; otherwise AMD / Intel / Apple via WMI (Windows), lspci + sysfs (Linux) or\n"
    "  system_profiler (macOS).\n"
)

_COMMON_HELP = (
    """PRECONDITIONS:
"""
    + _DETECT_HELP
    + """  The bundled model list is used unless `genmodelfit update` stored a newer one (--no-cache).

SIDE EFFECTS:
  None - read-only. No network access.

EXIT CODES:
  0  Success{zero}
  1  Error{one} (e.g. a broken --db file; with --json the error is in "error")
  2  Usage error (unknown option or value)
"""
)

_HELP_EN = {
    "recommend": """Recommend generation models for this PC, best first.

Without -m / --task: one section per type (image, video, music, sfx, tts, 3d), top -n
(default 3) each. With -m or --task: one table (default 10).
One row per model; the precision and workflow that fit best are chosen (--all-workflows: all).
Order: models that run, then Perfect/Good, then Marginal (faster mode first), official
templates, popularity (rank among the same type and source), Score, id (--json puts
auto-recommendable ones, gate: auto, before the levels). Score 0-100 is the memory headroom
(100 up to 70% use; not speed). Score rules: docs/scoring.md. Uncensored models and
utility tools are hidden unless asked. The output is a table; add --json for apps.

"""
    + _COMMON_HELP.format(zero="", one="")
    + """
AGENT USAGE:
  genmodelfit recommend --json
  genmodelfit recommend -m image --task edit -n 5 --min-fit good --json
  genmodelfit recommend -m video,music --auto-only --comfyui-version 0.40.0 --json
  genmodelfit recommend --license apache-2.0,mit --vram 8 --ram 32 --json

"""
    + _JSON_FIELDS,
    "fit": """Judge the models for this PC and show them in one table (not cut to the top few like recommend).
By default only the models that run (--fit runnable: perfect, good, marginal), without utility
tools and models with files of unknown license. --all also shows too_tight and unknown ones, utility
tools and unknown licenses; uncensored models need --uncensored (with or without --all).
Order: type, then fit level, official templates, popularity, Score (--sort changes it).

"""
    + _COMMON_HELP.format(zero="", one="")
    + """
AGENT USAGE:
  genmodelfit fit --json
  genmodelfit fit -m video --task i2v --min-fit good -n 20
  genmodelfit fit --uncensored --all-workflows --vram 12 --ram 32 --json

"""
    + _JSON_FIELDS,
    "search": """Search models by a part of the id or name (case-insensitive) and judge them for this PC.
--content applies (default sfw). With no match the list is empty and the exit code is 0.

"""
    + _COMMON_HELP.format(zero=' (also when nothing matches: empty models / "no match")', one=": blank query")
    + """
AGENT USAGE:
  genmodelfit search flux --json
  genmodelfit search wan -m video --min-fit good
  genmodelfit search sdxl --uncensored --json

"""
    + _JSON_FIELDS.replace("filters: {...}", "filters: {query, ...}"),
    "types": """List the big types (image, video, music, sfx, tts, 3d) and the uses inside each type,
with counts. The use names are what --task accepts.

"""
    + _COMMON_HELP.format(zero="", one="").replace(
        _DETECT_HELP,
        "  Nothing to prepare. The PC is not judged.\n",
    )
    + """
AGENT USAGE:
  genmodelfit types
  genmodelfit types --json

  JSON (--json): { genmodelfit, command, database, filters: {content}, types: [{ modality,
  label, label_ja, models, workflows, uses: {use: workflows} }], uses: [{ use, label,
  label_ja, tasks }], aliases }
  Details: docs/schema.md""",
    "info": """Show one model: files, licenses, estimate, limits on this PC, the gate checks, and the
other workflows that use the same model.
An exact id always matches (uncensored included). When the id does not match, a part of the id or name is
searched within --content; several matches are an error that lists the candidates.

"""
    + _COMMON_HELP.format(zero="", one=": no such model or several matches (candidates listed); other errors")
    + """
AGENT USAGE:
  genmodelfit info image_z_image_turbo_int8 --json
  genmodelfit info sdxlturbo --vram 8 --ram 32

  JSON (--json): { genmodelfit, command, system, database, model: ModelEntry, estimate,
  gate, measurements, group_key, same_model: [{id, name, tasks, use, precision, fit_level,
  memory_required_gb, disk_size_gb, auto, matched}] }
  On error: { error, candidates? }.
  Details: docs/schema.md""",
    "system": """Show this PC (GPU, RAM, free disk) and whether the model list in use is still fresh
(Data: source, HEAD check dates, expired / valid until, auto-recommendable count).

"""
    + _COMMON_HELP.format(
        zero=" (also when the list has expired: see Data / database.status.expired;"
        " run genmodelfit update --from-release to renew it)",
        one="",
    )
    + """
AGENT USAGE:
  genmodelfit system --json
  genmodelfit system --comfyui-version 0.40.0 --json

  JSON (--json): { genmodelfit, command, system, database: { origin, generated_at, models,
  measurements, status: { source, generated_at, heads_checked_oldest, heads_checked_newest,
  max_head_age_days, expired, expires_at, models, auto_count, auto_count_if_fresh,
  checked_at } } }
  Details: docs/schema.md""",
    "update": """Get a newer model list.
Users: run `genmodelfit update --from-release`.
--from-release takes in the signed latest list published by this project (only db.json and
db.json.sig are downloaded) and checks the signature; after that, the other commands use it instead
of the bundled list (--no-cache goes back to the bundled one).
Without options, update rebuilds the whole list from upstream (ComfyUI templates, the Hugging Face
Hub, a HEAD request to every download URL). Rebuilding is for maintainers: it is slow (many requests;
Hugging Face rate limits make it wait, see --rate-limit-budget). Maintainers can also redo only the
HEAD checks (--heads-only), re-apply only the app fields and the hand-added models (--fields-only),
or re-read only the file headers (--inspect-only).
The policy options (--comfyui-version, --max-head-age-days, ...) only change the
auto-recommendable count printed at the end. --db / --no-cache choose the list that --heads-only,
--fields-only and --inspect-only start from.

PRECONDITIONS:
  Network access.

SIDE EFFECTS:
  Network: GET / HEAD only. update reads the ComfyUI workflow templates and releases (GitHub),
  the Hugging Face Hub API and the model download URLs (HEAD, no download of weights);
  --from-release downloads only db.json and db.json.sig from this project's GitHub release.
  Nothing about this PC is sent (User-Agent: genmodelfit/<version>; no ~/.netrc credentials).
  Writes the list to the cache (or --output). The cache is left untouched on failure.

EXIT CODES:
  0  Success
  1  Error (network, bad signature, older list, ...; with --json the error is in "error")
  2  Usage error
""",
    "diff": """Compare two model lists (db.json) and show what changed for users.
Shown are added / removed models and, for each changed model (name (type) — id), the changes below.
Changes of a model: added / removed / changed files (with their license and source), license (the
permissive side is marked with !), newly uncensored, runtime / use, auto-recommendation, prompt /
speech languages, size, precision, review status, and the names of any other changed fields
("other changes"). Changed models are in three sections, each cut by
--limit separately: files / license / uncensored / runtime and use first, then auto-recommendation
changes (ids always listed), then license provenance, languages, size, review marks and the rest;
within a section the most important change comes first, so --limit never hides it; the same
low-importance change on many models (e.g. a review mark added to all) is one line with a count.
Every changed field is shown somewhere (or counted for --all). The raw JSON is compared, so
unknown keys count (as in merge-check).

With no arguments, compares the bundled list with the list fetched by `update` (the cache).
With one argument, compares the bundled list with that file. With two, compares the two files.
--all adds the maintenance view (the content of the weekly PR): all failed HEAD checks (not only
new ones), 401 repos, the changed numbers in `sources`, models where only popularity numbers,
dates or HEAD results changed, and the ids of collapsed lines. With --all there is no line limit
unless -n is given (without --all the default is -n 20 per section).

PRECONDITIONS:
  No arguments: a fetched list in the cache (run `genmodelfit update --from-release` first; without
  it, a short how-to is printed and the exit code is 0). With arguments: one or two db.json files.
  A warning is printed when the new list is older (generated_at) than the old one.

SIDE EFFECTS:
  None - read-only. No network access.

EXIT CODES:
  0  Success (also when there are differences, and when there is no fetched list yet)
  1  Error (a file cannot be read or has another schema; with --json the error is in "error")
  2  Usage error

AGENT USAGE:
  genmodelfit diff                          # bundled list vs the fetched list
  genmodelfit diff new.json                 # bundled list vs new.json
  genmodelfit diff old.json new.json --json
  genmodelfit diff old.json new.json --all  # maintenance view

  The auto-recommendation is compared with one policy for both lists: the ComfyUI version recorded
  in the new list (or --comfyui-version) and no HEAD age limit (HEAD failures still count).
  JSON (--json): { genmodelfit, command, old, new, old_label, new_label, policy, summary, added,
  removed, license_changes, auto_changes, size_changes, precision_changes, newly_uncensored,
  head_failures, unauthorized, partial_counts, stats, classification, field_changes,
  model_changes, top_changes, warnings }
  With no fetched list: the same keys, empty, plus "note".
  Details: docs/schema.md""",
    "download": """Download the files of a model for this PC (all files of one workflow), checking sha256.

MODEL is a model name (as in the tables), a workflow id, or a part of either (like info).
For a model name, the workflow is chosen like recommend: the highest precision that runs on
this PC without offloading (good or better on the GPU); when none does, the best fit level,
then the faster run mode, then the higher precision. The plan says which rule chose it.
--precision downloads the workflow of that precision instead.
--budget prefers the best workflow whose download stays under the limit; when none does, the
download is refused.
Files go to <ComfyUI>/models/<folder>/<file>, where the folder comes from the template
(diffusion_models, text_encoders, vae, loras, checkpoints, ...).
The place is the first of: --comfyui-dir (for a folder outside ComfyUI, --output-dir, with the
same order of priority), the environment variable GENMODELFIT_COMFYUI_DIR, comfyui-dir saved
with `genmodelfit config`, or a ComfyUI found automatically (the current folder, a ComfyUI
running on 127.0.0.1 port 8188 or 8000, the ComfyUI Desktop and comfy-cli settings, usual
places such as C:/ComfyUI). The plan always says which of these decided the place.
When no place or several places are found, a terminal lets you pick one or type a path, while
with --yes, --json or without a terminal nothing is downloaded (exit 1).
--list does not ask a running ComfyUI, so it may show the place as "not decided" although the
real download would find one (pass --comfyui-dir to see the exact plan).
An is_default section in the ComfyUI folder's extra_model_paths.yaml is honored.
Files whose ComfyUI folder is unknown go to other/; ComfyUI does not read other/, so move them to
the right folder (diffusion_models, ...) before use.
Before downloading, the files, each license and its URL (or the page to check when no license
is stated), the total size and the free disk are shown and you are asked [y/N/c] (c changes
the place; --yes skips the question). The first time a place found automatically is used, you
are asked whether to save it for next time ([Y/n]; `genmodelfit config` shows or removes it).
Only a ComfyUI folder can be saved: an --output-dir place must be given each time.
Uncensored models and files with an unknown license need your consent: in a terminal the
question names them (y/N); with --yes, --json or without a terminal pass --uncensored and/or
--any-license (old forms: --content all, --include-unknown-license; consent flags must be
written in full). When only the consent is missing, the plan ends with one command that adds
it (genmodelfit never adds it by itself).
Refused (no option lifts these): gated files (no login is used), too little disk (needs the
download + 1 GB), names that would land outside the folder, hosts other than Hugging Face /
GitHub.
To use the files: reload ComfyUI in the browser if it was open, then open, from ComfyUI's template
list, the template titled as in the first line of info (the id in [ ] is the template's file
name). Models whose id starts with hf: have no ComfyUI template: their
Hugging Face page (https://huggingface.co/<owner>/<repo>) says how to use them.

PRECONDITIONS:
  Network access to huggingface.co (or github.com) for the download. --list needs none.

SIDE EFFECTS:
  Network: GET of the listed URLs only (https; every redirect must stay on Hugging Face /
  GitHub hosts). Nothing about this PC is sent (User-Agent: genmodelfit/<version>; no
  ~/.netrc credentials, no tokens). Writes only inside the destination folder: <file>.part
  while downloading (kept on Ctrl+C or failure; the next run resumes it with HTTP Range),
  renamed to <file> after the sha256 matches. A sha256 mismatch deletes the file. Existing
  files whose sha256 matches are skipped; other existing files are never overwritten.
  When the place is not given (no option, environment variable or config), a ComfyUI running
  on this PC is asked where its folders are (GET http://127.0.0.1:8188 and :8000
  /internal/folder_paths, 2 s timeout). Answering Y to "next time" writes comfyui-dir to the
  config file (`genmodelfit config path`).
  --list: no network access (a running ComfyUI is not asked) and nothing is written.

EXIT CODES:
  0  Success (also --list, even when the plan is blocked: see "blocked")
  1  Error: no such model / several matches, refused (see above), no consent, declined,
     network, sha256 mismatch (with --json the error is in "error")
  2  Usage error
  130  Interrupted (Ctrl+C); run again to resume

AGENT USAGE:
  genmodelfit download "Z-Image-Turbo" --list --json
  genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI --yes --json
  genmodelfit download image_z_image_turbo_int8 --output-dir D:/models --budget 15 --yes

  JSON (--json): { genmodelfit, command, system, database, listed, plan: { model_id, name,
  model_name, precision, fit_level, layout, root, files: [{ name, relpath, dest, url, sha256,
  size_bytes, directory, directory_basis, license, license_url, check_url, license_allowed,
  gated, status, part_bytes, remaining_bytes, reason }], total_bytes, download_bytes,
  disk_free_bytes, disk_needed_bytes, blocked, blocked_codes, warnings, alternatives, ok },
  result: { downloaded, resumed,
  restarted, skipped, bytes_downloaded, seconds, files } | null, destination_from,
  comfyui_dir, extra_model_paths, destination_candidates, error? }
  Details: docs/schema.md""",
    "config": """Show or change the settings of the genmodelfit command line (the Python API never reads
them).

Keys:
  comfyui-dir  the ComfyUI folder download uses (after --comfyui-dir / --output-dir and
               GENMODELFIT_COMFYUI_DIR). `set` checks that the folder looks like ComfyUI
               (4 of main.py, comfy, nodes.py, comfy_extras, comfy_api, or a ComfyUI Desktop
               folder: models, input, user, output, custom_nodes). A folder outside ComfyUI
               (download --output-dir) cannot be saved: pass --output-dir each time.
  lang         the display language, en or ja (--lang and GENMODELFIT_LANG win).
Without an action, shows each setting and where it comes from (option, environment variable,
config file, default).

PRECONDITIONS:
  None.

SIDE EFFECTS:
  No network access. set / unset write the config file (config.json in the user config folder
  of genmodelfit; GENMODELFIT_CONFIG_DIR changes the folder). show and path write nothing.
  A broken file is not read (a warning is shown) and set / unset do not overwrite it.

EXIT CODES:
  0  Success
  1  Error: unknown key, invalid value, broken or unwritable file
  2  Usage error

AGENT USAGE:
  genmodelfit config --json
  genmodelfit config set comfyui-dir D:/ComfyUI
  genmodelfit config unset comfyui-dir
  genmodelfit config path

  JSON (--json): { genmodelfit, command, action, path, settings: { comfyui-dir: { value, from },
  lang: { value, from } }, warnings } for show; { ..., action, path, key, value } for set,
  { ..., key, removed } for unset, { ..., path } for path.
  Details: docs/schema.md""",
    "gate": """Count how many models in the list can be recommended automatically (Verified, "auto" in
JSON) and why the others are held. The checks are the same as in info (one model) and in the
"auto" / held_reasons fields of recommend --json.

How to read the output: the first line is the ComfyUI version the checks used, and the second the
number of models, how many are auto and how many held. The table has one row per check: "failing" is how many
models fail that check, "first-failing" how many models fail it as their first failed check (the
check to fix first). --only-auto / --only-held also list the model ids (held ones with the first
reason). The checks and the allowlist: docs/gate.md

"""
    + _COMMON_HELP.format(zero="", one=': an empty --license-allowlist ("," or an empty file)').replace(
        _DETECT_HELP, "  Nothing to prepare. The PC is not judged.\n"
    )
    + """
AGENT USAGE:
  genmodelfit gate
  genmodelfit gate -m video --only-held
  genmodelfit gate --comfyui-version 0.40.0 --json

  JSON (--json): { genmodelfit, command, database, policy, filters, summary: { models, auto, held,
  failing, first_failing }, models: [{ id, auto, held_by, checks, ... }] }
  Details: docs/schema.md""",
    "measure": """Measure a model on ComfyUI (started if needed) and save the result on this PC
(nothing is sent).
Without MODEL it lists models worth measuring: the ones that fit this PC's GPU (good or better on
the GPU) and are not yet measured on this PC at the template's settings (other people's
measurements of the same GPU do not remove a model: two agreeing sources let measurements set the
level), with a known license, those already installed first, then the shorter time, then the
smaller download; choose one by number (q quits). Models that need nodes the running ComfyUI
lacks are left out. Once a model has that measurement, the list offers runs at other settings
that help check the estimate's formula (#116): image resolution 512/768/1024/1536 px (text to
image), video frames 17/33/49/81 at the template's size (text or image to video), music and sound
length 10/30/60/120 s (text to audio); each only when it fits the GPU at that setting (good or
better) and this PC has not measured it at that setting, at most 2 per model. Runs over about
300 s are not listed. Each run is one generation (a few minutes); running measure again picks
the next one.
MODEL is a model name, a workflow id or a part of either, chosen like download (only workflows
with a ComfyUI template); --resolution, --frames and --length change the settings like bench.
Before measuring one screen shows the model and precision, the settings (as in the template, or
the ones changed), which ComfyUI and how it was found, whether the files are installed, a rough
time and that nothing is sent, then asks [Y/n] (--yes skips it). The time is a rough guide: the
median of measurements of the same model, else a rough value per type, scaled by the ratio of
pixels, frames or length when the settings are changed. When the template cannot take a
requested setting, or the ComfyUI lacks nodes it needs, it stops before downloading or
generating.
A model that is not installed is downloaded first, with the same questions and consent rules as
download. At the end (also after a failure or Ctrl+C) it lists what it downloaded with the total
size and asks [y/N] whether to delete it: only files this run newly downloaded, never ones that
were already there; --yes or no terminal keeps them, --delete-downloaded deletes without asking.
A ComfyUI started by genmodelfit is freed (/free: models unloaded, cached results
dropped) before the run; yours only with --free. Afterwards it shows the result and where it
saved it, and one line pointing to genmodelfit share, which sends it (it never asks whether to
send). A run that could not be measured is saved as a failure record that share sends.

PRECONDITIONS:
  A GPU on this PC and a ComfyUI on it: --url, or one running on this PC (127.0.0.1 port 8188
  or 8000). When none is running but a ComfyUI folder is found (like download), it is started
  in the background (portable: its python_embeded; manual or comfy-cli: its .venv/venv; Desktop:
  its .venv and the app's ComfyUI; on 127.0.0.1 and a free port, no browser), used, and stopped
  after measuring; the [Y/n] question covers it (--no-start or GENMODELFIT_COMFYUI_START=0
  turns this off; --start-timeout, default 300 s). Otherwise it says how to start ComfyUI and
  exits 1. The GPU must be free: when other programs use more than 1.5 GiB (or 20%) of its
  memory, nothing is measured (on AMD / Intel / Apple GPUs, where nvidia-smi is missing, this is
  read from ComfyUI's /system_stats where possible, and the result's note says what could not be
  checked). Only one measure or share measures at a time. A started ComfyUI is stopped also on a
  failure, Ctrl+C or when the terminal is closed.

SIDE EFFECTS:
  Network: asks 127.0.0.1 ports 8188 and 8000 whether ComfyUI runs and where its folders are
  (without --url), the ComfyUI it measures on (the node check and one job), the official template
  and its input images from GitHub when that ComfyUI lacks them, and the model files when it
  downloads them. Nothing about this PC is sent anywhere else.
  Writes the result to <cache>/measurements/ (used when this PC is judged) and a submission draft
  (or a failure record) to <cache>/submissions/ (what genmodelfit share sends). A started ComfyUI
  writes its output to <cache>/comfyui-<date>.log; <cache>/measure.lock while measuring.
  Deletes the model files this run downloaded only on y or --delete-downloaded (only inside the
  destination).

EXIT CODES:
  0  Success (also: nothing chosen, nothing to suggest, answered n to "Measure it?" or to the
     download, a result that only cannot be sent)
  1  Error: no ComfyUI found, no GPU, the GPU is busy, another measure is running, the model has
     no template, nodes are missing, it was not downloaded, the run failed (out of memory, another
     error, or Ctrl+C during the run), no terminal and no --yes
  2  Usage error
  130  Ctrl+C at a question

AGENT USAGE:
  genmodelfit measure --json
  genmodelfit measure image_z_image_turbo_int8 --yes --json
  genmodelfit measure "Z-Image-Turbo" --url http://127.0.0.1:8188
  genmodelfit measure image_sdxl_simple --resolution 1536x1536 --yes --json

  JSON (--json): { genmodelfit, command, system, database, action: suggest | measure,
  comfyui: { url, found_by }, suggestions? [{ ..., settings, args }] | model_id, settings,
  installed, time_hint, measurement, saved_to, submission, hint, error? }
  Details: docs/schema.md""",
    "share": """Measure for a few minutes and send the measurements as a GitHub pull request.
Without FILE it finds ComfyUI like measure (--url, else one running on this PC, else it starts
the one it finds and stops it afterwards), picks the suggestions of measure whose files are
installed and that are not yet measured on this PC (results saved on this PC count, sent or not;
other people's measurements do not) and whose nodes the ComfyUI has, until their rough times
add up to --budget (300 s), and shows the plan with the unsent measurements and the total time
(starting ComfyUI about 45 s, measuring, and downloading as an example at 100 Mbps). It asks
once [Y/n], then measures one by one, freeing the ComfyUI before each run (a failure does not
stop the rest; Ctrl+C goes on to sending what was measured). Models that are not installed are
downloaded only with --download or y to one [y/N] question (asked first when nothing installed
can be measured, else after measuring), with download's questions and consent rules; --yes
alone never downloads. Then it shows every file to send (the whole content and the privacy
checks) and asks how to send it: 1) a pull request (sign in to GitHub) 2) issues (no sign-in;
the form opens in your browser, one issue per file) q) not now. --yes, --json or no terminal
send a pull request; --issue --yes sends issues (the URLs are printed without a terminal).
Before measuring it only checks a token it already has (GITHUB_TOKEN or GH_TOKEN, else the
saved sign-in) and stops at once if it does not work; the device login in your browser (scope
public_repo; saved for next time; --logout removes it) happens only when you choose a pull
request. While it waits for the device login, keys work: r opens the page again, n gets a new
code, q sends the same files as issues; an expired code asks whether to sign in again. With
--yes and no terminal, a missing token stops it before measuring. The pull request
comes from a fork of the repository: a branch from the merge base of the upstream main and the
fork's main with the files under
data/contrib/<GPU>/ (titled "measurements: <GPU> (<count>)"), or your open share pull request
for that GPU; files already there are skipped. If the pull request cannot be made, a terminal
offers issues for the same files. Results are saved before sending and are never measured again:
genmodelfit share --no-measure (or --issue) sends them later. Sent files are marked on this PC
(--all includes them).
At the end (also after a failure or Ctrl+C) it lists the model files it downloaded with the total
size and asks [y/N] whether to delete them: only files this run newly downloaded and its leftover
.part files, never ones that were already there; --yes or no terminal keeps them, and
the flag --delete-downloaded deletes without asking.
What could not be measured goes into one failure record file per run
(names and numbers only, never error text). Without any ComfyUI it sends only the unsent
measurements; with none it says how to start ComfyUI and exits 1 without asking. FILE sends
that file only. share --issue without --yes is the older way: choose an unsent measurement and
open its issue form.

PRECONDITIONS:
  A GitHub account. To measure: a GPU and ComfyUI (as for measure).

SIDE EFFECTS:
  Network: like measure while measuring and downloading. GitHub (github.com and api.github.com):
  before measuring only a check of a token you already have (GET /user); the sign-in, fork,
  branch, files and pull request only after you agree to send. --dry-run only asks 127.0.0.1
  whether ComfyUI runs; --issue makes no network access to GitHub. Writes measurements and
  failure records like measure, the sent marks to <cache>/submissions_shared.json and the
  sign-in token to <config>/github_token (readable only by you). Deletes the model files this run
  downloaded only on y or --delete-downloaded (only inside the destination).

EXIT CODES:
  0  Success (also: answered n, nothing to measure or send, results that only cannot be sent)
  1  Error: cannot sign in, no ComfyUI and nothing to send, a measurement failed, a file was
     refused (privacy check NG or check errors), GitHub refused the upload (the results stay
     saved), --json or no terminal without --yes
  2  Usage error
  130  Ctrl+C (at a question; while measuring it first goes on to sending what was measured)

AGENT USAGE:
  genmodelfit share --dry-run --json
  GITHUB_TOKEN=<token> genmodelfit share --yes --json
  genmodelfit share --no-measure --yes --json
  genmodelfit share --issue --yes
  genmodelfit share --issue <cache>/submissions/<GPU>/<date>-<sha8>.json --print-url

  JSON (--json): { genmodelfit, command, action: send | logout, comfyui, budget_seconds,
  plan, download_plan, pending, measured?, failed?, interrupted?, downloaded?, files, prs
  [{ url, reused, branch, fork, uploaded, skipped, on_main }], method?, issues?, marked_shared,
  sent, dry_run?, error? }
  --issue: { listed, submissions? | file, ok, refused, checklist, report, share, opened, copied,
  marked_shared }
  Details: docs/schema.md""",
    "bench": """Measure one model on a ComfyUI that is running, and save the result (peak VRAM, RAM, time).
TEMPLATE is the exact workflow id of a ComfyUI template, such as image_z_image_turbo_int8 (the id
in [ ] on the first line of info). Model names and parts of ids are not accepted, and hf: models
have no template (pass an API-format workflow with --workflow instead).
Your saved results are used when this PC is judged.
bench is for maintainers: genmodelfit measure does the same without the template id (it suggests
models and finds ComfyUI).
--share prepares a submission and opens GitHub's measurement form in your browser: sign in to
GitHub (an account is needed) and press "Create" (like genmodelfit share --issue).

PRECONDITIONS:
  A ComfyUI running at --url with all the model files of TEMPLATE in its models folder (use
  genmodelfit download first). The GPU must be free: when another program uses it, or its free
  memory cannot be checked (nvidia-smi), bench refuses unless --force. Measure on a ComfyUI only you
  use; --free makes bench unload that ComfyUI's models first.

SIDE EFFECTS:
  Network: only the ComfyUI at --url (it sends one job and reads the results), plus the official
  template file from GitHub when that ComfyUI does not have it. Nothing about this PC is sent
  elsewhere; --share only opens your browser at the form (or prints its URL).
  Writes the result JSON to <cache>/measurements/ (or --output). Failed results and results under
  non-standard conditions are only shown unless --save-failed / --save-nonstandard / --output.
  --submission / --share also write a submission JSON.
  Settings you leave out (--width, --height, --frames, --length, --steps) stay as in the template.

EXIT CODES:
  0  Success
  1  Error: ComfyUI cannot be reached, the GPU is busy (without --force), the template or a file is
     missing, the run failed (out of memory or another error), timeout, Ctrl+C (our job is
     cancelled first)
  2  Usage error

AGENT USAGE:
  genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188
  genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188 --share
  genmodelfit bench video_wan2_2_5B_ti2v --url http://127.0.0.1:8188 --resolution 640x640 --json

  JSON (--json): { genmodelfit, command, measurement, saved_to, not_saved?, workflow_source,
  warnings, failed?, submission? }
  Details: docs/schema.md""",
}


# --- 日本語の説明(#76。英語の _HELP_EN と同じ内容を日本語だけで書く。JSON の鍵・コマンドは同じ) ---

_DETECT_HELP_JA = (
    "  用意するものは無い。GPU とメモリは調べて決める(--vram / --ram で置き換える)。\n"
    "  GPU は NVIDIA なら nvidia-smi、ほかの AMD / Intel / Apple は WMI(Windows)・lspci と sysfs(Linux)・\n"
    "  system_profiler(macOS)で調べる。\n"
)

_COMMON_HELP_JA = (
    "前提:\n"
    + _DETECT_HELP_JA
    + """  一覧は同梱のものを使う(`genmodelfit update` で新しい一覧を取ってあればそちら。--no-cache で同梱)。

副作用:
  なし(読むだけ。通信しない)。

終了コード:
  0  成功{zero}
  1  失敗{one}(--json なら誤りは "error" に入る)
  2  使い方の誤り(知らないオプション・値)
"""
)
_ONE_DB_JA = ": 壊れた --db のファイルなど"

_JSON_FIELDS_JA = (
    _JSON_KEYS
    + """
  1 項目に 1 モデル(同じ重みの系統 + 大きさ)。--all-workflows ならワークフローごとに 1 項目。
  詳しくは docs/schema.md"""
)

_HELP_JA = {
    "recommend": """この PC に合う生成モデルを、おすすめ順に出す。

-m も --task も無ければ、種類(image, video, music, sfx, tts, 3d)ごとに上位 -n 件(既定 3 件)。
-m か --task があれば 1 つの表(既定 10 件)。
同じモデルは 1 行にまとめ、一番合う精度とワークフローを選ぶ(--all-workflows で全部)。
並べ方: 動くもの → Perfect/Good → Marginal(速い動かし方が先)→ 公式のテンプレート →
人気(同じ種類・同じ出どころの中の順位)→ Score → ID
(--json は自動でおすすめに出せるもの(gate: auto)を段階より先にする)。
Score 0〜100 はメモリの余裕(使用率 70% までは 100。速さではない)。Score の決まりは docs/scoring.md。
無検閲のモデルと補助ツールは、指定しなければ出さない。出力は表。アプリは --json を付ける。

"""
    + _COMMON_HELP_JA.format(zero="", one=_ONE_DB_JA)
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit recommend --json
  genmodelfit recommend -m image --task edit -n 5 --min-fit good --json
  genmodelfit recommend -m video,music --auto-only --comfyui-version 0.40.0 --json
  genmodelfit recommend --license apache-2.0,mit --vram 8 --ram 32 --json

"""
    + _JSON_FIELDS_JA,
    "fit": """この PC でモデルを判定し、1 つの表にする(recommend のように上位だけに絞らない)。
既定は動くもの(--fit runnable: perfect・good・marginal)だけで、補助ツールとライセンスの分からない
ファイルを含むモデルは出さない。--all で too_tight・判定できないもの・補助ツール・ライセンス不明も出す。
無検閲のモデルは --uncensored で出す(--all だけでは出ない)。
並べ方: 種類 → 段階 → 公式のテンプレート → 人気 → Score(--sort で変える)。

"""
    + _COMMON_HELP_JA.format(zero="", one=_ONE_DB_JA)
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit fit --json
  genmodelfit fit -m video --task i2v --min-fit good -n 20
  genmodelfit fit --uncensored --all-workflows --vram 12 --ram 32 --json

"""
    + _JSON_FIELDS_JA,
    "search": """ID・名前の一部で探し(大文字小文字は区別しない)、この PC での判定を出す。
--content が効く(既定 sfw)。合うものが無ければ空の一覧で、終了コードは 0。

"""
    + _COMMON_HELP_JA.format(
        zero="(合うものが無いときも。models は空、表では「(該当なし)」)",
        one=": 探す語が空、壊れた --db のファイルなど",
    )
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit search flux --json
  genmodelfit search wan -m video --min-fit good
  genmodelfit search sdxl --uncensored --json

"""
    + _JSON_FIELDS_JA.replace("filters: {...}", "filters: {query, ...}"),
    "types": """大きな種類(image, video, music, sfx, tts, 3d)と、その中の用途と数を出す。
用途の名前は --task に使う。

"""
    + _COMMON_HELP_JA.format(zero="", one=_ONE_DB_JA).replace(
        _DETECT_HELP_JA, "  用意するものは無い。PC は判定しない。\n"
    )
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit types
  genmodelfit types --json

  JSON (--json): { genmodelfit, command, database, filters: {content}, types: [{ modality,
  label, label_ja, models, workflows, uses: {use: workflows} }], uses: [{ use, label,
  label_ja, tasks }], aliases }
  詳しくは docs/schema.md""",
    "info": """1 つのモデルの詳細(ファイル・ライセンス・見積もり・この PC での限界・自動のおすすめの条件・
同じモデルのほかのワークフロー)を出す。
ID がそのまま合えば必ずそのモデル(無検閲も)。
ID がそのまま合わなければ、ID・名前の一部を --content の中で探す。
2 件以上合えば失敗にして候補を並べる。

"""
    + _COMMON_HELP_JA.format(
        zero="", one=": モデルが無い、2 件以上合う(候補を並べる)、壊れた --db のファイルなど"
    )
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit info image_z_image_turbo_int8 --json
  genmodelfit info sdxlturbo --vram 8 --ram 32

  JSON (--json): { genmodelfit, command, system, database, model: ModelEntry, estimate,
  gate, measurements, group_key, same_model: [{id, name, tasks, use, precision, fit_level,
  memory_required_gb, disk_size_gb, auto, matched}] }
  失敗のときは { error, candidates? }。
  詳しくは docs/schema.md""",
    "system": """この PC(GPU・メモリ・ディスクの空き)と、使っている一覧がまだ新しいか(一覧の欄: 出どころ・
HEAD の確認の日付・期限切れか・いつまで有効か・自動のおすすめの数)を出す。

"""
    + _COMMON_HELP_JA.format(
        zero="(一覧が期限切れのときも。一覧の欄・database.status.expired を見る。"
        "新しくするには genmodelfit update --from-release)",
        one=_ONE_DB_JA,
    )
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit system --json
  genmodelfit system --comfyui-version 0.40.0 --json

  JSON (--json): { genmodelfit, command, system, database: { origin, generated_at, models,
  measurements, status: { source, generated_at, heads_checked_oldest, heads_checked_newest,
  max_head_age_days, expired, expires_at, models, auto_count, auto_count_if_fresh,
  checked_at } } }
  詳しくは docs/schema.md""",
    "update": """新しい一覧を取る。
利用者は `genmodelfit update --from-release` を実行する。
--from-release は、このプロジェクトが配る署名つきの最新の一覧を取り込み(落とすのは db.json と
db.json.sig だけ)、署名を確かめる。取り込んだ後は、ほかのコマンドも同梱の一覧の代わりにこれを使う
(--no-cache で同梱に戻る)。
オプションなしの update は、一覧を上流(ComfyUI のテンプレート・Hugging Face Hub・すべてのダウンロードの
URL への HEAD)から作り直す。作り直しは保守する人向けで、時間がかかる(問い合わせが多く、Hugging Face の
上限で待つ。--rate-limit-budget を見る)。保守する人は、HEAD の確認だけ(--heads-only)、アプリ向けの
項目と手で足したモデルだけ(--fields-only)、ファイルの見出しだけ(--inspect-only)をやり直すこともできる。
方針のオプション(--comfyui-version・--max-head-age-days など)が変えるのは、最後に出す自動のおすすめの
数だけ。--db / --no-cache は、--heads-only・--fields-only・--inspect-only が元にする一覧を選ぶ。

前提:
  通信できること。

副作用:
  通信: GET / HEAD だけ。update は ComfyUI のワークフローのテンプレートとリリース(GitHub)、
  Hugging Face Hub の API、モデルのダウンロードの URL(HEAD だけ。重みは落とさない)を読む。
  --from-release は、このプロジェクトの GitHub のリリースから db.json と db.json.sig だけを落とす。
  この PC のことは何も送らない(User-Agent: genmodelfit/<版>。~/.netrc の資格情報は送らない)。
  一覧をキャッシュ(か --output)に書く。失敗したらキャッシュは変えない。

終了コード:
  0  成功
  1  失敗(通信・署名が合わない・前より古い一覧など。--json なら誤りは "error" に入る)
  2  使い方の誤り
""",
    "diff": """2 つの一覧(db.json)を比べ、利用者に関係のある違いを出す。
出すのは、増えた・消えたモデルと、変わったモデル(名前 (種類) — ID)ごとの次の違い。
変わったモデルの違い: 増えた・消えた・変わったファイル(ライセンスと出どころつき)、ライセンス(寛容な
側に ! を付ける)、新しく無検閲になったもの、動かし方・用途、自動のおすすめ、指示・読み上げの言語、
大きさ、精度、確認の状態、ほかに変わった項目の名前(「その他の変化」)。
変わったモデルは 3 つの節に分け、節ごとに --limit で切る: ファイル・ライセンス・無検閲・動かし方と
用途が先、次に自動のおすすめの変化(ID は必ず出す)、最後にライセンスの出どころ・言語・大きさ・
確認の印など。
節の中では大事な変化を先に出すので、大事な変化は --limit で隠れない。
大事さの低い同じ変化が多くのモデルにあれば(全部に確認の印が付いたなど)、件数つきの 1 行にまとめる。
変わった項目はどこかに必ず出る(--all では数える)。
JSON をそのまま比べるので、知らない鍵の変化も数える(merge-check と同じ)。

引数なしなら、同梱の一覧と `update` で取ってきた一覧(キャッシュ)を比べる。引数が 1 つなら同梱の
一覧とそのファイル、2 つならその 2 つを比べる。
--all は保守用の内容(毎週の PR の中身)を足す: 新しいものに限らずすべての HEAD の失敗、401 の
リポジトリ、`sources` の変わった数、人気の数・日付・HEAD の結果だけが変わったモデル、まとめた行の
ID。--all のときは、-n を付けなければ行の上限は無い(--all でなければ節ごとに既定 -n 20)。

前提:
  引数なし: キャッシュに取ってきた一覧があること(先に `genmodelfit update --from-release`。無ければ
  短い案内を出して終了コード 0)。引数があるときは db.json のファイルを 1 つか 2 つ。
  新しい一覧(generated_at)が前の一覧より古ければ注意を出す。

副作用:
  なし(読むだけ。通信しない)。

終了コード:
  0  成功(違いがあるときも、取ってきた一覧がまだ無いときも)
  1  失敗(ファイルが読めない・形が違う。--json なら誤りは "error" に入る)
  2  使い方の誤り

使い方の例(エージェント・アプリ向け):
  genmodelfit diff                          # 同梱の一覧 ↔ 取ってきた一覧
  genmodelfit diff new.json                 # 同梱の一覧 ↔ new.json
  genmodelfit diff old.json new.json --json
  genmodelfit diff old.json new.json --all  # 保守用の内容

  自動のおすすめは、両方の一覧を 1 つの条件で比べる: 新しい一覧が記録した ComfyUI の版
  (か --comfyui-version)で、HEAD の期限は無し(HEAD の失敗は数える)。
  JSON (--json): { genmodelfit, command, old, new, old_label, new_label, policy, summary, added,
  removed, license_changes, auto_changes, size_changes, precision_changes, newly_uncensored,
  head_failures, unauthorized, partial_counts, stats, classification, field_changes,
  model_changes, top_changes, warnings }
  取ってきた一覧が無いときは、同じ鍵が空で "note" が付く。
  詳しくは docs/schema.md""",
    "download": """モデルのファイルをこの PC 向けに落とす(1 つのワークフローの全部のファイル。
sha256 で確かめる)。

MODEL はモデルの名前(表の Model)・ワークフローの ID・そのどちらかの一部(info と同じ)。
モデルの名前なら、ワークフローは recommend と同じ選び方で決める: この PC で逃がさずに載る(good 以上で
GPU)中で一番精度の高いもの、それが無ければ段階が一番良いもの(同じなら逃がさない載せ方、次に精度の
高いもの)。どの決まりで選んだかは計画に出す。
--precision を付けると、その精度のワークフローを落とす。
--budget を付けると、落とす量が上限に収まる中で一番良いワークフローを選び、収まるものが無ければ断る。
ファイルは <ComfyUI>/models/<folder>/<file> に置く(folder はテンプレートで決まる:
diffusion_models・text_encoders・vae・loras・checkpoints など)。
置き場所は次の順で最初に決まったもの: --comfyui-dir(ComfyUI の外は --output-dir。優先の順は同じ)、
環境変数 GENMODELFIT_COMFYUI_DIR、`genmodelfit config` で保存した comfyui-dir、自動で見つけた
ComfyUI(今いるフォルダ、127.0.0.1 の 8188・8000 で起動中の ComfyUI、ComfyUI デスクトップ版と
comfy-cli の設定、C:/ComfyUI などのよくある場所)。置き場所をどれで決めたかは、計画に必ず出す。
置き場所が見つからない・2 つ以上見つかったときは、端末なら選ぶかパスを入れ、--yes・--json・端末で
ないときは落とさない(終了コード 1)。
--list は起動中の ComfyUI に尋ねないので、本当に落とすときは見つかる場所でも「置き場所: 未定」と
出ることがある(正しい計画を見るには --comfyui-dir を付ける)。
ComfyUI のフォルダの extra_model_paths.yaml に is_default の節があれば、そこに置く。
ComfyUI のどのフォルダか分からないファイルは other/ に置く。ComfyUI は other/ を読まないので、使う
前に正しいフォルダ(diffusion_models など)へ移す。
落とす前に、ファイル・それぞれのライセンスと URL(記載が無ければ確かめる場所)・合計の大きさ・
ディスクの空きを出し、[y/N/c] で尋ねる(c で置き場所を変える。--yes で尋ねない)。
自動で見つけた場所に初めて落とすときは、次からもそこにするかを尋ねる([Y/n]。`genmodelfit config`
で見る・消す)。保存できるのは ComfyUI のフォルダだけで、--output-dir の場所は毎回指定する。
無検閲のモデルとライセンスの分からないファイルには同意が要る: 端末ならそれを書いて y/N で尋ね、
--yes・--json・端末でないときは --uncensored・--any-license を指定する(前からの書き方:
--content all、--include-unknown-license。同意の指定は略さずに書く)。
足りないのが同意だけなら、計画の最後に同意を足したコマンドを 1 つ出す(genmodelfit が勝手に足して
落とすことはしない)。
断るもの(どのオプションでも変わらない): gated のファイル(ログインしない)、ディスクの空きが
足りない(落とす量 + 1 GB が要る)、置き場所の外に出る名前、Hugging Face / GitHub 以外のホスト。
落としたファイルを使うには: ComfyUI を開いていたらブラウザで読み込み直し、ComfyUI のテンプレートの
一覧から、info の 1 行目の名前のテンプレートを開く([ ] の中の ID はテンプレートのファイル名)。
ID が hf: で始まるモデルには ComfyUI のテンプレートが無いので、使い方は Hugging Face のページ
(https://huggingface.co/<owner>/<repo>)を見る。

前提:
  落とすには huggingface.co(か github.com)と通信できること。--list は通信しない。

副作用:
  通信: 一覧の URL の GET だけ(https。転送先もすべて Hugging Face / GitHub のホストに限る)。
  この PC のことは何も送らない(User-Agent: genmodelfit/<版>。~/.netrc の資格情報もトークンも
  使わない)。書くのは置き場所のフォルダの中だけ: 落としている間は <file>.part(Ctrl+C や失敗では
  残し、次の実行で HTTP Range で続きから落とす)、sha256 が合えば <file> に名前を変える。落とした
  ファイルの sha256 が合わなければ消す。sha256 の合う既にあるファイルは飛ばし、ほかの既にある
  ファイルは上書きしない。
  置き場所の指定(オプション・環境変数・設定)が無いときは、この PC で起動中の ComfyUI にフォルダの
  場所を尋ねる(GET http://127.0.0.1:8188 と :8000 の /internal/folder_paths。時間切れ 2 秒)。
  「次からも」に Y と答えると、設定ファイルに comfyui-dir を書く(`genmodelfit config path`)。
  --list: 通信せず(起動中の ComfyUI にも尋ねない)、何も書かない。

終了コード:
  0  成功(--list も。計画が止まっていても: "blocked" を見る)
  1  失敗: モデルが無い・2 件以上合う、断った(上を見る)、同意が無い、通信、sha256 が合わない
     (--json なら誤りは "error" に入る)
  2  使い方の誤り
  130  中断(Ctrl+C)。もう一度実行すれば続きから落とす

使い方の例(エージェント・アプリ向け):
  genmodelfit download "Z-Image-Turbo" --list --json
  genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI --yes --json
  genmodelfit download image_z_image_turbo_int8 --output-dir D:/models --budget 15 --yes

  JSON (--json): { genmodelfit, command, system, database, listed, plan: { model_id, name,
  model_name, precision, fit_level, layout, root, files: [{ name, relpath, dest, url, sha256,
  size_bytes, directory, directory_basis, license, license_url, check_url, license_allowed,
  gated, status, part_bytes, remaining_bytes, reason }], total_bytes, download_bytes,
  disk_free_bytes, disk_needed_bytes, blocked, blocked_codes, warnings, alternatives, ok },
  result: { downloaded, resumed,
  restarted, skipped, bytes_downloaded, seconds, files } | null, destination_from,
  comfyui_dir, extra_model_paths, destination_candidates, error? }
  詳しくは docs/schema.md""",
    "config": """genmodelfit のコマンドラインの設定を見る・変える(Python の API は読まない)。

キー:
  comfyui-dir  download が使う ComfyUI のフォルダ(--comfyui-dir・--output-dir と
               GENMODELFIT_COMFYUI_DIR の次)。set はそのフォルダが ComfyUI に見えることを確かめる
               (main.py・comfy・nodes.py・comfy_extras・comfy_api のうち 4 つ、または ComfyUI
               デスクトップ版のフォルダ: models・input・user・output・custom_nodes)。ComfyUI の外の
               フォルダ(download の --output-dir)は保存できないので、毎回 --output-dir を付ける。
  lang         表示の言語。en か ja(--lang と GENMODELFIT_LANG が優先)。
操作を書かなければ、それぞれの設定と、どこから来たか(指定・環境変数・設定ファイル・既定)を出す。

前提:
  なし。

副作用:
  通信しない。set・unset は設定ファイル(genmodelfit の利用者の設定のフォルダの config.json。
  GENMODELFIT_CONFIG_DIR でフォルダを変えられる)を書く。show・path は何も書かない。
  壊れたファイルは読まず(注意を出す)、set・unset でも上書きしない。

終了コード:
  0  成功
  1  失敗: 知らないキー、正しくない値、壊れた・書けないファイル
  2  使い方の誤り

使い方の例(エージェント・アプリ向け):
  genmodelfit config --json
  genmodelfit config set comfyui-dir D:/ComfyUI
  genmodelfit config unset comfyui-dir
  genmodelfit config path

  JSON (--json): show は { genmodelfit, command, action, path, settings: { comfyui-dir: { value,
  from }, lang: { value, from } }, warnings }。set は { ..., action, path, key, value }、unset は
  { ..., key, removed }、path は { ..., path }。
  詳しくは docs/schema.md""",
    "gate": """一覧のうち、自動でおすすめに出せる(Verified。JSON では "auto")モデルの数と、ほかが保留になる
理由を数える。条件は info(1 つのモデル)や recommend --json の "auto"・held_reasons と同じ。

出力の読み方: 1 行目は条件に使った ComfyUI の版、2 行目はモデルの数と、出せる数・保留の数。表は条件ごとに
1 行で、「落ちた数」はその条件に通らないモデルの数、「最初に落ちた数」はそれが最初に落ちた条件である
モデルの数(先に直すところ)。--only-auto / --only-held はモデルの ID も並べる(保留は最初の理由つき)。
条件と許可リストは docs/gate.md

"""
    + _COMMON_HELP_JA.format(
        zero="", one=": 壊れた --db のファイル、空の --license-allowlist(「,」や空のファイル)など"
    ).replace(_DETECT_HELP_JA, "  用意するものは無い。PC は判定しない。\n")
    + """
使い方の例(エージェント・アプリ向け):
  genmodelfit gate
  genmodelfit gate -m video --only-held
  genmodelfit gate --comfyui-version 0.40.0 --json

  JSON (--json): { genmodelfit, command, database, policy, filters, summary: { models, auto, held,
  failing, first_failing }, models: [{ id, auto, held_by, checks, ... }] }
  詳しくは docs/schema.md""",
    "measure": """ComfyUI でモデルを実測し、結果をこの PC に保存する(ComfyUI が無ければ起動する。
何も送らない)。
MODEL を省くと、測ると役に立つモデルを出す: この PC の GPU に載り(GPU で good 以上)、この PC で
テンプレートの設定のまま、まだ測っていないもの(同じ GPU のほかの人の実測があっても外さない。2 つの
出どころの実測がそろうと、段階を実測で決められるため)。ライセンスの分からないものは出さない。
並びは、もう入っているもの → 時間が短いもの → 落とす量が少ないもの。番号で選ぶ(q でやめる)。
起動中の ComfyUI に無いノードが要るものは出さない。その実測があるモデルには、予測式の確かめに
役立つ「設定を変えた測定」を出す(#116): 画像の解像度 512・768・1024・1536 px(文章から画像)、
動画のコマ数 17・33・49・81(大きさはテンプレートのまま。文章・画像から動画)、音楽・効果音の長さ
10・30・60・120 秒(文章から音声)。その設定で GPU に載り(good 以上)、この PC でその設定をまだ
測っていないものだけで、1 つのモデルに 2 件まで。時間の目安が約 300 秒を超えるものは出さない。
1 回は生成 1 回(数分)で、もう一度 measure を実行すると次のものを選べる。
MODEL はモデルの名前・ワークフローの ID・その一部で、download と同じ選び方で決める(ComfyUI の
テンプレートのあるワークフローだけ)。--resolution・--frames・--length で bench と同じく設定を変える。
測る前に 1 画面で、モデルと精度、設定(テンプレートのまま、または変えた設定)、どの ComfyUI か・
どう見つけたか、ファイルが入っているか、時間の目安、何も送らないことを見せ、[Y/n] で尋ねる
(--yes で尋ねない)。時間は目安: 同じモデルの実測の中央値、無ければ種類ごとのおおよその値。
設定を変えたときは画素数・コマ数・長さの比で伸び縮みさせる。テンプレートが頼んだ設定を受け付け
ないとき・ComfyUI に要るノードが無いときは、落とす前・生成の前に止める。
入っていないモデルは、download と同じ確かめ・同意の決まりで先に落とす。落としたものは、終わると
(失敗・Ctrl+C でも)一覧と合計の量を見せて消すかを [y/N] で尋ねる(この実行で新しく落としたファイル
だけ。前から入っていたものは消さない。--yes・端末でないときは残す。--delete-downloaded で尋ねずに
消す)。genmodelfit が起動した
ComfyUI は、測る前に空ける(/free。モデルを外し、前の結果を消す)。自分の ComfyUI は --free の
ときだけ。終わると結果と保存した場所を出し、最後に genmodelfit share(送るコマンド)を 1 行だけ
案内する(送るかどうかは尋ねない)。測れなかったときは、失敗の記録を保存する(share が送る)。

前提:
  この PC の GPU と、そこで動く ComfyUI: --url か、この PC で起動中のもの(127.0.0.1 の 8188 か
  8000)。起動していなくても ComfyUI のフォルダが見つかれば(download と同じ見つけ方)、裏で起動して
  使い、測り終えたら止める(ポータブル版は python_embeded、手で入れたもの・comfy-cli は .venv・venv、
  デスクトップ版は .venv とアプリの ComfyUI で、127.0.0.1 の空いているポートに、ブラウザを開かずに)。
  [Y/n] の質問がその同意を兼ねる(--no-start か GENMODELFIT_COMFYUI_START=0 で止める。
  --start-timeout は既定 300 秒)。見つからなければ、起動のしかたを出して 1 で終わる。GPU が
  空いていること: ほかのプログラムが GPU のメモリを 1.5 GiB(か 20%)より多く使っていれば測らない
  (nvidia-smi の無い AMD・Intel・Apple の GPU では、できるところは ComfyUI の /system_stats で
  確かめ、確かめられなかったことは結果のメモに書く)。測るのは、同時に 1 つの measure か share
  だけ。起動した ComfyUI は、失敗・Ctrl+C・端末を閉じたときも止める。

副作用:
  通信: ComfyUI が起動しているか・フォルダの場所を 127.0.0.1 の 8188・8000 に尋ねる(--url が
  無いとき)、測る ComfyUI(ノードの確かめと仕事を 1 つ)、その ComfyUI に無ければ GitHub の公式の
  テンプレートと入力の画像、落とすときはモデルのファイル。この PC のことはほかへ送らない。
  結果を <cache>/measurements/(この PC の判定に使う)に、送るための下書き(か失敗の記録)を
  <cache>/submissions/(genmodelfit share が送るもの)に書く。起動した ComfyUI の出力は
  <cache>/comfyui-<日付>.log。測るあいだは <cache>/measure.lock。この実行で落としたモデルの
  ファイルを消すのは、y と答えたか --delete-downloaded のときだけ(置き場所の中だけ)。

終了コード:
  0  成功(選ばなかった・出す提案が無い・「測りますか?」や落とす質問に n と答えた・送れない
     だけの結果のときも)
  1  失敗: ComfyUI が見つからない、GPU が無い、GPU が使われている、ほかの measure が測って
     いる、テンプレートが無いモデル、ノードが足りない、落とさなかった、実行の失敗(メモリ不足・
     ほかの誤り・実行中の Ctrl+C)、端末でなく --yes も無い
  2  使い方の誤り
  130  質問の途中の Ctrl+C

使い方の例(エージェント・アプリ向け):
  genmodelfit measure --json
  genmodelfit measure image_z_image_turbo_int8 --yes --json
  genmodelfit measure "Z-Image-Turbo" --url http://127.0.0.1:8188
  genmodelfit measure image_sdxl_simple --resolution 1536x1536 --yes --json

  JSON (--json): { genmodelfit, command, system, database, action: suggest | measure,
  comfyui: { url, found_by }, suggestions? [{ ..., settings, args }] | model_id, settings,
  installed, time_hint, measurement, saved_to, submission, hint, error? }
  詳しくは docs/schema.md""",
    "share": """数分測って、実測を GitHub の PR で送る。
FILE を省くと、measure と同じく ComfyUI を見つけ(--url、無ければこの PC で起動中のもの、無ければ
見つけたものを起動し、終わったら止める)、measure の提案のうち、ファイルが入っていて、この PC で
まだ測っていないもの(この PC に保存したものは、送ったかどうかによらず測ったと数える。ほかの人の
実測は数えない)で、その ComfyUI にノードがそろうものを、時間の目安の合計が --budget(300 秒)に
収まるまで選び、送っていない実測と合計の時間(ComfyUI の起動 約 45 秒・測る
時間・落とす時間を 100 Mbps の例で)を添えて計画を見せる。1 回だけ [Y/n] で尋ね、1 つずつ測る(失敗
しても残りは続け、Ctrl+C ならそこまでのものを送るかに進む)。測る前には毎回 ComfyUI を空ける。
入っていないモデルを落とすのは、
--download か、1 回の [y/N] の質問に y のときだけ(入っていて測れるものが無ければ最初に、ほかは
測り終えてから尋ねる)。download と同じ確かめと同意の決まりで、--yes だけでは落とさない。そのあと
送るファイルのすべて(中身のすべてと個人の情報の点検)を見せて、送り方を尋ねる: 1) PR(GitHub に
ログイン) 2) Issue(ログインなし。ブラウザでフォームを開く。1 つのファイルに 1 つ) q) 送らない。
--yes・--json・端末でないときは PR、--issue --yes は Issue(端末でなければ URL を出す)。測る前は、
もうあるトークン(GITHUB_TOKEN か GH_TOKEN、無ければ保存したログイン)を確かめるだけで、使えなければ
すぐ止める。ブラウザでのデバイス認証(権限 public_repo。次のために保存し、--logout で消す)は、PR を
選んだときだけ。デバイス認証を待つあいだはキーが使える: r ページを開き直す、n 新しいコード、
q 同じファイルを Issue で送る。コードの期限が切れたら、もう一度ログインするかを尋ねる。
--yes で端末でなく、トークンが無ければ、測る前に止める。PR は、リポジトリの
フォークで、本家とフォークの main の共通の祖先からブランチを作り、data/contrib/<GPU>/ にファイルを
置いて開く(題は「measurements: <GPU> (<件数>)」)。その GPU の share の PR が開いていれば、そこに足す。もうある
ファイルは飛ばす。PR を作れなければ、端末なら同じファイルを Issue で送るかを尋ねる。結果は送る前に
保存し、測り直さない: あとで genmodelfit share --no-measure(か --issue)で送れる。送ったものは
この PC で印を付ける(--all で含める)。測るために落としたモデルのファイルは、最後に
(失敗・Ctrl+C でも)一覧と合計の量を見せて消すかを [y/N] で尋ねる(この実行で新しく落としたファイルと
残った .part だけ。前から入っていたものは消さない。--yes・端末でないときは残す。--delete-downloaded で
尋ねずに消す)。測れなかったものは、1 回に 1 つの失敗の記録のファイルに
まとめる(名前と数だけ。エラーの本文は入れない)。ComfyUI が無ければ送っていない実測だけを送り、それも無ければ
尋ねずに ComfyUI の起動の仕方を出して 1 で終える。FILE はそのファイルだけを送る。--yes の無い
share --issue は前からの送り方で、送っていない実測を選んで Issue のフォームを開く。

前提:
  GitHub のアカウント。測るには GPU と ComfyUI(measure と同じ)。

副作用:
  通信: 測る・落とすあいだは measure と同じ。GitHub(github.com と api.github.com): 測る前は、
  もうあるトークンを確かめるだけ(GET /user)。ログイン・フォーク・ブランチ・ファイル・PR は、
  送ることに同意したあとだけ。--dry-run は ComfyUI が起動しているかを 127.0.0.1 に尋ねるだけ、
  --issue は GitHub に通信しない。実測と失敗の記録を measure と同じく書き、送った印を
  <cache>/submissions_shared.json に、ログインのトークンを <config>/github_token(本人だけが
  読める)に書く。この実行で落としたモデルのファイルを消すのは、y と答えたか
  --delete-downloaded のときだけ(置き場所の中だけ)。

終了コード:
  0  成功(n と答えた・測るものも送るものも無い・送れないだけの結果のときも)
  1  失敗: ログインできない、ComfyUI が無く送るものも無い、測れなかったものがある、送れない
     ファイルがある(個人の情報の点検の NG・検査の誤り)、GitHub が受け付けない(結果は保存した
     まま)、--json・端末でないのに --yes が無い
  2  使い方の誤り
  130  Ctrl+C(質問の途中。測る途中なら、先にそこまでのものを送るかに進む)

使い方の例(エージェント・アプリ向け):
  genmodelfit share --dry-run --json
  GITHUB_TOKEN=<トークン> genmodelfit share --yes --json
  genmodelfit share --no-measure --yes --json
  genmodelfit share --issue --yes
  genmodelfit share --issue <cache>/submissions/<GPU>/<日付>-<sha8>.json --print-url

  JSON (--json): { genmodelfit, command, action: send | logout, comfyui, budget_seconds,
  plan, download_plan, pending, measured?, failed?, interrupted?, downloaded?, files, prs
  [{ url, reused, branch, fork, uploaded, skipped, on_main }], method?, issues?, marked_shared,
  sent, dry_run?, error? }
  --issue: { listed, submissions? | file, ok, refused, checklist, report, share, opened, copied,
  marked_shared }
  詳しくは docs/schema.md""",
    "bench": """動いている ComfyUI で 1 つのモデルを実測し、結果(VRAM・メモリの最大・時間)を保存する。
TEMPLATE は ComfyUI のテンプレートのワークフローの ID そのもの(例 image_z_image_turbo_int8。info の
1 行目の [ ] の中)。モデルの名前や ID の一部は受けない。hf: のモデルにはテンプレートが無い
(代わりに --workflow で API 形式のワークフローを渡す)。
保存した結果はこの PC の判定に使う。
bench は保守する人向け。genmodelfit measure は、テンプレートの ID を知らなくても同じことをする(モデルを
提案し、ComfyUI を探す)。
--share は提出の用意をし、ブラウザで GitHub の実測のフォームを開く: GitHub にログインし(アカウントが
要る)、「Create」を押す(genmodelfit share --issue と同じ)。

前提:
  --url で ComfyUI が動いていて、TEMPLATE のモデルのファイルがすべてその models のフォルダにあること
  (先に genmodelfit download で落とす)。GPU が空いていること: ほかのプログラムが使っている、または
  空きを確かめられない(nvidia-smi)ときは、--force が無ければ測らない。自分だけが使う ComfyUI で測る。
  --free を付けると、先にその ComfyUI のモデルを解放する。

副作用:
  通信: --url の ComfyUI だけ(仕事を 1 つ送り、結果を読む)。その ComfyUI にテンプレートが無ければ、
  GitHub から公式のテンプレートのファイルを読む。この PC のことはほかへ送らない。--share は
  ブラウザでフォームを開く(か URL を出す)だけ。
  結果の JSON を <cache>/measurements/(か --output)に書く。失敗した結果と標準でない条件の結果は、
  --save-failed・--save-nonstandard・--output が無ければ表示だけ。--submission・--share は提出用の
  JSON も書く。
  省いた設定(--width・--height・--frames・--length・--steps)はテンプレートのまま。

終了コード:
  0  成功
  1  失敗: ComfyUI につながらない、GPU が使われている(--force なし)、テンプレートやファイルが無い、
     実行の失敗(メモリ不足など)、時間切れ、Ctrl+C(送った自分の仕事を先に取り消す)
  2  使い方の誤り

使い方の例(エージェント・アプリ向け):
  genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188
  genmodelfit bench image_z_image_turbo_int8 --url http://127.0.0.1:8188 --share
  genmodelfit bench video_wan2_2_5B_ti2v --url http://127.0.0.1:8188 --resolution 640x640 --json

  JSON (--json): { genmodelfit, command, measurement, saved_to, not_saved?, workflow_source,
  warnings, failed?, submission? }
  詳しくは docs/schema.md""",
}


def _help(name: str) -> str:
    """サブコマンドの説明(今の言語。1 つの言語だけで書く。#76)。"""
    return (_HELP_JA if i18n.get_language() == "ja" else _HELP_EN)[name]


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """オプションの説明を全角 2 桁で数えて幅に折り返す。英数字の並び(--include-unknown-license など)の
    途中では切らない(table.wrap_text。#76)。説明(description)・例(epilog)は書いた形のまま。"""

    def _split_lines(self, text: str, width: int) -> list[str]:
        from .table import wrap_text

        return wrap_text(" ".join(text.split()), width, space_room=False)

    def _format_usage(self, usage: Any, actions: Any, groups: Any, prefix: Any) -> str:
        # argparse は続きの行の字下げを文字数で数える。「使い方: 」は表示の幅が文字数より広いので、
        # その差だけ続きの行を右へずらす
        from rich.cells import cell_len

        text = super()._format_usage(usage, actions, groups, prefix)
        head = prefix if prefix is not None else argparse._("usage: ")  # type: ignore[attr-defined]
        extra = cell_len(head) - len(head)
        if extra <= 0:
            return text
        lines = text.split("\n")
        return "\n".join([lines[0], *(" " * extra + ln if ln.strip() else ln for ln in lines[1:])])


class _Parser(argparse.ArgumentParser):
    """説明の折り返しを _HelpFormatter にする(サブコマンドも同じ型で作られる)。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", _HelpFormatter)
        super().__init__(*args, **kwargs)


# argparse の決まった文(usage:・options・-h の説明・誤り)の日本語(#76)。キーは argparse の原文
# (Python 3.10〜3.13)。知らない文は英語のまま
_ARGPARSE_JA = {
    "usage: ": "使い方: ",
    "positional arguments": "引数",
    "options": "オプション",
    "optional arguments": "オプション",
    "show this help message and exit": "この説明を出して終わる",
    "show program's version number and exit": "版を出して終わる",
    "%(prog)s: error: %(message)s\n": "%(prog)s: エラー: %(message)s\n",
    "unrecognized arguments: %s": "知らない引数です: %s",
    "the following arguments are required: %s": "次の引数が要ります: %s",
    "argument %(argument_name)s: %(message)s": "引数 %(argument_name)s: %(message)s",
    "invalid choice: %(value)r (choose from %(choices)s)": (
        "選べない値です: %(value)r(選べるのは %(choices)s)"
    ),
    "invalid %(type)s value: %(value)r": "値が正しくありません(%(type)s): %(value)r",
    "expected one argument": "値を 1 つ指定してください",
    "expected at least one argument": "値を 1 つ以上指定してください",
    "expected at most one argument": "値は 1 つまでです",
    "ambiguous option: %(option)s could match %(matches)s": (
        "略した書き方が紛らわしいオプションです: %(option)s(%(matches)s のどれか)"
    ),
    "not allowed with argument %s": "引数 %s とは同時に指定できません",
    "one of the arguments %s is required": "引数 %s のどれかを指定してください",
    "ignored explicit argument %r": "値 %r は使えません",
}
_ARGPARSE_JA_N = {"expected %s argument": "値を %s 個指定してください"}


_ARGPARSE_LOCK = threading.Lock()
_ARGPARSE_DEPTH = 0
_ARGPARSE_ORIG: tuple[Any, Any] | None = None


@contextlib.contextmanager
def _argparse_language() -> Any:
    """argparse の決まった文を今の言語にする(main の間だけ)。

    差し替えは argparse モジュールの全体に効く。main を同時に複数のスレッドで呼んでも戻せるように、
    入った数を数え、最後に出る main が元に戻す(途中で戻すと、ほかの main の文が英語になる)。
    置き換えた関数は呼ぶたびに言語を見る。言語が en なら元の関数と同じ文を返すので影響はない。
    main の外のスレッドの argparse も、main の間は今の言語で出る(日本語のとき)。"""
    global _ARGPARSE_DEPTH, _ARGPARSE_ORIG
    with _ARGPARSE_LOCK:
        if _ARGPARSE_DEPTH == 0:
            _ARGPARSE_ORIG = (argparse._, argparse.ngettext)  # type: ignore[attr-defined]
            gettext_, ngettext_ = _ARGPARSE_ORIG

            def ja_gettext(msg: str) -> str:
                return _ARGPARSE_JA.get(msg, msg) if i18n.get_language() == "ja" else gettext_(msg)

            def ja_ngettext(singular: str, plural: str, n: int) -> str:
                if i18n.get_language() == "ja" and singular in _ARGPARSE_JA_N:
                    return _ARGPARSE_JA_N[singular]
                return ngettext_(singular, plural, n)

            argparse._, argparse.ngettext = ja_gettext, ja_ngettext  # type: ignore[attr-defined]
        _ARGPARSE_DEPTH += 1
    try:
        yield
    finally:
        with _ARGPARSE_LOCK:
            _ARGPARSE_DEPTH -= 1
            if _ARGPARSE_DEPTH == 0 and _ARGPARSE_ORIG is not None:
                argparse._, argparse.ngettext = _ARGPARSE_ORIG  # type: ignore[attr-defined]
                _ARGPARSE_ORIG = None


def _help_width() -> int:
    """argparse と同じ幅(COLUMNS - 2、最大 78 ではなく argparse の既定どおり)。"""
    import shutil

    return max(shutil.get_terminal_size().columns - 2, 11)


# --help の最後に出す使い方の例(#73。オーナーの依頼)。(コマンド, 日本語の説明, 英語の説明)。
# コマンドは両方の言語で同じ。tests/test_help_examples.py が本物の引数の解釈で通るかを確かめる
EXAMPLES: dict[str, list[tuple[str, str, str]]] = {
    "": [
        (
            "genmodelfit system",
            "この PC の GPU・メモリと一覧の新しさ",
            "this PC's GPU and RAM, and how fresh the list is",
        ),
        ("genmodelfit recommend", "種類ごとのおすすめ(各 3 件)", "top 3 per type for this PC"),
        ("genmodelfit recommend -m image --task edit", "画像の編集のおすすめ", "image editing models"),
        (
            "genmodelfit recommend --uncensored",
            "無検閲のモデルも含める(既定は sfw)",
            "include uncensored models (default is sfw)",
        ),
        ("genmodelfit recommend --vram 12 --ram 32", "別の PC として判定する", "judge for another PC"),
        ('genmodelfit info "Z-Image-Turbo"', "1 つのモデルの詳細", "details of one model"),
        ("genmodelfit recommend --lang ja", "日本語で出す", "output in Japanese"),
    ],
    "recommend": [
        ("genmodelfit recommend", "種類ごとのおすすめ(各 3 件)", "top 3 per type"),
        (
            "genmodelfit recommend -m image --task edit -n 5",
            "画像の編集の上位 5 件",
            "top 5 image editing models",
        ),
        (
            "genmodelfit recommend --uncensored",
            "無検閲のモデルも含める(既定は sfw)",
            "include uncensored models (default is sfw)",
        ),
        ("genmodelfit recommend --vram 12 --ram 32", "別の PC として判定する", "judge for another PC"),
        ("genmodelfit recommend -m video --json", "アプリ向けの JSON", "JSON for apps"),
    ],
    "fit": [
        ("genmodelfit fit", "この PC で動くモデルの全部", "every model that runs on this PC"),
        (
            "genmodelfit fit --sort score --fit good",
            "good だけを種類をまたいだ順位で",
            "good only, ranked across types",
        ),
        (
            "genmodelfit fit --all",
            "動かないもの・補助ツールなども全部",
            "everything, including too tight models and utility tools",
        ),
        (
            "genmodelfit fit -m video --task i2v --vram 12 --ram 32",
            "別の PC で画像から動画",
            "image to video on another PC",
        ),
        (
            "genmodelfit fit --uncensored --json",
            "無検閲も含めて JSON で(既定は sfw)",
            "JSON, uncensored included (default is sfw)",
        ),
    ],
    "info": [
        ('genmodelfit info "Z-Image-Turbo"', "モデルの名前で", "by model name"),
        (
            "genmodelfit info image_z_image_turbo_int8 --json",
            "ワークフローの ID で、JSON",
            "by workflow id, as JSON",
        ),
    ],
    "search": [
        ("genmodelfit search flux", "名前に flux を含むモデル", "models whose name contains flux"),
        (
            "genmodelfit search wan -m video --min-fit good",
            "good 以上の動画だけ",
            "video models that are good or better",
        ),
    ],
    "download": [
        (
            'genmodelfit download "Z-Image-Turbo" --list',
            "1. まず落とすファイル・ライセンス・大きさを見る(落とさない)",
            "1. first see the files, licenses and sizes (downloads nothing)",
        ),
        (
            'genmodelfit download "Z-Image-Turbo" --comfyui-dir C:/ComfyUI',
            "2. ComfyUI のフォルダへ落とす(同意を尋ねる)",
            "2. download into your ComfyUI folder (asks first)",
        ),
        (
            "genmodelfit download MODEL --uncensored --any-license --yes",
            "尋ねずに落とす(無検閲・ライセンス不明も。条件は自分で確かめる)",
            "download without the question, also uncensored / unknown-license (check the terms yourself)",
        ),
    ],
    "measure": [
        (
            "genmodelfit measure",
            "測ると役に立つモデルを選んで測る(何も送らない。送るのは genmodelfit share)",
            "pick a model worth measuring and measure it (sends nothing; genmodelfit share sends it)",
        ),
        ('genmodelfit measure "Z-Image-Turbo"', "モデルを名前で決めて測る", "measure a model given by name"),
        (
            "genmodelfit measure --url http://127.0.0.1:8000",
            "この ComfyUI で測る(探さない)",
            "measure on this ComfyUI (no search)",
        ),
    ],
    "share": [
        ("genmodelfit share", "数分測って、PR で送る", "measure for a few minutes and send a pull request"),
        (
            "genmodelfit share --dry-run",
            "計画と送る中身を見るだけ",
            "only show the plan and what would be sent",
        ),
        ("genmodelfit share --no-measure", "送っていない実測だけを送る", "send only the unsent measurements"),
        (
            "genmodelfit share --issue",
            "ログインせず Issue のフォームで送る",
            "send with the issue form, no sign-in",
        ),
    ],
}


def _examples(name: str) -> str:
    """EXAMPLES の節(無ければ空)。説明は今の言語。"""
    rows = EXAMPLES.get(name, [])
    if not rows:
        return ""
    width = max(len(cmd) for cmd, _, _ in rows)
    lines = [tr("例:", "EXAMPLES:")]
    lines += [f"  {cmd:<{width}}  # {tr(ja, en)}" for cmd, ja, en in rows]
    return "\n".join(lines)


def _doc(action: argparse.Action, ja: str | None = None, en: str | None = None) -> argparse.Action:
    """argparse の既定(default)が None のオプションに、利用者向けの既定の言い方を付ける。
    docs/commands.md の「既定」の欄に出す(動きは変えない。tools/cli_reference.py が読む)。"""
    if en is not None:
        action.doc_default = tr(ja or en, en)  # type: ignore[attr-defined]
    return action


def _tune(
    parser: argparse.ArgumentParser,
    option: str,
    *,
    help: str | None = None,
    default: tuple[str, str] | None = None,
    hide: bool = False,
) -> None:
    """親(parents)から受け継いだオプションの説明・既定の言い方を、このコマンドだけ変える。
    親の Action はほかのコマンドと共有なので、写しを作って差し替える(引数の解釈は変わらない)。
    hide は --help と文書から隠す(受け付けるのは前と同じ。前からのスクリプトを壊さないため)。"""
    import copy

    old = parser._option_string_actions[option]
    new = copy.copy(old)
    if help is not None:
        new.help = help
    if default is not None:
        new.doc_default = tr(*default)  # type: ignore[attr-defined]
    if hide:
        new.help = argparse.SUPPRESS
    parser._actions[parser._actions.index(old)] = new
    for name in old.option_strings:
        parser._option_string_actions[name] = new
    for group in [*parser._action_groups, *parser._mutually_exclusive_groups]:
        acts = group._group_actions
        if old in acts:
            acts[acts.index(old)] = new


def _no_effect(parser: argparse.ArgumentParser, *options: str) -> None:
    """このコマンドでは何もしないオプションを隠し、そう書いた 1 行を --help の最後に足す。"""
    from .table import wrap_text

    for o in options:
        _tune(parser, o, hide=True)
    text = tr(
        f"{'・'.join(options)} も受け付けますが、このコマンドでは何もしません(前からのスクリプトのため)。",
        f"{', '.join(options)} {'are' if len(options) > 1 else 'is'} also accepted but"
        f" {'have' if len(options) > 1 else 'has'} no effect in this command (kept for older scripts).",
    )
    line = "\n".join(wrap_text(text, _help_width(), space_room=False))
    parser.epilog = f"{parser.epilog}\n\n{line}" if parser.epilog else line


def build_parser() -> argparse.ArgumentParser:
    """引数の解釈。説明の文は今の言語で作る(main は --lang を先に読んでから呼ぶ。#73)。"""
    lang = argparse.ArgumentParser(add_help=False)
    lang.add_argument(
        "--lang",
        choices=LANGUAGES,
        default=argparse.SUPPRESS,
        help=tr(
            "表示の言語(既定は環境変数 GENMODELFIT_LANG、genmodelfit config の lang、無ければ en)",
            "display language (default: $GENMODELFIT_LANG, then lang in genmodelfit config, else en)",
        ),
    )
    json_help = tr("JSON で出す", "output JSON")
    common = argparse.ArgumentParser(add_help=False, parents=[lang])
    common.add_argument("--json", action="store_true", help=json_help)
    _doc(
        common.add_argument(
            "--vram",
            type=_non_negative_float,
            metavar="GB",
            help=tr(
                "GPU の VRAM を GB(GiB)で決める(例: 8。0 なら GPU なしとして判定)。"
                "別の PC として判定するときに",
                "judge with this much GPU VRAM in GB (GiB), e.g. 8 (0: no GPU); to judge another PC",
            ),
        ),
        "この PC から調べる",
        "detected on this PC",
    )
    _doc(
        common.add_argument(
            "--ram",
            type=_positive_float,
            metavar="GB",
            help=tr(
                "メインメモリを GB(GiB)で決める(例: 32)。別の PC として判定するときに",
                "judge with this much system RAM in GB (GiB), e.g. 32; to judge another PC",
            ),
        ),
        "この PC から調べる",
        "detected on this PC",
    )
    _doc(
        common.add_argument(
            "--db",
            metavar="PATH",
            help=tr("この一覧のファイル(db.json)を使う", "use this model list file (db.json)"),
        ),
        "update で取った一覧、無ければ同梱の一覧",
        "the list fetched by update, else the bundled list",
    )
    common.add_argument(
        "--no-cache",
        action="store_true",
        help=tr(
            "update で取った一覧を使わず、同梱のデータを使う",
            "use the bundled list, not the one fetched by update",
        ),
    )

    filt = argparse.ArgumentParser(add_help=False)
    filt.add_argument(
        "--modality",
        "-m",
        type=_modality_list,
        metavar="TYPE[,TYPE]",
        help=tr("種類で絞る(カンマ区切り可): ", "filter by type (comma-separated): ") + ", ".join(MODALITIES),
    )
    content_help = tr(
        "内容の印で絞る: sfw(既定)/ uncensored(無検閲だけ)/ all(両方。--uncensored と同じ)。"
        "--uncensored と一緒に使えるのは all だけ。略さずに書く",
        "filter by the content mark: sfw (default) / uncensored (only uncensored) / all (both; same as"
        " --uncensored). Only all can be combined with --uncensored. Write it in full",
    )
    _doc(
        filt.add_argument("--content", choices=CONTENT_FILTERS, default=None, help=content_help), "sfw", "sfw"
    )

    filt.add_argument(
        "--uncensored",
        action="store_true",
        help=tr(
            "無検閲のモデルも含める(--content all と同じ)",
            "include uncensored models (same as --content all)",
        ),
    )
    from .estimate import DEFAULT_SETTINGS, PRECISION_BYTES

    def per_type(key: str, unit_ja: str = "", unit_en: str = "") -> tuple[str, str]:
        vals = [(m, getattr(s, key)) for m, s in DEFAULT_SETTINGS.items() if getattr(s, key)]
        return (
            "種類ごと: " + "、".join(f"{m} {v:g}{unit_ja}" for m, v in vals),
            "by type: " + ", ".join(f"{m} {v:g}{unit_en}" for m, v in vals),
        )

    _doc(
        filt.add_argument(
            "--width",
            type=_positive_int,
            metavar="PX",
            help=tr(
                "画像・動画の幅(ピクセル)で判定する。省いた設定は種類ごとの既定",
                "judge images / videos at this width in pixels; settings left out use the defaults by type",
            ),
        ),
        *per_type("width"),
    )
    _doc(
        filt.add_argument(
            "--height",
            type=_positive_int,
            metavar="PX",
            help=tr("画像・動画の高さ(ピクセル)で判定する", "judge images / videos at this height in pixels"),
        ),
        *per_type("height"),
    )
    _doc(
        filt.add_argument(
            "--frames",
            type=_positive_int,
            metavar="N",
            help=tr("動画のコマ数で判定する", "judge videos at this number of frames"),
        ),
        *per_type("frames"),
    )
    _doc(
        filt.add_argument(
            "--length",
            type=_positive_float,
            metavar="SEC",
            help=tr(
                "音楽・効果音・読み上げの長さ(秒)で判定する",
                "judge music / sound effects / speech at this length in seconds",
            ),
        ),
        *per_type("seconds", " 秒", " s"),
    )
    _doc(
        filt.add_argument(
            "--precision",
            metavar="PREC",
            help=tr(
                "見積もりで本体の重みをこの精度に換算する("
                + "・".join(PRECISION_BYTES)
                + "。元の精度が分かるときだけ)。download の --precision(落とすワークフローを選ぶ)とは別",
                "estimate as if the weights were converted to this precision ("
                + ", ".join(PRECISION_BYTES)
                + "; only when the original precision is known). Not the same as download --precision,"
                " which picks the workflow to download",
            ),
        ),
        "一覧にある精度のまま",
        "the precision in the list",
    )
    filt.add_argument(
        "--limit",
        "-n",
        type=_positive_int,
        metavar="N",
        help=tr("出す行の数(1 以上)", "number of rows to show (1 or more)"),
    )
    filt.add_argument(
        "--limits",
        action="store_true",
        help=tr(
            "この PC の GPU で逃がさずに good になる最大の解像度・コマ数・長さも出す(JSON の limits)",
            "also show the largest resolution / frames / length that is good on this PC's GPU without"
            " offloading (limits in JSON)",
        ),
    )

    gate = argparse.ArgumentParser(add_help=False)
    allow = gate.add_argument(
        "--license-allowlist",
        metavar="LIST|FILE",
        help=tr(
            "自動のおすすめに許すライセンス(「mit,apache-2.0」か、1"
            ' 行に 1 つ書いたファイル)。既定は docs/gate.md。空の文字("")は指定なしと同じ',
            'licenses allowed for auto-recommendation ("mit,apache-2.0", or a file with one per line).'
            ' Default: docs/gate.md. An empty string ("") is the same as not giving it',
        ),
    )
    _doc(allow, "docs/gate.md の許可リスト", "the allowlist in docs/gate.md")
    cver = gate.add_argument(
        "--comfyui-version",
        metavar="VER",
        help=tr(
            "使う ComfyUI の版。アプリは自分の版を必ず渡す(既定は update が記録した最新のリリース)",
            "the ComfyUI version in use; apps should always pass theirs"
            " (default: the latest release recorded by update)",
        ),
    )
    _doc(cver, "一覧が記録した最新のリリース", "the latest release recorded in the list")
    gate.add_argument(
        "--max-head-age-days",
        type=_head_age,
        default=30,
        metavar="DAYS",
        help=tr(
            "HEAD の確認をこの日数まで信じる(既定 30。none で期限なし)",
            "trust HEAD checks for this many days (default 30; none for no limit)",
        ),
    )
    gate.add_argument(
        "--allow-uncensored-auto",
        action="store_true",
        help=tr(
            "無検閲のモデルも自動のおすすめ(Verified)に出してよい(表示の絞り込み・download の同意とは別)",
            "allow uncensored models in automatic recommendations (Verified); not a display filter and"
            " not download consent",
        ),
    )

    epilog = tr(
        "genmodelfit コマンドが見つからない(PATH に無い)とき: pipx・uv tool で入れたなら、pipx ensurepath か"
        " uv tool update-shell を 1 回実行して端末を開き直す。pip で入れたなら、入れたのと同じ Python の"
        " `python -m genmodelfit ...` で動きます(README「入れ方」)。",
        "If the genmodelfit command is not found (not on PATH): after pipx or uv tool, run pipx"
        " ensurepath or uv tool update-shell once and open a new terminal; after pip,"
        ' `python -m genmodelfit ...` works with the Python you installed it into (README "Install").',
    )
    from .table import wrap_text

    p = _Parser(
        prog="genmodelfit",
        description=tr(
            "画像・動画・音楽・効果音・読み上げ・3D の生成モデルが、この PC で動くかを判定する。",
            "Right-size generation models to your PC.",
        ),
        epilog="\n".join(wrap_text(epilog, _help_width(), space_room=False)) + "\n\n" + _examples(""),
        formatter_class=_HelpFormatter,
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"genmodelfit {__version__}",
        # Python 3.10 の argparse はこの既定の文を訳さずに使うので、自分で渡す(#76)
        help=tr("版を出して終わる", "show program's version number and exit"),
    )
    p.add_argument("--lang", choices=LANGUAGES, default=argparse.SUPPRESS, help=lang._actions[0].help)
    sub = p.add_subparsers(dest="command", required=True)

    def sub_parser(name: str, help_text: str, parents: list) -> argparse.ArgumentParser:
        return sub.add_parser(
            name,
            parents=parents,
            help=help_text,
            description=_help(name),
            epilog=_examples(name) or None,
            formatter_class=_HelpFormatter,
        )

    sp = sub_parser(
        "system",
        tr(
            "この PC の GPU・メモリ・空きと、一覧の新しさを出す",
            "show this PC's GPU, memory and free disk, and how fresh the model list is",
        ),
        [common, gate],
    )
    _doc(
        sp.add_argument(
            "--disk-path",
            metavar="PATH",
            help=tr("ディスクの空きを調べる場所(フォルダ)", "the folder whose disk's free space is shown"),
        ),
        "ホームのフォルダ",
        "your home folder",
    )
    sp.add_argument(
        "--all-gpus",
        action="store_true",
        help=tr(
            "NVIDIA 以外も含めて画面の部品(GPU)をすべて出す(Windows は約 1 秒かかる。判定には使わない)",
            "list all display adapters (GPUs), not only NVIDIA"
            " (takes about 1 s on Windows; not used for judging)",
        ),
    )
    sp.set_defaults(func=cmd_system)

    sel = argparse.ArgumentParser(add_help=False)
    sel.add_argument(
        "--min-fit",
        choices=FIT_LEVELS,
        help=tr(
            "この段階以上だけ出す(recommend の既定は marginal、fit / search は全部)",
            "show only this fit level or better (recommend default: marginal; fit / search: all)",
        ),
    )
    sel.add_argument(
        "--license",
        type=_license_list,
        metavar="LIST",
        help=tr(
            "すべてのファイルのライセンスがこの中にあるものだけ(例: apache-2.0,mit。不明は合わない)",
            "only models whose files all have one of these"
            " licenses (e.g. apache-2.0,mit; unknown never matches)",
        ),
    )
    sel.add_argument(
        "--fit",
        choices=list(FIT_FILTERS),
        help=tr(
            "出す段階: perfect / good / marginal(その段階だけ)、runnable(動くもの全部。fit の既定)、"
            "all(too_tight・判定できないものも)。--min-fit とは同時に使えない",
            "fit levels to show: perfect / good / marginal (that"
            " level only), runnable (all that run; fit default),"
            " all (too_tight and unknown too). Not with --min-fit",
        ),
    )
    sel.add_argument(
        "--any-license",
        "--include-unknown-license",
        dest="include_unknown_license",
        action="store_true",
        help=tr(
            "ライセンスの分からないファイルを含むモデルも出す(既定は出さない。info <ID> では常に出る。"
            "前からの書き方 --include-unknown-license も同じ)",
            "also show models with files of unknown license (hidden by default; info <ID> always shows them;"
            " old form --include-unknown-license)",
        ),
    )

    sel.add_argument(
        "--task",
        type=_task_list,
        metavar="USE[,USE]",
        help=tr(
            "用途で絞る(gen / edit / i2v / lipsync / motion / voice / upscale / tool、"
            "または t2i・i2i・inpaint などの tasks。一覧は genmodelfit types)",
            "filter by use (gen / edit / i2v / lipsync / motion / voice / upscale / tool,"
            " or task codes such as t2i, i2i, inpaint; see genmodelfit types)",
        ),
    )
    sel.add_argument(
        "--all-workflows",
        action="store_true",
        help=tr(
            "同じモデル(精度・使い方だけ違うもの)をまとめず、ワークフローごとに 1 行ずつ出す",
            "one row per workflow instead of grouping the same model (differing only in precision or use)",
        ),
    )
    tools_help = tr(
        "深度・ポーズ・切り抜き・拡大などの補助ツールも含める(既定は「つくる」モデルだけ)",
        "include utility tools such as depth, pose, background removal and upscaling"
        " (default: generation models only)",
    )

    sp = sub_parser(
        "recommend", tr("おすすめ順に出す", "recommend models, best first"), [common, filt, sel, gate]
    )
    sp.add_argument(
        "--auto-only",
        action="store_true",
        help=tr("自動でおすすめに出せるものだけ", "only auto-recommendable models"),
    )
    sp.add_argument("--include-tools", action="store_true", help=tools_help)
    sp.set_defaults(func=cmd_recommend)  # 既定の 10 件・marginal は cmd_recommend で決める(親の引数は共有)
    _tune(sp, "--limit", default=("種類ごとに 3(-m・--task があれば 10)", "3 per type (10 with -m / --task)"))
    _tune(sp, "--min-fit", default=("marginal", "marginal"))

    listing = argparse.ArgumentParser(add_help=False)
    listing.add_argument(
        "--sort",
        choices=SORTS,
        default="type",
        help=tr(
            "並べ方: type(既定。種類 → 段階 → 公式 → 人気 → Score)、score(種類をまたいで段階 → Score)、"
            "vram / disk(少ない順)、name",
            "order: type (default; type -> fit level -> official -> popularity -> Score),"
            " score (across types; level -> Score), vram / disk (smallest first), name",
        ),
    )
    listing.add_argument(
        "--all",
        action="store_true",
        help=tr(
            "全部出す(動かないもの・判定できないもの・補助ツール・ライセンス不明も。無検閲は --uncensored)",
            "show everything (too tight, unknown, utility tools,"
            " unknown license; uncensored needs --uncensored)",
        ),
    )
    listing.add_argument(
        "--perfect",
        "-p",
        action="store_true",
        help=tr("perfect だけ(--fit perfect と同じ)", "perfect only (same as --fit perfect)"),
    )

    sp = sub_parser(
        "fit",
        tr(
            "動くモデルの判定を表で出す(--all で全部)",
            "judge the models that run, as a table (--all for all)",
        ),
        [common, filt, sel, gate, listing],
    )
    sp.add_argument("--include-tools", action="store_true", help=tools_help)
    sp.set_defaults(func=cmd_fit)
    _tune(sp, "--limit", default=("全部", "all"))
    _tune(sp, "--fit", default=("runnable(--all なら all)", "runnable (all with --all)"))

    sp = sub_parser(
        "search",
        tr(
            "ID・名前の一部で探して、この PC での判定を出す",
            "search by a part of the id or name and judge the matches for this PC",
        ),
        [common, filt, sel, gate, listing],
    )
    sp.add_argument(
        "query",
        help=tr(
            "探す語(ID・名前の一部。大文字小文字は区別しない)",
            "search word (part of the id or name; case-insensitive)",
        ),
    )
    sp.set_defaults(func=cmd_search)
    _tune(sp, "--limit", default=("全部", "all"))
    _tune(sp, "--fit", default=("すべての段階", "every level"))

    sp = sub.add_parser(
        "types",
        parents=[common],
        help=tr(
            "大きな種類と、その中の用途(--task の名前)と数",
            "the big types, the uses inside each (names for --task), and counts",
        ),
        description=_help("types"),
        formatter_class=_HelpFormatter,
    )
    _doc(
        sp.add_argument(
            "--content",
            choices=CONTENT_FILTERS,
            default=None,
            help=tr(
                "数えるモデルの内容の印: sfw(既定)/ uncensored(無検閲だけ)/ all(両方。--uncensored と同じ)。"
                "略さずに書く",
                "which content marks to count: sfw (default) / uncensored (only uncensored) / all"
                " (both; same as --uncensored). Write it in full",
            ),
        ),
        "sfw",
        "sfw",
    )
    sp.add_argument(
        "--uncensored",
        action="store_true",
        help=tr(
            "無検閲のモデルも含める(--content all と同じ)",
            "include uncensored models (same as --content all)",
        ),
    )
    sp.set_defaults(func=cmd_types)
    _no_effect(sp, "--vram", "--ram")

    sp = sub_parser("info", tr("1 つのモデルの詳細", "details of one model"), [common, filt, gate])
    sp.add_argument(
        "model",
        help=tr(
            "モデルの名前(表の Model)・ワークフローの ID・そのどちらかの一部",
            "model name (the Model column), workflow id, or a part of either",
        ),
    )
    sp.set_defaults(func=cmd_info)
    _no_effect(sp, "--modality", "--limit", "--limits")

    sp = sub_parser(
        "update",
        tr(
            "新しい一覧を取る(利用者は --from-release。オプションなしは上流から作り直す)",
            "get a newer model list (users: --from-release; without options it rebuilds from upstream)",
        ),
        [common, gate],
    )
    sp.add_argument(
        "--offline-sources",
        metavar="DIR",
        help=tr("(試験用)上流の代わりに使うファイル", "(for tests) files to use instead of upstream"),
    )
    sp.add_argument("--skip-hf", action="store_true", help=tr("Hugging Face を見ない", "skip Hugging Face"))
    sp.add_argument(
        "--skip-head",
        action="store_true",
        help=tr("URL の HEAD 確認を飛ばす", "skip the HEAD checks of URLs"),
    )
    sp.add_argument(
        "--heads-only",
        action="store_true",
        help=tr(
            "今の一覧はそのままで、URL の HEAD と ComfyUI の最新の版の確認だけをやり直す",
            "keep the current list; redo only the URL HEAD checks and the latest ComfyUI release check",
        ),
    )
    sp.add_argument(
        "--fields-only",
        action="store_true",
        help=tr(
            "今の一覧はそのままで、アプリ向けの項目(言語・動かし方・説明)と手のモデル(data/manual.json)だけを"
            "当て直す。通信するのは、言語を読むカードと手のモデルのファイルだけ(--offline で通信せず、"
            "保存済みのカードの言語を使う)",
            "keep the current list; re-apply only the app fields (languages, runtime, description) and the"
            " hand-added models (data/manual.json). Only the model cards (for languages) and the hand-added"
            " models' files are fetched (--offline: no network, use the stored card languages)",
        ),
    )
    sp.add_argument(
        "--inspect-only",
        action="store_true",
        help=tr(
            "今の一覧はそのままで、本体のファイルの見出し(精度)"
            "・テンプレートのサンプラーの組と読み込みノードの精度・"
            "リポジトリの精度(Hub API・config.json)だけを読み直し、401 だったリポジトリを確かめ直す"
            "(Range 要求。重み本体は落とさない。読んだ結果はキャッシュに残る)",
            "keep the current list; re-read only the weight file headers (precision), the templates' sampler"
            " settings and loader precision, and the repository precision (Hub API, config.json), and recheck"
            " repositories that returned 401 (Range requests;"
            " weights are not downloaded; results are cached)",
        ),
    )
    sp.add_argument(
        "--offline",
        action="store_true",
        help=tr(
            "--inspect-only / --fields-only で通信せず、キャッシュにある見出し・リポジトリの精度だけを使う",
            "with --inspect-only / --fields-only: no network;"
            " use only the cached headers and repository precision",
        ),
    )
    _doc(
        sp.add_argument(
            "--output",
            metavar="PATH",
            help=tr(
                "一覧の書き出し先(既定はキャッシュの db.json)",
                "where to write the list (default: db.json in the cache)",
            ),
        ),
        "キャッシュの db.json",
        "db.json in the cache",
    )
    sp.add_argument(
        "--from-release",
        action="store_true",
        help=tr(
            "配布された署名つきの一覧を取り込む(署名が合わなければ何も変えない)。--output があればそこへ書く",
            "take in the signed list published by this project (nothing changes if the signature does not"
            " match). Writes to --output if given",
        ),
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help=tr(
            "(--from-release)前に取り込んだ一覧より古くても取り込む",
            "(--from-release) take it in even if it is older than the list taken in before",
        ),
    )
    sp.add_argument(
        "--allow-partial",
        action="store_true",
        help=tr(
            "一部(テンプレート・Hugging Face)を取得できなくても書き出す(sources.partial が true になる)",
            "write the list even if some sources (templates,"
            " Hugging Face) failed (sources.partial becomes true)",
        ),
    )
    sp.add_argument(
        "--rate-limit-budget",
        type=float,
        metavar="SEC",
        help=tr(
            "Hugging Face の上限(429)で待つ時間の合計の上限(秒。既定 900)。超えたら止める",
            "the most time to wait in total for Hugging Face rate limits (429), in seconds (default 900);"
            " stops when exceeded",
        ),
    )
    sp._actions[-1].doc_default = "900"  # type: ignore[attr-defined]
    sp.set_defaults(func=cmd_update)
    only_with = tr(
        "(--heads-only・--fields-only・--inspect-only のときだけ使う)",
        " (used only with --heads-only, --fields-only or --inspect-only)",
    )
    _tune(
        sp,
        "--db",
        help=tr("元にする一覧のファイル(db.json)", "the list file (db.json) to start from") + only_with,
    )
    _tune(
        sp,
        "--no-cache",
        help=tr(
            "update で取った一覧ではなく、同梱の一覧を元にする",
            "start from the bundled list, not the one fetched by update",
        )
        + only_with,
    )
    _no_effect(sp, "--vram", "--ram")

    sp = sub_parser(
        "download",
        tr(
            "モデルのファイルを落とす(同意・sha256・続きから・ComfyUI のフォルダ)",
            "download the files of a model (consent, sha256, resume, ComfyUI folders)",
        ),
        [common, gate],
    )
    sp.add_argument(
        "model",
        help=tr(
            "モデルの名前(表の Model)・ワークフローの ID・その一部",
            "model name (the Model column), workflow id, or a part of either",
        ),
    )
    _doc(
        sp.add_argument(
            "--precision",
            metavar="PREC",
            help=tr(
                "同じモデルのワークフローのうち、この精度のものを落とす(例: fp8 / bf16 / int8 / gguf-q8_0。"
                "あるものは表の Prec・info で分かり、無ければ誤りにして並べる)。recommend などの --precision"
                "(見積もりの換算)とは別",
                "download the workflow of the same model that has this precision (e.g. fp8 / bf16 / int8 /"
                " gguf-q8_0; the Prec column and info show which exist, and an unknown one is an error that"
                " lists them). Not the same as --precision of recommend etc. (which converts the estimate)",
            ),
        ),
        "recommend と同じ選び方(上を見る)",
        "chosen like recommend (see above)",
    )
    sp.add_argument(
        "--budget",
        type=_positive_float,
        metavar="GB",
        help=tr(
            "落とす量の上限(GB。既にあるファイルは数えない)。収まる中で一番良いワークフローを選び、"
            "収まるものが無ければ断る",
            "the most to download in GB (files already there are not counted); the best workflow within it"
            " is chosen, and when none fits the download is refused",
        ),
    )
    sp.add_argument(
        "--with-optional",
        action="store_true",
        help=tr(
            "テンプレートが既定で使わない部品(無効・素通しのノードなどにしかないもの)も落とす(#81)",
            "also download parts the template does not use by default"
            " (only on muted or bypassed nodes, etc.; #81)",
        ),
    )
    sp.add_argument(
        "--list",
        action="store_true",
        help=tr(
            "落とすファイルと合計を出すだけ(落とさない・通信しない)",
            "only list the files and the total (no download, no network)",
        ),
    )
    where = sp.add_mutually_exclusive_group()
    where.add_argument(
        "--output-dir",
        metavar="DIR",
        help=tr(
            "ComfyUI の外の置き場所。ファイルは <DIR>/<folder>/<file> に置く"
            "(folder は diffusion_models・vae など。ComfyUI の models と同じ並びなので、"
            "ComfyUI の models のフォルダそのものを渡してもよい)",
            "a destination outside ComfyUI; files go to <DIR>/<folder>/<file> (folder: diffusion_models,"
            " vae, ...; the same layout as ComfyUI's models folder, so passing that models folder itself"
            " also works)",
        ),
    )
    where.add_argument(
        "--comfyui-dir",
        metavar="DIR",
        help=tr(
            "ComfyUI のフォルダ。ファイルは <DIR>/models/<folder>/<file> に置く(既定は環境変数"
            " GENMODELFIT_COMFYUI_DIR、genmodelfit config の comfyui-dir、自動で見つけたもの)",
            "the ComfyUI folder; files go to <DIR>/models/<folder>/<file>"
            " (default: $GENMODELFIT_COMFYUI_DIR, then comfyui-dir in genmodelfit config, then found"
            " automatically)",
        ),
    )
    _doc(
        where._group_actions[0], "なし(--comfyui-dir の決め方で決める)", "none (decided as for --comfyui-dir)"
    )
    _doc(
        where._group_actions[1],
        "GENMODELFIT_COMFYUI_DIR → config の comfyui-dir → 自動で見つけたもの",
        "GENMODELFIT_COMFYUI_DIR, then config, then found automatically",
    )
    sp.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=tr(
            "同意の確認を省く(--json で落とすときは必須)",
            "skip the consent question (required to download with --json)",
        ),
    )
    consent = sp.add_argument_group(
        tr(
            "同意(端末では y/N で尋ねる。--yes・--json・端末でないときに指定する。略さずに書く)",
            "consent (asked y/N in a terminal; pass these with --yes, --json or no terminal;"
            " write them in full)",
        )
    )
    consent.add_argument(
        "--uncensored",
        action="store_true",
        help=tr(
            "無検閲のモデルも落とす(前からの書き方 --content all と同じ)",
            "also download uncensored models (old form --content all)",
        ),
    )
    consent.add_argument(
        "--any-license",
        "--include-unknown-license",
        dest="include_unknown_license",
        action="store_true",
        help=tr(
            "ライセンスの分からないファイルを含むモデルも落とす(条件は自分で確かめる。"
            "前からの書き方 --include-unknown-license も同じ)",
            "also download models with files of unknown license (check the terms yourself;"
            " old form --include-unknown-license)",
        ),
    )
    consent.add_argument(
        "--content",
        choices=CONTENT_FILTERS,
        default=None,
        help=tr(
            "名前で探すモデルの内容の印: sfw(既定)/ uncensored(無検閲だけ)/ all(両方)。"
            "uncensored と all は無検閲のモデルを落とす同意にもなる"
            "(--uncensored は all と同じ。--uncensored と一緒に使えるのは all だけ)",
            "which content marks the name search looks at: sfw (default) / uncensored (only uncensored) / all"
            " (both). uncensored and all also give consent to download uncensored models (--uncensored is the"
            " same as all; only all can be combined with --uncensored)",
        ),
    )
    sp._actions[-1].doc_default = "sfw"  # type: ignore[attr-defined]
    sp.set_defaults(func=cmd_download)

    # genmodelfit measure / share(#113。CONTRIBUTING.md「かんたんな送り方」)
    sp = sub_parser(
        "measure",
        tr(
            "測ると役に立つモデルを選んで、ComfyUI で実測する(無ければ起動する。何も送らない)",
            "pick a model worth measuring and measure it on ComfyUI (started if needed; nothing is sent)",
        ),
        [lang],
    )
    sp.add_argument("--json", action="store_true", help=json_help)
    _doc(
        sp.add_argument(
            "--db",
            metavar="PATH",
            help=tr("この一覧のファイル(db.json)を使う", "use this model list file (db.json)"),
        ),
        "update で取った一覧、無ければ同梱の一覧",
        "the list fetched by update, else the bundled list",
    )
    sp.add_argument(
        "--no-cache",
        action="store_true",
        help=tr(
            "update で取った一覧を使わず、同梱のデータを使う",
            "use the bundled list, not the one fetched by update",
        ),
    )
    sp.add_argument(
        "model",
        nargs="?",
        help=tr(
            "測るモデルの名前(表の Model)・ワークフローの ID・その一部"
            "(省くと、測ると役に立つモデルを出して選ぶ)",
            "model name (the Model column), workflow id, or a part of either (omitted: list models worth"
            " measuring and choose one)",
        ),
    )
    _doc(
        sp.add_argument(
            "--url",
            help=tr(
                "測りに使う ComfyUI の URL(例 http://127.0.0.1:8188)",
                "URL of the ComfyUI to measure on (e.g. http://127.0.0.1:8188)",
            ),
        ),
        "この PC で起動中の ComfyUI(127.0.0.1 の 8188・8000)を探す",
        "a ComfyUI running on this PC (127.0.0.1 ports 8188 and 8000)",
    )
    _doc(
        sp.add_argument(
            "--comfyui-dir",
            metavar="DIR",
            help=tr(
                "ComfyUI のフォルダ(モデルが入っているかを見る・入っていないとき落とす場所)",
                "your ComfyUI folder (to see whether the model is installed, and where to download it)",
            ),
        ),
        "download と同じ決め方",
        "decided like download",
    )
    sp.add_argument(
        "--no-start",
        action="store_true",
        help=tr(
            "起動中の ComfyUI が無くても、見つけた ComfyUI を起動しない"
            "(環境変数 GENMODELFIT_COMFYUI_START=0 と同じ)",
            "do not start the ComfyUI that was found when none is running (like GENMODELFIT_COMFYUI_START=0)",
        ),
    )
    sp.add_argument(
        "--start-timeout",
        type=_positive_float,
        metavar="SEC",
        help=tr(
            "起動した ComfyUI の準備を待つ時間の上限(秒。既定 300)",
            "the most time to wait for a started ComfyUI to be ready (seconds; default 300)",
        ),
    )
    sp._actions[-1].doc_default = "300"  # type: ignore[attr-defined]
    sp.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=tr(
            "測る前に尋ねない(入っていないモデルは download --yes と同じ決まりで落とす)",
            "do not ask before measuring (a model that is not installed is downloaded under the same rules as"
            " download --yes)",
        ),
    )
    sp.add_argument(
        "--delete-downloaded",
        action="store_true",
        help=tr(
            "測るために落としたファイル(この実行で新しく落としたものだけ)を、終わったら尋ねずに消す",
            "after measuring, delete without asking the files this run downloaded to measure (only new ones)",
        ),
    )
    sp.add_argument(
        "--free",
        action="store_true",
        help=tr(
            "測る前に ComfyUI の /free でモデルと前の結果を外す(起動中の自分の ComfyUI 向け。"
            "genmodelfit が起動した ComfyUI ではいつも外す)",
            "free the ComfyUI's models and cached results with /free before measuring (for your own"
            " running ComfyUI; a ComfyUI started by genmodelfit is always freed)",
        ),
    )
    sp.add_argument(
        "--resolution",
        metavar="WxH",
        help=tr(
            "幅と高さ(例: 1536x1536)を変えて測る(bench と同じ。省くとテンプレートのまま)",
            "measure at this width and height (e.g. 1536x1536; like bench; omitted: as in the template)",
        ),
    )
    sp.add_argument(
        "--frames",
        type=_positive_int,
        metavar="N",
        help=tr(
            "動画のコマ数を変えて測る(bench と同じ。省くとテンプレートのまま)",
            "measure at this number of video frames (like bench; omitted: as in the template)",
        ),
    )
    sp.add_argument(
        "--length",
        type=_positive_float,
        metavar="SEC",
        help=tr(
            "音声の長さ(秒)を変えて測る(bench と同じ。省くとテンプレートのまま)",
            "measure at this audio length in seconds (like bench; omitted: as in the template)",
        ),
    )
    sp.add_argument(
        "--limit",
        "-n",
        type=_positive_int,
        metavar="N",
        help=tr(
            f"引数なしで出す提案の数(既定 {MEASURE_LIMIT})",
            f"how many suggestions to list without a model (default {MEASURE_LIMIT})",
        ),
    )
    sp.add_argument(
        "--timeout",
        type=float,
        metavar="SEC",
        help=tr(
            "待つ時間の上限(0 より大きい秒数。既定 1800)", "the most time to wait (seconds > 0; default 1800)"
        ),
    )
    sp._actions[-1].doc_default = "1800"  # type: ignore[attr-defined]
    sp.set_defaults(func=cmd_measure, output_dir=None, vram=None, ram=None)  # この PC で測る(置き換えない)

    sp = sub_parser(
        "share",
        tr(
            "入っているモデルを数分測り、送っていない実測といっしょに GitHub の PR で送る"
            "(--issue は Issue のフォームで送る)",
            "measure installed models for a few minutes and send them, with your unsent measurements, as a"
            " GitHub pull request (--issue: send with the issue form)",
        ),
        [lang],
    )
    sp.add_argument(
        "file",
        nargs="?",
        help=tr(
            "送る提出の JSON(省くと、測ってから送っていない実測をすべて送る)",
            "the submission JSON to send (omitted: measure, then send all unsent measurements)",
        ),
    )
    sp.add_argument("--json", action="store_true", help=json_help)
    sp.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help=tr(
            "測る・送る前に尋ねない(入っていないモデルを落とすことには同意しない)",
            "do not ask before measuring and sending (does not agree to downloading models)",
        ),
    )
    sp.add_argument(
        "--download",
        action="store_true",
        help=tr(
            "時間が残れば、入っていない提案も落として測る(download と同じ決まり。尋ねない)",
            "if time is left, also download and measure suggestions that are not installed (same rules as"
            " download; no question)",
        ),
    )
    sp.add_argument(
        "--delete-downloaded",
        action="store_true",
        help=tr(
            "測るために落としたファイル(この実行で新しく落としたものだけ)を、終わったら尋ねずに消す",
            "at the end, delete without asking the files this run downloaded to measure (only new ones)",
        ),
    )
    sp.add_argument(
        "--any-license",
        dest="include_unknown_license",
        action="store_true",
        help=tr(
            "--download・端末でないとき、ライセンスの分からないモデルも落とす(download と同じ。略せない)",
            "with --download or without a terminal, also download models whose license is unknown (as in"
            " download; cannot be abbreviated)",
        ),
    )
    sp.add_argument(
        "--no-measure",
        action="store_true",
        help=tr("測らずに、送っていない実測だけを送る", "do not measure; send only the unsent measurements"),
    )
    _doc(
        sp.add_argument(
            "--budget",
            type=_positive_float,
            metavar="SEC",
            help=tr(
                "測る時間の合計の上限(時間の目安の合計。秒)",
                "the most time to spend measuring (sum of the rough time guides; seconds)",
            ),
        ),
        "300",
        "300",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help=tr(
            "計画と送る中身を見せるだけ(測らない・ログインしない・GitHub に通信しない。"
            "ComfyUI が起動しているかは 127.0.0.1 に尋ねる)",
            "only show the plan and what would be sent (no measuring, no sign-in, no network access to"
            " GitHub; it asks 127.0.0.1 whether ComfyUI runs)",
        ),
    )
    sp.add_argument(
        "--issue",
        action="store_true",
        help=tr(
            "ログインせず、GitHub の Issue のフォームで送る(--yes なら測ってから。無ければ前からの送り方)",
            "send with the GitHub issue form instead, without signing in (with --yes after measuring;"
            " without it the older way)",
        ),
    )
    sp.add_argument(
        "--print-url",
        action="store_true",
        help=tr(
            "--issue で、ブラウザを開かず、フォームの URL を 1 行だけ出す(スクリプト向け)",
            "with --issue: do not open a browser; print only the URL of the form (for scripts)",
        ),
    )
    sp.add_argument(
        "--logout",
        action="store_true",
        help=tr("保存した GitHub のログイン(トークン)を消す", "remove the saved GitHub sign-in (token)"),
    )
    sp.add_argument(
        "--all",
        action="store_true",
        help=tr("送った印のある実測も含める", "also include measurements already marked as sent"),
    )
    sp.add_argument(
        "--mark-sent",
        action="store_true",
        help=tr(
            "--issue --json でも送った印を付ける(アプリがフォームを開くとき。既定の --json は見るだけ)",
            "with --issue --json, also mark it as sent (for apps that open the form themselves; by default"
            " --json only previews)",
        ),
    )
    _doc(
        sp.add_argument(
            "--url",
            help=tr(
                "測りに使う ComfyUI の URL(measure と同じ)",
                "URL of the ComfyUI to measure on (like measure)",
            ),
        ),
        "この PC で起動中の ComfyUI(127.0.0.1 の 8188・8000)を探す",
        "a ComfyUI running on this PC (127.0.0.1 ports 8188 and 8000)",
    )
    _doc(
        sp.add_argument(
            "--comfyui-dir",
            metavar="DIR",
            help=tr(
                "ComfyUI のフォルダ(モデルが入っているかを見る・落とす場所。measure と同じ)",
                "your ComfyUI folder (to see whether models are installed, and where to download; like"
                " measure)",
            ),
        ),
        "download と同じ決め方",
        "decided like download",
    )
    sp.add_argument(
        "--no-start",
        action="store_true",
        help=tr(
            "起動中の ComfyUI が無くても、見つけた ComfyUI を起動しない"
            "(環境変数 GENMODELFIT_COMFYUI_START=0 と同じ)",
            "do not start the ComfyUI that was found when none is running (like GENMODELFIT_COMFYUI_START=0)",
        ),
    )
    sp.add_argument(
        "--start-timeout",
        type=_positive_float,
        metavar="SEC",
        help=tr(
            "起動した ComfyUI の準備を待つ時間の上限(秒。既定 300)",
            "the most time to wait for a started ComfyUI to be ready (seconds; default 300)",
        ),
    )
    sp._actions[-1].doc_default = "300"  # type: ignore[attr-defined]
    sp.add_argument(
        "--timeout",
        type=_positive_float,
        metavar="SEC",
        help=tr(
            "1 件を測るのを待つ時間の上限(秒。既定 1800)",
            "the most time to wait for one run (seconds; default 1800)",
        ),
    )
    sp._actions[-1].doc_default = "1800"  # type: ignore[attr-defined]
    _doc(
        sp.add_argument(
            "--db",
            metavar="PATH",
            help=tr(
                "この一覧のファイル(db.json)を使う(測るものを選ぶ・送る前の検査)",
                "use this model list file (db.json) (to choose what to measure and to check before sending)",
            ),
        ),
        "選ぶのは update で取った一覧(無ければ同梱)、検査は同梱の一覧",
        "choosing: the list fetched by update, else the bundled list; checks: the bundled list",
    )
    sp.add_argument(
        "--no-cache",
        action="store_true",
        help=tr(
            "update で取った一覧を使わず、同梱のデータを使う",
            "use the bundled list, not the one fetched by update",
        ),
    )
    # ComfyUI・モデルの置き場所の決め方は measure と同じ(この PC で測る。置き換えない)
    sp.set_defaults(func=cmd_share, output_dir=None, vram=None, ram=None)

    # genmodelfit config(#111。docs/download.ja.md「genmodelfit config」)
    sp = sub_parser(
        "config",
        tr(
            "設定(download の ComfyUI のフォルダ・表示の言語)を見る・変える",
            "show or change settings (download's ComfyUI folder, display language)",
        ),
        [lang],
    )
    sp.add_argument("--json", action="store_true", help=json_help)
    csub = sp.add_subparsers(dest="action", metavar="{show,set,unset,path}")
    c = csub.add_parser(
        "show",
        parents=[lang],
        help=tr(
            "今の設定と出どころ(サブコマンドを省いたときはこれ)",
            "current settings and where they come from (the default when no subcommand is given)",
        ),
    )
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    c = csub.add_parser("set", parents=[lang], help=tr("値を保存する", "save a value"))
    c.add_argument("key", help=tr("comfyui-dir か lang", "comfyui-dir or lang"))
    c.add_argument(
        "value",
        help=tr(
            "comfyui-dir なら ComfyUI のフォルダのパス、lang なら en か ja",
            "for comfyui-dir: the path of the ComfyUI folder; for lang: en or ja",
        ),
    )
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    c = csub.add_parser("unset", parents=[lang], help=tr("値を消す", "remove a value"))
    c.add_argument("key", help=tr("comfyui-dir か lang", "comfyui-dir or lang"))
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    c = csub.add_parser("path", parents=[lang], help=tr("設定ファイルの場所", "where the config file is"))
    c.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=json_help)
    sp.set_defaults(func=cmd_config)

    sp = sub_parser(
        "gate",
        tr("自動でおすすめに出せるかの集計", "summary of which models can be auto-recommended"),
        [common, gate],
    )
    sp.add_argument(
        "--modality",
        "-m",
        choices=MODALITIES,
        help=tr("この種類だけ数える(1 つだけ)", "count only this type (one type)"),
    )
    only = sp.add_mutually_exclusive_group()
    only.add_argument(
        "--only-auto",
        action="store_true",
        help=tr("自動で出せるものの ID も並べる", "also list the ids of auto-recommendable models"),
    )
    only.add_argument(
        "--only-held",
        action="store_true",
        help=tr("保留のものの ID と理由も並べる", "also list the ids of held models and why"),
    )
    sp.set_defaults(func=cmd_gate)
    _no_effect(sp, "--vram", "--ram")

    sp = sub.add_parser(
        "diff",
        parents=[lang],
        help=tr(
            "2 つの一覧(db.json)の違い(増減・ライセンス・自動のおすすめ・HEAD の失敗)",
            "differences between two model lists (db.json): added / removed, licenses, auto-recommendation,"
            " HEAD failures",
        ),
        description=_help("diff"),
        formatter_class=_HelpFormatter,
    )
    sp.add_argument(
        "old",
        nargs="?",
        help=tr(
            "前の一覧。省くと同梱の一覧と取ってきた一覧(update のキャッシュ)を比べる。"
            "1 つだけなら同梱の一覧とそのファイルを比べる",
            "the old list. Omitted: compare the bundled list with the fetched list (the update cache)."
            " With one file: compare the bundled list with that file",
        ),
    )
    sp.add_argument("new", nargs="?", help=tr("新しい一覧", "the new list"))
    sp.add_argument("--json", action="store_true", help=json_help)
    sp.add_argument(
        "--all",
        action="store_true",
        help=tr(
            "保守用の内容も出す(前からの HEAD の失敗・401"
            " のリポジトリ・sources の数・人気の数などだけの変化)",
            "also show the maintenance view (older HEAD failures, 401 repositories, sources numbers,"
            " popularity-only changes)",
        ),
    )
    sp.add_argument(
        "--comfyui-version",
        metavar="VER",
        help=tr(
            "自動のおすすめを比べるときの ComfyUI の版(既定は新しい一覧の記録)",
            "ComfyUI version used to compare auto-recommendation (default: recorded in the new list)",
        ),
    )
    sp.add_argument(
        "--limit",
        "-n",
        type=_positive_int,
        default=None,
        metavar="N",
        help=tr(
            "1 つの節に出す行の上限(既定 20。--all では全部)",
            "max lines per section (default 20; all with --all)",
        ),
    )
    sp._actions[-1].doc_default = tr("20(--all なら全部)", "20 (all with --all)")  # type: ignore[attr-defined]
    sp.set_defaults(func=cmd_diff)

    db_cache_help = tr(
        "update で取った一覧を使わず、同梱のデータを使う",
        "use the bundled list, not the one fetched by update",
    )
    write_db_help = tr(
        "書き換える一覧(例 src/genmodelfit/data/db.json)",
        "the list file to rewrite (e.g. src/genmodelfit/data/db.json)",
    )
    sp = sub.add_parser(
        "review",
        parents=[lang],
        help=tr(
            "確認待ち(新しい項目・ライセンスの変化など)の一覧・確かめる・外す(保守する人向け)",
            "list, approve or reject models waiting for review (new entries, license changes, ...;"
            " for maintainers)",
        ),
        description=_review_help(),
        formatter_class=_HelpFormatter,
    )
    rsub = sp.add_subparsers(dest="review_command", required=True)
    r = rsub.add_parser(
        "list", parents=[lang], help=tr("確認待ちの項目と理由", "models waiting for review and why")
    )
    r.add_argument("--json", action="store_true", help=json_help)
    r.add_argument(
        "--db",
        metavar="PATH",
        help=tr(
            "一覧のファイル(既定はキャッシュ、無ければ同梱)",
            "the list file (default: the cache, else the bundled list)",
        ),
    )
    r.add_argument("--no-cache", action="store_true", help=db_cache_help)
    r.add_argument("--all", action="store_true", help=tr("確認済みの項目も出す", "also show approved models"))
    r.set_defaults(func=cmd_review_list)
    r = rsub.add_parser(
        "approve",
        parents=[lang],
        help=tr(
            "根拠(一次資料の URL)を付けて確認済みにする(一覧のファイルを書き換える)",
            "approve with evidence (primary-source URLs); rewrites the list file",
        ),
    )
    r.add_argument("id", help=tr("モデルの ID", "model id"))
    r.add_argument(
        "--evidence",
        action="append",
        metavar="URL",
        help=tr(
            "根拠の URL(https。1 つ以上。繰り返して足す)", "evidence URL (https; one or more; repeat to add)"
        ),
    )
    r.add_argument("--note", help=tr("確かめた内容のメモ", "a note on what was checked"))
    r.add_argument(
        "--by",
        default="review",
        help=tr(
            "確かめた人・仕組み(decided_by。既定 review)",
            "who or what checked it (decided_by; default review)",
        ),
    )
    r.add_argument("--db", required=True, metavar="PATH", help=write_db_help)
    r.add_argument("--json", action="store_true", help=json_help)
    r.set_defaults(func=cmd_review_approve)
    r = rsub.add_parser(
        "reject",
        parents=[lang],
        help=tr(
            "一覧から外し、update でも入れないように記録する",
            "remove from the list and record it so that update does not add it again",
        ),
    )
    r.add_argument("id", help=tr("モデルの ID", "model id"))
    r.add_argument("--note", required=True, help=tr("外す理由", "why it is removed"))
    r.add_argument("--db", required=True, metavar="PATH", help=write_db_help)
    r.add_argument(
        "--rejected",
        metavar="PATH",
        help=tr(
            "外した記録のファイル(既定は --db と同じフォルダの review_rejected.txt)",
            "the record file (default: review_rejected.txt in the same folder as --db)",
        ),
    )
    r.add_argument(
        "--hide-name",
        action="store_true",
        help=tr(
            "記録に ID を残さず sha256 だけ残す(安全上の理由で外すとき。既存の除外と同じく名前を残さない)",
            "record only the sha256, not the id (when removing"
            " for safety reasons; like the existing exclusions)",
        ),
    )
    r.add_argument("--json", action="store_true", help=json_help)
    r.set_defaults(func=cmd_review_reject)

    sp = sub_parser(
        "bench",
        tr(
            "保守する人向け: 動いている ComfyUI で実測する(ふつうは measure)",
            "for maintainers: measure on a running ComfyUI (usually: measure)",
        ),
        [common],
    )
    sp.add_argument(
        "template",
        help=tr(
            "ComfyUI のテンプレートのワークフローの ID そのもの(info の 1 行目の [ ] の中)",
            "the exact workflow id of a ComfyUI template (the id in [ ] on the first line of info)",
        ),
    )
    sp.add_argument(
        "--url",
        required=True,
        help=tr(
            "測りに使う ComfyUI の URL(例 http://127.0.0.1:8188。既定は持たない)",
            "URL of the ComfyUI to measure on (e.g. http://127.0.0.1:8188; no default)",
        ),
    )
    sp.add_argument(
        "--workflow",
        metavar="PATH",
        help=tr(
            "API 形式のワークフロー(省略時はテンプレートから作る)",
            "workflow in API format (default: built from the template)",
        ),
    )
    as_template = ("テンプレートの値", "the template's value")
    for flag, metavar, kind, ja, en in (
        ("--width", "PX", _positive_int, "幅(ピクセル)", "width in pixels"),
        ("--height", "PX", _positive_int, "高さ(ピクセル)", "height in pixels"),
        ("--frames", "N", _positive_int, "動画のコマ数", "number of video frames"),
        ("--length", "SEC", _positive_float, "音声の長さ(秒)", "audio length in seconds"),
        ("--steps", "N", _positive_int, "サンプラーのステップ数", "number of sampler steps"),
    ):
        _doc(
            sp.add_argument(
                flag,
                type=kind,
                metavar=metavar,
                help=tr(f"{ja}(省くとテンプレートのまま)", f"{en} (omitted: as in the template)"),
            ),
            *as_template,
        )
    sp.add_argument(
        "--output",
        metavar="PATH",
        help=tr(
            "結果の JSON の書き出し先(失敗・標準でない結果もここへ書く)",
            "where to write the result JSON (failed and non-standard results are written here too)",
        ),
    )
    sp._actions[-1].doc_default = tr(  # type: ignore[attr-defined]
        "キャッシュの measurements/", "measurements/ in the cache"
    )
    sp.add_argument(
        "--save-failed",
        action="store_true",
        help=tr(
            "失敗(OOM など)の結果もキャッシュに保存する(既定は表示だけ)",
            "also save failed results (OOM etc.) to the cache (default: show only)",
        ),
    )
    sp.add_argument(
        "--save-nonstandard",
        action="store_true",
        help=tr(
            "標準でない条件(--workflow・--set・一部だけ実行など)の結果もキャッシュに保存する",
            "also save results under non-standard conditions"
            " (--workflow, --set, partial runs, ...) to the cache",
        ),
    )
    sp.add_argument(
        "--resolution",
        metavar="WxH",
        help=tr(
            "幅と高さ(例: 768x768)。--width/--height と同じ",
            "width and height (e.g. 768x768); same as --width/--height",
        ),
    )
    sp.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help=tr(
            "入力を名前で変える(例: octree_resolution=192)",
            "change an input by name (e.g. octree_resolution=192)",
        ),
    )
    sp.add_argument(
        "--free",
        action="store_true",
        help=tr(
            "開始前に /free でその ComfyUI のモデルを解放する(自分だけが使う ComfyUI のときに)",
            "free that ComfyUI's models with /free before starting (for a ComfyUI only you use)",
        ),
    )
    sp.add_argument(
        "--timeout",
        type=float,
        metavar="SEC",
        help=tr(
            "待つ時間の上限(0 より大きい秒数。既定 1800)", "the most time to wait (seconds > 0; default 1800)"
        ),
    )
    sp._actions[-1].doc_default = "1800"  # type: ignore[attr-defined]
    sp.add_argument(
        "--force",
        action="store_true",
        help=tr(
            "GPU が使われている・GPU の空きを確かめられないときも測る",
            "measure even when the GPU is in use or its free memory cannot be checked",
        ),
    )
    sp.add_argument(
        "--source",
        help=tr("誰がどこで測ったか(結果の source に入る)", "who measured it where (goes into source)"),
    )
    sp.add_argument("--note", help=tr("結果の note に足す文", "text to add to the result's note"))
    sp.add_argument(
        "--submission",
        metavar="PATH",
        help=tr(
            "提出用の JSON(contrib_schema 3)も書く。フォルダなら <フォルダ>/<GPU>/<日付>-<sha8>.json"
            "(フォークの data/contrib を渡すとそのまま PR にできる)。何も送らない(CONTRIBUTING.md)",
            "also write a submission JSON (contrib_schema 3). For a folder: <folder>/<GPU>/<date>-<sha8>.json"
            " (pass your fork's data/contrib to open a PR as is). Nothing is sent (CONTRIBUTING.md)",
        ),
    )
    sp.add_argument(
        "--share",
        action="store_true",
        help=tr(
            "提出用の JSON を書き(--submission が無ければキャッシュの submissions に)、"
            "中身と点検を見せて、GitHub の実測のフォームを開く"
            "(genmodelfit share --issue と同じ。端末でなければ URL を出す)。"
            "送るには GitHub のアカウントが要る。genmodelfit は何も送らない(CONTRIBUTING.md)",
            "write the submission JSON (to the cache's submissions folder without --submission),"
            " show it with the checks, and open GitHub's measurement form (like genmodelfit share --issue;"
            " without a terminal it prints the URL). Sending needs a GitHub account; genmodelfit itself sends"
            " nothing (CONTRIBUTING.md)",
        ),
    )
    sp.set_defaults(func=cmd_bench)
    _no_effect(sp, "--vram", "--ram")

    cj = argparse.ArgumentParser(add_help=False, parents=[lang])
    cj.add_argument("--json", action="store_true", help=json_help)
    cj.add_argument(
        "--db",
        metavar="PATH",
        help=tr(
            "モデル ID を確かめる一覧(既定は同梱のもの。キャッシュは使わない)",
            "the list used to check model ids (default: the bundled list; the cache is not used)",
        ),
    )
    sp = sub.add_parser(
        "contrib",
        parents=[lang],
        help=tr(
            "保守する人向け: 実測の提出を作る・検査する・まとめる(送るのは measure と share)",
            "for maintainers: make, validate and aggregate measurement submissions (to send one: measure"
            " and share)",
        ),
        description=_contrib_help(),
        formatter_class=_HelpFormatter,
    )
    csub = sp.add_subparsers(dest="contrib_command", required=True)
    c = csub.add_parser(
        "make",
        parents=[cj],
        help=tr(
            "bench が保存した結果から提出用の JSON を作る",
            "make a submission JSON from a result saved by bench",
        ),
    )
    c.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="FILE",
        help=tr("bench の結果の JSON", "the bench result JSON"),
    )
    c.add_argument(
        "--output",
        default=CONTRIB_DIR_DEFAULT,
        metavar="PATH",
        help=tr(
            f"書き出す場所(フォルダなら <GPU>/<日付>-<sha8>.json。既定 {CONTRIB_DIR_DEFAULT})",
            f"where to write (for a folder: <GPU>/<date>-<sha8>.json; default {CONTRIB_DIR_DEFAULT})",
        ),
    )
    c.add_argument(
        "--no-pc-info",
        action="store_true",
        help=tr(
            "この PC の OS の種類・メモリ・GPU の世代を入れない(ComfyUI が別の PC だったとき)",
            "do not include this PC's OS family, RAM and GPU generation (when ComfyUI ran on another PC)",
        ),
    )
    c.set_defaults(func=cmd_contrib_make)
    c = csub.add_parser(
        "show",
        parents=[cj],
        help=tr("送る中身と個人の情報の点検を見せる", "show what will be shared and the privacy checklist"),
    )
    c.add_argument("file", help=tr("提出用の JSON", "the submission JSON"))
    c.set_defaults(func=cmd_contrib_show)
    c = csub.add_parser(
        "share",
        parents=[cj],
        help=tr(
            "genmodelfit share --issue と同じ(前からのスクリプトのため)",
            "the same as genmodelfit share --issue (kept for older scripts)",
        ),
    )
    c.add_argument(
        "file",
        nargs="?",
        help=tr(
            "送る提出の JSON(省くと、送っていない実測を出して選ぶ)",
            "the submission JSON to send (omitted: list unsent measurements and choose one)",
        ),
    )
    c.add_argument(
        "--print-url",
        action="store_true",
        help=tr(
            "ブラウザを開かず、フォームの URL を 1 行だけ出す(スクリプト向け)",
            "do not open a browser; print only the URL of the form (for scripts)",
        ),
    )
    c.add_argument(
        "--all",
        action="store_true",
        help=tr("送った印のある実測も一覧に出す", "also list measurements already marked as sent"),
    )
    c.add_argument(
        "--mark-sent",
        action="store_true",
        help=tr("--json でも送った印を付ける", "with --json, also mark it as sent"),
    )
    c.set_defaults(func=cmd_contrib_share)
    c = csub.add_parser(
        "validate",
        parents=[cj],
        help=tr(
            "形・値・個人の情報・重複を検査する(CI でも使う)",
            "check shape, values, personal information and duplicates (also used in CI)",
        ),
    )
    c.add_argument(
        "files",
        nargs="+",
        metavar="FILE|DIR",
        help=tr(
            "提出用の JSON(フォルダなら下の *.json すべて)",
            "submission JSON files (for a folder: every *.json under it)",
        ),
    )
    c.add_argument(
        "--strict",
        action="store_true",
        help=tr("注意(warnings)も失敗にする", "treat warnings as failures too"),
    )
    c.add_argument(
        "--no-filename-check",
        action="store_true",
        help=tr(
            "ファイル名(<GPU>/<日付>-<sha8>.json)を確かめない",
            "do not check the file name (<GPU>/<date>-<sha8>.json)",
        ),
    )
    c.add_argument(
        "--markdown",
        action="store_true",
        help=tr("Markdown の表で出す(CI のまとめ用)", "output a Markdown table (for the CI summary)"),
    )
    c.set_defaults(func=cmd_contrib_validate)
    c = csub.add_parser(
        "intake",
        parents=[cj],
        help=tr(
            "GitHub の実測の Issue を検査し、PR に入れるファイルを作る(measurement-intake.yml が使う)",
            "check a measurement issue on GitHub and write the files for its PR (used by"
            " measurement-intake.yml)",
        ),
    )
    c.add_argument(
        "event", metavar="EVENT_JSON", help=tr("issues のイベントのファイル", "the issues event file")
    )
    c.add_argument(
        "--existing",
        default=CONTRIB_DIR_DEFAULT,
        metavar="DIR",
        help=tr(
            f"同じ内容の提出を探すフォルダ(既定 {CONTRIB_DIR_DEFAULT})",
            f"the folder searched for the same submission (default {CONTRIB_DIR_DEFAULT})",
        ),
    )
    c.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help=tr("結果のファイルを書くフォルダ", "the folder to write the result files to"),
    )
    c.set_defaults(func=cmd_contrib_intake)
    c = csub.add_parser(
        "aggregate",
        parents=[cj],
        help=tr(
            "同じモデル・設定・GPU ごとにまとめ、精度を点検する",
            "aggregate by model, settings and GPU, and check the accuracy",
        ),
    )
    c.add_argument(
        "dir",
        nargs="?",
        default=CONTRIB_DIR_DEFAULT,
        help=tr(
            f"提出のフォルダ(既定 {CONTRIB_DIR_DEFAULT})",
            f"the submissions folder (default {CONTRIB_DIR_DEFAULT})",
        ),
    )
    c.add_argument(
        "--output",
        metavar="FILE",
        help=tr(
            "まとめを書き出す(同梱するなら src/genmodelfit/data/measurements_community.json)",
            "write the result (to bundle it: src/genmodelfit/data/measurements_community.json)",
        ),
    )
    c.add_argument(
        "--report",
        action="store_true",
        help=tr(
            "校正をやり直し、種類ごとの誤差の表と閾値の判定を出す",
            "redo the calibration and show the error table per type and the threshold check",
        ),
    )
    c.add_argument(
        "--threshold",
        type=_positive_float,
        default=25.0,
        metavar="PCT",
        help=tr("誤差の閾値(%%、既定 25)", "error threshold (%%, default 25)"),
    )
    c.add_argument(
        "--fail-on-breach",
        action="store_true",
        help=tr(
            f"--report で閾値を超えたら終了コード {EXIT_BREACH} にする",
            f"with --report, exit with code {EXIT_BREACH} when the threshold is exceeded",
        ),
    )
    c.add_argument(
        "--allow-empty",
        action="store_true",
        help=tr(
            "まとめが空でも --output に書き出す(既定は、空のまとめで上書きしない)",
            "write to --output even if the result is empty (by default an empty result does not overwrite)",
        ),
    )
    c.add_argument(
        "--allow-file-sources",
        action="store_true",
        help=tr(
            "git の履歴が無くても(出どころを提出のファイルごとに数えても)--output に書き出す。"
            "試験・手元の確かめだけ。版を出すときは使わない",
            "write to --output even without git history (sources counted per file). For tests and local"
            " experiments only; never for a release",
        ),
    )
    c.add_argument(
        "--check",
        action="store_true",
        help=tr(
            "書き出さず、同梱のまとめにまだ入っていない提出を数える(あれば終了コード 1)",
            "write nothing; count the submissions not in the bundled aggregate yet (exit code 1 if any)",
        ),
    )
    c.set_defaults(func=cmd_contrib_aggregate)
    return p


_REVIEW_HELP_JA = """確認待ち(review.status = pending)の項目を出す・根拠を付けて確かめる・一覧から外す
(保守する人向け)。
pending は update が前の一覧と比べて付ける
(new / license_permissive / need_decreased / uncensored_new / files_changed)。
確かめるまで自動のおすすめに出さない(gate の reviewed)。

  list     [--json] [--all] [--db PATH]          確認待ちと理由
  approve  <id> --evidence URL [--evidence URL…] [--note TEXT] [--by NAME] --db PATH
           根拠が無ければ断る。一覧のファイルをその場で書き換える
  reject   <id> --note TEXT --db PATH [--rejected FILE] [--hide-name]
           一覧から外し、review_rejected.txt に記録する(update でも入れない)

副作用:
  通信しない。list は読むだけ。approve / reject は --db のファイルを書き換える(reject は外した
  記録のファイルにも足す)。

終了コード:
  0  成功
  1  失敗(ID が無い・根拠が無い・確認待ちでない・--db が読めない。--json なら誤りは "error" に入る)
  2  使い方の誤り

使い方の例(エージェント・アプリ向け):
  genmodelfit review list --json --db src/genmodelfit/data/db.json
  genmodelfit review approve hf:org/model --db src/genmodelfit/data/db.json \\
      --evidence https://huggingface.co/org/model/blob/main/LICENSE
  genmodelfit review reject hf:org/model --db src/genmodelfit/data/db.json --note "商用には使えない"
詳しくは docs/schema.md「review」、docs/release.md「毎週のスキル」"""

_REVIEW_HELP_EN = """List, approve or reject models waiting for review (for maintainers).
update marks an entry pending (review.status = pending) when it differs from the previous list
(new / license_permissive / need_decreased / uncensored_new / files_changed).
Pending entries are not auto-recommended until approved (gate check: reviewed).

  list     [--json] [--all] [--db PATH]          entries waiting for review and why
  approve  <id> --evidence URL [--evidence URL…] [--note TEXT] [--by NAME] --db PATH
           refused without evidence; rewrites the list file in place
  reject   <id> --note TEXT --db PATH [--rejected FILE] [--hide-name]
           removes the entry and records it in review_rejected.txt (update does not add it again)

SIDE EFFECTS:
  No network access. list is read-only; approve / reject rewrite the --db file (reject also appends to the
  rejected file).

EXIT CODES:
  0  Success
  1  Error (no such id, no evidence, not pending, unreadable --db; with --json the error is in "error")
  2  Usage error

AGENT USAGE:
  genmodelfit review list --json --db src/genmodelfit/data/db.json
  genmodelfit review approve hf:org/model --db src/genmodelfit/data/db.json \\
      --evidence https://huggingface.co/org/model/blob/main/LICENSE
  genmodelfit review reject hf:org/model --db src/genmodelfit/data/db.json --note "non-commercial only"
Details: docs/schema.md ("review"), docs/release.md (weekly skill)"""


def _review_help() -> str:
    return tr(_REVIEW_HELP_JA, _REVIEW_HELP_EN)


CONTRIB_DIR_DEFAULT = "data/contrib"
_CONTRIB_HELP_JA = """実測の提出用 JSON(contrib_schema 3)を作る・送る前に見る・検査する・まとめる
(保守する人向け。実測を送るのは genmodelfit measure と genmodelfit share)。
genmodelfit は何も送らない(共有は、利用者が GitHub のフォームで「Create」を押すときか、自分で
Pull Request を出すときだけ。CONTRIBUTING.md)。

  make      --from <bench の結果>  → data/contrib/<GPU>/<日付>-<sha8>.json
  show      <file>                 送る中身と個人の情報の点検(同意のため)
  share     [file]                 genmodelfit share --issue と同じ(前からのスクリプトのため)
  intake    <event.json> --out DIR GitHub の実測の Issue を検査し、PR に入れるファイルを作る
                                   (measurement-intake.yml が使う)
  validate  <files|dirs>...        形・値の範囲・個人の情報・重複・ファイル名を検査
  aggregate [dir] [--output F] [--report] [--threshold 25] [--fail-on-breach]
            同じモデル・設定・GPU ごとに中央値と MAD で外れ値を除いてまとめ、校正の誤差を点検
  aggregate [dir] --check
            同梱のまとめにまだ入っていない提出を数える(まとめは版を出すときに作る)

副作用:
  通信しない。make は提出のファイルを書く。aggregate は --output のときだけ書く。intake は --out に書く。
  share は genmodelfit share --issue と同じ(端末ならブラウザを開く。--json・--print-url・端末でないときは
  URL を出すだけ。送った印を書く)。

終了コード:
  0  成功(validate: すべてのファイルが通った。show: 直すところが無い)
  1  失敗(読めないファイル・知らない形など)
  1  validate: 機械的な検査に通らないファイルがある、または --strict で注意がある
     (マージする人向けの確認では失敗にしない)
  1  show: 直すところがある
  1  share: 個人の情報の点検に NG がある、または検査の誤りがある(送れない)
  1  aggregate: フォルダが無い、または --output で結果が空(--allow-empty が無いとき)
  1  aggregate --check: 同梱のまとめに入っていない提出がある、またはまとめ直すと違う
  2  使い方の誤り
  3  aggregate --report --fail-on-breach: 誤差が閾値を超えた

使い方の例(エージェント・アプリ向け):
  genmodelfit bench image_sdxl_simple --url http://127.0.0.1:8188 --submission data/contrib/
  genmodelfit contrib show data/contrib/<GPU>/<日付>-<sha8>.json
  genmodelfit contrib share data/contrib/<GPU>/<日付>-<sha8>.json --json
  genmodelfit contrib validate data/contrib --json
  genmodelfit contrib aggregate --report --json --fail-on-breach
詳しくは docs/schema.md「contrib」"""

_CONTRIB_HELP_EN = """Make, preview, validate and aggregate measurement submissions (contrib_schema 3; for
maintainers. To send a measurement use genmodelfit measure and genmodelfit share).
genmodelfit sends nothing (data is shared only when you press "Create" in the GitHub form or open a
Pull Request yourself; CONTRIBUTING.md).

  make      --from <bench result>  → data/contrib/<GPU>/<date>-<sha8>.json
  show      <file>                 what will be shared, and the privacy checklist (for consent)
  share     [file]                 the same as genmodelfit share --issue (kept for older scripts)
  intake    <event.json> --out DIR check a measurement issue on GitHub and write the files for its PR
                                   (used by measurement-intake.yml)
  validate  <files|dirs>...        check shape, value ranges, personal information, duplicates, file names
  aggregate [dir] [--output F] [--report] [--threshold 25] [--fail-on-breach]
            group by model, settings and GPU, drop outliers by median and MAD, check the calibration error
  aggregate [dir] --check
            count the submissions not in the bundled aggregate yet (it is rebuilt for each release)

SIDE EFFECTS:
  No network access. make writes the submission files; aggregate writes only with --output; intake writes to
  --out.
  share is genmodelfit share --issue (opens a browser in a terminal; with --json, --print-url or without a
  terminal it only prints the URL; writes the sent marks).

EXIT CODES:
  0  Success (validate: every file passed; show: nothing to fix)
  1  Error (an unreadable file, an unknown shape, ...)
  1  validate: a file failed the mechanical checks, or has a warning with --strict (notices for the
     maintainer who merges never fail)
  1  show: something to fix
  1  share: a privacy check is NG or the checks have errors (cannot share)
  1  aggregate: no such folder, or an empty result with --output (unless --allow-empty)
  1  aggregate --check: submissions not in the bundled aggregate yet, or re-aggregating it differs
  2  Usage error
  3  aggregate --report --fail-on-breach: an error exceeded the threshold

AGENT USAGE:
  genmodelfit bench image_sdxl_simple --url http://127.0.0.1:8188 --submission data/contrib/
  genmodelfit contrib show data/contrib/<GPU>/<date>-<sha8>.json
  genmodelfit contrib share data/contrib/<GPU>/<date>-<sha8>.json --json
  genmodelfit contrib validate data/contrib --json
  genmodelfit contrib aggregate --report --json --fail-on-breach
Details: docs/schema.md ("contrib")"""


def _contrib_help() -> str:
    return tr(_CONTRIB_HELP_JA, _CONTRIB_HELP_EN)


def _quiet_stdout() -> None:
    # 終了時の flush で再び落ちないように、出力先(fd)ごと捨て先に向ける(Python の文書の推奨手順)
    with contextlib.suppress(OSError, ValueError, AttributeError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    with contextlib.suppress(OSError):
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows の cp932 でも落ちず、組み込み側が UTF-8 で読めるように
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    err = sys.stderr
    if not isinstance(err, _OrderedStderr):
        sys.stderr = _OrderedStderr(err)
    old_hup = _catch_hangup()
    try:
        return _main_outer(argv)
    finally:
        sys.stderr = err
        _restore_hangup(old_hup)


def _hangup(signum, frame):  # pragma: no cover - シグナルで呼ばれる
    raise KeyboardInterrupt  # 端末を閉じた(SIGHUP): Ctrl+C と同じく、起動した ComfyUI を止めて終える(L1a)


def _catch_hangup():
    """POSIX: 端末を閉じた(SIGHUP)ときも finally を通す(起動した ComfyUI を残さない。L1a)。Windows は
    Job Object(comfystart.kill_on_close_job)で止める。"""
    import signal
    import threading

    if not hasattr(signal, "SIGHUP") or threading.current_thread() is not threading.main_thread():
        return None
    try:
        return signal.signal(signal.SIGHUP, _hangup)
    except (ValueError, OSError):
        return None


def _restore_hangup(old) -> None:
    import signal

    if old is not None and hasattr(signal, "SIGHUP"):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGHUP, old)


class _OrderedStderr:
    """stderr に書く前に stdout を流す(パイプ・ファイルでも、画面の文と注意・途中経過の順が入れ替わらない。
    #124)。ほかは元の stderr のまま。"""

    def __init__(self, inner):
        self._inner = inner

    def write(self, text):
        with contextlib.suppress(Exception):
            sys.stdout.flush()
        return self._inner.write(text)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _main_outer(argv: list[str]) -> int:
    lang = _lang_in(argv) or _config_lang(argv)  # 説明の文を作る前に言語を決める(#73・#111)
    # ContextVar で固定するので、ほかのスレッドのライブラリの呼び出しには効かず、main を出れば元に戻る
    with i18n.forced(lang) if lang else contextlib.nullcontext(), _argparse_language():
        return _main(argv)


def _config_lang(argv: list[str]) -> str | None:
    """genmodelfit config の lang(#111)。環境変数 GENMODELFIT_LANG があればそちら(None)。
    壊れた設定ファイルは使わずに、stderr に 1 回だけ注意を出す(config のコマンドは自分で出す)。"""
    if i18n.normalize(os.environ.get(i18n.ENV_VAR)) is not None:
        return None
    from . import config

    values, notes = config.load()
    if notes and (not argv or argv[0] != "config"):
        for n in notes:
            print(tr(f"注意: {n}", f"warning: {n}"), file=sys.stderr)
    return values.get("lang")


def _is_lang_flag(a: str) -> bool:
    """--lang と、argparse が受ける略し方(--la・--lan。--l は --limit などと紛れるので数えない)。"""
    return len(a) >= 4 and "--lang".startswith(a)


def _lang_in(argv: list[str]) -> str | None:
    """--lang X / --lang=X(略した --lan X も)を先に読む(-- より後は見ない)。
    正しくない値は None(argparse が誤りにする)。"""
    found = None
    for k, a in enumerate(argv):
        if a == "--":
            break
        if _is_lang_flag(a) and k + 1 < len(argv):
            found = argv[k + 1]
        elif "=" in a and _is_lang_flag(a.split("=", 1)[0]):
            found = a.split("=", 1)[1]
    return found if found in LANGUAGES else None


# 同意の指定(年齢・ライセンスの同意)は略して書けない(1〜3 文字の打ち間違いで同意したことにならないように。
# #75)。argparse の略し方(--u → --uncensored など)を、解釈する前に止める
CONSENT_FLAGS = ("--uncensored", "--any-license", "--include-unknown-license", "--content")


def _check_consent_spelling(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    for a in argv:
        if a == "--":
            break
        name = a.split("=", 1)[0]
        if not name.startswith("--") or len(name) <= 2 or name in CONSENT_FLAGS:
            continue
        full = [f for f in CONSENT_FLAGS if f.startswith(name)]
        if full:
            parser.error(
                tr(
                    f"{name}: 同意の指定は略さずに書いてください({' / '.join(full)})",
                    f"{name}: write consent flags in full ({' / '.join(full)})",
                )
            )


def _main(argv: list[str]) -> int:
    parser = build_parser()
    if not argv:  # 引数なしは使い方の全文を出して正常に終わる(初めて試す人向け)
        parser.print_help()
        return 0
    _check_consent_spelling(parser, argv)
    args = parser.parse_args(argv)
    chosen = getattr(args, "lang", None)
    # 先に読めなかった形の --lang(念のため)。説明の文から作り直す
    if chosen and chosen != i18n.get_language():
        with i18n.forced(chosen):
            return _main(argv)
    # --uncensored は --content all と同じ(#75)。--content sfw とは同時に使えない
    if hasattr(args, "content"):
        if getattr(args, "uncensored", False):
            if args.content not in (None, "all"):
                parser.error(
                    tr(
                        f"--uncensored(= --content all)と --content {args.content} は同時に指定できません",
                        f"--uncensored (= --content all) and --content {args.content}"
                        " cannot be used together",
                    )
                )
            args.content = "all"
        elif args.content is None:
            args.content = DEFAULT_CONTENT
    # 使い方の誤り(argparse と同じく終了コード 2)
    if getattr(args, "min_fit", None) is not None and (
        getattr(args, "fit", None) is not None or getattr(args, "perfect", False)
    ):
        parser.error(
            tr(
                "--fit(--perfect)と --min-fit は同時に指定できません",
                "--fit (--perfect) and --min-fit cannot be used together",
            )
        )
    try:
        return args.func(args)
    except BrokenPipeError:  # | head などで出力を途中で閉じられた
        _quiet_stdout()
        return 0
    except OSError as e:
        if getattr(e, "errno", None) == 22 and not sys.stdout.isatty():  # Windows でパイプを閉じられたとき
            _quiet_stdout()
            return 0
        raise
    except (CliError, DatabaseError) as e:
        code = 2 if isinstance(e, UsageError) else 1
        cands = getattr(e, "candidates", None)
        if getattr(args, "json", False):
            err: dict[str, Any] = {"genmodelfit": __version__, "command": args.command, "error": str(e)}
            if cands is not None:
                err["candidates"] = [{"id": m.id, "name": m.name, "content": m.content} for m in cands]
            _out(err)
        else:
            print(tr(f"エラー: {e}", f"error: {e}"), file=sys.stderr)
            for m in (cands or [])[:30]:
                print(f"  {m.id}  ({m.name})", file=sys.stderr)
            if cands and len(cands) > 30:
                rest = len(cands) - 30
                print(
                    tr(f"  …ほか {rest} 件(--json で全部)", f"  … {rest} more (--json for all)"),
                    file=sys.stderr,
                )
        return code


if __name__ == "__main__":
    raise SystemExit(main())
