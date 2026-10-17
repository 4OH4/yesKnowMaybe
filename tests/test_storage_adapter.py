"""Behavioural tests for the adapter base class, via an in-memory stub.

The stub is not a backend — it exists so the shared, non-abstract behaviour in
:class:`dikms.storage.StorageAdapter` (write validation, endpoint rules,
traversal result helpers) is exercised without a database. Real backends are
separate tickets.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from dikms.models import Provenance
from dikms.storage import (
    Direction,
    EdgeRecord,
    EdgeRef,
    GraphSchema,
    NodeRecord,
    NodeRef,
    PathStep,
    ProvenanceHit,
    SchemaViolation,
    SimilarityHit,
    StorageAdapter,
    TraversalResult,
    provenance_properties,
)
from dikms.storage.schema import SOURCE_LABEL


class StubAdapter(StorageAdapter):
    """Minimal concrete adapter: records writes, answers nothing clever."""

    def __init__(self, schema: GraphSchema) -> None:
        super().__init__(schema)
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[EdgeRef, dict[str, Any]] = {}
        self.initialised = False

    def initialise_schema(self) -> None:
        self.initialised = True

    def upsert_node(self, label, node_id, properties):
        props = self.prepare_node(label, node_id, properties)
        self.nodes[(label, node_id)] = props
        return NodeRef(label, node_id)

    def upsert_edge(self, edge_type, source, target, properties):
        ref, props = self.prepare_edge(edge_type, source, target, properties)
        self.edges[ref] = props  # keyed exactly as the schema says
        return ref

    def get_node(self, ref):
        props = self.nodes.get((ref.label, ref.id))
        return None if props is None else NodeRecord(ref, props)

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


@pytest.fixture()
def adapter(schema: GraphSchema) -> StubAdapter:
    return StubAdapter(schema)


def _provenance() -> Provenance:
    return Provenance(
        source_id="mod-digital-strategy-2021",
        chunk_id="chunk-3",
        section_refs=["p. 12"],
        supporting_quote="ASGARD will deliver...",
        extraction_model="gemini-2.5-flash",
        extracted_at="2026-07-24T09:00:00+00:00",
    )


def _programme(node_id: str, name: str) -> dict[str, Any]:
    return {"id": node_id, "name": name, **provenance_properties(_provenance())}


# -- writes go through schema validation ------------------------------------


def test_a_fully_provenanced_node_is_accepted(adapter: StubAdapter) -> None:
    ref = adapter.upsert_node("Programme", "p:asgard", _programme("p:asgard", "ASGARD"))
    assert ref == NodeRef("Programme", "p:asgard")
    stored = adapter.get_node(ref)
    assert stored is not None
    assert stored.properties["source_id"] == "mod-digital-strategy-2021"


def test_a_node_without_provenance_is_refused(adapter: StubAdapter) -> None:
    with pytest.raises(SchemaViolation, match="source_id"):
        adapter.upsert_node(
            "Programme", "p:asgard", {"id": "p:asgard", "name": "ASGARD"}
        )
    assert adapter.nodes == {}


def test_an_edge_without_provenance_is_refused(adapter: StubAdapter) -> None:
    org = NodeRef("Organisation", "o:bae")
    prog = NodeRef("Programme", "p:asgard")
    with pytest.raises(SchemaViolation, match="chunk_id"):
        adapter.upsert_edge("develops", org, prog, {"confidence": 0.9})
    assert adapter.edges == {}


def test_edge_endpoints_must_follow_the_ontology(adapter: StubAdapter) -> None:
    org = NodeRef("Organisation", "o:bae")
    prog = NodeRef("Programme", "p:asgard")
    props = provenance_properties(_provenance())
    adapter.upsert_edge("develops", org, prog, props)  # Organisation -> Programme
    with pytest.raises(ValueError, match="not a permitted"):
        adapter.upsert_edge("develops", prog, org, props)  # reversed


def test_same_as_links_two_distinct_nodes_of_the_same_type(
    adapter: StubAdapter,
) -> None:
    a = NodeRef("Programme", "p:asgard")
    b = NodeRef("Programme", "p:asgard-2")
    props = {
        "confidence": 0.82,
        "review_status": "unreviewed",
        "method": "embedding",
        "created_at": "2026-07-24T09:00:00+00:00",
    }
    ref = adapter.upsert_edge("same_as", a, b, props)
    assert ref.type == "same_as"

    # Cross-type identity is not a thing resolution proposes.
    with pytest.raises(ValueError, match="not a permitted"):
        adapter.upsert_edge("same_as", a, NodeRef("Organisation", "o:bae"), props)

    # Nor is a node being same_as itself.
    with pytest.raises(ValueError, match="itself"):
        adapter.upsert_edge("same_as", a, a, props)


def test_extracted_from_edge_carries_the_provenance_block(adapter: StubAdapter) -> None:
    fact = NodeRef("Programme", "p:asgard")
    source = NodeRef("Source", "mod-digital-strategy-2021")
    adapter.upsert_edge(
        "extracted_from", fact, source, provenance_properties(_provenance())
    )
    ref = EdgeRef(
        "extracted_from", fact, source, source_id="mod-digital-strategy-2021",
        chunk_id="chunk-3",
    )
    assert adapter.edges[ref]["chunk_id"] == "chunk-3"


def test_an_edge_to_a_source_must_name_that_source(
    adapter: StubAdapter, schema: GraphSchema
) -> None:
    """For every edge type with a Source endpoint and a provenance block, and
    every (endpoint id, source_id) pair from a small grid: the write is
    accepted exactly when the two agree, so a trace-back can never name two
    different documents for one edge."""
    ids = ("src-a", "src-b", "src-a ")
    checked = 0
    for edge in schema.edges:
        if edge.property("source_id") is None:
            continue
        for source_label, target_label in edge.pairs():
            if SOURCE_LABEL not in (source_label, target_label):
                continue
            for endpoint_id, claimed in itertools.product(ids, ids):
                source = NodeRef(
                    source_label, endpoint_id if source_label == SOURCE_LABEL else "e:1"
                )
                target = NodeRef(
                    target_label, endpoint_id if target_label == SOURCE_LABEL else "e:1"
                )
                props = dict(provenance_properties(_provenance()), source_id=claimed)
                if claimed == endpoint_id:
                    ref = adapter.upsert_edge(edge.type, source, target, props)
                    assert ref.source_id == endpoint_id
                else:
                    with pytest.raises(ValueError, match="does not match the Source"):
                        adapter.upsert_edge(edge.type, source, target, props)
                checked += 1
    assert checked, "no provenanced edge type touches a Source node"


def test_a_second_source_adds_an_edge_rather_than_overwriting_the_first(
    adapter: StubAdapter,
) -> None:
    """Several sources may support one fact, and ARCHITECTURE.md requires them
    to stay rankable — so an edge is keyed per extraction, not per relation."""
    org = NodeRef("Organisation", "o:bae")
    prog = NodeRef("Programme", "p:asgard")
    first = provenance_properties(_provenance())
    second = provenance_properties(
        Provenance(
            source_id="naval-news-fads",
            chunk_id="chunk-1",
            section_refs=["Introduction"],
            supporting_quote="BAE Systems is developing ASGARD",
            extraction_model="gemini-2.5-flash",
            extracted_at="2026-07-25T09:00:00+00:00",
        )
    )
    ref_a = adapter.upsert_edge("develops", org, prog, first)
    ref_b = adapter.upsert_edge("develops", org, prog, second)

    assert ref_a != ref_b
    assert len(adapter.edges) == 2
    quotes = {props["supporting_quote"] for props in adapter.edges.values()}
    assert quotes == {first["supporting_quote"], second["supporting_quote"]}


def test_re_extracting_the_same_chunk_is_idempotent(adapter: StubAdapter) -> None:
    org = NodeRef("Organisation", "o:bae")
    prog = NodeRef("Programme", "p:asgard")
    props = provenance_properties(_provenance())
    assert adapter.upsert_edge("develops", org, prog, props) == adapter.upsert_edge(
        "develops", org, prog, props
    )
    assert len(adapter.edges) == 1


def test_same_as_is_keyed_on_the_pair_alone(adapter: StubAdapter) -> None:
    """One identity claim per pair — it is not extracted from a document, so
    there is no per-chunk occurrence to keep apart."""
    a = NodeRef("Programme", "p:asgard")
    b = NodeRef("Programme", "p:asgard-2")
    ref = adapter.upsert_edge(
        "same_as",
        a,
        b,
        {
            "confidence": 0.82,
            "method": "embedding",
            "created_at": "2026-07-25T00:00:00Z",
        },
    )
    assert ref == EdgeRef("same_as", a, b)
    assert not ref.is_occurrence()


def test_declared_defaults_are_filled_in(adapter: StubAdapter) -> None:
    """A write that omits alignment_status still records one — the schema
    declares the default, so it is not left to each backend."""
    props = _programme("p:asgard", "ASGARD")
    del props["alignment_status"]
    adapter.upsert_node("Programme", "p:asgard", props)
    assert adapter.nodes[("Programme", "p:asgard")]["alignment_status"] == "pending"

    a, b = NodeRef("Programme", "p:a"), NodeRef("Programme", "p:b")
    ref = adapter.upsert_edge(
        "same_as",
        a,
        b,
        {"confidence": 0.5, "method": "alias", "created_at": "2026-07-25T00:00:00Z"},
    )
    assert adapter.edges[ref]["review_status"] == "unreviewed"


def test_the_primary_key_property_must_match_the_node_id(
    adapter: StubAdapter,
) -> None:
    props = _programme("p:something-else", "ASGARD")
    with pytest.raises(ValueError, match="does not match the node id"):
        adapter.upsert_node("Programme", "p:asgard", props)


@pytest.mark.parametrize("bad_id", ["", " ", "\t"])
def test_an_empty_node_id_is_refused(adapter: StubAdapter, bad_id: str) -> None:
    props = _programme(bad_id, "ASGARD")
    with pytest.raises(ValueError, match="node id"):
        adapter.upsert_node("Programme", bad_id, props)
    with pytest.raises(ValueError, match="node id"):
        adapter.upsert_edge(
            "develops",
            NodeRef("Organisation", bad_id),
            NodeRef("Programme", "p:asgard"),
            provenance_properties(_provenance()),
        )
    assert adapter.nodes == {} and adapter.edges == {}


def test_empty_provenance_is_refused_on_a_write(adapter: StubAdapter) -> None:
    """The reviewer's probe: every provenance field present but empty."""
    props = dict(
        _programme("p:x", "X"),
        source_id="",
        chunk_id="",
        section_refs=[],
        supporting_quote="",
        extraction_model="",
        extracted_at="",
    )
    with pytest.raises(SchemaViolation, match="empty"):
        adapter.upsert_node("Programme", "p:x", props)
    with pytest.raises(SchemaViolation, match="extracted_at"):
        adapter.upsert_node(
            "Programme", "p:x", dict(_programme("p:x", "X"), extracted_at="yesterday")
        )
    assert adapter.nodes == {}


