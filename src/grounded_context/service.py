"""Answer construction shared by every consumer of the deterministic path.

The CLI and the MCP server must produce identical envelopes. The citation contract is the
product here, so it lives in one place rather than being re-implemented per surface.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from . import telemetry
from .bundle import Bundle
from .lookup import find_entity, find_field, is_rollup, query_entities, resolve
from .provenance import (
    DETERMINISTIC,
    MIXED,
    SEMANTIC,
    AnswerEnvelope,
    Citation,
    citation,
    grounded_answer,
)
from .router import BOTH as ROUTE_BOTH
from .router import SEMANTIC as ROUTE_SEMANTIC
from .router import QueryRouter, Route, route
from .store import KnowledgeStore

SEMANTIC_RESULTS = 5

DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / "knowledge"


def bundle_root(explicit: str | None = None) -> Path:
    """Resolve the bundle directory: explicit path, then `GC_BUNDLE`, then the default."""
    return Path(explicit or os.environ.get("GC_BUNDLE") or DEFAULT_BUNDLE)


def load_bundle(explicit: str | None = None) -> Bundle:
    """Load and validate the knowledge bundle."""
    return Bundle.load(bundle_root(explicit))


def as_of_date(raw: str | None = None) -> date:
    """Parse an ISO date for staleness evaluation, defaulting to today."""
    if raw is None:
        return datetime.now(UTC).date()
    return date.fromisoformat(raw)


def format_value(value: Any) -> str:
    """Render a canonical value for reading: booleans as yes/no, ints with separators."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _elapsed_ms(started: float) -> float:
    """Wall-clock milliseconds since a `perf_counter` reading."""
    return (perf_counter() - started) * 1000


def _lookup_envelope(
    bundle: KnowledgeStore,
    entity_id: str,
    field: str,
    as_of: date,
    decision: Route | None = None,
) -> AnswerEnvelope:
    """Envelope for one exact field, or the refusal when the bundle doesn't hold it."""
    result = resolve(bundle, entity_id, field)
    router = decision.as_dict() if decision else None
    if result is None:
        return grounded_answer("", [], DETERMINISTIC, router)
    return grounded_answer(
        format_value(result.value), [citation(result, as_of)], DETERMINISTIC, router
    )


def _rollup_envelope(
    bundle: KnowledgeStore, field: str, as_of: date, decision: Route
) -> AnswerEnvelope:
    """Answer one field across every concept that holds it, with a citation for each.

    The answer names each entity and its value. It is built from the results, not written, so it
    cannot say something the citations below it do not support.
    """
    results = query_entities(bundle, field)
    if not results:
        return grounded_answer("", [], DETERMINISTIC, decision.as_dict())
    answer = "; ".join(
        f"{result.concept.title}: {format_value(result.value)}" for result in results
    )
    return grounded_answer(
        answer, [citation(result, as_of) for result in results], DETERMINISTIC, decision.as_dict()
    )


def lookup_field(
    bundle: KnowledgeStore,
    entity_id: str,
    field: str,
    as_of: date,
    decision: Route | None = None,
) -> AnswerEnvelope:
    """Envelope for one exact field, or the refusal when the bundle doesn't hold it.

    This is the entry point for a lookup that names its entity and field outright — `gctx lookup`
    and the MCP `lookup_canonical_fact` tool — so it records one event. `ask` builds through the
    private helper instead, so a routed deterministic answer produces one event, not two.
    """
    started = perf_counter()
    envelope = _lookup_envelope(bundle, entity_id, field, as_of, decision)
    elapsed = _elapsed_ms(started)
    telemetry.record(
        f"{entity_id} {field}", envelope, total_ms=elapsed, deterministic_ms=elapsed
    )
    return envelope


@dataclass(frozen=True)
class SemanticResult:
    """Citations, plus what the relevance floor did — which the envelope has no field for."""

    citations: list[Citation] = dataclass_field(default_factory=list)
    #: `True` cleared, `False` blocked, `None` the probe never ran.
    floor_passed: bool | None = None
    #: The pre-fusion score behind that verdict, so a near miss is distinguishable from a
    #: query that was never in domain. `None` whenever `floor_passed` is.
    floor_score: float | None = None
    #: `True` the cluster was configured and could not be reached, `False` it answered, `None`
    #: no request was attempted because nothing is configured. `None` is not `False`, for the
    #: same reason it is not on `canonical_hit`: a deployment that never had a cluster and one
    #: whose cluster is down produce the same empty citation list and are not the same fact.
    unavailable: bool | None = None


