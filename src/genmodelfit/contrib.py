# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""利用者が出す実測(提出用の JSON)の作成・検査・まとめ(#34)。

流れ(genmodelfit 自体は何も送らない。利用者が GitHub の Issue のフォームで「Create」を押すか、
自分で Pull Request を出す):

1. `genmodelfit measure`(#113)・`genmodelfit bench ... --submission <path>`
   (または `contrib make --from <bench の結果>`)で提出用の JSON(`contrib_schema` 3)を作る。
   入れるのは GPU 名・VRAM・世代・compute capability・ドライバ・ComfyUI の版・
   OS の種類(windows / linux。版やビルドは入れない)・メインメモリ(GiB の整数に丸める)・
   モデル ID・設定・結果・最大 VRAM / メモリ・時間・測り方・日付(UTC の日付だけ)。
   PC 名・ユーザ名・パス・GPU の UUID・PCI の位置・シリアル・IP・時刻は入れない(note の中も消す)
   share は測ったときの見積もり・時間の内訳・一覧の版も付け、測れなかったものを失敗の記録のファイル
   (`<日付>-<sha8>-failures.json`。文は入れない)にする(#132)
2. `contrib show` で、送る中身をそのまま見せる(同意のため)。個人の情報の点検の結果も出す
3. `genmodelfit share` が中身を入れた実測の Issue のフォームを開き、利用者が「Create」を押す。
   .github/workflows/measurement-intake.yml が `contrib intake`(intake_issue)で検査して
   `data/contrib/<GPU>/<日付>-<内容の sha256 の先頭 8 桁>.json` を足す PR を作る(git で自分で PR にしてもよい)
4. CI(.github/workflows/contrib.yml)が `contrib validate` で形・値・個人の情報・重複を機械的に検査する
   (llmfit の validate_community_benchmarks.py と同じ考え方。2026-10-22)。式から大きく外れる・段階を変える
   などは失敗にせず、注意(notices)としてジョブのまとめに出す。マージするメンテナが見て決める
5. `contrib aggregate` が同じモデル・設定・GPU ごとに中央値と MAD で外れ値を除いてまとめ、
   同梱の `measurements_community.json` を作る。`--report` は校正(leave-one-out)の誤差の表と閾値の判定

ライブラリの関数は何も表示しない(表示は cli.py)。
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import itertools
import json
import math
import os
import platform
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._version import __version__
from .db import Database
from .estimate import (
    COMMUNITY_DEVIATION,
    MIN_LEVEL_SOURCES,
    NOMINAL_VRAM_TOLERANCE,
    PEAK_VRAM_RATIO,
    PEAK_VRAM_SLACK_GB,
    PRECISION_BYTES,
    files_match,
    generic_gpu_name,
    gpu_key,
    no_size_input,
    norm_precision,
    nvidia_nominal_vram,
)
from .estimate import CONSISTENT_REL as _LEVEL_AGREE_REL
from .hardware import (
    APPLE_CHIP_NAME,
    arch_of,
    cc_from_name,
    intel_xpu,
    nvidia_name_well_formed,
    parse_compute_cap,
    pci_name_conflict,
)
from .i18n import tr
from .models import MODALITIES, ORIGIN_COMMUNITY, OUTCOMES, Measurement, Settings, SystemInfo

# 提出の形の版。2(#90)= 1 に measurement.files_fp(測ったときの一覧のファイルの指紋)を足した形。
# 3(#132)= 2 に、測ったときの見積もり(measurement.estimate)・時間の内訳(measurement.timing)・
# VRAM を超えた印(measurement.spilled・spilled_gb)・一覧の版(db)と、失敗の記録のファイル(kind
# genmodelfit-failures)を足した形。書くのは 3、検査は 1・2・3 を受け付ける(古い版には新しい項目を入れない)
CONTRIB_SCHEMA = 3
CONTRIB_SCHEMAS = (1, 2, 3)
CONTRIB_DIR = "data/contrib"  # リポジトリの中の置き場所(PR で足す)
KIND = "genmodelfit-measurement"
FAILURES_KIND = "genmodelfit-failures"  # 失敗の記録(#132。1 回の share・1 つの GPU で 1 ファイル)
FAILURES_SUFFIX = "-failures"  # 失敗の記録のファイル名の終わり(<日付>-<sha8>-failures.json)

# 値の健全さの許容
VRAM_TOLERANCE_GB = PEAK_VRAM_SLACK_GB  # 最大 VRAM は GPU の総量 × 1.02 + これまで(estimate.peak_vram_cap)
RAM_TOLERANCE_GB = 1.0  # 最大メモリはメインメモリ × 1.05 + これまで
MAX_GPU_VRAM_GB = 256.0  # 1 枚の GPU の VRAM の上限(超えたら誤り。0.9.0 で 512 から下げた)
MAX_RAM_GB = 4096.0
# note の長さの上限(#90 で 2000 から下げた。bench の note は固定の部分が 230 字ほどで、長い失敗の理由などで
# 超えるときは build_submission が切り詰める)
MAX_NOTE_CHARS = 500
# 設定の範囲(テンプレートで使うありえる幅。外れたら誤り)
SETTING_RANGES: dict[str, tuple[float, float]] = {
    "width": (64, 8192),
    "height": (64, 8192),
    "frames": (1, 2000),
    "seconds": (0.1, 3600),
    "steps": (1, 1000),
    "batch": (1, 64),
}
ARCHES = (
    "fermi",
    "kepler",
    "maxwell",
    "pascal",
    "volta",
    "turing",
    "ampere",
    "ada",
    "hopper",
    "blackwell",
)
OS_FAMILIES = ("windows", "linux", "macos")
OFFLOADS = ("none", "dynamic", "partial", "lowvram", "oom")
VRAM_METRICS = ("torch_reserved",)

# 項目(これ以外のキーは誤り。知らない項目に PC の情報が紛れ込まないように)
# db(一覧の版)は contrib_schema 3 だけ(#132)
TOP_KEYS = {"contrib_schema", "kind", "tool", "date", "system", "comfyui_version", "measurement", "db"}
# 失敗の記録のファイル(kind genmodelfit-failures。contrib_schema 3 だけ。#132)
FAILURES_TOP_KEYS = {"contrib_schema", "kind", "tool", "date", "system", "comfyui_version", "db", "failures"}
TOOL_KEYS = {"name", "version"}
SYSTEM_KEYS = {"os_family", "ram_total_gb", "gpu"}
# vendor・backend は 0.9.0 で足した任意の項目
# (NVIDIA 以外の GPU の実測を見分けるため。docs/schema.md「contrib」)
# pci_id(PCI の型番 ID "1002:744c"。機種の番号で、個体・差し場所は表さない)も 0.9.0 の任意の項目
GPU_KEYS = {"name", "vram_gb", "arch", "compute_capability", "driver", "vendor", "backend", "pci_id"}
# PCI の vendor ID → 会社(提出の pci_id と vendor が合うかを見る)
PCI_VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
_PCI_ID = re.compile(r"[0-9a-f]{4}:[0-9a-f]{4}")
# GPU の会社 → PyTorch の backend(models.USABLE_BACKENDS)
VENDOR_BACKEND = {"nvidia": "cuda", "amd": "rocm", "intel": "xpu", "apple": "mps"}
MEASUREMENT_KEYS = {
    "model_id",
    "settings",
    "outcome",
    "standard",
    "offload",
    "peak_vram_gb",
    "peak_ram_gb",
    "seconds",
    "vram_metric",
    "note",
    "files_fp",  # contrib_schema 2 だけ(#90)
    # テンプレートの設定を変えて測った印(#116。任意で、true のときだけ書く。contrib_schema は上げない:
    # 0.9.0 の GPU の任意の項目と同じ)
    "settings_override",
    # 以下は contrib_schema 3 だけ(#132)
    "estimate",  # 測ったときの genmodelfit の見積もり(この実測とこの PC のほかの実測を使わずに作ったもの)
    "timing",  # 時間の内訳(ComfyUI の /history とログから。分からない項目は null)
    "spilled",  # ピークの VRAM が GPU の総量を超えた(共有メモリへ逃げた)印。true のときだけ書く
    "spilled_gb",  # 総量を超えた量(GiB)。spilled のときだけ
}
_FILES_FP = re.compile(r"[0-9a-f]{64}")
# 測ったときの見積もり(#132。docs/schema.md「contrib」)
ESTIMATE_KEYS = {
    "fit_level",  # 段階(perfect / good / marginal / too_tight)
    "run_mode",  # 動かし方(gpu / sequential / offload / cpu / none)
    "vram_gb",  # 段階を決めた VRAM の量
    "vram_resident_gb",  # 全部を載せたまま仕上げまで動かすときに空けようとする量
    "work_gb",  # 作業用メモリの式の値(いちばん重い段)
    "ram_required_gb",  # 要るメインメモリ
    "seconds",  # 時間の目安(measure の目安と同じ)
    "basis",  # measured / estimated
    # 見積もりに使った実測(#132)。bundled-excluding-this-gpu = 同梱の実測から、同じ GPU・同じモデルのもの
    # (作者の PC の実測かもしれない)を除いたもの
    "measurements",
}
ESTIMATE_SOURCES = ("bundled-excluding-this-gpu",)
ESTIMATE_LEVELS = ("perfect", "good", "marginal", "too_tight")
ESTIMATE_MODES = ("gpu", "sequential", "offload", "cpu", "none")
ESTIMATE_BASES = ("measured", "estimated")
# 時間の内訳(#132)。どれも秒で、分からなければ null
TIMING_KEYS = {
    "queue_s",  # 送ってから実行が始まるまで
    "load_s",  # 実行の始まりからサンプラの始まりまで(モデルの読み込みと文の符号化)
    "sampling_s",  # サンプラ(ステップ)の時間の合計
    "sec_per_step",  # 1 ステップの秒数
    "decode_s",  # サンプラの終わりから実行の終わりまで(VAE の復元と保存)
    "save_s",  # 保存だけの時間(今は HTTP では分けられないので null)
    "total_s",  # 送ってから結果が分かるまで(measurement.seconds と同じ)
    "cold_load",  # 初めての読み込み(true)・同じ ComfyUI で 2 回目(false)・分からない(null)
}
DB_KEYS = {"generated_at", "sha256"}  # 一覧の版: 作った日(日付だけ)と db.json の sha256 の先頭 12 桁
_DB_SHA = re.compile(r"[0-9a-f]{12}")
# 失敗の記録 1 件の項目(#132)。文は送らない(例外の型名・ComfyUI の種類の名前・ノードの名前だけ)
FAILURE_KEYS = {
    "model_id",
    "settings",
    "outcome",  # FAILURE_OUTCOMES
    "seconds",  # 経過時間(測る前に分かったものは null)
    "timeout_s",  # 時間切れの上限(timeout のとき)
    "missing_nodes",  # 足りないノードの名前の並び(missing_nodes のとき)
    "other_nodes",  # 名前の形に合わない(送らない)足りないノードの数(missing_nodes のとき)
    "error_type",  # 例外の型名(torch.OutOfMemoryError など)か ComfyUI の node_errors の type
    "node_type",  # 失敗したノードの種類(送ったワークフローにあるものだけ)
    "peak_vram_gb",
    "before_run",  # 測る前(ノードの確かめ)に分かった(実行していない)
    "files_fp",
}
# 送る失敗。中断(interrupted。利用者が止めた)・ほかのプログラムのせい(interference)は送らない
FAILURE_OUTCOMES = ("missing_nodes", "timeout", "error", "oom")
MAX_FAILURES = 50  # 1 つのファイルの失敗の記録の数の上限
MAX_MISSING_NODES = 30
# ノードの名前(ComfyUI の組み込みとカスタムノードの公開の名前)。文字(各国の文字も)・数字・空白と
# _ . + ( ) | ' : , [ ] - だけ、100 字まで。パス・URL に使う / \ は入れない。形に合わない名前は送らず、数だけ
# (other_nodes)送る。個人の情報らしい文字(電話番号・ドメインなど)は、形に合っても送る側・受け取る側の
# 両方で断る
NODE_NAME = re.compile(r"[^\W_][\w .+()|':,\[\]-]{0,99}")
# 例外の型名(モジュールを含む)か ComfyUI の node_errors の type
ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}")
SETTINGS_KEYS = {"width", "height", "frames", "seconds", "steps", "precision", "batch"}

# --- 個人の情報の検出 ------------------------------------------------------------------------------

# 文字列の長さの上限(note 以外)。長い値に正規表現を当て続けないためでもある
MAX_STR = 100
MAX_KEY = 64
MAX_FILE_BYTES = 64 * 1024  # 提出 1 件のファイルの大きさの上限
MAX_DEPTH = 8

# 制御文字・改行・見えない文字・向きを変える文字(CI のまとめやログへの差し込み、見えない書き換えを防ぐ)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u00ad\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
_ZERO_WIDTH = re.compile(r"[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]")
_HOST_WORDS = r"host ?name|computer ?name|PC ?name|user ?name|ホスト名|コンピューター?名|PC ?名|ユーザー?名"
_POSIX_ROOTS = r"home|Users|root|mnt|media|tmp|var|opt|srv|private|Volumes|etc|usr|data|workspace|run"

# 公開の道具・モデルの置き場のドメイン(note に書いても個人の情報ではない。#90)
PUBLIC_DOMAINS = (
    "huggingface.co",
    "hf.co",
    "github.com",
    "githubusercontent.com",
    "comfy.org",
    "pytorch.org",
    "nvidia.com",
    "python.org",
)
_B64_RUN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{16,}(={0,2})(?![A-Za-z0-9+/=])")


def _char_class(c: str) -> int:
    return 0 if c.isdigit() else (1 if c.isupper() else 2)


class _Base64Like:
    """base64 らしい続き(#90): [A-Za-z0-9+/] の続きで、次のどれか。
    (1) 16 字以上で末尾に = の詰め物がある(「C:\\Users\\taro」の base64 も当たる)、
    20 字以上で (2) 中に + がある、(3) 大文字が 2 割以上・小文字・数字がどれも入り、語の形をしていない:
    3 字以上の小文字の語(先頭だけ大文字可)が 2 つ未満、その語の 4 分の 1 より多くが母音(aeiou)を含まない、
    または隣り合う文字の種類(大文字・小文字・数字)の切り替わりが半分以上。ComfyUI のノードの名前
    (HunyuanVideo15ImageToVideo・Qwen2VLForConditionalGeneration)は語の並びなので当たらない。
    / だけでは数えない(パスは別の形で見る)。短い base64・語に見える base64 は見逃す
    (docs/schema.md「contrib」)。"""

    def search(self, text: str) -> bool:
        for m in _B64_RUN.finditer(text):
            run, pad = m.group(0).rstrip("="), m.group(1)
            if pad:
                return True
            if len(run) < 20:
                continue
            if "+" in run:
                return True
            letters = run.replace("/", "")
            upper = sum(c.isupper() for c in letters)
            if not (
                upper >= 0.2 * len(letters)
                and any(c.islower() for c in letters)
                and any(c.isdigit() for c in letters)
            ):
                continue  # base64 は大文字が 4 割ほど。ファイル名の略記(t2v14Bfp8e4m3fnscaledKJ)は少ない
            words = [w for w in re.findall(r"[A-Z]?[a-z]{2,}", letters) if len(w) >= 3]
            voweled = sum(bool(re.search("[aeiouAEIOU]", w)) for w in words)
            switches = sum(_char_class(a) != _char_class(b) for a, b in itertools.pairwise(letters))
            if len(words) < 2 or voweled < 0.75 * len(words) or switches >= 0.5 * (len(letters) - 1):
                return True
        return False


# (種類, 正規表現, (説明の日本語, 英語))。値を NFKC でそろえ(全角の Ｃ：＼ → C:\)、見えない文字を除き、
# ¥ を \ とみなしてから当てる。
# どれも入れ子の繰り返しの無い形にした(長い値でも時間が線形に近い。値の長さは MAX_STR / MAX_NOTE_CHARS まで)。
# 当てはまらなければ安全、ではない(人の名前・自由な文の意味は分からない)。docs/schema.md「contrib」
PRIVACY_PATTERNS: tuple[tuple[str, Any, tuple[str, str]], ...] = (
    (
        "path",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
        ("Windows のパス(C:\\… など)", "a Windows path (C:\\… etc.)"),
    ),
    (
        "path",
        re.compile(r"\\"),
        (
            "バックスラッシュ(Windows のパスの区切り・\\\\server\\…)",
            "a backslash (Windows path separator, \\\\server\\…)",
        ),
    ),
    (
        "path",
        re.compile(r"\w[\\/]\w+[\\/]"),
        (
            "区切りが 2 つ以上あるパス(a/b/c、Users\\name\\…)",
            "a path with two or more separators (a/b/c, Users\\name\\…)",
        ),
    ),
    (
        "path",
        re.compile(r"(?<![\w.])//?(" + _POSIX_ROOTS + r")\b", re.I),
        ("Linux / macOS のパス(/home/… など)", "a Linux / macOS path (/home/… etc.)"),
    ),
    (
        "path",
        re.compile(r"(?<![\w.:])//\w"),
        ("ネットワークのパス(//server/…)", "a network path (//server/…)"),
    ),
    (
        "path",
        re.compile(r"(?<!\w)~[\\/]|%[A-Za-z_]\w*%|\$\{?[A-Za-z_]\w*\}?[\\/]|\$HOME\b|\$env:", re.I),
        (
            "ホーム・環境変数のパス(~/、%USERPROFILE%、$HOME)",
            "a home or environment-variable path (~/, %USERPROFILE%, $HOME)",
        ),
    ),
    (
        "path",
        re.compile(r"%(2F|5C|3A)", re.I),
        ("URL エンコードしたパス(%2F %5C %3A)", "a URL-encoded path (%2F %5C %3A)"),
    ),
    (
        "users_dir",
        re.compile(r"(?<![A-Za-z])Users[\\/]|Documents and Settings", re.I),
        ("ユーザーのフォルダ", "a user folder"),
    ),
    ("at_sign", re.compile(r"@\w"), ("メールアドレス・@ の名前", "an email address or @name")),
    (
        "uuid",
        re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"),
        ("UUID(GPU の UUID など)", "a UUID (GPU UUID etc.)"),
    ),
    ("gpu_uuid", re.compile(r"\b(GPU|MIG)-[0-9A-Za-z]{4}", re.I), ("GPU の UUID", "a GPU UUID")),
    (
        "pci",
        re.compile(
            r"\b[0-9A-Fa-f]{4,8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]\b|\b[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]\b"
            r"|PCI\\VEN_|\bbus[ _-]?id\b",
            re.I,
        ),
        ("PCI の位置", "a PCI location"),
    ),
    (
        "ip",
        re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])"),
        ("IP アドレス", "an IP address"),
    ),
    (
        "ip",
        re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])"),
        ("IPv6 アドレス", "an IPv6 address"),
    ),
    ("mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), ("MAC アドレス", "a MAC address")),
    (
        "url",
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{1,15}://|\bwww\.", re.I),
        ("URL(ComfyUI の場所など)", "a URL (where ComfyUI runs etc.)"),
    ),
    (
        "hostname",
        re.compile(
            r"\b(DESKTOP|LAPTOP|WIN)-[A-Z0-9]{4,}\b|\b[A-Za-z0-9]+-PC\b"
            r"|\b[A-Za-z0-9-]{1,63}\.(local|lan|localdomain|internal|corp|home\.arpa)\b|" + _HOST_WORDS,
            re.I,
        ),
        (
            "PC 名・ユーザ名らしい文字(DESKTOP-XXXXXXX、*-PC、*.local、「PC 名」など)",
            'what looks like a PC or user name (DESKTOP-XXXXXXX, *-PC, *.local, "PC name" etc.)',
        ),
    ),
    (
        "time",
        re.compile(
            r"\b\d{1,2}:\d{2}\b|\b\d{1,2} ?(am|pm)\b|\d{1,2}時\d{1,2}分|\d{4}-\d{2}-\d{2}[T ]\d", re.I
        ),
        ("時刻(hh:mm、2pm など。日付より細かい)", "a time of day (hh:mm, 2pm etc.; finer than a date)"),
    ),
    (
        "serial",
        re.compile(r"\b(S/?N|serial)\b|シリアル|製造番号|製品番号|個体番号", re.I),
        ("シリアル番号", "a serial number"),
    ),
    # #90 で足したもの(書き換えた・崩した書き方の個人の情報)
    (
        "encoded",
        # base64 らしい 20 字以上の続き(_Base64Like の説明。パスやメールを書き換えて隠す)
        _Base64Like(),
        ("base64 などで書き換えた文字", "base64 or other encoded text"),
    ),
    (
        "hex_id",
        # 32 桁以上の続き(ダッシュの無い UUID・個体の ID・16 進で書き換えた文字)。
        # ちょうど 40 桁(git のコミット)と 64 桁(sha256)は当てない
        re.compile(
            r"(?<![0-9A-Za-z])(?![0-9A-Fa-f]{40}(?![0-9A-Za-z]))(?![0-9A-Fa-f]{64}(?![0-9A-Za-z]))"
            r"[0-9A-Fa-f]{32,}(?![0-9A-Za-z])"
        ),
        ("32 桁以上の 16 進(UUID・個体の ID など)", "32 or more hex digits (a UUID or device ID)"),
    ),
    (
        "domain",
        # 公開の道具・モデルの置き場(PUBLIC_DOMAINS とそのサブドメイン)は当てない。ホスト名の最後が
        # その名前で終わるときだけ(nvidia.com.taro.jp・evil.github.com.attacker.jp は当てる)
        re.compile(
            r"(?<![\w.-])(?!(?:[A-Za-z0-9-]+\.)*(?:"
            + "|".join(re.escape(d) for d in PUBLIC_DOMAINS)
            + r")(?![\w-]|\.[A-Za-z0-9]))"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|net|org|jp|io|dev|me|info|biz|co|ai|app|xyz"
            r"|us|uk|de|cn|kr|tw|ru|fr|eu|tv|cc|ne|or|ac|go|ed)\b(?![.-]\w)",
            re.I,
        ),
        ("ドメイン名(example.com など)", "a domain name (example.com etc.)"),
    ),
    (
        "phone",
        re.compile(
            r"(?<![\d-])0\d{1,4}-\d{1,4}-\d{3,4}(?![\d-])|\+\d{1,3}[ -]\d{1,4}[ -]\d{2,4}[ -]?\d{0,4}"
        ),
        ("電話番号", "a phone number"),
    ),
    (
        "mac",
        re.compile(
            r"\b[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\b|\b(?:[0-9A-Fa-f]{2}\.){5}[0-9A-Fa-f]{2}\b"
        ),
        ("MAC アドレス(点で区切った形)", "a MAC address (dotted form)"),
    ),
    (
        "at_sign",
        re.compile(r"[\[(]\s*(?:at|dot)\s*[\])]|アット|あっと|\bat\s+\w+\s+dot\b", re.I),
        ("崩して書いたメールアドレス([at]・アット など)", "an obfuscated email address ([at] etc.)"),
    ),
)


