# Spec: Evaluation Set (20 questions)

Small and illustrative — **NOT a benchmark**. Purpose: show which engine answers, that
provenance is always present, and where each path wins. Doubles as the first-person "what
broke" story and the single live proof moment (Q9).

Columns: id · question · expected_path · correct answer shape · notes

| id | question | expected_path | correct answer shape | notes |
|----|----------|---------------|----------------------|-------|
| Q1 | What is the exact context window of \<model X\>? | deterministic | exact number + provenance | canonical field lookup |
| Q2 | What is the endpoint path for Anthropic's Messages API? | deterministic | `/v1/messages` + provenance | |
| Q3 | Which of these models support vision? | deterministic | list + per-model provenance | matrix traversal |
| Q4 | What is the max output tokens for \<model Y\>? | deterministic | exact number + provenance | |
| Q5 | How do I stream responses from the API? | semantic | grounded prose + cited doc | |
| Q6 | What's the recommended way to do hybrid search in Elasticsearch? | semantic | grounded prose + cited doc | |
| Q7 | How should I chunk documents for retrieval? | semantic | grounded prose + cited doc | |
| Q8 | What's the difference between BM25 and vector search? | semantic | grounded prose + cited doc(s) | |
| Q9 | What does the `rank_constant` parameter do? | semantic (hybrid) | grounded prose + cited doc | **THE planted proof.** Pure-vector grabs a semantically-adjacent wrong doc; BM25/hybrid nails the exact token. Design the corpus so this reliably reproduces. Rehearse it. |
| Q10 | Compare \<provider A\> and \<provider B\> on \<exact field\>. | both | merged answer, both cited | cross-entity |
| Q11 | \<a precision question about an entity the bundle does not hold\> | refusal | "Not found in the grounded sources" | guardrail: no-hallucination. Refuses on the *routing* decision, not the floor — see below |
| Q12 | What is \<an exact fact that changed recently\>? | deterministic | exact value + OKF `verified` / `stale_after` shown | shows governed, trust-tiered canonical data |
| Q13 | Abbreviated field, bare alias, no punctuation | deterministic | exact number + provenance | paraphrase |
| Q14 | Natural interrogative, spaced alias | deterministic | exact number + provenance | paraphrase |
| Q15 | Field synonym rather than the canonical field name | deterministic | exact number + provenance | paraphrase |
| Q16 | Dotted version alias, no question form | deterministic | exact number + provenance | paraphrase |
| Q17 | Alias plus a one-hop traversal | deterministic | endpoint path + provenance + `traversed:` | paraphrase |
| Q18 | A comparison the bundle cannot answer | refusal | "Not found in the grounded sources" | precision exception, router.md |
| Q19 | An off-topic question phrased exploratorily | refusal | "Not found in the grounded sources" | the relevance floor, end to end |
| Q20 | An off-topic question the floor does not catch | refusal | *declared deviation* — returns passages | the floor's documented false positive |

## Q13–Q18: why paraphrases are in the set

Q1–Q12 all name the canonical identifier verbatim — `claude-opus-5`, `claude-haiku-4-5`. That is
not how anyone types, and it is how a defect that broke *every* natural phrasing survived a green
suite for six days: the model files carried no aliases, so `Opus 5` resolved to nothing and the
query fell through to ranked passages, returning a confident, cited, adjacent answer to a question
the bundle held exactly.

These six ask for facts the bundle already holds, without using the bundle's vocabulary. They
cover alias shape (bare, spaced, dotted), field synonyms, a one-hop traversal reached through an
alias, and the refusal the precision exception now produces.

**What they are not.** Six cases are not a routing benchmark and do not measure routing accuracy.
They are regression cover for a specific class of defect that has already occurred once. The set
stays illustrative.

## Q9: why `rank_constant`

Chosen from the fetched corpus rather than staged for it. The token appears in exactly one
document — Elastic's reciprocal-rank-fusion reference — while its semantic neighbors live
elsewhere: `rank_window_size` in the retrievers reference, `num_candidates` in the kNN guide.
Both are "parameters that tune a ranked result set," so an embedding has every reason to rank
them close to the question while missing the one document that defines the term. Lexical
matching has no such difficulty.

