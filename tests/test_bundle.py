from datetime import date
from pathlib import Path

import pytest

from grounded_context.bundle import (
    HUMAN_REVIEWED,
    MACHINE_CONFIRMED,
    UNVERIFIED,
    Bundle,
    BundleError,
    parse_concept,
)

BUNDLE = Path(__file__).resolve().parents[1] / "knowledge"


def write(path: Path, front_matter: str, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front_matter.strip()}\n---\n{body}\n", encoding="utf-8")
    return path


def test_real_bundle_loads():
    bundle = Bundle.load(BUNDLE)
    assert len(bundle) == 4
    assert {c.id for c in bundle} == {
        "anthropic.claude-opus-5",
        "anthropic.claude-sonnet-5",
        "anthropic.claude-haiku-4-5",
        "anthropic.messages",
    }


def test_every_concept_carries_provenance_and_freshness():
    """The governance guarantee: no canonical fact without a source and an expiry."""
    for concept in Bundle.load(BUNDLE):
        assert concept.source_url, f"{concept.id} has no source"
        assert concept.stale_after is not None, f"{concept.id} has no stale_after"
        assert concept.trust_tier == HUMAN_REVIEWED
        assert concept.verified_at


def test_api_version_stays_a_string():
    """2023-06-01 unquoted would parse as a YAML date and break the header."""
    concept = Bundle.load(BUNDLE).get("anthropic.messages")
    assert concept.canonical["api_version"] == "2023-06-01"


def test_trust_tier_is_derived_from_actors(tmp_path):
    def tier(verified: str) -> str:
        path = write(
            tmp_path / f"{abs(hash(verified))}.md",
            f"type: concept\nid: t\n{verified}",
        )
        return parse_concept(path).trust_tier

    assert tier("") == UNVERIFIED
    assert tier("verified:\n  - by: machine:fetcher\n    at: 2026-08-01T00:00:00Z") == (
        MACHINE_CONFIRMED
    )
    assert tier("verified:\n  - by: human:cdev\n    at: 2026-08-01T00:00:00Z") == (
        HUMAN_REVIEWED
    )


def test_single_verified_mapping_without_list_dash(tmp_path):
    """OKF permits `verified` as one mapping rather than a list."""
    path = write(
        tmp_path / "m.md",
        "type: concept\nid: t\nverified:\n  by: human:cdev\n  at: 2026-08-01T00:00:00Z",
    )
    assert parse_concept(path).trust_tier == HUMAN_REVIEWED


def test_staleness_is_an_absolute_date_comparison(tmp_path):
    path = write(tmp_path / "m.md", "type: concept\nid: t\nstale_after: 2026-09-10")
    concept = parse_concept(path)
    assert not concept.is_stale(date(2026, 9, 9))
    assert concept.is_stale(date(2026, 9, 10))  # today >= stale_after
    assert concept.is_stale(date(2026, 12, 1))


def test_no_stale_after_is_never_stale(tmp_path):
    path = write(tmp_path / "m.md", "type: concept\nid: t")
    assert not parse_concept(path).is_stale(date(2099, 1, 1))


def test_rejects_missing_front_matter(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("no front matter here\n", encoding="utf-8")
    with pytest.raises(BundleError, match="no YAML front matter"):
        parse_concept(path)


def test_rejects_missing_required_fields(tmp_path):
    path = write(tmp_path / "m.md", "title: no type or id")
    with pytest.raises(BundleError, match="missing required field"):
        parse_concept(path)


def test_rejects_non_date_stale_after(tmp_path):
    path = write(tmp_path / "m.md", 'type: concept\nid: t\nstale_after: "soon"')
    with pytest.raises(BundleError, match="must be a YYYY-MM-DD date"):
        parse_concept(path)


def test_rejects_duplicate_ids(tmp_path):
    write(tmp_path / "a.md", "type: concept\nid: dup")
    write(tmp_path / "b.md", "type: concept\nid: dup")
    with pytest.raises(BundleError, match="duplicate concept id"):
        Bundle.load(tmp_path)


def test_rejects_dangling_link(tmp_path):
    write(
        tmp_path / "a.md",
        'type: concept\nid: a\nlinks:\n  - "[gone](missing.md)"',
    )
    with pytest.raises(BundleError, match="link target missing"):
        Bundle.load(tmp_path)


def test_rejects_link_escaping_the_bundle(tmp_path):
    write(
        tmp_path / "nested" / "a.md",
        'type: concept\nid: a\nlinks:\n  - "[out](../../etc/passwd)"',
    )
    with pytest.raises(BundleError, match="escapes the bundle root"):
        Bundle.load(tmp_path / "nested")


def test_links_resolve_both_ways():
    bundle = Bundle.load(BUNDLE)
    linked = {c.id for c in bundle.linked("anthropic.claude-opus-5")}
    assert "anthropic.messages" in linked
    back = {c.id for c in bundle.linked("anthropic.messages")}
    assert "anthropic.claude-opus-5" in back


def test_timestamps_are_quoted_not_reformatted():
    """A verification stamp must come back exactly as the file writes it.

    PyYAML resolves timestamp-shaped scalars to datetime by default, and str() then renders
    them with a space instead of the `T` — not valid ISO-8601, and not what the bundle says.
    Cross-checking against the JVM port is what surfaced it.
    """
    concept = Bundle.load(BUNDLE).get("anthropic.claude-opus-5")
    assert concept.verified_at == "2026-08-10T19:06:23-07:00"
    assert " " not in concept.verified_at


def test_a_lifecycle_date_is_still_a_real_date():
    """Keeping timestamps as text must not turn stale_after into a string comparison."""
    concept = Bundle.load(BUNDLE).get("anthropic.claude-opus-5")
    assert concept.stale_after == date(2026, 9, 9)
    assert concept.is_stale(date(2026, 9, 8)) is False
    assert concept.is_stale(date(2026, 9, 9)) is True
