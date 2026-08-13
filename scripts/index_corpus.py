"""Index the fetched corpus into Elasticsearch for the semantic path.

One document per chunk, not per page: the citation contract cites `chunk:N` with a snippet, so
the unit indexed has to be the unit cited. Each chunk carries its source metadata, and
`content` is copied into a `semantic_text` field so the same text is reachable both lexically
(BM25) and semantically (ELSER) — which is what makes the RRF fusion in semantic.py meaningful.

The index is a rebuildable projection. Markdown on disk stays the source of truth.

    uv run --extra es python scripts/index_corpus.py [--recreate]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterator

import yaml

from grounded_context.bundle import FRONT_MATTER
from grounded_context.es_client import INDEX, INFERENCE_ID, client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "corpus" / "raw"

TARGET_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 200

# The standard analyzer splits `rank_constant` into "rank" + "constant", so an exact
# identifier is unfindable by the lexical arm — the precise failure hybrid search is meant to
# prevent. A whitespace-tokenized subfield keeps such tokens whole.
SETTINGS: dict[str, Any] = {
    "analysis": {
        "analyzer": {
            "exact_token": {"tokenizer": "whitespace", "filter": ["lowercase"]}
        }
    }
}

MAPPING: dict[str, Any] = {
    "properties": {
        "source_id": {"type": "keyword"},
        "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
        "url": {"type": "keyword"},
        "provider": {"type": "keyword"},
        "topic": {"type": "keyword"},
        "chunk_index": {"type": "integer"},
        "fetched_at": {"type": "date"},
        "content": {
            "type": "text",
            "copy_to": "semantic",
            "fields": {"exact": {"type": "text", "analyzer": "exact_token"}},
        },
        "semantic": {"type": "semantic_text", "inference_id": INFERENCE_ID},
    }
}


def chunk(text: str) -> list[str]:
    """Pack consecutive lines into passages of roughly TARGET_CHUNK_CHARS.

    Extracted documentation text arrives as many short lines rather than prose paragraphs,
    so packing lines is what produces passages a person would recognize as a passage — and
    a citation snippet has to be quotable.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        if size and size + len(line) > TARGET_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def documents() -> Iterator[dict[str, Any]]:
    """Yield one bulk action per chunk of every fetched page."""
    for path in sorted(RAW.glob("*.md")):
        match = FRONT_MATTER.match(path.read_text(encoding="utf-8"))
        if not match:
            print(f"skip {path.name}: no front matter", file=sys.stderr)
            continue
        meta = yaml.safe_load(match.group(1)) or {}
        for index, passage in enumerate(chunk(match.group(2))):
            yield {
                "_index": INDEX,
                "_id": f"{meta['id']}::{index}",
                "_source": {
                    "source_id": meta["id"],
                    "title": meta["title"],
                    "url": meta["url"],
                    "provider": meta["provider"],
                    "topic": meta["topic"],
                    "chunk_index": index,
                    "fetched_at": str(meta["fetched_at"]),
                    "content": passage,
                },
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recreate", action="store_true", help="delete and rebuild the index first"
    )
    args = parser.parse_args(argv)

    from elasticsearch.helpers import bulk

    es = client()
    if args.recreate and es.indices.exists(index=INDEX):
        es.indices.delete(index=INDEX)
        print(f"deleted index {INDEX}")
    if not es.indices.exists(index=INDEX):
        es.indices.create(index=INDEX, mappings=MAPPING, settings=SETTINGS)
        print(f"created index {INDEX} (semantic_text via {INFERENCE_ID})")

    actions = list(documents())
    if not actions:
        print(f"error: no fetched pages in {RAW}", file=sys.stderr)
        return 1

    succeeded, errors = bulk(es.options(request_timeout=300), actions)
    es.indices.refresh(index=INDEX)
    count = es.count(index=INDEX)["count"]
    print(f"indexed {succeeded} chunks, {len(errors) if errors else 0} errors")
    print(f"{INDEX} now holds {count} documents")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
