"""Extraction orchestration: parsed document -> chunks -> per-chunk extraction.

Off-ontology types are not dropped: the model is instructed to stay within the
ontology, but if it emits an unknown type anyway that is recorded as a warning
for manual review rather than silently discarded — surfacing model behaviour is
exactly what M0 is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..chunking import DEFAULT_MAX_CHARS, DEFAULT_TARGET_CHARS, chunk_document
from ..models import ChunkExtraction, ExtractedEntity, ExtractedRelation, ParsedDocument, TypeProposal
from ..ontology import Ontology


class Extractor(Protocol):
    def extract(self, ontology: Ontology, chunk) -> ChunkExtraction: ...


@dataclass
class DocumentExtraction:
    source_id: str
    parse_id: str
    chunks: list[ChunkExtraction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # (chunk_id, error) for chunks that could not be extracted. Recorded, not
    # silently dropped, so partial output is honest about what is missing.
    failed_chunks: list[tuple[str, str]] = field(default_factory=list)

    def all_entities(self) -> list[ExtractedEntity]:
        return [e for c in self.chunks for e in c.entities]

    def all_relations(self) -> list[ExtractedRelation]:
        return [r for c in self.chunks for r in c.relations]

    def all_proposals(self) -> list[TypeProposal]:
        return [p for c in self.chunks for p in c.proposals]


def extract_document(
    parsed: ParsedDocument,
    ontology: Ontology,
    extractor: Extractor,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> DocumentExtraction:
    chunks = chunk_document(parsed, target_chars=target_chars, max_chars=max_chars)
    doc = DocumentExtraction(source_id=parsed.source_id, parse_id=parsed.parse_id)
    entity_names = ontology.entity_names()
    relation_names = ontology.relation_names()

    for chunk in chunks:
        try:
            result = extractor.extract(ontology, chunk)
        except Exception as exc:  # noqa: BLE001 — isolate one chunk's failure
            # Persist progress on the other chunks rather than losing the whole
            # document to one transient failure (e.g. quota exhaustion).
            doc.failed_chunks.append((chunk.chunk_id, f"{type(exc).__name__}: {exc}"))
            continue
        doc.chunks.append(result)
        doc.warnings.extend(
            _validate(result, chunk.chunk_id, entity_names, relation_names)
        )
    return doc


def _validate(
    result: ChunkExtraction,
    chunk_id: str,
    entity_names: set[str],
    relation_names: set[str],
) -> list[str]:
    warnings: list[str] = []
    for e in result.entities:
        if e.entity_type not in entity_names:
            warnings.append(
                f"{chunk_id}: off-ontology entity type '{e.entity_type}' for '{e.name}'"
            )
    for r in result.relations:
        if r.relation_type not in relation_names:
            warnings.append(
                f"{chunk_id}: off-ontology relation type '{r.relation_type}'"
            )
        for role, t in (("source", r.source_type), ("target", r.target_type)):
            if t and t not in entity_names:
                warnings.append(
                    f"{chunk_id}: off-ontology {role} type '{t}' on relation '{r.relation_type}'"
                )
    return warnings
