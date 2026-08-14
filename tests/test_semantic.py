"""Semantic-path tests.

The unit tests need no cluster: the citation shape and the retriever body are the parts that
must not drift. The live tests are skipped without credentials so a fresh clone still passes,
and they pin the claim this project actually makes about hybrid search — that it is never worse
than the weaker arm, not that it beats the stronger one. `rank_window_size` is the case where
it does not, and it is pinned too.
"""

from __future__ import annotations

from typing import Any

import pytest

from grounded_context.es_client import INDEX, is_configured
from grounded_context.provenance import DETERMINISTIC, SEMANTIC
from grounded_context.semantic import (
    EXACT_TOKEN_BOOST,
    RANK_CONSTANT,
    RANK_WINDOW_SIZE,
    citation,
    hybrid_retriever,
    search,
    search_lexical_only,
    search_semantic_only,
)

TOKEN = "rank_constant"
DEFINING_CHUNK = ("elastic-rrf", 1)

HIT: dict[str, Any] = {
    "_score": 0.0871,
    "_source": {
        "source_id": "elastic-rrf",
        "url": "https://www.elastic.co/docs/…",
        "chunk_index": 1,
        "fetched_at": "2026-08-13T06:44:00-0700",
        "content": "x" * 900,
    },
}

DETERMINISTIC_KEYS = {
    "path", "source_id", "source_url", "locator", "method", "score", "verified_at",
    "trust_tier", "status", "stale_after", "is_stale", "hops", "snippet",
}


def test_citation_matches_the_deterministic_key_set() -> None:
    """Both engines emit one shape, or the dual path stops reading as one system."""
    assert set(citation(HIT)) == DETERMINISTIC_KEYS


def test_citation_reports_retrieval_not_verification() -> None:
    cite = citation(HIT)
    assert cite["path"] == SEMANTIC
    assert cite["path"] != DETERMINISTIC
    assert cite["locator"] == "chunk:1"
    assert cite["score"] == 0.0871
    # A fetched page has no OKF trust tier; claiming one would be a lie.
    assert cite["trust_tier"] is None
    assert cite["verified_at"] == "2026-08-13T06:44:00-0700"


def test_citation_snippet_is_bounded() -> None:
    assert len(citation(HIT)["snippet"]) <= 320


def test_retriever_fuses_both_arms_with_documented_constants() -> None:
    body = hybrid_retriever("rank_constant")["rrf"]
    assert body["rank_constant"] == RANK_CONSTANT
    assert body["rank_window_size"] == RANK_WINDOW_SIZE
    arms = body["retrievers"]
    assert len(arms) == 2
    assert any("semantic" in str(arm) for arm in arms), "no ELSER arm"
    assert any("content.exact" in str(arm) for arm in arms), "no exact-token arm"


# --- live cluster -------------------------------------------------------------------

pytestmark_reason = "no ES_URL / ES_API_KEY, or the corpus index is missing"


def _index_ready() -> bool:
    if not is_configured():
        return False
    from grounded_context.es_client import client

    try:
        return bool(client().indices.exists(index=INDEX))
    except Exception:
        return False


requires_index = pytest.mark.skipif(not _index_ready(), reason=pytestmark_reason)


def _rank_of_defining_chunk(results: list[dict[str, Any]]) -> int | None:
    for position, cite in enumerate(results, 1):
        source, chunk = DEFINING_CHUNK
        if cite["source_id"] == source and cite["locator"] == f"chunk:{chunk}":
            return position
    return None


@requires_index
def test_hybrid_returns_grounded_citations() -> None:
    results = search("How should I chunk documents for retrieval?", size=3)
    assert results and all(cite["source_url"] for cite in results)
    assert all(cite["method"] == "hybrid(bm25+elser,rrf)" for cite in results)


