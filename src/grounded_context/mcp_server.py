"""MCP server: the deterministic path as one model-agnostic retrieval tool set.

It adds no retrieval logic of its own. Every tool returns the same envelope `gctx` renders,
because the point of reaching this over MCP is that the contract does not change per
consumer — the same stdio command serves Claude, Antigravity, or anything else that speaks
the protocol.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server import MCPServer

from .bundle import Bundle
from .provenance import render
from .service import as_of_date, ask, load_bundle, lookup_field

INSTRUCTIONS = """Grounded context layer over a curated, provenance-carrying knowledge bundle.

Exact facts — model ids, context windows, endpoint paths, API versions, prices — MUST come
from these tools and never from your own memory. That is the entire reason this server exists.

Every result carries a `rendered` citation block: reproduce it alongside the answer. When
`answer` is "Not found in the grounded sources.", say exactly that and stop rather than
filling the gap yourself. When a citation reports staleness, pass that warning on."""

server = MCPServer(name="grounded-context", instructions=INSTRUCTIONS)


@lru_cache(maxsize=1)
def _bundle() -> Bundle:
    """Load the bundle once per process. Markdown on disk stays the source of truth."""
    return load_bundle()


def _with_citation_block(envelope: dict[str, Any]) -> dict[str, Any]:
    return {**envelope, "rendered": render(envelope)}


@server.tool()
def lookup_canonical_fact(
    entity_id: str, field: str, as_of: str | None = None
) -> dict[str, Any]:
    """Exact value of one canonical field. Use this for any fact that must not be guessed.

    `entity_id` is a bundle id such as `anthropic.claude-opus-5`; `field` is a canonical
    field name such as `context_window_tokens`. Call `list_entities` to discover both. One
    Markdown link is traversed, so a model's `method` resolves through its endpoint concept.
    Pass `as_of` (YYYY-MM-DD) to evaluate staleness at that date instead of today. A field
    the bundle does not hold returns the refusal, not a guess.
    """
    return _with_citation_block(
        lookup_field(_bundle(), entity_id, field, as_of_date(as_of))
    )


@server.tool()
def ask_grounded(query: str, as_of: str | None = None) -> dict[str, Any]:
    """Answer a natural-language question, choosing a retrieval path first.

    Precision questions route to exact lookup. Exploratory ones are declined, because the
    semantic path is not wired up in this build — an honest refusal beats a ranked guess.
    The routing decision and its rationale come back in `router` and are part of the audit
    trail. Prefer `lookup_canonical_fact` when you already know the entity id and field.
    """
    return _with_citation_block(ask(_bundle(), query, as_of_date(as_of)))


@server.tool()
def list_entities() -> dict[str, Any]:
    """Inventory of the bundle: entity ids, types, trust tiers, and canonical field names.

    Call this first to discover valid `entity_id` and `field` arguments.
    """
    return {
        "entities": [
            {
                "id": concept.id,
                "type": concept.type,
                "trust_tier": concept.trust_tier,
                "stale_after": (
                    concept.stale_after.isoformat() if concept.stale_after else None
                ),
                "canonical_fields": sorted(concept.canonical),
            }
            for concept in sorted(_bundle(), key=lambda concept: concept.id)
        ]
    }


def main() -> None:
    """Serve over stdio — the transport every MCP client supports."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
