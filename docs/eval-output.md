# Captured output

The numbers in [`findings.md`](findings.md) come from a live index, which a reader cannot
reach: the corpus is fetched rather than committed, and the cluster is mine. This file is the
run, captured verbatim, so the claims are checkable without either.

Captured 2026-08-28 against Elastic Cloud Serverless 9.6.0, index `grounded-context-corpus`,
320 chunks, ELSER via the preconfigured `.elser-2-elasticsearch` endpoint. The corpus was
reindexed onto a new deployment that day, so these supersede an earlier capture; the ELSER
figures moved slightly and the BM25 and analyzer ones did not.

To reproduce, with `ES_URL` and `ES_API_KEY` in `.env`:

```bash
uv sync --extra dev --extra es
uv run python scripts/fetch_corpus.py
uv run --extra es python scripts/index_corpus.py --recreate
```

## The eval set

```console
$ uv run --extra es gctx eval
id   expected       actual         route         cites  verdict
Q1   deterministic  deterministic  DETERMINISTIC 1      PASS
Q2   deterministic  deterministic  DETERMINISTIC 1      PASS
Q3   semantic       semantic       BOTH          5      KNOWN
Q4   deterministic  deterministic  DETERMINISTIC 1      PASS
Q5   semantic       semantic       SEMANTIC      5      PASS
Q6   semantic       semantic       SEMANTIC      5      PASS
Q7   semantic       semantic       SEMANTIC      5      PASS
Q8   semantic       semantic       SEMANTIC      5      PASS
Q9   semantic       semantic       BOTH          5      PASS
Q10  mixed          mixed          BOTH          6      PASS
Q11  refusal        refusal        DETERMINISTIC 0      PASS
Q12  deterministic  deterministic  DETERMINISTIC 1      PASS
Q13  deterministic  deterministic  DETERMINISTIC 1      PASS
Q14  deterministic  deterministic  DETERMINISTIC 1      PASS
Q15  deterministic  deterministic  DETERMINISTIC 1      PASS
Q16  deterministic  deterministic  DETERMINISTIC 1      PASS
Q17  deterministic  deterministic  DETERMINISTIC 1      PASS
Q18  refusal        refusal        BOTH          0      PASS
Q19  refusal        refusal        SEMANTIC      0      PASS
Q20  refusal        semantic       SEMANTIC      5      KNOWN

Q3 KNOWN — eval.md expects a deterministic list. Lookup answers one entity at a time, so a cross-model rollup has no engine and falls through to semantic passages that do not really answer it. docs/compatibility-matrix.md is what answers this today.

Q20 KNOWN — findings.md finding 3: this clears the floor at 16.11 because Elastic's semantic_text page teaches the feature with running and exercise sample documents. The retrieval is correct and the passages are real; only the subject is a surprise. The floor measures the corpus as text, not as subject matter.

18 pass · 2 known deviation · 0 fail
```

