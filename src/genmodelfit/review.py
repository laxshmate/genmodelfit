# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""確認の印(ModelEntry.review)を付ける・確かめる・外す(#42)。

毎週の更新(Claude Code のスキル genmodelfit-weekly)で、新しいモデルやライセンスの変化を一覧に入れても、
人(またはスキル)が一次資料で確かめるまで自動のおすすめに出さないための印。

- update は前の一覧(引き継ぎの元)と比べて、次の項目を `pending` にする(理由は `reasons`):
  - `new`                 前の一覧に無い項目
  - `license_permissive`  ライセンスが寛容な側に変わった(段階が下がった・既定の許可リストに入るようになった・
                          gate の license が通るようになった)
  - `need_decreased`      見積もりの必要量が減った(REFERENCE_PCS のどれかで、段階が上がった・大きさが分かる
                          ようになった・全部載せの VRAM か RAM が 1% を超えて減った。精度・大きさ・
                          ファイルの変化による)
  - `uncensored_new`      新しく無検閲になった(新しい項目が無検閲のときも付ける)
  - `files_changed`       ファイルが変わった(前の一覧と同じ ID で、ファイルの増減・URL・
                          repo_id・大きさの変化(大きくなった・小さくなった・分かった・分からなくなった)・
                          sha256 の変化(前も今も値があって違う、または確かめた値が消えた。前が空で今入った
                          のは付けない)。アプリは一覧の URL をそのまま落とすので、確認済みの中身が黙って
                          差し替わらないようにする。URL はフラグメントだけ除いて比べる。0.9.0)
  それ以外は前の印を引き継ぐ(前の一覧に印が無ければ、確認済みとして引き継いだことを note に書く)。
- 外した項目(`review reject`)は data/review_rejected.txt に残し、update は作り直しても入れない。
- gate の `reviewed` が pending を自動のおすすめに出さない。

ライブラリとして何も表示しない(表示は cli.py)。ネットには触らない。
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .i18n import tr
from .models import REVIEW_REASONS, Measurement, ModelEntry, Review, SystemInfo

# 同梱の今の一覧に付けた印の decided_by(すべて ok で始めた。2026-10-21)
BASELINE = "baseline-2026-10-21"
# 外した項目のファイル(同梱のデータ。リポジトリでは src/genmodelfit/data/review_rejected.txt)
REJECTED_FILE = "review_rejected.txt"
# 必要量が「減った」とみなす割合(見積もりの丸めの揺れを拾わない)
NEED_DECREASE_RATIO = 0.01
# 必要量を比べる PC(VRAM GB, RAM GB)。0 は GPU なし(CPU で動かす判定)
REFERENCE_PCS: tuple[tuple[float, float], ...] = ((0, 16), (6, 16), (8, 16), (12, 32), (16, 32), (24, 64))
# 印が無い前の一覧から引き継いだときの note
LEGACY_NOTE = "前の一覧に確認の印が無い(0.6.0 より前)ため、確認済みとして引き継いだ"
_EVIDENCE_PREFIX = "https://"
_HASH_PREFIX = "sha256:"


class ReviewError(ValueError):
    """review の操作ができない(ID が無い・根拠が無い・確認待ちでない など)。文は利用者に見せる。"""


# --- 比べる -----------------------------------------------------------------------------------------

# 必要量に関わらない項目(違っても見積もりを比べ直さない)。ここに無い項目が変われば比べ直す(安全側)
_ENTRY_SKIP = frozenset(
    {
        "review",
        "name",
        "downloads",
        "likes",
        "downloads_all_time",
        "discovered_from",
        "updated",
        "homepage",
        "languages",
        "description",
        "card_languages",
        "license",
        "license_name",
        "license_url",
        "license_evidence",
        "content",
        "content_evidence",
        "base_model",
        "total_size_bytes",
    }
)
_FILE_SKIP = frozenset(
    {
        "url",
        "sha256",
        "head_status",
        "head_checked_at",
        "license",
        "license_name",
        "license_from",
        "license_url",
        "repo_license",
        "license_path",
        "license_evidence",
        "gated",
    }
)
_TEMPLATE_SKIP = frozenset({"title", "usage", "date", "username", "open_source"})