def test_timestamps_are_stored_in_one_utc_form(adapter: StubAdapter) -> None:
    """``Z``, ``+00:00`` and another offset for one instant store identically,
    so string ordering in either backend is chronological."""
    stored = set()
    spellings = (
        "2026-07-24T09:00:00Z",
        "2026-07-24T09:00:00+00:00",
        "2026-07-24T10:00:00+01:00",
    )
    for i, spelling in enumerate(spellings):
        node_id = f"p:{i}"
        adapter.upsert_node(
            "Programme", node_id, dict(_programme(node_id, "X"), extracted_at=spelling)
        )
        stored.add(adapter.nodes[("Programme", node_id)]["extracted_at"])
    assert stored == {"2026-07-24T09:00:00.000000+00:00"}


def test_unknown_label_or_edge_type_is_reported(adapter: StubAdapter) -> None:
    with pytest.raises(KeyError, match="Widget"):
        adapter.upsert_node("Widget", "w:1", {})
    with pytest.raises(KeyError, match="wobbles"):
        adapter.upsert_edge(
            "wobbles", NodeRef("Programme", "a"), NodeRef("Programme", "b"), {}
        )


# -- result types -----------------------------------------------------------


def test_traversal_result_reports_its_path(adapter: StubAdapter) -> None:
    node = NodeRecord(NodeRef("Programme", "p:asgard"))
    start = NodeRef("Organisation", "o:bae")
    edge = EdgeRecord(EdgeRef("develops", start, node.ref), {"confidence": 1.0})
    result = TraversalResult(start=start, steps=(PathStep(edge, node),))
    assert result.hops == 1
    assert result.end is node

    empty = TraversalResult(start=NodeRef("Organisation", "o:bae"), steps=())
    assert empty.hops == 0
    assert empty.end is None  # a zero-hop result must not raise


