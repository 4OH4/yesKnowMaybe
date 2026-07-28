"""Serialisation of extraction output for review and the M0 eval seed.

Two forms:
* JSON — the machine-readable record (stored output stays JSON per the config
  format decision), preserving full provenance for every item;
* Markdown — a human review sheet grouping entities/relations/proposals with
  their supporting quotes and provenance, which is what the M0 manual review
  reads and annotates to seed the eval set (DECISIONS.md: eval set timing).

Reading back is dataclass reconstruction, not dict-poking: later stages
(alignment, then ingestion) work on the same types the pipeline produced, so
there is one representation of a fact in the codebase rather than two.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .extraction.pipeline import DocumentExtraction
from .models import (
    AlignmentStatus,
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelation,
    Provenance,
    Span,
    TypeProposal,
)


@dataclass
class ProposalGroup:
    """All proposals sharing a (kind, name), for the review view only."""

    kind: str
    name: str
    proposals: list[TypeProposal] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.proposals)


def group_proposals(proposals: list[TypeProposal]) -> list[ProposalGroup]:
    """Group by (kind, name), most frequent first (ties keep first-seen order).

    Presentation only: the groups reference the original records, which stay
    complete and unaggregated in the JSON output.
    """
    groups: dict[tuple[str, str], ProposalGroup] = {}
    for pr in proposals:
        key = (pr.kind, pr.name)
        groups.setdefault(key, ProposalGroup(pr.kind, pr.name)).proposals.append(pr)
    return sorted(groups.values(), key=lambda g: -g.count)


def document_to_dict(doc: DocumentExtraction) -> dict:
    return {
        "source_id": doc.source_id,
        "parse_id": doc.parse_id,
        "warnings": doc.warnings,
        "failed_chunks": [{"chunk_id": cid, "error": err} for cid, err in doc.failed_chunks],
        "entities": [asdict(e) for e in doc.all_entities()],
        "relations": [asdict(r) for r in doc.all_relations()],
        "proposals": [asdict(p) for p in doc.all_proposals()],
    }


def write_json(doc: DocumentExtraction, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(document_to_dict(doc), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def _provenance_from_dict(d: dict) -> Provenance:
    span = d.get("span")
    return Provenance(
        source_id=d["source_id"],
        chunk_id=d["chunk_id"],
        section_refs=list(d.get("section_refs") or []),
        supporting_quote=d.get("supporting_quote", ""),
        extraction_model=d.get("extraction_model", ""),
        extracted_at=d["extracted_at"],
        alignment_status=AlignmentStatus(d.get("alignment_status", "pending")),
        span=Span(**span) if span else None,
    )


def _merge_chunk_order(sequences: list[list[str]]) -> list[str]:
    """Merge per-list chunk sequences into one order consistent with all of them.

    Each of the entity/relation/proposal lists is grouped by chunk in chunk
    order, so each yields the true order *restricted to* the chunks it mentions.
    Taking first-seen order from one list alone gets this wrong — a chunk that
    produced only relations would sort after a later chunk that produced
    entities, silently reordering facts on rewrite. Merging all three recovers
    the original order.

    Chunks that share no list with each other cannot be ordered relative to one
    another; any linear extension is returned, which still regenerates each list
    identically. Every id is emitted exactly once.
    """
    seqs = [list(dict.fromkeys(s)) for s in sequences]
    order: list[str] = []
    while any(seqs):
        for seq in seqs:
            # Only take a head that no other sequence still expects later.
            if not seq or any(seq[0] in other[1:] for other in seqs):
                continue
            head = seq[0]
            break
        else:
            # Orderings contradict each other, so the file was not written by
            # document_to_dict. Take any head: order is best-effort, but no
            # fact is lost.
            head = next(s[0] for s in seqs if s)
        order.append(head)
        # Remove wherever it appears, not just where it leads: in the fallback
        # branch the id is mid-sequence elsewhere, and leaving it there would
        # emit it a second time as a duplicate, empty chunk.
        for other in seqs:
            if head in other:
                other.remove(head)
    return order


def document_from_dict(data: dict) -> DocumentExtraction:
    """Rebuild a :class:`DocumentExtraction` from :func:`document_to_dict` output.

    The JSON form flattens the per-chunk grouping, so it is rebuilt from each
    item's ``provenance.chunk_id``. Chunk order is recovered by merging the
    orderings implied by all three lists (see :func:`_merge_chunk_order`), which
    makes write → read → write byte-stable.
    """
    doc = DocumentExtraction(
        source_id=data["source_id"],
        parse_id=data["parse_id"],
        warnings=list(data.get("warnings") or []),
        failed_chunks=[
            (f["chunk_id"], f["error"]) for f in data.get("failed_chunks") or []
        ],
    )

    doc.chunks.extend(
        ChunkExtraction(chunk_id=cid)
        for cid in _merge_chunk_order(
            [
                [item["provenance"]["chunk_id"] for item in data.get(key) or []]
                for key in ("entities", "relations", "proposals")
            ]
        )
    )
    by_chunk = {c.chunk_id: c for c in doc.chunks}

    def chunk_for(chunk_id: str) -> ChunkExtraction:
        return by_chunk[chunk_id]

    for e in data.get("entities") or []:
        prov = _provenance_from_dict(e["provenance"])
        chunk_for(prov.chunk_id).entities.append(
            ExtractedEntity(
                entity_type=e["entity_type"],
                name=e["name"],
                provenance=prov,
                attributes=dict(e.get("attributes") or {}),
            )
        )
    for r in data.get("relations") or []:
        prov = _provenance_from_dict(r["provenance"])
        chunk_for(prov.chunk_id).relations.append(
            ExtractedRelation(
                relation_type=r["relation_type"],
                source_name=r["source_name"],
                source_type=r["source_type"],
                target_name=r["target_name"],
                target_type=r["target_type"],
                provenance=prov,
            )
        )
    for pr in data.get("proposals") or []:
        prov = _provenance_from_dict(pr["provenance"])
        chunk_for(prov.chunk_id).proposals.append(
            TypeProposal(
                kind=pr["kind"],
                name=pr["name"],
                description=pr.get("description", ""),
                justification=pr.get("justification", ""),
                provenance=prov,
            )
        )
    return doc


def read_json(path: str | Path) -> DocumentExtraction:
    """Load a previously-written extraction output file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No extraction output at {p}. Run `dikms extract` first."
        )
    return document_from_dict(json.loads(p.read_text(encoding="utf-8")))


