"""Declarative, backend-agnostic description of the knowledge graph schema.

This module is the single source of truth for *what* the store holds: node
labels, edge types, property names and types. It contains no storage code and
imports no database driver — a backend takes a :class:`GraphSchema` and
realises it in its own DDL.

Everything type-related is derived from the loaded ontology
(``config/ontology.yaml``), never from a hardcoded list, so an ontology change
flows through automatically and this module can never quietly widen the
ontology (CLAUDE.md constraint 3). The vector width is read from
``embedding.dimension`` in ``config/extraction.yaml`` for the same reason.

Provenance (CLAUDE.md constraint 1)
-----------------------------------
Every fact-bearing node and edge carries the provenance block, using exactly
the field names of :class:`dikms.models.Provenance` — no parallel vocabulary:

* Mandatory, non-nullable and non-empty: ``source_id``, ``chunk_id``,
  ``section_refs``, ``supporting_quote``, ``extraction_model``,
  ``extracted_at``. A fact cannot be represented in this schema without its
  document/section provenance. ``extracted_at`` must parse as ISO 8601 with a
  UTC offset and is stored in one normalised UTC form.
* Exact-span fields, present only when alignment verified them:
  ``span_parse_id``, ``span_start``, ``span_end`` (nullable). ``Span`` is
  flattened into three properties because neither backend stores nested
  records on a property. They are all-or-nothing, set exactly when
  ``alignment_status`` is ``aligned``, with ``0 <= span_start <= span_end``.
* ``alignment_status`` belongs to the span group but is itself **required**,
  defaulting to ``pending``. ``ARCHITECTURE.md`` requires a failed alignment to
  be *recorded* rather than silently absent — "which facts have exact-span
  backing and which don't" is only answerable if the status is always there —
  and :class:`dikms.models.Provenance` likewise always carries a value.

How a fact reaches its ``Source`` depends on what the fact is:

* **An entity** (a node) has an ``extracted_from`` edge to its ``Source``
  for each extraction, carrying that extraction's provenance block.
* **A relation** (an edge) carries its provenance block as its own
  properties and traces back through its own ``source_id``/``chunk_id``.
  It has no ``extracted_from`` edge: a property-graph edge cannot start
  from another edge, in Kùzu or Spanner Graph, so ``extracted_from``'s
  source labels are the entity labels only, narrower than the ontology's
  ``domain: ["*"]`` and its "any extracted entity/relation" description.

Identity (CLAUDE.md constraint 2)
---------------------------------
``same_as`` is a pairwise edge carrying ``confidence`` and ``review_status``.
There is deliberately no cluster/group-id property anywhere in this schema, on
nodes or edges — transitive closure must remain a query-time decision
(``DECISIONS.md``: identity evaluated per-edge).

Expressibility in both backends
-------------------------------
Each schema element maps as follows; nothing here is available in only one
backend.

===================== ============================== ==============================
Schema element        Kùzu                           Spanner Graph
===================== ============================== ==============================
Node label            ``CREATE NODE TABLE <Label>``  Input table + ``NODE TABLES``
                                                     in the property graph DDL
Edge type             ``CREATE REL TABLE <Type>``    Interleaved/edge input table +
                      with one or more ``FROM..TO``  ``EDGE TABLES`` with
                      pairs (multi-pair rel tables)  ``SOURCE KEY``/``DESTINATION KEY``
Primary key ``id``    ``PRIMARY KEY`` on the node    ``PRIMARY KEY`` on the input
                      table                          table
STRING / INT64 /      ``STRING`` / ``INT64`` /       ``STRING(MAX)`` / ``INT64`` /
DOUBLE / BOOL         ``DOUBLE`` / ``BOOL``          ``FLOAT64`` / ``BOOL``
TIMESTAMP             stored as ``STRING``, one      stored as ``STRING(MAX)``, the
                      fixed-width UTC ISO 8601 form  same form — so string order is
                      (:func:`normalise_timestamp`)  chronological in both
STRING_LIST           ``STRING[]``                   ``ARRAY<STRING(MAX)>``
                      (``section_refs``)             (``section_refs``)
Required property     **no DDL equivalent** — Kùzu   ``NOT NULL``
                      has no ``NOT NULL`` column
                      constraint, so this is
                      enforced by
                      :func:`validate_properties`
Property default      column ``DEFAULT``             column ``DEFAULT``
Enum-valued property  ``STRING`` + validation here   ``STRING(MAX)`` + validation
                      (neither backend has a native enum type)
Vector property       ``FLOAT[<dim>]``, searched by  ``ARRAY<FLOAT32>`` +
                      an exact cosine scan (0.11.3's ``VECTOR INDEX`` with
                      HNSW index cannot be updated;  ``COSINE`` distance
                      see ``kuzu_backend``)
Numeric range         validation here (Kùzu has no   validation here (no ``CHECK``
(e.g. ``confidence``) ``CHECK`` constraint)          is declared, to match)
Arbitrary edge props  properties on a REL table      columns on the edge input table
===================== ============================== ==============================

Two asymmetries worth stating plainly rather than papering over:

* **Required-ness is not enforceable in Kùzu's DDL.** Kùzu has no ``NOT NULL``,
  so on that backend "provenance is mandatory" holds because every write goes
  through :func:`validate_properties` — it is load-bearing there, not a
  convenience. Spanner additionally enforces it in the column definition.
* **Edge endpoint pairs are cheap in Kùzu, priced per pair in Spanner.** Kùzu
  declares one ``REL TABLE`` with several ``FROM..TO`` pairs; Spanner needs one
  edge definition per (source table, destination table). That is why ``same_as``
  is restricted to same-label pairs (:attr:`EdgeDef.same_label_only`) — which is
  what ``ARCHITECTURE.md`` describes anyway ("compared against existing entities
  of the same type"), so the cheap shape and the correct shape agree.

Both backends require declared columns, so "arbitrary edge properties" means
*any property the schema declares* rather than schemaless key/value writes —
that constraint is the same on either side and is enforced at write time by
:func:`validate_properties`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import ExtractionConfig
from ..models import AlignmentStatus, Provenance, SourceFormat
from ..ontology import Attribute, Ontology

SOURCE_LABEL = "Source"
EXTRACTED_FROM = "extracted_from"
SAME_AS = "same_as"
MENTIONS = "mentions"

#: Wildcard used in the ontology's domain/range lists.
ANY = "*"


class PropertyType(StrEnum):
    """Backend-neutral property types. See the mapping table above."""

    STRING = "string"
    STRING_LIST = "string_list"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    TIMESTAMP = "timestamp"  # ISO 8601 string in both backends
    VECTOR = "vector"


@dataclass(frozen=True)
class PropertyDef:
    name: str
    type: PropertyType
    required: bool = True
    description: str = ""
    values: tuple[str, ...] = ()  # controlled vocabulary, empty for free values
    dimension: int | None = None  # VECTOR only
    default: Any = None  # column DEFAULT; both backends support one
    #: Inclusive bounds for a numeric property, checked at write time.
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class NodeDef:
    label: str
    properties: tuple[PropertyDef, ...]
    primary_key: str = "id"
    system_managed: bool = False

    def property(self, name: str) -> PropertyDef | None:
        return next((p for p in self.properties if p.name == name), None)

    def property_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties)

    def required_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties if p.required)

    def vector_property(self) -> PropertyDef | None:
        return next((p for p in self.properties if p.type is PropertyType.VECTOR), None)


@dataclass(frozen=True)
class EdgeDef:
    type: str
    source_labels: tuple[str, ...]
    target_labels: tuple[str, ...]
    properties: tuple[PropertyDef, ...]
    structural: bool = False
    #: Endpoints must share a label (``same_as`` only — resolution compares an
    #: entity against existing entities *of the same type*, ARCHITECTURE.md).
    #: Also keeps the edge cheap in Spanner Graph, which needs one edge
    #: definition per (source table, destination table) pair.
    same_label_only: bool = False
    #: Endpoint order carries no meaning (``same_as``): A->B and B->A are one
    #: edge, stored under the canonical order :meth:`StorageAdapter.edge_ref`
    #: gives, so one pair can never hold two contradictory review states.
    symmetric: bool = False

    def property(self, name: str) -> PropertyDef | None:
        return next((p for p in self.properties if p.name == name), None)

    def property_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties)

    def required_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties if p.required)

    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Permitted (source label, target label) pairs, as both backends need
        edge endpoints declared explicitly."""
        if self.same_label_only:
            targets = set(self.target_labels)
            return tuple((s, s) for s in self.source_labels if s in targets)
        return tuple((s, t) for s in self.source_labels for t in self.target_labels)


