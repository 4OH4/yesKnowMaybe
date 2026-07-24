"""HTML main-content extraction, stripping navigation, boilerplate and ads.

Uses trafilatura for main-content isolation. Plain text (not markdown) is
stored as the parse, so the extraction LLM's supporting quotes align cleanly
against it later without markdown markup interfering. Document-level
structure is the reliable floor; sections are best-effort via the shared
heading heuristic.
"""

from __future__ import annotations

import trafilatura

from ..models import ParsedDocument, SourceFormat, StructuralUnit, UnitKind, parse_id_for
from .structure import detect_headings


class EmptyExtractionError(RuntimeError):
    """Raised when main-content extraction yields nothing usable."""


def parse_html(source_id: str, raw: bytes) -> ParsedDocument:
    html = raw.decode("utf-8", errors="replace")
    # favour_recall keeps more of the article body at the cost of the odd
    # boilerplate line — acceptable for an extraction PoC.
    text = trafilatura.extract(
        html,
        favor_recall=True,
        include_comments=False,
        include_tables=True,
    )
    if not text or not text.strip():
        raise EmptyExtractionError(
            f"{source_id}: trafilatura extracted no main content — the page "
            f"may be JavaScript-rendered or an unsupported layout."
        )
    text = text.strip()
    parser = f"trafilatura-{trafilatura.__version__}"

    units: list[StructuralUnit] = [
        StructuralUnit(kind=UnitKind.PAGE, label="1", start=0, end=len(text))
    ]
    headings = detect_headings(text)
    for i, (start, label) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        units.append(StructuralUnit(kind=UnitKind.SECTION, label=label, start=start, end=end))

    return ParsedDocument(
        source_id=source_id,
        fmt=SourceFormat.HTML,
        parser=parser,
        parse_id=parse_id_for(source_id, parser, raw),
        text=text,
        units=units,
    )
