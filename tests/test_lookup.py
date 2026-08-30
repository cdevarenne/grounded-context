from pathlib import Path

import pytest

from grounded_context.bundle import Bundle
from grounded_context.lookup import find_entity, find_field, lookup, resolve

BUNDLE = Path(__file__).resolve().parents[1] / "knowledge"


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    return Bundle.load(BUNDLE)


def test_exact_lookup_returns_the_value(bundle):
    result = lookup(bundle, "anthropic.claude-opus-5", "context_window_tokens")
    assert result.value == 1_000_000
    assert result.locator == "canonical.context_window_tokens"
    assert result.hops == ("anthropic.claude-opus-5",)


def test_haiku_inverts_its_siblings_on_thinking(bundle):
    """The per-model fact a family-level generalization gets wrong."""
    assert lookup(bundle, "anthropic.claude-haiku-4-5", "adaptive_thinking").value is False
    assert lookup(bundle, "anthropic.claude-haiku-4-5", "extended_thinking").value is True
    assert lookup(bundle, "anthropic.claude-opus-5", "adaptive_thinking").value is True
    assert lookup(bundle, "anthropic.claude-opus-5", "extended_thinking").value is False


def test_unknown_field_returns_none_not_a_guess(bundle):
    assert lookup(bundle, "anthropic.claude-opus-5", "rate_limit_rpm") is None


def test_unknown_entity_returns_none(bundle):
    assert lookup(bundle, "anthropic.claude-nonexistent", "context_window_tokens") is None


def test_one_hop_traversal_finds_a_field_on_a_linked_concept(bundle):
    """A model file doesn't restate the endpoint's HTTP method; it links to the owner."""
    assert lookup(bundle, "anthropic.claude-opus-5", "method") is None
    result = resolve(bundle, "anthropic.claude-opus-5", "method")
    assert result.value == "POST"
    assert result.concept.id == "anthropic.messages"
    assert result.hops == ("anthropic.claude-opus-5", "anthropic.messages")


def test_traversal_can_be_disabled(bundle):
    assert resolve(bundle, "anthropic.claude-opus-5", "method", max_hops=0) is None


def test_traversal_does_not_invent_a_hit(bundle):
    assert resolve(bundle, "anthropic.claude-opus-5", "not_a_field") is None


def test_find_entity_prefers_the_longest_match(bundle):
    """The pinned snapshot id must not be shadowed by its own alias."""
    assert (
        find_entity(bundle, "what is the model id claude-haiku-4-5-20251001")
        == "anthropic.claude-haiku-4-5"
    )
    assert find_entity(bundle, "context window of claude-opus-5") == (
        "anthropic.claude-opus-5"
    )
    assert find_entity(bundle, "how do I chunk documents") is None


def test_find_field_matches_names_and_synonyms(bundle):
    entity = "anthropic.claude-opus-5"
    assert find_field(bundle, "context window", entity) == "context_window_tokens"
    assert find_field(bundle, "max output tokens", entity) == "max_output_tokens"
    assert find_field(bundle, "what is the input price", entity) == (
        "input_price_per_mtok_usd"
    )
    assert find_field(bundle, "does it do vision", entity) == "vision"
    assert find_field(bundle, "nothing relevant at all", entity) is None


def test_find_field_sees_fields_one_hop_away(bundle):
    assert find_field(bundle, "the method", "anthropic.claude-opus-5") == "method"


def test_sonnet_carries_both_standard_and_introductory_pricing(bundle):
    """Both are exact; which applies depends on the date the question is asked."""
    entity = "anthropic.claude-sonnet-5"
    assert lookup(bundle, entity, "input_price_per_mtok_usd").value == 3.0
    assert lookup(bundle, entity, "introductory_input_price_per_mtok_usd").value == 2.0
    assert str(lookup(bundle, entity, "introductory_pricing_ends").value) == "2026-08-31"


@pytest.mark.parametrize(
    "phrasing, expected",
    [
        ("Opus 5", "anthropic.claude-opus-5"),
        ("opus", "anthropic.claude-opus-5"),
        ("the Opus model", "anthropic.claude-opus-5"),
        ("Sonnet 5", "anthropic.claude-sonnet-5"),
        ("Haiku 4.5", "anthropic.claude-haiku-4-5"),
    ],
)
def test_a_model_resolves_by_the_names_people_use(bundle, phrasing, expected):
    """A canonical layer is only authoritative over the questions it can recognise.

    Without aliases the lookup answered only to the hyphenated identifier, so "Opus 5" resolved
    to nothing and the query fell through to ranked passages — a confident, cited, adjacent
    answer to a question the bundle held exactly. That is the failure this project exists to
    prevent, and it was self-inflicted.
    """
    assert find_entity(bundle, phrasing) == expected