@dataclass(frozen=True)
class GraphSchema:
    ontology_version: int
    vector_dimension: int
    nodes: tuple[NodeDef, ...]
    edges: tuple[EdgeDef, ...]

    def node(self, label: str) -> NodeDef:
        for n in self.nodes:
            if n.label == label:
                return n
        known = ", ".join(sorted(n.label for n in self.nodes))
        raise KeyError(f"Unknown node label '{label}'. Known: {known}")

    def edge(self, edge_type: str) -> EdgeDef:
        for e in self.edges:
            if e.type == edge_type:
                return e
        known = ", ".join(sorted(e.type for e in self.edges))
        raise KeyError(f"Unknown edge type '{edge_type}'. Known: {known}")

    def node_labels(self) -> tuple[str, ...]:
        return tuple(n.label for n in self.nodes)

    def edge_types(self) -> tuple[str, ...]:
        return tuple(e.type for e in self.edges)

    def vector_nodes(self) -> tuple[NodeDef, ...]:
        return tuple(n for n in self.nodes if n.vector_property() is not None)


# --------------------------------------------------------------------------
# Property blocks
# --------------------------------------------------------------------------

#: The non-negotiable floor: document/section-level provenance. Names mirror
#: :class:`dikms.models.Provenance` exactly.
MANDATORY_PROVENANCE_PROPERTIES: tuple[PropertyDef, ...] = (
    PropertyDef(
        "source_id", PropertyType.STRING, description="Id of the Source document."
    ),
    PropertyDef(
        "chunk_id",
        PropertyType.STRING,
        description="Chunk the fact was extracted from.",
    ),
    PropertyDef(
        "section_refs",
        PropertyType.STRING_LIST,
        description="Human-readable page/section labels covered by the chunk.",
    ),
    PropertyDef(
        "supporting_quote",
        PropertyType.STRING,
        description="Verbatim grounding text returned by the extraction model.",
    ),
    PropertyDef(
        "extraction_model", PropertyType.STRING, description="Model that extracted it."
    ),
    PropertyDef(
        "extracted_at", PropertyType.TIMESTAMP, description="Extraction timestamp."
    ),
)

