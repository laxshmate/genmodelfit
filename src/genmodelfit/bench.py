# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""bench: 動いている ComfyUI に HTTP でワークフローを送り、最大 VRAM・メモリ・時間を測る。

ComfyUI のコードは import しない(GPL のため)。使うのは HTTP の API だけ:
`/system_stats` `/object_info` `/queue` `/free` `/prompt` `/history/<id>` `/interrupt`
`/upload/image` `/templates/<name>.json` `/internal/logs/raw`。

測り方:
- 最大 VRAM(peak_vram_gb): 生成中に `/system_stats` を短い間隔で読み、torch が確保した量
  (`torch_vram_total`)の最大値。ComfyUI のプロセスの分で、CUDA の文脈(約 0.3〜0.5 GB)は含まない
- GPU 全体: `nvidia-smi` の使用量の最大値から、開始前の値を引いたもの(他のアプリの増減も入る)。note に書く。
  NVIDIA 以外は各社の道具(amd-smi・rocm-smi・xpu-smi・Windows の性能カウンタ・ioreg。gpustat.py)で読む
- メモリ(peak_ram_gb): ポートで待ち受けている ComfyUI のプロセス(と子)の常駐メモリの最大値(同じ PC のときだけ)
- 時間(seconds): ワークフローを送ってから終わるまで(モデルの読み込みを含む)

他の人の仕事を邪魔しないための順番:
1. 読むだけの確認(キュー・GPU の使用率・モデルのファイル)をすべて先に済ませ、断るならここで断る
2. そのあとで初めて書き換える要求(画像のアップロード、`--free` のときの `/free`)を送る
3. 送る直前にもう一度キューを確かめる。中断は自分のプロンプトだけ(`/interrupt` に prompt_id を付ける)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import psutil

if TYPE_CHECKING:
    import requests

from . import hardware
from ._version import __version__
from .db import cache_dir
from .estimate import gpu_vendor
from .fetch import make_session
from .gpustat import Probe
from .hardware import VENDOR_LABEL
from .hardware import find_nvidia_smi as _nvidia_smi
from .i18n import tr
from .models import Measurement
from .workflow import (
    FRAME_KEYS,
    SECONDS_KEYS,
    WorkflowError,
    apply_settings,
    check_inputs,
    effective_settings,
    is_api_format,
    reachable_nodes,
    set_output_prefix,
    template_input_url,
    template_url,
    ui_to_api,
    upload_inputs,
)

GIB = 1024**3
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
# 送ってから ComfyUI が実行を始めるまでの許容(秒)。超えたら、前に他の仕事が動いたとみなす
START_DELAY_S = 10.0
# --set で変えても「標準」のままとみなす入力(大きさ・長さ・ステップ数と、メモリに効かない seed)
STANDARD_SET_KEYS = {"width", "height", "steps", "seed", "noise_seed", *FRAME_KEYS, *SECONDS_KEYS}
# torch の確保量(/system_stats)を読む間隔(秒)。#131: 作業用メモリの山は短い(SDXL 1024px の VAE の復号の山は
# 約 0.19 秒、ACE-Step 10 秒の山は 0.02〜0.05 秒。RTX 3070)。山は実行の終わりに ComfyUI が torch の空きを
# 返すので消える。0.2 秒ごとでは山を飛ばす回があり、SDXL 1024px で 2.28 GB と 1.28 GB に揺れた。
# 読んでから次まで 0.015 秒待つ。答えを待つ時間を足すと、実際は約 0.03 秒ごと
# (RTX 3070・Windows で 1 秒に 30〜36 回)。
# この速さで読んでも、ステップの時間・復号の時間は変わらなかった(docs/accuracy.md「測り方の揺れ(#131)」)
POLL_INTERVAL = 0.015
# 常駐メモリ(プロセスの木をたどる。重い)を読む間隔(秒)。VRAM より遅くてよい
RAM_INTERVAL = 0.2
# 一瞬の山(#131): 最大値に近い値(BRIEF_PEAK_COUNT 回目に大きい値)との差がこれを超えたら、最大値は
# 読む間隔より短い山で、測るたびに揺れる。標準にしない(照合・段階に使わない)
BRIEF_PEAK_COUNT = 3  # 3 回見た = 山が読む間隔の 2 倍以上続いた = どの回でも 1 回は読める
BRIEF_PEAK_MIN_GIB = 0.1  # 差がこれ以下なら、揺れても照合に効かない
BRIEF_PEAK_SHARE = 0.1  # 最大値に対する差の割合
BRIEF_PEAK_MIN_SAMPLES = 10  # 読めた回がこれより少なければ決めない


class BenchError(RuntimeError):
    """測れなかった理由(利用者に見せる文)。failure は送れる失敗の記録の材料(#132。時間切れ・ComfyUI が
    ワークフローを受け付けなかったときだけ。{outcome, seconds, timeout_s, error_type, node_type})。
    文(パスが入りうる)は failure に入れない。"""

    def __init__(self, *args: Any, failure: dict[str, Any] | None = None):
        super().__init__(*args)
        self.failure = failure


# --- ComfyUI との HTTP ------------------------------------------------------------------------


def _queue_ids(items: Any) -> list[str]:
    """/queue の queue_running / queue_pending の並び([番号, prompt_id, ...])から ID を取る。"""
    return [str(x[1]) for x in items or [] if isinstance(x, list) and len(x) > 1]


class ComfyClient:
    def __init__(self, url: str, session: requests.Session | None = None, timeout: float = 30):
        self.url = url.rstrip("/")
        self.s = session or make_session()
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw: Any) -> requests.Response:
        import requests

        kw.setdefault("timeout", self.timeout)
        try:
            return self.s.request(method, self.url + path, **kw)
        except requests.ConnectionError as e:
            raise BenchError(
                tr(
                    f"ComfyUI({self.url})に接続できません(止まったか、起動していません)",
                    f"Cannot connect to ComfyUI ({self.url}; it stopped or is not running)",
                )
            ) from e
        except requests.RequestException as e:
            raise BenchError(
                tr(
                    f"ComfyUI({self.url})と通信できません({type(e).__name__})",
                    f"Cannot talk to ComfyUI ({self.url}; {type(e).__name__})",
                )
            ) from e

    def get_json(self, path: str) -> Any:
        r = self._req("GET", path)
        if r.status_code in (401, 403):
            raise BenchError(
                tr(
                    f"ComfyUI({self.url})がログイン(認証)を求めています({r.status_code})。genmodelfit は"
                    "認証の付いた ComfyUI では測れません(認証の無い ComfyUI を --url で指定してください)",
                    f"ComfyUI ({self.url}) asks for a sign-in ({r.status_code}). genmodelfit cannot measure"
                    " on a ComfyUI behind authentication (give one without it with --url)",
                )
            )
        if r.status_code != 200:
            raise BenchError(
                tr(
                    f"ComfyUI の {path} が {r.status_code} を返しました",
                    f"ComfyUI {path} returned {r.status_code}",
                )
            )
        try:
            return r.json()
        except ValueError as e:
            raise BenchError(
                tr(f"ComfyUI の {path} の応答が JSON ではありません", f"ComfyUI {path} did not return JSON")
            ) from e

    def system_stats(self) -> dict[str, Any]:
        return self.get_json("/system_stats")

    def object_info(self) -> dict[str, Any]:
        return self.get_json("/object_info")

    def queue(self) -> dict[str, Any]:
        return self.get_json("/queue")

    def queue_ids(self) -> tuple[list[str], list[str]]:
        """(実行中の ID, 待っている ID)。"""
        q = self.queue()
        return _queue_ids(q.get("queue_running")), _queue_ids(q.get("queue_pending"))

    def free(self) -> None:
        self._req("POST", "/free", json={"unload_models": True, "free_memory": True})

    def interrupt(self, prompt_id: str) -> None:
        """自分のプロンプトだけを止める(prompt_id を付けると、それが実行中のときだけ止まる)。"""
        self._req("POST", "/interrupt", json={"prompt_id": prompt_id})

    def delete_from_queue(self, prompt_id: str) -> None:
        self._req("POST", "/queue", json={"delete": [prompt_id]})

    def submit(self, workflow: dict[str, Any], client_id: str) -> str:
        r = self._req("POST", "/prompt", json={"prompt": workflow, "client_id": client_id})
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or "prompt_id" not in body:
            raise BenchError(
                tr("ComfyUI がワークフローを受け付けませんでした: ", "ComfyUI rejected the workflow: ")
                + _prompt_error_text(body, r),
                failure=_prompt_error_kind(body, workflow),
            )
        return str(body["prompt_id"])

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        data = self.get_json(f"/history/{prompt_id}")
        return data.get(prompt_id) if isinstance(data, dict) else None

    def template(self, name: str) -> dict[str, Any] | None:
        """サーバーが持つテンプレート(版がサーバーと合う)。無ければ None。"""
        r = self._req("GET", f"/templates/{name}.json")
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) and "nodes" in data else None

    def upload_image(self, filename: str, content: bytes) -> str:
        """入力の画像を送る。上書きはしない(同じ名前の別の画像があれば、ComfyUI が別の名前を付ける)。"""
        r = self._req(
            "POST",
            "/upload/image",
            files={"image": (filename, content, "application/octet-stream")},
            data={"type": "input"},
        )
        if r.status_code != 200:
            raise BenchError(
                tr(
                    f"入力の画像をアップロードできません({r.status_code}): {filename}",
                    f"Cannot upload the input image ({r.status_code}): {filename}",
                )
            )
        try:
            return str(r.json().get("name") or filename)
        except ValueError:
            return filename

    def log_entries(self) -> list[dict[str, Any]]:
        """ComfyUI のログ(新しめの版だけ。無ければ空)。"""
        try:
            r = self._req("GET", "/internal/logs/raw")
            if r.status_code == 200:
                entries = r.json().get("entries")
                return entries if isinstance(entries, list) else []
        except (BenchError, ValueError, AttributeError):
            pass
        return []


