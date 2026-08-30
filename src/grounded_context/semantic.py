"""The semantic path: BM25 and ELSER, fused with reciprocal rank fusion.

Two retrievers run independently over the same text — one lexical, one learned-sparse — and
RRF merges their rankings. Lexical matching is what catches an exact token like a parameter
name that an embedding will happily rank next to its semantic neighbors; the sparse model is
what catches a question phrased nothing like the document. Neither alone is enough, which is
the argument for hybrid.

Results come back as citations in the same shape the deterministic path emits. That sameness
is deliberate: a dual engine should read as one auditable system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .es_client import INDEX, client

if TYPE_CHECKING:
    from elasticsearch import Elasticsearch
from .provenance import SEMANTIC, Citation

# RRF tuning. rank_constant sets how much influence lower-ranked documents keep; a higher
# value flattens the contribution curve. rank_window_size is how deep each retriever is read
# before fusion.
#
# 20 rather than the default: omitting `rank_constant` reproduces `rank_constant=60` to the
# digit against this cluster, and 20 is the value Elastic's own reciprocal-rank-fusion
# reference sets in its worked examples. The choice moves the numbers, not the argument — a
# document ranked first by both arms scores `2/(k+1)`, which is 0.0952 at k=20 and 0.0328 at
# k=60. The ceiling exists at every k; only where it sits changes.
RANK_CONSTANT = 20
RANK_WINDOW_SIZE = 50
DEFAULT_SIZE = 5
SNIPPET_CHARS = 320

# Minimum pre-fusion sparse score for a query to count as answerable at all. Chosen from 16
# probes against this corpus — 9 of 10 off-topic landed at 1.6–6.0 and all 6 genuine ones at
# 14.0–19.3, with one off-topic probe at 16.1 that this floor does not catch (see
# `is_relevant`). Not tuned on a labeled set: a guardrail, not a classifier. The number is a
# property of this index — re-chunk, re-index, or change the inference model and it means
# nothing. What ports is the method (read a pre-fusion score), never the constant.
#
# Re-derived 2026-08-28 against the rebuilt index and left at 8.0. The usable gap moved from
# [5.9, 14.1] to [6.0, 14.0], so 8.0 still sits inside it. Centering it at 10.0 would balance the
# headroom (4.0 either side, against 2.0/6.0 at a floor of 8.0) but classifies all 16 tuning
# probes identically, so the change would be churn on a published constant with no measured
# effect.
RELEVANCE_FLOOR = 8.0

METHOD = "hybrid(bm25+elser,rrf)"
METHOD_LEXICAL = "bm25"
METHOD_SEMANTIC = "elser"


# Weight on the exact-token clause. It is a tie-breaker, not the mechanism: measured across
# the 236 identifiers unique to one chunk of this corpus, dropping it to 1.0 costs 3 of them a
# rank and improves none, and it does not move either phrasing of the eval's Q9. The work of
# separating a definition from a code sample is done by the subfield's analyzer, not by this.
EXACT_TOKEN_BOOST = 3.0


def _lexical(query: str) -> dict[str, Any]:
    """BM25 over the analyzed text, plus the whitespace-tokenized subfield.

    The two clauses fail in opposite directions, which is the reason both are queried.

    The standard analyzer strips punctuation and splits on hyphens (it keeps underscores:
    `rank_constant` survives, `claude-opus-5` becomes `claude`/`opus`/`5`). Stripping
    punctuation collapses a code sample's `"rank_constant":` onto the prose mention, so the
    chunk that *defines* a term competes with every chunk that merely uses it.

    The `exact` subfield lowercases and splits on whitespace only, so hyphens and punctuation
    survive. That makes it strict enough to tell those apart — and strict enough to miss an
    identifier the corpus only ever writes as `"batch_id":`, since the quotes are part of the
    token. Neither field is reliable alone, so the `should` takes whichever one hits.
    """
    return {
        "standard": {
            "query": {
                "bool": {
                    "should": [
                        {"match": {"content": query}},
                        {
                            "match": {
                                "content.exact": {
                                    "query": query,
                                    "boost": EXACT_TOKEN_BOOST,
                                }
                            }
                        },
                    ]
                }
            }
        }
    }


def _sparse(query: str) -> dict[str, Any]:
    return {"standard": {"query": {"semantic": {"field": "semantic", "query": query}}}}


def hybrid_retriever(query: str) -> dict[str, Any]:
    """The RRF retriever body: BM25 and ELSER fused."""
    return {
        "rrf": {
            "retrievers": [_lexical(query), _sparse(query)],
            "rank_window_size": RANK_WINDOW_SIZE,
            "rank_constant": RANK_CONSTANT,
        }
    }


def citation(hit: dict[str, Any], method: str = METHOD) -> Citation:
    """Build a citation from one search hit, key-for-key identical to a deterministic one."""
    source = hit["_source"]
    text = source.get("content", "")
    return {
        "path": SEMANTIC,
        "source_id": source["source_id"],
        "source_url": source.get("url"),
        "locator": f"chunk:{source.get('chunk_index')}",
        "method": method,
        "score": hit.get("_score"),
        # OKF lifecycle belongs to the canonical layer; a fetched page carries only the date
        # it was retrieved, which is what provenance.md means by <index_time>.
        "verified_at": source.get("fetched_at"),
        "trust_tier": None,
        "status": None,
        "stale_after": None,
        "is_stale": False,
        "hops": [],
        "snippet": text[:SNIPPET_CHARS].strip(),
    }


def is_relevant(query: str, es: Elasticsearch | None = None,
                floor: float = RELEVANCE_FLOOR) -> bool:
    """Probe whether anything in the index genuinely matches, before fusing.

    RRF scores cannot answer this. They are computed from rank position — `1/(k+rank)` — so
    the top hit scores about the same whether it is a perfect match or the least bad of
    hundreds of irrelevant chunks. Measured on this index, "how do I bake sourdough bread?"
    fused to 0.0635 against 0.0729 for a real question about streaming. The pre-fusion sparse
    score keeps the magnitude those two share, so that is what the floor reads.

    Three things it does not do, all by construction. It does not catch an in-domain question
    about the wrong entity — "the price of GPT-5" scores 18.8 against real pricing prose, just
    the wrong vendor's — which belongs to the router and the canonical layer. And it measures
    the corpus as *text*, not as subject matter: a question about marathon training clears the
    floor at 16.1, because Elastic's `semantic_text` page teaches the feature with running and
    exercise sample documents. The retrieval is correct; only the topic is a surprise.

    And it gates the *corpus*, not the *document*. Clearing the floor says the index holds text
    the sparse model scored as relevant; it says nothing about whether the chunk RRF then
    returns at rank 1 is the right one. The two come apart — `findings.md` §1's
    `rank_window_size` row is a case where ELSER spreads its attention across a page while BM25
    carries the defining chunk, so the floor passes on a score that belongs to a document the
    ranking does not surface first. Per-document correctness is the ranking's problem, and this
    probe deliberately does not touch it.
    """
    cleared, _ = probe(query, es=es, floor=floor)
    return cleared


def probe(
    query: str, es: Elasticsearch | None = None, floor: float = RELEVANCE_FLOOR
) -> tuple[bool, float | None]:
    """The floor verdict *and* the score behind it. `None` when nothing came back at all.

    The score is worth keeping, not just the comparison. A query at 7.9 against a floor of 8.0
    is a corpus gap — in domain, and not yet answerable — while one at 1.7 is off topic and
    always will be. Both refuse; only one is a curation backlog item, and the boolean alone
    cannot tell them apart after the fact.

    The comparison lives here rather than at the call site so the floor has one implementation.
    """
    top = search_semantic_only(query, size=1, es=es)
    if not top:
        return False, None
    score = top[0]["score"] or 0.0
    return score >= floor, score


def search(
    query: str,
    size: int = DEFAULT_SIZE,
    es: Elasticsearch | None = None,
    retriever: Any = None,
    floor: float | None = RELEVANCE_FLOOR,
) -> list[Citation]:
    """Run the hybrid search and return citations, best first.

    Returns nothing when the floor is not cleared — an empty list becomes the refusal, and
    citing an irrelevant passage would be worse than admitting there is no grounded answer.
    """
    es = es or client()
    if floor is not None and not is_relevant(query, es=es, floor=floor):
        return []
    body = retriever if retriever is not None else hybrid_retriever(query)
    response = es.search(index=INDEX, retriever=body, size=size)
    method = METHOD if retriever is None else "custom"
    return [citation(hit, method) for hit in response["hits"]["hits"]]


def search_lexical_only(
    query: str, size: int = DEFAULT_SIZE, es: Elasticsearch | None = None
) -> list[Citation]:
    """BM25 alone — one comparison arm for the retrieval-arm table in docs/findings.md."""
    es = es or client()
    response = es.search(index=INDEX, retriever=_lexical(query), size=size)
    return [citation(hit, METHOD_LEXICAL) for hit in response["hits"]["hits"]]


def search_semantic_only(
    query: str, size: int = DEFAULT_SIZE, es: Elasticsearch | None = None
) -> list[Citation]:
    """ELSER alone — the arm that plausibly-but-wrongly answers an exact-token question."""
    es = es or client()
    response = es.search(index=INDEX, retriever=_sparse(query), size=size)
    return [citation(hit, METHOD_SEMANTIC) for hit in response["hits"]["hits"]]
