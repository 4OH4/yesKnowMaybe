"""Source manifest loading (data/sources.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import SourceFormat


@dataclass
class Source:
    id: str
    title: str
    publisher: str
    url: str
    fmt: SourceFormat
    authoritativeness: str
    authoritativeness_basis: str = ""
    notes: str = ""


def load_sources(path: str | Path) -> list[Source]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [
        Source(
            id=s["id"],
            title=s["title"],
            publisher=s.get("publisher", ""),
            url=s["url"],
            fmt=SourceFormat(s["format"]),
            authoritativeness=s.get("authoritativeness", "low"),
            authoritativeness_basis=s.get("authoritativeness_basis", ""),
            notes=s.get("notes", ""),
        )
        for s in data.get("sources", [])
    ]


def get_source(sources: list[Source], source_id: str) -> Source:
    for s in sources:
        if s.id == source_id:
            return s
    known = ", ".join(s.id for s in sources)
    raise KeyError(f"Unknown source id '{source_id}'. Known: {known}")
