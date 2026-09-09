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
from grounded_context.provenance import MIXED, NOT_FOUND
from grounded_context.router import BOTH, route
from grounded_context.router import DETERMINISTIC as ROUTE_DETERMINISTIC
from grounded_context.router import SEMANTIC as ROUTE_SEMANTIC
from grounded_context.service import as_of_date, ask, load_bundle, lookup_field

# Cross-entity, so the router sends it to BOTH, and the bundle holds no single exact answer.
#: An ambiguous question: it names no entity, no canonical field and no precision signal, so the
#: router returns BOTH and the deterministic path finds nothing. It used to be "which of these
#: models support vision?", which stopped being a deterministic miss when `query_entities` began
#: answering rollups. A fixture has to keep the property it was chosen for.
BOTH_QUERY = "Tell me about batch processing"
ROLLUP_QUERY = "Which of these models support vision?"

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
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
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
    assert [r.getMessage() for r in caplog.records] == [
        "telemetry: KeyError: 'retrieval_path'"
    ], "reported once, through logging, and the answer still returned"


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


# --- a precision miss refuses rather than ranking (docs/specs/router.md) ---------------

# Routes to BOTH as a cross-entity comparison, and "cheaper" maps to no single canonical
# field — so the deterministic path misses and the fallback is the thing under test.
PRECISION_MISS_QUERY = "Is Sonnet 5 cheaper than Opus 5?"


def test_the_comparison_sample_is_a_precision_both() -> None:
    """Guards the premise: if routing changed, the test below would pass while proving nothing."""
    decision = route(PRECISION_MISS_QUERY)
    assert decision.route == BOTH
    assert decision.precision is True


def test_a_precision_miss_refuses_instead_of_ranking(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure the whole design exists to prevent, pinned.

    A comparison asks for exact values. When the bundle does not hold them, passages about an
    adjacent topic are not an answer — they are a plausible, cited, wrong one. The curation gap
    is reported as a refusal, which the telemetry then counts as a canonical miss.
    """
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult([PASSAGE], floor_passed=True, floor_score=19.0),
    )
    envelope = ask(bundle, PRECISION_MISS_QUERY, as_of_date())

    assert envelope["answer"] == NOT_FOUND
    assert envelope["citations"] == []


def test_a_rollup_leads_with_the_exact_answer_and_keeps_the_passages(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollup routes BOTH, so both engines run. The exact answer must lead.

    This is the case that retired eval Q3's declared deviation. The question has an exact answer
    for every model, and the canonical layer now gives it with one citation each. Passages still
    follow, because the router asked for both and provenance is never dropped.
    """
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult([PASSAGE], floor_passed=True, floor_score=19.0),
    )
    envelope = ask(bundle, ROLLUP_QUERY, as_of_date())

    assert envelope["answer"] == "Claude Haiku 4.5: yes; Claude Opus 5: yes; Claude Sonnet 5: yes"
    assert envelope["retrieval_path"] == MIXED
    paths = [c["path"] for c in envelope["citations"]]
    assert paths == ["deterministic"] * 3 + ["semantic"], "exact hits lead, passages follow"


