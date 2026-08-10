# Spec: Evaluation Set (~12 questions)

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
| Q9 | What does the `<exact_param>` parameter do? | semantic (hybrid) | grounded prose + cited doc | **THE planted proof.** Pure-vector grabs a semantically-adjacent wrong doc; BM25/hybrid nails the exact token. Design the corpus so this reliably reproduces. Rehearse it. |
| Q10 | Compare \<provider A\> and \<provider B\> on \<exact field\>. | both | merged answer, both cited | cross-entity |
| Q11 | \<a question whose answer is NOT in the corpus\> | either | "Not found in the grounded sources" | guardrail: tests no-hallucination |
| Q12 | What is \<an exact fact that changed recently\>? | deterministic | exact value + `last_verified` shown | shows governed / date-stamped canonical data |

## Pass criteria
- Right path chosen (or BOTH when appropriate), with a logged `rationale`.
- Answer is grounded and carries a valid citation block (provenance.md).
- **Q11 refuses** rather than inventing.
- **Q9 demonstrably differs** between pure-vector and hybrid — this is the moment that proves
  platform depth, so verify it reproduces before the interview.
