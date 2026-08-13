# Captured output

The numbers in [`findings.md`](findings.md) come from a live index, which a reader cannot
reach: the corpus is fetched rather than committed, and the cluster is mine. This file is the
run, captured verbatim, so the claims are checkable without either.

Captured 2026-08-13 against Elastic Cloud Serverless 9.6, index `grounded-context-corpus`,
320 chunks, ELSER via the preconfigured `.elser-2-elasticsearch` endpoint.

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

Q3 KNOWN — eval.md expects a deterministic list. Lookup answers one entity at a time, so a cross-model rollup has no engine and falls through to semantic passages that do not really answer it. docs/compatibility-matrix.md is what answers this today.

11 pass · 1 known deviation · 0 fail
```

Q3 is a declared deviation, not a pass: a multi-entity rollup ("which models support vision?")
has no engine on the deterministic path, which answers one entity at a time. The harness reports
it as `KNOWN` rather than letting it read as green.

## Neither arm wins both phrasings

Finding 1 in [`findings.md`](findings.md). Both runs rank the same target — the one chunk that
defines `rank_constant` — under each retrieval arm.

```console
$ uv run --extra es gctx eval --compare "What does the rank_constant parameter do?"
query: 'What does the rank_constant parameter do?'
rank of the chunk that defines the term, per retrieval arm:

  elser    rank 2
  bm25     rank 3
  hybrid   rank 1
```

```console
$ uv run --extra es gctx eval --compare "rank_constant"
query: 'rank_constant'
rank of the chunk that defines the term, per retrieval arm:

  elser    rank 5
  bm25     rank 1
  hybrid   rank 1
```

ELSER drops to rank 5 on the bare identifier; BM25 drops to rank 3 on the sentence. Fusion holds
rank 1 on both.

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
rank_constant      content=['rank_constant']              content.exact=['rank_constant']
num_candidates     content=['num_candidates']             content.exact=['num_candidates']
claude-opus-5      content=['claude', 'opus', '5']        content.exact=['claude-opus-5']
claude-haiku-4-5   content=['claude', 'haiku', '4', '5']  content.exact=['claude-haiku-4-5']
```

## Fused score versus pre-fusion score

Finding 3. The fused RRF score cannot separate an answerable question from an unanswerable one;
the pre-fusion sparse score can. `sparse` is what `RELEVANCE_FLOOR` reads.

| Question | Kind | Fused (RRF) | Pre-fusion (ELSER) |
|---|---|---|---|
| How do I bake sourdough bread? | off-topic | 0.0678 | 2.61 |
| What is the capital of France? | off-topic | 0.0680 | 4.52 |
| Who won the 1998 World Cup? | off-topic | — | 1.66 |
| How do I change a flat tire on a bicycle? | off-topic | — | 5.90 |
| What is the plot of Hamlet? | off-topic | — | 5.06 |
| How do I stream responses from the API? | genuine | 0.0729 | 16.79 |
| How should I chunk documents for retrieval? | genuine | 0.0893 | 17.66 |
| What is reciprocal rank fusion? | genuine | 0.0931 | 19.48 |
| How do I use `semantic_text`? | genuine | — | 14.10 |
| What is the price per million tokens of GPT-5? | wrong entity | 0.0893 | 19.39 |
| What is the best way to train for a marathon? | off-topic by subject | 0.0725 | 16.14 |

The fused scores for sourdough (0.0678) and streaming (0.0729) are the pair quoted in the
findings. The last two rows are the floor's declared limits: a wrong-entity question scores as
high as a right one, and the marathon question clears the floor because Elastic's
`semantic_text` page teaches the feature with running and exercise sample documents — the
retrieval is correct, only the subject is a surprise.
