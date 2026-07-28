"""Tests for post-hoc span alignment.

The load-bearing assertions here are the ones that check a *derived* span really
selects the quoted text (rather than merely being present), and that a quote
which cannot be matched ends as UNRESOLVED with no span at all.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import pytest

from dikms.alignment import (
    MATCH_EXACT,
    MATCH_FUZZY,
    MATCH_NORMALISED,
    align_document,
    align_quote,
    normalise,
)
from dikms.config import AlignmentConfig
from dikms.extraction.pipeline import DocumentExtraction
from dikms.models import (
    AlignmentStatus,
    Chunk,
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelation,
    ParsedDocument,
    Provenance,
    SourceFormat,
    Span,
    TypeProposal,
    parse_id_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PARSE_ID = parse_id_for("test-source", "test-parser-1.0", b"raw bytes")
OTHER_PARSE_ID = parse_id_for("test-source", "test-parser-1.0", b"different bytes")

# Two chunks. The second deliberately repeats a phrase from the first so the
# "found in the document but not in this chunk" case can be tested.
CHUNK_ONE_TEXT = (
    "The UK Royal Navy is developing the Future Air Dominance System "
    "(FADS) programme.\n"
    "It will deliver the “Type 83 destroyer” and a maritime\n"
    "integrated air and missile defence capa-\nbility for the fleet. "
    "The oﬃce of the CIO oversees the work. "
    "It carries stand-\noff weapons and a next-\ngeneration radar."
)
CHUNK_TWO_TEXT = (
    "Separately, Defence Digital funds the Digital Targeting Web. "
    "The UK Royal Navy is developing the Future Air Dominance System "
    "in a different context entirely."
)
PARSED_TEXT = CHUNK_ONE_TEXT + "\n\n" + CHUNK_TWO_TEXT

# The shipped floor, so the tests exercise what the CLI actually runs with.
CONFIG = AlignmentConfig(min_ratio=0.95, min_fuzzy_chars=12)


def make_parsed(text: str = PARSED_TEXT, parse_id: str = PARSE_ID) -> ParsedDocument:
    return ParsedDocument(
        source_id="test-source",
        fmt=SourceFormat.HTML,
        parser="test-parser-1.0",
        parse_id=parse_id,
        text=text,
    )


def make_chunks(parsed: ParsedDocument) -> list[Chunk]:
    split = len(CHUNK_ONE_TEXT)
    return [
        Chunk(
            chunk_id=f"{parsed.parse_id}::0000",
            source_id=parsed.source_id,
            parse_id=parsed.parse_id,
            ordinal=0,
            text=parsed.text[0:split],
            start=0,
            end=split,
            section_refs=["page 1"],
        ),
        Chunk(
            chunk_id=f"{parsed.parse_id}::0001",
            source_id=parsed.source_id,
            parse_id=parsed.parse_id,
            ordinal=1,
            text=parsed.text[split + 2 :],
            start=split + 2,
            end=len(parsed.text),
            section_refs=["page 2"],
        ),
    ]


def provenance(quote: str, chunk_id: str) -> Provenance:
    return Provenance(
        source_id="test-source",
        chunk_id=chunk_id,
        section_refs=["page 1"],
        supporting_quote=quote,
        extraction_model="test-model",
    )


def entity(quote: str, chunk_id: str, name: str = "Thing") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type="Programme", name=name, provenance=provenance(quote, chunk_id)
    )


def align_one(quote: str, chunk_index: int = 0, config: AlignmentConfig = CONFIG):
    parsed = make_parsed()
    chunk = make_chunks(parsed)[chunk_index]
    return parsed, align_quote(parsed, chunk, quote, config)


# --- the ladder -----------------------------------------------------------


def test_exact_quote_aligns_and_span_slices_back_to_it():
    quote = "Future Air Dominance System"
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_EXACT
    assert match.ratio == 1.0
    assert match.span.parse_id == parsed.parse_id
    # The derived offsets must select exactly the quoted characters.
    assert parsed.text[match.span.start : match.span.end] == quote


def test_curly_quotes_and_line_breaks_align_via_normalisation():
    # Source has typographic quotes and a hard line break; the model returned
    # ASCII punctuation on one line, as it routinely does.
    quote = '"Type 83 destroyer" and a maritime integrated air'
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_NORMALISED
    sliced = parsed.text[match.span.start : match.span.end]
    assert sliced != quote  # genuinely different characters in the source...
    assert normalise(sliced).text == normalise(quote).text  # ...same text


def test_ligature_aligns_via_normalisation():
    quote = "The office of the CIO oversees the work"
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_NORMALISED
    sliced = parsed.text[match.span.start : match.span.end]
    assert "ﬃ" in sliced  # the raw source really does use the ffi ligature
    assert normalise(sliced).text == normalise(quote).text


def test_compound_broken_at_its_own_hyphen_rejoins_exactly():
    """The case that actually occurs in the corpus: all 40 hyphen-at-linebreak
    sites across both PDFs are compounds breaking at their own hyphen, not words
    hyphenated to wrap. The hyphen is kept and only the break is swallowed, so
    these match character-identically rather than leaning on the fuzzy floor."""
    quote = "It carries stand-off weapons and a next-generation radar."
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_NORMALISED
    assert match.ratio == 1.0
    sliced = parsed.text[match.span.start : match.span.end]
    assert "stand-\noff" in sliced  # the raw source really is broken at the hyphen
    assert normalise(sliced).text == normalise(quote).text


def test_genuinely_wrapped_word_falls_through_to_fuzzy():
    """The converse case, indistinguishable without a dictionary: a word
    hyphenated purely to wrap keeps its hyphen too, so it can no longer match
    exactly. It degrades to a fuzzy match — never to a wrong span."""
    quote = "missile defence capability for the fleet. The office of the CIO"
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_FUZZY
    sliced = parsed.text[match.span.start : match.span.end]
    assert "capa-\nbility" in sliced
    # The one retained hyphen is the only difference.
    assert normalise(sliced).text == normalise(quote).text.replace(
        "capability", "capa-bility"
    )


def test_near_miss_quote_aligns_fuzzily_above_the_floor():
    # The characteristic real near-miss: the quote is a character out from the
    # source (here a stray plural), so it cannot match under normalisation but
    # is unmistakably the same text.
    quote = "The UK Royal Navy is developing the Future Air Dominance Systems"
    parsed, match = align_one(quote)
    assert match is not None
    assert match.method == MATCH_FUZZY
    assert CONFIG.min_ratio <= match.ratio < 1.0
    sliced = parsed.text[match.span.start : match.span.end]
    # A fuzzy span cannot normalise equal to the quote, so the guarantee is the
    # weaker one the floor actually promises — and it must hold for the text the
    # offsets really select, recomputed here rather than taken on trust.
    assert sliced == "The UK Royal Navy is developing the Future Air Dominance System"
    assert (
        SequenceMatcher(
            None, normalise(sliced).text, normalise(quote).text, autojunk=False
        ).ratio()
        >= CONFIG.min_ratio
    )


def test_absent_quote_does_not_align():
    """The required negative case: no match, and no guessed span."""
    _, match = align_one("Typhoon fighter jets were procured from Eurofighter GmbH")
    assert match is None


def test_quote_from_another_chunk_does_not_align_into_this_one():
    # This text exists in chunk 0, but the fact claims chunk 1: alignment must
    # not reach outside the chunk that produced the fact.
    quote = "It will deliver the “Type 83 destroyer”"
    _, match = align_one(quote, chunk_index=1)
    assert match is None


def test_empty_quote_does_not_align():
    _, match = align_one("   ")
    assert match is None


def test_near_miss_below_the_ratio_floor_does_not_align():
    # "FADS" vs "FODS" scores 0.75 — the ratio floor rejects it outright.
    _, match = align_one("FODS")
    assert match is None


def test_min_fuzzy_chars_rejects_a_short_quote_that_would_clear_the_ratio():
    # "Royal Navvy" scores 0.952 against "Royal Navy" — over the ratio floor,
    # but only because a one-character slip in a short string barely moves the
    # ratio. The length floor is what must reject it, so this isolates that knob
    # rather than re-testing the ratio.
    quote = "Royal Navvy"
    assert len(quote) < CONFIG.min_fuzzy_chars
    assert align_one(quote, config=CONFIG)[1] is None

    no_length_floor = AlignmentConfig(min_ratio=CONFIG.min_ratio, min_fuzzy_chars=0)
    relaxed = align_one(quote, config=no_length_floor)[1]
    assert relaxed is not None
    assert relaxed.ratio >= CONFIG.min_ratio


def test_ambiguous_quote_uses_first_match_and_records_the_count():
    parsed = make_parsed()
    chunk = Chunk(  # one chunk spanning the whole document, so the repeat is inside it
        chunk_id=f"{parsed.parse_id}::0000",
        source_id=parsed.source_id,
        parse_id=parsed.parse_id,
        ordinal=0,
        text=parsed.text,
        start=0,
        end=len(parsed.text),
        section_refs=["page 1"],
    )
    quote = "The UK Royal Navy is developing"
    match = align_quote(parsed, chunk, quote, CONFIG)
    assert match is not None
    assert match.occurrences == 2
    assert match.span.start == parsed.text.index(quote)  # first occurrence wins


def test_hyphen_join_does_not_fire_on_punctuation_or_list_markers():
    """The join rule keys off a preceding "-", so check it cannot swallow a
    break after a dash used as punctuation or a hyphen bullet — that would glue
    unrelated words together and shift every following offset."""
    cases = {
        # em dash normalises to "-", but is followed by a space, not a wrap
        "a clause —\nthen more": "a clause - then more",
        # hyphen bullet at the start of a line: the break before it is not
        # preceded by a hyphen, so it collapses normally
        "items:\n- first\n- second": "items: - first - second",
        # a real compound wrap still joins
        "stand-\noff": "stand-off",
    }
    for source, expected in cases.items():
        assert normalise(source).text == expected, source


# Adversarial characters: ligatures, soft hyphen, combining marks, curly
# punctuation, exotic spaces, CRLF — everything the offset map has to survive.
_FUZZ_ALPHABET = "ab -‐–—\n\r\t \xa0 ﬁﬃ\xad'’\"“́é.:"


def test_offset_map_invariants_hold_on_adversarial_input():
    """The offset map is the most dangerous code here: normalisation is not
    length-preserving, so a wrong map yields a plausible-looking but WRONG span.
    These invariants are what `_map_back` relies on."""
    import random

    rng = random.Random(20260724)
    for _ in range(3000):
        text = "".join(
            rng.choice(_FUZZ_ALPHABET) for _ in range(rng.randint(0, 60))
        )
        norm = normalise(text)
        assert len(norm.starts) == len(norm.text)
        assert len(norm.ends) == len(norm.text)
        for k in range(len(norm.text)):
            # In range, non-empty, and pointing at real source characters.
            assert 0 <= norm.starts[k] < norm.ends[k] <= len(text)
            if k:
                # Monotonic: normalised order follows source order.
                assert norm.starts[k] >= norm.starts[k - 1]
                assert norm.ends[k] >= norm.ends[k - 1]


def test_mapped_back_span_renormalises_to_contain_the_match():
    """The end-to-end guarantee: whatever region the map produces, re-slicing
    the source at those offsets and normalising again must still contain the
    text that was matched."""
    import random

    rng = random.Random(20260725)
    for _ in range(2000):
        text = "".join(
            rng.choice(_FUZZ_ALPHABET) for _ in range(rng.randint(4, 80))
        )
        norm = normalise(text)
        if len(norm.text) < 3:
            continue
        a = rng.randrange(0, len(norm.text) - 1)
        b = rng.randrange(a + 1, len(norm.text) + 1)
        matched = norm.text[a:b].strip()
        if not matched:
            continue
        start, end = norm.starts[a], norm.ends[b - 1]
        assert matched in normalise(text[start:end]).text


# --- config, not code -----------------------------------------------------


def test_match_floor_comes_from_config(tmp_path):
    # One word substituted in a long quote: ~0.89, so the floor alone decides
    # whether this aligns.
    quote = "The UK Royal Navy is developing the Future Air Supremacy System"
    lenient = AlignmentConfig(min_ratio=0.80, min_fuzzy_chars=12)
    strict = AlignmentConfig(min_ratio=0.99, min_fuzzy_chars=12)
    assert align_one(quote, config=lenient)[1] is not None
    assert align_one(quote, config=strict)[1] is None

    config_file = tmp_path / "extraction.yaml"
    config_file.write_text(
        "alignment:\n  min_ratio: 0.99\n  min_fuzzy_chars: 40\n", encoding="utf-8"
    )
    loaded = AlignmentConfig.load(config_file)
    assert loaded.min_ratio == 0.99
    assert loaded.min_fuzzy_chars == 40
    assert align_one(quote, config=loaded)[1] is None


def test_repository_config_supplies_the_shipped_floor():
    """The shipped config file, not a code default, is what the CLI reads.

    Pinned to the value the corpus evidence settled on (notes/M1-alignment.md):
    changing the floor should be a deliberate act that updates the recorded
    rates, not a silent edit.
    """
    loaded = AlignmentConfig.load(REPO_ROOT / "config" / "extraction.yaml")
    assert loaded.min_ratio == 0.95
    assert loaded.min_fuzzy_chars == 12
    assert (loaded.min_ratio, loaded.min_fuzzy_chars) == (
        CONFIG.min_ratio,
        CONFIG.min_fuzzy_chars,
    )


# --- document level -------------------------------------------------------


def make_doc(parse_id: str = PARSE_ID) -> DocumentExtraction:
    c0 = f"{parse_id}::0000"
    c1 = f"{parse_id}::0001"
    chunk_one = ChunkExtraction(
        chunk_id=c0,
        entities=[
            entity("Future Air Dominance System", c0, "FADS"),  # exact
            entity('"Type 83 destroyer"', c0, "Type 83"),  # normalised
            entity(  # fuzzy — a character out from the source
                "It will deliver the “Type 83 destroyers”", c0, "Type 83 (again)"
            ),
            entity("Typhoon fighter jets from Eurofighter", c0, "Typhoon"),  # fails
        ],
        relations=[
            ExtractedRelation(
                relation_type="develops",
                source_name="UK Royal Navy",
                source_type="Organisation",
                target_name="FADS",
                target_type="Programme",
                provenance=provenance(
                    "The UK Royal Navy is developing the Future Air Dominance System",
                    c0,
                ),
            )
        ],
        proposals=[
            TypeProposal(
                kind="entity",
                name="Fleet",
                description="A naval fleet",
                justification="appears in the text",
                provenance=provenance("for the fleet", c0),
            )
        ],
    )
    chunk_two = ChunkExtraction(
        chunk_id=c1,
        entities=[entity("Digital Targeting Web", c1, "DTW")],
    )
    return DocumentExtraction(
        source_id="test-source", parse_id=parse_id, chunks=[chunk_one, chunk_two]
    )


def all_provenance(doc: DocumentExtraction) -> list[Provenance]:
    return [
        item.provenance
        for item in (*doc.all_entities(), *doc.all_relations(), *doc.all_proposals())
    ]


def test_every_fact_ends_aligned_or_unresolved_and_none_are_dropped():
    parsed = make_parsed()
    doc = make_doc()
    before = len(all_provenance(doc))

    report = align_document(doc, parsed, make_chunks(parsed), CONFIG)

    provenances = all_provenance(doc)
    assert len(provenances) == before  # nothing dropped
    for prov in provenances:
        assert prov.alignment_status in (
            AlignmentStatus.ALIGNED,
            AlignmentStatus.UNRESOLVED,
        )
        if prov.alignment_status is AlignmentStatus.ALIGNED:
            assert prov.span is not None
            assert prov.span.parse_id == parsed.parse_id
        else:
            assert prov.span is None
    assert report.total == before
    assert report.total_unresolved >= 1  # the fabricated Typhoon quote
    assert 0.0 < report.rate < 1.0


def test_aligned_spans_slice_back_to_the_quote_under_the_same_normalisation():
    parsed = make_parsed()
    doc = make_doc()
    report = align_document(doc, parsed, make_chunks(parsed), CONFIG)

    aligned = [
        p for p in all_provenance(doc) if p.alignment_status is AlignmentStatus.ALIGNED
    ]
    assert aligned  # otherwise the assertions below are vacuous
    # Both match classes must be represented, or this only proves the easy case.
    assert report.methods[MATCH_FUZZY] >= 1
    assert report.methods[MATCH_EXACT] + report.methods[MATCH_NORMALISED] >= 1

    exactly_equal = 0
    for prov in aligned:
        sliced = parsed.text[prov.span.start : prov.span.end]
        slice_norm = normalise(sliced).text
        quote_norm = normalise(prov.supporting_quote).text
        # The floor's promise, checked against the text the offsets really
        # select — this is the invariant that holds for fuzzy matches too.
        assert (
            SequenceMatcher(None, slice_norm, quote_norm, autojunk=False).ratio()
            >= CONFIG.min_ratio
        )
        exactly_equal += slice_norm == quote_norm
    assert exactly_equal >= 1  # exact/normalised matches are character-identical


def test_document_provenance_survives_alignment_untouched():
    parsed = make_parsed()
    doc = make_doc()
    before = [
        (p.source_id, p.chunk_id, tuple(p.section_refs), p.supporting_quote,
         p.extracted_at)
        for p in all_provenance(doc)
    ]
    align_document(doc, parsed, make_chunks(parsed), CONFIG)
    after = [
        (p.source_id, p.chunk_id, tuple(p.section_refs), p.supporting_quote,
         p.extracted_at)
        for p in all_provenance(doc)
    ]
    assert before == after


def test_a_claimed_span_is_never_preserved():
    parsed = make_parsed()
    doc = make_doc()
    # Simulate an offset asserted by a model rather than derived: wrong parse,
    # fabricated offsets, already flagged ALIGNED.
    for prov in all_provenance(doc):
        prov.span = Span(parse_id=OTHER_PARSE_ID, start=999, end=1099)
        prov.alignment_status = AlignmentStatus.ALIGNED

    align_document(doc, parsed, make_chunks(parsed), CONFIG)

    for prov in all_provenance(doc):
        if prov.span is None:
            assert prov.alignment_status is AlignmentStatus.UNRESOLVED
            continue
        assert prov.span.parse_id == parsed.parse_id
        assert (prov.span.start, prov.span.end) != (999, 1099)
        assert normalise(parsed.text[prov.span.start : prov.span.end]).text


def test_parse_id_mismatch_fails_loudly_and_changes_nothing():
    parsed = make_parsed(parse_id=OTHER_PARSE_ID)
    doc = make_doc()  # recorded against PARSE_ID
    with pytest.raises(RuntimeError, match="parse_id mismatch"):
        align_document(doc, parsed, make_chunks(parsed), CONFIG)
    assert all(
        p.alignment_status is AlignmentStatus.PENDING and p.span is None
        for p in all_provenance(doc)
    )


def test_align_quote_rejects_a_chunk_from_another_parse():
    """The chunk-level guard, below `align_document`'s document-level one."""
    parsed = make_parsed()
    foreign = make_chunks(make_parsed(parse_id=OTHER_PARSE_ID))[0]
    with pytest.raises(ValueError, match="belongs to parse"):
        align_quote(parsed, foreign, "Future Air Dominance System", CONFIG)


def test_unknown_chunk_id_fails_loudly():
    parsed = make_parsed()
    doc = make_doc()
    chunks = make_chunks(parsed)[:1]  # chunk 0001 missing
    with pytest.raises(RuntimeError, match="not in the re-chunked document"):
        align_document(doc, parsed, chunks, CONFIG)


def test_report_counts_ambiguity_and_methods():
    parsed = make_parsed()
    doc = make_doc()
    report = align_document(doc, parsed, make_chunks(parsed), CONFIG)
    assert sum(report.methods.values()) == report.total_aligned
    assert report.aligned["entity"] + report.unresolved["entity"] == 5
    assert [u.quote for u in report.unresolved_facts] == [
        "Typhoon fighter jets from Eurofighter"
    ]
