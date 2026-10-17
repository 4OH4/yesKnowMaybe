"""Invariant tests for the shared write validators in :mod:`dikms.storage.schema`.

These validators are the only thing enforcing provenance on Kùzu (no ``NOT
NULL``), so their failure mode is a plausible-looking but untraceable write
rather than a crash. The tests therefore assert invariants over every node and
edge definition the schema derives, and over generated inputs, rather than a
handful of examples. ``hypothesis`` is not a dependency, so inputs are
exhaustive small grids or a fixed-seed generator.
"""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from dikms.models import AlignmentStatus, Provenance, Span
from dikms.storage import (
    EdgeDef,
    GraphSchema,
    NodeDef,
    PropertyDef,
    PropertyType,
    SchemaViolation,
    normalise_properties,
    normalise_timestamp,
    parse_timestamp,
    provenance_properties,
    validate_properties,
)

VALID_TIMESTAMP = "2026-07-24T09:00:00+00:00"


def _value_for(prop: PropertyDef, dimension: int) -> Any:
    """A valid, non-empty value for any declared property."""
    if prop.values:
        return prop.values[0]
    return {
        PropertyType.STRING: f"{prop.name}-value",
        PropertyType.TIMESTAMP: VALID_TIMESTAMP,
        PropertyType.STRING_LIST: ["p. 1"],
        PropertyType.INT: 0,
        PropertyType.FLOAT: 0.5,
        PropertyType.BOOL: True,
        PropertyType.VECTOR: [0.0] * (prop.dimension or dimension),
    }[prop.type]


def valid_properties(
    definition: NodeDef | EdgeDef, dimension: int
) -> dict[str, Any]:
    """The minimal valid write for a definition: every required property."""
    return {
        p.name: _value_for(p, dimension) for p in definition.properties if p.required
    }


def _definitions(schema: GraphSchema) -> list[NodeDef | EdgeDef]:
    return [*schema.nodes, *schema.edges]


def _name(definition: NodeDef | EdgeDef) -> str:
    return getattr(definition, "label", None) or definition.type


#: Values that are present but carry nothing, per property type.
BLANKS: dict[PropertyType, tuple[Any, ...]] = {
    PropertyType.STRING: ("", " ", "\t\n"),
    PropertyType.TIMESTAMP: ("", " "),
    PropertyType.STRING_LIST: ([], (), [""], ["p. 1", " "]),
}


def test_the_generated_minimal_write_is_valid_for_every_definition(
    schema: GraphSchema,
) -> None:
    """Guards the builder the other invariants rely on."""
    for definition in _definitions(schema):
        validate_properties(
            definition, valid_properties(definition, schema.vector_dimension)
        )


def test_no_required_property_may_be_blank_anywhere(schema: GraphSchema) -> None:
    """For every definition, every required string/list property, and every
    blank form of it: the write is refused, naming the property. This is the
    "no provenance field is empty" rule, applied to all six mandatory fields
    on every fact-bearing node and edge at once."""
    checked = 0
    for definition in _definitions(schema):
        base = valid_properties(definition, schema.vector_dimension)
        for prop in definition.properties:
            if not prop.required or prop.values:
                continue
            for blank in BLANKS.get(prop.type, ()):
                with pytest.raises(SchemaViolation, match=prop.name):
                    validate_properties(definition, dict(base, **{prop.name: blank}))
                checked += 1
    assert checked > 0


def test_every_mandatory_provenance_field_is_covered_by_the_blank_rule(
    schema: GraphSchema,
) -> None:
    """The six fields #10 must never leave empty, checked by name so a type
    change to one of them cannot quietly take it out of the rule above."""
    fields = (
        "source_id",
        "chunk_id",
        "section_refs",
        "supporting_quote",
        "extraction_model",
        "extracted_at",
    )
    node = schema.node("Programme")
    base = valid_properties(node, schema.vector_dimension)
    for name in fields:
        prop = node.property(name)
        assert prop is not None and prop.required, name
        assert BLANKS.get(prop.type), name
        for blank in BLANKS[prop.type]:
            with pytest.raises(SchemaViolation, match=name):
                validate_properties(node, dict(base, **{name: blank}))


def test_an_optional_property_may_be_empty(schema: GraphSchema) -> None:
    """Blankness is only a defect for required fields; optional free text
    (e.g. a Source's notes) may legitimately be empty."""
    source = schema.node("Source")
    validate_properties(
        source, dict(valid_properties(source, schema.vector_dimension), notes="")
    )


# -- timestamps -------------------------------------------------------------


NOT_TIMESTAMPS = (
    "yesterday",
    "t",
    "2026-07-24",  # a date, no time and no offset
    "2026-07-24T09:00:00",  # naive
    "2026-07-24 09:00",  # naive
    "24/07/2026 09:00 +00:00",
    "2026-13-01T00:00:00+00:00",
)


@pytest.mark.parametrize("value", NOT_TIMESTAMPS)
def test_a_timestamp_must_be_iso_8601_with_an_offset(
    schema: GraphSchema, value: str
) -> None:
    for definition in _definitions(schema):
        for prop in definition.properties:
            if prop.type is not PropertyType.TIMESTAMP:
                continue
            base = valid_properties(definition, schema.vector_dimension)
            with pytest.raises(SchemaViolation, match=prop.name):
                validate_properties(definition, dict(base, **{prop.name: value}))


