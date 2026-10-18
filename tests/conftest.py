"""Shared fixtures. No test in this package needs credentials or the network;
the only database is embedded Kùzu, in a temporary directory per test."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from dikms.config import ExtractionConfig
from dikms.ontology import Ontology
from dikms.storage import GraphSchema, StorageAdapter, build_schema, create_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = REPO_ROOT / "config" / "ontology.yaml"
EXTRACTION_CONFIG_PATH = REPO_ROOT / "config" / "extraction.yaml"


@pytest.fixture(scope="session")
def ontology() -> Ontology:
    return Ontology.load(ONTOLOGY_PATH)


@pytest.fixture(scope="session")
def config() -> ExtractionConfig:
    return ExtractionConfig.load(EXTRACTION_CONFIG_PATH)


@pytest.fixture(scope="session")
def schema(ontology: Ontology, config: ExtractionConfig) -> GraphSchema:
    return build_schema(ontology, config.require_embedding().dimension)


_KUZU_CONFIG = """
extraction:
  model: "test-model"
embedding:
  model: "test-embedding"
  dimension: {dimension}
storage:
  backend: "kuzu"
  kuzu:
    path: "{path}"
"""

OpenKuzu = Callable[..., StorageAdapter]


@pytest.fixture()
def open_kuzu(tmp_path: Path, schema: GraphSchema) -> Iterator[OpenKuzu]:
    """Open a Kùzu store in ``tmp_path`` the way callers do: from config,
    through :func:`dikms.storage.create_adapter`. Calling it again with the
    same ``name`` reopens the same database file. Every store opened is
    closed at teardown."""
    opened: list[StorageAdapter] = []

    def _open(
        graph_schema: GraphSchema | None = None, name: str = "graph.kuzu"
    ) -> StorageAdapter:
        graph_schema = graph_schema or schema
        config_path = tmp_path / f"{name}.yaml"
        config_path.write_text(
            _KUZU_CONFIG.format(
                dimension=graph_schema.vector_dimension,
                path=(tmp_path / name).as_posix(),
            ),
            encoding="utf-8",
        )
        adapter = create_adapter(ExtractionConfig.load(config_path), graph_schema)
        opened.append(adapter)
        return adapter

    yield _open
    for adapter in opened:
        adapter.close()
