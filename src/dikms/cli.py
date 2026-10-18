"""Command-line entry point for the M0 pipeline.

Subcommands:
  fetch    download manifest sources into the local corpus
  parse    parse one source to clean text + structure, print a summary
  chunk    parse + chunk one source, print a chunk summary
  extract  full pipeline for one source (requires GCP/Vertex), write output
  align    derive verified spans for an existing extraction output

Parse, chunk and align run without GCP; extract needs Vertex credentials (see
SETUP.md). Defaults assume the command is run from the repository root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .chunking import chunk_document
from .config import ConfigError
from .fetch import fetch_source, load_bytes
from .parsing import parse_source
from .sources import get_source, load_sources

DEFAULT_ONTOLOGY = "config/ontology.yaml"
DEFAULT_CONFIG = "config/extraction.yaml"
DEFAULT_SOURCES = "data/sources.yaml"
DEFAULT_CORPUS = "data/corpus"
DEFAULT_OUTPUT = "output"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dikms", description="Defence KMS M0 pipeline")
    parser.add_argument("--sources", default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="download sources into the local corpus")
    p_fetch.add_argument("--only", help="fetch a single source id")
    p_fetch.add_argument("--force", action="store_true", help="re-download if present")

    p_parse = sub.add_parser("parse", help="parse one source")
    p_parse.add_argument("source_id")
    p_parse.add_argument("--show", type=int, default=0, help="print first N chars of text")

    p_chunk = sub.add_parser("chunk", help="parse + chunk one source")
    p_chunk.add_argument("source_id")

    p_extract = sub.add_parser("extract", help="full extraction (requires Vertex)")
    p_extract.add_argument("source_id")
    p_extract.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    p_extract.add_argument("--config", default=DEFAULT_CONFIG)
    p_extract.add_argument("--output", default=DEFAULT_OUTPUT)
    p_extract.add_argument(
        "--no-retry",
        action="store_true",
        help="fire requests straight through with no 429/503 backoff (observe raw API traffic)",
    )

    p_extract.add_argument(
        "--verbose",
        action="store_true",
        help="print per-chunk progress to stderr",
    )

    p_align = sub.add_parser(
        "align", help="derive verified spans for an existing extraction output"
    )
    p_align.add_argument("source_id")
    p_align.add_argument("--config", default=DEFAULT_CONFIG)
    p_align.add_argument("--output", default=DEFAULT_OUTPUT)
    p_align.add_argument(
        "--dry-run",
        action="store_true",
        help="report the alignment rate without rewriting the output file",
    )
    p_align.add_argument(
        "--show-unresolved",
        type=int,
        default=20,
        help="how many unresolved quotes to list (0 for none)",
    )

    args = parser.parse_args(argv)
    sources = load_sources(args.sources)

    try:
        if args.command == "fetch":
            return _cmd_fetch(args, sources)
        if args.command == "parse":
            return _cmd_parse(args, sources)
        if args.command == "chunk":
            return _cmd_chunk(args, sources)
        if args.command == "extract":
            return _cmd_extract(args, sources)
        if args.command == "align":
            return _cmd_align(args, sources)
    # ConfigError specifically — a malformed config file is a user error and
    # should report as one, not a traceback. Other ValueErrors are bugs and
    # keep their traceback.
    except (KeyError, ConfigError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


def _cmd_fetch(args, sources) -> int:
    targets = [get_source(sources, args.only)] if args.only else sources
    failures = 0
    for s in targets:
        try:
            path = fetch_source(s, args.corpus, force=args.force)
        except (OSError, ValueError) as exc:
            # Don't let one dead URL abort the whole batch — report and continue.
            failures += 1
            print(f"{s.id}: FAILED — {exc}", file=sys.stderr)
            continue
        print(f"{s.id}: {path} ({path.stat().st_size:,} bytes)")
    return 1 if failures else 0


def _cmd_parse(args, sources) -> int:
    source = get_source(sources, args.source_id)
    parsed = parse_source(source.id, source.fmt, load_bytes(source, args.corpus))
    n_pages = sum(1 for u in parsed.units if u.kind.value == "page")
    n_sections = sum(1 for u in parsed.units if u.kind.value == "section")
    print(f"{source.id}: {parsed.parser}")
    print(f"  {len(parsed.text):,} chars · {n_pages} pages · {n_sections} sections")
    if args.show:
        print("  ---")
        print(parsed.text[: args.show])
    return 0


def _cmd_chunk(args, sources) -> int:
    source = get_source(sources, args.source_id)
    parsed = parse_source(source.id, source.fmt, load_bytes(source, args.corpus))
    chunks = chunk_document(parsed)
    print(f"{source.id}: {len(chunks)} chunks")
    for c in chunks:
        print(f"  {c.ordinal:04d} [{c.start:>7}:{c.end:<7}] {len(c.text):>5} chars · "
              f"{', '.join(c.section_refs) or 'no ref'}")
    return 0


def _stderr_progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _cmd_extract(args, sources) -> int:
    # Imports deferred so `parse`/`chunk` do not require google-genai.
    from .config import ExtractionConfig
    from .extraction import GeminiExtractor, extract_document
    from .extraction.client import _RETRY_BACKOFF
    from .ontology import Ontology
    from .output import write_json, write_review_markdown

    source = get_source(sources, args.source_id)
    ontology = Ontology.load(args.ontology)
    config = ExtractionConfig.load(args.config)
    parsed = parse_source(source.id, source.fmt, load_bytes(source, args.corpus))
    extractor = GeminiExtractor(
        project=config.project,
        location=config.location,
        model=config.model,
        temperature=config.temperature,
        retry_backoff=() if args.no_retry else _RETRY_BACKOFF,
    )
    doc = extract_document(
        parsed, ontology, extractor,
        target_chars=config.target_chars, max_chars=config.max_chars,
        progress=_stderr_progress if args.verbose else None,
    )
    json_path = write_json(doc, Path(args.output) / f"{source.id}.json")
    md_path = write_review_markdown(doc, Path(args.output) / f"{source.id}.review.md")
    print(f"{source.id}: {len(doc.all_entities())} entities · "
          f"{len(doc.all_relations())} relations · "
          f"{len(doc.all_proposals())} proposals · {len(doc.warnings)} warnings "
          f"({len(doc.domain_range_warnings())} domain/range) · "
          f"{len(doc.failed_chunks)} failed chunks")
    for w in doc.warnings:
        print(f"  warn: {w}")
    for cid, err in doc.failed_chunks:
        print(f"  FAILED {cid}: {err}")
    print(f"  wrote {json_path}")
    print(f"  wrote {md_path}")
    return 1 if doc.failed_chunks else 0


def _cmd_align(args, sources) -> int:
    # No Vertex call here: alignment re-parses the local corpus copy and matches
    # quotes offline, so it runs with no credentials.
    from .alignment import align_document
    from .config import AlignmentConfig, ExtractionConfig
    from .output import read_json, write_json

    source = get_source(sources, args.source_id)
    extraction_config = ExtractionConfig.load(args.config)
    alignment_config = AlignmentConfig.load(args.config)

    json_path = Path(args.output) / f"{source.id}.json"
    doc = read_json(json_path)
    parsed = parse_source(source.id, source.fmt, load_bytes(source, args.corpus))
    chunks = chunk_document(
        parsed,
        target_chars=extraction_config.target_chars,
        max_chars=extraction_config.max_chars,
    )
    report = align_document(doc, parsed, chunks, alignment_config)

    print(
        f"{source.id}: {report.total_aligned}/{report.total} aligned "
        f"({report.rate:.1%}) · {report.total_unresolved} unresolved "
        f"(floor {alignment_config.min_ratio})"
    )
    if report.total:
        by_kind = " · ".join(
            f"{kind} {report.aligned[kind]}/{total}"
            for kind, total in (
                (k, report.aligned[k] + report.unresolved[k])
                for k in ("entity", "relation", "proposal")
            )
            if total
        )
        print(f"  by kind: {by_kind}")
        methods = " · ".join(f"{m} {n}" for m, n in report.methods.most_common())
        print(f"  by method: {methods or 'none'}")
    if report.ambiguous:
        print(
            f"  ambiguous (first match within the chunk used): {len(report.ambiguous)}"
        )
        for amb in report.ambiguous[: args.show_unresolved]:
            print(f"    {amb.occurrences}x {amb.chunk_id}: {_snip(amb.quote)}")
    if report.unresolved_facts and args.show_unresolved:
        print("  unresolved:")
        for item in report.unresolved_facts[: args.show_unresolved]:
            print(f"    {item.kind}: {_snip(item.quote)}")
        remaining = len(report.unresolved_facts) - args.show_unresolved
        if remaining > 0:
            print(f"    … and {remaining} more")

    if args.dry_run:
        print("  dry run — output file not rewritten")
    else:
        print(f"  wrote {write_json(doc, json_path)}")
    return 0


def _snip(text: str, limit: int = 90) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


if __name__ == "__main__":
    raise SystemExit(main())
