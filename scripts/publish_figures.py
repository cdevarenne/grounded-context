"""Write every published figure to `docs/data/measurements.json`, once, with its provenance.

The docs used to carry numbers that were typed. No arithmetic was ever wrong — the scripts have
never produced an incorrect value — but a typed number cannot be re-derived, so it drifts the
moment the index is rebuilt, and nothing notices. Seven pre-reindex figures once sat in
the documents directly beneath the run that refuted them.

So the numbers live here instead, computed once, and the docs reference them by key:

    Off-topic spans <!--fig:probes.tuning.fused.min-->0.0476<!--/--> …

`tests/test_figures.py` resolves every marker against this file, and refuses to let an unmarked
decimal appear inside a guarded region. That makes coverage a property of the markup rather than
of remembering to write a test.

Every value carries the run that produced it. A figure without its index, its chunk count and its
date is not checkable, and this project's whole claim is that its numbers are.

    uv run --extra es python scripts/publish_figures.py [--check]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import measure_findings
import single_call_probe
from grounded_context.es_client import INDEX, client, setting
from grounded_context.semantic import RANK_CONSTANT, RANK_WINDOW_SIZE, RELEVANCE_FLOOR
from measure_findings import IN_DOMAIN, OFF_TOPIC, margin

DATA_FILE = Path(__file__).resolve().parents[1] / "docs" / "data" / "measurements.json"

#: Tuning probes the prose names one at a time rather than as part of a range — each is either a
#: worked example or a declared limit of the floor. Keyed by text so a reworded probe fails loudly
#: instead of silently publishing a different query's score.
NAMED_PROBES = {
    "sourdough": "How do I bake sourdough bread?",
    "streaming": "How do I stream responses from the API?",
    "vitamin_d": "What are the symptoms of vitamin D deficiency?",
    "marathon": "What is the best way to train for a marathon?",
    "wrong_entity": "What is the price per million tokens of GPT-5?",
}


def git_sha() -> str:
    """The commit the measurement ran against, or `unknown` outside a checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=DATA_FILE.parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def run_metadata(es: Any, chunks: int) -> dict[str, Any]:
    """What a reader needs to know before any figure below means anything."""
    return {
        "measured_at": datetime.now(UTC).date().isoformat(),
        "index": INDEX,
        "chunks": chunks,
        "es_version": es.info()["version"]["number"],
        "inference_id": setting("ES_INFERENCE_ID", ".elser-2-elasticsearch"),
        "relevance_floor": RELEVANCE_FLOOR,
        "rank_constant": RANK_CONSTANT,
        "rank_window_size": RANK_WINDOW_SIZE,
        "git_sha": git_sha(),
    }


def _probe(rows: list[dict[str, Any]], query: str) -> dict[str, Any]:
    matches = [r for r in rows if r["query"] == query]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one probe row for {query!r}, found {len(matches)}")
    return matches[0]


def _span(rows: list[dict[str, Any]], kind: str, column: str) -> dict[str, Any]:
    scores = sorted(r[column] for r in rows if r["kind"] == kind)
    return {"n": len(scores), "min": scores[0], "max": scores[-1]}


def tuning_figures(report: dict[str, Any]) -> dict[str, Any]:
    """The finding-3 probe table, as the shapes the prose actually quotes."""
    rows = report["probes"]
    sparse_off = sorted(r["sparse"] for r in rows if r["kind"] == "off-topic")
    return {
        "off_topic": {
            "n": len(OFF_TOPIC),
            "fused": _span(rows, "off-topic", "fused"),
            "sparse": _span(rows, "off-topic", "sparse"),
            # "nine of the ten" — the tenth is the marathon probe, named on its own below.
            "sparse_max_excluding_marathon": sparse_off[-2],
        },
        "in_domain": {
            "n": len(IN_DOMAIN),
            "fused": _span(rows, "in-domain", "fused"),
            "sparse": _span(rows, "in-domain", "sparse"),
        },
        **{
            name: {"sparse": _probe(rows, query)["sparse"], "fused": _probe(rows, query)["fused"]}
            for name, query in NAMED_PROBES.items()
        },
        "sparse": {
            **report["separation"]["sparse"],
            # The figure the prose reaches for when it wants the floor to look good, and the one
            # that caused the only population mismatch this repo has published: it is computed on
            # nine off-topic probes, not ten. Recorded under a name that says so.
            "margin_pct_excluding_marathon": round(
                margin([r["sparse"] for r in rows if r["kind"] == "in-domain"], sparse_off[:-1]), 1
            ),
        },
        "fused": report["separation"]["fused"],
    }


