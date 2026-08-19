"""The telemetry event and its sink.

Two of the three non-negotiables in `docs/specs/observability.md` live at this level: the write is
best-effort, and it needs no network. The third — that telemetry never changes an answer — belongs
to the emit site and is tested there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from grounded_context import telemetry
from grounded_context.provenance import DETERMINISTIC, MIXED, NOT_FOUND, SEMANTIC, grounded_answer

# Only what a citation needs for the event to be derivable; the full shape is pinned elsewhere.
EXACT_CITE: dict[str, Any] = {"path": DETERMINISTIC, "locator": "canonical.context_window_tokens"}
PASSAGE_CITE: dict[str, Any] = {"path": SEMANTIC, "locator": "chunk:1"}

ROUTED_SEMANTIC = {"route": "SEMANTIC", "rationale": "open-ended phrasing"}
ROUTED_BOTH = {"route": "BOTH", "rationale": "ambiguous; run both"}


@pytest.fixture(autouse=True)
def sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file writes to its own log, never the repo's."""
    path = tmp_path / "telemetry.ndjson"
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(path))
    return path


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the event ------------------------------------------------------------------------


def test_the_event_carries_every_field_the_spec_names() -> None:
    envelope = grounded_answer("1,000,000", [EXACT_CITE], DETERMINISTIC, ROUTED_BOTH)
    record = telemetry.event("q", envelope, total_ms=2.06, deterministic_ms=1.84)

    assert set(record) == {
        "@timestamp", "schema_version", "query", "route", "rationale", "retrieval_path",
        "canonical_hit", "relevance_floor_passed", "relevance_score", "refused", "cites",
        "latency_ms",
    }
    assert record["schema_version"] == telemetry.SCHEMA_VERSION
    assert record["@timestamp"].endswith("Z")
    assert set(record["latency_ms"]) == {"deterministic", "semantic", "total"}
    # One decimal, so a summary over these reproduces the committed golden output.
    assert record["latency_ms"] == {"deterministic": 1.8, "semantic": None, "total": 2.1}


def test_a_direct_lookup_reports_itself_as_unrouted() -> None:
    """`gctx lookup` and `lookup_canonical_fact` name the field outright; no router runs."""
    envelope = grounded_answer("POST", [EXACT_CITE], DETERMINISTIC, None)
    record = telemetry.event("anthropic.messages method", envelope, total_ms=1.7)

    assert record["route"] == telemetry.DIRECT
    assert record["rationale"] == telemetry.DIRECT_RATIONALE


@pytest.mark.parametrize(
    ("citations", "path", "router", "expected"),
    [
        ([EXACT_CITE], DETERMINISTIC, ROUTED_BOTH, True),
        ([], DETERMINISTIC, ROUTED_BOTH, False),
        ([EXACT_CITE, PASSAGE_CITE], MIXED, ROUTED_BOTH, True),
        ([PASSAGE_CITE], SEMANTIC, ROUTED_BOTH, False),
        ([PASSAGE_CITE], SEMANTIC, ROUTED_SEMANTIC, None),
        ([EXACT_CITE], DETERMINISTIC, None, True),
        ([], DETERMINISTIC, None, False),
    ],
)
def test_canonical_hit_separates_a_miss_from_a_query_that_never_asked(
    citations: list[dict[str, Any]], path: str, router: dict[str, str] | None, expected: bool | None
) -> None:
    """`None` is not `False`: only one of them is the curation backlog."""
    record = telemetry.event("q", grounded_answer("x", citations, path, router), total_ms=1.0)
    assert record["canonical_hit"] is expected


def test_a_refusal_is_recorded_as_one() -> None:
    record = telemetry.event("q", grounded_answer("", [], DETERMINISTIC, None), total_ms=1.5)
    assert record["refused"] is True
    assert record["cites"] == 0


# --- the sink -------------------------------------------------------------------------


def test_the_sink_appends_one_line_per_event(sink: Path) -> None:
    telemetry.emit({"n": 1})
    telemetry.emit({"n": 2})
    assert [r["n"] for r in read(sink)] == [1, 2]


def test_the_sink_creates_its_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "var" / "telemetry.ndjson"
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(nested))
    telemetry.emit({"n": 1})
    assert nested.is_file()


def test_a_failing_sink_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unavailable sink is a no-op. The answer that was going to return still returns."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()  # a directory where the log should be, so the append cannot succeed
    monkeypatch.setenv("GCTX_TELEMETRY_SINK", str(blocked))

    telemetry.emit({"n": 1})

    stderr = capsys.readouterr().err.strip()
    # Reported, so a broken sink is visible — but reported once, and not raised.
    assert stderr.startswith("telemetry: IsADirectoryError")
    assert stderr.count("\n") == 0, "at most one line on stderr"


def test_turning_telemetry_off_writes_nothing(sink: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCTX_TELEMETRY", "0")
    telemetry.emit({"n": 1})
    assert not sink.exists()


def test_the_sink_resolves_independently_of_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP server is spawned from its client's directory, wherever that is."""
    monkeypatch.delenv("GCTX_TELEMETRY_SINK", raising=False)
    assert telemetry.sink_path().is_absolute()


def test_recording_an_event_never_reaches_the_cloud_half(tmp_path: Path) -> None:
    """The zero-cloud guarantee, measured in a fresh interpreter rather than promised.

    Building and writing an event must not import Elasticsearch — not directly, and not through
    the deterministic modules it reads its constants from.
    """
    code = (
        "import sys, grounded_context.telemetry as t\n"
        "envelope = {'answer': 'x', 'retrieval_path': 'deterministic',"
        " 'router': None, 'citations': []}\n"
        "t.emit(t.event('q', envelope, total_ms=1.0))\n"
        "print('elasticsearch' in sys.modules,"
        " 'grounded_context.es_client' in sys.modules,"
        " 'grounded_context.semantic' in sys.modules)\n"
    )
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "GCTX_TELEMETRY_SINK": str(tmp_path / "telemetry.ndjson"),
        },
    )
    assert result.stdout.split() == ["False", "False", "False"]
    assert (tmp_path / "telemetry.ndjson").is_file(), "the event still has to land"


