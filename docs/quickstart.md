# Quickstart — clone to first grounded answer

From `git clone` to an answer with a citation block. **Part 1 needs no cloud account, no API key
and no network** — it is the deterministic spine, and it is the guaranteed deliverable. Part 2
adds the semantic half, which does need Elasticsearch. Part 3 puts the whole thing behind MCP so
an agent can call it.

Every output below is captured from a real run, not typed by hand — only the checkout path is
shortened.

The JVM port has its own quickstart with the same shape:
[grounded-context-jvm/docs/quickstart.md](https://github.com/cdevarenne/grounded-context-jvm/blob/main/docs/quickstart.md).

---

## Part 1 — The deterministic path (no cloud)

### 1. Install

Python **3.11 or newer**. The repo develops against 3.14 and locks with
[uv](https://docs.astral.sh/uv/), but the floor is deliberately lower so an older interpreter can
still run the demo.

```bash
git clone https://github.com/cdevarenne/grounded-context.git
cd grounded-context
uv sync --extra dev
```

No uv, and no wish to install one:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Both paths install exactly one runtime dependency — PyYAML. That is the zero-cloud-dependency
guarantee, and you can check it rather than take it on faith:

```console
$ uv run python -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies'])"
['PyYAML>=6']
```

Prefix every command below with `uv run`, or use `.venv/bin/gctx` if you took the venv path. With
nothing installed at all, `PYTHONPATH=src python3 -m grounded_context.cli …` works too.

### 2. Your first grounded answer

```console
$ uv run gctx lookup anthropic.claude-opus-5 context_window_tokens
Answer: 1,000,000

  ↳ source: anthropic.claude-opus-5 · canonical.context_window_tokens
    path: deterministic (exact-lookup) · human-reviewed 2026-08-10
    fresh until 2026-09-09
    https://platform.claude.com/docs/en/about-claude/models/overview
```

That block under the `↳` is the point of the project. Read it field by field:

| Line | What it commits to |
|---|---|
| `source:` | the entity and the exact canonical field the value came from |
| `path:` | which retrieval path answered — `deterministic` here, so nothing was ranked |
| `human-reviewed 2026-08-10` | the trust tier and the date a human verified it against the live doc |
| `fresh until 2026-09-09` | the governance date; after it, this citation prints `STALE` |
| the URL | the source a reader can open and check |

No answer is ever emitted without one. The full contract is
[`docs/specs/provenance.md`](specs/provenance.md).

### 3. See what is in the bundle

`lookup` needs an entity and a field. This is how you find out what exists:

```console
$ uv run gctx entities
anthropic.claude-haiku-4-5  [model]  human-reviewed
    canonical.adaptive_thinking
    canonical.api_alias
    canonical.context_window_tokens
    canonical.default_endpoint
    canonical.extended_thinking
    canonical.input_price_per_mtok_usd
    canonical.max_output_tokens
    canonical.model_string
    canonical.output_price_per_mtok_usd
    canonical.vision
anthropic.claude-opus-5  [model]  human-reviewed
    …
anthropic.messages  [endpoint]  human-reviewed
    canonical.api_version
    canonical.api_version_header
    canonical.auth_header
    canonical.base_url
    canonical.method
    canonical.path
```

Four concepts, hand-curated and date-stamped. Small on purpose: the canonical layer is the part
that must be exactly right, and something exactly right is something a person maintained. The
format is [OKF v0.2](specs/okf-bundle.md); the upkeep procedure is
[`docs/maintenance.md`](maintenance.md).

### 4. Watch a link get traversed

`method` is not a field on a model. It belongs to the endpoint the model points at, and the
lookup follows that link:

```console
$ uv run gctx lookup anthropic.claude-opus-5 method
Answer: POST

  ↳ source: anthropic.messages · canonical.method
    path: deterministic (exact-lookup) · human-reviewed 2026-08-10
    fresh until 2026-09-09
    traversed: anthropic.claude-opus-5 → anthropic.messages
    https://platform.claude.com/docs/en/get-started
```

The extra `traversed:` line is not decoration. One hop is one more place an answer could have
gone wrong, so the hop is in the audit trail.

### 5. Ask in plain English, and see the router decide

```console
$ uv run gctx ask "What is the exact context window of claude-opus-5?"
router: DETERMINISTIC — precision phrasing ("context window", "exact") plus a named model (claude-opus-5) — an exact fact must not be ranked

Answer: 1,000,000

  ↳ source: anthropic.claude-opus-5 · canonical.context_window_tokens
    path: deterministic (exact-lookup) · human-reviewed 2026-08-10
    fresh until 2026-09-09
    https://platform.claude.com/docs/en/about-claude/models/overview
```

The router states its rationale, not just its verdict — a routing decision you cannot read is a
decision you cannot audit. To see the decision without running the query:

```console
$ uv run gctx route "How should I chunk documents for retrieval?"
SEMANTIC — exploratory phrasing ("how should i", "should i"), no exact field requested
```

The rules are in [`docs/specs/router.md`](specs/router.md).

### 6. See a refusal — and the exit code

Ask for something the bundle does not hold:

```console
$ uv run gctx lookup anthropic.claude-opus-5 rate_limit_rpm
Answer: Not found in the grounded sources.

  ↳ no grounded source — nothing was returned rather than guessed.
$ echo $?
1
```

**Exit `1` is a refusal, not a failure.** Exit `2` is a real error, such as a malformed bundle. A
refusal is the correct result when nothing grounded exists, and it is the behavior the whole
design is protecting: the layer never falls back to a model's own memory.

### 7. See staleness arrive

Every canonical fact carries a `stale_after` date. Ask again as if that date had passed:

```console
$ uv run gctx --as-of 2026-10-01 lookup anthropic.claude-opus-5 context_window_tokens
Answer: 1,000,000

  ↳ source: anthropic.claude-opus-5 · canonical.context_window_tokens
    path: deterministic (exact-lookup) · human-reviewed 2026-08-10
    ⚠ STALE since 2026-09-09 — re-verify before relying on this
    https://platform.claude.com/docs/en/about-claude/models/overview
```

The value is still returned, and it is still flagged. An "authoritative" layer that goes quietly
out of date is worse than no authoritative layer.

### 8. Read back what the layer recorded about itself

Every answered query emits one telemetry event. The log is local ndjson, and the readback needs
no cloud — so this works right now, on the machine you just cloned onto:

```console
$ uv run gctx telemetry summary
gctx telemetry summary — /opt/devel/grounded-context/var/telemetry.ndjson
events: 5   window: 2026-08-19T21:32:10.883Z .. 2026-08-19T21:32:16.469Z

route mix        DETERMINISTIC 1 (20%)   SEMANTIC 0 (0%)   BOTH 0 (0%)   DIRECT 4 (80%)
canonical        hit 4   miss 1   n/a 0      miss rate 20% of 5 precision queries
refusals         1 (20%)
floor            cleared 0   blocked 0      (of 0 semantic-consulted)
floor scores     blocked n/a   cleared n/a
latency p50 ms   deterministic 0.2   semantic n/a   total 0.2
latency p95 ms   deterministic 0.4   semantic n/a   total 0.4
```

Those are the six commands above, counted: four direct `lookup` calls (un-routed, so `DIRECT`),
one routed `ask`, one refusal. `var/` is gitignored, so the log stays on your machine. The event
schema is [`docs/specs/observability.md`](specs/observability.md).

**You have now reached a citation block, a routing decision, a refusal, a staleness warning and a
telemetry readback without a cloud account.** Everything after this point is optional.

---

## Part 2 — The semantic path (needs Elasticsearch + ELSER)

The exploratory half runs BM25 and ELSER on Elasticsearch and fuses them with RRF. Four things
must exist before `gctx ask` can reach it, and the ordering is not optional.

### Prerequisites, in order

**1. An Elasticsearch endpoint with ELSER.** Elastic Cloud Serverless (project type
*Elasticsearch*) is what this was built on; `.elser-2-elasticsearch` is preconfigured there. A
self-managed cluster works too, but you deploy ELSER yourself and name your own inference
endpoint.

**2. Credentials in a gitignored `.env`.** The repo ships no `.env` and will not borrow one from a
sibling checkout.

```bash
cp .env.example .env      # then fill in ES_URL and ES_API_KEY
```

| Setting | |
|---|---|
| `ES_URL` · `ES_API_KEY` | required; never logged, never committed |
| `ES_INDEX` | the index every command reads and writes (default `grounded-context-corpus`) |
| `ES_INFERENCE_ID` | the ELSER endpoint the mapping is built against (default `.elser-2-elasticsearch`) |

`ES_INFERENCE_ID` is baked into the mapping when the index is created, so set it **before** step
4 — a wrong value is not a setting you correct later, it is an index you rebuild.

**3. The `es` extra.** It is an extra precisely so the deterministic path stays a PyYAML-only
install:

```bash
uv sync --extra dev --extra es
```

**4. A corpus on disk, then an index built from it.** The corpus is *not* committed — third-party
documentation prose does not belong in this repo. The manifest is committed, and the fetcher
reads it:

```bash
uv run python scripts/fetch_corpus.py              # 25 curated pages → corpus/raw/ (gitignored)
uv run --extra es python scripts/index_corpus.py --recreate
```

`fetch_corpus.py` follows no links and crawls nothing; it retrieves the URLs a human chose, with a
delay between requests. `index_corpus.py` builds the mapping the retrieval path depends on — the
`exact_token` analyzer, the `content.exact` subfield, and `semantic_text` wired to ELSER. The rule
it chunks by is [`docs/index-spec.md`](index-spec.md), so the index comes out the same whoever
builds it.

### Ask an exploratory question

```console
$ uv run --extra es gctx ask "How should I chunk documents for retrieval?"
router: SEMANTIC — exploratory phrasing ("how should i", "should i"), no exact field requested

Top passage: Generates embeddings during indexing: Automatically generates embeddings when you index documents, without requiring ingestion pipelines or inference processors.
Handles chunking: Automatically chunks long text documents during indexing.
…

  ↳ source: elastic-mapping-semantic-text · chunk:1
    path: semantic (hybrid(bm25+elser,rrf)) · indexed 2026-08-13 · score 0.0893
    https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/semantic-text

  ↳ source: elastic-inference-api · chunk:4
    path: semantic (hybrid(bm25+elser,rrf)) · indexed 2026-08-13 · score 0.0861
    https://www.elastic.co/docs/explore-analyze/elastic-inference/inference-api

  ↳ source: elastic-elser · chunk:12
    path: semantic (hybrid(bm25+elser,rrf)) · indexed 2026-08-13 · score 0.0688
    https://www.elastic.co/docs/explore-analyze/machine-learning/nlp/ml-nlp-elser
```

Same citation contract, different `path:` — `semantic` names the retrieval method and the fused
score instead of a trust tier and a review date, because that is what a ranked result can honestly
claim. Chunks are cited, not pages, so the unit you can check is the unit that was retrieved.

**Without credentials this returns `Not found in the grounded sources.`** rather than failing. An
unavailable engine is a refusal.

### Run the eval set

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

Q3 is reported as a known deviation rather than quietly passed. To compare the retrieval arms
against each other:

```bash
uv run --extra es gctx eval --compare rank_constant   # ELSER vs BM25 vs hybrid
```

What those comparisons showed — including a hypothesis the cluster contradicted — is
[`docs/findings.md`](findings.md), with the raw runs in [`docs/eval-output.md`](eval-output.md).

### Project the telemetry log into Elasticsearch

The ndjson log stays the source of truth; the index is a rebuildable projection, never the
reverse.

```console
$ uv run --extra es gctx telemetry index
projected 18 events from /opt/devel/grounded-context/var/telemetry.ndjson into grounded-context-telemetry
```

A six-panel Kibana dashboard reads that index and is exported to [`docs/kibana/`](kibana/);
importing it is [`docs/kibana-setup.md`](kibana-setup.md).

---

## Part 3 — From an agent, over MCP

The retrieval tool is exposed as an MCP server on stdio, so a model calls it instead of you.

```bash
uv sync --extra dev --extra mcp
uv run gctx-mcp        # serves on stdio; a client drives it
```

[`.mcp.json`](../.mcp.json) wires it up for Claude Code on clone. Three tools —
`lookup_canonical_fact`, `ask_grounded`, `list_entities` — carrying the same citation block the
CLI prints. The same command was driven from Claude **and** from Gemini via the Antigravity CLI
with no adapter and no code change; the transcript is
[`docs/AntigravityQandA.md`](AntigravityQandA.md).

Because it is stdio, **stdout is the JSON-RPC channel**. All logging goes to stderr.

---

## Running the tests

```bash
uv run pytest -q                                  # cluster and MCP tests skip without their extras
uv run pytest --junitxml=var/test-results.xml     # machine-readable report
uv run --extra report pytest --html=var/test-report.html --self-contained-html
```

Tests that need Elasticsearch skip without credentials, and tests pinning reference-corpus numbers
skip on any other index. A run with parts skipped is the expected outcome of a partial setup, not
a broken clone — the report says which ran and which did not.

---

## When something does not work

| What you see | What it means |
|---|---|
| `Not found in the grounded sources.` on an exact fact | The field is not in `knowledge/`. Run `gctx entities` to see what is. This is the design working. |
| `Not found in the grounded sources.` on an exploratory question | No Elasticsearch reachable, or an empty index. Check `.env`, then re-run `index_corpus.py`. |
| Exit code `1` | A grounded refusal. Not an error. |
| Exit code `2` | A real error — a malformed bundle, or a path that does not resolve. |
| `⚠ STALE` in a citation | The `stale_after` date has passed. Follow [`docs/maintenance.md`](maintenance.md); do not edit the date to silence it. |
| `gctx: command not found` | The install did not put the console script on PATH. Use `uv run gctx …`, or `.venv/bin/gctx`. |
| ELSER errors on a self-managed cluster | `ES_INFERENCE_ID` still points at the Serverless default. Set it to your own endpoint and rebuild the index. |

---

## Where to go next

- [`docs/design.md`](design.md) — why it is built this way: the five design properties, the
  governance split, and the central tradeoff.
- [`docs/findings.md`](findings.md) — three things that surfaced while building the hybrid path.
- [`docs/specs/`](specs/) — the contracts the implementation follows.
- [`README.md`](../README.md#out-of-scope) — what this deliberately is not.
