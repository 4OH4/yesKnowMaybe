"""The identity rule for entity node ids, shared by every backend and caller.

:meth:`StorageAdapter.upsert_node` takes ``node_id`` from the caller and
merges on it, so the rule deciding when two extracted mentions are *one
stored node* lives here, above every backend, and ingestion (#10) builds ids
with it rather than each backend inventing its own.

The rule: **an entity's id is its label plus its normalised name.**
:func:`normalise_name` removes only differences that cannot distinguish two
entities:

* Unicode compatibility forms (NFKC): a non-breaking space, a full-width
  letter or a ligature becomes its plain equivalent;
* letter case (Unicode case folding, so ``Straße`` and ``STRASSE`` agree);
* whitespace: leading and trailing whitespace is dropped and every internal
  run (spaces, tabs, line breaks from PDF text) becomes one space.

Nothing else is touched. Punctuation, hyphens, digits and word order all
still distinguish names, so ``F-35`` and ``F35``, or ``U.K.`` and ``UK``,
stay separate nodes.

This is **not entity resolution** (``ARCHITECTURE.md``: entity resolution).
It only decides when two writes are the same node. Abbreviations, aliases
and near-matches are M2's job, and M2 links them with ``same_as`` edges
rather than fusing nodes. The rule is deliberately conservative: a spurious
duplicate is recoverable with one ``same_as`` edge, and a wrong merge is not
(CLAUDE.md constraint 2).

The ``Source`` node is keyed differently: its id is the manifest id from
``data/sources.yaml``, so this helper is for entity nodes only.
"""

from __future__ import annotations

import unicodedata

#: Separates the label from the normalised name in a node id.
KEY_SEPARATOR = ":"


def normalise_name(name: str) -> str:
    """The form of an entity name that :func:`node_key` compares.

    NFKC, then case folding, then NFKC again (case folding can produce a
    string that is no longer NFKC), then whitespace collapsed. Idempotent:
    ``normalise_name(normalise_name(x)) == normalise_name(x)``.
    """
    if not isinstance(name, str):
        raise TypeError(f"an entity name must be a string, got {type(name).__name__}")
    folded = unicodedata.normalize(
        "NFKC", unicodedata.normalize("NFKC", name).casefold()
    )
    return " ".join(folded.split())


def node_key(label: str, name: str) -> str:
    """The node id for an entity of type ``label`` extracted as ``name``.

    ``label:normalised name``, e.g. ``node_key("Organisation", "BAE  Systems")``
    is ``"Organisation:bae systems"``. The label is part of the id, so the
    same name under two types gives two nodes; re-typing an entity is not an
    identity question. ``label`` must be a plain identifier (as every
    ontology label is), so the id splits back unambiguously at the first
    separator. A name that normalises to nothing names no entity and is
    refused.
    """
    if not isinstance(label, str) or not label.isidentifier():
        raise ValueError(f"node_key: {label!r} is not a valid label")
    normalised = normalise_name(name)
    if not normalised:
        raise ValueError(f"node_key: {label} name {name!r} is empty once normalised")
    return f"{label}{KEY_SEPARATOR}{normalised}"
