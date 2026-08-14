"""Load an OKF v0.2 knowledge bundle: Markdown files with YAML front matter.

Markdown is the source of truth. Nothing here touches the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)

TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class LiteralTimestampLoader(yaml.SafeLoader):
    """A loader that leaves timestamp-shaped scalars as text.

    PyYAML resolves them to `date` / `datetime`, which rewrites what the file says: an `at:`
    of `2026-08-10T19:06:23-07:00` came back out of `str()` as `2026-08-10 19:06:23-07:00`,
    with a space instead of the `T`, which is not valid ISO-8601. Provenance has to quote its
    source, not reinterpret it, so the text is kept and any date is parsed where one is
    required. The JVM port hit the same class of bug from the other direction, where the
    conversion moved `stale_after` a day earlier.
    """


LiteralTimestampLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != TIMESTAMP_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

# OKF v0.2 derives a trust tier from the `verified` actor list.
UNVERIFIED = "unverified"
MACHINE_CONFIRMED = "machine-confirmed"
HUMAN_REVIEWED = "human-reviewed"

# Markdown link, e.g. "[Claude Opus 5](../models/anthropic-claude-opus-5.md)"
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class BundleError(Exception):
    """A bundle file is malformed or violates the spec."""


@dataclass(frozen=True)
class Concept:
    """One OKF document."""

    path: Path
    id: str
    type: str
    title: str
    canonical: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    verified: list[dict[str, Any]] = field(default_factory=list)
    generated: dict[str, Any] = field(default_factory=dict)
    status: str = "stable"
    stale_after: date | None = None
    links: list[str] = field(default_factory=list)
    # Local extension: names a person might use for this concept in a question.
    aliases: list[str] = field(default_factory=list)
    body: str = ""

    @property
    def trust_tier(self) -> str:
        actors = [str(v.get("by", "")) for v in self.verified]
        if not actors:
            return UNVERIFIED
        if any(a.startswith("human:") for a in actors):
            return HUMAN_REVIEWED
        return MACHINE_CONFIRMED

    @property
    def verified_at(self) -> str | None:
        """The most recent verification timestamp, or None if unverified."""
        stamps = [str(v["at"]) for v in self.verified if "at" in v]
        return max(stamps) if stamps else None

    @property
    def source_url(self) -> str | None:
        for source in self.sources:
            if "resource" in source:
                return str(source["resource"])
        return None

    def is_stale(self, as_of: date) -> bool:
        """OKF v0.2: a concept is stale when today >= stale_after."""
        return self.stale_after is not None and as_of >= self.stale_after

    def link_targets(self, root: Path) -> list[Path]:
        """Resolve this concept's Markdown links to bundle-relative paths."""
        targets = []
        for link in self.links:
            match = MARKDOWN_LINK.search(link)
            if not match:
                continue
            resolved = (self.path.parent / match.group(1)).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                raise BundleError(
                    f"{self.path}: link escapes the bundle root: {match.group(1)}"
                ) from None
            targets.append(resolved)
        return targets


def parse_concept(path: Path) -> Concept:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(text)
    if not match:
        raise BundleError(f"{path}: no YAML front matter")

    # Safe despite the bare `load`: LiteralTimestampLoader derives from SafeLoader, so it
    # keeps SafeConstructor and cannot instantiate arbitrary Python types. It differs from
    # safe_load in exactly one respect — it does not resolve timestamps.
    meta = yaml.load(match.group(1), Loader=LiteralTimestampLoader) or {}
    if not isinstance(meta, dict):
        raise BundleError(f"{path}: front matter is not a mapping")

    # `type` is OKF's only always-required key; `id` is our lookup key.
    for required in ("type", "id"):
        if not meta.get(required):
            raise BundleError(f"{path}: missing required field '{required}'")

    # Now that timestamps stay text, an OKF lifecycle date is parsed here. Anything that is
    # not a plain date is rejected rather than coerced: a misread lifecycle would silently
    # disable staleness, which is the one guarantee the canonical layer cannot lose.
    stale_after = meta.get("stale_after")
    if stale_after is not None:
        try:
            stale_after = date.fromisoformat(str(stale_after))
        except ValueError:
            raise BundleError(
                f"{path}: stale_after must be a YYYY-MM-DD date, got {stale_after!r}"
            ) from None

    verified = meta.get("verified") or []
    if isinstance(verified, dict):  # OKF permits a single mapping without the list dash
        verified = [verified]

    return Concept(
        path=path,
        id=str(meta["id"]),
        type=str(meta["type"]),
        title=str(meta.get("title", meta["id"])),
        canonical=meta.get("canonical") or {},
        sources=meta.get("sources") or [],
        verified=verified,
        generated=meta.get("generated") or {},
        status=str(meta.get("status", "stable")),
        stale_after=stale_after,
        links=meta.get("links") or [],
        aliases=[str(a) for a in (meta.get("aliases") or [])],
        body=match.group(2).strip(),
    )


class Bundle:
    """An indexed OKF bundle, addressable by concept id."""

    def __init__(self, root: Path, concepts: dict[str, Concept]) -> None:
        self.root = root
        self._concepts = concepts

    @classmethod
    def load(cls, root: Path) -> Bundle:
        root = Path(root)
        if not root.is_dir():
            raise BundleError(f"bundle root not found: {root}")

        concepts: dict[str, Concept] = {}
        for path in sorted(root.rglob("*.md")):
            concept = parse_concept(path)
            if concept.id in concepts:
                raise BundleError(
                    f"duplicate concept id {concept.id!r}: "
                    f"{concepts[concept.id].path} and {path}"
                )
            concepts[concept.id] = concept

        by_path = {c.path.resolve(): c for c in concepts.values()}
        for concept in concepts.values():
            for target in concept.link_targets(root):
                if target not in by_path:
                    raise BundleError(f"{concept.path}: link target missing: {target}")
        return cls(root, concepts)

    def get(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def linked(self, concept_id: str) -> list[Concept]:
        concept = self.get(concept_id)
        if concept is None:
            return []
        by_path = {c.path.resolve(): c for c in self._concepts.values()}
        return [by_path[t] for t in concept.link_targets(self.root) if t in by_path]

    def __iter__(self):
        return iter(self._concepts.values())

    def __len__(self) -> int:
        return len(self._concepts)
