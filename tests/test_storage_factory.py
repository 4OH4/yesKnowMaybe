"""Backend factory tests — selection comes from config, errors are clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from dikms.config import ConfigError, ExtractionConfig
from dikms.storage import (
    BACKENDS,
    BackendUnavailableError,
    StorageAdapter,
    UnknownBackendError,
    create_adapter,
    known_backends,
    resolve_backend,
)


class _FakeAdapter(StorageAdapter):
    """Stand-in for a real backend, so the factory's happy path is testable
    before either backend implementation exists."""

    def initialise_schema(self) -> None: ...

    def upsert_node(self, label, node_id, properties): ...

    def upsert_edge(self, edge_type, source, target, properties): ...

    def get_node(self, ref): ...

    def get_edge(self, ref): ...

    def traverse(self, start, **kwargs):
        return []

    def similar_nodes(self, label, embedding, *, limit=10, min_score=None):
        return []

    def trace_back(self, subject):
        return []


class _OtherFakeAdapter(_FakeAdapter):
    """A second, distinguishable stand-in."""


class _NotAnAdapter:
    """Registered but not a StorageAdapter."""


CONFIG_TEMPLATE = """
vertex:
  project: "test"
  location: "europe-west2"
extraction:
  model: "gemini-2.5-flash"
embedding:
  model: "gemini-embedding-001"
  dimension: {dimension}
storage:
  backend: "{backend}"
  kuzu:
    path: "data/graph.kuzu"
"""


def _write_config(
    tmp_path: Path, backend: str, dimension: int = 8, name: str = "extraction.yaml"
) -> Path:
    path = tmp_path / name
    path.write_text(
        CONFIG_TEMPLATE.format(backend=backend, dimension=dimension), encoding="utf-8"
    )
    return path


def test_registry_covers_the_two_locked_backends() -> None:
    assert known_backends() == ("kuzu", "spanner")


def test_unknown_backend_raises_clearly(tmp_path: Path, schema) -> None:
    config = ExtractionConfig.load(_write_config(tmp_path, "postgres"))
    with pytest.raises(UnknownBackendError) as excinfo:
        create_adapter(config, schema)
    message = str(excinfo.value)
    assert "postgres" in message
    assert "kuzu" in message and "spanner" in message
    assert "storage.backend" in message


def test_backend_name_is_taken_from_config(
    tmp_path: Path, schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing only the config value changes which backend is built."""
    monkeypatch.setitem(BACKENDS, "kuzu", f"{__name__}:_FakeAdapter")
    monkeypatch.setitem(BACKENDS, "spanner", f"{__name__}:_OtherFakeAdapter")

    kuzu_cfg = ExtractionConfig.load(_write_config(tmp_path, "kuzu", name="a.yaml"))
    spanner_cfg = ExtractionConfig.load(
        _write_config(tmp_path, "spanner", name="b.yaml")
    )
    assert isinstance(create_adapter(kuzu_cfg, schema), _FakeAdapter)
    assert isinstance(create_adapter(spanner_cfg, schema), _OtherFakeAdapter)


@pytest.mark.parametrize("backend", ["kuzu", "spanner"])
def test_a_registered_backend_with_no_module_reports_unavailable(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registry entry pointing at a module that is not importable must say so
    rather than fall back to something else.

    Note for whoever lands the Kùzu/Spanner backends: this test points the
    registry at a deliberately missing module, so it keeps working once the
    real implementations exist.
    """
    monkeypatch.setitem(BACKENDS, backend, "dikms.storage.definitely_not_here:Nope")
    with pytest.raises(BackendUnavailableError) as excinfo:
        resolve_backend(backend)
    assert backend in str(excinfo.value)


def test_a_registered_backend_is_constructed_with_schema_and_config(
    tmp_path: Path, schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path: the factory builds the class the registry names, handing
    it the schema and the storage config block."""
    monkeypatch.setitem(BACKENDS, "kuzu", f"{__name__}:_FakeAdapter")
    config = ExtractionConfig.load(_write_config(tmp_path, "kuzu", name="ok.yaml"))
    adapter = create_adapter(config, schema)
    assert isinstance(adapter, _FakeAdapter)
    assert adapter.schema is schema
    assert adapter.config is config.require_storage()


def test_a_registry_entry_that_is_not_an_adapter_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(BACKENDS, "kuzu", f"{__name__}:_NotAnAdapter")
    with pytest.raises(BackendUnavailableError, match="not a StorageAdapter"):
        resolve_backend("kuzu")


def test_the_real_registry_entries_point_at_this_package() -> None:
    """Guards against a registry entry drifting to a third-party module."""
    for name, target in BACKENDS.items():
        assert target.startswith("dikms.storage."), (name, target)


def test_config_without_a_storage_block_cannot_build_an_adapter(
    tmp_path: Path, schema
) -> None:
    path = tmp_path / "extraction.yaml"
    path.write_text(
        "extraction:\n  model: x\nembedding:\n  model: e\n  dimension: 8\n",
        encoding="utf-8",
    )
    config = ExtractionConfig.load(path)
    with pytest.raises(ConfigError, match="storage"):
        create_adapter(config, schema)


def test_real_config_selects_a_registered_backend(config) -> None:
    """config/extraction.yaml must name a backend the factory knows about."""
    assert config.require_storage().backend in known_backends()


def test_kuzu_path_is_read_from_config(tmp_path: Path) -> None:
    path = tmp_path / "extraction.yaml"
    path.write_text(
        CONFIG_TEMPLATE.format(backend="kuzu", dimension=8).replace(
            'path: "data/graph.kuzu"', 'path: "somewhere/else.kuzu"'
        ),
        encoding="utf-8",
    )
    config = ExtractionConfig.load(path)
    assert config.require_storage().kuzu.path == "somewhere/else.kuzu"
