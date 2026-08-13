# What broke while building the hybrid path

Three things surfaced building the semantic path. None is exotic. Two are about the analyzer
underneath the lexical arm, and one is about reciprocal rank fusion itself.

Every number below was measured against the live index described in the README — 320 chunks of
curated Elastic and Anthropic documentation — and the commands that produce them are in
[`eval-output.md`](eval-output.md).

## 1. Neither retrieval arm wins both phrasings of the same question

The test case is `rank_constant`, an RRF parameter defined in exactly one chunk of the corpus.
Two ways to ask about it, and the rank each arm gives that defining chunk:

| Query | ELSER only | BM25 only | Hybrid (RRF) |
|---|---|---|---|
| "What does the `rank_constant` parameter do?" | 2 | 3 | **1** |
| `rank_constant` | 5 | 1 | **1** |

The headline is not that fusion wins. It is *which arm loses, and when.* ELSER degrades on the
bare identifier — it has no notion of a literal string, so it returns the semantic neighborhood
of the term instead of its definition. BM25 degrades on the natural-language sentence, where
the identifier is diluted by common words the corpus is full of.

Which arm fails therefore depends on how the user happens to type, and a user types both ways.
That variance is the real argument for hybrid retrieval — stronger than any single headline
score, because it is an argument about the worst case rather than the average one.

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
hyphens shatter — the opposite of the original claim. Across the identifiers appearing in
exactly one chunk of this corpus, adding the exact subfield improved the rank of **44 of 149**
hyphenated ones and **0 of 87** underscore ones. Zero. The subfield does nothing for the case
it was supposedly added to fix.

It is still load-bearing, for a second reason that only showed up on inspection. The standard
analyzer also strips punctuation, so a code sample's `"rank_constant":` and a prose mention of
`rank_constant` collapse onto the same token — six chunks match. The whitespace subfield keeps
the quotes and colon attached, so the same query matches **one** chunk: the one place the term
appears as bare prose, which is the chunk that defines it. That is what lifts it from rank 3 to
rank 1, and the `boost: 3` is not what does it — at `boost: 1` the rank is already 1.

The same strictness cuts the other way, which is why the lexical arm queries *both* fields
rather than the exact one alone. Of the identifiers in this corpus, **137 of 568** match on
`content` but are invisible to `content.exact`, because the corpus only ever writes them inside
punctuation — `"batch_id":`, `"claude-sonnet-4-6"`. An exact-only arm would silently lose all
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
API responses it is **0.073**. The two are indistinguishable, and the reason is structural: RRF
scores come from *rank position* — `1/(k + rank)` — so the best hit scores about the same
whether it is a perfect match or the least bad of 320 irrelevant chunks. Fusion deliberately
discards the magnitude that would have separated them. That is not a flaw; it is the property
that lets RRF combine rankings whose raw scores are not comparable. It just means the fused
score is unusable as a confidence signal.

The pre-fusion scores still hold the magnitude. Measured on the same index, the sparse arm
scores off-topic questions **1.7–5.9** and genuine ones **14–19.5**, so the semantic path probes
that score first and returns nothing below a floor. An empty result becomes the refusal.

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
