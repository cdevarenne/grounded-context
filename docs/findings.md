# What broke while building the hybrid path

Two things surfaced building the semantic path. Neither is exotic; both are easy to miss, and
both are about the same technique everyone reaches for — reciprocal rank fusion.

<!-- TODO(devarenne): two open items before this goes public or in front of the screen.
     1. Finding 1 below claims the *standard* analyzer splits `rank_constant` on the underscore.
        Verify with `POST /_analyze {"analyzer":"standard","text":"rank_constant"}`. Under UAX#29
        the underscore is a connector (ExtendNumLet) — hyphens split, underscores usually don't —
        so the analyzer may keep it as ONE token, which would make the boost (not tokenization)
        the real cause of the rank change. Confirm the mechanism before asserting it.
     2. Finding 2 uses "how do I bake sourdough bread?" at 0.068 vs 0.073; the code docstring in
        semantic.py uses "what is the capital of France?" with the SAME two numbers. Pick one
        example, re-measure, and make the repo and this file agree. -->

## The lexical arm couldn't see exact tokens

The premise of the hybrid path is that lexical matching catches what embeddings miss —
identifiers, parameter names, version strings. Building it, that turned out not to be true by
default.

The test case is `rank_constant`, an RRF parameter defined in exactly one chunk of the corpus.
Elasticsearch's standard analyzer splits it on the underscore into `rank` and `constant`, so
the "exact token" arm was matching two common words across unrelated documents. The arm meant
to guarantee precision was the one behaving fuzzily.

The fix is a `content.exact` subfield analyzed with a whitespace tokenizer, so identifiers
survive as single tokens, queried alongside the standard field. Measured against the same
index, ranking the one chunk that defines the term:

| Query | ELSER only | BM25 only | Hybrid (RRF) |
|---|---|---|---|
| "What does the `rank_constant` parameter do?" | 2 | 3 | **1** |
| `rank_constant` | 5 | 1 | **1** |

Two things worth taking from that table. **Neither single arm wins both phrasings** — ELSER
degrades on the bare identifier, BM25 degrades on the natural-language sentence — and which one
fails depends on how the user happens to type. That variance, not a headline win, is the real
argument for fusion. And the exact-token subfield is load-bearing: without it the same lookups
rank 3rd and 4th instead of 1st and 2nd.

The broader lesson is that "hybrid search" is not a switch you turn on. The lexical half only
does its job if the analyzer preserves the tokens you need it to match, and nothing warns you
when it doesn't — the queries simply return plausible, adjacent, wrong documents.

## RRF scores can't tell you when nothing matches

The refusal guarantee — *no answer without a grounded source* — quietly assumes retrieval knows
when it has found nothing. Fusion does not.

Asked "how do I bake sourdough bread?", this corpus returned five confident, cited chunks about
Elasticsearch. The fused score for that top hit was **0.068**. For a real question about
streaming API responses, it was **0.073**. The two are indistinguishable, and the reason is
structural: RRF scores come from *rank position* — `1/(k + rank)` — so the best hit scores
roughly the same whether it is a perfect match or the least-bad of 320 irrelevant chunks.
Fusion deliberately discards the magnitude that would have told you.

Pre-fusion scores keep it. Measured on the same queries, the sparse arm gives out-of-domain
questions **2.6–4.5** against **15+** for genuine ones, so the semantic path now probes that
score first and returns nothing when it falls below a floor. An empty result becomes the
refusal, which is the honest outcome.

What that floor does **not** catch is a question that is in-domain but about the wrong entity:
"the price per million tokens of GPT-5" scores 19.4, because the corpus genuinely discusses
pricing — just Anthropic's. Relevance and correct-entity are different problems, and the second
belongs to the router and the canonical layer, not the retriever. Worth saying plainly, because
a floor that looks like a correctness check is more dangerous than no floor at all.

<!-- Context on how well-known this is (for your own framing, not necessarily to publish):
     the rank-based / score-agnostic property is RRF's headline selling point and is documented
     everywhere as an *advantage*. The *consequence* — that you can't threshold the fused score
     to detect "nothing matched" — is absent from Elastic's official RRF docs and only recently
     written up in practitioner posts (dev.to / srcecde.me, July 2026; howaiworks.ai). Frame this
     as "under-documented where a practitioner would look, and I hit it by building," not as a
     discovery. -->
