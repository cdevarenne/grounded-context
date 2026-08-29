"""Regenerate finding 4: can one call carry both the ranking and the floor?

Three tables. First, four scoring configurations over the sixteen tuning probes — the one that
shows `minmax` pinning every top document to 1.0 and overlapping worse than the RRF it would
replace. Second, the lexical-arm weight sweep scored against the tuning probes and against the
thirty held-out ones, which is where the tuned weight stops agreeing with itself. Third, the
other single-call shape: `min_score` on the inner ELSER arm, which classifies every probe
correctly and still cannot ship.

Every separability figure is reported twice, as AUC and as a margin, because the two disagree
and the disagreement is the point. `measure_findings.METRIC_LEGEND` defines both.

    uv run --extra es python scripts/single_call_probe.py [--json]
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from typing import Any

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


def top(es: Any, query: str, retriever: dict[str, Any]) -> float:
    hits = es.search(index=INDEX, retriever=retriever, size=1)["hits"]["hits"]
    return hits[0]["_score"] if hits else 0.0


def elser_raw(es: Any, query: str) -> float:
    return search_semantic_only(query, size=1, es=es)[0]["score"]


def scored(
    score: Callable[[str], float], off: Sequence[str], genuine: Sequence[str]
) -> tuple[list[float], list[float]]:
    """Score both classes with one configuration, in the order the tables read them."""
    return [score(q) for q in off], [score(q) for q in genuine]


def normalizer_comparison(es: Any) -> list[dict[str, Any]]:
    """Top score by scoring configuration over the tuning probes — the case against `minmax`."""
    configs: tuple[tuple[str, Callable[[str], dict[str, Any]]], ...] = (
        ("rrf (shipped)", hybrid_retriever),
        ("linear / minmax", lambda q: linear(q, "minmax")),
        ("linear / l2_norm", lambda q: linear(q, "l2_norm")),
        ("linear / none", lambda q: linear(q, "none")),
    )
    rows = []
    for name, build in configs:
        off, gen = scored(lambda q, b=build: top(es, q, b(q)), OFF_TOPIC, IN_DOMAIN)
        rows.append({
            "config": name,
            "off_topic_min": min(off), "off_topic_max": max(off),
            "genuine_min": min(gen), "genuine_max": max(gen),
            "gap": min(gen) - max(off), "auc": round(auc(gen, off), 3),
        })
    return rows


def weight_sweep(es: Any) -> list[dict[str, Any]]:
    """The incumbent and each candidate weight, on both probe sets, with confusion counts.

    A floor is derived per row from that row's own tuning probes and applied unchanged to the
    held-out ones, because every row is a different score scale. The incumbent is the exception
    and says so: 8.0 is the published constant, and it is *not* the midpoint of this set — the
    marathon probe overlaps the genuine band, so no midpoint exists and 8.0 was chosen from the
    other nine. Recording that keeps the incumbent held to the candidate's standard.
    """
    sweep: list[tuple[str, Callable[[str], float]]] = [
        ("ELSER raw (shipped)", lambda q: elser_raw(es, q)),
    ]
    sweep += [
        (f"linear/none w={w}", lambda q, w=w: top(es, q, linear(q, "none", w))) for w in WEIGHTS
    ]

    rows = []
    for name, score in sweep:
        off_t, gen_t = scored(score, OFF_TOPIC, IN_DOMAIN)
        off_h, gen_h = scored(score, OFF_TOPIC_HELDOUT, IN_DOMAIN_HELDOUT)
        incumbent = name.startswith("ELSER")
        floor = RELEVANCE_FLOOR if incumbent else tuning_midpoint(gen_t, off_t)
        row: dict[str, Any] = {
            "config": name,
            "tuning_auc": round(auc(gen_t, off_t), 3),
            "tuning_margin_pct": round(margin(gen_t, off_t), 1),
            "heldout_auc": round(auc(gen_h, off_h), 3),
            "heldout_margin_pct": round(margin(gen_h, off_h), 1),
            "floor": floor,
            "floor_source": "published" if incumbent else ("midpoint" if floor else None),
            "false_accepts": None,
            "false_rejects": None,
        }
        if floor is not None:
            row["false_accepts"], row["false_rejects"] = confusion(gen_h, off_h, floor)
        # The held-out query that comes closest to being answered. It is named in the prose of
        # findings.md and the spec, so it has to be a recorded figure rather than a remembered
        # one — the console table has no column wide enough to carry it.
        score, query = max(zip(off_h, OFF_TOPIC_HELDOUT, strict=True))
        row["worst_heldout_off_topic"] = {"score": score, "query": query}
        rows.append(row)
    return rows


def nested_gate(es: Any) -> list[dict[str, Any]]:
    """`min_score` on the inner ELSER arm: what it classifies, and what it cannot report.

    `distinct_scores` is the finding. Every refusal collapses onto the single-arm ceiling, so a
    query that missed by a hair and one that was never in domain arrive indistinguishable — which
    is exactly the signal `relevance_score` exists to carry.
    """
    rows = []
    for probe_set, off_qs, gen_qs in (("tuning", OFF_TOPIC, IN_DOMAIN),
                                      ("held-out", OFF_TOPIC_HELDOUT, IN_DOMAIN_HELDOUT)):
        for kind, queries in (("off-topic", off_qs), ("in-domain", gen_qs)):
            fused = [top(es, q, gated_hybrid(q)) for q in queries]
            raw = [elser_raw(es, q) for q in queries]
            refused = [s <= SINGLE_ARM_CEILING + AT_CEILING for s in fused]
            off_topic = kind == "off-topic"
            wrong = len(refused) - sum(refused) if off_topic else sum(refused)
            distinct = {round(s, 7) for s, r in zip(fused, refused, strict=True) if r}
            rows.append({
                "set": probe_set, "class": kind, "n": len(queries), "refused": sum(refused),
                "false_accepts": wrong if off_topic else None,
                "false_rejects": None if off_topic else wrong,
                "distinct_scores": len(distinct),
                # The value Elasticsearch actually returns, which is float32 and therefore not
                # bit-identical to `SINGLE_ARM_CEILING` computed in Python. The docs quote the
                # observed number, so the observed number is what gets recorded.
                "refused_score": next(iter(distinct)) if len(distinct) == 1 else None,
                "elser_min": min(raw), "elser_max": max(raw),
            })
    return rows


def build_report(es: Any) -> dict[str, Any]:
    """Finding 4's three tables as data, for `publish_figures.py` and for `--json`."""
    return {
        "single_arm_ceiling": SINGLE_ARM_CEILING,
        "normalizers": normalizer_comparison(es),
        "sweep": weight_sweep(es),
        "nested_gate": nested_gate(es),
    }