# --- readback -------------------------------------------------------------------------

SAMPLE = Path(__file__).resolve().parent / "data" / "telemetry-sample.ndjson"
GOLDEN = Path(__file__).resolve().parent / "data" / "telemetry-summary.golden.txt"


def test_the_summary_reproduces_the_committed_golden_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The numbers behind the observability claims, checkable without a cluster.

    Run from a directory where the log really is `var/telemetry.ndjson`, so the header line is
    compared too rather than normalized away.
    """
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "telemetry.ndjson").write_text(
        SAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert telemetry.summary(Path("var/telemetry.ndjson")) == GOLDEN.read_text(encoding="utf-8")


def test_percentages_truncate_rather_than_round() -> None:
    """Pinned by name, because a port that rounds produces a different line and still looks right.

    12 of 26 is 46.15 and agrees either way; 8 of 26 is 30.77 and does not.
    """
    assert telemetry._pct(12, 26) == 46
    assert telemetry._pct(8, 26) == 30
    assert telemetry._pct(2, 26) == 7
    assert telemetry._pct(1, 0) == 0, "an empty population is not a division by zero"


def test_percentiles_use_nearest_rank_without_interpolating() -> None:
    """An interpolating percentile drifts the last digit and breaks the golden compare."""
    values = [1.0, 2.0, 3.0, 4.0]
    assert telemetry._percentile(values, 50) == 2.0  # interpolation would say 2.5
    assert telemetry._percentile(values, 95) == 4.0
    assert telemetry._percentile([7.0], 50) == 7.0


def test_a_missing_log_is_not_an_error(tmp_path: Path) -> None:
    """Before the first answer there is no log, and asking for the summary is still reasonable."""
    report = telemetry.summary(tmp_path / "absent.ndjson")
    assert "no events recorded yet" in report


def test_the_summary_counts_what_the_log_holds(sink: Path) -> None:
    """A round trip: emit through the real sink, then read those events back."""
    envelope = grounded_answer("1,000,000", [EXACT_CITE], DETERMINISTIC, None)
    telemetry.record("a b", envelope, total_ms=1.0, deterministic_ms=1.0)
    telemetry.record("c d", envelope, total_ms=2.0, deterministic_ms=2.0)

    report = telemetry.summary(sink)
    assert "events: 2" in report
    assert "DIRECT 2 (100%)" in report
    assert "hit 2   miss 0" in report


def test_the_score_behind_the_floor_verdict_is_recorded() -> None:
    """A boolean says a query was blocked; the score says whether it was close."""
    envelope = grounded_answer("", [], SEMANTIC, ROUTED_SEMANTIC)
    record = telemetry.event(
        "q", envelope, total_ms=190.0, semantic_ms=189.0,
        relevance_floor_passed=False, relevance_score=7.94,
    )
    assert record["relevance_score"] == 7.9
    assert record["schema_version"] == 2


def test_no_probe_means_no_score() -> None:
    """Absent, not zero — a query the floor never saw did not score badly, it did not score."""
    envelope = grounded_answer("1,000,000", [EXACT_CITE], DETERMINISTIC, None)
    assert telemetry.event("q", envelope, total_ms=1.7)["relevance_score"] is None


def test_the_summary_separates_a_near_miss_from_off_topic(sink: Path) -> None:
    """The reason the field exists, visible in the cloud-free readback."""
    envelope = grounded_answer("", [], SEMANTIC, ROUTED_SEMANTIC)
    for score in (1.7, 7.9):
        telemetry.record("q", envelope, total_ms=1.0, relevance_floor_passed=False,
                         relevance_score=score)
    telemetry.record("q", envelope, total_ms=1.0, relevance_floor_passed=True,
                     relevance_score=18.2)

    assert "floor scores     blocked 1.7 – 7.9   cleared 18.2 – 18.2" in telemetry.summary(sink)


def test_an_older_log_is_named_rather_than_under_reported(sink: Path) -> None:
    """A v1 log has no scores, so a silent summary would look like a corpus with no refusals."""
    sink.write_text(json.dumps({
        "@timestamp": "2026-08-18T15:00:00.000Z", "schema_version": 1, "query": "q",
        "route": "SEMANTIC", "rationale": "r", "retrieval_path": "semantic",
        "canonical_hit": None, "relevance_floor_passed": False, "refused": True, "cites": 0,
        "latency_ms": {"deterministic": None, "semantic": 1.0, "total": 1.0},
    }) + "\n", encoding="utf-8")

    report = telemetry.summary(sink)
    assert "schema_version 1" in report and f"expects {telemetry.SCHEMA_VERSION}" in report


def test_a_current_log_carries_no_warning() -> None:
    """The golden output has no warning line, so this must not fire on the committed fixture."""
    assert telemetry._schema_warning([{"schema_version": telemetry.SCHEMA_VERSION}]) is None
