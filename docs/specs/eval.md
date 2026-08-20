# Spec: Evaluation Set (18 questions)

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
| Q11 | \<a question whose answer is NOT in the corpus\> | either | "Not found in the grounded sources" | guardrail: tests no-hallucination |
| Q12 | What is \<an exact fact that changed recently\>? | deterministic | exact value + OKF `verified` / `stale_after` shown | shows governed, trust-tiered canonical data |
| Q13 | Abbreviated field, bare alias, no punctuation | deterministic | exact number + provenance | paraphrase |
| Q14 | Natural interrogative, spaced alias | deterministic | exact number + provenance | paraphrase |
| Q15 | Field synonym rather than the canonical field name | deterministic | exact number + provenance | paraphrase |
| Q16 | Dotted version alias, no question form | deterministic | exact number + provenance | paraphrase |
| Q17 | Alias plus a one-hop traversal | deterministic | endpoint path + provenance + `traversed:` | paraphrase |
| Q18 | A comparison the bundle cannot answer | refusal | "Not found in the grounded sources" | precision exception, router.md |

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
- Right path chosen (or BOTH when appropriate), with a logged `rationale`.
- Answer is grounded and carries a valid citation block (provenance.md).
- **Q11 refuses** rather than inventing.
- **Q9 demonstrably differs** between pure-vector and hybrid — this is the moment that proves
  platform depth, so verify it reproduces before the interview.
