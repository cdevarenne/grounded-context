"""Every number findings.md publishes must match the run captured in eval-output.md.

findings.md makes the claims; eval-output.md is the evidence for them. Nothing kept the two in
step. They drifted for three commits when a section 1 rank was regenerated from the wrong
phrasing of its query — the sentence form of the rate-limit lookup asks about a *header*, and a
sweep that asked about a *parameter* produced a different, plausible, incorrect number.

That is the failure this file exists to catch, so the section 1 check deliberately pairs a table
row with a capture by the query text that produced it rather than by position. A row measured
from a different question stops matching, which is the only signal that would have caught it.

These tests read two committed documents and touch no cluster, so they guard the docs on a bare
clone with no credentials.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
FINDINGS = (DOCS / "findings.md").read_text(encoding="utf-8")
CAPTURED = (DOCS / "eval-output.md").read_text(encoding="utf-8")

#: findings.md writes ranges with an en dash and negatives with a minus sign; console output uses
#: a hyphen for both. Compare on a single spelling.
DASHES = str.maketrans({"−": "-", "–": "-"})


def normalize(text: str) -> str:
    return " ".join(text.translate(DASHES).split())


def compare_captures() -> dict[str, dict[str, int]]:
    """Every `gctx eval --compare` block in eval-output.md, keyed by the query it ran."""
    blocks = re.findall(
        r"^\$ .*gctx eval --compare \"(?P<query>[^\"]+)\"\n(?P<body>(?:.*\n)*?)(?=^\$ |^```)",
        CAPTURED,
        re.M,
    )
    captures = {
        query: {arm: int(rank) for arm, rank in re.findall(r"(elser|bm25|hybrid)\s+rank (\d+)", body)}
        for query, body in blocks
    }
    assert len(captures) == len(blocks), "two capture blocks ran the same query"
    return captures


def findings_arm_table() -> list[tuple[str, str, dict[str, int]]]:
    """The section 1 table: (identifier, phrasing, ranks per arm)."""
    return [
        (identifier, phrasing, {"elser": int(elser), "bm25": int(bm25), "hybrid": int(hybrid)})
        for identifier, phrasing, elser, bm25, hybrid in re.findall(
            r"^\| `([^`]+)` \| (token|sentence) \| (\d+) \| (\d+) \| \*{0,2}(\d+)\*{0,2} \|$",
            FINDINGS,
            re.M,
        )
    ]


def capture_for(identifier: str, phrasing: str) -> dict[str, int]:
    """The capture whose query is this identifier asked this way.

    A token row is the bare identifier. A sentence row is the one other query mentioning it —
    and the point of resolving it this way is that the document, not the test, decides how that
    sentence is phrased.
    """
    captures = compare_captures()
    if phrasing == "token":
        assert identifier in captures, f"no capture ran the bare query {identifier!r}"
        return captures[identifier]
    matches = [q for q in captures if identifier in q and q != identifier]
    assert len(matches) == 1, f"expected one sentence capture for {identifier}, got {matches}"
    return captures[matches[0]]


ARM_ROWS = findings_arm_table()


def test_the_arm_table_has_a_row_for_every_compare_capture() -> None:
    assert len(ARM_ROWS) == 8
    assert len(compare_captures()) == len(ARM_ROWS)


@pytest.mark.parametrize(("identifier", "phrasing", "published"), ARM_ROWS)
def test_every_published_rank_matches_the_capture(
    identifier: str, phrasing: str, published: dict[str, int]
) -> None:
    assert capture_for(identifier, phrasing) == published


def probe_scores() -> dict[str, list[tuple[float, float]]]:
    """The per-probe fused and pre-fusion scores from the captured sweep."""
    rows = re.findall(r"^\s+(off-topic|in-domain)\s+([\d.]+)\s+([\d.]+)\s+\S", CAPTURED, re.M)
    scores: dict[str, list[tuple[float, float]]] = {"off-topic": [], "in-domain": []}
    for kind, fused, sparse in rows:
        scores[kind].append((float(fused), float(sparse)))
    assert len(scores["off-topic"]) == 10 and len(scores["in-domain"]) == 6
    return scores


@pytest.mark.parametrize(
    ("kind", "label", "places"),
    [("off-topic", "10 off-topic questions", 4), ("in-domain", "6 genuine questions", 4)],
)
def test_the_score_ranges_in_section_3_match_the_captured_probes(
    kind: str, label: str, places: int
) -> None:
    fused = [f for f, _ in probe_scores()[kind]]
    sparse = [s for _, s in probe_scores()[kind]]
    expected = (
        f"| {label} | {min(fused):.{places}f} - {max(fused):.{places}f}"
        f" | {min(sparse):.2f} - {max(sparse):.2f} |"
    )
    assert expected in {normalize(line) for line in FINDINGS.splitlines()}


@pytest.mark.parametrize(
    ("capture_label", "findings_label"),
    [
        ("rrf (shipped)", "`rrf` (current)"),
        ("linear / minmax", "`linear` / `minmax`"),
        ("linear / l2_norm", "`linear` / `l2_norm`"),
        ("linear / none", "`linear` / `none`"),
    ],
)
def test_the_normalizer_table_in_section_4_matches_the_captured_run(
    capture_label: str, findings_label: str
) -> None:
    line = re.search(
        rf"^{re.escape(capture_label)}\s+(\S+ – \S+)\s+(\S+ – \S+)\s+(\S+)$", CAPTURED, re.M
    )
    assert line, f"no captured row for {capture_label}"
    off, genuine, gap = (normalize(g) for g in line.groups())
    published = [
        normalize(row)
        for row in FINDINGS.splitlines()
        if row.startswith(f"| {findings_label} |")
    ]
    assert len(published) == 1, f"expected one section 4 row for {findings_label}"
    for value in (off, genuine, gap.lstrip("+")):
        assert value in published[0], f"{value!r} missing from {published[0]!r}"
