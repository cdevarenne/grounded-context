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
from collections.abc import Iterable, Sequence
from typing import Any

from grounded_context.es_client import INDEX, client
from grounded_context.semantic import (
    RELEVANCE_FLOOR,
    hybrid_retriever,
    search_semantic_only,
)

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

# Printed wherever a separability figure is, so no percentage in the docs is unfalsifiable.
# Both scripts import this: one definition, quoted identically wherever the metrics are printed.
METRIC_LEGEND = (
    "  AUC     P(a genuine probe outscores an off-topic one), ties counting half. 1.000 is full\n"
    "          separation, 0.500 no signal. Every pair counts, so one outlier moves it by at\n"
    "          most 1/(genuine x off-topic).\n"
    "  margin  (min(genuine) - max(off-topic)) / min(genuine) * 100 — the headroom a threshold\n"
    "          has. Two order statistics and nothing else, so one outlier can move it freely.\n"
    "          Negative means the two sets overlap and no threshold separates them."
)

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


def auc(genuine: Sequence[float], off_topic: Sequence[float]) -> float:
    """Probability a random genuine probe outscores a random off-topic one; ties count half.

    The Mann-Whitney form of ROC-AUC. It reads every genuine/off-topic pair, so one outlier
    moves it by at most `1 / (len(genuine) * len(off_topic))` — which is the whole reason it is
    reported next to `margin` rather than instead of it.
    """
    wins = sum((g > o) + 0.5 * (g == o) for g in genuine for o in off_topic)
    return wins / (len(genuine) * len(off_topic))


def margin(genuine: Sequence[float], off_topic: Sequence[float]) -> float:
    """Headroom between the two sets as a percentage of the lowest genuine score.

        (min(genuine) - max(off_topic)) / min(genuine) * 100

    Negative when the sets overlap. This is two order statistics and nothing else: it says how
    much room a threshold has, and a single outlier at either extreme moves it a long way.
    `auc` is what says whether the rest of the set agrees.
    """
    lowest = min(genuine)
    return (lowest - max(off_topic)) / lowest * 100 if lowest else 0.0


def tuning_midpoint(genuine: Sequence[float], off_topic: Sequence[float]) -> float | None:
    """A floor halfway between the two sets, or `None` when they overlap so none separates.

    Chosen on the tuning probes and then applied unchanged, which is what makes a held-out
    count evidence rather than a fit.
    """
    lowest, highest = min(genuine), max(off_topic)
    return (lowest + highest) / 2 if lowest > highest else None


def confusion(
    genuine: Sequence[float], off_topic: Sequence[float], floor: float
) -> tuple[int, int]:
    """`(false accepts, false rejects)` for a floor applied to probes already scored."""
    return sum(s >= floor for s in off_topic), sum(s < floor for s in genuine)


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
    off_scores = [s for _, s in off]
    genuine_scores = [s for _, s in genuine]
    return {
        "floor": floor,
        "off_topic_max": round(max(off_scores), 2),
        "in_domain_min": round(min(genuine_scores), 2),
        "false_accepts": [(q, round(s, 2)) for q, s in off if s >= floor],
        "false_rejects": [(q, round(s, 2)) for q, s in genuine if s < floor],
        "counts": {"off_topic": len(off), "in_domain": len(genuine)},
        "auc": round(auc(genuine_scores, off_scores), 3),
        "margin_pct": round(margin(genuine_scores, off_scores), 1),
    }


