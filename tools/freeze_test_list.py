# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""試験の凍らせた一覧(tests/fixtures/frozen/db.json)を作り直す・項目を足す・写し直す(#197)。

    python tools/freeze_test_list.py                       # 今の項目をそのまま、決まった形で書き直す
    python tools/freeze_test_list.py --add <id> [<id> …]    # 同梱の一覧から項目を足す
    python tools/freeze_test_list.py --refresh <id> [<id> …]  # その項目だけ同梱の一覧の今の中身で写し直す
    python tools/freeze_test_list.py --check               # 何も書かず、決まった形でなければ 1 で終わる

手入れの手順(いつ足すか・SCHEMA_VERSION を上げたとき・同梱の一覧から消えた項目)は tests/frozenlist.py の
先頭にある。項目は同梱の一覧の JSON をそのまま写し、引数なしでは今の凍らせた項目を動かさない(同梱の一覧の
中身で置き換えない)。並びは ID の順、sources の counts と review は写した項目で数え直す。
ネットワークには触らない。tests/test_frozenlist.py が --check と --add を確かめる。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genmodelfit.db import parse_db  # noqa: E402
from genmodelfit.update import count_entries  # noqa: E402

FROZEN = ROOT / "tests" / "fixtures" / "frozen" / "db.json"
BUNDLED = ROOT / "src" / "genmodelfit" / "data" / "db.json"

NOTE = (
    "試験が名指しする項目だけを同梱の一覧から写した凍らせた一覧(#197)。毎週の更新で同梱の一覧の"
    "顔ぶれが変わっても、規則の試験が同じ入力で確かめられるようにする。項目は手で直さず、"
    "tools/freeze_test_list.py で足す(--add)・写し直す(--refresh)。"
    "手入れの手順は tests/frozenlist.py の先頭。"
    "_frozen_from は最初に写した元(同梱の一覧の db.json を最後に変えたコミットと generated_at)、"
    "_frozen_later はその後に足した・写し直した項目の元。"
    "ほかの sources は最初に写した元のまま(counts・review だけ写した項目で数え直す)"
)


class FreezeError(Exception):
    """凍らせた一覧を作れない(足す項目が同梱の一覧に無い・一覧の形の版が違う、など)。"""


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


def render(frozen: dict[str, Any]) -> str:
    """凍らせた一覧の決まった形(ID の順・数え直した sources・indent 1・末尾の改行)。読めない形なら
    DatabaseError(parse_db と同じ)。"""
    models = sorted(frozen["models"], key=lambda m: m["id"])
    db = parse_db({**frozen, "models": models}, "path")  # 形の確かめ(読めない項目は写さない)
    src = {k: v for k, v in frozen.get("sources", {}).items() if k not in ("_frozen", "counts", "review")}
    old_review = frozen.get("sources", {}).get("review") or {}
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
    """frozen に、bundled(同梱の一覧の JSON)から add の項目を足し、refresh の項目を写し直したもの。"""
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
    return {**frozen, "sources": sources, "models": models}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="試験の凍らせた一覧を作り直す(#197)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--add", nargs="+", default=[], metavar="ID", help="同梱の一覧から項目を足す")
    g.add_argument(
        "--refresh", nargs="+", default=[], metavar="ID", help="項目を同梱の一覧の今の中身で写し直す"
    )
    g.add_argument("--check", action="store_true", help="何も書かず、決まった形でなければ 1 で終わる")
    p.add_argument(
        "--frozen", type=Path, default=FROZEN, help="凍らせた一覧(既定 tests/fixtures/frozen/db.json)"
    )
    p.add_argument("--db", type=Path, default=BUNDLED, help="写す元の一覧(既定は同梱の一覧)")
    p.add_argument("--out", type=Path, help="書く先(既定は --frozen)")
    a = p.parse_args(argv)
    text = a.frozen.read_text(encoding="utf-8")
    frozen = json.loads(text)
    if a.check:
        if render(frozen) != text:
            msg = f"{a.frozen.name} は決まった形でない: python tools/freeze_test_list.py で書き直す"
            print(msg, file=sys.stderr)
            return 1
        return 0
    try:
        if a.add or a.refresh:
            frozen = update(frozen, _read(a.db), add=a.add, refresh=a.refresh, commit=_commit(a.db))
        out = render(frozen)
    except FreezeError as e:
        print(e, file=sys.stderr)
        return 1
    dst = a.out or a.frozen
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8", newline="\n")
    print(f"{len(frozen['models'])} 件、{dst.stat().st_size} バイト")
    return 0


if __name__ == "__main__":
    sys.exit(main())
