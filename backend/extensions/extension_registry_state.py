"""Extension registry state container."""

from __future__ import annotations

from dataclasses import dataclass, field

from .extension_contracts import ExtensionManifest


@dataclass(slots=True)
class ExtensionRegistryState:
    manifests: dict[str, ExtensionManifest] = field(default_factory=dict)

    def get_manifest(self, extension_id: str) -> ExtensionManifest | None:
        return self.manifests.get(extension_id)

    def store_manifest(self, manifest: ExtensionManifest) -> None:
        self.manifests[manifest.extension_id] = manifest

    def remove_manifest(self, extension_id: str) -> ExtensionManifest | None:
        return self.manifests.pop(extension_id, None)

    def has_extension(self, extension_id: str) -> bool:
        return extension_id in self.manifests

    def list_registered_manifests(self) -> tuple[ExtensionManifest, ...]:
        return tuple(self.manifests[extension_id] for extension_id in sorted(self.manifests.keys()))
