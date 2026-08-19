"""Packaging smoke tests: the installed console script, run as a subprocess.

Every other test imports the package directly, so all of them pass even when
`pyproject.toml` is wrong — a typo'd `[project.scripts]` target, an undeclared runtime
dependency, or an executable that never lands on PATH would ship silently. These run the
real `gctx` binary from a neutral working directory instead, and skip when it isn't
installed so a fresh clone still passes `pytest`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Protocol

import pytest

GCTX = shutil.which("gctx")

requires_install = pytest.mark.skipif(
    GCTX is None, reason="no gctx on PATH — run `uv sync --extra dev` first"
)


class Runner(Protocol):
    """Calls the installed console script with the given arguments."""

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def gctx(tmp_path: Path) -> Runner:
    """Run `gctx` outside the repo, so nothing can resolve by accident of cwd."""

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        assert GCTX is not None  # guarded by requires_install
        return subprocess.run(
            [GCTX, *args], capture_output=True, text=True, cwd=tmp_path
        )

    return run


def test_declared_python_floor_is_not_above_the_development_pin() -> None:
    """The floor and the pin are deliberately different, and only one direction is valid.

    `.python-version` is the interpreter this repo develops and locks against;
    `requires-python` is the public floor a consumer must clear. The floor is set lower on
    purpose, so a reviewer on an older Python can still `pip install -e .` — a pin that
    doubles as the floor turns a build preference into a barrier. The floor must never
    exceed the pin, or the repo would declare support it never exercises.
    """
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        requires = tomllib.load(handle)["project"]["requires-python"]
    pin = tuple(int(part) for part in (root / ".python-version").read_text().strip().split("."))

    assert requires.startswith(">=")
    floor = tuple(int(part) for part in requires.removeprefix(">=").split("."))

    assert floor <= pin, f"declared floor {floor} exceeds the development pin {pin}"
    assert sys.version_info >= floor, "the running interpreter is below the declared floor"


def test_the_readme_test_count_matches_what_collection_produced() -> None:
    """The README publishes a number; this is what stops it drifting.

    The count depends on which extras are installed — without `mcp` the server tests are not
    collected at all — so the published figure is the full-suite one and this skips otherwise.
    It also skips when the invocation itself selected a subset, for the same reason: a partial
    run cannot confirm a number that describes a complete one.
    """
    import importlib.util

    from conftest import COLLECTED

    if not COLLECTED.get("whole_suite"):
        pytest.skip("narrowed run — this count does not describe the whole suite")
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("no mcp extra — collection is a subset of the published count")

    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    published = {int(match) for match in re.findall(r"(\d+) tests", readme)}

    assert published, "the README no longer states a test count"
    assert published == {COLLECTED["count"]}, (
        f"README says {sorted(published)}, collection produced {COLLECTED['count']}"
    )


@requires_install
def test_declared_entry_points_are_installed(gctx: Runner) -> None:
    """Covers the entry-point targets, PyYAML, and default bundle resolution at once."""
    assert shutil.which("grounded-context") is not None
    # Not executed: gctx-mcp serves on stdio and would block. Its import path is
    # covered by tests/test_mcp_server.py; this only asserts it reached PATH.
    assert shutil.which("gctx-mcp") is not None
    result = gctx("entities")
    assert result.returncode == 0, result.stderr
    assert "anthropic.claude-opus-5  [model]  human-reviewed" in result.stdout


@requires_install
def test_installed_script_answers_with_provenance(gctx: Runner) -> None:
    result = gctx("lookup", "anthropic.claude-opus-5", "context_window_tokens")
    assert result.returncode == 0, result.stderr
    assert "Answer: 1,000,000" in result.stdout
    assert "deterministic (exact-lookup)" in result.stdout


@requires_install
def test_installed_script_refuses_with_exit_code_1(gctx: Runner) -> None:
    """The exit-code contract is part of the interface, not an implementation detail."""
    result = gctx("lookup", "anthropic.claude-opus-5", "rate_limit_rpm")
    assert result.returncode == 1
    assert "Not found in the grounded sources." in result.stdout


@requires_install
def test_installed_script_emits_parseable_json(gctx: Runner) -> None:
    result = gctx("--json", "lookup", "anthropic.messages", "path")
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["answer"] == "/v1/messages"
    assert envelope["citations"][0]["trust_tier"] == "human-reviewed"
