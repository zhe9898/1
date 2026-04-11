"""Manifest loading facade for external extensions."""

from __future__ import annotations

from pathlib import Path

from .extension_contracts import ExtensionManifest
from .extension_manifest_parser import parse_file_manifest
from .extension_manifest_source import DEFAULT_EXTENSION_MANIFESTS_DIR, ExtensionManifestSource


class ExtensionManifestLoader:
    def __init__(self, source: ExtensionManifestSource | None = None) -> None:
        self._source = source or ExtensionManifestSource()

    def resolve_manifests_dir(self, manifests_dir: str | Path | None = None) -> Path:
        return self._source.resolve_manifests_dir(manifests_dir)

    def load_from_dir(self, manifests_dir: str | Path | None = None) -> list[ExtensionManifest]:
        manifests: list[ExtensionManifest] = []
        seen_extension_ids: dict[str, Path] = {}

        for path in self._source.iter_manifest_paths(manifests_dir):
            manifest = parse_file_manifest(
                self._source.read_manifest_file(path),
                source_manifest_path=str(path),
            )
            previous = seen_extension_ids.get(manifest.extension_id)
            if previous is not None:
                raise ValueError(f"Duplicate extension_id '{manifest.extension_id}' in '{previous}' and '{path}'")
            seen_extension_ids[manifest.extension_id] = path
            manifests.append(manifest)

        return manifests
