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

from typing import Any

from .es_client import INDEX, client
from .provenance import SEMANTIC

# RRF tuning. rank_constant sets how much influence lower-ranked documents keep; a higher
# value flattens the contribution curve. rank_window_size is how deep each retriever is read
# before fusion.
RANK_CONSTANT = 20
RANK_WINDOW_SIZE = 50
DEFAULT_SIZE = 5
SNIPPET_CHARS = 320

# Minimum pre-fusion sparse score for a query to count as answerable at all. Chosen from 16
# probes against this corpus — 9 of 10 off-topic landed at 1.7–5.9 and all 6 genuine ones at
# 14.1–19.5, with one off-topic probe at 16.1 that this floor does not catch (see
# `is_relevant`). Not tuned on a labeled set: a guardrail, not a classifier. The number is a
# property of this index — re-chunk, re-index, or change the inference model and it means
# nothing. What ports is the method (read a pre-fusion score), never the constant.
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


def citation(hit: dict[str, Any], method: str = METHOD) -> dict[str, Any]:
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


def is_relevant(query: str, es: Any = None, floor: float = RELEVANCE_FLOOR) -> bool:
    """Probe whether anything in the index genuinely matches, before fusing.

    RRF scores cannot answer this. They are computed from rank position — `1/(k+rank)` — so
    the top hit scores about the same whether it is a perfect match or the least bad of
    hundreds of irrelevant chunks. Measured on this index, "how do I bake sourdough bread?"
    fused to 0.068 against 0.073 for a real question about streaming. The pre-fusion sparse
    score keeps the magnitude those two share, so that is what the floor reads.

    Two things it does not do, both by construction. It does not catch an in-domain question
    about the wrong entity — "the price of GPT-5" scores 19.4 against real pricing prose, just
    the wrong vendor's — which belongs to the router and the canonical layer. And it measures
    the corpus as *text*, not as subject matter: a question about marathon training clears the
    floor at 16.1, because Elastic's `semantic_text` page teaches the feature with running and
    exercise sample documents. The retrieval is correct; only the topic is a surprise.
    """
    top = search_semantic_only(query, size=1, es=es)
    return bool(top) and (top[0]["score"] or 0.0) >= floor


def search(
    query: str,
    size: int = DEFAULT_SIZE,
    es: Any = None,
    retriever: Any = None,
    floor: float | None = RELEVANCE_FLOOR,
) -> list[dict[str, Any]]:
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


def search_lexical_only(query: str, size: int = DEFAULT_SIZE, es: Any = None) -> list[dict[str, Any]]:
    """BM25 alone — the comparison arm for the hybrid-beats-single-path demo."""
    es = es or client()
    response = es.search(index=INDEX, retriever=_lexical(query), size=size)
    return [citation(hit, METHOD_LEXICAL) for hit in response["hits"]["hits"]]


def search_semantic_only(query: str, size: int = DEFAULT_SIZE, es: Any = None) -> list[dict[str, Any]]:
    """ELSER alone — the arm that plausibly-but-wrongly answers an exact-token question."""
    es = es or client()
    response = es.search(index=INDEX, retriever=_sparse(query), size=size)
    return [citation(hit, METHOD_SEMANTIC) for hit in response["hits"]["hits"]]
