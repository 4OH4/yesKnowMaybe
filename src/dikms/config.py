"""Runtime configuration loading (config/extraction.yaml).

Keeps GCP/Vertex settings, the extraction model choice, and pipeline tuning
parameters out of code (config-over-hardcoding convention). Project and
location fall back to the standard Google Cloud environment variables so
credentials/config need not be duplicated here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .chunking import DEFAULT_MAX_CHARS, DEFAULT_TARGET_CHARS


@dataclass
class ExtractionConfig:
    project: str | None
    location: str
    model: str
    temperature: float = 0.0
    target_chars: int = DEFAULT_TARGET_CHARS
    max_chars: int = DEFAULT_MAX_CHARS

    @classmethod
    def load(cls, path: str | Path) -> "ExtractionConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        vertex = data.get("vertex", {})
        extraction = data.get("extraction", {})
        chunking = data.get("chunking", {})
        return cls(
            project=vertex.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=vertex.get("location")
            or os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west2"),
            model=extraction.get("model", "gemini-2.5-flash"),
            temperature=extraction.get("temperature", 0.0),
            target_chars=chunking.get("target_chars", DEFAULT_TARGET_CHARS),
            max_chars=chunking.get("max_chars", DEFAULT_MAX_CHARS),
        )
