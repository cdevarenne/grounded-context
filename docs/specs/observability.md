# Spec: Observability — the context layer, observed

The retrieval layer emits telemetry about its own decisions so the project's open question —
**does curation scale?** ([design.md](../design.md)) — can be answered from data instead of
opinion. This spec defines the slice that ships: the four per-query signals, captured by one
event at one site, with a local event log as the source of truth and Elasticsearch as a
projection over it.

## What this measures

design.md names six signals. They are two different shapes, and only one shape is per-query:

| # | Signal | Shape | In this slice |
|---|---|---|---|
| 1 | Router decision + rationale | per-query event | **yes** |
| 2 | Canonical hit / miss rate | per-query event | **yes** |
| 5 | Refusal rate | per-query event | **yes** |
| 6 | Per-path latency | per-query event | **yes** |
| 3 | Concepts past `stale_after` | corpus-state snapshot | no — deferred |
| 4 | Trust-tier distribution | corpus-state snapshot | no — deferred |

Signals 1, 2, 5 and 6 are all observations *of a single query being answered*, so all four fall
out of **one event emitted once per answer** — no extra instrumentation site per signal.
Signals 3 and 4 are not about a query at all; they are a scan over `knowledge/` describing the
bundle's governance state at a moment. That is a different job with a different cadence, so it is
a separate deliverable and not padded into this one.

## Rule (non-negotiable)

