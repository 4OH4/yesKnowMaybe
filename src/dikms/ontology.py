"""Ontology loading and prompt rendering.

The ontology is config, not code (config/ontology.yaml). This module loads it
and renders the extractable portion into the extraction prompt. System-managed
entity types (Source) and structural relations (extracted_from, same_as,
mentions) are excluded from what the LLM is asked to emit — those are attached
by the pipeline or the resolution stage, never freely generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Attribute:
    name: str
    description: str
    values: list[str] = field(default_factory=list)  # controlled vocab, or empty for free text


@dataclass
class EntityType:
    name: str
    description: str
    examples: list[str] = field(default_factory=list)
    system_managed: bool = False
    attributes: list[Attribute] = field(default_factory=list)


@dataclass
class RelationType:
    name: str
    description: str
    domain: list[str] = field(default_factory=list)
    range: list[str] = field(default_factory=list)
    structural: bool = False


@dataclass
class Ontology:
    version: int
    entity_types: list[EntityType]
    relation_types: list[RelationType]

    @classmethod
    def load(cls, path: str | Path) -> "Ontology":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        entities = [
            EntityType(
                name=e["name"],
                description=e.get("description", "").strip(),
                examples=e.get("examples", []),
                system_managed=e.get("system_managed", False),
                attributes=[
                    Attribute(
                        name=a["name"],
                        description=a.get("description", "").strip(),
                        values=a.get("values", []),
                    )
                    for a in e.get("attributes", [])
                ],
            )
            for e in data.get("entity_types", [])
        ]
        relations = [
            RelationType(
                name=r["name"],
                description=r.get("description", "").strip(),
                domain=r.get("domain", []),
                range=r.get("range", []),
                structural=r.get("structural", False),
            )
            for r in data.get("relation_types", [])
        ]
        return cls(version=data["version"], entity_types=entities, relation_types=relations)

    def extractable_entities(self) -> list[EntityType]:
        return [e for e in self.entity_types if not e.system_managed]

    def extractable_relations(self) -> list[RelationType]:
        return [r for r in self.relation_types if not r.structural]

    def entity_names(self) -> set[str]:
        return {e.name for e in self.entity_types}

    def relation_names(self) -> set[str]:
        return {r.name for r in self.relation_types}

    def render_for_prompt(self) -> str:
        """Human-readable ontology block for the extraction prompt."""
        lines = [f"ONTOLOGY (version {self.version})", "", "Entity types:"]
        for e in self.extractable_entities():
            ex = f" e.g. {', '.join(e.examples)}" if e.examples else ""
            lines.append(f"- {e.name}: {e.description}{ex}")
            for a in e.attributes:
                vocab = f" (one of: {', '.join(a.values)})" if a.values else ""
                lines.append(f"    · attribute {a.name}{vocab}: {a.description}")
        lines += ["", "Relation types (source_type -> target_type):"]
        for r in self.extractable_relations():
            dom = "/".join(r.domain) or "*"
            rng = "/".join(r.range) or "*"
            lines.append(f"- {r.name} ({dom} -> {rng}): {r.description}")
        return "\n".join(lines)
