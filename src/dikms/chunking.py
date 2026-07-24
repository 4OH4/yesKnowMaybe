"""Structure-respecting chunking.

Documents are split before extraction because they exceed a single effective
pass and because output quality degrades well before hard context limits
(ARCHITECTURE.md). Boundaries follow document structure — paragraphs, and
section starts where known — rather than fixed character counts, to avoid
splitting entities awkwardly. Every chunk keeps its offset range into the
parsed text and the page/section labels it covers, since that is what
provenance and (later) span alignment resolve against.
"""

from __future__ import annotations

import re

from .models import Chunk, ParsedDocument, UnitKind

# Character-based sizing (a deliberately simple proxy for tokens at M0 — no
# tokeniser dependency). Tunable via config later if it proves too coarse.
DEFAULT_TARGET_CHARS = 6000
DEFAULT_MAX_CHARS = 9000

_PARA_SPLIT = re.compile(r"\n[ \t]*\n")


def chunk_document(
    parsed: ParsedDocument,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    """Split ``parsed`` into structure-respecting chunks."""
    section_starts = {
        u.start for u in parsed.units if u.kind is UnitKind.SECTION
    }
    paragraphs = _paragraphs(parsed.text)

    chunks: list[Chunk] = []
    cur_start: int | None = None
    cur_end = 0
    ordinal = 0

    def flush() -> None:
        nonlocal cur_start, ordinal
        if cur_start is None:
            return
        chunks.append(_make_chunk(parsed, ordinal, cur_start, cur_end))
        ordinal += 1
        cur_start = None

    for start, end, para in paragraphs:
        length = end - start
        # A single oversized paragraph is hard-split as a fallback.
        if length > max_chars:
            flush()
            for h_start, h_end in _hard_split(start, end, max_chars):
                chunks.append(_make_chunk(parsed, ordinal, h_start, h_end))
                ordinal += 1
            continue

        if cur_start is None:
            cur_start, cur_end = start, end
            continue

        prospective = end - cur_start
        crosses_section = start in section_starts and (cur_end - cur_start) >= target_chars // 2
        if prospective > target_chars or crosses_section:
            flush()
            cur_start, cur_end = start, end
        else:
            cur_end = end

    flush()
    return chunks


def _make_chunk(parsed: ParsedDocument, ordinal: int, start: int, end: int) -> Chunk:
    refs = _section_refs(parsed, start, end)
    return Chunk(
        chunk_id=f"{parsed.parse_id}::{ordinal:04d}",
        source_id=parsed.source_id,
        parse_id=parsed.parse_id,
        ordinal=ordinal,
        text=parsed.text[start:end],
        start=start,
        end=end,
        section_refs=refs,
    )


def _section_refs(parsed: ParsedDocument, start: int, end: int) -> list[str]:
    """Human-readable page/section labels this chunk overlaps."""
    pages = [
        u.label for u in parsed.units_covering(start, end) if u.kind is UnitKind.PAGE
    ]
    sections = [
        u.label for u in parsed.units_covering(start, end) if u.kind is UnitKind.SECTION
    ]
    refs = []
    if pages:
        refs.append("pages " + "-".join([pages[0], pages[-1]]) if len(pages) > 1 else f"page {pages[0]}")
    refs.extend(sections)
    return refs


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """Yield ``(start, end, text)`` for non-empty paragraphs, with offsets."""
    result: list[tuple[int, int, str]] = []
    pos = 0
    for part in _PARA_SPLIT.split(text):
        idx = text.find(part, pos)
        if idx == -1:
            idx = pos
        stripped = part.strip()
        if stripped:
            lead = len(part) - len(part.lstrip())
            start = idx + lead
            end = start + len(stripped)
            result.append((start, end, stripped))
        pos = idx + len(part)
    return result


def _hard_split(start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Fixed-size fallback for a single paragraph larger than ``max_chars``."""
    spans = []
    s = start
    while s < end:
        spans.append((s, min(s + max_chars, end)))
        s += max_chars
    return spans
