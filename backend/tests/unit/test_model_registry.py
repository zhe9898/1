from __future__ import annotations

from pathlib import Path

from backend.platform.db.migration_governance import collect_model_tables
from backend.models import CANONICAL_MODEL_MODULES, load_canonical_model_metadata


def test_registry_declares_every_model_module() -> None:
    models_dir = Path(__file__).resolve().parents[2] / "models"
    declared = set(CANONICAL_MODEL_MODULES)
    discovered = {f"backend.models.{path.stem}" for path in models_dir.glob("*.py") if path.stem not in {"__init__", "base", "registry"}}

    assert declared == discovered


def test_canonical_metadata_covers_every_model_table() -> None:
    metadata = load_canonical_model_metadata()

    assert set(metadata.tables) == collect_model_tables()