#: Best-effort exact span, verified by post-hoc alignment only. ``Span`` is
#: flattened; ``alignment_status`` is required (see module docstring).
SPAN_PROPERTIES: tuple[PropertyDef, ...] = (
    PropertyDef(
        "alignment_status",
        PropertyType.STRING,
        required=True,
        values=tuple(s.value for s in AlignmentStatus),
        default=AlignmentStatus.PENDING.value,
        description=(
            "Whether an exact span was aligned, is pending, or could not be resolved."
        ),
    ),
    PropertyDef(
        "span_parse_id",
        PropertyType.STRING,
        required=False,
        description="Parse the verified offsets are relative to.",
    ),
    PropertyDef(
        "span_start",
        PropertyType.INT,
        required=False,
        description="Verified start offset.",
    ),
    PropertyDef(
        "span_end", PropertyType.INT, required=False, description="Verified end offset."
    ),
)

PROVENANCE_PROPERTIES: tuple[PropertyDef, ...] = (
    MANDATORY_PROVENANCE_PROPERTIES + SPAN_PROPERTIES
)

#: Properties every entity node carries regardless of type.
_ENTITY_CORE_PROPERTIES: tuple[PropertyDef, ...] = (
    PropertyDef("id", PropertyType.STRING, description="Stable node identifier."),
    PropertyDef("name", PropertyType.STRING, description="Surface form as extracted."),
)

