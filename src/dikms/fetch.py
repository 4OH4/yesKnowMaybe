"""Download manifest sources into the local corpus (data/corpus/, gitignored).

Uses stdlib urllib to avoid a dependency; a browser-like User-Agent is sent
because some government asset hosts reject the default Python agent. Downloaded
bytes are cached on disk so parsing/extraction is reproducible against a fixed
local copy rather than re-fetching a possibly-changed URL each run.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from .models import SourceFormat
from .sources import Source

_UA = "Mozilla/5.0 (compatible; dikms/0.0; +local research tool)"
_EXT = {SourceFormat.PDF: "pdf", SourceFormat.HTML: "html"}


def corpus_path(source: Source, corpus_dir: str | Path) -> Path:
    return Path(corpus_dir) / f"{source.id}.{_EXT[source.fmt]}"


def fetch_source(source: Source, corpus_dir: str | Path, force: bool = False) -> Path:
    dest = corpus_path(source, corpus_dir)
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(source.url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted manifest URLs)
        data = resp.read()
    dest.write_bytes(data)
    return dest


def load_bytes(source: Source, corpus_dir: str | Path) -> bytes:
    """Read a previously-fetched source, with a clear error if it is missing."""
    path = corpus_path(source, corpus_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"{source.id} not in corpus at {path}. Run `dikms fetch` first."
        )
    return path.read_bytes()
