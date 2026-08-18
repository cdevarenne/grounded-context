"""The shared answer-builder, tested directly rather than through a surface.

The `BOTH` branch had no coverage at all, which is how it came to retrieve twice for a single
query. These tests sit on `service.ask` itself so the merge policy is pinned where it lives.
"""

from __future__ import annotations

from typing import Any

import pytest

from grounded_context import service
from grounded_context.bundle import Bundle
from grounded_context.router import BOTH, route
from grounded_context.service import as_of_date, ask, load_bundle

# Cross-entity, so the router sends it to BOTH, and the bundle holds no single exact answer.
BOTH_QUERY = "Which of these models support vision?"


@pytest.fixture
def bundle() -> Bundle:
    return load_bundle()


def test_the_sample_query_still_routes_to_both() -> None:
    """Guards the premise below: if routing changed, that test would pass while proving nothing."""
    assert route(BOTH_QUERY).route == BOTH


def test_both_retrieves_once_per_query(bundle: Bundle, monkeypatch: pytest.MonkeyPatch) -> None:
    """The semantic arm is a network round trip, and per-path latency must report one of them."""
    calls: list[str] = []

    def counting(query: str, size: int = service.SEMANTIC_RESULTS) -> list[dict[str, Any]]:
        calls.append(query)
        return []

    monkeypatch.setattr(service, "semantic_citations", counting)
    ask(bundle, BOTH_QUERY, as_of_date())

    assert calls == [BOTH_QUERY], f"the semantic arm ran {len(calls)} times for one query"