It is also RRF's own parameter — the fusion step this system uses — so the demo explains the
retrieval method while proving why lexical matching still earns its place in it.

## Pass criteria

Each case declares two expectations, and both are asserted. They are different claims: a question
can reach the right answer by the wrong path.

| Field | What it claims | Checked by |
|---|---|---|
| `expected` | which engine answers — `deterministic`, `semantic`, `mixed` or `refusal` | `run_case` |
| `expected_route` | which route the router chose — `DETERMINISTIC`, `SEMANTIC` or `BOTH` | `run_case` |

A case passes when both match **and** the answer is grounded: at least one citation, unless the
answer is the refusal, which carries none by definition. Anything else is `FAIL`.

Asserting the route is not redundant. Q19 refuses correctly whether the router sends it to the
semantic arm or the deterministic one — but only the semantic route runs the relevance floor,
which is the entire reason the case exists. Without the route assertion it could silently become
a second copy of Q11 and the suite would stay green.

### Declared deviations

A case that fails for an understood reason carries a `known_deviation` and reports `KNOWN`. It is
counted apart from a pass and never reads as green.

`known_deviation` silences a failure, so the set of cases allowed to carry one is pinned in
`tests/test_evaluation.py` as `DECLARED_DEVIATIONS`. Adding a deviation therefore means editing a
test, which is a visible act, rather than turning the suite green quietly. Today the set is
exactly **Q3** and **Q20**, and both are explained where they appear.

### Two cases that must keep working

- **Q11 refuses** rather than inventing.
- **Q9 demonstrably differs** between pure-vector and hybrid — this is the moment that proves
  platform depth, so verify it reproduces before the interview.

### How the result is published and checked

`scripts/publish_eval.py` writes the whole run to [`docs/data/eval.json`](../data/eval.json) with
the bundle, the index, the date and the commit behind it. It refuses to write a run made without a
reachable cluster, because every semantic case would record a false refusal.

Two tests read it, and the split is what makes the guarantee complete:

- **Without credentials** — the verdict table published in
  [`eval-output.md`](../eval-output.md) is compared row by row to `eval.json`: expected, actual,
  route, citation count and verdict, plus the totals line. A bare clone catches a document that
  drifted from the run it claims to show.
- **Against the cluster** — the eval is re-run and asserted to still reproduce `eval.json`. This
  is the half the first cannot cover: a document that faithfully reports a result that stopped
  being true.

After a reindex or a bundle change: `uv run --extra es python scripts/publish_eval.py`, then
recapture `gctx eval` into `eval-output.md`. `--check` re-runs and reports what moved without
writing.

## Q19–Q20: why the floor needs its own cases

Q11 and Q18 both refuse, and neither one tests the relevance floor. Q11 refuses because the router
sends a precision question to the deterministic path alone, and Q18 because of the precision
exception in `router.md`. In both, the semantic arm never runs. Every case that *does* reach the
semantic arm returns passages, so until Q19 nothing in the set walked the path that ends in a floor
refusal: routed SEMANTIC, scored below the floor, empty result, refusal.

That mattered because Q11's note used to claim its answer was "absent from both the bundle and the
corpus." The bundle half is true. The corpus half is not: the query scores **18.84** against a floor
of 8, because the corpus genuinely discusses pricing — Anthropic's. Had the router ever classified
it SEMANTIC, Q11 would have returned cited Anthropic pricing for a question about a different
vendor's model. The case passed for a reason other than the one written beside it, which is the
failure `findings.md` finding 2 is about.

**Q19** is the floor working: routed SEMANTIC on exploratory phrasing, scored 2.05, refused.

**Q20** is the floor failing, declared rather than hidden. It clears the floor at 16.11 because
Elastic's `semantic_text` page teaches the feature with running and exercise sample documents, so
the retrieval is correct and the passages are real — only the subject is a surprise. It is reported
`KNOWN`, which keeps a documented limitation visible in the suite instead of only in prose. If a
future change makes the floor catch it, the case turns green and the deviation is retired.
