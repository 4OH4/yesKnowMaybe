"""Document parsing: source bytes -> clean text with structural metadata.

A distinct pipeline stage before chunking and extraction (ARCHITECTURE.md):
parsing quality caps extraction quality, so it is kept explicit and testable
in isolation.
"""

from __future__ import annotations

from ..models import ParsedDocument, SourceFormat
from .html import parse_html
from .pdf import parse_pdf

__all__ = ["parse_source", "parse_pdf", "parse_html"]


def parse_source(source_id: str, fmt: SourceFormat, raw: bytes) -> ParsedDocument:
    """Dispatch to the parser for ``fmt``."""
    if fmt is SourceFormat.PDF:
        return parse_pdf(source_id, raw)
    if fmt is SourceFormat.HTML:
        return parse_html(source_id, raw)
    raise ValueError(f"Unsupported source format: {fmt}")
