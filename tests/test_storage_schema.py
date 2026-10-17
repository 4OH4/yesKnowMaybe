"""Schema definition tests — no database, no credentials.

These assert the properties the four non-negotiable constraints depend on:
provenance is mandatory and enforced, ``same_as`` is pairwise with no group id,
and every type comes from config/ontology.yaml rather than a list in code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dikms.config import ConfigError, ExtractionConfig
from dikms.models import AlignmentStatus, Provenance, SourceFormat, Span
from dikms.ontology import Attribute, EntityType, Ontology, RelationType
from dikms.sources import Source as SourceManifest
from dikms.storage import (
    MANDATORY_PROVENANCE_PROPERTIES,
    GraphSchema,
    PropertyType,
    SchemaViolation,
    build_schema,
    provenance_properties,
    schema_from_config,
    validate_properties,
)
from dikms.storage.schema import SOURCE_LABEL

ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "config" / "ontology.yaml"
MANDATORY_NAMES = tuple(p.name for p in MANDATORY_PROVENANCE_PROPERTIES)
SPAN_NAMES = ("span_parse_id", "span_start", "span_end")


def _fact_nodes(schema: GraphSchema):
    return [n for n in schema.nodes if n.label != SOURCE_LABEL]


def _fact_edges(schema: GraphSchema):
    """Edges that assert something extracted from a document."""
    return [e for e in schema.edges if e.type != "same_as"]


# -- derived from the ontology, not hardcoded -------------------------------


def test_node_labels_match_ontology_exactly(
    schema: GraphSchema, ontology: Ontology
) -> None:
    assert set(schema.node_labels()) == ontology.entity_names()
    assert schema.ontology_version == ontology.version


def test_edge_types_match_ontology_exactly(
    schema: GraphSchema, ontology: Ontology
) -> None:
    assert set(schema.edge_types()) == ontology.relation_names()


def test_source_is_a_first_class_node(schema: GraphSchema) -> None:
    source = schema.node(SOURCE_LABEL)
    assert source.system_managed
    # Source is the provenance *target*, so it carries source metadata rather
    # than a provenance block of its own.
    assert "authoritativeness" in source.property_names()
    assert "source_id" not in source.property_names()


def test_source_node_mirrors_the_source_manifest_model(schema: GraphSchema) -> None:
    """The Source node stores what data/sources.yaml already carries; a field
    added to `sources.Source` should not silently fail to reach the store."""
    model_fields = set(SourceManifest.__dataclass_fields__)
    node_names = set(schema.node(SOURCE_LABEL).property_names())
    # `fmt` is the model's Python attribute for the `format` column.
    assert model_fields - {"fmt"} <= node_names
    assert "format" in node_names


def test_schema_follows_a_different_ontology(config) -> None:
    """Types are read from the ontology object, so a different ontology gives a
    different schema — proof there is no hardcoded type list."""
    fake = Ontology(
        version=99,
        entity_types=[
            EntityType(
                name="Widget",
                description="",
                attributes=[Attribute("colour", "", ["red"])],
            ),
            EntityType(name=SOURCE_LABEL, description="", system_managed=True),
        ],
        relation_types=[
            RelationType(
                name="wobbles", description="", domain=["Widget"], range=["Widget"]
            ),
            RelationType(name="extracted_from", description="", structural=True),
        ],
    )
    built = build_schema(fake, config.require_embedding().dimension)
    assert set(built.node_labels()) == {"Widget", SOURCE_LABEL}
    assert set(built.edge_types()) == {"wobbles", "extracted_from"}
    assert built.ontology_version == 99
    colour = built.node("Widget").property("colour")
    assert colour is not None and colour.values == ("red",)


def test_ontology_attributes_become_node_properties(schema: GraphSchema) -> None:
    org = schema.node("Organisation")
    org_type = org.property("org_type")
    assert org_type is not None
    assert org_type.values == (
        "government",
        "military",
        "commercial",
        "academic",
        "ngo",
        "multinational",
        "other",
    )
    # Attributes are omitted when not evidenced, so never required.
    assert not org_type.required


# -- provenance (constraint 1) ---------------------------------------------


def test_every_fact_node_carries_mandatory_provenance(schema: GraphSchema) -> None:
    for node in _fact_nodes(schema):
        required = set(node.required_names())
        assert set(MANDATORY_NAMES) <= required, node.label


def test_every_fact_edge_carries_mandatory_provenance(schema: GraphSchema) -> None:
    for edge in _fact_edges(schema):
        required = set(edge.required_names())
        assert set(MANDATORY_NAMES) <= required, edge.type


def test_mandatory_provenance_mirrors_the_provenance_model() -> None:
    """The schema must not invent a parallel vocabulary for provenance."""
    model_fields = set(Provenance.__dataclass_fields__)
    assert set(MANDATORY_NAMES) <= model_fields


def test_span_fields_are_optional_and_alignment_status_is_recorded(
    schema: GraphSchema,
) -> None:
    node = schema.node("Programme")
    for name in SPAN_NAMES:
        prop = node.property(name)
        assert prop is not None and not prop.required, name
    status = node.property("alignment_status")
    # Required with a controlled vocabulary: a failed alignment is *recorded*,
    # never silently absent (ARCHITECTURE.md, span-level provenance).
    assert status is not None and status.required
    assert status.values == tuple(s.value for s in AlignmentStatus)


def test_a_fact_cannot_be_written_without_its_provenance(schema: GraphSchema) -> None:
    node = schema.node("Programme")
    complete = {
        "id": "programme:asgard",
        "name": "ASGARD",
        "source_id": "mod-digital-strategy-2021",
        "chunk_id": "chunk-3",
        "section_refs": ["p. 12"],
        "supporting_quote": "ASGARD will deliver...",
        "extraction_model": "gemini-2.5-flash",
        "extracted_at": "2026-07-24T09:00:00+00:00",
        "alignment_status": "pending",
    }
    validate_properties(node, complete)  # baseline: this is a valid write

    for dropped in MANDATORY_NAMES:
        partial = {k: v for k, v in complete.items() if k != dropped}
        with pytest.raises(SchemaViolation, match=dropped):
            validate_properties(node, partial)

    nulled = dict(complete, chunk_id=None)
    with pytest.raises(SchemaViolation, match="chunk_id"):
        validate_properties(node, nulled)


def test_extracted_from_points_at_source(schema: GraphSchema) -> None:
    edge = schema.edge("extracted_from")
    assert edge.structural
    assert edge.target_labels == (SOURCE_LABEL,)
    assert SOURCE_LABEL not in edge.source_labels
    assert set(MANDATORY_NAMES) <= set(edge.required_names())


def test_provenance_properties_round_trip_without_a_span() -> None:
    prov = Provenance(
        source_id="s1",
        chunk_id="c1",
        section_refs=["Section 2"],
        supporting_quote="quote",
        extraction_model="gemini-2.5-flash",
        extracted_at="2026-07-24T09:00:00+00:00",
    )
    props = provenance_properties(prov)
    assert props["source_id"] == "s1"
    assert props["section_refs"] == ["Section 2"]
    assert props["alignment_status"] == AlignmentStatus.PENDING.value
    assert props["span_parse_id"] is None
    assert props["span_start"] is None and props["span_end"] is None


def test_provenance_properties_flatten_a_verified_span() -> None:
    prov = Provenance(
        source_id="s1",
        chunk_id="c1",
        section_refs=[],
        supporting_quote="quote",
        extraction_model="gemini-2.5-flash",
        alignment_status=AlignmentStatus.ALIGNED,
        span=Span(parse_id="s1@pypdf#abc", start=100, end=140),
    )
    props = provenance_properties(prov)
    assert props["alignment_status"] == "aligned"
    assert props["span_parse_id"] == "s1@pypdf#abc"
    assert (props["span_start"], props["span_end"]) == (100, 140)
    # The flattened keys are exactly what the schema declares.
    assert set(SPAN_NAMES) <= set(props)


# -- identity (constraint 2) -----------------------------------------------


def test_same_as_is_pairwise_and_confidence_weighted(schema: GraphSchema) -> None:
    edge = schema.edge("same_as")
    names = set(edge.property_names())
    assert "confidence" in names
    confidence = edge.property("confidence")
    assert confidence is not None and confidence.type is PropertyType.FLOAT
    review = edge.property("review_status")
    assert review is not None and review.required
    assert set(review.values) == {"unreviewed", "confirmed", "rejected"}
    # Endpoints are two distinct nodes; Source is not an identity candidate.
    assert SOURCE_LABEL not in edge.source_labels
    assert SOURCE_LABEL not in edge.target_labels


def test_no_group_or_cluster_id_anywhere_in_the_schema(schema: GraphSchema) -> None:
    """Entity resolution must never store a merged identity group
    (DECISIONS.md: identity evaluated per-edge, not transitive closure)."""
    banned = ("group", "cluster", "canonical", "merged", "merge")
    for node in schema.nodes:
        for name in node.property_names():
            assert not any(b in name.lower() for b in banned), f"{node.label}.{name}"
    for edge in schema.edges:
        for name in edge.property_names():
            assert not any(b in name.lower() for b in banned), f"{edge.type}.{name}"


def test_same_as_endpoints_are_same_type_pairs_only(schema: GraphSchema) -> None:
    """Resolution compares an entity against existing entities of the same type
    (ARCHITECTURE.md). Expanding the ontology's `*` wildcard across every label
    would also cost one edge definition per pair in Spanner Graph."""
    pairs = schema.edge("same_as").pairs()
    assert pairs, "same_as has no permitted endpoint pairs"
    assert all(src == dst for src, dst in pairs)
    entity_labels = {label for label in schema.node_labels() if label != SOURCE_LABEL}
    assert {src for src, _ in pairs} == entity_labels


def test_same_as_carries_no_provenance_block(schema: GraphSchema) -> None:
    """A same_as edge is produced by resolution, not extracted from a document,
    so it must not pretend to have document provenance."""
    names = set(schema.edge("same_as").property_names())
    assert not (set(MANDATORY_NAMES) & names)


# -- vector channel ---------------------------------------------------------


def test_vector_width_comes_from_config(schema: GraphSchema, config) -> None:
    dimension = config.require_embedding().dimension
    assert schema.vector_dimension == dimension
    vector_nodes = schema.vector_nodes()
    assert vector_nodes, "no node carries the vector channel"
    for node in vector_nodes:
        prop = node.vector_property()
        assert prop is not None
        assert prop.dimension == dimension
        # Stub-only in M1: real Vertex embeddings need credentials (M2).
        assert not prop.required


def test_schema_from_config_reads_the_width_from_the_config_file(
    tmp_path, config
) -> None:
    """The one function that wires embedding.dimension into the schema."""
    path = tmp_path / "extraction.yaml"
    path.write_text(
        'extraction:\n  model: "m"\nembedding:\n  model: "e"\n  dimension: 64\n',
        encoding="utf-8",
    )
    built = schema_from_config(ONTOLOGY_PATH, ExtractionConfig.load(path))
    assert built.vector_dimension == 64

    # And the real config file gives the real width.
    assert (
        schema_from_config(ONTOLOGY_PATH, config).vector_dimension
        == config.require_embedding().dimension
    )


def test_schema_from_config_refuses_a_config_with_no_embedding_block(
    tmp_path,
) -> None:
    path = tmp_path / "extraction.yaml"
    path.write_text('extraction:\n  model: "m"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="never hardcoded"):
        schema_from_config(ONTOLOGY_PATH, ExtractionConfig.load(path))


def test_build_schema_honours_a_different_dimension(ontology: Ontology) -> None:
    built = build_schema(ontology, 768)
    assert built.vector_dimension == 768
    assert built.node("Programme").vector_property().dimension == 768


def test_wrong_width_vector_is_rejected(schema: GraphSchema) -> None:
    node = schema.node("Programme")
    props = {
        "id": "x",
        "name": "X",
        "source_id": "s",
        "chunk_id": "c",
        "section_refs": [],
        "supporting_quote": "q",
        "extraction_model": "m",
        "extracted_at": "t",
        "alignment_status": "pending",
        "embedding": [0.0] * (schema.vector_dimension - 1),
    }
    with pytest.raises(SchemaViolation, match="dimensions"):
        validate_properties(node, props)


def test_build_schema_rejects_a_non_positive_dimension(ontology: Ontology) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_schema(ontology, 0)


# -- general validation -----------------------------------------------------


def test_unknown_properties_are_rejected(schema: GraphSchema) -> None:
    with pytest.raises(SchemaViolation, match="unknown"):
        validate_properties(schema.node(SOURCE_LABEL), {"nonsense": 1})


def test_controlled_vocabulary_is_enforced(schema: GraphSchema) -> None:
    node = schema.node("Programme")
    props = {
        "id": "x",
        "name": "X",
        "source_id": "s",
        "chunk_id": "c",
        "section_refs": [],
        "supporting_quote": "q",
        "extraction_model": "m",
        "extracted_at": "t",
        "alignment_status": "definitely-aligned",
    }
    with pytest.raises(SchemaViolation, match="alignment_status"):
        validate_properties(node, props)


def test_property_types_are_enforced(schema: GraphSchema) -> None:
    """Both backends have typed columns, so a mistyped write is caught here
    rather than differently by each driver."""
    node = schema.node("Programme")
    base = {
        "id": "x",
        "name": "X",
        "source_id": "s",
        "chunk_id": "c",
        "section_refs": ["p. 1"],
        "supporting_quote": "q",
        "extraction_model": "m",
        "extracted_at": "t",
        "alignment_status": "pending",
    }
    validate_properties(node, base)

    for key, bad in (
        ("name", 42),
        ("section_refs", "p. 1"),  # a bare string is not a list of strings
        ("section_refs", [1, 2]),
        ("span_start", "12"),
        ("span_start", True),  # bool is not an int here
        ("embedding", ["a"] * schema.vector_dimension),
    ):
        with pytest.raises(SchemaViolation, match=key):
            validate_properties(node, dict(base, **{key: bad}))


def test_property_names_are_unique_per_definition(schema: GraphSchema) -> None:
    """An ontology attribute colliding with a provenance field would shadow it."""
    for node in schema.nodes:
        names = node.property_names()
        assert len(names) == len(set(names)), node.label
    for edge in schema.edges:
        names = edge.property_names()
        assert len(names) == len(set(names)), edge.type


def test_an_unmodelled_structural_relation_fails_loudly(
    ontology: Ontology, config
) -> None:
    """Structural relations are handled by name; a new one must not silently
    become an ordinary provenanced relation."""
    extended = Ontology(
        version=ontology.version,
        entity_types=list(ontology.entity_types),
        relation_types=[
            *ontology.relation_types,
            RelationType(name="derived_from", description="", structural=True),
        ],
    )
    with pytest.raises(ValueError, match="derived_from"):
        build_schema(extended, config.require_embedding().dimension)


def test_defaults_are_declared_for_the_recorded_status_fields(
    schema: GraphSchema,
) -> None:
    """Both backends support a column DEFAULT; the schema states it rather than
    leaving it implicit in models.Provenance."""
    assert schema.node("Programme").property("alignment_status").default == "pending"
    assert schema.edge("same_as").property("review_status").default == "unreviewed"


def test_source_format_vocabulary_comes_from_the_model(schema: GraphSchema) -> None:
    prop = schema.node(SOURCE_LABEL).property("format")
    assert prop is not None
    assert prop.values == tuple(f.value for f in SourceFormat)


def test_edge_endpoint_pairs_follow_the_ontology(
    schema: GraphSchema, ontology: Ontology
) -> None:
    """Every substantive relation's endpoint pairs are exactly the ontology's
    domain x range: derived from the loaded file, so an ontology bump cannot
    leave this test asserting an older version's shape."""
    structural = {r.name for r in ontology.relation_types if r.structural}
    for relation in ontology.relation_types:
        if relation.name in structural:
            continue
        edge = schema.edge(relation.name)
        assert edge.source_labels == tuple(relation.domain), relation.name
        assert edge.target_labels == tuple(relation.range), relation.name
        assert set(edge.pairs()) == {
            (s, t) for s in relation.domain for t in relation.range
        }, relation.name

    # And the direction matters: a pair outside domain x range is refused.
    develops = next(r for r in ontology.relation_types if r.name == "develops")
    for target in develops.range:
        if target not in develops.domain:
            assert (target, develops.domain[0]) not in schema.edge("develops").pairs()


def test_mentions_runs_from_source_to_entities(schema: GraphSchema) -> None:
    mentions = schema.edge("mentions")
    assert mentions.source_labels == (SOURCE_LABEL,)
    assert SOURCE_LABEL not in mentions.target_labels
    assert mentions.structural