def render(report: dict[str, Any]) -> None:
    """The three tables, in the shape docs/eval-output.md captures them."""
    print("=== normalizer comparison, 16 tuning probes ===")
    print(f"{'config':<22}{'off-topic':>20}{'genuine':>20}{'gap':>10}{'AUC':>8}")
    for row in report["normalizers"]:
        print(f"{row['config']:<22}"
              f"{row['off_topic_min']:9.4f} – {row['off_topic_max']:<8.4f}"
              f"{row['genuine_min']:9.4f} – {row['genuine_max']:<8.4f}"
              f"{row['gap']:>+10.4f}{row['auc']:>8.3f}")

    print("\n=== lexical-arm weight sweep, linear / none ===")
    print("A separability sweep, not a fixed-threshold classification: each row is a different")
    print("score scale, so a floor is derived per row from that row's tuning probes and then")
    print("applied unchanged to the held-out ones. Confusion counts are held-out only.")
    print(METRIC_LEGEND)
    print(f"\n{'config':<22}{'tuning':>16}{'held-out':>16}   {'floor':<28}{'held-out errors':>17}")
    print(f"{'':<22}{'AUC':>8}{'margin':>8}{'AUC':>8}{'margin':>8}   {'':<28}"
          f"{'accept':>9}{'reject':>8}")
    for row in report["sweep"]:
        floor = row["floor"]
        label = f"{floor:.2f} {row['floor_source']}" if floor else "none — tuning sets overlap"
        errors = (f"{'n/a':>9}{'n/a':>8}" if floor is None
                  else f"{row['false_accepts']:>9}{row['false_rejects']:>8}")
        print(f"{row['config']:<22}{row['tuning_auc']:>8.3f}{row['tuning_margin_pct']:>7.1f}%"
              f"{row['heldout_auc']:>8.3f}{row['heldout_margin_pct']:>7.1f}%   "
              f"{label:<28}{errors}")

    ceiling = report["single_arm_ceiling"]
    print("\n=== min_score on the inner ELSER arm ===")
    print("One call. The parent still returns the lexical arm's hits, so the refusal rule cannot be")
    print(f"'zero hits' — it is 'top score is at the single-arm ceiling {ceiling:.6f}',")
    print("which is where a document ranked first by BM25 lands when nothing survives the gate.")
    print(f"\n{'set':<11}{'class':<12}{'n':>4}{'refused':>9}{'accept':>8}{'reject':>8}"
          f"{'distinct scores':>17}   true ELSER range")
    for row in report["nested_gate"]:
        off_topic = row["class"] == "off-topic"
        wrong = row["false_accepts"] if off_topic else row["false_rejects"]
        errors = f"{wrong:>8}{'':>8}" if off_topic else f"{'':>8}{wrong:>8}"
        print(f"{row['set']:<11}{row['class']:<12}{row['n']:>4}{row['refused']:>9}{errors}"
              f"{row['distinct_scores'] or '-':>17}   "
              f"{row['elser_min']:.2f} – {row['elser_max']:.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit raw numbers")
    args = parser.parse_args(argv)

    report = build_report(client())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
