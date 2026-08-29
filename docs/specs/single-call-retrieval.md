# Spec: Single-Call Semantic Retrieval

## The cost this removes

`findings.md` §3 closes on a cost: *"this floor is a second retrieval on every semantic query,
since the probe runs before the fusion it gates."* Every semantic answer makes two round trips —
`probe()` reads a pre-fusion ELSER score to decide answerability, then `search()` runs the RRF
fusion it gated. `service.py:130` makes both.

This spec covers the candidates for collapsing those into one call, what was measured about them,
and the order the work had to happen in.

> **Verdict: no-go, and the two-call design stays.** Two shapes were built and measured. A
> `linear` retriever with a down-weighted lexical arm separates on the probes it was tuned
> against and its weight does not survive held-out ones. `min_score` on the inner ELSER arm
> classifies every probe correctly and cannot report *why* it refused. The sections below are in
> the order the work happened, so the design decisions are stated before the results that
> overturned two of them — read [Phase 2](#phase-2-result-2026-08-28-no-go) for the conclusion.

Everything below was measured against the rebuilt index: 320 chunks, Elasticsearch 9.6.0
serverless, ELSER `.elser-2-elasticsearch`. Phases 1 and 2 ran on 2026-08-28; the nested-gate
result and the AUC figures were added on 2026-08-29 against the same index.

**Probe counts, used the same way everywhere.** The **tuning set is 16** — the 10 off-topic and
6 in-domain queries in `scripts/measure_findings.py` — and it is what `RELEVANCE_FLOOR` was
derived from. The **held-out set is 30**, written before scoring and sharing no query with it.
That is **46 classified probes**. The wrong-entity query ("the price of GPT-5") is counted in
neither: it is neither off-topic nor answerable from the bundle, so it belongs to no class and
is carried as a **diagnostic**, reported separately wherever it appears. Any separability
figure below is computed on 16 or on 30, never on 17 or 47.

## Rejected: MinMax normalization

The obvious shape — a `linear` retriever with `minmax` normalization, thresholded by `min_score`
— **does not work, and is measurably worse than the RRF it would replace.** Top score by config:

| Config | Off-topic | Genuine | Gap |
|---|---|---|---|
| `rrf` (current) | 0.0476 – 0.0952 | 0.0707 – 0.0931 | −0.0245 |
| `linear` / `minmax` | 1.0000 – 2.0000 | 1.0000 – 1.8971 | **−1.0000** |
| `linear` / `l2_norm` | 0.3421 – 0.8172 | 0.3318 – 0.7786 | −0.4854 |
| `linear` / `none` | 4.5191 – 50.2991 | 50.4771 – 72.0512 | **+0.1780** |
| ELSER raw (incumbent) | 1.5647 – 16.1074 | 14.0186 – 19.2503 | −2.0888 |

The mechanism is visible in the numbers. MinMax is `(score − min) / (max − min)` computed over
each sub-retriever's own result set, so the top document of each arm always normalizes to exactly
1.0. Scores land on exactly `1.0000` and exactly `2.0000` for that reason, bounded to [1.0, 2.0],
and what they measure is **how far the two arms agreed on a top document** — the same defect as
RRF, reached by different arithmetic. "What are the symptoms of vitamin D deficiency?" scores
2.0000, above every genuine query in the set.

`l2_norm` fails the same way and for the same underlying reason: it is also relative to the
candidate set, so it also discards absolute magnitude.

This is worth stating plainly because the intuition is strong and wrong. Normalizing to [0, 1]
makes scores *comparable between arms*; it does not make them *interpretable as confidence*. Only
`none` preserves the magnitude a threshold needs.

## Decision (superseded by Phase 2 — kept as the record of what was built)

Single `linear` retriever over the existing two arms with `normalizer: "none"`, weighted toward
the sparse arm, with the floor compared **in Python against the top hit's score** — not with
`min_score`.

```
linear(
  { retriever: _lexical(q), weight: <w>,  normalizer: none },
  { retriever: _sparse(q),  weight: 1.0,  normalizer: none },
  rank_window_size: 50
)
```

### Why not `min_score`

`min_score` works. Verified in one call against this index:

```
min_score=None   How do I bake sourdough bread?   -> 5 hits, top=47.1067
min_score=50.4   How do I bake sourdough bread?   -> 0 hits          <- refusal, one call
min_score=50.4   What is reciprocal rank fusion?  -> 3 hits, top=72.0512
```

It is still the wrong tool here. It discards the score of everything it filters, so a blocked
query returns zero hits and *no number*. That destroys `SemanticResult.floor_score`
(`service.py:108`), which exists precisely so a near miss at 7.9 is distinguishable after the fact
from a query that was never in domain at 1.7 — the distinction `probe()` documents and
`telemetry.py` reports as `relevance_score`.

Comparing the returned top score to the floor in Python is the same single round trip and keeps
the number. `min_score` buys nothing the client-side comparison does not, and costs an
observability signal the repo already committed to.

### Rejected: `min_score` on the inner ELSER arm

`min_score` is documented on the `standard` retriever, not only on compound ones, so nothing stops
applying it to the sparse child *inside* one hybrid call:

```
rrf(
  { standard: <bm25> },
  { standard: <elser>, min_score: 8.0 },
  rank_window_size: 50, rank_constant: 20
)
```

This is the shape that most looks like the two-call design collapsed into one — the same floor,
the same constant, evaluated server-side — so it was measured rather than argued about. All 46
probes, 2026-08-29:

**The obvious refusal rule fails 20 out of 20.** Gating one arm does not refuse a query. The
parent still holds the lexical arm, which always returns something, so `hits == 0` fired on none
of the thirty held-out probes.

**A second rule does work.** With the sparse arm gated empty, every surviving document is ranked
by BM25 alone, so the best score available is a single `1/(k+rank)` term at rank 1 —
<!--figures:on-->`1/(20+1)` = <!--fig:single_call.ceiling-->0.047619<!--/-->.<!--figures:off--> Read "top score is at that ceiling" as the refusal signal and it classifies
**all 46 probes correctly**, including the marathon query the shipped floor gets wrong.

So this one is not rejected for failing. It is rejected for three other things, and the first
settles it on its own.

<!--figures:on-->**It destroys `floor_score`.** All thirty refused off-topic probes report the identical
`<!--fig:single_call.nested_gate.off_topic.refused_score-->0.0476191<!--/-->`, while their true sparse scores span <!--fig:single_call.nested_gate.off_topic.elser_min-->1.54<!--/--> to <!--fig:single_call.nested_gate.off_topic.elser_max-->16.11<!--/-->.<!--figures:off--> `observability.md` defines
`relevance_score` as precisely what separates a near miss at 7.9 from a query that was never in
domain at 1.7, and `telemetry.py:192` reads it back grouped by verdict on *both* sides. That is
the same objection that rejected `min_score` on the compound retriever, arrived at by a better
route: the classification is available in one call, the *number* is not.

**The marathon result is the right answer for the wrong reason.** Marathon's gated sparse arm is
not empty — three chunks clear 8.0. It lands at the ceiling because all three are from
`elastic-semantic-text` and none appears in BM25's fifty-document window:

```
marathon:  bm25 window 50 docs  |  gated ELSER 3 docs  |  overlap 0
```

The rule therefore fires on `sparse arm empty` **or** `the arms share no document`. The second
disjunct is rank agreement, which `findings.md` §3 establishes is not a relevance signal. Crediting
this construction with fixing marathon would be crediting the exact quantity the finding rejects.

**That second disjunct is a false-reject mode two calls do not have.** A genuine question whose
arms disagree is refused, with no score to explain why. It fired on none of the sixteen genuine
probes — but `findings.md` §1's `rank_window_size` row is arm divergence on an in-domain query, so
the mode is real rather than theoretical, and it is invisible in telemetry by construction.

## The control comes first

**Re-deriving the incumbent floor is Phase 1, ahead of any candidate work.** Not as housekeeping
— the incumbent is the control the candidate is measured against, and on this index it is
stronger than the candidate on most of the probe set.

`RELEVANCE_FLOOR = 8.0` is documented in `semantic.py:45` as "a property of this index — re-chunk,
re-index, or change the inference model and it means nothing." The rebuild is exactly that event.
The published figures have already drifted: `findings.md` §3 quotes off-topic ELSER at 1.66–16.14
and genuine at 14.10–19.48; on 2026-08-28 the same probes give 1.5647–16.1074 and 14.0186–19.2503.

Measured side by side, per probe:

| | all 10 off-topic | 9 off-topic (marathon excluded) |
|---|---|---|
| Incumbent (ELSER raw) | off ≤ 16.1074, gen ≥ 14.0186 — **−14.9%** | off ≤ 6.0139, gen ≥ 14.0186 — **+57.1%** |
| Candidate (`none`, w=0.25) | off ≤ 16.1074, gen ≥ 17.8077 — **+9.5%** | off ≤ 15.6995, gen ≥ 17.8077 — **+11.8%** |

The candidate does clear the marathon query that `findings.md` §3 documents as an unfixed limit.
But read the mechanism before crediting it: the marathon score is **16.1074 under both**,
unchanged. The candidate does not score marathon lower — it raises everything else. Sourdough goes
2.0474 → 11.7767, flat tire 6.0139 → 15.6995. BM25 magnitude lifts the whole off-topic
distribution toward the genuine band, and marathon stops being an outlier because the band moved,
not because the query was better understood.

So the trade is not "same guarantee, one fewer call." It is:

> one round trip and the marathon case, bought with a safety margin **~5× narrower** on the nine
> off-topic queries the incumbent already handles comfortably (57.1% → 11.8%).

For a project with a water-tight refusal guarantee, that is a real question and not an obvious
win. Phase 1 is what makes it answerable.

## Weight selection

Under `none` the combined score inherits BM25's magnitude, and BM25 rewards long off-topic
queries full of common words — which is what Elastic's own docs warn about for this normalizer.
"How do I change a flat tire on a bicycle?" scores 50.2991 against a genuine floor of 50.4771 at
equal weights. Down-weighting the lexical arm is the lever:

| Lexical weight | Off-topic max | Genuine min | Margin |
|---|---|---|---|
| 1.0 | 50.2991 | 50.4771 | 0.4% |
| 0.5 | 25.3852 | 25.5835 | 0.8% |
| **0.25** | 16.1074 | 17.8077 | **9.5%** |
| 0.1 | 16.1074 | 15.0687 | −6.9% |
| 0.0 | 16.1074 | 14.0186 | −14.9% |

**This sweep is fitted on the same 16 probes it is evaluated against.** The 9.5% figure is
therefore an upper bound on what held-out queries will show, and `w=0.25` is a starting point, not
a result. Phase 2 exists to correct for this, and it is the phase most likely to kill the
candidate.

### Ranking cost

Rank of the defining chunk for four identifiers under both phrasings, RRF against the candidate at
each weight:

| Identifier | Phrasing | RRF | w=1.0 | w=0.5 | w=0.25 | w=0.1 |
|---|---|---|---|---|---|---|
| `rank_constant` | token | 1 | 1 | 1 | **2** | 4 |
| `rank_constant` | sentence | 1 | 1 | 1 | 1 | 1 |
| `num_candidates` | token | 1 | 1 | 1 | 1 | 1 |
| `num_candidates` | sentence | 1 | 1 | 1 | 1 | 1 |
| `rank_window_size` | token | 5 | 5 | 5 | 5 | 6 |
| `rank_window_size` | sentence | 3 | 3 | 3 | **4** | 5 |
| `anthropic-ratelimit-tokens-reset` | token | 1 | 1 | 1 | 1 | 1 |
| `anthropic-ratelimit-tokens-reset` | sentence | 1 | 1 | 1 | 1 | 2 |

Ranked against the pinned defining chunks in `evaluation.DEFINING_CHUNKS`. One caveat on reading
this against the `findings.md` §1 table: this sweep phrases every sentence query as "What does the
`<term>` parameter do?", while §1 asks the rate-limit one as a *header* rather than a parameter.
That row's ranks therefore differ between the two tables. RRF and the candidate are measured on
identical queries here, so every column-to-column comparison below holds.

The separation margin and the ranking pull in opposite directions, and the crossover is sharp.
At `w=1.0` and `w=0.5` the candidate reproduces RRF's ranking on **all eight** lookups — and the
separation margin is unusable (0.4%, 0.8%). At `w=0.25`, where the margin becomes workable, it
loses two: the bare `rank_constant` token drops 1 → 2, and the `rank_window_size` sentence drops
3 → 4. **0 better, 6 same, 2 worse.**

Eval Q9 uses the `rank_constant` sentence phrasing, which holds at rank 1, so the eval suite would
not regress. But `findings.md` §1's headline case — the bare-token lookup that the whole
lexical-arm argument is built on — would.

## Phases

| Phase | Work | Est. |
|---|---|---|
| ~~**1. Re-derive the control**~~ | **Done 2026-08-28** — see below | — |
| ~~**2. Held-out validation**~~ | **Done 2026-08-28 — NO-GO.** See below | — |
| **3. Implementation** | `linear_retriever()` in `semantic.py`; collapse the two calls in `semantic_citations()`; new floor constant; update `test_semantic.py` (16 tests) and `test_service.py` mocks, which assume two calls | 2–3 h |
| **4. Re-measure** | New arms in `measure_findings.py`; regenerate `eval-output.md` | 2 h |
| **5. Write-up** | `findings.md` §4; amend §3's closing cost paragraph | 2–3 h |

**10–13 hours, ~2 focused days.** Phases 1 and 2 are ~5 h of that and are worth doing even if the
candidate is rejected: Phase 1 is owed to the docs regardless, and Phase 2's held-out probe set
strengthens the existing floor whichever retriever ends up behind it.

Phase 2 is a genuine branch. If the margin collapses on held-out queries, Phase 3 does not ship and
the work becomes a negative-result section — which for this repo is publishable content, and
cheaper than shipping a fragile floor.

### Phase 1 result (2026-08-28)

The control holds, and the floor did not move.

- **`RELEVANCE_FLOOR` stays at 8.0.** The usable gap moved from [5.9, 14.1] to [6.0, 14.0], so 8.0
  still sits inside it. Centering at 10.0 would balance the headroom — 4.0 either side against
  2.0/6.0 on 2026-08-28 — but classifies all 16 identically (and the diagnostic too), so the
  change would be churn on a
  published constant with no measured effect. The derivation is recorded at `semantic.py:45`.
- **`findings.md` §2 reproduced to the digit**: 44 of 149 hyphenated improved, 0 of 87 underscored,
  0 regressed, 137 of 568 invisible to `content.exact`, 6 chunks collapsing to 1. It is a finding
  about tokenization, which a new inference endpoint cannot move.
- **§1's eight ranks moved in one cell**: ELSER on the `num_candidates` sentence, 2 → 1. Every
  claim in §1 survives — the hybrid is still never worse than the weaker arm on all eight, and
  still matches or beats the stronger arm on exactly six of eight.
- **§3's numbers drifted and two claims got stronger.** The best off-topic query now outranks *all
  six* genuine ones rather than four, and at 0.0952 it reaches the RRF ceiling `2/(k+1)` exactly —
  so the highest fused score this corpus can produce belongs to a question it cannot answer.

Suite green afterwards: 226 passed.

One correction this phase forced on the sections above. The first ranking sweep matched any chunk
of the source document for three of the four identifiers instead of the pinned defining chunk, and
it flattered the candidate — it reported `w=1.0` beating RRF twice and `w=0.25` costing one rank.
Measured against the pinned targets, `w=1.0` merely ties RRF everywhere and `w=0.25` costs two.
The table above is the corrected one.

## Interface changes

`search()` already accepts an injectable `retriever=`, so the candidate needs no new seam to be
measured. Shipping it touches:

- `semantic.py` — add `linear_retriever(query, lexical_weight)`; `METHOD` becomes
  `hybrid(bm25+elser,linear)`, which flows into every citation's `method` field and is therefore a
  **visible change to the published provenance contract**.
- `service.py:111` — `semantic_citations()` drops from two calls to one; `floor_passed` and
  `floor_score` are derived from the single response's top hit.
- `probe()` / `is_relevant()` — retained. They are the incumbent arm in the comparison table and
  the only way to reproduce the ELSER-raw column.

Telemetry needs no schema change: `relevance_score` keeps its meaning, but its *scale* changes
from ELSER-raw to the weighted combination, so historical events are not comparable across the
cutover. That belongs in the `observability.md` notes if the candidate ships.

## Known limitations

- **Wrong-entity is unchanged.** "What is the price per million tokens of GPT-5?" scores 18.8367
  under the incumbent and 29.6621 under the candidate — above the genuine minimum in both. It
  clears any floor either design can set. This belongs to the router and the canonical layer, as
  §3 already states, and nothing here improves it.
- **The floor becomes less portable, not more.** Raw combined scores are unbounded and scale with
  query length and corpus statistics. The current floor is already index-specific; the candidate's
  is index- *and* weight-specific. What ports is still the method, never the constant.
- **16 probes is not a labeled evaluation set.** Same limitation `findings.md` §3 already declares.
  Phase 2 widens it; it does not remove it.


## Phase 2 result (2026-08-28): no-go

Twenty off-topic and ten in-domain probes, written before scoring and sharing no query with the
tuning set. Both live in `scripts/measure_findings.py` as `OFF_TOPIC_HELDOUT` and
`IN_DOMAIN_HELDOUT`; a test asserts the two sets stay disjoint.

**This is a separability sweep, not a fixed-threshold classification.** Every row is a different
score scale, so there is no single floor to apply across them. Each row's floor is derived from
that row's *tuning* probes — the midpoint of the gap — and then applied unchanged to the held-out
ones. Where a row's tuning sets overlap, no midpoint exists and the row has no floor to test.

Two measures, because they disagree and the disagreement is the finding:

- **AUC** — the probability a random genuine probe outscores a random off-topic one. Every pair
  counts, so no single query can move it far. It says whether the score *orders* the two classes.
- **margin** — `(min(genuine) − max(off-topic)) / min(genuine)`. Two order statistics and nothing
  else, so one outlier moves it freely. It says how much *headroom* a threshold has.

| Config | Tuning AUC | Tuning margin | Held-out AUC | Held-out margin | Floor | Held-out FA / FR |
|---|---|---|---|---|---|---|
<!--figures:on-->
| Incumbent (ELSER raw) | <!--fig:single_call.sweep.elser_raw.tuning_auc-->0.983<!--/--> | <!--fig:single_call.sweep.elser_raw.tuning_margin_pct-->−14.9<!--/-->% | **<!--fig:single_call.sweep.elser_raw.heldout_auc-->1.000<!--/-->** | **<!--fig:single_call.sweep.elser_raw.heldout_margin_pct-->59.8<!--/-->%** | <!--fig:single_call.sweep.elser_raw.floor-->8.00<!--/--> published | **0 / 0** |
| Candidate w=<!--lit-->1.0<!--/--> | <!--fig:single_call.sweep.w1_0.tuning_auc-->1.000<!--/--> | <!--fig:single_call.sweep.w1_0.tuning_margin_pct-->0.4<!--/-->% | <!--fig:single_call.sweep.w1_0.heldout_auc-->0.855<!--/--> | <!--fig:single_call.sweep.w1_0.heldout_margin_pct-->−42.9<!--/-->% | <!--fig:single_call.sweep.w1_0.floor-->50.39<!--/--> midpoint | 1 / 4 |
| Candidate w=<!--lit-->0.5<!--/--> | <!--fig:single_call.sweep.w0_5.tuning_auc-->1.000<!--/--> | <!--fig:single_call.sweep.w0_5.tuning_margin_pct-->0.8<!--/-->% | <!--fig:single_call.sweep.w0_5.heldout_auc-->0.990<!--/--> | <!--fig:single_call.sweep.w0_5.heldout_margin_pct-->−9.1<!--/-->% | <!--fig:single_call.sweep.w0_5.floor-->25.48<!--/--> midpoint | 1 / 0 |
| Candidate w=<!--lit-->0.25<!--/--> | <!--fig:single_call.sweep.w0_25.tuning_auc-->1.000<!--/--> | <!--fig:single_call.sweep.w0_25.tuning_margin_pct-->9.5<!--/-->% | **<!--fig:single_call.sweep.w0_25.heldout_auc-->1.000<!--/-->** | <!--fig:single_call.sweep.w0_25.heldout_margin_pct-->21.9<!--/-->% | <!--fig:single_call.sweep.w0_25.floor-->16.96<!--/--> midpoint | **0 / 0** |
| Candidate w=<!--lit-->0.1<!--/--> | <!--fig:single_call.sweep.w0_1.tuning_auc-->0.983<!--/--> | <!--fig:single_call.sweep.w0_1.tuning_margin_pct-->−6.9<!--/-->% | **<!--fig:single_call.sweep.w0_1.heldout_auc-->1.000<!--/-->** | <!--fig:single_call.sweep.w0_1.heldout_margin_pct-->49.2<!--/-->% | none — sets overlap | n/a |
<!--figures:off-->

**Only two rows classify the held-out set perfectly: the incumbent at 8.0, and the candidate at
w=0.25.** An earlier draft of this section said "both classify the held-out set perfectly"
without naming a weight, which is false for w=1.0 (one off-topic answered, four genuine refused)
and for w=0.5 (one off-topic answered). The claim holds only for the deployed configuration, and
is now stated that way wherever it appears.

Two corrections the AUC column forced, both against the incumbent:

- **The incumbent's tuning margin is −14.9%, not 57.1%.** 57.1% is the figure with the marathon
  probe excluded. Quoting it in a column where every candidate row is computed on all ten
  off-topic probes compared the incumbent on nine against the candidates on ten. Both numbers are
  real; only one belongs in that column.
- **8.0 is a fitted constant too.** It is not the midpoint of the tuning gap — no midpoint exists,
  because marathon at 16.11 sits inside the genuine band. It was chosen from the other nine. The
  candidate's weight is worse than the incumbent's floor, but not because one is fitted and the
  other is not.

What the AUC column then shows is that the incumbent's real advantage is narrower and more
defensible than the margin alone suggested. On held-out probes the incumbent, w=0.25 and w=0.1
all score **1.000** — they order the two classes identically well. They are not separated by
whether they work, but by how much room the threshold has, and by whether the setting that
produced that room survives being chosen. Three things kill it.

**The incumbent generalizes and the candidate's weight does not.** ELSER raw goes from AUC 0.983
on the set it was derived from to 1.000 on one it has never seen, and its headroom widens rather
than collapsing. The candidate's *optimal weight moves*: 0.25 was best on tuning, where 0.1 was
negative; on held-out 0.1 has the widest headroom and 0.25 less than half as much. The optimum is
set by whichever single off-topic outlier a probe set happens to contain — marathon at 16.11 in
tuning, nothing equivalent in held-out. A constant chosen that way cannot be trusted at a floor.

This is the argument that does not depend on the fragile metric. It is the *direction* the optimum
moves, not the size of any one margin, and the AUC column shows the same thing from the other
side: w=1.0 is a perfect 1.000 on tuning and 0.855 on held-out, so it is not that the tuning set
was read wrong — it is that the tuning set could not have told you.

**Equal weights fail outright.** At w=1.0 the held-out margin is −42.9%: "How do I get a passport
renewed?" scores 56.25, above every genuine query in the set. Applying that row's own tuning floor
of 50.39 to the held-out probes answers one off-topic question and refuses four genuine ones. The
tuning set's 0.4% margin was not a thin pass, it was an accident of having too few long off-topic
queries. This is Elastic's documented warning about `none` showing up exactly as documented.

**There is no weight that is good at both jobs.** Separation improves monotonically as the weight
falls — 42.9% → −9.1% → 21.9% → 49.2% — converging on the incumbent, because it converges on
*being* the incumbent. Ranking moves the other way: w=1.0 and w=0.5 reproduce RRF on all eight
identifier lookups, w=0.25 loses two, w=0.1 loses four. Every unit of BM25 that helps the ranking
degrades the answerability signal, and vice versa.

### What this actually establishes

The second call is not overhead to be optimized away. It is what buys the separation of concerns:
one score is read for *ranking*, a different score for *answerability*, and neither has to
compromise for the other. Fusing them into a single number forces one scale to serve two purposes
that pull in opposite directions — which is the same lesson as §3 of `findings.md`, one level up.
RRF discards magnitude and cannot report confidence; a linear combination keeps magnitude but
contaminates it with the arm that exists for a different reason.

**Recommendation: keep the two-call design.** The cost is one extra round trip on semantic queries
only. Against it: equal ordering on held-out probes (AUC 1.000 either way) with **2.7× the
headroom** behind the threshold, ranking that is strictly better, a bounded and better-understood
score, and no tunable constant that moves with the probe set.

State the ordering claim and the headroom claim separately. "2.7× better separated" invites the
reply that both are perfect classifiers on this data, and that reply is correct.

**Which metric the write-up leads with:** AUC first, margin second, always both. AUC is what
survives an outlier and is what an IR reader expects; the margin is what actually distinguishes
the two designs once AUC saturates at 1.000, so dropping it would remove the case rather than
strengthen it. Neither is quoted anywhere without the other.

Phases 3–5 are cancelled. What survives is the held-out probe set, which is now permanent
regression coverage for the floor, and this document as the record of why the obvious optimization
is the wrong one.