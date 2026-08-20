"""The evaluation set from docs/specs/eval.md, runnable.

Small and illustrative — **not a benchmark**. Twelve questions, each with the engine that
should answer it. What it checks is that the router sends questions to the right place, that
every answer carries provenance, and that the one question with no grounded answer refuses.

Known deviations are declared rather than hidden. A harness that quietly expects whatever the
code currently does is a rubber stamp, so a case that fails for a understood reason is reported
as KNOWN with the reason attached, and still counted separately from a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .bundle import Bundle
from .provenance import DETERMINISTIC, MIXED, NOT_FOUND, SEMANTIC
from .service import ask

REFUSAL = "refusal"


@dataclass(frozen=True)
class EvalCase:
    """One question and the engine that ought to answer it."""

    id: str
    question: str
    expected: str
    note: str = ""
    known_deviation: str = ""


@dataclass(frozen=True)
class EvalResult:
    """What actually happened, and whether that is acceptable."""

    case: EvalCase
    route: str
    rationale: str
    retrieval_path: str
    answer: str
    citations: int
    verdict: str = field(default="")

    @property
    def actual(self) -> str:
        return REFUSAL if self.answer == NOT_FOUND else self.retrieval_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "question": self.case.question,
            "expected": self.case.expected,
            "actual": self.actual,
            "route": self.route,
            "rationale": self.rationale,
            "citations": self.citations,
            "verdict": self.verdict,
        }


CASES: tuple[EvalCase, ...] = (
    EvalCase("Q1", "What is the exact context window of claude-opus-5?", DETERMINISTIC,
             "canonical field lookup"),
    EvalCase("Q2", "What is the endpoint path for Anthropic's Messages API?", DETERMINISTIC,
             "resolves through an alias, not the literal id"),
    EvalCase("Q3", "Which of these models support vision?", SEMANTIC,
             "multi-entity rollup",
             known_deviation="eval.md expects a deterministic list. Lookup answers one "
                             "entity at a time, so a cross-model rollup has no engine and "
                             "falls through to semantic passages that do not really answer "
                             "it. docs/compatibility-matrix.md is what answers this today."),
    EvalCase("Q4", "What is the max output tokens for claude-haiku-4-5?", DETERMINISTIC),
    EvalCase("Q5", "How do I stream responses from the API?", SEMANTIC),
    EvalCase("Q6", "What's the recommended way to do hybrid search in Elasticsearch?", SEMANTIC),
    EvalCase("Q7", "How should I chunk documents for retrieval?", SEMANTIC),
    EvalCase("Q8", "What's the difference between BM25 and vector search?", SEMANTIC),
    EvalCase("Q9", "What does the rank_constant parameter do?", SEMANTIC,
             "the planted proof — see compare_arms()"),
    EvalCase("Q10", "Compare claude-opus-5 and claude-sonnet-5 on max output tokens.", MIXED,
             "cross-entity: exact hit leads, semantic context follows"),
    EvalCase("Q11", "What is the price per million tokens of GPT-5?", REFUSAL,
             "guardrail: absent from both the bundle and the corpus"),
    EvalCase("Q12", "What is the exact context window of claude-haiku-4-5?", DETERMINISTIC,
             "shows OKF verified / stale_after on a governed fact"),
    # --- paraphrases: the same canonical facts, asked the way a person types them -------
    # Q1-Q12 all name the canonical identifier verbatim, which is how a bug that broke
    # every natural phrasing survived a green suite. These ask for facts the bundle holds
    # without using its vocabulary, so they fail when matching or routing regresses.
    EvalCase("Q13", "Whats the ctx window for opus", DETERMINISTIC,
             "abbreviated field, bare alias, no punctuation"),
    EvalCase("Q14", "How big is the Opus 5 context window?", DETERMINISTIC,
             "natural interrogative, spaced alias"),
    EvalCase("Q15", "What is the context length of Sonnet 5", DETERMINISTIC,
             "field synonym rather than the canonical field name"),
    EvalCase("Q16", "Haiku 4.5 max output", DETERMINISTIC,
             "dotted version alias, no question form"),
    EvalCase("Q17", "the Opus model endpoint", DETERMINISTIC,
             "alias plus a one-hop traversal to the endpoint concept"),
    EvalCase("Q18", "Is Sonnet 5 cheaper than Opus 5?", REFUSAL,
             "precision exception: a comparison the bundle cannot answer refuses "
             "rather than falling back to passages (router.md)"),
)


def run_case(bundle: Bundle, case: EvalCase, as_of: date) -> EvalResult:
    """Ask one question and judge the outcome against the spec."""
    envelope = ask(bundle, case.question, as_of)
    router = envelope.get("router") or {"route": "-", "rationale": "-"}
    result = EvalResult(
        case=case,
        route=router["route"],
        rationale=router["rationale"],
        retrieval_path=envelope["retrieval_path"],
        answer=envelope["answer"],
        citations=len(envelope["citations"]),
    )
    passed = result.actual == case.expected
    grounded = result.citations > 0 or result.actual == REFUSAL
    if passed and grounded:
        verdict = "KNOWN" if case.known_deviation else "PASS"
    elif case.known_deviation:
        verdict = "KNOWN"
    else:
        verdict = "FAIL"
    return EvalResult(**{**result.__dict__, "verdict": verdict})


def run_all(bundle: Bundle, as_of: date) -> list[EvalResult]:
    """Run the whole set, in spec order."""
    return [run_case(bundle, case, as_of) for case in CASES]


# The chunk that defines each identifier the compare table covers, found by reading the
# passage rather than by trusting the top hit. `rank_window_size` shares a chunk with
# `rank_constant`: both are defined in the same parameter-reference block.
DEFINING_CHUNKS: dict[str, tuple[str, int]] = {
    "rank_constant": ("elastic-rrf", 1),
    "rank_window_size": ("elastic-rrf", 1),
    "num_candidates": ("elastic-knn", 7),
    "anthropic-ratelimit-tokens-reset": ("anthropic-rate-limits", 12),
}

DEFAULT_TARGET = DEFINING_CHUNKS["rank_constant"]


def target_for(query: str) -> tuple[str, int]:
    """Pick the defining chunk to rank, by the identifier the query mentions."""
    lowered = query.lower()
    for identifier, target in DEFINING_CHUNKS.items():
        if identifier in lowered:
            return target
    return DEFAULT_TARGET


def compare_arms(
    query: str, size: int = 20, target: tuple[str, int] | None = None
) -> dict[str, int | None]:
    """Rank of the chunk that defines the queried term, under each retrieval arm.

    The point of Q9: neither single arm wins every phrasing of the same question, which is
    the argument for fusing them rather than picking one. Fusion is a hedge, not a maximum —
    see `rank_window_size` in docs/findings.md, where it lands between the two arms rather
    than above both.
    """
    from .semantic import search, search_lexical_only, search_semantic_only

    source_id, chunk = target or target_for(query)

    def rank(results: list[dict[str, Any]]) -> int | None:
        for position, cite in enumerate(results, 1):
            if cite["source_id"] == source_id and cite["locator"] == f"chunk:{chunk}":
                return position
        return None

    return {
        "elser": rank(search_semantic_only(query, size=size)),
        "bm25": rank(search_lexical_only(query, size=size)),
        "hybrid": rank(search(query, size=size)),
    }
