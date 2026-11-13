# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""テンプレートの既定の値で、どのスイッチがどちらの枝を通すかを出す(式を当てる前の確かめ。
docs/accuracy.md「系統の当て方」)。

    python tools/template_branches.py utility_seedvr2_3b_int8_upscale_video   # 公式テンプレートを取ってくる
    python tools/template_branches.py path/to/workflow.json                    # 手元の JSON

テンプレートに枝(コマを分けるノードなど)があっても、既定で通るかはスイッチの値(サブグラフの外の入力欄・
Primitive の値を含む)で決まる。スイッチごとに既定の値と、値を逆にすると実行し始める(+)・しなくなる(-)
ノードを出す。たどり方は genmodelfit.optional(ComfyUI の実行と同じ向き)と同じ。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genmodelfit.fetch import make_session  # noqa: E402
from genmodelfit.optional import SwitchBranch, switch_branches  # noqa: E402
from genmodelfit.update import RAW  # noqa: E402


def load(arg: str) -> dict:
    p = Path(arg)
    if p.suffix == ".json" and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    r = make_session().get(f"{RAW}/{arg}.json", timeout=30)
    r.raise_for_status()
    return r.json()


def render(branches: list[SwitchBranch]) -> str:
    if not branches:
        return "既定の値で実行するスイッチはありません(または分けられない形です)。"
    lines = []
    for b in branches:
        where = "/".join(str(x) for x in b.path)
        lines.append(f"{b.type} [{where}] {b.title or ''} = {b.value}")
        for sign, items in (("+", b.starts), ("-", b.stops)):
            for path, typ in items:
                lines.append(f"  {sign} {typ} [{'/'.join(str(x) for x in path)}]")
        if not b.starts and not b.stops:
            lines.append("  (逆にしても実行するノードは変わらない)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("template", help="公式テンプレートの名前(.json なし)か、手元の JSON のパス")
    args = ap.parse_args(argv)
    print(render(switch_branches(load(args.template))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
