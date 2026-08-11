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

## Rendered form (shown under each answer in the notebook/UI)
Deterministic:
```
Answer: The context window is 200,000 tokens.

  ↳ source: anthropic.claude-opus · canonical.context_window_tokens
    path: deterministic (exact-lookup) · human-reviewed 2026-08-01 · fresh until 2026-09-01
    https://docs.anthropic.com/…
```
When `today >= stale_after`, the same block carries the warning inline:
```
    path: deterministic (exact-lookup) · human-reviewed 2026-08-01 · ⚠ STALE since 2026-09-01
```
Semantic (show score + method):
```
  ↳ source: elastic-hybrid-search-guide · section: Reciprocal rank fusion
    path: semantic (hybrid bm25+elser, rrf) · score 0.87
    https://www.elastic.co/…
```

## Notes
- Keep the block compact and **identical in structure across both paths** — the consistency is
  the point; it's what makes the dual engine read as one auditable system.
- The router's decision (which path, and why) is also part of the audit trail — log/surface it
  alongside the citations (see router.md), not just the retrieval result.
- This contract is the concrete, on-screen version of Elastic's "every step is auditable"
  agentic-SOC language and Hemant's "grounded / responsible AI controls" framing.
