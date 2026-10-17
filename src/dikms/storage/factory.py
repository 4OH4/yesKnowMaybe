"""Backend selection for the storage adapter.

The backend is chosen by ``storage.backend`` in ``config/extraction.yaml``, not
in code. Implementations are resolved lazily by import path so this module —
like the interface and the schema — imports no database driver; only the
selected backend's own module ever imports one.

The registry lives here rather than in config because it records which
implementations *exist*, which is a property of the codebase, not a tuning
parameter. Both backends are separate tickets, so until they land a valid
backend name resolves to :class:`BackendUnavailableError` rather than silently
falling back to something that happens to be importable.
"""

from __future__ import annotations

import importlib

from ..config import ExtractionConfig
from .base import StorageAdapter
from .schema import GraphSchema

#: Backend name -> "module path:class name". Names must match the values
#: ``storage.backend`` may take in config/extraction.yaml.
BACKENDS: dict[str, str] = {
    "kuzu": "dikms.storage.kuzu_backend:KuzuAdapter",
    "spanner": "dikms.storage.spanner_backend:SpannerAdapter",
}


class UnknownBackendError(ValueError):
    """``storage.backend`` names a backend this codebase has no registry entry for."""


class BackendUnavailableError(RuntimeError):
    """A registered backend whose implementation module cannot be imported."""


def known_backends() -> tuple[str, ...]:
    return tuple(sorted(BACKENDS))


def resolve_backend(name: str) -> type[StorageAdapter]:
    """Import and return the adapter class registered under ``name``."""
    try:
        target = BACKENDS[name]
    except KeyError:
        raise UnknownBackendError(
            f"Unknown storage backend {name!r}. "
            f"Set storage.backend in config/extraction.yaml to one of: "
            f"{', '.join(known_backends())}."
        ) from None

    module_path, _, class_name = target.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise BackendUnavailableError(
            f"Storage backend {name!r} is registered but not available: "
            f"could not import {module_path} ({exc})."
        ) from exc

    try:
        adapter_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise BackendUnavailableError(
            f"Storage backend {name!r}: {module_path} defines no {class_name}."
        ) from exc

    if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, StorageAdapter)):
        raise BackendUnavailableError(
            f"Storage backend {name!r}: {target} is not a StorageAdapter subclass."
        )
    return adapter_cls


def create_adapter(config: ExtractionConfig, schema: GraphSchema) -> StorageAdapter:
    """Build the adapter named by ``storage.backend`` in the loaded config."""
    storage = config.require_storage()
    adapter_cls = resolve_backend(storage.backend)
    return adapter_cls(schema=schema, config=storage)
