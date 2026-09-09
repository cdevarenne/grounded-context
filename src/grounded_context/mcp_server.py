"""MCP server: the deterministic path as one model-agnostic retrieval tool set.

It adds no retrieval logic of its own. Every tool returns the same envelope `gctx` renders,
because the point of reaching this over MCP is that the contract does not change per
consumer — the same stdio command serves Claude, Antigravity, or anything else that speaks
the protocol.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .provenance import AnswerEnvelope, render
from .service import as_of_date, ask, bundle_root, load_bundle, lookup_field
from .store import KnowledgeStore

INSTRUCTIONS = """Grounded context layer over a curated, provenance-carrying knowledge bundle.

Exact facts — model ids, context windows, endpoint paths, API versions, prices — MUST come
from these tools and never from your own memory. That is the entire reason this server exists.

Every result carries a `rendered` citation block: reproduce it alongside the answer. When
`answer` is "Not found in the grounded sources.", say exactly that and stop rather than
filling the gap yourself. When a citation reports staleness, pass that warning on."""

server = MCPServer(name="grounded-context", instructions=INSTRUCTIONS)


def _fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    """What the bundle looks like on disk right now: every file, its size and its mtime."""
    return tuple(sorted(
        (str(path.relative_to(root)), info.st_size, info.st_mtime_ns)
        for path in root.rglob("*.md")
        for info in (path.stat(),)
    ))


@lru_cache(maxsize=2)
def _load(fingerprint: tuple[tuple[str, int, int], ...]) -> KnowledgeStore:
    """Parse the bundle. The argument is never read — it is what makes the cache expire."""
    return load_bundle()


def _bundle() -> KnowledgeStore:
    """The bundle as it is on disk, parsed once per version of it.

    The CLI exits between questions, so it never had this problem. A long-running MCP server
    does: cached unconditionally, it answers from the bundle as it was when the process started,
    for as long as the process lives.

    That is not a slow refresh, it is a wrong answer with provenance attached. Re-verification —
    the procedure in docs/maintenance.md — is precisely when the file changes, so the moment the
    layer is corrected is the moment a running server begins serving the value that was just
    found to be wrong, still carrying the old `verified` date and trust tier.

    Stat is cheap and parsing four Markdown files is not expensive either; the cache is kept
    because it is free, not because it is needed.
    """
    return _load(_fingerprint(bundle_root()))


def _with_citation_block(envelope: AnswerEnvelope) -> dict[str, Any]:
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

    Precision questions route to exact lookup. Exploratory ones route to hybrid semantic
    search and come back as passage citations; with no cluster configured that path returns
    nothing and the answer is the refusal, never a guess. A cross-entity comparison queries
    both, and a deterministic miss there refuses rather than falling back to passages. The
    routing decision and its rationale come back in `router` and are part of the audit
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
