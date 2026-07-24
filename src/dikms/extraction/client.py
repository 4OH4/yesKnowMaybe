"""Gemini extraction via Vertex AI (DECISIONS.md: extraction model).

The google-genai client is imported lazily so the rest of the package — and
the parse/chunk stages in particular — import and run without GCP credentials
or the SDK present. Constructing a GeminiExtractor is cheap; the client (and
thus the credential requirement) is only created on first extract() call.

Response parsing is defensive: the model is asked for a bare JSON object, but
a stray code fence or preamble is tolerated. A response that cannot be parsed
is surfaced with its raw text attached, not silently dropped — M0's whole
point is judging real output.
"""

from __future__ import annotations

import json
import re
import time

from ..models import (
    Chunk,
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelation,
    Provenance,
    TypeProposal,
)
from ..ontology import Ontology
from .prompt import SYSTEM_INSTRUCTION, build_user_prompt

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ExtractionResponseError(RuntimeError):
    """Model response could not be parsed as the expected JSON."""

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


# Backoff schedule (seconds) for transient RESOURCE_EXHAUSTED (429) / 503
# responses. New GCP projects have low default Gemini quotas, so a long
# document routinely trips per-minute limits; backing off spaces requests out
# rather than failing the run.
_RETRY_BACKOFF = (5, 15, 30, 60, 60)
_RETRYABLE_CODES = {429, 503}


class GeminiExtractor:
    def __init__(
        self,
        project: str | None,
        location: str,
        model: str,
        temperature: float = 0.0,
        retry_backoff: tuple[int, ...] = _RETRY_BACKOFF,
    ):
        self.project = project
        self.location = location
        self.model = model
        self.temperature = temperature
        # Empty schedule => a single attempt, no backoff (fire requests straight
        # through so raw API behaviour, including 429s, is observable).
        self.retry_backoff = retry_backoff
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai  # lazy: keeps GCP optional for parse/chunk

            if not self.project:
                raise RuntimeError(
                    "No GCP project configured. Set `vertex.project` in "
                    "config/extraction.yaml or GOOGLE_CLOUD_PROJECT, and see "
                    "SETUP.md for authentication."
                )
            self._client = genai.Client(
                vertexai=True, project=self.project, location=self.location
            )
        return self._client

    def extract(self, ontology: Ontology, chunk: Chunk) -> ChunkExtraction:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=self.temperature,
            response_mime_type="application/json",
        )
        contents = build_user_prompt(ontology, chunk)
        response = self._generate_with_retry(client, contents, config)
        raw = response.text or ""
        return parse_response(raw, chunk, self.model)

    def _generate_with_retry(self, client, contents, config):
        from google.genai import errors

        for attempt, wait in enumerate((*self.retry_backoff, None)):
            try:
                return client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except errors.APIError as exc:
                # Retry only transient quota/availability codes; re-raise the
                # rest (bad request, auth, etc.) immediately.
                if getattr(exc, "code", None) not in _RETRYABLE_CODES or wait is None:
                    raise
                time.sleep(wait)


def parse_response(raw: str, chunk: Chunk, model: str) -> ChunkExtraction:
    """Parse a raw model response into a ChunkExtraction with provenance.

    Separated from the network call so it is unit-testable against captured
    fixtures without Vertex access.
    """
    cleaned = _FENCE.sub("", raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionResponseError(
            f"{chunk.chunk_id}: response was not valid JSON ({exc})", raw
        ) from exc

    def prov(quote: str) -> Provenance:
        return Provenance(
            source_id=chunk.source_id,
            chunk_id=chunk.chunk_id,
            section_refs=list(chunk.section_refs),
            supporting_quote=quote or "",
            extraction_model=model,
        )

    entities = [
        ExtractedEntity(
            entity_type=e.get("type", ""),
            name=e.get("name", ""),
            provenance=prov(e.get("quote", "")),
            attributes=e.get("attributes", {}) or {},
        )
        for e in data.get("entities", [])
    ]
    relations = [
        ExtractedRelation(
            relation_type=r.get("type", ""),
            source_name=r.get("source_name", ""),
            source_type=r.get("source_type", ""),
            target_name=r.get("target_name", ""),
            target_type=r.get("target_type", ""),
            provenance=prov(r.get("quote", "")),
        )
        for r in data.get("relations", [])
    ]
    proposals = [
        TypeProposal(
            kind=p.get("kind", ""),
            name=p.get("name", ""),
            description=p.get("description", ""),
            justification=p.get("justification", ""),
            provenance=prov(p.get("quote", "")),
        )
        for p in data.get("proposals", [])
    ]
    return ChunkExtraction(
        chunk_id=chunk.chunk_id,
        entities=entities,
        relations=relations,
        proposals=proposals,
        raw_response=raw,
    )
