---
type: model
title: Claude Opus 5
description: Anthropic's model for complex agentic coding and enterprise work.
resource: https://platform.claude.com/docs/en/about-claude/models/overview
tags: [anthropic, model, opus]

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
id: anthropic.claude-opus-5
provider: anthropic
links:
  - "[Anthropic Messages API](../endpoints/anthropic-messages.md)"
  - "[Claude Sonnet 5](anthropic-claude-sonnet-5.md)"

canonical:
  model_string: claude-opus-5
  api_alias: claude-opus-5
  context_window_tokens: 1000000
  max_output_tokens: 128000
  max_output_tokens_batch_api: 300000
  adaptive_thinking: true
  extended_thinking: false
  vision: true
  default_endpoint: /v1/messages
  input_price_per_mtok_usd: 5.0
  output_price_per_mtok_usd: 25.0
---

Recommended starting point for complex agentic coding and enterprise work.

**Scope notes on the canonical values.** `max_output_tokens` (128000) applies to the
*synchronous* Messages API. `max_output_tokens_batch_api` (300000) applies only on the Message
Batches API and requires the `output-300k-2026-03-24` beta header — the two are separate facts,
which is exactly why they are separate canonical fields rather than one number in prose.

`adaptive_thinking` is `true` and on by default; `extended_thinking`
(`thinking.type: "enabled"`) is not supported on this model.
