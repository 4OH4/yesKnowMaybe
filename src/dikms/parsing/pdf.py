"""Text-native PDF parsing with page and best-effort section structure.

Scanned/image PDFs are out of scope for v1 (ARCHITECTURE.md). A parse that
yields almost no text is flagged, since that is the signature of an
image-only PDF that would silently cap extraction quality.
"""

from __future__ import annotations

import pypdf

from ..models import ParsedDocument, SourceFormat, StructuralUnit, UnitKind, parse_id_for
from .structure import detect_headings

# Below this many characters of text per page on average, assume the PDF is
# scanned/image-only rather than text-native, and warn.
_MIN_CHARS_PER_PAGE = 40


class LikelyScannedPdfError(RuntimeError):
    """Raised when a PDF yields too little text to be text-native."""


def parse_pdf(source_id: str, raw: bytes) -> ParsedDocument:
    reader = pypdf.PdfReader(_as_stream(raw))
    parser = f"pypdf-{pypdf.__version__}"

    parts: list[str] = []
    units: list[StructuralUnit] = []
    cursor = 0
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        block = page_text + "\n\n"
        start = cursor
        end = start + len(page_text)  # unit covers the text, not the trailing gap
        units.append(
            StructuralUnit(kind=UnitKind.PAGE, label=str(page_number), start=start, end=end)
        )
        parts.append(block)
        cursor += len(block)

    text = "".join(parts)
    _check_text_native(text, len(reader.pages), source_id)
    units.extend(_section_units(text))

    return ParsedDocument(
        source_id=source_id,
        fmt=SourceFormat.PDF,
        parser=parser,
        parse_id=parse_id_for(source_id, parser, raw),
        text=text,
        units=units,
    )


def _section_units(text: str) -> list[StructuralUnit]:
    """Derive SECTION units spanning each heading to the next."""
    headings = detect_headings(text)
    units: list[StructuralUnit] = []
    for i, (start, label) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        units.append(StructuralUnit(kind=UnitKind.SECTION, label=label, start=start, end=end))
    return units


def _check_text_native(text: str, n_pages: int, source_id: str) -> None:
    if n_pages and len(text) / n_pages < _MIN_CHARS_PER_PAGE:
        raise LikelyScannedPdfError(
            f"{source_id}: ~{len(text) // max(n_pages, 1)} chars/page across "
            f"{n_pages} pages — likely a scanned/image PDF, which is out of "
            f"scope for v1. Confirm the source is text-native."
        )


def _as_stream(raw: bytes):
    import io

    return io.BytesIO(raw)
