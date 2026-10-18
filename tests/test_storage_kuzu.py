"""The Kùzu backend, exercised only through the adapter interface.

Every store is opened from config through :func:`dikms.storage.create_adapter`
(the ``open_kuzu`` fixture), in a temporary directory: no credentials, no
network, never the configured ``data/graph.kuzu``. No test here imports
``kuzu``; the DDL tests import the backend module's pure DDL functions,
which is the only reach past the interface.

Property tests (seeded, so failures reproduce) cover the parts whose failure
mode is a plausible but wrong answer rather than a crash: traversal against
a brute-force reference, similarity ranking against a direct cosine, the
DDL's endpoint pairs against the schema, and the stored state after random
write sequences against the in-memory stub.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import itertools
import math
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from stub_adapter import StubAdapter

from dikms.models import Provenance, Span
from dikms.ontology import Ontology
from dikms.storage import (
    MANDATORY_PROVENANCE_PROPERTIES,
    PROVENANCE_PROPERTIES,
    Direction,
    EdgeRef,
    GraphSchema,
    MissingNodeError,
    NodeRef,
    PropertyDef,
    PropertyType,
    SchemaViolation,
    StorageAdapter,
    TraversalResult,
    build_schema,
    provenance_properties,
    write_same_as,
)
from dikms.storage.schema import SOURCE_LABEL

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_MODULE = REPO_ROOT / "src" / "dikms" / "storage" / "kuzu_backend.py"

#: The ``open_kuzu`` fixture from conftest.py.
OpenKuzu = Callable[..., StorageAdapter]

# -- fixtures and builders ----------------------------------------------------


@pytest.fixture()
def store(open_kuzu: OpenKuzu) -> StorageAdapter:
    return open_kuzu()


def _prov(
    source_id: str = "src-a",
    chunk_id: str = "chunk-1",
    *,
    quote: str = "BAE Systems is developing ASGARD",
    at: str = "2026-07-24T09:00:00+00:00",
    span: Span | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    if span is not None:
        status = "aligned"
    return provenance_properties(
        Provenance(
            source_id=source_id,
            chunk_id=chunk_id,
            section_refs=["p. 12", "Section 3.1"],
            supporting_quote=quote,
            extraction_model="gemini-2.5-flash",
            extracted_at=at,
            alignment_status=status,
            span=span,
        )
    )


def _entity(node_id: str, name: str | None = None, **extra: Any) -> dict[str, Any]:
    provenance = extra.pop("provenance", None) or _prov()
    return {"id": node_id, "name": name or node_id, **provenance, **extra}


def _source(source_id: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": f"Document {source_id}",
        "url": f"https://example.org/{source_id}",
        "publisher": "Example Publisher",
        "format": "pdf",
        "authoritativeness": "high",
        "ingested_at": "2026-07-24T10:00:00Z",
    }


def _vector(width: int, **entries: float) -> list[float]:
    """A stub embedding: zero apart from the given ``i<n>`` entries. Values
    are exactly representable in float32, so they round-trip exactly."""
    vector = [0.0] * width
    for key, value in entries.items():
        vector[int(key[1:])] = value
    return vector


# -- the backend stays behind the interface -----------------------------------


def _imports_kuzu(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "kuzu" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] == "kuzu":
                return True
    return False


def test_only_the_backend_module_imports_kuzu() -> None:
    files = sorted((REPO_ROOT / "src").rglob("*.py")) + sorted(
        (REPO_ROOT / "tests").rglob("*.py")
    )
    importers = {p.relative_to(REPO_ROOT).as_posix() for p in files if _imports_kuzu(p)}
    assert importers == {"src/dikms/storage/kuzu_backend.py"}


def _code_strings(path: Path) -> list[str]:
    """Every string constant in a module except docstrings: the text its
    queries are built from."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node, clean=False) is not None:
            docstrings.add(id(node.body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_backend_never_deletes_or_drops_anything() -> None:
    """Non-destructive by construction (CLAUDE.md constraint 2): no query the
    backend can build removes a node, an edge, a column or a table."""
    destructive = re.compile(r"\b(DELETE|DETACH|DROP|REMOVE)\b", re.IGNORECASE)
    offending = [s for s in _code_strings(BACKEND_MODULE) if destructive.search(s)]
    assert offending == []


def test_the_factory_builds_the_kuzu_backend(store: StorageAdapter) -> None:
    assert isinstance(store, StorageAdapter)
    assert type(store).__module__ == "dikms.storage.kuzu_backend"


# -- schema ---------------------------------------------------------------------


def test_the_ddl_declares_every_label_property_and_endpoint_pair(
    schema: GraphSchema,
) -> None:
    from dikms.storage.kuzu_backend import column_type, schema_ddl

    statements = schema_ddl(schema)
    assert len(statements) == len(schema.nodes) + len(schema.edges)
    by_table = {re.search(r"EXISTS `(\w+)`", s).group(1): s for s in statements}
    assert set(by_table) == set(schema.node_labels()) | set(schema.edge_types())
    for node in schema.nodes:
        ddl = by_table[node.label]
        assert ddl.startswith("CREATE NODE TABLE IF NOT EXISTS")
        assert f"PRIMARY KEY(`{node.primary_key}`)" in ddl
        for prop in node.properties:
            assert f"`{prop.name}` {column_type(prop)}" in ddl, (node.label, prop)
    for edge in schema.edges:
        ddl = by_table[edge.type]
        assert ddl.startswith("CREATE REL TABLE IF NOT EXISTS")
        declared = set(re.findall(r"FROM `(\w+)` TO `(\w+)`", ddl))
        assert declared == set(edge.pairs()), edge.type
        for prop in edge.properties:
            assert f"`{prop.name}` {column_type(prop)}" in ddl, (edge.type, prop)
    vector = schema.node("Programme").vector_property()
    assert column_type(vector) == f"FLOAT[{schema.vector_dimension}]"
    assert schema.vector_dimension == 3072


def _random_ontology(base: Ontology, rng: random.Random) -> Ontology:
    """The real ontology with each substantive relation's domain and range
    redrawn: either ``*`` or a random non-empty subset of the entity labels."""
    ontology = copy.deepcopy(base)
    labels = [e.name for e in ontology.entity_types if e.name != SOURCE_LABEL]
    for relation in ontology.relation_types:
        if relation.structural:
            continue
        for side in ("domain", "range"):
            if rng.random() < 0.25:
                setattr(relation, side, ["*"])
            else:
                setattr(relation, side, rng.sample(labels, rng.randint(1, 3)))
    return ontology


@pytest.mark.parametrize("seed", range(2))
def test_every_permitted_pair_and_only_those_can_be_stored(
    open_kuzu: OpenKuzu, ontology: Ontology, seed: int
) -> None:
    """Property: for a randomly redrawn ontology (``*`` included), every edge
    type accepts exactly the endpoint pairs the schema enumerates, so the DDL
    declared each one and the shared validation refuses the rest."""
    rng = random.Random(seed)
    redrawn = _random_ontology(ontology, rng)
    assert any(r.domain == ["*"] or r.range == ["*"] for r in redrawn.relation_types
               if not r.structural), "the seed exercises no wildcard"
    schema = build_schema(redrawn, 4)
    store = open_kuzu(schema, name=f"random-{seed}.kuzu")
    labels = schema.node_labels()
    for label in labels:
        props = _source("s") if label == SOURCE_LABEL else _entity("x")
        node_id = "s" if label == SOURCE_LABEL else "x"
        store.upsert_node(label, node_id, props | {"id": node_id})
        if label != SOURCE_LABEL:
            store.upsert_node(label, "y", _entity("y"))
    for edge in schema.edges:
        props = (
            {"confidence": 0.5, "method": "alias", "created_at": "2026-07-25T00:00:00Z"}
            if edge.type == "same_as"
            else _prov(source_id="s")
        )
        for source_label, target_label in itertools.product(labels, labels):
            source = NodeRef(source_label, "s" if source_label == SOURCE_LABEL else "x")
            target = NodeRef(target_label, "s" if target_label == SOURCE_LABEL else "y")
            if (source_label, target_label) in edge.pairs():
                ref = store.upsert_edge(edge.type, source, target, props)
                assert store.get_edge(ref) is not None, (edge.type, ref)
            else:
                with pytest.raises(ValueError, match="not a permitted"):
                    store.upsert_edge(edge.type, source, target, props)


def test_reopening_a_database_is_safe_and_keeps_its_data(
    open_kuzu: OpenKuzu,
) -> None:
    store = open_kuzu()
    org = store.upsert_node("Organisation", "o:bae", _entity("o:bae", "BAE Systems"))
    prog = store.upsert_node("Programme", "p:asgard", _entity("p:asgard", "ASGARD"))
    edge = store.upsert_edge("develops", org, prog, _prov())
    store.initialise_schema()  # idempotent on a populated database
    store.initialise_schema()
    store.close()

    reopened = open_kuzu()  # same file: CREATE ... IF NOT EXISTS throughout
    reopened.initialise_schema()
    assert reopened.get_node(org).properties["name"] == "BAE Systems"
    assert reopened.get_edge(edge).properties["supporting_quote"] == (
        "BAE Systems is developing ASGARD"
    )


def test_an_older_database_gains_new_properties_and_pairs_on_open(
    open_kuzu: OpenKuzu, schema: GraphSchema
) -> None:
    """A schema change of the kind #33 makes (a new ``extracted_from``
    property) and a widened relation reach an existing database when it is
    next opened, without touching what is already stored."""
    store = open_kuzu()
    org = store.upsert_node("Organisation", "o:bae", _entity("o:bae"))
    source = store.upsert_node(SOURCE_LABEL, "src-a", _source("src-a"))
    old = store.upsert_edge("extracted_from", org, source, _prov())
    store.close()

    extracted_from = schema.edge("extracted_from")
    member_of = schema.edge("member_of")
    newer = dataclasses.replace(
        schema,
        edges=tuple(
            dataclasses.replace(
                e,
                properties=e.properties
                + (PropertyDef("attributes", PropertyType.STRING, required=False),),
            )
            if e is extracted_from
            else dataclasses.replace(
                e, source_labels=e.source_labels + ("Programme",)
            )
            if e is member_of
            else e
            for e in schema.edges
        ),
    )
    store = open_kuzu(newer)
    assert store.get_edge(old).properties["attributes"] is None
    assert store.get_edge(old).properties["chunk_id"] == "chunk-1"
    later = store.upsert_edge(
        "extracted_from", org, source,
        _prov(chunk_id="chunk-2") | {"attributes": '{"nationality": "UK"}'},
    )
    assert store.get_edge(later).properties["attributes"] == '{"nationality": "UK"}'
    prog = store.upsert_node("Programme", "p:x", _entity("p:x"))
    assert store.get_edge(store.upsert_edge("member_of", prog, org, _prov()))


def test_a_database_with_a_different_vector_width_is_refused(
    open_kuzu: OpenKuzu, ontology: Ontology
) -> None:
    open_kuzu(build_schema(ontology, 8), name="narrow.kuzu").close()
    with pytest.raises(RuntimeError, match=r"FLOAT\[8\].*FLOAT\[3072\]"):
        open_kuzu(build_schema(ontology, 3072), name="narrow.kuzu")


# -- writes and round trips ---------------------------------------------------


def test_nodes_and_edges_round_trip_with_every_property(store: StorageAdapter) -> None:
    width = store.schema.vector_dimension
    span = Span(parse_id="parse-1", start=120, end=152)
    source = store.upsert_node(SOURCE_LABEL, "src-a", _source("src-a"))
    org_props = _entity(
        "o:bae", "BAE Systems",
        provenance=_prov(span=span, at="2026-07-24T10:00:00+01:00"),
        org_type="commercial", nationality="UK",
        embedding=_vector(width, i0=0.5, i7=-1.25, i3071=2.0),
    )
    org = store.upsert_node("Organisation", "o:bae", org_props)
    prog = store.upsert_node(
        "Programme", "p:asgard",
        _entity("p:asgard", "ASGARD", provenance=_prov(status="unresolved")),
    )
    develops_props = _prov(chunk_id="chunk-2", span=span) | {"confidence": 0.75}
    develops = store.upsert_edge("develops", org, prog, develops_props)
    extracted = store.upsert_edge("extracted_from", org, source, _prov(span=span))
    other = store.upsert_node("Organisation", "o:bae-2", _entity("o:bae-2", "BAE"))
    same_as = write_same_as(
        store, other, org, confidence=0.875, method="embedding",
        created_at="2026-07-25T00:00:00Z",
    )

    stored = store.get_node(org).properties
    expected = store.prepare_node("Organisation", "o:bae", org_props)
    assert stored == expected
    # Spelled out, so the round trip does not lean only on prepare_node:
    assert stored["source_id"] == "src-a" and stored["chunk_id"] == "chunk-1"
    assert stored["section_refs"] == ["p. 12", "Section 3.1"]
    assert stored["supporting_quote"] == "BAE Systems is developing ASGARD"
    assert stored["extraction_model"] == "gemini-2.5-flash"
    assert stored["extracted_at"] == "2026-07-24T09:00:00.000000+00:00"
    assert stored["alignment_status"] == "aligned"
    assert stored["span_parse_id"] == "parse-1"
    assert (stored["span_start"], stored["span_end"]) == (120, 152)
    assert stored["embedding"] == org_props["embedding"]

    assert store.get_node(prog).properties["alignment_status"] == "unresolved"
    assert store.get_node(prog).properties["span_start"] is None
    assert store.get_node(prog).properties["embedding"] is None
    assert store.get_node(source).properties == store.prepare_node(
        SOURCE_LABEL, "src-a", _source("src-a")
    )
    for ref, edge_type, a, b, props in (
        (develops, "develops", org, prog, develops_props),
        (extracted, "extracted_from", org, source, _prov(span=span)),
    ):
        record = store.get_edge(ref)
        assert record.ref == ref
        assert record.properties == store.prepare_edge(edge_type, a, b, props)[1]
    assert store.get_edge(develops).properties["confidence"] == 0.75
    record = store.get_edge(same_as)
    assert record.ref == EdgeRef("same_as", org, other)  # canonical order
    assert record.properties == {
        "confidence": 0.875,
        "review_status": "unreviewed",
        "method": "embedding",
        "created_at": "2026-07-25T00:00:00.000000+00:00",
    }


def test_an_upsert_overwrites_the_node_but_keeps_its_embedding(
    store: StorageAdapter,
) -> None:
    width = store.schema.vector_dimension
    vector = _vector(width, i1=1.0)
    store.upsert_node(
        "Organisation", "o:bae",
        _entity("o:bae", nationality="UK", embedding=vector),
    )
    ref = store.upsert_node(
        "Organisation", "o:bae", _entity("o:bae", provenance=_prov("src-b"))
    )
    stored = store.get_node(ref).properties
    assert stored["source_id"] == "src-b"
    assert stored["nationality"] is None  # not left behind under src-b
    assert stored["embedding"] == vector  # computed separately; kept
    store.upsert_node("Organisation", "o:bae", _entity("o:bae", embedding=_vector(
        width, i2=1.0)))
    assert store.get_node(ref).properties["embedding"] == _vector(width, i2=1.0)


@pytest.mark.parametrize("field", [p.name for p in MANDATORY_PROVENANCE_PROPERTIES])
@pytest.mark.parametrize("fault", ["missing", "none", "empty"])
def test_a_write_missing_any_provenance_field_fails_and_writes_nothing(
    store: StorageAdapter, field: str, fault: str
) -> None:
    def broken(props: dict[str, Any]) -> dict[str, Any]:
        props = dict(props)
        if fault == "missing":
            del props[field]
        elif fault == "none":
            props[field] = None
        else:
            props[field] = [] if field == "section_refs" else "  "
        return props

    with pytest.raises(SchemaViolation, match=field):
        store.upsert_node("Programme", "p:x", broken(_entity("p:x")))
    assert store.get_node(NodeRef("Programme", "p:x")) is None

    org = store.upsert_node("Organisation", "o:a", _entity("o:a"))
    prog = store.upsert_node("Programme", "p:a", _entity("p:a"))
    source = store.upsert_node(SOURCE_LABEL, "src-a", _source("src-a"))
    for edge_type, a, b in (
        ("develops", org, prog),
        ("extracted_from", prog, source),
        ("mentions", source, prog),
    ):
        with pytest.raises(SchemaViolation, match=field):
            store.upsert_edge(edge_type, a, b, broken(_prov()))
        assert store.trace_back(EdgeRef(edge_type, a, b)) == []
    assert store.trace_back(prog) == []


def test_an_edge_to_a_missing_node_raises_and_writes_nothing(
    store: StorageAdapter,
) -> None:
    org, prog = NodeRef("Organisation", "o:bae"), NodeRef("Programme", "p:asgard")
    with pytest.raises(MissingNodeError, match="o:bae"):
        store.upsert_edge("develops", org, prog, _prov())
    store.upsert_node(org.label, org.id, _entity(org.id))
    with pytest.raises(MissingNodeError, match="p:asgard"):
        store.upsert_edge("develops", org, prog, _prov())
    assert store.traverse(org) == []
    store.upsert_node(prog.label, prog.id, _entity(prog.id))
    store.upsert_edge("develops", org, prog, _prov())
    assert len(store.traverse(org)) == 1


def test_same_as_is_a_pairwise_edge_and_never_fuses_the_nodes(
    store: StorageAdapter,
) -> None:
    """Both nodes keep their own properties, edges and provenance after a
    confirmed ``same_as``; the claim is one edge with a confidence."""
    a = store.upsert_node("Programme", "p:asgard", _entity("p:asgard", "ASGARD"))
    b = store.upsert_node(
        "Programme", "p:asgard-2",
        _entity("p:asgard-2", "Project ASGARD", provenance=_prov("src-b")),
    )
    org = store.upsert_node("Organisation", "o:bae", _entity("o:bae"))
    store.upsert_edge("develops", org, b, _prov("src-b"))
    write_same_as(
        store, b, a, confidence=0.97, method="human", review_status="confirmed"
    )

    assert store.get_node(a).properties["name"] == "ASGARD"
    assert store.get_node(b).properties["name"] == "Project ASGARD"
    assert store.get_node(b).properties["source_id"] == "src-b"
    (path,) = store.traverse(a, edge_types=["same_as"])
    assert path.end.ref == b
    assert path.steps[0].edge.properties["confidence"] == 0.97
    # a's own neighbourhood is unchanged: b's develops edge is one hop further.
    assert [p.end.ref for p in store.traverse(a, edge_types=["develops"])] == []
    two_hops = store.traverse(a, max_hops=2, target_labels=["Organisation"])
    assert [[s.edge.ref.type for s in p.steps] for p in two_hops] == [
        ["same_as", "develops"]
    ]


# -- traversal ----------------------------------------------------------------


def _fixture_graph(store: StorageAdapter) -> dict[str, NodeRef]:
    """BAE develops ASGARD, ASGARD is part of FADS, FADS part of ASGARD-2,
    and ASGARD-2 same_as ASGARD (a cycle through four nodes)."""
    nodes = {
        "bae": store.upsert_node("Organisation", "o:bae", _entity("o:bae")),
        "asgard": store.upsert_node("Programme", "p:asgard", _entity("p:asgard")),
        "fads": store.upsert_node("Programme", "p:fads", _entity("p:fads")),
        "asgard2": store.upsert_node("Programme", "p:asgard-2", _entity("p:asgard-2")),
    }
    store.upsert_edge("develops", nodes["bae"], nodes["asgard"], _prov())
    store.upsert_edge("part_of", nodes["asgard"], nodes["fads"], _prov())
    store.upsert_edge("part_of", nodes["fads"], nodes["asgard2"], _prov())
    write_same_as(store, nodes["asgard"], nodes["asgard2"], confidence=0.6,
                  method="embedding")
    return nodes


def _shape(path: TraversalResult) -> tuple[tuple[str, str], ...]:
    return tuple((s.edge.ref.type, s.node.ref.id) for s in path.steps)


def test_a_two_hop_traversal_returns_the_expected_paths(store: StorageAdapter) -> None:
    n = _fixture_graph(store)
    out = store.traverse(n["bae"], max_hops=2, direction=Direction.OUTGOING)
    assert sorted(map(_shape, out)) == [
        (("develops", "p:asgard"),),
        (("develops", "p:asgard"), ("part_of", "p:fads")),
        (("develops", "p:asgard"), ("same_as", "p:asgard-2")),
    ]
    (path,) = [p for p in out if p.hops == 2 and p.steps[1].edge.ref.type == "part_of"]
    assert path.node_refs() == (n["bae"], n["asgard"], n["fads"])
    assert path.steps[1].edge.properties["supporting_quote"] == (
        "BAE Systems is developing ASGARD"
    )
    assert path.steps[1].node.properties["source_id"] == "src-a"

    back = store.traverse(
        n["fads"], max_hops=2, direction=Direction.INCOMING,
        target_labels=["Organisation"],
    )
    assert list(map(_shape, back)) == [
        (("part_of", "p:asgard"), ("develops", "o:bae"))
    ]
    # Each step records the edge as stored, whichever way it was followed.
    assert back[0].steps[0].edge.ref.source == n["asgard"]


def test_a_cycle_never_yields_a_path_that_revisits_a_node(
    store: StorageAdapter,
) -> None:
    n = _fixture_graph(store)
    paths = store.traverse(n["bae"], max_hops=5)
    assert paths and all(p.is_simple() for p in paths)
    assert sorted(map(_shape, paths)) == [
        (("develops", "p:asgard"),),
        (("develops", "p:asgard"), ("part_of", "p:fads")),
        (("develops", "p:asgard"), ("part_of", "p:fads"), ("part_of", "p:asgard-2")),
        (("develops", "p:asgard"), ("same_as", "p:asgard-2")),
        (("develops", "p:asgard"), ("same_as", "p:asgard-2"), ("part_of", "p:fads")),
    ]
    assert [p.hops for p in paths] == [1, 2, 2, 3, 3]  # shortest first
    limited = store.traverse(n["bae"], max_hops=5, limit=2)
    assert [p.hops for p in limited] == [1, 2]
    assert {_shape(p) for p in limited} <= {_shape(p) for p in paths}


def _reference_paths(
    edges: list[EdgeRef],
    start: NodeRef,
    *,
    edge_types: list[str] | None,
    max_hops: int,
    direction: Direction,
    target_labels: list[str] | None,
) -> list[tuple]:
    """Every simple path by brute force, each as ((edge ref, node), ...)."""
    found: list[tuple] = []

    def extend(visited: list[NodeRef], steps: list[tuple]) -> None:
        if steps and (target_labels is None or visited[-1].label in target_labels):
            found.append(tuple(steps))
        if len(steps) == max_hops:
            return
        here = visited[-1]
        for edge in edges:
            if edge_types is not None and edge.type not in edge_types:
                continue
            ahead = []
            if direction is not Direction.INCOMING and edge.source == here:
                ahead.append(edge.target)
            if direction is not Direction.OUTGOING and edge.target == here:
                ahead.append(edge.source)
            for node in ahead:
                if node not in visited:
                    extend([*visited, node], [*steps, (edge, node)])

    extend([start], [])
    return found


def _signature(path: TraversalResult) -> tuple:
    return tuple((s.edge.ref, s.node.ref) for s in path.steps)


@pytest.mark.parametrize("seed", range(6))
def test_traversal_matches_a_brute_force_reference(
    store: StorageAdapter, seed: int
) -> None:
    """Property: on a random small graph (parallel edges, self-loops,
    ``same_as``, Source edges), every query returns exactly the simple paths a
    brute-force search finds, each edge as stored, shortest first, and
    ``limit`` keeps a shortest-first prefix."""
    rng = random.Random(seed)
    schema = store.schema
    nodes = [NodeRef("Organisation", f"o{i}") for i in range(3)]
    nodes += [NodeRef("Programme", f"p{i}") for i in range(3)]
    nodes += [NodeRef("Capability", "c0"), NodeRef(SOURCE_LABEL, "s0")]
    for node in nodes:
        props = _source(node.id) if node.label == SOURCE_LABEL else _entity(node.id)
        store.upsert_node(node.label, node.id, props)

    stored: dict[EdgeRef, None] = {}
    candidates = [
        (edge.type, a, b)
        for edge in schema.edges
        for a, b in itertools.product(nodes, nodes)
        if (a.label, b.label) in edge.pairs()
        and not (edge.type == "same_as" and a == b)
    ]
    for edge_type, a, b in rng.sample(candidates, 14):
        for _ in range(rng.choice((1, 1, 2))):  # sometimes a parallel edge
            if edge_type == "same_as":
                ref = write_same_as(store, a, b, confidence=0.5, method="alias")
            else:
                ref = store.upsert_edge(
                    edge_type, a, b, _prov("s0", rng.choice(("c1", "c2")))
                )
            stored[ref] = None
    edges = list(stored)
    types = sorted({e.type for e in edges})
    labels = sorted({n.label for n in nodes})

    for _ in range(25):
        start = rng.choice(nodes)
        query = {
            "edge_types": rng.choice((None, rng.sample(types, rng.randint(1, 2)))),
            "max_hops": rng.randint(1, 3),
            "direction": rng.choice(list(Direction)),
            "target_labels": rng.choice((None, rng.sample(labels, 2))),
        }
        expected = _reference_paths(edges, start, **query)
        got = store.traverse(start, **query)
        assert sorted(map(_signature, got), key=repr) == sorted(expected, key=repr), (
            start, query
        )
        assert all(p.start == start and p.is_simple() for p in got)
        assert [p.hops for p in got] == sorted(p.hops for p in got)
        limit = rng.randint(1, 4)
        kept = store.traverse(start, limit=limit, **query)
        assert len(kept) == min(limit, len(expected))
        assert {_signature(p) for p in kept} <= set(expected)
        assert [p.hops for p in kept] == sorted(len(e) for e in expected)[:limit]


# -- vector similarity --------------------------------------------------------


def test_similarity_finds_the_nearest_stub_vector_at_the_configured_width(
    store: StorageAdapter,
) -> None:
    width = store.schema.vector_dimension
    assert width == 3072
    for i in range(5):
        store.upsert_node(
            "Programme", f"p:{i}",
            _entity(f"p:{i}", embedding=_vector(width, **{f"i{i * 700}": 1.0})),
        )
    store.upsert_node("Programme", "p:none", _entity("p:none"))  # no embedding
    store.upsert_node(  # same vector, other label: never returned
        "Capability", "c:0", _entity("c:0", embedding=_vector(width, i1400=1.0))
    )
    query = _vector(width, i1400=1.0, i2100=0.25)

    hits = store.similar_nodes("Programme", query, limit=3)
    assert [h.node.ref.id for h in hits[:2]] == ["p:2", "p:3"]
    assert len(hits) == 3 and hits[2].score == pytest.approx(0.0, abs=1e-6)
    assert hits[0].score == pytest.approx(1 / math.sqrt(1.0625), abs=1e-6)
    assert hits[1].score == pytest.approx(0.25 / math.sqrt(1.0625), abs=1e-6)
    assert hits[0].node.properties["embedding"] == _vector(width, i1400=1.0)
    assert all(h.node.ref.label == "Programme" for h in hits)
    assert [h.node.ref.id for h in store.similar_nodes(
        "Programme", query, limit=10, min_score=0.2)] == ["p:2", "p:3"]
    assert "p:none" not in {h.node.ref.id for h in store.similar_nodes(
        "Programme", query, limit=10)}

    # Re-embedding an existing node is reflected at once (the update an
    # HNSW index in this Kùzu release cannot take; see the module docstring).
    store.upsert_node(
        "Programme", "p:4", _entity("p:4", embedding=_vector(width, i100=1.0))
    )
    assert store.similar_nodes("Programme", _vector(width, i100=1.0), limit=1)[
        0
    ].node.ref.id == "p:4"
    with pytest.raises(ValueError, match="dimensions"):
        store.similar_nodes("Programme", [1.0] * 8)
    with pytest.raises(ValueError, match="limit"):
        store.similar_nodes("Programme", query, limit=0)


@pytest.mark.parametrize("seed", range(3))
def test_similarity_ranks_exactly_as_a_direct_cosine(
    store: StorageAdapter, seed: int
) -> None:
    """Property: over random 3072-wide vectors, the ranking and scores match a
    float64 cosine computed here, to float32 precision."""
    rng = random.Random(seed)
    width = store.schema.vector_dimension
    vectors = {
        f"p:{i}": [rng.uniform(-1, 1) for _ in range(width)] for i in range(15)
    }
    for node_id, vector in vectors.items():
        store.upsert_node("Programme", node_id, _entity(node_id, embedding=vector))

    def cosine(u: list[float], v: list[float]) -> float:
        dot = sum(a * b for a, b in zip(u, v, strict=True))
        return dot / math.sqrt(sum(a * a for a in u) * sum(b * b for b in v))

    for _ in range(3):
        query = [rng.uniform(-1, 1) for _ in range(width)]
        ranked = sorted(vectors, key=lambda k: cosine(vectors[k], query), reverse=True)
        hits = store.similar_nodes("Programme", query, limit=5)
        assert [h.node.ref.id for h in hits] == ranked[:5]
        for hit in hits:
            assert hit.score == pytest.approx(
                cosine(vectors[hit.node.ref.id], query), abs=1e-5
            )


# -- trace-back ---------------------------------------------------------------


def test_trace_back_returns_every_extraction_with_its_source(
    store: StorageAdapter,
) -> None:
    sources = [
        store.upsert_node(SOURCE_LABEL, s, _source(s)) for s in ("src-a", "src-b")
    ]
    org = store.upsert_node("Organisation", "o:bae", _entity("o:bae"))
    prog = store.upsert_node("Programme", "p:asgard", _entity("p:asgard"))
    later = _prov("src-b", "chunk-9", quote="ASGARD, led by BAE",
                  at="2026-07-26T09:00:00Z")
    earlier = _prov("src-a", "chunk-1", at="2026-07-24T09:00:00Z")
    store.upsert_edge("extracted_from", org, sources[1], later)
    store.upsert_edge("extracted_from", org, sources[0], earlier)

    hits = store.trace_back(org)
    assert [h.source.ref for h in hits] == sources  # ordered by extracted_at
    assert [h.provenance["supporting_quote"] for h in hits] == [
        earlier["supporting_quote"], later["supporting_quote"]
    ]
    for hit in hits:
        assert set(hit.provenance) == {p.name for p in PROVENANCE_PROPERTIES}
        assert hit.subject == org
        assert hit.source.properties["url"].startswith("https://example.org/")

    one = store.upsert_edge("develops", org, prog, earlier)
    two = store.upsert_edge("develops", org, prog, later)
    (hit,) = store.trace_back(one)
    assert hit.subject == one and hit.provenance["chunk_id"] == "chunk-1"
    assert hit.source.ref == sources[0]
    generic = store.trace_back(EdgeRef("develops", org, prog))
    assert [h.subject for h in generic] == [one, two]
    assert [h.source.ref for h in generic] == sources

    assert store.trace_back(sources[0]) == []  # a Source is the provenance
    other = store.upsert_node("Organisation", "o:bae-2", _entity("o:bae-2"))
    write_same_as(store, org, other, confidence=0.4, method="alias")
    assert store.trace_back(EdgeRef("same_as", org, other)) == []
    with pytest.raises(ValueError):
        store.trace_back(EdgeRef("develops", org, prog, source_id="src-a"))
    with pytest.raises(ValueError, match="not a permitted"):
        store.trace_back(EdgeRef("develops", prog, org))


def test_a_relation_whose_source_node_is_absent_traces_back_with_none(
    store: StorageAdapter,
) -> None:
    """The provenance block still names the document; ``source`` is ``None``
    only because that ``Source`` node was never written."""
    org = store.upsert_node("Organisation", "o:bae", _entity("o:bae"))
    prog = store.upsert_node("Programme", "p:asgard", _entity("p:asgard"))
    ref = store.upsert_edge("develops", org, prog, _prov("src-unwritten"))
    (hit,) = store.trace_back(ref)
    assert hit.source is None
    assert hit.provenance["source_id"] == "src-unwritten"


# -- stored state matches the reference model ---------------------------------


_UNIVERSE = (
    NodeRef("Organisation", "o0"),
    NodeRef("Organisation", "o1"),
    NodeRef("Programme", "p0"),
    NodeRef("Programme", "p1"),
    NodeRef(SOURCE_LABEL, "s0"),
)


def _random_op(rng: random.Random, width: int):
    """One random write, valid or not, as (name, args) for either adapter."""
    entities = [n for n in _UNIVERSE if n.label != SOURCE_LABEL]
    kind = rng.choice(("node", "node", "edge", "edge", "same_as", "source"))
    if kind == "source":
        return ("upsert_node", (SOURCE_LABEL, "s0", _source("s0")))
    if kind == "node":
        node = rng.choice(entities)
        span = Span("parse-1", 3, 9) if rng.random() < 0.3 else None
        props = _entity(
            node.id, rng.choice((node.id, f"{node.id} renamed")),
            provenance=_prov(
                rng.choice(("s0", "s1")), rng.choice(("c1", "c2")), span=span,
                at=rng.choice(("2026-07-24T09:00:00Z", "2026-07-25T09:00:00+02:00")),
            ),
        )
        if node.label == "Organisation" and rng.random() < 0.5:
            props["nationality"] = rng.choice(("UK", "FR"))
        if rng.random() < 0.3:
            props["embedding"] = _vector(width, i0=rng.choice((0.5, 1.0)))
        if rng.random() < 0.1:
            del props[rng.choice(MANDATORY_PROVENANCE_PROPERTIES).name]
        return ("upsert_node", (node.label, node.id, props))
    if kind == "same_as":
        label = rng.choice(("Organisation", "Programme"))
        pair = [NodeRef(label, f"{label[0].lower()}{i}") for i in (0, 1)]
        rng.shuffle(pair)
        status = rng.choice((None, "confirmed", "rejected"))
        return ("write_same_as", (*pair, rng.choice((0.25, 0.5)), status))
    edge_type, source, target = rng.choice((
        ("develops", NodeRef("Organisation", rng.choice(("o0", "o1"))),
         NodeRef("Programme", rng.choice(("p0", "p1")))),
        ("part_of", NodeRef("Programme", "p0"), NodeRef("Programme", "p1")),
        ("extracted_from", rng.choice(entities), NodeRef(SOURCE_LABEL, "s0")),
    ))
    props = _prov("s0", rng.choice(("c1", "c2")))
    if edge_type == "develops" and rng.random() < 0.5:
        props["confidence"] = rng.choice((0.25, 1.0))
    return ("upsert_edge", (edge_type, source, target, props))


def _apply(adapter: StorageAdapter, op: tuple) -> object:
    name, args = op
    try:
        if name == "write_same_as":
            a, b, confidence, status = args
            return write_same_as(
                adapter, a, b, confidence=confidence, method="llm",
                review_status=status, created_at="2026-07-25T00:00:00Z",
            )
        return getattr(adapter, name)(*args)
    except (SchemaViolation, MissingNodeError, ValueError) as exc:
        return type(exc)


def _edge_keys() -> list[EdgeRef]:
    keys = [EdgeRef("same_as", NodeRef(lb, f"{lb[0].lower()}0"),
                    NodeRef(lb, f"{lb[0].lower()}1"))
            for lb in ("Organisation", "Programme")]
    for chunk in ("c1", "c2"):
        for o, p in itertools.product(("o0", "o1"), ("p0", "p1")):
            keys.append(EdgeRef("develops", NodeRef("Organisation", o),
                                NodeRef("Programme", p), "s0", chunk))
        keys.append(EdgeRef("part_of", NodeRef("Programme", "p0"),
                            NodeRef("Programme", "p1"), "s0", chunk))
        for node in _UNIVERSE[:4]:
            keys.append(EdgeRef("extracted_from", node, NodeRef(SOURCE_LABEL, "s0"),
                                "s0", chunk))
    return keys


def _assert_same_state(model: StubAdapter, store: StorageAdapter) -> None:
    for ref in _UNIVERSE:
        expected, got = model.get_node(ref), store.get_node(ref)
        assert (expected is None) == (got is None), ref
        if expected is not None:
            names = store.schema.node(ref.label).property_names()
            assert {n: expected.properties.get(n) for n in names} == dict(
                got.properties
            ), ref
    for key in _edge_keys():
        expected, got = model.get_edge(key), store.get_edge(key)
        assert (expected is None) == (got is None), key
        if expected is not None:
            assert got.ref == expected.ref
            assert dict(got.properties) == dict(expected.properties), key


@pytest.mark.parametrize("seed", range(3))
def test_random_write_sequences_store_what_the_reference_model_stores(
    store: StorageAdapter, schema: GraphSchema, seed: int
) -> None:
    """Property: any sequence of node, edge and ``same_as`` writes (some of
    them invalid, some repeating a key) leaves Kùzu holding exactly what the
    in-memory stub holds, and refuses exactly the writes the stub refuses.
    Re-writing a key updates it in place; nothing is fused or lost."""
    rng = random.Random(seed)
    model = StubAdapter(schema, require_existing_endpoints=True)
    for step in range(60):
        op = _random_op(rng, schema.vector_dimension)
        assert _apply(model, op) == _apply(store, op), (step, op)
        if step % 30 == 29:
            _assert_same_state(model, store)
