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

KINDS = ("domain/range", "off-ontology", "missing type", "missing provenance")


def classify(warning: str) -> str:
    if _CANNOT_CHECK_MARK in warning:
        return "missing type"
    if _DOMAIN_MARK in warning or _RANGE_MARK in warning:
        return "domain/range"
    return "off-ontology"


@dataclass
class Finding:
    kind: str
    label: str  # relation type, or "entity <type>" for entities
    text: str
    item: tuple[str, int]  # ("relation" | "entity", index in the file)


@dataclass
class FileReport:
    path: str
    relations_checked: int = 0
    entities_checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def warnings(self, kind: str) -> int:
        return sum(1 for f in self.findings if f.kind == kind)

    def items(self, kind: str, which: str | None = None) -> int:
        """Distinct items with at least one finding of this kind."""
        return len({f.item for f in self.findings
                    if f.kind == kind and (which is None or f.item[0] == which)})

    def by_type(self, kind: str) -> Counter:
        """Distinct items per type label (one item has one type)."""
        seen = {(f.label, f.item) for f in self.findings if f.kind == kind}
        return Counter(label for label, _ in seen)


def _prov(d: dict) -> Provenance:
    return Provenance(
        source_id=d.get("source_id", ""),
        chunk_id=d.get("chunk_id", ""),
        section_refs=list(d.get("section_refs", [])),
        supporting_quote=d.get("supporting_quote", ""),
        extraction_model=d.get("extraction_model", ""),
    )


def _items(data, key: str) -> list:
    if not isinstance(data, dict):
        raise ValueError("top level is not a JSON object")
    items = data.get(key)
    if not isinstance(items, list):
        raise ValueError(f"missing or non-list {key!r}")
    return items


def _provenance(item, where: str) -> dict | None:
    """Provenance dict; None if absent or no chunk_id; ValueError if malformed."""
    if not isinstance(item, dict):
        raise ValueError(f"{where} is not an object")
    prov = item.get("provenance")
    if prov is None:
        return None
    if not isinstance(prov, dict):
        raise ValueError(f"{where}: 'provenance' is not an object")
    return prov if prov.get("chunk_id") else None


def validate_file(path: str | Path, ontology: Ontology) -> FileReport:
    """Raises OSError or ValueError (bad JSON or structure) for the caller to report."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entities = _items(data, "entities")
    relations = _items(data, "relations")
    entity_names = ontology.entity_names()
    relation_names = ontology.relation_names()
    constraints = constraint_map(ontology)
    report = FileReport(path=str(path))

    def check(result: ChunkExtraction, chunk_id: str, label: str, item) -> None:
        for w in _validate(result, chunk_id, entity_names, relation_names, constraints):
            report.findings.append(Finding(classify(w), label, w, item))

    def no_prov(label: str, where: str, item) -> None:
        text = f"{where}: missing provenance or chunk_id"
        report.findings.append(Finding("missing provenance", label, text, item))

    for i, e in enumerate(entities):
        pd = _provenance(e, f"entities[{i}]")
        report.entities_checked += 1
        label = f"entity {e.get('entity_type', '')}"
        if pd is None:
            no_prov(label, f"entities[{i}]", ("entity", i))
            continue
        prov = _prov(pd)
        ent = ExtractedEntity(
            entity_type=e.get("entity_type", ""),
            name=e.get("name", ""),
            provenance=prov,
        )
        chunk = ChunkExtraction(chunk_id=prov.chunk_id, entities=[ent])
        check(chunk, prov.chunk_id, label, ("entity", i))
    for i, r in enumerate(relations):
        pd = _provenance(r, f"relations[{i}]")
        report.relations_checked += 1
        label = r.get("relation_type", "")
        if pd is None:
            no_prov(label, f"relations[{i}]", ("relation", i))
            continue
        prov = _prov(pd)
        rel = ExtractedRelation(
            relation_type=label,
            source_name=r.get("source_name", ""),
            source_type=r.get("source_type", ""),
            target_name=r.get("target_name", ""),
            target_type=r.get("target_type", ""),
            provenance=prov,
        )
        chunk = ChunkExtraction(chunk_id=prov.chunk_id, relations=[rel])
        check(chunk, prov.chunk_id, label, ("relation", i))
    return report


def _type_lines(counter: Counter) -> list[str]:
    ordered = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    return [f"    {t or '(none)'}: {n}" for t, n in ordered]


def _headline(kind: str, rel: int, ent: int, warnings: int) -> str:
    parts = [f"{rel} relations"]
    if ent:
        parts.append(f"{ent} entities")
    return f"  {kind}: {', '.join(parts)} ({warnings} warnings)"


def format_reports(reports: list[FileReport], verbose: bool = False) -> str:
    """Counts are distinct items: a relation breaking domain and range counts once."""
    lines: list[str] = []
    for rep in reports:
        lines.append(
            f"{rep.path}: {rep.relations_checked} relations, "
            f"{rep.entities_checked} entities checked"
        )
        for kind in KINDS:
            lines.append(_headline(kind, rep.items(kind, "relation"),
                                   rep.items(kind, "entity"), rep.warnings(kind)))
            lines += _type_lines(rep.by_type(kind))
        if verbose:
            lines += [f"  warn: {f.text}" for f in rep.findings]
    rel = sum(r.relations_checked for r in reports)
    ent = sum(r.entities_checked for r in reports)
    lines.append(
        f"total: {rel} relations, {ent} entities checked across {len(reports)} file(s)"
    )
    for kind in KINDS:
        by_type: Counter = Counter()
        for r in reports:
            by_type.update(r.by_type(kind))
        lines.append(_headline(
            kind,
            sum(r.items(kind, "relation") for r in reports),
            sum(r.items(kind, "entity") for r in reports),
            sum(r.warnings(kind) for r in reports),
        ))
        lines += _type_lines(by_type)
    return "\n".join(lines)
