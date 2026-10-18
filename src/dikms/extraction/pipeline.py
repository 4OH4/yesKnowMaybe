"""Extraction orchestration: parsed document -> chunks -> per-chunk extraction.

Off-ontology types are not dropped: the model is instructed to stay within the
ontology, but if it emits an unknown type anyway that is recorded as a warning
for manual review rather than silently discarded — surfacing model behaviour is
exactly what M0 is for. The same goes for relations whose source/target types
fall outside the relation's declared domain/range: warned about, never dropped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from ..chunking import DEFAULT_MAX_CHARS, DEFAULT_TARGET_CHARS, chunk_document
from ..models import ChunkExtraction, ExtractedEntity, ExtractedRelation, ParsedDocument, TypeProposal
from ..ontology import Ontology


_DOMAIN_MARK = "outside declared domain"
_RANGE_MARK = "outside declared range"
_MISSING_MARK = "missing"


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

    def domain_range_warnings(self) -> list[str]:
        return [w for w in self.warnings if _DOMAIN_MARK in w or _RANGE_MARK in w]

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
    progress: Callable[[str], None] | None = None,
) -> DocumentExtraction:
    chunks = chunk_document(parsed, target_chars=target_chars, max_chars=max_chars)
    doc = DocumentExtraction(source_id=parsed.source_id, parse_id=parsed.parse_id)
    entity_names = ontology.entity_names()
    relation_names = ontology.relation_names()
    constraints = {r.name: (r.domain, r.range) for r in ontology.relation_types}

    total = len(chunks)
    started = time.monotonic()

    def report(n: int, text: str) -> None:
        if progress is not None:
            elapsed = time.monotonic() - started
            progress(f"chunk {n}/{total} · {text} · {elapsed:.0f}s elapsed")

    for n, chunk in enumerate(chunks, start=1):
        try:
            result = extractor.extract(ontology, chunk)
        except Exception as exc:  # noqa: BLE001 — isolate one chunk's failure
            # Persist progress on the other chunks rather than losing the whole
            # document to one transient failure (e.g. quota exhaustion).
            doc.failed_chunks.append((chunk.chunk_id, f"{type(exc).__name__}: {exc}"))
            report(n, f"{chunk.chunk_id} FAILED ({type(exc).__name__}: {exc})")
            continue
        doc.chunks.append(result)
        doc.warnings.extend(
            _validate(result, chunk.chunk_id, entity_names, relation_names, constraints)
        )
        report(
            n,
            f"{len(doc.all_entities())} entities/{len(doc.all_relations())} relations/"
            f"{len(doc.all_proposals())} proposals (running totals)",
        )
    return doc


def _validate(
    result: ChunkExtraction,
    chunk_id: str,
    entity_names: set[str],
    relation_names: set[str],
    constraints: dict[str, tuple[list[str], list[str]]],
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
        elif r.relation_type in constraints:
            domain, range_ = constraints[r.relation_type]
            for t, allowed, mark, name in (
                (r.source_type, domain, _DOMAIN_MARK, r.source_name),
                (r.target_type, range_, _RANGE_MARK, r.target_name),
            ):
                if not t and _constrained(allowed):
                    role = "domain" if mark == _DOMAIN_MARK else "range"
                    warnings.append(
                        f"{chunk_id}: relation '{r.relation_type}' {_MISSING_MARK} "
                        f"{role} type (for '{name}'); cannot check it"
                    )
                elif _outside(t, allowed):
                    warnings.append(
                        f"{chunk_id}: relation '{r.relation_type}' type '{t}' "
                        f"(for '{name}') {mark} [{', '.join(allowed)}]"
                    )
        for role, t in (("source", r.source_type), ("target", r.target_type)):
            if t and t not in entity_names:
                warnings.append(
                    f"{chunk_id}: off-ontology {role} type '{t}' on relation '{r.relation_type}'"
                )
    return warnings


def _outside(entity_type: str, allowed: list[str]) -> bool:
    """True if the type is outside a declared domain/range.

    An empty list or a "*" entry means unconstrained. An empty entity type is
    never "outside" here; `_validate` reports it separately as a missing type.
    """
    if not _constrained(allowed) or not entity_type:
        return False
    return entity_type not in allowed


def _constrained(allowed: list[str]) -> bool:
    return bool(allowed) and "*" not in allowed
