"""The deterministic path: exact lookup of canonical fields, plus link traversal.

No embeddings, no ranking, no network. A field either exists or it does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .bundle import Bundle, Concept


@dataclass(frozen=True)
class LookupResult:
    value: Any
    concept: Concept
    field: str
    hops: tuple[str, ...]

    @property
    def locator(self) -> str:
        return f"canonical.{self.field}"


def lookup(bundle: Bundle, entity_id: str, field: str) -> LookupResult | None:
    """Exact match on one canonical field of one concept."""
    concept = bundle.get(entity_id)
    if concept is None or field not in concept.canonical:
        return None
    return LookupResult(
        value=concept.canonical[field],
        concept=concept,
        field=field,
        hops=(entity_id,),
    )


def resolve(
    bundle: Bundle, entity_id: str, field: str, max_hops: int = 1
) -> LookupResult | None:
    """Exact lookup, falling back to the concept's Markdown links.

    A model file does not restate the endpoint's HTTP method; it links to the endpoint
    concept that owns it. One hop is enough for that and keeps the audit trail short —
    every hop is recorded so the citation can show the path taken.
    """
    direct = lookup(bundle, entity_id, field)
    if direct is not None:
        return direct
    if max_hops < 1 or bundle.get(entity_id) is None:
        return None

    for neighbour in bundle.linked(entity_id):
        hit = resolve(bundle, neighbour.id, field, max_hops - 1)
        if hit is not None:
            return LookupResult(
                value=hit.value,
                concept=hit.concept,
                field=hit.field,
                hops=(entity_id, *hit.hops),
            )
    return None


@lru_cache(maxsize=512)
def _whole(needle: str) -> re.Pattern[str]:
    r"""`needle` as a whole term: no word character may touch either end of it.

    Plain containment was the bug. `vision` sits inside `revision`, so "what is the exact
    revision number for opus 5?" resolved to the `vision` field and answered `yes`, cited to
    `canonical.vision = True` — a confident, wrong, fully sourced answer, which is the one
    outcome the deterministic path exists to prevent.

    Lookarounds rather than `\b`, because `\b` is defined against the character next to it: a
    needle that begins or ends on punctuation would assert the opposite of what is meant here.
    `(?<!\w)` and `(?!\w)` say the same thing whatever the needle's own edges look like.

    Word characters, not whitespace, so identifiers keep matching inside longer ones the way
    they always have: `claude-haiku-4-5` is still found in `claude-haiku-4-5-20251001`, because
    a hyphen is not a word character — and longest-match-wins still prefers the longer id.
    """
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")


def contains(text: str, needle: str) -> bool:
    """True when `needle` appears in `text` as a whole term. Both are compared lowercased."""
    return _whole(needle.lower()).search(text.lower()) is not None


def find_entity(bundle: Bundle, text: str) -> str | None:
    """Match a query against concept ids and their canonical id-ish strings.

    Longest match wins so that `claude-haiku-4-5-20251001` is not shadowed by
    `claude-haiku-4-5`.
    """
    candidates: list[tuple[int, str]] = []
    for concept in bundle:
        needles = {concept.id, *concept.aliases}
        for key in ("model_string", "api_alias"):
            if key in concept.canonical:
                needles.add(str(concept.canonical[key]))
        for needle in needles:
            if contains(text, needle):
                candidates.append((len(needle), concept.id))
    if not candidates:
        return None
    return max(candidates)[1]


def find_field(bundle: Bundle, text: str, entity_id: str | None = None) -> str | None:
    """Match a query against canonical field names, directly or by synonym."""
    scope = [bundle.get(entity_id)] if entity_id else list(bundle)
    fields = {f for c in scope if c for f in c.canonical}
    if entity_id:
        for neighbour in bundle.linked(entity_id):
            fields.update(neighbour.canonical)

    best: tuple[int, str] | None = None
    for name in fields:
        for phrase in (name, name.replace("_", " ")):
            if contains(text, phrase) and (best is None or len(phrase) > best[0]):
                best = (len(phrase), name)
    if best is not None:
        return best[1]

    for phrase in sorted(SYNONYMS, key=len, reverse=True):
        if contains(text, phrase) and SYNONYMS[phrase] in fields:
            return SYNONYMS[phrase]
    return None


# Phrasings that don't contain the field name. Matched longest-phrase-first, so
# "max output" wins over a bare "output" substring.
SYNONYMS: dict[str, str] = {
    "context window": "context_window_tokens",
    "ctx window": "context_window_tokens",
    "context length": "context_window_tokens",
    "max output": "max_output_tokens",
    "maximum output": "max_output_tokens",
    "output tokens": "max_output_tokens",
    "output limit": "max_output_tokens",
    "model id": "model_string",
    "model string": "model_string",
    "alias": "api_alias",
    "endpoint": "default_endpoint",
    "input price": "input_price_per_mtok_usd",
    "output price": "output_price_per_mtok_usd",
    "input cost": "input_price_per_mtok_usd",
    "output cost": "output_price_per_mtok_usd",
    "adaptive thinking": "adaptive_thinking",
    "extended thinking": "extended_thinking",
    "vision": "vision",
    "images": "vision",
}