#: The ``Source`` node is system-managed: it *is* the provenance target, so it
#: carries source metadata (mirroring :class:`dikms.sources.Source`) rather
#: than a provenance block of its own.
#: Bands the rule table assigns (ARCHITECTURE.md: source authoritativeness).
#: A literal for now because there is no config home for it yet; it becomes
#: load-bearing in M2, when the review queue prioritises on it — move it to
#: config at that point rather than adding a config key this ticket does not use.
AUTHORITATIVENESS_BANDS: tuple[str, ...] = ("high", "medium", "low")

_SOURCE_PROPERTIES: tuple[PropertyDef, ...] = (
    PropertyDef("id", PropertyType.STRING, description="Source manifest id."),
    PropertyDef("title", PropertyType.STRING, description="Document/page title."),
    PropertyDef("url", PropertyType.STRING, description="Canonical URL."),
    PropertyDef("publisher", PropertyType.STRING, required=False),
    PropertyDef(
        "format",
        PropertyType.STRING,
        required=False,
        values=tuple(f.value for f in SourceFormat),
    ),
    PropertyDef(
        "authoritativeness",
        PropertyType.STRING,
        description="Rule-based authoritativeness score (DECISIONS.md).",
        values=AUTHORITATIVENESS_BANDS,
    ),
    PropertyDef("authoritativeness_basis", PropertyType.STRING, required=False),
    PropertyDef("notes", PropertyType.STRING, required=False),
    PropertyDef(
        "ingested_at",
        PropertyType.TIMESTAMP,
        description="When the source was ingested.",
    ),
)

#: Review state of a candidate ``same_as`` edge — the reviewed/rejected flag,
#: kept separate from ``confidence`` (DECISIONS.md: entity resolution).
SAME_AS_REVIEW_STATES: tuple[str, ...] = ("unreviewed", "confirmed", "rejected")

#: How a ``same_as`` edge was proposed, for auditability.
SAME_AS_METHODS: tuple[str, ...] = ("alias", "embedding", "llm", "human")


def _attribute_properties(attributes: Iterable[Attribute]) -> tuple[PropertyDef, ...]:
    """Ontology entity attributes become optional, typed node properties.

    Attributes are omitted when not evidenced (``config/ontology.yaml``), so
    they are never required.
    """
    return tuple(
        PropertyDef(
            a.name,
            PropertyType.STRING,
            required=False,
            description=a.description,
            values=tuple(a.values),
        )
        for a in attributes
    )


def _vector_property(dimension: int) -> PropertyDef:
    """The single vector channel, at the configured width.

    Optional because M1 only ever writes stub vectors — real Vertex embeddings
    need credentials and are an M2 concern (DECISIONS.md: embeddings).
    """
    return PropertyDef(
        "embedding",
        PropertyType.VECTOR,
        required=False,
        dimension=dimension,
        description="Entity embedding for the vector channel and resolution shortlist.",
    )


def _resolve_labels(
    relation: str, side: str, names: Iterable[str], all_labels: tuple[str, ...]
) -> tuple[str, ...]:
    """Expand the ontology's ``*`` wildcard into concrete node labels.

    Any other name must be one of ``all_labels``. A typo in a relation's
    domain or range in ``config/ontology.yaml`` therefore fails here, naming
    the relation, rather than as a DDL error in whichever backend runs first.
    """
    resolved: list[str] = []
    for name in names:
        if name == ANY:
            resolved.extend(all_labels)
        elif name in all_labels:
            resolved.append(name)
        else:
            raise ValueError(
                f"Relation '{relation}' names unknown label {name!r} in its "
                f"{side}. Known entity labels: {', '.join(all_labels)}"
            )
    seen: dict[str, None] = {}
    for label in resolved:
        seen.setdefault(label, None)
    return tuple(seen)


#: Structural relations this module models explicitly (by name).
_HANDLED_STRUCTURAL: frozenset[str] = frozenset({EXTRACTED_FROM, SAME_AS, MENTIONS})


