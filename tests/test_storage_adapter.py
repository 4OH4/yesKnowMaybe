"""Behavioural tests for the adapter base class and its shared contract.

Most tests run on the in-memory stub (``stub_adapter.py``), which is not a
backend: it exists so the shared, non-abstract behaviour in
:class:`dikms.storage.StorageAdapter` (write validation, endpoint rules,
traversal result helpers) is exercised without a database, and many of those
tests inspect what it stored directly.

The contract tests marked ``@both_backends`` (edge reads, ``same_as``
verdicts) use only the adapter interface and also run against the Kùzu
backend, through the parametrised ``adapter`` fixture. That is the proof that
a real backend honours the canonical-order and verdict-keeping contract, not
only the stub.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from typing import Any

import pytest
from stub_adapter import StubAdapter

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
    SchemaViolation,
    SimilarityHit,
    StorageAdapter,
    TraversalResult,
    normalise_timestamp,
    parse_timestamp,
    provenance_properties,
    write_same_as,
)
from dikms.storage.schema import SOURCE_LABEL


@pytest.fixture()
def adapter(request: pytest.FixtureRequest, schema: GraphSchema) -> StorageAdapter:
    """The stub by default; ``@both_backends`` parametrises it over the stub
    (which then requires endpoints to exist, as a real backend does) and the
    Kùzu backend in a temporary directory."""
    backend = getattr(request, "param", "stub")
    if backend == "kuzu":
        return request.getfixturevalue("open_kuzu")()
    return StubAdapter(schema, require_existing_endpoints=backend == "stub-strict")


#: Run a test against the stub and against every real backend.
both_backends = pytest.mark.parametrize(
    "adapter", ["stub-strict", "kuzu"], indirect=True
)


def _ensure_nodes(adapter: StorageAdapter, refs: Iterable[NodeRef]) -> None:
    """Write a minimal, fully provenanced node for each ref not yet stored,
    so an edge test can run on a backend that requires its endpoints."""
    for ref in refs:
        if adapter.get_node(ref) is not None:
            continue
        if ref.label == SOURCE_LABEL:
            props = {
                "id": ref.id,
                "title": f"Source {ref.id}",
                "url": f"https://example.org/{ref.id}",
                "authoritativeness": "medium",
                "ingested_at": "2026-07-24T09:00:00+00:00",
            }
        else:
            props = _programme(ref.id, ref.id)
        adapter.upsert_node(ref.label, ref.id, props)


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


# -- edge reads -------------------------------------------------------------


@both_backends
def test_get_edge_finds_a_same_as_pair_named_either_way_round(
    adapter: StorageAdapter,
) -> None:
    for i, flipped in enumerate((False, True)):
        a, b = NodeRef("Programme", f"p:a{i}"), NodeRef("Programme", f"p:b{i}")
        _ensure_nodes(adapter, (a, b))
        assert adapter.get_edge(EdgeRef("same_as", a, b)) is None
        written = (b, a) if flipped else (a, b)
        stored = adapter.upsert_edge("same_as", *written, _same_as(0.7))
        for asked in ((a, b), (b, a)):
            record = adapter.get_edge(EdgeRef("same_as", *asked))
            assert record is not None, (written, asked)
            assert record.ref == stored == EdgeRef("same_as", a, b)
            assert record.properties["confidence"] == 0.7


@both_backends
def test_get_edge_reads_the_key_every_write_is_stored_under(
    adapter: StorageAdapter, schema: GraphSchema
) -> None:
    """Over every edge type, every endpoint pair it permits and both ways of
    naming two distinct ids: the key a read resolves to is the key the write
    of the same edge returned, so a read never misses a stored edge."""
    props = provenance_properties(_provenance())
    checked = 0
    for edge in schema.edges:
        for source_label, target_label in edge.pairs():
            for x, y in (("e:1", "e:2"), ("e:2", "e:1"), ("e:B", "e:a")):
                src, tgt = NodeRef(source_label, x), NodeRef(target_label, y)
                if source_label == SOURCE_LABEL:
                    src = NodeRef(SOURCE_LABEL, props["source_id"])
                if target_label == SOURCE_LABEL:
                    tgt = NodeRef(SOURCE_LABEL, props["source_id"])
                _ensure_nodes(adapter, (src, tgt))
                write = _same_as() if edge.type == "same_as" else props
                written = adapter.upsert_edge(edge.type, src, tgt, write)
                asks = [EdgeRef(edge.type, src, tgt, written.source_id,
                                written.chunk_id)]
                if edge.symmetric:
                    asks.append(EdgeRef(edge.type, tgt, src))
                for ask in asks:
                    assert adapter.canonical_edge_ref(ask) == written, ask
                    record = adapter.get_edge(ask)
                    assert record is not None and record.ref == written, ask
                checked += 1
    assert checked


@both_backends
def test_get_edge_refuses_a_ref_that_names_no_single_edge(
    adapter: StorageAdapter,
) -> None:
    org, prog = NodeRef("Organisation", "o:bae"), NodeRef("Programme", "p:asgard")
    a, b = NodeRef("Programme", "p:a"), NodeRef("Programme", "p:b")
    props = provenance_properties(_provenance())
    _ensure_nodes(adapter, (org, prog, a, b))
    occurrence = adapter.upsert_edge("develops", org, prog, props)
    assert adapter.get_edge(occurrence).properties["chunk_id"] == "chunk-3"

    for bad in (
        EdgeRef("develops", org, prog),  # generic: may be several extractions
        EdgeRef("develops", org, prog, source_id=props["source_id"]),
        EdgeRef("develops", org, prog, chunk_id="chunk-3"),
        EdgeRef("same_as", a, b, source_id="src", chunk_id="c"),  # pair-keyed
        EdgeRef("same_as", a, NodeRef("Organisation", "o:bae")),  # cross-label
        EdgeRef("develops", prog, org, "src", "c"),  # reversed domain/range
        EdgeRef("same_as", a, NodeRef("Programme", " ")),  # empty id
    ):
        with pytest.raises(ValueError):
            adapter.get_edge(bad)
    with pytest.raises(KeyError, match="wobbles"):
        adapter.get_edge(EdgeRef("wobbles", a, b))


@both_backends
def test_get_edge_returns_none_for_an_absent_edge(adapter: StorageAdapter) -> None:
    """Including one whose endpoint nodes were never written."""
    org, prog = NodeRef("Organisation", "o:none"), NodeRef("Programme", "p:none")
    assert adapter.get_edge(EdgeRef("develops", org, prog, "src", "c")) is None
    assert adapter.get_edge(EdgeRef("same_as", prog, NodeRef("Programme", "x"))) is None


# -- same_as verdicts survive re-proposal -----------------------------------


_PAIR = (NodeRef("Programme", "p:a"), NodeRef("Programme", "p:b"))


def _same_as_edges(
    adapter: StorageAdapter, pair: tuple[NodeRef, NodeRef]
) -> list[EdgeRecord]:
    """Every stored ``same_as`` edge between the two nodes, either way round:
    read from the stub's own store, or through ``traverse`` on a backend."""
    a, b = pair
    if isinstance(adapter, StubAdapter):
        keys = {EdgeRef("same_as", a, b), EdgeRef("same_as", b, a)}
        return [EdgeRecord(k, adapter.edges[k]) for k in keys if k in adapter.edges]
    return [
        path.steps[0].edge
        for path in adapter.traverse(a, edge_types=["same_as"], max_hops=1)
        if path.end is not None and path.end.ref == b
    ]