# 自由な文(note)にだけ当てる種類(#90)。モデル ID(hf:Ine007/waiNSFWIllustrious_v140 など)は base64 や
# ドメインに似ることがあり、決まった形の項目は別に形と一覧で縛っているので当てない
NOTE_ONLY_KINDS = frozenset({"encoded", "hex_id", "domain", "phone"})


def normalize_for_scan(text: str) -> str:
    """検出の前にそろえる: NFKC(全角 → 半角)、見えない文字を除く、
    ¥(日本語の Windows のパスの区切り)を \\ に。"""
    t = unicodedata.normalize("NFKC", text)
    t = _ZERO_WIDTH.sub("", t)
    return t.replace("¥", "\\")


def _hits(text: str) -> list[tuple[str, str]]:
    t = normalize_for_scan(text)
    return [(kind, tr(*what)) for kind, pat, what in PRIVACY_PATTERNS if pat.search(t)]


def _identity_words() -> list[str]:
    """この PC の名前・ユーザ名・ホーム(短すぎるものは誤検出が多いので除く)。"""
    words: list[str] = []
    with contextlib.suppress(Exception):
        words.append(platform.node())
    with contextlib.suppress(Exception):
        words.append(getpass.getuser())
    with contextlib.suppress(Exception):
        words.append(str(Path.home()))
    words += [os.environ.get(k, "") for k in ("COMPUTERNAME", "USERNAME", "USER", "HOSTNAME")]
    return sorted({w for w in words if len(w) >= 4}, key=len, reverse=True)


_SEGMENT = re.compile(r"[。\n;]")


def scrub_text(text: str | None, extra: list[str] | None = None) -> str | None:
    """note から個人の情報らしいところを消す。

    文(「。」・改行・「;」で区切ったもの)ごとに見て、この PC の名前・ユーザ名・ホーム(と extra)を含む文と、
    PRIVACY_PATTERNS のどれかに当たる文を、**文ごと**落とす(空白を含むパスの続きが残らないように、パスの途中で
    切らない)。この PC の名前はパスより先に見る。落とした数を最後に書く。"""
    if text is None:
        return None
    t = CONTROL_CHARS.sub("。", normalize_for_scan(text))
    words = [normalize_for_scan(w).lower() for w in [*(extra or []), *_identity_words()] if w]
    kept: list[str] = []
    dropped = 0
    for seg in _SEGMENT.split(t):
        s = seg.strip()
        if not s:
            continue
        low = s.lower()
        if any(w in low for w in words) or _hits(s):
            dropped += 1
            continue
        kept.append(s)
    out = "。".join(kept)
    if dropped:
        out = (out + "。" if out else "") + f"(個人の情報らしい文を {dropped} 個省いた)"
    return out