def _assert_unique_property_names(
    owner: str, properties: tuple[PropertyDef, ...]
) -> None:
    """An ontology attribute colliding with a provenance field would shadow it."""
    seen: set[str] = set()
    for prop in properties:
        if prop.name in seen:
            raise ValueError(
                f"{owner}: duplicate property name {prop.name!r} — an ontology "
                f"attribute is colliding with a built-in property."
            )
        seen.add(prop.name)


def build_schema(ontology: Ontology, vector_dimension: int) -> GraphSchema:
    """Derive the graph schema from a loaded :class:`dikms.ontology.Ontology`.

    ``vector_dimension`` comes from ``embedding.dimension`` in
    ``config/extraction.yaml`` — see :func:`schema_from_config`.
    """
    if vector_dimension < 1:
        raise ValueError(f"vector_dimension must be positive, got {vector_dimension}")

    # Structural relations are handled by name below. If the ontology grows a
    # new one (an HITL-approved change), fail loudly rather than silently
    # treating it as an ordinary provenanced relation.
    structural = {r.name for r in ontology.relation_types if r.structural}
    unhandled = structural - _HANDLED_STRUCTURAL
    if unhandled:
        raise ValueError(
            f"Ontology declares structural relation(s) {sorted(unhandled)} that "
            f"build_schema does not model. Extend this module deliberately."
        )
    # The other direction: without the provenance edge there is no trace-back
    # path at all, so an ontology missing it is a broken schema, not a smaller
    # one (CLAUDE.md constraint 1).
    if EXTRACTED_FROM not in structural:
        raise ValueError(
            f"Ontology declares no structural '{EXTRACTED_FROM}' relation; every "
            f"fact must be linkable to its Source."
        )
    if SOURCE_LABEL not in {e.name for e in ontology.entity_types}:
        raise ValueError(
            f"Ontology declares no '{SOURCE_LABEL}' entity type; provenance has "
            f"nothing to point at."
        )

    vector = _vector_property(vector_dimension)

    nodes: list[NodeDef] = []
    for entity in ontology.entity_types:
        if entity.name == SOURCE_LABEL:
            nodes.append(
                NodeDef(
                    label=entity.name,
                    properties=_SOURCE_PROPERTIES,
                    system_managed=True,
                )
            )
            continue
        nodes.append(
            NodeDef(
                label=entity.name,
                properties=(
                    _ENTITY_CORE_PROPERTIES
                    + _attribute_properties(entity.attributes)
                    + PROVENANCE_PROPERTIES
                    + (vector,)
                ),
                system_managed=entity.system_managed,
            )
        )

    all_labels = tuple(n.label for n in nodes)
    entity_labels = tuple(label for label in all_labels if label != SOURCE_LABEL)

    edges: list[EdgeDef] = []
    for relation in ontology.relation_types:
        if relation.name == EXTRACTED_FROM:
            # The provenance edge itself: any entity node -> Source. Not from
            # a relation: an edge cannot start an edge, so a relation's
            # provenance lives on the relation (see module docstring).
            edges.append(
                EdgeDef(
                    type=relation.name,
                    source_labels=entity_labels,
                    target_labels=(SOURCE_LABEL,),
                    properties=PROVENANCE_PROPERTIES,
                    structural=True,
                )
            )
            continue

        if relation.name == SAME_AS:
            # Pairwise only. No group/cluster id — see module docstring.
            edges.append(
                EdgeDef(
                    type=relation.name,
                    source_labels=entity_labels,
                    target_labels=entity_labels,
                    same_label_only=True,
                    symmetric=True,
                    properties=(
                        PropertyDef(
                            "confidence",
                            PropertyType.FLOAT,
                            minimum=0.0,
                            maximum=1.0,
                            description=(
                                "Continuous identity confidence for this pair alone."
                            ),
                        ),
                        PropertyDef(
                            "review_status",
                            PropertyType.STRING,
                            values=SAME_AS_REVIEW_STATES,
                            default="unreviewed",
                            description=(
                                "Whether a human has adjudicated this pair "
                                "— independent of confidence, so rejected "
                                "pairs are not re-proposed."
                            ),
                        ),
                        PropertyDef(
                            "method",
                            PropertyType.STRING,
                            values=SAME_AS_METHODS,
                            description="Which resolution step proposed the edge.",
                        ),
                        PropertyDef("created_at", PropertyType.TIMESTAMP),
                    ),
                    structural=True,
                )
            )
            continue

        properties: tuple[PropertyDef, ...]
        if relation.name == MENTIONS:
            properties = PROVENANCE_PROPERTIES
            source_labels: tuple[str, ...] = (SOURCE_LABEL,)
            target_labels: tuple[str, ...] = entity_labels
        else:
            source_labels = _resolve_labels(
                relation.name, "domain", relation.domain or [ANY], entity_labels
            )
            target_labels = _resolve_labels(
                relation.name, "range", relation.range or [ANY], entity_labels
            )
            # Substantive extracted relations: fully provenanced, plus the
            # extraction confidence the review queue ranks on.
            properties = PROVENANCE_PROPERTIES + (
                PropertyDef(
                    "confidence",
                    PropertyType.FLOAT,
                    required=False,
                    minimum=0.0,
                    maximum=1.0,
                    description=(
                        "Extraction confidence, where the pipeline records one."
                    ),
                ),
            )

        edges.append(
            EdgeDef(
                type=relation.name,
                source_labels=source_labels,
                target_labels=target_labels,
                properties=properties,
                structural=relation.structural,
            )
        )

    for node in nodes:
        _assert_unique_property_names(node.label, node.properties)
    for edge in edges:
        _assert_unique_property_names(edge.type, edge.properties)

    return GraphSchema(
        ontology_version=ontology.version,
        vector_dimension=vector_dimension,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def schema_from_config(
    ontology_path: str | Path, config: ExtractionConfig
) -> GraphSchema:
    """Build the schema from ``config/ontology.yaml`` and a loaded
    :class:`dikms.config.ExtractionConfig`.

    The vector width comes from ``embedding.dimension``; there is deliberately
    no fallback literal here.
    """
    return build_schema(
        Ontology.load(ontology_path),
        config.require_embedding().dimension,
    )


# --------------------------------------------------------------------------
# Write-time helpers
# --------------------------------------------------------------------------


def provenance_properties(provenance: Provenance) -> dict[str, Any]:
    """Flatten a :class:`~dikms.models.Provenance` into schema properties.

    Backends and callers use this rather than assembling the dict by hand, so
    the mandatory fields cannot drift apart from ``models.Provenance``.
    """
    props: dict[str, Any] = {
        "source_id": provenance.source_id,
        "chunk_id": provenance.chunk_id,
        "section_refs": list(provenance.section_refs),
        "supporting_quote": provenance.supporting_quote,
        "extraction_model": provenance.extraction_model,
        "extracted_at": provenance.extracted_at,
        "alignment_status": AlignmentStatus(provenance.alignment_status).value,
        "span_parse_id": None,
        "span_start": None,
        "span_end": None,
    }
    if provenance.span is not None:
        props["span_parse_id"] = provenance.span.parse_id
        props["span_start"] = provenance.span.start
        props["span_end"] = provenance.span.end
    return props


def apply_defaults(
    definition: NodeDef | EdgeDef, properties: Mapping[str, Any]
) -> dict[str, Any]:
    """Copy ``properties``, filling in declared defaults for absent keys.

    The declared ``default`` is realised as a column ``DEFAULT`` in both
    backends, but it is applied here too so a write is complete before it
    reaches the store and every backend sees the same value.
    """
    props = dict(properties)
    for prop in definition.properties:
        if prop.default is not None and props.get(prop.name) is None:
            props[prop.name] = prop.default
    return props


class SchemaViolation(ValueError):
    """A write that does not satisfy the declared schema."""


#: Accepted Python types per declared property type. Both backends have typed
#: columns, so a mistyped write fails at the driver either way — catching it
#: here gives the same error whichever backend is configured.
_PYTHON_TYPES: dict[PropertyType, tuple[type, ...]] = {
    PropertyType.STRING: (str,),
    PropertyType.TIMESTAMP: (str,),  # ISO 8601, per models.now_iso
    PropertyType.STRING_LIST: (list, tuple),
    PropertyType.INT: (int,),
    PropertyType.FLOAT: (float, int),  # an int is a valid float value
    PropertyType.BOOL: (bool,),
    PropertyType.VECTOR: (list, tuple),
}


def _check_type(owner: str, prop: PropertyDef, value: Any) -> None:
    accepted = _PYTHON_TYPES[prop.type]
    # bool is a subclass of int; only PropertyType.BOOL should accept it.
    if isinstance(value, bool) and prop.type is not PropertyType.BOOL:
        raise SchemaViolation(
            f"{owner}.{prop.name}: expected {prop.type.value}, got bool"
        )
    if not isinstance(value, accepted):
        raise SchemaViolation(
            f"{owner}.{prop.name}: expected {prop.type.value}, "
            f"got {type(value).__name__}"
        )
    if prop.type is PropertyType.STRING_LIST and not all(
        isinstance(item, str) for item in value
    ):
        raise SchemaViolation(f"{owner}.{prop.name}: expected a list of strings")
    if prop.type is PropertyType.VECTOR and not all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
    ):
        raise SchemaViolation(f"{owner}.{prop.name}: expected a list of numbers")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp that carries a UTC offset.

    A naive timestamp is refused: "when was this extracted" is part of a
    fact's provenance, and a time with no offset cannot be placed on one
    timeline. Raises :class:`ValueError` on anything else.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a non-empty ISO 8601 string")
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{value!r} has no UTC offset")
    return parsed


