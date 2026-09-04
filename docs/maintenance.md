# Keeping the bundle and the corpus current

A canonical layer that nobody refreshes is worse than no canonical layer: it answers exact
questions with confident, stale facts, which is the failure this project exists to prevent. This
is the procedure for keeping it honest, and what is automated versus what is not.

The design deliberately makes going stale **visible rather than silent** — every citation block
carries a freshness line, and past `stale_after` it says `STALE` instead of `fresh until`. So the
system degrades into a warning, not into a wrong answer. That is the safety net, not the plan.

## What ages, and when

| | Value |
|---|---|
| Concepts in `knowledge/` | 4 — three models, one endpoint |
| Last verified | `2026-08-10` (all four) |
| `stale_after` | `2026-09-09` (all four) |

Thirty days is the interval the committed bundle uses. It is a **convention, not a rule** — OKF
defines `stale_after` as an absolute date and says nothing about how far ahead to set it, and
neither does [`specs/okf-bundle.md`](specs/okf-bundle.md). Thirty days suits model documentation,
which changes on the vendor's schedule and not yours. A slower-moving corpus can justify longer.

Check where a concept stands without waiting for the date to arrive — the deterministic path
already time-travels:

```bash
uv run gctx --as-of 2026-09-10 lookup anthropic.claude-opus-5 context_window_tokens
```

Anything that prints `STALE` on a date you care about needs re-verification before then.

## Re-verifying a concept

Do this against the live source, not from memory. The whole point of `verified` is that a human
looked.

1. **Open the source.** Each file names it twice: `resource` in the front matter and
   `sources[].resource`. For the models that is Anthropic's models overview page.
2. **Compare every value under `canonical:`.** Correct any that changed. If a value moved, that is
   the finding — say so in the commit rather than editing silently.
3. **Record the verification.** Update `verified[].at` to now, ISO-8601 with offset, and
   `verified[].by` to `human:<you>`. The trust tier is *derived* from this, so an unedited
   `verified` block means the citation keeps claiming a review that did not happen.
4. **Move `stale_after` forward**, thirty days out unless you have reason to choose otherwise.
5. **Regenerate the compatibility matrix.** It is a generated view over the same files:
   ```bash
   uv run python scripts/build_matrix.py
   ```
6. **Run the suite.** The matrix has a drift test, so a regeneration you forgot fails here rather
   than in a demo:
   ```bash
   uv run pytest -q
   ```
