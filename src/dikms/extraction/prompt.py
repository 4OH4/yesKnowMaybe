"""Extraction prompt construction, constrained to the ontology.

The prompt enforces the design constraints directly:
* entities/relations must use only the current ontology's types;
* anything important that does not fit becomes a *proposal* (HITL gating —
  the model proposes, it never extends the ontology itself);
* every item must carry a verbatim supporting quote from the chunk, and the
  model is told NOT to emit character offsets (offsets are derived later by
  verified alignment, not trusted from the model).
"""

from __future__ import annotations

from ..models import Chunk
from ..ontology import Ontology

SYSTEM_INSTRUCTION = """\
You extract structured knowledge about the UK defence industry from source \
text, strictly against a provided ontology.

Rules:
1. Only use entity and relation types that appear in the ontology. Do not \
invent types for the `entities` or `relations` arrays.
2. If the text clearly describes an important entity or relationship that does \
NOT fit any ontology type, do not force it — instead add an item to \
`proposals` describing the type you would need. Proposals are reviewed by a \
human; they never change the ontology automatically.
3. For every entity, relation and proposal, include a `quote`: a SHORT span of \
text copied VERBATIM from the provided chunk that supports the item. Copy it \
exactly, character for character. Never paraphrase the quote and never invent \
character positions or offsets.
4. Extract only what the text supports. Do not add outside knowledge.
5. Respond with a single JSON object and nothing else.
"""

_SCHEMA = """\
Respond with JSON of exactly this shape:
{
  "entities": [
    {"type": "<ontology entity type>", "name": "<canonical name>",
     "attributes": {"<key>": "<value>"}, "quote": "<verbatim supporting text>"}
  ],
  "relations": [
    {"type": "<ontology relation type>",
     "source_name": "...", "source_type": "<entity type>",
     "target_name": "...", "target_type": "<entity type>",
     "quote": "<verbatim supporting text>"}
  ],
  "proposals": [
    {"kind": "entity|relation", "name": "...", "description": "...",
     "justification": "<why the existing ontology is insufficient>",
     "quote": "<verbatim supporting text>"}
  ]
}
Use empty arrays where there is nothing to report."""


def build_user_prompt(ontology: Ontology, chunk: Chunk) -> str:
    """The per-chunk user message: ontology + schema + the chunk text."""
    return (
        f"{ontology.render_for_prompt()}\n\n"
        f"{_SCHEMA}\n\n"
        f"--- CHUNK (source: {chunk.source_id}, {', '.join(chunk.section_refs) or 'no section ref'}) ---\n"
        f"{chunk.text}\n"
        f"--- END CHUNK ---"
    )
