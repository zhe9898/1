"""Bootstrap-time registry reconciliation helpers."""

from __future__ import annotations

from collections.abc import Callable

from .extension_contracts import ExtensionManifest
from .extension_registry_kind_writer import ExtensionKindRegistryWriter
from .extension_registry_state import ExtensionRegistryState
from .extension_registry_template_writer import ExtensionTemplateRegistryWriter


class ExtensionRegistryBootstrapWriter:
    def __init__(
        self,
        state: ExtensionRegistryState,
        *,
        kind_writer: ExtensionKindRegistryWriter,
        template_writer: ExtensionTemplateRegistryWriter,
        unregister_extension: Callable[[str], None],
    ) -> None:
        self._state = state
        self._kind_writer = kind_writer
        self._template_writer = template_writer
        self._unregister_extension = unregister_extension

    def clear_extensions_except(self, retained_extension_ids: set[str] | frozenset[str]) -> None:
        for manifest in self._state.list_registered_manifests():
            if manifest.extension_id in retained_extension_ids:
                continue
            self._unregister_extension(manifest.extension_id)

    def register_external_manifests(self, manifests: list[ExtensionManifest]) -> None:
        for manifest in manifests:
            self._kind_writer.register_manifest_kinds(manifest)
        for manifest in manifests:
            self._template_writer.register_manifest_templates(manifest)
            self._state.store_manifest(manifest)
