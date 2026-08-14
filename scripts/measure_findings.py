"""Recompute every corpus-wide number quoted in docs/findings.md.

The per-query claims are reproducible with `gctx eval --compare`, but the aggregate ones —
how many identifiers the exact subfield helps, how many it hides, how the probe scores
separate — were the one place the findings asserted rather than captured. This regenerates
them from the live index so a reader can check the figures instead of trusting them.

    uv run --extra es python scripts/measure_findings.py [--json]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from typing import Any, Iterable

from grounded_context.es_client import INDEX, client
from grounded_context.semantic import RELEVANCE_FLOOR, hybrid_retriever, search_semantic_only

# Identifier shapes. Underscore and hyphen are separated because the whole point is that the
# standard analyzer treats them differently.
HYPHENATED = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+){1,}\b")
UNDERSCORED = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+){1,}\b")
ANY_IDENTIFIER = re.compile(r"\b[a-z0-9]+(?:[_-][a-z0-9]+){1,}\b")

MIN_UNIQUE_LEN = 10  # for the per-shape sweep
MIN_VISIBLE_LEN = 8  # for the invisible-to-exact count
TOP_N = 20

OFF_TOPIC = (
    "How do I bake sourdough bread?",
    "What is the capital of France?",
    "What is the best way to train for a marathon?",
    "Who won the 1998 World Cup?",
    "What is a good recipe for beef bourguignon?",
    "How do I change a flat tire on a bicycle?",
    "What are the symptoms of vitamin D deficiency?",
    "When did the Berlin Wall fall?",
    "How tall is Mount Kilimanjaro?",
    "What is the plot of Hamlet?",
)
IN_DOMAIN = (
    "How do I stream responses from the API?",
    "How should I chunk documents for retrieval?",
    "What is reciprocal rank fusion?",
    "How does prompt caching work?",
    "What are the rate limit headers?",
    "How do I use semantic_text?",
)
WRONG_ENTITY = ("What is the price per million tokens of GPT-5?",)


def _all_chunks(es: Any) -> list[dict[str, Any]]:
    """Every indexed chunk. The corpus is small enough to read in one page."""
    response = es.search(
        index=INDEX, query={"match_all": {}}, size=1000,
        _source=["content", "source_id", "chunk_index"],
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def _content_only(query: str) -> dict[str, Any]:
    """The lexical arm as it would be without the exact-token subfield."""
    return {"standard": {"query": {"match": {"content": query}}}}


def _rank_of(es: Any, retriever: dict[str, Any], target: tuple[str, int]) -> int | None:
    response = es.search(
        index=INDEX, retriever=retriever, size=TOP_N, _source=["source_id", "chunk_index"]
    )
    for position, hit in enumerate(response["hits"]["hits"], 1):
        source = hit["_source"]
        if (source["source_id"], source["chunk_index"]) == target:
            return position
    return None


def _unique_to_one_chunk(
    chunks: Iterable[dict[str, Any]], pattern: re.Pattern[str], min_len: int
) -> dict[str, tuple[str, int]]:
    """Identifiers appearing in exactly one chunk, mapped to that chunk.

    Uniqueness is what makes the rank meaningful: there is one right answer to find.
    """
    seen: collections.Counter[str] = collections.Counter()
    owner: dict[str, tuple[str, int]] = {}
    for chunk in chunks:
        for token in set(pattern.findall(chunk["content"].lower())):
            seen[token] += 1
            owner[token] = (chunk["source_id"], chunk["chunk_index"])
    return {t: owner[t] for t, count in seen.items() if count == 1 and len(t) > min_len}


def sweep_subfield_effect(es: Any, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """How many identifiers the `content.exact` subfield actually promotes, by shape."""
    from grounded_context.semantic import _lexical

    results: dict[str, Any] = {}
    for shape, pattern in (("hyphenated", HYPHENATED), ("underscored", UNDERSCORED)):
        unique = _unique_to_one_chunk(chunks, pattern, MIN_UNIQUE_LEN)
        improved = [
            term
            for term, target in unique.items()
            if _rank_of(es, _content_only(term), target)
            != _rank_of(es, _lexical(term), target)
        ]
        results[shape] = {"total": len(unique), "improved": len(improved),
                          "examples": sorted(improved)[:5]}
    return results


def sweep_invisible_to_exact(es: Any, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Identifiers the strict subfield cannot see, because they only appear in punctuation."""
    tokens = {
        token
        for chunk in chunks
        for token in ANY_IDENTIFIER.findall(chunk["content"].lower())
        if len(token) >= MIN_VISIBLE_LEN
    }
    invisible = [
        token
        for token in tokens
        if es.count(index=INDEX, query={"match": {"content": token}})["count"] > 0
        and es.count(index=INDEX, query={"match": {"content.exact": token}})["count"] == 0
    ]
    return {"total": len(tokens), "invisible": len(invisible),
            "examples": sorted(invisible)[:5]}


def probe_scores(es: Any) -> list[dict[str, Any]]:
    """Fused RRF score against pre-fusion sparse score, for every probe."""
    rows: list[dict[str, Any]] = []
    for kind, queries in (
        ("off-topic", OFF_TOPIC), ("in-domain", IN_DOMAIN), ("wrong-entity", WRONG_ENTITY)
    ):
        for query in queries:
            fused = es.search(index=INDEX, retriever=hybrid_retriever(query), size=1)
            rows.append({
                "kind": kind,
                "query": query,
                "fused": round(fused["hits"]["hits"][0]["_score"], 4),
                "sparse": round(search_semantic_only(query, size=1, es=es)[0]["score"], 2),
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit raw numbers")
    args = parser.parse_args(argv)

    es = client()
    if not es.indices.exists(index=INDEX):
        print(f"error: index {INDEX} is missing — run scripts/index_corpus.py", file=sys.stderr)
        return 1

    chunks = _all_chunks(es)
    report = {
        "chunks": len(chunks),
        "subfield_effect": sweep_subfield_effect(es, chunks),
        "invisible_to_exact": sweep_invisible_to_exact(es, chunks),
        "floor": RELEVANCE_FLOOR,
        "probes": probe_scores(es),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"index {INDEX}: {report['chunks']} chunks\n")
    print("Finding 2 — rank improved by the content.exact subfield")
    print("  (identifiers appearing in exactly one chunk)")
    for shape, data in report["subfield_effect"].items():
        print(f"    {shape:12} {data['improved']:3} of {data['total']:3} improved")
    hidden = report["invisible_to_exact"]
    print(f"\n  identifiers matching `content` but INVISIBLE to `content.exact`:")
    print(f"    {hidden['invisible']} of {hidden['total']}  e.g. {', '.join(hidden['examples'][:3])}")

    print(f"\nFinding 3 — fused vs pre-fusion score (floor = {report['floor']})")
    print(f"  {'kind':13} {'fused':>7} {'sparse':>7}  query")
    for row in report["probes"]:
        print(f"  {row['kind']:13} {row['fused']:7.4f} {row['sparse']:7.2f}  {row['query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
