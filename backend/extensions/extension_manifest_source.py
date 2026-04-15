"""Manifest directory discovery and file reading for external extensions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_EXTENSION_MANIFESTS_DIR = Path("contracts/extensions")


class ExtensionManifestSource:
    def resolve_manifests_dir(self, manifests_dir: str | Path | None = None) -> Path:
        raw = manifests_dir or os.getenv("ZEN70_EXTENSION_MANIFESTS_DIR") or DEFAULT_EXTENSION_MANIFESTS_DIR
        return Path(raw).resolve()

    def iter_manifest_paths(self, manifests_dir: str | Path | None = None) -> tuple[Path, ...]:
        directory = self.resolve_manifests_dir(manifests_dir)
        if not directory.exists():
            return ()
        if not directory.is_dir():
            raise ValueError(f"Extension manifests path '{directory}' must be a directory")
        return tuple(sorted(candidate for candidate in directory.rglob("*") if self._is_manifest_candidate(candidate)))

    def read_manifest_file(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
        if not isinstance(payload, dict):
            raise ValueError(f"Extension manifest '{path}' must decode to an object")
        return payload

    def _is_manifest_candidate(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name.startswith(".") or ".example." in path.name:
            return False
        return path.suffix.lower() in {".json", ".yaml", ".yml"}