def test_similarity_hit_carries_a_score() -> None:
    hit = SimilarityHit(node=NodeRecord(NodeRef("Programme", "p:asgard")), score=0.91)
    assert hit.score == pytest.approx(0.91)


def test_adapter_is_a_context_manager(schema: GraphSchema) -> None:
    closed: list[bool] = []

    class ClosingAdapter(StubAdapter):
        def close(self) -> None:
            closed.append(True)

    with ClosingAdapter(schema) as adapter:
        adapter.initialise_schema()
    assert adapter.initialised
    assert closed == [True], "__exit__ must close the adapter"


def test_direction_covers_both_ways(schema: GraphSchema) -> None:
    assert {d.value for d in Direction} == {"outgoing", "incoming", "both"}


def test_the_interface_cannot_be_instantiated_directly(schema: GraphSchema) -> None:
    with pytest.raises(TypeError):
        StorageAdapter(schema)  # type: ignore[abstract]


def test_every_required_method_is_abstract() -> None:
    """The ticket's method list must stay mandatory for every backend."""
    assert StorageAdapter.__abstractmethods__ == frozenset(
        {
            "initialise_schema",
            "upsert_node",
            "upsert_edge",
            "get_node",
            "traverse",
            "similar_nodes",
            "trace_back",
        }
    )