def _need_key(m: ModelEntry) -> dict[str, Any]:
    d = {k: v for k, v in asdict(m).items() if k not in _ENTRY_SKIP}
    d["files"] = [{k: v for k, v in f.items() if k not in _FILE_SKIP} for f in d.get("files") or []]
    if isinstance(d.get("template"), dict):
        d["template"] = {k: v for k, v in d["template"].items() if k not in _TEMPLATE_SKIP}
    return d


def _pc(vram: float, ram: float) -> SystemInfo:
    from .models import Gpu

    gpus = [Gpu(name="(review)", vendor="nvidia", vram_total_gb=vram, backend="cuda")] if vram else []
    return SystemInfo(os="-", cpu="-", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=gpus)


def need_decreased(old: ModelEntry, new: ModelEntry, measurements: Iterable[Measurement] = ()) -> bool:
    """old → new で見積もりの必要量が減ったか。REFERENCE_PCS のどれか 1 台で、段階が上がった・大きさが
    分からなかったのが分かるようになった、または全部載せの VRAM(vram_gb)かメインメモリ(ram_gb)が
    NEED_DECREASE_RATIO を超えて減った。逃がす・載せ替えのときの VRAM は比べない(部品の大きさの並びで
    上下し、効く変化は段階に出る)。必要量に関わる項目が同じなら見積もらない。"""
    if _need_key(old) == _need_key(new):
        return False
    from .estimate import estimate
    from .models import fit_rank

    ms = list(measurements)
    for vram, ram in REFERENCE_PCS:
        pc = _pc(vram, ram)
        a, b = estimate(old, pc, None, ms), estimate(new, pc, None, ms)
        if b.unknown_size:
            continue
        if a.unknown_size or fit_rank(b.fit) < fit_rank(a.fit):
            return True
        for va, vb in ((a.vram_gb, b.vram_gb), (a.ram_gb, b.ram_gb)):
            if va > 0 and vb < va * (1 - NEED_DECREASE_RATIO):
                return True
    return False


def _norm_url(url: Any) -> Any:
    """URL の比べ方: フラグメント(# から後。サーバーに送られない)だけ捨てる。クエリ(?download=true など)は
    落とすファイルを変えうるので比べる(0.9.0。diff の分類と同じ)。"""
    if not isinstance(url, str):
        return url
    return url.split("#", 1)[0]


def files_changed(old: ModelEntry, new: ModelEntry) -> bool:
    """old → new でファイルが変わったか: ファイルの増減、URL(フラグメントだけ除く)・repo_id・大きさの変化、
    sha256 の変化(前も今も値があって違う、または今が空になった。前が空で今入ったのは初めて確かめただけなので
    含めない)。ファイルの対応づけは diff と同じ(`diff._file_match`)。並びだけの変化・HEAD の結果は見ない。"""
    from .diff import _file_match

    fa, fb = asdict(old)["files"], asdict(new)["files"]
    pairs, gone, added = _file_match(fa, fb)
    if gone or added:
        return True
    for a, b in pairs:
        if _norm_url(a.get("url")) != _norm_url(b.get("url")) or a.get("repo_id") != b.get("repo_id"):
            return True
        if a.get("size_bytes") != b.get("size_bytes"):
            # 大きさが変わった(どちらの向きでも)= 同じ URL の中身が差し替わった。
            # 分かった・分からなくなったも付ける(0.9.0。前は大きくなっただけは付けなかった)
            return True
        if a.get("sha256") and a.get("sha256") != b.get("sha256"):
            return True
    return False


def license_permissive(old: ModelEntry, new: ModelEntry, allow: frozenset[str] | None = None) -> bool:
    """ライセンスが寛容な側に変わったか: 一式の段階(hf.license_tier)が下がった、一式のライセンスが許可リストに
    入るようになった、gate の license(ファイルごとに本家で読む)が通るようになった。"""
    from .gate import DEFAULT_LICENSE_ALLOWLIST, check_license, license_allowed
    from .hf import license_tier

    allow = DEFAULT_LICENSE_ALLOWLIST if allow is None else allow
    if license_tier(new.license, new.license_name) < license_tier(old.license, old.license_name):
        return True
    if license_allowed(new.license, new.license_name, allow) and not license_allowed(
        old.license, old.license_name, allow
    ):
        return True
    return check_license(new, allow).passed and not check_license(old, allow).passed


