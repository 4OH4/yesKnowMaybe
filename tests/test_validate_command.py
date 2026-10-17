"""`dikms validate`: offline re-check of committed extraction output."""

from __future__ import annotations

import json

import pytest

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
from dikms.validate import format_reports, validate_file

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
    assert rep.warnings("domain/range") == 3
    assert rep.items("domain/range") == 3
    assert rep.items("missing type") == 1
    assert rep.items("off-ontology") == 2
    assert dict(rep.by_type("domain/range")) == {"rel": 3}


def test_matches_whole_chunk_validate(tmp_path):
    """Per-item attribution must not lose or add warnings versus the pipeline."""
    rep = validate_file(_write(tmp_path, RELS), ONTOLOGY)
    whole = _validate(
        ChunkExtraction(chunk_id="c1", relations=RELS), "c1",
        ONTOLOGY.entity_names(), ONTOLOGY.relation_names(), constraint_map(ONTOLOGY),
    )
    assert sorted(f.text for f in rep.findings) == sorted(whole)


def test_off_ontology_entity(tmp_path):
    ent = ExtractedEntity("Alien", "x", _prov("c1"))
    rep = validate_file(_write(tmp_path, [], [ent]), ONTOLOGY)
    assert rep.entities_checked == 1
    assert dict(rep.by_type("off-ontology")) == {"entity Alien": 1}


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
    assert "domain/range: 3 relations (3 warnings)" in out
    assert "missing type: 1 relations (1 warnings)" in out
    assert "total: 7 relations" in out
    assert "warn: c1:" in out
    assert open(path, "rb").read() == before


def test_cli_missing_file_is_an_error(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "nope.json")]) == 1
    err = capsys.readouterr().err
    assert "error" in err and "nope.json" in err


def test_missing_type_warnings_helper():
    doc = DocumentExtraction(source_id="s", parse_id="p")
    doc.warnings = [
        "c1: relation 'r' missing domain type (for 'x'); cannot check it",
        "c1: off-ontology entity type 'missing' for 'missing'",
    ]
    assert len(doc.missing_type_warnings()) == 1


def test_relation_breaking_domain_and_range_counts_once(tmp_path):
    both = _rel("rel", "Term", "Term")  # wrong domain and wrong range
    rep = validate_file(_write(tmp_path, [both]), ONTOLOGY)
    assert rep.warnings("domain/range") == 2
    assert rep.items("domain/range") == 1
    assert dict(rep.by_type("domain/range")) == {"rel": 1}
    assert "domain/range: 1 relations (2 warnings)" in format_reports([rep])


def test_entity_and_relation_type_names_are_not_mixed(tmp_path):
    ents = [ExtractedEntity("bogus", "x", _prov("c1"))]
    path = _write(tmp_path, [_rel("bogus", "Term", "Term")], ents)
    rep = validate_file(path, ONTOLOGY)
    assert dict(rep.by_type("off-ontology")) == {"bogus": 1, "entity bogus": 1}


def _raw(tmp_path, obj) -> str:
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def test_missing_provenance_is_its_own_kind(tmp_path):
    good = {"relation_type": "rel", "source_type": "Organisation",
            "target_type": "Programme", "provenance": {"chunk_id": "c1"}}
    bad = [
        {"relation_type": "rel", "source_type": "Term", "target_type": "Term"},
        {"relation_type": "rel", "provenance": None},
        {"relation_type": "rel", "provenance": {"source_id": "s"}},
    ]
    path = _raw(tmp_path, {"entities": [], "relations": [good, *bad]})
    rep = validate_file(path, ONTOLOGY)
    assert rep.items("missing provenance") == 3
    assert rep.items("domain/range") == 0


@pytest.mark.parametrize(
    "obj",
    [
        {"source_id": "x", "foo": 1},
        [],
        {"entities": [], "relations": None},
        {"entities": [], "relations": ["notadict"]},
        {"entities": [], "relations": [{"provenance": "str"}]},
    ],
)
def test_malformed_input_is_an_error(tmp_path, capsys, obj):
    path = _raw(tmp_path, obj)
    assert main(["validate", path]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "raw.json" in err


def test_invalid_json_is_an_error(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{nope", encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "bad.json" in capsys.readouterr().err