Telemetry is an *observer*. Three guarantees, each with a test that breaks them (see
[Verification](#verification)):

1. **It never changes an answer.** The event is built and emitted *after* the answer envelope is
   final. The same query returns the same answer, byte-for-byte, whether the sink is on, off, or
   failing. This is the `Repeatable` property; it is not negotiable for a layer whose pitch is
   determinism.
2. **It never blocks or fails an answer.** The emit is best-effort and fully wrapped: any error —
   a broken sink, an unreachable cluster, a full disk — is swallowed to at most one line on
   stderr, never propagated. An answer that was going to return still returns. An unavailable
   telemetry sink is a no-op, the same way an unavailable engine is a refusal rather than a crash.
3. **The deterministic path stays cloud-free.** With no `ES_URL`, the event still lands in the
   local log. The zero-network guarantee on the deterministic path survives untouched, and the
   instrument still runs in the offline demo.

## Source of truth, and projection

The telemetry design rhymes with the corpus design on purpose.

| | corpus | telemetry |
|---|---|---|
| Source of truth | `knowledge/` Markdown | the local event log (`var/telemetry.ndjson`) |
| Projection | the `grounded-context-corpus` index | the `grounded-context-telemetry` index |
| Rebuildable? | index rebuilds from Markdown | index rebuilds from the log |

Every answered query appends **one line** of newline-delimited JSON to a gitignored local log.
That append is the durable record and the only thing on the answer path. Indexing that log into
Elasticsearch is a separate, optional step that produces the queryable/visualizable projection —
and, like the corpus index, it is rebuildable from its source and never the reverse.

This keeps the answer path free of any new cloud dependency or failure mode, and it means the
"does curation scale?" numbers exist even in a fully offline run.

## The telemetry event

One document per answered query, emitted from `service.py` so the CLI and the MCP server are
instrumented once rather than once each.

`service.py` has **two** public entry points, not one. `ask()` routes a natural-language question.
`lookup_field()` is called directly by `gctx lookup` and by the MCP `lookup_canonical_fact` tool,
which name an entity and a field outright and therefore never consult the router. Both emit, and
`ask()` reaches `lookup_field()` through a private helper so a routed deterministic answer
produces one event rather than two.

An un-routed lookup carries `route: "DIRECT"`. Dropping those events instead would bias the
signal the slice exists to measure: a direct lookup is a precision query by construction, so it is
the purest input to `canonical_hit`, and a curation-backlog number computed without them would be
filtered without saying so.

```json
{
  "@timestamp": "2026-08-18T15:04:23.117Z",
  "schema_version": 2,
  "query": "What does the rank_constant parameter do?",
  "route": "BOTH",
  "rationale": "no exact entity+field; 'what does' -> SEMANTIC signals; ambiguous -> BOTH",
  "retrieval_path": "semantic",
  "canonical_hit": null,
  "relevance_floor_passed": true,
  "relevance_score": 16.8,
  "refused": false,
  "cites": 5,
  "latency_ms": { "deterministic": 1.8, "semantic": 214.6, "total": 216.9 }
}
```

| Field | Source | Meaning |
|---|---|---|
| `@timestamp` | emit time, UTC, ms | the Kibana time field |
| `schema_version` | constant | bump on any field change; summary and indexer assert on it |
| `query` | the raw query | logged verbatim — see [scope](#scope) on why that is acceptable here and not everywhere |
| `route` | router (`router.md`), or `DIRECT` | `DETERMINISTIC \| SEMANTIC \| BOTH \| DIRECT` — **signal 1** |
| `rationale` | router | the decision's reason, already produced; this persists it into the audit trail. Fixed text on `DIRECT`, where no decision was made |
| `retrieval_path` | answer envelope (`provenance.md`) | `deterministic \| semantic \| mixed` — the path actually taken; differs from `route` when `BOTH` resolves to an exact hit |
| `canonical_hit` | deterministic lookup | `true` = returned a value; `false` = consulted, `NOT_FOUND`; **absent** = deterministic path not consulted — **signal 2, the curation backlog** |
| `relevance_floor_passed` | semantic probe | `true/false` when the pre-fusion probe ran against `RELEVANCE_FLOOR`; absent when semantic not consulted — ties the refusal to [findings.md](../findings.md) §3 |
| `relevance_score` | semantic probe | the score behind that verdict, absent whenever the verdict is. A query at 7.9 against a floor of 8.0 is a **curation gap**; one at 1.7 is off topic. Both refuse, and the boolean alone cannot tell them apart afterwards |
| `refused` | answer envelope | `true` when the answer is "Not found in the grounded sources." — **signal 5** |
| `cites` | answer envelope | citation count on the answer |
| `latency_ms` | timers in `service.py` | wall-clock per path; a path not taken is `null`; `total` on `route: BOTH` is the cost of running both — **signal 6** |

`canonical_hit` being *absent* rather than `false` on a pure-semantic query is load-bearing: a
missing canonical field (a precision query the bundle could not answer) and a query that never
asked for one are different facts, and the curation-backlog number is only honest if they do not
collapse. Elasticsearch stores a null boolean as absent, so an `exists` query separates the three
states cleanly.

## Emit site and timing

In `service.py`, around each of the two public entry points:

1. start a total timer;
2. call the router, record `route` + `rationale` — skipped on `lookup_field()`, which is `DIRECT`;
3. for each path the route runs, wrap the call in a per-path timer;
4. build the answer envelope exactly as today (unchanged);
5. **after** the envelope is final, assemble the event from it and hand it to the sink,
   best-effort.

`canonical_hit`, `retrieval_path`, `refused`, and `cites` are read off the finished envelope, not
recomputed — the telemetry must describe the answer that was actually returned, not a second
evaluation that could disagree with it.

## Sinks

**Local log (primary).** Append one JSON line to `var/telemetry.ndjson` (gitignored). Pure
stdlib, no network. This is the source of truth and the only sink on the answer path.

**Elasticsearch index (projection).** A separate command reads the log and bulk-loads it:

```
gctx telemetry index [--recreate]      # projects var/telemetry.ndjson -> grounded-context-telemetry
```

It reuses `es_client.py` — same `is_configured()` gate, same never-log-the-key discipline. With
no credentials it prints that the projection is unavailable and exits cleanly; it never touches
the answer path.

## The telemetry index

Name: `grounded-context-telemetry`. Mapping is written **data-stream-ready** — a `@timestamp`
field and no custom `_id` — so it drops into a data-stream index template unchanged:

```json
{
  "mappings": {
    "properties": {
      "@timestamp":             { "type": "date" },
      "schema_version":         { "type": "integer" },
      "query":                  { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 512 } } },
      "route":                  { "type": "keyword" },
      "rationale":              { "type": "text" },
      "retrieval_path":         { "type": "keyword" },
      "canonical_hit":          { "type": "boolean" },
      "relevance_floor_passed": { "type": "boolean" },
      "refused":                { "type": "boolean" },
      "cites":                  { "type": "integer" },
      "latency_ms": {
        "properties": {
          "deterministic":      { "type": "float" },
          "semantic":           { "type": "float" },
          "total":              { "type": "float" }
        }
      }
    }
  }
}
```

A **data stream** is the production-correct shape for append-only time-series telemetry. This
slice uses a plain index to stay minimal — no index template, no ILM — and the mapping above is
deliberately compatible with promoting it to one later. Say that in the room; do not build the ILM.

## Readback

Two readbacks, because the demo must survive a cluster that is down.

**Local, always works.** Reads the log directly, no cloud:

```
gctx telemetry summary
```

Shape (illustrative — the real numbers come from the committed sample fixture, the way
[eval-output.md](../eval-output.md) captures real runs, not hand-written figures):

```
events: N   window: <first> .. <last>
route mix        DETERMINISTIC .. / SEMANTIC .. / BOTH ..
canonical        hit .. / miss .. / n-a ..     miss rate ..% of precision queries
refusals         ..
floor            semantic cleared .. / blocked ..
latency ms p50   deterministic .. / semantic .. / total ..
latency ms p95   deterministic .. / semantic .. / BOTH-total ..
```

**Kibana dashboard (the live flourish).** Over the projection index. Five panels, each mapping to
a signal; each is a single aggregation:

| Panel | Aggregation | Answers |
|---|---|---|
| Route mix | `terms` on `route` | signal 1 — what fraction of real queries need the deterministic path |
| Canonical hit / miss | `terms` on `canonical_hit` (+ `exists` for n/a) | signal 2 — the curation backlog, measured |
| Refusal rate | count `refused:true` / total, plus a `date_histogram` trend | signal 5 |
| Blocked score distribution | `histogram` on `relevance_score` filtered to `relevance_floor_passed:false` | which refusals were near misses worth curating, and which were never in domain |
| Per-path latency | `percentiles` (p50/p95) on the three `latency_ms.*` | signal 6 |
| BOTH cost | `percentiles` on `latency_ms.total` filtered to `route:BOTH` | signal 6 — what routing to BOTH actually costs |

Export the dashboard to [`../kibana/telemetry-dashboard.json`](../kibana/telemetry-dashboard.json)
and commit it, so the panels are a checked-in artifact rather than a screenshot.

The format is a dashboard *definition*, not a saved-objects bundle: this Serverless project offers
no `.ndjson` export and no Saved Objects app. It carries every panel's configuration, which is what
makes it diffable, but it references the data view by id without including it — so the file records
the dashboard rather than importing it. [`../kibana-setup.md`](../kibana-setup.md) is what makes it
reproducible, and records the UI traps found building it.

## Verification

Each non-negotiable has a test that fails when the guarantee is broken — the repo's
verified-by-breaking-it standard.

| Test | Asserts |
|---|---|
| `test_telemetry_does_not_change_answer` | same query, sink on vs a null sink → identical answer envelope, key-for-key |
| `test_answer_survives_sink_failure` | a sink whose write raises → envelope unchanged, nothing propagates, ≤1 stderr line |
| `test_one_event_per_answered_query` | N answers → N events, each with required fields present and typed; `schema_version` matches |
| `test_a_routed_deterministic_answer_emits_once` | `ask()` on a `DETERMINISTIC` route → exactly one event, not one per public function it passed through |
| `test_canonical_hit_semantics` | deterministic hit → `true`; deterministic `NOT_FOUND` → `false`; pure-semantic route → field absent |
| `test_the_score_behind_the_floor_verdict_is_recorded` | the probe's score reaches the event; absent when the probe never ran |
| `test_direct_lookup_is_not_routed` | `lookup_field()` → `route: "DIRECT"`, fixed rationale, `canonical_hit` present either way |
| `test_zero_cloud_still_emits` | ES unconfigured → event lands in the local log **and** `es_client` was never called |
| `test_summary_reproducible` | `gctx telemetry summary` over `tests/data/telemetry-sample.ndjson` prints the captured aggregates (golden output, like the eval) |
| `test_index_projection_roundtrip` | *(gated on ES, skips without creds)* sample log → index → read back → same field values |

The sample fixture makes the summary and dashboard numbers checkable without the cluster, exactly
as `eval-output.md` makes the findings checkable without it.

## Constants

| Constant | Value |
|---|---|
| `SCHEMA_VERSION` | 2 |
| `TELEMETRY_SINK` | `var/telemetry.ndjson` (gitignored) |
| `TELEMETRY_INDEX` | `grounded-context-telemetry` |
| `RELEVANCE_FLOOR` (referenced, not owned) | 8.0 — see [index-spec.md](../index-spec.md) |

## Scope

- **Two of six signals deferred.** Concepts past `stale_after` and trust-tier distribution are
  corpus-state snapshots — a scan over `knowledge/`, not a per-query event. Separate deliverable.
- **Single-writer.** The append-only log assumes the single-user prototype; concurrent MCP writers
  could interleave lines. Consistent with the existing single-user, single-index scope; a real
  deployment writes through a proper ingest path, not a shared file.
- **Query text is logged verbatim.** Acceptable here because the corpus is public and the user is
  one person. It is called out, not solved: a production deployment must weigh query sensitivity
  and PII before persisting query text, and the schema is arranged so `query` can be dropped or
  hashed without touching any other field.
- **Not a benchmark.** Latency is wall-clock on one machine against one Serverless cluster —
  indicative, never a performance claim. Same disclaimer the eval carries.

## JVM parity

The **event schema is the contract.** The Python repo is the reference (ELX-24); the JVM repo
ports it for parity (JVM-17), the way the bundle and index-spec are ported and drift-guarded. On
the JVM the idiomatic emitter is Micrometer / Spring Actuator into Elasticsearch — a stronger
enterprise-observability story, and worth naming in the room before it is built — but the
transport may differ only *below* the document: the emitted event must match this schema
field-for-field, enforced by a drift check like `IndexSpecParityTest`.