def _stored_same_as(
    adapter: StorageAdapter, pair: tuple[NodeRef, NodeRef] = _PAIR
) -> dict[str, Any]:
    edges = _same_as_edges(adapter, pair)
    assert len(edges) == 1, edges  # one pair, one stored claim
    (edge,) = edges
    assert (edge.ref.source, edge.ref.target) == tuple(
        sorted(pair, key=lambda n: (n.label, n.id))
    )
    return dict(edge.properties)


@both_backends
@pytest.mark.parametrize("verdict", ["rejected", "confirmed"])
@pytest.mark.parametrize("first", [_PAIR, _PAIR[::-1]], ids=["A->B", "B->A"])
@pytest.mark.parametrize("again", [_PAIR, _PAIR[::-1]], ids=["A->B", "B->A"])
def test_a_re_proposal_keeps_the_human_verdict(
    adapter: StorageAdapter,
    verdict: str,
    first: tuple[NodeRef, NodeRef],
    again: tuple[NodeRef, NodeRef],
) -> None:
    """The issue's probe: a human rejects (or confirms) the pair, then
    resolution proposes it again, either way round, with a new confidence."""
    _ensure_nodes(adapter, _PAIR)
    write_same_as(
        adapter, *first, confidence=0.6, method="human", review_status=verdict,
        created_at="2026-07-25T00:00:00Z",
    )
    ref = write_same_as(adapter, *again, confidence=0.97, method="llm")
    assert ref == EdgeRef("same_as", *_PAIR)
    stored = _stored_same_as(adapter)
    assert stored["review_status"] == verdict
    assert stored["confidence"] == 0.97  # the automated fields are updated
    assert stored["method"] == "llm"
    assert stored["created_at"] == "2026-07-25T00:00:00.000000+00:00"


@both_backends
@pytest.mark.parametrize("new", ["confirmed", "rejected", "unreviewed"])
@pytest.mark.parametrize("old", ["confirmed", "rejected"])
def test_an_explicit_verdict_overwrites_the_stored_one(
    adapter: StorageAdapter, old: str, new: str
) -> None:
    a, b = _PAIR
    _ensure_nodes(adapter, _PAIR)
    write_same_as(adapter, a, b, confidence=0.5, method="human", review_status=old)
    write_same_as(adapter, b, a, confidence=0.5, method="human", review_status=new)
    assert _stored_same_as(adapter)["review_status"] == new


