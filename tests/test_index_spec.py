"""The index spec is duplicated across repos, so something has to compare the copies.

Each repo carries its own `docs/index-spec.md` so it stands alone — the same reasoning that
duplicates `knowledge/` into the JVM repo. Two copies of a contract diverge unless a test says
otherwise, and a diverged index spec means two implementations quietly building different
indices from the same corpus.

Skipped when the JVM repo is not checked out alongside; point `GCTX_JVM_REPO` elsewhere if it
lives somewhere other than a sibling directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "index-spec.md"

DEFAULT_JVM_REPO = ROOT.parent / "grounded-context-jvm"

# The copies differ on one line by design: each points at the other repo.
CROSS_LINK = "grounded-context"


def jvm_spec() -> Path:
    configured = os.environ.get("GCTX_JVM_REPO")
    root = Path(configured) if configured else DEFAULT_JVM_REPO
    return root / "docs" / "index-spec.md"


requires_jvm_repo = pytest.mark.skipif(
    not jvm_spec().is_file(), reason="the JVM repo is not checked out alongside"
)


@requires_jvm_repo
def test_the_two_index_specs_agree_apart_from_their_cross_link() -> None:
    ours = SPEC.read_text(encoding="utf-8").splitlines()
    theirs = jvm_spec().read_text(encoding="utf-8").splitlines()

    assert len(ours) == len(theirs), "the index specs have diverged in length"

    differing = [
        (number, mine, other)
        for number, (mine, other) in enumerate(zip(ours, theirs), 1)
        if mine != other
    ]
    # Exactly one line may differ, and only because each copy links to the other repo.
    assert len(differing) <= 1, f"index specs differ on {len(differing)} lines: {differing[:3]}"
    for _, mine, other in differing:
        assert CROSS_LINK in mine and CROSS_LINK in other


@requires_jvm_repo
def test_the_spec_still_states_the_constants_the_code_uses() -> None:
    """A spec that drifts from the code is worse than no spec: it is a confident wrong answer."""
    from grounded_context.semantic import (
        EXACT_TOKEN_BOOST,
        RANK_CONSTANT,
        RANK_WINDOW_SIZE,
        RELEVANCE_FLOOR,
    )

    text = SPEC.read_text(encoding="utf-8")
    for label, value in (
        ("`RANK_CONSTANT` (RRF `k`) | 20", RANK_CONSTANT),
        ("`RANK_WINDOW_SIZE` | 50", RANK_WINDOW_SIZE),
        ("`EXACT_TOKEN_BOOST` | 3.0", EXACT_TOKEN_BOOST),
        ("`RELEVANCE_FLOOR` | 8.0", RELEVANCE_FLOOR),
    ):
        assert label in text, f"the spec no longer states {label!r}"
        assert str(value) in label, f"the code changed {label!r} to {value}"


def test_the_spec_states_the_chunking_constants_the_indexer_uses() -> None:
    from scripts.index_corpus import MIN_CHUNK_CHARS, TARGET_CHUNK_CHARS

    text = SPEC.read_text(encoding="utf-8")
    assert f"`TARGET_CHUNK_CHARS` | {TARGET_CHUNK_CHARS}" in text
    assert f"`MIN_CHUNK_CHARS` | {MIN_CHUNK_CHARS}" in text
