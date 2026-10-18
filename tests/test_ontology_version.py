"""The ontology version is recorded once per document extraction."""

from __future__ import annotations

import json
from pathlib import Path

from dikms.extraction.pipeline import extract_document
from dikms.models import ChunkExtraction, ParsedDocument, SourceFormat
from dikms.ontology import Ontology
from dikms.output import ontology_version_from_dict, write_json


class _Fake:
    def extract(self, ontology, chunk):
        return ChunkExtraction(chunk_id=chunk.chunk_id)


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        source_id="s", fmt=SourceFormat.HTML, parser="t", parse_id="p",
        text="Paragraph one. " + "x" * 80,
    )


def test_new_extraction_records_configured_version_and_round_trips(
    ontology: Ontology, tmp_path: Path
):
    doc = extract_document(_parsed(), ontology, _Fake())
    assert doc.ontology_version == ontology.version
    data = json.loads(write_json(doc, tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["ontology_version"] == ontology.version
    assert ontology_version_from_dict(data) == ontology.version


def test_m0_format_file_reads_as_unknown():
    m0 = {
        "source_id": "s", "parse_id": "p", "warnings": [], "failed_chunks": [],
        "entities": [], "relations": [], "proposals": [],
    }
    assert ontology_version_from_dict(m0) is None