def heldout_figures(report: dict[str, Any]) -> dict[str, Any]:
    held = report["heldout"]
    return {
        "off_topic": {"n": held["counts"]["off_topic"], "sparse_max": held["off_topic_max"]},
        "in_domain": {"n": held["counts"]["in_domain"], "sparse_min": held["in_domain_min"]},
        "false_accepts": len(held["false_accepts"]),
        "false_rejects": len(held["false_rejects"]),
        "auc": held["auc"],
        "margin_pct": held["margin_pct"],
    }


def _slug(config: str) -> str:
    """`linear/none w=0.25` -> `w0_25`; `ELSER raw (shipped)` -> `elser_raw`."""
    if config.startswith("ELSER"):
        return "elser_raw"
    return "w" + config.split("w=")[1].replace(".", "_")


def single_call_figures(report: dict[str, Any]) -> dict[str, Any]:
    normalizers = {
        row["config"].split("/")[-1].strip().split()[0]: row for row in report["normalizers"]
    }
    gate = report["nested_gate"]
    return {
        "ceiling": report["single_arm_ceiling"],
        "normalizers": normalizers,
        "sweep": {_slug(row["config"]): row for row in report["sweep"]},
        # findings.md pools the two probe sets per class; the record keeps them apart.
        "nested_gate": {
            kind.replace("-", "_"): {
                "n": sum(r["n"] for r in gate if r["class"] == kind),
                "refused": sum(r["refused"] for r in gate if r["class"] == kind),
                "distinct_scores": max(r["distinct_scores"] for r in gate if r["class"] == kind),
                "refused_score": next(
                    (r["refused_score"] for r in gate
                     if r["class"] == kind and r["refused_score"] is not None), None
                ),
                "elser_min": min(r["elser_min"] for r in gate if r["class"] == kind),
                "elser_max": max(r["elser_max"] for r in gate if r["class"] == kind),
            }
            for kind in ("off-topic", "in-domain")
        },
    }


def build(es: Any) -> dict[str, Any]:
    """Every figure the docs quote, from one measurement pass."""
    findings = measure_findings.build_report(es)
    single_call = single_call_probe.build_report(es)
    return {
        "run": run_metadata(es, findings["chunks"]),
        "mechanism": findings["mechanism"],
        "subfield": findings["subfield_effect"],
        "invisible": findings["invisible_to_exact"],
        "probes": {"tuning": tuning_figures(findings), "heldout": heldout_figures(findings)},
        "single_call": single_call_figures(single_call),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="re-measure and report whether any figure moved, without writing",
    )
    args = parser.parse_args(argv)

    fresh = build(client())
    if not args.check:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {DATA_FILE.relative_to(DATA_FILE.parents[2])}")
        return 0

    if not DATA_FILE.exists():
        print(f"error: {DATA_FILE} does not exist — run without --check first")
        return 1
    committed = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    # The run block is expected to differ: a re-measurement has a new date and commit.
    moved = [
        key for key in set(committed) | set(fresh)
        if key != "run" and committed.get(key) != fresh.get(key)
    ]
    if moved:
        print("figures moved since the committed measurement: " + ", ".join(sorted(moved)))
        return 1
    print(f"every figure reproduces (committed {committed['run']['measured_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
