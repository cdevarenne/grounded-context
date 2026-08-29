# What broke while building the hybrid path

Four things surfaced building the semantic path. None is exotic. Two are about the analyzer
underneath the lexical arm, one is about reciprocal rank fusion itself, and the fourth is about
the optimization the third one invites.

Every number below was measured against the live index described in the README — 320 chunks of
curated Elastic and Anthropic documentation — and every one is regenerable. Per-query ranks come
from `gctx eval --compare`; the corpus-wide figures come from
`scripts/measure_findings.py`. Both are captured verbatim in [`eval-output.md`](eval-output.md),
so none of this has to be taken on trust.

The figures were re-measured on 2026-08-28, after the corpus was reindexed onto a new
Elasticsearch deployment. Section 2 came back identical to the digit — it is a finding about
tokenization, which a new inference endpoint cannot move. Everything that shifted in sections 1
and 3 is ELSER's. Section 1's claims survive unchanged. Two of section 3's got *stronger*, and
both are marked where they appear: the best off-topic query now outranks all six genuine ones
rather than four, and it reaches the RRF ceiling exactly.

## 1. Neither retrieval arm wins both phrasings of the same question

The test case is an identifier and the one chunk that defines it. Ask for it two ways — as a
bare token and as a sentence — and rank that chunk under each arm. Four identifiers, across
three different source documents and two vendors:

| Identifier | Phrasing | ELSER only | BM25 only | Hybrid (RRF) |
|---|---|---|---|---|
| `rank_constant` | token | 5 | 1 | **1** |
| `rank_constant` | sentence | 2 | 3 | **1** |
| `num_candidates` | token | 1 | 1 | **1** |
| `num_candidates` | sentence | 1 | 5 | **1** |
| `anthropic-ratelimit-tokens-reset` | token | 2 | 1 | **1** |
| `anthropic-ratelimit-tokens-reset` | sentence | 1 | 1 | **1** |
| `rank_window_size` | token | 6 | 2 | 5 |
| `rank_window_size` | sentence | 7 | 2 | 3 |

The headline is not that fusion wins. It is *which arm loses, and when.* ELSER degrades on the
bare identifier — it has no notion of a literal string, so it returns the semantic neighborhood
of the term instead of its definition. BM25 degrades on the natural-language sentence, where
the identifier is diluted by common words the corpus is full of. Which arm fails depends on how
the user happens to type, and a user types both ways.

**The last two rows are the honest part.** For `rank_window_size`, fusion does *not* beat the
better arm — BM25 alone ranks the defining chunk 2nd both times, while the hybrid lands 5th and
3rd. Because RRF sums `1/(k + rank)` from each arm, a document ranked poorly by one arm carries
a near-zero contribution from it. The weak arm therefore dilutes the strong one rather than
deferring to it. That term appears in nine chunks of the reference page, and ELSER spreads its
weight across the ones about pagination rather than the one that defines the parameter.

So the defensible claim is narrower than "hybrid wins," and worth stating precisely: across
these eight lookups the hybrid is **never worse than the weaker arm**, and in six of eight it
matches or beats the stronger one — but it does not guarantee beating the stronger arm. Fusion
is a hedge against the worst case, not a maximum over the best. That is still the right default
when you cannot predict how a user will phrase a question. It is not a free upgrade, and a
benchmark reporting only an average would have hidden both halves of that.

## 2. The analyzer gotcha is real, and not the one I hypothesized

The first version of this document stated a hypothesis: the standard analyzer splits
`rank_constant` on the underscore, and rescuing that token is what the `content.exact` subfield
is for. It explained the symptom, so it went unchecked. Testing it is a one-line call:

```console
$ POST /grounded-context-corpus/_analyze { "field": "content", "text": "..." }
```

| Identifier | `content` (standard) | `content.exact` (whitespace) |
|---|---|---|
| `rank_constant` | `rank_constant` | `rank_constant` |
| `num_candidates` | `num_candidates` | `num_candidates` |
| `claude-opus-5` | `claude`, `opus`, `5` | `claude-opus-5` |
| `claude-haiku-4-5` | `claude`, `haiku`, `4`, `5` | `claude-haiku-4-5` |