def test_an_ambiguous_both_still_falls_back_to_passages(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exception is narrow. Only a comparison switches the fallback off."""
    assert route(BOTH_QUERY).precision is False
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult([PASSAGE], floor_passed=True, floor_score=19.0),
    )
    envelope = ask(bundle, BOTH_QUERY, as_of_date())

    assert envelope["answer"] == PASSAGE["snippet"]
    assert len(envelope["citations"]) == 1


# --- an unreachable cluster (ELX-51) --------------------------------------------------------


class _Unreachable(Exception):
    """Stands in for a transport failure without needing the `es` extra installed."""


def _cluster_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured, and every request to it raises. The one state `is_configured()` cannot see."""
    from grounded_context import es_client

    monkeypatch.setattr(es_client, "is_configured", lambda: True)
    monkeypatch.setattr(es_client, "transport_errors", lambda: (_Unreachable,))
    monkeypatch.setattr(
        es_client, "client", lambda **kw: (_ for _ in ()).throw(_Unreachable("Connection error"))
    )


def test_an_unreachable_cluster_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It used to propagate: `gctx ask` died on a traceback and the MCP tool call failed.

    An outage has the same *outcome* as an unconfigured cluster — no passages, so the refusal —
    and it has to arrive the same way. What differs is that it is reported.
    """
    _cluster_is_down(monkeypatch)

    result = service.semantic_citations("explain hybrid search")

    assert result.citations == []
    assert result.unavailable is True
    assert "semantic path unavailable" in capsys.readouterr().err


def test_an_outage_is_not_recorded_as_an_unconfigured_cluster(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None` is not `False`. A deployment with no cluster and one with a broken cluster return
    the same empty citation list, and the corpus-state signal must not merge them."""
    monkeypatch.setattr("grounded_context.es_client.is_configured", lambda: False)
    assert service.semantic_citations("q").unavailable is None

    _cluster_is_down(monkeypatch)
    assert service.semantic_citations("q").unavailable is True


def test_an_outage_reaches_telemetry(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _cluster_is_down(monkeypatch)
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(tmp_path / "t.ndjson"))

    ask(bundle, "how do I stream responses from the API?", as_of_date("2026-08-20"))

    events = [json.loads(line) for line in (tmp_path / "t.ndjson").read_text().splitlines()]
    assert [e["semantic_unavailable"] for e in events] == [True]
    assert events[0]["refused"] is True, "no passages, so the refusal — the envelope is unchanged"


def test_an_outage_still_returns_the_exact_hit_on_a_both_query(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the semantic arm is lost. The deterministic path never touched the network."""
    _cluster_is_down(monkeypatch)

    envelope = ask(bundle, "Compare claude-opus-5 and claude-sonnet-5 on max output tokens.",
                   as_of_date("2026-08-20"))

    assert envelope["answer"] != NOT_FOUND
    # Every surviving citation is deterministic — that is the property. The count is two because
    # this query is a comparison and both sides are answered (ELX-70); it was one before that.
    assert {c["path"] for c in envelope["citations"]} == {"deterministic"}
    assert len(envelope["citations"]) == 2


# --- comparisons answer every side, or none (ELX-70) ----------------------------------------
#
# "Compare the max output tokens of A and B" used to answer 64,000 — B's value, cited to B, with
# nothing saying A had been dropped. "Is A cheaper than B" refused. Same question shape, opposite
# behaviour, and the difference was only whether a field synonym happened to match.

COMPARISON_QUERY = "Compare the max output tokens of claude-sonnet-5 and claude-haiku-4-5."
EXPLORATORY_COMPARISON = (
    "What is the difference between claude-opus-5 and claude-sonnet-5 max output tokens?"
)


def _with_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service, "semantic_citations",
        lambda query, size=5: service.SemanticResult(
            [PASSAGE], floor_passed=True, floor_score=19.0
        ),
    )


@pytest.mark.parametrize("query", [COMPARISON_QUERY, EXPLORATORY_COMPARISON])
def test_a_comparison_answers_every_side_it_named(
    bundle: Bundle, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    """Both sides, each separately cited, whichever router branch the question arrived by.

    `EXPLORATORY_COMPARISON` is the reason this is not gated on `decision.precision`: the router
    reads "difference between" as exploratory, so that phrasing never sets the flag and would
    have kept answering with one side.
    """
    envelope = ask(bundle, query, as_of_date())
    exact = [c for c in envelope["citations"] if c["path"] == "deterministic"]
    assert len(exact) == 2, "a two-sided question needs two exact citations"
    assert {c["source_id"] for c in exact} == set(service.find_entities(bundle, query))
    for concept_id in service.find_entities(bundle, query):
        assert concept_id in {c["source_id"] for c in exact}
    assert envelope["answer"].count(":") == 2, envelope["answer"]


def test_a_comparison_refuses_when_one_side_cannot_be_answered(bundle: Bundle) -> None:
    """Every side or none. A half-answered comparison is the answer to a different question.

    `max_output_tokens_batch_api` is the case the bundle actually has: Opus 5 and Sonnet 5 carry
    it and Haiku 4.5 does not. Answering Opus alone would be a cited number that reads as though
    the comparison had been made.
    """
    envelope = service._comparison_envelope(
        bundle,
        ["anthropic.claude-opus-5", "anthropic.claude-haiku-4-5"],
        "max_output_tokens_batch_api",
        as_of_date(),
        route(COMPARISON_QUERY),
    )
    assert envelope["answer"] == NOT_FOUND
    assert envelope["citations"] == []


def test_one_named_entity_is_still_a_plain_lookup(bundle: Bundle) -> None:
    """The comparison path must not capture the single-entity question it sits beside."""
    envelope = ask(bundle, "What is the max output tokens for claude-sonnet-5?", as_of_date())
    assert envelope["answer"] == "128,000"
    assert len(envelope["citations"]) == 1


# --- fields that answer correctly and were never pinned (ELX-68, ELX-69, ELX-71) -------------
#
# Found by running every query in the demo set rather than by a failure. All of these were right
# already; nothing asserted them, in the resolver that has broken three times in three weeks.


@pytest.mark.parametrize(
    "question, expected, locator",
    [
        # ELX-68 — the endpoint concept carries six canonical fields and only `path` had a case.
        ("What is the api version for the messages endpoint?", "2023-06-01", "api_version"),
        ("What is the auth header the messages endpoint expects?", "x-api-key", "auth_header"),
        ("What is the base url for the messages endpoint?",
         "https://api.anthropic.com", "base_url"),
        # ELX-69 — the two identity strings, which are different strings on Haiku.
        ("What is the api alias for claude-opus-5?", "claude-opus-5", "api_alias"),
        ("What is the model id for claude-haiku-4-5?",
         "claude-haiku-4-5-20251001", "model_string"),
    ],
)
def test_an_exact_field_answers_through_ask(
    bundle: Bundle, question: str, expected: str, locator: str
) -> None:
    envelope = ask(bundle, question, as_of_date())
    assert envelope["answer"] == expected
    assert envelope["citations"][0]["locator"] == f"canonical.{locator}"


def test_the_alias_and_the_pinned_id_are_not_interchangeable(bundle: Bundle) -> None:
    """Haiku 4.5 is the case the bundle prose calls the point of the file.

    `model_string` is `claude-haiku-4-5-20251001` and `api_alias` is `claude-haiku-4-5`. A
    resolver that collapsed the two would answer the wrong string with a citation, which is the
    ELX-44 and issue #11 failure shape a third time.
    """
    haiku = "anthropic.claude-haiku-4-5"
    assert lookup_field(bundle, haiku, "model_string", as_of_date())["answer"] == (
        "claude-haiku-4-5-20251001"
    )
    assert lookup_field(bundle, haiku, "api_alias", as_of_date())["answer"] == "claude-haiku-4-5"


@pytest.mark.parametrize(
    "question",
    [
        "What is the rate limit for claude-opus-5?",
        "How many parameters does claude-sonnet-5 have?",
    ],
)
def test_a_field_the_bundle_does_not_hold_refuses_through_ask(
    bundle: Bundle, question: str
) -> None:
    """ELX-71. `gctx lookup` had this pinned; `ask` is the path an agent actually takes.

    The entity is real and the field is not, which is the shape most likely to tempt a guess:
    everything about the question looks answerable.
    """
    envelope = ask(bundle, question, as_of_date())
    assert envelope["answer"] == NOT_FOUND
    assert envelope["citations"] == []
