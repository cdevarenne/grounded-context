"""Shared collection state.

The README publishes a test count, and a hand-typed count drifts the moment a test is added.
Recording what collection actually produced lets one test assert the number instead.
"""

from __future__ import annotations

from typing import Any

COLLECTED: dict[str, int] = {}


def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    """Record how many tests this environment collected, for the README drift check."""
    COLLECTED["count"] = len(items)
