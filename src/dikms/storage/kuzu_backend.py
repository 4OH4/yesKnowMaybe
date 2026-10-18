"""The embedded Kùzu implementation of :class:`~dikms.storage.StorageAdapter`.

This is the backend every unattended session and every test uses: no server,
no credentials, one database file on disk (``storage.kuzu.path`` in
``config/extraction.yaml``, ``data/graph.kuzu`` by default and gitignored).
It is the only module in the codebase that imports ``kuzu``; callers get it
from :func:`dikms.storage.create_adapter` and see only the adapter interface.

Pinned to the locked Kùzu version (0.11.3)
------------------------------------------
Behaviour here was checked against what 0.11.3 actually does, not what the
documentation of other releases describes:

* **The database is a single file** (plus ``.wal``/``.shadow`` siblings while
  it is open), not a directory. ``.gitignore`` covers all three.
* **No vector index; similarity is an exact cosine scan.** 0.11.3's HNSW
  index (``CREATE_VECTOR_INDEX``, statically linked, so no extension download)
  cannot be lived with on data that changes. Once a column is indexed,
  ``SET`` on it fails, even to ``NULL``. ``DROP_VECTOR_INDEX`` leaves the table
  unwritable (later inserts fail with "``_0_<index>_UPPER`` does not exist in
  catalog") and the index name cannot be reused. Deleting and re-inserting an
  embedding row instead keeps the row but drops it from index results. An
  entity's embedding is computed after its node is written (M2), so the
  column must be updatable, and an index that silently returns nothing is
  worse than none. :meth:`KuzuAdapter.similar_nodes` therefore computes
  ``array_cosine_similarity`` over every stored ``FLOAT[<dim>]`` vector of the
  label: exact rather than approximate, and about 30 ms for 1,000 nodes at
  3,072 dimensions, well within the corpus scale (``DECISIONS.md``: knowledge
  store). The column is created at ``embedding.dimension`` from config.
* **Simple paths are filtered here, not by Kùzu.** On an undirected
  variable-length pattern, ``ACYCLIC`` returns paths that revisit the start
  node (``a-b-a``). :meth:`KuzuAdapter.traverse` asks for ``TRAIL`` paths (no
  repeated edge, which every simple path is) and keeps the simple ones.
* **No ``NOT NULL``, no ``CHECK``.** Required-ness, vocabularies and bounds
  are enforced by the shared ``prepare_node``/``prepare_edge`` before
  anything reaches Kùzu, as the mapping table in :mod:`dikms.storage.schema`
  says. Every write here goes through them.

Schema
------
:func:`schema_ddl` derives the DDL from the shared :class:`GraphSchema`: one
node table per label, one relationship table per edge type with every
``FROM``/``TO`` pair the schema enumerates (the ontology's ``"*"`` already
expanded to every entity label by :func:`dikms.storage.build_schema`).
:meth:`KuzuAdapter.initialise_schema` runs it with ``IF NOT EXISTS``, so
opening an existing database is safe, and then brings an older database up to
date without deleting anything: it adds declared properties and endpoint
pairs the database lacks, and refuses to open one whose column types differ
from the schema (a changed ``embedding.dimension``, say) rather than coercing
it. Tables and columns the schema no longer declares are left in place.

Writes
------
* **Nodes** ``MERGE`` on the label's primary key, the ``node_id`` the caller
  passes, and then ``SET`` every property :meth:`prepare_node` returns, so an
  upsert overwrites fully. The backend applies no identity rule of its own;
  callers build the id with :func:`dikms.storage.node_key`. Nothing here
  fuses or deletes a node, and the module issues no ``DELETE`` at all.
* **Edges** ``MATCH`` both endpoints and ``MERGE`` the edge on its key
  (:meth:`StorageAdapter.edge_ref`): ``source_id`` + ``chunk_id`` for a
  fact-bearing edge, the canonically ordered pair alone for ``same_as``. The
  check and the write are one statement, so a missing endpoint writes nothing
  and raises :class:`~dikms.storage.MissingNodeError`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import kuzu

from ..config import StorageConfig
from .base import (
    Direction,
    EdgeRecord,
    EdgeRef,
    MissingNodeError,
    NodeRecord,
    NodeRef,
    PathStep,
    ProvenanceHit,
    SimilarityHit,
    StorageAdapter,
    TraversalResult,
)
from .schema import (
    EXTRACTED_FROM,
    PROVENANCE_PROPERTIES,
    SOURCE_LABEL,
    EdgeDef,
    GraphSchema,
    NodeDef,
    PropertyDef,
    PropertyType,
)

#: Kùzu column type per schema property type (see the mapping table in
#: :mod:`dikms.storage.schema`). ``VECTOR`` is sized per property.
_COLUMN_TYPES: dict[PropertyType, str] = {
    PropertyType.STRING: "STRING",
    PropertyType.STRING_LIST: "STRING[]",
    PropertyType.INT: "INT64",
    PropertyType.FLOAT: "DOUBLE",
    PropertyType.BOOL: "BOOL",
    PropertyType.TIMESTAMP: "STRING",
}

#: Spellings Kùzu's ``TABLE_INFO`` may report for a type, keyed by the one
#: the DDL uses.
_TYPE_ALIASES: dict[str, frozenset[str]] = {"BOOL": frozenset({"BOOL", "BOOLEAN"})}

_PROVENANCE_NAMES: tuple[str, ...] = tuple(p.name for p in PROVENANCE_PROPERTIES)


class SchemaMismatchError(RuntimeError):
    """The database on disk has a column whose type differs from the schema.

    Raised on open rather than coerced: a changed ``embedding.dimension``, for
    example, means every stored vector must be recomputed, which is a
    deliberate step, not something opening a database should do quietly.
    """


# --------------------------------------------------------------------------
# DDL, derived from the shared schema
# --------------------------------------------------------------------------


def _ident(name: str) -> str:
    """Quote a table or column name. Names come from the ontology through the
    schema; anything that is not a plain identifier is refused rather than
    escaped, so no name can change the shape of a statement."""
    if not name.isidentifier():
        raise ValueError(f"{name!r} is not a valid Kùzu identifier")
    return f"`{name}`"


def column_type(prop: PropertyDef) -> str:
    """The Kùzu column type for one declared property."""
    if prop.type is PropertyType.VECTOR:
        if not prop.dimension or prop.dimension < 1:
            raise ValueError(f"{prop.name}: a vector property needs a dimension")
        return f"FLOAT[{prop.dimension}]"
    return _COLUMN_TYPES[prop.type]


def _literal(value: Any) -> str:
    """A column ``DEFAULT`` literal. Only the types the schema declares
    defaults for are supported."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(value, (int, float)) and math.isfinite(value):
        return repr(value)
    raise ValueError(f"no Kùzu literal for default {value!r}")