@requires_index
@pytest.mark.parametrize(
    "query", [f"What does the {TOKEN} parameter do?", TOKEN]
)
def test_hybrid_ranks_the_defining_chunk_first_for_both_phrasings(query: str) -> None:
    """eval.md Q9. Neither arm alone manages this across both phrasings; fusion does."""
    assert _rank_of_defining_chunk(search(query, size=20)) == 1


@requires_index
@pytest.mark.parametrize(
    "query", ["How do I bake sourdough bread?", "What is the capital of France?"]
)
def test_out_of_domain_questions_return_nothing(query: str) -> None:
    """Citing an irrelevant passage is worse than admitting there is no grounded answer."""
    assert search(query) == []


@requires_index
def test_the_floor_is_what_rejects_them_not_the_absence_of_hits() -> None:
    """Disabling the floor shows the index would happily return those same passages."""
    ungated = search("How do I bake sourdough bread?", floor=None)
    assert ungated, "without the floor the index returns plausible-looking chunks"
    assert all(cite["score"] for cite in ungated)


@requires_index
def test_rrf_score_cannot_separate_relevant_from_irrelevant() -> None:
    """Why the floor reads a pre-fusion score: RRF encodes rank, not match quality."""
    on_topic = search("How do I stream responses from the API?", size=1)
    off_topic = search("What is the capital of France?", size=1, floor=None)
    assert on_topic and off_topic
    assert abs(on_topic[0]["score"] - off_topic[0]["score"]) < 0.02


@requires_index
def test_neither_single_arm_wins_both_phrasings() -> None:
    """The actual finding: which arm degrades depends on how the question is phrased."""
    sentence = f"What does the {TOKEN} parameter do?"
    lexical_on_sentence = _rank_of_defining_chunk(search_lexical_only(sentence, size=20))
    semantic_on_token = _rank_of_defining_chunk(search_semantic_only(TOKEN, size=20))
    assert lexical_on_sentence is not None and lexical_on_sentence > 1
    assert semantic_on_token is not None and semantic_on_token > 1


# --- what the analyzer actually does -------------------------------------------------
#
# findings.md once claimed the standard analyzer split `rank_constant` on the underscore.
# It does not. These pin the real behavior so the claim cannot drift back.


def _tokens(field: str, text: str) -> list[str]:
    from grounded_context.es_client import client

    analyzed = client().indices.analyze(index=INDEX, field=field, text=text)
    return [token["token"] for token in analyzed["tokens"]]


@requires_index
@pytest.mark.parametrize(
    ("identifier", "standard"),
    [
        ("rank_constant", ["rank_constant"]),
        ("num_candidates", ["num_candidates"]),
        ("claude-opus-5", ["claude", "opus", "5"]),
        ("claude-haiku-4-5", ["claude", "haiku", "4", "5"]),
    ],
)
def test_standard_analysis_keeps_underscores_and_splits_hyphens(
    identifier: str, standard: list[str]
) -> None:
    """UAX #29 treats `_` as a connector and `-` as a break — not the other way around."""
    assert _tokens("content", identifier) == standard
    assert _tokens("content.exact", identifier) == [identifier]


@requires_index
def test_the_exact_subfield_separates_prose_from_code_samples() -> None:
    """Why the subfield earns its place, now that 'it rescues a split token' is disproved.

    Standard analysis strips punctuation, so a code sample's `"rank_constant":` collapses onto
    the prose mention and the defining chunk competes with every chunk that merely uses the
    term. The whitespace subfield keeps the punctuation attached, so only the bare mention
    matches.
    """
    from grounded_context.es_client import client

    es = client()
    broad = es.count(index=INDEX, query={"match": {"content": TOKEN}})["count"]
    narrow = es.count(index=INDEX, query={"match": {"content.exact": TOKEN}})["count"]
    assert broad > narrow == 1


