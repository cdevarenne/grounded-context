"""CLI for the deterministic path. Zero cloud dependency."""

from __future__ import annotations

import argparse
import json
import sys

from . import telemetry
from .bundle import BundleError
from .es_client import ElasticsearchNotConfigured
from .provenance import AnswerEnvelope, render
from .router import route
from .service import as_of_date, ask, load_bundle, lookup_field


def _emit(envelope: AnswerEnvelope, as_json: bool) -> int:
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


def cmd_telemetry_summary(args: argparse.Namespace) -> int:
    """Aggregate the local log. Reads the source of truth, so it works with no cluster."""
    print(telemetry.summary(telemetry.sink_path(args.log)), end="")
    return 0


def cmd_telemetry_index(args: argparse.Namespace) -> int:
    """Project the local log into Elasticsearch. Imported here so the CLI stays lean."""
    from .telemetry_index import run

    return run(telemetry.sink_path(args.log), index=args.index, recreate=args.recreate)


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


def cmd_eval(args: argparse.Namespace) -> int:
    """Run the eval set, or compare retrieval arms on one query."""
    from .evaluation import compare_arms, run_all

    if args.compare:
        from .evaluation import target_for

        target = target_for(args.compare)
        ranks = compare_arms(args.compare, target=target)
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.compare,
                        "target": f"{target[0]}:chunk:{target[1]}",
                        "ranks": ranks,
                    },
                    indent=2,
                )
            )
            return 0
        print(f"query: {args.compare!r}")
        print(f"target: {target[0]} chunk:{target[1]} — the chunk that defines the term\n")
        for arm, rank in ranks.items():
            print(f"  {arm:8} {'not in top 20' if rank is None else f'rank {rank}'}")
        return 0

    results = run_all(load_bundle(args.bundle), as_of_date(args.as_of))
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        print(f"{'id':5}{'expected':15}{'actual':15}{'route':14}{'cites':7}verdict")
        for r in results:
            # A route the case did not expect is marked inline: the verdict already counts it as
            # a failure, and without the marker the table would look merely different, not wrong.
            route = r.route if r.routed_as_expected else f"{r.route}!={r.case.expected_route}"
            print(
                f"{r.case.id:5}{r.case.expected:15}{r.actual:15}"
                f"{route:14}{r.citations:<7}{r.verdict}"
            )
        for r in results:
            if r.case.known_deviation:
                print(f"\n{r.case.id} KNOWN — {r.case.known_deviation}")
        passed = sum(1 for r in results if r.verdict == "PASS")
        known = sum(1 for r in results if r.verdict == "KNOWN")
        failed = sum(1 for r in results if r.verdict == "FAIL")
        print(f"\n{passed} pass · {known} known deviation · {failed} fail")
    return 1 if any(r.verdict == "FAIL" for r in results) else 0


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

    p = sub.add_parser("telemetry", help="read back what the layer recorded about itself")
    # Nested, because `telemetry index` and `telemetry snapshot` join `summary` here.
    telemetry_sub = p.add_subparsers(dest="telemetry_command", required=True)
    q = telemetry_sub.add_parser("summary", help="aggregate the local log — no cloud needed")
    q.add_argument("--log", metavar="PATH", help="the ndjson log (default: the configured sink)")
    q.set_defaults(func=cmd_telemetry_summary)

    q = telemetry_sub.add_parser("index", help="project the log into Elasticsearch")
    q.add_argument("--log", metavar="PATH", help="the ndjson log (default: the configured sink)")
    q.add_argument("--index", default=telemetry.TELEMETRY_INDEX, help="target index")
    q.add_argument("--recreate", action="store_true", help="delete and rebuild the index first")
    q.set_defaults(func=cmd_telemetry_index)

    p = sub.add_parser("eval", help="run the eval set from docs/specs/eval.md")
    p.add_argument(
        "--compare",
        metavar="QUERY",
        help="instead: rank one query under ELSER, BM25, and hybrid",
    )
    p.set_defaults(func=cmd_eval)

    return parser


def _from_the_cluster(exc: BaseException) -> bool:
    """True when `exc` came from the Elasticsearch client, without importing it to find out.

    The bare install has no `elasticsearch` package, and importing one here would put the `es`
    extra on the critical path of a CLI whose entire claim is that it runs without a cluster. It
    cannot have raised what it cannot import, so the missing case is simply False.
    """
    try:
        from .es_client import transport_errors

        return isinstance(exc, transport_errors())
    except ImportError:
        return False


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand. Every expected failure leaves as a message, not a traceback.

    `gctx eval` reaches the cluster directly rather than through the service layer, so it used to
    exit on a raw `ElasticsearchNotConfigured` stack trace while `gctx telemetry index` printed a
    usable sentence for the identical condition. A CLI that reports one class of missing
    configuration and dumps a traceback for another is telling the reader the second one is a bug
    in the tool.
    """
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BundleError, ValueError, ElasticsearchNotConfigured) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # A refused connection, a rotated key, a 500. `gctx eval` and `gctx telemetry index` reach
        # the cluster directly rather than through the service layer, so this is the only place
        # those can be turned into a sentence. Anything else still raises.
        if not _from_the_cluster(exc):
            raise
        print(f"error: Elasticsearch: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
