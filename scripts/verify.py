"""One command that answers: is everything this repo publishes true, against the index, today?

The pieces already exist — every publisher has a `--check` mode, and the
suite guards the documents against the data. What was missing is a single thing to run, so that
knowing the repo is honest does not depend on remembering five commands and what each covers.

Read-only by default. `verify` that rewrites what it is verifying is not a verification, and
before a demo the last thing wanted is a command that quietly edits published documents. Pass
`--update` to regenerate instead, which is the mode for the other side of a reindex.

    uv run --extra es --extra mcp python scripts/verify.py [--update]

Expect roughly ten minutes, nearly all of it Elasticsearch. Each stage re-runs the measurement
behind one record and reports whether the committed copy still reproduces, so a stage that passes
means the documents quoting that record are true of the index as it stands today.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Stage:
    """One check, and the command that regenerates what it checks."""

    name: str
    check: str
    update: str | None = None
    #: Pulled out of the command's own output so the report says what it found, not just OK.
    detail: str = ""


STAGES: tuple[Stage, ...] = (
    Stage("compatibility matrix",
          "uv run python scripts/build_matrix.py --check",
          "uv run python scripts/build_matrix.py"),
    Stage("test suite",
          "uv run --extra es --extra mcp pytest -q",
          detail=r"(\d+ passed[^\n]*)"),
    Stage("published figures",
          "uv run --extra es python scripts/publish_figures.py --check",
          "uv run --extra es python scripts/publish_figures.py"),
    Stage("eval result",
          "uv run --extra es python scripts/publish_eval.py --check",
          "uv run --extra es python scripts/publish_eval.py"),
    Stage("retrieval arms",
          "uv run --extra es python scripts/publish_arms.py --check",
          "uv run --extra es python scripts/publish_arms.py"),
    Stage("fusion audit",
          "uv run --extra es python scripts/rrf_audit.py --check",
          "uv run --extra es python scripts/rrf_audit.py"),
)


def shell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, shell=True, capture_output=True, text=True, cwd=ROOT, check=False,
    )


def provenance() -> list[tuple[str, str]]:
    """What the checks below are being run against. A verdict without this is not citable."""
    import json

    from grounded_context.es_client import INDEX, INFERENCE_ID, client

    es = client()
    dirty = shell("git status --porcelain").stdout.strip()
    sha = shell("git rev-parse --short HEAD").stdout.strip() or "unknown"
    rows = [
        ("index", f"{INDEX} ({es.count(index=INDEX)['count']} chunks)"),
        ("elasticsearch", es.info()["version"]["number"]),
        ("inference", INFERENCE_ID),
        ("commit", f"{sha} ({'uncommitted changes' if dirty else 'clean'})"),
    ]
    for label, name in (("figures", "measurements.json"), ("eval", "eval.json")):
        path = ROOT / "docs" / "data" / name
        measured = json.loads(path.read_text(encoding="utf-8"))["run"]["measured_at"]
        rows.append((label, f"{name} measured {measured}"))
    return rows


def run_stage(stage: Stage, update: bool) -> tuple[bool, str, float]:
    """Run one stage, returning whether it held, what it reported, and how long it took."""
    command = (stage.update or stage.check) if update else stage.check
    started = time.monotonic()
    result = shell(command)
    elapsed = time.monotonic() - started
    output = (result.stdout + result.stderr).strip()
    detail = ""
    if stage.detail:
        found = re.search(stage.detail, output)
        detail = found.group(1) if found else ""
    elif output:
        detail = output.splitlines()[-1]
    return result.returncode == 0, detail, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true",
        help="regenerate everything instead of checking it — use after a reindex, not before a demo",
    )
    args = parser.parse_args(argv)

    print("verify — " + ("regenerating" if args.update else "checking") + " every published claim\n",
          flush=True)
    for label, value in provenance():
        print(f"  {label:<16}{value}", flush=True)

    print(f"\n  running {len(STAGES)} checks; the cluster ones take a few minutes each\n",
          flush=True)
    failed = []
    started = time.monotonic()
    for stage in STAGES:
        held, detail, elapsed = run_stage(stage, args.update)
        mark = "ok  " if held else "FAIL"
        print(f"  {mark} {stage.name:<22}{elapsed:7.1f}s   {detail}", flush=True)
        if not held:
            failed.append(stage.name)
    total = time.monotonic() - started

    print()
    if failed:
        print(f"{len(failed)} of {len(STAGES)} checks failed after {total / 60:.1f} min: "
              + ", ".join(failed))
        print("Re-run with --update to regenerate, then read the diff before committing it.")
        return 1
    verb = "regenerated" if args.update else "verified"
    print(f"all {len(STAGES)} checks {verb} in {total / 60:.1f} min — "
          "every published number holds against this index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
