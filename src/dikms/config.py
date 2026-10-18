"""Runtime configuration loading (config/extraction.yaml).

Keeps GCP/Vertex settings, the extraction model choice, and pipeline tuning
parameters out of code (config-over-hardcoding convention). Project and
location fall back to the standard Google Cloud environment variables so
credentials/config need not be duplicated here.

The ``embedding`` and ``storage`` blocks are loaded here too rather than by a
second reader, so the storage layer takes its vector width and backend choice
from the same file as everything else. Neither has a code-side default: a
silently-defaulted vector width would be baked into a frozen schema, and a
silently-defaulted backend would hide which store was written to. Instead an
absent block stays ``None`` and :meth:`ExtractionConfig.require_embedding` /
:meth:`~ExtractionConfig.require_storage` raise at the point of use — so
commands that touch neither (``dikms extract``) still run on a minimal config,
while a block that *is* present is validated eagerly at load.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .chunking import DEFAULT_MAX_CHARS, DEFAULT_TARGET_CHARS


class ConfigError(ValueError):
    """Raised when the configuration file is missing a required setting."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """Vector channel settings (DECISIONS.md: embeddings).

    ``dimension`` is the width of the store's vector property/column. It has no
    default on purpose — see the module docstring.
    """

    model: str
    dimension: int
    normalise: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbeddingConfig:
        if "dimension" not in data:
            raise ConfigError(
                "config: 'embedding.dimension' is required — the store's vector "
                "width is read from config, never hardcoded."
            )
        dimension = data["dimension"]
        if (
            not isinstance(dimension, int)
            or isinstance(dimension, bool)
            or dimension < 1
        ):
            raise ConfigError(
                "config: 'embedding.dimension' must be a positive integer, "
                f"got {dimension!r}"
            )
        if "model" not in data:
            raise ConfigError("config: 'embedding.model' is required.")
        return cls(
            model=data["model"],
            dimension=dimension,
            normalise=bool(data.get("normalise", True)),
        )


@dataclass(frozen=True)
class KuzuConfig:
    """Embedded Kùzu backend settings."""

    path: str = "data/graph.kuzu"


@dataclass(frozen=True)
class SpannerConfig:
    """Spanner Graph backend settings. ``project`` falls back to the standard
    Google Cloud environment variable, as ``vertex.project`` does."""

    project: str | None = None
    instance: str = ""
    database: str = ""


@dataclass(frozen=True)
class StorageConfig:
    """Knowledge-store settings (DECISIONS.md: knowledge store).

    ``backend`` names which adapter implementation the factory should build.
    The set of *valid* names is owned by the factory's registry (it tracks which
    implementations exist), not by this module.
    """

    backend: str
    kuzu: KuzuConfig = field(default_factory=KuzuConfig)
    spanner: SpannerConfig = field(default_factory=SpannerConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorageConfig:
        if not data.get("backend"):
            raise ConfigError(
                "config: 'storage.backend' is required — the storage adapter is "
                "selected from config, not chosen in code."
            )
        kuzu = data.get("kuzu") or {}
        spanner = data.get("spanner") or {}
        return cls(
            backend=str(data["backend"]),
            kuzu=KuzuConfig(path=kuzu.get("path", KuzuConfig.path)),
            spanner=SpannerConfig(
                project=(
                    spanner.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT")
                ),
                instance=spanner.get("instance", ""),
                database=spanner.get("database", ""),
            ),
        )


@dataclass
class ExtractionConfig:
    project: str | None
    location: str
    model: str
    temperature: float = 0.0
    target_chars: int = DEFAULT_TARGET_CHARS
    max_chars: int = DEFAULT_MAX_CHARS
    embedding: EmbeddingConfig | None = None
    storage: StorageConfig | None = None

    @classmethod
    def load(cls, path: str | Path) -> ExtractionConfig:
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
            # Absent blocks stay None rather than raising here: `dikms extract`
            # touches neither the vector channel nor the store, and should not
            # fail on config it never reads. A block that *is* present is
            # validated eagerly, and the consumers below refuse to proceed
            # without one — so nothing ever falls back to a default in code.
            # `in` rather than truthiness: an empty `embedding:` block is a
            # present-but-incomplete block and must be reported, not silently
            # read as absent.
            embedding=(
                EmbeddingConfig.from_dict(data["embedding"] or {})
                if "embedding" in data
                else None
            ),
            storage=(
                StorageConfig.from_dict(data["storage"] or {})
                if "storage" in data
                else None
            ),
        )

    def require_embedding(self) -> EmbeddingConfig:
        """The embedding block, or a clear error. Used by anything that needs
        the vector width — which is never hardcoded (DECISIONS.md: embeddings)."""
        if self.embedding is None:
            raise ConfigError(
                "config: an 'embedding' block with a 'dimension' is required — "
                "the store's vector width is read from config, never hardcoded."
            )
        return self.embedding

    def require_storage(self) -> StorageConfig:
        """The storage block, or a clear error."""
        if self.storage is None:
            raise ConfigError(
                "config: a 'storage' block with a 'backend' is required — the "
                "storage adapter is selected from config, not chosen in code."
            )
        return self.storage


@dataclass
class AlignmentConfig:
    """Tuning parameters for post-hoc span alignment.

    The values below are fallbacks only — ``config/extraction.yaml`` carries the
    real ones and is the surface these get tuned on, since the right floor is
    only knowable from real extraction output (see the alignment rates in
    ``notes/M1-alignment.md``).
    """

    min_ratio: float = 0.95
    min_fuzzy_chars: int = 12
    fuzzy_anchor_chars: int = 3

    @classmethod
    def load(cls, path: str | Path) -> AlignmentConfig:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        alignment = data.get("alignment", {})
        return cls(
            min_ratio=float(alignment.get("min_ratio", cls.min_ratio)),
            min_fuzzy_chars=int(alignment.get("min_fuzzy_chars", cls.min_fuzzy_chars)),
            fuzzy_anchor_chars=int(
                alignment.get("fuzzy_anchor_chars", cls.fuzzy_anchor_chars)
            ),
        )
