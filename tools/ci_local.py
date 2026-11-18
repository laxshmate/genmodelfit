# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ci.yml と同じ検査を手元で流す(2026-10-22 オーナー決定。2026-10-28 に毎回は --quick に)。

    python tools/ci_local.py --quick         # push の前に毎回: 3.10 と 3.13、ruff、配布物(約 15 分)
    python tools/ci_local.py                 # この PC の OS で Python 3.10〜3.13 と配布物
    python tools/ci_local.py --linux         # 続けて WSL の中でも同じものを流す(Windows から)
    python tools/ci_local.py --linux-only    # WSL の中だけ
    python tools/ci_local.py --python 3.12 --no-wheel   # 絞って流す
    python tools/ci_local.py --quick --resume   # 途中で止まったら、同じ中身のまま続きから(下の「再開」)

再開(--resume): 検査が通るたびに、git の管理するファイル全部の中身から作った鍵と一緒に記録する
(--env-dir の中の resume-*.json と resume-*-py<版>.txt)。--resume を付けると、鍵が同じ
(ファイルを 1 つも変えていない)ときだけ、前に通った ruff・pytest(試験 1 件ずつ)・CLI の検査と、
全部通った配布物の検査を飛ばす。飛ばしたものはまとめに skip と出る。鍵が違う・記録が無い・読めないときは
記録を全部消して最初から流す。--resume を付けなければいつも最初から流す(記録は新しく始める)。
venv と install は毎回流す。落ちた検査は記録しない。迷ったら流し直す。再開して通った結果は「どの試験も、
同じ中身で 1 回は通った」の意味(1 回の通しで全部通ったのとは違う。PR の説明には再開したことも書く)。

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
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = Path(__file__).resolve().parent / "ci_plugins"  # pytest のプラグインだけを置く
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


