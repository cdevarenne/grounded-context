# Compatibility matrix

**Generated — do not edit.** Rendered from the model files in [`knowledge/`](../knowledge/)
by `scripts/build_matrix.py`. The model files are the source of truth; if this table and
a model file ever disagree, the model file wins. Regenerate with:

```bash
uv run python scripts/build_matrix.py
```

| Field | Claude Haiku 4.5 | Claude Opus 5 | Claude Sonnet 5 |
|---|---|---|---|
| `model_string`<br/>Model string (pinned) | claude-haiku-4-5-20251001 | claude-opus-5 | claude-sonnet-5 |
| `api_alias`<br/>API alias | claude-haiku-4-5 | claude-opus-5 | claude-sonnet-5 |
| `context_window_tokens`<br/>Context window (tokens) | 200,000 | 1,000,000 | 1,000,000 |
| `max_output_tokens`<br/>Max output (tokens) | 64,000 | 128,000 | 128,000 |
| `max_output_tokens_batch_api`<br/>Max output, Batch API | — | 300,000 | 300,000 |
| `vision`<br/>Vision | yes | yes | yes |
| `adaptive_thinking`<br/>Adaptive thinking | no | yes | yes |
| `extended_thinking`<br/>Extended thinking | yes | no | no |
| `input_price_per_mtok_usd`<br/>Input $/Mtok | 1.0 | 5.0 | 3.0 |
| `output_price_per_mtok_usd`<br/>Output $/Mtok | 5.0 | 25.0 | 15.0 |
| `introductory_input_price_per_mtok_usd`<br/>Introductory input $/Mtok | — | — | 2.0 |
| `introductory_output_price_per_mtok_usd`<br/>Introductory output $/Mtok | — | — | 10.0 |
| `introductory_pricing_ends`<br/>Introductory pricing ends | — | — | 2026-08-31 |
| `default_endpoint`<br/>Default endpoint | /v1/messages | /v1/messages | /v1/messages |

## Provenance

| Model | Trust tier | Verified | Stale after | Source |
|---|---|---|---|---|
| `anthropic.claude-haiku-4-5` | human-reviewed | 2026-08-10 | 2026-09-09 | [link](https://platform.claude.com/docs/en/about-claude/models/overview) |
| `anthropic.claude-opus-5` | human-reviewed | 2026-08-10 | 2026-09-09 | [link](https://platform.claude.com/docs/en/about-claude/models/overview) |
| `anthropic.claude-sonnet-5` | human-reviewed | 2026-08-10 | 2026-09-09 | [link](https://platform.claude.com/docs/en/about-claude/models/overview) |
