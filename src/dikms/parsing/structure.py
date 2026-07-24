"""Shared, conservative heading detection used by both parsers.

Section detection is best-effort: page-level (PDF) and document-level (HTML)
structure is the reliable floor that provenance depends on; headings are an
enhancement layered on top. The heuristic is deliberately conservative —
missing a heading is cheaper than shredding the text into spurious sections.
"""

from __future__ import annotations

import re

# A numbered heading: "3", "3.2", "3.2.1 Title", optionally trailing dot.
_NUMBERED = re.compile(r"^\s*\d+(?:\.\d+)*\.?(?:\s+\S.*)?$")
# A short ALL-CAPS line (common in government PDF section titles).
_ALLCAPS = re.compile(r"^\s*[A-Z0-9][A-Z0-9 &/,'’\-]{2,80}$")

# A line ending in a bare number is almost always a table-of-contents entry
# (e.g. "2.4 Maritime 35"), not a body heading. Excluding these stops the ToC
# from being detected as dozens of sections whose spans then bleed across
# unrelated body chunks.
_TOC_ENTRY = re.compile(r"\s\d{1,4}$")

_SENTENCE_END = (".", ":", ";", ",")


def detect_headings(text: str) -> list[tuple[int, str]]:
    """Return ``(char_offset, label)`` for lines that look like headings."""
    headings: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _is_heading(stripped):
            headings.append((offset, stripped))
        offset += len(line)
    return headings


def _is_heading(stripped: str) -> bool:
    if not (3 <= len(stripped) <= 90):
        return False
    if stripped.endswith(_SENTENCE_END):
        return False
    if _TOC_ENTRY.search(stripped):
        return False
    words = stripped.split()
    if _NUMBERED.match(stripped) and len(words) <= 12:
        return True
    # ALL-CAPS: require at least one letter and no lowercase, keep it short.
    if _ALLCAPS.match(stripped) and any(c.isalpha() for c in stripped):
        if len(words) <= 10:
            return True
    return False
