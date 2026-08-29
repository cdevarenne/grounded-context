"""Regenerate finding 4: can one `linear` call carry both the ranking and the floor?

Two tables. First, the four scoring configurations over the sixteen tuning probes — the one that
shows `minmax` pinning every top document to 1.0 and overlapping worse than the RRF it would
replace. Second, the lexical-arm weight sweep scored against the tuning probes and against the
thirty held-out ones, which is where the tuned weight stops agreeing with itself.

    uv run --extra es python scripts/single_call_probe.py
"""
from grounded_context.es_client import INDEX, client
from grounded_context.semantic import _lexical, _sparse, hybrid_retriever
from measure_findings import (
    IN_DOMAIN, IN_DOMAIN_HELDOUT, OFF_TOPIC, OFF_TOPIC_HELDOUT)

es = client()
WEIGHTS = (1.0, 0.5, 0.25, 0.1)


def linear(query, normalizer="none", lexical_weight=1.0):
    return {"linear": {"retrievers": [
        {"retriever": _lexical(query), "weight": lexical_weight, "normalizer": normalizer},
        {"retriever": _sparse(query), "weight": 1.0, "normalizer": normalizer},
    ], "rank_window_size": 50}}


def top(query, retriever):
    hits = es.search(index=INDEX, retriever=retriever, size=1, _source=["source_id"])
    hits = hits["hits"]["hits"]
    return hits[0]["_score"] if hits else 0.0


def spread(off, genuine):
    """Off-topic ceiling, genuine floor, and the gap between them as a share of the floor."""
    hi, lo = max(off), min(genuine)
    return hi, lo, lo - hi, (lo - hi) / lo * 100 if lo else 0.0


print("=== normalizer comparison, 16 tuning probes ===")
print(f"{'config':<22}{'off-topic':>20}{'genuine':>20}{'gap':>10}")
for name, build in (
    ("rrf (shipped)", lambda q: hybrid_retriever(q)),
    ("linear / minmax", lambda q: linear(q, "minmax")),
    ("linear / l2_norm", lambda q: linear(q, "l2_norm")),
    ("linear / none", lambda q: linear(q, "none")),
):
    off = [top(q, build(q)) for q in OFF_TOPIC]
    gen = [top(q, build(q)) for q in IN_DOMAIN]
    hi, lo, gap, _ = spread(off, gen)
    print(f"{name:<22}{min(off):9.4f} – {hi:<8.4f}{lo:9.4f} – {max(gen):<8.4f}{gap:>+10.4f}")

print("\n=== lexical-arm weight sweep, linear / none ===")
print(f"{'weight':<10}{'tuning margin':>16}{'held-out margin':>18}   worst held-out off-topic")
for w in WEIGHTS:
    _, _, _, tuning = spread([top(q, linear(q, "none", w)) for q in OFF_TOPIC],
                             [top(q, linear(q, "none", w)) for q in IN_DOMAIN])
    scored = [(top(q, linear(q, "none", w)), q) for q in OFF_TOPIC_HELDOUT]
    _, _, _, held = spread([s for s, _ in scored],
                           [top(q, linear(q, "none", w)) for q in IN_DOMAIN_HELDOUT])
    worst_score, worst_query = max(scored)
    print(f"{w:<10}{tuning:>15.1f}%{held:>17.1f}%   {worst_score:7.2f}  {worst_query}")
