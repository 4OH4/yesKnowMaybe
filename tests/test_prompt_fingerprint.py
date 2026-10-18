"""The prompt fingerprint is recorded once per document extraction (issue #26)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dikms.extraction import prompt
from dikms.extraction.pipeline import extract_document
from dikms.models import ChunkExtraction, ParsedDocument, SourceFormat
from dikms.ontology import Ontology
from dikms.output import prompt_fingerprint_from_dict, write_json, write_review_markdown


class _Fake:
    def extract(self, ontology, chunk):
        return ChunkExtraction(chunk_id=chunk.chunk_id)


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        source_id="s", fmt=SourceFormat.HTML, parser="t", parse_id="p",
        text="Paragraph one. " + "x" * 80,
    )


def test_fingerprint_is_stable_and_short_hex():
    fp = prompt.prompt_fingerprint()
    assert fp == prompt.prompt_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{12}", fp)


def _line_indices(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines()) if line.strip()]


def _edit_line(text: str, i: int) -> str:
    lines = text.splitlines()
    lines[i] += " x"
    return "\n".join(lines)


@pytest.mark.parametrize("i", _line_indices(prompt.SYSTEM_INSTRUCTION))
def test_changing_any_instruction_line_changes_fingerprint(monkeypatch, i):
    base = prompt.prompt_fingerprint()
    edited = _edit_line(prompt.SYSTEM_INSTRUCTION, i)
    monkeypatch.setattr(prompt, "SYSTEM_INSTRUCTION", edited)
    assert prompt.prompt_fingerprint() != base


@pytest.mark.parametrize("i", _line_indices(prompt._SCHEMA))
def test_changing_any_schema_line_changes_fingerprint(monkeypatch, i):
    base = prompt.prompt_fingerprint()
    monkeypatch.setattr(prompt, "_SCHEMA", _edit_line(prompt._SCHEMA, i))
    assert prompt.prompt_fingerprint() != base


def test_text_cannot_slide_between_instruction_and_schema(monkeypatch):
    base = prompt.prompt_fingerprint()
    monkeypatch.setattr(prompt, "SYSTEM_INSTRUCTION", prompt.SYSTEM_INSTRUCTION + "R")
    monkeypatch.setattr(prompt, "_SCHEMA", prompt._SCHEMA[1:])
    assert prompt.prompt_fingerprint() != base


def test_new_output_carries_fingerprint_and_review_header(
    ontology: Ontology, tmp_path: Path
):
    doc = extract_document(_parsed(), ontology, _Fake())
    fp = prompt.prompt_fingerprint()
    assert doc.prompt_fingerprint == fp
    data = json.loads(write_json(doc, tmp_path / "s.json").read_text(encoding="utf-8"))
    assert data["prompt_fingerprint"] == fp
    assert prompt_fingerprint_from_dict(data) == fp
    md_path = write_review_markdown(doc, tmp_path / "s.review.md")
    md = md_path.read_text(encoding="utf-8")
    assert f"`{fp}`" in md


def test_old_format_file_reads_as_unknown():
    old = {
        "source_id": "s", "parse_id": "p", "warnings": [], "failed_chunks": [],
        "entities": [], "relations": [], "proposals": [],
    }
    assert prompt_fingerprint_from_dict(old) is None