def normalise_timestamp(value: str) -> str:
    """The one stored form of a timestamp: UTC, ``+00:00``, microseconds.

    Fixed width (``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``), so string order is
    chronological order in both backends, which store timestamps as strings
    (see the mapping table). ``Z`` and ``+00:00`` inputs, or any other offset,
    all normalise to the same string for the same instant.
    """
    return (
        parse_timestamp(value)
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )


def normalise_properties(
    definition: NodeDef | EdgeDef, properties: Mapping[str, Any]
) -> dict[str, Any]:
    """Copy ``properties`` with every timestamp in its one stored form.

    Values that do not parse are left as they are for
    :func:`validate_properties` to reject with a schema error.
    """
    props = dict(properties)
    for prop in definition.properties:
        value = props.get(prop.name)
        if prop.type is PropertyType.TIMESTAMP and isinstance(value, str):
            try:
                props[prop.name] = normalise_timestamp(value)
            except ValueError:
                pass
    return props


def _is_blank(prop: PropertyDef, value: Any) -> bool:
    """True when a present value carries no information.

    A required string that is empty or whitespace, or a required list that is
    empty or holds only such strings, is as good as missing: ``source_id=""``
    names no document and ``section_refs=[]`` no section.
    """
    if prop.type in (PropertyType.STRING, PropertyType.TIMESTAMP):
        return isinstance(value, str) and not value.strip()
    if prop.type is PropertyType.STRING_LIST:
        return isinstance(value, (list, tuple)) and (
            not value or any(isinstance(v, str) and not v.strip() for v in value)
        )
    return False


