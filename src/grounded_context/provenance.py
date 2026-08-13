"""The grounded-answer contract: no answer without a citation.

Both retrieval paths emit the same citation structure. That sameness is the point —
it is what makes a dual engine read as one auditable system.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .lookup import LookupResult

NOT_FOUND = "Not found in the grounded sources."

DETERMINISTIC = "deterministic"
SEMANTIC = "semantic"
MIXED = "mixed"


def citation(result: LookupResult, as_of: date) -> dict[str, Any]:
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
    citations: list[dict[str, Any]],
    retrieval_path: str,
    router: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the answer envelope. An empty citation list forces the refusal."""
    if not citations:
        answer = NOT_FOUND
    return {
        "answer": answer,
        "retrieval_path": retrieval_path,
        "router": router,
        "citations": citations,
    }


def render(envelope: dict[str, Any]) -> str:
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


def _freshness(cite: dict[str, Any]) -> str:
    stale_after = cite.get("stale_after")
    if not stale_after:
        return "freshness: no stale_after set"
    if cite.get("is_stale"):
        return f"⚠ STALE since {stale_after} — re-verify before relying on this"
    return f"fresh until {stale_after}"
