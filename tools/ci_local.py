# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ci.yml と同じ検査を手元で流す(2026-10-22 オーナー決定。2026-10-28 に毎回は --quick に)。

    python tools/ci_local.py --quick         # push の前に毎回: 3.10 と 3.13、ruff、配布物(約 15 分)
    python tools/ci_local.py                 # この PC の OS で Python 3.10〜3.13 と配布物
    python tools/ci_local.py --linux         # 続けて WSL の中でも同じものを流す(Windows から)
    python tools/ci_local.py --linux-only    # WSL の中だけ
    python tools/ci_local.py --python 3.12 --no-wheel   # 絞って流す

ci.yml との対応:
- test: 版ごとに venv(.ci/<OS>-py<版>。2 回目からは作り直さない)に `.[dev]` を入れ、3.12 だけ ruff、
  全部の版で pytest と CLI の 3 つのコマンド
- wheel: wheel と sdist を作り、twine check と tools/check_dist.py、まっさらな venv(3.10・3.12・3.13)に
  wheel だけを入れて主なコマンド。dev の依存が入っていないことも見る
- 網羅率(--cov)は表示だけなので流さない

メモリを食わないよう、すべて 1 つずつ順に流す。要るのは uv だけ(WSL の中にも)。
WSL へは git の管理するファイル(まだコミットしていない変更・新しいファイルも含む。.gitignore のものは除く)を
~/.cache/gmf-ci/src に写して流す(/mnt/c の上で流すと遅いため)。
終了コード: 0 = すべて通った、1 = どれか落ちた、2 = 使い方の誤り・uv が無い。
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ["3.10", "3.11", "3.12", "3.13"]
LINT_VERSION = "3.12"  # ci.yml と同じ。ruff はこの版だけ
# --quick: いちばん古い版と新しい版だけ(新しい版だけで落ちる不具合を毎回見つける。全部はマージの前に 1 回)
QUICK_VERSIONS = ["3.10", "3.13"]
WHEEL_VERSIONS = ["3.10", "3.12", "3.13"]
SMOKE = [
    ["system", "--json", "--vram", "8", "--ram", "32"],
    ["recommend", "--json", "--vram", "8", "--ram", "32", "--no-cache", "-n", "3"],
    ["fit", "--vram", "16", "--ram", "32", "--no-cache"],
]
WHEEL_COMMANDS = [
    ["--version"],
    ["system", "--json"],
    ["system", "--vram", "8", "--ram", "32"],
    ["recommend", "--json", "--vram", "8", "--ram", "32", "--no-cache", "-n", "3"],
    ["fit", "--vram", "16", "--ram", "32", "--no-cache"],
    ["info", "image_z_image_turbo_int8", "--vram", "8", "--ram", "32"],
]
WSL_HOME_DIR = "$HOME/.cache/gmf-ci"

IS_WIN = os.name == "nt"
OS_TAG = "windows" if IS_WIN else "linux"


