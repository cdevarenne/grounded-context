"""The fusion-math audit behind `findings.md` §3, checked against the measured record.

§3's central claim is that a fused score carries *rank agreement* and nothing else. That is an
argument from RRF's definition, so the section checks it against the definition: compute
`Σ 1/(k + rank)` from each arm's ranks and compare to what Elasticsearch returned.

The audit's output used to be console text guarded by nothing at all — the one measurement in
this repo with no test behind it. What matters is not any individual row but the bound: if the
formula and the engine ever disagreed by more than floating-point noise, §3's reasoning would be
about a different scoring function than the one running.

Reads a committed record and touches no cluster.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reporting import RrfAuditReport

FINDINGS = (ROOT / "docs" / "findings.md").read_text(encoding="utf-8")
REPORT = RrfAuditReport.read()

#: Well below the ~1e-9 the audit actually reaches, and well above float32 noise on a score of
#: this magnitude. A disagreement bigger than this is a different scoring function, not rounding.
NOISE = 1e-6


def test_the_formula_reproduces_the_engine() -> None:
    assert REPORT.worst_delta < NOISE, (
        f"predicted and observed fused scores disagree by {REPORT.worst_delta:.2e}, which is "
        f"more than floating-point noise — findings.md §3 argues from the formula, so the formula "
        f"has to be the one Elasticsearch is running"
    )


def test_findings_states_the_agreement_it_measured() -> None:
    """§3 quotes the order of magnitude. It has to be the one the audit reaches."""
    assert "1e-9" in FINDINGS
    assert REPORT.worst_delta < 1e-8, (
        f"findings.md §3 says the formula reproduces the score to about 1e-9; the audit now "
        f"reaches {REPORT.worst_delta:.2e}, so the sentence needs rewriting"
    )


def test_the_audit_covers_the_three_regimes_section_3_distinguishes() -> None:
    """Arms that agree, a bare identifier where they do not, and a question with no answer."""
    assert [q.query for q in REPORT.queries] == [
        "What is reciprocal rank fusion?",
        "rank_constant",
        "How do I bake sourdough bread?",
    ]


@pytest.mark.parametrize(
    "document",
    [d for q in REPORT.queries for d in q.documents],
    ids=lambda d: d.doc,
)
def test_every_audited_document_was_ranked_by_at_least_one_arm(document) -> None:
    """A document the hybrid returned that neither arm ranked would mean the window is too
    small to reconstruct the score, and the predicted value would be silently short a term."""
    assert document.bm25 is not None or document.elser is not None
    assert document.observed > 0
