"""Tests for the eval harness.

The harness exists to catch the system drifting away from its own spec, so what matters here
is that it cannot quietly pass: a declared expectation that stops matching must surface, and a
known deviation must never be counted as a pass.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from grounded_context.es_client import is_configured
from grounded_context.evaluation import CASES, EvalCase, run_all, run_case
from grounded_context.provenance import DETERMINISTIC, MIXED, NOT_FOUND, SEMANTIC
from grounded_context.router import DETERMINISTIC as ROUTE_DETERMINISTIC
from grounded_context.router import SEMANTIC as ROUTE_SEMANTIC
from grounded_context.service import load_bundle

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 8, 13)
VALID = {DETERMINISTIC, SEMANTIC, MIXED, "refusal"}

requires_elasticsearch = pytest.mark.skipif(
    not is_configured(), reason="no ES_URL / ES_API_KEY — semantic cases unavailable"
)


@pytest.fixture(scope="module")
def bundle():
    return load_bundle()


def test_the_set_matches_the_ids_the_spec_lists() -> None:
    """The spec is the contract, so the guard reads it rather than restating its length.

    This previously pinned a hardcoded `range(1, 13)`, which meant adding a case to the set and
    a row to the spec still failed until someone bumped a third number that was not the truth
    of either. Reading the table makes the spec and the code the only two things that can drift.
    """
    spec = (Path(__file__).resolve().parents[1] / "docs" / "specs" / "eval.md").read_text(
        encoding="utf-8"
    )
    documented = re.findall(r"^\| (Q\d+) \|", spec, flags=re.MULTILINE)

    assert documented, "the spec no longer lists any cases"
    assert [case.id for case in CASES] == documented


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
    wrong = EvalCase("QX", "What is the exact context window of claude-opus-5?", SEMANTIC,
                     ROUTE_DETERMINISTIC)
    assert run_case(bundle, wrong, AS_OF).verdict == "FAIL"


def test_the_right_answer_by_the_wrong_route_still_fails(bundle) -> None:
    """The reason `expected_route` exists.

    This case answers exactly as its expectation says it should. Only the route is wrong, and
    that is enough to fail it — otherwise Q19 could stop exercising the relevance floor and the
    suite would stay green, because a refusal reached through the deterministic path is still
    a refusal.
    """
    right_answer_wrong_path = EvalCase(
        "QX", "What is the exact context window of claude-opus-5?", DETERMINISTIC, ROUTE_SEMANTIC
    )
    result = run_case(bundle, right_answer_wrong_path, AS_OF)
    assert result.actual == DETERMINISTIC, "the answer itself is what the case expected"
    assert not result.routed_as_expected
    assert result.verdict == "FAIL"


def test_every_case_declares_the_route_it_expects() -> None:
    """`expected_route` has a default, so a new case can be added without one. This catches it."""
    missing = [case.id for case in CASES if not case.expected_route]
    assert not missing, f"cases with no expected_route: {missing}"


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


@requires_elasticsearch
@pytest.mark.parametrize(
    "query",
    [
        "rank_constant",
        "What does the rank_constant parameter do?",
        "num_candidates",
        "What does the num_candidates parameter do?",
        "rank_window_size",
        "What does the rank_window_size parameter do?",
        "anthropic-ratelimit-tokens-reset",
        "What does the anthropic-ratelimit-tokens-reset header do?",
    ],
)
def test_fusion_is_never_worse_than_the_weaker_arm(query: str) -> None:
    """The claim findings.md actually makes, across every row of its table.

    Deliberately not "hybrid wins": it does not always beat the stronger arm.
    """
    from grounded_context.evaluation import compare_arms

    ranks = compare_arms(query)
    assert all(rank is not None for rank in ranks.values()), f"{query}: fell out of top 20"
    assert ranks["hybrid"] <= max(ranks["elser"], ranks["bm25"])


@requires_elasticsearch
def test_the_counter_example_where_fusion_loses_to_bm25() -> None:
    """`rank_window_size` is what stops the table being read as 'hybrid always wins'.

    If a re-index ever makes fusion win here too, this fails — and findings.md needs its
    claim widened rather than left understated.
    """
    from grounded_context.evaluation import compare_arms

    for query in ["rank_window_size", "What does the rank_window_size parameter do?"]:
        ranks = compare_arms(query)
        assert ranks["bm25"] < ranks["hybrid"], f"{query}: fusion no longer loses to BM25"


# --- the published table, against the recorded run ------------------------------------------
#
# docs/eval-output.md prints the verdict table, and nothing compared it to a run until now. It
# went stale once already: the capture was regenerated by hand after Q19 and Q20 were added, and
# had that been forgotten, the document would have published a nineteen-row table indefinitely.
#
# The split mirrors the figures. This file's bare-clone tests compare the document to
# docs/data/eval.json, so a doc that drifted is caught with no credentials. The cluster-gated
# test re-runs the eval and asserts the recorded run still holds, which is what catches a
# document that is faithful to a result that stopped being true.

EVAL_DATA = json.loads((ROOT / "docs" / "data" / "eval.json").read_text(encoding="utf-8"))
EVAL_OUTPUT = (ROOT / "docs" / "eval-output.md").read_text(encoding="utf-8")

#: The only cases allowed to report KNOWN. `known_deviation` silences a failure, so the set of
#: cases carrying one is pinned here rather than in the module it excuses: adding a deviation
#: then requires editing a test, which is a visible act, instead of turning the suite green
#: quietly. Both are documented in docs/specs/eval.md.
DECLARED_DEVIATIONS = {"Q3", "Q20"}

TABLE_ROW = re.compile(r"^(Q\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w+)$", re.MULTILINE)
TOTALS_LINE = re.compile(r"^(\d+) pass · (\d+) known deviation · (\d+) fail$", re.MULTILINE)


def published_rows() -> dict[str, dict[str, str]]:
    """The verdict table as published, keyed by case id."""
    rows = {
        case_id: {"expected": expected, "actual": actual,
                  "route": route, "citations": cites, "verdict": verdict}
        for case_id, expected, actual, route, cites, verdict in TABLE_ROW.findall(EVAL_OUTPUT)
    }
    assert len(rows) == len(CASES), f"published table has {len(rows)} rows, the set has {len(CASES)}"
    return rows


def test_only_the_declared_cases_carry_a_known_deviation() -> None:
    """A new deviation must be added here deliberately, not discovered later in a green suite."""
    carrying = {case.id for case in CASES if case.known_deviation}
    assert carrying == DECLARED_DEVIATIONS, (
        f"declared deviations changed: {carrying ^ DECLARED_DEVIATIONS}. If this is intended, "
        f"update DECLARED_DEVIATIONS and docs/specs/eval.md together."
    )


@pytest.mark.parametrize("case_id", [case.id for case in CASES])
def test_the_published_table_matches_the_recorded_run(case_id: str) -> None:
    published = published_rows()[case_id]
    recorded = next(c for c in EVAL_DATA["cases"] if c["id"] == case_id)
    assert published == {
        "expected": recorded["expected"], "actual": recorded["actual"],
        "route": recorded["route"], "citations": str(recorded["citations"]),
        "verdict": recorded["verdict"],
    }


def test_the_published_totals_match_the_recorded_run() -> None:
    match = TOTALS_LINE.search(EVAL_OUTPUT)
    assert match, "eval-output.md has no totals line"
    totals = EVAL_DATA["totals"]
    assert [int(n) for n in match.groups()] == [totals["pass"], totals["known"], totals["fail"]]


def test_the_recorded_run_records_a_cluster_was_reachable() -> None:
    """Without one every semantic case refuses, which would publish twenty false results."""
    assert EVAL_DATA["run"]["elasticsearch_configured"] is True


@requires_elasticsearch
def test_the_recorded_run_still_reproduces(bundle) -> None:
    """The half the bare-clone tests cannot check: is the recorded result still true?"""
    live = {r.case.id: r.as_dict() for r in run_all(bundle, AS_OF)}
    moved = [
        c["id"] for c in EVAL_DATA["cases"]
        if {k: live[c["id"]][k] for k in ("actual", "route", "citations", "verdict")}
        != {k: c[k] for k in ("actual", "route", "citations", "verdict")}
    ]
    assert not moved, (
        f"the eval no longer matches docs/data/eval.json for {moved} — "
        f"re-run scripts/publish_eval.py and update eval-output.md"
    )
