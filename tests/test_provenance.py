from datetime import date
from pathlib import Path

import pytest

from grounded_context.bundle import Bundle
from grounded_context.lookup import resolve
from grounded_context.provenance import (
    DETERMINISTIC,
    NOT_FOUND,
    SEMANTIC,
    citation,
    grounded_answer,
    render,
)
from grounded_context.router import route

BUNDLE = Path(__file__).resolve().parents[1] / "knowledge"
FRESH = date(2026, 8, 11)
LATER = date(2026, 10, 1)


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    return Bundle.load(BUNDLE)


def envelope_for(bundle, entity, field, as_of=FRESH, query=None):
    result = resolve(bundle, entity, field)
    cites = [citation(result, as_of)] if result else []
    router = route(query).as_dict() if query else None
    answer = str(result.value) if result else ""
    return grounded_answer(answer, cites, DETERMINISTIC, router)


def test_citation_carries_full_okf_provenance(bundle):
    cite = envelope_for(bundle, "anthropic.claude-opus-5", "context_window_tokens")[
        "citations"
    ][0]
    assert cite["source_id"] == "anthropic.claude-opus-5"
    assert cite["locator"] == "canonical.context_window_tokens"
    assert cite["path"] == DETERMINISTIC
    assert cite["method"] == "exact-lookup"
    assert cite["score"] is None  # deterministic hits are not ranked
    assert cite["trust_tier"] == "human-reviewed"
    assert cite["stale_after"] == "2026-09-09"
    assert cite["is_stale"] is False
    assert cite["source_url"].startswith("https://")


def test_no_citation_forces_the_refusal():
    """The non-negotiable rule: an empty citation list cannot yield an answer."""
    out = grounded_answer("1,000,000 tokens", [], DETERMINISTIC)
    assert out["answer"] == NOT_FOUND
    assert out["citations"] == []


def test_refusal_renders_an_explicit_reason():
    text = render(grounded_answer("", [], SEMANTIC, route("how do I chunk?").as_dict()))
    assert NOT_FOUND in text
    assert "nothing was returned rather than guessed" in text
    assert "router: SEMANTIC" in text


def test_render_shows_source_trust_and_freshness(bundle):
    text = render(envelope_for(bundle, "anthropic.claude-opus-5", "context_window_tokens"))
    assert "anthropic.claude-opus-5 · canonical.context_window_tokens" in text
    assert "deterministic (exact-lookup)" in text
    assert "human-reviewed 2026-08-10" in text
    assert "fresh until 2026-09-09" in text


def test_render_warns_when_the_fact_has_aged_out(bundle):
    """Same real data, clock moved forward — the warning is earned, not staged."""
    text = render(
        envelope_for(bundle, "anthropic.claude-opus-5", "context_window_tokens", LATER)
    )
    assert "⚠ STALE since 2026-09-09" in text
    assert "fresh until" not in text


def test_render_records_the_traversal_path(bundle):
    text = render(envelope_for(bundle, "anthropic.claude-opus-5", "method"))
    assert "traversed: anthropic.claude-opus-5 → anthropic.messages" in text


def test_render_omits_traversal_line_for_a_direct_hit(bundle):
    text = render(envelope_for(bundle, "anthropic.messages", "method"))
    assert "traversed:" not in text


def test_router_decision_is_part_of_the_audit_trail(bundle):
    envelope = envelope_for(
        bundle,
        "anthropic.claude-opus-5",
        "context_window_tokens",
        query="What is the exact context window of claude-opus-5?",
    )
    assert envelope["router"]["route"] == "DETERMINISTIC"
    assert "must not be ranked" in envelope["router"]["rationale"]
    assert "router: DETERMINISTIC" in render(envelope)


def test_both_paths_share_one_citation_shape(bundle):
    """A hand-built semantic citation must key-match a deterministic one."""
    deterministic = envelope_for(
        bundle, "anthropic.claude-opus-5", "context_window_tokens"
    )["citations"][0]
    semantic = {
        "path": SEMANTIC,
        "source_id": "elastic-hybrid-search-guide",
        "source_url": "https://www.elastic.co/…",
        "locator": "section:Reciprocal rank fusion",
        "method": "hybrid(bm25+elser,rrf)",
        # A fused RRF score, so ~0.09 — not a similarity. See docs/findings.md.
        "score": 0.0931,
        "verified_at": None,
        "trust_tier": None,
        "status": "stable",
        "stale_after": None,
        "is_stale": False,
        "hops": [],
        "snippet": "…",
    }
    assert set(deterministic) == set(semantic)