def write_review_markdown(doc: DocumentExtraction, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Extraction review — {doc.source_id}",
        "",
        f"- Parse: `{doc.parse_id}`",
        f"- Entities: {len(doc.all_entities())} · "
        f"Relations: {len(doc.all_relations())} · "
        f"Proposals: {len(doc.all_proposals())} "
        f"({len(group_proposals(doc.all_proposals()))} unique) · "
        f"Warnings: {len(doc.warnings)}",
        "",
        "> Annotate inline (✓/✗/note) — this sheet seeds the eval set.",
        "",
    ]

    if doc.warnings:
        lines += ["## Warnings", ""]
        lines += [f"- {w}" for w in doc.warnings]
        lines.append("")

    lines += ["## Entities", ""]
    for e in doc.all_entities():
        prov = e.provenance
        lines.append(f"- **{e.entity_type}**: {e.name}")
        if e.attributes:
            lines.append(f"  - attributes: {e.attributes}")
        lines.append(f"  - quote: “{prov.supporting_quote}”")
        lines.append(f"  - provenance: {prov.chunk_id} ({', '.join(prov.section_refs) or 'no ref'})")

    lines += ["", "## Relations", ""]
    for r in doc.all_relations():
        prov = r.provenance
        lines.append(
            f"- **{r.relation_type}**: {r.source_type}:{r.source_name} → "
            f"{r.target_type}:{r.target_name}"
        )
        lines.append(f"  - quote: “{prov.supporting_quote}”")
        lines.append(f"  - provenance: {prov.chunk_id} ({', '.join(prov.section_refs) or 'no ref'})")

    if doc.all_proposals():
        lines += ["", "## Proposed new types (HITL — not committed)", ""]
        for g in group_proposals(doc.all_proposals()):
            first = g.proposals[0]
            chunks = sorted({pr.provenance.chunk_id for pr in g.proposals})
            lines.append(
                f"- **{g.kind}**: {g.name} — {first.description} "
                f"(proposed ×{g.count} in {len(chunks)} chunk(s): {', '.join(chunks)})"
            )
            seen: set[tuple[str, str, str, str]] = set()
            for pr in g.proposals:
                key = (
                    pr.provenance.chunk_id,
                    pr.description,
                    pr.justification,
                    pr.provenance.supporting_quote,
                )
                if key in seen:
                    continue
                seen.add(key)
                head = f"  - {key[0]}"
                if pr.description != first.description:
                    head += f" · description: {pr.description}"
                lines.append(head)
                lines.append(f"    - justification: {pr.justification}")
                lines.append(f"    - quote: “{key[3]}”")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
