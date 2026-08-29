"""Run the commands docs/eval-output.md publishes, and write their output into it.

Every console block in that document is a real run. Getting them there was a manual splice —
run the command, copy the output, paste it into the right fence — done by hand several times and
wrong once, when a header line was dropped on the way in.

This does the splice. Each block is registered below with the commands that produce it; the
output is written to `docs/captures/<name>.txt` and the matching fence in `eval-output.md` is
rewritten from the same text.

Markdown has no include directive, so the capture is stored *and* embedded rather than
referenced. That keeps `eval-output.md` self-contained for a reader — the point of the file is
that you can check a claim without running anything — while `tests/test_captures.py` asserts the
two copies agree, so the duplication cannot drift.

    uv run --extra es python scripts/capture.py [--check] [--only NAME]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_OUTPUT = ROOT / "docs" / "eval-output.md"
CAPTURES_DIR = ROOT / "docs" / "captures"

ANALYZER = '''uv run --extra es python -c "
from grounded_context.es_client import client, INDEX
es = client()
for t in ['rank_constant', 'num_candidates', 'claude-opus-5', 'claude-haiku-4-5']:
    std = [x['token'] for x in es.indices.analyze(index=INDEX, field='content', text=t)['tokens']]
    exa = [x['token'] for x in es.indices.analyze(index=INDEX, field='content.exact', text=t)['tokens']]
    print(f'{t:18} content={str(std):38} content.exact={exa}')
"'''


def compare(term: str) -> list[str]:
    """The two phrasings finding 1 is built on, as one block — they are read against each other."""
    return [
        f'uv run --extra es gctx eval --compare "{term}"',
        f'uv run --extra es gctx eval --compare "What does the {term} parameter do?"',
    ]


@dataclass(frozen=True)
class Capture:
    """One console block: what to run, and the file its output is kept in."""

    name: str
    commands: list[str]

    @property
    def path(self) -> Path:
        return CAPTURES_DIR / f"{self.name}.txt"

    @property
    def key(self) -> str:
        """The line that identifies this block's fence in the document."""
        return "$ " + self.commands[0].splitlines()[0]


CAPTURES: tuple[Capture, ...] = (
    Capture("eval", ["uv run --extra es gctx eval"]),
    Capture("compare-rank-constant", compare("rank_constant")),
    Capture("compare-num-candidates", compare("num_candidates")),
    Capture("compare-ratelimit-header", [
        'uv run --extra es gctx eval --compare "anthropic-ratelimit-tokens-reset"',
        ('uv run --extra es gctx eval --compare '
         '"What does the anthropic-ratelimit-tokens-reset header do?"'),
    ]),
    Capture("compare-rank-window-size", compare("rank_window_size")),
    Capture("analyzer", [ANALYZER]),
    Capture("measure-findings", ["uv run --extra es python scripts/measure_findings.py"]),
    Capture("rrf-audit", ["uv run --extra es python scripts/rrf_audit.py"]),
    Capture("single-call", ["uv run --extra es python scripts/single_call_probe.py"]),
)


def run(capture: Capture) -> str:
    """The block body: each command echoed as a prompt, followed by what it printed."""
    parts = []
    for command in capture.commands:
        # shell=True is what lets a registered command carry its own quoting and, for the
        # analyzer block, span several lines. Every command here is a literal in this file.
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, cwd=ROOT, check=False,
        )
        if result.returncode != 0:
            print(f"error: {capture.name} exited {result.returncode}\n{result.stderr}",
                  file=sys.stderr)
            raise SystemExit(1)
        parts.append(f"$ {command}\n{result.stdout.rstrip()}")
    return "\n\n".join(parts)


def fence(document: str, capture: Capture) -> re.Match[str]:
    """The console block this capture owns, found by the command that opens it."""
    pattern = re.compile(
        r"```console\n" + re.escape(capture.key) + r"(?:\n.*?)?\n```", re.DOTALL
    )
    matches = list(pattern.finditer(document))
    if len(matches) != 1:
        raise SystemExit(
            f"expected one console block starting {capture.key!r} in {EVAL_OUTPUT.name}, "
            f"found {len(matches)}"
        )
    return matches[0]


def splice(document: str, capture: Capture, body: str) -> str:
    """Replace the block's contents, leaving everything around it untouched."""
    match = fence(document, capture)
    return document[:match.start()] + f"```console\n{body}\n```" + document[match.end():]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="re-run and report what moved, without writing")
    parser.add_argument("--only", metavar="NAME",
                        help="regenerate one block; run with no arguments to list the names")
    args = parser.parse_args(argv)

    selected = [c for c in CAPTURES if args.only in (None, c.name)]
    if not selected:
        print(f"unknown capture {args.only!r}. Known: "
              + ", ".join(c.name for c in CAPTURES), file=sys.stderr)
        return 1

    document = EVAL_OUTPUT.read_text(encoding="utf-8")
    moved = []
    for capture in selected:
        body = run(capture)
        if args.check:
            committed = capture.path.read_text(encoding="utf-8") if capture.path.exists() else ""
            if committed.rstrip("\n") != body:
                moved.append(capture.name)
            continue
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        capture.path.write_text(body + "\n", encoding="utf-8")
        document = splice(document, capture, body)
        print(f"captured {capture.name}")

    if args.check:
        if moved:
            print("captures moved: " + ", ".join(moved))
            return 1
        print(f"all {len(selected)} captures reproduce")
        return 0

    EVAL_OUTPUT.write_text(document, encoding="utf-8")
    print(f"spliced {len(selected)} block(s) into {EVAL_OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
