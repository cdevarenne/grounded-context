---
type: endpoint
title: Anthropic Messages API
description: The single endpoint through which Claude text generation, tool use, and structured output all flow.
resource: https://api.anthropic.com/v1/messages
tags: [anthropic, endpoint, messages]

sources:
  - resource: https://platform.claude.com/docs/en/get-started
    title: Get started with the Claude API
    author: Anthropic

generated:
  by: human:cdevarenne
  at: 2026-08-10T19:06:23-07:00

verified:
  - by: human:cdevarenne
    at: 2026-08-10T19:06:23-07:00

status: stable
stale_after: 2026-09-09

# --- local extensions ---
id: anthropic.messages
provider: anthropic
aliases:
  - messages api
  - messages endpoint
links:
  - "[Claude Opus 5](../models/anthropic-claude-opus-5.md)"
  - "[Claude Sonnet 5](../models/anthropic-claude-sonnet-5.md)"
  - "[Claude Haiku 4.5](../models/anthropic-claude-haiku-4-5.md)"

canonical:
  path: /v1/messages
  method: POST
  base_url: https://api.anthropic.com
  api_version_header: anthropic-version
  api_version: "2023-06-01"
  auth_header: x-api-key
---

Tools and output constraints are features of this one endpoint, not separate APIs.

`api_version` is quoted in the front matter deliberately: `2023-06-01` unquoted parses as a YAML
date, not the string the header requires. An exact fact that changes type on the way through the
parser is no longer exact.