def review_reasons(
    old: ModelEntry | None,
    new: ModelEntry,
    measurements: Iterable[Measurement] = (),
    allow: frozenset[str] | None = None,
) -> list[str]:
    """old(前の一覧の同じ ID。無ければ None)→ new で、確認待ちにする理由(REVIEW_REASONS の順)。"""
    if old is None:
        return ["new"] + (["uncensored_new"] if new.content == "uncensored" else [])
    out = []
    if license_permissive(old, new, allow):
        out.append("license_permissive")
    if need_decreased(old, new, measurements):
        out.append("need_decreased")
    if old.content != "uncensored" and new.content == "uncensored":
        out.append("uncensored_new")
    if files_changed(old, new):
        out.append("files_changed")
    return out


def _ordered(reasons: Iterable[str]) -> list[str]:
    rs = set(reasons)
    return [r for r in REVIEW_REASONS if r in rs] + sorted(rs - set(REVIEW_REASONS))


def apply_reviews(
    entries: list[ModelEntry],
    previous: Iterable[ModelEntry] | None,
    measurements: Iterable[Measurement] = (),
) -> dict[str, Any]:
    """entries のすべてに review を付ける(前の一覧 previous と比べて。None は前の一覧が無い = 全部 new)。

    理由があれば pending(前も pending なら理由を足す)。理由が無ければ前の印を引き継ぐ(前の一覧に印が
    無ければ ok として引き継ぎ、LEGACY_NOTE を書く)。返す値は sources["review"] の集計。"""
    prev_by = {m.id: m for m in previous} if previous is not None else {}
    ms = list(measurements)
    marked: dict[str, int] = {}
    for e in entries:
        old = prev_by.get(e.id)
        reasons = review_reasons(old, e, ms)
        prev_review = copy.deepcopy(old.review) if old is not None else None
        if reasons:
            for r in reasons:
                marked[r] = marked.get(r, 0) + 1
            if prev_review is not None and prev_review.status != "ok":
                reasons = _ordered([*prev_review.reasons, *reasons])
                e.review = Review("pending", reasons, note=prev_review.note)
            else:
                e.review = Review("pending", _ordered(reasons))
        elif old is not None:
            e.review = prev_review if prev_review is not None else Review("ok", note=LEGACY_NOTE)
        # old が無く理由も無いことは無い(old が無ければ new)
    pending = sum(1 for e in entries if not e.reviewed)
    return {"ok": len(entries) - pending, "pending": pending, "marked": dict(sorted(marked.items()))}


# --- 外した項目(data/review_rejected.txt) ----------------------------------------------------------
#
# 1 行に 1 件: `<鍵>\t<YYYY-MM-DD>\t<理由>`。# から始まる行と空の行は読まない。鍵は ID そのものか、
# 名前を残さない外し方(--hide-name。既存の除外と同じく名前を残さない)のときは `sha256:<ID の sha256>`。


def id_hash(model_id: str) -> str:
    return _HASH_PREFIX + hashlib.sha256(model_id.encode("utf-8")).hexdigest()


def parse_rejected(text: str) -> dict[str, dict[str, str | None]]:
    """review_rejected.txt の中身 → {鍵: {date, note}}。"""
    out: dict[str, dict[str, str | None]] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        key = parts[0].strip()
        if key:
            out[key] = {
                "date": parts[1].strip() if len(parts) > 1 and parts[1].strip() else None,
                "note": parts[2].strip() if len(parts) > 2 and parts[2].strip() else None,
            }
    return out


def bundled_rejected() -> dict[str, dict[str, str | None]]:
    """同梱の review_rejected.txt(無ければ空)。"""
    from .db import bundled_text

    try:
        return parse_rejected(bundled_text(REJECTED_FILE))
    except (OSError, ValueError):
        return {}


def is_rejected(model_id: str, rejected: dict[str, Any]) -> bool:
    return model_id in rejected or id_hash(model_id) in rejected


def drop_rejected(
    entries: list[ModelEntry], rejected: dict[str, Any] | None = None
) -> tuple[list[ModelEntry], int]:
    """外した項目を除く。(残り, 除いた数)。"""
    rej = bundled_rejected() if rejected is None else rejected
    kept = [e for e in entries if not is_rejected(e.id, rej)]
    return kept, len(entries) - len(kept)


def finish(
    entries: list[ModelEntry],
    previous: Iterable[ModelEntry] | None,
    measurements: Iterable[Measurement] | None = None,
) -> tuple[list[ModelEntry], dict[str, Any]]:
    """update の最後に呼ぶ: 外した項目を除き、印を付ける。(残り, sources["review"] の集計)。"""
    if measurements is None:
        from .db import load_measurements

        measurements = load_measurements()
    kept, dropped = drop_rejected(entries)
    stats = apply_reviews(kept, previous, measurements)
    stats["rejected_skipped"] = dropped
    return kept, stats


