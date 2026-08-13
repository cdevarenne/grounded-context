"""Answer construction shared by every consumer of the deterministic path.

The CLI and the MCP server must produce identical envelopes. The citation contract is the
product here, so it lives in one place rather than being re-implemented per surface.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import Bundle
from .lookup import find_entity, find_field, resolve
from .provenance import DETERMINISTIC, SEMANTIC, citation, grounded_answer
from .router import SEMANTIC as ROUTE_SEMANTIC
from .router import Route, route

DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / "knowledge"


def bundle_root(explicit: str | None = None) -> Path:
    """Resolve the bundle directory: explicit path, then `GC_BUNDLE`, then the default."""
    return Path(explicit or os.environ.get("GC_BUNDLE") or DEFAULT_BUNDLE)


def load_bundle(explicit: str | None = None) -> Bundle:
    """Load and validate the knowledge bundle."""
    return Bundle.load(bundle_root(explicit))


def as_of_date(raw: str | None = None) -> date:
    """Parse an ISO date for staleness evaluation, defaulting to today."""
    if raw is None:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(raw)


def format_value(value: Any) -> str:
    """Render a canonical value for reading: booleans as yes/no, ints with separators."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def lookup_field(
    bundle: Bundle,
    entity_id: str,
    field: str,
    as_of: date,
    decision: Route | None = None,
) -> dict[str, Any]:
    """Envelope for one exact field, or the refusal when the bundle doesn't hold it."""
    result = resolve(bundle, entity_id, field)
    router = decision.as_dict() if decision else None
    if result is None:
        return grounded_answer("", [], DETERMINISTIC, router)
    return grounded_answer(
        format_value(result.value), [citation(result, as_of)], DETERMINISTIC, router
    )


def ask(bundle: Bundle, query: str, as_of: date) -> dict[str, Any]:
    """Route a natural-language question, then answer it on the path chosen."""
    decision = route(query)
    if decision.route == ROUTE_SEMANTIC:
        # The semantic path is not wired yet. Refusing is the honest answer.
        return grounded_answer("", [], SEMANTIC, decision.as_dict())

    entity = find_entity(bundle, query)
    field = find_field(bundle, query, entity)
    if entity is None or field is None:
        return grounded_answer("", [], DETERMINISTIC, decision.as_dict())
    return lookup_field(bundle, entity, field, as_of, decision)
