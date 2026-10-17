"""Serialisation of extraction output for review and the M0 eval seed.

Two forms:
* JSON — the machine-readable record (stored output stays JSON per the config
  format decision), preserving full provenance for every item;
* Markdown — a human review sheet grouping entities/relations/proposals with
  their supporting quotes and provenance, which is what the M0 manual review
  reads and annotates to seed the eval set (DECISIONS.md: eval set timing).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .extraction.pipeline import DocumentExtraction
from .models import TypeProposal


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
