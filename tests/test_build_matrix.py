"""Tests for the generated compatibility matrix.

The matrix is a view over the model files. The risk it carries is drift — a canonical value
edited in `knowledge/` while the published table still shows the old one, or a new field added
to a model file and silently missing from the table. Both are checked here.
"""

from __future__ import annotations

from grounded_context.service import load_bundle
from scripts.build_matrix import FIELDS, MISSING, OUTPUT, models, render


def test_committed_matrix_is_up_to_date() -> None:
    """Regenerate and compare: editing a model file without rebuilding fails here."""
    assert OUTPUT.exists(), "run: uv run python scripts/build_matrix.py"
    assert OUTPUT.read_text(encoding="utf-8") == render(load_bundle())


def test_no_canonical_field_is_silently_omitted() -> None:
    """Every field the model files carry must be represented in the table."""
    listed = {name for name, _ in FIELDS}
    present = {field for concept in models(load_bundle()) for field in concept.canonical}
    assert present - listed == set()


def test_only_model_concepts_become_columns() -> None:
    bundle = load_bundle()
    assert [concept.id for concept in models(bundle)] == [
        "anthropic.claude-haiku-4-5",
        "anthropic.claude-opus-5",
        "anthropic.claude-sonnet-5",
    ]


def test_absent_field_renders_as_missing_not_blank() -> None:
    """Haiku has no Batch API output limit; that must read as absent, not as zero."""
    table = render(load_bundle())
    row = next(line for line in table.splitlines() if "max_output_tokens_batch_api" in line)
    assert MISSING in row


def test_matrix_carries_provenance_not_just_values() -> None:
    table = render(load_bundle())
    assert "## Provenance" in table
    assert "human-reviewed" in table
    assert "2026-09-09" in table


def test_output_is_deterministic() -> None:
    """No generation clock in the file — otherwise every run would look like a change."""
    assert render(load_bundle()) == render(load_bundle())
