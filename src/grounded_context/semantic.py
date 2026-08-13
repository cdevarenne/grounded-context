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

METHOD = "hybrid(bm25+elser,rrf)"
METHOD_LEXICAL = "bm25"
METHOD_SEMANTIC = "elser"


EXACT_TOKEN_BOOST = 3.0


def _lexical(query: str) -> dict[str, Any]:
    """BM25 over the analyzed text, plus the whitespace-tokenized subfield.

    The second clause is what lets an exact identifier — `rank_constant`, `num_candidates` —
    match as one token. Without it the standard analyzer splits on the underscore and the
    lexical arm cannot see the very thing it is supposed to be good at.
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


def search(
    query: str, size: int = DEFAULT_SIZE, es: Any = None, retriever: Any = None
) -> list[dict[str, Any]]:
    """Run the hybrid search and return citations, best first."""
    es = es or client()
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
