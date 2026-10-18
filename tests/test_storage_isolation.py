"""The adapter interface, schema and factory must not know about any backend.

`DECISIONS.md`: "build all ingestion, provenance-write, and query code against
a thin storage-adapter interface — not against Spanner APIs directly". If any
driver leaks into these modules, the Spanner-vs-Kùzu choice stops being
reversible, so it is checked mechanically rather than by convention.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import sys
from pathlib import Path

import pytest

import dikms.storage

#: Modules that make up the backend-agnostic surface.
SURFACE_MODULES = ("__init__", "base", "schema", "factory", "identity", "keys")

#: Top-level packages that would couple the surface to one backend.
FORBIDDEN_ROOTS = {
    "kuzu",
    "google",
    "spanner",
    "vertexai",
    "google_cloud_spanner",
}

STORAGE_DIR = Path(dikms.storage.__file__).parent
ONTOLOGY_PATH = Path(__file__).resolve().parents[1] / "config" / "ontology.yaml"


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names imported by a module, at any nesting level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", SURFACE_MODULES)
def test_surface_module_imports_no_backend_driver(module: str) -> None:
    path = STORAGE_DIR / f"{module}.py"
    assert path.exists(), path
    offending = _imported_roots(path) & FORBIDDEN_ROOTS
    assert not offending, f"{path.name} imports backend driver(s): {sorted(offending)}"


def test_the_surface_is_usable_with_no_driver_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing *and using* the storage surface must work with no driver
    installed — an unattended session has neither Spanner credentials nor,
    necessarily, a compiled Kùzu wheel."""
    real_import = builtins.__import__

    def guarded(name: str, *args: object, **kwargs: object):
        if name.split(".")[0] in FORBIDDEN_ROOTS:
            raise ImportError(f"{name} is not available in this test")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    for name in list(sys.modules):
        if name.split(".")[0] in FORBIDDEN_ROOTS:
            monkeypatch.delitem(sys.modules, name, raising=False)
    for name in list(sys.modules):
        if name.startswith("dikms.storage"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    monkeypatch.setattr(builtins, "__import__", guarded)

    storage = importlib.import_module("dikms.storage")
    ontology = importlib.import_module("dikms.ontology").Ontology.load(ONTOLOGY_PATH)

    # Real work, not just an import: build the schema and enforce provenance.
    built = storage.build_schema(ontology, 8)
    assert "Programme" in built.node_labels()
    with pytest.raises(storage.SchemaViolation, match="source_id"):
        storage.validate_properties(built.node("Programme"), {"id": "x", "name": "X"})
