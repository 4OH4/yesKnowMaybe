"""Relation domain/range validation in the extraction pipeline."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from dikms.extraction.pipeline import _validate
from dikms.models import ChunkExtraction, ExtractedRelation, Provenance
from dikms.ontology import EntityType, Ontology, RelationType

TYPES = ["Organisation", "Programme", "Hardware", "Term"]


def _ontology(domain: list[str], range_: list[str]) -> Ontology:
    return Ontology(
        version=1,
        entity_types=[EntityType(name=t, description="") for t in TYPES],
        relation_types=[
            RelationType(name="rel", description="", domain=domain, range=range_)
        ],
    )


def _rel(rtype: str, src: str, tgt: str) -> ExtractedRelation:
    return ExtractedRelation(
        relation_type=rtype,
        source_name="a",
        source_type=src,
        target_name="b",
        target_type=tgt,
        provenance=Provenance(
            source_id="s", chunk_id="c1", section_refs=[], supporting_quote="q",
            extraction_model="m",
        ),
    )


def _run(ont: Ontology, rel: ExtractedRelation) -> list[str]:
    result = ChunkExtraction(chunk_id="c1", relations=[rel])
    constraints = {r.name: (r.domain, r.range) for r in ont.relation_types}
    return _validate(
        result, "c1", ont.entity_names(), ont.relation_names(), constraints
    )


def test_in_range_and_out_of_range():
    ont = _ontology(["Organisation"], ["Programme"])
    assert _run(ont, _rel("rel", "Organisation", "Programme")) == []
    warnings = _run(ont, _rel("rel", "Term", "Programme"))
    assert len(warnings) == 1
    assert "c1" in warnings[0] and "'rel'" in warnings[0] and "'Term'" in warnings[0]
    assert "domain" in warnings[0]
    warnings = _run(ont, _rel("rel", "Organisation", "Hardware"))
    assert len(warnings) == 1 and "range" in warnings[0] and "'Hardware'" in warnings[0]


def test_violating_both_sides_gives_two_warnings():
    ont = _ontology(["Organisation"], ["Programme"])
    assert len(_run(ont, _rel("rel", "Term", "Term"))) == 2


@pytest.mark.parametrize("unconstrained", [[], ["*"], ["Organisation", "*"]])
def test_wildcard_and_empty_mean_unconstrained(unconstrained):
    ont = _ontology(unconstrained, unconstrained)
    for src, tgt in itertools.product(TYPES, TYPES):
        assert _run(ont, _rel("rel", src, tgt)) == []


def test_exhaustive_matches_set_membership():
    """Over every (domain, range, source, target) combination the warning count
    equals the number of sides outside a non-wildcard constraint."""
    subsets = [[], ["*"], *([t] for t in TYPES), TYPES[:2], TYPES[1:3]]
    for dom, rng in itertools.product(subsets, subsets):
        ont = _ontology(dom, rng)
        for src, tgt in itertools.product(TYPES, TYPES):
            expected = 0
            for t, allowed in ((src, dom), (tgt, rng)):
                if allowed and "*" not in allowed and t not in allowed:
                    expected += 1
            assert len(_run(ont, _rel("rel", src, tgt))) == expected


def test_unknown_relation_not_double_reported():
    ont = _ontology(["Organisation"], ["Programme"])
    warnings = _run(ont, _rel("nope", "Term", "Term"))
    assert len(warnings) == 1 and "off-ontology relation type" in warnings[0]


def test_committed_ontology_wildcards_never_warn():
    ont = Ontology.load(Path(__file__).parent.parent / "config" / "ontology.yaml")
    for rt in ont.relation_types:
        if rt.domain in ([], ["*"]) and rt.range in ([], ["*"]):
            for src, tgt in itertools.product(sorted(ont.entity_names()), repeat=2):
                assert _run(ont, _rel(rt.name, src, tgt)) == []