7. **Sync the JVM copy.** [`grounded-context-jvm`](https://github.com/cdevarenne/grounded-context-jvm)
   carries its own `knowledge/` so it runs standalone, which makes it a second source of truth.
   Its `BundleParityTest` fails when the copies diverge — that guard is what turns "remember to
   copy it" into "the build tells you":
   ```bash
   cp -R knowledge/. ../grounded-context-jvm/knowledge/
   cd ../grounded-context-jvm && ./gradlew build
   ```

Only step 1 needs judgment. The rest is mechanical, and every mechanical step has a test behind it.

## Refreshing the corpus

The semantic corpus is a different body of content with different rules — see the two-corpora
split in [`design.md`](design.md). It is fetched, never committed, and it is a curated reading
list rather than a crawl.

```bash
uv run python scripts/fetch_corpus.py                       # re-fetch the 25 curated pages
uv run --extra es python scripts/index_corpus.py --recreate # rebuild the index from them
```

Two consequences worth knowing before you do it. Re-fetching changes the source text, so chunk
boundaries can move — and the figures published in [`findings.md`](findings.md) are properties of
the corpus as fetched. If the counts move, the records must be regenerated rather than left to
disagree with the index. Adding or removing a page in `corpus/manifest.yml` has the same effect.

Regenerating is one command per record, and the suite tells you if you forgot:

```bash
uv run --extra es python scripts/publish_figures.py   # docs/data/measurements.json
uv run --extra es python scripts/publish_eval.py      # docs/data/eval.json
uv run --extra es python scripts/publish_arms.py      # docs/data/arms.json
uv run --extra es python scripts/rrf_audit.py         # docs/data/rrf_audit.json
```

One record per producing script, so a change invalidates only what it touches: the eval depends on
the bundle as well as the cluster, and a bundle edit should not appear to move a corpus figure.
Each takes `--check`, which re-measures and reports what moved without writing.

Nothing here writes a `.txt` artifact and no test parses console output. Example output for a
reader lives in [`eval-how-to.md`](eval-how-to.md), is labeled illustrative, and is asserted
against nothing — see [`CLAUDE.md`](../CLAUDE.md) on why those are separate documents.

## Verifying everything at once

Knowing the repo is honest should not depend on remembering five commands and what each one
covers, so there is one:

```bash
uv run --extra es --extra mcp python scripts/verify.py            # check, writes nothing
uv run --extra es --extra mcp python scripts/verify.py --update   # regenerate, after a reindex
```

Read-only by default. A `verify` that rewrites what it is verifying is not a verification, and the
last thing wanted before a demo is a command that quietly edits published documents.

It prints what it ran against before it runs anything — index, chunk count, Elasticsearch version,
inference endpoint, commit and whether the tree is clean, plus the dates on the two data files —
so the verdict can be quoted rather than just believed. **Measured 2026-08-30: 5.0 minutes.**

```
  ok   compatibility matrix      0.3s   ok: compatibility-matrix.md matches the bundle
  ok   test suite              151.8s   447 passed
  ok   published figures       136.1s   every figure reproduces
  ok   eval result               4.1s   the eval reproduces
  ok   retrieval arms            8.5s   every arm rank reproduces
  ok   fusion audit              1.4s   the fusion audit reproduces
```

One stage per record, so a stage that passes means every document quoting that record is true of
the index as it stands. Most of the time is the cluster, and nearly all of that is the two stages
that sweep every chunk. The run got faster when the captures went: re-running nine commands to
compare their console text cost more than measuring the two records that replaced them.

A stage list that falls behind would be worse than no command at all — `verify` would report
success over an artifact it never looked at. `tests/test_verify.py` derives the list instead of
trusting it: any script in `scripts/` offering a `--check` mode must appear in a stage.

## Rebuilding the index, rehearsed

The index is a rebuildable projection, but "rebuildable" is a claim, and a claim about a step you
have never run is a guess. Rehearse it against a **scratch index** rather than the live one:
`ES_INDEX` names the target, so nothing needs editing.

```bash
export ES_INDEX=grounded-context-scratch

uv run --extra es python scripts/index_corpus.py --recreate      # build it
uv run --extra es python scripts/publish_figures.py --check      # do the figures still hold?
uv run --extra es python scripts/publish_eval.py --check         # does the eval still hold?
uv run --extra es --extra mcp pytest -q                          # does anything break?

uv run --extra es python -c "
from elasticsearch import Elasticsearch
from grounded_context.es_client import client
client().indices.delete(index='grounded-context-scratch')"

unset ES_INDEX
```

Both `--check` modes re-measure and report what moved without writing, so a rehearsal cannot
overwrite a published figure by accident.

`index_corpus.py` will not report success on a rebuild it cannot vouch for. After indexing it
scores the 16 tuning and 30 held-out probes and prints a verdict, exiting non-zero if the floor
has stopped working:

```
relevance floor 8.0 against this index
  usable gap [6.01, 14.02] from the 16 tuning probes   -> floor sits inside it
  0 false accepts across 30 held-out probes
  0 false rejects across 30 held-out probes
  VERDICT: floor still holds
```

`--skip-floor-check` turns it off, and nothing else will check for you if you use it.

**Measured 2026-08-29**, from `corpus/raw/` as fetched on 2026-08-13, against Elastic Cloud
Serverless 9.6.0:

| | |
|---|---|
| Rebuild | **3.94 s**, 320 chunks, 0 errors |
| ELSER readiness | immediate — a semantic query straight after the bulk returned the expected score |
| Figures | every one reproduced |
| Eval | reproduced, 18 pass · 2 known · 0 fail |
| Suite | 426 passed |

Nothing moved. Chunking, BM25 and ELSER are all deterministic for a given corpus and inference
endpoint, so a rebuild is safe to do the week of a demo.

Three things that will bite, in the order they bite:

- **`ES_INFERENCE_ID` is baked into the mapping when the index is created.** A wrong value is not
  a setting you correct afterwards; it is an index you rebuild. Check it before you build, not
  after.
- **A fresh project offers more than one ELSER endpoint, and their names differ by one word.**
  This project lists both `.elser-2-elasticsearch` (the one used here) and `.elser-2-elastic`.
  `uv run --extra es python -c "from grounded_context.es_client import client;
  print([e['inference_id'] for e in client().inference.get()['endpoints']])"` lists what a project
  actually has.
- **`ES_INDEX` is read once, at import time.** Exporting it works; setting it inside a Python
  session after `grounded_context.es_client` is imported does not.

When you rebuild the **real** index rather than a scratch one, the same three `--check` commands
become the acceptance test, and then both publishers must be run for real so
`docs/data/*.json` describe the index that now exists.

## Keeping the architecture diagram current

[`docs/architecture.mmd`](architecture.mmd) is the source. It is text, it renders inline on
GitHub, and an agent or a developer can read it. It shows every route the code has, including the
relevance floor, the BOTH route and its refusal on a precision miss, and the DIRECT route of
`lookup_canonical_fact`.

`docs/grounded-context-diagram.png` is a render of that source, and it is the image the README
shows. Nothing was drawn by hand, so the picture cannot say something the source does not.

Regenerate it after any edit to the source:

```bash
npx @mermaid-js/mermaid-cli -i docs/architecture.mmd -c docs/mermaid-render.json \
    -o docs/grounded-context-diagram.png -w 1800 -s 2 -b white
```

Then set `GENERATED_FROM` in `tests/test_diagram.py` to the new digest —
`shasum -a 256 docs/architecture.mmd`.

**The test detects drift; it cannot repair it.** The render needs node and a headless browser,
which the rest of this repo does not, so no CI job regenerates the image. The pin is over the
source rather than over the PNG bytes: mermaid renders through a browser, so the same source on a
different chromium or font set produces a different file, and a byte comparison would fail for
reasons that have nothing to do with the diagram.

The ELK layout and theme live in [`docs/mermaid-render.json`](mermaid-render.json) rather than in
the `.mmd` front matter. GitHub and Medium render mermaid with their own build, which does not
load the ELK layout package. A source that demanded it would degrade where developers read it.
The source stays portable; the polish stays in the render step.

`-s 2` renders at twice the scale. The same file is then legible projected as well as in a
browser, which is the only reason the image is 3878 pixels tall.

## What is automated today, and what is not

Automated — these fail a build:

| Check | Catches |
|---|---|
| `scripts/build_matrix.py --check` | a compatibility matrix that no longer matches the bundle |
| `pytest` bundle tests | malformed front matter, dangling links, a non-date `stale_after` |
| `BundleParityTest` (JVM) | the two `knowledge/` copies diverging |
| `tests/test_diagram.py` | the architecture source moving without the published image being regenerated |

**Not automated: nothing warns you that a date is approaching.** `is_stale` is evaluated per
answer, so you find out when a citation says `STALE` — correct behaviour, late notice. Closing
that is [issue #3](https://github.com/cdevarenne/grounded-context/issues/3), backed by the corpus-state snapshot: a scan over `knowledge/` that reports how many
concepts are past `stale_after` and, with `--as-of`, how many *will* be on a future date. Run on a
schedule, that turns the cliff into a line on a chart you can see coming. Until it exists, the
`gctx --as-of` command above is the manual equivalent.

A CI job can run the automated checks today:

```yaml
- run: uv sync --extra dev
- run: uv run pytest -q
- run: uv run python scripts/build_matrix.py --check
```

Note what that job deliberately does not need: no cluster, no API key. The checks that protect the
canonical layer are the ones that run with no cloud dependency at all.
