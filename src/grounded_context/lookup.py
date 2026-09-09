"""The deterministic path: exact lookup of canonical fields, plus link traversal.

No embeddings, no ranking, no network. A field either exists or it does not.

Every function here takes a `KnowledgeStore`. It does not take a `Bundle`. The engine reads four
methods and does not know whether the concepts came from Markdown files, from a database or from
a dict in a test. See `store.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .bundle import Concept
from .store import KnowledgeStore


@dataclass(frozen=True)
class LookupResult:
    value: Any
    concept: Concept
    field: str
    hops: tuple[str, ...]

    @property
    def locator(self) -> str:
        return f"canonical.{self.field}"


def lookup(bundle: KnowledgeStore, entity_id: str, field: str) -> LookupResult | None:
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
    bundle: KnowledgeStore, entity_id: str, field: str, max_hops: int = 1
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


def query_entities(bundle: KnowledgeStore, field: str) -> list[LookupResult]:
    """Every concept that holds `field`, with the value each one holds.

    `lookup` answers one entity at a time. A rollup question asks the same field of every entity:
    "which of these models support vision?". The engine had no answer for that shape, so the
    question fell through to ranked passages. Passages do not answer it. They discuss the topic.

    This keeps the guarantee the single-entity path gives. Each result carries its own concept, so
    each carries its own OKF provenance: source, trust tier, verification date and staleness. A
    rollup is therefore a list of exact facts, not a summary of them.

    Results come back in concept-id order. The order is stable, so a rendered list does not change
    between runs for a reason the data did not cause.
    """
    return sorted(
        (
            LookupResult(
                value=concept.canonical[field],
                concept=concept,
                field=field,
                hops=(concept.id,),
            )
            for concept in bundle
            if field in concept.canonical
        ),
        key=lambda result: result.concept.id,
    )


def is_rollup(text: str) -> bool:
    """Does this question ask one field of several entities, rather than of one?

    Two signals, and both must hold. The question names no single entity, and it uses a plural
    or a set word. "Which of these models support vision?" qualifies. "Does Opus 5 support
    vision?" does not, because it names an entity.

    The check is deliberately narrow. A rollup that answers the wrong question is worse than a
    rollup that does not fire, because the single-entity path and the refusal are both correct
    fallbacks.
    """
    lowered = text.lower()
    return any(signal in lowered for signal in ROLLUP_SIGNALS)


#: Phrasings that ask about a set of entities. Matched as whole terms, like every other match
#: in this module.
ROLLUP_SIGNALS = (
    "which of these",
    "which models",
    "which ones",
    "list the models",
    "all models",
    "each model",
    "every model",
)


def find_entity(bundle: KnowledgeStore, text: str) -> str | None:
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


def find_entities(bundle: KnowledgeStore, text: str) -> list[str]:
    """Every concept the text names, in the order the text names them.

    `find_entity` returns the single best match. That is right for a lookup and wrong for a
    comparison: "compare A and B" names two concepts, and answering for one of them is not a
    partial answer, it is the answer to a different question.

    Order follows the query rather than concept id, so a rendered comparison reads in the order
    it was asked. Matching is `contains`, so the whole-term rule that keeps `vision` out of
    `revision` applies here too.
    """
    found: dict[str, int] = {}
    for concept in bundle:
        needles = {concept.id, *concept.aliases}
        for key in ("model_string", "api_alias"):
            if key in concept.canonical:
                needles.add(str(concept.canonical[key]))
        starts = [at for at in (_start(text, needle) for needle in needles) if at is not None]
        if starts:
            found[concept.id] = min(starts)
    return sorted(found, key=lambda concept_id: found[concept_id])


def _start(text: str, needle: str) -> int | None:
    """Where `needle` begins in `text` as a whole term, or `None`."""
    match = _whole(needle.lower()).search(text.lower())
    return match.start() if match else None


def find_field(
    bundle: KnowledgeStore,
    text: str,
    entity_id: str | None = None,
    synonyms: dict[str, str] | None = None,
) -> str | None:
    """Match a query against canonical field names, directly or by synonym.

    `synonyms` maps a phrase to a canonical field name. It defaults to `SYNONYMS` below.

    It is a parameter because it is matching vocabulary, not canonical truth. An adopter whose
    users say "token limit" needs to add that phrase. They must not have to edit this package, and
    they must not put it in the bundle: an OKF file is governed by verification dates and trust
    tiers, and a query synonym has neither. The two belong to different lifecycles.
    """
    table = SYNONYMS if synonyms is None else synonyms
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

    for phrase in sorted(table, key=len, reverse=True):
        if contains(text, phrase) and table[phrase] in fields:
            return table[phrase]
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
    # Longer than "output tokens" on purpose. "How much does X cost per million output
    # tokens?" contains that phrase, so without these it resolved to `max_output_tokens`
    # and answered a token count to a question about dollars — cited, and wrong.
    "cost per million input": "input_price_per_mtok_usd",
    "cost per million output": "output_price_per_mtok_usd",
    "price per million input": "input_price_per_mtok_usd",
    "price per million output": "output_price_per_mtok_usd",
    "adaptive thinking": "adaptive_thinking",
    "extended thinking": "extended_thinking",
    "vision": "vision",
    "images": "vision",
}
