"""An in-memory :class:`~dikms.storage.StorageAdapter` for tests.

Not a backend. It exercises the shared, non-abstract behaviour of the adapter
base class (write validation, endpoint rules, edge keying) without a
database, and it is the reference model the Kùzu backend's stored state is
compared against in ``test_storage_kuzu.py``.
"""

from __future__ import annotations

from typing import Any

from dikms.storage import (
    Direction,
    EdgeRecord,
    EdgeRef,
    GraphSchema,
    NodeRecord,
    NodeRef,
    ProvenanceHit,
    StorageAdapter,
    TraversalResult,
)


class StubAdapter(StorageAdapter):
    """Minimal concrete adapter: records writes, answers nothing clever.

    Writes merge into what is stored, as Kùzu's ``MERGE ... SET`` and
    Spanner's ``insert_or_update`` do: a key the prepared dict leaves out
    keeps its old value. That makes the full-overwrite rule observable.
    Endpoint existence is checked only with ``require_existing_endpoints``,
    so the many tests of edge validation need not create nodes first.
    """

    def __init__(
        self, schema: GraphSchema, *, require_existing_endpoints: bool = False
    ) -> None:
        super().__init__(schema)
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[EdgeRef, dict[str, Any]] = {}
        self.initialised = False
        self.require_existing_endpoints = require_existing_endpoints

    def initialise_schema(self) -> None:
        self.initialised = True

    def upsert_node(self, label, node_id, properties):
        props = self.prepare_node(label, node_id, properties)
        self.nodes.setdefault((label, node_id), {}).update(props)
        return NodeRef(label, node_id)

    def upsert_edge(self, edge_type, source, target, properties):
        ref, props = self.prepare_edge(edge_type, source, target, properties)
        if self.require_existing_endpoints:
            self.require_endpoints(ref.source, ref.target)
        self.edges.setdefault(ref, {}).update(props)  # keyed as the schema says
        return ref

    def get_node(self, ref):
        props = self.nodes.get((ref.label, ref.id))
        return None if props is None else NodeRecord(ref, props)

    def get_edge(self, ref):
        key = self.canonical_edge_ref(ref)  # as the contract requires
        props = self.edges.get(key)
        return None if props is None else EdgeRecord(key, dict(props))

    def traverse(
        self,
        start,
        *,
        edge_types=None,
        max_hops=1,
        direction=Direction.BOTH,
        target_labels=None,
        limit=None,
    ) -> list[TraversalResult]:
        return []

    def similar_nodes(self, label, embedding, *, limit=10, min_score=None):
        return []

    def trace_back(self, subject) -> list[ProvenanceHit]:
        return []
