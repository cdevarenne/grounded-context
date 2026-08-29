"""Tests for the findings sweep.

The script exists so the corpus-wide numbers in docs/findings.md are regenerable rather than
asserted, so what matters is that its identifier extraction is right — a sloppy regex would
silently change 44 of 149 into some other pair — and that the published claims still hold
against the live index.
"""

from __future__ import annotations

import pytest

from grounded_context.es_client import INDEX, is_configured
from scripts import measure_findings


def _index_ready() -> bool:
    if not is_configured():
        return False
    from grounded_context.es_client import client

    try:
        return bool(client().indices.exists(index=INDEX))
    except Exception:  # noqa: BLE001 - any failure to reach the cluster means skip, not crash
        return False


requires_index = pytest.mark.skipif(
    not _index_ready(), reason="no ES_URL / ES_API_KEY, or the corpus index is missing"
)


# The sweeps issue hundreds of queries each, so every live test shares one run of them.


@pytest.fixture(scope="module")
def es():
    from grounded_context.es_client import client

    return client()


@pytest.fixture(scope="module")
def chunks(es):
    return measure_findings._all_chunks(es)


@pytest.fixture(scope="module")
def sweep(es, chunks):
    return measure_findings.sweep_subfield_effect(es, chunks)


@pytest.fixture(scope="module")
def hidden(es, chunks):
    return measure_findings.sweep_invisible_to_exact(es, chunks)


@pytest.fixture(scope="module")
def probes(es):
    return measure_findings.probe_scores(es)


@pytest.fixture(scope="module")
def heldout(es):
    return measure_findings.heldout_floor_check(es)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("claude-opus-5", {"claude-opus-5"}),
        ("rank_constant", set()),
        ('the "claude-sonnet-4-6" model', {"claude-sonnet-4-6"}),
        ("no identifiers here", set()),
    ],
)
def test_hyphenated_pattern_matches_only_hyphenated_identifiers(
    text: str, expected: set[str]
) -> None:
    assert set(measure_findings.HYPHENATED.findall(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rank_constant", {"rank_constant"}),
        ("claude-opus-5", set()),
        ('"num_candidates": 100', {"num_candidates"}),
    ],
)
def test_underscored_pattern_matches_only_underscored_identifiers(
    text: str, expected: set[str]
) -> None:
    assert set(measure_findings.UNDERSCORED.findall(text)) == expected


def test_unique_to_one_chunk_ignores_terms_that_appear_twice() -> None:
    """The rank sweep is only meaningful when there is exactly one right chunk to find."""
    chunks = [
        {"content": "alpha-beta-one shared-term", "source_id": "a", "chunk_index": 0},
        {"content": "gamma-delta-two shared-term", "source_id": "b", "chunk_index": 1},
    ]
    unique = measure_findings._unique_to_one_chunk(
        chunks, measure_findings.HYPHENATED, min_len=5
    )
    assert set(unique) == {"alpha-beta-one", "gamma-delta-two"}
    assert unique["alpha-beta-one"] == ("a", 0)


def test_a_term_counts_once_per_chunk_however_often_it_repeats() -> None:
    """Otherwise a term repeated in one chunk would look like it spans several."""
    chunks = [{"content": "a-b a-b a-b", "source_id": "s", "chunk_index": 3}]
    unique = measure_findings._unique_to_one_chunk(
        chunks, measure_findings.HYPHENATED, min_len=1
    )
    assert unique == {"a-b": ("s", 3)}


# --- live cluster: the published aggregates ------------------------------------------


@requires_index
def test_the_subfield_helps_hyphenated_identifiers_and_no_underscore_ones(sweep) -> None:
    """findings.md publishes 44 of 149 and 0 of 87; the zero is the load-bearing half."""
    assert sweep["underscored"]["improved"] == 0, (
        "the standard analyzer already keeps underscores whole — if this is nonzero the "
        "finding's central claim has changed"
    )
    assert sweep["hyphenated"]["improved"] > 0


@requires_index
def test_no_term_regresses_when_the_exact_clause_is_added(sweep) -> None:
    """What makes "improved" a safe word: the exact clause can only match the target chunk.

    If anything ever regresses, the sweep's counts stop meaning "improved" and findings.md
    would be overstating them.
    """
    assert all(shape["regressed"] == 0 for shape in sweep.values())


@requires_index
def test_the_tokens_findings_md_cites_are_really_invisible_to_the_exact_field(hidden) -> None:
    """The prose names two examples; this is what stops them being decoration."""
    assert hidden["documented"], "no documented examples declared"
    assert all(hidden["documented"].values()), (
        f"findings.md cites tokens not in the set: {hidden['documented']}"
    )


@requires_index
def test_the_mechanism_numbers_quoted_in_findings_md(es) -> None:
    """The 6 / 1 / rank 3 -> 1 walkthrough, which was asserted before it was emitted."""
    mech = measure_findings.mechanism_counts(es)
    assert mech["content_matches"] > mech["exact_matches"] == 1
    assert mech["rank_with_exact"] < mech["rank_content_only"]


