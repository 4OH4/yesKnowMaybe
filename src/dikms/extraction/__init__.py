"""Ontology-constrained extraction (M0).

Extraction returns candidate entities/relations with verbatim supporting
quotes and full document/section-level provenance. Exact-span alignment and
storage are M1; entity resolution is M2.
"""

from __future__ import annotations

from .client import ExtractionResponseError, GeminiExtractor, parse_response
from .pipeline import DocumentExtraction, extract_document

__all__ = [
    "GeminiExtractor",
    "parse_response",
    "ExtractionResponseError",
    "DocumentExtraction",
    "extract_document",
]
