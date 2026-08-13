"""Tests for the eval harness.

The harness exists to catch the system drifting away from its own spec, so what matters here
is that it cannot quietly pass: a declared expectation that stops matching must surface, and a
known deviation must never be counted as a pass.
"""

from __future__ import annotations

from datetime import date

import pytest

from grounded_context.es_client import is_configured
from grounded_context.evaluation import CASES, EvalCase, run_all, run_case
from grounded_context.provenance import DETERMINISTIC, MIXED, NOT_FOUND, SEMANTIC
from grounded_context.service import load_bundle

AS_OF = date(2026, 8, 13)
VALID = {DETERMINISTIC, SEMANTIC, MIXED, "refusal"}

requires_elasticsearch = pytest.mark.skipif(
    not is_configured(), reason="no ES_URL / ES_API_KEY — semantic cases unavailable"
)


@pytest.fixture(scope="module")
def bundle():
    return load_bundle()


def test_the_set_matches_the_spec_size() -> None:
    assert [case.id for case in CASES] == [f"Q{n}" for n in range(1, 13)]


def test_every_expectation_is_a_real_path() -> None:
    assert all(case.expected in VALID for case in CASES)


def test_deterministic_cases_pass_without_a_cluster(bundle) -> None:
    """The spine answers on its own; no case that needs the exact path may depend on ES."""
    for case in CASES:
        if case.expected == DETERMINISTIC:
            assert run_case(bundle, case, AS_OF).verdict == "PASS", case.id


def test_the_guardrail_case_refuses(bundle) -> None:
    """Q11: absent from both engines, so nothing may be invented."""
    q11 = next(case for case in CASES if case.id == "Q11")
    result = run_case(bundle, q11, AS_OF)
    assert result.answer == NOT_FOUND
    assert result.citations == 0


def test_alias_resolution_answers_the_natural_phrasing(bundle) -> None:
    """Q2 asks for 'Anthropic's Messages API', not the literal concept id."""
    q2 = next(case for case in CASES if case.id == "Q2")
    result = run_case(bundle, q2, AS_OF)
    assert result.verdict == "PASS"
    assert result.answer == "/v1/messages"


def test_a_known_deviation_never_reads_as_a_pass(bundle) -> None:
    declared = [case for case in CASES if case.known_deviation]
    assert declared, "the Q3 rollup gap should still be declared"
    for case in declared:
        assert run_case(bundle, case, AS_OF).verdict == "KNOWN"


def test_a_broken_expectation_fails_loudly(bundle) -> None:
    """The harness must not rubber-stamp: a wrong expectation has to show up as FAIL."""
    wrong = EvalCase("QX", "What is the exact context window of claude-opus-5?", SEMANTIC)
    assert run_case(bundle, wrong, AS_OF).verdict == "FAIL"


@requires_elasticsearch
def test_the_whole_set_has_no_failures(bundle) -> None:
    results = run_all(bundle, AS_OF)
    failures = [r.case.id for r in results if r.verdict == "FAIL"]
    assert not failures, f"eval regressions: {failures}"


@requires_elasticsearch
def test_hybrid_wins_both_phrasings_of_q9() -> None:
    """The claim the README publishes, pinned so it cannot rot silently."""
    from grounded_context.evaluation import compare_arms

    sentence = compare_arms("What does the rank_constant parameter do?")
    token = compare_arms("rank_constant")
    assert sentence["hybrid"] == 1 and token["hybrid"] == 1
    assert sentence["bm25"] > 1, "BM25 should degrade on the sentence phrasing"
    assert token["elser"] > 1, "ELSER should degrade on the bare identifier"