@requires_index
def test_the_fused_score_ranks_an_off_topic_question_above_a_genuine_one(probes) -> None:
    """Finding 3, in its strongest form: the fused ranges do not merely overlap.

    If this ever stops holding, the finding is overstated and findings.md must be softened.
    """
    off_topic = [row["fused"] for row in probes if row["kind"] == "off-topic"]
    genuine = [row["fused"] for row in probes if row["kind"] == "in-domain"]

    assert max(off_topic) > min(genuine), "fused scores now separate — claim is too strong"


@requires_index
def test_the_pre_fusion_score_is_what_actually_separates(probes) -> None:
    """The counterpart: sparse scores keep the magnitude the fused ones discard."""
    from grounded_context.semantic import RELEVANCE_FLOOR

    genuine = [row["sparse"] for row in probes if row["kind"] == "in-domain"]
    off_topic = sorted(row["sparse"] for row in probes if row["kind"] == "off-topic")

    assert min(genuine) > RELEVANCE_FLOOR, "a genuine question fell below the floor"
    # Nine of ten sit well below it; the tenth is the declared marathon leaker.
    assert off_topic[-2] < RELEVANCE_FLOOR


def test_the_heldout_probes_are_disjoint_from_the_ones_the_floor_was_derived_from() -> None:
    """The held-out set is only evidence if it shares no query with the tuning set."""
    tuning = set(measure_findings.OFF_TOPIC + measure_findings.IN_DOMAIN
                 + measure_findings.WRONG_ENTITY)
    held = set(measure_findings.OFF_TOPIC_HELDOUT + measure_findings.IN_DOMAIN_HELDOUT)
    assert not tuning & held
    assert len(held) == len(measure_findings.OFF_TOPIC_HELDOUT) + len(
        measure_findings.IN_DOMAIN_HELDOUT
    )


@requires_index
def test_the_floor_generalizes_to_probes_it_was_not_derived_from(heldout) -> None:
    """The claim Phase 2 of docs/specs/single-call-retrieval.md rests on.

    A floor chosen against sixteen probes could be fitted to them. On thirty it never saw, it
    misclassifies none — which is what makes it a guardrail rather than a coincidence.
    """
    assert heldout["false_accepts"] == []
    assert heldout["false_rejects"] == []
    assert heldout["off_topic_max"] < heldout["floor"] < heldout["in_domain_min"]


# --- the separability metrics ---------------------------------------------------------
#
# Every percentage and AUC published in findings.md §3, §4 and the single-call spec comes out
# of these four functions. They are pure, so they get pinned against hand-computable cases
# rather than against the index — an arithmetic slip here would rewrite the conclusions
# silently and no ES-backed test would notice.


def test_auc_is_one_when_the_classes_do_not_overlap() -> None:
    assert measure_findings.auc([10.0, 20.0], [1.0, 2.0]) == 1.0


def test_auc_is_zero_point_five_when_the_classes_are_identical() -> None:
    """Every pair ties, and a tie counts half — the no-signal baseline."""
    assert measure_findings.auc([5.0, 5.0], [5.0, 5.0]) == 0.5


def test_auc_counts_pairs_not_extremes() -> None:
    """One off-topic query above everything costs 3 of 9 pairs, not the whole score.

    This is the property the metric was added for: the margin goes negative on this input
    while the AUC records that six pairs out of nine are still ordered correctly.
    """
    genuine, off_topic = [10.0, 11.0, 12.0], [1.0, 2.0, 99.0]
    assert measure_findings.auc(genuine, off_topic) == pytest.approx(6 / 9)
    assert measure_findings.margin(genuine, off_topic) < 0


def test_margin_is_the_gap_as_a_share_of_the_lowest_genuine_score() -> None:
    assert measure_findings.margin([10.0, 20.0], [2.0, 5.0]) == pytest.approx(50.0)


def test_margin_is_negative_when_the_classes_overlap() -> None:
    assert measure_findings.margin([10.0], [12.0]) == pytest.approx(-20.0)


def test_tuning_midpoint_sits_halfway_between_the_classes() -> None:
    assert measure_findings.tuning_midpoint([18.0], [12.0]) == pytest.approx(15.0)


def test_tuning_midpoint_is_none_when_no_threshold_separates() -> None:
    """w=0.1 in the sweep: the tuning classes overlap, so there is no floor to carry forward."""
    assert measure_findings.tuning_midpoint([12.0], [18.0]) is None


def test_confusion_counts_a_floor_applied_to_scores_it_did_not_choose() -> None:
    """At the floor exactly, an off-topic query is accepted — `>=` matches `is_relevant`."""
    accepts, rejects = measure_findings.confusion([9.0, 4.0], [8.0, 1.0], floor=8.0)
    assert (accepts, rejects) == (1, 1)