def test_the_canonical_identifier_still_wins_over_a_short_alias(bundle):
    """Longest match, so a bare "opus" cannot shadow a pinned id that contains it."""
    assert find_entity(bundle, "claude-haiku-4-5-20251001") == "anthropic.claude-haiku-4-5"
    assert find_entity(bundle, "ctx window for claude-opus-5") == "anthropic.claude-opus-5"


def test_an_abbreviated_field_still_resolves(bundle):
    """`ctx window` is what people type; `context_window_tokens` is what the bundle calls it."""
    assert find_field(bundle, "whats the ctx window for opus") == "context_window_tokens"
    assert find_field(bundle, "context length of sonnet 5") == "context_window_tokens"


# --- whole-term matching (ELX-44) ---------------------------------------------------------
#
# Plain substring containment answered questions it had no business answering. These are the
# cases that were wrong, and the ones that must keep working after the fix.


def test_a_field_name_buried_inside_another_word_is_not_a_match(bundle):
    """The defect: `vision` sits inside `revision`, and the answer looked authoritative.

    "What is the exact revision number for opus 5?" resolved to `canonical.vision`, answered
    `yes`, and cited it. Not the refusal — a wrong fact with provenance attached, which is the
    one outcome the deterministic path exists to rule out. A miss here has to reach the refusal.
    """
    query = "What is the exact revision number for opus 5?"
    assert find_entity(bundle, query) == "anthropic.claude-opus-5"
    assert find_field(bundle, query, "anthropic.claude-opus-5") is None


@pytest.mark.parametrize(
    "query",
    [
        "What revision of claude-opus-5 is current?",
        "Does the provisioning API need claude-opus-5?",
        "list the aliases for claude-opus-5",
    ],
)
def test_no_field_is_matched_from_inside_a_longer_word(bundle, query):
    assert find_field(bundle, query, "anthropic.claude-opus-5") is None


def test_an_identifier_still_matches_inside_a_longer_identifier(bundle):
    """Hyphens are not word characters, so pinned ids keep resolving — and longest still wins.

    This is why the rule is "no *word* character either side" rather than "surrounded by
    whitespace": the latter would break every hyphenated model id the bundle is addressed by.
    """
    assert find_entity(bundle, "claude-haiku-4-5-20251001") == "anthropic.claude-haiku-4-5"


def test_contains_matches_a_whole_term_only():
    from grounded_context.lookup import contains

    assert contains("does it do vision", "vision")
    assert contains("VISION?", "vision"), "comparison is case-insensitive"
    assert not contains("a revision number", "vision")
    assert not contains("provisioning", "vision")
    assert contains("claude-haiku-4-5-20251001", "claude-haiku-4-5"), "hyphen is not a word char"


# --- rollups and injectable vocabulary (ELX-56, ELX-58) ---------------------------------------


def test_a_rollup_answers_one_field_for_every_entity(bundle):
    """`lookup` answers one entity at a time. A rollup asks the same field of every entity.

    The question had no engine before this. It fell through to ranked passages, which discuss
    the topic and do not answer the question. Each result keeps its own concept, so each keeps
    its own provenance: a rollup is a list of exact facts, not a summary of them.
    """
    from grounded_context.lookup import query_entities

    results = query_entities(bundle, "vision")

    assert [r.concept.id for r in results] == [
        "anthropic.claude-haiku-4-5",
        "anthropic.claude-opus-5",
        "anthropic.claude-sonnet-5",
    ], "concept-id order, so a rendered list is stable between runs"
    assert all(r.value is True for r in results)
    assert all(r.locator == "canonical.vision" for r in results)
    assert all(r.concept.trust_tier == "human-reviewed" for r in results)


def test_a_rollup_on_a_field_no_concept_holds_is_empty(bundle):
    from grounded_context.lookup import query_entities

    assert query_entities(bundle, "rate_limit_rpm") == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which of these models support vision?", True),
        ("Which models support vision?", True),
        ("every model that does vision", True),
        # Names one entity, so the single-entity path is correct and must win.
        ("Does Opus 5 support vision?", False),
        ("What is the context window of claude-opus-5?", False),
    ],
)
def test_a_rollup_is_recognised_only_when_no_single_entity_is_named(question, expected):
    """Narrow on purpose. A rollup that fires on the wrong question is worse than one that does
    not fire, because the single-entity path and the refusal are both correct fallbacks."""
    from grounded_context.lookup import is_rollup

    assert is_rollup(question) is expected


def test_an_adopter_can_supply_their_own_synonyms(bundle):
    """Matching vocabulary is not canonical truth, so it is a parameter and not a bundle field.

    An OKF file is governed by a verification date and a trust tier. A query synonym has neither.
    Putting one in the bundle would give it a governance model it does not need and cannot honour.
    """
    assert find_field(bundle, "whats the token limit for opus") is None
    assert find_field(
        bundle, "whats the token limit for opus", synonyms={"token limit": "max_output_tokens"}
    ) == "max_output_tokens"
