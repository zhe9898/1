"""Manifest parsing facade for external extensions."""

from __future__ import annotations

from typing import Any

from .extension_contracts import ExtensionManifest
from .extension_manifest_file_contracts import FileExtensionManifest
from .extension_manifest_projection import project_file_manifest


def parse_file_manifest(payload: dict[str, Any], *, source_manifest_path: str) -> ExtensionManifest:
    file_manifest = FileExtensionManifest.model_validate(payload)
    return project_file_manifest(file_manifest, source_manifest_path=source_manifest_path)