def _generated_instants(count: int) -> list[datetime]:
    rng = random.Random(20261017)
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    span = int(timedelta(days=365 * 40).total_seconds())
    instants = []
    for _ in range(count):
        micros = rng.choice((0, rng.randrange(10**6)))
        moment = epoch + timedelta(seconds=rng.randrange(span), microseconds=micros)
        # Any real-world offset, in 15-minute steps from -12:00 to +14:00.
        offset = timezone(timedelta(minutes=15 * rng.randrange(-48, 57)))
        instants.append(moment.astimezone(offset))
    return instants


def _spellings(moment: datetime) -> list[str]:
    """Several ISO 8601 spellings of one instant."""
    forms = [moment.isoformat(), moment.isoformat(timespec="seconds")]
    if moment.microsecond:
        forms.pop()  # seconds-only would drop information
    utc = moment.astimezone(UTC)
    forms.append(utc.isoformat().replace("+00:00", "Z"))
    forms.append(utc.isoformat())
    return forms


def test_normalised_timestamps_are_one_fixed_width_utc_form() -> None:
    for moment in _generated_instants(400):
        forms = {normalise_timestamp(s) for s in _spellings(moment)}
        assert len(forms) == 1, forms  # every spelling of an instant agrees
        (stored,) = forms
        assert stored.endswith("+00:00")
        assert len(stored) == len("2026-07-24T09:00:00.000000+00:00")
        assert parse_timestamp(stored) == moment  # the same instant
        assert normalise_timestamp(stored) == stored  # idempotent


def test_normalised_timestamp_order_is_chronological_order() -> None:
    """Both backends store timestamps as strings, so ``ORDER BY extracted_at``
    is only meaningful if string order and time order agree."""
    instants = _generated_instants(300)
    by_string = sorted(instants, key=lambda m: normalise_timestamp(m.isoformat()))
    by_time = sorted(instants)
    assert [m.astimezone(UTC) for m in by_string] == [
        m.astimezone(UTC) for m in by_time
    ]


def test_normalise_properties_rewrites_every_timestamp_and_nothing_else(
    schema: GraphSchema,
) -> None:
    for definition in _definitions(schema):
        base = valid_properties(definition, schema.vector_dimension)
        # The same instant as VALID_TIMESTAMP, spelt at +01:00.
        given = {
            k: "2026-07-24T10:00:00+01:00" if v == VALID_TIMESTAMP else v
            for k, v in base.items()
        }
        out = normalise_properties(definition, given)
        for prop in definition.properties:
            if prop.name not in given:
                assert prop.name not in out
            elif prop.type is PropertyType.TIMESTAMP:
                assert out[prop.name] == "2026-07-24T09:00:00.000000+00:00"
            else:
                assert out[prop.name] == given[prop.name], prop.name
        validate_properties(definition, out)


# -- exact spans are verified-only ------------------------------------------

SPAN_STATUSES = tuple(s.value for s in AlignmentStatus)
PARSE_IDS = (None, "", " ", "parse-1")
OFFSETS = (None, -1, 0, 3, 7)


def _span_should_be_accepted(
    status: str, parse_id: str | None, start: int | None, end: int | None
) -> bool:
    """The rule, written independently of the validator."""
    fields = (parse_id, start, end)
    if all(f is None for f in fields):
        return status != AlignmentStatus.ALIGNED.value
    if any(f is None for f in fields):
        return False  # a partial span
    return (
        status == AlignmentStatus.ALIGNED.value
        and bool(parse_id.strip())
        and 0 <= start <= end
    )


def test_span_fields_are_all_or_nothing_and_aligned_only(
    schema: GraphSchema,
) -> None:
    """Exhaustive over status x parse id x start x end (300 cases), on every
    definition that carries the span group: the validator accepts exactly the
    combinations the rule allows, so no unverified, partial or inverted
    offset can be stored as if it were a confirmed span."""
    bearing = [d for d in _definitions(schema) if d.property("alignment_status")]
    assert bearing, "no definition carries the span group"
    for definition in bearing:
        base = valid_properties(definition, schema.vector_dimension)
        for status, parse_id, start, end in itertools.product(
            SPAN_STATUSES, PARSE_IDS, OFFSETS, OFFSETS
        ):
            props = dict(
                base,
                alignment_status=status,
                span_parse_id=parse_id,
                span_start=start,
                span_end=end,
            )
            if _span_should_be_accepted(status, parse_id, start, end):
                validate_properties(definition, props)
            else:
                with pytest.raises(SchemaViolation, match="span"):
                    validate_properties(definition, props)


def test_an_aligned_span_from_the_model_is_accepted(schema: GraphSchema) -> None:
    node = schema.node("Programme")
    prov = Provenance(
        source_id="s",
        chunk_id="c",
        section_refs=["p. 1"],
        supporting_quote="q",
        extraction_model="m",
        extracted_at=VALID_TIMESTAMP,
        alignment_status=AlignmentStatus.ALIGNED,
        span=Span(parse_id="parse-1", start=10, end=10),  # zero width is allowed
    )
    validate_properties(node, {"id": "p:x", "name": "X", **provenance_properties(prov)})
