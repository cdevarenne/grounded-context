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

# The identifier findings.md walks through, and its defining chunk.
MECHANISM_TERM = "rank_constant"
MECHANISM_TARGET = ("elastic-rrf", 1)

# Tokens findings.md names as examples of the invisible-to-exact set. Printed with their
# membership so the prose and this output cannot cite different things.
DOCUMENTED_EXAMPLES = ("batch_id", "claude-sonnet-4-6")

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

# A second, larger probe set, written without looking at how it scores and never used to choose
# the floor. The sixteen probes above are what RELEVANCE_FLOOR was derived from, so they cannot
# also be evidence that it generalizes; these can. The off-topic half deliberately includes the
# shapes a score-magnitude floor is weakest against — long queries full of common words, and
# health/exercise topics, which collide with the sample documents on Elastic's semantic_text page.
OFF_TOPIC_HELDOUT = (
    "What is the boiling point of water at high altitude?",
    "How do I replace the brake pads on a car?",
    "Who painted the ceiling of the Sistine Chapel?",
    "What are the rules of cricket?",
    "How long should I marinate chicken before grilling?",
    "What causes the northern lights?",
    "When is the best time to plant tomatoes?",
    "How do I get a passport renewed?",
    "What is the population of Brazil?",
    "How do I teach a dog to sit?",
    "What are the health benefits of swimming regularly?",
    "How many calories are in a banana?",
    "What is the tallest building in the world?",
    "How do I fix a leaking kitchen faucet?",
    "What year did the Titanic sink?",
    "How do I knit a scarf for beginners?",
    "What is the best way to remove a coffee stain from carpet?",
    "How should I prepare for a job interview at a large company?",
    "What is the difference between a violin and a viola?",
    "How do I set up a tent in the rain?",
)
IN_DOMAIN_HELDOUT = (
    "How do I use extended thinking?",
    "What is ELSER and how does it work?",
    "How do I handle errors from the API?",
    "What is the batch processing API for?",
    "How does tool use work?",
    "What is a dense vector mapping?",
    "How do I run a kNN search?",
    "What does the inference API do?",
    "How do I send images to the model?",
    "What is a sparse vector field?",
)


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


def _rank_value(rank: int | None) -> float:
    """Absent from the top N sorts as worse than any rank, so comparisons stay total."""
    return float("inf") if rank is None else float(rank)


