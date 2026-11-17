# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""毎週入れ替わる同梱の一覧に左右されずに試すための共通の部品(#197)。

- 凍らせた一覧(tests/fixtures/frozen/db.json): 試験が名指しする項目だけを、ある版の同梱の一覧から写したもの。
  規則の試験はこれに今の規則を当てて確かめる(写した元は、そのファイルの sources の `_frozen_from` と
  `_frozen_later`)。毎週の更新で名指しの項目が同梱の一覧から消えても、同じ期待値で確かめられる。
- 手で確かめ済みの項目(hand_checked): 手の表(overrides.json など)の行を人が足したはずの項目。
  毎週の更新は db.json だけを変えるので、新しい項目(確認待ち・毎週の自動の承認)には手の表の行が無い。
- 全部を確認済みにした写し(all_ok): 「同梱の一覧 + 1 件の確認待ち」の形の試験の出発点。

凍らせた一覧の手入れ(項目は手で直さず、tools/freeze_test_list.py を使う):

- 足すとき: 試験が同梱の一覧の HF の項目を新しく名指しするとき、名前の決まりの表(groups.NAME_OVERRIDES)など
  ID で引く表に行を足して試験で確かめるとき。`python tools/freeze_test_list.py --add <id> …`(同梱の一覧に
  無い ID は失敗する)。名前の決まりの試験は、同じまとまりの仲間も足さないと名前が変わる。
- 写し直すとき: 試験が見る項目の中身(ファイル・ライセンスなど)を、わざと今の同梱の一覧に合わせるときだけ
  `--refresh <id> …`。引数なしは今の凍らせた項目をそのまま決まった形で書き直す(同梱の一覧の中身で
  置き換えない)。
- 項目ごとの sha256 を sources の `_frozen_sha256` に残す。項目を手で直すと `--check`
  (tests/test_frozenlist.py が流す)が落ち、書き直し(引数なし・`--add`・`--refresh`・`--rederive`)も
  書かずに失敗する。直しを認めるなら `--rehash` で記録を作り直す。記録は同じファイルの中にあるので、
  見つけるのはうっかりの直しだけ(項目と記録を一緒に直せば通る)。
- 系統の表(families)・アプリ向けの表(overrides.json)・一式がそろったかの規則を変えて
  tests/test_frozenlist.py の当て直しの検査が落ちたら、`--rederive` で凍らせた項目に今の規則を当て直す
  (同梱の一覧から消えた ID にも効く。記録も作り直す)。手のモデル(manual.json)の項目は当て直しでは
  変わらないので、manual.json を変えたら `--refresh <id>` で写し直す。
- 一覧の形を変える(models.SCHEMA_VERSION を上げる)PR は、同梱の一覧に当てたのと同じ変換を凍らせた一覧にも
  当て、`--rehash` で記録を作り直す。変換の道具は形が決まるまで作れないので、その PR で用意する。当てるまでは
  道具は「今の形で読めません」で終わり、凍らせた一覧を使う試験が全部落ちるので気づく。
- 同梱の一覧から消えた ID: 凍らせた項目をそのまま保つ(消さない。凍らせるのが目的)。
"""

from __future__ import annotations

import copy
import functools
import re
from pathlib import Path

from genmodelfit import review
from genmodelfit.db import Database, load
from genmodelfit.models import ModelEntry, Review

FROZEN_DB = Path(__file__).parent / "fixtures" / "frozen" / "db.json"

# 同梱の一覧の承認に使ってよい人の名前(review approve --by)。毎週の自動の承認(review.DECIDED_BY_WEEKLY)と
# claude-maker-<番号> は別に見る
HUMAN_APPROVERS = frozenset({"KoyoYeager"})
_MAKER = re.compile(r"claude-maker-\d+")


@functools.lru_cache(maxsize=1)
def _frozen() -> Database:
    # 一覧はファイルから読み(利用者のキャッシュの db.json を見ない)、実測は同梱のものだけ(この PC の bench の
    # 実測を混ぜない)。最初に呼んだ試験の GENMODELFIT_CACHE_DIR に、読んだ中身が左右されない
    return load(FROZEN_DB, user_measurements=False)


def frozen_db() -> Database:
    """凍らせた一覧(試験ごとの写し。書き換えても次の試験に残らない)。実測は同梱のものだけを合わせる。"""
    return copy.deepcopy(_frozen())


def hand_checked(m: ModelEntry) -> bool:
    """人か Claude の Maker が一次資料で確かめて承認した項目(確認待ちと、毎週の自動の承認は外す)。

    毎週の承認(claude-weekly)で入った項目は、この検査の対象にならない。手の表に行を足し、人の承認に変えるまで
    そのまま残る(知らせる仕組みはまだ無い)。"""
    r = m.review
    if r is None or r.status != "ok":
        return False
    who = r.decided_by or ""
    return who == review.BASELINE or who in HUMAN_APPROVERS or bool(_MAKER.fullmatch(who))


def all_ok(db: Database) -> Database:
    """db の写しで、確認待ちを全部確認済みにしたもの(sources の review の数も合わせる)。
    確認待ちの無い一覧なら、中身は元のまま。"""
    out = copy.deepcopy(db)
    for m in out.models:
        if not m.reviewed:
            reasons = list(m.review.reasons) if m.review else []
            m.review = Review("ok", reasons, "test-all-ok", "2026-10-21", [], None)
    rv = out.sources.get("review") if isinstance(out.sources.get("review"), dict) else {}
    out.sources["review"] = {**rv, "ok": len(out.models), "pending": 0}
    return out
