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
    MissingNodeError,
    NodeRecord,
    NodeRef,
    PathStep,
    PropertyType,
    ProvenanceHit,
    SchemaViolation,
    SimilarityHit,
    StorageAdapter,
    TraversalResult,
    provenance_properties,
)
from dikms.storage.schema import SOURCE_LABEL


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


def test_every_relation_traces_back_through_its_own_source_id(
    adapter: StubAdapter, schema: GraphSchema
) -> None:
    """An edge cannot start an edge, so no relation has an ``extracted_from``
    edge; each stored relation edge must therefore name its own source and
    chunk, i.e. its key is one extraction (an occurrence)."""
    assert set(schema.edge("extracted_from").source_labels).isdisjoint(
        schema.edge_types()
    )
    props = provenance_properties(_provenance())
    for edge in schema.edges:
        if edge.structural:
            continue
        source_label, target_label = edge.pairs()[0]
        ref = adapter.upsert_edge(
            edge.type, NodeRef(source_label, "e:1"), NodeRef(target_label, "e:2"), props
        )
        assert ref.is_occurrence(), edge.type
        assert adapter.edges[ref]["source_id"] == props["source_id"]


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


def _same_as(confidence: float = 0.9, **extra: Any) -> dict[str, Any]:
    return {
        "confidence": confidence,
        "method": "embedding",
        "created_at": "2026-07-25T00:00:00Z",
        **extra,
    }


def test_same_as_is_one_edge_whichever_way_round_it_is_written(
    adapter: StubAdapter, schema: GraphSchema
) -> None:
    """Over every ordered pair of distinct nodes from a small set, for every
    label same_as allows: A->B and B->A give the same key, so a pair has one
    stored identity claim and one review state."""
    ids = ("p:a", "p:b", "p:B", "p:a2", "z", "0")
    labels = [s for s, _ in schema.edge("same_as").pairs()]
    for label in labels:
        for x, y in itertools.permutations(ids, 2):
            a, b = NodeRef(label, x), NodeRef(label, y)
            forward = adapter.edge_ref("same_as", a, b, _same_as())
            backward = adapter.edge_ref("same_as", b, a, _same_as())
            assert forward == backward
            assert (forward.source.id, forward.target.id) == tuple(sorted((x, y)))


def test_a_rejected_pair_cannot_be_reproposed_the_other_way_round(
    adapter: StubAdapter,
) -> None:
    """The reviewer's probe: reject A->B, then propose B->A."""
    a, b = NodeRef("Programme", "p:a"), NodeRef("Programme", "p:b")
    adapter.upsert_edge("same_as", a, b, _same_as(review_status="rejected"))
    adapter.upsert_edge("same_as", b, a, _same_as(0.95))
    same_as = {r: p for r, p in adapter.edges.items() if r.type == "same_as"}
    assert len(same_as) == 1  # one pair, one claim: the later write updates it
    (ref,) = same_as
    assert (ref.source, ref.target) == (a, b)


@pytest.mark.parametrize(
    ("confidence", "accepted"),
    [
        (0.0, True),
        (0.5, True),
        (1.0, True),
        (1, True),
        (-0.0001, False),
        (1.0001, False),
        (7.5, False),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
    ],
)
def test_confidence_is_a_finite_value_in_zero_to_one(
    adapter: StubAdapter, confidence: float, accepted: bool
) -> None:
    """Both the identity confidence on same_as and the optional extraction
    confidence on a substantive relation."""
    a, b = NodeRef("Programme", "p:a"), NodeRef("Programme", "p:b")
    org = NodeRef("Organisation", "o:bae")
    develops = dict(provenance_properties(_provenance()), confidence=confidence)
    writes = (
        lambda: adapter.upsert_edge("same_as", a, b, _same_as(confidence)),
        lambda: adapter.upsert_edge("develops", org, a, develops),
    )
    for write in writes:
        if accepted:
            write()
        else:
            with pytest.raises(SchemaViolation, match="confidence"):
                write()


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


# -- upsert semantics -------------------------------------------------------


def _organisation(node_id: str, source_id: str, **attributes: Any) -> dict[str, Any]:
    props = dict(_programme(node_id, "BAE Systems"), source_id=source_id)
    return {**props, **attributes}


def test_a_node_upsert_clears_attributes_the_new_extraction_lacks(
    adapter: StubAdapter,
) -> None:
    """The reviewer's probe: nationality from source A must not survive under
    source B's provenance block, even on a backend that merges columns."""
    ref = NodeRef("Organisation", "o:bae")
    first = _organisation(ref.id, "src-a", nationality="UK")
    adapter.upsert_node(ref.label, ref.id, first)
    adapter.upsert_node(ref.label, ref.id, _organisation(ref.id, "src-b"))
    stored = adapter.get_node(ref).properties
    assert stored["source_id"] == "src-b"
    assert stored["nationality"] is None


def test_prepare_emits_every_declared_property_except_the_vector(
    adapter: StubAdapter, schema: GraphSchema
) -> None:
    """For every fact-bearing label and every subset of its optional
    attributes: the prepared dict names every declared property apart from
    ``embedding``, so a backend that writes the dict overwrites fully."""
    base = _programme("n:1", "N")
    for node in schema.nodes:
        if node.label == SOURCE_LABEL:
            continue
        expected = set(node.property_names()) - {"embedding"}
        # The ontology attributes: the optional facts a later write may lack.
        attributes = [
            p for p in node.properties if not p.required and p.name not in base
            and p.type is not PropertyType.VECTOR
        ]
        for r in range(len(attributes) + 1):
            for subset in itertools.combinations(attributes, r):
                given = {p.name: p.values[0] if p.values else "x" for p in subset}
                props = adapter.prepare_node(node.label, "n:1", dict(base, **given))
                assert set(props) == expected, (node.label, sorted(given))
                for name in expected - set(given) - set(base):
                    assert props[name] is None, (node.label, name)


