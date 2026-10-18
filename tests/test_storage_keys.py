"""The entity node-id rule: label plus normalised name.

The failure mode that matters is a quiet one: two genuinely different
entities given one id (a fusion, which CLAUDE.md constraint 2 forbids and
nothing downstream can undo), or one entity given two ids for a difference
that carries no meaning. The property tests below generate names from an
alphabet chosen to provoke both (case pairs, compatibility characters,
combining marks, odd whitespace, punctuation) with a seeded generator, so a
failure reproduces.
"""

from __future__ import annotations

import random
import unicodedata

import pytest

from dikms.storage import node_key, normalise_name
from dikms.storage.keys import KEY_SEPARATOR

#: Characters that differ only in case, compatibility form or spacing from
#: something else here, plus ones that must keep names apart.
_ALPHABET = (
    "aAbBsSzZ09"  # case pairs and digits
    "ßẞﬁＡａ²"  # sharp s, a ligature, full-width letters, a superscript
    "éÉ́"  # precomposed accents and a combining acute
    " \t\n  　"  # ASCII and Unicode whitespace
    "-./&'():"  # punctuation that distinguishes names (and the separator)
)
_WHITESPACE = " \t\n  　"
_MEANINGFUL = "-./&'():09"


def _names(seed: int, count: int = 400) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 12)))
        for _ in range(count)
    ]


def _variant(rng: random.Random, name: str) -> str:
    """The same name with only meaningless differences: random case, extra
    whitespace around and between the existing runs, NFKC/NFD forms."""
    words = name.split()
    cased = [
        "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in word)
        for word in words
    ]
    joined = "".join(
        word + "".join(rng.choice(_WHITESPACE) for _ in range(rng.randint(1, 3)))
        for word in cased
    )
    form = rng.choice(("NFC", "NFD", "NFKC", "NFKD"))
    pad = rng.choice(("", " ", " ", "\n\t"))
    return pad + unicodedata.normalize(form, joined) + pad


@pytest.mark.parametrize("seed", range(5))
def test_normalising_is_idempotent(seed: int) -> None:
    for name in _names(seed):
        once = normalise_name(name)
        assert normalise_name(once) == once, repr(name)
        assert once == once.strip() and "  " not in once
        assert not any(c.isspace() and c != " " for c in once), repr(name)


@pytest.mark.parametrize("seed", range(5))
def test_meaningless_differences_give_the_same_key(seed: int) -> None:
    rng = random.Random(seed)
    for name in _names(seed):
        if not normalise_name(name):
            continue
        variant = _variant(rng, name)
        assert node_key("Programme", variant) == node_key("Programme", name), (
            repr(name), repr(variant)
        )


@pytest.mark.parametrize("seed", range(5))
def test_a_meaningful_difference_always_gives_a_different_key(seed: int) -> None:
    """Changing, adding or removing one punctuation mark or digit never maps
    to the same id: nothing beyond case, compatibility form and whitespace is
    folded away."""
    rng = random.Random(seed)
    for name in _names(seed):
        base = normalise_name(name)
        if not base:
            continue
        i = rng.randrange(len(base) + 1)
        mark = rng.choice(_MEANINGFUL)
        changed = base[:i] + mark + base[i:]
        assert node_key("Programme", changed) != node_key("Programme", base), (
            repr(base), repr(changed)
        )
        keep = [j for j, c in enumerate(base) if c in _MEANINGFUL]
        if keep:
            j = rng.choice(keep)
            dropped = base[:j] + base[j + 1:]
            if normalise_name(dropped):
                assert node_key("Programme", dropped) != node_key("Programme", base)


@pytest.mark.parametrize("seed", range(3))
def test_the_key_is_injective_in_label_and_normalised_name(seed: int) -> None:
    """Two keys are equal exactly when their labels and normalised names are,
    and every key splits back into the two at the first separator."""
    rng = random.Random(seed)
    labels = ("Programme", "Organisation", "Capability")
    pairs = [
        (rng.choice(labels), name) for name in _names(seed, 200) if normalise_name(name)
    ]
    for (l1, n1), (l2, n2) in zip(pairs, rng.sample(pairs, len(pairs)), strict=True):
        same = l1 == l2 and normalise_name(n1) == normalise_name(n2)
        assert (node_key(l1, n1) == node_key(l2, n2)) == same, (l1, n1, l2, n2)
    for label, name in pairs:
        head, _, tail = node_key(label, name).partition(KEY_SEPARATOR)
        assert (head, tail) == (label, normalise_name(name))


def test_worked_examples() -> None:
    assert node_key("Organisation", "  BAE Systems\n") == "Organisation:bae systems"
    assert node_key("Organisation", "BAE SYSTEMS") == node_key(
        "Organisation", "bae systems"
    )
    assert node_key("Location", "Straße") == node_key("Location", "STRASSE")
    assert node_key("Programme", "ＡＳＧＡＲＤ") == node_key("Programme", "ASGARD")
    # Kept apart: punctuation, digits and type.
    assert node_key("Hardware", "F-35") != node_key("Hardware", "F35")
    assert node_key("Organisation", "U.K.") != node_key("Organisation", "UK")
    assert node_key("Hardware", "Type 26") != node_key("Hardware", "Type 31")
    assert node_key("Programme", "ASGARD") != node_key("Capability", "ASGARD")


@pytest.mark.parametrize("name", ["", " ", "\n\t", " 　"])
def test_an_empty_name_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        node_key("Programme", name)


@pytest.mark.parametrize("label", ["", "Pro gramme", "a:b", "1abc"])
def test_a_label_that_is_not_an_identifier_is_refused(label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        node_key(label, "ASGARD")


def test_a_non_string_name_is_refused() -> None:
    with pytest.raises(TypeError):
        normalise_name(None)  # type: ignore[arg-type]
