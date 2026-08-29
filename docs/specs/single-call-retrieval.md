# Spec: Single-Call Semantic Retrieval

## The cost this removes

`findings.md` §3 closes on a cost: *"this floor is a second retrieval on every semantic query,
since the probe runs before the fusion it gates."* Every semantic answer makes two round trips —
`probe()` reads a pre-fusion ELSER score to decide answerability, then `search()` runs the RRF
fusion it gated. `service.py:130` makes both.

This spec covers the candidate for collapsing those into one call, what was measured about it,
and the order the work has to happen in.

Everything below was measured on 2026-08-28 against the rebuilt index: 320 chunks, Elasticsearch
9.6.0 serverless, ELSER `.elser-2-elasticsearch`. The probe set is the 10 off-topic + 6 in-domain
+ 1 wrong-entity queries already defined in `scripts/measure_findings.py`.

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

## Decision

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
(`service.py:106`), which exists precisely so a near miss at 7.9 is distinguishable after the fact
from a query that was never in domain at 1.7 — the distinction `probe()` documents and
`telemetry.py` reports as `relevance_score`.

Comparing the returned top score to the floor in Python is the same single round trip and keeps
the number. `min_score` buys nothing the client-side comparison does not, and costs an
observability signal the repo already committed to.

## The control comes first

**Re-deriving the incumbent floor is Phase 1, ahead of any candidate work.** Not as housekeeping
— the incumbent is the control the candidate is measured against, and on this index it is
stronger than the candidate on most of the probe set.

`RELEVANCE_FLOOR = 8.0` is documented in `semantic.py:31` as "a property of this index — re-chunk,
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

**This sweep is fitted on the same 17 probes it is evaluated against.** The 9.5% figure is
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

Ranked against the pinned defining chunks in `evaluation.DEFINING_CHUNKS`, so these are directly
comparable with the `findings.md` §1 table.

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
  2.0/6.0 on 2026-08-28 — but classifies all 17 probes identically, so the change would be churn on a
  published constant with no measured effect. The derivation is recorded at `semantic.py:31`.
- **`findings.md` §2 reproduced to the digit**: 44 of 149 hyphenated improved, 0 of 87 underscored,
  0 regressed, 137 of 568 invisible to `content.exact`, 6 chunks collapsing to 1. It is a finding
  about tokenization, which a new inference endpoint cannot move.
- **§1's eight ranks moved in two cells**, both ELSER (`num_candidates` sentence 2 → 1,
  `anthropic-ratelimit-tokens-reset` sentence 1 → 3). Every claim in §1 survives: the hybrid is
  still never worse than the weaker arm on all eight, and still matches or beats the stronger arm
  on exactly six of eight.
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
- **17 probes is not a labeled evaluation set.** Same limitation `findings.md` §3 already declares.
  Phase 2 widens it; it does not remove it.


## Phase 2 result (2026-08-28): no-go

Twenty off-topic and ten in-domain probes, written before scoring and sharing no query with the
tuning set. Both live in `scripts/measure_findings.py` as `OFF_TOPIC_HELDOUT` and
`IN_DOMAIN_HELDOUT`; a test asserts the two sets stay disjoint. Floors applied, not refitted:
incumbent 8.0, candidate 17.0 (the midpoint of the tuning gap [16.11, 17.81]).

**Both classify the held-out set perfectly — 0 false accepts in 20, 0 false rejects in 10.** The
candidate is not broken. It is simply dominated:

| Config | Tuning margin | Held-out margin | Held-out off ≤ / gen ≥ |
|---|---|---|---|
| Incumbent (ELSER raw) | 57.1% | **59.8%** | 5.34 / 13.28 |
| Candidate w=1.0 | 0.4% | **−42.9%** | 56.25 / 39.36 |
| Candidate w=0.5 | 0.8% | −9.1% | 28.95 / 26.52 |
| Candidate w=0.25 | 9.5% | 21.9% | 15.30 / 19.58 |
| Candidate w=0.1 | −6.9% | 49.2% | 7.70 / 15.15 |

Three things kill it.

**The incumbent generalizes and the candidate's weight does not.** ELSER raw scores 57.1% on the
set it was derived from and 59.8% on one it has never seen — the floor is a real signal, not a
fit. The candidate's *optimal weight moves*: 0.25 was best on tuning, where 0.1 was negative; on
held-out 0.1 is the best config and 0.25 is less than half as good. The optimum is set by whichever
single off-topic outlier a probe set happens to contain — marathon at 16.11 in tuning, nothing
equivalent in held-out. A constant chosen that way cannot be trusted at a floor.

**Equal weights fail outright.** At w=1.0 the held-out margin is −42.9%: "How do I get a passport
renewed?" scores 56.25, above every genuine query in the set. The tuning set's 0.4% margin was not
a thin pass, it was an accident of having too few long off-topic queries. This is Elastic's
documented warning about `none` showing up exactly as documented.

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
only, against a floor that is 2.7× better separated, ranking that is strictly better, a bounded
and better-understood score, and no tunable constant that moves with the probe set.

Phases 3–5 are cancelled. What survives is the held-out probe set, which is now permanent
regression coverage for the floor, and this document as the record of why the obvious optimization
is the wrong one.