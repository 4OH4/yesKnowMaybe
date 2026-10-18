"""The extraction output file is read back by alignment and, next, by ingestion.

These tests pin the round-trip, since two stages now depend on the JSON form
meaning exactly what it did when it was written — including the provenance
fields that are the traceability floor.
"""

from __future__ import annotations

import pytest
from test_alignment import PARSE_ID, entity, make_doc, provenance

from dikms.extraction.pipeline import DocumentExtraction
from dikms.models import AlignmentStatus, ChunkExtraction, ExtractedRelation, Span
from dikms.output import (
    MANDATORY_PROVENANCE_FIELDS,
    _merge_chunk_order,
    document_from_dict,
    document_to_dict,
    read_json,
    write_json,
)


@pytest.mark.parametrize(
    "sequences",
    [
        [["A", "B"], ["B", "A"], []],  # directly contradictory
        [["A", "B"], ["B", "C"], ["C", "A"]],  # cyclic
        [["A", "C"], ["B"], []],  # no shared chunk to order B against
        [["A", "B", "C"], ["A", "C"], ["B"]],  # consistent
        [[], [], []],
    ],
)
def test_merge_chunk_order_emits_every_id_exactly_once(sequences):
    """Including the fallback branch for orderings that cannot all be satisfied:
    the result must still be a permutation, never a duplicated (and therefore
    empty, phantom) chunk."""
    merged = _merge_chunk_order([list(s) for s in sequences])
    expected = {cid for seq in sequences for cid in seq}
    assert sorted(merged) == sorted(expected)
    assert len(merged) == len(set(merged))


def test_merge_chunk_order_respects_consistent_orderings():
    # B appears only in the second list, between A and C in the first.
    assert _merge_chunk_order([["A", "C"], ["A", "B", "C"], []]) == ["A", "B", "C"]


def test_write_read_write_is_stable(tmp_path):
    doc = make_doc()
    path = write_json(doc, tmp_path / "doc.json")
    first = path.read_text(encoding="utf-8")

    reloaded = read_json(path)
    write_json(reloaded, path)

    assert path.read_text(encoding="utf-8") == first


def test_reader_restores_provenance_and_spans(tmp_path):
    doc = make_doc()
    prov = doc.all_entities()[0].provenance
    prov.span = Span(parse_id=PARSE_ID, start=4, end=31)
    prov.alignment_status = AlignmentStatus.ALIGNED

    reloaded = read_json(write_json(doc, tmp_path / "doc.json"))
    restored = reloaded.all_entities()[0].provenance

    assert restored.span == Span(parse_id=PARSE_ID, start=4, end=31)
    assert restored.alignment_status is AlignmentStatus.ALIGNED
    assert restored.source_id == prov.source_id
    assert restored.chunk_id == prov.chunk_id
    assert restored.section_refs == prov.section_refs
    assert restored.supporting_quote == prov.supporting_quote
    assert restored.extraction_model == prov.extraction_model
    assert restored.extracted_at == prov.extracted_at

    # Facts that were never aligned come back with no span, not a zero span.
    untouched = reloaded.all_relations()[0].provenance
    assert untouched.span is None
    assert untouched.alignment_status is AlignmentStatus.PENDING


@pytest.mark.parametrize("field", MANDATORY_PROVENANCE_FIELDS)
@pytest.mark.parametrize("kind", ["entities", "relations", "proposals"])
def test_incomplete_provenance_fails_to_load(field, kind):
    """No mandatory provenance field is ever defaulted on read: a fact missing
    one must fail loudly, not load with an empty value and drift into the
    store (#10 reads through this function)."""
    data = document_to_dict(make_doc())
    del data[kind][0]["provenance"][field]
    with pytest.raises(KeyError, match=field):
        document_from_dict(data)


def test_bare_string_section_refs_fails_to_load():
    data = document_to_dict(make_doc())
    data["entities"][0]["provenance"]["section_refs"] = "page 1"
    with pytest.raises(ValueError, match="section_refs"):
        document_from_dict(data)


def test_unaligned_provenance_still_loads_without_alignment_fields():
    """The alignment fields are the only optional ones: an M0-era file that
    predates them loads as PENDING with no span."""
    data = document_to_dict(make_doc())
    for item in data["entities"]:
        del item["provenance"]["alignment_status"]
        del item["provenance"]["span"]
    prov = document_from_dict(data).all_entities()[0].provenance
    assert prov.alignment_status is AlignmentStatus.PENDING
    assert prov.span is None


def test_reader_preserves_every_fact_and_its_chunk_grouping(tmp_path):
    doc = make_doc()
    reloaded = read_json(write_json(doc, tmp_path / "doc.json"))

    assert [c.chunk_id for c in reloaded.chunks] == [c.chunk_id for c in doc.chunks]
    assert len(reloaded.all_entities()) == len(doc.all_entities())
    assert len(reloaded.all_relations()) == len(doc.all_relations())
    assert len(reloaded.all_proposals()) == len(doc.all_proposals())
    assert document_to_dict(reloaded) == document_to_dict(doc)


def test_chunk_order_survives_a_chunk_with_no_entities(tmp_path):
    """Regression: chunk order cannot be inferred from the entity list alone.

    Chunk A produced only a relation, chunk B produced both. Recovering the
    order from entities alone puts B first and silently reorders the relations
    on rewrite — and `dikms align` rewrites these files in place.
    """
    a, b = f"{PARSE_ID}::0000", f"{PARSE_ID}::0001"

    def relation(quote: str, chunk_id: str, name: str) -> ExtractedRelation:
        return ExtractedRelation(
            relation_type="develops",
            source_name=name,
            source_type="Organisation",
            target_name="Something",
            target_type="Programme",
            provenance=provenance(quote, chunk_id),
        )

    doc = DocumentExtraction(
        source_id="test-source",
        parse_id=PARSE_ID,
        chunks=[
            ChunkExtraction(chunk_id=a, relations=[relation("first", a, "A")]),
            ChunkExtraction(
                chunk_id=b,
                entities=[entity("only entity", b)],
                relations=[relation("second", b, "B")],
            ),
        ],
    )

    path = write_json(doc, tmp_path / "doc.json")
    original = path.read_text(encoding="utf-8")
    reloaded = read_json(path)

    assert [c.chunk_id for c in reloaded.chunks] == [a, b]
    assert [r.source_name for r in reloaded.all_relations()] == ["A", "B"]
    write_json(reloaded, path)
    assert path.read_text(encoding="utf-8") == original
