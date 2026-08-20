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