The standard tokenizer follows Unicode Text Segmentation (UAX #29), where the underscore is a
*connector* that holds a run together and the hyphen is a break. So underscores survive and
hyphens shatter — the opposite of the original claim. Across the tokens appearing in exactly
one chunk of this corpus and longer than ten characters, adding the exact subfield improved the
rank of **44 of 149** hyphenated ones and **0 of 87** underscore ones — with nothing regressing
in either group. Zero. The subfield does nothing for the case it was supposedly added to fix.

It is still load-bearing, for a second reason that only showed up on inspection. The standard
analyzer also strips punctuation, so a code sample's `"rank_constant":` and a prose mention of
`rank_constant` collapse onto the same token — six chunks match. The whitespace subfield keeps
the quotes and colon attached, so the same query matches **one** chunk: the one place the term
appears as bare prose, which is the chunk that defines it. That is what lifts it from rank 3 to
rank 1, and the `boost: 3` is not what does it — at `boost: 1` the rank is already 1.

The same strictness cuts the other way, which is why the lexical arm queries *both* fields
rather than the exact one alone. Of the hyphenated or underscored tokens in this corpus at least
eight characters long, **137 of 568** match on `content` but are invisible to `content.exact`,
because the corpus only ever writes them inside punctuation — `batch_id` and `claude-sonnet-4-6`
are two the sweep confirms, and it prints their membership so this sentence cannot drift from
the data. (The set is *tokens*, not clean identifiers: it also collects dates like `2019-05-01`,
which the same punctuation rule hides.) An exact-only arm would silently lose all
of them.

The lesson is not "add a keyword subfield." It is that the lexical half of hybrid search
inherits whatever the analyzer decided, that the failure is silent — the query returns
plausible, adjacent, incorrect chunks — and that `_analyze` is the only thing that reports which
way it went. An explanation that fits the symptom is not the same as one that has been run. This one was
committed at 08:35 and corrected at 15:48 the same day, and the check that settled it was a
single call.

## 3. RRF scores cannot tell you when nothing matches

The refusal guarantee — *no answer without a grounded source* — quietly assumes retrieval knows
when it has found nothing. Fusion does not.

Asked "how do I bake sourdough bread?", this corpus returns five confident, cited chunks about
Elasticsearch. The fused score of the top hit is **0.0635**. For a real question about streaming
API responses it is **0.0729**. Two queries is a coincidence, though, so I ran sixteen — ten
off-topic, six genuine — and the result is worse than "indistinguishable":

| | fused (RRF) | pre-fusion (ELSER) |
|---|---|---|
| 10 off-topic questions | 0.0476 – 0.0952 | 1.56 – 16.11 |
| 6 genuine questions | 0.0707 – 0.0931 | 14.02 – 19.25 |

The fused ranges **overlap across almost their whole span**. The best-scoring off-topic
question — "what are the symptoms of vitamin D deficiency?" at 0.0952 — outranks **all six
genuine questions**, including "how do I stream responses from the API?" at 0.0729. A
threshold on the fused score would not merely be unreliable; it would actively prefer a
question the corpus cannot answer over one it can.

The reason is structural. RRF sums reciprocal ranks — `Σ 1/(k + rank)` across arms — so the
spread reflects how much the two arms *agreed on an ordering*, not how good the documents are.
Fusion deliberately discards the magnitude that would have separated them. That is not a flaw;
it is the property that lets RRF combine rankings whose raw scores are not comparable. It just
means the fused score is unusable as a confidence signal.

That is an argument from the definition, so it is checked against the definition rather than
inferred from output. Taking each returned document's rank in the two arms separately and
computing the sum reproduces the score Elasticsearch reported to about 1e-9, every time —
`scripts/rrf_audit.py`, captured in [`eval-output.md`](eval-output.md).

The audit also corrected a claim. On the first index, sixteen probes topped out at 0.0931, which
suggested the ceiling `2/(k+1)` = 0.0952 is unreachable because the arms rarely agree on first
place. That conclusion is about all queries, and sixteen probes cannot support it. Tested
directly, the query `reciprocal rank fusion` ranks the same chunk first in *both* arms and scores
exactly 0.095238. What 0.0931 means is `1/21 + 1/22` — first in one arm, second in the other. The
score reports arm agreement and nothing else.

The rebuilt index then made the same point without the direct test. "What are the symptoms of
vitamin D deficiency?" now scores 0.0952 — the ceiling itself — because both arms happen to agree
on a first place for a question this corpus cannot answer at all. The highest fused score the
corpus can produce belongs to an off-topic query, which is the cleanest statement of the problem
this section describes.

The pre-fusion scores keep that magnitude, and there the separation is clean: **9 of the 10
off-topic land at 1.56–6.01** against **14.02–19.25** for all six genuine ones — a gap of eight
points with nothing in it. So the semantic path probes that score first and returns nothing
below a floor of 8. An empty result becomes the refusal. The tenth off-topic probe scored 16.11
and is the second limit below — it is not an outlier to be waved away, and the floor lets it
through.

Every figure here is regenerable: `uv run --extra es python scripts/measure_findings.py`.

Two limits, both worth stating plainly, because a floor that *looks* like a correctness check is
more dangerous than no floor at all.

It does not catch a question that is in-domain but about the wrong entity. "The price per
million tokens of GPT-5" scores 18.84, because the corpus genuinely discusses pricing — just
Anthropic's. Relevance and correct-entity are different problems, and the second belongs to the
router and the canonical layer, not the retriever.

And it measures the corpus as *text*, not as subject matter. "What is the best way to train for
a marathon?" clears the floor at 16.11, which looked like a bug until I read the passage it
matched: Elastic's `semantic_text` documentation teaches the feature using running and exercise
sample documents. The retrieval is correct. A doc page's illustrative data is part of the
retrievable surface, whether or not it is part of the subject.

One cost worth naming: this floor is a second retrieval on every semantic query, since the probe
runs before the fusion it gates. Removing it looks easy, which is finding 4.

## 4. The obvious way to remove that second call makes the floor worse

Finding 3 ends with a cost: two round trips per semantic query, because the score that gates the
answer is not the score that ranks it. Elasticsearch has an apparent fix. The `linear` retriever
combines arms by weighted sum instead of by rank, `min_score` filters a compound retriever after
scoring, and together they should collapse the probe and the fusion into one call.

I built it and measured it. It does not work, and the way it fails is more interesting than the
saving would have been.

### Normalizing to [0, 1] does not make a score mean anything

The natural configuration is `minmax` normalization — it is what the docs use in nearly every
`linear` example, and it puts both arms on a comparable scale. Top score across the same sixteen
probes:

| Config | 10 off-topic | 6 genuine | Gap |
|---|---|---|---|
| `rrf` (current) | 0.0476 – 0.0952 | 0.0707 – 0.0931 | −0.0245 |
| `linear` / `minmax` | 1.0000 – 2.0000 | 1.0000 – 1.8971 | **−1.0000** |
| `linear` / `l2_norm` | 0.3421 – 0.8172 | 0.3318 – 0.7786 | −0.4854 |
| `linear` / `none` | 4.5191 – 50.2991 | 50.4771 – 72.0512 | +0.1780 |

MinMax overlaps *worse than the RRF it was supposed to fix*, and the exact values say why. Scores
land on precisely 1.0000 and precisely 2.0000 because minmax is `(score − min) / (max − min)`
computed over each sub-retriever's own result set — so the top document of each arm always
normalizes to exactly 1.0, whatever it scored. The sum is pinned to [1.0, 2.0] and measures how
far the two arms agreed on a first place. That is the same quantity RRF reports, reached by
different arithmetic. "What are the symptoms of vitamin D deficiency?" scores a flat 2.0000.

`l2_norm` fails for the same underlying reason: it is also relative to the candidate set.

The lesson generalizes past this corpus. Normalization makes scores from different retrievers
*comparable to each other*. It does not make them *interpretable on their own*, and a floor needs
the second property. Only `none` keeps it.

### With raw scores there is a threshold, and no good place to stand

`none` does separate, and `min_score` does gate it in a single call. But under `none` the sum
inherits BM25's magnitude, and BM25 rewards long questions full of common words regardless of
subject. The lever is the weight on the lexical arm. Sweeping it against the sixteen tuning probes
picked 0.25, so I wrote twenty more off-topic and ten more in-domain questions, scored them once,
and applied both floors without refitting either:

| Config | Margin on the 16 tuning probes | Margin on 30 held-out |
|---|---|---|
| Pre-fusion ELSER (shipped) | 57.1% | **59.8%** |
| `linear`/`none` w=1.0 | 0.4% | **−42.9%** |
| `linear`/`none` w=0.5 | 0.8% | −9.1% |
| `linear`/`none` w=0.25 | 9.5% | 21.9% |
| `linear`/`none` w=0.1 | −6.9% | 49.2% |

Both shipped and candidate classify all thirty held-out probes correctly — no off-topic question
answered, no genuine one refused. The candidate is not broken. It is dominated, in three ways.

**Its tuned constant does not survive contact with new queries.** Weight 0.25 was the optimum on
the probes it was fitted to, where 0.1 scored negative; on held-out probes 0.1 is the best setting
and 0.25 is less than half as good. The optimum is decided by whichever single off-topic outlier a
probe set happens to contain — the marathon question at 16.11 in the first set, nothing like it in
the second. The shipped floor has no such constant to move: 57.1% on the set that produced it,
59.8% on a set it had never seen.

**Equal weights fail outright on held-out data.** At w=1.0 the margin is −42.9%, and "How do I get
a passport renewed?" scores 56.25 — above every genuine question in the set. The 0.4% margin on
the tuning probes was not a narrow pass. It was too few long off-topic queries.

**No weight is good at both jobs.** Separation improves monotonically as the weight falls,
converging on the shipped floor because it converges on *being* the shipped floor. Ranking moves
the other way: at w=1.0 and w=0.5 the candidate reproduces RRF's rank for the defining chunk on
all eight identifier lookups from finding 1; at w=0.25 it loses two, at w=0.1 it loses four. Every
unit of BM25 that helps the ranking degrades the answerability signal.

Both tables are regenerable: `uv run --extra es python scripts/single_call_probe.py`, captured in
[`eval-output.md`](eval-output.md). The held-out probes live in `scripts/measure_findings.py`, and
a test asserts they stay disjoint from the sixteen the floor was derived from — a held-out set
that quietly acquires a tuning query stops being evidence and nothing else would catch it.

### What the second call is actually buying

It is not overhead. It is the separation of concerns: one score is read to *rank*, a different
score to decide *whether to answer at all*, and neither has to compromise for the other. Fusing
them into one number forces a single scale to serve two purposes that pull in opposite directions.

That is finding 3 one level up. RRF discards magnitude and therefore cannot report confidence; a
weighted sum keeps magnitude but contaminates it with an arm that is in the query for an unrelated
reason. Both are the same mistake — asking the ranking score to also be the confidence score.

So the two-call design stays, and the cost paragraph at the end of finding 3 stands as a cost
rather than a defect. What came out of the attempt is worth more than the round trip: thirty
probes the floor was never fitted to, which now run as regression coverage
(`heldout_floor_check`), and a measured reason to distrust the configuration the documentation
demonstrates first.

<!-- Framing note, not for publication: keep finding 4 scoped to this corpus and this index. The
     defensible claim is "measured here, and the mechanism explains why" — minmax pinning the top
     document to 1.0 is arithmetic and does generalize; the weight instability and the specific
     margins are properties of a 320-chunk corpus and 46 probes. Never write that linear
     retrievers cannot support a floor. Write that this one could not, and show the numbers. -->

<!-- Framing note, not for publication: the rank-based property is RRF's headline selling point
     and is documented everywhere as an advantage. The *consequence* — that you cannot threshold
     the fused score to detect "nothing matched" — is absent from Elastic's official RRF docs and
     only recently written up by practitioners. Claim "under-documented where a practitioner
     would look, and I hit it by building," never discovery. -->
