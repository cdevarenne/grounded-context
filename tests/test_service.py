"""The shared answer-builder, tested directly rather than through a surface.

The `BOTH` branch had no coverage at all, which is how it came to retrieve twice for a single
query. These tests sit on `service.ask` itself so the merge policy is pinned where it lives.
"""

from __future__ import annotations

from typing import Any

import pytest

from grounded_context import service
from grounded_context.bundle import Bundle
from grounded_context.provenance import MIXED
from grounded_context.router import BOTH, route
from grounded_context.service import as_of_date, ask, load_bundle

# Cross-entity, so the router sends it to BOTH, and the bundle holds no single exact answer.
BOTH_QUERY = "Which of these models support vision?"

# Also BOTH, but this one names an entity and a field the bundle does hold, so the merge runs.
MIXED_QUERY = "Compare the context window of claude-opus-5 and GPT-5."

# The semantic citation contract, as a stand-in for a real hit. Same shape the MCP tests use.
PASSAGE: dict[str, Any] = {
    "path": "semantic",
    "source_id": "elastic-rrf",
    "source_url": "https://example.test",
    "locator": "chunk:1",
    "method": "hybrid(bm25+elser,rrf)",
    "score": 0.09,
    "verified_at": "2026-08-13T00:00:00-07:00",
    "trust_tier": None,
    "status": None,
    "stale_after": None,
    "is_stale": False,
    "hops": [],
    "snippet": "rank_constant determines influence.",
}


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


def test_the_mixed_sample_query_routes_to_both_and_has_an_exact_hit(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the premise below the same way: both halves must be present for a merge to occur."""
    monkeypatch.setattr(service, "semantic_citations", lambda query, size=5: [])
    assert route(MIXED_QUERY).route == BOTH
    assert ask(bundle, MIXED_QUERY, as_of_date())["citations"], (
        "the bundle no longer answers this query exactly"
    )


def test_both_leads_with_the_exact_hit_and_keeps_the_semantic_citations(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """router.md's merge policy: the exact hit answers, and no provenance is dropped."""
    monkeypatch.setattr(service, "semantic_citations", lambda query, size=5: [PASSAGE])
    envelope = ask(bundle, MIXED_QUERY, as_of_date())

    assert envelope["answer"] == "1,000,000"
    assert envelope["retrieval_path"] == MIXED
    assert [cite["path"] for cite in envelope["citations"]] == ["deterministic", "semantic"]
