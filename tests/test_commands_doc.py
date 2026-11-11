# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""docs/commands.md・commands.ja.md(全コマンド・全オプションの一覧)が、パーサから作った出力と一致する(#111)。

直し方: `python tools/cli_reference.py`(ci_local・ci.yml には足さず、この試験が検査する)。
"""

import argparse
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cli_reference", ROOT / "tools" / "cli_reference.py")
cli_reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_reference)


@pytest.fixture(scope="module", params=["en", "ja"])
def page(request):
    lang = request.param
    return lang, cli_reference.TARGETS[lang].read_bytes().decode("utf-8"), cli_reference.render(lang)


def test_committed_page_equals_generated(page):
    _lang, committed, fresh = page
    assert committed == fresh, "docs/commands*.md が古い。python tools/cli_reference.py で作り直す"


def _command_names(parser, prefix="genmodelfit"):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sub in a.choices.items():
                yield f"{prefix} {name}"
                yield from _command_names(sub, f"{prefix} {name}")


def test_every_subcommand_has_a_heading(page):
    from genmodelfit import cli

    _lang, committed, _ = page
    headings = set(re.findall(r"^#{2,3} `([^`]+)`$", committed, flags=re.M))
    names = list(_command_names(cli.build_parser()))
    assert len(names) >= 20
    assert [n for n in names if n not in headings] == []


def test_every_environment_variable_is_documented(page):
    _, committed, _ = page
    used = set()
    for f in (ROOT / "src" / "genmodelfit").glob("*.py"):
        used |= set(re.findall(r"GENMODELFIT_[A-Z_]+", f.read_text(encoding="utf-8")))
    assert [v for v in sorted(used) if f"`{v}`" not in committed] == []


def test_no_machine_specific_paths(page):
    _, committed, _ = page
    assert not re.search(r"[A-Za-z]:[\/]Users[\/]|/home/\w+|/Users/\w+|AppData", committed)
    assert str(Path.home()) not in committed


def test_check_mode_passes():
    assert cli_reference.main(["--check"]) == 0


def test_user_name_is_replaced_only_inside_paths():
    # ユーザ名が owner のような普通の語でも、本文の `<owner>/<repo>` を壊さない(2026-11-11)
    pairs = [("owner", "<user>")]
    clean = cli_reference._clean
    assert clean("https://huggingface.co/<owner>/<repo>", pairs) == "https://huggingface.co/<owner>/<repo>"
    assert clean("the owner of the repo", pairs) == "the owner of the repo"
    assert clean(r"/home/owner/x and C:\Users\owner", pairs) == r"/home/<user>/x and C:\Users\<user>"
