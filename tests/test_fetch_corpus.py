"""Tests for the corpus fetcher and the curated manifest.

No network. The fetch itself is one `urlopen` call not worth mocking; what is worth testing
is the text extraction, and the manifest — a hand-edited YAML file whose ids become filenames
and whose URLs decide what gets retrieved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fetch_corpus import MANIFEST, Source, TextExtractor, load_manifest


def extract(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def test_extractor_drops_scripts_and_navigation_chrome() -> None:
    text = extract(
        "<nav>Docs / Search</nav><script>var x = 1;</script>"
        "<style>.a{color:red}</style><p>Reciprocal rank fusion combines result sets.</p>"
    )
    assert text == "Reciprocal rank fusion combines result sets."


def test_extractor_keeps_exact_parameter_tokens() -> None:
    """Q9 depends on tokens like rank_constant surviving extraction intact."""
    assert "rank_constant" in extract("<p>The <code>rank_constant</code> value.</p>")


def test_manifest_ids_are_unique_and_filename_safe() -> None:
    sources = load_manifest()
    ids = [source.id for source in sources]
    assert len(ids) == len(set(ids))
    assert all(id.replace("-", "").replace("_", "").isalnum() for id in ids)


def test_manifest_entries_are_complete_and_https() -> None:
    for source in load_manifest():
        assert source.url.startswith("https://"), source.id
        assert source.title and source.provider and source.topic, source.id


def test_manifest_covers_both_providers_the_specs_name() -> None:
    providers = {source.provider for source in load_manifest()}
    assert {"elastic", "anthropic"} <= providers


def test_fetched_text_is_written_outside_version_control(tmp_path: Path) -> None:
    """corpus/raw is gitignored; the manifest is the only committed half."""
    source = Source(
        id="x", title="t", url="https://example.test/x", provider="elastic", topic="t"
    )
    assert source.destination.parent.name == "raw"
    assert "corpus" in source.destination.parts


def test_manifest_parses_from_disk() -> None:
    assert MANIFEST.exists()
    assert len(load_manifest()) >= 10


def test_unknown_manifest_field_is_rejected_rather_than_ignored() -> None:
    with pytest.raises(TypeError):
        Source(id="x", title="t", url="u", provider="p", topic="t", extra="oops")  # type: ignore[call-arg]
