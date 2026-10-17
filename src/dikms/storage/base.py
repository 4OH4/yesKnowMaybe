"""The storage-adapter interface — the only surface downstream code writes to.

``DECISIONS.md`` ("Knowledge store: Spanner Graph is the canonical target,
behind a thin storage-adapter interface with Kùzu as an embedded backend")
locks the direction: Spanner Graph is the production home, Kùzu is the
embedded, credential-free backend, and *nothing* in ingestion, provenance
write or query talks to either directly. This module imports no database
driver and must never do so.

The surface is deliberately the minimum ``ARCHITECTURE.md`` needs — node/edge
upsert with arbitrary declared edge properties, multi-hop traversal, vector
similarity over the same data, and a provenance trace-back read. It is not
"whatever Kùzu happens to expose"; a method that only one backend could
implement is a design smell to flag rather than add.

Two things beyond a bare CRUD surface are here on purpose:

* :meth:`StorageAdapter.initialise_schema` — schema is application-owned
  ("the adapter's migrations", ``ARCHITECTURE.md`` → Knowledge store), not
  provisioned by Terraform, so realising a :class:`~dikms.storage.schema.GraphSchema`
  is part of the adapter contract.
* :meth:`StorageAdapter.validate_node` / :meth:`~StorageAdapter.validate_edge`
  — concrete, shared helpers so every backend rejects a write missing its
  provenance identically. Traceability is enforced above the backend, not
  re-implemented (and potentially relaxed) in each one.

Backend implementations subclass :class:`StorageAdapter`; they are selected by
:func:`dikms.storage.factory.create_adapter` from ``storage.backend`` in
``config/extraction.yaml``, never constructed directly by callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..config import StorageConfig
from .schema import (
    SOURCE_LABEL,
    EdgeDef,
    GraphSchema,
    NodeDef,
    apply_defaults,
    normalise_properties,
    validate_properties,
)


def _require_id(label: str, node_id: object) -> None:
    """A node id names the node a fact is about; an empty one names nothing."""
    if not isinstance(node_id, str) or not node_id.strip():
        raise ValueError(
            f"{label}: node id must be a non-empty string, got {node_id!r}"
        )


class Direction(StrEnum):
    """Traversal direction. Both backends can follow an edge either way."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


@dataclass(frozen=True)
class NodeRef:
    """Identifies one node: its label plus its primary key."""

    label: str
    id: str


@dataclass(frozen=True)
class EdgeRef:
    """Identifies one stored edge. There is no portable internal edge id, so
    the key is the relation, its endpoints, and — for fact-bearing edges — the
    provenance that produced it.

    ``source_id``/``chunk_id`` are part of the key, not decoration: without
    them, re-extracting ``develops(BAE -> ASGARD)`` from a second document (or
    a second chunk of the same document, which ``ARCHITECTURE.md`` calls a
    routine case) would *overwrite* the first extraction's supporting quote and
    timestamp. Keeping them distinct is what makes several sources supporting
    one fact — and ranking those by significance — possible at all.

    Both are ``None`` for edges that are not extracted from a document
    (``same_as``, which records one identity claim per pair) and when naming a
    relation generically, e.g. asking :meth:`StorageAdapter.trace_back` for
    every occurrence of one relation.
    """

    type: str
    source: NodeRef
    target: NodeRef
    source_id: str | None = None
    chunk_id: str | None = None

    def is_occurrence(self) -> bool:
        """True when this ref names one specific extraction, not a relation."""
        return self.source_id is not None and self.chunk_id is not None


@dataclass(frozen=True)
class NodeRecord:
    ref: NodeRef
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeRecord:
    ref: EdgeRef
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PathStep:
    """One hop: the edge traversed and the node arrived at."""

    edge: EdgeRecord
    node: NodeRecord


