# CLAUDE.md — Grounded Context Layer for Enterprise Agents

## What this is
A portfolio artifact: a grounded, composable, deterministic-where-it-matters context layer
for enterprise agents — on Elasticsearch, reached via MCP, model-agnostic. 
v1 must be a genuine, public, first-person build. 
It later matures into a reference-architecture presentation.

## Thesis
Elasticsearch is the authoritative, auditable context layer that makes an LLM agent
trustworthy in the enterprise — not just a vector store, but the grounding substrate that
makes the agent's reasoning explainable and verifiable. A deterministic canonical path
handles facts that must be exact; a semantic (hybrid) path handles exploration; every
answer carries provenance.

## Design north star — five properties
Every decision serves: **useful, secure, repeatable, composable, deterministic-where-it-matters.**
When trading off, prefer the option that strengthens these over cleverness or scope.

## Architecture (in words)
User → agent (Claude or Antigravity) → MCP retrieval tool → router →
{ deterministic canonical path  |  Elasticsearch hybrid (BM25 + ELSER, RRF) } →
grounded answer with a citation block.
- Router: canonical/precision → deterministic; exploratory → hybrid.
- Agent Builder and Workflows/SOAR are DESCRIBED, not built — the demo is the hand-rolled
  version of what Agent Builder does natively.

## Hard constraints (do not violate)
- **IP hygiene:** personal account only. Public or synthetic content only.
- **Corpus:** a curated ~30–60 page hand-picked subset of Elastic + Anthropic + OpenAI
  dev/API docs. Do NOT scrape whole sites. Do NOT commit copyrighted doc text — ship a
  fetch/index script plus a small curated subset.
- **Scope (v1):** read-only. No auth, no scale, no multi-tenant. State what's deliberately
  out of scope in the README.
- **Honesty:** this repo is real and public; the broader flagship is direction-not-shipped.
  Don't let the README or blog overclaim.

## Conventions (these differ from defaults — follow them)
- **Deterministic-first:** build the canonical path + MCP + provenance BEFORE Elasticsearch.
  The deterministic path must run with zero cloud dependency (pure Python + markdown) — it is
  the guaranteed deliverable.
- **Provenance is mandatory:** no answer without a citation block (source, section, retrieval
  path [deterministic|semantic], score/method). This is the visual signature of the thesis.
- **Determinism where it matters:** never let a probabilistic path answer an exact fact
  (model ID, context window, endpoint param). Those route to the canonical path.
- **Canonical data is governed:** the compatibility matrix is date-stamped and sourced from
  live docs; treat it as versioned truth. A stale "authoritative" layer undercuts the thesis.
- **Small + composable:** small scripts, clear interfaces, always demoable.

## Toolchain
Python **3.14**, pinned in `.python-version`; managed with **uv**. `uv sync --extra dev`,
then `uv run pytest` / `uv run gctx …`. `uv.lock` is committed: the interpreter and the exact
dependency set are properties of the repo, not of the shell.
- Keep the plain `python3.14 -m venv` + `pip install -e ".[dev]"` path working and documented
  in the README. This is a public artifact — nobody should need uv installed to run the demo.
- New dependencies that aren't required by the deterministic path go in an **extra**, not in
  `[project] dependencies`. The zero-cloud-dependency guarantee above is a promise about what
  a bare install pulls in.

## Deployment
Elastic Cloud Serverless, Elasticsearch project type, closest US-West region (unchangeable
after creation), ELSER for semantic. Fallback: Docker single-node + a local embedding.

## Models
Agnostic via MCP: Claude (primary) + Antigravity both consume the same MCP tool. OpenAI is a
stage-3 addition via a metered API key — not needed for v1.

## Source of truth
- Task list: `backlog.csv` — authoritative; work top-to-bottom by id.
- Detailed specs live in `docs/specs/` and are read on demand:
  - `okf-bundle.md` — canonical bundle format (OKF v0.2: YAML front-matter, Markdown-link convention) + compatibility-matrix schema
  - `provenance.md` — the exact citation-block shape
  - `router.md` — classification rules + interface
  - `eval.md` — the ~12-question eval set + expected engine per question
  - `observability.md` — the per-query telemetry event, its emit sites, and the three
    non-negotiables (emitted after the answer, best-effort, never blocks)
  - `observability-corpus-state.md` — the bundle-governance snapshot: the other two signals
- Keeping the canonical layer current: `docs/maintenance.md` — re-verification, `stale_after`,
  corpus refresh, and which checks are automated.
