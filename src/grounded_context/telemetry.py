"""Telemetry about the layer's own decisions, per `docs/specs/observability.md`.

Telemetry here is an *observer*. The event is built from a finished answer envelope and never
recomputes it, the write is best-effort, and the sink is a local newline-delimited log that needs
no network. A layer whose pitch is determinism cannot let the instrument measuring it change what
it measures — so the log is the source of truth and the Elasticsearch index is a projection over
it, never the reverse.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provenance import DETERMINISTIC, NOT_FOUND
from .router import SEMANTIC as ROUTE_SEMANTIC

SCHEMA_VERSION = 1

TELEMETRY_INDEX = "grounded-context-telemetry"

DEFAULT_SINK = Path(__file__).resolve().parents[2] / "var" / "telemetry.ndjson"

#: A lookup that named its entity and field outright, so no router ran.
DIRECT = "DIRECT"
DIRECT_RATIONALE = "explicit entity+field lookup, no routing"

_DISABLED = {"0", "false", "no", "off"}


def sink_path(explicit: str | None = None) -> Path:
    """Resolve the log: explicit path, then `GCTX_TELEMETRY_SINK`, then the default.

    The default is anchored to the package, not the working directory. The MCP server is spawned
    by its client from wherever that client happens to sit, and a relative path would scatter the
    log across the filesystem.
    """
    return Path(explicit or os.environ.get("GCTX_TELEMETRY_SINK") or DEFAULT_SINK)


def is_enabled() -> bool:
    """Telemetry is on unless `GCTX_TELEMETRY` turns it off."""
    return os.environ.get("GCTX_TELEMETRY", "1").strip().lower() not in _DISABLED


def _canonical_hit(envelope: dict[str, Any]) -> bool | None:
    """Whether the deterministic path was consulted, and whether it held the fact.

    `None` is not `False`. A precision query the bundle could not answer and a query that never
    asked for a canonical field are different facts, and the curation-backlog number is only
    honest if they do not collapse into one another.
    """
    router = envelope.get("router")
    if router and router["route"] == ROUTE_SEMANTIC:
        return None
    return any(cite["path"] == DETERMINISTIC for cite in envelope["citations"])


def _ms(value: float | None) -> float | None:
    """One decimal place, matching the fixture the summary output is checked against."""
    return None if value is None else round(value, 1)


def event(
    query: str,
    envelope: dict[str, Any],
    *,
    total_ms: float,
    deterministic_ms: float | None = None,
    semantic_ms: float | None = None,
    relevance_floor_passed: bool | None = None,
) -> dict[str, Any]:
    """Build one event from a finished envelope.

    Every field that can be read off the envelope is read off it rather than recomputed, so the
    telemetry describes the answer that was actually returned and cannot disagree with it.
    """
    router = envelope.get("router")
    return {
        "@timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "schema_version": SCHEMA_VERSION,
        "query": query,
        "route": router["route"] if router else DIRECT,
        "rationale": router["rationale"] if router else DIRECT_RATIONALE,
        "retrieval_path": envelope["retrieval_path"],
        "canonical_hit": _canonical_hit(envelope),
        "relevance_floor_passed": relevance_floor_passed,
        "refused": envelope["answer"] == NOT_FOUND,
        "cites": len(envelope["citations"]),
        "latency_ms": {
            "deterministic": _ms(deterministic_ms),
            "semantic": _ms(semantic_ms),
            "total": _ms(total_ms),
        },
    }


def record(query: str, envelope: dict[str, Any], **fields: Any) -> None:
    """Build and emit one event — the single call an answer path makes, and it cannot raise.

    `fields` are the keyword arguments of :func:`event`. Building is inside the guard as well as
    writing, because an answer must survive a malformed event just as it survives a broken disk.
    """
    if not is_enabled():
        return
    try:
        emit(event(query, envelope, **fields))
    except Exception as error:  # noqa: BLE001
        print(f"telemetry: {type(error).__name__}: {error}", file=sys.stderr)


def emit(entry: dict[str, Any], sink: str | None = None) -> None:
    """Append one event to the log, best-effort.

    Every failure is swallowed to at most one line on stderr. An unavailable telemetry sink is a
    no-op, the same way an unavailable engine is a refusal rather than a crash: an answer that was
    going to return still returns. The broad `except` is the requirement, not an oversight.
    """
    if not is_enabled():
        return
    try:
        path = sink_path(sink)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception as error:  # noqa: BLE001
        print(f"telemetry: {type(error).__name__}: {error}", file=sys.stderr)