def _check_number(name: str, prop: PropertyDef, value: float) -> None:
    """Finite, and inside any declared bounds. NaN compares false with
    everything, so the bounds alone would not catch it; and a confidence of
    7.5 or NaN would rank or sort unpredictably against every real one."""
    if not math.isfinite(value):
        raise SchemaViolation(f"{name}.{prop.name}: {value!r} is not finite")
    if prop.minimum is not None and value < prop.minimum:
        raise SchemaViolation(
            f"{name}.{prop.name}: {value!r} is below the minimum {prop.minimum}"
        )
    if prop.maximum is not None and value > prop.maximum:
        raise SchemaViolation(
            f"{name}.{prop.name}: {value!r} is above the maximum {prop.maximum}"
        )


def validate_properties(
    definition: NodeDef | EdgeDef,
    properties: Mapping[str, Any],
) -> None:
    """Check a property dict against a node/edge definition.

    Enforces, backend-independently:

    * no unknown properties (both backends need declared columns);
    * every required property present, non-``None`` and non-empty (no empty
      or whitespace string, no empty list, no empty list item) — which is
      what makes "a fact cannot be stored without its provenance" true rather
      than aspirational;
    * declared property types (both backends have typed columns);
    * timestamps parse as ISO 8601 with a UTC offset;
    * controlled vocabularies;
    * numbers are finite and inside declared bounds (``confidence`` in 0..1);
    * vector width matches the configured dimension;
    * exact-span coherence (see :func:`_check_span`).
    """
    name = getattr(definition, "label", None) or definition.type
    declared = {p.name: p for p in definition.properties}

    unknown = sorted(set(properties) - set(declared))
    if unknown:
        raise SchemaViolation(f"{name}: unknown properties {unknown}")

    missing = [
        p.name
        for p in definition.properties
        if p.required and properties.get(p.name) is None
    ]
    if missing:
        raise SchemaViolation(f"{name}: missing required properties {sorted(missing)}")

    for key, value in properties.items():
        if value is None:
            continue
        prop = declared[key]
        _check_type(name, prop, value)
        if prop.required and _is_blank(prop, value):
            raise SchemaViolation(f"{name}.{key}: required property is empty")
        if prop.type is PropertyType.TIMESTAMP:
            try:
                parse_timestamp(value)
            except ValueError as exc:
                raise SchemaViolation(
                    f"{name}.{key}: {value!r} is not an ISO 8601 timestamp with a "
                    f"UTC offset ({exc})"
                ) from None
        if prop.values and value not in prop.values:
            raise SchemaViolation(
                f"{name}.{key}: {value!r} not in {list(prop.values)}"
            )
        if prop.type is PropertyType.FLOAT:
            _check_number(name, prop, value)
        if prop.type is PropertyType.VECTOR:
            if prop.dimension is not None and len(value) != prop.dimension:
                raise SchemaViolation(
                    f"{name}.{key}: expected {prop.dimension} dimensions, "
                    f"got {len(value)}"
                )

    if "alignment_status" in declared:
        _check_span(name, properties)