def _prompt_error_text(body: dict[str, Any], r: requests.Response) -> str:
    parts: list[str] = []
    err = body.get("error")
    if isinstance(err, dict):
        parts.append(str(err.get("message") or err.get("type") or err))
        if err.get("details"):
            parts.append(str(err["details"]))
    for nid, ne in (body.get("node_errors") or {}).items():
        for e in (ne or {}).get("errors") or []:
            parts.append(
                tr(
                    f"ノード {nid}({ne.get('class_type')}): {e.get('message')} {e.get('details') or ''}",
                    f"node {nid} ({ne.get('class_type')}): {e.get('message')} {e.get('details') or ''}",
                ).strip()
            )
    return " / ".join(parts) if parts else f"HTTP {r.status_code}"


def _class_types(workflow: Any) -> set[str]:
    """ワークフロー(API 形式)にあるノードの種類。失敗の記録には、送ったワークフローにある名前だけを入れる。"""
    if not isinstance(workflow, dict):
        return set()
    return {
        str(n.get("class_type")) for n in workflow.values() if isinstance(n, dict) and n.get("class_type")
    }


NOT_A_MODEL_ERROR = frozenset({"value_not_in_list"})  # この PC の事情(ファイルが無いなど)。送らない


def _prompt_error_kind(body: Any, workflow: Any) -> dict[str, Any] | None:
    """ComfyUI が受け付けなかったときの失敗の記録の材料(#132): node_errors の type(無ければ error の type)と
    ノードの種類(送ったワークフローにあるものだけ)。文(message・details)は入れない。"""
    body = body if isinstance(body, dict) else {}
    kinds: list[str] = []
    node_type = None
    # value_not_in_list(選んだファイルが ComfyUI に無い = この PC にモデルのファイルが無い)はモデルの性質で
    # ないので、失敗の記録にしない(#132)
    for ne in (body.get("node_errors") or {}).values():
        if not isinstance(ne, dict):
            continue
        node_type = node_type or ne.get("class_type")
        kinds += [str(e.get("type")) for e in ne.get("errors") or [] if isinstance(e, dict) and e.get("type")]
    err = body.get("error")
    if not kinds and isinstance(err, dict) and err.get("type"):
        kinds.append(str(err["type"]))
    if NOT_A_MODEL_ERROR & set(kinds):
        return None
    return {
        "outcome": "error",
        "error_type": kinds[0] if kinds else None,
        "node_type": node_type if node_type in _class_types(workflow) else None,
    }


def cancel_own_prompt(client: ComfyClient, prompt_id: str) -> str:
    """自分のプロンプトだけを取り消す。実行中なら中断、待っているならキューから消す。

    実行中のものが他の人の仕事なら中断しない(`/interrupt` は ComfyUI 全体に効くことがあるため)。
    返り値: interrupted / deleted / none(確かめられなかった)。
    """
    try:
        running, _pending = client.queue_ids()
    except BenchError:
        running = None
    try:
        if running is not None and prompt_id in running:
            client.interrupt(prompt_id)
            return "interrupted"
        client.delete_from_queue(prompt_id)  # 待っている自分の分だけ(無ければ何も起きない)
        return "deleted"
    except BenchError:
        return "none"


# --- 計測 -------------------------------------------------------------------------------------


@dataclass
class Peaks:
    torch_vram: int = 0  # bytes(/system_stats の torch_vram_total の最大)
    device_used: int = 0  # bytes(/system_stats の vram_total - vram_free の最大)
    gpu_used_mib: float = 0.0  # nvidia-smi の使用量の最大(MiB)
    ram: int = 0  # bytes(ComfyUI のプロセスの常駐メモリの最大)
    samples: int = 0
    smi_samples: int = 0


def _end_process(p: subprocess.Popen[str]) -> None:
    """自分で起動した子プロセスを確実に終わらせて回収する。"""
    with contextlib.suppress(OSError):
        p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            p.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=5)
    if p.stdout is not None:
        with contextlib.suppress(OSError, ValueError):
            p.stdout.close()


