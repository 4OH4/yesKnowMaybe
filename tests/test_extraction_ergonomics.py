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
    def extract(self, ontology, chunk, on_retry=None):
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


class _Throttled(Exception):
    code = 429
    status = "RESOURCE_EXHAUSTED"


def test_pipeline_formats_retry_messages_with_chunk_number():
    class Retrying:
        def extract(self, ontology, chunk, on_retry=None):
            if on_retry is not None:
                on_retry(2, 15, _Throttled())
            return ChunkExtraction(chunk_id=chunk.chunk_id)

    ont = Ontology(version=1, entity_types=[], relation_types=[])
    msgs: list[str] = []
    extract_document(
        _parsed(), ont, Retrying(), target_chars=100, max_chars=150,
        progress=msgs.append,
    )
    retries = [m for m in msgs if "retry" in m]
    assert retries and retries[0].startswith("chunk 1/")
    assert "retry 2 in 15s (429 RESOURCE_EXHAUSTED)" in retries[0]


def test_extract_document_with_progress_over_each_stub_shape():
    # A stub that does not accept on_retry would TypeError on every chunk and
    # land in failed_chunks; fail loudly if any stub shape drifts.
    class Plain:
        def extract(self, ontology, chunk, on_retry=None):
            return ChunkExtraction(chunk_id=chunk.chunk_id)

    ont = Ontology(version=1, entity_types=[], relation_types=[])
    for stub in (Plain(), _Stub()):
        doc = extract_document(
            _parsed(), ont, stub, target_chars=100, max_chars=150,
            progress=lambda m: None,
        )
        assert all("TypeError" not in e for _, e in doc.failed_chunks)
        assert doc.chunks


def test_gemini_extract_passes_on_retry_through(monkeypatch):
    from google.genai import errors

    from dikms.extraction import client as client_mod
    from dikms.models import Chunk

    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)

    class Models:
        calls = 0

        def generate_content(self, **kw):
            Models.calls += 1
            if Models.calls == 1:
                raise errors.APIError(503, {"error": {"status": "UNAVAILABLE"}})

            class R:
                text = '{"entities": [], "relations": [], "proposals": []}'

            return R()

    class FakeClient:
        models = Models()

    ex = client_mod.GeminiExtractor("p", "l", "m", retry_backoff=(7,))
    ex._client = FakeClient()
    chunk = Chunk(
        chunk_id="c1", source_id="s", parse_id="p", ordinal=1, start=0, end=4,
        text="text",
        section_refs=[],
    )
    ont = Ontology(version=1, entity_types=[], relation_types=[])
    seen = []
    result = ex.extract(ont, chunk, on_retry=lambda k, d, e: seen.append((k, d)))
    assert result.chunk_id == "c1" and seen == [(1, 7)]


def test_on_retry_called_per_backoff_with_configured_delays(monkeypatch):

    from google.genai import errors

    from dikms.extraction import client as client_mod

    slept: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", slept.append)

    class FakeModels:
        calls = 0

        def generate_content(self, **kw):
            FakeModels.calls += 1
            if FakeModels.calls <= 2:
                raise errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
            return "ok"

    class FakeClient:
        models = FakeModels()

    ex = client_mod.GeminiExtractor("p", "l", "m", retry_backoff=(5, 15, 30))
    seen = []
    out = ex._generate_with_retry(
        FakeClient(), "c", None, lambda k, d, e: seen.append((k, d, e.code))
    )
    assert out == "ok"
    assert seen == [(1, 5, 429), (2, 15, 429)]
    assert slept == [5, 15]
