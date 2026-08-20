# Spec: Query Router (Dual-Path)

## Decision
```
route(query) -> {route: DETERMINISTIC | SEMANTIC | BOTH, rationale: str}
```

- **DETERMINISTIC** — the query names an exact entity and asks for an exact field.
  Signals: contains a model id/string, or terms like *context window, max tokens, endpoint,
  rate limit, version, exact, "how many", "what is the value of"*.
- **SEMANTIC** — exploratory / explanatory.
  Signals: *"how do I", "best way", "explain", "difference between", "recommended", "why"*, or
  otherwise open-ended.
- **BOTH** — ambiguous, or a cross-entity comparison. Query both paths; prefer a deterministic
  exact hit when present, else fall back to the semantic result. Never drop provenance on merge.

### The precision exception to the BOTH fallback

A **cross-entity comparison** is a precision question by construction: *"is X cheaper than Y"*
asks for two exact values, so a ranked passage is never a correct answer to it. When the router
routes to BOTH *for that reason* and the deterministic path finds nothing, the answer is the
**refusal**, not the semantic result.

This is the one case where the fallback is switched off, and it is switched off deliberately.
Falling back there produces exactly the failure this project exists to prevent — a plausible,
cited, adjacent passage answering a question that had a right answer the bundle simply did not
hold. A deterministic miss on a precision query is a **curation gap**, and the honest report of a
curation gap is a refusal, which the telemetry then counts as a canonical miss.

The signal is carried on `Route.precision`, which is **not serialized**: the envelope's `router`
block stays `{route, rationale}`, an unchanged published contract. Ambiguous BOTH — mixed
precision and exploratory phrasing — is *not* marked precision, and still falls back, because
those queries may genuinely be exploratory.

**Known limitation, not solved by this rule.** When the deterministic path *does* resolve a
comparison, it answers for one entity, because `lookup` resolves a single entity and field. So
"compare A and B on max output" returns B's value with B's citation — auditable, but not the
comparison that was asked for. Multi-entity rollup has no engine; it is the eval's declared Q3
deviation and is tracked separately. The rule above makes the miss case honest. It does not make
the hit case complete.

## Implementation
- v1: a single small rule/keyword function with the signal tables above (keyword + light regex).
- The interface must let an LLM classifier drop in later without changing callers.
- **Always emit `rationale`** — the router's choice is part of the audit trail (provenance.md).
- Fallback: a documented manual override (e.g., `route=DETERMINISTIC`) for the demo.

## Default on uncertainty
**BOTH.** It's safe, and it visibly demonstrates the dual engine — which is the pitch. Better to
show both paths and let the exact hit win than to mis-route a precision question to semantics.
