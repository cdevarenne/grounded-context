---
type: model
title: Claude Sonnet 5
description: Anthropic's best combination of speed and intelligence.
resource: https://platform.claude.com/docs/en/about-claude/models/overview
tags: [anthropic, model, sonnet]

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
id: anthropic.claude-sonnet-5
provider: anthropic
links:
  - "[Anthropic Messages API](../endpoints/anthropic-messages.md)"
  - "[Claude Opus 5](anthropic-claude-opus-5.md)"

canonical:
  model_string: claude-sonnet-5
  api_alias: claude-sonnet-5
  context_window_tokens: 1000000
  max_output_tokens: 128000
  max_output_tokens_batch_api: 300000
  adaptive_thinking: true
  extended_thinking: false
  vision: true
  default_endpoint: /v1/messages
  input_price_per_mtok_usd: 3.0
  output_price_per_mtok_usd: 15.0
  introductory_input_price_per_mtok_usd: 2.0
  introductory_output_price_per_mtok_usd: 10.0
  introductory_pricing_ends: 2026-08-31
---

The speed/intelligence balance point in the current lineup.

**Why pricing needs four canonical fields, not one sentence.** Standard pricing is $3/$15 per
MTok, but introductory pricing of $2/$10 applies through 2026-08-31. Both are exact, both are
true, and which one applies depends on the date the question is asked. A prose summary has to
pick one and will be wrong half the time; four dated fields are correct on both sides of the
boundary.
