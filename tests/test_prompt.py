"""Tests for the extraction prompt (issue #23).

Rule 3 must require the supporting quote to be one contiguous passage, with no
words skipped by an ellipsis unless the ellipsis is in the source, so the model
stops stitching phrases from separate places into a quote that span alignment
cannot locate. Offline: the Gemini client is replaced by a fake that
records what would have been sent.
"""

from __future__ import annotations

import re
from pathlib import Path

from dikms.extraction import prompt
from dikms.extraction.client import GeminiExtractor
from dikms.models import Chunk
from dikms.ontology import Ontology

ONTOLOGY = Path(__file__).resolve().parents[1] / "config" / "ontology.yaml"

CONTIGUITY_PHRASES = (
    "ONE CONTIGUOUS passage of the chunk",
    "never phrases joined together from separate places",
    "if the support is spread out, quote the single most supportive passage",
)

# Explicit elision ("current and future warhead...programmes") is one form of
# stitching in the M0 output. The rule forbids inserting an ellipsis but must
# not ban one outright, because a source can contain a genuine ellipsis.
ELLIPSIS_PHRASES = (
    "Never skip words with an ellipsis",
    '("..." or "…")',
    "a quote may contain an ellipsis only where the chunk itself contains one",
)


def _rules(instruction: str) -> dict[int, str]:
    """Split the system instruction into its numbered rules."""
    matches = list(re.finditer(r"^(\d+)\. ", instruction, re.MULTILINE))
    rules = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(instruction)
        rules[int(m.group(1))] = instruction[m.end() : end].strip()
    return rules


def test_rules_are_numbered_one_to_eight() -> None:
    assert sorted(_rules(prompt.SYSTEM_INSTRUCTION)) == list(range(1, 9))


def test_rule_three_requires_a_contiguous_quote() -> None:
    rule3 = _rules(prompt.SYSTEM_INSTRUCTION)[3]
    for phrase in CONTIGUITY_PHRASES:
        assert phrase in rule3
    # The pre-existing rule 3 requirements still stand.
    assert "VERBATIM" in rule3
    assert "never invent character positions or offsets" in rule3


def test_rule_three_forbids_inserted_ellipses() -> None:
    rule3 = _rules(prompt.SYSTEM_INSTRUCTION)[3]
    for phrase in ELLIPSIS_PHRASES:
        assert phrase in rule3


def test_contiguity_rule_appears_only_in_rule_three() -> None:
    rules = _rules(prompt.SYSTEM_INSTRUCTION)
    for number, text in rules.items():
        if number != 3:
            assert "CONTIGUOUS" not in text
            assert "ellipsis" not in text


class _FakeResponse:
    text = '{"entities": [], "relations": [], "proposals": []}'


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse()


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_extractor_sends_the_contiguity_rule() -> None:
    ontology = Ontology.load(ONTOLOGY)
    chunk = Chunk(
        chunk_id="c0",
        source_id="test-source",
        parse_id="p0",
        ordinal=0,
        text="BAE Systems builds the Type 26 frigate.",
        start=0,
        end=39,
        section_refs=["p1"],
    )
    extractor = GeminiExtractor(project="offline", location="test", model="fake")
    fake = _FakeClient()
    extractor._client = fake

    extractor.extract(ontology, chunk)

    (call,) = fake.models.calls
    sent = call["config"].system_instruction
    assert sent == prompt.SYSTEM_INSTRUCTION
    for phrase in CONTIGUITY_PHRASES + ELLIPSIS_PHRASES:
        assert phrase in _rules(sent)[3]
    assert call["contents"] == prompt.build_user_prompt(ontology, chunk)