@both_backends
def test_a_first_proposal_is_unreviewed_and_dated(adapter: StorageAdapter) -> None:
    from datetime import UTC, datetime

    _ensure_nodes(adapter, _PAIR)
    before = datetime.now(UTC)
    write_same_as(adapter, *_PAIR, confidence=0.8, method="embedding")
    stored = _stored_same_as(adapter)
    assert stored["review_status"] == "unreviewed"
    assert before <= parse_timestamp(stored["created_at"]) <= datetime.now(UTC)


def test_the_unreviewed_status_is_the_schema_default(schema: GraphSchema) -> None:
    """write_same_as treats exactly the default status as 'no human verdict'."""
    from dikms.storage.identity import UNREVIEWED
    from dikms.storage.schema import SAME_AS_REVIEW_STATES

    prop = schema.edge("same_as").property("review_status")
    assert prop is not None and prop.default == UNREVIEWED
    assert UNREVIEWED in SAME_AS_REVIEW_STATES


@both_backends
def test_a_refused_re_proposal_leaves_the_verdict_alone(
    adapter: StorageAdapter,
) -> None:
    _ensure_nodes(adapter, _PAIR)
    write_same_as(
        adapter, *_PAIR, confidence=0.5, method="human", review_status="rejected"
    )
    before = dict(_stored_same_as(adapter))
    for bad in ({"confidence": 1.5, "method": "llm"},
                {"confidence": 0.9, "method": "guesswork"}):
        with pytest.raises(SchemaViolation):
            write_same_as(adapter, *_PAIR[::-1], **bad)
    assert _stored_same_as(adapter) == before


@both_backends
def test_a_re_proposal_needs_both_nodes_to_exist(adapter: StorageAdapter) -> None:
    with pytest.raises(MissingNodeError):
        write_same_as(adapter, *_PAIR, confidence=0.5, method="alias")
    _ensure_nodes(adapter, _PAIR[:1])
    with pytest.raises(MissingNodeError, match="p:b"):
        write_same_as(adapter, *_PAIR, confidence=0.5, method="alias")
    assert adapter.get_edge(EdgeRef("same_as", *_PAIR)) is None


@both_backends
def test_review_status_follows_the_last_explicit_verdict_over_any_history(
    adapter: StorageAdapter,
) -> None:
    """Exhaustive over every sequence of up to three writes, each one either
    way round, with or without a verdict and with one of two confidences: the
    stored status is the last explicit verdict (``unreviewed`` if none was
    ever given), the automated fields are always the last write's, and
    ``created_at`` is the first write's. One stored edge throughout. Each
    sequence writes its own pair, so sequences cannot see each other's edges.

    On a real backend, to keep the suite fast, sequences are up to two writes
    with one confidence (72 sequences); the confidence update is covered on
    every backend by ``test_a_re_proposal_keeps_the_human_verdict``."""
    on_stub = isinstance(adapter, StubAdapter)
    statuses = (None, "unreviewed", "confirmed", "rejected")
    confidences = (0.3, 0.9) if on_stub else (0.3,)
    writes = list(itertools.product((False, True), statuses, confidences))
    lengths = (1, 2, 3) if on_stub else (1, 2)
    first_stamp = "2026-01-01T00:00:00Z"
    sequences = 0
    for length in lengths:
        for sequence in itertools.product(writes, repeat=length):
            pair = (
                NodeRef("Programme", f"p:a{sequences}"),
                NodeRef("Programme", f"p:b{sequences}"),
            )
            _ensure_nodes(adapter, pair)
            for i, (flipped, status, confidence) in enumerate(sequence):
                # Only the first write names a created_at; later ones must
                # keep it rather than restamp the pair.
                write_same_as(
                    adapter, *(pair[::-1] if flipped else pair),
                    confidence=confidence, method="llm", review_status=status,
                    created_at=first_stamp if i == 0 else None,
                )
            explicit = [s for _, s, _ in sequence if s is not None]
            expected = explicit[-1] if explicit else "unreviewed"
            stored = _stored_same_as(adapter, pair)
            assert stored["review_status"] == expected, sequence
            assert stored["confidence"] == sequence[-1][2], sequence
            assert stored["created_at"] == normalise_timestamp(first_stamp)
            for asked in (pair, pair[::-1]):
                record = adapter.get_edge(EdgeRef("same_as", *asked))
                assert record is not None and record.properties == stored
            sequences += 1
    assert sequences == sum(len(writes) ** n for n in lengths)


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
            "get_edge",
            "traverse",
            "similar_nodes",
            "trace_back",
        }
    )
