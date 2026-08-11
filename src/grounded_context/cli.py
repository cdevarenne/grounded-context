"""CLI for the deterministic path. Zero cloud dependency."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import Bundle, BundleError
from .lookup import find_entity, find_field, resolve
from .provenance import (
    DETERMINISTIC,
    SEMANTIC,
    citation,
    grounded_answer,
    render,
)
from .router import SEMANTIC as ROUTE_SEMANTIC
from .router import Route, route

DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / "knowledge"


def _bundle_root(explicit: str | None) -> Path:
    return Path(explicit or os.environ.get("GC_BUNDLE") or DEFAULT_BUNDLE)


def _as_of(raw: str | None) -> date:
    if raw is None:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(raw)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _emit(envelope: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        print(render(envelope))
    return 0 if envelope["citations"] else 1


def _answer(result, as_of: date, decision: Route | None) -> dict[str, Any]:
    router = decision.as_dict() if decision else None
    if result is None:
        return grounded_answer("", [], DETERMINISTIC, router)
    return grounded_answer(
        _format_value(result.value), [citation(result, as_of)], DETERMINISTIC, router
    )


def cmd_lookup(args: argparse.Namespace) -> int:
    bundle = Bundle.load(_bundle_root(args.bundle))
    result = resolve(bundle, args.entity, args.field)
    return _emit(_answer(result, _as_of(args.as_of), None), args.json)


def cmd_ask(args: argparse.Namespace) -> int:
    bundle = Bundle.load(_bundle_root(args.bundle))
    decision = route(args.query)

    if decision.route == ROUTE_SEMANTIC:
        # The semantic path is not wired yet. Refusing is the honest answer.
        return _emit(
            grounded_answer("", [], SEMANTIC, decision.as_dict()), args.json
        )

    entity = find_entity(bundle, args.query)
    field = find_field(bundle, args.query, entity)
    result = resolve(bundle, entity, field) if entity and field else None
    return _emit(_answer(result, _as_of(args.as_of), decision), args.json)


def cmd_entities(args: argparse.Namespace) -> int:
    bundle = Bundle.load(_bundle_root(args.bundle))
    as_of = _as_of(args.as_of)
    for concept in sorted(bundle, key=lambda c: c.id):
        flag = " ⚠ STALE" if concept.is_stale(as_of) else ""
        print(f"{concept.id}  [{concept.type}]  {concept.trust_tier}{flag}")
        for name in sorted(concept.canonical):
            print(f"    canonical.{name}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    decision = route(args.query)
    if args.json:
        print(json.dumps(decision.as_dict(), indent=2))
    else:
        print(f"{decision.route} — {decision.rationale}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gc", description="Grounded context layer — deterministic path."
    )
    parser.add_argument("--bundle", help="path to the knowledge/ bundle")
    parser.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="evaluate staleness as of this date instead of today",
    )
    parser.add_argument("--json", action="store_true", help="emit the raw envelope")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lookup", help="exact lookup of one canonical field")
    p.add_argument("entity")
    p.add_argument("field")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("ask", help="route a natural-language question, then answer it")
    p.add_argument("query")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("route", help="show the routing decision only")
    p.add_argument("query")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("entities", help="list concepts and their canonical fields")
    p.set_defaults(func=cmd_entities)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BundleError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
