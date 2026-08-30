"""The knowledge-store port, and the two implementations behind it.

A Protocol with one implementation documents a seam. It does not prove one. These tests run the
lookup engine against a store that has no files, no YAML and no disk, and assert it answers the
same way the Markdown bundle does.

That is the enterprise claim in one test. A production deployment reads canonical facts from a
system of record, not from Markdown. If the engine works against a store built from a dict, it
works against a store built from a database.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grounded_context.bundle import Bundle, Concept
from grounded_context.lookup import find_entity, find_field, lookup, resolve
from grounded_context.service import as_of_date, ask
from grounded_context.store import InMemoryStore, KnowledgeStore

BUNDLE = Path(__file__).resolve().parents[1] / "knowledge"


@pytest.fixture(scope="module")
def markdown_store() -> Bundle:
    return Bundle.load(BUNDLE)


@pytest.fixture(scope="module")
def memory_store(markdown_store: Bundle) -> InMemoryStore:
    """The same concepts, with the links restated as ids rather than as file paths."""
    links = {
        concept.id: [target.id for target in markdown_store.linked(concept.id)]
        for concept in markdown_store
    }
    return InMemoryStore(list(markdown_store), links)


def test_the_markdown_bundle_satisfies_the_port(markdown_store: Bundle) -> None:
    assert isinstance(markdown_store, KnowledgeStore)


def test_the_in_memory_store_satisfies_the_port(memory_store: InMemoryStore) -> None:
    assert isinstance(memory_store, KnowledgeStore)


def test_both_stores_hold_the_same_concepts(
    markdown_store: Bundle, memory_store: InMemoryStore
) -> None:
    assert len(memory_store) == len(markdown_store)
    assert {c.id for c in memory_store} == {c.id for c in markdown_store}


def test_an_exact_lookup_gives_the_same_answer_from_either_store(
    memory_store: InMemoryStore,
) -> None:
    result = lookup(memory_store, "anthropic.claude-opus-5", "context_window_tokens")
    assert result is not None
    assert result.value == 1_000_000


def test_a_one_hop_traversal_works_without_files(memory_store: InMemoryStore) -> None:
    """The hop is the part that used to need the file system.

    `Bundle.linked` resolves a Markdown link to a path and looks the path up. A store with no
    files states the link as an id instead. The engine cannot tell the difference, which is the
    whole point of the port.
    """
    result = resolve(memory_store, "anthropic.claude-opus-5", "method")
    assert result is not None
    assert result.value == "POST"
    assert result.hops == ("anthropic.claude-opus-5", "anthropic.messages")


def test_matching_works_against_the_port(memory_store: InMemoryStore) -> None:
    assert find_entity(memory_store, "how big is the opus 5 context window?") == (
        "anthropic.claude-opus-5"
    )
    assert find_field(memory_store, "how big is the opus 5 context window?") == (
        "context_window_tokens"
    )


def test_the_whole_service_answers_from_a_store_with_no_disk(
    memory_store: InMemoryStore,
) -> None:
    """`ask` is the top of the deterministic path. It takes the port, not the adapter."""
    envelope = ask(
        memory_store, "What is the exact context window of claude-opus-5?", date(2026, 8, 20)
    )
    assert envelope["answer"] == "1,000,000"
    assert envelope["citations"][0]["source_id"] == "anthropic.claude-opus-5"


def test_a_store_built_from_nothing_refuses(fresh_concept: Concept) -> None:
    """An empty store is a valid store. It answers nothing, and it does not fail."""
    store = InMemoryStore([])
    assert len(store) == 0
    assert store.get("anything") is None
    assert store.linked("anything") == []
    assert ask(store, "What is the context window of claude-opus-5?", as_of_date()) == {
        "answer": "Not found in the grounded sources.",
        "retrieval_path": "deterministic",
        "router": {
            "route": "DETERMINISTIC",
            "rationale": (
                'precision phrasing ("context window") plus a named model (claude-opus-5) '
                "— an exact fact must not be ranked"
            ),
        },
        "citations": [],
    }


def test_a_link_to_a_concept_the_store_does_not_hold_is_skipped(
    fresh_concept: Concept,
) -> None:
    """A dangling link is a data defect the Markdown adapter rejects at load time.

    An in-memory store has no load step, so it cannot reject one. It skips the target instead of
    raising a `KeyError` inside a lookup, because a partial store is a legitimate state for an
    adapter that reads a system of record page by page.
    """
    store = InMemoryStore([fresh_concept], {fresh_concept.id: ["absent.concept"]})
    assert store.linked(fresh_concept.id) == []


@pytest.fixture
def fresh_concept() -> Concept:
    return Concept(
        path=Path("memory://test"),
        id="test.concept",
        type="model",
        title="Test",
        canonical={"context_window_tokens": 42},
        stale_after=date(2099, 1, 1),
    )
