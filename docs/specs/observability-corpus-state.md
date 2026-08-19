# Spec: Observability — corpus-state snapshot

The two signals [design.md](../design.md) lists that are **not** per-query events. Where
[observability.md](observability.md) observes a query being answered, this observes the
`knowledge/` bundle itself: how much of it has gone stale, and how much is human-reviewed versus
taken on trust. A time series of these snapshots is what turns "is governance keeping up?" into a
line on a chart.

Companion to [observability.md](observability.md); same source-of-truth-plus-projection shape,
different cadence and a different index.

## What this measures

| # | Signal | From |
|---|---|---|
| 3 | Concepts past `stale_after` | `today >= stale_after` over every concept in `knowledge/` |
| 4 | Trust-tier distribution | the OKF-derived tier of each concept: `unverified` · `machine-confirmed` · `human-reviewed` |
| — | Status distribution *(bonus, free)* | OKF lifecycle: `draft` · `stable` · `deprecated` |

## Shape: a snapshot, not an event

A per-query event fires on every answer (thousands a day). A corpus-state snapshot is a scan over
the bundle, run **on demand or at index time** — governance drifts slowly, so it is sampled
slowly. Because the shape and cadence differ, it lands in its own index rather than being unioned
into the per-query telemetry mapping.

## Rule (non-negotiable)

1. **Reuse the deterministic path's OKF derivation.** Staleness (`is_stale(as_of)`) and the trust
   tier come from the same code the deterministic lookup uses (ELX-03). The snapshot must not
   grow a second, divergent implementation of "is this stale" or "what tier is this" — if the
   snapshot ever disagreed with `lookup()` about a concept, one of them is a bug, and a test pins
   that they agree.
2. **Zero-cloud.** The scan reads `knowledge/` Markdown; no network. It prints locally with no
   credentials, and only *projects* to Elasticsearch best-effort when configured.
3. **Honor `--as-of`.** Same time-travel the deterministic path already supports, so the snapshot
   can answer "how stale will the bundle be on 2026-10-01?" — which is how you see a governance
   cliff *before* you fall off it.
4. **Scale-aware.** The snapshot stores aggregates plus the **bounded** list of stale concept ids
   — not a per-concept dump of the whole bundle. The instrument built to answer "does curation
   scale?" must not itself scale linearly with the corpus; at ten thousand concepts the snapshot
   document is still small, and the stale list is small precisely when governance is healthy.

## The snapshot document

One document per snapshot run:

```json
{
  "@timestamp": "2026-08-18T15:10:02.554Z",
  "schema_version": 1,
  "as_of": "2026-08-18",
  "concepts_total": 4,
  "stale":      { "count": 0, "concept_ids": [] },
  "trust_tier": { "unverified": 0, "machine_confirmed": 0, "human_reviewed": 4 },
  "status":     { "draft": 0, "stable": 4, "deprecated": 0 }
}
```

| Field | Meaning |
|---|---|
| `@timestamp` | when the snapshot was taken (the time-series axis) |
| `as_of` | the date the staleness comparison used — equals today unless `--as-of` was passed |
| `concepts_total` | concepts loaded from `knowledge/` |
| `stale.count` / `stale.concept_ids` | how many are past `stale_after` as of `as_of`, and which — **signal 3**; the ids keep the number inspectable |
| `trust_tier.*` | counts per OKF-derived tier — **signal 4** |
| `status.*` | counts per OKF lifecycle value |

## Sinks

Same split as the per-query slice: append one line to a local log
(`var/corpus-state.ndjson`, gitignored) as the durable time series; project it into
`grounded-context-corpus-state` with `gctx telemetry snapshot --index`, reusing `es_client.py`.
The scan runs and prints regardless; the projection is best-effort and optional.

## The index

```json
{
  "mappings": {
    "properties": {
      "@timestamp":      { "type": "date" },
      "schema_version":  { "type": "integer" },
      "as_of":           { "type": "date" },
      "concepts_total":  { "type": "integer" },
      "stale": {
        "properties": {
          "count":       { "type": "integer" },
          "concept_ids": { "type": "keyword" }
        }
      },
      "trust_tier": {
        "properties": {
          "unverified":       { "type": "integer" },
          "machine_confirmed":{ "type": "integer" },
          "human_reviewed":   { "type": "integer" }
        }
      },
      "status": {
        "properties": {
          "draft":      { "type": "integer" },
          "stable":     { "type": "integer" },
          "deprecated": { "type": "integer" }
        }
      }
    }
  }
}
```

Data-stream-ready for the same reason as the telemetry index: `@timestamp` present, no custom
`_id`.

## Readback

**Local, always works** — prints the current snapshot with no cloud:

```
gctx telemetry snapshot                 # prints the snapshot for today
gctx telemetry snapshot --as-of 2026-10-01   # time-travel: what the bundle looks like then
```

**Kibana** — two time-series panels over the projection:

| Panel | Aggregation | Answers |
|---|---|---|
| Stale concepts over time | `max` of `stale.count` per `date_histogram` | signal 3 — is the freshness backlog growing? |
| Trust-tier mix over time | `max` of each `trust_tier.*` per `date_histogram` | signal 4 — is the corpus getting more or less human-reviewed as it grows? |

Commit the panels to `docs/kibana/corpus-state-dashboard.ndjson`.

## Verification

| Test | Asserts |
|---|---|
| `test_snapshot_counts` | over the committed `knowledge/` bundle → `concepts_total` 4, all `human_reviewed`, `stale.count` 0 (golden) |
| `test_snapshot_as_of_time_travel` | `--as-of 2026-10-01` → `stale.count` 4 and `concept_ids` lists all four (staleness flips on the date, verified by breaking it) |
| `test_snapshot_tier_matches_lookup` | for every concept, the snapshot's tier equals `lookup()`'s derived tier — no divergent second derivation |
| `test_snapshot_zero_cloud` | runs and prints with no ES configured; `es_client` is never called |
| `test_snapshot_reproducible` | `gctx telemetry snapshot` over the committed bundle prints the captured golden aggregates |

The `--as-of` test is the one that matters most: it proves the snapshot reads staleness the same
way the citation block does, so the governance dashboard and the answer footer can never tell a
user two different stories about the same concept.

## Constants

| Constant | Value |
|---|---|
| `SCHEMA_VERSION` | 1 |
| `CORPUS_STATE_SINK` | `var/corpus-state.ndjson` (gitignored) |
| `CORPUS_STATE_INDEX` | `grounded-context-corpus-state` |

## Scope

- **Aggregates + stale ids only** — no full per-concept array embedded, on purpose (see rule 4).
- **Manual / at-index-time cadence** — governance is a slow signal; there is no daemon and no
  schedule in this slice. A cron or a CI step that runs `gctx telemetry snapshot --index` on a
  cadence is the obvious production follow-up, and is named rather than built.

## JVM parity

Same contract, same discipline as [observability.md](observability.md): the snapshot **document
schema** is the parity surface; the Python reference (ELX-25) leads and the JVM ports it, with a
drift check against this schema. The tier and staleness derivations already have parity coverage
via the bundle parity test (JVM-07), so the snapshot inherits most of its correctness guarantee
from work that is already done.
