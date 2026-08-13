# Spec: Grounded-Answer & Provenance Contract

## Rule (non-negotiable)
No answer is emitted without at least one citation. If retrieval returns nothing, the agent
replies **"Not found in the grounded sources"** — it does NOT answer from model memory. This
is the auditability guarantee and the visual signature of the whole artifact.

## Structured form (returned by every retrieval + attached to every answer)
```json
{
  "answer": "…",
  "retrieval_path": "deterministic | semantic | mixed",
  "citations": [
    {
      "path": "deterministic | semantic",
      "source_id": "anthropic.claude-opus | <es_doc_id>",
      "source_url": "https://…",   // deterministic: from OKF sources[].resource
      "locator": "canonical.context_window_tokens | section:Hybrid search | chunk:12",
      "method": "exact-lookup | hybrid(bm25+elser,rrf)",
      "score": null,          // null for exact deterministic; float for semantic
      "verified_at": "2026-08-01 | <index_time>",   // OKF verified[].at (latest)
      "trust_tier": "human-reviewed",               // OKF-derived; null on the semantic path
      "stale_after": "2026-09-01",                  // OKF lifecycle; null on the semantic path
      "is_stale": false,                            // today >= stale_after
      "snippet": "…"
    }
  ]
}
```

## Rendered form (shown under each answer)

These blocks are **captured output, not illustrations** — each is what the command above it
prints today. A spec that shows a shape the code does not emit is worse than no spec.

Deterministic (`gctx lookup anthropic.claude-opus-5 context_window_tokens`):
```
Answer: 1,000,000

  ↳ source: anthropic.claude-opus-5 · canonical.context_window_tokens
    path: deterministic (exact-lookup) · human-reviewed 2026-08-10
    fresh until 2026-09-09
    https://platform.claude.com/docs/en/about-claude/models/overview
```
When `today >= stale_after`, the freshness line becomes the warning
(`gctx --as-of 2026-10-01 lookup …`):
```
    ⚠ STALE since 2026-09-09 — re-verify before relying on this
```
Semantic — score and method (`gctx ask "What is reciprocal rank fusion?"`):
```
  ↳ source: elastic-rrf · chunk:3
    path: semantic (hybrid(bm25+elser,rrf)) · indexed 2026-08-13 · score 0.0889
    https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
```
The score is a *fused* RRF score, so it is small and it is not a similarity. RRF sums
`1/(k + rank)` across arms, which puts a top hit near `2/(k+1)` — about 0.09 at `k = 20`,
whatever the match quality. Displaying it is honest about what ranked the passage; reading it
as confidence is the mistake [`findings.md`](../findings.md) documents.

A fetched page carries no OKF lifecycle, so the semantic line shows `indexed <date>` where the
deterministic line shows a trust tier and a freshness date. That asymmetry is deliberate:
claiming a trust tier for a scraped page would be a lie.

## Notes
- Keep the block compact and **identical in structure across both paths** — the consistency is
  the point; it's what makes the dual engine read as one auditable system.
- The router's decision (which path, and why) is also part of the audit trail — log/surface it
  alongside the citations (see router.md), not just the retrieval result.
- This contract is the concrete, on-screen version of Elastic's "every step is auditable"
  agentic-SOC language and Hemant's "grounded / responsible AI controls" framing.
