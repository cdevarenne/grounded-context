"""The quickstart is the one doc a reader follows literally, so its instructions are checked.

Its acceptance criterion is that someone who has never seen the repo reaches a citation block.
A link that 404s or a subcommand that was renamed breaks that silently — prose passes review
while being false. These two checks are the same discipline the bundle and index-spec copies get.
"""

from __future__ import annotations

import re
from argparse import ArgumentParser, _SubParsersAction
from pathlib import Path

import pytest

from grounded_context.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "docs" / "quickstart.md"

# `[text](target)` — the target only, and only when it is a path rather than a URL or an anchor.
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((?!https?://|#)([^)#]+)[^)]*\)")

# `gctx …` with any global flags skipped, so the capture is the subcommand itself.
# `gctx-mcp` cannot match: the space is required.
INVOKED = re.compile(r"\bgctx (?:--[\w-]+(?:[= ]\S+)? )*([a-z][\w-]*)")


def _subcommands(parser: ArgumentParser) -> set[str]:
    """The subcommand names the parser accepts."""
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            return set(action.choices)
    raise AssertionError("the CLI parser no longer declares subcommands")


@pytest.mark.parametrize(
    "doc", [QUICKSTART, ROOT / "README.md"], ids=["quickstart", "readme"]
)
def test_every_relative_link_resolves(doc: Path) -> None:
    """A reader following the quickstart should not land on a 404."""
    broken = [
        target
        for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8"))
        if not (doc.parent / target).exists()
    ]
    assert not broken, f"{doc.name} links to paths that do not exist: {broken}"


def test_every_gctx_command_it_teaches_exists() -> None:
    """The quickstart names subcommands; renaming one must not leave the doc teaching it."""
    taught = set(INVOKED.findall(QUICKSTART.read_text(encoding="utf-8")))

    # Guards the guard: an expression that matched nothing would pass trivially.
    assert taught, "no gctx invocations were found in the quickstart"

    assert taught <= _subcommands(build_parser()), (
        f"the quickstart teaches subcommands the CLI does not have: "
        f"{sorted(taught - _subcommands(build_parser()))}"
    )
