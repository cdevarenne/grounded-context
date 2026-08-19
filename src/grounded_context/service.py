"""Answer construction shared by every consumer of the deterministic path.

The CLI and the MCP server must produce identical envelopes. The citation contract is the
product here, so it lives in one place rather than being re-implemented per surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from . import telemetry
from .bundle import Bundle
from .lookup import find_entity, find_field, resolve
from .provenance import DETERMINISTIC, MIXED, SEMANTIC, citation, grounded_answer
from .router import BOTH as ROUTE_BOTH
from .router import SEMANTIC as ROUTE_SEMANTIC
from .router import Route, route

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
        return datetime.now(timezone.utc).date()
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
    bundle: Bundle,
    entity_id: str,
    field: str,
    as_of: date,
    decision: Route | None = None,
) -> dict[str, Any]:
    """Envelope for one exact field, or the refusal when the bundle doesn't hold it."""
    result = resolve(bundle, entity_id, field)
    router = decision.as_dict() if decision else None
    if result is None:
        return grounded_answer("", [], DETERMINISTIC, router)
    return grounded_answer(
        format_value(result.value), [citation(result, as_of)], DETERMINISTIC, router
    )


def lookup_field(
    bundle: Bundle,
    entity_id: str,
    field: str,
    as_of: date,
    decision: Route | None = None,
) -> dict[str, Any]:
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

    citations: list[dict[str, Any]] = dataclass_field(default_factory=list)
    #: `True` cleared, `False` blocked, `None` the probe never ran.
    floor_passed: bool | None = None
    #: The pre-fusion score behind that verdict, so a near miss is distinguishable from a
    #: query that was never in domain. `None` whenever `floor_passed` is.
    floor_score: float | None = None


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

    from .es_client import client
    from .semantic import probe, search

    es = client()
    cleared, score = probe(query, es=es)
    if not cleared:
        return SemanticResult(floor_passed=False, floor_score=score)
    return SemanticResult(
        search(query, size=size, es=es, floor=None), floor_passed=True, floor_score=score
    )


def _semantic_answer(
    citations: list[dict[str, Any]], decision: Route, path: str = SEMANTIC
) -> dict[str, Any]:
    """Grounded passages, best first. The caller writes prose; this supplies the ground.

    Takes citations rather than fetching them, so a caller that has already retrieved cannot
    retrieve a second time for the same query.
    """
    answer = citations[0]["snippet"] if citations else ""
    return grounded_answer(answer, citations, path, decision.as_dict())


def _merge(exact: dict[str, Any], extra: list[dict[str, Any]], decision: Route) -> dict[str, Any]:
    """router.md: query both, prefer an exact hit where one exists, never drop provenance."""
    if not exact["citations"]:
        return _semantic_answer(extra, decision)
    if not extra:
        return exact
    return grounded_answer(
        exact["answer"], exact["citations"] + extra, MIXED, decision.as_dict()
    )


def ask(bundle: Bundle, query: str, as_of: date) -> dict[str, Any]:
    """Route a natural-language question, then answer it on the path chosen.

    Records one event per answered question, built from the finished envelope and emitted after
    it — so the same query returns the same answer whether the sink works, fails, or is off.
    """
    started = perf_counter()
    decision = route(query)

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
        )
        return envelope

    deterministic_started = perf_counter()
    entity = find_entity(bundle, query)
    field = find_field(bundle, query, entity)
    exact = (
        _lookup_envelope(bundle, entity, field, as_of, decision)
        if entity and field
        else grounded_answer("", [], DETERMINISTIC, decision.as_dict())
    )
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
    )
    return envelope
