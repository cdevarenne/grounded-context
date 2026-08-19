# Building the telemetry dashboard in Kibana

[`kibana/telemetry-dashboard.json`](kibana/telemetry-dashboard.json) is the committed dashboard —
six panels over the projected telemetry index. This is how it was built, including the places
where the UI does not do what you would expect.

Written from an actual build session against **Elastic Cloud Serverless (Elasticsearch project
type)** on 2026-08-18 and 2026-08-19, not from memory. Menu labels move between versions; where this disagrees
with what you see, trust the screen.

## Before you start

```bash
uv run --extra es gctx telemetry index      # projects var/telemetry.ndjson into Elasticsearch
```

Nothing below works against an empty index. To reproduce the exact numbers in this document
without a live session of your own, project the committed fixture instead:

```bash
uv run --extra es gctx telemetry index --log tests/data/telemetry-sample.ndjson
```

That is 26 events: 8 `DETERMINISTIC`, 12 `SEMANTIC`, 4 `DIRECT`, 2 `BOTH`.

**After a schema change, rebuild rather than re-index.** A new field is not added to an existing
mapping by loading documents into it, so `--recreate` is what makes it queryable:

```bash
uv run --extra es gctx telemetry index --log tests/data/telemetry-sample.ndjson --recreate
```

The log is the source of truth and the index is a projection over it, so replaying the whole log
is the normal repair, not a last resort.

## 1. The data view

**Discover → data view selector → Create a data view.**

| | |
|---|---|
| Name | `telemetry` |
| Index pattern | `grounded-context-telemetry` |
| Timestamp field | `@timestamp` |

> **Gotcha — the selector is hidden in the default Discover view.** Switch Discover to **classic
> view** and the data view selector appears in the top left.

> **Gotcha — there is no Stack Management → Data Views in this project type.** The left nav offers
> **Data management → Index Management**, which is a different thing: "Reload indices" refreshes
> that table and has no effect on a data view. In practice new fields appeared in Lens without any
> refresh after the index was rebuilt, so check the field list before hunting for the menu.

> **Gotcha — the time picker will show you nothing.** The fixture's events are timestamped
> `2026-08-18T15:00–15:07Z`, so anything narrower than the age of your data returns zero hits and
> looks like a broken setup. Set **Last 24 hours**, or an absolute range covering the log.

**Check:** Discover shows **26 hits**.

## 2. Create the dashboard first

> **Gotcha — "Create visualization" from inside a dashboard saves the panel into *that*
> dashboard.** Building panels without an existing dashboard produces one dashboard per panel.

**Dashboards → Create dashboard**, save it empty as `Grounded context — telemetry`, and build
every panel from inside it. That title travels into the export.

If you already have stray single-panel dashboards: open each, panel context menu (**⋮**) →
**Copy to dashboard** → pick the target, then delete the empties. Prefer *Copy to dashboard* over
*Save to library* — it keeps panels by value, embedded in the dashboard, which is what makes the
export self-contained.

## 3. The six panels

Each maps to one signal in [`specs/observability.md`](specs/observability.md). Expected values are
over the committed fixture.

### Route mix — *what fraction of queries need the deterministic path*

Pie · Metric `Count of records` · Slice by **Top values** of `route` · **Number of values: 4**.

> **Gotcha — "Number of values" defaults to 3.** With four routes the smallest silently collapses
> into an "Other" slice. You get a plausible chart that hides `BOTH`.

> **Gotcha — Donut is not offered** as a Pie variant in this version.

| | |
|---|---|
| SEMANTIC | 46.15% (12) |
| DETERMINISTIC | 30.77% (8) |
| DIRECT | 15.38% (4) |
| BOTH | 7.69% (2) |

### Canonical hit / miss — *the curation backlog, measured*

This is the awkward one. `canonical_hit` is `true`, `false`, **or absent** — absent meaning the
deterministic path was never consulted. A terms aggregation shows only the 14 documents where the
field exists and silently drops the other 12.

> **Gotcha — "Include documents without the selected field" greys out** whenever the Fields list
> holds more than one entry, including an empty "Select a field" row. It is unavailable for
> multi-terms aggregations.

> **Gotcha — `not canonical_hit: *` in the KQL bar returned zero hits**, although the equivalent
> `must_not exists` query returns 12 through the API. Unexplained; avoid the negation.

What works: in the **Functions** row at the top of the Slice panel, switch from **Top values** to
**Filters**, then add three positive filters —

| KQL | Label | |
|---|---|---|
| `canonical_hit: true` | `hit` | 34.62% (9) |
| `canonical_hit: false` | `miss` | 19.23% (5) |
| `route: "SEMANTIC"` | `n/a — not consulted` | 46.15% (12) |

