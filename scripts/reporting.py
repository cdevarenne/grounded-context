"""Typed records for the measurements this repo publishes, and how they reach disk.

Two documents used to be one. `docs/eval-output.md` taught a reader how to reproduce a run *and*
recorded what the run produced, by dumping console text into Markdown and reading it back out
with regular expressions. The published rank in `findings.md` §1 was recovered from a line that
read `  elser    rank 5`, so a number in a document depended on the column widths of a terminal.

The repo already retired this pattern once. Commit `d6f8d4f` — "Report the test suite instead of
publishing a count" — stopped the README restating a number pytest states itself. This applies the
same rule to the measurements: the script that produces a figure writes it as data, the document
quotes it through a marker, and no test parses a console line.

What lives here is the *shape* of each record, plus write and read. Reporting is a build concern
rather than a library one, so it sits in `scripts/` beside its publishers rather than inside the
installed package — nothing outside this repo's documentation pipeline would import it.

Stdlib only. `dataclasses` and `json` are enough, and the bare install stays PyYAML.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"

#: A rank of `None` means the chunk fell outside the window. Ordering has to place that *worse*
#: than any real rank, so comparisons stand in a number no result can reach.
UNRANKED = 10**6


def rank_value(rank: int | None) -> int:
    return UNRANKED if rank is None else rank


def git_sha() -> str:
    """The commit a measurement ran against, or `unknown` outside a checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=ROOT,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


@dataclass(frozen=True)
class Run:
    """What a record was measured against. A figure without this is not citable.

    Every published number is true *of an index on a day*. Reindexing moves ELSER scores, and a
    figure that does not say which index produced it cannot be argued with — which is the same as
    not being checkable.
    """

    measured_at: str
    index: str
    chunks: int
    es_version: str
    git_sha: str
    rank_constant: int
    rank_window_size: int

    @classmethod
    def observed(cls, es: Any) -> Self:
        """Read the run's context off the cluster it is about to measure."""
        from grounded_context.es_client import INDEX
        from grounded_context.semantic import RANK_CONSTANT, RANK_WINDOW_SIZE

        return cls(
            measured_at=datetime.now(UTC).date().isoformat(),
            index=INDEX,
            chunks=es.count(index=INDEX)["count"],
            es_version=es.info()["version"]["number"],
            git_sha=git_sha(),
            rank_constant=RANK_CONSTANT,
            rank_window_size=RANK_WINDOW_SIZE,
        )


class Record:
    """Mixin: a frozen dataclass that round-trips through one file under `docs/data/`.

    `of()` is per-record rather than generic. Reconstructing arbitrary nesting from JSON needs
    either a dependency or a type-annotation walker, and both are more machinery than two shapes
    are worth.
    """

    #: File name under `docs/data/`, set by each record.
    filename: str = ""

    @classmethod
    def path(cls) -> Path:
        return DATA_DIR / cls.filename

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")  # type: ignore[call-overload]
        return target

    @classmethod
    def read(cls, path: Path | None = None) -> Self:
        return cls.of(json.loads((path or cls.path()).read_text(encoding="utf-8")))

    @classmethod
    def of(cls, data: dict[str, Any]) -> Self:
        raise NotImplementedError


# --- the retrieval-arm comparison ------------------------------------------------------------


@dataclass(frozen=True)
class ArmRanking:
    """Where one identifier's defining chunk lands under each arm, for one phrasing."""

    identifier: str
    #: `token` is the bare identifier; `sentence` is the same question asked in prose.
    phrasing: str
    query: str
    target_source: str
    target_chunk: int
    elser: int | None
    bm25: int | None
    hybrid: int | None

    @property
    def weaker_arm(self) -> int:
        return max(rank_value(self.elser), rank_value(self.bm25))

    @property
    def stronger_arm(self) -> int:
        return min(rank_value(self.elser), rank_value(self.bm25))

    @property
    def no_worse_than_the_weaker_arm(self) -> bool:
        return rank_value(self.hybrid) <= self.weaker_arm

    @property
    def matches_or_beats_the_stronger_arm(self) -> bool:
        return rank_value(self.hybrid) <= self.stronger_arm


@dataclass(frozen=True)
class ArmReport(Record):
    """`findings.md` §1: four identifiers, two phrasings each, ranked under three arms.

    The two sentences that section argues from — that fusion is *never worse than the weaker arm*,
    and that it matches or beats the stronger one in *six of eight* — were counted by hand off the
    table and guarded by nothing. They are derived here, so a reindex that moves a rank also moves
    the claim, and the claim cannot quietly stop being true.
    """

    filename = "arms.json"

    run: Run
    #: identifier -> phrasing -> ranking. Nested rather than a list so a published cell has a
    #: stable address — `arms.rows.rank_constant.token.hybrid` — that names the question it came
    #: from. Addressing by position is what let a rank measured from the wrong phrasing sit in
    #: the table for three commits: the rate-limit row was regenerated from a query about a
    #: *parameter* where the document asked about a *header*, and position could not tell.
    rows: dict[str, dict[str, ArmRanking]]

    @classmethod
    def of(cls, data: dict[str, Any]) -> Self:
        return cls(
            run=Run(**data["run"]),
            rows={
                identifier: {p: ArmRanking(**r) for p, r in phrasings.items()}
                for identifier, phrasings in data["rows"].items()
            },
        )

    @property
    def lookups(self) -> list[ArmRanking]:
        """Every row, flattened — the eight lookups the section's claims are counted over."""
        return [row for phrasings in self.rows.values() for row in phrasings.values()]

    @property
    def never_worse_than_the_weaker_arm(self) -> bool:
        return all(row.no_worse_than_the_weaker_arm for row in self.lookups)

    @property
    def matches_or_beats_the_stronger_arm(self) -> int:
        return sum(row.matches_or_beats_the_stronger_arm for row in self.lookups)


# --- the fusion-math audit ---------------------------------------------------------------------


@dataclass(frozen=True)
class FusedDocument:
    """One returned document: its rank in each arm, the score RRF should give it, and the one
    Elasticsearch reported."""

    doc: str
    bm25: int | None
    elser: int | None
    predicted: float
    observed: float

    @property
    def delta(self) -> float:
        return abs(self.predicted - self.observed)


@dataclass(frozen=True)
class FusedQuery:
    query: str
    documents: list[FusedDocument]


@dataclass(frozen=True)
class RrfAuditReport(Record):
    """`findings.md` §3 argues from how RRF is *defined*. This checks the definition holds.

    Everywhere else the fused score is read out of Elasticsearch. Here it is computed from the two
    arms' ranks and compared against what came back, so "the score carries rank agreement and
    nothing else" is a measured statement rather than an appeal to the documentation.
    """

    filename = "rrf_audit.json"

    run: Run
    queries: list[FusedQuery]

    @classmethod
    def of(cls, data: dict[str, Any]) -> Self:
        return cls(
            run=Run(**data["run"]),
            queries=[
                FusedQuery(q["query"], [FusedDocument(**d) for d in q["documents"]])
                for q in data["queries"]
            ],
        )

    @property
    def worst_delta(self) -> float:
        """The largest disagreement between the formula and the engine, across every document."""
        return max(
            (document.delta for query in self.queries for document in query.documents),
            default=0.0,
        )
