"""Every figure quoted in prose must resolve to `docs/data/measurements.json`.

`test_published_figures.py` compares findings.md's **tables** against the console captures in
eval-output.md. That leaves the prose, which is where every drift defect in this repo has
actually happened: a regen script that asked "parameter" where the doc asked "header", and seven
pre-reindex numbers sitting in paragraphs directly beneath the capture that refuted them.

So prose figures are marked, and the markup is what makes coverage total:

    Off-topic spans <!--fig:probes.tuning.fused.min-->0.0476<!--/--> …

Two rules, and the second is the one that matters. Every marker must match the measurement — and
inside a region opened by `<!--figures:on-->`, **no unmarked decimal is allowed at all**. The
first rule catches a number that went stale. The second stops a new number being added without
being guarded, which is the failure the older tests had: they covered whatever someone
remembered to write an assertion for.

A decimal that is genuinely not a measurement — a version string, an exponent in a formula —
is declared with `<!--lit-->9.6.0<!--/-->` rather than exempted silently.

These tests read committed files and touch no cluster, so they guard the docs on a bare clone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "docs" / "data" / "measurements.json"
GUARDED_DOCS = ("docs/findings.md", "docs/eval-output.md", "docs/specs/single-call-retrieval.md")

MEASUREMENTS: dict[str, Any] = json.loads(DATA_FILE.read_text(encoding="utf-8"))

MARKER = re.compile(r"<!--fig:([a-z0-9_.]+)-->(.*?)<!--/-->", re.DOTALL)
LITERAL = re.compile(r"<!--lit-->.*?<!--/-->", re.DOTALL)
REGION = re.compile(r"<!--figures:on-->(.*?)<!--figures:off-->", re.DOTALL)
#: A decimal, or an integer percentage. Bare integers are not guarded: they are overwhelmingly
#: section numbers and counts in prose, and the probe counts have their own assertions.
FIGURE_SHAPED = re.compile(r"\d+\.\d+%?|\d+%")

#: Docs write ranges with an en dash and negatives with a minus sign; JSON has neither.
DASHES = str.maketrans({"−": "-", "–": "-", "—": "-"})


def resolve(key: str) -> Any:
    """Walk a dotted key into the measurement file, failing with the path that broke."""
    node: Any = MEASUREMENTS
    for step in key.split("."):
        if not isinstance(node, dict) or step not in node:
            pytest.fail(f"no measurement at {key!r} (stopped at {step!r})")
        node = node[step]
    return node


def shown_as_number(text: str) -> tuple[float, int]:
    """The value a reader sees, and the precision they see it at."""
    cleaned = text.translate(DASHES).replace("%", "").replace(",", "").strip()
    places = len(cleaned.partition(".")[2])
    return float(cleaned), places


def markers() -> list[tuple[str, str, str]]:
    """Every `(doc, key, shown)` triple across the guarded documents."""
    return [
        (doc, key, shown)
        for doc in GUARDED_DOCS
        for key, shown in MARKER.findall((ROOT / doc).read_text(encoding="utf-8"))
    ]


def regions() -> list[tuple[str, int, str]]:
    """Every guarded region as `(doc, ordinal, body)`."""
    return [
        (doc, ordinal, body)
        for doc in GUARDED_DOCS
        for ordinal, body in enumerate(
            REGION.findall((ROOT / doc).read_text(encoding="utf-8")), 1
        )
    ]


def test_the_measurement_file_records_the_run_that_produced_it() -> None:
    """A figure without its index and its date is not checkable, which is the whole point."""
    run = MEASUREMENTS["run"]
    for field in ("measured_at", "index", "chunks", "es_version", "inference_id",
                  "relevance_floor", "rank_constant", "git_sha"):
        assert run.get(field) not in (None, ""), f"run block is missing {field}"


def test_the_guarded_documents_actually_contain_markers() -> None:
    """Guards that quietly cover nothing are worse than no guards, so assert they cover something."""
    marked = {doc for doc, _, _ in markers()}
    assert marked == set(GUARDED_DOCS), f"no figure markers found in {set(GUARDED_DOCS) - marked}"


@pytest.mark.parametrize(("doc", "ordinal"), [(d, o) for d, o, _ in regions()])
def test_guarded_regions_are_opened_and_closed_in_pairs(doc: str, ordinal: int) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert text.count("<!--figures:on-->") == text.count("<!--figures:off-->"), (
        f"{doc} has unbalanced figure guards, so part of it is silently unchecked"
    )
    assert ordinal >= 1


@pytest.mark.parametrize(("doc", "key", "shown"), markers())
def test_every_marked_figure_matches_the_measurement(doc: str, key: str, shown: str) -> None:
    measured = resolve(key)
    assert measured is not None, f"{doc} quotes {key}, which the measurement records as null"
    value, places = shown_as_number(shown)
    assert round(float(measured), places) == round(value, places), (
        f"{doc} publishes {shown!r} for {key}, but the measurement says {measured}"
    )


@pytest.mark.parametrize(("doc", "ordinal", "body"), regions())
def test_no_unmarked_figure_inside_a_guarded_region(doc: str, ordinal: int, body: str) -> None:
    """The rule that makes coverage automatic rather than remembered."""
    bare = FIGURE_SHAPED.findall(LITERAL.sub("", MARKER.sub("", body)))
    assert not bare, (
        f"{doc} guarded region {ordinal} quotes {bare} without a marker. Add "
        f"<!--fig:key-->{bare[0]}<!--/--> so it is checked, or <!--lit-->{bare[0]}<!--/--> "
        f"if it is not a measurement."
    )
