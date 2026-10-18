"""The storage-adapter interface — the only surface downstream code writes to.

``DECISIONS.md`` ("Knowledge store: Spanner Graph is the canonical target,
behind a thin storage-adapter interface with Kùzu as an embedded backend")
locks the direction: Spanner Graph is the production home, Kùzu is the
embedded, credential-free backend, and *nothing* in ingestion, provenance
write or query talks to either directly. This module imports no database
driver and must never do so.

The surface is deliberately the minimum ``ARCHITECTURE.md`` needs — node/edge
upsert with arbitrary declared edge properties, node/edge reads by key
(the edge read so a write can respect what is stored, e.g. a human
``same_as`` verdict), multi-hop traversal, vector
similarity over the same data, and a provenance trace-back read. It is not
"whatever Kùzu happens to expose"; a method that only one backend could
implement is a design smell to flag rather than add.

Two things beyond a bare CRUD surface are here on purpose:

* :meth:`StorageAdapter.initialise_schema` — schema is application-owned
  ("the adapter's migrations", ``ARCHITECTURE.md`` → Knowledge store), not
  provisioned by Terraform, so realising a :class:`~dikms.storage.schema.GraphSchema`
  is part of the adapter contract.
* :meth:`StorageAdapter.prepare_node` / :meth:`~StorageAdapter.prepare_edge`
  — concrete, shared helpers so every backend rejects a write missing its
  provenance identically. Traceability is enforced above the backend, not
  re-implemented (and potentially relaxed) in each one.

Backend implementations subclass :class:`StorageAdapter`; they are selected by
:func:`dikms.storage.factory.create_adapter` from ``storage.backend`` in
``config/extraction.yaml``, never constructed directly by callers.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..config import StorageConfig
from .schema import (
    SOURCE_LABEL,
    EdgeDef,
    GraphSchema,
    NodeDef,
    PropertyType,
    apply_defaults,
    normalise_properties,
    validate_properties,
)


def _complete(definition: NodeDef | EdgeDef, props: dict[str, Any]) -> dict[str, Any]:
    """Every declared property, ``None`` where absent, so an upsert overwrites
    fully. The vector property is left out unless given: it is written
    separately (M2) and must not be cleared by a fact write."""
    for prop in definition.properties:
        if prop.type is not PropertyType.VECTOR:
            props.setdefault(prop.name, None)
    return props


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
    Each step's ``edge`` is recorded as stored (its ``ref.source`` and
    ``ref.target``), whichever way it was followed; ``node`` is the node
    arrived at.
    """

    start: NodeRef
    steps: tuple[PathStep, ...]

    @property
    def end(self) -> NodeRecord | None:
        """The node reached, or ``None`` for a zero-hop path (which
        :meth:`StorageAdapter.traverse` never returns)."""
        return self.steps[-1].node if self.steps else None

    @property
    def hops(self) -> int:
        return len(self.steps)

    def node_refs(self) -> tuple[NodeRef, ...]:
        """Every node on the path, the start included, in order."""
        return (self.start, *(step.node.ref for step in self.steps))

    def is_simple(self) -> bool:
        """True when no node repeats, as :meth:`StorageAdapter.traverse`
        requires of every path it returns."""
        refs = self.node_refs()
        return len(set(refs)) == len(refs)


@dataclass(frozen=True)
class SimilarityHit:
    """One vector-search result. ``score`` is a *similarity*: higher means
    closer (cosine similarity, at most 1.0 for an identical direction)."""

    node: NodeRecord
    score: float


class MissingNodeError(LookupError):
    """An edge write named an endpoint node that is not in the store."""


