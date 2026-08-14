# What broke while building the hybrid path

Three things surfaced building the semantic path. None is exotic. Two are about the analyzer
underneath the lexical arm, and one is about reciprocal rank fusion itself.

Every number below was measured against the live index described in the README — 320 chunks of
curated Elastic and Anthropic documentation — and every one is regenerable. Per-query ranks come
from `gctx eval --compare`; the corpus-wide figures come from
`scripts/measure_findings.py`. Both are captured verbatim in [`eval-output.md`](eval-output.md),
so none of this has to be taken on trust.

## 1. Neither retrieval arm wins both phrasings of the same question

The test case is an identifier and the one chunk that defines it. Ask for it two ways — as a
bare token and as a sentence — and rank that chunk under each arm. Four identifiers, across
three different source documents and two vendors:

| Identifier | Phrasing | ELSER only | BM25 only | Hybrid (RRF) |
|---|---|---|---|---|
| `rank_constant` | token | 5 | 1 | **1** |
| `rank_constant` | sentence | 2 | 3 | **1** |
| `num_candidates` | token | 1 | 1 | **1** |
| `num_candidates` | sentence | 2 | 5 | **1** |
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
3rd. Because RRF sums `1/(k + rank)` from each arm, a document one arm ranks poorly carries a
near-zero contribution from that arm, so a strongly-wrong arm dilutes a strongly-right one
instead of deferring to it. That term appears in nine chunks of the reference page, and ELSER
spreads its weight across the ones about pagination rather than the one that defines the
parameter.

So the defensible claim is narrower than "hybrid wins," and worth stating precisely: across
these eight lookups the hybrid is **never worse than the weaker arm**, and in six of eight it
matches or beats the stronger one — but it does not guarantee beating the stronger arm. Fusion
is a hedge against the worst case, not a maximum over the best. That is still the right default
when you cannot predict how a user will phrase a question. It is not a free upgrade, and a
benchmark reporting only an average would have hidden both halves of that.

## 2. The analyzer gotcha is real, but it is not the one I assumed

The first version of this document claimed the standard analyzer split `rank_constant` on the
underscore, and that rescuing the token was what the `content.exact` subfield was for. That was
wrong, and checking it is a one-line call:

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
plausible, adjacent, wrong chunks — and that `_analyze` is the only thing that tells you which
way it went. I asserted a mechanism that sounded right for a month before running it.

## 3. RRF scores cannot tell you when nothing matches

The refusal guarantee — *no answer without a grounded source* — quietly assumes retrieval knows
when it has found nothing. Fusion does not.

Asked "how do I bake sourdough bread?", this corpus returns five confident, cited chunks about
Elasticsearch. The fused score of the top hit is **0.068**. For a real question about streaming
API responses it is **0.073**. Two queries is a coincidence, though, so I ran sixteen — ten
off-topic, six genuine — and the result is worse than "indistinguishable":

| | fused (RRF) | pre-fusion (ELSER) |
|---|---|---|
| 10 off-topic questions | 0.0476 – 0.0911 | 1.66 – 16.14 |
| 6 genuine questions | 0.0723 – 0.0931 | 14.10 – 19.48 |

The fused ranges **overlap across almost their whole span**. The best-scoring off-topic
question — "what are the symptoms of vitamin D deficiency?" at 0.0911 — outranks **four of the
six genuine questions**, including "how do I stream responses from the API?" at 0.0729. A
threshold on the fused score would not merely be unreliable; it would actively prefer a
question the corpus cannot answer over one it can.

The reason is structural. RRF sums reciprocal ranks — `Σ 1/(k + rank)` across arms — so a top
hit lands near `2/(k+1)` regardless of match quality, and the spread that remains reflects how
much the two arms *agreed on an ordering*, not how good the documents are. Fusion deliberately
discards the magnitude that would have separated them. That is not a flaw; it is the property
that lets RRF combine rankings whose raw scores are not comparable. It just means the fused
score is unusable as a confidence signal.

The pre-fusion scores keep that magnitude, and there the separation is clean: **9 of the 10
off-topic land at 1.66–5.90** against **14.10–19.48** for all six genuine ones — a gap of more
than 8 with nothing in it. So the semantic path probes that score first and returns nothing
below a floor of 8. An empty result becomes the refusal. The tenth off-topic probe scored 16.14
and is the second limit below — it is not an outlier to be waved away, and the floor lets it
through.

Every figure here is regenerable: `uv run --extra es python scripts/measure_findings.py`.

Two limits, both worth stating plainly, because a floor that *looks* like a correctness check is
more dangerous than no floor at all.

It does not catch a question that is in-domain but about the wrong entity. "The price per
million tokens of GPT-5" scores 19.4, because the corpus genuinely discusses pricing — just
Anthropic's. Relevance and correct-entity are different problems, and the second belongs to the
router and the canonical layer, not the retriever.

And it measures the corpus as *text*, not as subject matter. "What is the best way to train for
a marathon?" clears the floor at 16.1, which looked like a bug until I read the passage it
matched: Elastic's `semantic_text` documentation teaches the feature using running and exercise
sample documents. The retrieval is correct. A doc page's illustrative data is part of the
retrievable surface, whether or not it is part of the subject.

One cost worth naming: this floor is a second retrieval on every semantic query, since the probe
runs before the fusion it gates.

<!-- Framing note, not for publication: the rank-based property is RRF's headline selling point
     and is documented everywhere as an advantage. The *consequence* — that you cannot threshold
     the fused score to detect "nothing matched" — is absent from Elastic's official RRF docs and
     only recently written up by practitioners. Claim "under-documented where a practitioner
     would look, and I hit it by building," never discovery. -->