class Runner:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, float]] = []

    def step(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path = ROOT,
        env: dict[str, str] | None = None,
        quiet: bool = False,
    ) -> bool:
        print(f"\n=== [{OS_TAG}] {name}", flush=True)
        t0 = time.monotonic()
        kw: dict = {"cwd": cwd, "env": env or child_env()}
        if quiet:
            kw.update(stdout=subprocess.DEVNULL)
        ok = subprocess.run(cmd, check=False, **kw).returncode == 0
        self.results.append((name, ok, time.monotonic() - t0))
        if not ok:
            print(f"*** 失敗: {name}", flush=True)
        return ok

    def record(self, name: str, ok: bool) -> None:
        self.results.append((name, ok, 0.0))
        if not ok:
            print(f"*** 失敗: {name}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)

    def summary(self) -> None:
        print(f"\n===== まとめ({OS_TAG})")
        for name, ok, sec in self.results:
            print(f"  {'ok  ' if ok else 'FAIL'}  {sec:7.1f}s  {name}")
        print("すべて通りました" if self.ok else "落ちたものがあります", flush=True)


def child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def venv_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")


def venv_bin(env_dir: Path, name: str) -> str:
    return str(env_dir / ("Scripts" if IS_WIN else "bin") / (name + (".exe" if IS_WIN else "")))


def run_tests(r: Runner, ver: str, env_root: Path, cache: Path, *, lint: bool | None = None) -> None:
    env_dir = env_root / f"{OS_TAG}-py{ver}"
    py = venv_python(env_dir)
    if not py.exists() and not r.step(f"py{ver} venv", ["uv", "venv", "-q", "--python", ver, str(env_dir)]):
        return
    if not r.step(f"py{ver} install", ["uv", "pip", "install", "-q", "--python", str(py), "-e", ".[dev]"]):
        return
    lint = ver == LINT_VERSION if lint is None else lint
    if lint:
        r.step("ruff check", [str(py), "-m", "ruff", "check", "."])
        r.step("ruff format --check", [str(py), "-m", "ruff", "format", "--check", "."])
    r.step(f"py{ver} pytest", [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    exe = venv_bin(env_dir, "genmodelfit")
    smoke_env = child_env(GENMODELFIT_CACHE_DIR=str(cache))
    for args in SMOKE:
        r.step(f"py{ver} CLI {args[0]}", [exe, *args], env=smoke_env, quiet=True)
    if lint:
        # 知らせるだけ(失敗にしない。ci.yml には無い): 同梱のまとめにまだ入っていない data/contrib の提出。
        # 入れるのは版を出すとき(docs/release.md「版を出す手順」。#90)
        print(f"\n=== [{OS_TAG}] (情報) contrib aggregate --check", flush=True)
        subprocess.run([exe, "contrib", "aggregate", "--check"], cwd=ROOT, env=smoke_env, check=False)


def run_wheel(r: Runner, versions: list[str], tmp: Path) -> None:
    dist = tmp / "dist"
    if not r.step("uv build", ["uv", "build", "-q", "--out-dir", str(dist)]):
        return
    # uv build は出力先に .gitignore も置くので、配布物だけを渡す
    files = [str(p) for p in sorted(dist.iterdir()) if p.name.endswith((".whl", ".tar.gz"))]
    r.step("twine check --strict", ["uvx", "twine", "check", "--strict", *files])
    r.step("check_dist", [sys.executable, str(ROOT / "tools" / "check_dist.py"), str(dist)])
    wheel = next(p for p in dist.iterdir() if p.suffix == ".whl")
    for ver in versions:
        env_dir = tmp / f"wheel-py{ver}"
        py = venv_python(env_dir)
        if not r.step(f"wheel py{ver} venv", ["uv", "venv", "-q", "--python", ver, str(env_dir)]):
            continue
        if not r.step(
            f"wheel py{ver} install", ["uv", "pip", "install", "-q", "--python", str(py), str(wheel)]
        ):
            continue
        work = tmp / f"work-py{ver}"
        work.mkdir()
        env = child_env(GENMODELFIT_CACHE_DIR=str(tmp / f"cache-py{ver}"))
        exe = venv_bin(env_dir, "genmodelfit")
        for args in WHEEL_COMMANDS:
            r.step(f"wheel py{ver} {' '.join(args[:2])}", [exe, *args], cwd=work, env=env, quiet=True)
        code = "import genmodelfit as g; print(len(g.recommend(limit=3)), g.data_status().to_dict())"
        r.step(f"wheel py{ver} import", [str(py), "-c", code], cwd=work, env=env, quiet=True)
        has_pytest = (
            subprocess.run(
                [str(py), "-c", "import pytest"], cwd=work, env=env, check=False, capture_output=True
            ).returncode
            == 0
        )
        r.record(f"wheel py{ver} dev の依存が入っていない", not has_pytest)


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "-co", "--exclude-standard"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f and (ROOT / f).is_file()]


def run_in_wsl(args: argparse.Namespace) -> bool:
    if not IS_WIN:
        print("--linux は Windows から使います(Linux ではそのまま流してください)", file=sys.stderr)
        return False
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in tracked_files():
            tar.add(ROOT / f, arcname=f)
    inner = ["--env-dir", f"{WSL_HOME_DIR}/envs", "--python", *args.python]
    if args.no_wheel:
        inner.append("--no-wheel")
    if args.no_tests:
        inner.append("--no-tests")
    script = (
        f'set -e; d="{WSL_HOME_DIR}"; rm -rf "$d/src"; mkdir -p "$d/src"; tar -x -C "$d/src"; cd "$d/src"; '
        f"exec uv run -q --no-project --python {LINT_VERSION} python tools/ci_local.py {' '.join(inner)}"
    )
    distro = args.wsl_distro or wsl_distro()
    where = ["-d", distro] if distro else []
    name = f"({distro})" if distro else ""
    print(f"\n##### WSL{name} の中で流します({len(buf.getvalue()) // 1024} KB を写す)", flush=True)
    cmd = ["wsl", *where, "-e", "bash", "-lc", script]
    return subprocess.run(cmd, input=buf.getvalue(), check=False).returncode == 0


def wsl_distro(listing: str | None = None) -> str | None:
    """流す WSL のディストリ。既定のディストリが Docker Desktop のもの(`docker-desktop`。bash が無い)
    のときは、ほかの最初のものを使う(2026-11-11: Docker Desktop が既定になり、`wsl -e bash` が
    起動できなかった)。
    既定が使えるとき・一覧を読めないときは None(既定のまま)。listing は試験用(`wsl -l -v` の出力)。"""
    if listing is None:
        try:
            raw = subprocess.run(["wsl", "-l", "-v"], capture_output=True, check=False, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        listing = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw else raw.decode(errors="ignore")
    rows = []  # (既定か, 名前, 版)。1 行目は見出し
    for line in listing.replace("\x00", "").splitlines()[1:]:
        parts = line.split()
        is_default = bool(parts) and parts[0] == "*"
        parts = parts[1:] if is_default else parts
        if len(parts) >= 3 and parts[-1].isdigit():
            rows.append((is_default, parts[0], int(parts[-1])))
    default = next((n for d, n, _ in rows if d), None)
    if default is None or not default.lower().startswith("docker-desktop"):
        return None
    usable = [n for d, n, v in rows if not d and v >= 2 and not n.lower().startswith("docker-desktop")]
    return usable[0] if usable else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--python", nargs="+", default=VERSIONS, choices=VERSIONS, help="流す Python の版")
    ap.add_argument("--no-wheel", action="store_true", help="配布物の確かめを飛ばす")
    ap.add_argument("--no-tests", action="store_true", help="試験と ruff を飛ばす(配布物だけ流し直す)")
    ap.add_argument("--linux", action="store_true", help="続けて WSL の中でも流す")
    ap.add_argument("--linux-only", action="store_true", help="WSL の中だけで流す")
    ap.add_argument(
        "--wsl-distro",
        default=os.environ.get("GENMODELFIT_WSL_DISTRO"),
        help="流す WSL のディストリ(既定: WSL の既定。それが docker-desktop ならほかの最初のもの)",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="push の前の毎回の分: 3.10 と 3.13 だけ(ruff は 3.10 で)と配布物。全部はマージの前に 1 回",
    )
    ap.add_argument("--env-dir", default=str(ROOT / ".ci"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.quick:
        if args.linux or args.linux_only:
            ap.error("--quick は --linux / --linux-only と一緒に使えない(全部を流すときは --quick を外す)")
        args.python = QUICK_VERSIONS
    if shutil.which("uv") is None:
        print("uv が見つかりません(https://docs.astral.sh/uv/)", file=sys.stderr)
        return 2

    ok = True
    if not args.linux_only:
        env_root = Path(os.path.expandvars(os.path.expanduser(args.env_dir)))
        env_root.mkdir(parents=True, exist_ok=True)
        r = Runner()
        with tempfile.TemporaryDirectory(prefix="gmf-ci-") as t:
            tmp = Path(t)
            for i, ver in enumerate([] if args.no_tests else args.python):
                # --quick では 3.12 を流さないので、ruff は最初の版で流す
                run_tests(r, ver, env_root, tmp / "smoke-cache", lint=(i == 0) if args.quick else None)
            if not args.no_wheel:
                run_wheel(r, [v for v in args.python if v in WHEEL_VERSIONS], tmp)
        r.summary()
        ok = r.ok
    if args.linux or args.linux_only:
        wsl_ok = run_in_wsl(args)
        print(f"\nWSL: {'すべて通りました' if wsl_ok else '落ちたものがあります'}", flush=True)
        ok = ok and wsl_ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
