"""The published diagram is rendered from the source, and nothing runs that renderer in CI.

`docs/architecture.mmd` is the shape of the system. `docs/grounded-context-diagram.png` is a
render of it, and it is the image the README shows. The render needs node and a headless browser,
so CI can detect that the two have parted company but cannot put them back together. This pins
the source the image was generated from and names the command that regenerates it.

The pin is over the *source*, not the PNG bytes. Mermaid renders through a headless browser, so
the same source on a different chromium or a different font set produces a different file. A byte
comparison would fail for reasons that have nothing to do with the diagram, and a test that cries
wolf is one people learn to skip.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "architecture.mmd"
PUBLISHED = ROOT / "docs" / "grounded-context-diagram.png"

#: sha256 of `architecture.mmd` when `grounded-context-diagram.png` was last rendered from it.
#: Update this in the same commit that regenerates the image.
GENERATED_FROM = "10d8e69722fd6c55350a63a163a55a6bf38c0ba4998e083f620fb0505e599a82"

REGENERATE = (
    "npx @mermaid-js/mermaid-cli -i docs/architecture.mmd -c docs/mermaid-render.json "
    "-o docs/grounded-context-diagram.png -w 1800 -s 2 -b white"
)


def source_digest() -> str:
    """sha256 of the diagram source, as `shasum -a 256 docs/architecture.mmd` reports it."""
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def test_the_published_image_was_rendered_from_the_current_source() -> None:
    assert source_digest() == GENERATED_FROM, (
        f"docs/architecture.mmd has changed since {PUBLISHED.name} was rendered from it, so the "
        f"README shows a diagram the source no longer describes.\n  {REGENERATE}\n"
        f"then set GENERATED_FROM to {source_digest()}"
    )


def test_both_halves_of_the_diagram_are_present() -> None:
    """A pin over a missing file would pass while claiming to guard something."""
    assert SOURCE.is_file(), f"{SOURCE} is missing"
    assert PUBLISHED.is_file(), f"{PUBLISHED} is missing"
