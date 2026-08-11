"""Routing tests keyed to the question IDs in docs/specs/eval.md."""

import pytest

from grounded_context.router import BOTH, DETERMINISTIC, SEMANTIC, route


@pytest.mark.parametrize(
    ("qid", "query", "expected"),
    [
        ("Q1", "What is the exact context window of claude-opus-5?", DETERMINISTIC),
        ("Q2", "What is the endpoint path for Anthropic's Messages API?", DETERMINISTIC),
        ("Q4", "What is the max output tokens for claude-haiku-4-5?", DETERMINISTIC),
        ("Q5", "How do I stream responses from the API?", SEMANTIC),
        ("Q6", "What's the recommended way to do hybrid search?", SEMANTIC),
        ("Q7", "How should I chunk documents for retrieval?", SEMANTIC),
        ("Q8", "What's the difference between BM25 and vector search?", SEMANTIC),
        ("Q10", "Compare claude-opus-5 and claude-sonnet-5 on context window", BOTH),
    ],
)
def test_eval_set_routes_as_specified(qid, query, expected):
    assert route(query).route == expected, qid


def test_precision_beats_a_bare_model_mention():
    assert route("context window of claude-opus-5").route == DETERMINISTIC


def test_named_model_without_a_field_goes_both():
    """The intent is under-specified — guessing it is the failure mode to avoid."""
    decision = route("tell me about claude-opus-5")
    assert decision.route == BOTH
    assert "matched no precision signal" in decision.rationale


def test_mixed_signals_go_both():
    decision = route("How do I find the exact context window?")
    assert decision.route == BOTH
    assert "mixed signals" in decision.rationale


def test_uncertainty_defaults_to_both():
    """router.md: BOTH is the safe default, and it shows the dual engine."""
    decision = route("airspeed velocity of an unladen swallow")
    assert decision.route == BOTH
    assert "no decisive signal" in decision.rationale


def test_q3_currently_routes_both_not_deterministic():
    """Known discrepancy between the two specs, asserted so it can't drift silently.

    eval.md Q3 expects `deterministic`; as phrased the query names no entity and no
    field, so router.md's own rules land it on BOTH. Resolving this means either
    rephrasing Q3 or teaching the router about field names — not fudging either spec.
    """
    assert route("Which of these models support vision?").route == BOTH


def test_every_decision_carries_a_rationale():
    for query in (
        "context window of claude-opus-5",
        "how do I stream responses",
        "compare opus and sonnet",
        "",
    ):
        decision = route(query)
        assert decision.rationale
        assert decision.as_dict()["route"] in {DETERMINISTIC, SEMANTIC, BOTH}
