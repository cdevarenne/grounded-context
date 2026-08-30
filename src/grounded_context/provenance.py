"""The grounded-answer contract: no answer without a citation.

Both retrieval paths emit the same citation structure. That sameness is the point —
it is what makes a dual engine read as one auditable system.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict

from .lookup import LookupResult

NOT_FOUND = "Not found in the grounded sources."

DETERMINISTIC = "deterministic"
SEMANTIC = "semantic"
MIXED = "mixed"


class Citation(TypedDict):
    """One citation, in the shape `docs/specs/provenance.md` publishes.

    Thirteen keys, produced in two places — `citation()` here for an exact hit, and
    `semantic.citation()` for a retrieved passage — and read in five: `render()`,
    `telemetry.event()`, `service._merge()`, the CLI, and the MCP server. That is a contract with
    two writers and five readers and, until this was declared, nothing but tests stopping a key
    from being misspelled on one side of it.

    Deliberately a `TypedDict` rather than a dataclass. The value stays a plain `dict`, so the
    JSON the MCP server returns, the envelope the CLI prints, and every existing test are
    unchanged — this adds a check at the type level and nothing at runtime.

    Every key is always present. A field that does not apply to a path is `None`, never absent:
    the two paths emit the same shape, and that sameness is what makes a dual engine read as one
    auditable system.
    """

    #: `deterministic` or `semantic` — which engine produced this citation.
    path: str
    source_id: str
    source_url: str | None
    locator: str
    method: str
    #: `None` on the deterministic path: an exact lookup is not ranked, so it has no score.
    score: float | None
    verified_at: str | None
    #: OKF-derived, so `None` on the semantic path — a fetched page has no trust tier.
    trust_tier: str | None
    status: str | None
    stale_after: str | None
    is_stale: bool
    #: The concepts traversed to reach the value. Empty on the semantic path.
    hops: list[str]
    snippet: str


class AnswerEnvelope(TypedDict):
    """What every surface returns: an answer, the path that produced it, and its citations.

    `router` is `None` for a lookup that named its entity and field outright, because no routing
    decision was made — absent, which is not the same as a decision with no rationale.
    """

    answer: str
    retrieval_path: str
    router: dict[str, str] | None
    citations: list[Citation]


def citation(result: LookupResult, as_of: date) -> Citation:
    """Build one citation from a deterministic hit, inheriting its OKF provenance."""
    concept = result.concept
    return {
        "path": DETERMINISTIC,
        "source_id": concept.id,
        "source_url": concept.source_url,
        "locator": result.locator,
        "method": "exact-lookup",
        "score": None,
        "verified_at": concept.verified_at,
        "trust_tier": concept.trust_tier,
        "status": concept.status,
        "stale_after": concept.stale_after.isoformat() if concept.stale_after else None,
        "is_stale": concept.is_stale(as_of),
        "hops": list(result.hops),
        "snippet": f"{result.locator} = {result.value!r}",
    }


def grounded_answer(
    answer: str,
    citations: list[Citation],
    retrieval_path: str,
    router: dict[str, str] | None = None,
) -> AnswerEnvelope:
    """Assemble the answer envelope. An empty citation list forces the refusal."""
    if not citations:
        answer = NOT_FOUND
    return {
        "answer": answer,
        "retrieval_path": retrieval_path,
        "router": router,
        "citations": citations,
    }


def render(envelope: AnswerEnvelope) -> str:
    """Render the envelope for a terminal, per docs/specs/provenance.md."""
    lines = []
    router = envelope.get("router")
    if router:
        lines.append(f"router: {router['route']} — {router['rationale']}")
        lines.append("")

    # A retrieved passage is not a synthesized answer, so only an exact hit may be called
    # one. On a mixed result the exact hit still leads, and still earns the label.
    citations = envelope["citations"]
    exact = not citations or citations[0]["path"] == DETERMINISTIC
    lines.append(f"{'Answer' if exact else 'Top passage'}: {envelope['answer']}")

    for cite in envelope["citations"]:
        lines.append("")
        lines.append(f"  ↳ source: {cite['source_id']} · {cite['locator']}")

        trust = cite.get("trust_tier")
        verified_at = (cite.get("verified_at") or "")[:10]
        detail = f"{cite['path']} ({cite['method']})"
        if trust and verified_at:
            detail += f" · {trust} {verified_at}"
        elif trust:
            detail += f" · {trust}"
        elif verified_at:
            # The semantic path has no OKF trust tier — only the date it was retrieved.
            detail += f" · indexed {verified_at}"
        if cite.get("score") is not None:
            detail += f" · score {cite['score']:.4f}"
        lines.append(f"    path: {detail}")
        if cite.get("stale_after"):
            lines.append(f"    {_freshness(cite)}")

        hops = cite.get("hops") or []
        if len(hops) > 1:
            lines.append(f"    traversed: {' → '.join(hops)}")
        if cite.get("source_url"):
            lines.append(f"    {cite['source_url']}")

    if not envelope["citations"]:
        lines.append("")
        lines.append("  ↳ no grounded source — nothing was returned rather than guessed.")

    return "\n".join(lines)


def _freshness(cite: Citation) -> str:
    stale_after = cite.get("stale_after")
    if not stale_after:
        return "freshness: no stale_after set"
    if cite.get("is_stale"):
        return f"⚠ STALE since {stale_after} — re-verify before relying on this"
    return f"fresh until {stale_after}"
