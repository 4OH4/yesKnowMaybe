"""Config loading tests for the blocks the storage layer depends on."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dikms.config import ConfigError, ExtractionConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_CONFIG_PATH = REPO_ROOT / "config" / "extraction.yaml"

BASE = """
extraction:
  model: "gemini-2.5-flash"
storage:
  backend: "kuzu"
"""


def _load(tmp_path: Path, text: str) -> ExtractionConfig:
    path = tmp_path / "extraction.yaml"
    path.write_text(text, encoding="utf-8")
    return ExtractionConfig.load(path)


def test_embedding_dimension_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="embedding.dimension"):
        _load(tmp_path, BASE + '\nembedding:\n  model: "e"\n')


def test_embedding_dimension_must_be_a_positive_integer(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="positive integer"):
        _load(tmp_path, BASE + '\nembedding:\n  model: "e"\n  dimension: 0\n')


def test_missing_blocks_only_bite_at_the_point_of_use(tmp_path: Path) -> None:
    """An absent block does not stop `dikms extract`, which reads neither — but
    anything that needs one gets a clear error rather than a default in code."""
    config = _load(tmp_path, 'extraction:\n  model: "m"\n')
    assert config.model == "m"
    assert config.embedding is None and config.storage is None

    with pytest.raises(ConfigError, match="never hardcoded"):
        config.require_embedding()
    with pytest.raises(ConfigError, match="not chosen in code"):
        config.require_storage()


def test_a_present_but_incomplete_block_still_fails_at_load(tmp_path: Path) -> None:
    text = 'extraction:\n  model: "m"\nstorage:\n  kuzu:\n    path: p\n'
    with pytest.raises(ConfigError, match="storage.backend"):
        _load(tmp_path, text)


def test_an_empty_block_is_present_not_absent(tmp_path: Path) -> None:
    """`storage:` with nothing under it is a config mistake to report, not a
    reason to treat the block as missing."""
    with pytest.raises(ConfigError, match="storage.backend"):
        _load(tmp_path, 'extraction:\n  model: "m"\nstorage:\n')
    with pytest.raises(ConfigError, match="embedding.dimension"):
        _load(tmp_path, 'extraction:\n  model: "m"\nembedding: {}\n')


def test_real_config_matches_the_yaml_file(config: ExtractionConfig) -> None:
    """Values come from config/extraction.yaml verbatim — no code-side default
    is standing in for any of them."""
    raw = yaml.safe_load(EXTRACTION_CONFIG_PATH.read_text(encoding="utf-8"))
    embedding = config.require_embedding()
    assert embedding.dimension == raw["embedding"]["dimension"]
    assert embedding.model == raw["embedding"]["model"]
    assert embedding.normalise == raw["embedding"]["normalise"]

    storage = config.require_storage()
    assert storage.backend == raw["storage"]["backend"]
    assert storage.kuzu.path == raw["storage"]["kuzu"]["path"]
    assert storage.spanner.instance == raw["storage"]["spanner"]["instance"]

    # Existing settings still load as before.
    assert config.model == raw["extraction"]["model"]
    assert config.target_chars == raw["chunking"]["target_chars"]
