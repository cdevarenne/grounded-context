"""Audit the fusion math: does ES's fused score equal the RRF formula from the two arms' ranks?"""
from grounded_context.es_client import client, INDEX
from grounded_context.semantic import (
    RANK_CONSTANT, RANK_WINDOW_SIZE, _lexical, _sparse, hybrid_retriever)

es = client()
K = RANK_CONSTANT

def ranked(retriever, size=RANK_WINDOW_SIZE):
    r = es.search(index=INDEX, retriever=retriever, size=size,
                  _source=["source_id", "chunk_index"])
    return [(h["_source"]["source_id"], h["_source"]["chunk_index"]) for h in r["hits"]["hits"]]

def fused(query, size=5):
    r = es.search(index=INDEX, retriever=hybrid_retriever(query), size=size,
                  _source=["source_id", "chunk_index"])
    return [((h["_source"]["source_id"], h["_source"]["chunk_index"]), h["_score"])
            for h in r["hits"]["hits"]]

for query in ["What is reciprocal rank fusion?", "rank_constant",
              "How do I bake sourdough bread?"]:
    lex = ranked(_lexical(query))
    spa = ranked(_sparse(query))
    print(f"\n=== {query!r}   (k={K}) ===")
    print(f"{'doc':34} {'bm25':>5} {'elser':>6} {'predicted':>10} {'observed':>9} {'delta':>9}")
    for doc, observed in fused(query, size=3):
        rl = lex.index(doc) + 1 if doc in lex else None
        rs = spa.index(doc) + 1 if doc in spa else None
        predicted = (1 / (K + rl) if rl else 0) + (1 / (K + rs) if rs else 0)
        label = f"{doc[0]}:{doc[1]}"
        print(f"{label:34} {str(rl):>5} {str(rs):>6} {predicted:10.6f} {observed:9.6f} "
              f"{abs(predicted - observed):9.2e}")
