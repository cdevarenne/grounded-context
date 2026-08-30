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


# --- the precision exception to the BOTH fallback (docs/specs/router.md) ---------------


def test_a_cross_entity_comparison_is_marked_precision() -> None:
    """A comparison asks for exact values, so a ranked passage cannot be a correct answer."""
    decision = route("Is Sonnet 5 cheaper than Opus 5?")
    assert decision.route == BOTH
    assert decision.precision is True


def test_an_ambiguous_both_is_not_marked_precision() -> None:
    """Mixed signals may genuinely be exploratory, so that BOTH keeps its semantic fallback."""
    decision = route("What is the recommended pricing approach?")
    assert decision.route == BOTH
    assert decision.precision is False


def test_precision_is_not_serialized() -> None:
    """The envelope's router block is a published contract; its shape does not change."""
    assert set(route("compare a and b").as_dict()) == {"route", "rationale"}


# --- the router port (ELX-49) ----------------------------------------------------------------


def test_the_default_router_satisfies_the_port() -> None:
    """`QueryRouter` is a callable type, so `route` satisfies it as written."""
    from grounded_context.router import QueryRouter, route

    classifier: QueryRouter = route
    assert classifier("What is the context window of claude-opus-5?").route == "DETERMINISTIC"


def test_ask_uses_the_router_it_is_given() -> None:
    """The claim router.py makes in its own docstring, now true of the code.

    A caller supplies a classifier and the engine follows it. Nothing else changes: the envelope
    carries the supplied decision and its rationale, so the audit trail names what actually
    decided.
    """
    from datetime import date

    from grounded_context.router import SEMANTIC, Route
    from grounded_context.service import ask, load_bundle

    def always_semantic(query: str) -> Route:
        return Route(SEMANTIC, "a stand-in classifier that sends everything to the semantic arm")

    envelope = ask(
        load_bundle(),
        "What is the exact context window of claude-opus-5?",
        date(2026, 8, 20),
        router=always_semantic,
    )

    # The heuristic router sends this to DETERMINISTIC and it resolves. The substitute sends it
    # to the semantic arm, so with no cluster it refuses. Either way the decision is recorded.
    assert envelope["router"]["route"] == "SEMANTIC"
    assert envelope["router"]["rationale"].startswith("a stand-in classifier")


def test_a_stateful_classifier_also_satisfies_the_port() -> None:
    """A callable object, not only a function. An LLM classifier holds a client and a threshold."""
    from grounded_context.router import BOTH, QueryRouter, Route

    class Recording:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def __call__(self, query: str) -> Route:
            self.seen.append(query)
            return Route(BOTH, "recorded")

    classifier: QueryRouter = Recording()
    classifier("a question")
    assert classifier.seen == ["a question"]
