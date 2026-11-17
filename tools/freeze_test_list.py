# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""試験の凍らせた一覧(tests/fixtures/frozen/db.json)を作り直す・項目を足す・写し直す(#197)。

    python tools/freeze_test_list.py                       # 今の項目をそのまま、決まった形で書き直す
    python tools/freeze_test_list.py --add <id> [<id> …]    # 同梱の一覧から項目を足す
    python tools/freeze_test_list.py --refresh <id> [<id> …]  # その項目だけ同梱の一覧の今の中身で写し直す
    python tools/freeze_test_list.py --rederive            # 計算で決まる値を今の規則で当て直す
    python tools/freeze_test_list.py --rehash              # 項目の記録(sha256)を今の中身で作り直す
    python tools/freeze_test_list.py --check               # 何も書かず、形か記録が合わなければ 1 で終わる

手入れの手順(いつ足すか・一覧の形を変えたとき・同梱の一覧から消えた項目)は tests/frozenlist.py の
先頭にある。項目は同梱の一覧の JSON をそのまま写し、引数なしでは今の凍らせた項目を動かさない(同梱の一覧の
中身で置き換えない)。並びは ID の順、sources の counts と review は写した項目で数え直す。
項目ごとの sha256 を sources._frozen_sha256 に残し、手で直した項目は --check と引数なしの書き直しで見つける
(認めるなら --rehash)。ネットワークには触らない。tests/test_frozenlist.py がこの道具を確かめる。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genmodelfit.db import DatabaseError, parse_db  # noqa: E402
from genmodelfit.update import count_entries  # noqa: E402

FROZEN = ROOT / "tests" / "fixtures" / "frozen" / "db.json"
BUNDLED = ROOT / "src" / "genmodelfit" / "data" / "db.json"
HASH_KEY = "_frozen_sha256"

NOTE = (
    "試験が名指しする項目だけを同梱の一覧から写した凍らせた一覧(#197)。毎週の更新で同梱の一覧の"
    "顔ぶれが変わっても、規則の試験が同じ入力で確かめられるようにする。項目は手で直さず、"
    "tools/freeze_test_list.py で足す(--add)・写し直す(--refresh)・計算で決まる値を当て直す(--rederive)。"
    "手入れの手順は tests/frozenlist.py の先頭。"
    "_frozen_from は最初に写した元(同梱の一覧の db.json を最後に変えたコミットと generated_at)、"
    "_frozen_later はその後に足した・写し直した項目の元。"
    "_frozen_sha256 は項目ごとの中身の sha256(手で直すと --check が落ちる。認めるなら --rehash)。"
    "一覧の形を変える PR は、同梱の一覧に当てたのと同じ変換をこの一覧にも当て、--rehash で記録を作り直す。"
    "ほかの sources は最初に写した元のまま(counts・review だけ写した項目で数え直す)"
)


