"""Shared collection state.

The README publishes a test count, and a hand-typed count drifts the moment a test is added.
Recording what collection actually produced lets one test assert the number instead.

A count only describes the published figure when the run collected everything. Running one file,
or filtering with `-k`, produces a smaller number that says nothing about the suite — so the run
also records whether it can speak for the whole suite, and the check stands down when it cannot.
"""

from __future__ import annotations

from typing import Any

COLLECTED: dict[str, Any] = {}

# Selection options that narrow a run. `-k` and `-m` carry their expression in a separate
# argument that `config.args` does not show, so they are read off the parsed options instead.
SELECTORS = ("keyword", "markexpr", "deselect", "lf", "ff")


def _is_whole_suite(config: Any) -> bool:
    """True when this invocation collected the whole suite rather than a chosen part of it."""
    if list(config.args) != list(config.getini("testpaths")):
        return False
    return not any(getattr(config.option, name, None) for name in SELECTORS)


def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    """Record what collection produced, and whether it can speak for the whole suite."""
    COLLECTED["count"] = len(items)
    COLLECTED["whole_suite"] = _is_whole_suite(config)
