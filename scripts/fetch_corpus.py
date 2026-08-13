"""Fetch the curated semantic corpus listed in corpus/manifest.yml.

This is a hand-picked reading list, not a crawler: it follows no links, obeys a delay
between requests, and touches only the URLs a human chose. Fetched text lands in
corpus/raw/, which is gitignored — third-party documentation prose is never committed to
this repo. Only the manifest is.

    uv run python scripts/fetch_corpus.py [--force] [--only <id>]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "manifest.yml"
RAW = ROOT / "corpus" / "raw"

USER_AGENT = "grounded-context-corpus-fetcher/0.1 (personal portfolio project)"
DELAY_SECONDS = 1.0
TIMEOUT_SECONDS = 30
SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "svg"}


@dataclass(frozen=True)
class Source:
    """One curated document from the manifest."""

    id: str
    title: str
    url: str
    provider: str
    topic: str

    @property
    def destination(self) -> Path:
        return RAW / f"{self.id}.md"


class TextExtractor(HTMLParser):
    """Collect readable text, dropping scripts, nav chrome, and markup."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._suppress += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._suppress:
            self._suppress -= 1

    def handle_data(self, data: str) -> None:
        if not self._suppress and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        joined = "\n".join(self._chunks)
        return re.sub(r"\n{3,}", "\n\n", joined)


def load_manifest(path: Path = MANIFEST) -> list[Source]:
    """Read the curated source list."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Source(**entry) for entry in data.get("sources", [])]


def fetch(source: Source) -> str:
    """Retrieve one page and return its readable text."""
    request = urllib.request.Request(source.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def write(source: Source, text: str) -> None:
    """Store the fetched text with a provenance header, outside version control."""
    RAW.mkdir(parents=True, exist_ok=True)
    header = yaml.safe_dump(
        {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "provider": source.provider,
            "topic": source.topic,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        sort_keys=False,
        allow_unicode=True,
    )
    source.destination.write_text(f"---\n{header}---\n\n{text}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="refetch existing files")
    parser.add_argument("--only", help="fetch a single manifest id")
    args = parser.parse_args(argv)

    sources = load_manifest()
    if args.only:
        sources = [s for s in sources if s.id == args.only]
        if not sources:
            print(f"error: no manifest entry with id {args.only!r}", file=sys.stderr)
            return 2

    failures = 0
    for source in sources:
        if source.destination.exists() and not args.force:
            print(f"skip    {source.id}")
            continue
        try:
            text = fetch(source)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"FAILED  {source.id}: {exc}", file=sys.stderr)
            failures += 1
            continue
        write(source, text)
        print(f"ok      {source.id}  ({len(text):,} chars)")
        time.sleep(DELAY_SECONDS)

    print(f"\n{len(sources) - failures}/{len(sources)} fetched into {RAW}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