def probe_separation(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """How well each score column separates the tuning probes, by both measures.

    The wrong-entity probe is excluded: it is neither off-topic nor answerable from the bundle,
    so it belongs to neither class and would only blur the comparison it exists to complicate.
    """
    off = [r for r in rows if r["kind"] == "off-topic"]
    genuine = [r for r in rows if r["kind"] == "in-domain"]
    return {
        column: {
            "auc": round(auc([r[column] for r in genuine], [r[column] for r in off]), 3),
            "margin_pct": round(margin([r[column] for r in genuine], [r[column] for r in off]), 1),
        }
        for column in ("sparse", "fused")
    }


def build_report(es: Any) -> dict[str, Any]:
    """Every corpus-wide figure findings.md quotes, as data.

    Separated from `main` so `publish_figures.py` can write these into
    `docs/data/measurements.json` without re-running the measurement or re-parsing the
    printed table. One computation, two renderings.
    """
    chunks = _all_chunks(es)
    probes = probe_scores(es)
    return {
        "chunks": len(chunks),
        "mechanism": mechanism_counts(es),
        "subfield_effect": sweep_subfield_effect(es, chunks),
        "invisible_to_exact": sweep_invisible_to_exact(es, chunks),
        "floor": RELEVANCE_FLOOR,
        "probes": probes,
        "separation": probe_separation(probes),
        "heldout": heldout_floor_check(es),
    }


def floor_verdict(es: Any, floor: float = RELEVANCE_FLOOR) -> dict[str, Any]:
    """Is `RELEVANCE_FLOOR` still a valid floor for the index that exists right now?

    `semantic.py` states outright that the constant is a property of the index: re-chunk,
    re-index or change the inference model and it means nothing. The 2026-08-28 rebuild moved
    the usable gap from [5.9, 14.1] to [6.0, 14.0] and 8.0 happened to survive, which is luck
    rather than evidence. This is what turns the next rebuild's luck into a verdict.

    Both probe sets are scored. The tuning set says whether the floor still sits inside the gap
    it was derived from; the held-out set says whether it still classifies queries it has never
    seen. The second is the one that matters, so a miss there is what makes the verdict fail.
    """
    tuning = {
        kind: [search_semantic_only(q, size=1, es=es)[0]["score"] for q in queries]
        for kind, queries in (("off-topic", OFF_TOPIC), ("in-domain", IN_DOMAIN))
    }
    # The marathon probe sits inside the genuine band, so the usable gap is derived from the
    # other nine — the same nine 8.0 was originally chosen from.
    off_topic_ceiling = sorted(tuning["off-topic"])[-2]
    genuine_low = min(tuning["in-domain"])
    held = heldout_floor_check(es, floor)
    return {
        "floor": floor,
        "usable_gap": [round(off_topic_ceiling, 2), round(genuine_low, 2)],
        "inside_gap": off_topic_ceiling < floor < genuine_low,
        "false_accepts": held["false_accepts"],
        "false_rejects": held["false_rejects"],
        "ok": not held["false_accepts"] and not held["false_rejects"],
    }


def print_floor_verdict(verdict: dict[str, Any]) -> None:
    """One block a rebuild can print, readable without opening anything else."""
    low, high = verdict["usable_gap"]
    print(f"\nrelevance floor {verdict['floor']} against this index")
    print(f"  usable gap [{low}, {high}] from the 16 tuning probes"
          f"   -> floor {'sits inside it' if verdict['inside_gap'] else 'IS OUTSIDE IT'}")
    for label, misses in (("false accepts", verdict["false_accepts"]),
                          ("false rejects", verdict["false_rejects"])):
        print(f"  {len(misses)} {label} across 30 held-out probes")
        for query, score in misses:
            print(f"      {score:7.2f}  {query}")
    print("  VERDICT: " + ("floor still holds" if verdict["ok"] else
                           "FLOOR NO LONGER HOLDS — re-derive it before publishing anything"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit raw numbers")
    args = parser.parse_args(argv)

    es = client()
    if not es.indices.exists(index=INDEX):
        print(f"error: index {INDEX} is missing — run scripts/index_corpus.py", file=sys.stderr)
        return 1

    report = build_report(es)

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

    print("\nFinding 2 — rank improved by the content.exact subfield")
    print(f"  (tokens unique to one chunk, longer than {MIN_UNIQUE_LEN} characters)")
    for shape, data in report["subfield_effect"].items():
        print(f"    {shape:12} {data['improved']:3} of {data['total']:3} improved,"
              f" {data['regressed']} regressed")
    hidden = report["invisible_to_exact"]
    print("\n  tokens matching `content` but INVISIBLE to `content.exact`")
    print(f"  (hyphenated or underscored, at least {MIN_VISIBLE_LEN} characters):")
    print(f"    {hidden['invisible']} of {hidden['total']}")
    print(f"    first three alphabetically: {', '.join(hidden['examples'][:3])}")
    for token, present in hidden["documented"].items():
        print(f"    cited in findings.md: {token:20} {'in the set' if present else 'ABSENT'}")

    print(f"\nFinding 3 — fused vs pre-fusion score (floor = {report['floor']})")
    print(f"  {'kind':13} {'fused':>7} {'sparse':>7}  query")
    for row in report["probes"]:
        print(f"  {row['kind']:13} {row['fused']:7.4f} {row['sparse']:7.2f}  {row['query']}")

    print("\n  separability of each column, 10 off-topic vs 6 in-domain (wrong-entity excluded)")
    print(METRIC_LEGEND)
    print(f"    {'column':<8}{'AUC':>8}{'margin':>10}")
    for column, scores in report["separation"].items():
        print(f"    {column:<8}{scores['auc']:>8.3f}{scores['margin_pct']:>9.1f}%")

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
    print(f"  separability  AUC {held['auc']:.3f}   margin {held['margin_pct']:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
