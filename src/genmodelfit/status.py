# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""一覧の新しさ(期限切れか)を調べる。`gmf.data_status()` と `genmodelfit system` の Data の欄。

自動のおすすめ(gate)は、URL の匿名 HEAD の確認が新しい(既定 30 日以内)ことを条件にしている。
確認が古くなると、ほかの条件を満たすモデルも url_reachable で保留になり、自動のおすすめが 0 件に向かう。
使う側(AI生成キット)は `expired` を見て、期限切れなら「新しいモデル」の案内をやめ、自分のカタログに戻る。

期限の判定は gate.evaluate_offline をそのまま使う(式を二重に持たない):
- auto_count: いまの時刻で自動のおすすめに出せる項目の数
- auto_count_if_fresh: HEAD の期限だけを外した(max_head_age_days=None)ときの数
- expired: auto_count < auto_count_if_fresh(HEAD の古さで落ちた項目が 1 つでもある)
- expires_at: 期限を外せば出せる項目のうち、一番早く期限が来る時刻(その項目の一番古い確認 + 期限の日数)。
  gate は「経過 > 期限の日数」で落とすので、expires_at ちょうどはまだ切れていない
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import gate as _gate
from .db import Database
from .gate import GatePolicy, _parse_time, evaluate_offline

SOURCES = ("bundled", "cache", "release", "path")


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


@dataclass(frozen=True)
class DataStatus:
    """一覧の出どころと新しさ。to_dict() は `system --json` の database.status と同じ形(docs/schema.md)。

    source: bundled(同梱)/ cache(手元の `genmodelfit update` が書いたキャッシュ)/
        release(`update --from-release` で取り込んだ配布)/ path(--db・db= で渡したファイル)
    generated_at: 一覧を作った日時(一覧に書かれたまま)
    heads_checked_oldest / heads_checked_newest: 一覧のすべてのファイルの HEAD の確認日時の最古・最新
    max_head_age_days: HEAD の確認を信じる日数(None は期限なし)
    expired: HEAD の確認が古いせいで自動のおすすめから落ちた項目がある(True なら update で取り直す)
    expires_at: 次に期限が切れる時刻(期限なし・対象なしは None)
    models: 一覧の項目の数(ワークフロー単位)
    auto_count: いま自動のおすすめに出せる項目の数(content は既定の方針どおり sfw だけ)
    auto_count_if_fresh: HEAD の期限を外したときの数(期限切れで減った分が分かる)
    checked_at: 判定に使った時刻
    """

    source: str
    generated_at: str | None
    heads_checked_oldest: str | None
    heads_checked_newest: str | None
    max_head_age_days: int | None
    expired: bool
    expires_at: str | None
    models: int
    auto_count: int
    auto_count_if_fresh: int
    checked_at: str

    @property
    def generated_time(self) -> datetime | None:
        """generated_at を UTC の datetime にしたもの(#96。`Database.generated_time` と同じ読み方)。
        無い・読めないときは None。to_dict() には入らない。"""
        from .db import parse_generated_at

        return parse_generated_at(self.generated_at)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _source(db: Database) -> str:
    """db.origin に、キャッシュが配布の取り込み(update --from-release)かどうかを足す。"""
    if db.origin != "cache":
        return db.origin if db.origin in SOURCES else "path"
    from .remote import _read_state

    accepted = _read_state()
    made = _parse_time(db.generated_at)
    return "release" if accepted is not None and made is not None and made == accepted else "cache"


def compute(
    db: Database,
    policy: GatePolicy | None = None,
    *,
    now: datetime | None = None,
    source: str | None = None,
) -> DataStatus:
    """db の新しさを policy(既定は GatePolicy())で調べる。ネットには触らない。"""
    p = policy or GatePolicy()
    now = now or _gate._now()  # gate と同じ時計(試験では差し替える)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    fresh_policy = dataclasses.replace(p, max_head_age_days=None)

    times = [t for m in db.models for f in m.files if (t := _parse_time(f.head_checked_at)) is not None]
    auto = fresh = 0
    expiries: list[datetime] = []
    for m in db.models:
        if not evaluate_offline(m, fresh_policy, sources=db.sources, now=now).auto:
            continue
        fresh += 1
        if evaluate_offline(m, p, sources=db.sources, now=now).auto:
            auto += 1
        if p.max_head_age_days is not None:
            own = [_parse_time(f.head_checked_at) for f in m.files]
            if own and all(t is not None for t in own):
                expiries.append(min(t for t in own if t is not None) + timedelta(days=p.max_head_age_days))
    return DataStatus(
        source=source or _source(db),
        generated_at=db.generated_at,
        heads_checked_oldest=_iso(min(times)) if times else None,
        heads_checked_newest=_iso(max(times)) if times else None,
        max_head_age_days=p.max_head_age_days,
        expired=auto < fresh,
        expires_at=_iso(min(expiries)) if expiries else None,
        models=len(db.models),
        auto_count=auto,
        auto_count_if_fresh=fresh,
        checked_at=_iso(now) or "",
    )
