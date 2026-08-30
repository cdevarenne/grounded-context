"""The Elasticsearch projection over the telemetry log.

Most of this needs no cluster. The mapping is checked against the event the code actually emits,
which is the drift that would otherwise surface as a silently unqueryable field in Kibana.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from grounded_context import telemetry, telemetry_index
from grounded_context.es_client import is_configured
from grounded_context.provenance import DETERMINISTIC, grounded_answer
from grounded_context.telemetry_index import MAPPING

ROOT = Path(__file__).resolve().parents[1]

requires_elasticsearch = pytest.mark.skipif(
    not is_configured(), reason="no ES_URL / ES_API_KEY — the projection cannot run"
)

SAMPLE = Path(__file__).resolve().parent / "data" / "telemetry-sample.ndjson"

#: Never the real telemetry index: this one is created and deleted by the test.
SCRATCH_INDEX = "grounded-context-telemetry-scratch"

CITE: dict[str, Any] = {"path": DETERMINISTIC, "locator": "canonical.context_window_tokens"}


def test_the_mapping_covers_every_field_an_event_carries() -> None:
    """A field the mapping forgets is not an error — it is a Kibana panel that is quietly empty.

    Built from a real event rather than a literal list, so adding a field to the event without
    mapping it fails here instead of in a demo.
    """
    event = telemetry.event(
        "q",
        grounded_answer("1,000,000", [CITE], DETERMINISTIC, None),
        total_ms=2.1,
        deterministic_ms=1.8,
    )

    assert set(event) == set(telemetry_index.MAPPING["properties"])
    assert set(event["latency_ms"]) == set(
        telemetry_index.MAPPING["properties"]["latency_ms"]["properties"]
    )


def test_the_mapping_is_data_stream_ready() -> None:
    """`@timestamp` as a date and no custom `_id`, so it promotes to a data stream unchanged."""
    assert telemetry_index.MAPPING["properties"]["@timestamp"] == {"type": "date"}
    assert "_id" not in telemetry_index.MAPPING


def test_an_absent_cluster_is_a_message_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The projection is optional by design; the log it reads is not."""
    monkeypatch.setattr(telemetry_index, "is_configured", lambda: False)

    # Never the default index: if the patch above ever stops taking, this must not reach it.
    assert telemetry_index.run(SAMPLE, index=SCRATCH_INDEX) == 0
    assert "unavailable" in capsys.readouterr().out


def test_an_empty_log_projects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(telemetry_index, "is_configured", lambda: True)

    assert telemetry_index.run(tmp_path / "absent.ndjson", index=SCRATCH_INDEX) == 0
    assert "nothing to project" in capsys.readouterr().out


@requires_elasticsearch
def test_the_projection_round_trips() -> None:
    """Every event in the log lands, and comes back with the values it went in with."""
    from grounded_context.es_client import client

    es = client()
    try:
        indexed = telemetry_index.project(SAMPLE, index=SCRATCH_INDEX, recreate=True)
        expected = telemetry.read_log(SAMPLE)
        assert indexed == len(expected)

        response = es.search(index=SCRATCH_INDEX, size=len(expected), sort="@timestamp")
        returned = [hit["_source"] for hit in response["hits"]["hits"]]

        assert returned == expected, "the projection must not reinterpret what the log recorded"
    finally:
        if es.indices.exists(index=SCRATCH_INDEX):
            es.indices.delete(index=SCRATCH_INDEX)


@requires_elasticsearch
def test_the_projection_is_rebuildable_from_the_log() -> None:
    """Replaying is idempotent because the index is a view: --recreate restores it from source."""
    from grounded_context.es_client import client

    es = client()
    try:
        first = telemetry_index.project(SAMPLE, index=SCRATCH_INDEX, recreate=True)
        second = telemetry_index.project(SAMPLE, index=SCRATCH_INDEX, recreate=True)

        assert first == second
        assert es.count(index=SCRATCH_INDEX)["count"] == first, "a rebuild must not double up"
    finally:
        if es.indices.exists(index=SCRATCH_INDEX):
            es.indices.delete(index=SCRATCH_INDEX)


# --- the spec and the mapping (ELX-51) ------------------------------------------------------


def _spec_mapping() -> dict:
    """The mapping `docs/specs/observability.md` publishes, parsed out of its JSON block."""
    spec = (ROOT / "docs" / "specs" / "observability.md").read_text(encoding="utf-8")
    block = re.search(r'```json\n(\{\n  "mappings".*?\n\})\n```', spec, re.DOTALL)
    assert block, "observability.md no longer publishes a mappings block"
    return json.loads(block.group(1))["mappings"]


def test_the_published_mapping_matches_the_one_that_is_written() -> None:
    """The spec is the contract; `MAPPING` is what actually reaches Elasticsearch.

    They had already drifted: `relevance_score` was added to the code and never to the spec, so
    the published mapping was missing a field for three commits and nothing said so. A document
    that describes the index has to be checked against the index it describes, or it is a
    comment.
    """
    assert _spec_mapping() == MAPPING

