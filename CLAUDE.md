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
- **Reproduction and results are different documents.** A page that teaches someone to re-run a
  measurement wants example output; a published figure wants a machine-readable record. One file
  being both is what made a documented rank recoverable only by regular expression from console
  text. `docs/eval-how-to.md` is the instructions, with illustrative output nothing asserts.
  `docs/data/*.json` is the result. No script writes a `.txt` artifact, and no test parses a
  console line.
- **Published numbers are generated, never typed.** Every figure quoted in `findings.md` and
  `docs/specs/single-call-retrieval.md` lives in a record under `docs/data/`, and is referenced
  inline: `<!--fig:probes.heldout.auc-->1.000<!--/-->`. `measurements.json` resolves at the root
  because that is what the committed markers say; every other record hangs under its file name —
  `<!--fig:arms.rows.rank_constant.token.hybrid-->`. Inside a `<!--figures:on-->` region **no
  unmarked decimal is allowed** — `tests/test_figures.py` fails on one — so a new number cannot be
  added without being checked. A decimal that is not a measurement is declared
  `<!--lit-->9.6.0<!--/-->`.
- **One record per producing script.** `measurements.json`, `eval.json`, `arms.json`,
  `rrf_audit.json`. Split so a change invalidates only what it actually touches: the eval depends
  on the bundle as well as the cluster, so a bundle edit must not appear to move a corpus figure.
  Each carries its own `run` block — index, chunk count, ES version, commit — because a figure
  that does not say which index produced it is not citable.
- **A claim counted by hand is a claim nobody checks.** `findings.md` §1 argued from *never worse
  than the weaker arm* and *six of eight*; both were true, and both were arithmetic a person did
  once. Derive a claim like that from the record — `ArmReport.matches_or_beats_the_stronger_arm` —
  and assert the sentence against it, so a reindex that moves a rank moves the prose too.
- **One command verifies all of it:** `uv run --extra es --extra mcp python scripts/verify.py`
  — matrix, suite, and one stage per record, read-only, ~7 min. `--update` regenerates instead.
  Run it before publishing anything and after any reindex. `tests/test_verify.py` derives the
  stage list from every script declaring `--check`, so a new publisher that is not wired in fails.
  See `docs/maintenance.md`.
- **The eval result is a record, not a table.** `scripts/publish_eval.py` writes
  `docs/data/eval.json`. The verdicts are not restated as prose anywhere — the same rule the
  README follows for the test count. Each case declares `expected` **and** `expected_route` —
  both are asserted, because a question can reach the right answer by the wrong path. See
  `docs/specs/eval.md` for the criteria and the cap on declared deviations.

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
  - `eval.md` — the 20-question eval set + expected engine per question
  - `observability.md` — the per-query telemetry event, its emit sites, and the three
    non-negotiables (emitted after the answer, best-effort, never blocks)
  - `observability-corpus-state.md` — the bundle-governance snapshot: the other two signals
  - `single-call-retrieval.md` — the one-call answerability candidates, both measured and both
    rejected: MinMax and `l2_norm` cannot carry a floor, a `none`-normalized weight does not
    survive held-out probes, and `min_score` on the inner ELSER arm classifies perfectly while
    destroying `floor_score`. Also the AUC-beside-margin convention every published percentage
    now follows
- Keeping the canonical layer current: `docs/maintenance.md` — re-verification, `stale_after`,
  corpus refresh, and which checks are automated.