def _column(prop: PropertyDef) -> str:
    sql = f"{_ident(prop.name)} {column_type(prop)}"
    if prop.default is not None:
        sql += f" DEFAULT {_literal(prop.default)}"
    return sql


def node_table_ddl(node: NodeDef) -> str:
    columns = ", ".join(_column(p) for p in node.properties)
    return (
        f"CREATE NODE TABLE IF NOT EXISTS {_ident(node.label)}"
        f"({columns}, PRIMARY KEY({_ident(node.primary_key)}))"
    )


def rel_table_ddl(edge: EdgeDef) -> str:
    pairs = edge.pairs()
    if not pairs:
        raise ValueError(f"{edge.type}: no endpoint pairs to declare")
    parts = [f"FROM {_ident(s)} TO {_ident(t)}" for s, t in pairs]
    parts.extend(_column(p) for p in edge.properties)
    return f"CREATE REL TABLE IF NOT EXISTS {_ident(edge.type)}({', '.join(parts)})"


def schema_ddl(schema: GraphSchema) -> list[str]:
    """Every statement that creates ``schema`` in an empty database, in an
    order Kùzu accepts (node tables before the relationship tables that
    reference them). Each is ``IF NOT EXISTS``, so the list is safe to run
    against a database that already has some or all of it."""
    return [node_table_ddl(n) for n in schema.nodes] + [
        rel_table_ddl(e) for e in schema.edges
    ]


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------


def _pattern(direction: Direction, inner: str) -> str:
    if direction is Direction.OUTGOING:
        return f"-[{inner}]->"
    if direction is Direction.INCOMING:
        return f"<-[{inner}]-"
    return f"-[{inner}]-"


def _internal_id(value: Mapping[str, Any]) -> tuple[int, int]:
    return (value["table"], value["offset"])


