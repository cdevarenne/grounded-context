"""The retrieval-arm claims in `findings.md` §1, checked against the measured record.

The table's twenty-four ranks are marked figures, so `test_figures.py` already holds each cell to
`docs/data/arms.json`. What it cannot hold is the two *sentences* the section argues from:

    the hybrid is **never worse than the weaker arm**, and in six of eight it
    matches or beats the stronger one

Those were counted by hand off the table and guarded by nothing. They are the load-bearing claims
— the table is evidence for them — and they are the ones that would survive a reindex unchanged
while quietly becoming false.

Reads committed files and touches no cluster, so it guards the document on a bare clone. The
cluster-gated half is elsewhere and both are needed: `test_evaluation.py` re-derives the property
live against the index, and `scripts/publish_arms.py --check` re-measures every rank and reports
whether the record still reproduces. This file catches a document that drifted from the record;
those catch a record that stopped being true.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reporting import ArmReport

FINDINGS = (ROOT / "docs" / "findings.md").read_text(encoding="utf-8")
REPORT = ArmReport.read()

#: The section writes counts as words, so the assertion has to compare what a reader sees.
WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four",
    5: "five", 6: "six", 7: "seven", 8: "eight",
}


def test_every_lookup_was_measured_both_ways() -> None:
    assert len(REPORT.lookups) == 8
    assert {row.phrasing for row in REPORT.lookups} == {"token", "sentence"}


def test_the_never_worse_claim_is_measured_rather_than_counted() -> None:
    """If fusion ever lands below the arm that lost, the sentence has to go — not the table."""
    assert REPORT.never_worse_than_the_weaker_arm
    assert "never worse than the weaker arm" in FINDINGS


def test_the_six_of_eight_claim_matches_the_record() -> None:
    matched = REPORT.matches_or_beats_the_stronger_arm
    published = f"in {WORDS[matched]} of {WORDS[len(REPORT.lookups)]} it"
    assert published in FINDINGS, (
        f"the record says {matched} of {len(REPORT.lookups)} lookups match or beat the stronger "
        f"arm, so findings.md §1 should read {published!r}"
    )


def test_the_honest_rows_are_still_the_honest_ones() -> None:
    """§1 singles out `rank_window_size` as the case fusion loses. That has to stay true.

    The paragraph is titled "the honest part" and the whole argument of the section rests on it
    being a real exception rather than a rhetorical one. If a reindex made fusion win there, the
    section would be understating its own result and still reading as candid.
    """
    losers = {row.identifier for row in REPORT.lookups
              if not row.matches_or_beats_the_stronger_arm}
    assert losers == {"rank_window_size"}


@pytest.mark.parametrize("row", REPORT.lookups, ids=lambda r: f"{r.identifier}-{r.phrasing}")
def test_each_row_was_measured_against_the_defining_chunk(row) -> None:
    """The rank is only meaningful against the chunk that defines the term.

    Pairing a row with its query in the record — rather than with its position in a table — is
    what makes the rate-limit defect impossible to repeat: that row was regenerated from a query
    about a *parameter* where the document asked about a *header*, and produced a different,
    plausible, incorrect number that position could not detect.
    """
    assert row.identifier in row.query
    assert row.target_source and row.target_chunk >= 0
    if row.phrasing == "token":
        assert row.query == row.identifier