@dataclass(frozen=True)
class ProvenanceHit:
    """A fact and the source that backs it.

    ``provenance`` is the stored provenance property block (the field names of
    :class:`dikms.models.Provenance`), so a trace-back answers "which
    document, which chunk, which section, when" in one read. ``source`` is
    the ``Source`` node that block names, found differently by subject:

    * **a node** (entity): the ``Source`` at the end of one of its
      ``extracted_from`` edges, one hit per edge, each with that edge's
      provenance block;
    * **an edge** (relation): the ``Source`` whose id is the edge's *own*
      ``source_id`` property, with the edge's own block as ``provenance``.
      A property-graph edge cannot start another edge, in Kùzu or Spanner
      Graph, so a relation has no ``extracted_from`` edge; its provenance
      travels on it as properties instead, and one stored edge is one
      extraction (see :class:`EdgeRef`).

    ``source`` is ``None`` only if the named ``Source`` node is absent.
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
        every extraction of an entity also writes an ``extracted_from`` edge
        from the node to its ``Source``, and those are
        keyed per (source, chunk) — see :class:`EdgeRef` — so each occurrence
        survives and :meth:`trace_back` returns all of them. Callers writing a
        fact must write both; the node alone is not a complete record of where
        the entity came from.

        Overwriting is *full*: the backend writes every key of the dict
        :meth:`prepare_node` returns, ``None`` included, and that dict holds
        every declared property apart from the vector one. So an attribute
        the new extraction does not carry (``nationality`` from source A,
        absent from source B) is cleared rather than left behind under B's
        provenance, which would misattribute it. The one exception is
        ``embedding``: it is computed separately (M2), so it is written only
        when passed, and left as stored otherwise.
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

        A relation's provenance block is on the edge itself; there is no
        ``extracted_from`` edge for a relation, because a property-graph edge
        cannot start from an edge. Its trace-back goes through its own
        ``source_id`` (see :meth:`trace_back`).

        The stored edge is keyed as :meth:`edge_ref` describes — for a
        fact-bearing edge that includes ``source_id`` and ``chunk_id``, so
        re-extracting the same relation from another source or chunk *adds* an
        edge rather than overwriting the earlier one's provenance. Re-running
        extraction over the same chunk updates that one edge in place, which is
        what makes ingestion idempotent. As with nodes, the update is full:
        :meth:`prepare_edge` emits every declared property, ``None`` where
        absent, and the backend writes all of them.

        Both endpoint nodes must already exist; if either is missing the
        backend raises :class:`MissingNodeError` and writes nothing, rather
        than silently creating no edge (Kùzu's ``MATCH ... MERGE``) or a
        dangling one. :meth:`require_endpoints` does that check.

        Returns the key of the edge written. A backend stores the edge from
        the returned ``ref.source`` to ``ref.target``, not from the arguments
        as passed: for ``same_as`` the key puts the endpoints in canonical
        order, so writing B->A updates the stored A->B edge.

        Because the update is full, a ``same_as`` write that omits
        ``review_status`` stores the default ``unreviewed`` over whatever
        verdict a human recorded. Code proposing matches therefore writes
        ``same_as`` through :func:`dikms.storage.write_same_as`, which reads
        the stored edge first and keeps a human verdict; ``upsert_edge``
        itself stays a plain, unconditional write.
        """

    # -- reads ----------------------------------------------------------

    @abstractmethod
    def get_node(self, ref: NodeRef) -> NodeRecord | None:
        """Fetch one node by label and id, or ``None`` if absent."""

    @abstractmethod
    def get_edge(self, ref: EdgeRef) -> EdgeRecord | None:
        """Fetch one stored edge by its key, or ``None`` if absent.

        The edge counterpart of :meth:`get_node`, and the read that lets a
        write depend on what is already stored (:func:`dikms.storage.write_same_as`
        keeps a human ``same_as`` verdict this way). Semantics every backend
        must share:

        * **Resolve the ref first.** A backend calls
          :meth:`canonical_edge_ref` on ``ref`` and looks up the key it
          returns, never ``ref`` as passed. For ``same_as`` that puts the
          endpoints in canonical order, exactly as :meth:`edge_ref` does on
          write, so ``same_as(B, A)`` finds the stored ``same_as(A, B)``.
          The same call rejects a ref that names no single stored edge
          (``ValueError``) or an unknown edge type (``KeyError``) before the
          backend queries anything; see :meth:`canonical_edge_ref`.
        * **One edge or none.** ``ref`` names exactly one key: for
          ``same_as`` the pair, for a fact-bearing edge one extraction (an
          occurrence ref, :meth:`EdgeRef.is_occurrence`). Every stored
          extraction of a relation is a :meth:`trace_back` or
          :meth:`traverse` question, not this one.
        * **The record is keyed as stored.** The returned
          :class:`EdgeRecord`'s ``ref`` is the canonical key (what
          :meth:`upsert_edge` returned when the edge was written), not
          ``ref`` as passed. Its ``properties`` hold every declared property
          of the edge type as stored, ``None`` where the last write had no
          value (the full-overwrite rule in :meth:`upsert_edge`).
        * **Absent means ``None``.** A permitted key with no stored edge,
          including one whose endpoint nodes do not exist, returns ``None``
          rather than raising.
        """

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

        Returns every **simple path** of 1 to ``max_hops`` hops from
        ``start``: no node repeats on a path, ``start`` included
        (:meth:`TraversalResult.is_simple`). Semantics every backend must
        share:

        * **No zero-hop result.** ``start`` itself is never returned as a
          path; :meth:`get_node` reads it.
        * **Parallel edges stay distinct.** Fact-bearing edges are stored
          once per extraction (:class:`EdgeRef`), so two sources supporting
          ``develops(BAE -> ASGARD)`` give two one-hop paths, one per edge,
          each traceable to its own source. Deduplicating them is a
          retrieval decision (M3), not the store's.
        * ``edge_types`` filters every hop; ``target_labels`` filters the
          path's end node only (intermediate nodes may have any label).
          ``None`` means "any". Unknown types or labels raise ``KeyError``.
        * ``direction`` applies to every hop; with ``BOTH`` an edge may be
          followed either way. Each step records the edge as stored.
        * Paths are ordered by hop count, shortest first (order within one
          hop count is unspecified), and ``limit`` keeps the first ``limit``
          of them. ``max_hops`` must be at least 1 and ``limit``, if given,
          at least 1; :meth:`check_traversal_args` checks all of this.

        Note that ``same_as`` is just another edge type here: identity is
        *not* resolved implicitly, and no backend may collapse
        ``same_as``-linked nodes — traversing identity is a query-time
        decision made per edge (``DECISIONS.md``: identity evaluated
        per-edge, not transitive closure).
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

        ``embedding`` must be ``self.schema.vector_dimension`` wide and finite
        (:meth:`check_query_embedding`). In M1 this is only ever exercised
        with stub vectors — real Vertex embeddings need credentials and are
        an M2 concern (``DECISIONS.md``: embeddings).

        ``SimilarityHit.score`` is the **cosine similarity**, so higher is
        closer. A backend whose search reports cosine *distance* (Spanner's
        vector index) returns ``1 - distance``; the Kùzu backend computes the
        similarity directly with an exact scan. ``min_score`` is an inclusive lower
        bound on that similarity (hits with ``score >= min_score`` are kept).
        Hits are ordered by score, highest first, at most ``limit`` of them;
        nodes with no embedding are never returned.
        :meth:`rank_similarity_hits` applies the bound, the order and the
        limit, so backends share them.
        """

    @abstractmethod
    def trace_back(self, subject: NodeRef | EdgeRef) -> list[ProvenanceHit]:
        """Return every provenance record backing a node or edge.

        A list, not a single record: the same fact may be supported by several
        sources, and ``ARCHITECTURE.md`` requires those to be rankable by
        significance rather than collapsed.

        For a node, one hit per ``extracted_from`` edge. For an edge, the
        relation traces back through its own ``source_id``/``chunk_id``
        properties, not an ``extracted_from`` edge (an edge cannot start an
        edge): an occurrence ref (:meth:`EdgeRef.is_occurrence`) gives that
        one extraction's hit, and a generic ref (no ``source_id``/``chunk_id``)
        gives one hit per stored extraction of that relation between those
        endpoints. See :class:`ProvenanceHit`.
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
        return _complete(definition, props)

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
        return self.edge_ref(edge_type, source, target, props), _complete(
            definition, props
        )

    def require_endpoints(self, source: NodeRef, target: NodeRef) -> None:
        """Raise :class:`MissingNodeError` unless both endpoints are stored.

        The default reads each with :meth:`get_node`; a backend may override
        it with a cheaper check, or do the check atomically with the write,
        so long as a missing endpoint still raises before anything is written.
        """
        for endpoint in (source, target):
            if self.get_node(endpoint) is None:
                raise MissingNodeError(
                    f"{endpoint.label} {endpoint.id!r} does not exist; write the "
                    f"node before an edge that touches it"
                )

    # -- shared read helpers --------------------------------------------

    def check_query_embedding(self, label: str, embedding: Sequence[float]) -> None:
        """Check a :meth:`similar_nodes` query before it reaches the index:
        the label has a vector property, and the query vector is that wide
        and finite."""
        prop = self.schema.node(label).vector_property()
        if prop is None:
            raise ValueError(f"{label} has no vector property to search")
        if len(embedding) != prop.dimension:
            raise ValueError(
                f"query embedding has {len(embedding)} dimensions, expected "
                f"{prop.dimension}"
            )
        if not all(
            isinstance(x, (int, float))
            and not isinstance(x, bool)
            and math.isfinite(x)
            for x in embedding
        ):
            raise ValueError("query embedding must be finite numbers")

    @staticmethod
    def rank_similarity_hits(
        hits: Iterable[SimilarityHit],
        *,
        limit: int,
        min_score: float | None = None,
    ) -> list[SimilarityHit]:
        """Apply :meth:`similar_nodes`' shared result rules to raw hits:
        drop any below ``min_score`` (inclusive lower bound on similarity),
        order by score highest first, and keep at most ``limit``."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        kept = [h for h in hits if min_score is None or h.score >= min_score]
        kept.sort(key=lambda h: h.score, reverse=True)
        return kept[:limit]

    def check_traversal_args(
        self,
        *,
        edge_types: Sequence[str] | None,
        max_hops: int,
        target_labels: Sequence[str] | None,
        limit: int | None,
    ) -> None:
        """Validate :meth:`traverse` arguments the same way in every backend."""
        if max_hops < 1:
            raise ValueError(f"max_hops must be at least 1, got {max_hops}")
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        for edge_type in edge_types or ():
            self.schema.edge(edge_type)  # KeyError naming the unknown type
        for label in target_labels or ():
            self.schema.node(label)  # KeyError naming the unknown label

    def canonical_edge_ref(self, ref: EdgeRef) -> EdgeRef:
        """The stored key a :meth:`get_edge` lookup of ``ref`` reads.

        Resolves ``ref`` through :meth:`edge_ref`, so a read and a write of
        the same edge agree on its key by construction: for a symmetric type
        (``same_as``) the endpoints come back in canonical (label, id)
        order, whichever way round ``ref`` names them.

        Raises ``KeyError`` for an unknown edge type, and ``ValueError`` when
        ``ref`` cannot name one stored edge:

        * an empty endpoint id, or an endpoint pair the relation does not
          permit (so a backend never queries a pair it has no table or edge
          definition for);
        * ``source_id`` or ``chunk_id`` on an edge type that has no
          provenance block (``same_as``), whose key is the pair alone;
        * a fact-bearing edge type without *both* ``source_id`` and
          ``chunk_id``, since such a ref may match several stored
          extractions.
        """
        definition = self.schema.edge(ref.type)
        _require_id(ref.source.label, ref.source.id)
        _require_id(ref.target.label, ref.target.id)
        if (ref.source.label, ref.target.label) not in definition.pairs():
            raise ValueError(
                f"{ref.type}: {ref.source.label} -> {ref.target.label} is not a "
                f"permitted endpoint pair for this relation"
            )
        if definition.property("source_id") is None:
            if ref.source_id is not None or ref.chunk_id is not None:
                raise ValueError(
                    f"{ref.type} is keyed on its endpoints alone; a ref with "
                    f"source_id/chunk_id names no stored edge"
                )
        elif not ref.is_occurrence():
            raise ValueError(
                f"{ref.type} is stored once per extraction; get_edge needs both "
                f"source_id and chunk_id (use trace_back for every extraction)"
            )
        return self.edge_ref(
            ref.type,
            ref.source,
            ref.target,
            {"source_id": ref.source_id, "chunk_id": ref.chunk_id},
        )

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
