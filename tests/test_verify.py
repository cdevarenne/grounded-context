"""`scripts/verify.py` must cover every check the repo has.

The value of one command is that running it means everything was checked. That holds only while
the stage list keeps up: a publisher added later and not wired in would leave `verify` reporting
success over an artifact it never looked at, which is worse than having no such command at all.

So rather than pinning the list, this derives it — any script offering a `--check` mode is a
check, and `verify` has to know about it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify import STAGES

SCRIPTS = ROOT / "scripts"


def scripts_offering_a_check_mode() -> list[str]:
    """Every script in `scripts/` that declares a `--check` flag."""
    return sorted(
        path.name for path in SCRIPTS.glob("*.py")
        if path.name != "verify.py" and '"--check"' in path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("script", scripts_offering_a_check_mode())
def test_verify_runs_every_script_that_offers_a_check_mode(script: str) -> None:
    commands = " ".join(stage.check for stage in STAGES)
    assert script in commands, (
        f"{script} has a --check mode that scripts/verify.py never runs, so `verify` would "
        f"report success without looking at what {script} guards"
    )


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.name)
def test_every_stage_names_a_command_that_exists(stage) -> None:
    for command in (stage.check, stage.update):
        if command is None:
            continue
        for token in command.split():
            if token.startswith("scripts/"):
                assert (ROOT / token).exists(), f"{stage.name} runs {token}, which is missing"


def test_the_suite_itself_is_one_of_the_stages() -> None:
    """Without it `verify` would check the data and skip the tests that guard the documents."""
    assert any("pytest" in stage.check for stage in STAGES)