#: The flattened :class:`dikms.models.Span`; all present or all absent.
SPAN_FIELDS: tuple[str, ...] = ("span_parse_id", "span_start", "span_end")


def _check_span(name: str, properties: Mapping[str, Any]) -> None:
    """The exact span is verified-only (ARCHITECTURE.md: span-level provenance).

    * The three span fields are all present or all absent — a partial span is
      not a span.
    * They are present exactly when ``alignment_status`` is ``aligned``. A
      ``pending`` or ``unresolved`` fact with offsets would be an unverified
      offset stored as if confirmed; an ``aligned`` fact with none would claim
      a verification that left nothing to check.
    * ``0 <= span_start <= span_end`` and ``span_parse_id`` is non-empty.
    """
    present = [f for f in SPAN_FIELDS if properties.get(f) is not None]
    aligned = properties.get("alignment_status") == AlignmentStatus.ALIGNED.value
    if present and len(present) != len(SPAN_FIELDS):
        absent = sorted(set(SPAN_FIELDS) - set(present))
        raise SchemaViolation(f"{name}: partial span, {absent} missing")
    if aligned and not present:
        raise SchemaViolation(
            f"{name}: alignment_status is 'aligned' but no span is recorded"
        )
    if present and not aligned:
        raise SchemaViolation(
            f"{name}: span fields are set but alignment_status is "
            f"{properties.get('alignment_status')!r}; only an aligned span may be "
            f"stored"
        )
    if not present:
        return
    if not properties["span_parse_id"].strip():
        raise SchemaViolation(f"{name}.span_parse_id: empty")
    start, end = properties["span_start"], properties["span_end"]
    if not 0 <= start <= end:
        raise SchemaViolation(
            f"{name}: span offsets must satisfy 0 <= span_start <= span_end, "
            f"got {start}..{end}"
        )
