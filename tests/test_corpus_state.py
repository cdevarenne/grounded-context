"""The corpus-state snapshot: the bundle observing its own governance.

`docs/specs/observability-corpus-state.md` sets four rules. Three are pinned here — one
derivation, zero cloud, and `--as-of` honoured — and the fourth, boundedness, is pinned by
`test_the_stale_list_is_bounded`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grounded_context import corpus_state
from grounded_context.bundle import Bundle
from grounded_context.service import load_bundle

BUNDLE = Path(__file__).resolve().parents[1] / "knowledge"

BEFORE = date(2026, 9, 9)
AFTER = date(2026, 12, 1)


@pytest.fixture(scope="module")
def bundle() -> Bundle:
    return load_bundle()


def test_the_snapshot_never_disagrees_with_the_lookup_path(bundle: Bundle) -> None:
    """The spec's first non-negotiable, and the only one that could produce a lie.

    Staleness and the trust tier already exist on `Concept`, where the deterministic lookup reads
    them. A snapshot that recomputed either would be a second opinion about the same fact, and
    the two could drift without anything failing — a dashboard reporting a bundle in good health
    while every citation renders `STALE`.
    """
    for as_of in (BEFORE, AFTER):
        document = corpus_state.snapshot(bundle, as_of)
        expected = sorted(c.id for c in bundle if c.is_stale(as_of))
        assert document["stale"]["concept_ids"] == expected
        assert document["stale"]["count"] == len(expected)

    tiers = corpus_state.snapshot(bundle, BEFORE)["trust_tier"]
    for concept in bundle:
        field = corpus_state._TIER_FIELDS[concept.trust_tier]
        assert tiers[field] >= 1, f"{concept.id} is {concept.trust_tier} and the count says 0"
    assert sum(tiers.values()) == len(list(bundle))


def test_as_of_shows_the_cliff_before_it_arrives(bundle: Bundle) -> None:
    """The point of the snapshot: a governance cliff is visible in advance, not in hindsight."""
    assert corpus_state.snapshot(bundle, BEFORE)["stale"]["count"] == 0
    assert corpus_state.snapshot(bundle, AFTER)["stale"]["count"] == len(list(bundle))


def test_the_stale_list_is_bounded(monkeypatch: pytest.MonkeyPatch, bundle: Bundle) -> None:
    """The instrument built to ask whether curation scales must not scale with the corpus."""
    monkeypatch.setattr(corpus_state, "STALE_IDS_CAP", 2)
    document = corpus_state.snapshot(bundle, AFTER)
    assert document["stale"]["count"] == 4, "the count is the whole truth"
    assert len(document["stale"]["concept_ids"]) == 2, "the list is a bounded sample of it"


def test_the_scan_needs_no_cluster(bundle: Bundle, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-cloud, like the deterministic path it measures. No credentials, no network."""
    monkeypatch.delenv("ES_URL", raising=False)
    monkeypatch.delenv("ES_API_KEY", raising=False)
    assert corpus_state.render(corpus_state.snapshot(bundle, BEFORE)).startswith("corpus state")


def test_a_broken_sink_does_not_lose_the_snapshot(
    bundle: Bundle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Best-effort, like the per-query sink: the printed snapshot is the deliverable."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    corpus_state.emit(corpus_state.snapshot(bundle, BEFORE), str(blocked))

    assert "corpus-state sink unavailable" in capsys.readouterr().err


def test_every_field_the_snapshot_emits_is_in_the_mapping(bundle: Bundle) -> None:
    """A field with no mapping is a field Kibana cannot chart, which is the whole deliverable."""
    document = corpus_state.snapshot(bundle, BEFORE)
    mapped = corpus_state.MAPPING["properties"]
    assert set(document) == set(mapped)
    for nested in ("stale", "trust_tier", "status"):
        assert set(document[nested]) == set(mapped[nested]["properties"]), nested


def test_the_log_round_trips(bundle: Bundle, tmp_path: Path) -> None:
    """The log is the durable time series; the index is a view over it, never the reverse."""
    log = tmp_path / "corpus-state.ndjson"
    corpus_state.emit(corpus_state.snapshot(bundle, BEFORE), str(log))
    corpus_state.emit(corpus_state.snapshot(bundle, AFTER), str(log))

    recorded = corpus_state.read_log(log)
    assert [entry["as_of"] for entry in recorded] == [BEFORE.isoformat(), AFTER.isoformat()]
    assert [entry["stale"]["count"] for entry in recorded] == [0, 4]


def test_a_missing_log_is_empty_not_an_error(tmp_path: Path) -> None:
    assert corpus_state.read_log(tmp_path / "nothing.ndjson") == []
