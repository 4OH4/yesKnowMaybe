"""`dikms validate`: offline re-check of committed extraction output."""

from __future__ import annotations

import json

from dikms.cli import main
from dikms.extraction.pipeline import DocumentExtraction, _validate, constraint_map
from dikms.models import (
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelation,
    Provenance,
)
from dikms.ontology import EntityType, Ontology, RelationType
from dikms.output import document_to_dict
from dikms.validate import validate_file

TYPES = ["Organisation", "Programme", "Term"]
ONTOLOGY = Ontology(
    version=1,
    entity_types=[EntityType(name=t, description="") for t in TYPES],
    relation_types=[
        RelationType(
            name="rel", description="", domain=["Organisation"], range=["Programme"]
        ),
        RelationType(name="free", description="", domain=[], range=["*"]),
    ],
)


def _prov(chunk: str) -> Provenance:
    return Provenance(
        source_id="s", chunk_id=chunk, section_refs=[], supporting_quote="q",
        extraction_model="m",
    )


def _rel(rtype: str, src: str, tgt: str, chunk: str = "c1") -> ExtractedRelation:
    return ExtractedRelation(rtype, "a", src, "b", tgt, _prov(chunk))


def _write(tmp_path, relations, entities=()) -> str:
    doc = DocumentExtraction(source_id="s", parse_id="p")
    doc.chunks.append(
        ChunkExtraction(
            chunk_id="c1", relations=list(relations), entities=list(entities)
        )
    )
    path = tmp_path / "out.json"
    path.write_text(json.dumps(document_to_dict(doc)), encoding="utf-8")
    return str(path)


RELS = [
    _rel("rel", "Organisation", "Programme"),   # fine
    _rel("rel", "Term", "Programme"),           # domain
    _rel("rel", "Organisation", "Term"),        # range
    _rel("rel", "", "Programme"),               # missing type
    _rel("bogus", "Term", "Term"),              # off-ontology relation
    _rel("free", "Term", "Term"),               # unconstrained
    _rel("rel", "Organisation", "Alien"),       # range + off-ontology target type
]


def test_counts_by_kind_and_type(tmp_path):
    rep = validate_file(_write(tmp_path, RELS), ONTOLOGY)
    assert rep.relations_checked == len(RELS)
    assert rep.count("domain/range") == 3
    assert rep.count("missing type") == 1
    assert rep.count("off-ontology") == 2
    assert dict(rep.by_type("domain/range")) == {"rel": 3}


def test_matches_whole_chunk_validate(tmp_path):
    """Per-item attribution must not lose or add warnings versus the pipeline."""
    rep = validate_file(_write(tmp_path, RELS), ONTOLOGY)
    whole = _validate(
        ChunkExtraction(chunk_id="c1", relations=RELS), "c1",
        ONTOLOGY.entity_names(), ONTOLOGY.relation_names(), constraint_map(ONTOLOGY),
    )
    assert sorted(w for _, _, w in rep.findings) == sorted(whole)


def test_off_ontology_entity(tmp_path):
    ent = ExtractedEntity("Alien", "x", _prov("c1"))
    rep = validate_file(_write(tmp_path, [], [ent]), ONTOLOGY)
    assert rep.entities_checked == 1
    assert dict(rep.by_type("off-ontology")) == {"Alien": 1}


def test_cli_reports_and_exits_zero_without_modifying_input(tmp_path, capsys):
    path = _write(tmp_path, RELS)
    before = open(path, "rb").read()
    onto = tmp_path / "onto.yaml"
    onto.write_text(
        "version: 1\nentity_types:\n"
        + "".join(f"  - name: {t}\n    description: d\n" for t in TYPES)
        + "relation_types:\n"
        "  - name: rel\n    description: d\n"
        "    domain: [Organisation]\n    range: [Programme]\n"
        "  - name: free\n    description: d\n    domain: []\n    range: ['*']\n",
        encoding="utf-8",
    )
    code = main(["validate", path, "--ontology", str(onto), "--verbose"])
    out = capsys.readouterr().out
    assert code == 0
    assert "domain/range: 3" in out and "missing type: 1" in out
    assert "total: 7 relations" in out
    assert "warn: c1:" in out
    assert open(path, "rb").read() == before


def test_cli_missing_file_is_an_error(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "nope.json")]) == 1
    assert "error" in capsys.readouterr().err


def test_missing_type_warnings_helper():
    doc = DocumentExtraction(source_id="s", parse_id="p")
    doc.warnings = [
        "c1: relation 'r' missing domain type (for 'x'); cannot check it",
        "c1: off-ontology entity type 'missing' for 'missing'",
    ]
    assert len(doc.missing_type_warnings()) == 1
