"""CLI for the deterministic path. Zero cloud dependency."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .bundle import BundleError
from .provenance import render
from .router import route
from .service import as_of_date, ask, load_bundle, lookup_field


def _emit(envelope: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        print(render(envelope))
    return 0 if envelope["citations"] else 1


def cmd_lookup(args: argparse.Namespace) -> int:
    envelope = lookup_field(
        load_bundle(args.bundle), args.entity, args.field, as_of_date(args.as_of)
    )
    return _emit(envelope, args.json)


def cmd_ask(args: argparse.Namespace) -> int:
    envelope = ask(load_bundle(args.bundle), args.query, as_of_date(args.as_of))
    return _emit(envelope, args.json)


def cmd_entities(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.bundle)
    as_of = as_of_date(args.as_of)
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
        prog="gctx", description="Grounded context layer — deterministic path."
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
