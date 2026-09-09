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
  at: 2026-08-10T19:06:23-07:00

verified:
  - by: human:cdevarenne
    at: 2026-09-09T00:11:31-07:00

status: stable
stale_after: 2026-11-30

# --- local extensions ---
id: anthropic.claude-sonnet-5
provider: anthropic
aliases:
  - sonnet 5
  - sonnet-5
  - claude sonnet 5
  - sonnet
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
  input_price_per_mtok_usd: 2.0
  output_price_per_mtok_usd: 10.0
---

The speed/intelligence balance point in the current lineup.

**The pricing fields changed on 2026-09-09, and the reason is the point.** This file first
carried four pricing fields: a standard price of $3/$15 per MTok, and an introductory price of
$2/$10 through 2026-08-31. The two were modeled separately because the correct answer depended
on the date of the question.

Anthropic then cancelled the increase. $2/$10 is now the standard price. The introductory fields
describe a period that no longer exists, so they are removed.

**Re-verification caught a wrong exact fact.** From 2026-09-01 the old data made the layer answer
$3 per MTok, with a citation, in the confident tone the deterministic path is built for. The
value was wrong for nine days. Nothing in the system could detect that, because the bundle was
internally consistent — only a human comparison against the live source finds an error of this
kind. That is what `stale_after` exists to schedule.
