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
    except Exception:
        return False


requires_index = pytest.mark.skipif(
    not _index_ready(), reason="no ES_URL / ES_API_KEY, or the corpus index is missing"
)


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
def test_the_subfield_helps_hyphenated_identifiers_and_no_underscore_ones() -> None:
    """findings.md publishes 44 of 149 and 0 of 87; the zero is the load-bearing half."""
    from grounded_context.es_client import client

    es = client()
    sweep = measure_findings.sweep_subfield_effect(es, measure_findings._all_chunks(es))

    assert sweep["underscored"]["improved"] == 0, (
        "the standard analyzer already keeps underscores whole — if this is nonzero the "
        "finding's central claim has changed"
    )
    assert sweep["hyphenated"]["improved"] > 0


@requires_index
def test_the_fused_score_ranks_an_off_topic_question_above_a_genuine_one() -> None:
    """Finding 3, in its strongest form: the fused ranges do not merely overlap.

    If this ever stops holding, the finding is overstated and findings.md must be softened.
    """
    from grounded_context.es_client import client

    probes = measure_findings.probe_scores(client())
    off_topic = [row["fused"] for row in probes if row["kind"] == "off-topic"]
    genuine = [row["fused"] for row in probes if row["kind"] == "in-domain"]

    assert max(off_topic) > min(genuine), "fused scores now separate — claim is too strong"


@requires_index
def test_the_pre_fusion_score_is_what_actually_separates() -> None:
    """The counterpart: sparse scores keep the magnitude the fused ones discard."""
    from grounded_context.es_client import client
    from grounded_context.semantic import RELEVANCE_FLOOR

    probes = measure_findings.probe_scores(client())
    genuine = [row["sparse"] for row in probes if row["kind"] == "in-domain"]
    off_topic = sorted(row["sparse"] for row in probes if row["kind"] == "off-topic")

    assert min(genuine) > RELEVANCE_FLOOR, "a genuine question fell below the floor"
    # Nine of ten sit well below it; the tenth is the declared marathon leaker.
    assert off_topic[-2] < RELEVANCE_FLOOR
