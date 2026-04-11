"""Validation helpers for external extension bootstrap."""

from __future__ import annotations

from .extension_contracts import ExtensionManifest
from .extension_registry import ExtensionRegistry


class ExtensionBootstrapValidator:
    def validate_external_manifests(
        self,
        manifests: list[ExtensionManifest],
        *,
        registry: ExtensionRegistry,
        force_reload_external: bool,
    ) -> None:
        for manifest in manifests:
            if manifest.extension_id == "zen70.core":
                raise ValueError("External manifests must not reuse reserved extension_id 'zen70.core'")

        for manifest in manifests:
            registry.validate_manifest_versions(manifest)
            if registry.has_extension(manifest.extension_id) and not force_reload_external:
                raise ValueError(f"Extension '{manifest.extension_id}' is already registered")