# --- 一覧・確かめる・外す ------------------------------------------------------------------------


def review_list(db: Any, *, include_ok: bool = False) -> list[dict[str, Any]]:
    """確認待ち(include_ok なら全部)の項目: id・name・modality・content・source・license と review の中身。"""
    from .gate import _license_label

    out = []
    for m in db.models:
        if m.reviewed and not include_ok:
            continue
        r = m.review
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "modality": m.modality,
                "content": m.content,
                "source": m.source,
                "license": _license_label(m.license, m.license_name),
                "license_url": m.license_url,
                "homepage": m.homepage,
                "status": r.status if r else "ok",
                "reasons": list(r.reasons) if r else [],
                "decided_by": r.decided_by if r else None,
                "decided_at": r.decided_at if r else None,
                "evidence": list(r.evidence) if r else [],
                "note": r.note if r else None,
                "card_url": card_url(m),
                "files": [_file_info(f) for f in m.files],
            }
        )
    return out


def _file_info(f: Any) -> dict[str, Any]:
    """確認に使うファイルの情報(リポジトリ・パス・ライセンス・その出どころ)。値はそのまま(JSON のデータ)。"""
    from .hf import parse_hf_url

    parsed = parse_hf_url(f.url)
    return {
        "name": f.name,
        "repo": parsed[0] if parsed else f.repo_id,
        "path": parsed[2] if parsed else f.name,
        "url": f.url,
        "license": f.license,
        "license_name": f.license_name,
        "license_from": f.license_from,
        "license_url": f.license_url,
        "role": f.role,
    }


def card_url(m: Any) -> str | None:
    """モデルカードの URL: HF の項目はそのリポジトリ、ほかは本体のファイルのライセンスの出どころ・
    置き場所(無ければ homepage)。"""
    if m.id.startswith("hf:"):
        return f"https://huggingface.co/{m.id[3:]}"
    from .hf import parse_hf_url

    for f in sorted(m.files, key=lambda f: f.role != "main"):
        repo = f.license_from or (parse_hf_url(f.url) or (f.repo_id,))[0]
        if repo and "/" in repo and "://" not in repo:
            return f"https://huggingface.co/{repo}"
    return m.homepage


def _today(today: str | None) -> str:
    return today or datetime.now(UTC).date().isoformat()


def _https_url(text: str) -> bool:
    """https で、ホストがあり、空白を含まない URL か("https://" だけ・"https:///x" は断る)。"""
    from urllib.parse import urlsplit

    if any(c.isspace() for c in text) or not text.startswith(_EVIDENCE_PREFIX):
        return False
    try:
        u = urlsplit(text)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname)


def check_evidence(evidence: Iterable[str]) -> list[str]:
    ev = [e.strip() for e in evidence if e and e.strip()]
    if not ev:
        raise ReviewError(
            tr(
                "根拠(--evidence の URL。一次資料)が 1 つも無いため確かめられません",
                "cannot approve without evidence (--evidence URL of a primary source)",
            )
        )
    bad = [e for e in ev if not _https_url(e)]
    if bad:
        raise ReviewError(
            tr(
                f"根拠は https:// で始まり、ホストのある URL にしてください: {', '.join(bad)}",
                f"evidence must be URLs that start with https:// and have a host: {', '.join(bad)}",
            )
        )
    return ev


def approve(
    db: Any,
    model_id: str,
    evidence: Iterable[str],
    *,
    note: str | None = None,
    by: str = "review",
    today: str | None = None,
) -> ModelEntry:
    """確認待ちの項目を確認済みにする(db を書き換える。保存はしない)。理由は残す(何を確かめたか)。"""
    ev = check_evidence(evidence)
    m = db.get(model_id)
    if m is None:
        raise ReviewError(tr(f"一覧に無い ID です: {model_id}", f"no such id in the list: {model_id}"))
    if m.reviewed:
        raise ReviewError(tr(f"確認待ちではありません: {model_id}", f"not waiting for review: {model_id}"))
    if not by or not by.strip():
        raise ReviewError(tr("確かめた人(--by)が空です", "the reviewer (--by) is empty"))
    assert m.review is not None
    m.review = Review(
        "ok", list(m.review.reasons), by.strip(), _today(today), ev, (note or "").strip() or None
    )
    return m


