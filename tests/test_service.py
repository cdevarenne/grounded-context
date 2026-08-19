"""The shared answer-builder, tested directly rather than through a surface.

The `BOTH` branch had no coverage at all, which is how it came to retrieve twice for a single
query. These tests sit on `service.ask` itself so the merge policy is pinned where it lives.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from grounded_context import service, telemetry
from grounded_context.bundle import Bundle
from grounded_context.provenance import MIXED
from grounded_context.router import BOTH, DETERMINISTIC as ROUTE_DETERMINISTIC, SEMANTIC as ROUTE_SEMANTIC, route
from grounded_context.service import as_of_date, ask, load_bundle, lookup_field

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

    def counting(query: str, size: int = service.SEMANTIC_RESULTS) -> service.SemanticResult:
        calls.append(query)
        return service.SemanticResult()

    monkeypatch.setattr(service, "semantic_citations", counting)
    ask(bundle, BOTH_QUERY, as_of_date())

    assert calls == [BOTH_QUERY], f"the semantic arm ran {len(calls)} times for one query"


def test_the_mixed_sample_query_routes_to_both_and_has_an_exact_hit(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the premise below the same way: both halves must be present for a merge to occur."""
    monkeypatch.setattr(service, "semantic_citations", lambda query, size=5: service.SemanticResult())
    assert route(MIXED_QUERY).route == BOTH
    assert ask(bundle, MIXED_QUERY, as_of_date())["citations"], (
        "the bundle no longer answers this query exactly"
    )


def test_both_leads_with_the_exact_hit_and_keeps_the_semantic_citations(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """router.md's merge policy: the exact hit answers, and no provenance is dropped."""
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult([PASSAGE], floor_passed=True),
    )
    envelope = ask(bundle, MIXED_QUERY, as_of_date())

    assert envelope["answer"] == "1,000,000"
    assert envelope["retrieval_path"] == MIXED
    assert [cite["path"] for cite in envelope["citations"]] == ["deterministic", "semantic"]


# --- telemetry at the emit site -------------------------------------------------------
#
# The module-level guarantees are tested in test_telemetry.py. What can only be tested here is
# that the answer path emits once per answer, and that the answer does not depend on it.

DETERMINISTIC_QUERY = "What is the exact context window of claude-opus-5?"
SEMANTIC_QUERY = "How should I chunk documents for retrieval?"

NO_ENGINE = "grounded_context.es_client.is_configured"


def emitted() -> list[dict[str, Any]]:
    """Everything recorded so far, from the per-test log conftest hands out."""
    path = Path(os.environ["GCTX_TELEMETRY_SINK"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_routed_deterministic_answer_records_exactly_one_event(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ask` reaches the same lookup the CLI calls directly; only one of them may emit."""
    assert route(DETERMINISTIC_QUERY).route == ROUTE_DETERMINISTIC

    ask(bundle, DETERMINISTIC_QUERY, as_of_date())

    events = emitted()
    assert len(events) == 1, f"one answer produced {len(events)} events"
    assert events[0]["route"] == ROUTE_DETERMINISTIC
    assert events[0]["canonical_hit"] is True
    assert events[0]["latency_ms"]["deterministic"] is not None
    assert events[0]["latency_ms"]["semantic"] is None


def test_a_direct_lookup_records_one_unrouted_event(bundle: Bundle) -> None:
    lookup_field(bundle, "anthropic.claude-opus-5", "context_window_tokens", as_of_date())

    events = emitted()
    assert len(events) == 1
    assert events[0]["route"] == telemetry.DIRECT
    assert events[0]["query"] == "anthropic.claude-opus-5 context_window_tokens"
    assert events[0]["canonical_hit"] is True


def test_a_direct_lookup_miss_is_recorded_as_the_curation_backlog(bundle: Bundle) -> None:
    """A precision query the bundle cannot answer is the signal, not a defect to hide."""
    lookup_field(bundle, "anthropic.claude-opus-5", "rate_limit_rpm", as_of_date())

    event = emitted()[0]
    assert event["canonical_hit"] is False
    assert event["refused"] is True
    assert event["cites"] == 0


def test_telemetry_never_changes_the_answer(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first non-negotiable: identical envelopes with the sink on and off, key for key."""
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult([PASSAGE], floor_passed=True),
    )
    recorded = ask(bundle, MIXED_QUERY, as_of_date())

    monkeypatch.setenv("GCTX_TELEMETRY", "0")
    silent = ask(bundle, MIXED_QUERY, as_of_date())

    assert recorded == silent
    assert len(emitted()) == 1, "the second answer must not have been recorded"


def test_the_answer_survives_a_failing_sink(
    bundle: Bundle,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The second non-negotiable: a broken sink is a no-op, never an error."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(blocked))

    envelope = lookup_field(
        bundle, "anthropic.claude-opus-5", "context_window_tokens", as_of_date()
    )

    assert envelope["answer"] == "1,000,000"
    assert capsys.readouterr().err.strip().count("\n") == 0


def test_an_unconfigured_engine_reports_no_floor_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unconfigured is not blocked. The probe never ran, so there is nothing to report."""
    monkeypatch.setattr(NO_ENGINE, lambda: False)

    result = service.semantic_citations("anything at all")

    assert result.citations == []
    assert result.floor_passed is None


def test_an_answer_with_no_cloud_still_records(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third non-negotiable: the instrument runs in the offline demo."""
    monkeypatch.setattr(NO_ENGINE, lambda: False)
    assert route(SEMANTIC_QUERY).route == ROUTE_SEMANTIC

    ask(bundle, SEMANTIC_QUERY, as_of_date())

    event = emitted()[0]
    assert event["refused"] is True
    assert event["canonical_hit"] is None, "the deterministic path was never consulted"
    assert event["relevance_floor_passed"] is None, "the floor probe never ran"


def test_the_answer_survives_an_unbuildable_event(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard covers building as well as writing — a malformed event is not an outage.

    `emit` swallows write failures on its own, so without this the guard around *building* one
    could be deleted and every other test would still pass.
    """

    def unbuildable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise KeyError("retrieval_path")

    monkeypatch.setattr(telemetry, "event", unbuildable)

    envelope = lookup_field(
        bundle, "anthropic.claude-opus-5", "context_window_tokens", as_of_date()
    )

    assert envelope["answer"] == "1,000,000"
    assert capsys.readouterr().err.strip().startswith("telemetry: KeyError")


def test_the_floor_score_reaches_the_event(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Threaded from the probe through SemanticResult to the record, not recomputed."""
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult(floor_passed=False, floor_score=7.9),
    )
    ask(bundle, SEMANTIC_QUERY, as_of_date())

    event = emitted()[0]
    assert event["relevance_floor_passed"] is False
    assert event["relevance_score"] == 7.9
    assert event["refused"] is True


def test_an_unconfigured_engine_reports_no_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same reasoning as the verdict: the probe never ran, so there is nothing to report."""
    monkeypatch.setattr(NO_ENGINE, lambda: False)

    result = service.semantic_citations("anything at all")

    assert result.floor_passed is None
    assert result.floor_score is None
