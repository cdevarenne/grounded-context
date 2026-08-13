"""Generate the compatibility matrix from the model files.

The bundle is the source of truth and this is a rendered view of it — never the reverse. A
test regenerates the file and fails on any difference, so a model file edited without
regenerating is caught rather than left to rot.

Output is deterministic on purpose: no generation timestamp, only the bundle's own OKF
verification dates. A clock in the output would make every run look like a change.

    uv run python scripts/build_matrix.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grounded_context.bundle import Bundle, Concept
from grounded_context.service import format_value, load_bundle

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "compatibility-matrix.md"
MISSING = "—"

# Ordered; fields absent from every model are dropped rather than shown as empty rows.
FIELDS: tuple[tuple[str, str], ...] = (
    ("model_string", "Model string (pinned)"),
    ("api_alias", "API alias"),
    ("context_window_tokens", "Context window (tokens)"),
    ("max_output_tokens", "Max output (tokens)"),
    ("max_output_tokens_batch_api", "Max output, Batch API"),
    ("vision", "Vision"),
    ("adaptive_thinking", "Adaptive thinking"),
    ("extended_thinking", "Extended thinking"),
    ("input_price_per_mtok_usd", "Input $/Mtok"),
    ("output_price_per_mtok_usd", "Output $/Mtok"),
    ("introductory_input_price_per_mtok_usd", "Introductory input $/Mtok"),
    ("introductory_output_price_per_mtok_usd", "Introductory output $/Mtok"),
    ("introductory_pricing_ends", "Introductory pricing ends"),
    ("default_endpoint", "Default endpoint"),
)


def models(bundle: Bundle) -> list[Concept]:
    """Every model concept, in stable id order."""
    return sorted((c for c in bundle if c.type == "model"), key=lambda c: c.id)


def _cell(concept: Concept, field: str) -> str:
    if field not in concept.canonical:
        return MISSING
    return format_value(concept.canonical[field])


def render(bundle: Bundle) -> str:
    """Build the Markdown view: one row per canonical field, one column per model."""
    entries = models(bundle)
    if not entries:
        raise SystemExit("no concepts of type 'model' in the bundle")

    titles = [concept.title for concept in entries]
    lines = [
        "# Compatibility matrix",
        "",
        "**Generated — do not edit.** Rendered from the model files in [`knowledge/`](../knowledge/)",
        "by `scripts/build_matrix.py`. The model files are the source of truth; if this table and",
        "a model file ever disagree, the model file wins. Regenerate with:",
        "",
        "```bash",
        "uv run python scripts/build_matrix.py",
        "```",
        "",
        "| Field | " + " | ".join(titles) + " |",
        "|---|" + "---|" * len(entries),
    ]

    for field, label in FIELDS:
        if not any(field in concept.canonical for concept in entries):
            continue
        cells = [_cell(concept, field) for concept in entries]
        lines.append(f"| `{field}`<br/>{label} | " + " | ".join(cells) + " |")

    lines += ["", "## Provenance", "", "| Model | Trust tier | Verified | Stale after | Source |", "|---|---|---|---|---|"]
    for concept in entries:
        verified = (concept.verified_at or MISSING)[:10]
        stale = concept.stale_after.isoformat() if concept.stale_after else MISSING
        source = f"[link]({concept.source_url})" if concept.source_url else MISSING
        lines.append(
            f"| `{concept.id}` | {concept.trust_tier} | {verified} | {stale} | {source} |"
        )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed file is out of date",
    )
    args = parser.parse_args(argv)

    expected = render(load_bundle())
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != expected:
            print(f"error: {OUTPUT.name} is stale — regenerate it", file=sys.stderr)
            return 1
        print(f"ok: {OUTPUT.name} matches the bundle")
        return 0

    OUTPUT.write_text(expected, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
