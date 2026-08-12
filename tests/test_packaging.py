"""Packaging smoke tests: the installed console script, run as a subprocess.

Every other test imports the package directly, so all of them pass even when
`pyproject.toml` is wrong — a typo'd `[project.scripts]` target, an undeclared runtime
dependency, or an executable that never lands on PATH would ship silently. These run the
real `gctx` binary from a neutral working directory instead, and skip when it isn't
installed so a fresh clone still passes `pytest`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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


@requires_install
def test_both_declared_entry_points_are_installed(gctx: Runner) -> None:
    """Covers the entry-point targets, PyYAML, and default bundle resolution at once."""
    assert shutil.which("grounded-context") is not None
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
