"""Extension manifest registration facade and discovery state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .extension_contracts import ExtensionManifest
from .extension_registry_projection import build_extension_info
from .extension_registry_state import ExtensionRegistryState
from .extension_registry_writer import ExtensionRegistryWriter


@dataclass(slots=True)
class ExtensionRegistry:
    _state: ExtensionRegistryState = field(default_factory=ExtensionRegistryState)
    _writer: ExtensionRegistryWriter = field(init=False)

    def __post_init__(self) -> None:
        self._writer = ExtensionRegistryWriter(self._state)

    def register_manifest(self, manifest: ExtensionManifest, *, replace_existing: bool = False) -> None:
        self._writer.register_manifest(manifest, replace_existing=replace_existing)

    def register_manifest_kinds(self, manifest: ExtensionManifest) -> None:
        self._writer.register_manifest_kinds(manifest)

    def register_manifest_templates(self, manifest: ExtensionManifest) -> None:
        self._writer.register_manifest_templates(manifest)

    def unregister_manifest(self, manifest: ExtensionManifest) -> None:
        self._writer.unregister_manifest(manifest)

    def unregister_extension(self, extension_id: str) -> None:
        self._writer.unregister_extension(extension_id)

    def has_extension(self, extension_id: str) -> bool:
        return self._state.has_extension(extension_id)

    def list_registered_manifests(self) -> tuple[ExtensionManifest, ...]:
        return self._state.list_registered_manifests()

    def clear_extensions_except(self, retained_extension_ids: set[str] | frozenset[str]) -> None:
        self._writer.clear_extensions_except(retained_extension_ids)

    def register_external_manifests(self, manifests: list[ExtensionManifest]) -> None:
        self._writer.register_external_manifests(manifests)

    def get_extension_info(self, extension_id: str) -> dict[str, Any]:
        manifest = self._state.get_manifest(extension_id)
        if manifest is None:
            raise ValueError(f"Extension '{extension_id}' is not registered")
        return build_extension_info(manifest)

    def list_extension_infos(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.get_extension_info(manifest.extension_id) for manifest in self.list_registered_manifests())

    def validate_manifest_versions(self, manifest: ExtensionManifest) -> None:
        self._writer.validate_manifest_versions(manifest)
