# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""毎週の一覧の作り直しと、自動 PR の本文を作る(#19)。

.github/workflows/weekly-data.yml がこれを動かし、手元でも同じ流れを試せる:

    python -m genmodelfit.weekly --dry-run --out <dir>            # 上流から作り直す(書くのは <dir> だけ)
    python -m genmodelfit.weekly --out <dir> --apply src/genmodelfit/data/db.json   # ワークフローと同じ
    python -m genmodelfit.weekly --dry-run --out <dir> --record <rec>   # 上流の応答を <rec> に残す
    python -m genmodelfit.weekly --dry-run --out <dir> --offline-sources <rec> # 残した応答だけで(通信しない)

流れ:
1. 比べる元(--base。既定は同梱の一覧 = main の db.json)を読む
2. `genmodelfit update` と同じ作り直し(テンプレート・HF・HEAD・ファイルの見出しと精度。見出しと精度は
   キャッシュ(GENMODELFIT_CACHE_DIR)にあるものは読み直さない)。引き継ぎの元は 1 の一覧。続けて
   `update --inspect-only` と同じ読み直し(401 だったリポジトリの確かめ直し・テンプレートのサンプラーの組)。
   同梱の一覧もこの 2 段で作るので、毎週の差分に段の違いが出ない
3. 確かめる: 形(db.load で読み直す)、自動のおすすめが 0 件でない、区分ごとに元の半分未満に減っていない、
   新しく無検閲になった項目が MAX_NEW_UNCENSORED 件 × 週の数まで(週の数は、元の一覧を作り直した日
   (generated_at)からの週を四捨五入したもの。通らなかった週の分がたまっても、次の週に通れるように。#197)、
   **HEAD の期限(30 日)が 7 日より先**(作り直した直後なのに近ければ、HEAD の取り直しが効いていない)
4. 一部を取得できなかったとき(sources.partial): 既定は失敗(PR を作らない)。--allow-partial のときだけ、
   中身が筋の通るとき(下の partial_problems が空)に限って「一部欠け」の警告を付けて通す
5. 元との違い(diff.diff_db)と PR の本文を <dir> に書く: db.json・diff.json・pr_body.md・result.json・
   review_queue.json(確認待ちの項目と、diff の分類 classification。#42。毎週のスキルがこれを読んで、
   安全な向きだけの週か、根拠付きの PR をオーナーに出す週かを決める)
6. --apply があれば、確かめを通った db.json をそこへ写す(ワークフローはこのあと pytest を回す)
7. GPU の表の見張り(gpuwatch。#103): 公式の GPU の一覧を取り、表に無い・表と違う候補と読めなかった出典を
   PR の本文の「GPU 表の見張り」と <dir>/gpu_watch.json に書く。表は書き換えず、結果(ok)にも効かない
   (見つかった件は別の Issue にする。データの自動マージは止めない)。--no-gpu-watch で飛ばす

終了コード: 0 = 通った、1 = 確かめに落ちた・作り直しをやめた(<dir>/result.json の problems に理由)、
2 = 使い方の誤り。

    python -m genmodelfit.weekly merge-check --base <main の db.json> --head <新しい db.json> [--json]

は、2 つのファイルから分類(diff の classification)を計算し直す(#42。毎週のスキルの実行役が、マージしてよいかの
機械の確かめに使う)。終了コード: 0 = 安全な向きだけ、3 = 判断が要る、1 = 読めない、2 = 使い方の誤り。
公開(署名と data-latest)はここではしない。PR が main に入ったときに data.yml が行う。
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import math
import os
import re
import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from . import i18n
from ._version import __version__
from .db import Database, DatabaseError, duplicate_ids
from .db import load as load_db
from .diff import diff_db
from .fetch import DirFetcher, Fetcher, RecordingFetcher
from .gate import GatePolicy, _parse_time
from .review import queue as review_queue
from .review import review_list
from .status import compute as compute_status
from .update import LICENSE_POLICY, UpdateFailed, inspect_database, make_fetcher, update_database

# 作り直した直後の HEAD の期限が、これより近ければ失敗にする(取り直しが効いていない)
EXPIRY_MARGIN_DAYS = 7
# 項目がこの割合より少なくなったら失敗にする(上流の形が変わった・検索が止まった疑い)
MIN_KEEP_RATIO = 0.5
# PR の本文の 1 つの表に載せる行の上限(残りは diff.json)。本文は GitHub の上限(65536 文字)より短くする
MAX_ROWS = 40
MAX_BODY_CHARS = 60_000
# 1 週あたりに新しく無検閲の印が付いてよい数。元の一覧を作り直してからの週の数を掛けたものを超えたら、
# 定期の実行は失敗(手動の実行は先頭に警告。#19)。週の数を掛けるのは、通らなかった週の分がたまって、
# 次の週からも通らなくなるのを防ぐため(#197)
MAX_NEW_UNCENSORED = 5
# 週の数の上限(8 週 = 40 件)。上限はこれより増えない。超えたら今までどおり失敗して人が見る
MAX_UNCENSORED_WEEKS = 8
# 減り方を見る区分の、元の件数の下限(少ない区分は 1 件の増減で半分を切るので見ない)
SHRINK_MIN_BASE = 4
HEADS_FILE = "heads.json"


class WeeklyRecorder(RecordingFetcher):
    """本物の取得をしながら、GET の応答と HEAD の応答コードを --offline-sources で読める形で残す。"""

    def __init__(self, inner: Fetcher, root: str | Path):
        super().__init__(inner, root)
        self.statuses: dict[str, int] = {}

    def head_status(self, url: str) -> int | None:
        status = self.inner.head_status(url)
        if status is not None:
            self.statuses[url] = status
        return status

    def save_heads(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / HEADS_FILE).write_text(
            json.dumps(self.statuses, indent=1, sort_keys=True), encoding="utf-8", newline="\n"
        )

    def __getattr__(self, name: str) -> Any:  # get_range・budget_exhausted・rate_limited などは中身のもの
        return getattr(self.inner, name)


@contextlib.contextmanager
def _japanese():
    """表示の言語を ja に固定する(#73)。毎週の PR の本文・結果はオーナー(日本語)向けなので、
    diff・review・update が返す文も今までどおり日本語にする。終わったら前の状態に戻す。"""
    with i18n.forced("ja"):
        yield


def replay_fetcher(root: str | Path) -> DirFetcher:
    """--record で残したフォルダを上流の代わりに読む(通信しない)。HEAD の応答コードは heads.json から。"""
    p = Path(root) / HEADS_FILE
    statuses: dict[str, int] = {}
    if p.is_file():
        statuses = {str(k): int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    return DirFetcher(root, statuses=statuses)


def bundled_db_path() -> Path:
    """同梱の一覧のファイル(開発の checkout では src/genmodelfit/data/db.json = main の一覧)。"""
    return Path(str(resources.files("genmodelfit.data").joinpath("db.json")))


def partial_problems(new: Database, old: Database) -> list[str]:
    """一部が欠けた(sources.partial)一覧を、--allow-partial で PR にしてよいか。だめな理由を返す。

    よいのは、欠けたのが HF のリポジトリの一時的な失敗だけで、その項目を前の一覧から引き継げたときだけ:
    - 429 の待ち時間の上限に達していない(残りの取得が全部抜けている)
    - HF の検索が失敗していない(見つかるはずのモデルが「消えた」に見える)
    - テンプレートの JSON が読めないものが無い(テンプレートの項目が古いまま・消えたに見える)
    - 影響を受けた項目があるなら、前の一覧が同じ決め方(license_policy)で作られている(引き継げている)
    """
    if not new.sources.get("partial"):
        return []
    pc = new.sources.get("partial_counts") or {}
    out = []
    if pc.get("rate_limit_budget_exhausted"):
        out.append("429 の待ち時間の上限に達しました(残りの取得が抜けています)")
    if pc.get("hf_search_failed"):
        out.append(
            f"Hugging Face の検索が {pc['hf_search_failed']} 件失敗しました(消えたように見える項目が出ます)"
        )
    if pc.get("templates_failed"):
        out.append(f"テンプレートの JSON が {pc['templates_failed']} 件読めませんでした")
    if pc.get("entries_affected") and old.sources.get("license_policy") != LICENSE_POLICY:
        out.append(
            "影響を受けた項目を前の一覧から引き継げません(前の一覧の license_policy "
            f"{old.sources.get('license_policy')} が今の {LICENSE_POLICY} と違う)"
        )
    return out


def check_new(new: Database, old: Database, *, now: datetime) -> tuple[list[str], dict[str, Any]]:
    """作り直した一覧を確かめる。(問題の一覧, 一覧の新しさ DataStatus の dict)。"""
    problems: list[str] = []
    st = compute_status(new, GatePolicy(), now=now, source="path")
    if st.auto_count == 0:
        problems.append("自動のおすすめが 0 件です(HEAD・ライセンス・ComfyUI の版の記録を確かめてください)")
    expires = _parse_time(st.expires_at)
    if st.expired:
        problems.append("作り直した直後なのに HEAD の確認が期限切れの項目があります")
    if expires is None or expires < now + timedelta(days=EXPIRY_MARGIN_DAYS):
        problems.append(
            f"HEAD の期限が近すぎます(expires_at={st.expires_at}、{EXPIRY_MARGIN_DAYS} 日より先のはず)。"
            "HEAD の取り直しが効いていません"
        )
    return problems, st.to_dict()


def shrink_problems(old: Database, new: Database, d: dict[str, Any]) -> list[str]:
    """出どころ・内容ごとに、元の半分未満に減っていないか(全体の数だけでは、HF の検索が止まって
    HF の項目だけ消えたのを見落とすため)。元が SHRINK_MIN_BASE 件未満の区分は見ない。"""

    def count(db: Database, pred: Callable[[Any], bool]) -> int:
        return sum(1 for m in db.models if pred(m))

    def hf(m: Any) -> bool:
        return m.source == "huggingface"

    def tmpl(m: Any) -> bool:
        return m.source == "comfyui-template"

    def unc(m: Any) -> bool:
        return m.content == "uncensored"

    s = d["summary"]
    parts = [
        ("項目", len(old.models), len(new.models)),
        ("Hugging Face の項目", count(old, hf), count(new, hf)),
        ("公式テンプレートの項目", count(old, tmpl), count(new, tmpl)),
        ("無検閲の項目", count(old, unc), count(new, unc)),
        ("自動のおすすめ(HEAD の古さを除く)", s["auto_old"], s["auto_new"]),
    ]
    return [
        f"{label}が {a} → {b} 件に減りました(上流の形が変わった・検索が止まった疑い)"
        for label, a, b in parts
        if a >= SHRINK_MIN_BASE and b < a * MIN_KEEP_RATIO
    ]


def _run_url() -> str | None:
    server, repo, run = (
        os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else None


_MD_SPECIAL = re.compile(r"([\\`*_\[\]!|#~])")


def md(v: Any) -> str:
    """上流から来た文字(モデルの名前・ID・ライセンスの名前・根拠・URL・リポジトリ名など)を、PR の本文
    (GitHub の Markdown)にそのまま置いても効かないようにする: HTML の文字を実体参照に(`<!--`・`<img>`・
    `</details>` が効かない)、Markdown の記号をバックスラッシュで(リンク・画像・強調・表の区切り)、
    `@` の後に幅の無い空白(メンション・通知を出さない)、改行は空白に。"""
    s = "—" if v is None else str(v)
    s = " ".join(s.split())
    s = html.escape(s, quote=False)
    s = _MD_SPECIAL.sub(r"\\\1", s)
    return s.replace("@", "@\u200b")


def code_block(text: str, max_chars: int = 8_000) -> str:
    """ほかのコマンドの出力(#34 の実測の集計など、利用者の出した値を含む)を、そのまま見える囲みにする。
    囲みの中では HTML・リンク・メンションが効かない。囲みの記号は中身のどの ` の並びより長くする。"""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(長いため省きました)"
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def _cell(v: Any) -> str:
    return md(v)


def _table(
    head: list[str],
    rows: list[list[Any]],
    total: int | None = None,
    where: str = "diff.json",
    limit: int | None = None,
) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    cap = MAX_ROWS if limit is None else limit
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows[:cap]]
    n = len(rows) if total is None else total
    if n > cap:
        out.append(f"\n…ほか {n - cap} 件(全部は artifact の {where})")
    return out


def _gb(b: int | None) -> str:
    return "—" if not b else f"{b / 2**30:.2f}"


MAX_IDS_PER_ROW = 3
# 「実測の無い式」の節は知らせるだけなので小さく保つ(本文が長いと limit_body は後ろから切る)
MAX_UNMEASURED_ROWS = 15
MAX_UNMEASURED_CHARS = 80  # 表の 1 つのセル(鍵・id)に出す文字数


def _short(v: Any) -> str:
    t = str(v)
    return t if len(t) <= MAX_UNMEASURED_CHARS else t[: MAX_UNMEASURED_CHARS - 1] + "…"


def unmeasured_section(u: dict[str, Any] | None) -> list[str]:
    """「実測の無い式(増えた項目)」の節(#205。知らせるだけ)。u は coverage.unmeasured_new の結果
    (失敗したときは {"error": 例外の型の名前})。増えた項目が 0 件、または実測の無い式も当てられない項目も
    無いときは節を出さない。表は MAX_UNMEASURED_ROWS 行まで、セルは MAX_UNMEASURED_CHARS 字まで。"""
    if not u:
        return []
    if u.get("error"):
        return [
            "### 実測の無い式(増えた項目)",
            "",
            f"一覧を作れませんでした({md(u['error'])})。更新の確かめには関係しません。",
            "",
        ]
    if not u.get("added") or not (u.get("formulas") or u.get("not_staged")):
        return []
    c = u["counts"]
    cannot = c["not_staged"] + c["not_staged_measured"]
    cannot_text = f"式を当てられない {cannot}" + (
        f"(うち実測あり {c['not_staged_measured']})" if c["not_staged_measured"] else ""
    )
    lines = [
        "### 実測の無い式(増えた項目)",
        "",
        f"増えた {u['added']} 件のうち、両方あり {c['both']}・片方だけ {c['vae_only'] + c['work_only']}・"
        f"どちらも無し {c['none']}・{cannot_text}。",
        "",
    ]
    if u["formulas"]:
        rows = []
        for f in u["formulas"]:
            ids = f["models"]
            shown = ", ".join(_short(i) for i in ids[:MAX_IDS_PER_ROW])
            more = len(ids) - MAX_IDS_PER_ROW
            tail = f" ほか {more} 件" if more > 0 else ""
            rows.append([_short(f["key"]), "VAE" if f["kind"] == "vae" else "生成", len(ids), shown + tail])
        lines += _table(
            ["式の鍵", "段", "増えた項目の数", "項目"], rows, where="result.json", limit=MAX_UNMEASURED_ROWS
        )
        lines.append("")
    if u["not_staged"]:
        parts = [f"{_short(x['modality'])} {len(x['models'])} 件" for x in u["not_staged"]]
        lines += ["式を当てられない項目(実測なし): " + md("、".join(parts)), ""]
    lines += [
        "`genmodelfit share` は、まだ実測の無い式のモデルを先に勧めます(#200。既定は sfw のモデル)。",
        "",
    ]
    return lines


@_japanese()
def pr_body(d: dict[str, Any], result: dict[str, Any]) -> str:
    """PR の本文(Markdown)。d は diff_db の結果、result は run の結果。"""
    s = d["summary"]
    lines = ["## 毎週の一覧の更新(自動)", ""]
    lines += [
        "> このPRは `.github/workflows/weekly-data.yml` が作りました。**自動ではマージしません。**",
        "> 中身を確かめてからマージしてください。マージすると `data.yml` が main の db.json に署名して",
        "> `data-latest` に配り、使う側は `genmodelfit update --from-release` で取り込みます。",
        ">",
        "> GITHUB_TOKEN で作った PR なので、CI(ci.yml)は自動では動きません。",
    ]
    if result.get("run_url"):
        lines += [
            "> 代わりに、この PR を作った同じワークフローの中で、新しい一覧を入れた状態で",
            "> ruff と pytest を通してあります(通らなければ PR は作られません)。",
            f"> 実行: {result['run_url']}",
        ]
    else:
        lines.append("> (手元の実行です。新しい一覧を入れた状態の ruff と pytest は別に回してください)")
    if result.get("offline"):
        lines.append(
            "> **通信せずに記録した応答で作った一覧です(--offline-sources)。HEAD の日時は今の時刻です。**"
        )
    lines.append("")

    warn: list[str] = [f"**{md(w)}**" for w in result.get("warnings") or []]
    if d["new"]["partial"]:
        pc = d.get("partial_counts") or {}
        detail = ", ".join(f"{k}={v}" for k, v in pc.items() if v)
        warn.append(f"**一部を取得できないまま作りました(--allow-partial)**: {detail}")
    if s["license_permissive_side"]:
        warn.append(f"**ライセンスが寛容な側に変わった項目: {s['license_permissive_side']} 件**(下の表の ⚠)")
    if s["auto_gained"] or s["auto_lost"]:
        warn.append(f"自動のおすすめに入った項目 {s['auto_gained']} 件・外れた項目 {s['auto_lost']} 件")
    if s["head_failures_new"]:
        warn.append(f"**新しく HEAD が失敗した URL: {s['head_failures_new']} 件**")
    if s["removed"]:
        warn.append(f"消えた項目: {s['removed']} 件")
    if s["newly_uncensored"]:
        warn.append(f"新しく無検閲の印が付いた項目: {s['newly_uncensored']} 件")
    if s["unauthorized_added"]:
        warn.append(f"匿名の API が 401 になったリポジトリ: {s['unauthorized_added']} 件")
    for b in result.get("popularity_bias") or []:
        if b.get("flagged") and bias_warning(b) not in (result.get("warnings") or []):
            warn.append(f"**{md(bias_warning(b))}**")
    cl = d.get("classification") or {}
    if cl and not cl.get("safe"):
        warn.append(
            f"**判断が要る変化: {len(cl.get('judgement_needed') or [])} 件**(下の「確認待ち」と diff.json)"
        )
    lines += ["### 注意", ""]
    lines += [f"- {w}" for w in warn] if warn else ["- なし"]
    lines.append("")

    st = result.get("status") or {}
    old_st = result.get("status_old") or {}
    lines += ["### 要約", ""]
    lines += _table(
        ["項目", "前(main)", "今回"],
        [
            ["生成日時", d["old"]["generated_at"], d["new"]["generated_at"]],
            ["項目の数", s["models_old"], s["models_new"]],
            ["自動のおすすめ(HEAD の古さを除く同じ方針)", s["auto_old"], s["auto_new"]],
            [
                "ComfyUI の最新の版(記録)",
                d["old"]["comfyui_latest_release"],
                d["new"]["comfyui_latest_release"],
            ],
            ["license_policy", d["old"]["license_policy"], d["new"]["license_policy"]],
            ["HEAD の期限(expires_at)", old_st.get("expires_at"), st.get("expires_at")],
        ],
    )
    lines.append("")
    lines += _table(
        ["変化", "件数"],
        [
            ["増えた", s["added"]],
            ["消えた", s["removed"]],
            ["ライセンスの変化(うち寛容な側)", f"{s['license_changed']}({s['license_permissive_side']})"],
            ["自動のおすすめ 入った / 外れた", f"{s['auto_gained']} / {s['auto_lost']}"],
            ["一式の大きさの変化(10% かつ 100 MiB 超)", s["size_changed"]],
            ["精度の変化", s["precision_changed"]],
            ["新しく無検閲", s["newly_uncensored"]],
            ["HEAD の失敗(うち新しい)", f"{s['head_failures']}({s['head_failures_new']})"],
            ["HEAD の未確認(応答なし)", s["head_unchecked"]],
            ["401 のリポジトリ 増 / 減", f"{s['unauthorized_added']} / {s['unauthorized_removed']}"],
        ],
    )
    lines.append("")

    lines += review_section(d, result.get("review_pending") or [])

    bias = [b for b in result.get("popularity_bias") or [] if b.get("checked")]
    if bias:
        lines += [
            "<details><summary>人気のかたよりの確認"
            "(要る VRAM と使用数の順位の相関。-0.3 未満で注意)</summary>",
            "",
        ]
        lines += _table(
            ["種類", "モデル", "相関(Spearman)"], [[b["modality"], b["models"], b["rho"]] for b in bias]
        )
        lines += ["", "</details>", ""]

    if d["license_changes"]:
        rows = sorted(d["license_changes"], key=lambda c: (not c["permissive_side"], c["id"]))
        lines += ["### ライセンスの変化", ""]
        lines += _table(
            ["", "id", "前", "今回", "許可リスト 前→今回"],
            [
                [
                    "⚠" if c["permissive_side"] else "",
                    c["id"],
                    c["old"],
                    c["new"],
                    f"{c['allowed_old']}→{c['allowed_new']}",
                ]
                for c in rows
            ],
        )
        lines.append("")
    if d["auto_changes"]:
        lines += ["### 自動のおすすめの変化", ""]
        lines += _table(
            ["id", "前", "今回", "今回の保留の理由"],
            [[c["id"], c["old"], c["new"], ", ".join(c["held_by"]) or "—"] for c in d["auto_changes"]],
        )
        lines.append("")
    fresh = [h for h in d["head_failures"] if h["new"]]
    stale = [h for h in d["head_failures"] if not h["new"]]
    head_cols = ["応答", "URL", "項目"]
    if fresh:
        lines += ["### 新しく HEAD が失敗した URL(200 / 302 / 307 以外)", ""]
        lines += _table(head_cols, [[h["status"], h["url"], ", ".join(h["ids"][:3])] for h in fresh])
        lines.append("")
    if stale:
        # 前から失敗している URL(gated など)は毎週同じなので畳む
        lines += [f"<details><summary>前から HEAD が失敗している URL({len(stale)} 件)</summary>", ""]
        lines += _table(head_cols, [[h["status"], h["url"], ", ".join(h["ids"][:3])] for h in stale])
        lines += ["", "</details>", ""]
    for key, title in (("added", "増えた項目"), ("removed", "消えた項目")):
        if d[key]:
            lines += [f"### {title}", ""]
            lines += _table(
                ["id", "名前", "種類", "内容", "ライセンス"],
                [[m["id"], m["name"], m["modality"], m["content"], m["license"]] for m in d[key]],
            )
            lines.append("")
    if d["newly_uncensored"]:
        lines += ["### 新しく無検閲の印が付いた項目", ""]
        lines += uncensored_lines(result.get("uncensored"))
        lines += _table(
            ["id", "種類", "新規", "根拠"],
            [
                [m["id"], m["modality"], m["added"], "; ".join(m["evidence"][:2])]
                for m in d["newly_uncensored"]
            ],
        )
        lines.append("")
    if d["size_changes"] or d["precision_changes"]:
        lines += ["### 大きさ・精度の変化", ""]
        rows = [
            [c["id"], "大きさ (GiB)", _gb(c["old_bytes"]), _gb(c["new_bytes"])] for c in d["size_changes"]
        ]
        rows += [[c["id"], "精度", c["old"], c["new"]] for c in d["precision_changes"]]
        lines += _table(["id", "何", "前", "今回"], rows)
        lines.append("")
    if d["unauthorized"]["added"] or d["unauthorized"]["removed"]:
        lines += ["### 匿名の API が 401 のリポジトリ", ""]
        lines += [f"- 増えた: {', '.join(md(r) for r in d['unauthorized']['added']) or 'なし'}"]
        lines += [f"- 減った: {', '.join(md(r) for r in d['unauthorized']['removed']) or 'なし'}", ""]
    if d["stats"]:
        lines += ["<details><summary>sources の数の変化</summary>", ""]
        lines += _table(["鍵", "前", "今回"], [[x["key"], x["old"], x["new"]] for x in d["stats"]])
        lines += ["", "</details>", ""]

    lines += gpu_watch_section(result.get("gpu_watch"))
    lines += unmeasured_section(
        result.get("unmeasured_formulas")
    )  # 知らせるだけ。切られるなら最初に切られる後ろ
    lines += [
        "### マージの前に確かめること",
        "",
        "- [ ] ⚠ の付いたライセンスの変化を、原文(license_url)で確かめた"
        "(寛容な側への変化は自動のおすすめに出る)",
        "- [ ] 消えた項目・自動のおすすめから外れた項目に、AI生成キットが使うものが無い"
        "(あれば理由を確かめた)",
        "- [ ] 新しく HEAD が失敗した URL(gated になった・消えた)を確かめた",
        "- [ ] 確認待ち(review)の項目と、review approve で付けた根拠(evidence)の URL を見た",
        "- [ ] 新しく無検閲の印が付いた項目の根拠を見た(未成年の性的表現を目的とするものは除外する)",
        "- [ ] 実測のあるモデルのファイルが変わっていたら、"
        "`python -m genmodelfit.calibrate` を別の PR で直す",
        *(
            [
                "- [ ] 「GPU 表の見張り」に候補があれば、一次資料で確かめて別の Issue にした"
                "(このマージは止めない)"
            ]
            if (result.get("gpu_watch") or {}).get("candidates")
            else []
        ),
        "",
        f"<sub>genmodelfit {__version__} / `python -m genmodelfit.weekly` / "
        "全部の違いは artifact の diff.json</sub>",
    ]
    return limit_body("\n".join(lines) + "\n")


@_japanese()
def review_section(d: dict[str, Any], pending: list[dict[str, Any]]) -> list[str]:
    """PR の本文の「分類」と「確認待ち」の節(#42)。上流の文字は md() で無害にする(_table が使う)。"""
    cl = d.get("classification") or {}
    lines = ["### 分類(毎週のスキルが使う)", ""]
    if cl.get("safe"):
        lines.append(
            "- **安全な向きの変化だけ**(HEAD の日時・失敗、ライセンスが厳しくなる向き、消えた項目、"
            "sources の数など)"
        )
    else:
        lines.append(f"- **判断が要る変化があります: {len(cl.get('judgement_needed') or [])} 件**")
        lines += _table(
            ["id", "理由"], [[j["id"], ", ".join(j["reasons"])] for j in cl.get("judgement_needed") or []]
        )
    lines.append("")
    lines += [f"### 確認待ち(review: pending)の項目: {len(pending)} 件", ""]
    if pending:
        lines.append(
            "確かめるまで自動のおすすめに出ません。一次資料を見て `genmodelfit review approve` か `reject`。"
        )
        lines.append("")
        lines += _table(
            ["id", "名前", "種類", "内容", "ライセンス", "理由"],
            [
                [p["id"], p["name"], p["modality"], p["content"], p["license"], ", ".join(p["reasons"])]
                for p in pending
            ],
        )
    else:
        lines.append("- なし")
    lines.append("")
    return lines


def limit_body(body: str) -> str:
    """本文を MAX_BODY_CHARS に収める。行の途中で切らず、開いたままの <details> を閉じる
    (上流の文字の < > は md() で実体参照にしてあるので、ここで数える <details> は自分で書いたものだけ)。"""
    if len(body) <= MAX_BODY_CHARS:
        return body
    cut = body[: MAX_BODY_CHARS - 300]
    cut = cut[: cut.rfind("\n") + 1]
    tail = "\n…(本文が長いため省きました。全部は artifact の diff.json)\n"
    tail += "\n</details>\n" * max(0, cut.count("<details>") - cut.count("</details>"))
    return cut + tail


def uncensored_lines(u: dict[str, Any] | None) -> list[str]:
    """新しく無検閲の印の数と上限の内訳の行(PR の本文の無検閲の節の頭)。上限は日数で増えるので、止まった後の
    週は、たまった分か規則が緩んだ分かを人が項目で確かめるように書く(#197)。古い result(uncensored が
    無い)なら何も出さない。"""
    if not isinstance(u, dict) or not all(isinstance(u.get(k), int) for k in ("new", "limit", "weeks")):
        return []
    line = (
        f"新しく無検閲の印: {u['new']} 件(上限 {u['limit']} 件 = {MAX_NEW_UNCENSORED} 件 × {u['weeks']} 週)"
    )
    if u["weeks"] >= 2:
        line += (
            f"。元の一覧を作り直してから {u['weeks']} 週ぶんです。1 週で増えたのか、たまった分かを項目で"
            "確かめてください"
        )
    return [line, ""]


def uncensored_limit(old: Database, now: datetime) -> tuple[int, int]:
    """新しく無検閲の印が付いてよい数と、その週の数: (MAX_NEW_UNCENSORED × 週の数, 週の数)。

    週の数は、元の一覧(old)を作り直した日(generated_at)から now までの日数 ÷ 7 の四捨五入(1 から
    MAX_UNCENSORED_WEEKS まで)。切り上げにしないのは、毎週の起動の時刻が数時間ずれ、ふだんの週の経過が
    「7 日 + 数時間」になるため(ずれで 1 週ぶん増えないように)。round() は偶数への丸めなので使わない。
    generated_at は一覧を上流から作り直したときだけ変わり、HEAD だけ・項目だけのやり直し
    (update --heads-only など)では変わらない(週の数が戻って、たまった分がまた通らなくなることが無い)。
    日が読めない・now より先のときは 1 週(今までと同じ数)。"""
    made = old.generated_time
    if made is None or made > now:
        weeks = 1
    else:
        days = (now - made).total_seconds() / 86400
        weeks = min(MAX_UNCENSORED_WEEKS, max(1, math.floor(days / 7 + 0.5)))
    return MAX_NEW_UNCENSORED * weeks, weeks


@_japanese()
def run(
    out_dir: str | Path,
    *,
    base: str | Path | None = None,
    fetch: Fetcher | None = None,
    allow_partial: bool = False,
    allow_many_uncensored: bool = False,
    apply_to: str | Path | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
    offline: bool = False,
    gpu_watch: bool = True,
) -> dict[str, Any]:
    """作り直して確かめ、<out_dir> に db.json・diff.json・pr_body.md・result.json を書く。何も表示しない。

    返す値(result.json と同じ): ok・problems・partial・status(DataStatus)・summary(diff の要約)・files。
    ok が False のときは --apply しない。作り直しをやめた(UpdateFailed)ときも ok=False で返す。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base_path = Path(base) if base else bundled_db_path()
    result: dict[str, Any] = {
        "genmodelfit": __version__,
        "ok": False,
        "problems": [],
        "base": str(base_path),
        "offline": offline,
        "run_url": _run_url(),
    }
    old = load_db(base_path)
    fetch = fetch or make_fetcher()
    new_path = out / "db.json"
    try:
        new = update_database(fetch, new_path, allow_partial=True, progress=progress, previous=old)
        inspect_database(fetch, new_path, db_path=str(new_path), offline=offline, progress=progress)
        new = load_db(new_path)  # 書いたものを形の検査を通して読み直す
    except (UpdateFailed, DatabaseError) as e:
        result["problems"].append(str(e))
        _write_json(out / "result.json", result)
        return result
    when = now or datetime.now(timezone.utc)
    problems = []
    if new.sources.get("partial"):
        if allow_partial:
            problems += partial_problems(new, old)
        else:
            pc = new.sources.get("partial_counts") or {}
            detail = ", ".join(f"{k}={v}" for k, v in pc.items() if v)
            problems.append(f"一部を取得できませんでした({detail})。--allow-partial で通せる場合があります")
    more, status = check_new(new, old, now=when)
    problems += more
    d = diff_db(old, new)
    problems += shrink_problems(old, new, d)
    warnings: list[str] = []
    many = d["summary"]["newly_uncensored"]
    limit, weeks = uncensored_limit(old, when)
    if many > limit:
        msg = (
            f"新しく無検閲の印が付いた項目が {many} 件あります(上限 {limit} 件 = "
            f"{MAX_NEW_UNCENSORED} 件 × {weeks} 週)。選び方の規則が緩んでいないか確かめてください"
        )
        (warnings if allow_many_uncensored else problems).append(msg)
    pending = review_list(new)
    from .coverage import unmeasured_new
    from .popularity import vram_bias

    # 増えた項目のうち実測の無い式を持つもの(#205。知らせるだけ。ok・problems・分類・merge-check には効かない)
    try:
        unmeasured = unmeasured_new(new, [m["id"] for m in d["added"]])
    except Exception as e:  # 知らせるだけの節で更新を止めない(例外の文は手元の場所が混ざりうるので入れない)
        unmeasured = {"error": type(e).__name__}
        if progress:
            progress(f"実測の無い式の一覧を作れませんでした: {type(e).__name__}")
    bias = vram_bias(new)  # 人気のかたより(#95。docs/scoring.md「人気」)
    warnings += [bias_warning(b) for b in bias if b["flagged"]]  # オーナーが見るように(merge-check も止める)
    result.update(
        popularity_bias=bias,
        review_pending=pending,
        uncensored={"new": many, "limit": limit, "weeks": weeks},  # 超えない週も数を見えるように(#197)
        classification={
            "safe": d["classification"]["safe"],
            "judgement_needed": len(d["classification"]["judgement_needed"]),
        },
        warnings=warnings,
        unmeasured_formulas=unmeasured,
        status_old=compute_status(old, GatePolicy(), now=when, source="path").to_dict(),
        problems=problems,
        ok=not problems,
        partial=bool(new.sources.get("partial")),
        status=status,
        summary=d["summary"],
        files={
            "db": str(new_path),
            "diff": str(out / "diff.json"),
            "pr_body": str(out / "pr_body.md"),
            "review_queue": str(out / "review_queue.json"),
        },
    )
    if gpu_watch:
        result["gpu_watch"] = watch_gpus(fetch, progress)
        result["files"]["gpu_watch"] = str(out / "gpu_watch.json")
        _write_json(out / "gpu_watch.json", result["gpu_watch"])
    _write_json(out / "diff.json", d)
    _write_json(out / "review_queue.json", review_queue(new, d["classification"]))
    body = pr_body(d, result)
    if problems:
        body = (
            "## 確かめに落ちました(PR は作りません)\n\n"
            + "".join(f"- {md(p)}\n" for p in problems)
            + "\n"
            + body
        )
    (out / "pr_body.md").write_text(body, encoding="utf-8", newline="\n")
    if result["ok"] and apply_to:
        shutil.copyfile(new_path, apply_to)
        result["applied_to"] = str(apply_to)
    _write_json(out / "result.json", result)
    return result


def watch_gpus(fetch: Fetcher, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """GPU の表の見張り(gpuwatch.watch)。例外を出さない(毎週の更新を止めない)。"""
    if progress:
        progress("GPU の表の見張り: 公式の一覧を読む")
    try:
        from . import gpuwatch

        return gpuwatch.watch(fetch).to_dict()
    except Exception as e:
        return {
            "candidates": [],
            "unreadable": [{"source": "gpuwatch", "url": "", "error": f"{type(e).__name__}: {e}"}],
            "read": [],
            "baseline": None,
        }


@_japanese()
def gpu_watch_section(w: dict[str, Any] | None) -> list[str]:
    """PR の本文の「GPU 表の見張り」の節(#103)。上流の文字は md() で無害にする(_table が使う)。"""
    if not w:
        return []
    cands = w.get("candidates") or []
    bad = w.get("unreadable") or []
    lines = ["### GPU 表の見張り", ""]
    lines.append(
        "公式の GPU の一覧と GPU の登録の表(`gputables.py`)を照らした結果です。**表は自動では書き換えません。"
        "この節はマージを止めません**(候補は一次資料で確かめてから別の Issue で足す。"
        "docs/release.ja.md「毎週の一覧の更新」)。"
    )
    lines.append("")
    kind_ja = {"not in tables": "表に無い", "differs": "表と違う"}
    if cands:
        lines.append(f"候補: {len(cands)} 件")
        lines.append("")
        rows = [[c["source"], kind_ja.get(c["kind"], c["kind"]), c["item"], c["detail"]] for c in cands]
        lines += _table(["出典", "種類", "項目", "詳しく"], rows[:MAX_ROWS])
        if len(rows) > MAX_ROWS:
            lines.append(f"…ほか {len(rows) - MAX_ROWS} 件(全部は artifact の gpu_watch.json)")
    else:
        lines.append(f"- 候補なし(読んだ出典 {len(w.get('read') or [])} 件)")
    lines.append("")
    if bad:
        lines.append(f"読めなかった出典: {len(bad)} 件")
        lines.append("")
        lines += _table(["出典", "URL", "理由"], [[b["source"], b["url"], b["error"]] for b in bad])
        lines.append("")
    return lines


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


MERGE_NOT_SAFE = 3


def bias_warning(b: dict[str, Any]) -> str:
    """人気のかたよりの注意の 1 文(#95)。PR の本文・result の warnings・merge-check の理由に同じ文を使う。"""
    return (
        f"人気のかたより: {b['modality']} で要る VRAM と使用数の順位の相関が {b['rho']}"
        f"({b['models']} モデル。-0.3 を下回った)。軽いモデルほど人気になっているので、"
        "人気を並びの鍵から外すかを考える(docs/scoring.md「人気」)"
    )


@_japanese()
def merge_check(base: str | Path, head: str | Path) -> dict[str, Any]:
    """2 つの一覧のファイルから分類を計算し直す(何も書かず、通信しない)。読めなければ DatabaseError。

    返す値: safe・judgement_needed(件数)・reasons([{id, name, reasons}])・safe_changes・base・head。
    ファイルの JSON をそのまま比べるので、この版が知らない鍵の変化も判断が要るに数える。"""
    raws = []
    for path in (base, head):
        try:
            raws.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise DatabaseError(f"一覧のファイルを読めません: {path}: {e}") from e
        models = raws[-1].get("models") if isinstance(raws[-1], dict) else None
        dup = duplicate_ids(models if isinstance(models, list) else [])
        if dup:  # 同じ ID の 2 つ目に差し替えた項目を紛れ込ませられないように(load も断る)
            raise DatabaseError(f"同じモデル ID が 2 回以上あります: {path}: {', '.join(map(str, dup[:5]))}")
    old, new = load_db(base), load_db(head)
    cl = diff_db(old, new, raw=(raws[0], raws[1]))["classification"]
    from .popularity import vram_bias

    # 人気のかたよりが出た週は自動でマージしない(オーナーが見る。#95。docs/scoring.md「人気」)
    reasons = list(cl["judgement_needed"])
    reasons += [
        {"id": f"popularity_bias:{b['modality']}", "name": b["modality"], "reasons": [bias_warning(b)]}
        for b in vram_bias(new)
        if b["flagged"]
    ]
    return {
        "genmodelfit": __version__,
        "command": "merge-check",
        "base": str(base),
        "head": str(head),
        "safe": cl["safe"] and len(reasons) == len(cl["judgement_needed"]),
        "judgement_needed": len(reasons),
        "reasons": reasons,
        "safe_changes": cl["safe_changes"],
    }


@_japanese()
def merge_check_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="python -m genmodelfit.weekly merge-check",
        description="2 つの一覧から分類を計算し直す(#42)。0 = 安全な向きだけ、3 = 判断が要る、1 = 読めない",
    )
    p.add_argument("--base", required=True, metavar="PATH", help="main の一覧(src/genmodelfit/data/db.json)")
    p.add_argument("--head", required=True, metavar="PATH", help="マージしようとする一覧")
    p.add_argument("--json", action="store_true", help="結果を JSON で出す")
    args = p.parse_args(argv)
    try:
        r = merge_check(args.base, args.head)
    except DatabaseError as e:
        if args.json:
            err = {"command": "merge-check", "error": str(e)}
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        print("SAFE" if r["safe"] else f"NOT SAFE: 判断が要る {r['judgement_needed']} 件")
        for j in r["reasons"]:
            print(f"  {j['id']}: {', '.join(j['reasons'])}")
    return 0 if r["safe"] else MERGE_NOT_SAFE


def _utf8_stdio() -> None:
    """Windows の cp932 / cp1252 でも日本語の出力で落ちず、呼ぶ側が UTF-8 で読めるようにする(cli と同じ)。"""
    import contextlib
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.reconfigure(encoding="utf-8")


@_japanese()
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    _utf8_stdio()
    if argv[:1] == ["merge-check"]:
        return merge_check_main(argv[1:])
    p = argparse.ArgumentParser(
        prog="python -m genmodelfit.weekly",
        description="毎週の一覧の作り直し(上流から)・確かめ・差分と PR の本文を作る(公開はしない)。"
        "docs/release.md",
    )
    p.add_argument(
        "--out", required=True, metavar="DIR", help="db.json・diff.json・pr_body.md・result.json の置き場所"
    )
    p.add_argument("--base", metavar="PATH", help="比べる元の一覧(既定は同梱の一覧 = main の db.json)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", metavar="PATH", help="確かめを通った一覧をここへ写す(ワークフローは同梱の db.json)"
    )
    mode.add_argument("--dry-run", action="store_true", help="<DIR> の外には書かない(既定)")
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--offline-sources", metavar="DIR", help="--record で残した応答を上流の代わりに使う(通信しない)"
    )
    src.add_argument("--record", metavar="DIR", help="取った上流の応答と HEAD の応答コードを残す")
    p.add_argument("--allow-partial", action="store_true", help="一部が欠けても、筋が通れば警告付きで通す")
    p.add_argument(
        "--allow-many-uncensored",
        action="store_true",
        help=(
            f"新しく無検閲の項目が上限({MAX_NEW_UNCENSORED} 件 × 元の一覧を作り直してからの週の数)を"
            "超えても失敗にせず、先頭に警告(手動の実行)"
        ),
    )
    p.add_argument(
        "--rate-limit-budget", type=float, metavar="SEC", help="429 で待つ時間の合計の上限(既定 900)"
    )
    p.add_argument("--no-gpu-watch", action="store_true", help="GPU の表の見張り(gpuwatch)を飛ばす")
    p.add_argument("--json", action="store_true", help="result.json と同じものを標準出力に出す")
    args = p.parse_args(argv)

    def say(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    recorder = None
    if args.offline_sources:
        fetch: Fetcher = replay_fetcher(args.offline_sources)
    else:
        fetch = make_fetcher(rate_limit_budget=args.rate_limit_budget)
        if args.record:
            fetch = recorder = WeeklyRecorder(fetch, args.record)
    try:
        result = run(
            args.out,
            base=args.base,
            fetch=fetch,
            allow_partial=args.allow_partial,
            allow_many_uncensored=args.allow_many_uncensored,
            apply_to=None if args.dry_run else args.apply,
            progress=say,
            offline=bool(args.offline_sources),
            gpu_watch=not args.no_gpu_watch,
        )
    except DatabaseError as e:  # --base が読めない
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if recorder is not None:
            recorder.save_heads()
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        s = result.get("summary") or {}
        print(f"{'OK' if result['ok'] else 'NG'}: {args.out}")
        for prob in result["problems"]:
            print(f"  - {prob}")
        if s:
            print(
                f"  項目 {s['models_old']} → {s['models_new']}(+{s['added']} / -{s['removed']})、"
                f"自動 {s['auto_old']} → {s['auto_new']}、ライセンスの変化 {s['license_changed']}"
                f"(寛容な側 {s['license_permissive_side']})、HEAD の失敗 {s['head_failures']}"
            )
        st = result.get("status") or {}
        if st:
            print(f"  HEAD の期限: {st.get('expires_at')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