class FreezeError(Exception):
    """凍らせた一覧を作れない(足す項目が同梱の一覧に無い・一覧の形の版が違う・記録と合わない、など)。"""


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _commit(path: Path) -> str:
    """path を最後に変えたコミット(短い形)。git が無い・リポジトリの外なら "unknown"。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(path.parent), "log", "-1", "--format=%h", "--", path.name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out or "unknown"


def entry_hash(m: dict[str, Any]) -> str:
    """項目の中身の sha256(キーの順・区切りを決めた JSON の UTF-8 のバイト)。"""
    text = json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_problems(frozen: dict[str, Any]) -> list[str]:
    """記録(sources._frozen_sha256)と合わない ID(中身が違う・記録が無い・項目が無い記録)。"""
    rec = frozen.get("sources", {}).get(HASH_KEY)
    rec = rec if isinstance(rec, dict) else {}
    have = {m["id"]: entry_hash(m) for m in frozen["models"]}
    bad = [i for i, h in have.items() if rec.get(i) != h]
    bad += [i for i in rec if i not in have]
    return sorted(set(bad))


def _with_hashes(frozen: dict[str, Any], ids: Sequence[str] | None = None) -> dict[str, Any]:
    """ids の項目(None は全部)の記録を今の中身で作り直したもの。項目の無い記録は消す。"""
    sources = dict(frozen.get("sources", {}))
    old = sources.get(HASH_KEY) if isinstance(sources.get(HASH_KEY), dict) else {}
    have = {m["id"]: m for m in frozen["models"]}
    take = set(have) if ids is None else set(ids)
    rec = {i: (entry_hash(have[i]) if i in take else old.get(i)) for i in have}
    sources[HASH_KEY] = {i: h for i, h in rec.items() if h is not None}
    return {**frozen, "sources": sources}


def render(frozen: dict[str, Any]) -> str:
    """凍らせた一覧の決まった形(ID の順・数え直した sources・indent 1・末尾の改行)。読めない形なら
    DatabaseError(parse_db と同じ)。"""
    models = sorted(frozen["models"], key=lambda m: m["id"])
    db = parse_db({**frozen, "models": models}, "path")  # 形の確かめ(読めない項目は写さない)
    src = {
        k: v
        for k, v in frozen.get("sources", {}).items()
        if k not in ("_frozen", "counts", "review", HASH_KEY)
    }
    old_review = frozen.get("sources", {}).get("review") or {}
    rec = frozen.get("sources", {}).get(HASH_KEY)
    sources = {
        "_frozen": NOTE,
        **src,
        "counts": count_entries(db.models),
        "review": {
            "ok": sum(1 for m in db.models if m.reviewed),
            "pending": sum(1 for m in db.models if not m.reviewed),
            "marked": old_review.get("marked", {}),
            "rejected_skipped": old_review.get("rejected_skipped", 0),
        },
    }
    if isinstance(rec, dict):
        sources[HASH_KEY] = dict(sorted(rec.items()))
    out = {
        "schema_version": frozen["schema_version"],
        "generated_at": frozen.get("generated_at"),
        "sources": sources,
        "models": models,
    }
    return json.dumps(out, ensure_ascii=False, indent=1) + "\n"


def update(
    frozen: dict[str, Any],
    bundled: dict[str, Any] | None,
    *,
    add: Sequence[str] = (),
    refresh: Sequence[str] = (),
    commit: str = "unknown",
) -> dict[str, Any]:
    """frozen に、bundled(同梱の一覧の JSON)から add の項目を足し、refresh の項目を写し直したもの
    (その項目の記録も足す・置き換える)。"""
    if not add and not refresh:
        return frozen
    if bundled is None:
        raise FreezeError("同梱の一覧が要る(--add / --refresh)")
    if bundled.get("schema_version") != frozen.get("schema_version"):
        raise FreezeError(
            f"一覧の形の版が違う(同梱 {bundled.get('schema_version')} / 凍らせた一覧 "
            f"{frozen.get('schema_version')})。先に凍らせた一覧を今の形に直す(tests/frozenlist.py の先頭)"
        )
    by = {m["id"]: m for m in bundled["models"]}
    have = {m["id"] for m in frozen["models"]}
    missing = [i for i in [*add, *refresh] if i not in by]
    if missing:
        raise FreezeError(f"同梱の一覧に無い ID: {', '.join(missing)}")
    already = [i for i in add if i in have]
    if already:
        raise FreezeError(f"もう凍らせてある ID(写し直すなら --refresh): {', '.join(already)}")
    absent = [i for i in refresh if i not in have]
    if absent:
        raise FreezeError(f"凍らせていない ID(足すなら --add): {', '.join(absent)}")
    take = set(add) | set(refresh)
    models = [m for m in frozen["models"] if m["id"] not in take] + [by[i] for i in sorted(take)]
    sources = dict(frozen.get("sources", {}))
    later = list(sources.get("_frozen_later", []))
    later.append({"ids": sorted(take), "commit": commit, "generated_at": bundled.get("generated_at")})
    sources["_frozen_later"] = later
    return _with_hashes({**frozen, "sources": sources, "models": models}, sorted(take))


def rederive(frozen: dict[str, Any]) -> dict[str, Any]:
    """計算で決まる値(系統・VAE・一式がそろったか・アプリ向けの値)を、同梱の一覧の作り直しと同じ関数で
    当て直したもの(規則はここに写さない。update.refresh_heads と同じ順)。記録も作り直す。"""
    from genmodelfit import appinfo
    from genmodelfit.families import assign
    from genmodelfit.update import set_completeness

    db = parse_db({**frozen, "models": frozen["models"]}, "path")
    for m in db.models:
        set_completeness(m)
        assign(m)
    appinfo.apply_app_fields(db.models)
    return _with_hashes({**frozen, "models": [m.to_dict() for m in db.models]})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="試験の凍らせた一覧を作り直す(#197)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--add", nargs="+", default=[], metavar="ID", help="同梱の一覧から項目を足す")
    g.add_argument(
        "--refresh", nargs="+", default=[], metavar="ID", help="項目を同梱の一覧の今の中身で写し直す"
    )
    g.add_argument("--rederive", action="store_true", help="系統・アプリ向けの値などを今の規則で当て直す")
    g.add_argument("--rehash", action="store_true", help="項目の記録(sha256)を今の中身で作り直す")
    g.add_argument("--check", action="store_true", help="何も書かず、形か記録が合わなければ 1 で終わる")
    p.add_argument(
        "--frozen", type=Path, default=FROZEN, help="凍らせた一覧(既定 tests/fixtures/frozen/db.json)"
    )
    p.add_argument("--db", type=Path, default=BUNDLED, help="写す元の一覧(既定は同梱の一覧)")
    p.add_argument("--out", type=Path, help="書く先(既定は --frozen)")
    a = p.parse_args(argv)
    text = a.frozen.read_text(encoding="utf-8")
    frozen = json.loads(text)
    try:
        if a.check:
            bad = hash_problems(frozen)
            if bad:
                print(f"記録(sha256)と合わない項目: {', '.join(bad)}", file=sys.stderr)
                return 1
            if render(frozen) != text:
                msg = f"{a.frozen.name} は決まった形でない: python tools/freeze_test_list.py で書き直す"
                print(msg, file=sys.stderr)
                return 1
            return 0
        if a.rehash:
            frozen = _with_hashes(frozen)
        elif a.rederive:
            frozen = rederive(frozen)
        else:
            bad = hash_problems(frozen)
            if bad:  # 手の直しを黙って認めない
                raise FreezeError(
                    f"記録(sha256)と合わない項目: {', '.join(bad)}(認めるなら --rehash。書いていません)"
                )
            if a.add or a.refresh:
                frozen = update(frozen, _read(a.db), add=a.add, refresh=a.refresh, commit=_commit(a.db))
        out = render(frozen)
    except FreezeError as e:
        print(e, file=sys.stderr)
        return 1
    except DatabaseError as e:  # 一覧の形が読めない(SCHEMA_VERSION を上げた後など。frozenlist.py の先頭)
        print(f"凍らせた一覧を今の形で読めません: {e}", file=sys.stderr)
        return 1
    dst = a.out or a.frozen
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8", newline="\n")
    print(f"{len(frozen['models'])} 件、{dst.stat().st_size} バイト")
    return 0


if __name__ == "__main__":
    sys.exit(main())
