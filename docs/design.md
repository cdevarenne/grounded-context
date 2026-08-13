# Design

Why the [grounded context layer](../README.md) is built the way it is: the properties every
decision serves, how it grounds on OKF, why the two corpora are governed differently, the
tradeoff the design accepts, and the plan for proving whether it holds at scale.

## Design north star — five properties

Every decision in this repo serves these, in preference to cleverness or scope:

| Property | How it shows up here |
|---|---|
| **Useful** | Answers real questions about real API docs, not a toy corpus |
| **Secure** | Read-only; no credentials in the retrieval path; provenance on every claim |
| **Repeatable** | Same query, same route, same citations — the deterministic path is pure functions over Markdown |
| **Composable** | One MCP tool, consumed unchanged by two agent runtimes — Claude and Gemini |
| **Deterministic where it matters** | Exact facts never touch a ranking function |

## The two paths

**Deterministic path.** A curated `knowledge/` bundle of structured Markdown — YAML
front-matter carrying a `canonical:` block of fields that must never be guessed, plus ordinary
Markdown links for multi-hop traversal. Lookup is an exact match on a field, returning the value
with its OKF provenance — `sources`, trust tier, `stale_after`. Pure Python over Markdown, no
network, no ranking, no embedding. **Markdown is the source of truth; any index is a rebuildable
projection of it — never the reverse.**

**Semantic path.** The same questions that don't have one exact answer go to Elasticsearch:
BM25 for lexical precision and ELSER for learned-sparse semantics, combined with reciprocal rank
fusion. Returns document, score, and snippet.

**Router.** Rule-based to start, with an interface that lets an LLM classifier drop in later
without changing callers. Queries naming an entity and asking for a field go deterministic;
open-ended questions go semantic; **on ambiguity it queries both and lets the exact hit win.**
The router's decision and its rationale are part of the audit trail, not just an internal
detail.

## Built on OKF

The canonical layer conforms to
**[OKF, the Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)**
(v0.2, published by Google Cloud) — *"a universal, vendor-neutral format for representing
knowledge as plain markdown files with YAML frontmatter."* A *knowledge bundle* is a directory
of those files.

Vendor-neutrality is the point, and it's why OKF belongs **alongside** an Elasticsearch
architecture rather than competing with one. The canonical layer stays portable and
inspectable; Elasticsearch's job is to be the best index, projection, and observability surface
over it — not to own the format. Markdown remains the source of truth and the index is
rebuildable from it.

**Provenance and freshness come from the format, not from this repo.** OKF v0.2 makes *"trust,
provenance, and freshness… first-class"* and standardizes precisely what a grounded layer needs:

| OKF field | What it carries |
|---|---|
| `sources` | Where a fact came from, with per-source credibility signals |
| `generated` | How the content was produced — actor and timestamp |
| `verified` | Verification events, from which a **trust tier** is derived: unverified · machine-confirmed · human-reviewed |
| `status` | `draft` · `stable` · `deprecated` |
| `stale_after` | Absolute date — a concept is stale when `today >= stale_after` |

Those flow straight into the citation block, which is why the provenance contract here is
thinner than it would otherwise have to be. An earlier draft of this repo invented its own
`source_url` and `last_verified` fields; aligning to OKF's was a strict improvement, and the
`stale_after` absolute date is a better design than the relative TTL it replaced.

**What this repo adds is the retrieval half, which OKF deliberately leaves open.** The spec is
*"minimally opinionated, freely extensible"* — it defines no canonical field values and no
retrieval contract, since bundles *"can be consumed by anything that reads markdown."* So
[`specs/okf-bundle.md`](specs/okf-bundle.md) supplies:

- a **`canonical:` block** — exact-fact values that must never be inferred (model string, context
  window, endpoint path)
- a deterministic **`lookup(entity, field)` contract** returning the value plus its inherited OKF
  provenance, trust tier, and staleness state
- the **router** that decides when a question deserves that path at all, plus the uniform citation
  contract shared with the semantic path

## Two bodies of content, two different rules

The two retrieval paths read from two different corpora, and the split is deliberate — they
carry different governance obligations, so conflating them would be a licensing problem as much
as an architectural one:

| | `knowledge/` — canonical layer | `corpus/` — semantic layer |
|---|---|---|
| Holds | My own structured facts: exact values with sources and verification dates | Third-party documentation prose |
| Produced by | Hand-curation, one concept per file | A fetch script, from public docs |
| In git? | **Yes** — it *is* the source of truth | **No** — `corpus/raw/` is gitignored; the script is what ships |
| Governed by | Every exact fact carries `sources`, `verified`, `stale_after` | Never scrape whole sites; never commit copyrighted text |
| Size driver | Small because each fact is hand-verified | ~30–60 pages because curation is the scope lever |
| Status | Built — 4 concepts | Planned |

This is why the canonical layer is small and the semantic corpus is fetched rather than
vendored: one is mine to govern, the other isn't mine to redistribute.

## Provenance is mandatory

No answer is emitted without at least one citation. If retrieval returns nothing, the answer is
**"Not found in the grounded sources"** — never a fallback to model memory.

Both paths emit the *same* citation structure, which is what makes a dual engine read as one
auditable system:

```
Answer: <answer text>

  ↳ source: <entity id> · <canonical field>
    path: deterministic (exact-lookup) · verified <ISO date>
    <source url>
```

```
  ↳ source: <document id> · section: <heading>
    path: semantic (hybrid bm25+elser, rrf) · score <float>
    <source url>
```

Format is illustrative — real values come from the date-stamped canonical files and the live
index. The canonical layer is **governed**: OKF's `verified` events yield a trust tier, and once
`today >= stale_after` the citation carries a staleness warning — because a stale
"authoritative" layer undercuts the whole point.

The exact citation-block shape is pinned in [`specs/provenance.md`](specs/provenance.md).

## The tradeoff, and the open question

The deterministic path does not eliminate the data-preparation problem. It **relocates** it —
from chunking strategy and embedding drift to **curation governance.** Someone has to decide
which facts are canonical and keep them fresh. That cost is real, and it's the honest price of
determinism.

It's the right trade for facts where a confident wrong answer is worse than no answer. But it
raises the question this project exists to answer:

> **Does curation scale?** A 30–60 page corpus is hand-curatable by one person. At ten thousand
> documents, is a canonical layer still viable — or does it become the bottleneck that makes the
> whole approach un-shippable in production?

That's an open question here, not a settled one. I'd rather instrument it than have an opinion
about it.

## Observability — how the open question gets answered

The retrieval layer emits its own telemetry into Elasticsearch, the same platform serving the
semantic path:

| Signal | What it tells you |
|---|---|
| Router decisions + rationale | What fraction of real queries actually need the deterministic path |
| Canonical hit / miss rate | How often a precision query finds no canonical field — the curation backlog, measured |
| Concepts past `stale_after` | Whether governance is keeping up or falling behind |
| Trust-tier distribution | What share of the corpus is human-reviewed vs machine-confirmed vs unverified |
| Refusal rate | How often "not found in the grounded sources" fires |
| Per-path latency | What routing to `BOTH` actually costs |

Those six signals turn "does curation scale?" from an argument into a dashboard, and they make
the context layer *itself* observable. Lessons from that instrumentation are what should decide
whether this approach is production-worthy, and what the alternatives are if it isn't.