def sweep_subfield_effect(es: Any, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """How many identifiers the `content.exact` subfield actually promotes, by shape.

    "Improved" is a strict rank comparison rather than "the rank changed". For terms unique
    to one chunk the two are equivalent — the exact clause can only match that chunk, so its
    rank cannot fall — but asserting the comparison beats relying on that argument, and it
    lets `regressed` stay in the report as a check rather than an assumption.
    """
    from grounded_context.semantic import _lexical

    results: dict[str, Any] = {}
    for shape, pattern in (("hyphenated", HYPHENATED), ("underscored", UNDERSCORED)):
        unique = _unique_to_one_chunk(chunks, pattern, MIN_UNIQUE_LEN)
        improved, regressed = [], []
        for term, target in unique.items():
            before = _rank_value(_rank_of(es, _content_only(term), target))
            after = _rank_value(_rank_of(es, _lexical(term), target))
            if after < before:
                improved.append(term)
            elif after > before:
                regressed.append(term)
        results[shape] = {
            "total": len(unique), "improved": len(improved), "regressed": len(regressed),
            "examples": sorted(improved)[:5],
        }
    return results


def mechanism_counts(es: Any) -> dict[str, Any]:
    """The four numbers findings.md quotes when explaining *why* the subfield helps."""
    from grounded_context.semantic import _lexical

    return {
        "term": MECHANISM_TERM,
        "target": f"{MECHANISM_TARGET[0]}:chunk:{MECHANISM_TARGET[1]}",
        "content_matches": es.count(
            index=INDEX, query={"match": {"content": MECHANISM_TERM}}
        )["count"],
        "exact_matches": es.count(
            index=INDEX, query={"match": {"content.exact": MECHANISM_TERM}}
        )["count"],
        "rank_content_only": _rank_of(es, _content_only(MECHANISM_TERM), MECHANISM_TARGET),
        "rank_with_exact": _rank_of(es, _lexical(MECHANISM_TERM), MECHANISM_TARGET),
    }


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
    return {
        "total": len(tokens),
        "invisible": len(invisible),
        "examples": sorted(invisible)[:5],
        # Membership of the tokens the prose names, so the doc cannot drift from the data.
        "documented": {token: token in invisible for token in DOCUMENTED_EXAMPLES},
    }


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


def heldout_floor_check(es: Any, floor: float = RELEVANCE_FLOOR) -> dict[str, Any]:
    """Score the held-out probes and count what the floor gets wrong on them.

    The floor is applied, not fitted. A false accept is an off-topic question scoring at or above
    it; a false reject is a genuine one scoring below. Both are reported with their queries,
    because the count alone does not say whether a miss is marginal or nowhere near.
    """
    scored = {
        kind: [(q, search_semantic_only(q, size=1, es=es)[0]["score"]) for q in queries]
        for kind, queries in (("off-topic", OFF_TOPIC_HELDOUT), ("in-domain", IN_DOMAIN_HELDOUT))
    }
    off, genuine = scored["off-topic"], scored["in-domain"]
    return {
        "floor": floor,
        "off_topic_max": round(max(s for _, s in off), 2),
        "in_domain_min": round(min(s for _, s in genuine), 2),
        "false_accepts": [(q, round(s, 2)) for q, s in off if s >= floor],
        "false_rejects": [(q, round(s, 2)) for q, s in genuine if s < floor],
        "counts": {"off_topic": len(off), "in_domain": len(genuine)},
    }


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
        "mechanism": mechanism_counts(es),
        "subfield_effect": sweep_subfield_effect(es, chunks),
        "invisible_to_exact": sweep_invisible_to_exact(es, chunks),
        "floor": RELEVANCE_FLOOR,
        "probes": probe_scores(es),
        "heldout": heldout_floor_check(es),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"index {INDEX}: {report['chunks']} chunks\n")

    mech = report["mechanism"]
    print(f"Finding 2 — why the subfield helps, on {mech['term']} ({mech['target']})")
    print(f"    matches on content        {mech['content_matches']} chunks"
          "   (punctuation stripped, so code samples collapse onto the prose mention)")
    print(f"    matches on content.exact  {mech['exact_matches']} chunk"
          "    (punctuation kept, so only the bare prose mention matches)")
    print(f"    rank of the defining chunk: content-only {mech['rank_content_only']}"
          f" -> with exact {mech['rank_with_exact']}")

    print(f"\nFinding 2 — rank improved by the content.exact subfield")
    print(f"  (tokens unique to one chunk, longer than {MIN_UNIQUE_LEN} characters)")
    for shape, data in report["subfield_effect"].items():
        print(f"    {shape:12} {data['improved']:3} of {data['total']:3} improved,"
              f" {data['regressed']} regressed")
    hidden = report["invisible_to_exact"]
    print(f"\n  tokens matching `content` but INVISIBLE to `content.exact`")
    print(f"  (hyphenated or underscored, at least {MIN_VISIBLE_LEN} characters):")
    print(f"    {hidden['invisible']} of {hidden['total']}")
    print(f"    first three alphabetically: {', '.join(hidden['examples'][:3])}")
    for token, present in hidden["documented"].items():
        print(f"    cited in findings.md: {token:20} {'in the set' if present else 'ABSENT'}")

    print(f"\nFinding 3 — fused vs pre-fusion score (floor = {report['floor']})")
    print(f"  {'kind':13} {'fused':>7} {'sparse':>7}  query")
    for row in report["probes"]:
        print(f"  {row['kind']:13} {row['fused']:7.4f} {row['sparse']:7.2f}  {row['query']}")

    held = report["heldout"]
    print(f"\nFinding 3 — the floor on held-out probes (floor = {held['floor']}, applied not fitted)")
    print(f"  {held['counts']['off_topic']} off-topic  top score {held['off_topic_max']:.2f}"
          f"   -> {len(held['false_accepts'])} false accepts")
    for query, score in held["false_accepts"]:
        print(f"      {score:7.2f}  {query}")
    print(f"  {held['counts']['in_domain']} in-domain  low score {held['in_domain_min']:.2f}"
          f"   -> {len(held['false_rejects'])} false rejects")
    for query, score in held["false_rejects"]:
        print(f"      {score:7.2f}  {query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
