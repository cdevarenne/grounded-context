"""Project the telemetry log into Elasticsearch, per `docs/specs/observability.md`.

The local log is the source of truth and this builds the queryable view over it — the same
relationship the Markdown bundle has to the corpus index, and never the reverse. Nothing here runs
on the answer path: recording an answer appends a line, and projecting those lines is a separate,
optional step that a missing cluster turns into a message rather than a failure.

Kept out of `telemetry.py` so that writing the log stays a stdlib-only operation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .es_client import client, is_configured
from .telemetry import TELEMETRY_INDEX, read_log

#: Written data-stream-ready — a `@timestamp` field and no custom `_id` — so it drops into a
#: data-stream index template unchanged. A data stream is the production-correct shape for
#: append-only time-series telemetry; this slice stays a plain index on purpose, with no template
#: and no ILM. Say that in the room rather than building it.
MAPPING: dict[str, Any] = {
    "properties": {
        "@timestamp": {"type": "date"},
        "schema_version": {"type": "integer"},
        "query": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
        },
        "route": {"type": "keyword"},
        "rationale": {"type": "text"},
        "retrieval_path": {"type": "keyword"},
        "canonical_hit": {"type": "boolean"},
        "relevance_floor_passed": {"type": "boolean"},
        "refused": {"type": "boolean"},
        "cites": {"type": "integer"},
        "latency_ms": {
            "properties": {
                "deterministic": {"type": "float"},
                "semantic": {"type": "float"},
                "total": {"type": "float"},
            }
        },
    }
}

UNAVAILABLE = (
    "telemetry projection unavailable: no ES_URL / ES_API_KEY, or the `es` extra is not installed."
    "\nThe log is still the source of truth — `gctx telemetry summary` reads it with no cluster."
)


def project(
    path: Path,
    index: str = TELEMETRY_INDEX,
    recreate: bool = False,
    es: Any = None,
) -> int:
    """Bulk-load every event in the log into `index`, returning how many landed.

    Rebuildable by construction: the log is replayed in full, so `--recreate` restores the index
    from its source rather than from a backup of itself.
    """
    events = read_log(path)
    if not events:
        return 0

    # Imported here, not at the top: with nothing to project there is no reason to need the
    # `es` extra at all.
    from elasticsearch.helpers import bulk

    es = es or client()
    if recreate and es.indices.exists(index=index):
        es.indices.delete(index=index)
    if not es.indices.exists(index=index):
        es.indices.create(index=index, mappings=MAPPING)

    # No `_id`: the events are append-only observations, and letting Elasticsearch assign ids is
    # what keeps the mapping promotable to a data stream.
    actions = ({"_index": index, "_source": event} for event in events)
    succeeded, errors = bulk(es.options(request_timeout=300), actions)
    es.indices.refresh(index=index)
    if errors:
        raise RuntimeError(f"{len(errors)} events failed to index")
    return succeeded


def run(path: Path, index: str = TELEMETRY_INDEX, recreate: bool = False) -> int:
    """The CLI's entry point. An absent cluster is a message and a clean exit, never an error."""
    if not is_configured():
        print(UNAVAILABLE)
        return 0

    indexed = project(path, index=index, recreate=recreate)
    if not indexed:
        print(f"nothing to project — {path} holds no events")
        return 0

    print(f"projected {indexed} events from {path} into {index}")
    return 0
