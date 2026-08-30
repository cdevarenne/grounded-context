# Reproducing the measurements

Everything this repo publishes comes from a live index. This page is how you get one and run the
commands that produce the numbers; it is **not** where the numbers live. Those are in
[`docs/data/`](data/), written by the publishing scripts, and every figure quoted in
[`findings.md`](findings.md) resolves to a record there and is asserted against it by the suite.

The split matters. A page that teaches reproduction wants example output — it is most of what
makes the instructions legible. A published figure wants a machine-readable record. Keeping both
in one file meant a documented number was recovered from console text with a regular expression,
so the figure depended on a terminal's column widths.

**The output below is illustrative.** It is real, and it was produced by the commands shown, on
the date given — but nothing asserts it, and it will age. Quote a figure from
[`findings.md`](findings.md), never from this page.

Captured 29 August 2026 against Elastic Cloud Serverless 9.6.0, index `grounded-context-corpus`,
320 chunks, ELSER via the preconfigured `.elser-2-elasticsearch` endpoint.

## 1. Build the index

With `ES_URL` and `ES_API_KEY` in a gitignored `.env`:

```bash
uv sync --extra dev --extra es
uv run python scripts/fetch_corpus.py
uv run --extra es python scripts/index_corpus.py --recreate
```

`index_corpus.py` re-derives the relevance floor after indexing and exits non-zero if it stops
separating the probes, so a rebuild that broke the floor cannot pass quietly.

## 2. Run the eval set

The twenty questions from [`specs/eval.md`](specs/eval.md), each with the engine that should
answer it and the route that should get there.

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

The verdicts are recorded in [`data/eval.json`](data/eval.json) by `scripts/publish_eval.py`.

## 3. Compare the retrieval arms

The same identifier asked two ways, ranked under each arm. This is the evidence behind
[`findings.md`](findings.md) §1.

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

All eight lookups are recorded in [`data/arms.json`](data/arms.json) by
`scripts/publish_arms.py`, which also derives the two claims §1 argues from — that fusion is never
worse than the weaker arm, and how many of the eight it matches or beats the stronger one on.

## 4. Inspect the analyzers

Why `content` and `content.exact` disagree about a hyphenated identifier — [`findings.md`](findings.md) §2.

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

## 5. Measure the corpus-wide figures

Findings 2 and 3 quote aggregates over the whole index rather than single queries.

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

  separability of each column, 10 off-topic vs 6 in-domain (wrong-entity excluded)
  AUC     P(a genuine probe outscores an off-topic one), ties counting half. 1.000 is full
          separation, 0.500 no signal. Every pair counts, so one outlier moves it by at
          most 1/(genuine x off-topic).
  margin  (min(genuine) - max(off-topic)) / min(genuine) * 100 — the headroom a threshold
          has. Two order statistics and nothing else, so one outlier can move it freely.
          Negative means the two sets overlap and no threshold separates them.
    column       AUC    margin
    sparse     0.983    -14.9%
    fused      0.758    -34.7%

Finding 3 — the floor on held-out probes (floor = 8.0, applied not fitted)
  20 off-topic  top score 5.34   -> 0 false accepts
  10 in-domain  low score 13.28   -> 0 false rejects
  separability  AUC 1.000   margin 59.8%
```

Recorded in [`data/measurements.json`](data/measurements.json) by `scripts/publish_figures.py`.

## 6. Check the fusion math against the formula

[`findings.md`](findings.md) §3 argues from how RRF is *defined*. This checks the definition
holds: take each returned document's rank in the two arms separately, compute `Σ 1/(k + rank)`,
and compare to the score Elasticsearch reported.

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

Recorded in [`data/rrf_audit.json`](data/rrf_audit.json) by `scripts/rrf_audit.py`.

## 7. Re-measure the single-call candidate

The three tables behind [`findings.md`](findings.md) §4 and
[`specs/single-call-retrieval.md`](specs/single-call-retrieval.md): the normalizer comparison, the
lexical-arm weight sweep, and `min_score` pushed into the ELSER arm.

```console
$ uv run --extra es python scripts/single_call_probe.py
=== normalizer comparison, 16 tuning probes ===
config                           off-topic             genuine       gap     AUC
rrf (shipped)            0.0476 – 0.0952     0.0707 – 0.0931     -0.0245   0.758
linear / minmax          1.0000 – 2.0000     1.0000 – 1.8971     -1.0000   0.658
linear / l2_norm         0.3421 – 0.8172     0.3318 – 0.7786     -0.4854   0.400
linear / none            4.5191 – 50.2991   50.4771 – 72.0512    +0.1780   1.000

=== lexical-arm weight sweep, linear / none ===
A separability sweep, not a fixed-threshold classification: each row is a different
score scale, so a floor is derived per row from that row's tuning probes and then
applied unchanged to the held-out ones. Confusion counts are held-out only.
  AUC     P(a genuine probe outscores an off-topic one), ties counting half. 1.000 is full
          separation, 0.500 no signal. Every pair counts, so one outlier moves it by at
          most 1/(genuine x off-topic).
  margin  (min(genuine) - max(off-topic)) / min(genuine) * 100 — the headroom a threshold
          has. Two order statistics and nothing else, so one outlier can move it freely.
          Negative means the two sets overlap and no threshold separates them.

config                          tuning        held-out   floor                         held-out errors
                           AUC  margin     AUC  margin                                  accept  reject
ELSER raw (shipped)      0.983  -14.9%   1.000   59.8%   8.00 published                      0       0
linear/none w=1.0        1.000    0.4%   0.855  -42.9%   50.39 midpoint                      1       4
linear/none w=0.5        1.000    0.8%   0.990   -9.1%   25.48 midpoint                      1       0
linear/none w=0.25       1.000    9.5%   1.000   21.9%   16.96 midpoint                      0       0
linear/none w=0.1        0.983   -6.9%   1.000   49.2%   none — tuning sets overlap        n/a     n/a

=== min_score on the inner ELSER arm ===
One call. The parent still returns the lexical arm's hits, so the refusal rule cannot be
'zero hits' — it is 'top score is at the single-arm ceiling 0.047619',
which is where a document ranked first by BM25 lands when nothing survives the gate.

set        class          n  refused  accept  reject  distinct scores   true ELSER range
tuning     off-topic     10       10       0                        1   1.56 – 16.11
tuning     in-domain      6        0               0                -   14.02 – 19.25
held-out   off-topic     20       20       0                        1   1.54 – 5.34
held-out   in-domain     10        0               0                -   13.28 – 19.18
```

## 8. Verify everything

```bash
uv run --extra es --extra mcp python scripts/verify.py
```

Read-only. It re-measures every record against the index, re-runs the suite, and reports whether
any published number moved. `--update` regenerates instead, which is the mode for the other side
of a reindex. See [`maintenance.md`](maintenance.md).
