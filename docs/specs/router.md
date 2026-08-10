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

## Implementation
- v1: a single small rule/keyword function with the signal tables above (keyword + light regex).
- The interface must let an LLM classifier drop in later without changing callers.
- **Always emit `rationale`** — the router's choice is part of the audit trail (provenance.md).
- Fallback: a documented manual override (e.g., `route=DETERMINISTIC`) for the demo.

## Default on uncertainty
**BOTH.** It's safe, and it visibly demonstrates the dual engine — which is the pitch. Better to
show both paths and let the exact hit win than to mis-route a precision question to semantics.
