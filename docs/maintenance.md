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
boundaries can move — and the figures published in [`findings.md`](findings.md) and
[`eval-output.md`](eval-output.md) are properties of the corpus as fetched. If the counts move,
the published numbers must be regenerated (`scripts/measure_findings.py`) rather than left to
disagree with the index. Adding or removing a page in `corpus/manifest.yml` has the same effect.

## What is automated today, and what is not

Automated — these fail a build:

| Check | Catches |
|---|---|
| `scripts/build_matrix.py --check` | a compatibility matrix that no longer matches the bundle |
| `pytest` bundle tests | malformed front matter, dangling links, a non-date `stale_after` |
| `BundleParityTest` (JVM) | the two `knowledge/` copies diverging |

**Not automated: nothing warns you that a date is approaching.** `is_stale` is evaluated per
answer, so you find out when a citation says `STALE` — correct behaviour, late notice. Closing
that is `ELX-25`, the corpus-state snapshot: a scan over `knowledge/` that reports how many
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
