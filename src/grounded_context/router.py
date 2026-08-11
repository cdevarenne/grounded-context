"""The router: which engine should answer this question?

Rule-based on purpose. The interface is what matters — an LLM classifier can replace
`route()` without any caller changing, and the rationale is part of the audit trail
either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DETERMINISTIC = "DETERMINISTIC"
SEMANTIC = "SEMANTIC"
BOTH = "BOTH"

# Asks for an exact value that must never be ranked.
PRECISION_SIGNALS = (
    "context window",
    "max tokens",
    "max output",
    "maximum output",
    "output limit",
    "endpoint",
    "rate limit",
    "model id",
    "model string",
    "alias",
    "version",
    "exact",
    "how many",
    "how much",
    "what is the value of",
    "price",
    "pricing",
    "cost per",
)

# Open-ended: judgement, explanation, or advice.
EXPLORATORY_SIGNALS = (
    "how do i",
    "how should i",
    "how would i",
    "best way",
    "best practice",
    "explain",
    "why",
    "recommended",
    "should i",
    "trade-off",
    "tradeoff",
    "when should",
    "what's the point",
    # router.md lists "difference between" as a SEMANTIC signal, not a comparison one:
    # explaining how two techniques differ is exposition, not a field lookup.
    "difference between",
)

# Cross-entity: the same exact field asked of two entities. Worth asking both engines.
COMPARISON_SIGNALS = ("compare", " vs ", " versus ")

# A pinned or aliased Claude model id appearing verbatim in the query.
MODEL_ID = re.compile(r"claude-[a-z0-9.-]+", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    route: str
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {"route": self.route, "rationale": self.rationale}


def route(query: str) -> Route:
    """Classify a query. On genuine uncertainty, return BOTH."""
    text = f" {query.lower().strip()} "

    precision = [s for s in PRECISION_SIGNALS if s in text]
    exploratory = [s for s in EXPLORATORY_SIGNALS if s in text]
    comparison = [s for s in COMPARISON_SIGNALS if s in text]
    named_model = MODEL_ID.search(text)

    if comparison:
        return Route(
            BOTH,
            f"cross-entity comparison ({_quote(comparison)}) — query both, "
            "prefer an exact hit where one exists",
        )

    if precision and exploratory:
        return Route(
            BOTH,
            f"mixed signals: precision ({_quote(precision)}) and "
            f"exploratory ({_quote(exploratory)}) — safer to query both",
        )

    if precision:
        why = f"precision phrasing ({_quote(precision)})"
        if named_model:
            why += f" plus a named model ({named_model.group(0)})"
        return Route(DETERMINISTIC, f"{why} — an exact fact must not be ranked")

    if exploratory:
        return Route(
            SEMANTIC,
            f"exploratory phrasing ({_quote(exploratory)}), no exact field requested",
        )

    if named_model:
        # The router is decoupled from the bundle on purpose, so it cannot tell a
        # query that names a canonical field from one that names nothing. Say only
        # what it actually knows: no precision *signal* fired.
        return Route(
            BOTH,
            f"names a model ({named_model.group(0)}) but matched no precision "
            "signal — query both rather than guess the intent",
        )

    return Route(BOTH, "no decisive signal — defaulting to BOTH, which is the safe side")


def _quote(signals: list[str]) -> str:
    return ", ".join(f'"{s.strip()}"' for s in sorted(signals, key=len, reverse=True)[:2])
