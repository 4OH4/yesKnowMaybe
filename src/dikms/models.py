"""Core data models for the M0 pipeline.

Design notes rooted in the non-negotiable constraints (CLAUDE.md):

* **Provenance is mandatory.** Every extracted entity and relation carries a
  :class:`Provenance` record — it is a required constructor argument with no
  default, so a fact without provenance cannot be represented at all.
* **Offsets are parse-relative.** Character offsets are only meaningful
  against a specific, versioned parse of a specific source, so a
  :class:`ParsedDocument` carries a ``parse_id`` and every chunk and span
  refers back to it.
* **Spans are verified, never claimed.** The extraction step records a
  supporting *quote*; an exact character span is a separate, best-effort,
  verified enhancement (M1 alignment). Until aligned, a fact still has full
  document/section-level provenance — see :class:`AlignmentStatus`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string (extraction/parse timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def parse_id_for(source_id: str, parser: str, raw: bytes) -> str:
    """Deterministic id for one parse of one source.

    Combines the source id, the parser identity (name + version) and a hash
    of the raw bytes, so offsets can be tied to exactly the parse that
    produced them and a re-parse with a different parser is distinguishable.
    """
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"{source_id}@{parser}#{digest}"


class SourceFormat(str, Enum):
    PDF = "pdf"
    HTML = "html"


class UnitKind(str, Enum):
    PAGE = "page"
    SECTION = "section"
    PARAGRAPH = "paragraph"


class AlignmentStatus(str, Enum):
    """State of exact-span alignment for a fact's supporting quote."""

    PENDING = "pending"  # quote captured, exact-span alignment not yet attempted (M0)
    ALIGNED = "aligned"  # quote matched to a verified character span in the parse
    UNRESOLVED = "unresolved"  # alignment attempted, no confident match found


@dataclass(frozen=True)
class StructuralUnit:
    """A page/section/paragraph boundary within a parsed document.

    ``start`` and ``end`` are character offsets into the parsed document's
    ``text``; ``label`` is a human reference (page number, heading text).
    """

    kind: UnitKind
    label: str
    start: int
    end: int


@dataclass
class ParsedDocument:
    """Clean text plus structural metadata for one source (parser output)."""

    source_id: str
    fmt: SourceFormat
    parser: str  # parser name + version, e.g. "pypdf-5.1.0"
    parse_id: str
    text: str
    units: list[StructuralUnit] = field(default_factory=list)
    parsed_at: str = field(default_factory=now_iso)

    def units_covering(self, start: int, end: int) -> list[StructuralUnit]:
        """Structural units overlapping the ``[start, end)`` character range."""
        return [u for u in self.units if u.start < end and u.end > start]


@dataclass
class Chunk:
    """A structure-respecting slice of a parsed document handed to extraction."""

    chunk_id: str
    source_id: str
    parse_id: str
    ordinal: int
    text: str
    start: int  # char offset into the parsed document's text
    end: int
    # Human-readable structural references covered by this chunk (page/section
    # labels), retained so provenance can name where a fact came from.
    section_refs: list[str] = field(default_factory=list)


@dataclass
class Span:
    """A verified exact character span within a parse. Only ever created by a
    successful alignment — never populated from an offset claimed by the LLM."""

    parse_id: str
    start: int
    end: int


@dataclass
class Provenance:
    """Mandatory provenance for an extracted fact.

    The document/section-level fields (``source_id``, ``chunk_id``,
    ``section_refs``) are the non-negotiable floor and are always present.
    ``supporting_quote`` is the grounding text the LLM returned;
    ``span``/``alignment_status`` record the best-effort exact span on top.
    """

    source_id: str
    chunk_id: str
    section_refs: list[str]
    supporting_quote: str
    extraction_model: str
    extracted_at: str = field(default_factory=now_iso)
    alignment_status: AlignmentStatus = AlignmentStatus.PENDING
    span: Span | None = None


@dataclass
class ExtractedEntity:
    entity_type: str
    name: str
    provenance: Provenance  # required — no fact without provenance
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class ExtractedRelation:
    relation_type: str
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    provenance: Provenance  # required — no fact without provenance


@dataclass
class TypeProposal:
    """A proposed new entity/relation type. Surfaced for HITL approval; never
    committed to the ontology automatically (DECISIONS.md: ontology gating)."""

    kind: str  # "entity" or "relation"
    name: str
    description: str
    justification: str
    provenance: Provenance


@dataclass
class ChunkExtraction:
    """Everything extraction produced from a single chunk."""

    chunk_id: str
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    proposals: list[TypeProposal] = field(default_factory=list)
    raw_response: str = ""
