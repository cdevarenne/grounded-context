# Spec: Canonical Knowledge Bundle (OKF)

Conforms to **[OKF — the Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)**
v0.2 (Google Cloud): *"a universal, vendor-neutral format for representing knowledge as plain
markdown files with YAML frontmatter."*

**Provenance and freshness come from OKF, not from here.** v0.2 makes *"trust, provenance, and
freshness… first-class"* and already standardizes exactly what this layer needs: `sources` with
per-source credibility signals, `generated` and `verified` (from which consumers *"derive a trust
tier"*), and `status` + `stale_after` for lifecycle. Use those field names and semantics — do not
invent local equivalents.

What this spec adds is **not format**: OKF is deliberately *"minimally opinionated"* and defines
no canonical field values and no retrieval contract — bundles *"can be consumed by anything that
reads markdown."* This spec supplies the missing retrieval half: a `canonical:` block of
exact-fact values and a deterministic `lookup()` contract over it.

## Purpose
The deterministic layer's source of truth. Structured Markdown + YAML files that the
deterministic path reads for exact, provenance-carrying answers. Markdown is the source of
truth; any index (Elasticsearch) is a rebuildable projection of it — never the reverse.

## Layout
```
knowledge/
  models/        # one file per model (exact facts)
  endpoints/     # one file per API endpoint
  concepts/      # prose concepts (definitions, explanations)
```
The compatibility matrix is a **rendered view, not source of truth**, so it lives at
`docs/compatibility-matrix.md` rather than inside the bundle: everything under `knowledge/` is
loaded and addressable as a concept, and a generated rollup indexed alongside the facts it
summarizes would be a second, lower-quality copy of them. It is built by
`scripts/build_matrix.py`, and a test regenerates it to fail on drift.

## Concept file format
Every file = YAML front-matter + optional Markdown body.

Front-matter — OKF v0.2 fields, plus two local extensions (v0.2 is *"freely extensible"*):
```yaml
---
type: model                          # OKF: the only always-required key
title: Claude Opus                   # OKF recommended
description: One-sentence summary.   # OKF recommended
resource: https://...                # OKF: URI identifying the underlying asset
tags: [anthropic, model]             # OKF recommended

sources:                             # OKF provenance family
  - resource: https://docs.anthropic.com/…
    title: Anthropic model reference
    author: Anthropic
    last_modified: 2026-08-01        # recency signal (YYYY-MM-DD)

generated:                           # OKF trust family — how this content was produced
  by: human:cdevarenne               # REQUIRED within generated
  at: 2026-08-01T12:00:00Z

verified:                            # OKF trust family — list of verification events
  - by: human:cdevarenne
    at: 2026-08-01T12:00:00Z

status: stable                       # OKF lifecycle: draft | stable | deprecated
stale_after: 2026-09-01              # OKF lifecycle: absolute date, NOT a relative TTL

# --- local extensions ---
id: anthropic.claude-opus            # stable lookup key for the deterministic path
provider: anthropic                  # anthropic | openai | elastic
aliases:                             # names a person might use in a question
  - messages api                     # without these, "Anthropic's Messages API" resolves
  - messages endpoint                # to nothing and a known fact reads as absent
links:                               # ordinary Markdown links, traversed for multi-hop lookups
  - "[Anthropic Messages API](../endpoints/anthropic-messages.md)"
---
```

For `type: model` (and any exact-fact file), add a `canonical:` block — the fields that must
never be guessed:
```yaml
canonical:
  model_string: claude-opus-...      # EXAMPLE — fill from live docs, do NOT trust these values
  context_window_tokens: 200000      # EXAMPLE
  max_output_tokens: 0               # EXAMPLE
  modalities: [text, vision]         # EXAMPLE
  default_endpoint: "/v1/messages"
```

## Rules
- **Every `canonical` field inherits its document's OKF provenance** — `sources`, `generated`,
  `verified`, `status`, `stale_after`. Provenance in OKF is per-document; a file with six
  canonical fields carries one provenance record covering all six. Keep exact facts in
  small-grained files so that record stays meaningful.
- **Staleness is OKF's rule, not a local one:** a concept is stale when `today >= stale_after`.
  Absolute date, not a relative TTL — per the spec, it *"keeps the staleness decision a plain date
  comparison with no reference to when the concept was read."* A stale hit still answers, but the
  citation block must carry the staleness warning; a stale "authoritative" layer undercuts the
  whole thesis.
- **Trust tier is derived, not declared** — OKF's tiers: no `verified` key → *unverified*;
  `verified` by non-`human:` actors only → *machine-confirmed*; a `human:<id>` actor →
  *human-reviewed*. Surface the tier in the citation block.
- **Links are ordinary Markdown links**, per the OKF v0.2 convention — *"normal markdown links,
  expressing relationships richer than the parent/child implied by the directory layout"* —
  written relative to the containing file. The deterministic path parses and traverses them for
  multi-hop lookups.
- **Never put an exact fact only in prose.** If it must be exact, it lives in `canonical:`.
- **`docs/compatibility-matrix.md` is generated FROM the model files** — a human view, not the
  lookup source. If the matrix and a model file ever disagree, the model file wins, and the
  drift test enforces exactly that.
- Do NOT commit large copyrighted doc text. The semantic corpus is fetched by a script into a
  small curated subset; this bundle holds only your own structured canonical data.

## Deterministic lookup contract
This is the part OKF deliberately leaves open — the format defines no retrieval contract.

```
lookup(entity_id, field) -> {
  value,
  sources,          # OKF sources[] — origin + credibility signals
  verified,         # OKF verified[] — verification events
  trust_tier,       # derived: unverified | machine-confirmed | human-reviewed
  status,           # OKF: draft | stable | deprecated
  stale_after,      # OKF absolute date
  is_stale,         # today >= stale_after
  file
} | NOT_FOUND
```
Exact-fact queries resolve to a `canonical.<field>`; the result is the value plus its inherited
OKF provenance (see provenance.md).
