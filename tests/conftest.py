"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _telemetry_stays_out_of_the_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test's telemetry log at its own temporary directory.

    Redirected rather than disabled, so the emit path still runs on every answer a test asks for.
    A sink that started raising would surface here rather than in a demo.
    """
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(tmp_path / "telemetry.ndjson"))
