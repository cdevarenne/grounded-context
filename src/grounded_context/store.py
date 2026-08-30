"""The port between the lookup engine and the store that holds the canonical facts.

`lookup.py` reads four things from a knowledge store. It reads one concept by id. It reads the
concepts that one concept links to. It iterates every concept. It counts them. It uses nothing
else. This module declares those four as a Protocol.

The seam is therefore a description of the code as it is. It is not a new abstraction. What the
Protocol adds is a name for the boundary and a second implementation that proves the boundary
holds.

The reason to name it is the enterprise case. A production deployment does not keep canonical
facts in Markdown files. It reads them from a system of record such as a CMDB, a wiki or a
relational database. That adapter replaces `Bundle` and changes no other module.

The port is read-only. Loading and validation stay with the adapter that needs them. A Markdown
bundle validates link targets and raises `BundleError`. A relational store uses foreign keys. A
`load()` method in the port would force every adapter to invent one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol, runtime_checkable

from .bundle import Concept


@runtime_checkable
class KnowledgeStore(Protocol):
    """A read-only source of canonical concepts, addressable by id.

    `runtime_checkable` so a test can assert that an adapter satisfies the port. The check tests
    method names only. It does not test signatures.
    """

    def get(self, concept_id: str) -> Concept | None:
        """The concept with this id, or `None` if the store does not hold it."""
        ...

    def linked(self, concept_id: str) -> list[Concept]:
        """The concepts this concept points at. Empty if it points at none."""
        ...

    def __iter__(self) -> Iterator[Concept]:
        ...

    def __len__(self) -> int:
        ...


class InMemoryStore:
    """A store built from concepts in memory, with the links given as ids.

    This is the second implementation, and it is a real one. Every test that needed a store used
    to write Markdown files to a temporary directory and load them. That made a test of the
    lookup engine also a test of the YAML parser and the file system. This store removes both
    from those tests.

    It also answers the question the port exists for. A store that never touches a disk serves
    `lookup`, `service` and the MCP tools without one change to them.
    """

    def __init__(
        self,
        concepts: Iterable[Concept],
        links: dict[str, list[str]] | None = None,
    ) -> None:
        self._concepts = {concept.id: concept for concept in concepts}
        #: Concept id to the ids it links to. A Markdown bundle derives this from its links.
        #: Here the caller states it, because there are no files to resolve a path against.
        self._links = links or {}

    def get(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def linked(self, concept_id: str) -> list[Concept]:
        targets = self._links.get(concept_id, [])
        return [self._concepts[t] for t in targets if t in self._concepts]

    def __iter__(self) -> Iterator[Concept]:
        return iter(self._concepts.values())

    def __len__(self) -> int:
        return len(self._concepts)
