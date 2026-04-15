"""Extension registry write orchestration."""

from __future__ import annotations

from backend.extensions.extension_guard import validate_extension_manifest_contract

from .extension_contracts import ExtensionManifest, validate_extension_manifest_versions
from .extension_registry_bootstrap_writer import ExtensionRegistryBootstrapWriter
from .extension_registry_kind_writer import ExtensionKindRegistryWriter
from .extension_registry_state import ExtensionRegistryState
from .extension_registry_template_writer import ExtensionTemplateRegistryWriter


class ExtensionRegistryWriter:
    def __init__(self, state: ExtensionRegistryState) -> None:
        self._state = state
        self._kind_writer = ExtensionKindRegistryWriter()
        self._template_writer = ExtensionTemplateRegistryWriter()
        self._bootstrap_writer = ExtensionRegistryBootstrapWriter(
            state,
            kind_writer=self._kind_writer,
            template_writer=self._template_writer,
            unregister_extension=self.unregister_extension,
        )

    def register_manifest(self, manifest: ExtensionManifest, *, replace_existing: bool = False) -> None:
        extension_id = str(manifest.extension_id or "").strip()
        if not extension_id:
            raise ValueError("extension_id is required")
        validate_extension_manifest_contract(manifest)
        existing_manifest = self._state.get_manifest(extension_id)
        if existing_manifest is not None and not replace_existing:
            raise ValueError(f"Extension '{extension_id}' is already registered")

        self.validate_manifest_versions(manifest)
        if existing_manifest is not None:
            self.unregister_manifest(existing_manifest)
        self.register_manifest_kinds(manifest)
        self.register_manifest_templates(manifest)
        self._state.store_manifest(manifest)

    def register_manifest_kinds(self, manifest: ExtensionManifest) -> None:
        self._kind_writer.register_manifest_kinds(manifest)

    def register_manifest_templates(self, manifest: ExtensionManifest) -> None:
        self._template_writer.register_manifest_templates(manifest)

    def unregister_manifest(self, manifest: ExtensionManifest) -> None:
        self._template_writer.unregister_manifest_templates(manifest)
        self._kind_writer.unregister_manifest_kinds(manifest)

    def unregister_extension(self, extension_id: str) -> None:
        manifest = self._state.get_manifest(extension_id)
        if manifest is None:
            return
        self.unregister_manifest(manifest)
        self._state.remove_manifest(extension_id)

    def clear_extensions_except(self, retained_extension_ids: set[str] | frozenset[str]) -> None:
        self._bootstrap_writer.clear_extensions_except(retained_extension_ids)

    def register_external_manifests(self, manifests: list[ExtensionManifest]) -> None:
        self._bootstrap_writer.register_external_manifests(manifests)

    def validate_manifest_versions(self, manifest: ExtensionManifest) -> None:
        validate_extension_manifest_versions(manifest)