def reject(
    db: Any,
    model_id: str,
    note: str,
    *,
    hide_name: bool = False,
    today: str | None = None,
) -> str:
    """項目を一覧から外す(db を書き換える。保存はしない)。返す値は review_rejected.txt に足す 1 行。"""
    if not note or not note.strip():
        raise ReviewError(tr("外す理由(--note)が空です", "the reason (--note) is empty"))
    m = db.get(model_id)
    if m is None:
        raise ReviewError(tr(f"一覧に無い ID です: {model_id}", f"no such id in the list: {model_id}"))
    db.models = [x for x in db.models if x.id != model_id]
    key = id_hash(model_id) if hide_name else model_id
    text = " ".join(note.split())
    return f"{key}\t{_today(today)}\t{text}\n"


def _load_file(db_path: str | Path):
    from .db import DatabaseError
    from .db import load as load_db

    try:
        return load_db(db_path)
    except DatabaseError as e:
        raise ReviewError(str(e)) from e


def _save(db: Any, path: Path) -> None:
    """一覧を書き戻す(db.json と同じ形。sources の review の数も数え直す)。"""
    pending = sum(1 for m in db.models if not m.reviewed)
    rv = db.sources.get("review") if isinstance(db.sources.get("review"), dict) else {}
    db.sources["review"] = {**rv, "ok": len(db.models) - pending, "pending": pending}
    db.save(path)


def approve_file(
    db_path: str | Path,
    model_id: str,
    evidence: Iterable[str],
    *,
    note: str | None = None,
    by: str = "review",
    today: str | None = None,
) -> ModelEntry:
    """`review approve`: db_path の一覧を読み、確認済みにして書き戻す。"""
    p = Path(db_path)
    db = _load_file(p)
    m = approve(db, model_id, evidence, note=note, by=by, today=today)
    _save(db, p)
    return m


def default_rejected_path(db_path: str | Path) -> Path:
    """外した項目のファイルの既定: 一覧と同じフォルダの review_rejected.txt
    (`--db src/genmodelfit/data/db.json` なら同梱のもの)。"""
    return Path(db_path).parent / REJECTED_FILE


def reject_file(
    db_path: str | Path,
    model_id: str,
    note: str,
    *,
    rejected_path: str | Path | None = None,
    hide_name: bool = False,
    today: str | None = None,
) -> dict[str, Any]:
    """`review reject`: db_path の一覧から外して書き戻し、review_rejected.txt に 1 行足す。"""
    p = Path(db_path)
    db = _load_file(p)
    line = reject(db, model_id, note, hide_name=hide_name, today=today)
    rp = Path(rejected_path) if rejected_path else default_rejected_path(p)
    old = rp.read_text(encoding="utf-8") if rp.is_file() else REJECTED_HEADER
    if old and not old.endswith("\n"):
        old += "\n"
    db.sources["counts"] = _counts(db)
    _save(db, p)
    rp.write_text(old + line, encoding="utf-8", newline="\n")
    key, date, text = line.rstrip("\n").split("\t", 2)
    return {"key": key, "date": date, "note": text, "rejected_file": str(rp), "db": str(p)}


def _counts(db: Any) -> dict[str, Any]:
    from .update import count_entries

    return count_entries(db.models)


REJECTED_HEADER = """\
# genmodelfit review reject で一覧から外した項目(#42)。update は作り直してもこれらを入れない。
# 1 行に 1 件: <ID か sha256:<ID の sha256>>\\t<YYYY-MM-DD>\\t<理由>
# 名前を残さない外し方(review reject --hide-name)は sha256: の鍵だけを残す
"""


def publishable(db: Any) -> Any:
    """署名して配る一覧(data-latest、data.yml)。確認待ちの項目を除いた写し(元の db は変えない)。

    0.5.x までの genmodelfit は review を知らない(知らない鍵として捨てる)ので、確認待ちの項目を配ると、
    ほかの条件を満たすものは古い版で自動のおすすめに出てしまう。そこで配る一覧には確認済みの項目だけを入れる
    (新しい版でも、確認待ちの項目は確かめて main に入るまで data-latest に出ない)。同梱の一覧(main の db.json)
    には確認待ちの項目も残る(新しい版は gate の reviewed で保留にする)。"""
    out = copy.deepcopy(db)
    kept = [m for m in out.models if m.reviewed]
    withheld = len(out.models) - len(kept)
    out.models = kept
    rv = out.sources.get("review") if isinstance(out.sources.get("review"), dict) else {}
    out.sources["review"] = {**rv, "ok": len(kept), "pending": 0, "withheld_pending": withheld}
    out.sources["counts"] = _counts(out)
    return out


