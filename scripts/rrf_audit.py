"""Audit the fusion math: does the score Elasticsearch returns equal the RRF formula?

`findings.md` §3 argues from how RRF is *defined* — a fused score is `Σ 1/(k + rank)` over the
arms, so it carries how much the arms agreed on an ordering and not how good the documents are.
Everywhere else in this repo that claim is read *out of* Elasticsearch. This checks it *against*
the formula: take each returned document's rank in the two arms separately, compute the sum, and
compare it to what came back.

The result was previously only console text, guarded by nothing. It is a record now, so the claim
"the formula reproduces the engine" is a number — `worst_delta` — rather than three columns a
reader has to compare by eye.

    uv run --extra es python scripts/rrf_audit.py [--check]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grounded_context.es_client import INDEX, client
from grounded_context.semantic import (
    RANK_CONSTANT,
    RANK_WINDOW_SIZE,
    _lexical,
    _sparse,
    hybrid_retriever,
)
from reporting import FusedDocument, FusedQuery, RrfAuditReport, Run

#: One query where the arms agree, one bare identifier where they do not, and one off-topic
#: question — the three regimes §3 distinguishes.
QUERIES = (
    "What is reciprocal rank fusion?",
    "rank_constant",
    "How do I bake sourdough bread?",
)

#: How many of the fused results to audit per query. Three is enough to show the arithmetic; the
#: point is exactness, not coverage.
DEPTH = 3


def _ranked(es: Any, retriever: dict[str, Any]) -> list[tuple[str, int]]:
    """Every document one arm returns, best first, as `(source_id, chunk_index)`."""
    response = es.search(index=INDEX, retriever=retriever, size=RANK_WINDOW_SIZE,
                         _source=["source_id", "chunk_index"])
    return [(h["_source"]["source_id"], h["_source"]["chunk_index"])
            for h in response["hits"]["hits"]]


def audit(es: Any, query: str) -> FusedQuery:
    """Predicted against observed for the top fused documents of one query."""
    lexical = _ranked(es, _lexical(query))
    sparse = _ranked(es, _sparse(query))
    response = es.search(index=INDEX, retriever=hybrid_retriever(query), size=DEPTH,
                         _source=["source_id", "chunk_index"])

    documents = []
    for hit in response["hits"]["hits"]:
        doc = (hit["_source"]["source_id"], hit["_source"]["chunk_index"])
        bm25 = lexical.index(doc) + 1 if doc in lexical else None
        elser = sparse.index(doc) + 1 if doc in sparse else None
        predicted = sum(1 / (RANK_CONSTANT + rank) for rank in (bm25, elser) if rank)
        documents.append(FusedDocument(
            doc=f"{doc[0]}:{doc[1]}",
            bm25=bm25,
            elser=elser,
            predicted=predicted,
            observed=hit["_score"],
        ))
    return FusedQuery(query=query, documents=documents)


def build(es: Any) -> RrfAuditReport:
    return RrfAuditReport(run=Run.observed(es), queries=[audit(es, q) for q in QUERIES])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="re-measure and report whether the audit still holds, without writing")
    args = parser.parse_args(argv)

    fresh = build(client())

    if not args.check:
        path = fresh.write()
        print(f"wrote {path.name}: {len(fresh.queries)} queries · "
              f"worst predicted-vs-observed delta {fresh.worst_delta:.2e}")
        return 0

    if not RrfAuditReport.path().exists():
        print(f"error: {RrfAuditReport.filename} does not exist — run without --check first")
        return 1
    committed = RrfAuditReport.read()
    if [q.query for q in committed.queries] != [q.query for q in fresh.queries]:
        print("the audited queries changed since the committed run")
        return 1
    moved = [
        f"{new.query!r} {d.doc}"
        for old, new in zip(committed.queries, fresh.queries, strict=True)
        for d, e in zip(old.documents, new.documents, strict=True)
        if (d.bm25, d.elser) != (e.bm25, e.elser)
    ]
    if moved:
        print("fused ranks moved since the committed run: " + ", ".join(moved))
        return 1
    print(f"the fusion audit reproduces (committed {committed.run.measured_at}, "
          f"worst delta {fresh.worst_delta:.2e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
