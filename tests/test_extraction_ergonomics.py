"""Proposal aggregation (review view) and per-chunk progress callback."""

from __future__ import annotations

import json
import random

from dikms.extraction.pipeline import DocumentExtraction, extract_document
from dikms.models import (
    ChunkExtraction,
    ParsedDocument,
    Provenance,
    SourceFormat,
    TypeProposal,
)
from dikms.ontology import EntityType, Ontology
from dikms.output import document_to_dict, group_proposals, write_review_markdown


def _prov(chunk: str, quote: str) -> Provenance:
    return Provenance(
        source_id="s", chunk_id=chunk, section_refs=[], supporting_quote=quote,
        extraction_model="stub",
    )


def _prop(kind: str, name: str, chunk: str, quote: str = "q") -> TypeProposal:
    return TypeProposal(kind, name, f"{name} desc", "because", _prov(chunk, quote))


def _doc(per_chunk: list[list[TypeProposal]]) -> DocumentExtraction:
    doc = DocumentExtraction(source_id="s", parse_id="p")
    for i, props in enumerate(per_chunk):
        doc.chunks.append(ChunkExtraction(chunk_id=f"c{i}", proposals=props))
    return doc


def test_groups_sorted_by_count_with_all_quotes(tmp_path):
    doc = _doc([
        [_prop("entity", "A", "c0", "qa0"), _prop("entity", "B", "c0", "qb")],
        [_prop("entity", "B", "c1", "qb1")],
        [_prop("entity", "B", "c2", "qb2"), _prop("relation", "A", "c2", "qr")],
    ])
    groups = group_proposals(doc.all_proposals())
    assert [(g.kind, g.name, g.count) for g in groups] == [
        ("entity", "B", 3), ("entity", "A", 1), ("relation", "A", 1),
    ]
    md = write_review_markdown(doc, tmp_path / "r.md").read_text(encoding="utf-8")
    assert "proposed ×3 in 3 chunk(s): c0, c1, c2" in md
    for q in ("qb", "qb1", "qb2"):
        assert f"“{q}”" in md
    assert md.count("**entity**: B") == 1


def test_json_keeps_every_proposal_with_provenance():
    doc = _doc([[_prop("entity", "A", "c0")], [_prop("entity", "A", "c1")]])
    out = json.loads(json.dumps(document_to_dict(doc)))
    assert [p["provenance"]["chunk_id"] for p in out["proposals"]] == ["c0", "c1"]


def test_grouping_is_a_partition():
    rng = random.Random(7)
    for _ in range(200):
        proposals = [
            _prop(rng.choice(["entity", "relation"]), rng.choice("abc"),
                  rng.choice(["c0", "c1"]))
            for _ in range(rng.randint(0, 30))
        ]
        groups = group_proposals(proposals)
        assert sum(g.count for g in groups) == len(proposals)
        assert len({(g.kind, g.name) for g in groups}) == len(groups)
        counts = [g.count for g in groups]
        assert counts == sorted(counts, reverse=True)
        assert all(
            pr.kind == g.kind and pr.name == g.name
            for g in groups for pr in g.proposals
        )
        # Every original record object is retained exactly once.
        assert sorted(id(pr) for g in groups for pr in g.proposals) == sorted(
            id(pr) for pr in proposals
        )


class _Stub:
    def extract(self, ontology, chunk):
        if chunk.ordinal == 1:
            raise RuntimeError("boom")
        prop = _prop("entity", "A", chunk.chunk_id)
        return ChunkExtraction(chunk_id=chunk.chunk_id, proposals=[prop])


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        source_id="s", fmt=SourceFormat.HTML, parser="t", parse_id="p",
        text="\n\n".join(f"Paragraph {i}. " + "x" * 80 for i in range(4)),
    )


def test_progress_reports_each_chunk_and_failures():
    ont = Ontology(
        version=1,
        entity_types=[EntityType(name="T", description="")],
        relation_types=[],
    )
    msgs: list[str] = []
    doc = extract_document(
        _parsed(), ont, _Stub(), target_chars=100, max_chars=150, progress=msgs.append
    )
    n = len(msgs)
    assert n >= 2 and len(doc.failed_chunks) == 1
    for i, m in enumerate(msgs, 1):
        assert m.startswith(f"chunk {i}/{n} · ") and "s elapsed" in m
    assert sum("FAILED" in m for m in msgs) == 1
    # No callback: same result, nothing required.
    again = extract_document(_parsed(), ont, _Stub(), target_chars=100, max_chars=150)
    assert len(again.chunks) == len(doc.chunks)


def test_review_sheet_shows_every_distinct_description(tmp_path):
    props = [
        TypeProposal(
            "relation", "funds", f"desc {i}", f"why {i}", _prov(f"c{i}", f"quote {i}")
        )
        for i in range(4)
    ]
    # Duplicate of the first occurrence: collapses.
    props.append(
        TypeProposal("relation", "funds", "desc 0", "why 0", _prov("c0", "quote 0"))
    )
    doc = _doc([[p] for p in props])
    md = write_review_markdown(doc, tmp_path / "r.md").read_text(encoding="utf-8")
    for i in range(4):
        assert f"desc {i}" in md
        assert f"quote {i}" in md
        assert f"why {i}" in md
    assert "Proposals: 5 (1 unique)" in md
