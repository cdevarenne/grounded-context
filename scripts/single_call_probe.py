"""Regenerate finding 4: can one call carry both the ranking and the floor?

Three tables. First, four scoring configurations over the sixteen tuning probes — the one that
shows `minmax` pinning every top document to 1.0 and overlapping worse than the RRF it would
replace. Second, the lexical-arm weight sweep scored against the tuning probes and against the
thirty held-out ones, which is where the tuned weight stops agreeing with itself. Third, the
other single-call shape: `min_score` on the inner ELSER arm, which classifies every probe
correctly and still cannot ship.

Every separability figure is reported twice, as AUC and as a margin, because the two disagree
and the disagreement is the point. `measure_findings.METRIC_LEGEND` defines both.

    uv run --extra es python scripts/single_call_probe.py
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from measure_findings import (
    IN_DOMAIN,
    IN_DOMAIN_HELDOUT,
    METRIC_LEGEND,
    OFF_TOPIC,
    OFF_TOPIC_HELDOUT,
    auc,
    confusion,
    margin,
    tuning_midpoint,
)

from grounded_context.es_client import INDEX, client
from grounded_context.semantic import (
    RANK_CONSTANT,
    RANK_WINDOW_SIZE,
    RELEVANCE_FLOOR,
    _lexical,
    _sparse,
    hybrid_retriever,
    search_semantic_only,
)

es = client()
WEIGHTS = (1.0, 0.5, 0.25, 0.1)

# With the ELSER arm gated empty, every surviving document is ranked by BM25 alone, so the best
# fused score any of them can reach is a single `1/(k+rank)` term at rank 1.
SINGLE_ARM_CEILING = 1.0 / (RANK_CONSTANT + 1)
AT_CEILING = 1e-6


def linear(query: str, normalizer: str = "none", lexical_weight: float = 1.0) -> dict[str, Any]:
    return {"linear": {"retrievers": [
        {"retriever": _lexical(query), "weight": lexical_weight, "normalizer": normalizer},
        {"retriever": _sparse(query), "weight": 1.0, "normalizer": normalizer},
    ], "rank_window_size": RANK_WINDOW_SIZE}}


def gated_hybrid(query: str, floor: float = RELEVANCE_FLOOR) -> dict[str, Any]:
    """The shipped RRF, with the floor pushed into the sparse arm as a server-side `min_score`."""
    return {"rrf": {"retrievers": [
        _lexical(query),
        {"standard": dict(_sparse(query)["standard"], min_score=floor)},
    ], "rank_window_size": RANK_WINDOW_SIZE, "rank_constant": RANK_CONSTANT}}


def top(query: str, retriever: dict[str, Any]) -> float:
    hits = es.search(index=INDEX, retriever=retriever, size=1)["hits"]["hits"]
    return hits[0]["_score"] if hits else 0.0


def elser_raw(query: str) -> float:
    return search_semantic_only(query, size=1, es=es)[0]["score"]


def scored(
    score: Callable[[str], float], off: Sequence[str], genuine: Sequence[str]
) -> tuple[list[float], list[float]]:
    """Score both classes with one configuration, in the order the tables read them."""
    return [score(q) for q in off], [score(q) for q in genuine]


print("=== normalizer comparison, 16 tuning probes ===")
print(f"{'config':<22}{'off-topic':>20}{'genuine':>20}{'gap':>10}{'AUC':>8}")
for name, build in (
    ("rrf (shipped)", hybrid_retriever),
    ("linear / minmax", lambda q: linear(q, "minmax")),
    ("linear / l2_norm", lambda q: linear(q, "l2_norm")),
    ("linear / none", lambda q: linear(q, "none")),
):
    off, gen = scored(lambda q, b=build: top(q, b(q)), OFF_TOPIC, IN_DOMAIN)
    print(f"{name:<22}{min(off):9.4f} – {max(off):<8.4f}{min(gen):9.4f} – {max(gen):<8.4f}"
          f"{min(gen) - max(off):>+10.4f}{auc(gen, off):>8.3f}")

print("\n=== lexical-arm weight sweep, linear / none ===")
print("A separability sweep, not a fixed-threshold classification: each row is a different")
print("score scale, so a floor is derived per row from that row's tuning probes and then")
print("applied unchanged to the held-out ones. Confusion counts are held-out only.")
print(METRIC_LEGEND)
print(f"\n{'config':<22}{'tuning':>16}{'held-out':>16}   {'floor':<28}{'held-out errors':>17}")
print(f"{'':<22}{'AUC':>8}{'margin':>8}{'AUC':>8}{'margin':>8}   {'':<28}{'accept':>9}{'reject':>8}")

sweep: list[tuple[str, Callable[[str], float]]] = [("ELSER raw (shipped)", elser_raw)]
sweep += [(f"linear/none w={w}", lambda q, w=w: top(q, linear(q, "none", w))) for w in WEIGHTS]

for name, score in sweep:
    off_t, gen_t = scored(score, OFF_TOPIC, IN_DOMAIN)
    off_h, gen_h = scored(score, OFF_TOPIC_HELDOUT, IN_DOMAIN_HELDOUT)
    if name.startswith("ELSER"):
        # 8.0 is the published constant, and it is *not* the midpoint of this set: the marathon
        # probe at 16.11 overlaps the genuine band, so no midpoint exists. It was derived from
        # the other nine. Saying so here keeps the incumbent held to the same standard as the
        # candidate rather than quietly exempted from it.
        floor, label = RELEVANCE_FLOOR, f"{RELEVANCE_FLOOR:.2f} published"
    else:
        floor = tuning_midpoint(gen_t, off_t)
        label = f"{floor:.2f} midpoint" if floor else "none — tuning sets overlap"
    if floor is None:
        errors = f"{'n/a':>9}{'n/a':>8}"
    else:
        accepts, rejects = confusion(gen_h, off_h, floor)
        errors = f"{accepts:>9}{rejects:>8}"
    print(f"{name:<22}{auc(gen_t, off_t):>8.3f}{margin(gen_t, off_t):>7.1f}%"
          f"{auc(gen_h, off_h):>8.3f}{margin(gen_h, off_h):>7.1f}%   {label:<28}{errors}")

print("\n=== min_score on the inner ELSER arm ===")
print("One call. The parent still returns the lexical arm's hits, so the refusal rule cannot be")
print(f"'zero hits' — it is 'top score is at the single-arm ceiling {SINGLE_ARM_CEILING:.6f}',")
print("which is where a document ranked first by BM25 lands when nothing survives the gate.")
print(f"\n{'set':<11}{'class':<12}{'n':>4}{'refused':>9}{'accept':>8}{'reject':>8}"
      f"{'distinct scores':>17}   true ELSER range")
for setname, off_qs, gen_qs in (("tuning", OFF_TOPIC, IN_DOMAIN),
                                ("held-out", OFF_TOPIC_HELDOUT, IN_DOMAIN_HELDOUT)):
    for kind, queries in (("off-topic", off_qs), ("in-domain", gen_qs)):
        fused = [top(q, gated_hybrid(q)) for q in queries]
        raw = [elser_raw(q) for q in queries]
        refused = [s <= SINGLE_ARM_CEILING + AT_CEILING for s in fused]
        seen = {round(s, 7) for s, r in zip(fused, refused) if r}
        wrong = sum(refused) if kind == "in-domain" else len(refused) - sum(refused)
        errors = (f"{wrong:>8}{'':>8}" if kind == "off-topic" else f"{'':>8}{wrong:>8}")
        print(f"{setname:<11}{kind:<12}{len(queries):>4}{sum(refused):>9}{errors}"
              f"{len(seen) or '-':>17}   {min(raw):.2f} – {max(raw):.2f}")
