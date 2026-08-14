# Spec: Semantic Index

The contract both implementations build to. An index built differently returns different ranks
for the same corpus, and every number the findings publish stops meaning anything.

Implemented by `scripts/index_corpus.py` (Python) and `CorpusIndexer.java` (JVM). Both are
checked against this document, and against each other: the JVM test suite asserts that its
chunks are byte-identical to the ones in the reference index.

> This file is duplicated in
> [grounded-context-jvm](https://github.com/cdevarenne/grounded-context-jvm/blob/main/docs/index-spec.md)
> so each repo stands alone. The copies are compared by a test; see [Drift](#drift).

## Input

A directory of Markdown files with YAML front matter. Each file is one fetched page.

```yaml
---
id: anthropic-batch-processing          # unique; becomes the document id prefix
title: Batch processing
url: https://platform.claude.com/docs/en/build-with-claude/batch-processing
provider: anthropic
topic: api
fetched_at: 2026-08-13T07:34:00-0700    # kept as written, never reformatted
---
<body text>
```

Files are processed in file-name order. A file without front matter is skipped with a warning,
not an error — one malformed page must not stop an index build.

## Chunking

One document per chunk, not per page. The citation contract cites `chunk:N` with a snippet, so
the unit indexed has to be the unit cited.

| Constant | Value |
|---|---|
| `TARGET_CHUNK_CHARS` | 1200 |
| `MIN_CHUNK_CHARS` | 200 |

The rule, exactly:

1. Split the body on newlines. Strip each line. Discard empty lines.
2. Keep a running `size`. For each line: if `size > 0` and `size + line.length > 1200`, close the
   current chunk and start a new one.
3. Append the line. Add `line.length + 1` to `size`.
4. Close the final chunk.
5. Discard any chunk shorter than 200 characters.

Chunks join their lines with `\n`.

Extracted documentation arrives as many short lines rather than prose paragraphs, so packing
lines is what produces a passage a person would recognize — and a citation snippet has to be
quotable.

## Document

Id is `{front_matter.id}::{chunk_index}`, zero-based.

| Field | Source |
|---|---|
| `source_id` | front matter `id` |
| `title`, `url`, `provider`, `topic` | front matter, verbatim |
| `chunk_index` | position of the chunk within its page |
| `fetched_at` | front matter, **as written** — see below |
| `content` | the chunk text |

`fetched_at` is passed through as a string. A YAML parser that resolves timestamps will rewrite
it: PyYAML turns `2026-08-10T19:06:23-07:00` into a `datetime` whose `str()` uses a space instead
of the `T`, and SnakeYAML turns a plain date into a `java.util.Date` at UTC midnight, which reads
back a day earlier in a negative-offset zone. Both implementations disable timestamp resolution.
Provenance quotes its source; it does not reinterpret it.

## Mapping and settings

```json
{
  "settings": {
    "analysis": {
      "analyzer": {
        "exact_token": { "tokenizer": "whitespace", "filter": ["lowercase"] }
      }
    }
  },
  "mappings": {
    "properties": {
      "source_id":   { "type": "keyword" },
      "title":       { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "url":         { "type": "keyword" },
      "provider":    { "type": "keyword" },
      "topic":       { "type": "keyword" },
      "chunk_index": { "type": "integer" },
      "fetched_at":  { "type": "date" },
      "content": {
        "type": "text",
        "copy_to": "semantic",
        "fields": { "exact": { "type": "text", "analyzer": "exact_token" } }
      },
      "semantic": { "type": "semantic_text", "inference_id": ".elser-2-elasticsearch" }
    }
  }
}
```

Three parts of this are load-bearing:

- **`copy_to: semantic`** puts the same text behind both arms. Without it the lexical and sparse
  retrievers would be searching different content, and fusing them would mean nothing.
- **`content.exact`** keeps punctuation and hyphens. The standard analyzer strips punctuation and
  splits on hyphens, so `claude-opus-5` shatters and a code sample's `"rank_constant":` collapses
  onto a prose mention. Both fields are queried, because the exact field's strictness also hides
  identifiers the corpus only writes inside punctuation.
- **`semantic_text`** with an inference id runs ELSER at ingest. Changing the inference model
  changes every score, including the relevance floor.

## Retrieval constants

Not part of the index, but an index is only interchangeable with the same values:

| Constant | Value |
|---|---|
| `RANK_CONSTANT` (RRF `k`) | 20 |
| `RANK_WINDOW_SIZE` | 50 |
| `EXACT_TOKEN_BOOST` | 3.0 |
| `RELEVANCE_FLOOR` | 8.0 |

The floor is a property of this corpus and this inference model, not a portable threshold. The
method travels; the number does not.

## Drift

Two copies of a specification diverge unless something checks them. `test_index_spec.py` here and
`IndexSpecParityTest` in the JVM repo compare the two files and fail when they differ. Each skips
when the other repo is not checked out alongside.
