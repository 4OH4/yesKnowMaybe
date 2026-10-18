"""Writing ``same_as`` edges without losing a human verdict.

A ``same_as`` pair has one stored edge (its key is the pair alone, in
canonical order; see :meth:`StorageAdapter.edge_ref`), and
:meth:`StorageAdapter.upsert_edge` overwrites every property of it. Left to
itself, an automated re-proposal of a pair a human has rejected would write
the default ``review_status`` of ``unreviewed`` over that rejection, and the
match would come back for review, or be read as unreviewed evidence.
``ARCHITECTURE.md`` (entity resolution) keeps the reviewed/rejected flag
separate from confidence precisely "so the system doesn't re-propose matches
that have already been explicitly rejected"; :func:`write_same_as` is the
write path that honours it.

Everything here goes through the adapter interface, so it behaves the same on
every backend.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .base import EdgeRef, NodeRef, StorageAdapter
from .schema import SAME_AS

#: The ``review_status`` an automated proposal carries, and the only one that
#: is not a human verdict.
UNREVIEWED = "unreviewed"


def write_same_as(
    adapter: StorageAdapter,
    a: NodeRef,
    b: NodeRef,
    *,
    confidence: float,
    method: str,
    review_status: str | None = None,
    created_at: str | None = None,
) -> EdgeRef:
    """Write the ``same_as`` claim for the pair (``a``, ``b``), keeping any
    human verdict already stored on it.

    The pair is one edge whichever way round it is named, so ``(a, b)`` and
    ``(b, a)`` read and write the same stored edge. The stored edge is read
    with :meth:`StorageAdapter.get_edge` and then written in full with
    :meth:`StorageAdapter.upsert_edge`:

    * ``confidence`` and ``method`` (the automated fields) are always written
      as given.
    * ``review_status``: an explicit value always wins, ``unreviewed``
      included, so a human (or a deliberate reset) can change a verdict. If
      it is ``None``, a stored human verdict (any status other than
      ``unreviewed``) is kept as stored; with no edge, or an ``unreviewed``
      one, the edge is written ``unreviewed``.
    * ``created_at`` is when the pair was first proposed: an explicit value
      wins; otherwise a stored edge keeps its own, and a new edge gets the
      current UTC time.

    Validation is :meth:`StorageAdapter.upsert_edge`'s: an unknown method or
    status, a confidence outside [0, 1], a cross-label pair, a node paired
    with itself or a missing endpoint node all raise there, and nothing is
    written.

    Returns the key of the edge written, endpoints in canonical order.

    The read and the write are two adapter calls, not one transaction. A
    concurrent verdict written between them would be overwritten, so callers
    must not run a re-proposal for a pair while a human verdict on the same
    pair is being written. M2 resolution runs as a single writer; a backend
    that later needs concurrent writers should make this an atomic
    conditional update behind the same function.
    """
    ref = adapter.canonical_edge_ref(EdgeRef(SAME_AS, a, b))
    existing = adapter.get_edge(ref)
    stored = dict(existing.properties) if existing is not None else {}

    if review_status is None:
        kept = stored.get("review_status")
        review_status = kept if kept is not None else UNREVIEWED
    if created_at is None:
        created_at = stored.get("created_at") or datetime.now(UTC).isoformat()

    return adapter.upsert_edge(
        SAME_AS,
        ref.source,
        ref.target,
        {
            "confidence": confidence,
            "method": method,
            "review_status": review_status,
            "created_at": created_at,
        },
    )