# --- 判断の一覧を当てる(`python -m genmodelfit.review apply`。毎週の実行役が Claude の答えを当てる) -------
#
# 2026-10-21 のオーナーの決定: 毎週の仕事で Claude はファイルを書き換えず、コマンドも動かさない。読むだけで、
# 確認待ちの項目ごとの判断(approve / reject / hold)を JSON で返す。実行役のスクリプトがそれをここで当てる。
# 形を厳しく確かめ、1 つでもおかしければ何も当てない(全部か無しか)。

DECISIONS_FILE_KEYS = frozenset({"decisions"})
DECISION_KEYS = frozenset({"id", "action", "evidence", "note", "safety"})
DECISION_ACTIONS = ("approve", "reject", "hold")
DECIDED_BY_WEEKLY = "claude-weekly"
NOTE_MAX_CHARS = 500
DEFAULT_MAX_APPROVE = 10


class DecisionError(ReviewError):
    """判断の一覧の形・中身が正しくない(何も当てていない)。終了コード 2。"""


def _bad_text(text: str) -> bool:
    """制御文字(改行・タブも)と、書式の文字(向きの指定・幅の無い文字など)を含むか。"""
    import unicodedata

    return any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn") for c in text)


def validate_decisions(data: Any, db: Any, *, max_approve: int = DEFAULT_MAX_APPROVE) -> list[dict[str, Any]]:
    """判断の一覧を確かめ、正規化した並びを返す。おかしければ DecisionError(理由をすべて並べる)。

    - 形: {"decisions": [{"id", "action", "evidence"?, "note"?, "safety"?}]}。知らない鍵は断る
    - id は文字列で、いまの一覧で確認待ちの項目(同じ id を 2 回は断る)
    - action は approve / reject / hold。approve は根拠(https でホストのある URL)が 1 つ以上要り、
      safety は付けない。reject は note が要る
    - evidence は https でホストのある URL の並び。note は 500 文字まで・制御文字なし。safety は真偽
    - approve は max_approve 件まで
    """
    errs: list[str] = []
    shape_ok = isinstance(data, dict) and set(data) == DECISIONS_FILE_KEYS
    if not (shape_ok and isinstance(data["decisions"], list)):
        raise DecisionError(
            tr(
                '判断の一覧は {"decisions": [...]} の形にしてください(ほかの鍵は持たない)',
                'the decisions must be {"decisions": [...]} (no other keys)',
            )
        )
    pending = {m.id for m in db.models if not m.reviewed}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k, d in enumerate(data["decisions"]):
        where = f"decisions[{k}]"
        if not isinstance(d, dict):
            errs.append(tr(f"{where} はオブジェクトではない", f"{where} is not an object"))
            continue
        unknown = sorted(set(d) - DECISION_KEYS)
        if unknown:
            errs.append(
                tr(
                    f"{where} に知らない鍵: {', '.join(map(repr, unknown))}",
                    f"{where} has unknown keys: {', '.join(map(repr, unknown))}",
                )
            )
        mid, action = d.get("id"), d.get("action")
        if not isinstance(mid, str) or not mid:
            errs.append(tr(f"{where}.id は空でない文字列", f"{where}.id must be a non-empty string"))
            continue
        if mid in seen:
            errs.append(tr(f"{where}.id {mid!r} が 2 回ある", f"{where}.id {mid!r} appears twice"))
        seen.add(mid)
        if mid not in pending:
            errs.append(
                tr(
                    f"{where}.id {mid!r} は確認待ちの項目ではない",
                    f"{where}.id {mid!r} is not waiting for review",
                )
            )
        if action not in DECISION_ACTIONS:
            errs.append(
                tr(
                    f"{where}.action は {' / '.join(DECISION_ACTIONS)}: {action!r}",
                    f"{where}.action must be {' / '.join(DECISION_ACTIONS)}: {action!r}",
                )
            )
        ev = d.get("evidence", [])
        if not isinstance(ev, list) or not all(isinstance(e, str) for e in ev):
            errs.append(tr(f"{where}.evidence は文字列の並び", f"{where}.evidence must be a list of strings"))
            ev = []
        bad_ev = [e for e in ev if not _https_url(e)]
        if bad_ev:
            errs.append(
                tr(
                    f"{where}.evidence は https でホストのある URL だけ: {bad_ev!r}",
                    f"{where}.evidence must be https URLs with a host: {bad_ev!r}",
                )
            )
        note = d.get("note")
        if note is not None and not isinstance(note, str):
            errs.append(tr(f"{where}.note は文字列", f"{where}.note must be a string"))
            note = None
        if note is not None and (len(note) > NOTE_MAX_CHARS or _bad_text(note)):
            errs.append(
                tr(
                    f"{where}.note は {NOTE_MAX_CHARS} 文字まで・制御文字なし",
                    f"{where}.note must be up to {NOTE_MAX_CHARS} characters, no control characters",
                )
            )
        safety = d.get("safety", False)
        if not isinstance(safety, bool):
            errs.append(tr(f"{where}.safety は真偽", f"{where}.safety must be a boolean"))
            safety = False
        if action == "approve":
            if not ev:
                errs.append(
                    tr(
                        f"{where}: approve には根拠(evidence)が 1 つ以上要る",
                        f"{where}: approve needs at least one evidence URL",
                    )
                )
            if safety:
                errs.append(
                    tr(f"{where}: approve に safety は付けない", f"{where}: approve cannot have safety")
                )
        if action == "reject" and not (note and note.strip()):
            errs.append(
                tr(f"{where}: reject には note(理由)が要る", f"{where}: reject needs a note (reason)")
            )
        out.append({"id": mid, "action": action, "evidence": list(ev), "note": note, "safety": safety})
    approvals = sum(1 for d in out if d["action"] == "approve")
    if approvals > max_approve:
        errs.append(
            tr(
                f"approve が {approvals} 件ある({max_approve} 件まで。--max-approve)",
                f"{approvals} approvals (up to {max_approve}; --max-approve)",
            )
        )
    if errs:
        raise DecisionError(
            tr(
                "判断の一覧を当てませんでした(何も変えていない): ",
                "decisions not applied (nothing changed): ",
            )
            + " / ".join(errs)
        )
    return out


