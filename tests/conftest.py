"""Shared fixtures. No test in this package needs a database or credentials."""

from __future__ import annotations

from pathlib import Path

import pytest

from dikms.config import ExtractionConfig
from dikms.ontology import Ontology
from dikms.storage import GraphSchema, build_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = REPO_ROOT / "config" / "ontology.yaml"
EXTRACTION_CONFIG_PATH = REPO_ROOT / "config" / "extraction.yaml"


@pytest.fixture(scope="session")
def ontology() -> Ontology:
    return Ontology.load(ONTOLOGY_PATH)


@pytest.fixture(scope="session")
def config() -> ExtractionConfig:
    return ExtractionConfig.load(EXTRACTION_CONFIG_PATH)


@pytest.fixture(scope="session")
def schema(ontology: Ontology, config: ExtractionConfig) -> GraphSchema:
    return build_schema(ontology, config.require_embedding().dimension)