class KuzuAdapter(StorageAdapter):
    """Embedded Kùzu backend. Opening it creates or updates the schema.

    The database path is ``config.kuzu.path``, relative to the working
    directory unless absolute; its parent directory is created if needed.
    """

    def __init__(
        self, schema: GraphSchema, config: StorageConfig | None = None
    ) -> None:
        super().__init__(schema, config)
        if config is None:
            raise ValueError(
                "KuzuAdapter needs the storage config (storage.kuzu.path in "
                "config/extraction.yaml); build it with create_adapter"
            )
        self.path = Path(config.kuzu.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db: kuzu.Database | None = kuzu.Database(str(self.path))
        self._conn: kuzu.Connection | None = kuzu.Connection(self._db)
        try:
            self.initialise_schema()
        except BaseException:
            self.close()
            raise

    # -- plumbing -------------------------------------------------------

    def _rows(self, query: str, params: Mapping[str, Any] | None = None) -> list[list]:
        if self._conn is None:
            raise RuntimeError("the Kùzu adapter is closed")
        result = self._conn.execute(query, dict(params or {}))
        rows: list[list] = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- schema ---------------------------------------------------------

    def initialise_schema(self) -> None:
        for statement in schema_ddl(self.schema):
            self._rows(statement)
        for node in self.schema.nodes:
            self._reconcile_columns(node.label, node.properties)
        for edge in self.schema.edges:
            self._reconcile_columns(edge.type, edge.properties)
            self._reconcile_pairs(edge)

    def _reconcile_columns(
        self, table: str, properties: Iterable[PropertyDef]
    ) -> None:
        existing = {
            row[1]: str(row[2])
            for row in self._rows(f"CALL TABLE_INFO('{table}') RETURN *")
        }
        for prop in properties:
            wanted = column_type(prop)
            found = existing.get(prop.name)
            if found is None:
                self._rows(
                    f"ALTER TABLE {_ident(table)} ADD IF NOT EXISTS {_column(prop)}"
                )
            elif found not in _TYPE_ALIASES.get(wanted, frozenset({wanted})):
                raise SchemaMismatchError(
                    f"{self.path}: {table}.{prop.name} is {found} in the database "
                    f"but the schema declares {wanted}. Not changed on open; "
                    f"migrate or rebuild the database deliberately."
                )

    def _reconcile_pairs(self, edge: EdgeDef) -> None:
        existing = {
            (row[0], row[1])
            for row in self._rows(f"CALL SHOW_CONNECTION('{edge.type}') RETURN *")
        }
        for source, target in edge.pairs():
            if (source, target) not in existing:
                self._rows(
                    f"ALTER TABLE {_ident(edge.type)} ADD IF NOT EXISTS "
                    f"FROM {_ident(source)} TO {_ident(target)}"
                )

    # -- record conversion ----------------------------------------------

    def _node_props(self, label: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Every declared property of ``label``, as stored (``None`` where
        absent). Kùzu returns a union of columns for mixed-label results, so
        only the label's own declared names are kept."""
        names = self.schema.node(label).property_names()
        return {name: raw.get(name) for name in names}

    def _edge_props(self, edge_type: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        return {
            name: raw.get(name) for name in self.schema.edge(edge_type).property_names()
        }

    def _node_record(self, raw: Mapping[str, Any]) -> NodeRecord:
        label = raw["_label"]
        definition = self.schema.node(label)
        return NodeRecord(
            NodeRef(label, raw[definition.primary_key]), self._node_props(label, raw)
        )

    def _edge_record(
        self, raw: Mapping[str, Any], source: NodeRef, target: NodeRef
    ) -> EdgeRecord:
        edge_type = raw["_label"]
        props = self._edge_props(edge_type, raw)
        return EdgeRecord(self.edge_ref(edge_type, source, target, props), props)

    # -- writes ---------------------------------------------------------

    def upsert_node(
        self, label: str, node_id: str, properties: Mapping[str, Any]
    ) -> NodeRef:
        props = self.prepare_node(label, node_id, properties)
        definition = self.node_def(label)
        assignments, params = [], {"__id": node_id}
        for i, (name, value) in enumerate(props.items()):
            if name == definition.primary_key:
                continue
            params[f"p{i}"] = _to_kuzu(definition.property(name), value)
            assignments.append(f"n.{_ident(name)} = $p{i}")
        query = (
            f"MERGE (n:{_ident(label)} {{{_ident(definition.primary_key)}: $__id}})"
        )
        if assignments:
            query += " SET " + ", ".join(assignments)
        self._rows(query, params)
        return NodeRef(label, node_id)

    def upsert_edge(
        self,
        edge_type: str,
        source: NodeRef,
        target: NodeRef,
        properties: Mapping[str, Any],
    ) -> EdgeRef:
        ref, props = self.prepare_edge(edge_type, source, target, properties)
        definition = self.edge_def(edge_type)
        key_names = ("source_id", "chunk_id") if ref.is_occurrence() else ()
        params: dict[str, Any] = {"__a": ref.source.id, "__b": ref.target.id}
        key = ", ".join(f"{_ident(k)}: $__{k}" for k in key_names)
        for k in key_names:
            params[f"__{k}"] = props[k]
        assignments = []
        for i, (name, value) in enumerate(props.items()):
            if name in key_names:
                continue
            params[f"p{i}"] = _to_kuzu(definition.property(name), value)
            assignments.append(f"e.{_ident(name)} = $p{i}")
        query = (
            f"{self._match_endpoints(ref)} "
            f"MERGE (a)-[e:{_ident(edge_type)}{' {' + key + '}' if key else ''}]->(b)"
        )
        if assignments:
            query += " SET " + ", ".join(assignments)
        query += " RETURN 1"
        if not self._rows(query, params):
            # MATCH found no pair, so MERGE wrote nothing; say which is missing.
            self.require_endpoints(ref.source, ref.target)
            raise MissingNodeError(
                f"{edge_type}: endpoints {ref.source} / {ref.target} not found"
            )
        return ref

    def _match_endpoints(self, ref: EdgeRef) -> str:
        source_pk = _ident(self.node_def(ref.source.label).primary_key)
        target_pk = _ident(self.node_def(ref.target.label).primary_key)
        return (
            f"MATCH (a:{_ident(ref.source.label)} {{{source_pk}: $__a}}), "
            f"(b:{_ident(ref.target.label)} {{{target_pk}: $__b}})"
        )

    # -- reads ----------------------------------------------------------

    def get_node(self, ref: NodeRef) -> NodeRecord | None:
        definition = self.node_def(ref.label)
        rows = self._rows(
            f"MATCH (n:{_ident(ref.label)} {{{_ident(definition.primary_key)}: $id}}) "
            f"RETURN n",
            {"id": ref.id},
        )
        if not rows:
            return None
        return NodeRecord(ref, self._node_props(ref.label, rows[0][0]))

    def get_edge(self, ref: EdgeRef) -> EdgeRecord | None:
        key = self.canonical_edge_ref(ref)
        records = self._edges_between(key)
        if len(records) > 1:
            raise RuntimeError(
                f"{key}: {len(records)} stored edges share one key; the store's "
                f"upsert invariant is broken"
            )
        return records[0] if records else None

    def _edges_between(self, ref: EdgeRef) -> list[EdgeRecord]:
        """Every stored ``ref.type`` edge from ``ref.source`` to ``ref.target``,
        narrowed to one extraction when ``ref`` is an occurrence."""
        params: dict[str, Any] = {"__a": ref.source.id, "__b": ref.target.id}
        key = ""
        if ref.is_occurrence():
            key = " {`source_id`: $__source_id, `chunk_id`: $__chunk_id}"
            params["__source_id"] = ref.source_id
            params["__chunk_id"] = ref.chunk_id
        rows = self._rows(
            f"MATCH (a:{_ident(ref.source.label)} "
            f"{{{_ident(self.node_def(ref.source.label).primary_key)}: $__a}})"
            f"-[e:{_ident(ref.type)}{key}]->"
            f"(b:{_ident(ref.target.label)} "
            f"{{{_ident(self.node_def(ref.target.label).primary_key)}: $__b}}) "
            f"RETURN e",
            params,
        )
        return [self._edge_record(row[0], ref.source, ref.target) for row in rows]

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
        self.check_traversal_args(
            edge_types=edge_types,
            max_hops=max_hops,
            target_labels=target_labels,
            limit=limit,
        )
        direction = Direction(direction)
        definition = self.node_def(start.label)
        types = ""
        if edge_types:
            types = ":" + "|".join(_ident(t) for t in dict.fromkeys(edge_types))
        query = (
            f"MATCH p = (s:{_ident(start.label)} "
            f"{{{_ident(definition.primary_key)}: $id}})"
            f"{_pattern(direction, f'e{types}* TRAIL 1..{int(max_hops)}')}(t)"
        )
        params: dict[str, Any] = {"id": start.id}
        if target_labels:
            query += " WHERE label(t) IN $labels"
            params["labels"] = list(dict.fromkeys(target_labels))
        query += " RETURN nodes(p), rels(p)"

        results: list[TraversalResult] = []
        for raw_nodes, raw_rels in self._rows(query, params):
            records = [self._node_record(n) for n in raw_nodes]
            by_id = {
                _internal_id(raw["_id"]): record.ref
                for raw, record in zip(raw_nodes, records, strict=True)
            }
            steps = tuple(
                PathStep(
                    self._edge_record(
                        rel, by_id[_internal_id(rel["_src"])],
                        by_id[_internal_id(rel["_dst"])],
                    ),
                    node,
                )
                for rel, node in zip(raw_rels, records[1:], strict=True)
            )
            result = TraversalResult(start, steps)
            if result.is_simple():
                results.append(result)
        results.sort(key=lambda r: r.hops)  # stable: shortest first
        return results if limit is None else results[:limit]

    def similar_nodes(
        self,
        label: str,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SimilarityHit]:
        self.check_query_embedding(label, embedding)
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        prop = self.node_def(label).vector_property()
        assert prop is not None  # check_query_embedding guarantees it
        column = _ident(prop.name)
        query = (
            f"MATCH (n:{_ident(label)}) WHERE n.{column} IS NOT NULL "
            f"WITH n, array_cosine_similarity(n.{column}, "
            f"CAST($v, '{column_type(prop)}')) AS score "
        )
        params: dict[str, Any] = {
            "v": [float(x) for x in embedding],
            "k": int(limit),
        }
        if min_score is not None:
            query += "WHERE score >= $min "
            params["min"] = float(min_score)
        query += "RETURN n, score ORDER BY score DESC LIMIT $k"
        hits = [
            SimilarityHit(self._node_record(node), float(score))
            for node, score in self._rows(query, params)
            if score is not None and not math.isnan(score)
        ]
        return self.rank_similarity_hits(hits, limit=limit, min_score=min_score)

    def trace_back(self, subject: NodeRef | EdgeRef) -> list[ProvenanceHit]:
        if isinstance(subject, NodeRef):
            hits = self._trace_node(subject)
        else:
            hits = self._trace_edge(subject)
        return sorted(
            hits,
            key=lambda h: (
                str(h.provenance.get("extracted_at")),
                str(h.provenance.get("source_id")),
                str(h.provenance.get("chunk_id")),
            ),
        )

    def _trace_node(self, ref: NodeRef) -> list[ProvenanceHit]:
        definition = self.node_def(ref.label)
        if (ref.label, SOURCE_LABEL) not in self.edge_def(EXTRACTED_FROM).pairs():
            return []  # a Source is not extracted from anything
        rows = self._rows(
            f"MATCH (n:{_ident(ref.label)} {{{_ident(definition.primary_key)}: $id}})"
            f"-[e:{_ident(EXTRACTED_FROM)}]->(s:{_ident(SOURCE_LABEL)}) RETURN e, s",
            {"id": ref.id},
        )
        return [
            ProvenanceHit(
                subject=ref,
                provenance=_provenance_block(edge),
                source=self._node_record(source),
            )
            for edge, source in rows
        ]

    def _trace_edge(self, ref: EdgeRef) -> list[ProvenanceHit]:
        definition = self.edge_def(ref.type)
        if definition.property("source_id") is None:
            return []  # same_as: an identity claim, not extracted from a source
        if ref.is_occurrence():
            record = self.get_edge(ref)
            records = [] if record is None else [record]
        else:
            if ref.source_id is not None or ref.chunk_id is not None:
                raise ValueError(
                    f"{ref.type}: trace_back takes an occurrence ref (source_id "
                    f"and chunk_id) or a generic one (neither), not one of them"
                )
            if (ref.source.label, ref.target.label) not in definition.pairs():
                raise ValueError(
                    f"{ref.type}: {ref.source.label} -> {ref.target.label} is not a "
                    f"permitted endpoint pair for this relation"
                )
            records = self._edges_between(ref)
        hits = []
        for record in records:
            block = _provenance_block(record.properties)
            hits.append(
                ProvenanceHit(
                    subject=record.ref,
                    provenance=block,
                    source=self.get_node(NodeRef(SOURCE_LABEL, block["source_id"])),
                )
            )
        return hits


def _provenance_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The provenance properties of a stored edge, by the shared names."""
    return {name: raw.get(name) for name in _PROVENANCE_NAMES}


def _to_kuzu(prop: PropertyDef | None, value: Any) -> Any:
    """A validated property value in the form the Kùzu driver binds."""
    if value is None or prop is None:
        return value
    if prop.type is PropertyType.VECTOR:
        return [float(x) for x in value]
    if prop.type is PropertyType.STRING_LIST:
        return list(value)
    if prop.type is PropertyType.FLOAT:
        return float(value)
    return value
