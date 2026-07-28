"""Post-hoc span alignment: supporting quotes -> verified character spans.

The governing rule (ARCHITECTURE.md "Span-level provenance", and the matching
DECISIONS.md entry): **never trust a claimed offset**. The extraction step
returns a short supporting quote; this module searches for that quote inside the
chunk it came from and *derives* the offset. A fact that cannot be matched
confidently keeps its document/section-level provenance and is marked
``UNRESOLVED`` — it is never dropped, and no span is ever guessed.

The ladder, cheapest first, searching only within the originating chunk's
``[start, end)`` window of the parsed text:

1. exact substring of the raw window;
2. substring of the *normalised* window (whitespace collapsed, curly quotes and
   dashes flattened, ligatures decomposed, PDF line-wrap hyphenation joined,
   case folded) mapped back to real source offsets;
3. a bounded fuzzy match (``difflib.SequenceMatcher``) whose ratio must clear a
   floor read from ``config/extraction.yaml``;
4. otherwise no span.

Whichever stage hits, the derived offsets are sliced back out of the parsed text
and re-normalised before a :class:`~dikms.models.Span` is returned, so a stored
span is always reproducible from the stored text rather than merely asserted.

Offsets are parse-relative, so alignment refuses to run against a different
parse than the one extraction saw (``parse_id`` hashes the raw bytes).

Standard library only — ``difflib`` and ``unicodedata`` are enough; no new
dependency.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from .config import AlignmentConfig
from .extraction.pipeline import DocumentExtraction
from .models import AlignmentStatus, Chunk, ParsedDocument, Provenance, Span

MATCH_EXACT = "exact"
MATCH_NORMALISED = "normalised"
MATCH_FUZZY = "fuzzy"

# Characters flattened before matching. LLM-returned quotes are routinely
# retyped with ASCII punctuation where the source has typographic punctuation,
# and PDF extraction sprinkles exotic spaces about.
_CHAR_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',  # double quotes
    "′": "'", "″": '"',                                 # primes
    "‐": "-", "‑": "-", "‒": "-", "–": "-",   # hyphens/dashes
    "—": "-", "―": "-", "−": "-",
}
_SOFT_HYPHEN = "­"
# True hyphens, as opposed to en/em dashes. Both flatten to "-" for matching,
# but only this class marks word-internal hyphenation, so only this class may
# rejoin across a line break (see `normalise`).
_JOIN_HYPHENS = frozenset("-‐‑")


@dataclass(frozen=True)
class Normalised:
    """Normalised text plus a map from each character back to its source range.

    Normalisation is not length-preserving (whitespace collapses, ligatures
    expand), so an offset map is the only way to turn a match in the normalised
    text back into a real offset in the parse. ``starts[i]``/``ends[i]`` bound
    the source characters that produced normalised character ``i``.
    """

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]


@lru_cache(maxsize=512)  # a chunk window is normalised once, not once per fact
def normalise(text: str) -> Normalised:
    """Normalise ``text`` for matching, retaining a map back to source offsets.

    A hyphen before a line break is **kept**, and only the break collapses (via
    the whitespace rule below). PDF text wraps at hyphens for two different
    reasons and they are indistinguishable without a dictionary: a compound
    breaking at its own hyphen ("dual-\\nuse") versus a word hyphenated purely to
    wrap ("capa-\\nbility"). Measured on the corpus, all 40 such sites across
    both PDFs are the former and none the latter, so dropping the hyphen
    corrupted 40 compounds ("dualuse") to rescue nothing. Keeping it makes those
    match character-identically under normalisation; a genuine wrapped word
    instead falls to the fuzzy stage, where a single extra hyphen costs little
    ratio on any quote of reasonable length — and if it falls short, the outcome
    is UNRESOLVED, never a wrong span.

    Note that NFKC is applied per source character so the offset map stays at
    one-source-character granularity. The trade-off is that combining sequences
    do not compose: "cafe" + U+0301 will not match a precomposed "café" here.
    That costs recall only (the fuzzy stage still sees them as near-identical);
    it cannot produce a wrong span.
    """
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    n = len(text)
    # Whether the character just emitted was a true hyphen (not an en/em dash),
    # which is what may rejoin across a line break.
    last_was_hyphen = False
    while i < n:
        ch = text[i]
        if ch == _SOFT_HYPHEN:
            i += 1
            continue
        if ch.isspace():
            j = i
            saw_break = False
            while j < n and text[j].isspace():
                saw_break = saw_break or text[j] in "\r\n"
                j += 1
            # A line break directly after a true hyphen is swallowed whole
            # rather than collapsed to a space, so "stand-\noff" rejoins as
            # "stand-off" and matches the quote the model actually returns.
            # Both tests are deliberately *local* — the hyphen's own character
            # class, and the character after the break. Looking back past the
            # hyphen would read outside a slice, making normalise(slice) differ
            # from normalise(whole), which `_verify` relies on being equal.
            joins_hyphen = (
                saw_break and last_was_hyphen and j < n and text[j].isalnum()
            )
            if out and not joins_hyphen:  # collapse runs to one space
                out.append(" ")
                starts.append(i)
                ends.append(j)
                last_was_hyphen = False
            i = j
            continue
        mapped = _CHAR_MAP.get(ch)
        if mapped is None:
            # NFKC folds the ligatures text-native PDFs emit (ﬁ -> fi) and
            # assorted compatibility forms; both can expand to several chars.
            mapped = unicodedata.normalize("NFKC", ch)
        for c in mapped.lower():
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
        last_was_hyphen = ch in _JOIN_HYPHENS
        i += 1
    return Normalised(text="".join(out), starts=tuple(starts), ends=tuple(ends))


@dataclass(frozen=True)
class QuoteMatch:
    """A verified match of a supporting quote within one chunk."""

    span: Span
    method: str
    ratio: float
    # How many times the quote was found in the chunk, counted on the normalised
    # text. >1 means the match was ambiguous; the first occurrence within the
    # chunk bounds is used and the ambiguity is reported rather than silently
    # resolved. A fuzzy match always reports 1, since asking "how many other
    # regions would also have cleared the floor" has no cheap answer — the one
    # remaining blind spot, covering 1% of aligned facts. See
    # notes/M1-alignment.md.
    occurrences: int


def _verify(
    parsed: ParsedDocument,
    start: int,
    end: int,
    quote_norm: str,
    method: str,
    occurrences: int,
    config: AlignmentConfig,
) -> QuoteMatch | None:
    """Re-derive the match from the candidate offsets, or reject them.

    This is what makes a stored span verifiable: the text the offsets actually
    select is normalised again and scored against the quote, so the recorded
    ratio can be reproduced from the parse alone. Anything under the configured
    floor is refused, whichever stage proposed it.
    """
    if end <= start:
        return None
    slice_norm = normalise(parsed.text[start:end]).text
    if slice_norm == quote_norm:
        ratio = 1.0
    else:
        ratio = SequenceMatcher(None, slice_norm, quote_norm, autojunk=False).ratio()
        # An exact/normalised candidate that does not re-verify identically is
        # reported honestly as a fuzzy match rather than overstated.
        method = MATCH_FUZZY
    if ratio < config.min_ratio:
        return None
    return QuoteMatch(
        span=Span(parse_id=parsed.parse_id, start=start, end=end),
        method=method,
        ratio=ratio,
        occurrences=occurrences,
    )


def _count_occurrences(haystack: str, needle: str) -> int:
    return haystack.count(needle) if needle else 0


def _map_back(norm: Normalised, a: int, b: int, offset: int) -> tuple[int, int] | None:
    """Turn a ``[a, b)`` match in normalised text into source offsets."""
    while a < b and norm.text[a] == " ":
        a += 1
    while b > a and norm.text[b - 1] == " ":
        b -= 1
    if a >= b:
        return None
    return offset + norm.starts[a], offset + norm.ends[b - 1]


# How many candidate start/end boundaries the fuzzy stage weighs up. Bounds the
# refinement below to a fixed, small number of comparisons.
_BOUNDARY_CANDIDATES = 8


def _fuzzy_region(window_norm: str, quote_norm: str) -> tuple[int, int] | None:
    """Locate the best-scoring region for ``quote_norm`` in ``window_norm``.

    Bounded rather than a scan of every offset: the longest matching block
    anchors a region of roughly the quote's length (plus a quarter either side).
    The region's edges are then chosen from the boundaries of the matching
    blocks, keeping whichever pair *best* reproduces the quote — trimming the
    edges greedily instead lets the span swallow trailing text just because one
    stray character in it happens to match ("… System" running on into
    "… System (FADS").
    """
    sm = SequenceMatcher(None, window_norm, quote_norm, autojunk=False)
    anchor = sm.find_longest_match(0, len(window_norm), 0, len(quote_norm))
    if anchor.size == 0:
        return None
    q = len(quote_norm)
    slack = max(4, q // 4)
    lo = max(0, anchor.a - anchor.b - slack)
    tail = q - anchor.b - anchor.size  # quote characters after the anchor
    hi = min(len(window_norm), anchor.a + anchor.size + tail + slack)
    candidate = window_norm[lo:hi]
    ops = SequenceMatcher(None, candidate, quote_norm, autojunk=False).get_opcodes()
    if not ops:
        return None

    equal = [op for op in ops if op[0] == "equal"]
    if not equal:
        return None
    starts = sorted({op[1] for op in equal})[:_BOUNDARY_CANDIDATES]
    ends = sorted({op[2] for op in equal}, reverse=True)[:_BOUNDARY_CANDIDATES]

    best_key: tuple[float, int] | None = None
    best: tuple[int, int] | None = None
    for start in starts:
        for end in ends:
            if end <= start:
                continue
            ratio = SequenceMatcher(
                None, candidate[start:end], quote_norm, autojunk=False
            ).ratio()
            # Best ratio wins; the shorter span breaks ties, so the span never
            # includes text it did not need.
            key = (ratio, start - end)
            if best_key is None or key > best_key:
                best_key, best = key, (start, end)
    if best is None:
        return None
    return lo + best[0], lo + best[1]


def align_quote(
    parsed: ParsedDocument,
    chunk: Chunk,
    quote: str,
    config: AlignmentConfig,
) -> QuoteMatch | None:
    """Resolve ``quote`` to a verified span inside ``chunk``, or return ``None``.

    The search window is exactly the chunk's ``[start, end)`` range of the parsed
    text: a quote that appears elsewhere in the document is not evidence for a
    fact attributed to this chunk.
    """
    if chunk.parse_id != parsed.parse_id:
        raise ValueError(
            f"chunk {chunk.chunk_id} belongs to parse {chunk.parse_id!r}, "
            f"not {parsed.parse_id!r}"
        )
    quote = (quote or "").strip()
    if not quote:
        return None

    window = parsed.text[chunk.start : chunk.end]
    window_norm = normalise(window)
    quote_norm = normalise(quote).text
    if not quote_norm:
        return None

    # Ambiguity is always counted on the normalised text, even when the exact
    # stage wins: a quote occurring once verbatim can still occur several times
    # once typography is flattened, and reporting the raw count would call that
    # unambiguous. This is the honest upper bound on "how many places in the
    # chunk could this quote have come from".
    occurrences = _count_occurrences(window_norm.text, quote_norm)

    # 1. Exact substring.
    if quote in window:
        start = chunk.start + window.index(quote)
        match = _verify(
            parsed, start, start + len(quote), quote_norm,
            MATCH_EXACT, occurrences, config,
        )
        if match:
            return match

    # 2. Normalised substring.
    if occurrences:
        a = window_norm.text.index(quote_norm)
        bounds = _map_back(window_norm, a, a + len(quote_norm), chunk.start)
        if bounds:
            match = _verify(
                parsed, bounds[0], bounds[1], quote_norm,
                MATCH_NORMALISED, occurrences, config,
            )
            if match:
                return match

    # 3. Bounded fuzzy match, floored by config.
    if len(quote_norm) >= config.min_fuzzy_chars:
        region = _fuzzy_region(window_norm.text, quote_norm)
        if region:
            bounds = _map_back(window_norm, region[0], region[1], chunk.start)
            if bounds:
                return _verify(
                    parsed, bounds[0], bounds[1], quote_norm,
                    MATCH_FUZZY, 1, config,
                )

    # 4. Give up — UNRESOLVED, never a guess.
    return None


@dataclass
class Ambiguity:
    """A quote that matched in more than one place within its chunk."""

    kind: str
    chunk_id: str
    quote: str
    occurrences: int


@dataclass
class Unresolved:
    """A fact whose quote could not be matched confidently within its chunk."""

    kind: str
    chunk_id: str
    quote: str


@dataclass
class AlignmentReport:
    """Outcome of aligning one document: counts, method mix, and what failed."""

    source_id: str
    parse_id: str
    aligned: Counter = field(default_factory=Counter)      # by fact kind
    unresolved: Counter = field(default_factory=Counter)   # by fact kind
    methods: Counter = field(default_factory=Counter)
    ambiguous: list[Ambiguity] = field(default_factory=list)
    unresolved_facts: list[Unresolved] = field(default_factory=list)

    @property
    def total_aligned(self) -> int:
        return sum(self.aligned.values())

    @property
    def total_unresolved(self) -> int:
        return sum(self.unresolved.values())

    @property
    def total(self) -> int:
        return self.total_aligned + self.total_unresolved

    @property
    def rate(self) -> float:
        """Fraction of facts with a verified exact span (0.0 when there are none)."""
        return self.total_aligned / self.total if self.total else 0.0


def align_document(
    doc: DocumentExtraction,
    parsed: ParsedDocument,
    chunks: list[Chunk],
    config: AlignmentConfig,
) -> AlignmentReport:
    """Align every fact in ``doc`` against ``parsed``, in place.

    Each fact ends with exactly one of ``ALIGNED`` (carrying a verified
    :class:`~dikms.models.Span`) or ``UNRESOLVED`` (carrying ``span is None``).
    Nothing is dropped, and any pre-existing span is recomputed from scratch —
    an offset that did not come out of this function is never preserved.
    """
    if doc.parse_id != parsed.parse_id:
        raise RuntimeError(
            f"parse_id mismatch for {doc.source_id}: extraction recorded "
            f"{doc.parse_id!r} but re-parsing the source produced "
            f"{parsed.parse_id!r}. Offsets are only meaningful against the parse "
            "that produced them — re-run extraction rather than aligning against "
            "different text."
        )

    by_id = {c.chunk_id: c for c in chunks}
    report = AlignmentReport(source_id=doc.source_id, parse_id=doc.parse_id)

    for kind, items in (
        ("entity", doc.all_entities()),
        ("relation", doc.all_relations()),
        ("proposal", doc.all_proposals()),
    ):
        for item in items:
            _align_fact(item.provenance, kind, by_id, parsed, config, report)
    return report


def _align_fact(
    prov: Provenance,
    kind: str,
    by_id: dict[str, Chunk],
    parsed: ParsedDocument,
    config: AlignmentConfig,
    report: AlignmentReport,
) -> None:
    chunk = by_id.get(prov.chunk_id)
    if chunk is None:
        raise RuntimeError(
            f"chunk {prov.chunk_id!r} is not in the re-chunked document. The "
            "parse matches, so the chunking parameters must differ from the ones "
            "extraction ran with (see `chunking` in config/extraction.yaml)."
        )

    match = align_quote(parsed, chunk, prov.supporting_quote, config)
    if match is None:
        # Document/section provenance stands; only the exact span is missing.
        prov.span = None
        prov.alignment_status = AlignmentStatus.UNRESOLVED
        report.unresolved[kind] += 1
        report.unresolved_facts.append(
            Unresolved(kind=kind, chunk_id=prov.chunk_id, quote=prov.supporting_quote)
        )
        return

    prov.span = match.span
    prov.alignment_status = AlignmentStatus.ALIGNED
    report.aligned[kind] += 1
    report.methods[match.method] += 1
    if match.occurrences > 1:
        report.ambiguous.append(
            Ambiguity(
                kind=kind,
                chunk_id=prov.chunk_id,
                quote=prov.supporting_quote,
                occurrences=match.occurrences,
            )
        )