def semantic_citations(query: str, size: int = SEMANTIC_RESULTS) -> SemanticResult:
    """Hybrid-search citations, or none when Elasticsearch isn't configured.

    Imported lazily so the deterministic path never needs the `es` extra installed.

    The floor is probed here rather than inside `search`, because its verdict is a signal the
    answer envelope has nowhere to carry. Both calls together are the same two round trips
    `search(floor=...)` already makes on its own — the probe is not a new cost, only a visible one.
    """
    from .es_client import is_configured

    if not is_configured():
        # The probe never ran, so there is no verdict: absent, which is not the same as blocked.
        return SemanticResult()

    from .es_client import client, transport_errors
    from .semantic import probe, search

    try:
        es = client()
        cleared, score = probe(query, es=es)
        if not cleared:
            return SemanticResult(floor_passed=False, floor_score=score, unavailable=False)
        return SemanticResult(
            search(query, size=size, es=es, floor=None),
            floor_passed=True,
            floor_score=score,
            unavailable=False,
        )
    except transport_errors() as error:
        # An unreachable cluster is the same outcome as an unconfigured one — no passages, so the
        # refusal — and it must arrive the same way. Letting it propagate killed `gctx ask` on a
        # traceback and failed the MCP tool call instead of answering it, which turns a degraded
        # retrieval path into a broken agent.
        #
        # Only the semantic arm is lost. A BOTH query still returns its exact hit, and the
        # deterministic path never touched the network to begin with.
        print(f"semantic path unavailable: {type(error).__name__}: {error}", file=sys.stderr)
        return SemanticResult(unavailable=True)


def _semantic_answer(
    citations: list[Citation], decision: Route, path: str = SEMANTIC
) -> AnswerEnvelope:
    """Grounded passages, best first. The caller writes prose; this supplies the ground.

    Takes citations rather than fetching them, so a caller that has already retrieved cannot
    retrieve a second time for the same query.
    """
    answer = citations[0]["snippet"] if citations else ""
    return grounded_answer(answer, citations, path, decision.as_dict())


def _merge(exact: AnswerEnvelope, extra: list[Citation], decision: Route) -> AnswerEnvelope:
    """router.md: query both, prefer an exact hit where one exists, never drop provenance.

    One exception, and it is the point of the whole design: when the router identified a
    precision question — a cross-entity comparison asks for exact values by construction — a
    deterministic miss is a *curation gap*, not an invitation to rank. Falling back to passages
    there is exactly the failure this project exists to prevent: a plausible, cited, adjacent
    answer to a question that had a right one. So it refuses instead.
    """
    if not exact["citations"]:
        if decision.precision:
            return exact
        return _semantic_answer(extra, decision)
    if not extra:
        return exact
    return grounded_answer(
        exact["answer"], exact["citations"] + extra, MIXED, decision.as_dict()
    )


def ask(
    bundle: KnowledgeStore,
    query: str,
    as_of: date,
    router: QueryRouter = route,
) -> AnswerEnvelope:
    """Route a natural-language question, then answer it on the path chosen.

    Records one event per answered question. The event is built from the finished envelope and
    emitted after it. The same query therefore returns the same answer whether the sink works,
    fails or is off.

    `router` names the classifier. The default is the heuristic in `router.py`. A caller that
    supplies another one changes which engine runs and changes nothing else: the envelope, the
    citations and the telemetry all read the decision, not the classifier that made it.
    """
    started = perf_counter()
    decision = router(query)

    if decision.route == ROUTE_SEMANTIC:
        semantic_started = perf_counter()
        result = semantic_citations(query)
        semantic_ms = _elapsed_ms(semantic_started)
        envelope = _semantic_answer(result.citations, decision)
        telemetry.record(
            query,
            envelope,
            total_ms=_elapsed_ms(started),
            semantic_ms=semantic_ms,
            relevance_floor_passed=result.floor_passed,
            relevance_score=result.floor_score,
            semantic_unavailable=result.unavailable,
        )
        return envelope

    deterministic_started = perf_counter()
    entity = find_entity(bundle, query)
    field = find_field(bundle, query, entity)
    if entity and field:
        exact = _lookup_envelope(bundle, entity, field, as_of, decision)
    elif field and is_rollup(query):
        # No single entity, a known field, and a phrasing that asks about a set. That is a
        # rollup, and the canonical layer can answer it exactly for every entity at once.
        exact = _rollup_envelope(bundle, field, as_of, decision)
    else:
        exact = grounded_answer("", [], DETERMINISTIC, decision.as_dict())
    deterministic_ms = _elapsed_ms(deterministic_started)

    if decision.route != ROUTE_BOTH:
        telemetry.record(
            query, exact, total_ms=_elapsed_ms(started), deterministic_ms=deterministic_ms
        )
        return exact

    semantic_started = perf_counter()
    result = semantic_citations(query)
    semantic_ms = _elapsed_ms(semantic_started)

    envelope = _merge(exact, result.citations, decision)
    telemetry.record(
        query,
        envelope,
        total_ms=_elapsed_ms(started),
        deterministic_ms=deterministic_ms,
        semantic_ms=semantic_ms,
        relevance_floor_passed=result.floor_passed,
        relevance_score=result.floor_score,
        semantic_unavailable=result.unavailable,
    )
    return envelope