@requires_index
def test_the_exact_token_boost_breaks_ties_without_carrying_the_finding() -> None:
    """Pins what the boost is worth, so it is never credited with the punctuation effect.

    `multi-index` is one of the three identifiers in this corpus whose rank depends on it.
    Q9 does not, which is the point: the finding survives at boost 1.0.
    """
    from grounded_context.es_client import client

    es = client()
    target = ("elastic-semantic-text", 3)

    def rank(boost: float, query: str, expect: tuple[str, int]) -> int | None:
        body = {
            "standard": {
                "query": {
                    "bool": {
                        "should": [
                            {"match": {"content": query}},
                            {"match": {"content.exact": {"query": query, "boost": boost}}},
                        ]
                    }
                }
            }
        }
        response = es.search(index=INDEX, retriever=body, size=20,
                             _source=["source_id", "chunk_index"])
        for position, hit in enumerate(response["hits"]["hits"], 1):
            source = hit["_source"]
            if (source["source_id"], source["chunk_index"]) == expect:
                return position
        return None

    assert rank(EXACT_TOKEN_BOOST, "multi-index", target) == 1
    assert rank(1.0, "multi-index", target) == 2
    # The published Q9 result does not depend on it.
    assert rank(1.0, TOKEN, DEFINING_CHUNK) == rank(EXACT_TOKEN_BOOST, TOKEN, DEFINING_CHUNK) == 1


@requires_index
def test_the_lexical_arm_needs_both_fields_not_just_the_exact_one() -> None:
    """The same strictness hides identifiers the corpus only writes inside punctuation."""
    from grounded_context.es_client import client

    es = client()
    quoted = "claude-sonnet-4-6"
    assert es.count(index=INDEX, query={"match": {"content": quoted}})["count"] > 0
    assert es.count(index=INDEX, query={"match": {"content.exact": quoted}})["count"] == 0
    # The shipped arm still finds it, because it queries `content` alongside `content.exact`.
    assert search_lexical_only(quoted, size=1)


@requires_index
def test_the_fused_score_is_exactly_the_sum_of_reciprocal_ranks() -> None:
    """Audit the fusion math against its definition rather than reading it out of the engine.

    Every other claim about RRF here is inferred from scores Elasticsearch produced. This is
    the one check that verifies the formula itself, which is what Finding 3 argues from.
    """
    from grounded_context.es_client import client
    from grounded_context.semantic import _lexical, _sparse

    es = client()
    query = "What is reciprocal rank fusion?"

    def ranked(retriever):
        response = es.search(index=INDEX, retriever=retriever, size=RANK_WINDOW_SIZE,
                             _source=["source_id", "chunk_index"])
        return [(h["_source"]["source_id"], h["_source"]["chunk_index"])
                for h in response["hits"]["hits"]]

    lexical, sparse = ranked(_lexical(query)), ranked(_sparse(query))
    fused = es.search(index=INDEX, retriever=hybrid_retriever(query), size=3,
                      _source=["source_id", "chunk_index"])["hits"]["hits"]

    for hit in fused:
        doc = (hit["_source"]["source_id"], hit["_source"]["chunk_index"])
        predicted = sum(
            1 / (RANK_CONSTANT + arm.index(doc) + 1)
            for arm in (lexical, sparse) if doc in arm
        )
        assert predicted == pytest.approx(hit["_score"], abs=1e-6), doc


@requires_index
def test_the_fused_ceiling_is_never_reached_because_the_arms_disagree() -> None:
    """2/(k+1) needs rank 1 in both arms. Finding 1 says that rarely happens."""
    from grounded_context.es_client import client

    ceiling = 2 / (RANK_CONSTANT + 1)
    top = client().search(index=INDEX, retriever=hybrid_retriever("What is reciprocal rank fusion?"),
                          size=1)["hits"]["hits"][0]["_score"]
    assert top < ceiling
    # Rank 1 in one arm and rank 2 in the other is the best this corpus achieves.
    assert top == pytest.approx(1 / 21 + 1 / 22, abs=1e-6)