def test_a_fact_write_leaves_a_stored_embedding_alone(adapter: StubAdapter) -> None:
    """The embedding is computed separately (M2); re-writing the fact must not
    clear it, while an explicit embedding is written as given."""
    width = adapter.schema.vector_dimension
    props = _programme("p:asgard", "ASGARD")
    adapter.upsert_node("Programme", "p:asgard", dict(props, embedding=[0.5] * width))
    adapter.upsert_node("Programme", "p:asgard", props)
    stored = adapter.nodes[("Programme", "p:asgard")]
    assert stored["embedding"] == [0.5] * width


def test_an_edge_upsert_also_overwrites_fully(adapter: StubAdapter) -> None:
    org, prog = NodeRef("Organisation", "o:bae"), NodeRef("Programme", "p:asgard")
    props = provenance_properties(_provenance())
    ref = adapter.upsert_edge("develops", org, prog, dict(props, confidence=0.4))
    adapter.upsert_edge("develops", org, prog, props)
    assert adapter.edges[ref]["confidence"] is None


def test_an_edge_to_a_missing_node_raises_and_writes_nothing(
    schema: GraphSchema,
) -> None:
    adapter = StubAdapter(schema, require_existing_endpoints=True)
    org, prog = NodeRef("Organisation", "o:bae"), NodeRef("Programme", "p:asgard")
    props = provenance_properties(_provenance())
    with pytest.raises(MissingNodeError, match="o:bae"):
        adapter.upsert_edge("develops", org, prog, props)
    adapter.upsert_node(org.label, org.id, _organisation(org.id, "src-a"))
    with pytest.raises(MissingNodeError, match="p:asgard"):
        adapter.upsert_edge("develops", org, prog, props)
    assert adapter.edges == {}
    adapter.upsert_node(prog.label, prog.id, _programme(prog.id, "ASGARD"))
    adapter.upsert_edge("develops", org, prog, props)
    assert len(adapter.edges) == 1


# -- read helpers -----------------------------------------------------------


def _hit(node_id: str, score: float) -> SimilarityHit:
    return SimilarityHit(NodeRecord(NodeRef("Programme", node_id)), score)


def test_similarity_hits_are_ranked_highest_first_with_an_inclusive_floor() -> None:
    """Over a grid of scores, limits and floors: the result is exactly the
    hits with score >= min_score, highest first, truncated to limit."""
    scores = [0.9, -0.2, 0.5, 0.5, 1.0, 0.0, 0.75]
    hits = [_hit(f"p:{i}", s) for i, s in enumerate(scores)]
    for limit in (1, 2, 3, 10):
        for floor in (None, -1.0, 0.0, 0.5, 0.75, 1.0, 1.01):
            ranked = StorageAdapter.rank_similarity_hits(
                hits, limit=limit, min_score=floor
            )
            kept = sorted(
                (s for s in scores if floor is None or s >= floor), reverse=True
            )[:limit]
            assert [h.score for h in ranked] == kept, (limit, floor)
    with pytest.raises(ValueError, match="limit"):
        StorageAdapter.rank_similarity_hits(hits, limit=0)


def test_a_query_embedding_is_checked(adapter: StubAdapter) -> None:
    width = adapter.schema.vector_dimension
    adapter.check_query_embedding("Programme", [0.1] * width)
    for bad in ([0.1] * (width - 1), [float("nan")] * width, [True] * width):
        with pytest.raises(ValueError):
            adapter.check_query_embedding("Programme", bad)
    with pytest.raises(ValueError, match="no vector"):
        adapter.check_query_embedding(SOURCE_LABEL, [0.1] * width)


def test_traversal_arguments_are_checked(adapter: StubAdapter) -> None:
    ok = {"edge_types": None, "max_hops": 1, "target_labels": None, "limit": None}
    adapter.check_traversal_args(**ok)
    adapter.check_traversal_args(
        **dict(ok, edge_types=["develops", "same_as"], target_labels=["Programme"])
    )
    for bad, error in (
        ({"max_hops": 0}, ValueError),
        ({"limit": 0}, ValueError),
        ({"edge_types": ["wobbles"]}, KeyError),
        ({"target_labels": ["Widget"]}, KeyError),
    ):
        with pytest.raises(error):
            adapter.check_traversal_args(**dict(ok, **bad))


def test_a_path_that_revisits_a_node_is_not_simple() -> None:
    """``traverse`` returns simple paths only; ``is_simple`` is the check,
    exhaustive over every 1- to 3-hop walk on three nodes."""
    nodes = [NodeRef("Programme", n) for n in ("a", "b", "c")]
    start = nodes[0]
    for hops in (1, 2, 3):
        for walk in itertools.product(nodes, repeat=hops):
            prev, steps = start, []
            for node in walk:
                edge = EdgeRecord(EdgeRef("part_of", prev, node))
                steps.append(PathStep(edge, NodeRecord(node)))
                prev = node
            result = TraversalResult(start, tuple(steps))
            sequence = (start, *walk)
            assert result.node_refs() == sequence
            assert result.is_simple() == (len(set(sequence)) == len(sequence))


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