def privacy_findings(data: Any, raw: str | None = None) -> list[dict[str, str]]:
    """個人の情報らしい文字の一覧 [{where, kind, what}]。値とキーを調べる。

    raw は互換のため受け取るが使わない(JSON として読めた時点で、中身はすべて値とキーにある)。
    長すぎる文字列は調べない(validate が長さの誤りにする)。"""
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def check(where: str, text: str) -> None:
        if len(text) > MAX_NOTE_CHARS:
            return
        for kind, what in _hits(text):
            if kind in NOTE_ONLY_KINDS and where != "$.measurement.note" and not _strict_place(where):
                continue  # 決まった形の項目(モデル ID・GPU 名など)は形の検査で縛っている
            if (where, kind) not in seen:
                seen.add((where, kind))
                found.append({"where": where, "kind": kind, "what": what})

    def walk(x: Any, where: str, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                key = str(k)[:MAX_KEY]
                check(f"{where}.{key} (key)", str(k))
                walk(v, f"{where}.{key}", depth + 1)
        elif isinstance(x, list):
            for i, v in enumerate(x[:100]):
                walk(v, f"{where}[{i}]", depth + 1)
        elif isinstance(x, str):
            if where == "$.measurement.files_fp" and _FILES_FP.fullmatch(x):
                return  # ファイルの sha256 から作った指紋(個人の情報ではない。形は validate が見る)
            if _FAILURE_FP.fullmatch(where) and _FILES_FP.fullmatch(x):
                return  # 失敗の記録の files_fp(同じ)
            if where == "$.db.sha256" and _DB_SHA.fullmatch(x):
                return  # 一覧の db.json の sha256 の先頭(個人の情報ではない)
            check(where, x)

    walk(data, "$")
    return found


# 配列を置ける場所(#132。失敗の記録の並びと、足りないノードの名前の並び)と、その長さの上限
_LIST_PLACES = (
    (re.compile(r"\$\.failures"), MAX_FAILURES),
    (re.compile(r"\$\.failures\[\d+\]\.missing_nodes"), MAX_MISSING_NODES),
)
_FAILURE_FP = re.compile(r"\$\.failures\[\d+\]\.files_fp")


def _list_limit(where: str) -> int | None:
    return next((n for pat, n in _LIST_PLACES if pat.fullmatch(where)), None)


def _strict_place(where: str) -> bool:
    """すべての検出(電話番号・ドメイン・符号化など)を当てる場所(#132): 失敗の記録の名前(ノード名・
    エラーの種類)と、見積もり・時間の内訳の中。モデル ID・ファイルの指紋は形と一覧で縛っているので除く。"""
    if where.endswith((".model_id", ".files_fp")):
        return False
    return where.startswith(("$.failures", "$.measurement.estimate", "$.measurement.timing"))


def string_problems(data: Any) -> list[str]:
    """制御文字・改行・見えない文字を含む文字列、長すぎる文字列・キー、配列(決まった場所のほか)、
    深すぎる入れ子(場所の一覧)。"""
    out: list[str] = []

    def walk(x: Any, where: str, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            out.append(tr(f"{where}: 入れ子が深すぎます", f"{where}: nested too deeply"))
            return
        if isinstance(x, dict):
            for k, v in x.items():
                ks = str(k)
                if len(ks) > MAX_KEY or CONTROL_CHARS.search(ks):
                    out.append(
                        tr(
                            f"{where}: キーが長すぎるか、制御文字を含みます",
                            f"{where}: a key is too long or has control characters",
                        )
                    )
                    continue
                walk(v, f"{where}.{ks}", depth + 1)
        elif isinstance(x, list):
            limit = _list_limit(where)
            if limit is None:
                out.append(tr(f"{where}: 配列は使えません", f"{where}: arrays are not allowed"))
            elif len(x) > limit:
                out.append(tr(f"{where}: {limit} 個を超えています", f"{where}: more than {limit} items"))
            else:
                for i, v in enumerate(x):
                    walk(v, f"{where}[{i}]", depth + 1)
        elif isinstance(x, str):
            limit = MAX_NOTE_CHARS if where == "$.measurement.note" else MAX_STR
            if len(x) > limit:
                out.append(
                    tr(f"{where}: {limit} 字を超えています", f"{where}: longer than {limit} characters")
                )
            elif CONTROL_CHARS.search(x):
                out.append(
                    tr(
                        f"{where}: 改行・制御文字・見えない文字を含みます",
                        f"{where}: has line breaks, control or invisible characters",
                    )
                )

    walk(data, "$")
    return out


def md_cell(value: Any) -> str:
    """Markdown の表の 1 マスに入れる文字。| ` < > [ ] & @ と改行・制御文字を無害にし、
    GFM の自動リンク(scheme://・www.)も切る(CI のまとめへの差し込み・リンクや HTML の埋め込みを防ぐ)。"""
    s = CONTROL_CHARS.sub(" ", str(value))
    for a, b in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ("|", "&#124;"),
        ("`", "&#96;"),
        ("[", "&#91;"),
        ("]", "&#93;"),
        ("://", ":&#47;&#47;"),
        ("@", "&#64;"),
    ):
        s = s.replace(a, b)
    return re.sub(r"(?i)www\.", "www&#46;", s)


# 個人の情報の点検の一覧(contrib show)。(id, (見出しの日本語, 英語), 当てはまる PRIVACY_PATTERNS の種類)
CHECKLIST: tuple[tuple[str, tuple[str, str], tuple[str, ...]], ...] = (
    (
        "paths",
        (
            "フォルダのパス(C:\\Users\\…、/home/… など)が入っていない",
            "No folder paths (C:\\Users\\…, /home/… etc.)",
        ),
        ("path", "users_dir"),
    ),
    (
        "host_user",
        ("PC の名前・ユーザ名らしい文字が入っていない", "Nothing that looks like a PC or user name"),
        ("hostname",),
    ),
    (
        "device_ids",
        (
            "GPU の UUID・PCI の位置・MAC・シリアルが入っていない",
            "No GPU UUID, PCI location, MAC or serial number",
        ),
        ("uuid", "gpu_uuid", "pci", "mac", "serial", "hex_id", "encoded"),
    ),
    (
        "network",
        (
            "IP アドレス・URL・メールアドレス・@ の名前が入っていない",
            "No IP address, URL, email address or @name",
        ),
        ("ip", "url", "at_sign", "domain", "phone"),
    ),
    ("date_only", ("日時は日付だけ(時刻が入っていない)", "Dates only (no time of day)"), ("time",)),
)


def _unknown_fields(data: dict[str, Any]) -> list[str]:
    """決まった項目に無いキーの場所(#132 で足した見積もり・時間の内訳・一覧の版・失敗の記録の中も見る)。"""
    failures = data.get("kind") == FAILURES_KIND
    unknown = sorted(set(data) - (FAILURES_TOP_KEYS if failures else TOP_KEYS))

    def sub(d: Any, allowed: set[str], where: str) -> None:
        if isinstance(d, dict):
            unknown.extend(f"{where}.{k}" for k in sorted(set(d) - allowed))

    sub(data.get("system"), SYSTEM_KEYS, "system")
    sub(data.get("db"), DB_KEYS, "db")
    system = data.get("system")
    sub(system.get("gpu") if isinstance(system, dict) else None, GPU_KEYS, "system.gpu")
    mm = data.get("measurement")
    if not failures:
        sub(mm, MEASUREMENT_KEYS, "measurement")
        if isinstance(mm, dict):
            sub(mm.get("settings"), SETTINGS_KEYS, "measurement.settings")
            sub(mm.get("estimate"), ESTIMATE_KEYS, "measurement.estimate")
            sub(mm.get("timing"), TIMING_KEYS, "measurement.timing")
    items = data.get("failures")
    if failures and isinstance(items, list):
        for i, f in enumerate(items[:MAX_FAILURES]):
            sub(f, FAILURE_KEYS, f"failures[{i}]")
            if isinstance(f, dict):
                sub(f.get("settings"), SETTINGS_KEYS, f"failures[{i}].settings")
    return unknown


def privacy_checklist(
    data: Any, raw: str | None = None, *, self_identity: bool = True
) -> list[dict[str, Any]]:
    """送る前の点検の一覧 [{id, label, ok, detail}]。
    最後の note の項目は人が読んで確かめるもの(ok は None)。"""
    found = privacy_findings(data, raw)
    text = raw if raw is not None else json.dumps(data, ensure_ascii=False)
    out: list[dict[str, Any]] = []
    unknown: list[str] = []
    if isinstance(data, dict):
        unknown = _unknown_fields(data)
    out.append(
        {
            "id": "known_fields",
            "label": tr("決まった項目だけ(知らない項目が無い)", "Known fields only (no unknown fields)"),
            "ok": not unknown,
            "detail": ", ".join(unknown) or None,
        }
    )
    for cid, label, kinds in CHECKLIST:
        hits = [f"{f['where']}({f['what']})" for f in found if f["kind"] in kinds]
        if cid == "host_user":
            # self_identity が偽(intake。GitHub の実行機の名前・ユーザ名は送った人の情報ではない)なら見ない
            own = [w for w in _identity_words() if w.lower() in text.lower()] if self_identity else []
            hits += [
                tr("この PC の名前・ユーザ名・ホーム", "this PC's name, user name or home") for _ in own[:1]
            ]
        out.append({"id": cid, "label": tr(*label), "ok": not hits, "detail": "; ".join(hits) or None})
    out.append(
        {
            "id": "note_reviewed",
            "label": tr(
                "note(メモ)を自分で読んだ(自動の点検は文の意味までは分からない)",
                "I read the note myself (the automatic check cannot understand what it says)",
            ),
            "ok": None,
            "detail": None,
        }
    )
    return out


# --- 作る -----------------------------------------------------------------------------------------


def gpu_slug(name: str) -> str:
    """GPU 名 → フォルダ名(例 'NVIDIA GeForce RTX 3070' → 'rtx-3070')。見積もりで「同じ GPU」と見る比べ方
    (estimate.gpu_key)と同じ。"""
    return gpu_key(name) or "unknown-gpu"


def canonical(data: dict[str, Any]) -> bytes:
    """内容の決まった並べ方(キーの順・空白によらない)。重複と名前の hash に使う。"""
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def content_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def is_failures(data: Any) -> bool:
    """失敗の記録のファイル(kind genmodelfit-failures。#132)か。"""
    return isinstance(data, dict) and data.get("kind") == FAILURES_KIND


def suggested_path(data: dict[str, Any]) -> str:
    """リポジトリの中の置き場所 data/contrib/<GPU>/<日付>-<sha8>.json(区切りは /)。
    失敗の記録は <日付>-<sha8>-failures.json(#132)。"""
    gpu = ((data.get("system") or {}).get("gpu") or {}).get("name") or "unknown"
    tail = FAILURES_SUFFIX if is_failures(data) else ""
    return f"{CONTRIB_DIR}/{gpu_slug(str(gpu))}/{data.get('date')}-{content_hash(data)[:8]}{tail}.json"


def _os_family(os_name: str | None) -> str | None:
    n = (os_name or "").lower()
    if n.startswith("windows"):
        return "windows"
    if n.startswith("linux"):
        return "linux"
    if n.startswith(("darwin", "macos", "mac os")):
        return "macos"
    return None


def _clean_settings(s: Settings | dict[str, Any]) -> dict[str, Any]:
    d = asdict(s) if isinstance(s, Settings) else dict(s)
    order = ("width", "height", "frames", "seconds", "steps", "precision", "batch")
    out = {k: d.get(k) for k in order if d.get(k) is not None}
    if out.get("batch") == 1:
        out.pop("batch")  # 1 枚は省く(既定)
    return out


def _system_block(
    system: SystemInfo | None,
    gpu_name: str,
    vram_gb: float,
    driver: str | None,
    backend: str | None,
    pci_id: str | None,
) -> dict[str, Any]:
    """提出の system(OS の種類・丸めたメインメモリ・GPU)。実測と失敗の記録で同じものを使う(#132)。
    GPU の世代は、同じ名前の GPU が system にあればその値、無ければ GPU 名の表から推す。"""
    arch = cc = None
    if system is not None:
        same = [g for g in system.gpus if g.name == gpu_name]
        if same:
            g = same[0]
            arch, cc, driver = g.arch, g.compute_capability, driver or g.driver
    if cc is None:
        guess = cc_from_name(gpu_name)
        if guess:
            cc, arch = f"{guess[0]}.{guess[1]}", arch or arch_of(guess)
    elif arch is None:
        parsed = parse_compute_cap(cc)
        arch = arch_of(parsed) if parsed else None
    if arch not in ARCHES:  # NVIDIA 以外の世代(RDNA 3 など)は arch に入れない(ARCHES は NVIDIA の世代だけ)
        arch = None
    backend = backend if backend in VENDOR_BACKEND.values() else None
    vendor = next((v for v, b in VENDOR_BACKEND.items() if b == backend), None)
    if vendor not in (None, "nvidia"):
        arch = cc = None  # compute capability は NVIDIA だけ
    ram = None
    os_family = None
    if system is not None:
        os_family = _os_family(system.os)
        if "ram" not in system.overrides and system.ram_total_gb:
            ram = round(system.ram_total_gb)
    return {
        "os_family": os_family,
        "ram_total_gb": ram,
        "gpu": {
            "name": gpu_name,
            "vram_gb": vram_gb,
            "arch": arch,
            "compute_capability": cc,
            "driver": driver,
            # 会社・backend は bench が記録したときだけ入れる(古い結果から作るときは入れない)
            **({"vendor": vendor, "backend": backend} if backend else {}),
            # PCI の型番 ID は bench が ComfyUI と同じ PC の検出から書いたときだけ(機種の番号だけ)
            **({"pci_id": pci_id} if pci_id else {}),
        },
    }


def spilled_gb(peak_vram_gb: Any, gpu_vram_gb: Any) -> float | None:
    """ピークの VRAM が GPU の総量を超えた量(GiB。#132)。超えていない・分からなければ None。
    超えたとみなすのは、検査が総量超えを誤りにする線(総量 × 1.02 + 0.25 GB)を超えたとき。"""
    if not (_num(peak_vram_gb) and _num(gpu_vram_gb)) or gpu_vram_gb <= 0:
        return None
    if peak_vram_gb <= gpu_vram_gb * PEAK_VRAM_RATIO + VRAM_TOLERANCE_GB:
        return None
    return round(peak_vram_gb - gpu_vram_gb, 2)


def build_submission(
    m: Measurement,
    system: SystemInfo | None = None,
    *,
    extra_secrets: list[str] | None = None,
    estimate: dict[str, Any] | None = None,
    timing: dict[str, Any] | None = None,
    db_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """bench の実測 1 件 → 提出用の JSON(contrib_schema 3)。

    system は測った PC(ComfyUI が同じ PC のときだけ渡す)。OS の種類・メインメモリ・GPU の世代を取る。
    source(自由に書ける文)は入れない。note は個人の情報らしいところを消して入れる。
    estimate(measurement_estimate)・timing(bench の BenchResult.timing)・db_info(db_info)は
    渡したときだけ入れる(#132)。最後まで動いた(ok)のにピークの VRAM が GPU の総量を超えたら
    spilled と超えた量を付ける(共有メモリへ逃げた。見積もり・段階には使わない)。"""
    note = scrub_text(m.note, extra_secrets)
    if note and len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 1] + "…"
    spill = spilled_gb(m.peak_vram_gb, m.gpu_vram_gb) if m.outcome == "ok" else None
    return {
        "contrib_schema": CONTRIB_SCHEMA,
        "kind": KIND,
        "tool": {"name": "genmodelfit", "version": __version__},
        "date": m.date or datetime.now(timezone.utc).date().isoformat(),
        "system": _system_block(
            system, m.gpu_name, m.gpu_vram_gb, m.driver, m.gpu_backend, getattr(m, "gpu_pci_id", None)
        ),
        "comfyui_version": m.comfyui_version,
        **({"db": dict(db_info)} if db_info else {}),
        "measurement": {
            "model_id": m.model_id,
            "settings": _clean_settings(m.settings),
            "outcome": m.outcome,
            "standard": bool(m.standard),
            "offload": m.offload,
            "peak_vram_gb": m.peak_vram_gb,
            "peak_ram_gb": m.peak_ram_gb,
            "seconds": m.seconds,
            "vram_metric": m.vram_metric,
            "note": note,
            # 測った時点の一覧のファイルの指紋(contrib_schema 2。#90)。分からなければ入れない
            **({"files_fp": m.files_fp} if getattr(m, "files_fp", None) else {}),
            # テンプレートの設定を変えて測ったときだけ(#116)
            **({"settings_override": True} if getattr(m, "settings_override", None) else {}),
            **({"estimate": dict(estimate)} if estimate else {}),
            **({"timing": {k: timing.get(k) for k in TIMING_ORDER}} if timing else {}),
            **({"spilled": True, "spilled_gb": spill} if spill else {}),
        },
    }


TIMING_ORDER = (
    "queue_s",
    "load_s",
    "sampling_s",
    "sec_per_step",
    "decode_s",
    "save_s",
    "total_s",
    "cold_load",
)


def clean_error_type(value: Any) -> str | None:
    """例外の型名・ComfyUI の node_errors の type(#132)。決まった形(英数字・_・.)のものだけ。
    本文(パスが入りうる)は受け取らない。形に合わない・個人の情報らしい文字があれば None。"""
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v if ERROR_TYPE.fullmatch(v) and not _identity_hit(v) else None


def clean_node_name(value: Any) -> str | None:
    """ノードの名前(#132)。決まった形(NODE_NAME)で、個人の情報らしい文字の無いものだけ。"""
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v if NODE_NAME.fullmatch(v) and not _identity_hit(v) else None


def _identity_hit(text: str) -> bool:
    """個人の情報らしい文字(PRIVACY_PATTERNS)か、この PC の名前・ユーザ名を含むか。"""
    low = normalize_for_scan(text).lower()
    return bool(_hits(text)) or any(normalize_for_scan(w).lower() in low for w in _identity_words())


def failure_record(
    model_id: str,
    outcome: str,
    *,
    settings: Settings | dict[str, Any] | None = None,
    seconds: float | None = None,
    timeout_s: float | None = None,
    missing_nodes: list[str] | None = None,
    error_type: Any = None,
    node_type: Any = None,
    peak_vram_gb: float | None = None,
    before_run: bool = False,
    files_fp: str | None = None,
) -> dict[str, Any] | None:
    """失敗の記録 1 件(#132)。送らない失敗(FAILURE_OUTCOMES の外: interrupted・interference など)は None。
    文は入れない: 型名・ノードの名前は決まった形のものだけ残す(clean_error_type・clean_node_name)。"""
    if outcome not in FAILURE_OUTCOMES:
        return None
    rec: dict[str, Any] = {"model_id": model_id, "outcome": outcome}
    if settings:
        rec["settings"] = _clean_settings(settings)
    rec["seconds"] = round(float(seconds), 1) if _num(seconds) and seconds > 0 else None
    if outcome == "timeout" and _num(timeout_s) and timeout_s > 0:
        rec["timeout_s"] = round(float(timeout_s), 1)
    if outcome == "missing_nodes":
        given = [x for x in missing_nodes or [] if x]
        names = sorted({n for n in (clean_node_name(x) for x in given) if n})[:MAX_MISSING_NODES]
        other = len({str(x) for x in given}) - len(names)
        if not names and other <= 0:
            return None  # 足りないノードが分からない記録は作らない
        rec["missing_nodes"] = names
        if other > 0:
            rec["other_nodes"] = other
    if outcome in ("error", "oom"):
        rec["error_type"] = clean_error_type(error_type)
        rec["node_type"] = clean_node_name(node_type)
    if _num(peak_vram_gb) and peak_vram_gb > 0 and outcome in ("error", "oom", "timeout"):
        rec["peak_vram_gb"] = round(float(peak_vram_gb), 2)
    if before_run:
        rec["before_run"] = True
    if isinstance(files_fp, str) and _FILES_FP.fullmatch(files_fp):
        rec["files_fp"] = files_fp
    return rec


def build_failures(
    records: list[dict[str, Any] | None],
    system: SystemInfo | None,
    *,
    gpu_name: str,
    gpu_vram_gb: float,
    driver: str | None = None,
    backend: str | None = None,
    pci_id: str | None = None,
    comfyui_version: str | None = None,
    db_info: dict[str, Any] | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """1 回の share・1 つの GPU の失敗の記録のファイル(kind genmodelfit-failures、contrib_schema 3。#132)。
    records は failure_record の結果(None は除く)。多すぎれば MAX_FAILURES 件まで。PC の概要(system)は
    実測の提出と同じ範囲。見積もりの式には使わない(何を勧めるか・要る ComfyUI の版を決める材料)。"""
    items = [r for r in records if r][:MAX_FAILURES]
    cv = comfyui_version if isinstance(comfyui_version, str) and _VERSION.fullmatch(comfyui_version) else None
    doc = {
        "contrib_schema": CONTRIB_SCHEMA,
        "kind": FAILURES_KIND,
        "tool": {"name": "genmodelfit", "version": __version__},
        "date": date or datetime.now(timezone.utc).date().isoformat(),
        "system": _system_block(system, gpu_name, gpu_vram_gb, driver, backend, pci_id),
        "comfyui_version": cv,
        **({"db": dict(db_info)} if db_info else {}),
        "failures": items,
    }
    # 大きさの上限(MAX_FILE_BYTES)に収まるまで、後ろの記録から外す(1 件は残す)
    while len(dumps(doc).encode("utf-8")) > MAX_FILE_BYTES - 1024 and len(doc["failures"]) > 1:
        doc["failures"].pop()
    return doc


_DB_INFO: dict[tuple, dict[str, Any]] = {}


def db_info(db: Database | None) -> dict[str, Any] | None:
    """一覧の版(#132): {generated_at: 作った日(日付だけ), sha256: db.json の sha256 の先頭 12 桁}。
    sha256 は同梱・キャッシュの一覧のときだけ(ファイルを読めたとき)。分からない項目は入れない。"""
    if db is None:
        return None
    key = (db.origin, db.generated_at)
    if key not in _DB_INFO:
        out: dict[str, Any] = {}
        gen = db.generated_at
        if isinstance(gen, str) and _DATE.match(gen):
            out["generated_at"] = gen[:10]
        raw: bytes | None = None
        with contextlib.suppress(OSError, ValueError):
            from .db import bundled_text, cached_db_path

            if db.origin == "bundled":
                raw = bundled_text("db.json").encode("utf-8")
            elif db.origin == "cache":
                raw = cached_db_path().read_bytes()
        if raw is not None:
            out["sha256"] = hashlib.sha256(raw).hexdigest()[:12]
        _DB_INFO[key] = out
    return dict(_DB_INFO[key]) or None


def measurement_estimate(
    entry: Any,
    system: SystemInfo | None,
    settings: Settings | None,
    measurements: list[Measurement],
    *,
    exclude_gpu: str | None = None,
) -> dict[str, Any] | None:
    """測ったモデル・設定に対する、その時点の genmodelfit の見積もり(#132。判定の計算は変えない)。

    measurements には、その実測と、同じ PC のほかの実測を入れない(入れると照合にならない。呼び出し側は
    同梱の実測だけを渡す: db.load_measurements(include_cache=False))。system が無い(ComfyUI が別の PC)・
    大きさが分からない・計算できないときは None。exclude_gpu(測った GPU の名前)を渡すと、同じ GPU・
    同じモデルの実測も除く(同梱の実測は作者の PC のものなので、同じ PC の実測かもしれない)。"""
    from .estimate import (
        apply_precision,
        components,
        estimate,
        formula_need,
        load_coefficients,
        same_gpu_name,
        settings_for,
    )
    from .measure import time_hint
    from .recommend import _how, vram_need

    if system is None or entry is None:
        return None
    if exclude_gpu:
        mid = getattr(entry, "id", None)
        measurements = [
            x for x in measurements if not (x.model_id == mid and same_gpu_name(x.gpu_name, exclude_gpu))
        ]
    try:
        c = load_coefficients()
        e = estimate(entry, system, settings, measurements, c)
        if e.unknown_size:
            return None
        s = settings_for(entry, settings, sized=not no_size_input(entry))
        n = formula_need(entry, s, c, apply_precision(components(entry), entry, s))
        secs, _basis = time_hint(entry, measurements, settings)
        need = vram_need(e, system)
        mode = e.run_mode
        if mode == "gpu" and e.fit_from != "measurement" and system.vram_gb > 0:
            mode = "sequential" if _how(e, system.vram_gb, system)[1] == "seq" else "gpu"
    except (TypeError, ValueError, ZeroDivisionError, AttributeError, KeyError):
        return None

    def r2(x: Any) -> float | None:
        return round(float(x), 2) if _num(x) else None

    return {
        "fit_level": e.fit if e.fit in ESTIMATE_LEVELS else None,
        "run_mode": mode if mode in ESTIMATE_MODES else None,
        "vram_gb": r2(need),
        "vram_resident_gb": r2(e.vram_resident_gb),
        "work_gb": r2(n.act),
        "ram_required_gb": r2(e.ram_gb),
        "seconds": float(secs) if _num(secs) else None,
        "basis": e.basis if e.basis in ESTIMATE_BASES else None,
        **({"measurements": ESTIMATE_SOURCES[0]} if exclude_gpu else {}),
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def write_submission(data: dict[str, Any], target: str | os.PathLike[str]) -> Path:
    """書き出す。target が既にあるフォルダ(または / で終わる)なら <target>/<GPU>/<日付>-<sha8>.json、
    それ以外はそのファイル名で書く。書いた場所を返す。"""
    t = Path(target)
    if t.is_dir() or str(target).endswith(("/", "\\")):
        rel = suggested_path(data).split("/", 2)[2]  # data/contrib/ を除いた <GPU>/<名前>
        t = t / rel
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(dumps(data), encoding="utf-8", newline="\n")
    return t


# --- ブラウザで送る(share。#90・#113) -------------------------------------------------------------
# genmodelfit は何も送らない。GitHub の Issue のフォーム(実測の提出用。中身を入れたもの)の URL を作るだけで、
# 開くのは CLI、送るのはブラウザで利用者が「Create」を押したとき。受け取った Issue は
# .github/workflows/measurement-intake.yml が intake_issue で検査し、通れば data/contrib に足す PR を作る

REPO = "laxshmate/genmodelfit"  # 提出を送るリポジトリ(share の URL はすべてここから作る)
ISSUE_TEMPLATE = "measurement.yml"  # .github/ISSUE_TEMPLATE/ のフォーム
ISSUE_FIELD = "submission"  # フォームの JSON の欄の id(URL の ?submission= で中身を入れる)
ISSUE_LABEL = "measurement"  # フォームが付ける印(受け取る仕組みはこの印の Issue だけを見る)
# 中身を入れた URL の長さの上限(文字)。ブラウザ・GitHub が受け付ける長さには公の上限が無く、
# 長い URL は途中で切られることがあるので控えめにした。超えたら中身を入れないフォーム(貼り付ける)を開く
SHARE_URL_LIMIT = 8000


def share_check(checklist: list[dict[str, Any]], report: Report) -> list[str]:
    """送ってよいか。断る理由の一覧(空なら送ってよい)。個人の情報の点検の [NG] と、検査の誤り(errors)で断る。
    注意(warnings)・確認(notices)・利用者が読む note(ok が None)では断らない。"""
    why = [
        f"{c['label']}" + (f": {c['detail']}" if c.get("detail") else "")
        for c in checklist
        if c["ok"] is False
    ]
    why += [f"{e['code']}: {e['message']}" for e in report.errors]
    return why


def issue_title(data: dict[str, Any]) -> str:
    """Issue の題(モデル ID と GPU 名。どちらも検査を通った形の文字)。失敗の記録は件数と GPU 名(#132)。"""
    gpu = ((data.get("system") or {}).get("gpu") or {}).get("name")
    if is_failures(data):
        items = data.get("failures") if isinstance(data.get("failures"), list) else []
        return f"measurement failures: {len(items)} on {gpu}"
    mm = data.get("measurement") if isinstance(data.get("measurement"), dict) else {}
    return f"measurement: {mm.get('model_id')} on {gpu}"


def share_urls(data: dict[str, Any], text: str) -> dict[str, Any]:
    """GitHub で提出を送る Issue のフォームの URL(通信しない。#113)。

    - issue_url: 実測の提出用のフォーム(ISSUE_TEMPLATE)に、題と中身(text。改行は LF にそろえる)を入れたもの。
      SHARE_URL_LIMIT を超えたら None(form_url を開き、中身は貼り付ける)
    - form_url: 同じフォームで、題だけを入れたもの
    path_in_repo は受け付けたときのリポジトリの中の置き場所(PR は受け取る仕組みが作る)。"""
    from urllib.parse import quote

    body = text.replace("\r\n", "\n")
    title = issue_title(data)
    form = f"https://github.com/{REPO}/issues/new?template={ISSUE_TEMPLATE}&title={quote(title, safe='')}"
    full = f"{form}&{ISSUE_FIELD}={quote(body, safe='')}"
    return {
        "path_in_repo": suggested_path(data),
        "title": title,
        "issue_url": full if len(full) <= SHARE_URL_LIMIT else None,
        "issue_url_length": len(full),
        "url_limit": SHARE_URL_LIMIT,
        "form_url": form,
    }


# --- Issue で受け取る(measurement-intake.yml。#113) ---------------------------------------------

_FENCE = re.compile(r"^(?P<fence>`{3,}|~{3,})[^\n`]*\n(?P<body>.*?)^(?P=fence)[ \t]*$", re.M | re.S)
_USER_ID = re.compile(r"\d{1,20}")  # GitHub のアカウントの数の ID
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
_REPO_PATH = re.compile(r"data/contrib/[a-z0-9-]+/\d{4}-\d{2}-\d{2}-[0-9a-f]{8}(-failures)?\.json")


def extract_issue_json(body: str | None) -> str | None:
    """Issue の本文から提出の JSON の文字を取り出す(データとしてだけ扱う。何も実行しない)。
    フォームの JSON の欄は ```json の囲みになる。{ で始まる最初の囲みの中身。囲みが無ければ、本文全体が
    { で始まり } で終わるときだけその文字。見つからなければ None。"""
    text = (body or "").replace("\r\n", "\n")
    for m in _FENCE.finditer(text):
        inner = m.group("body").strip()
        if inner.startswith("{"):
            return inner
    t = text.strip()
    return t if t.startswith("{") and t.endswith("}") else None


@dataclass
class IntakeResult:
    """Issue 1 件の受け付けの結果。ok なら path_in_repo に content を足す PR を作る。"""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    path_in_repo: str | None = None
    content: str | None = None
    sha256: str | None = None
    title: str = ""
    commit_message: str = ""
    pr_body: str = ""
    comment: str = ""
    comment_updated: str = ""  # PR ができたあとに Issue の中身が変わったとき(PR と Issue に書く)
    comment_pr_closed: str = ""  # PR ができたあとに通らない中身に変わったとき(PR に書いて閉じる)
    # 断った理由の組の印(コメントの最後に隠して書く HTML のコメント)。同じ理由で何度も編集されたとき、
    # measurement-intake は前のボットのコメントにこの印があればコメントを書かない
    marker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _md(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _parse_issue_json(text: str, res: IntakeResult) -> Any:
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        res.reasons.append(
            tr(f"大きすぎます({MAX_FILE_BYTES} バイトまで)", f"Too large (up to {MAX_FILE_BYTES} bytes)")
        )
        return None
    try:
        return loads_strict(text)
    except RecursionError:
        res.reasons.append(tr("入れ子が深すぎます", "Nested too deeply"))
    except DuplicateKeyError as e:
        res.reasons.append(str(e))
    except ValueError as e:
        res.reasons.append(tr(f"JSON ではありません({type(e).__name__})", f"Not JSON ({type(e).__name__})"))
    return None


def intake_issue(
    body: str | None,
    author: str,
    number: int,
    *,
    db: Database | None = None,
    existing: str | os.PathLike[str] | None = None,
    today: _date | None = None,
    prior: int | None = None,
    author_id: int | str | None = None,
) -> IntakeResult:
    """実測の Issue(#113)を検査し、PR に入れる中身と、Issue へのコメントを作る(通信しない・何も書かない)。

    本文はデータとしてだけ扱う(extract_issue_json)。検査は contrib validate と同じ(validate_submission。
    ファイル名はここで決めるので見ない)と、個人の情報の点検(share_check)。大きさは MAX_FILE_BYTES まで。
    existing(リポジトリの data/contrib)に同じ内容の提出があれば断る。PR に入れる中身は、取り出した文字を
    そのまま使わず dumps で書き直したもの(名前の sha8 は canonical の hash なので変わらない)。
    出どころは送った人(submission_sources)。コミットに `Submitted-by: github:<login>` を書き(ボットのコミット
    だけ信じる)、PR の本文に Issue の番号と書いた人、prior(この人が同じ GPU・モデルで既に送った数。
    submitter_count)を書く。コメントに入れる理由の文は md_cell で
    無害にする(提出から来る文字でリンク・@ の呼び出し・HTML を作らない)。"""
    if not _LOGIN.fullmatch(author or ""):
        author = "unknown"
    res = IntakeResult(ok=False)
    text = extract_issue_json(body)
    data: Any = None
    if text is None:
        res.reasons.append(
            tr(
                "本文に提出の JSON(```json の囲み)が見つかりません",
                "No submission JSON (a ```json block) was found in the body",
            )
        )
    else:
        data = _parse_issue_json(text, res)
    if text is not None and data is not None:
        report = validate_submission(data, db, raw=text, today=today, check_filename=False)
        if isinstance(data, dict):
            res.reasons += share_check(privacy_checklist(data, text, self_identity=False), report)
        else:
            res.reasons += [f"{e['code']}: {e['message']}" for e in report.errors]
        res.notices = [f"{v['code']}: {v['message']}" for v in [*report.warnings, *report.notices]]
        if not res.reasons and isinstance(data, dict):
            sha, path = content_hash(data), suggested_path(data)
            if not _REPO_PATH.fullmatch(path):
                res.reasons.append(tr("置き場所の名前を作れません", "Cannot make the file name"))
            elif existing is not None and _already_there(existing, sha):
                res.reasons.append(
                    tr("同じ内容の提出が既にあります", "The same submission is already in the repository")
                )
            else:
                res.ok, res.sha256, res.path_in_repo = True, sha, path
                res.content = dumps(data)
                res.title = issue_title(data)
    if not res.ok:
        res.marker = reasons_marker(res.reasons)
        res.comment = _md(
            [
                tr("**実測の提出を受け付けられませんでした**", "**The measurement could not be accepted**"),
                "",
                *(f"- {md_cell(r)}" for r in res.reasons),
                "",
                tr(
                    "直した中身で Issue の本文を編集すると、もう一度確かめます"
                    "(中身は genmodelfit share で作り直せます)。",
                    "Edit the issue body with the fixed content and it is checked again"
                    " (genmodelfit share makes the content again).",
                ),
                tr(
                    "どう直せばよいか分からないときは、このまま返信してください。メンテナが手伝います。",
                    "Not sure how to fix it? Just reply here and a maintainer will help.",
                ),
                "",
                res.marker,
            ]
        )
        res.comment_pr_closed = _md(
            [
                tr(
                    f"Issue #{number} の中身が、検査に通らない内容に変わったので、この PR を閉じます。"
                    "Issue を直すと、もう一度開きます。",
                    f"The content of issue #{number} changed and no longer passes the checks, so this PR is"
                    " closed. Fixing the issue reopens it.",
                ),
                "",
                *(f"- {md_cell(r)}" for r in res.reasons),
            ]
        )
        return res
    uid = str(author_id) if author_id is not None and _USER_ID.fullmatch(str(author_id)) else None
    res.commit_message = (
        f"data(contrib): {res.title}\n\nFrom issue #{number} by {author} (measurement-intake).\n\n"
        + (f"Submitted-by: github:{author}\n" if author != "unknown" else "")
        # アカウントの数の ID(名前を変えても変わらない)。出どころはこれで数える(submission_origins)
        + (f"Submitted-by-id: {uid}\n" if uid and author != "unknown" else "")
    )
    if prior:
        prior_line = tr(
            f"- @{author} さんが同じ GPU・モデルで既に送った提出: {prior} 件"
            "(同じ人の提出は合わせて 1 つの出どころ)",
            f"- Submissions @{author} already sent for this GPU and model: {prior} (one person counts as one"
            " source)",
        )
    elif prior is None:
        prior_line = tr(
            "- 同じ人の前の提出の数: git の履歴が無いので数えていない",
            "- Earlier submissions by the same person: not counted (no git history)",
        )
    else:
        prior_line = tr(
            f"- @{author} さんの、この GPU・モデルの最初の提出",
            f"- The first submission by @{author} for this GPU and model",
        )
    res.pr_body = _md(
        [
            tr(
                f"Issue #{number} で @{author} さんが送った実測です"
                "(measurement-intake が検査して作りました)。",
                f"A measurement sent by @{author} in issue #{number}"
                " (checked and opened by measurement-intake).",
            ),
            "",
            f"- `{res.path_in_repo}`",
            "- "
            + tr(
                "検査: contrib validate と個人の情報の点検を通過",
                "Checks: passed contrib validate and the privacy checks",
            ),
            prior_line,
            "- [ ] "
            + tr(
                "マージの前に: Issue を書いた人と、同じ人が別のアカウント・名前を変える前のアカウントで"
                "この実測を既に送っていないかを確かめる(出どころはアカウントごとに数える)",
                "Before merging: check who wrote the issue, and whether the same person already sent this"
                " measurement from another account or under an earlier name (sources are counted per"
                " account)",
            ),
            *(
                [
                    "",
                    tr("**マージする人への確認**", "**For the maintainer who merges**"),
                    *(f"- {md_cell(n)}" for n in res.notices),
                ]
                if res.notices
                else []
            ),
            "",
            f"Closes #{number}",
        ]
    )
    res.comment = _md(
        [
            tr("**受け付けました。ありがとうございます**", "**Accepted — thank you!**"),
            "",
            tr(
                "確かめて問題が無かったので、プロジェクトに入れる準備(Pull Request)を作りました(リンクは下)。"
                "メンテナが入れると、この Issue は閉じます。",
                "It passed the checks, so the bot prepared it to be added to the project (a pull request,"
                " link below). This issue closes when a maintainer adds it.",
            ),
            tr(
                "このあと Issue の本文を編集すると、その中身でもう一度確かめ、Pull Request も直します。",
                "If you edit the issue body later, the new content is checked again and the pull request is"
                " updated.",
            ),
        ]
    )
    res.comment_updated = _md(
        [
            tr(
                f"Issue #{number} の中身が変わったので、新しい中身で確かめて Pull Request を直しました。",
                f"The content of issue #{number} changed; the new content passed the checks and the pull"
                " request was updated.",
            ),
            "",
            f"- `{res.path_in_repo}`",
        ]
    )
    return res


def reasons_marker(reasons: list[str]) -> str:
    """断った理由の組の印(HTML のコメント。表示されない)。理由の文(今の言語)の組の sha256 の先頭 16 桁。"""
    digest = hashlib.sha256("\n".join(sorted(reasons)).encode("utf-8")).hexdigest()[:16]
    return f"<!-- measurement-intake:reasons={digest} -->"


def _already_there(root: str | os.PathLike[str], sha: str) -> bool:
    for p in find_files(root):
        with contextlib.suppress(OSError, ValueError):
            data, _raw = read_submission(p)
            if isinstance(data, dict) and content_hash(data) == sha:
                return True
    return False


def measurements_from_file(path: str | os.PathLike[str]) -> list[Measurement]:
    """bench が保存した JSON({"measurements": [...]})か、`bench --json` の出力({"measurement": {...}})。"""
    p = Path(path)
    data = loads_strict(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("measurement"), dict):
        items = [data["measurement"]]
    elif isinstance(data, dict) and isinstance(data.get("measurements"), list):
        items = data["measurements"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(
            tr(
                "bench の結果の形ではありません(measurements / measurement がない)",
                "Not a bench result (no measurements / measurement)",
            )
        )
    out = []
    for d in items:
        if not isinstance(d, dict) or "model_id" not in d or "gpu_name" not in d:
            raise ValueError(
                tr(
                    "bench の結果の形ではありません(model_id / gpu_name がない)",
                    "Not a bench result (no model_id / gpu_name)",
                )
            )
        out.append(Measurement.from_dict(d))
    return out


def to_measurement(data: dict[str, Any]) -> Measurement:
    """提出 1 件 → Measurement(まとめる前の 1 件)。最大メモリは、提出のメインメモリ(丸めた GiB)× 1.05 + 1 GiB
    (validate の上限と同じ)で切り詰める(見積もりの ram_gpu に入るため。0.9.0)。"""
    gpu = data["system"]["gpu"]
    mm = data["measurement"]
    pram = mm.get("peak_ram_gb")
    ram = (data.get("system") or {}).get("ram_total_gb")
    if _num(pram) and _num(ram) and ram > 0:
        pram = min(pram, ram * 1.05 + RAM_TOLERANCE_GB)
    return Measurement(
        model_id=mm["model_id"],
        gpu_name=gpu["name"],
        gpu_vram_gb=float(gpu["vram_gb"]),
        peak_vram_gb=float(mm["peak_vram_gb"]),
        settings=Settings.from_dict(mm.get("settings") or {}),
        peak_ram_gb=pram,
        seconds=mm.get("seconds"),
        driver=gpu.get("driver"),
        comfyui_version=data.get("comfyui_version"),
        offload=mm.get("offload"),
        source=ORIGIN_COMMUNITY,
        date=data.get("date"),
        note=mm.get("note"),
        outcome=mm.get("outcome", "ok"),
        standard=bool(mm.get("standard", True)),
        vram_metric=mm.get("vram_metric"),
        origin=ORIGIN_COMMUNITY,
        gpu_backend=gpu.get("backend"),
        gpu_pci_id=gpu.get("pci_id"),
        files_fp=mm.get("files_fp"),
        settings_override=True if mm.get("settings_override") is True else None,
    )


# --- 検査 -----------------------------------------------------------------------------------------


@dataclass
class Report:
    """1 ファイルの検査の結果。errors(機械的な検査: 形・値の範囲・個人の情報・重複・ファイル名)があれば
    受け付けない。warnings は受け付けるが提出した人に知らせる。notices も受け付ける(失敗にしない)が、
    マージするメンテナが見るもの(式から大きく外れる・段階を変える・一覧に無いモデルなど。
    CI のジョブのまとめに出す)。2026-10-22 に llmfit に合わせた
    (それまでの「人が確かめるまで受け付けない」review と REVIEWED は外した)。"""

    path: str
    ok: bool = True
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    notices: list[dict[str, str]] = field(default_factory=list)
    sha256: str | None = None
    suggested_path: str | None = None
    model_id: str | None = None
    gpu_name: str | None = None

    def error(self, code: str, message: str) -> None:
        self.errors.append({"code": code, "message": message})
        self.ok = False

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    def notice(self, code: str, message: str) -> None:
        self.notices.append({"code": code, "message": message})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(x: Any) -> bool:
    """有限の数か(NaN・inf は数とみなさない)。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def number_problems(data: Any) -> list[str]:
    """NaN・inf・負の数の場所(提出のどの数も有限で 0 以上。0.9.0)。"""
    out: list[str] = []

    def walk(x: Any, where: str) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{where}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x[:100]):
                walk(v, f"{where}[{i}]")
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            if not math.isfinite(x):
                out.append(
                    tr(
                        f"{where} が有限の数ではありません(NaN・inf)",
                        f"{where} is not a finite number (NaN or inf)",
                    )
                )
            elif x < 0:
                out.append(tr(f"{where} が負の数です: {x!r}", f"{where} is negative: {x!r}"))

    walk(data, "$")
    return out


def _no_constants(name: str) -> Any:
    raise ValueError(
        tr(
            f"JSON に {name} があります(NaN・Infinity は受け付けません)",
            f"The JSON has {name} (NaN and Infinity are not accepted)",
        )
    )


class DuplicateKeyError(ValueError):
    """同じオブジェクトの中に同じキーが 2 回ある(後の値が黙って勝つので、見た中身と使う中身が変わる)。"""


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise DuplicateKeyError(
                tr(f"同じキー {k!r} が 2 回あります", f"The key {k!r} appears twice in one object")
            )
        out[k] = v
    return out


def loads_strict(text: str) -> Any:
    """JSON を読む。NaN・Infinity・-Infinity と、同じオブジェクトの中の重複したキーは断る
    (Python の json は既定で読んでしまう)。"""
    return json.loads(text, parse_constant=_no_constants, object_pairs_hook=_no_duplicates)


def name_vendor(name: str) -> str | None:
    """GPU 名から分かる会社(分からなければ None。estimate.gpu_vendor と違い NVIDIA を既定にしない)。"""
    n = (name or "").lower()
    if re.search(r"nvidia|geforce|quadro|tesla|\brtx\b|\bgtx\b", n):
        return "nvidia"
    if "radeon" in n or n.startswith("amd") or "instinct" in n:
        return "amd"
    if n.startswith("intel") or re.search(r"\barc\b", n):
        return "intel"
    if n.startswith("apple") or n == "mps":
        return "apple"
    return None


def _check_keys(r: Report, d: Any, allowed: set[str], where: str, required: set[str]) -> bool:
    if not isinstance(d, dict):
        r.error("schema", tr(f"{where} はオブジェクトにしてください", f"{where} must be an object"))
        return False
    for k in sorted(set(d) - allowed):
        r.error(
            "unknown_field",
            tr(
                f"{where}.{k} は知らない項目です(PC の情報が紛れないよう、決まった項目だけ受け付けます)",
                f"{where}.{k} is an unknown field (only known fields are accepted, so no PC details slip in)",
            ),
        )
    for k in sorted(required - set(d)):
        r.error("schema", tr(f"{where}.{k} がありません", f"{where}.{k} is missing"))
    return True


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DRIVER = re.compile(r"\d{2,4}(\.\d{1,3}){1,3}")
_VERSION = re.compile(r"v?\d{1,3}\.\d{1,3}\.\d{1,4}")  # ComfyUI の版(厳密な x.y.z。後ろに何も付けない)
_TOOL_VERSION = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,4}((a|b|rc|\.dev|\.post)\d{1,4})?")
# GPU の名前: 英数字・空白・. - と、ComfyUI・PyTorch・Windows の名前に入る (R)・(TM)・™・® だけ
# と、Intel の内蔵 GPU の名前に付く大きさ「(16GB)」(「Intel(R) Arc(TM) 140V GPU (16GB)」)、
# NVIDIA の括弧の世代名「(Pascal)」(「NVIDIA TITAN X (Pascal)」。世代の名前だけ。#101)
# (ほかの括弧・記号・自由な文は入れられない。0.9.0 で (R) などを許した)
_GENERATION_WORD = r"\((?i:pascal|maxwell|kepler|volta|turing|ampere|ada|hopper|blackwell)\)"
_GPU_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9 .-]|\((?:R|TM|r|tm)\)|\(\d{1,3} ?GB\)|" + _GENERATION_WORD + r"|[™®]){1,63}"
)
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,199}")
_PRECISION = re.compile(r"[A-Za-z0-9_-]{1,24}")
# 式の見積もりから 40% より外れた値は注意(マージするメンテナが見る)。
# 見積もりで設定の違う推定へ広げる範囲と同じ
NOTICE_DEVIATION = COMMUNITY_DEVIATION
_CC = re.compile(r"\d{1,2}\.\d")
_FILENAME = re.compile(r"(\d{4}-\d{2}-\d{2})-([0-9a-f]{8})(-failures)?\.json")


def _version_tuple(v: Any) -> tuple[int, ...] | None:
    m = re.match(r"v?(\d{1,3})\.(\d{1,3})\.(\d{1,4})", v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def _check_versions(r: Report, data: dict[str, Any], db: Database | None) -> None:
    """版の範囲の確認(失敗にしない。#90): 検査する genmodelfit より新しい版で作った提出、テンプレートが求める
    ComfyUI より古い版・一覧が知る最新より新しい ComfyUI の版で測った提出。"""
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    tv, mine = _version_tuple(tool.get("version")), _version_tuple(__version__)
    if tv and mine and tv > mine:
        r.notice(
            "tool_newer",
            tr(
                f"検査している genmodelfit({__version__})より新しい版({tool.get('version')})で作った提出です",
                f"Made by a newer genmodelfit ({tool.get('version')})"
                f" than the one checking it ({__version__})",
            ),
        )
    cv = _version_tuple(data.get("comfyui_version"))
    if cv is None or db is None:
        return
    mm = data.get("measurement") if isinstance(data.get("measurement"), dict) else {}
    entry = db.get(mm.get("model_id")) if isinstance(mm.get("model_id"), str) else None
    need = _version_tuple(getattr(getattr(entry, "template", None), "min_comfyui_version", None))
    if need and cv < need:
        r.notice(
            "comfyui_old",
            tr(
                f"ComfyUI {data.get('comfyui_version')} はテンプレートが求める版"
                f"({'.'.join(map(str, need))} 以上)より古いです",
                f"ComfyUI {data.get('comfyui_version')} is older than the template needs"
                f" ({'.'.join(map(str, need))} or later)",
            ),
        )
    latest = _version_tuple(((db.sources or {}).get("comfyui") or {}).get("latest_release"))
    if latest and cv[:2] > (latest[0], latest[1] + 5):
        r.notice(
            "comfyui_newer",
            tr(
                f"ComfyUI {data.get('comfyui_version')} は一覧が知る最新({'.'.join(map(str, latest))})より"
                "ずっと新しい版です",
                f"ComfyUI {data.get('comfyui_version')} is much newer than the latest the list knows"
                f" ({'.'.join(map(str, latest))})",
            ),
        )


def gpu_known(name: str | None, vendor: str | None = None) -> bool:
    """判定に使える GPU 名か(#90、#101)。断るのは名前の代わりの値(空・n/a・none・null・unknown・-)と汎用の名前
    (NVIDIA Graphics Device など。estimate.generic_gpu_name)だけ。名前の表には頼らない
    (表に無い新しい GPU の人を締め出さない。作った名前は同じ文字列を返す PC にしか当たらない:
    estimate.same_gpu)。AMD・Intel・Apple は今までどおり(PCI の型番 ID・チップ名で照合するので、
    ここでは断らない)。"""
    if not isinstance(name, str) or not name.strip():
        return False
    if (vendor or name_vendor(name) or "nvidia") != "nvidia":
        return True
    return not generic_gpu_name(name)


def validate_submission(
    data: Any,
    db: Database | None = None,
    *,
    path: str | os.PathLike[str] | None = None,
    raw: str | None = None,
    today: _date | None = None,
    check_filename: bool = True,
) -> Report:
    """提出 1 件を検査する。db を渡すと、モデル ID が一覧にあるか・設定が種類に合うかも見る。"""
    r = Report(path=Path(path).as_posix() if path is not None else "-")
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(data, dict):
        r.error("schema", tr("JSON のオブジェクトではありません", "Not a JSON object"))
        return r
    for msg in string_problems(data):
        r.error("string", msg)
    numbers = number_problems(data)
    for msg in numbers:
        r.error("value", msg)
    if any("NaN" in msg for msg in numbers):
        return r  # NaN・inf は JSON に書けない(中身の hash も作れない)ので、ここで止める
    for f in privacy_findings(data, raw):
        r.error(
            "privacy",
            tr(
                f"{f['where']}: {f['what']}らしい文字があります。消してから出してください",
                f"{f['where']}: has what looks like {f['what']}. Remove it before submitting",
            ),
        )
    failures = data.get("kind") == FAILURES_KIND
    if failures:
        _check_keys(r, data, FAILURES_TOP_KEYS, "$", FAILURES_TOP_KEYS - {"comfyui_version", "db"})
    else:
        _check_keys(r, data, TOP_KEYS, "$", TOP_KEYS - {"comfyui_version", "db"})
    schema = data.get("contrib_schema")
    if isinstance(schema, bool) or schema not in CONTRIB_SCHEMAS:
        r.error(
            "schema",
            tr(
                f"contrib_schema が {' / '.join(map(str, CONTRIB_SCHEMAS))} のどれでもありません: {schema!r}",
                f"contrib_schema is not {' / '.join(map(str, CONTRIB_SCHEMAS))}: {schema!r}",
            ),
        )
        return r
    mm0 = data.get("measurement") if isinstance(data.get("measurement"), dict) else {}
    if "files_fp" in mm0:
        fp = mm0.get("files_fp")
        if schema == 1:
            r.error(
                "schema",
                tr(
                    "measurement.files_fp は contrib_schema 2 の項目です",
                    "measurement.files_fp is a contrib_schema 2 field",
                ),
            )
        elif not isinstance(fp, str) or not _FILES_FP.fullmatch(fp):
            r.error(
                "value",
                tr(
                    "measurement.files_fp は 16 進の小文字 64 桁(sha256)です",
                    "measurement.files_fp must be 64 lowercase hex digits (sha256)",
                ),
            )
    _check_versions(r, data, db)
    if data.get("kind") not in (KIND, FAILURES_KIND):
        r.error(
            "schema",
            tr(f"kind は {KIND!r} か {FAILURES_KIND!r} です", f"kind must be {KIND!r} or {FAILURES_KIND!r}"),
        )
    if schema < 3:
        # contrib_schema 3 の項目(#132)。古い版の提出には入れない
        new3 = [f"$.{k}" for k in ("db",) if k in data]
        new3 += [f"$.measurement.{k}" for k in ("estimate", "timing", "spilled", "spilled_gb") if k in mm0]
        if failures:
            new3.append(f"kind {FAILURES_KIND}")
        for where in new3:
            r.error(
                "schema",
                tr(f"{where} は contrib_schema 3 の項目です", f"{where} is a contrib_schema 3 field"),
            )
    if "db" in data:
        _check_db(r, data.get("db"))
    tool = data.get("tool")
    if _check_keys(r, tool, TOOL_KEYS, "$.tool", TOOL_KEYS) and (
        tool.get("name") != "genmodelfit"
        or not isinstance(tool.get("version"), str)
        or not _TOOL_VERSION.fullmatch(tool["version"])
    ):
        r.error(
            "schema",
            tr(
                "tool は {name: genmodelfit, version: 版} にしてください",
                "tool must be {name: genmodelfit, version: <version>}",
            ),
        )
    # 日付(日付だけ。未来の日付は誤り)
    d = data.get("date")
    day = None
    if not isinstance(d, str) or not _DATE.fullmatch(d):
        r.error(
            "date",
            tr(
                f"date は YYYY-MM-DD(日付だけ)にしてください: {d!r}",
                f"date must be YYYY-MM-DD (date only): {d!r}",
            ),
        )
    else:
        try:
            day = _date.fromisoformat(d)
        except ValueError:
            r.error("date", tr(f"date が日付として読めません: {d}", f"date is not a valid date: {d}"))
        if day and day > today + timedelta(days=1):
            r.error("date", tr(f"date が未来です: {d}", f"date is in the future: {d}"))
        if day and day.year < 2026:
            r.warn(
                "date_old",
                tr(
                    f"date が古すぎます(genmodelfit より前): {d}",
                    f"date is too old (before genmodelfit existed): {d}",
                ),
            )
    cv = data.get("comfyui_version")
    if cv is not None and (not isinstance(cv, str) or not _VERSION.fullmatch(cv)):
        r.error(
            "value",
            tr(
                f"comfyui_version は x.y.z の版の番号だけにしてください: {cv!r}",
                f"comfyui_version must be a plain x.y.z version: {cv!r}",
            ),
        )
    if cv is None:
        r.warn("comfyui_version_missing", tr("comfyui_version がありません", "comfyui_version is missing"))

    vram, ram = _check_system(r, data)
    if failures:
        _check_failures(r, data.get("failures"), vram, db)
    else:
        mm = data.get("measurement")
        if _check_keys(
            r, mm, MEASUREMENT_KEYS, "$.measurement", {"model_id", "settings", "outcome", "peak_vram_gb"}
        ):
            _check_new_fields(r, mm, vram, ram)
            _check_measurement(r, mm, vram, ram, db)

    r.sha256 = content_hash(data)
    r.suggested_path = suggested_path(data)
    if r.ok and db is not None and r.model_id and not failures:
        _check_effect(r, data, db)
    if path is not None and check_filename:
        _check_filename(r, Path(path), data)
    return r


def _check_system(r: Report, data: dict[str, Any]) -> tuple[Any, Any]:
    """system(OS の種類・メインメモリ・GPU)を検査する。(GPU の VRAM, メインメモリ)。
    実測と失敗の記録で同じ(#132)。"""
    system = data.get("system")
    gpu: dict[str, Any] = {}
    ram = None
    if _check_keys(r, system, SYSTEM_KEYS, "$.system", SYSTEM_KEYS):
        osf = system.get("os_family")
        if osf is not None and osf not in OS_FAMILIES:
            r.error(
                "value",
                tr(
                    f"system.os_family は {' / '.join(OS_FAMILIES)} か null です(版は入れない): {osf!r}",
                    f"system.os_family must be {' / '.join(OS_FAMILIES)} or null (no version): {osf!r}",
                ),
            )
        ram = system.get("ram_total_gb")
        if ram is not None:
            if not _num(ram) or not 0 < ram <= MAX_RAM_GB:
                r.error(
                    "value",
                    tr(
                        f"system.ram_total_gb がありえない値です: {ram!r}",
                        f"system.ram_total_gb is implausible: {ram!r}",
                    ),
                )
                ram = None
            elif ram != round(ram):
                r.warn(
                    "ram_not_rounded",
                    tr(
                        "system.ram_total_gb は GiB の整数に丸めてください(機種を絞らないため)",
                        "Round system.ram_total_gb to whole GiB (so it does not narrow down the machine)",
                    ),
                )
        g = system.get("gpu")
        if _check_keys(r, g, GPU_KEYS, "$.system.gpu", {"name", "vram_gb"}):
            gpu = g
    name = gpu.get("name")
    vram = gpu.get("vram_gb")
    if gpu:
        if not isinstance(name, str) or not _GPU_NAME.fullmatch(name):
            r.error(
                "value",
                tr(
                    "system.gpu.name は GPU の名前(英数字・空白・. -・(R)・(TM)・™・®、64 字まで)"
                    "だけにしてください",
                    "system.gpu.name must be just the GPU name "
                    "(letters, digits, spaces, . -, (R), (TM), ™, ®; "
                    "up to 64 characters)",
                ),
            )
            name = None
        if not _num(vram) or not 0 < vram <= MAX_GPU_VRAM_GB:
            r.error(
                "value",
                tr(
                    f"system.gpu.vram_gb がありえない値です: {vram!r}",
                    f"system.gpu.vram_gb is implausible: {vram!r}",
                ),
            )
            vram = None
        drv = gpu.get("driver")
        if drv is not None and (not isinstance(drv, str) or not _DRIVER.fullmatch(drv)):
            r.error(
                "value",
                tr(
                    f"system.gpu.driver はドライバの版にしてください: {drv!r}",
                    f"system.gpu.driver must be a driver version: {drv!r}",
                ),
            )
        if drv is None:
            r.warn("driver_missing", tr("system.gpu.driver がありません", "system.gpu.driver is missing"))
        vendor, backend = gpu.get("vendor"), gpu.get("backend")
        if vendor is not None and vendor not in VENDOR_BACKEND:
            r.error(
                "value",
                tr(
                    f"system.gpu.vendor は {' / '.join(VENDOR_BACKEND)} か無しです: {vendor!r}",
                    f"system.gpu.vendor must be {' / '.join(VENDOR_BACKEND)} or absent: {vendor!r}",
                ),
            )
        elif backend is not None and backend not in VENDOR_BACKEND.values():
            r.error(
                "value",
                tr(
                    f"system.gpu.backend は {' / '.join(VENDOR_BACKEND.values())} か無しです: {backend!r}",
                    f"system.gpu.backend must be {' / '.join(VENDOR_BACKEND.values())} "
                    f"or absent: {backend!r}",
                ),
            )
        elif (vendor is None) != (backend is None) or (vendor and VENDOR_BACKEND[vendor] != backend):
            r.error(
                "gpu_inconsistent",
                tr(
                    f"system.gpu の vendor({vendor!r})と backend({backend!r})が合いません",
                    f"system.gpu vendor ({vendor!r}) and backend ({backend!r}) do not match",
                ),
            )
        elif vendor not in (None, "nvidia") and (gpu.get("compute_capability") or gpu.get("arch")):
            r.error(
                "gpu_inconsistent",
                tr(
                    "compute capability・arch は NVIDIA の GPU だけに入れます",
                    "compute capability and arch are for NVIDIA GPUs only",
                ),
            )
        elif vendor and isinstance(name, str) and name_vendor(name) not in (None, vendor):
            r.error(
                "gpu_inconsistent",
                tr(
                    f"GPU 名({name})の会社と vendor({vendor})が合いません",
                    f"The GPU name ({name}) does not match vendor ({vendor})",
                ),
            )
        elif (
            vendor == "apple"
            and isinstance(name, str)
            and name != "mps"
            and not APPLE_CHIP_NAME.fullmatch(name)
        ):
            # Apple の GPU 名は bench が system_profiler で読んだチップ名(Apple M2 Pro など)か mps だけ
            r.error(
                "gpu_inconsistent",
                tr(
                    f"Apple の GPU 名はチップ名(Apple M2 Pro など)か mps だけです: {name}",
                    f"An Apple GPU name must be a chip name (Apple M2 Pro etc.) or mps: {name}",
                ),
            )
        pci = gpu.get("pci_id")
        if pci is not None:
            if not isinstance(pci, str) or not _PCI_ID.fullmatch(pci):
                r.error(
                    "value",
                    tr(
                        f"system.gpu.pci_id は PCI の型番 ID(1002:744c の形、小文字)です: {pci!r}",
                        f"system.gpu.pci_id must be a PCI device ID (like 1002:744c, lowercase): {pci!r}",
                    ),
                )
            elif (vendor or "nvidia") != PCI_VENDORS.get(pci[:4]):
                r.error(
                    "gpu_inconsistent",
                    tr(
                        f"system.gpu.pci_id の会社({PCI_VENDORS.get(pci[:4], pci[:4])})と"
                        f" vendor({vendor})が合いません",
                        f"The system.gpu.pci_id vendor ({PCI_VENDORS.get(pci[:4], pci[:4])}) does not match"
                        f" vendor ({vendor})",
                    ),
                )
            elif isinstance(name, str):
                conflict = pci_name_conflict(vendor or "nvidia", pci, name)
                if conflict is None:
                    r.notice(
                        "unknown_gpu",
                        tr(
                            f"system.gpu.pci_id {pci} が型番 ID の表(hardware.PCI_DEVICE_NAMES)にありません",
                            f"system.gpu.pci_id {pci} is not in the device ID table "
                            "(hardware.PCI_DEVICE_NAMES)",
                        ),
                    )
                elif conflict:
                    r.error(
                        "gpu_inconsistent",
                        tr(
                            f"system.gpu.pci_id {pci} の GPU と GPU 名({name})が合いません",
                            f"The GPU of system.gpu.pci_id {pci} does not match the GPU name ({name})",
                        ),
                    )
        arch, cc = gpu.get("arch"), gpu.get("compute_capability")
        if arch is not None and arch not in ARCHES:
            r.error(
                "value",
                tr(
                    f"system.gpu.arch が知らない世代です: {arch!r}",
                    f"system.gpu.arch is an unknown architecture: {arch!r}",
                ),
            )
        if cc is not None and (not isinstance(cc, str) or not _CC.fullmatch(cc)):
            r.error(
                "value",
                tr(
                    f"system.gpu.compute_capability は 8.6 の形にしてください: {cc!r}",
                    f"system.gpu.compute_capability must look like 8.6: {cc!r}",
                ),
            )
            cc = None
        if name:
            r.gpu_name = name
            known = cc_from_name(name)
            nvidia = (gpu.get("vendor") or name_vendor(name) or "nvidia") == "nvidia"
            # #101: NVIDIA の名前は表でなく形で見る。断るのは名前の代わりの値・汎用の名前だけ(gpu_known)で、
            # 形に合わない名前・表に無い名前はメンテナが確かめる(docs/schema.md「contrib」)
            if nvidia and not gpu_known(name, gpu.get("vendor")):
                r.notice(
                    "unknown_gpu",
                    tr(
                        f"GPU 名が汎用の名前・名前の代わりの値なので、判定には使いません: {name}",
                        "The GPU name is a generic name or a placeholder,"
                        f" so it is not used for judging: {name}",
                    ),
                )
            elif nvidia and not nvidia_name_well_formed(name):
                r.notice(
                    "gpu_name_form",
                    tr(
                        f"GPU 名が NVIDIA の名前の形に合いません(メンテナが確かめます。個人の名前などが入って"
                        f"いないか contrib show で見てください): {name}",
                        f"The GPU name does not fit the NVIDIA name form (a maintainer checks it; look with"
                        f" contrib show that no personal names are in it): {name}",
                    ),
                )
            elif known is None:
                r.notice(
                    "unknown_gpu",
                    tr(
                        "GPU 名が知っている表にありません(NVIDIA"
                        f" 以外・新しい GPU?。判定には使います): {name}",
                        f"The GPU name is not in the known table (non-NVIDIA or a new GPU?; it is used for"
                        f" judging): {name}",
                    ),
                )
            nominal = nvidia_nominal_vram(name) if nvidia else ()
            lo, hi = NOMINAL_VRAM_TOLERANCE
            if nominal and _num(vram) and not any(lo <= vram / v <= hi for v in nominal):
                # #101: 改造品(4090 の 48 GB など)があるので誤りにしない。
                # 混ざる心配は same_gpu の VRAM の比が防ぐ
                r.notice(
                    "gpu_vram_nominal",
                    tr(
                        f"{name} の公称の VRAM は {' / '.join(f'{v:g}' for v in nominal)} GB ですが、"
                        f"system.gpu.vram_gb が {vram:g} です(改造品でなければ誤り。メンテナが確かめます)",
                        f"{name} has {' / '.join(f'{v:g}' for v in nominal)} GB of VRAM nominally,"
                        f" but system.gpu.vram_gb is {vram:g} (an error unless a modified card; a maintainer"
                        " checks it)",
                    ),
                )
            if known is not None and cc and parse_compute_cap(cc) != known:
                r.error(
                    "gpu_inconsistent",
                    tr(
                        f"GPU 名({name})の compute capability は {known[0]}.{known[1]} ですが、"
                        f"{cc} とあります",
                        f"The compute capability of {name} is {known[0]}.{known[1]}, but {cc} is given",
                    ),
                )
            pcc = parse_compute_cap(cc) if cc else None
            if pcc and arch and arch_of(pcc) != arch:
                r.error(
                    "gpu_inconsistent",
                    tr(
                        f"compute capability {cc} と世代 {arch} が合いません",
                        f"compute capability {cc} does not match arch {arch}",
                    ),
                )
            elif known and arch and arch_of(known) != arch and not cc:
                r.error(
                    "gpu_inconsistent",
                    tr(
                        f"GPU 名({name})の世代は {arch_of(known)} ですが、{arch} とあります",
                        f"The architecture of {name} is {arch_of(known)}, but {arch} is given",
                    ),
                )

    return vram, ram


def _check_db(r: Report, d: Any) -> None:
    """一覧の版(#132): {generated_at: YYYY-MM-DD, sha256: 16 進の小文字 12 桁}。どちらも任意。"""
    if not _check_keys(r, d, DB_KEYS, "$.db", set()):
        return
    gen = d.get("generated_at")
    if gen is not None and (not isinstance(gen, str) or not _DATE.fullmatch(gen)):
        r.error(
            "value",
            tr(
                f"db.generated_at は YYYY-MM-DD(日付だけ)です: {gen!r}",
                f"db.generated_at must be YYYY-MM-DD (date only): {gen!r}",
            ),
        )
    sha = d.get("sha256")
    if sha is not None and (not isinstance(sha, str) or not _DB_SHA.fullmatch(sha)):
        r.error(
            "value",
            tr(
                "db.sha256 は 16 進の小文字 12 桁(db.json の sha256 の先頭)です",
                "db.sha256 must be 12 lowercase hex digits (the start of the db.json sha256)",
            ),
        )


def _seconds_ok(x: Any, allow_zero: bool = True) -> bool:
    return _num(x) and (x >= 0 if allow_zero else x > 0) and x <= 86400


def _check_new_fields(r: Report, mm: dict[str, Any], vram: Any, ram: Any) -> None:
    """contrib_schema 3 の実測の項目(#132): 見積もり・時間の内訳・VRAM を超えた印。"""
    est = mm.get("estimate")
    if est is not None and _check_keys(r, est, ESTIMATE_KEYS, "$.measurement.estimate", set()):
        for key, allowed in (
            ("fit_level", ESTIMATE_LEVELS),
            ("run_mode", ESTIMATE_MODES),
            ("basis", ESTIMATE_BASES),
        ):
            v = est.get(key)
            if v is not None and v not in allowed:
                r.error(
                    "value",
                    tr(
                        f"measurement.estimate.{key} は {' / '.join(allowed)} か null です: {v!r}",
                        f"measurement.estimate.{key} must be {' / '.join(allowed)} or null: {v!r}",
                    ),
                )
        for key in ("vram_gb", "vram_resident_gb", "work_gb", "ram_required_gb"):
            v = est.get(key)
            if v is not None and (not _num(v) or v < 0 or v > MAX_RAM_GB):
                r.error(
                    "value",
                    tr(
                        f"measurement.estimate.{key} は 0 以上の GiB か null です: {v!r}",
                        f"measurement.estimate.{key} must be GiB of 0 or more, or null: {v!r}",
                    ),
                )
        src = est.get("measurements")
        if src is not None and src not in ESTIMATE_SOURCES:
            r.error(
                "value",
                tr(
                    f"measurement.estimate.measurements は {' / '.join(ESTIMATE_SOURCES)} です: {src!r}",
                    f"measurement.estimate.measurements must be {' / '.join(ESTIMATE_SOURCES)}: {src!r}",
                ),
            )
        v = est.get("seconds")
        if v is not None and not _seconds_ok(v):
            r.error(
                "value",
                tr(
                    f"measurement.estimate.seconds は秒数か null です: {v!r}",
                    f"measurement.estimate.seconds must be seconds or null: {v!r}",
                ),
            )
    timing = mm.get("timing")
    if timing is not None and _check_keys(r, timing, TIMING_KEYS, "$.measurement.timing", set()):
        for key in sorted(TIMING_KEYS - {"cold_load"}):
            v = timing.get(key)
            if v is not None and not _seconds_ok(v):
                r.error(
                    "value",
                    tr(
                        f"measurement.timing.{key} は 0 以上の秒数か null です: {v!r}",
                        f"measurement.timing.{key} must be seconds of 0 or more, or null: {v!r}",
                    ),
                )
        cold = timing.get("cold_load")
        if cold is not None and not isinstance(cold, bool):
            r.error(
                "value",
                tr(
                    "measurement.timing.cold_load は true / false / null です",
                    "measurement.timing.cold_load must be true / false / null",
                ),
            )
    if "spilled" not in mm and "spilled_gb" not in mm:
        return
    spilled, extra, peak = mm.get("spilled"), mm.get("spilled_gb"), mm.get("peak_vram_gb")
    if spilled is not True:
        r.error(
            "value",
            tr(
                "measurement.spilled は true だけです(超えていなければ書かない)",
                "measurement.spilled must be true (leave it out when nothing spilled)",
            ),
        )
        return
    if mm.get("outcome") != "ok":
        r.error(
            "value",
            tr(
                "measurement.spilled は最後まで動いた(ok)実測だけに付けます",
                "measurement.spilled is only for runs that finished (ok)",
            ),
        )
    if not _num(extra) or extra <= 0:
        r.error(
            "value",
            tr(
                f"measurement.spilled_gb は総量を超えた量(0 より大きい GiB)です: {extra!r}",
                f"measurement.spilled_gb must be the amount over the total (GiB above 0): {extra!r}",
            ),
        )
        return
    if not (_num(peak) and _num(vram)):
        return
    if peak <= vram * PEAK_VRAM_RATIO + VRAM_TOLERANCE_GB:
        r.error(
            "value",
            tr(
                f"measurement.spilled なのに、最大 VRAM {peak} GB が GPU の総量 {vram} GB を超えていません",
                f"measurement.spilled is set, but peak VRAM {peak} GB does not exceed the GPU total"
                f" of {vram} GB",
            ),
        )
    elif abs(extra - (peak - vram)) > 0.1:
        r.error(
            "value",
            tr(
                f"measurement.spilled_gb({extra})が最大 VRAM − GPU の総量({peak - vram:.2f})と合いません",
                f"measurement.spilled_gb ({extra}) does not match peak VRAM minus the GPU total"
                f" ({peak - vram:.2f})",
            ),
        )
    # 共有メモリに逃げられるのはメインメモリまで(メモリが分からなければ GPU の総量の分まで)
    limit = vram + (ram if _num(ram) and ram > 0 else vram) * 1.05 + RAM_TOLERANCE_GB
    if peak > limit:
        r.error(
            "value",
            tr(
                f"最大 VRAM {peak} GB が、GPU の総量とメインメモリを合わせた量 {limit:.1f} GB を超えています",
                f"Peak VRAM {peak} GB exceeds the GPU total plus the RAM ({limit:.1f} GB)",
            ),
        )


def _check_failures(r: Report, items: Any, vram: Any, db: Database | None) -> None:
    """失敗の記録の並び(#132)。中断(interrupted)・ほかのプログラムのせい(interference)は受け付けない。"""
    if not isinstance(items, list) or not items or len(items) > MAX_FAILURES:
        r.error(
            "schema",
            tr("failures は 1 件以上の記録の並びです", "failures must be a list of one or more records"),
        )
        return
    for i, f in enumerate(items[:MAX_FAILURES]):
        where = f"$.failures[{i}]"
        if _check_keys(r, f, FAILURE_KEYS, where, {"model_id", "outcome"}):
            _check_failure(r, f, where, vram, db)


def _check_failure(r: Report, f: dict[str, Any], where: str, vram: Any, db: Database | None) -> None:
    mid = f.get("model_id")
    if not isinstance(mid, str) or not _MODEL_ID.fullmatch(mid):
        r.error(
            "value",
            tr(f"{where}.model_id は一覧の ID にしてください", f"{where}.model_id must be a list id"),
        )
    elif db is not None and db.get(mid) is None:
        r.notice(
            "unknown_model",
            tr(
                f"モデル ID が同梱の一覧にありません: {mid}",
                f"The model id is not in the bundled list: {mid}",
            ),
        )
    outcome = f.get("outcome")
    if outcome not in FAILURE_OUTCOMES:
        r.error(
            "value",
            tr(
                f"{where}.outcome は {' / '.join(FAILURE_OUTCOMES)} です(中断・ほかのプログラムのせいの"
                f"失敗は送らない): {outcome!r}",
                f"{where}.outcome must be {' / '.join(FAILURE_OUTCOMES)} (interrupted runs and runs"
                f" disturbed by other programs are not sent): {outcome!r}",
            ),
        )
    s = f.get("settings")
    if s is not None:
        _check_failure_settings(r, s, f"{where}.settings")
    secs = f.get("seconds")
    if secs is not None and not _seconds_ok(secs, allow_zero=False):
        r.error(
            "value",
            tr(
                f"{where}.seconds は 0 より大きい秒数か null です",
                f"{where}.seconds must be seconds above 0 or null",
            ),
        )
    if "timeout_s" in f and (outcome != "timeout" or not _seconds_ok(f.get("timeout_s"), allow_zero=False)):
        r.error(
            "value",
            tr(
                f"{where}.timeout_s は timeout の記録だけの秒数です",
                f"{where}.timeout_s is seconds, only for a timeout record",
            ),
        )
    nodes = f.get("missing_nodes")
    other = f.get("other_nodes")
    if other is not None and (
        outcome != "missing_nodes" or not isinstance(other, int) or isinstance(other, bool) or other < 1
    ):
        r.error(
            "value",
            tr(
                f"{where}.other_nodes は missing_nodes の記録だけの 1 以上の数です",
                f"{where}.other_nodes is a count of 1 or more, only for a missing_nodes record",
            ),
        )
    if (outcome == "missing_nodes" or nodes is not None) and (
        outcome != "missing_nodes"
        or not isinstance(nodes, list)
        or not (nodes or (isinstance(other, int) and other >= 1))
        or not all(isinstance(n, str) and NODE_NAME.fullmatch(n) for n in nodes)
    ):
        r.error(
            "value",
            tr(
                f"{where}.missing_nodes は missing_nodes の記録だけの、ノードの名前"
                "(文字・数字・空白と _ . + ( ) | ' : , [ ] -、100 字まで)の並びです"
                "(空なら other_nodes が要る)",
                f"{where}.missing_nodes is a list of node names (letters, digits, spaces and"
                " _ . + ( ) | ' : , [ ] -; up to 100 characters), only for a missing_nodes record"
                " (other_nodes is needed when it is empty)",
            ),
        )
    for key, pat in (("error_type", ERROR_TYPE), ("node_type", NODE_NAME)):
        v = f.get(key)
        bad = not isinstance(v, str) or not pat.fullmatch(v)
        if v is not None and (outcome not in ("error", "oom") or bad):
            r.error(
                "value",
                tr(
                    f"{where}.{key} は error・oom の記録だけの、決まった形の名前です(文は送らない)",
                    f"{where}.{key} is a plain name, only for error and oom records (no message text)",
                ),
            )
    peak = f.get("peak_vram_gb")
    if peak is not None and (
        not _num(peak) or peak <= 0 or (_num(vram) and peak > 2 * vram + VRAM_TOLERANCE_GB)
    ):
        r.error(
            "value",
            tr(
                f"{where}.peak_vram_gb がありえない値です: {peak!r}",
                f"{where}.peak_vram_gb is implausible: {peak!r}",
            ),
        )
    before = f.get("before_run")
    if before is not None and (before is not True or outcome != "missing_nodes"):
        r.error(
            "value",
            tr(
                f"{where}.before_run は missing_nodes の記録だけの true です",
                f"{where}.before_run is true, only for a missing_nodes record",
            ),
        )
    fp = f.get("files_fp")
    if fp is not None and (not isinstance(fp, str) or not _FILES_FP.fullmatch(fp)):
        r.error(
            "value",
            tr(
                f"{where}.files_fp は 16 進の小文字 64 桁(sha256)です",
                f"{where}.files_fp must be 64 lowercase hex digits (sha256)",
            ),
        )


def _check_failure_settings(r: Report, s: Any, where: str) -> None:
    if not isinstance(s, dict):
        r.error("schema", tr(f"{where} はオブジェクトにしてください", f"{where} must be an object"))
        return
    for k in sorted(set(s) - SETTINGS_KEYS):
        r.error("unknown_field", tr(f"{where}.{k} は知らない項目です", f"{where}.{k} is an unknown field"))
    for k, (lo, hi) in SETTING_RANGES.items():
        v = s.get(k)
        if v is not None and (not _num(v) or not lo <= v <= hi or (k != "seconds" and v != int(v))):
            r.error(
                "settings",
                tr(
                    f"{where}.{k} が範囲({lo:g}〜{hi:g})の外です: {v!r}",
                    f"{where}.{k} is out of range ({lo:g} to {hi:g}): {v!r}",
                ),
            )
    prec = s.get("precision")
    if prec is not None and (
        not isinstance(prec, str)
        or not _PRECISION.fullmatch(prec)
        or norm_precision(prec) not in PRECISION_BYTES
    ):
        r.error(
            "settings",
            tr(
                f"{where}.precision は知っている精度にしてください",
                f"{where}.precision must be a known precision",
            ),
        )


def _check_measurement(r: Report, mm: dict[str, Any], vram: Any, ram: Any, db: Database | None) -> None:
    mid = mm.get("model_id")
    entry = None
    if not isinstance(mid, str) or not _MODEL_ID.fullmatch(mid):
        r.error(
            "value",
            tr(
                "measurement.model_id は一覧の ID(英数字と _ . : / + -、200 字まで)にしてください",
                "measurement.model_id must be a list id "
                "(letters, digits and _ . : / + -; up to 200 characters)",
            ),
        )
    else:
        r.model_id = mid
        entry = db.get(mid) if db is not None else None
        if db is not None and entry is None:
            r.notice(
                "unknown_model",
                tr(
                    f"モデル ID が同梱の一覧にありません: {mid}",
                    f"The model id is not in the bundled list: {mid}",
                ),
            )
    outcome = mm.get("outcome")
    if outcome not in OUTCOMES:
        r.error(
            "value",
            tr(
                f"measurement.outcome は {' / '.join(OUTCOMES)} です: {outcome!r}",
                f"measurement.outcome must be {' / '.join(OUTCOMES)}: {outcome!r}",
            ),
        )
    if "settings_override" in mm and not isinstance(mm.get("settings_override"), bool):
        r.error(
            "value",
            tr(
                "measurement.settings_override は true / false です",
                "measurement.settings_override must be true / false",
            ),
        )
    std = mm.get("standard", True)
    if not isinstance(std, bool):
        r.error(
            "value",
            tr("measurement.standard は true / false です", "measurement.standard must be true / false"),
        )
    elif not std:
        r.warn(
            "nonstandard",
            tr(
                "標準でない条件の実測なので、まとめにも見積もりにも使いません(記録としては受け付けます)",
                "A measurement under non-standard conditions is used neither in the aggregate "
                "nor in estimates "
                "(accepted as a record)",
            ),
        )
    off = mm.get("offload")
    if off is not None and off not in OFFLOADS:
        r.error(
            "value",
            tr(
                f"measurement.offload は {' / '.join(OFFLOADS)} か null です: {off!r}",
                f"measurement.offload must be {' / '.join(OFFLOADS)} or null: {off!r}",
            ),
        )
    if outcome == "oom" and off not in (None, "oom"):
        r.warn(
            "offload_outcome",
            tr(f"outcome が oom なのに offload が {off} です", f"outcome is oom but offload is {off}"),
        )
    metric = mm.get("vram_metric")
    if metric is not None and metric not in VRAM_METRICS:
        r.error(
            "value",
            tr(
                f"measurement.vram_metric は {' / '.join(VRAM_METRICS)} か null です: {metric!r}",
                f"measurement.vram_metric must be {' / '.join(VRAM_METRICS)} or null: {metric!r}",
            ),
        )
    if metric is None:
        r.warn(
            "metric_missing",
            tr(
                "vram_metric がありません(測り方が分からないので比べにくい)",
                "vram_metric is missing (hard to compare without knowing how it was measured)",
            ),
        )
    peak = mm.get("peak_vram_gb")
    if not _num(peak) or peak < 0:
        r.error(
            "value",
            tr(
                f"measurement.peak_vram_gb は 0 以上の数です: {peak!r}",
                f"measurement.peak_vram_gb must be 0 or more: {peak!r}",
            ),
        )
    else:
        if outcome == "ok" and peak <= 0:
            r.error(
                "value",
                tr(
                    "最後まで動いた(ok)のに peak_vram_gb が 0 です",
                    "peak_vram_gb is 0 although the run finished (ok)",
                ),
            )
        spilled = mm.get("spilled") is True and outcome == "ok"  # 共有メモリへ逃げた印(#132)
        if _num(vram) and peak > vram * PEAK_VRAM_RATIO + VRAM_TOLERANCE_GB and not spilled:
            r.error(
                "value",
                tr(
                    f"最大 VRAM {peak} GB が GPU の総量 {vram} GB を超えています",
                    f"Peak VRAM {peak} GB exceeds the GPU total of {vram} GB",
                ),
            )
    pram = mm.get("peak_ram_gb")
    if pram is not None:
        if not _num(pram) or pram <= 0:
            r.error(
                "value",
                tr(
                    f"measurement.peak_ram_gb は 0 より大きい数か null です: {pram!r}",
                    f"measurement.peak_ram_gb must be above 0 or null: {pram!r}",
                ),
            )
        elif _num(ram) and pram > ram * 1.05 + RAM_TOLERANCE_GB:
            r.error(
                "value",
                tr(
                    f"最大メモリ {pram} GB がメインメモリ {ram} GB を超えています",
                    f"Peak RAM {pram} GB exceeds the RAM of {ram} GB",
                ),
            )
    secs = mm.get("seconds")
    if secs is None:
        if outcome == "ok":
            r.error(
                "value",
                tr(
                    "最後まで動いた(ok)のに seconds がありません",
                    "seconds is missing although the run finished (ok)",
                ),
            )
    elif not _num(secs) or secs <= 0 or secs > 86400:
        r.error(
            "value",
            tr(
                f"measurement.seconds は 0 より大きい秒数です: {secs!r}",
                f"measurement.seconds must be seconds above 0: {secs!r}",
            ),
        )
    note = mm.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE_CHARS):
        r.error(
            "value",
            tr(
                f"measurement.note は {MAX_NOTE_CHARS} 字までの文です",
                f"measurement.note must be text of up to {MAX_NOTE_CHARS} characters",
            ),
        )
    s = mm.get("settings")
    if not isinstance(s, dict):
        r.error(
            "schema",
            tr("measurement.settings はオブジェクトにしてください", "measurement.settings must be an object"),
        )
        return
    for k in sorted(set(s) - SETTINGS_KEYS):
        r.error(
            "unknown_field",
            tr(
                f"measurement.settings.{k} は知らない項目です",
                f"measurement.settings.{k} is an unknown field",
            ),
        )
    for k, (lo, hi) in SETTING_RANGES.items():
        v = s.get(k)
        if v is None:
            continue
        if not _num(v) or not lo <= v <= hi or (k != "seconds" and v != int(v)):
            r.error(
                "settings",
                tr(
                    f"settings.{k} が範囲({lo:g}〜{hi:g})の外です: {v!r}",
                    f"settings.{k} is out of range ({lo:g} to {hi:g}): {v!r}",
                ),
            )
    prec = s.get("precision")
    if prec is not None and (
        not isinstance(prec, str)
        or not _PRECISION.fullmatch(prec)
        or norm_precision(prec) not in PRECISION_BYTES
    ):
        r.error(
            "settings",
            tr(
                f"settings.precision は知っている精度({', '.join(PRECISION_BYTES)})にしてください",
                f"settings.precision must be a known precision ({', '.join(PRECISION_BYTES)})",
            ),
        )
    if entry is not None and outcome == "ok" and _num(peak) and peak > 0 and mm.get("spilled") is not True:
        _check_against_estimate(r, entry, mm, s, peak)
    if entry is not None:
        mod = entry.modality
        if mod not in MODALITIES:
            return
        if (
            mod in ("image", "video")
            and (s.get("width") is None or s.get("height") is None)
            and not no_size_input(entry)
        ):
            r.warn(
                "settings_missing",
                tr(
                    f"{mod} のモデルなのに width / height がありません",
                    f"width / height are missing for a {mod} model",
                ),
            )
        if no_size_input(entry) and s.get("batch") not in (None, 1):
            # 0.10.1 の bench は、大きさの入力の無いテンプレートで処理の単位(MoGe の batch_size)を
            # 枚数と記録した。判定は batch 1 なので、この提出は同じ設定の実測にならない(#90)
            b = s.get("batch")
            r.notice(
                "batch_sizeless",
                tr(
                    f"大きさの入力の無いテンプレートなのに batch が {b} です"
                    "(0.10.1 の bench が処理の単位を枚数と記録した値。"
                    "同じ設定の実測として使われないので、今の版で測り直してください)",
                    f"batch is {b} for a template without a size input"
                    " (0.10.1's bench recorded a processing batch as the batch;"
                    " it is not used as a same-settings measurement,"
                    " so measure again with the current version)",
                ),
            )
        if mod != "video" and s.get("frames") is not None:
            r.warn(
                "settings_modality", tr(f"{mod} のモデルに frames があります", f"A {mod} model has frames")
            )
        if mod in ("music", "sfx", "tts") and s.get("seconds") is None:
            r.warn(
                "settings_missing",
                tr(
                    f"{mod} のモデルなのに seconds(長さ)がありません",
                    f"seconds (length) is missing for a {mod} model",
                ),
            )


def _check_against_estimate(
    r: Report, entry: Any, mm: dict[str, Any], s: dict[str, Any], peak: float
) -> None:
    """式の見積もりと比べ、ありそうにない値を注意にする(失敗にはしない。マージするメンテナが見る)。"""
    from .estimate import apply_precision, components, formula_need, load_coefficients, settings_for

    parts = components(entry)
    if parts.unknown:
        return
    c = load_coefficients()
    try:
        settings = settings_for(entry, Settings.from_dict({k: v for k, v in s.items() if k in SETTINGS_KEYS}))
        parts = apply_precision(parts, entry, settings)
        n = formula_need(entry, settings, c, parts)
        full = n.full
    except (TypeError, ValueError):
        return
    # 作業用メモリだけの実測(torch_reserved かつ動的 VRAM。#100、estimate.working_only)は重みを含まないので、
    # 式の作業用メモリ(一番重い段)と比べる。見積もりにも段階にも使わず、照合だけに使う
    if mm.get("vram_metric") == "torch_reserved" and mm.get("offload") == "dynamic":
        work = n.act
        if work > 0 and abs(peak - work) / work > NOTICE_DEVIATION:
            r.notice(
                "deviation",
                tr(
                    f"作業用メモリだけの実測(動的 VRAM の torch の確保量。照合だけに使う){peak} GB が、式の"
                    f"作業用メモリ {work:.2f} GB から {abs(peak - work) / work:.0%} 外れています"
                    f"(±{NOTICE_DEVIATION:.0%} を超えた。見積もりにも段階にも使わない)",
                    "The working-memory-only measurement (torch reservation with dynamic VRAM; cross-check"
                    f" only), {peak} GB, is {abs(peak - work) / work:.0%} off the formula's working memory"
                    f" of {work:.2f} GB"
                    f" (beyond ±{NOTICE_DEVIATION:.0%}; not used for estimates or levels)",
                ),
            )
    elif full > 0 and abs(peak - full) / full > NOTICE_DEVIATION:
        r.notice(
            "deviation",
            tr(
                f"最大 VRAM {peak} GB が式の見積もり {full:.2f} GB から"
                f" {abs(peak - full) / full:.0%} 外れています(±{NOTICE_DEVIATION:.0%} を超えた。"
                f"設定の違う推定へは ±{NOTICE_DEVIATION:.0%} までしか広げない)",
                f"Peak VRAM {peak} GB is {abs(peak - full) / full:.0%} off the formula estimate "
                f"of {full:.2f} GB"
                f" (beyond ±{NOTICE_DEVIATION:.0%}; estimates for other settings are only stretched up to"
                f" ±{NOTICE_DEVIATION:.0%})",
            ),
        )
    # 動的 VRAM・partial で下限より小さいのは普通(一部の重みをメインメモリに置く)。全部載せ(none)と言うのに
    # 小さいものだけ知らせる。見積もりはどちらも下限(重み + 固定分)で切り詰める(estimate._prepare)
    floor = parts.total * c.weight_factor + c.overhead_gb
    if mm.get("offload") == "none" and peak < floor:
        r.notice(
            "below_weights",
            tr(
                f"全部 GPU に載せた(offload: none)のに、最大 VRAM {peak} GB が全部載せの下限"
                f"(重み + 固定分 {floor:.2f} GB)より小さい。見積もりでは下限の値にする",
                f"Everything was on the GPU (offload: none), yet peak VRAM {peak} GB is below the full-load"
                f" floor (weights + fixed part {floor:.2f} GB). Estimates use the floor",
            ),
        )


# 段階が変わるかを確かめるメインメモリ(よくある大きさ。GPU は実測と同じ GPU。_measured_pc)
CHECK_RAM_GB = (16.0, 32.0, 64.0)


def _measured_pc(m: Measurement, ram: float) -> SystemInfo:
    """実測と同じ GPU(名前・PCI の型番 ID・会社。VRAM は実測の GPU の総量)の仮の PC。ほかの利用者の実測は
    同じ GPU の PC にだけ使われる(estimate.pick_measurement)ので、段階が変わるかはこの PC で見る(0.9.0)。"""
    from .estimate import measurement_vendor
    from .hardware import classify_gpu
    from .models import Gpu

    vendor = measurement_vendor(m)
    g = Gpu(
        name=m.gpu_name,
        vendor=vendor,
        vram_total_gb=float(m.gpu_vram_gb),
        backend=VENDOR_BACKEND.get(vendor, "cuda"),
        pci_id=getattr(m, "gpu_pci_id", None),
    )
    if vendor == "nvidia":
        g = classify_gpu(g, windows_driver=True, comfy_windows=True)
        g.backend = "cuda"
    else:
        g.compute_dtype, g.bf16, g.fp8 = "fp16", True, False
        g.memory = "unified" if vendor == "apple" else "dedicated"
    return SystemInfo(os="-", cpu="-", cpu_cores=8, ram_total_gb=ram, ram_available_gb=ram, gpus=[g])


def _own_measurements(db: Database) -> list[Measurement]:
    return [m for m in db.measurements if getattr(m, "origin", None) != ORIGIN_COMMUNITY]


def community_effect(
    entry: Any, own: list[Measurement], community: list[Measurement], settings: Settings | None = None
) -> tuple[float, list[dict[str, Any]]]:
    """ほかの利用者の実測を足したときの (全部載せの VRAM が変わる割合(符号つき。絶対値の最大のもの),
    段階が変わる PC の一覧)。ほかの利用者の実測は同じ GPU の PC にだけ使われる(0.9.0)ので、PC は実測と同じ
    GPU(名前・型番 ID・会社、VRAM は実測の GPU の総量)× メインメモリ CHECK_RAM_GB。既定の設定
    (と settings を渡せばその設定)で比べる。同じ GPU で段階が上がるのも出る。"""
    from .estimate import estimate, measurement_vendor, nvidia_vram_variants

    rise = 0.0
    changes: list[dict[str, Any]] = []
    gpus: dict[tuple, Measurement] = {}
    for m in community:
        gpus.setdefault((gpu_key(m.gpu_name), getattr(m, "gpu_pci_id", None), round(m.gpu_vram_gb or 0)), m)
        # 名前の表で公称の VRAM が分かる GPU(Intel の Arc は hardware._INTEL_XPU、NVIDIA は
        # estimate.NVIDIA_VRAM_VARIANTS)は、その VRAM の
        # PC も点検する(同じ名前で VRAM の違う版がある: A770 の 8 GB と 16 GB など。同じ GPU の実測として
        # 使われるため)
        nominal = intel_xpu(m.gpu_name) if measurement_vendor(m) == "intel" else None
        if nominal and nominal[2] and round(nominal[2]) != round(m.gpu_vram_gb or 0):
            v = Measurement(**{**vars(m), "gpu_vram_gb": float(nominal[2])})
            gpus.setdefault((gpu_key(m.gpu_name), getattr(m, "gpu_pci_id", None), round(nominal[2])), v)
        # NVIDIA は estimate.NVIDIA_VRAM_VARIANTS(公式の製品ページで確かめた、VRAM の違う版)の各 VRAM
        if measurement_vendor(m) == "nvidia":
            for nv in nvidia_vram_variants(m.gpu_name):
                if round(nv) != round(m.gpu_vram_gb or 0):
                    v = Measurement(**{**vars(m), "gpu_vram_gb": nv})
                    gpus.setdefault((gpu_key(m.gpu_name), getattr(m, "gpu_pci_id", None), round(nv)), v)
    for s in [None] if settings is None else [None, settings]:
        for m in gpus.values():
            for ram in CHECK_RAM_GB:
                sysi = _measured_pc(m, ram)
                a = estimate(entry, sysi, s, own)
                b = estimate(entry, sysi, s, [*own, *community])
                if ram == CHECK_RAM_GB[-1] and a.vram_gb and not a.unknown_size:
                    rel = (b.vram_gb - a.vram_gb) / a.vram_gb
                    rise = rel if abs(rel) > abs(rise) else rise
                v = m.gpu_vram_gb
                if a.fit != b.fit and not any(
                    c["gpu_name"] == m.gpu_name and c["vram_gb"] == v and c["ram_gb"] == ram for c in changes
                ):
                    changes.append(
                        {
                            "model_id": entry.id,
                            "gpu_name": m.gpu_name,
                            "vram_gb": v,
                            "ram_gb": ram,
                            "before": a.fit,
                            "after": b.fit,
                        }
                    )
    return rise, changes


def _check_effect(r: Report, data: dict[str, Any], db: Database) -> None:
    """この提出だけを足すと、よくある PC で段階がどう変わるか(注意として出す。マージするメンテナが見る)。"""
    mm = data["measurement"]
    entry = db.get(mm["model_id"])
    if entry is None or mm.get("outcome") != "ok" or mm.get("standard") is False or mm.get("spilled") is True:
        return
    m = to_measurement(data)
    m.n_submissions, m.n_used, m.n_oom = 1, 1, 0
    own = _own_measurements(db)
    try:
        _rel, changes = community_effect(entry, own, [m], m.settings)
    except (TypeError, ValueError, ZeroDivisionError):
        return
    if changes:
        where = tr("、", "; ").join(
            tr(
                f"{c['gpu_name']} {c['vram_gb']:g} GB・メモリ {c['ram_gb']:g} GB: "
                f"{c['before']} → {c['after']}",
                f"{c['gpu_name']} {c['vram_gb']:g} GB, RAM {c['ram_gb']:g} GB: {c['before']} → {c['after']}",
            )
            for c in changes[:6]
        )
        r.notice(
            "fit_change",
            tr(
                f"同じ GPU の PC で段階が変わります({where})",
                f"The fit changes on PCs with the same GPU ({where})",
            ),
        )


def _check_filename(r: Report, p: Path, data: dict[str, Any]) -> None:
    m = _FILENAME.fullmatch(p.name)
    if not m:
        r.error(
            "filename",
            tr(
                f"ファイル名は <日付>-<sha8>.json(失敗の記録は <日付>-<sha8>-failures.json)にしてください"
                f"(例 {r.suggested_path})",
                f"The file name must be <date>-<sha8>.json (<date>-<sha8>-failures.json for failure records)"
                f" (e.g. {r.suggested_path})",
            ),
        )
        return
    if bool(m.group(3)) != is_failures(data):
        r.error(
            "filename",
            tr(
                f"失敗の記録のファイル名は <日付>-<sha8>-failures.json、実測は <日付>-<sha8>.json です"
                f"(例 {r.suggested_path})",
                f"A failure record file is named <date>-<sha8>-failures.json and a measurement"
                f" <date>-<sha8>.json (e.g. {r.suggested_path})",
            ),
        )
    if m.group(1) != data.get("date"):
        r.error(
            "filename",
            tr(
                f"ファイル名の日付 {m.group(1)} と date {data.get('date')} が違います",
                f"The file name date {m.group(1)} differs from date {data.get('date')}",
            ),
        )
    if r.sha256 and m.group(2) != r.sha256[:8]:
        r.error(
            "filename",
            tr(
                f"ファイル名の hash {m.group(2)} が内容の sha256 の先頭 {r.sha256[:8]} と違います",
                f"The file name hash {m.group(2)} differs from the start of the content sha256 "
                f"{r.sha256[:8]}",
            ),
        )
    gpu = ((data.get("system") or {}).get("gpu") or {}).get("name")
    if isinstance(gpu, str) and p.parent.name != gpu_slug(gpu):
        r.error(
            "filename",
            tr(
                f"フォルダ名 {p.parent.name} が GPU 名から決まる {gpu_slug(gpu)} と違います",
                f"The folder name {p.parent.name} differs from {gpu_slug(gpu)} given by the GPU name",
            ),
        )


class SubmissionReadError(ValueError):
    """読めない提出(大きすぎる・シンボリックリンク・JSON でない・入れ子が深すぎる)。"""


def read_submission(path: str | os.PathLike[str]) -> tuple[Any, str]:
    p = Path(path)
    if p.is_symlink():
        raise SubmissionReadError(tr("シンボリックリンクは受け付けません", "Symbolic links are not accepted"))
    if not p.is_file():
        raise SubmissionReadError(tr("ファイルではありません", "Not a file"))
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise SubmissionReadError(
            tr(
                f"大きすぎます({size} バイト。{MAX_FILE_BYTES} まで)",
                f"Too large ({size} bytes; up to {MAX_FILE_BYTES})",
            )
        )
    data = p.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        # BOM は消さずに断る(消すとファイルの中身と検査した中身が変わる。bench・contrib は BOM を書かない)
        raise SubmissionReadError(
            tr(
                "先頭に UTF-8 の BOM があります。BOM の無い UTF-8 で保存し直してください",
                "The file starts with a UTF-8 BOM. Save it again as UTF-8 without a BOM",
            )
        )
    try:
        raw = data.decode("utf-8")
        return loads_strict(raw), raw
    except RecursionError as e:
        raise SubmissionReadError(tr("入れ子が深すぎます", "Nested too deeply")) from e
    except DuplicateKeyError as e:
        raise SubmissionReadError(str(e)) from e
    except (UnicodeDecodeError, ValueError) as e:
        raise SubmissionReadError(
            tr(f"JSON ではありません({type(e).__name__})", f"Not JSON ({type(e).__name__})")
        ) from e


def validate_files(
    paths: list[str | os.PathLike[str]],
    db: Database | None = None,
    *,
    today: _date | None = None,
    check_filename: bool = True,
) -> list[Report]:
    """ファイルをまとめて検査する。
    読めない・JSON でないものも誤りとして返す。同じ内容(hash)は 2 件目以降を誤り。"""
    reports: list[Report] = []
    first: dict[str, str] = {}
    for p in paths:
        try:
            data, raw = read_submission(p)
        except SubmissionReadError as e:
            r = Report(path=Path(p).as_posix())
            r.error("read", tr(f"読めません: {e}", f"Cannot read: {e}"))
            reports.append(r)
            continue
        except OSError as e:
            r = Report(path=Path(p).as_posix())
            r.error("read", tr(f"読めません: {type(e).__name__}", f"Cannot read: {type(e).__name__}"))
            reports.append(r)
            continue
        r = validate_submission(data, db, path=p, raw=raw, today=today, check_filename=check_filename)
        if r.sha256:
            if r.sha256 in first:
                r.error(
                    "duplicate", tr(f"{first[r.sha256]} と同じ内容です", f"Same content as {first[r.sha256]}")
                )
            else:
                first[r.sha256] = str(p)
        reports.append(r)
    return reports


def find_files(root: str | os.PathLike[str]) -> list[Path]:
    """フォルダの下の *.json(並びは決まった順)。ファイルを渡せばそれだけ。"""
    p = Path(root)
    if p.is_file() or p.is_symlink():
        return [p]
    if not p.is_dir():
        return []
    out: list[Path] = []
    for d, dirs, files in os.walk(p, followlinks=False):  # シンボリックリンクのフォルダはたどらない
        dirs[:] = [x for x in dirs if not (Path(d) / x).is_symlink()]
        out += [Path(d) / f for f in files if f.endswith(".json")]
    return sorted(out, key=lambda x: x.as_posix())


def scan_dir(root: str | os.PathLike[str]) -> tuple[list[Path], list[Path]]:
    """提出のフォルダを見る(#90)。(<GPU>/<名前>.json の形の場所にある *.json, 置けない場所・形のファイル)。
    置けるのは <root>/<GPU のフォルダ>/<ファイル> の .json(小文字)だけ。直下・深いフォルダの .json、
    .json でないファイル(.txt・.JSON など)は置けないものに入れる。ファイルを渡せば、それだけを検査する。"""
    p = Path(root)
    if p.is_file() or p.is_symlink():
        return [p], []
    if not p.is_dir():
        return [], []
    ok: list[Path] = []
    bad: list[Path] = []
    for d, dirs, files in os.walk(p, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not (Path(d) / x).is_symlink())
        for f in files:
            fp = Path(d) / f
            depth = len(fp.relative_to(p).parts)
            (ok if f.endswith(".json") and depth == 2 else bad).append(fp)
    key = lambda x: x.as_posix()  # noqa: E731
    return sorted(ok, key=key), sorted(bad, key=key)


def layout_report(path: Path) -> Report:
    """置けない場所・形のファイルの検査結果(誤り layout)。"""
    r = Report(path=path.as_posix())
    r.error(
        "layout",
        tr(
            "提出として置けるのは <GPU のフォルダ>/<日付>-<sha8>.json(.json は小文字)だけです"
            "(ほかの形のファイル・フォルダの直下や深いフォルダには置けません)",
            "Only <GPU folder>/<date>-<sha8>.json (lowercase .json) can be submitted"
            " (no other files, nothing directly in the folder or in deeper folders)",
        ),
    )
    return r


def validate_dirs(
    roots: list[str | os.PathLike[str]],
    db: Database | None = None,
    *,
    today: _date | None = None,
    check_filename: bool = True,
) -> list[Report]:
    """フォルダ(とファイル)をまとめて検査する。置けない場所・形のファイルも誤り(layout)として返す
    (CI の「足したファイルの形」の検査と手元の検査をそろえる。#90)。並びはパスの順。"""
    files: list[Path] = []
    bad: list[Path] = []
    for root in roots:
        ok, ng = scan_dir(root)
        files += ok
        bad += ng
    reports = validate_files(files, db, today=today, check_filename=check_filename)
    reports += [layout_report(b) for b in bad]
    return sorted(reports, key=lambda r: r.path)


# --- まとめる -------------------------------------------------------------------------------------

MAD_SCALE = 1.4826  # 正規分布の標準偏差に合わせる係数
OUTLIER_Z = 3.5  # 外れ値とみなす |x - 中央値| / (1.4826 × MAD)(Iglewicz & Hoaglin の目安)
MAD_FLOOR_REL = 0.02  # MAD の下限(中央値の 2%)。そろいすぎた値で小さな差を外れ値にしない
# 残った値の幅(最大 - 最小)が中央値の 15% 以内なら「そろった」。「そろった」とみなす最小の出どころの数。
# 実測で段階を決める条件(estimate.measured_level。#95)と同じ値を使う
CONSISTENT_REL = _LEVEL_AGREE_REL
MIN_CONSISTENT = MIN_LEVEL_SOURCES


def _settings_key(s: Settings) -> tuple:
    """設定の鍵。精度は書き方をそろえる(BF16 と bf16、fp8_e4m3fn と fp8 は同じ)。
    ステップ数は鍵に入れない(#90): 最大 VRAM は 1 ステップの作業用メモリで決まり、ステップ数では変わらない。
    判定の実測の照合(estimate._settings_match)もステップ数を比べないので、それとそろえた。"""
    keys = ("width", "height", "frames", "seconds")
    return (
        *((k, getattr(s, k)) for k in keys),
        ("precision", norm_precision(s.precision)),
        ("batch", max(1, s.batch or 1)),
    )


def robust_filter(values: list[float]) -> tuple[list[int], list[int], float]:
    """中央値と MAD で外れ値を除く。(残した番号, 外した番号, MAD)。3 件未満は外さない(見分けられない)。"""
    if not values:
        return [], [], 0.0
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    if len(values) < 3:
        return list(range(len(values))), [], mad
    scale = MAD_SCALE * max(mad, MAD_FLOOR_REL * abs(med), 1e-6)
    keep = [i for i, v in enumerate(values) if abs(v - med) / scale <= OUTLIER_Z]
    drop = [i for i in range(len(values)) if i not in keep]
    return keep, drop, mad


def _mode(values: list[Any]) -> Any:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    counts = Counter(vals)
    best = max(counts.values())
    return sorted((v for v, c in counts.items() if c == best), key=str)[0]


def _med(values: list[float | None], digits: int = 2) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), digits) if vals else None


@dataclass
class Submission:
    path: str
    sha256: str
    data: dict[str, Any]

    @property
    def measurement(self) -> Measurement:
        return to_measurement(self.data)


def load_dir(
    root: str | os.PathLike[str], db: Database | None = None
) -> tuple[list[Submission], list[Report]]:
    """フォルダの提出を読み、検査を通ったもの(誤りの無いもの。注意があっても使う)だけ返す
    (同じ内容は 1 件)。(通ったもの, 全部の検査結果)。"""
    files, bad = scan_dir(root)
    reports = validate_files(files, db)
    subs: list[Submission] = []
    for p, r in zip(files, reports, strict=True):
        if r.ok and r.sha256:
            data, _ = read_submission(p)
            if is_failures(data):
                continue  # 失敗の記録は検査するが、まとめ・見積もりには使わない(#132)
            subs.append(Submission(path=p.as_posix(), sha256=r.sha256, data=data))
    return subs, sorted([*reports, *(layout_report(b) for b in bad)], key=lambda r: r.path)


BOT_EMAIL = (
    "41898282+github-actions[bot]@users.noreply.github.com"  # GitHub Actions のボット(intake のコミット)
)
# GitHub のアカウントの数の ID(Issue を書いた人の user.id。名前を変えても変わらない)。数字だけ
SUBMITTED_BY_ID = re.compile(r"^Submitted-by-id:[ \t]*(\d{1,20})[ \t]*$", re.M | re.I)
SUBMITTED_BY = re.compile(
    r"^Submitted-by:[ \t]*github:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))[ \t]*$", re.M | re.I
)
# GitHub が「Create a merge commit」で作るマージのコミットの 1 行目:
# `Merge pull request #N from <owner>/<branch>`。<owner> は PR の元(head)のリポジトリの持ち主の
# GitHub アカウント(フォークならフォークの持ち主)で、GitHub が
# 書く(PR を出した人は変えられない)。人の鍵はこれで決め、コミットのメールアドレス(誰でも書ける)は使わない
PR_MERGE = re.compile(r"Merge pull request #\d+ from ([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(\S+)[ \t]*")
BOT_BRANCH = re.compile(r"measurement/issue-\d+")  # measurement-intake のブランチ
REPO_OWNER = REPO.split("/", 1)[0].lower()  # 決まった値(contrib.REPO の持ち主)。git の remote は見ない


def _git_runner(base: Path):
    import subprocess

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", "-c", "core.quotepath=off", "-C", str(base), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    return git


def submission_origins(
    root: str | os.PathLike[str], subs: list[Submission]
) -> tuple[dict[str, tuple[str | None, str]], bool]:
    """提出ごとの (PR, 人)(#113。内容の sha256 → (PR の鍵 か None, 人の鍵), git の履歴を使えたか)。

    そのファイルを main に足したコミット(first-parent の履歴で。名前の変更はさかのぼる)を探す。
    - GitHub の PR のマージ(1 行目が `Merge pull request #N from <owner>/<branch>`。PR_MERGE): PR の鍵は
      `pr:<マージのコミット>`、人は PR の元の GitHub アカウント `github:<owner>`(GitHub が書く値。コミットの
      メールアドレスは誰でも書けるので使わない)。ただし <owner> がこのリポジトリの持ち主(REPO_OWNER)で
      ブランチが `measurement/issue-<M>`(intake のブランチ)、ファイルを足したコミットが BOT_EMAIL のもので
      `Submitted-by: github:<login>` があるときは、人は `github:<login>`(Issue を書いた人)。
      フォークの同じ名前のブランチは <owner> がフォークの持ち主なので、その持ち主になる
    - それ以外(main に直接・squash・rebase・手で書いたマージ): 人はコミットを書いた人のメールアドレス
      `email:<小文字>`(弱い。実測の PR は GitHub のマージのコミットで入れる決まり。docs/release.md
      「実測の Issue を受け取る(#113)」)
    鍵はメールアドレスを含むので、外に書かない(submission_sources は hash にして使う)。
    git の作業ツリーでない・浅い clone・git が無いときは ({}, False)。まだコミットしていない提出は入れない。
    通信しない(手元の git の履歴を読むだけ)。"""
    base = Path(root).resolve()
    git = _git_runner(base)
    top = git("rev-parse", "--show-toplevel")
    if not top or (git("rev-parse", "--is-shallow-repository") or "").strip() != "false":
        return {}, False
    top_dir = Path(top.strip()).resolve()
    log = git(
        "log",
        "--first-parent",
        "-M",
        "--diff-filter=AR",
        "--name-status",
        "--format=commit %H",
        "--",
        str(base),
    )
    if log is None:
        return {}, False
    # 今の名前 → 提出。新しいコミットから順に見て、名前の変更(R)は前の名前に付け替え、足した(A)ところで止める
    alias: dict[str, Submission] = {}
    for s in subs:
        with contextlib.suppress(ValueError):
            alias[Path(s.path).resolve().relative_to(top_dir).as_posix()] = s
    added: dict[str, tuple[str, str]] = {}
    commit = None
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line[7:].strip()
            continue
        parts = line.split("\t")
        if not commit or len(parts) < 2:
            continue
        if parts[0].startswith("R") and len(parts) == 3 and parts[2] in alias:
            alias[parts[1]] = alias.pop(parts[2])
        elif parts[0] == "A" and parts[1] in alias:
            s = alias.pop(parts[1])
            added.setdefault(s.sha256, (commit, parts[1]))
    cache: dict[tuple[str, str], tuple[str | None, str]] = {}

    def origin(commit: str, path: str) -> tuple[str | None, str]:
        if (commit, path) in cache:
            return cache[(commit, path)]
        parents = (git("rev-list", "--parents", "-n", "1", commit) or "").split()
        first = (git("log", "-1", "--format=%s", commit) or "").strip()
        merge = PR_MERGE.fullmatch(first) if len(parents) > 2 else None
        if merge is None:
            # GitHub の PR のマージでない(main に直接・squash・rebase、または手で書いたマージ):
            # コミットを書いた人のメールアドレス(誰でも書けるので弱い。
            # 実測の PR はマージのコミットで入れる決まり)
            target = commit
            if len(parents) > 2:
                inner = (
                    git(
                        "log",
                        "--diff-filter=A",
                        "--format=%H",
                        f"{commit}^1..{commit}^2",
                        "--",
                        f":(top){path}",
                    )
                    or ""
                ).split()
                target = inner[-1] if inner else parents[2]
            email = (git("log", "-1", "--format=%ae", target) or "").strip().lower()
            out: tuple[str | None, str] = (f"pr:{commit}" if len(parents) > 2 else None, f"email:{email}")
        else:
            owner, branch = merge.group(1).lower(), merge.group(2)
            person = f"github:{owner}"  # PR の元の GitHub アカウント
            if owner == REPO_OWNER and BOT_BRANCH.fullmatch(branch):
                # このリポジトリの中の intake のブランチ: ボットのコミットの Submitted-by(Issue を書いた人)
                inner = (
                    git(
                        "log",
                        "--diff-filter=A",
                        "--format=%H",
                        f"{commit}^1..{commit}^2",
                        "--",
                        f":(top){path}",
                    )
                    or ""
                ).split()
                info = git("log", "-1", "--format=%ae%x00%B", inner[-1] if inner else parents[2]) or ""
                email, _, body = info.partition("\x00")
                trailer = SUBMITTED_BY.search(body)
                uid = SUBMITTED_BY_ID.search(body)
                if trailer and email.strip().lower() == BOT_EMAIL:
                    # 数の ID があればそれ(名前を変えても同じ人)、無ければ(前の版のボット)アカウント名
                    person = f"github-id:{uid.group(1)}" if uid else f"github:{trailer.group(1).lower()}"
            out = (f"pr:{commit}", person)
        cache[(commit, path)] = out
        return out

    return {sha: origin(c, p) for sha, (c, p) in added.items()}, True


def submission_submitters(
    root: str | os.PathLike[str], subs: list[Submission]
) -> tuple[dict[str, str], bool]:
    """提出ごとの送った人の鍵(submission_origins の人。内容の sha256 → 鍵, git の履歴を使えたか)。"""
    origins, ok = submission_origins(root, subs)
    return {sha: person for sha, (_pr, person) in origins.items()}, ok


def submission_sources(root: str | os.PathLike[str], subs: list[Submission]) -> tuple[dict[str, str], bool]:
    """提出ごとの出どころ(#90・#113)。出どころは「同じ PR」と「同じ人」でつながった提出のまとまり
    (submission_origins の PR の鍵と人の鍵を合わせてつないだもの): 1 つの PR の提出は、中のコミットの
    書いた人が何人に見えても 1 つ(#90 と同じ)、1 人の提出は何回の PR・Issue でも 1 つ
    (作者の実測は何行あっても 1 つ、と
    同じ考え方。#95)。値はまとまりの中でいちばん小さい鍵の sha256(16 進。メールアドレス・アカウント名を同梱の
    まとめに書かない)。まだコミットしていないファイルは 1 件で 1 つ(file:<sha256>)。
    (内容の sha256 → 出どころ, git の履歴を使えたか)。git の作業ツリーでない・浅い clone・git が無いときは
    ({}, False)(aggregate はファイルごとに数える)。通信しない。"""
    origins, via_git = submission_origins(root, subs)
    if not via_git:
        return {}, False
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # いちばん小さい鍵を代表にする(決まった値になる)

    for pr, person in origins.values():
        find(person)
        if pr:
            union(pr, person)
    out = {
        sha: hashlib.sha256(f"source:{find(person)}".encode()).hexdigest()
        for sha, (_pr, person) in origins.items()
    }
    for s in subs:
        out.setdefault(s.sha256, f"file:{s.sha256}")  # まだコミットしていないファイルは 1 件で 1 つ
    return out, True


def submitter_count(
    root: str | os.PathLike[str],
    login: str,
    *,
    model_id: str | None,
    gpu_name: str | None,
    user_id: int | str | None = None,
) -> int | None:
    """root(リポジトリの data/contrib)にある、GitHub のアカウント login が送った(実測の Issue、
    またはそのアカウントから出した PR)、同じモデル・同じ GPU(gpu_key)の提出の数
    (intake の PR の本文に書く。#113)。git の履歴が無ければ None。"""
    if not Path(root).is_dir():
        return 0
    subs, _ = load_dir(root)
    same = [
        s
        for s in subs
        if (s.data.get("measurement") or {}).get("model_id") == model_id
        and gpu_key(str(((s.data.get("system") or {}).get("gpu") or {}).get("name") or ""))
        == gpu_key(gpu_name or "")
    ]
    who, via_git = submission_submitters(root, same)
    if not via_git:
        return None
    keys = {f"github:{login.lower()}"}
    if user_id is not None and _USER_ID.fullmatch(str(user_id)):
        keys.add(f"github-id:{user_id}")
    return sum(1 for k in who.values() if k in keys)


def source_id(source: str) -> str:
    """出どころ(送った人の鍵の sha256 か file:<sha256>)の短い ID(まとめの行の source_ids)。"""
    if source.startswith("file:"):
        return "file:" + source[5:17]
    return source[:12]


NONSTANDARD_FILE = "contrib_nonstandard.json"


def nonstandard_submissions() -> dict[str, str]:
    """標準でないとみなす提出(内容の sha256 → 理由。同梱の contrib_nonstandard.json。#131)。
    0.2 秒おきの読み取りで最大 VRAM の山を飛ばした提出など。提出のファイルは書き換えない(sha256 が変わる)。"""
    from .db import bundled_text

    try:
        data = json.loads(bundled_text(NONSTANDARD_FILE))
    except (OSError, ValueError):
        return {}
    subs = data.get("submissions") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in subs.items()} if isinstance(subs, dict) else {}


def aggregate(subs: list[Submission], sources: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """同じモデル・設定・GPU(と測り方・測ったときの一覧のファイル)ごとにまとめる。標準でない提出・
    VRAM を超えた(spilled)提出・失敗の記録は使わない(#132)。標準でないとみなす提出(nonstandard_submissions。
    #131)も使わない。
    sources は提出の内容の sha256 → 出どころ(submission_sources)。無い提出はそれぞれ 1 つの出どころ。

    - error で終わった提出は値に使わず、数だけ数える(n_error)。
    - 最後まで動いた(ok)ものは出どころ(sources。無ければ提出ごと)ごとに中央値をとり、その出どころの値で
      外れ値を除き(robust_filter)、残りの中央値を行の値にする(1 人がたくさん出しても 1 つとして数える)。
    - OOM だけのまとまりは outcome: oom の 1 行にする(見積もりには使わない)。
    結果は決まった順(モデル ID → GPU → 設定)で、同じ入力からは同じ出力になる。"""
    sources = sources or {}
    excluded = nonstandard_submissions()

    def src(sub: Submission) -> str:
        return sources.get(sub.sha256) or f"file:{sub.sha256}"

    groups: dict[tuple, list[Submission]] = {}
    for sub in subs:
        if is_failures(sub.data) or (sub.data.get("measurement") or {}).get("spilled") is True:
            continue  # 失敗の記録・共有メモリへ逃げた実測は、まとめにも見積もりにも使わない(#132)
        m = sub.measurement
        if not m.standard or sub.sha256 in excluded:
            continue
        # GPU 名は書き方の違い(NVIDIA / GeForce の有無・大文字小文字・空白)をそろえる
        # VRAM(1 GB に丸める)も鍵に入れる(RTX 4060 Ti の 8 GB と 16 GB を混ぜない)
        key = (
            m.model_id,
            # 型番 ID があれば ID で(Linux と Windows で名前の違う同じ GPU をまとめる)、無ければ名前で
            getattr(m, "gpu_pci_id", None) or gpu_slug(m.gpu_name),
            round(m.gpu_vram_gb or 0),
            m.vram_metric or "",
            _settings_key(m.settings),
            getattr(m, "files_fp", None) or "",  # 測ったときの一覧のファイルが違えば別の行(#90)
            bool(getattr(m, "settings_override", None)),  # 設定を変えた実測は別の行(#116)
        )
        groups.setdefault(key, []).append(sub)
    out: list[dict[str, Any]] = []
    for key in sorted(groups, key=lambda k: json.dumps(k, default=str)):
        everyone = sorted(groups[key], key=lambda s: s.sha256)
        errors = [s for s in everyone if s.measurement.outcome not in ("ok", "oom")]
        members = [s for s in everyone if s not in errors]
        if not members:
            continue  # error だけの組は行にしない(値に使えるものが無い)
        ms = [s.measurement for s in members]
        ok = [(s, m) for s, m in zip(members, ms, strict=True) if m.outcome == "ok"]
        oom = [(s, m) for s, m in zip(members, ms, strict=True) if m.outcome == "oom"]
        oom_sources = {src(s) for s, _ in oom}
        if ok:
            by_src: dict[str, list[tuple[Submission, Measurement]]] = {}
            for s, m in ok:
                by_src.setdefault(src(s), []).append((s, m))
            names = sorted(by_src)
            values = [statistics.median(m.peak_vram_gb for _, m in by_src[n]) for n in names]
            keep, drop, _ = robust_filter(values)
            kept = [names[i] for i in keep]
            used = [x for n in kept for x in by_src[n]]
            outliers = sorted(s.sha256[:8] for i in drop for s, _ in by_src[names[i]])
            uv = [values[i] for i in keep]
            med = statistics.median(uv)
            mad = statistics.median([abs(v - med) for v in uv])
            rel = (max(uv) - min(uv)) / med if med > 0 else 0.0
            base = [m for _, m in used]
            outcome = "ok"
            n_sources = len(kept)
            runs = n_sources > len(oom_sources)
            source_ids = sorted(source_id(n) for n in kept)
        else:
            used, outliers, base = [], [], [m for _, m in oom]
            uv = [m.peak_vram_gb for m in base]
            med = statistics.median(uv) if uv else 0.0
            mad, rel = 0.0, 0.0
            outcome = "oom"
            n_sources = 0
            runs = False
            source_ids = []
        first = ms[0]
        # consistent は係数の合わせ直し(calibrate)に入れる条件: 別々の出どころが 2 つ以上で、そろっていて、
        # OOM が無い。見積もりは ok の行を 1 件から使う(estimate.usable)
        consistent = outcome == "ok" and not oom and n_sources >= MIN_CONSISTENT and rel <= CONSISTENT_REL
        settings = {k: v for k, v in asdict(first.settings).items() if v is not None}
        steps = _mode([m.settings.steps for m in ms])
        settings.pop("steps", None)
        if steps is not None:
            settings["steps"] = steps  # 表示用(鍵には入れない)。いちばん多いステップ数
        if settings.get("precision"):
            settings["precision"] = norm_precision(settings["precision"])
        gpu_name = _mode([m.gpu_name for m in ms])
        vendor = next((v for v, b in VENDOR_BACKEND.items() if b == _mode([m.gpu_backend for m in ms])), None)
        out.append(
            {
                "model_id": first.model_id,
                "gpu_name": gpu_name,
                "gpu_vram_gb": _med([m.gpu_vram_gb for m in base] or [first.gpu_vram_gb]),
                # 測った GPU の backend(estimate.measurement_vendor が会社を名前より先にこれで決める。
                # Apple の MPS は GPU 名が "mps" になる)
                "gpu_backend": _mode([m.gpu_backend for m in ms]),
                "gpu_pci_id": _mode([getattr(m, "gpu_pci_id", None) for m in ms]),
                # 汎用の名前・名前の代わりの値の GPU は記録として残すだけで、判定に使わない(#90、#101)
                "gpu_known": gpu_known(gpu_name, vendor),
                "files_fp": getattr(first, "files_fp", None),
                # 設定を変えて測った行だけ印を付ける(#116。measure のテンプレートのままの実測の見分け)
                **({"settings_override": True} if getattr(first, "settings_override", None) else {}),
                "peak_vram_gb": round(med, 2),
                "settings": settings,
                "peak_ram_gb": _med([m.peak_ram_gb for m in base]),
                "seconds": _med([m.seconds for m in base], 1),
                "driver": _mode([m.driver for m in base]),
                "comfyui_version": _mode([m.comfyui_version for m in base]),
                "offload": _mode([m.offload for m in base]),
                "source": "community(data/contrib を genmodelfit contrib aggregate でまとめたもの)",
                "date": max((m.date or "" for m in ms), default="") or None,
                "note": (
                    f"ほかの利用者の実測 {len(ms)} 件(使った {len(used)} 件・出どころ {n_sources}・"
                    f"外れ値 {len(outliers)} 件・OOM {len(oom)} 件)の中央値"
                ),
                "outcome": outcome,
                "standard": True,
                "vram_metric": first.vram_metric,
                "origin": ORIGIN_COMMUNITY,
                "n_submissions": len(ms),
                "consistent": consistent,
                "n_used": len(used),
                "n_sources": n_sources,
                # 使った出どころの短い ID(コミットの先頭 12 桁か file:<内容の sha256 の先頭 12 桁>)。
                # 1 つの PR の提出が GPU の型番 ID の有無・測り方で別の行に分かれても、判定は ID の和集合で
                # 出どころを数える(estimate.count_sources。#95)
                "source_ids": source_ids,
                "n_outliers": len(outliers),
                "n_oom": len(oom),
                "n_error": len(errors),
                "runs_evidence": runs,
                "spread_mad_gb": round(mad, 3),
                "spread_rel_pct": round(rel * 100, 1),
                "peak_vram_min_gb": round(min(uv), 2) if uv else None,
                "peak_vram_max_gb": round(max(uv), 2) if uv else None,
                "outlier_submissions": outliers,
                "submissions": [s.sha256[:8] for s in members],
            }
        )
    return out


def community_document(
    rows: list[dict[str, Any]], used: list[Submission] | None = None, sources: dict[str, str] | None = None
) -> dict[str, Any]:
    """同梱の measurements_community.json の中身(日付は提出の最新の日付。作り直しても同じになる)。
    used はまとめに渡した提出、sources はその出どころ(内容の sha256 → 出どころ。aggregate と同じ)。
    submissions に [{sha256, source}] を sha256 の順に残す(どの提出を入れたか・どの出どころとして数えたか。
    まだ入れていない提出を bundled_status で見分け、git の履歴の無い所でも同じまとめを作り直せる。#90)。
    古い版は知らないキーとして読み飛ばす(db.parse_measurements は measurements だけを読む)。"""
    sources = sources or {}
    return {
        "unit": "GiB",
        "source": "community",
        "contrib_schema": CONTRIB_SCHEMA,
        "date": max((r.get("date") or "" for r in rows), default="") or None,
        "method": (
            "data/contrib の提出を、同じモデル・GPU・測り方・設定(ステップ数は除く)・"
            "測ったときの一覧のファイル(files_fp)ごとにまとめる。error の提出は数えるだけ(n_error)。"
            "最後まで動いた(ok)ものの最大 VRAM を出どころ(送った人。git の履歴から読む。無ければ"
            "提出ごと)ごとの中央値にし、"
            f"|x - 中央値| / ({MAD_SCALE} × MAD) > {OUTLIER_Z} を外れ値として除き"
            "(出どころが 3 つ以上のとき。MAD は中央値の "
            f"{MAD_FLOOR_REL:.0%} を下限)、残りの中央値を値にする。ok の行は 1 件から見積もりに使う"
            "(件数は n_used、出どころは n_sources、同じ構成の OOM は n_oom。"
            "OOM の出どころが ok の出どころ以上なら runs_evidence が false)。出どころが "
            f"{MIN_CONSISTENT} 以上で、幅が中央値の {CONSISTENT_REL:.0%} 以内で、OOM が無ければ consistent"
            "(係数の合わせ直しに入れる)。GPU 名が汎用の名前・名前の代わりの値の行は gpu_known が false で、"
            "判定に使わない。GPU の総量を超えた(共有メモリへ逃げた。spilled)提出と失敗の記録、"
            f"{NONSTANDARD_FILE} の提出(標準でないとみなすもの)は使わない"
        ),
        "submissions": [
            {"sha256": h, "source": sources.get(h) or f"file:{h}"}
            for h in sorted({s.sha256 for s in used or []})
        ],
        "measurements": rows,
    }


def write_community(
    rows: list[dict[str, Any]],
    path: str | os.PathLike[str],
    used: list[Submission] | None = None,
    sources: dict[str, str] | None = None,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(community_document(rows, used, sources)), encoding="utf-8", newline="\n")
    return p


def bundled_status(
    root: str | os.PathLike[str], document: dict[str, Any], db: Database | None = None
) -> dict[str, Any]:
    """同梱のまとめ(document)と提出のフォルダ(root)を比べる(#90。まとめは版を出すときに作る)。

    - listed: まとめに入っている提出の数(document の submissions)
    - pending: フォルダにあって検査を通ったが、まだまとめに入っていない提出のパス(マージした後の提出。
      次の版で入れる)
    - missing: まとめに入っているのにフォルダに無い(検査を通らない)提出の hash
    - matches: まとめに入っている提出だけを、記録した出どころでまとめ直すと document と同じになるか
      (git の履歴は読まない。浅い clone・git の外でも同じ結果になる)
    - stale: 測ったときの一覧のファイル(files_fp)が今の一覧(db)と違う行([model_id, gpu_name])。
      判定には使われない(estimate.files_match)。知らせるだけ
    - file_sources: git のコミットでなく提出のファイルごとに数えた出どころ(file:…)の数。版を出すまとめでは
      0 でなければならない(1 人が何件も出すと何人分にも数えられるため。#95。docs/release.md)
    - ok: missing が無く matches(pending・stale は ok を落とさない。数えて知らせるだけ)"""
    p = Path(root)
    subs, reports = load_dir(p, db) if p.is_dir() else ([], [])
    # 古い形(sha256 の文字列だけ)も読む。そのときの出どころは提出ごと
    recorded: dict[str, str] = {}
    for e in (document or {}).get("submissions") or []:
        h = e if isinstance(e, str) else e.get("sha256")
        recorded[h] = (None if isinstance(e, str) else e.get("source")) or f"file:{h}"
    listed = list(recorded)
    by = {s.sha256: s for s in subs}
    used = [by[h] for h in listed if h in by]
    missing = [h for h in listed if h not in by]
    matches = not missing and community_document(aggregate(used, recorded), used, recorded) == document
    stale = []
    if db is not None:
        for row in (document or {}).get("measurements") or []:
            entry = db.get(row.get("model_id")) if isinstance(row, dict) else None
            if entry is not None and not files_match(Measurement.from_dict(row), entry):
                stale.append([row.get("model_id"), row.get("gpu_name")])
    return {
        "listed": len(listed),
        "valid": len(subs),
        "invalid": [r.path for r in reports if not r.ok],
        "pending": [s.path for s in subs if s.sha256 not in set(listed)],
        "missing": missing,
        "stale": stale,
        "matches": matches,
        "file_sources": file_source_count(document),
        "ok": matches,
    }


def file_source_count(document: dict[str, Any] | None) -> int:
    """まとめ(document)の中の、ファイルごとに数えた出どころ(file:…)の数(提出の記録と行の source_ids)。"""
    ids = set()
    for e in (document or {}).get("submissions") or []:
        src = f"file:{e}" if isinstance(e, str) else (e.get("source") or f"file:{e.get('sha256')}")
        if str(src).startswith("file:"):
            ids.add(str(src))
    for row in (document or {}).get("measurements") or []:
        for sid in (row.get("source_ids") or []) if isinstance(row, dict) else []:
            if str(sid).startswith("file:"):
                ids.add("file:" + str(sid)[5:17])
    return len({i[:17] for i in ids})


# --- 精度の点検(毎週の表) ---------------------------------------------------------------------

DEFAULT_THRESHOLD_PCT = 25.0


def accuracy_report(db: Database, threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> dict[str, Any]:
    """校正(leave-one-out)をやり直し、種類ごとの誤差と閾値の判定を返す。

    閾値を超えたかは、(1) 評価できる種類(張り付いていない実測が 2 件以上)の LOO 誤差と、
    (2) ほかの利用者の実測(係数の合わせに使わない測り方のもの)を全件の係数で当てた誤差、で見る。
    作者の照合の行(RTX 3070 の 2 件など)と、作業用メモリだけの実測(#100)は表に出すが、閾値の判定には入れない
    (docs/accuracy.md に既知の外れとして書いた)。"""
    from .calibrate import calibrate

    result = calibrate(db, date="-")
    sm = result["summary"]
    evaluable = set(sm["evaluable_modalities"])
    by: dict[str, dict[str, Any]] = {}
    breaches: list[dict[str, Any]] = []

    def slot(mod: str) -> dict[str, Any]:
        return by.setdefault(
            mod,
            {
                "loo_n": 0,
                "loo_errors_pct": [],
                "evaluable": mod in evaluable,
                "check_n": 0,
                "check_errors_pct": [],
            },
        )

    for x in result["loo"]:
        if x["censored"]:
            continue
        b = slot(x["modality"])
        b["loo_n"] += 1
        b["loo_errors_pct"].append(x["loo_error_pct"])
        if x["modality"] in evaluable and abs(x["loo_error_pct"]) > threshold_pct:
            breaches.append(
                {
                    "kind": "loo",
                    "model_id": x["model_id"],
                    "gpu_name": x.get("gpu_name"),
                    "origin": x.get("origin"),
                    "error_pct": x["loo_error_pct"],
                }
            )
    for x in result.get("check", []):
        if x["censored"]:
            continue
        b = slot(x["modality"])
        b["check_n"] += 1
        b["check_errors_pct"].append(x["error_pct"])
        # 作業用メモリだけの実測(#100。quantity が work)は照合の表に出すだけで、閾値の判定に入れない
        # (段ごとの山を測れるようになるまで、作業用メモリの見積もりとは粗くしか比べられない)
        work_only = x.get("quantity") == "work"
        if x.get("origin") == ORIGIN_COMMUNITY and not work_only and abs(x["error_pct"]) > threshold_pct:
            breaches.append(
                {
                    "kind": "community_check",
                    "model_id": x["model_id"],
                    "gpu_name": x.get("gpu_name"),
                    "origin": x.get("origin"),
                    "error_pct": x["error_pct"],
                }
            )
    for b in by.values():
        for k in ("loo", "check"):
            errs = [abs(e) for e in b.pop(f"{k}_errors_pct")]
            b[f"{k}_mean_abs_pct"] = round(sum(errs) / len(errs), 1) if errs else None
            b[f"{k}_max_abs_pct"] = round(max(errs), 1) if errs else None
    own = _own_measurements(db)
    from .estimate import usable

    comm = [m for m in db.measurements if getattr(m, "origin", None) == ORIGIN_COMMUNITY and usable(m)]
    fit_changes: list[dict[str, Any]] = []
    for mid in sorted({m.model_id for m in comm}):
        entry = db.get(mid)
        if entry is not None:
            fit_changes += community_effect(entry, own, [m for m in comm if m.model_id == mid])[1]
    return {
        "threshold_pct": threshold_pct,
        "fit_changes": fit_changes,
        "breach": bool(breaches),
        "breaches": breaches,
        "n_fit": result["n"],
        "coefficients": result["coefficients"],
        "summary": sm,
        "by_modality": dict(sorted(by.items())),
        "loo": result["loo"],
        "check": result.get("check", []),
    }


def report_db(base: Database, community_rows: list[dict[str, Any]]) -> Database:
    """点検に使う一覧: 同梱の一覧のモデル + 同梱の作者の実測 + 今回まとめたほかの利用者の実測。
    この PC のキャッシュの実測と、同梱の古いまとめ(measurements_community.json)は使わない
    (毎回同じ結果にする)。"""
    from .db import bundled_text, parse_measurements

    maint = parse_measurements(json.loads(bundled_text("measurements.json")))
    comm = [Measurement.from_dict(r) for r in community_rows]
    return Database(
        models=base.models,
        measurements=maint + comm,
        generated_at=base.generated_at,
        sources=base.sources,
        origin=base.origin,
    )


__all__ = [
    "CONTRIB_SCHEMA",
    "Report",
    "Submission",
    "accuracy_report",
    "aggregate",
    "build_submission",
    "bundled_status",
    "community_document",
    "content_hash",
    "extract_issue_json",
    "gpu_known",
    "gpu_slug",
    "intake_issue",
    "load_dir",
    "measurements_from_file",
    "privacy_findings",
    "robust_filter",
    "scan_dir",
    "scrub_text",
    "share_check",
    "share_urls",
    "submission_origins",
    "submission_sources",
    "submission_submitters",
    "submitter_count",
    "suggested_path",
    "validate_dirs",
    "validate_files",
    "validate_submission",
    "write_community",
    "write_submission",
]