def apply_decisions(
    db_path: str | Path,
    decisions: Any,
    *,
    max_approve: int = DEFAULT_MAX_APPROVE,
    rejected_path: str | Path | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """判断の一覧(JSON を読んだもの)を db_path の一覧に当てる。全部確かめてから当てる(DecisionError なら
    何も書かない)。approve は decided_by "claude-weekly"、reject は safety なら名前を残さない(--hide-name)。

    返す値は PR の本文・実行役に渡す要約。文字はすべて weekly.md で無害にしてある(名前を残さない reject は
    ID を出さない)。"""
    from .weekly import md

    p = Path(db_path)
    db = _load_file(p)
    items = validate_decisions(decisions, db, max_approve=max_approve)
    lines: list[str] = []
    results: list[dict[str, Any]] = []
    for d in items:
        shown_id = md(d["id"])
        if d["action"] == "approve":
            approve(db, d["id"], d["evidence"], note=d["note"], by=DECIDED_BY_WEEKLY, today=today)
            result = "approved"
        elif d["action"] == "reject":
            lines.append(reject(db, d["id"], d["note"] or "", hide_name=d["safety"], today=today))
            result = "rejected"
            if d["safety"]:
                shown_id = id_hash(d["id"])[:15] + "…"
        else:
            result = "held"
        results.append(
            {
                "id": shown_id,
                "action": d["action"],
                "result": result,
                "safety": d["safety"],
                "evidence": [md(e) for e in d["evidence"]],
                "note": None if d["safety"] or d["note"] is None else md(d["note"]),
            }
        )
    rp = Path(rejected_path) if rejected_path else default_rejected_path(p)
    if lines:
        db.sources["counts"] = _counts(db)
    _save(db, p)
    if lines:
        old = rp.read_text(encoding="utf-8") if rp.is_file() else REJECTED_HEADER
        if old and not old.endswith("\n"):
            old += "\n"
        rp.write_text(old + "".join(lines), encoding="utf-8", newline="\n")
    counts = {a: sum(1 for d in items if d["action"] == a) for a in DECISION_ACTIONS}
    return {
        "applied": True,
        "db": str(p),
        "rejected_file": str(rp) if lines else None,
        "counts": counts,
        "pending_left": sum(1 for m in db.models if not m.reviewed),
        "results": results,
    }


def _utf8_stdio() -> None:
    """Windows の cp932 / cp1252 でも日本語の出力で落ちず、呼ぶ側が UTF-8 で読めるようにする(cli と同じ)。"""
    import contextlib
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """`python -m genmodelfit.review publish <IN> <OUT>`: 配る一覧を作る(data.yml が署名の前に呼ぶ)。

    `python -m genmodelfit.review apply <DECISIONS.json> --db PATH [--max-approve 10] [--rejected FILE]
    [--json]`: 判断の一覧を当てる。終了コード 0 = 当てた、2 = 判断の一覧がおかしい(何も変えていない)、
    1 = 一覧を読めない。"""
    _utf8_stdio()
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m genmodelfit.review", description=tr("確認の印の道具(#42)", "Review mark tools (#42)")
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    pub = sub.add_parser(
        "publish",
        help=tr(
            "確認待ちの項目を除いた、署名して配る一覧を書く",
            "write the list to sign and publish, without pending items",
        ),
    )
    pub.add_argument(
        "src",
        help=tr(
            "元の一覧(main の src/genmodelfit/data/db.json)",
            "the source list (src/genmodelfit/data/db.json on main)",
        ),
    )
    pub.add_argument("dst", help=tr("書き出す先(同じパスでもよい)", "where to write (may be the same path)"))
    ap = sub.add_parser(
        "apply",
        help=tr(
            "確認待ちの項目への判断(approve / reject / hold)の JSON を当てる",
            "apply a JSON of decisions (approve / reject / hold) on pending items",
        ),
    )
    ap.add_argument(
        "decisions",
        help=tr('判断の一覧(JSON。{"decisions": [...]})', 'the decisions (JSON: {"decisions": [...]})'),
    )
    ap.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help=tr(
            "書き換える一覧(例 src/genmodelfit/data/db.json)",
            "the list to rewrite (e.g. src/genmodelfit/data/db.json)",
        ),
    )
    ap.add_argument(
        "--max-approve",
        type=int,
        default=DEFAULT_MAX_APPROVE,
        help=tr("approve の上限(既定 10)", "maximum approvals (default 10)"),
    )
    ap.add_argument(
        "--rejected",
        metavar="PATH",
        help=tr("外した記録(既定は --db と同じフォルダ)", "the rejected file (default: the folder of --db)"),
    )
    ap.add_argument("--json", action="store_true", help=tr("要約を JSON で出す", "print the summary as JSON"))
    args = p.parse_args(argv)
    if args.cmd == "apply":
        return _apply_main(args)
    try:
        db = _load_file(args.src)
    except ReviewError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = publishable(db)
    out.save(Path(args.dst))
    withheld = out.sources["review"]["withheld_pending"]
    print(
        tr(
            f"{len(out.models)} 件を書きました(確認待ち {withheld} 件を除いた): {args.dst}",
            f"wrote {len(out.models)} entries ({withheld} pending left out): {args.dst}",
        )
    )
    return 0


def _apply_main(args: Any) -> int:
    import sys

    def fail(msg: str, code: int) -> int:
        if args.json:
            sys.stdout.write(json.dumps({"applied": False, "error": msg}, ensure_ascii=False) + "\n")
        else:
            print(f"error: {msg}", file=sys.stderr)
        return code

    try:
        data = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return fail(tr(f"判断の一覧を読めません: {e}", f"cannot read the decisions: {e}"), 2)
    try:
        res = apply_decisions(args.db, data, max_approve=args.max_approve, rejected_path=args.rejected)
    except DecisionError as e:
        return fail(str(e), 2)
    except ReviewError as e:
        return fail(str(e), 1)
    if args.json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    else:
        c = res["counts"]
        left = res["pending_left"]
        print(
            f"approve {c['approve']} / reject {c['reject']} / hold {c['hold']}"
            + tr(f"(残りの確認待ち {left})", f" ({left} still pending)")
        )
        for r in res["results"]:
            print(f"  {r['result']}: {r['id']}")
    return 0


def queue(db: Any, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    """毎週の review_queue.json の中身。"""
    from ._version import __version__

    return {
        "genmodelfit": __version__,
        "generated_at": db.generated_at,
        "pending": review_list(db),
        "classification": classification,
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
