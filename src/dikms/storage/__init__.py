"""Knowledge-store access: one backend-agnostic adapter interface and one
declarative schema.

Downstream code (ingestion, provenance writes, trace-back queries, retrieval)
imports from here and nothing else — never a backend module, never a database
driver. See :mod:`dikms.storage.base` for the contract,
:mod:`dikms.storage.schema` for what the store holds (including the
Kùzu/Spanner mapping table), :mod:`dikms.storage.keys` for the entity node-id
rule (:func:`node_key`), :mod:`dikms.storage.identity` for writing
``same_as`` without losing a human verdict, and :mod:`dikms.storage.factory`
for backend selection from ``storage.backend`` in ``config/extraction.yaml``.
"""

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
from .factory import (
    BACKENDS,
    BackendUnavailableError,
    UnknownBackendError,
    create_adapter,
    known_backends,
    resolve_backend,
)
from .identity import write_same_as
from .keys import node_key, normalise_name
from .schema import (
    MANDATORY_PROVENANCE_PROPERTIES,
    PROVENANCE_PROPERTIES,
    SPAN_PROPERTIES,
    EdgeDef,
    GraphSchema,
    NodeDef,
    PropertyDef,
    PropertyType,
    SchemaViolation,
    apply_defaults,
    build_schema,
    normalise_properties,
    normalise_timestamp,
    parse_timestamp,
    provenance_properties,
    schema_from_config,
    validate_properties,
)

__all__ = [
    "BACKENDS",
    "MANDATORY_PROVENANCE_PROPERTIES",
    "PROVENANCE_PROPERTIES",
    "SPAN_PROPERTIES",
    "BackendUnavailableError",
    "Direction",
    "EdgeDef",
    "EdgeRecord",
    "EdgeRef",
    "GraphSchema",
    "MissingNodeError",
    "NodeDef",
    "NodeRecord",
    "NodeRef",
    "PathStep",
    "PropertyDef",
    "PropertyType",
    "ProvenanceHit",
    "SchemaViolation",
    "SimilarityHit",
    "StorageAdapter",
    "TraversalResult",
    "UnknownBackendError",
    "apply_defaults",
    "build_schema",
    "create_adapter",
    "known_backends",
    "node_key",
    "normalise_name",
    "normalise_properties",
    "normalise_timestamp",
    "parse_timestamp",
    "provenance_properties",
    "resolve_backend",
    "schema_from_config",
    "validate_properties",
    "write_same_as",
]
