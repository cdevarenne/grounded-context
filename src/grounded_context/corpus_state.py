"""The corpus-state snapshot: the two observability signals that are not per-query events.

`telemetry.py` observes a query being answered. This observes the `knowledge/` bundle itself —
how much of it has gone stale, and how much is human-reviewed rather than taken on trust. A time
series of these is what turns "is governance keeping up?" into a line on a chart instead of an
opinion.

Same source-of-truth-plus-projection shape as the per-query slice, different cadence: a query
event fires thousands of times a day, and governance drifts slowly, so this is sampled on demand
rather than emitted. See `docs/specs/observability-corpus-state.md`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .bundle import HUMAN_REVIEWED, MACHINE_CONFIRMED, UNVERIFIED
from .store import KnowledgeStore

SCHEMA_VERSION = 1

CORPUS_STATE_INDEX = "grounded-context-corpus-state"

DEFAULT_SINK = Path(__file__).resolve().parents[2] / "var" / "corpus-state.ndjson"

#: The stale list is bounded because the instrument built to answer "does curation scale?" must
#: not itself scale with the corpus. The ids are there to keep the count inspectable, not to
#: mirror the bundle — and the list is short precisely when governance is healthy.
STALE_IDS_CAP = 50

#: Elasticsearch field names for the tiers. The bundle's own values are hyphenated; a mapping
#: property cannot be, so the two differ on purpose and this is the one place that knows it.
_TIER_FIELDS = {
    UNVERIFIED: "unverified",
    MACHINE_CONFIRMED: "machine_confirmed",
    HUMAN_REVIEWED: "human_reviewed",
}

_STATUSES = ("draft", "stable", "deprecated")


def sink_path(explicit: str | None = None) -> Path:
    """Resolve the log: explicit path, then `GCTX_CORPUS_STATE_SINK`, then the default."""
    return Path(explicit or os.environ.get("GCTX_CORPUS_STATE_SINK") or DEFAULT_SINK)


def snapshot(bundle: KnowledgeStore, as_of: date) -> dict[str, Any]:
    """One document describing the bundle's governance state on `as_of`.

    Staleness and the trust tier are read off `Concept`, which is the same derivation the
    deterministic lookup uses. The snapshot must never grow a second opinion about whether a
    concept is stale: if it disagreed with `lookup()` about one, one of them would be a bug, and
    `tests/test_corpus_state.py` pins that they agree.

    `as_of` is honoured rather than assumed to be today, so the scan answers "how stale will this
    be on 2026-10-01?" — which is how a governance cliff is seen before it arrives rather than
    after.
    """
    concepts = list(bundle)
    stale = sorted(concept.id for concept in concepts if concept.is_stale(as_of))
    tiers = dict.fromkeys(_TIER_FIELDS.values(), 0)
    statuses = dict.fromkeys(_STATUSES, 0)
    for concept in concepts:
        tiers[_TIER_FIELDS[concept.trust_tier]] += 1
        if concept.status in statuses:
            statuses[concept.status] += 1
    return {
        "@timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "concepts_total": len(concepts),
        "stale": {"count": len(stale), "concept_ids": stale[:STALE_IDS_CAP]},
        "trust_tier": tiers,
        "status": statuses,
    }


def render(document: dict[str, Any]) -> str:
    """The snapshot as a person reads it. No cloud, no credentials, always available."""
    stale = document["stale"]
    total = document["concepts_total"]
    lines = [
        f"corpus state — {total} concepts as of {document['as_of']}",
        "",
        f"stale        {stale['count']} of {total}"
        + (f"   {', '.join(stale['concept_ids'])}" if stale["concept_ids"] else ""),
        "trust tier   "
        + "   ".join(f"{name.replace('_', '-')} {count}"
                     for name, count in document["trust_tier"].items()),
        "status       "
        + "   ".join(f"{name} {count}" for name, count in document["status"].items()),
    ]
    return "\n".join(lines) + "\n"


def emit(document: dict[str, Any], sink: str | None = None) -> None:
    """Append one line to the local time series. Best-effort, like the per-query sink.

    A snapshot that cannot be written must not fail the command that produced it: the printed
    output is the deliverable and the log is the durable copy, in that order.
    """
    try:
        path = sink_path(sink)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(document) + "\n")
    except OSError as error:
        print(f"corpus-state sink unavailable: {type(error).__name__}: {error}", file=sys.stderr)


#: Data-stream-ready for the same reason as the telemetry index: `@timestamp` is present and no
#: custom `_id` is set, so the mapping is promotable without a rewrite.
MAPPING: dict[str, Any] = {
    "properties": {
        "@timestamp": {"type": "date"},
        "schema_version": {"type": "integer"},
        "as_of": {"type": "date"},
        "concepts_total": {"type": "integer"},
        "stale": {
            "properties": {
                "count": {"type": "integer"},
                "concept_ids": {"type": "keyword"},
            }
        },
        "trust_tier": {
            "properties": {name: {"type": "integer"} for name in _TIER_FIELDS.values()}
        },
        "status": {"properties": {name: {"type": "integer"} for name in _STATUSES}},
    }
}


def read_log(path: Path) -> list[dict[str, Any]]:
    """Every snapshot in the log, oldest first. A missing log is empty, not an error."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def project(
    path: Path,
    index: str = CORPUS_STATE_INDEX,
    recreate: bool = False,
    es: Any = None,
) -> int:
    """Bulk-load every snapshot in the log into `index`, returning how many landed.

    Rebuildable by construction, exactly like the per-query projection: the log on disk is the
    durable record and the index is a view over it, never the reverse.
    """
    snapshots = read_log(path)
    if not snapshots:
        return 0

    from elasticsearch.helpers import bulk

    from .es_client import client

    es = es or client()
    if recreate and es.indices.exists(index=index):
        es.indices.delete(index=index)
    if not es.indices.exists(index=index):
        es.indices.create(index=index, mappings=MAPPING)

    actions = ({"_index": index, "_source": document} for document in snapshots)
    succeeded, errors = bulk(es.options(request_timeout=300), actions)
    es.indices.refresh(index=index)
    if errors:
        raise RuntimeError(f"{len(errors)} snapshots failed to index")
    return succeeded


def run(
    bundle: KnowledgeStore,
    as_of: date,
    log: str | None = None,
    index: str | None = None,
    recreate: bool = False,
) -> int:
    """Take a snapshot, print it, record it, and project it only when asked and able.

    The scan itself needs no cluster and no credentials — that is the point of the two signals
    living here rather than in an aggregation over the telemetry index. Projection is opt-in and
    best-effort on top.
    """
    document = snapshot(bundle, as_of)
    print(render(document), end="")
    emit(document, log)

    if index is None:
        return 0

    from .es_client import is_configured

    if not is_configured():
        print("\nnote: no Elasticsearch configured — the snapshot above was still recorded")
        return 0

    path = sink_path(log)
    projected = project(path, index=index, recreate=recreate)
    print(f"\nprojected {projected} snapshots from {path} into {index}")
    return 0
