# Telemetry summary fixture

`telemetry-sample.ndjson` is a hand-built but realistic session — the 12-question eval run, ten
ad-hoc/demo queries, and four direct canonical lookups — used as the golden input for
`test_summary_reproducible`. `telemetry-summary.golden.txt` is exactly what
`gctx telemetry summary` must print over it.

Suggested repo home: `tests/data/telemetry-sample.ndjson` and
`tests/data/telemetry-summary.golden.txt`.

## What the sample deliberately exercises

- **All four routes** — 8 DETERMINISTIC, 12 SEMANTIC, 2 BOTH, 4 DIRECT.
- **`DIRECT`, the un-routed path.** `gctx lookup <entity> <field>` and the MCP
  `lookup_canonical_fact` tool call `lookup_field()` without consulting the router, so there is no
  routing decision to record. Those events carry `route: "DIRECT"` and a fixed
  `rationale: "explicit entity+field lookup, no routing"`. They are precision queries by
  construction, so `canonical_hit` is never absent on them.
- **All three `canonical_hit` states** — `true` (exact hit), `false` (a precision query the bundle
  cannot answer yet — the curation backlog), and absent (pure semantic, deterministic path not
  consulted). The 35% miss rate is the point of the signal, not a defect: it is the backlog the
  instrument exists to surface, and on a 4-concept prototype it should be visible.
- **Both refusal causes** — a deterministic `NOT_FOUND` (Q11, two backlog misses, and the
  `rate_limit_rpm` direct lookup) and a floor-blocked semantic probe (the three off-topic
  questions).
- **The declared floor limits from `findings.md`** — the GPT-5 pricing query clears the floor
  (correct retrieval, wrong entity) and is *not* refused; that is the honest limit, present in the
  data rather than hidden from it.

## The two things the summary implementation must pin

Both exist so the JVM port reproduces a **byte-identical** summary rather than an almost-equal
one. Either would drift a digit and break the golden compare.

**Percentiles use nearest-rank, no interpolation:**

```
sort ascending -> rank = ceil(p/100 * n) -> value = xs[rank-1]   (rank floored at 1)
```

Nearest-rank is chosen over interpolation because it is trivial to reimplement identically. Any
interpolating percentile (numpy's default, etc.) will drift the last digit.

**Percentages truncate, they never round:**

```
int(part / whole * 100)
```

`SEMANTIC` is 12 of 26 = 46.15%, which truncates to 46 and rounds to 46 — but `DETERMINISTIC` at
8 of 26 = 30.77% truncates to 30 and rounds to 31. `Math.round` in the port would produce a
different line.

A consequence worth seeing rather than discovering: **the route-mix percentages sum to 98%, not
100%**, because four buckets each lose their fractional part. That is arithmetic, not an error,
but it is visible on a slide.

## One quirk worth leaving visible

The total `p50` (188.2) sits **below** the semantic `p50` (218.0): nearly half the traffic — 12 of
26 — is cheap deterministic and direct queries at under 2.5 ms, which pulls the overall median
down to the fastest semantic query. That is correct.
