"""The console blocks in eval-output.md must match the capture files they came from.

`scripts/capture.py` writes each block to `docs/captures/<name>.txt` and splices the same text
into `eval-output.md`. Storing it twice is deliberate — the document stays readable on its own,
which is the whole point of a file whose job is letting someone check a claim without running
anything — but two copies of a thing can disagree, so this is what stops them.

What this cannot check is whether the captures are still *true*: that needs the cluster, and it
is `scripts/capture.py --check`. The split is the same one the figures and the eval use. This
half runs on a bare clone and catches an edited document; the cluster-gated half catches a
document that faithfully shows a run which no longer reproduces.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capture import CAPTURES, EVAL_OUTPUT, fence

DOCUMENT = EVAL_OUTPUT.read_text(encoding="utf-8")


@pytest.mark.parametrize("capture", CAPTURES, ids=lambda c: c.name)
def test_every_capture_file_matches_the_block_it_was_spliced_into(capture) -> None:
    assert capture.path.exists(), (
        f"{capture.path.name} is missing — run scripts/capture.py --only {capture.name}"
    )
    published = fence(DOCUMENT, capture).group(0)
    expected = f"```console\n{capture.path.read_text(encoding='utf-8').rstrip()}\n```"
    assert published == expected, (
        f"the {capture.name} block in {EVAL_OUTPUT.name} differs from "
        f"docs/captures/{capture.path.name}"
    )


def test_every_console_block_in_the_document_is_owned_by_a_capture() -> None:
    """An unregistered block is one nothing regenerates, which is how a stale one survives."""
    owned = sum(len(fence(DOCUMENT, capture).group(0).split("```console")) - 1
                for capture in CAPTURES)
    assert DOCUMENT.count("```console") == owned, (
        "eval-output.md has a console block that scripts/capture.py does not know about"
    )