**Why `route: "SEMANTIC"` stands in for "absent":** `telemetry._canonical_hit()` returns `None`
exactly when the router chose `SEMANTIC`; every other route always yields a `true` or `false`. The
two sets are the same 12 documents, verified by id and not just by count. It is a coupling, so it
is worth knowing that if the code ever let a `DIRECT` or `BOTH` query record an absent
`canonical_hit`, this panel would under-count while an `exists` filter would stay correct.

**Say it with the right denominator.** The panel shows share of *all* traffic. The CLI's
`miss rate 35%` is share of *precision queries* — 5 of 14. Both are correct: *"12 of 26 queries
never asked for a canonical fact; of the 14 that did, 5 found nothing."*

### Refusal rate

Metric · Primary metric → **Formula**:

```
count(kql='refused: true') / count()
```

Value format **Percent, 2 decimals** → **26.92%**.

> **Gotcha — a formula metric displays the formula as its label.** Set the **Name** field to
> something readable (`refused / all queries`) or the panel looks unfinished.

The date-histogram trend the spec mentions is deliberately omitted: over seven minutes of fixture
data all four floor-blocked refusals land in a single minute, so a sparkline would show how the
sample was written rather than how the system behaves.

### Per-path latency (ms)

Table, no rows, six **Percentile** metrics, each **Number, 2 decimals** — otherwise the `float`
mapping renders `240.69999694824219`.

| | p50 | p95 |
|---|---|---|
| `latency_ms.deterministic` | 1.70 | 2.13 |
| `latency_ms.semantic` | 219.50 | 241.75 |
| `latency_ms.total` | 189.70 | 240.70 |

### BOTH cost

Metric · the visualization's own KQL bar set to `route: "BOTH"` · primary **Percentile 95** of
`latency_ms.total` · **secondary metric `Count of records`**, labelled `queries`.

**241.70** with **queries 2** beside it.

The secondary metric is not decoration. A p95 over two data points is arithmetic, not statistics,
and putting the sample size on the panel face stops the number reading as a benchmark — the same
discipline as the README's "the eval set is illustrative, **not** a benchmark".

### Closest refusal (floor 8.0)

Metric · the visualization's own KQL bar set to `relevance_floor_passed: false` · primary
**Maximum** of `relevance_score`, Number 1 decimal, labelled `closest blocked score` ·
**secondary metric `Count of records`**, labelled `refusals`.

**3.1** with **refusals 3** beside it.

This is the panel `relevance_score` exists for. The floor rejected three queries and the nearest
scored 3.1 against a threshold of 8.0 — none were near misses, so the corpus is not failing to
answer questions it almost could. A closest-refusal creeping toward 8 is what a curation gap
looks like, and it is the number that would change what you do next.

> **Gotcha — do not reach for a histogram here.** The obvious panel is a distribution of blocked
> scores, and at this sample size it is useless: three values spanning 1.7–3.1 render as three
> hairline spikes against a y-axis running 0 to 1. It shows that three things exist, not how they
> are distributed. The same small-n honesty that puts `queries 2` on the BOTH panel says take the
> maximum instead and show the count beside it.

## 4. Export

Dashboard → **Export → JSON**.

> **Gotcha — there is no `.ndjson` option, and no Saved Objects app** in this Serverless project
> type. `<kibana-url>/app/management/kibana/objects` is not reachable. The dashboard's own Export
> is the only route, and it produces a dashboard *definition* rather than a saved-objects bundle.

That format carries every panel's full configuration, which is what makes it diffable in git. What
it does **not** carry is the data view: each panel references it by id
(`data_source.ref_id`), and no definition is included. So the file is a faithful record of the
dashboard, not a one-click import — importing it onto another cluster needs a data view built as
in step 1 first. That is why this document exists rather than just the JSON.

## Kibana and the CLI disagree, twice, on purpose

`gctx telemetry summary` reads the local log; the dashboard reads the projection. Same events, two
deliberate differences. Both are worth stating before someone finds them.

**Percentiles.** Elasticsearch's `percentiles` aggregation uses TDigest — approximate and
interpolating, so it works across shards at scale. The CLI uses nearest-rank, because that is what
makes the JVM port reproduce the summary byte for byte.

| | Kibana | CLI |
|---|---|---|
| p95 deterministic | 2.13 | 2.2 |
| p50 semantic | 219.50 | 218.0 |
| p95 semantic | 241.75 | 245.0 |
| p50 total | 189.70 | 188.2 |
| p95 total | 240.70 | 242.2 |

**Percentages.** The CLI truncates (`46 + 30 + 15 + 7 = 98%`); Kibana shows the real values
(`46.15 + 30.77 + 15.38 + 7.69 = 99.99%`). Truncation is what keeps the CLI output portable; the
dashboard is where the missing 2% becomes visibly a rounding artifact rather than lost data.
