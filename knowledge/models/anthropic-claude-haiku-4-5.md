---
type: model
title: Claude Haiku 4.5
description: Anthropic's fastest model, with near-frontier intelligence.
resource: https://platform.claude.com/docs/en/about-claude/models/overview
tags: [anthropic, model, haiku]

sources:
  - resource: https://platform.claude.com/docs/en/about-claude/models/overview
    title: Models overview
    author: Anthropic

generated:
  by: human:cdevarenne
  at: 2026-08-11T02:06:23Z

verified:
  - by: human:cdevarenne
    at: 2026-08-11T02:06:23Z

status: stable
stale_after: 2026-09-10

# --- local extensions ---
id: anthropic.claude-haiku-4-5
provider: anthropic
links:
  - "[Anthropic Messages API](../endpoints/anthropic-messages.md)"

canonical:
  model_string: claude-haiku-4-5-20251001
  api_alias: claude-haiku-4-5
  context_window_tokens: 200000
  max_output_tokens: 64000
  adaptive_thinking: false
  extended_thinking: true
  vision: true
  default_endpoint: /v1/messages
  input_price_per_mtok_usd: 1.0
  output_price_per_mtok_usd: 5.0
---

The fastest model in the current lineup, and the one that differs most from its siblings.

**Two exact facts a ranked answer tends to blur.** First, `model_string`
(`claude-haiku-4-5-20251001`) and `api_alias` (`claude-haiku-4-5`) are different strings and only
one of them is the pinned snapshot ID — a retrieval that returns "the model ID" without saying
which is not actually answering the question. Second, this model inverts its siblings on
thinking: `adaptive_thinking` is **false** and `extended_thinking` is **true**, the opposite of
Opus 5 and Sonnet 5. Any generalization across the family is wrong here, which is why the fields
are per-model rather than described once in prose.

No Message Batches 300k-output entry: this model is not listed as supporting it, so the field is
absent rather than guessed.