@dataclass(frozen=True)
class TraversalResult:
    """A path from the start node to a reachable node.

    ``steps`` is the ordered sequence of hops, so a multi-hop answer can be
    explained (and every edge on it traced back) rather than just asserted.
    """

    start: NodeRef
    steps: tuple[PathStep, ...]

    @property
    def end(self) -> NodeRecord | None:
        """The node reached, or ``None`` for a zero-hop result."""
        return self.steps[-1].node if self.steps else None

    @property
    def hops(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class SimilarityHit:
    node: NodeRecord
    score: float


@dataclass(frozen=True)
class ProvenanceHit:
    """A fact and the source that backs it.

    ``provenance`` is the stored provenance property block (the field names of
    :class:`dikms.models.Provenance`); ``source`` is the ``Source`` node the
    fact's ``extracted_from`` edge points at, so a trace-back answers "which
    document, which chunk, which section, when" in one read.
    """

    subject: NodeRef | EdgeRef
    provenance: Mapping[str, Any]
    source: NodeRecord | None = None


class StorageAdapter(ABC):
    """Backend-agnostic knowledge-store contract.

    Implementations hold a :class:`~dikms.storage.schema.GraphSchema` and
    realise it in their own DDL.
    """

    def __init__(
        self, schema: GraphSchema, config: StorageConfig | None = None
    ) -> None:
        #: The schema this adapter realises and validates writes against.
        self.schema = schema
        #: Backend settings from ``storage`` in config/extraction.yaml. Each
        #: backend reads only its own sub-block (``kuzu`` / ``spanner``).
        self.config = config

    # -- schema ---------------------------------------------------------

    @abstractmethod
    def initialise_schema(self) -> None:
        """Create (or bring up to date) the store's tables/labels/indexes so
        they match ``self.schema``. Must be idempotent."""

    # -- writes ---------------------------------------------------------

    @abstractmethod
    def upsert_node(
        self,
        label: str,
        node_id: str,
        properties: Mapping[str, Any],
    ) -> NodeRef:
        """Insert or update one node.

        ``properties`` must satisfy the schema — including the mandatory
        provenance block on fact-bearing labels.

        **Upsert overwrites the node.** A node carries one provenance block,
        recording the most recent extraction of that entity (``DECISIONS.md``
        defers attribute-level provenance). Earlier extractions are not lost:
        every extraction also writes an ``extracted_from`` edge, and those are
        keyed per (source, chunk) — see :class:`EdgeRef` — so each occurrence
        survives and :meth:`trace_back` returns all of them. Callers writing a
        fact must write both; the node alone is not a complete record of where
        the entity came from.
        """

    @abstractmethod
    def upsert_edge(
        self,
        edge_type: str,
        source: NodeRef,
        target: NodeRef,
        properties: Mapping[str, Any],
    ) -> EdgeRef:
        """Insert or update one edge, carrying arbitrary declared properties
        (confidence, provenance, timestamps) without contortion.

        The stored edge is keyed as :meth:`edge_ref` describes — for a
        fact-bearing edge that includes ``source_id`` and ``chunk_id``, so
        re-extracting the same relation from another source or chunk *adds* an
        edge rather than overwriting the earlier one's provenance. Re-running
        extraction over the same chunk updates that one edge in place, which is
        what makes ingestion idempotent.

        Returns the key of the edge written. A backend stores the edge from
        the returned ``ref.source`` to ``ref.target``, not from the arguments
        as passed: for ``same_as`` the key puts the endpoints in canonical
        order, so writing B->A updates the stored A->B edge.
        """

    # -- reads ----------------------------------------------------------

    @abstractmethod
    def get_node(self, ref: NodeRef) -> NodeRecord | None:
        """Fetch one node by label and id, or ``None`` if absent."""

    @abstractmethod
    def traverse(
        self,
        start: NodeRef,
        *,
        edge_types: Sequence[str] | None = None,
        max_hops: int = 1,
        direction: Direction = Direction.BOTH,
        target_labels: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[TraversalResult]:
        """Multi-hop traversal from ``start``.

        ``edge_types``/``target_labels`` of ``None`` mean "any". Note that
        ``same_as`` is just another edge type here: identity is *not* resolved
        implicitly, and no backend may collapse ``same_as``-linked nodes —
        traversing identity is a query-time decision made per edge
        (``DECISIONS.md``: identity evaluated per-edge, not transitive closure).
        """

    @abstractmethod
    def similar_nodes(
        self,
        label: str,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SimilarityHit]:
        """Vector similarity search over ``label``'s embedding property.

        ``embedding`` must be ``self.schema.vector_dimension`` wide. In M1 this
        is only ever exercised with stub vectors — real Vertex embeddings need
        credentials and are an M2 concern (``DECISIONS.md``: embeddings).
        """

    @abstractmethod
    def trace_back(self, subject: NodeRef | EdgeRef) -> list[ProvenanceHit]:
        """Return every provenance record backing a node or edge.

        A list, not a single record: the same fact may be supported by several
        sources, and ``ARCHITECTURE.md`` requires those to be rankable by
        significance rather than collapsed.
        """

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:  # noqa: B027 - optional hook, not every backend has state
        """Release backend resources.

        Deliberately concrete and empty: an embedded backend holds a connection
        to close, a remote one may not, and forcing every implementation to
        write a no-op adds nothing.
        """
        return None

    def __enter__(self) -> StorageAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- shared write preparation ---------------------------------------
    #
    # Concrete on purpose. Every backend routes its writes through these, so
    # provenance enforcement, defaults and edge keying are identical whichever
    # store is configured — and in Kùzu, which has no NOT NULL, this is the
    # *only* thing standing behind "provenance is mandatory".

    def node_def(self, label: str) -> NodeDef:
        return self.schema.node(label)

    def edge_def(self, edge_type: str) -> EdgeDef:
        return self.schema.edge(edge_type)

    def prepare_node(
        self, label: str, node_id: str, properties: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate a node write and return the properties to store.

        Fills declared defaults, checks the primary-key property agrees with
        ``node_id``, and rejects anything the schema does not allow — so "a
        fact cannot be stored without its source, chunk and section" holds by
        construction rather than by convention.
        """
        definition = self.schema.node(label)
        _require_id(label, node_id)
        props = normalise_properties(
            definition, apply_defaults(definition, properties)
        )
        key = props.get(definition.primary_key)
        if key is not None and key != node_id:
            raise ValueError(
                f"{label}: {definition.primary_key}={key!r} does not match the "
                f"node id {node_id!r} being written"
            )
        props[definition.primary_key] = node_id
        validate_properties(definition, props)
        return props

    def prepare_edge(
        self,
        edge_type: str,
        source: NodeRef,
        target: NodeRef,
        properties: Mapping[str, Any],
    ) -> tuple[EdgeRef, dict[str, Any]]:
        """Validate an edge write; return its key and the properties to store."""
        definition = self.schema.edge(edge_type)
        _require_id(source.label, source.id)
        _require_id(target.label, target.id)
        if (source.label, target.label) not in definition.pairs():
            raise ValueError(
                f"{edge_type}: {source.label} -> {target.label} is not a permitted "
                f"endpoint pair for this relation (see config/ontology.yaml)"
            )
        if definition.same_label_only and source == target:
            # `same_as` links two *distinct* nodes; a self-edge would be the
            # start of treating identity as a property of one node rather than
            # a reviewable claim about a pair (CLAUDE.md constraint 2).
            raise ValueError(f"{edge_type}: cannot link {source.id} to itself")
        props = normalise_properties(
            definition, apply_defaults(definition, properties)
        )
        validate_properties(definition, props)
        if definition.property("source_id") is not None:
            # An edge touching a Source node (``extracted_from``, ``mentions``)
            # must name that same document in its provenance; otherwise a
            # trace-back would answer with two different documents.
            for endpoint in (source, target):
                if endpoint.label == SOURCE_LABEL and props["source_id"] != endpoint.id:
                    raise ValueError(
                        f"{edge_type}: source_id={props['source_id']!r} does not "
                        f"match the Source endpoint {endpoint.id!r}"
                    )
        return self.edge_ref(edge_type, source, target, props), props

    def edge_ref(
        self,
        edge_type: str,
        source: NodeRef,
        target: NodeRef,
        properties: Mapping[str, Any],
    ) -> EdgeRef:
        """The key one edge is stored under.

        A fact-bearing edge is keyed per extraction — (relation, endpoints,
        ``source_id``, ``chunk_id``) — so a second source, or a second chunk of
        the same source, adds an edge instead of overwriting the first one's
        provenance. Edges with no provenance block (``same_as``) are keyed on
        the relation and its endpoints alone: one identity claim per pair.

        For a symmetric edge type (:attr:`EdgeDef.symmetric`, i.e. ``same_as``)
        the endpoints are put in canonical order, sorted by (label, id), so
        ``same_as(A, B)`` and ``same_as(B, A)`` are one key and one stored
        edge. Otherwise a pair rejected one way round could be proposed again
        the other way, holding two contradictory review states.
        """
        definition = self.schema.edge(edge_type)
        if definition.symmetric:
            source, target = sorted((source, target), key=lambda n: (n.label, n.id))
        if definition.property("source_id") is None:
            return EdgeRef(edge_type, source, target)
        return EdgeRef(
            edge_type,
            source,
            target,
            source_id=properties.get("source_id"),
            chunk_id=properties.get("chunk_id"),
        )