Two cases are declared deviations rather than passes, and the harness reports both as `KNOWN`
rather than letting them read as green. Q3 is a multi-entity rollup ("which models support
vision?"), which has no engine on the deterministic path — that answers one entity at a time. Q20
is the relevance floor's documented false positive: it clears the floor at 16.11 because Elastic's
`semantic_text` page teaches the feature with running and exercise sample documents.

Q13–Q18 are paraphrases: the same canonical facts, asked without using the bundle's vocabulary.
They were added after a defect that broke every natural phrasing survived a green suite for six
days — the model files carried no aliases, so `Opus 5` resolved to nothing and the query fell
through to ranked passages. Q18 is the precision exception: a comparison the bundle cannot answer
refuses rather than falling back.

Q19 and Q20 are the relevance floor, end to end. Q11 and Q18 both refuse without the floor ever
running — the router sends them to the deterministic path — and every case that does reach the
semantic arm returns passages, so nothing in the set walked the path that ends in a floor refusal
until Q19. Q19 is the floor working, at 2.05 against a floor of 8. Q20 is the floor failing, at
16.11, declared rather than hidden.

## Neither arm wins both phrasings

Finding 1 in [`findings.md`](findings.md). Each run ranks the chunk that *defines* the queried
identifier — chosen by reading the passage, not by trusting the top hit — under each arm.
`gctx eval --compare` prints the target it is ranking so the claim is checkable.

```console
$ uv run --extra es gctx eval --compare "rank_constant"
query: 'rank_constant'
target: elastic-rrf chunk:1 — the chunk that defines the term

  elser    rank 5
  bm25     rank 1
  hybrid   rank 1

$ uv run --extra es gctx eval --compare "What does the rank_constant parameter do?"
query: 'What does the rank_constant parameter do?'
target: elastic-rrf chunk:1 — the chunk that defines the term

  elser    rank 2
  bm25     rank 3
  hybrid   rank 1
```

A second identifier, in a different document — `num_candidates`, defined on the kNN page:

```console
$ uv run --extra es gctx eval --compare "num_candidates"
query: 'num_candidates'
target: elastic-knn chunk:7 — the chunk that defines the term

  elser    rank 1
  bm25     rank 1
  hybrid   rank 1

$ uv run --extra es gctx eval --compare "What does the num_candidates parameter do?"
query: 'What does the num_candidates parameter do?'
target: elastic-knn chunk:7 — the chunk that defines the term

  elser    rank 1
  bm25     rank 5
  hybrid   rank 1
```

A third from the other vendor, to show the pattern is not an Elastic-docs artifact:

```console
$ uv run --extra es gctx eval --compare "anthropic-ratelimit-tokens-reset"
query: 'anthropic-ratelimit-tokens-reset'
target: anthropic-rate-limits chunk:12 — the chunk that defines the term

  elser    rank 2
  bm25     rank 1
  hybrid   rank 1

$ uv run --extra es gctx eval --compare "What does the anthropic-ratelimit-tokens-reset header do?"
query: 'What does the anthropic-ratelimit-tokens-reset header do?'
target: anthropic-rate-limits chunk:12 — the chunk that defines the term

  elser    rank 1
  bm25     rank 1
  hybrid   rank 1
```

And the counter-example, kept because it is the one that constrains the claim. Here BM25 alone
beats the hybrid on **both** phrasings:

```console
$ uv run --extra es gctx eval --compare "rank_window_size"
query: 'rank_window_size'
target: elastic-rrf chunk:1 — the chunk that defines the term

  elser    rank 6
  bm25     rank 2
  hybrid   rank 5

$ uv run --extra es gctx eval --compare "What does the rank_window_size parameter do?"
query: 'What does the rank_window_size parameter do?'
target: elastic-rrf chunk:1 — the chunk that defines the term

  elser    rank 7
  bm25     rank 2
  hybrid   rank 3
```

Across these eight lookups the hybrid is never worse than the weaker arm, and in six of eight it
matches or beats the stronger one — but `rank_window_size` shows it does not always beat the
stronger arm. Note also that `rank_window_size` is defined in the *same* chunk as
`rank_constant`, so it is a second query against a shared target rather than a fully independent
case.

## What the analyzer actually does

Finding 2. This is the call that disproved the original claim — the standard analyzer keeps
underscores and splits hyphens, not the reverse.

```console
$ uv run --extra es python -c "
from grounded_context.es_client import client, INDEX
es = client()
for t in ['rank_constant', 'num_candidates', 'claude-opus-5', 'claude-haiku-4-5']:
    std = [x['token'] for x in es.indices.analyze(index=INDEX, field='content', text=t)['tokens']]
    exa = [x['token'] for x in es.indices.analyze(index=INDEX, field='content.exact', text=t)['tokens']]
    print(f'{t:18} content={str(std):38} content.exact={exa}')
"
rank_constant      content=['rank_constant']                      content.exact=['rank_constant']
num_candidates     content=['num_candidates']                     content.exact=['num_candidates']
claude-opus-5      content=['claude', 'opus', '5']                content.exact=['claude-opus-5']
claude-haiku-4-5   content=['claude', 'haiku', '4', '5']          content.exact=['claude-haiku-4-5']
```

## The corpus-wide figures

Findings 2 and 3 quote aggregates over the whole index rather than single queries. This is the
script that computes them, run end to end. It is the answer to "where did 44 of 149 come from?"

```console
$ uv run --extra es python scripts/measure_findings.py
index grounded-context-corpus: 320 chunks

Finding 2 — why the subfield helps, on rank_constant (elastic-rrf:chunk:1)
    matches on content        6 chunks   (punctuation stripped, so code samples collapse onto the prose mention)
    matches on content.exact  1 chunk    (punctuation kept, so only the bare prose mention matches)
    rank of the defining chunk: content-only 3 -> with exact 1

Finding 2 — rank improved by the content.exact subfield
  (tokens unique to one chunk, longer than 10 characters)
    hyphenated    44 of 149 improved, 0 regressed
    underscored    0 of  87 improved, 0 regressed

  tokens matching `content` but INVISIBLE to `content.exact`
  (hyphenated or underscored, at least 8 characters):
    137 of 568
    first three alphabetically: 024-token, 2019-05-01, 2019-05-04
    cited in findings.md: batch_id             in the set
    cited in findings.md: claude-sonnet-4-6    in the set

Finding 3 — fused vs pre-fusion score (floor = 8.0)
  kind            fused  sparse  query
  off-topic      0.0635    2.05  How do I bake sourdough bread?
  off-topic      0.0680    4.37  What is the capital of France?
  off-topic      0.0682   16.11  What is the best way to train for a marathon?
  off-topic      0.0889    1.56  Who won the 1998 World Cup?
  off-topic      0.0810    1.80  What is a good recipe for beef bourguignon?
  off-topic      0.0893    6.01  How do I change a flat tire on a bicycle?
  off-topic      0.0952    2.05  What are the symptoms of vitamin D deficiency?
  off-topic      0.0476    2.48  When did the Berlin Wall fall?
  off-topic      0.0707    3.99  How tall is Mount Kilimanjaro?
  off-topic      0.0702    4.88  What is the plot of Hamlet?
  in-domain      0.0729   16.86  How do I stream responses from the API?
  in-domain      0.0893   17.81  How should I chunk documents for retrieval?
  in-domain      0.0931   19.25  What is reciprocal rank fusion?
  in-domain      0.0889   17.39  How does prompt caching work?
  in-domain      0.0931   17.73  What are the rate limit headers?
  in-domain      0.0707   14.02  How do I use semantic_text?
  wrong-entity   0.0893   18.84  What is the price per million tokens of GPT-5?

Finding 3 — the floor on held-out probes (floor = 8.0, applied not fitted)
  20 off-topic  top score 5.34   -> 0 false accepts
  10 in-domain  low score 13.28   -> 0 false rejects
```

Read the two score columns against each other, because that is the whole of Finding 3.

**`sparse` separates.** Nine of the ten off-topic questions sit at 1.66–5.90; all six genuine
ones sit at 14.10–19.48. Nothing lands in between. That gap is what the floor of 8 is cutting.

**`fused` does not.** Off-topic spans 0.0476–0.0911 and genuine spans 0.0723–0.0931 — nearly the
same interval. "What are the symptoms of vitamin D deficiency?" fuses to 0.0911 and beats four
of the six genuine questions, including "how do I stream responses from the API?" at 0.0729. A
confidence threshold on the fused score would prefer the vitamin question.

The last two exceptions are the floor's declared limits, not noise. The wrong-entity question
scores 19.39 on `sparse` because the corpus really does discuss pricing, just Anthropic's. The
marathon question scores 16.14 because Elastic's `semantic_text` page teaches the feature with
running and exercise sample documents — the retrieval is correct, only the subject is a
surprise.

## The fusion math, checked against the formula

Finding 3 argues from how RRF is defined: a fused score is `Σ 1/(k + rank)` over the arms, so it
carries rank agreement rather than match quality. Everywhere else that claim is read *out of*
Elasticsearch. This checks it *against* the formula.

For each document the hybrid returns, take its rank in each arm separately, compute the sum, and
compare to the score Elasticsearch reported. `k` is `RANK_CONSTANT`, 20.

```console
$ uv run --extra es python scripts/rrf_audit.py

=== 'What is reciprocal rank fusion?'   (k=20) ===
doc                                 bm25  elser  predicted  observed     delta
elastic-rrf:0                          2      1   0.093074  0.093074  3.07e-09
elastic-rrf:4                          1      3   0.091097  0.091097  1.51e-09
elastic-rrf:3                          3      2   0.088933  0.088933  3.68e-09

=== 'rank_constant'   (k=20) ===
doc                                 bm25  elser  predicted  observed     delta
elastic-rrf:1                          1      5   0.087619  0.087619  2.38e-09
elastic-rrf:10                         2      6   0.083916  0.083916  3.92e-09
elastic-rrf:12                         6      2   0.083916  0.083916  3.92e-09

=== 'How do I bake sourdough bread?'   (k=20) ===
doc                                 bm25  elser  predicted  observed     delta
anthropic-batch-processing:19          3     30   0.063478  0.063478  8.70e-10
anthropic-batch-processing:11          9     16   0.062261  0.062261  3.60e-09
anthropic-batch-processing:2          13     31   0.049911  0.049911  4.40e-10
```

Agreement to about 1e-9 on every row — floating-point noise. The fused score is exactly the sum
of reciprocal ranks, and nothing else. No similarity, no magnitude.

Two things fall out of this that are worth stating.

**It explains the ceiling, and corrected a claim.** A document ranked 1 by both arms scores
`2/(k+1)` = 0.0952. On the first index the best score across the sixteen probes was 0.0931, so
the ceiling looked unreachable — but it is not. The query `reciprocal rank fusion` ranks
`elastic-rrf:0` first in both arms and scores exactly 0.095238. What 0.0931 means is
`1/21 + 1/22`: first in one arm, second in the other. The score reports how much the two arms
agree and nothing else. On this index the probe set reaches the ceiling on its own: "what are the
symptoms of vitamin D deficiency?" scores 0.0952, so the highest fused score in the table belongs
to a question the corpus cannot answer.

**It explains the sourdough number.** The off-topic top hit scored 0.0635, quoted in Finding 3.
That is `1/(20+3) + 1/(20+30)` — a chunk of the batch-processing page ranked 3rd by BM25 and 30th
by ELSER, which is to say two arms that found nothing better. A confidence threshold reading that
number sees 0.0635 and cannot tell it apart from a genuine answer, because the number never
described relevance in the first place.


## The single-call candidate

Finding 4. Two tables: the four scoring configurations over the sixteen tuning probes, and the
lexical-arm weight sweep scored against those probes and against the thirty held-out ones.

The first table is the whole argument against `minmax`. Its range is [1.0000, 2.0000] and the
endpoints are exact, because minmax pins the top document of each arm to 1.0 whatever it scored —
so the sum reports arm agreement, which is the quantity RRF already failed to threshold on.

The second is the argument against the whole candidate. Read the two margin columns against each
other: the weight that wins on the probes it was fitted to is not the weight that wins on probes
it has not seen, and the setting that separates best (0.1) is the one that damages the ranking
most. The floor that shipped scores 57.1% and 59.8% on those same two sets.

```console
$ uv run --extra es python scripts/single_call_probe.py
=== normalizer comparison, 16 tuning probes ===
config                           off-topic             genuine       gap
rrf (shipped)            0.0476 – 0.0952     0.0707 – 0.0931     -0.0245
linear / minmax          1.0000 – 2.0000     1.0000 – 1.8971     -1.0000
linear / l2_norm         0.3421 – 0.8172     0.3318 – 0.7786     -0.4854
linear / none            4.5191 – 50.2991   50.4771 – 72.0512    +0.1780

=== lexical-arm weight sweep, linear / none ===
weight       tuning margin   held-out margin   worst held-out off-topic
1.0                   0.4%            -42.9%     56.25  How do I get a passport renewed?
0.5                   0.8%             -9.1%     28.95  How do I get a passport renewed?
0.25                  9.5%             21.9%     15.30  How do I get a passport renewed?
0.1                  -6.9%             49.2%      7.70  How long should I marinate chicken before grilling?
```
