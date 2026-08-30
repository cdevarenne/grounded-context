"""Write the retrieval-arm comparison behind `findings.md` §1 to `docs/data/arms.json`.

Eight lookups: four identifiers, each asked as a bare token and as a sentence, each ranked under
ELSER alone, BM25 alone, and the hybrid. The section's whole argument is which arm loses and when,
so every one of those twenty-four ranks is a published number.

They used to reach the document as console text. `gctx eval --compare` printed `  elser    rank 5`,
that line was spliced into `eval-output.md`, and a test recovered the integer with a regular
expression. It worked, and it made a published figure a property of a terminal's column widths.

The two sentences the section argues from were worse off than the table: *never worse than the
weaker arm* and *six of eight* were counted by hand and guarded by nothing at all. Both are
derived from this record now, so a reindex that moves a rank moves the claim with it.

    uv run --extra es python scripts/publish_arms.py [--check]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grounded_context.es_client import client
from grounded_context.evaluation import DEFINING_CHUNKS, compare_arms
from reporting import ArmRanking, ArmReport, Run

#: The sentence form of each identifier, in the order `findings.md` §1 tabulates them. Kept here
#: rather than generated, because the phrasing is the variable under test: `rank_constant` asked
#: as prose is a different retrieval problem from the bare token, and the rate-limit row once
#: regressed for exactly this reason — a regeneration asked about a *parameter* where the document
#: asked about a *header*, and produced a different, plausible, incorrect rank.
SENTENCES: dict[str, str] = {
    "rank_constant": "What does the rank_constant parameter do?",
    "num_candidates": "What does the num_candidates parameter do?",
    "anthropic-ratelimit-tokens-reset":
        "What does the anthropic-ratelimit-tokens-reset header do?",
    "rank_window_size": "What does the rank_window_size parameter do?",
}

assert SENTENCES.keys() == DEFINING_CHUNKS.keys(), (
    "every identifier with a defining chunk needs a sentence phrasing, and vice versa"
)


def build(es: object) -> ArmReport:
    """Rank every identifier's defining chunk, both ways it gets asked."""
    rows: dict[str, dict[str, ArmRanking]] = {}
    for identifier, sentence in SENTENCES.items():
        source, chunk = DEFINING_CHUNKS[identifier]
        rows[identifier] = {}
        for phrasing, query in (("token", identifier), ("sentence", sentence)):
            ranks = compare_arms(query, target=(source, chunk))
            rows[identifier][phrasing] = ArmRanking(
                identifier=identifier,
                phrasing=phrasing,
                query=query,
                target_source=source,
                target_chunk=chunk,
                elser=ranks["elser"],
                bm25=ranks["bm25"],
                hybrid=ranks["hybrid"],
            )
    return ArmReport(run=Run.observed(es), rows=rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="re-measure and report what moved, without writing")
    args = parser.parse_args(argv)

    fresh = build(client())

    if not args.check:
        path = fresh.write()
        print(f"wrote {path.name}: {len(fresh.lookups)} lookups · "
              f"never worse than the weaker arm: {fresh.never_worse_than_the_weaker_arm} · "
              f"matches or beats the stronger arm: "
              f"{fresh.matches_or_beats_the_stronger_arm} of {len(fresh.lookups)}")
        return 0

    if not ArmReport.path().exists():
        print(f"error: {ArmReport.filename} does not exist — run without --check first")
        return 1
    committed = ArmReport.read()
    moved = [
        f"{new.identifier}/{new.phrasing}"
        for old, new in zip(committed.lookups, fresh.lookups, strict=True)
        if (old.elser, old.bm25, old.hybrid) != (new.elser, new.bm25, new.hybrid)
    ]
    if moved:
        print("arm ranks moved since the committed run: " + ", ".join(moved))
        return 1
    print(f"every arm rank reproduces (committed {committed.run.measured_at})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
