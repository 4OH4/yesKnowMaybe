"""Offline re-validation of committed extraction output against the ontology.

Reuses the pipeline's `_validate` per item (no second validator), so each
warning is attributed to the entity or relation type that raised it. Reads
only; the input files are never modified.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .extraction.pipeline import (
    _CANNOT_CHECK_MARK,
    _DOMAIN_MARK,
    _RANGE_MARK,
    _validate,
    constraint_map,
)
from .models import ChunkExtraction, ExtractedEntity, ExtractedRelation, Provenance
from .ontology import Ontology

KINDS = ("domain/range", "off-ontology", "missing type")


def classify(warning: str) -> str:
    if _CANNOT_CHECK_MARK in warning:
        return "missing type"
    if _DOMAIN_MARK in warning or _RANGE_MARK in warning:
        return "domain/range"
    return "off-ontology"


@dataclass
class FileReport:
    path: str
    relations_checked: int = 0
    entities_checked: int = 0
    # (kind, item type, warning text); the text carries the chunk id.
    findings: list[tuple[str, str, str]] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.findings if k == kind)

    def by_type(self, kind: str) -> Counter:
        return Counter(t for k, t, _ in self.findings if k == kind)


def _prov(d: dict) -> Provenance:
    return Provenance(
        source_id=d.get("source_id", ""),
        chunk_id=d.get("chunk_id", ""),
        section_refs=list(d.get("section_refs", [])),
        supporting_quote=d.get("supporting_quote", ""),
        extraction_model=d.get("extraction_model", ""),
    )


def validate_file(path: str | Path, ontology: Ontology) -> FileReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entity_names = ontology.entity_names()
    relation_names = ontology.relation_names()
    constraints = constraint_map(ontology)
    report = FileReport(path=str(path))

    def check(result: ChunkExtraction, chunk_id: str, item_type: str) -> None:
        for w in _validate(result, chunk_id, entity_names, relation_names, constraints):
            report.findings.append((classify(w), item_type, w))

    for e in data.get("entities", []):
        prov = _prov(e.get("provenance", {}))
        ent = ExtractedEntity(
            entity_type=e.get("entity_type", ""),
            name=e.get("name", ""),
            provenance=prov,
        )
        report.entities_checked += 1
        chunk = ChunkExtraction(chunk_id=prov.chunk_id, entities=[ent])
        check(chunk, prov.chunk_id, ent.entity_type)
    for r in data.get("relations", []):
        prov = _prov(r.get("provenance", {}))
        rel = ExtractedRelation(
            relation_type=r.get("relation_type", ""),
            source_name=r.get("source_name", ""),
            source_type=r.get("source_type", ""),
            target_name=r.get("target_name", ""),
            target_type=r.get("target_type", ""),
            provenance=prov,
        )
        report.relations_checked += 1
        chunk = ChunkExtraction(chunk_id=prov.chunk_id, relations=[rel])
        check(chunk, prov.chunk_id, rel.relation_type)
    return report


def _type_lines(counter: Counter) -> list[str]:
    ordered = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    return [f"    {t or '(none)'}: {n}" for t, n in ordered]


def format_reports(reports: list[FileReport], verbose: bool = False) -> str:
    lines: list[str] = []
    for rep in reports:
        lines.append(
            f"{rep.path}: {rep.relations_checked} relations, "
            f"{rep.entities_checked} entities checked"
        )
        for kind in KINDS:
            lines.append(f"  {kind}: {rep.count(kind)}")
            lines += _type_lines(rep.by_type(kind))
        if verbose:
            lines += [f"  warn: {w}" for _, _, w in rep.findings]
    rel = sum(r.relations_checked for r in reports)
    ent = sum(r.entities_checked for r in reports)
    lines.append(
        f"total: {rel} relations, {ent} entities checked across {len(reports)} file(s)"
    )
    for kind in KINDS:
        by_type: Counter = Counter()
        for r in reports:
            by_type.update(r.by_type(kind))
        lines.append(f"  {kind}: {sum(by_type.values())}")
        lines += _type_lines(by_type)
    return "\n".join(lines)