class Monitor:
    """生成中の最大値を裏のスレッドで集める。"""

    def __init__(
        self,
        client: ComfyClient,
        proc: psutil.Process | None,
        gpu_id: str | None,
        interval: float = POLL_INTERVAL,
        use_smi: bool = True,
        other_used=None,
        ram_interval: float = RAM_INTERVAL,
    ):
        self.client = client
        self.proc = proc
        self.gpu_id = gpu_id  # nvidia-smi の -i に渡す値(UUID か番号)
        self.interval = interval
        self.use_smi = use_smi
        # NVIDIA 以外: GPU 全体の使用中のメモリ(MiB)を 1 回読む関数(gpustat.Probe.used_mib)。約 1 秒ごとに呼ぶ
        self.other_used = other_used
        self.ram_interval = ram_interval
        self.peaks = Peaks()
        # 読めた torch の確保量(bytes)を順に(一瞬の山の判定。steady_peak)
        self.torch_values: list[int] = []
        self._next_ram = 0.0
        self.last_used_mib: float | None = None  # nvidia-smi が読んだ最後の GPU 全体の使用量(MiB)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._smi: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def sample_once(self, *, ram: bool = True) -> None:
        with contextlib.suppress(BenchError, ValueError, KeyError, TypeError):
            dev = (self.client.system_stats().get("devices") or [{}])[0]
            with self._lock:
                p = self.peaks
                torch_now = int(dev.get("torch_vram_total") or 0)
                p.torch_vram = max(p.torch_vram, torch_now)
                self.torch_values.append(torch_now)
                used = int(dev.get("vram_total") or 0) - int(dev.get("vram_free") or 0)
                p.device_used = max(p.device_used, used)
                p.samples += 1
        if ram and self.proc is not None:
            with contextlib.suppress(psutil.Error):
                rss = self.proc.memory_info().rss
                for c in self.proc.children(recursive=True):
                    with contextlib.suppress(psutil.Error):
                        rss += c.memory_info().rss
                with self._lock:
                    self.peaks.ram = max(self.peaks.ram, rss)

    def _poll(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            ram = now >= self._next_ram
            if ram:
                self._next_ram = now + self.ram_interval
            self.sample_once(ram=ram)
            self._stop.wait(self.interval)

    def _read_smi(self) -> None:
        exe = _nvidia_smi()
        if not exe:
            return
        cmd = [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-lms", "200"]
        if self.gpu_id is not None:
            cmd += ["-i", str(self.gpu_id)]
        # stop() と取り合わないよう、止める合図の確認と起動を同じ鍵の中で行う
        with self._lock:
            if self._stop.is_set():
                return
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return
            self._smi = proc
        try:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                with contextlib.suppress(ValueError):
                    v = float(line.strip().split(",")[0])
                    with self._lock:
                        self.peaks.gpu_used_mib = max(self.peaks.gpu_used_mib, v)
                        self.peaks.smi_samples += 1
                        self.last_used_mib = v
                if self._stop.is_set():
                    break
        finally:
            _end_process(proc)

    def _read_other(self) -> None:
        while not self._stop.is_set():
            v = None
            with contextlib.suppress(Exception):  # 道具の失敗で測定を止めない
                v = self.other_used()
            if v is not None:
                with self._lock:
                    self.peaks.gpu_used_mib = max(self.peaks.gpu_used_mib, v)
                    self.peaks.smi_samples += 1
            self._stop.wait(1.0)

    def start(self) -> None:
        targets = [self._poll] + ([self._read_smi] if self.use_smi else [])
        if self.other_used is not None:
            targets.append(self._read_other)
        for t in targets:
            th = threading.Thread(target=t, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> Peaks:
        self._stop.set()
        with self._lock:
            proc = self._smi
        if proc is not None:  # 読み取りの待ちを解く(回収は _read_smi の finally で行う)
            with contextlib.suppress(OSError):
                proc.terminate()
        for th in self._threads:
            th.join(timeout=10)
        self.sample_once()
        return self.peaks


def steady_peak(values: list[int], count: int = BRIEF_PEAK_COUNT) -> int:
    """count 回目に大きい値(読めた回が count より少なければ一番小さい値。無ければ 0)。#131: これと最大値の差が
    大きければ、最大値は読む間隔より短い一瞬の山。"""
    if not values:
        return 0
    top = sorted(values, reverse=True)
    return top[min(count, len(top)) - 1]


def brief_peak(values: list[int]) -> tuple[int, int] | None:
    """一瞬の山なら (最大値, count 回目に大きい値)、そうでなければ None(#131)。
    読めた回が BRIEF_PEAK_MIN_SAMPLES より少なければ決めない(None。実行が読む間隔の数回分より短く、
    山の長さを比べられない)。"""
    if len(values) < BRIEF_PEAK_MIN_SAMPLES:
        return None
    peak, steady = max(values), steady_peak(values)
    if peak - steady > max(BRIEF_PEAK_MIN_GIB * GIB, BRIEF_PEAK_SHARE * peak):
        return peak, steady
    return None


def _smi_query(args: list[str]) -> str | None:
    exe = _nvidia_smi()
    if not exe:
        return None
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def gpu_used_now(gpu_id: str | None) -> tuple[float, float] | None:
    """nvidia-smi の (使用量 MiB, 使用率 %)。無ければ None。"""
    args = ["--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"]
    if gpu_id is not None:
        args += ["-i", str(gpu_id)]
    out = _smi_query(args)
    if not out:
        return None
    try:
        used, util = (float(x) for x in out.strip().splitlines()[0].split(",")[:2])
    except (ValueError, IndexError):
        return None
    return used, util


def others_vram_limit(total_bytes: int) -> float:
    """ComfyUI 以外が使ってよい VRAM(バイト。#124): 1.5 GiB か全体の 20% の大きいほう。"""
    return max(OTHERS_VRAM_MIN, OTHERS_VRAM_SHARE * total_bytes)


def vram_in_use(name: str | None = None) -> tuple[int, int] | None:
    """nvidia-smi の GPU の (使用中のバイト, 全体のバイト)。読めなければ None(nvidia-smi が無い・答えない・
    変な出力でも例外にしない。L2a)。自分で ComfyUI を起動する前に、ほかのプログラムが VRAM を使っていないかを
    見るのに使う(#124)。GPU が 2 枚以上なら(L2b)、CUDA_VISIBLE_DEVICES の最初の番号、
    無ければ name と同じ名前、無ければいちばん大きいもの(ComfyUI が使う GPU と数える GPU をそろえる)。"""
    from .estimate import gpu_key

    out = _smi_query(["--query-gpu=index,name,memory.used,memory.total", "--format=csv,noheader,nounits"])
    rows = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            rows.append((parts[0], parts[1], int(float(parts[2]) * 1024**2), int(float(parts[3]) * 1024**2)))
        except ValueError:
            continue
    if not rows:
        return None
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")[0].strip()
    pick = next((r for r in rows if visible and r[0] == visible), None)
    if pick is None and name:
        pick = next((r for r in rows if gpu_key(r[1]) == gpu_key(name)), None)
    if pick is None:
        pick = max(rows, key=lambda r: r[3])
    return pick[2], pick[3]


def smi_gpus() -> list[dict[str, Any]]:
    """nvidia-smi の GPU の一覧(index, uuid, name, driver)。"""
    out = _smi_query(["--query-gpu=index,uuid,name,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[0].isdigit():
            gpus.append({"index": int(parts[0]), "uuid": parts[1], "name": parts[2], "driver": parts[3]})
    return gpus


def _gpu_app_pids(gpu_uuid: str | None) -> set[int]:
    """その GPU を使っているプロセスの PID(nvidia-smi の compute apps。uuid が分からなければすべて)。"""
    return {pid for pid, u in smi_compute_apps() if gpu_uuid is None or u == gpu_uuid}


_SMI_PROC = re.compile(r"^\|\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(C\+G|C|G)\s", re.M)


def smi_process_types() -> dict[int, str]:
    """nvidia-smi の表(Processes)の PID → 種類(C・G・C+G)。Windows の WDDM では compute apps に画面の
    アプリも出るので、種類で見分ける(#124)。読めなければ空。"""
    out = _smi_query([])
    return {int(pid): t for pid, t in _SMI_PROC.findall(out or "")}


# 種類を読めないうちに消えたプロセスで、GPU 全体がこれより増えたら数値が狂ったとみる(MiB)
SHORT_LIVED_RISE_MIB = 1024.0


class GpuAppWatch:
    """測っているあいだに現れた GPU のプロセスを、見つけたときにすぐ種類(C・C+G・G)と名前を読んで
    覚える(#124)。すぐ消える計算のプロセスも取りこぼさない。見えていたあいだの GPU 全体の使用量の最大と、
    現れる直前の量も覚える(種類を読めないまま消えたものを見分けるため)。"""

    def __init__(
        self,
        gpu_uuid: str | None,
        before: set[int],
        ours: Any = lambda: set(),
        used: Any = lambda: None,
    ):
        self.gpu_uuid = gpu_uuid
        self.before = set(before)
        self.ours = ours  # ComfyUI のプロセスの木の PID を返す関数
        self.used = used  # GPU 全体の今の使用量(MiB。Monitor が読んだ最後の値。nvidia-smi を増やさない)
        self.seen: dict[int, dict[str, Any]] = {}
        self.last_used: float | None = None

    def sample(self) -> None:
        now = _gpu_app_pids(self.gpu_uuid) - self.before - self.ours()
        used = self.used()
        fresh = now - set(self.seen)
        unknown = {p for p in now if p in self.seen and self.seen[p]["type"] is None}
        types = smi_process_types() if (fresh or unknown) else {}
        for p in fresh:
            self.seen[p] = {
                "type": types.get(p),
                "name": _process_names({p}),
                "before": self.last_used,
                "peak": used,
            }
        for p in now:
            info = self.seen[p]
            if info["type"] is None:
                info["type"] = types.get(p)
            if used is not None and (info["peak"] is None or used > info["peak"]):
                info["peak"] = used
        if used is not None:
            self.last_used = used

    def interfering(self) -> dict[int, str]:
        """数値を狂わせたとみるもの(PID → 名前): 計算だけのプロセス(種類 C。torch で CUDA を使う python
        など)。C+G・G(ブラウザ・画面のアプリ)は数えない。種類を読めないまま消えたものは、見えていたあいだに
        GPU 全体が現れる直前より SHORT_LIVED_RISE_MIB を超えて増えたときだけ数える。"""
        out = {}
        for p, info in self.seen.items():
            t, before, peak = info["type"], info["before"], info["peak"]
            if t == "C" or (
                t is None and before is not None and peak is not None and peak - before > SHORT_LIVED_RISE_MIB
            ):
                out[p] = info["name"]
        return out


def _interfering(new_apps: set[int], base_smi=None, peaks=None) -> set[int]:
    """(前からの形)今見えている新しいプロセスのうち、計算だけのもの(種類 C)。"""
    if not new_apps:
        return set()
    types = smi_process_types()
    return {p for p in new_apps if types.get(p) == "C"}


def _process_tree(proc) -> set[int]:
    """ComfyUI のプロセスとその子の PID(分からなければ空)。"""
    if proc is None:
        return set()
    out = {proc.pid}
    with contextlib.suppress(Exception):
        out |= {c.pid for c in proc.children(recursive=True)}
    return out


def _process_names(pids: set[int]) -> str:
    names = []
    for pid in sorted(pids)[:5]:
        try:
            names.append(f"{psutil.Process(pid).name()} {pid}")
        except Exception:
            names.append(f"PID {pid}")
    return tr("、", ", ").join(names)


def smi_compute_apps() -> list[tuple[int, str]]:
    """GPU を使っているプロセス (pid, GPU の UUID)。Windows の WDDM では空のことが多い。"""
    out = _smi_query(["--query-compute-apps=pid,gpu_uuid", "--format=csv,noheader,nounits"])
    apps = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            apps.append((int(parts[0]), parts[1]))
    return apps


def match_gpu(
    name: str, gpus: list[dict[str, Any]], proc: psutil.Process | None, local: bool
) -> tuple[dict[str, Any] | None, str | None]:
    """ComfyUI が使っている GPU を nvidia-smi の一覧から選ぶ。(GPU, 選べなかった理由)。

    同じ名前の GPU が複数あるときは、ComfyUI のプロセスが載っている GPU(UUID)で選ぶ。
    """
    if not local:
        return (
            None,
            tr(
                "ComfyUI が別の PC で動いているので、この PC の nvidia-smi では GPU の空きを確かめられません",
                "ComfyUI runs on another PC, so nvidia-smi on this PC cannot check whether the GPU is free",
            ),
        )
    if not gpus:
        return None, tr(
            "nvidia-smi が使えないので、GPU が他の仕事で使われていないかを確かめられません",
            "nvidia-smi is not available, so whether the GPU is busy with other work cannot be checked",
        )
    same = [g for g in gpus if g["name"] == name]
    if not same:
        return None, tr(
            f"nvidia-smi の一覧に ComfyUI の GPU({name})が見つかりません",
            f"ComfyUI's GPU ({name}) is not in the nvidia-smi list",
        )
    if len(same) == 1:
        return same[0], None
    if proc is not None:
        pids = {proc.pid}
        with contextlib.suppress(psutil.Error):
            pids.update(c.pid for c in proc.children(recursive=True))
        uuids = {u for pid, u in smi_compute_apps() if pid in pids}
        hit = [g for g in same if g["uuid"] in uuids]
        if len(hit) == 1:
            return hit[0], None
    return None, tr(
        f"同じ名前の GPU({name})が {len(same)} 枚あり、ComfyUI がどれを使っているか分かりません",
        f"There are {len(same)} GPUs named {name}; which one ComfyUI uses is unknown",
    )


def sample_gpu(gpu_id: str, count: int = 5, every: float = 0.5) -> list[tuple[float, float]]:
    """GPU の (使用量 MiB, 使用率 %) を約 count × every 秒のあいだ何回か読む。"""
    out: list[tuple[float, float]] = []
    for i in range(count):
        v = gpu_used_now(gpu_id)
        if v is not None:
            out.append(v)
        if i < count - 1:
            time.sleep(every)
    return out


def device_name(dev: dict[str, Any]) -> str:
    """'cuda:0 NVIDIA GeForce RTX 3070 : cudaMallocAsync' → 'NVIDIA GeForce RTX 3070'。"""
    name = str(dev.get("name") or "")
    name = re.sub(r"^\w+:\d+\s+", "", name)
    return name.split(" : ")[0].strip() or "unknown"


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


BACKEND_OF_VENDOR = {"nvidia": "cuda", "amd": "rocm", "intel": "xpu", "apple": "mps"}
# 使用率を読む道具が無いとき、ComfyUI 以外が GPU のメモリをこの割合より多く使っていたら、ほかのアプリが
# GPU を使っているとみなして断る(統合メモリの Apple では、メインメモリと同じなので見ない)
OTHER_APPS_VRAM_SHARE = 0.5
# NVIDIA で、空けたあとに ComfyUI 以外が使ってよい VRAM(#124): 1.5 GiB か全体の 20% の大きいほう
# (画面の表示・ComfyUI の CUDA の分を含む)。これを超えたら測らない
OTHERS_VRAM_MIN = int(1.5 * 1024**3)
OTHERS_VRAM_SHARE = 0.2
# 空けたあとに /system_stats でほかのプログラムの VRAM を見られる会社(0.13.1)。ComfyUI の
# comfy/model_management.py:1821-1865@426f5ee の get_free_memory は、NVIDIA・AMD(ROCm も torch.cuda)では
# torch.cuda.mem_get_info(GPU 全体の空き)を使うので、GPU 全体の使用量から torch の分を引けばほかのプログラムの
# 分になる。Intel(XPU)は torch の確保量から空きを出す(全体 - reserved)のでほかのプログラムの分が見えず、
# Apple(MPS)はメインメモリの空きなので見ない。その 2 つは確かめずに測り、note に OTHERS_VRAM_UNCHECKED を残す
OTHERS_VRAM_VENDORS = frozenset({"nvidia", "amd"})
OTHERS_VRAM_UNCHECKED = "ほかのプログラムの VRAM は確かめていない(/system_stats に出ない GPU)"
# NVIDIA 以外では、測っているあいだに GPU を使い始めたほかのプログラムを見ない(プロセスの表が nvidia-smi だけ)
GPU_APPS_UNCHECKED = "測っているあいだのほかのプログラムは確かめていない(NVIDIA 以外)"
GPU_APPS_EVERY = 2.0  # 測っているあいだ、GPU を使うプロセスを見る間隔(秒)
SLEEP_GAP = 30.0  # 待つあいだの 1 回の間がこれより長ければ、PC がスリープしていたとみなす(秒)


def device_vendor(dev: dict[str, Any], stats: dict[str, Any]) -> str:
    """ComfyUI の GPU の会社。ROCm 版 PyTorch も type は "cuda" なので、
    PyTorch の版(+rocm)と GPU 名で見分ける。"""
    t = str(dev.get("type") or "")
    if t == "xpu":
        return "intel"
    if t == "mps":
        return "apple"
    if t == "rocm":
        return "amd"
    torch_ver = str((stats.get("system") or {}).get("pytorch_version") or "").lower()
    if "rocm" in torch_ver or gpu_vendor(device_name(dev)) == "amd":
        return "amd"
    return "nvidia"


def _check_other_gpu(
    opts: BenchOptions, vendor: str, dev0: dict[str, Any], local: bool, warnings: list[str]
) -> tuple[tuple[float, float] | None, Probe | None, str | None]:
    """NVIDIA 以外の GPU が空いているかを確かめる(読むだけ)。(開始前の (使用中 MiB, 使用率 %), 読み取り役,
    確かめられなかったことの note)。断るときは BenchError。

    道具(gpustat.tools_for)で読めれば NVIDIA と同じく使用率 30% で断る。道具が無い・読めないときは
    止めずに、ComfyUI のキューが空なこと(呼ぶ前に確かめた)と、ComfyUI 以外が使う GPU のメモリが
    多くないことだけを確かめて進む(warnings と note に残す。standard は変えない:
    docs/accuracy.md「NVIDIA 以外の GPU の bench」)。"""
    label = VENDOR_LABEL.get(vendor, vendor)
    if not local:
        why = tr(
            "ComfyUI が別の PC で動いているので、この PC からは GPU の空きを確かめられません",
            "ComfyUI runs on another PC, so this PC cannot check whether the GPU is free",
        )
        if not opts.force:
            raise BenchError(_force_hint(why))
        warnings.append(_forced(why))
        return None, None, "GPU の空きは確かめていない(--force)"
    vram_total = int(dev0.get("vram_total") or 0)
    probe = Probe(vendor, total_mib=vram_total / 1024**2 if vram_total else None)
    samples: list[tuple[float, float]] = []
    for i in range(3):
        r = probe.read()
        if r is not None and r.util_pct is not None:
            samples.append((r.used_mib or 0.0, r.util_pct))
        if probe.tool is None:  # どの道具でも読めなければ繰り返さない
            break
        if i < 2:
            time.sleep(0.5)
    if samples:
        tool = probe.tool.name if probe.tool else "?"
        util = sum(u for _, u in samples) / len(samples)
        if probe.combined:
            warnings.append(
                tr(
                    f"{tool} に GPU が複数あり、ComfyUI の GPU を選べないので、使用率はいちばん高い GPU、"
                    "使用中のメモリは合計で見た",
                    f"{tool} shows several GPUs and ComfyUI's cannot be picked, so the busiest GPU's usage "
                    "and the total used memory were checked",
                )
            )
        if util >= opts.busy_util_pct:
            if not opts.force:
                raise BenchError(
                    tr(
                        f"GPU が他の仕事で使われています({tool}、平均の使用率 {util:.0f}%)。"
                        "空いてから測るか、--force を付けてください",
                        f"The GPU is busy with other work ({tool}, average usage {util:.0f}%). "
                        "Measure when it is free, or add --force",
                    )
                )
            warnings.append(_busy_forced(util))
        return (min(m for m, _ in samples), util), probe, None
    # 使用率を読む道具が無い・読めない
    used = vram_total - int(dev0.get("vram_free") or 0)
    other = used - int(dev0.get("torch_vram_total") or 0)
    if vendor != "apple" and vram_total and other > vram_total * OTHER_APPS_VRAM_SHARE:
        msg = tr(
            f"ComfyUI 以外が GPU のメモリを {other / GIB:.1f} GB(全体の {other / vram_total:.0%})使っています"
            "(ほかのアプリが GPU を使っているおそれ)",
            f"Something other than ComfyUI uses {other / GIB:.1f} GB of GPU memory ({other / vram_total:.0%} "
            "of it; another app may be using the GPU)",
        )
        if not opts.force:
            raise BenchError(
                tr(
                    f"{msg}。空いてから測るか、--force を付けてください",
                    f"{msg}. Measure when it is free, or add --force",
                )
            )
        warnings.append(tr(f"{msg}(--force)", f"{msg} (--force)"))
    warnings.append(
        tr(
            f"{label} の GPU の使用率を読む道具({probe.tool_names})が無いか読めないので、"
            "ComfyUI のキューが空なことと GPU のメモリだけを確かめて測った"
            "(ほかのアプリが GPU を使っていても分からない)",
            f"No tool could read the {label} GPU usage ({probe.tool_names}), so only an empty ComfyUI queue "
            "and the GPU memory were checked (another app using the GPU would go unnoticed)",
        )
    )
    return (
        None,
        None,
        f"GPU の使用率は確かめていない({probe.tool_names} で読めない。"
        "ComfyUI のキューと GPU のメモリだけ確かめた)",
    )


def _force_hint(why: str) -> str:
    return tr(
        f"{why}。確かめずに測るなら --force を付けてください",
        f"{why}. To measure without checking, add --force",
    )


def _forced(why: str) -> str:
    return tr(
        f"{why}(--force なので確かめずに測った)", f"{why} (measured without checking because of --force)"
    )


def _busy_forced(util: float) -> str:
    return tr(
        f"GPU の使用率が {util:.0f}% のまま測った(--force)",
        f"Measured with the GPU at {util:.0f}% usage (--force)",
    )


def _looks_like_comfy(p: psutil.Process) -> bool:
    try:
        cmd = " ".join(p.cmdline()).lower()
    except (psutil.Error, OSError):
        return False
    return "main.py" in cmd or "comfy" in cmd


def find_server_process(url: str) -> psutil.Process | None:
    """URL のポートで待ち受けている ComfyUI のプロセス(同じ PC のときだけ)。

    コマンドラインに main.py か comfy を含むものだけを認める(別のアプリのメモリを測らないため)。
    """
    if not is_local(url):
        return None
    u = urlparse(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    with contextlib.suppress(psutil.Error, OSError):
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                p = psutil.Process(c.pid)
                return p if _looks_like_comfy(p) else None
    return None


# ログからモデルの載せ方を読む(ComfyUI の版で文が変わるので、分からなければ None)
_LOG_PARTIAL = re.compile(r"loaded partially", re.I)
_LOG_FULL = re.compile(r"loaded completely", re.I)
_LOG_DYNAMIC = re.compile(r"dynamic(ally)?[ _-]?(vram|load)", re.I)
# --lowvram / --novram で起動した印(model_management.py:597@426f5ee の「Set vram state to: LOW_VRAM」)だけ。
# 「lowvram: loaded module regularly」(--verbose DEBUG で全部載った読み込みの行。model_patcher.py:1102)や
# 「lowvram patches」(loaded partially の行)には当てない(#102: 全部載った実測を lowvram と取り違えていた)
_LOG_LOWVRAM = re.compile(r"vram state to:\s*(LOW_VRAM|NO_VRAM)", re.I)


def offload_from_logs(lines: list[str]) -> str | None:
    text = "\n".join(lines)
    if _LOG_PARTIAL.search(text):
        return "partial"
    if _LOG_DYNAMIC.search(text):
        return "dynamic"
    if _LOG_LOWVRAM.search(text):
        return "lowvram"
    if _LOG_FULL.search(text):
        return "none"
    return None


def _new_log_entries(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {(e.get("t"), e.get("m")) for e in before}
    return [e for e in after if isinstance(e, dict) and (e.get("t"), e.get("m")) not in seen]


def _history_error(entry: dict[str, Any], *, stored: bool = False) -> tuple[str, bool] | None:
    """履歴が失敗なら (理由, メモリ不足か)。
    stored=True なら実測の note に残す文(表示の言語によらず日本語)。"""

    def say(ja: str, en: str) -> str:
        return ja if stored else tr(ja, en)

    status = entry.get("status") or {}
    if status.get("status_str") != "error":
        return None
    for msg in status.get("messages") or []:
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_error":
            d = msg[1] or {}
            text = (
                f"{d.get('node_type')}: {d.get('exception_type')}: {str(d.get('exception_message')).strip()}"
            )
            oom = "OutOfMemory" in str(d.get("exception_type")) or "out of memory" in text.lower()
            return text, oom
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_interrupted":
            return (
                say(
                    "途中で中断されました(ComfyUI の画面か /interrupt で止められた)",
                    "Interrupted (stopped from the ComfyUI page or /interrupt)",
                ),
                False,
            )
    return say(
        "実行に失敗しました(理由は ComfyUI のログを見てください)", "The run failed (see the ComfyUI log)"
    ), False


def _history_error_kind(entry: dict[str, Any], workflow: Any) -> dict[str, Any] | None:
    """履歴の失敗の種類(#132): {error_type: 例外の型名, node_type: ノードの種類, interrupted}。
    文(exception_message)は入れない。ノードの種類は送ったワークフローにあるものだけ。失敗でなければ None。"""
    status = entry.get("status") or {}
    if status.get("status_str") != "error":
        return None
    for msg in status.get("messages") or []:
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_interrupted":
            return {"error_type": None, "node_type": None, "interrupted": True}
        if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_error":
            d = msg[1] if isinstance(msg[1], dict) else {}
            node = d.get("node_type")
            return {
                "error_type": str(d.get("exception_type")) if d.get("exception_type") else None,
                "node_type": node if node in _class_types(workflow) else None,
                "interrupted": False,
            }
    return {"error_type": None, "node_type": None, "interrupted": False}


# 進み具合の行(tqdm。ComfyUI のログ /internal/logs/raw に入る): " 20/20 [00:05<00:00,  3.80it/s]"
_PROGRESS = re.compile(r"(\d+)/(\d+) \[(?:(\d+):)?(\d+):(\d+)<[^\]]*?(?:,\s*([0-9.]+)\s*(it/s|s/it))?\]")


def _log_time(t: Any) -> float | None:
    """ComfyUI のログの時刻(ISO の文字。タイムゾーンが無ければその PC の時刻)→ UNIX 時刻。
    読めなければ None。"""
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _progress_runs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ログの進み具合の行を、1 回の実行(サンプラなど)ごとにまとめる(順に見て、数が戻ったら次の実行)。"""
    runs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for e in entries:
        text = str(e.get("m") or "")
        t = _log_time(e.get("t"))
        for m in _PROGRESS.finditer(text):
            # サンプラの行は前に説明が無い(" 50%|#####| 10/20 [...]")。説明の付いた行(Hugging Face の
            # "Fetching 3 files: 100%|…" など、ほかの処理の進み具合)は使わない
            line = text[: m.start()].replace("\n", "\r").rsplit("\r", 1)[-1]
            if re.search(r"[^\W\d_]", line):
                continue
            n, total = int(m.group(1)), int(m.group(2))
            elapsed = int(m.group(3) or 0) * 3600 + int(m.group(4)) * 60 + int(m.group(5))
            if total <= 0 or n > total:
                continue
            if cur is None or n < cur["n"] or total != cur["total"]:
                cur = {"first_t": t, "first_elapsed": elapsed, "total": total}
                runs.append(cur)
            cur.update(n=n, elapsed=elapsed, last_t=t)
            if m.group(6):
                with contextlib.suppress(ValueError, ZeroDivisionError):
                    rate = float(m.group(6))
                    cur["spi"] = rate if m.group(7) == "s/it" else 1.0 / rate
    return runs


def timing_breakdown(
    entry: dict[str, Any],
    log_entries: list[dict[str, Any]],
    submit_wall: float,
    wall: float,
    *,
    local: bool,
    cold_load: bool | None = None,
    steps: Iterable[int] | None = None,
) -> dict[str, Any]:
    """時間の内訳(#132。docs/schema.md「contrib」)。HTTP だけで取る: /history の status.messages の時刻
    (execution_start・execution_success など)と、ComfyUI のログ(/internal/logs/raw)の進み具合の行。
    取れない項目は None。送った時刻・ログの時刻と比べる項目(queue_s・load_s・decode_s)は、ComfyUI が
    同じ PC のときだけ(時計が同じ)。save_s は HTTP では復元と分けられないので、今は None。
    サンプラの時間は、送ったワークフローのサンプラのステップ数(steps。sampler_steps)と同じ数の進み具合の
    行だけから取る(ほかの処理の行を混ぜない)。steps が分からない・合う行が無い・実行の時間より長い
    ときは None。"""
    ts = _timestamps(entry)
    start = ts.get("execution_start")
    end = ts.get("execution_success") or ts.get("execution_error") or ts.get("execution_interrupted")
    out: dict[str, Any] = {
        "queue_s": None,
        "load_s": None,
        "sampling_s": None,
        "sec_per_step": None,
        "decode_s": None,
        "save_s": None,
        "total_s": round(max(0.0, wall), 1) if wall is not None else None,
        "cold_load": cold_load,
    }
    if local and start:
        out["queue_s"] = round(max(0.0, start / 1000 - submit_wall), 2)
    allowed = {int(x) for x in steps or () if isinstance(x, int) and x > 0}
    done = [r for r in _progress_runs(log_entries) if r["n"] == r["total"] and r["total"] in allowed]
    if not done:
        return out
    steps = sum(r["total"] for r in done)
    secs = sum(r["elapsed"] for r in done)
    spis = [r["spi"] for r in done if r.get("spi")]
    if secs > 0:
        out["sampling_s"] = float(secs)
        out["sec_per_step"] = round(secs / steps, 3)
    elif spis:
        spi = sum(r.get("spi", 0.0) * r["total"] for r in done) / steps
        out["sec_per_step"] = round(spi, 3)
        out["sampling_s"] = round(spi * steps, 2)
    # ありえない値(時計のずれ・ほかの行)は捨てる: 実行の時間(無ければ全体の時間)より長い内訳は使わない
    span = max(wall or 0.0, (end - start) / 1000 if start and end else 0.0) + 1.0
    if out["sampling_s"] is not None and out["sampling_s"] > span:
        out["sampling_s"] = out["sec_per_step"] = None
    if local and start and done[0]["first_t"] is not None:
        load = done[0]["first_t"] - done[0]["first_elapsed"] - start / 1000
        if 0 <= load <= span:
            out["load_s"] = round(load, 2)
    if local and end and done[-1].get("last_t") is not None:
        dec = end / 1000 - done[-1]["last_t"]
        if 0 <= dec <= span:
            out["decode_s"] = round(dec, 2)
    return out


def sampler_steps(workflow: Any) -> set[int]:
    """送ったワークフロー(API 形式)のサンプラのステップ数(#132。時間の内訳で、進み具合の行を選ぶのに使う)。
    steps の入力(と、KSamplerAdvanced の start_at_step・end_at_step で切った数)。"""
    out: set[int] = set()
    if not isinstance(workflow, dict):
        return out
    for n in workflow.values():
        inputs = n.get("inputs") if isinstance(n, dict) else None
        if not isinstance(inputs, dict):
            continue
        st = inputs.get("steps")
        if isinstance(st, int) and not isinstance(st, bool) and st > 0:
            out.add(st)
            a, b = inputs.get("start_at_step"), inputs.get("end_at_step")
            if isinstance(a, int) and isinstance(b, int):
                k = min(b, st) - max(a, 0)
                if k > 0:
                    out.add(k)
    return out


def _timestamps(entry: dict[str, Any]) -> dict[str, float]:
    ts: dict[str, float] = {}
    for msg in (entry.get("status") or {}).get("messages") or []:
        if isinstance(msg, list) and len(msg) == 2 and isinstance(msg[1], dict) and "timestamp" in msg[1]:
            with contextlib.suppress(TypeError, ValueError):
                ts[str(msg[0])] = float(msg[1]["timestamp"])
    return ts


def _exec_seconds(entry: dict[str, Any]) -> float | None:
    """履歴のメッセージの時刻(ミリ秒)から、実行にかかった秒数。"""
    ts = _timestamps(entry)
    start = ts.get("execution_start")
    end = ts.get("execution_success") or ts.get("execution_error") or ts.get("execution_interrupted")
    if start and end:
        return round((end - start) / 1000, 2)
    return None


def _cached_nodes(entry: dict[str, Any]) -> set[str]:
    """前の実行の結果をそのまま使った(実行しなかった)ノード。"""
    out: set[str] = set()
    for msg in (entry.get("status") or {}).get("messages") or []:
        if (
            isinstance(msg, list)
            and len(msg) == 2
            and msg[0] == "execution_cached"
            and isinstance(msg[1], dict)
        ):
            out.update(str(n) for n in msg[1].get("nodes") or [])
    return out


# --- 本体 -------------------------------------------------------------------------------------


@dataclass
class BenchOptions:
    model_id: str
    url: str  # 既定は持たない(人の ComfyUI に間違えて送らないため)
    workflow_path: str | None = None
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    seconds: float | None = None
    steps: int | None = None
    sets: dict[str, Any] = field(default_factory=dict)  # 入力の名前 → 値(すべてのノード)
    free_before: bool = False  # True のときだけ開始前に /free で解放する
    timeout: float = 1800
    interval: float = POLL_INTERVAL
    force: bool = False
    source: str | None = None
    note: str | None = None
    precision: str | None = None
    busy_util_pct: float = 30.0
    files_fp: str | None = None  # 測る時点の一覧のファイルの指紋(estimate.files_fingerprint。#90)
    # 頼んだ設定(幅・高さ・コマ数・長さ)をワークフローに書けなければ、生成の前に止める(measure。#116)
    strict_settings: bool = False
    # 初めての読み込みか(#132。share が同じ ComfyUI で測ったモデルを覚えて渡す)。None は分からない
    cold_load: bool | None = None

    @property
    def settings_override(self) -> bool:
        """テンプレートの設定(幅・高さ・コマ数・長さ・ステップ数)を変えて測るか(#116)。"""
        return any(v is not None for v in (self.width, self.height, self.frames, self.seconds, self.steps))


@dataclass
class BenchResult:
    measurement: Measurement
    workflow_source: str
    warnings: list[str]
    failed: str | None = None  # 失敗したときの理由(メモリ不足などは測った値を残す)
    nonstandard: list[str] = field(default_factory=list)  # 標準でない条件(あれば standard は偽。#124)
    # 時間の内訳(#132。timing_breakdown)と、失敗の種類(_history_error_kind。文は入れない)
    timing: dict[str, Any] | None = None
    error_kind: dict[str, Any] | None = None
    # 最大 VRAM が一瞬だけの山だった(#131。brief_peak)。measure はこれだけで標準でない回を「試した」と覚える
    brief_peak: bool = False

    def to_dict(self) -> dict[str, Any]:
        m = asdict(self.measurement)
        return (
            {"measurement": m, "workflow_source": self.workflow_source, "warnings": self.warnings}
            | ({"failed": self.failed} if self.failed else {})
            # 時間の内訳(#132。contrib の measurement.timing と同じ項目。取れなければ null)
            | {"timing": self.timing}
        )


def read_workflow_file(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BenchError(
            tr(f"ワークフローを読めません: {p.name}: {e}", f"Cannot read the workflow: {p.name}: {e}")
        ) from e
    if not isinstance(data, dict):
        raise BenchError(
            tr(f"ワークフローの形が違います: {p.name}", f"The workflow has the wrong shape: {p.name}")
        )
    return data


def load_workflow(
    client: ComfyClient,
    opts: BenchOptions,
    object_info: dict[str, Any],
    session: requests.Session,
    data: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """(API 形式のワークフロー, どこから取ったか, 注意) を返す。"""
    if opts.workflow_path:
        if data is None:
            data = read_workflow_file(opts.workflow_path)
        if is_api_format(data):
            return data, "file:api", []
        conv = ui_to_api(data, object_info)
        return conv.workflow, "file:ui", conv.warnings
    ui, source = fetch_template(client, opts.model_id, session)
    conv = ui_to_api(ui, object_info)
    return conv.workflow, source, conv.warnings


def fetch_template(
    client: ComfyClient, model_id: str, session: requests.Session
) -> tuple[dict[str, Any], str]:
    """公式テンプレート(画面用の形式)と取った場所。
    ComfyUI が持つもの(版がサーバーと合う)、無ければ GitHub。"""
    import requests

    ui = client.template(model_id)
    if ui is not None:
        return ui, "server-template"
    try:
        r = session.get(template_url(model_id), timeout=30)
    except requests.RequestException as e:
        raise BenchError(
            tr(
                f"テンプレートを取れません(GitHub に繋がりません: {type(e).__name__})",
                f"Cannot fetch the template (cannot reach GitHub: {type(e).__name__})",
            )
        ) from e
    if r.status_code != 200:
        raise BenchError(
            tr(
                f"テンプレート {model_id} が見つかりません(ComfyUI にも GitHub にも無い)。"
                "名前を確かめるか、--workflow で API 形式のワークフローを渡してください",
                f"Template {model_id} not found (neither in ComfyUI nor on GitHub). "
                "Check the name, or pass an API-format workflow with --workflow",
            )
        )
    try:
        ui = r.json()
    except ValueError as e:
        raise BenchError(tr("テンプレートが JSON ではありません", "The template is not JSON")) from e
    return ui, "github-template"


class NodeCheck:
    """ComfyUI に、テンプレートが使うノードがそろっているか(#124。share・measure が測る前に見る)。
    /object_info は最初に 1 回だけ取り、テンプレートはモデルごとに 1 回だけ取る(覚えておく)。
    確かめられない(テンプレートを取れない・通信の誤り)ときは「足りないものは無い」として測らせる
    (測るときに bench が同じ理由で止める)。何も表示しない。"""

    def __init__(self, url: str, *, client: ComfyClient | None = None):
        self.session = make_session() if client is None else client.s
        self.client = client or ComfyClient(url, session=self.session)
        self._info: dict[str, Any] | None = None
        self._seen: dict[str, list[str]] = {}
        self.unchecked: list[str] = []  # 確かめられなかったもの(テンプレートを取れない・通信の誤り。L4a)

    def missing(self, model_id: str) -> list[str]:
        """そのテンプレートが使い、この ComfyUI に無いノード(無ければ空)。"""
        from .workflow import missing_nodes

        if model_id in self._seen:
            return self._seen[model_id]
        try:
            if self._info is None:
                self._info = self.client.object_info()
            ui, _src = fetch_template(self.client, model_id, self.session)
            out = missing_nodes(ui, self._info)
        except (BenchError, WorkflowError):
            out = []
            self.unchecked.append(model_id)
        self._seen[model_id] = out
        return out

    def comfyui_version(self) -> str | None:
        """ComfyUI の版(/system_stats。失敗の記録に書く。#132)。分からなければ None。"""
        with contextlib.suppress(BenchError, AttributeError, TypeError):
            v = (self.client.system_stats().get("system") or {}).get("comfyui_version")
            return str(v) if v else None
        return None


def _ensure_inputs(
    client: ComfyClient, wf: dict[str, Any], object_info: dict[str, Any], session: requests.Session
) -> list[str]:
    """テンプレートの入力画像がサーバーに無ければ、公式のものを取ってアップロードする。"""
    import requests

    notes: list[str] = []
    for nid, name, filename in upload_inputs(wf, object_info):
        try:
            r = session.get(template_input_url(filename), timeout=60)
        except requests.RequestException as e:
            raise BenchError(
                tr(
                    f"入力の画像を取れません: {filename}: {e}",
                    f"Cannot fetch the input image: {filename}: {e}",
                )
            ) from e
        if r.status_code != 200:
            raise BenchError(
                tr(
                    f"入力の画像 {filename} がサーバーにも公式テンプレートにもありません。"
                    "ComfyUI の input フォルダに置くか、--workflow で別の画像を指定してください",
                    f"The input image {filename} is neither on the server nor in the official templates. "
                    "Put it in ComfyUI's input folder, or give another image with --workflow",
                )
            )
        wf[nid]["inputs"][name] = client.upload_image(filename, r.content)
        notes.append(tr(f"入力の画像 {filename} をアップロードした", f"Uploaded the input image {filename}"))
    return notes


def _parse_set(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise BenchError(
                tr(f"--set は 名前=値 の形で書いてください: {item}", f"Write --set as NAME=VALUE: {item}")
            )
        k, v = item.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v
    return out


def describe_sets(sets: dict[str, Any]) -> str:
    """--set の記録用の文。数値は残し、文字列などの値は書かない(プロンプトなどを残さないため)。"""
    parts = []
    for k, v in sets.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}(値は記録しない)")
    return ", ".join(parts)


def _check_queue_empty(client: ComfyClient, when: str) -> None:
    running, pending = client.queue_ids()
    if running or pending:
        raise BenchError(
            tr(
                f"ComfyUI のキューに他の仕事があります({when})。"
                "終わってから測ってください(他の人の生成を邪魔しないため)",
                f"ComfyUI's queue has other work ({when}). "
                "Measure after it finishes (so as not to disturb others' generations)",
            )
        )


def run(opts: BenchOptions, *, client: ComfyClient | None = None, log=None, progress=None) -> BenchResult:
    """1 回測る。測れないときは BenchError。進み具合は log(既定は logging の genmodelfit)へ。
    progress(あれば)には、送ったあと約 0.5 秒ごとに {"phase", "elapsed", "peak_vram_gb"} を渡す(#124。
    phase は queued(キュー待ち)・running(実行中)・waiting(送った直後・結果を待つ)。表示は呼び出し側)。"""
    log = log or logging.getLogger("genmodelfit").info
    if not opts.timeout or opts.timeout <= 0:
        raise BenchError(_timeout_error())
    session = make_session()
    file_data = (
        read_workflow_file(opts.workflow_path) if opts.workflow_path else None
    )  # サーバーに触る前に読む
    client = client or ComfyClient(opts.url, session=session)

    # 1. 読むだけの確認(ここまでで断るなら、サーバーには何も書き込まない)
    stats = client.system_stats()
    devices = stats.get("devices") or []
    if not devices or devices[0].get("type") not in ("cuda", "rocm", "xpu", "mps"):
        raise BenchError(
            tr(
                "ComfyUI が GPU を使っていません(CPU だけでは VRAM を測れません)",
                "ComfyUI is not using a GPU (VRAM cannot be measured on CPU only)",
            )
        )
    dev0 = devices[0]
    _check_queue_empty(client, tr("開始前", "before starting"))

    object_info = client.object_info()
    wf, source, warnings = load_workflow(client, opts, object_info, session, file_data)
    wf, notes = apply_settings(
        wf, width=opts.width, height=opts.height, frames=opts.frames, seconds=opts.seconds, steps=opts.steps
    )
    warnings += notes
    if opts.strict_settings:
        _check_requested_settings(wf, opts, notes)
    nonstandard: list[str] = []  # 実測の note に残す(表示の言語によらず日本語)
    shown: list[str] = []  # 表示する(表示の言語。BenchResult.nonstandard)

    def ns(ja: str, en: str) -> None:
        if ja not in nonstandard:
            nonstandard.append(ja)
            shown.append(tr(ja, en))

    if opts.workflow_path:
        ns("自分のワークフロー(--workflow)", "your own workflow (--workflow)")
    for key, value in opts.sets.items():
        hit = [n for n in wf.values() if key in n["inputs"] and not isinstance(n["inputs"][key], list)]
        if not hit:
            warnings.append(
                tr(f"--set {key}: その名前の入力がありません", f"--set {key}: no input has that name")
            )
        for n in hit:
            n["inputs"][key] = value
        if hit and key not in STANDARD_SET_KEYS:
            ns(f"--set {key}", f"--set {key}")
    set_output_prefix(wf, f"genmodelfit_bench/{re.sub(r'[^A-Za-z0-9_.-]', '_', opts.model_id)}")
    problems = check_inputs(wf, object_info)
    if problems:
        raise BenchError(
            "\n".join(
                [
                    tr(
                        "必要なファイルがサーバーにありません。先にモデルを置いてください:",
                        "The server is missing required files. Put the model files in place first:",
                    ),
                    *problems,
                ]
            )
        )
    settings = effective_settings(wf, precision=opts.precision)

    # どの GPU か。確かめられないときは黙って進まず、--force を求める
    name = device_name(dev0)
    local = is_local(opts.url)
    proc = find_server_process(opts.url)
    vendor = device_vendor(dev0, stats)
    if vendor == "apple" and name.lower() in ("mps", "unknown"):
        # ComfyUI の MPS は GPU 名が "mps" なので、同じ PC ならこの PC のチップ名(Apple M2 Pro など。
        # system_profiler で読む。チップ名の形のものだけ)にする。判定はこの PC の GPU 名と同じ形で比べる。
        # 別の PC の ComfyUI(--force が要る)・読めないときは "mps" のまま(会社 apple としてだけ使われる)
        chip = hardware.apple_chip_name() if local else None
        if chip:
            name = chip
        else:
            name = "mps"
            warnings.append(
                tr(
                    "Apple のチップ名が分からないので GPU 名は mps のまま(同じ GPU の実測としては使われない)",
                    "The Apple chip name is unknown, so the GPU name stays mps "
                    "(not used as a same-GPU measurement)",
                )
            )
    # PCI の型番 ID(同じ GPU の照合に使う。Linux と Windows で名前が違う AMD・Intel のため)。
    # ComfyUI が同じ PC のときだけ
    pci_id = hardware.local_pci_id(vendor, name) if local else None
    gpu: dict[str, Any] | None = None
    gpu_id = driver = None
    probe: Probe | None = None
    unchecked: str | None = None  # GPU の空きを確かめられなかったことの note
    base_smi: tuple[float, float] | None = None
    if vendor != "nvidia":
        base_smi, probe, unchecked = _check_other_gpu(opts, vendor, dev0, local, warnings)
    else:
        gpu, why = match_gpu(name, smi_gpus(), proc, local)
        gpu_id = str(gpu.get("uuid") or gpu["index"]) if gpu else None
        driver = gpu.get("driver") if gpu else None
        if gpu is None:
            if not opts.force:
                raise BenchError(_force_hint(why))
            warnings.append(_forced(why))
            unchecked = "GPU の空きは確かめていない(--force)"
    if gpu_id is not None:
        samples = sample_gpu(gpu_id)
        if not samples:
            if not opts.force:
                raise BenchError(
                    tr(
                        "nvidia-smi で GPU の使用率を読めませんでした。"
                        "確かめずに測るなら --force を付けてください",
                        "nvidia-smi could not read the GPU usage. To measure without checking, add --force",
                    )
                )
            warnings.append(
                tr(
                    "nvidia-smi で GPU の使用率を読めなかった(--force なので確かめずに測った)",
                    "nvidia-smi could not read the GPU usage (measured without checking because of --force)",
                )
            )
        else:
            util = sum(u for _, u in samples) / len(samples)
            if util >= opts.busy_util_pct:
                if not opts.force:
                    raise BenchError(
                        tr(
                            f"GPU が他の仕事で使われています(約 2 秒の平均の使用率 {util:.0f}%)。"
                            "空いてから測るか、--force を付けてください",
                            f"The GPU is busy with other work (average usage over about 2 s: {util:.0f}%). "
                            "Measure when it is free, or add --force",
                        )
                    )
                warnings.append(_busy_forced(util))
            base_smi = (min(m for m, _ in samples), util)
    if local and proc is None:
        warnings.append(
            tr(
                "ComfyUI のプロセスを特定できないので、最大メモリは記録しない",
                "The ComfyUI process cannot be identified, so the peak RAM is not recorded",
            )
        )

    # 2. ここから書き換える要求
    warnings += _ensure_inputs(client, wf, object_info, session)
    preloaded = int(dev0.get("torch_vram_total") or 0)
    if opts.free_before:
        client.free()
        time.sleep(2.0)
    elif preloaded:
        warnings.append(
            tr(
                f"開始前に torch が {preloaded / GIB:.2f} GB 確保しています(読み込み済みのモデル)。"
                "結果に前のモデルの分が混ざることがあります。"
                "自分の ComfyUI なら --free で解放してから測れます",
                f"torch already holds {preloaded / GIB:.2f} GB before starting (models already loaded). "
                "The result may include the previous model. "
                "If this ComfyUI is yours, --free releases it first",
            )
        )
    base_stats = (client.system_stats().get("devices") or [dev0])[0]
    base_torch = int(base_stats.get("torch_vram_total") or 0)
    base_dev = int(base_stats.get("vram_total") or 0) - int(base_stats.get("vram_free") or 0)
    vram_unchecked: str | None = None  # 空けたあとのほかのプログラムの VRAM を確かめられなかったことの note
    if opts.free_before and vendor not in OTHERS_VRAM_VENDORS:
        vram_unchecked = OTHERS_VRAM_UNCHECKED
    if opts.free_before and (gpu_id is not None or vendor in OTHERS_VRAM_VENDORS - {"nvidia"}):
        # 空けたあとでも ComfyUI 以外が VRAM を多く使っていれば、測ると数値が狂う(#124)。Windows では
        # プロセスごとの量が出ないので、GPU 全体の使用量から ComfyUI の torch の分を引いて見積もる。
        # AMD(ROCm)は nvidia-smi が無くても /system_stats で同じに見る(0.13.1。OTHERS_VRAM_VENDORS)
        others = base_dev - base_torch
        total = int(base_stats.get("vram_total") or 0)
        limit = max(OTHERS_VRAM_MIN, OTHERS_VRAM_SHARE * total)
        if total and others > limit:
            msg = tr(
                f"ほかのプログラムが VRAM を約 {others / GIB:.1f} GB 使っています(ComfyUI 以外。"
                f"上限 {limit / GIB:.1f} GB)。閉じてから測ってください(測ると数値が狂います)",
                f"Other programs use about {others / GIB:.1f} GB of VRAM (besides ComfyUI; limit"
                f" {limit / GIB:.1f} GB). Close them before measuring (the numbers would be wrong)",
            )
            if not opts.force:
                raise BenchError(msg)
            warnings.append(tr(f"{msg}(--force)", f"{msg} (--force)"))

    # 3. 送る直前にもう一度キューを見る
    _check_queue_empty(client, tr("送る直前", "just before sending"))
    log_before = client.log_entries()
    mon = Monitor(
        client,
        proc,
        gpu_id,
        interval=opts.interval,
        use_smi=gpu_id is not None,
        other_used=probe.used_mib if probe is not None and probe.tool is not None else None,
    )
    client_id = uuid.uuid4().hex
    log(tr(f"{opts.model_id} を {opts.url} で実行します …", f"Running {opts.model_id} on {opts.url} …"))
    mon.start()
    t0 = time.monotonic()
    submit_wall = time.time()
    entry: dict[str, Any] | None = None
    prompt_id: str | None = None
    foreign: set[str] = set()  # 待っている間に実行中だった他の仕事
    # 測っているあいだに GPU を使い始めたほかのプログラム
    # (#124。nvidia-smi の compute apps。Windows でも PID は出る)
    gpu_uuid = str(gpu.get("uuid")) if gpu and gpu.get("uuid") else None
    watch = (
        GpuAppWatch(gpu_uuid, _gpu_app_pids(gpu_uuid), lambda: _process_tree(proc), lambda: mon.last_used_mib)
        if gpu_id is not None
        else None
    )
    last_tick = time.monotonic()
    slept = 0.0
    ran = False  # 自分の仕事が実行中になったのを見たか(時間切れを失敗の記録にするのはそのときだけ。#132)
    next_apps = time.monotonic() + GPU_APPS_EVERY
    try:
        prompt_id = client.submit(wf, client_id)
        while True:
            if watch is not None and time.monotonic() >= next_apps:
                next_apps = time.monotonic() + GPU_APPS_EVERY
                with contextlib.suppress(Exception):  # 見張りの失敗で測るのを止めない
                    watch.sample()
            entry = client.history(prompt_id)
            if entry and ((entry.get("status") or {}).get("completed") or _history_error(entry)):
                break
            phase = "waiting"
            with contextlib.suppress(BenchError):
                running, pending = client.queue_ids()
                foreign.update(r for r in running if r != prompt_id)
                phase = "running" if prompt_id in running else "queued" if prompt_id in pending else "waiting"
                ran = ran or phase == "running"
            if progress is not None:
                with mon._lock:
                    p = mon.peaks
                    torch_now = p.torch_vram if p.samples else 0
                    dev_now = max(0, p.device_used - base_dev)
                with contextlib.suppress(Exception):  # 表示の失敗で測るのを止めない
                    progress(
                        {
                            "phase": phase,
                            "elapsed": time.monotonic() - t0,
                            "peak_vram_gb": (torch_now or dev_now) / GIB,
                        }
                    )
            now = time.monotonic()
            if now - last_tick > SLEEP_GAP:
                # PC がスリープ・休止していた(L1b): 止まっていた時間は待つ時間に数えず、結果は標準にしない
                slept += now - last_tick
                ns("PC がスリープした", "the PC went to sleep")
            last_tick = now
            if now - t0 - slept > opts.timeout:
                done = cancel_own_prompt(client, prompt_id)
                timeout_failure = {
                    "outcome": "timeout",
                    "seconds": round(now - t0 - slept, 1),
                    "timeout_s": opts.timeout,
                }
                # キューで待っただけ・ほかの仕事が動いた・スリープした時間切れは、
                # モデルの性質でないので送らない
                if not ran or foreign or slept:
                    timeout_failure = None
                raise BenchError(
                    tr(
                        f"{opts.timeout:g} 秒で終わらなかったので中止しました"
                        + ("(自分の仕事を中断した)" if done == "interrupted" else ""),
                        f"Stopped: not finished in {opts.timeout:g} s"
                        + (" (interrupted our own job)" if done == "interrupted" else ""),
                    ),
                    failure=timeout_failure,
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        if prompt_id is not None:
            cancel_own_prompt(client, prompt_id)
        raise BenchError(
            tr("中断しました(送った自分の仕事は取り消しました)", "Interrupted (our own job was cancelled)")
        ) from None
    finally:
        peaks = mon.stop()
    wall = time.monotonic() - t0
    with contextlib.suppress(BenchError):  # 終わった直後に他の仕事が動き出していたら、最後の値に混ざりうる
        running, _ = client.queue_ids()
        foreign.update(r for r in running if r != prompt_id)

    failed: str | None = None
    failed_note: str | None = None  # 実測の note に残す文(表示の言語によらず日本語)
    outcome = "ok"
    err = _history_error(entry) if entry else None
    if err:
        failed, oom = err
        failed_note = (_history_error(entry, stored=True) or err)[0] if entry else failed
        outcome = "oom" if oom else "error"

    reach = reachable_nodes(wf, object_info)
    cached = _cached_nodes(entry or {}) & reach
    if outcome == "ok" and cached and cached >= reach:
        raise BenchError(
            tr(
                "前と同じ内容だったので ComfyUI が前の結果を返し、何も実行されませんでした。"
                "--free を付けるか、シードなどの入力を変えてください(--set seed=…)",
                "The job was the same as before, so ComfyUI returned the previous result and ran nothing. "
                "Add --free, or change an input such as the seed (--set seed=…)",
            )
        )
    interfering = watch.interfering() if watch is not None else {}
    if interfering:
        names = tr("、", ", ").join(interfering.values())
        ns(
            f"測っている間にほかのプログラムが GPU を使い始めた({names})",
            f"another program started using the GPU while measuring ({names})",
        )
        warnings.append(
            tr(
                f"測っている間にほかのプログラムが GPU を使い始めました({names})。数値が狂うので送りません",
                f"Another program started using the GPU while measuring ({names}); the result is not sent",
            )
        )
    if cached:
        ns(
            f"前の結果を使ったノードが {len(cached)} 個(一部だけ実行)",
            f"{len(cached)} nodes reused the previous result (only partly run)",
        )
        warnings.append(
            tr(
                f"{len(cached)} 個のノードが前の結果を使い、実行されませんでした。"
                "その分のメモリは測れていません(--free を付けると避けられます)",
                f"{len(cached)} nodes reused the previous result and did not run. "
                "Their memory is not measured (--free avoids this)",
            )
        )
    if foreign:
        ns("測っている間に他の仕事が動いた", "other work ran during the measurement")
        warnings.append(
            tr(
                "測っている間に ComfyUI で他の仕事が動いたので、値に混ざっているおそれがあります",
                "Other work ran in ComfyUI during the measurement, so the values may include it",
            )
        )
    start_ts = _timestamps(entry or {}).get("execution_start")
    if local and start_ts and start_ts / 1000 - submit_wall > START_DELAY_S:
        delay = start_ts / 1000 - submit_wall
        ns(
            f"送ってから実行が始まるまで {delay:.0f} 秒(先に他の仕事が動いた)",
            f"{delay:.0f} s from sending to the start (other work ran first)",
        )
        warnings.append(
            tr(
                f"送ってから実行が始まるまで {delay:.0f} 秒かかりました(先に他の仕事が動いたとみられます)",
                f"The run started {delay:.0f} s after sending (other work likely ran first)",
            )
        )

    log_new = _new_log_entries(log_before, client.log_entries())
    log_lines = [str(e.get("m") or "") for e in log_new]
    offload = "oom" if outcome == "oom" else offload_from_logs(log_lines)
    exec_s = _exec_seconds(entry or {})

    vram_total = int(dev0.get("vram_total") or 0)
    torch_peak = peaks.torch_vram if peaks.samples else 0
    dev_delta = max(0, peaks.device_used - base_dev)
    brief: tuple[int, int] | None = None
    if torch_peak > 0:
        peak_vram = torch_peak / GIB
        # 最後まで動いた回だけ見る(メモリ不足・失敗の記録は今までどおり残し、送る。#131)
        brief = brief_peak(mon.torch_values) if outcome == "ok" else None
        if brief:
            # #131: 最大値が読む間隔より短い山だった。測るたびに揺れるので標準にしない(値は残す)
            top, steady = brief[0] / GIB, brief[1] / GIB
            ns(
                f"最大 VRAM が一瞬だけの山({top:.2f} GB。"
                f"{BRIEF_PEAK_COUNT} 回以上読めた最大は {steady:.2f} GB)",
                f"the peak VRAM was a brief spike ({top:.2f} GB; the highest value read at least"
                f" {BRIEF_PEAK_COUNT} times is {steady:.2f} GB)",
            )
            warnings.append(
                tr(
                    f"最大 VRAM {top:.2f} GB は読む間隔より短い山でした。"
                    "測るたびに値が揺れるので、標準の実測にしません",
                    f"The peak VRAM {top:.2f} GB was a spike shorter than the polling interval."
                    " It varies from run to run,"
                    " so it is not a standard measurement",
                )
            )
    else:  # torch の量が出ない版では、GPU 全体の増分で代える
        peak_vram = dev_delta / GIB
        warnings.append(
            tr(
                "torch_vram_total が取れないので、GPU 全体の増分を最大 VRAM にした",
                "torch_vram_total is not available, so the whole-GPU increase is used as the peak VRAM",
            )
        )
    note_parts = []
    if base_smi and peaks.smi_samples:
        gpu_delta = max(0.0, peaks.gpu_used_mib - base_smi[0]) / 1024
        tool = probe.tool.name if probe is not None and probe.tool is not None else "nvidia-smi"
        if probe is not None and probe.combined:
            tool += "、複数の GPU の合計"
        note_parts.append(
            f"GPU 全体({tool})の増分 {gpu_delta:.2f} GB(開始前 {base_smi[0] / 1024:.2f} GB 使用、"
            f"最大 {peaks.gpu_used_mib / 1024:.2f} GB)"
        )
    note_parts.append(f"/system_stats の GPU 全体の増分 {dev_delta / GIB:.2f} GB")
    if opts.free_before:
        note_parts.append(
            "開始前に /free でモデルを解放"
            + (
                f"(解放前 torch {preloaded / GIB:.2f} GB、解放後 {base_torch / GIB:.2f} GB)"
                if preloaded
                else ""
            )
        )
    else:
        note_parts.append(f"/free はしていない(開始前に読み込み済み torch {base_torch / GIB:.2f} GB)")
    if exec_s is not None:
        note_parts.append(f"ComfyUI の実行時間 {exec_s:g} 秒")
    note_parts.append(f"計測 {peaks.samples} 回")
    # 確かめなかったこと(0.13.1)は失敗の文より前に置く(note は 500 字で切られる。contrib.MAX_NOTE_CHARS)
    if vram_unchecked:
        note_parts.append(vram_unchecked)
    if watch is None and vendor != "nvidia":
        note_parts.append(GPU_APPS_UNCHECKED)
    if failed:
        label = "メモリ不足で失敗" if outcome == "oom" else "失敗"
        note_parts.append(f"{label}: {(failed_note or failed)[:300]}")
    if opts.sets:
        note_parts.append("変えた入力: " + describe_sets(opts.sets))
    if nonstandard:
        note_parts.append("標準でない条件: " + "、".join(nonstandard))
    if unchecked:
        note_parts.append(unchecked)
    if opts.note:
        note_parts.append(opts.note)

    m = Measurement(
        model_id=opts.model_id,
        gpu_name=name,
        gpu_vram_gb=round(vram_total / GIB, 2),
        peak_vram_gb=round(peak_vram, 2),
        settings=settings,
        peak_ram_gb=round(peaks.ram / GIB, 2) if proc is not None and peaks.ram else None,
        seconds=round(wall, 1),
        driver=driver,
        comfyui_version=str((stats.get("system") or {}).get("comfyui_version") or "") or None,
        offload=offload,
        source=opts.source or f"genmodelfit bench {__version__}",
        date=datetime.now(timezone.utc).date().isoformat(),
        note="。".join(note_parts),
        outcome=outcome,
        standard=not nonstandard,
        vram_metric="torch_reserved",
        gpu_backend=BACKEND_OF_VENDOR.get(vendor),
        gpu_pci_id=pci_id,
        files_fp=opts.files_fp,
        settings_override=True if opts.settings_override else None,
    )
    return BenchResult(
        measurement=m,
        workflow_source=source,
        warnings=warnings,
        failed=failed,
        nonstandard=list(shown),
        timing=timing_breakdown(
            entry or {},
            log_new,
            submit_wall,
            wall,
            local=local,
            cold_load=opts.cold_load,
            steps=sampler_steps(wf),
        ),
        error_kind=_history_error_kind(entry, wf) if entry else None,
        brief_peak=brief is not None,
    )


def default_output_path(m: Measurement) -> Path:
    def slug(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    gpu = slug(m.gpu_name.replace("NVIDIA ", "").replace("GeForce ", ""))
    return cache_dir() / "measurements" / f"{slug(m.model_id)}_{gpu}_{ts}.json"


def save(m: Measurement, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"measurements": [asdict(m)]}
    text = json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False)  # NaN・inf は書かない
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def save_decision(
    m: Measurement, output: str | None, save_failed: bool, save_nonstandard: bool
) -> tuple[Path | None, str | None]:
    """(保存先, 保存しない理由)。

    --output があれば必ずそこへ。キャッシュには「最後まで動いた標準の結果」だけを置く。
    """
    if output:
        return Path(output), None
    if m.outcome != "ok" and not save_failed:
        return None, tr(
            "失敗した結果なのでキャッシュには保存しません(--save-failed か --output で保存できます)",
            "A failed result is not saved to the cache (--save-failed or --output saves it)",
        )
    if not m.standard and not save_nonstandard:
        return None, tr(
            "標準でない条件の結果なのでキャッシュには保存しません"
            "(--save-nonstandard か --output で保存できます)",
            "A result under non-standard conditions is not saved to the cache "
            "(--save-nonstandard or --output saves it)",
        )
    return default_output_path(m), None


def _timeout_error() -> str:
    return tr("--timeout は 0 より大きい秒数にしてください", "--timeout must be a number of seconds above 0")


def _check_requested_settings(wf: dict[str, Any], opts: BenchOptions, notes: list[str]) -> None:
    """頼んだ設定が、書き換えたワークフローから読める設定(effective_settings。実測に書く値)と同じか(#116)。
    違えば(その入力が無い・他のノードから来る)生成の前に BenchError で止める。"""
    got = effective_settings(wf)
    wrong = []
    for key, want in (
        ("width", opts.width),
        ("height", opts.height),
        ("frames", opts.frames),
        ("seconds", opts.seconds),
    ):
        have = getattr(got, key)
        if want is not None and (have is None or float(have) != float(want)):
            wrong.append(f"{key} {want:g} → {have if have is not None else '-'}")
    if wrong:
        raise BenchError(
            "\n".join(
                [
                    tr(
                        "このテンプレートでは、頼んだ設定を書けません(測りませんでした): " + "、".join(wrong),
                        "This template cannot take the requested settings (nothing was measured): "
                        + ", ".join(wrong),
                    ),
                    *notes,
                ]
            )
        )


def parse_resolution(res: str) -> tuple[int, int]:
    """--resolution の 幅x高さ(bench と measure)。形が違えば BenchError。"""
    mres = re.fullmatch(r"\s*(\d+)\s*[xX*×]\s*(\d+)\s*", res)
    if not mres or not int(mres.group(1)) or not int(mres.group(2)):
        raise BenchError(
            tr(
                f"--resolution は 幅x高さ の形で書いてください(例 768x768): {res}",
                f"Write --resolution as WIDTHxHEIGHT (e.g. 768x768): {res}",
            )
        )
    return int(mres.group(1)), int(mres.group(2))


def options_from_args(args: argparse.Namespace) -> BenchOptions:
    width, height = getattr(args, "width", None), getattr(args, "height", None)
    res = getattr(args, "resolution", None)
    if res:
        width, height = parse_resolution(res)
    timeout = getattr(args, "timeout", None)
    timeout = 1800.0 if timeout is None else timeout
    if timeout <= 0:
        raise BenchError(_timeout_error())
    precision = files_fp = None
    with contextlib.suppress(Exception):
        from .db import load as load_db
        from .estimate import files_fingerprint

        entry = load_db(getattr(args, "db", None), use_cache=not getattr(args, "no_cache", False)).get(
            args.template
        )
        precision = entry.precision if entry else None
        files_fp = files_fingerprint(entry) if entry else None
    return BenchOptions(
        model_id=args.template,
        url=args.url,
        workflow_path=args.workflow,
        width=width,
        height=height,
        frames=args.frames,
        seconds=args.length,
        steps=args.steps,
        sets=_parse_set(getattr(args, "set", None)),
        free_before=bool(getattr(args, "free", False)),
        timeout=timeout,
        force=getattr(args, "force", False),
        source=getattr(args, "source", None),
        note=getattr(args, "note", None),
        precision=precision,
        files_fp=files_fp,
    )