class Resume:
    """続きから流すための記録(--env-dir の中。中身が同じかどうかは tree_key の鍵で決める)。

    resume-<OS>.json = {"key": 鍵, "passed": [通った検査の名前]}、
    resume-<OS>-py<版>.txt = 通った試験の nodeid。
    start で、--resume が無い・鍵が違う・読めないなら記録を全部消す。key が None なら記録も飛ばしもしない。
    recheck があれば、記録する前に鍵を作り直して確かめる(流している間にファイルを変えたら、記録を捨てる)。"""

    def __init__(
        self, env_root: Path, key: str | None, *, recheck: Callable[[], str | None] | None = None
    ) -> None:
        self.env_root = env_root
        self.key = key
        self.recheck = recheck
        self.passed: list[str] = []
        self.active = False  # 前の記録を使っている(飛ばしてよい)
        self.enabled = key is not None
        self.why = ""

    @property
    def json_path(self) -> Path:
        return self.env_root / f"resume-{OS_TAG}.json"

    def pytest_file(self, ver: str) -> Path:
        return (self.env_root / f"resume-{OS_TAG}-py{ver}.txt").resolve()

    def clear(self) -> None:
        for p in self.env_root.glob(f"resume-{OS_TAG}*"):
            with contextlib.suppress(OSError):
                p.unlink()

    def _write(self) -> None:
        tmp = self.json_path.with_name(self.json_path.name + ".tmp")
        tmp.write_text(json.dumps({"key": self.key, "passed": self.passed}, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.json_path)

    def _load(self) -> list[str] | None:
        """記録の通った検査の名前。鍵が違う・形が違う・読めないときは None(理由は self.why)。"""
        try:
            doc = json.loads(self.json_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.why = "記録が無いので最初から流します"
            return None
        except (OSError, ValueError):
            self.why = "記録を読めないので最初から流します"
            return None
        names = doc.get("passed") if isinstance(doc, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            self.why = "記録を読めないので最初から流します"
            return None
        if doc.get("key") != self.key:
            self.why = "中身が変わったので最初から流します"
            return None
        return names

    def start(self, use: bool) -> None:
        self.env_root.mkdir(parents=True, exist_ok=True)
        if not self.enabled:
            self.clear()
            print("再開: 中身の鍵を作れないので、記録も飛ばしもしません(全部流します)", flush=True)
            return
        names = self._load() if use else None
        if names is None:
            self.clear()
            self.passed = []
            self._write()
            print(
                f"再開: {self.why}" if use else "再開: 記録を新しく始めます(最初から全部流します)", flush=True
            )
            return
        self.passed = names
        self.active = True
        print(f"再開: 同じ中身で前に通った検査 {len(names)} 個を飛ばします", flush=True)

    def is_passed(self, name: str) -> bool:
        return self.enabled and self.active and name in self.passed

    def mark(self, name: str) -> None:
        if not self.enabled:
            return
        if self.recheck is not None and self.recheck() != self.key:
            print(
                "*** 流している間にファイルが変わったので、記録を捨てます(続きからは流せません)", flush=True
            )
            self.enabled = False
            self.active = False
            self.clear()
            return
        if name not in self.passed:
            self.passed.append(name)
        self._write()


class Runner:
    def __init__(self, resume: Resume | None = None) -> None:
        self.results: list[tuple[str, str, float]] = []  # (名前, ok / FAIL / skip, 秒)
        self.resume = resume

    def step(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        quiet: bool = False,
        resume_key: str | None = None,
    ) -> bool:
        """resume_key があれば、同じ中身で前に通っているとき流さず skip にする。通ったら記録する。"""
        if resume_key and self.resume is not None and self.resume.is_passed(resume_key):
            self.skip(name)
            return True
        print(f"\n=== [{OS_TAG}] {name}", flush=True)
        t0 = time.monotonic()
        kw: dict = {"cwd": cwd or ROOT, "env": env or child_env()}
        if quiet:
            kw.update(stdout=subprocess.DEVNULL)
        ok = subprocess.run(cmd, check=False, **kw).returncode == 0
        self.results.append((name, "ok" if ok else "FAIL", time.monotonic() - t0))
        if not ok:
            print(f"*** 失敗: {name}", flush=True)
        elif resume_key and self.resume is not None:
            self.resume.mark(resume_key)
        return ok

    def skip(self, name: str) -> None:
        print(f"\n=== [{OS_TAG}] {name}(skip: 同じ中身で前に通った)", flush=True)
        self.results.append((name, "skip", 0.0))

    def record(self, name: str, ok: bool) -> None:
        self.results.append((name, "ok" if ok else "FAIL", 0.0))
        if not ok:
            print(f"*** 失敗: {name}", flush=True)

    @property
    def ok(self) -> bool:
        return all(status != "FAIL" for _, status, _ in self.results)

    def summary(self) -> None:
        print(f"\n===== まとめ({OS_TAG})")
        for name, status, sec in self.results:
            print(f"  {status.ljust(4)}  {sec:7.1f}s  {name}")
        skipped = sum(1 for _, status, _ in self.results if status == "skip")
        if skipped:
            print(f"(再開: {skipped} 個は同じ中身で前に通ったので飛ばしました)")
        print("すべて通りました" if self.ok else "落ちたものがあります", flush=True)


def child_env(**extra: str) -> dict[str, str]:
    # GMF_CI_*: 外から来た再開の指定(この道具を pytest の中から流したときなど)を子に渡さない
    drop = ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GMF_CI_")}
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
        # ruff は版ごとの venv で流すので、記録の名前に版を入れる(--quick の 3.10 の結果を 3.12 の分にしない)
        r.step("ruff check", [str(py), "-m", "ruff", "check", "."], resume_key=f"py{ver} ruff check")
        r.step(
            "ruff format --check",
            [str(py), "-m", "ruff", "format", "--check", "."],
            resume_key=f"py{ver} ruff format --check",
        )
    pytest_cmd = [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    pytest_env = None
    res = r.resume
    if res is not None and res.enabled:
        # 試験 1 件ずつの記録・再開(ci_plugins/ci_resume.py)。記録しない回(鍵が None)は付けない
        pytest_cmd += ["-p", "ci_resume"]
        extra = {"PYTHONPATH": str(PLUGIN_DIR), "GMF_CI_RESUME_FILE": str(res.pytest_file(ver))}
        if res.active:
            extra["GMF_CI_RESUME_SKIP"] = "1"
        pytest_env = child_env(**extra)
    r.step(f"py{ver} pytest", pytest_cmd, env=pytest_env, resume_key=f"py{ver} pytest")
    exe = venv_bin(env_dir, "genmodelfit")
    smoke_env = child_env(GENMODELFIT_CACHE_DIR=str(cache))
    for args in SMOKE:
        name = f"py{ver} CLI {args[0]}"
        r.step(name, [exe, *args], env=smoke_env, quiet=True, resume_key=name)
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


def tree_key() -> str | None:
    """作業ツリーの中身の鍵: tracked_files を並べ替え、相対パスと中身の sha256 を順に足した sha256。
    git が無い・ファイルを読めないなどのときは None(その回は記録も飛ばしもしない)。"""
    try:
        h = hashlib.sha256()
        for f in sorted(tracked_files()):
            h.update(f.replace("\\", "/").encode("utf-8"))
            h.update(b"\0")
            h.update(hashlib.sha256((ROOT / f).read_bytes()).hexdigest().encode("ascii"))
            h.update(b"\n")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"再開: 中身の鍵を作れませんでした({type(e).__name__})", flush=True)
        return None
    return h.hexdigest()


def run_in_wsl(args: argparse.Namespace) -> bool:
    if not IS_WIN:
        print("--linux は Windows から使います(Linux ではそのまま流してください)", file=sys.stderr)
        return False
    # WSL の中には git が無いので、鍵は Windows 側で作って渡す。写している間にファイルが変わったら鍵は渡さない
    key_before = tree_key()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in tracked_files():
            tar.add(ROOT / f, arcname=f)
    key = key_before if key_before is not None and tree_key() == key_before else None
    inner = ["--env-dir", f"{WSL_HOME_DIR}/envs", "--tree-key", key or "none", "--python", *args.python]
    if args.resume:
        inner.append("--resume")
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
    ap.add_argument(
        "--resume",
        action="store_true",
        help="途中で止まったとき、同じ中身(ファイルを変えていない)なら前に通った検査・試験を飛ばして続ける",
    )
    ap.add_argument("--tree-key", default=None, help=argparse.SUPPRESS)  # WSL の中で使う(git が無い)
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
        if args.tree_key is not None:
            resume = Resume(env_root, None if args.tree_key == "none" else args.tree_key)
        else:
            resume = Resume(env_root, tree_key(), recheck=tree_key)
        resume.start(args.resume)
        r = Runner(resume)
        with tempfile.TemporaryDirectory(prefix="gmf-ci-") as t:
            tmp = Path(t)
            for i, ver in enumerate([] if args.no_tests else args.python):
                # --quick では 3.12 を流さないので、ruff は最初の版で流す
                run_tests(r, ver, env_root, tmp / "smoke-cache", lint=(i == 0) if args.quick else None)
            if not args.no_wheel:
                wheel_versions = [v for v in args.python if v in WHEEL_VERSIONS]
                # 中の検査は一時フォルダに頼るので、まとめて 1 つ。流す版の組が同じときだけ飛ばす
                wkey = "wheel " + ",".join(wheel_versions)
                if resume.is_passed(wkey):
                    r.skip("配布物")
                else:
                    n0 = len(r.results)
                    run_wheel(r, wheel_versions, tmp)
                    new = r.results[n0:]
                    if new and all(status == "ok" for _, status, _ in new):
                        resume.mark(wkey)
        r.summary()
        ok = r.ok
    if args.linux or args.linux_only:
        wsl_ok = run_in_wsl(args)
        print(f"\nWSL: {'すべて通りました' if wsl_ok else '落ちたものがあります'}", flush=True)
        ok = ok and wsl_ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
