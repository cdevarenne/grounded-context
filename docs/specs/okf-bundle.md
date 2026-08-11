# Spec: Canonical Knowledge Bundle (OKF)

Builds on **[OKF — the Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)**
v0.2 (Google Cloud): *"a universal, vendor-neutral format for representing knowledge as plain
markdown files with YAML frontmatter."* This spec adds what v0.2 does not define —
**field-level provenance** for facts that must be exact: the `canonical:` block, per-file
`source_url` + `last_verified` inherited by every canonical field, and a staleness threshold.

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
  compatibility-matrix.md   # human-readable rollup (rendered VIEW, not source of truth)
```

## Concept file format
Every file = YAML front-matter + optional Markdown body.

Required front-matter:
```yaml
---
id: anthropic.claude-opus            # stable, unique, dotted
type: model | endpoint | concept
provider: anthropic | openai | elastic | null
title: Human Readable Title
source_url: https://...              # where the facts came from
last_verified: 2026-08-01            # ISO date — governance; MUST be refreshed
tags: [ ... ]
links:                               # explicit cross-references (deterministic traversal)
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
- **Every `canonical` field inherits provenance from its file's `source_url` + `last_verified`.**
  A field older than a set threshold (e.g., 30 days) should surface a staleness warning — a
  stale "authoritative" layer undercuts the whole thesis.
- **Links are ordinary Markdown links**, per the OKF v0.2 convention — *"normal markdown links,
  expressing relationships richer than the parent/child implied by the directory layout"* —
  written relative to the containing file. The deterministic path parses and traverses them for
  multi-hop lookups.
- **Never put an exact fact only in prose.** If it must be exact, it lives in `canonical:`.
- **`compatibility-matrix.md` is generated/maintained FROM the model files** — a human view,
  not the lookup source. If the matrix and a model file ever disagree, the model file wins.
- Do NOT commit large copyrighted doc text. The semantic corpus is fetched by a script into a
  small curated subset; this bundle holds only your own structured canonical data.

## Deterministic lookup contract
```
lookup(entity_id, field) -> {value, source_url, last_verified, file} | NOT_FOUND
```
Exact-fact queries resolve to a `canonical.<field>`; the result is the value plus full
provenance (see provenance.md).
