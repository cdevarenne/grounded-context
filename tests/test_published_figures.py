"""The tables in `findings.md` must match the records the measurement scripts wrote.

`test_figures.py` guards every *marked* figure. Marks cover prose and the §1 arm table, but the
§3 and §4 tables carry ranges — `0.0476 - 0.0952` — that read badly with a marker around each
endpoint, so they are checked here instead, against the same records.

This file used to parse console text out of `eval-output.md`: the §1 ranks came from a line that
read `  elser    rank 5`, and the score ranges from a fixed-width probe table. It worked, and it
tied a published figure to a terminal's column widths. The records replaced it — the arm table
moved to `test_arms.py` and to markers, and what remains reads `measurements.json` directly.

What has not changed is *why* the file exists. Section 1's rate-limit row was once regenerated
from the wrong phrasing of its query — the sentence form asks about a *header*, and a sweep that
asked about a *parameter* produced a different, plausible, incorrect number. Pairing a published
figure with the question that produced it, rather than with its position, is the only check that
catches that; `arms.json` now stores the query beside the rank so the pairing is in the data.

Reads committed files and touches no cluster, so it guards the docs on a bare clone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINDINGS = (ROOT / "docs" / "findings.md").read_text(encoding="utf-8")
MEASUREMENTS: dict[str, Any] = json.loads(
    (ROOT / "docs" / "data" / "measurements.json").read_text(encoding="utf-8")
)

#: findings.md writes ranges with an en dash and negatives with a minus sign; JSON has neither.
DASHES = str.maketrans({"−": "-", "–": "-", "—": "-"})

#: Stripped before comparison, so a range written as `<!--fig:a-->1.56<!--/-->–<!--fig:b-->6.01<!--/-->`
#: still reads as `1.56-6.01`.
FIGURE_MARKUP = re.compile(r"<!--(?:fig:[a-z0-9_.-]+|/|lit|figures:on|figures:off)-->")


def normalize(text: str) -> str:
    return " ".join(FIGURE_MARKUP.sub("", text).translate(DASHES).split())


def published_row(label: str) -> str:
    """The one findings.md table row for `label`, normalized for comparison."""
    rows = [normalize(r) for r in FINDINGS.splitlines() if r.startswith(f"| {label} |")]
    assert len(rows) == 1, f"expected one findings.md row for {label}, got {len(rows)}"
    return rows[0]


# --- section 3: the probe score ranges ------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "label"),
    [("off_topic", "10 off-topic questions"), ("in_domain", "6 genuine questions")],
)
def test_the_score_ranges_in_section_3_match_the_measured_probes(kind: str, label: str) -> None:
    span = MEASUREMENTS["probes"]["tuning"][kind]
    expected = (
        f"| {label} | {span['fused']['min']:.4f} - {span['fused']['max']:.4f}"
        f" | {span['sparse']['min']:.2f} - {span['sparse']['max']:.2f} |"
    )
    assert expected in {normalize(line) for line in FINDINGS.splitlines()}


# --- section 4: the normalizer comparison ---------------------------------------------------


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("rrf", "`rrf` (current)"),
        ("minmax", "`linear` / `minmax`"),
        ("l2_norm", "`linear` / `l2_norm`"),
        ("none", "`linear` / `none`"),
    ],
)
def test_the_normalizer_table_in_section_4_matches_the_measured_run(
    key: str, label: str
) -> None:
    row = MEASUREMENTS["single_call"]["normalizers"][key]
    published = published_row(label)
    for value in (
        f"{row['off_topic_min']:.4f} - {row['off_topic_max']:.4f}",
        f"{row['genuine_min']:.4f} - {row['genuine_max']:.4f}",
        f"{row['auc']:.3f}",
    ):
        assert value in published, f"{value!r} missing from {published!r}"


# --- section 4: the lexical-arm weight sweep ------------------------------------------------


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("elser_raw", "Pre-fusion ELSER (shipped)"),
        ("w1_0", "`linear`/`none` w=1.0"),
        ("w0_5", "`linear`/`none` w=0.5"),
        ("w0_25", "`linear`/`none` w=0.25"),
    ],
)
def test_the_weight_sweep_in_section_4_matches_the_measured_run(key: str, label: str) -> None:
    """Both metric columns, the floor, and the confusion counts — not just the margin.

    The margin alone is what section 4 published before, and it is the column a single outlier
    moves. Pinning the AUC and the held-out error counts beside it means a rerun that changes the
    conclusion cannot leave the conclusion's wording standing.
    """
    row = MEASUREMENTS["single_call"]["sweep"][key]
    published = published_row(label)
    for value in (
        f"{row['tuning_auc']:.3f}",
        normalize(f"{row['tuning_margin_pct']}%"),
        f"{row['heldout_auc']:.3f}",
        normalize(f"{row['heldout_margin_pct']}%"),
        # Confusion counts are held-out only, and `None` where the tuning sets overlap so no
        # floor could be derived at all — which the table prints as `n/a`, not as a zero.
        "n/a" if row["false_accepts"] is None
        else f"{row['false_accepts']} / {row['false_rejects']}",
    ):
        assert value in published, f"{value!r} missing from {published!r}"
