"""Extension runtime bootstrap orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .extension_bootstrap_builtins import ExtensionBuiltinBootstrap
from .extension_bootstrap_state import ExtensionBootstrapState
from .extension_bootstrap_validation import ExtensionBootstrapValidator
from .extension_manifest_loader import ExtensionManifestLoader
from .extension_registry import ExtensionRegistry


@dataclass(slots=True)
class ExtensionBootstrapper:
    loader: ExtensionManifestLoader
    registry: ExtensionRegistry
    state: ExtensionBootstrapState = field(default_factory=ExtensionBootstrapState)
    builtin_bootstrap: ExtensionBuiltinBootstrap = field(default_factory=ExtensionBuiltinBootstrap)
    validator: ExtensionBootstrapValidator = field(default_factory=ExtensionBootstrapValidator)

    def bootstrap_runtime(
        self,
        manifests_dir: str | Path | None = None,
        *,
        force_reload_external: bool = False,
    ) -> tuple[dict[str, object], ...]:
        self.builtin_bootstrap.ensure_builtin_extensions_registered(self.registry, state=self.state)
        resolved_dir = str(self.loader.resolve_manifests_dir(manifests_dir))

        if not force_reload_external and self.state.is_bootstrapped_for(resolved_dir):
            return self.registry.list_extension_infos()

        self.registry.clear_extensions_except({"zen70.core"})

        manifests = self.loader.load_from_dir(resolved_dir)
        self.validator.validate_external_manifests(
            manifests,
            registry=self.registry,
            force_reload_external=force_reload_external,
        )
        self.registry.register_external_manifests(manifests)

        self.state.mark_manifest_dir_bootstrapped(resolved_dir)
        return self.registry.list_extension_infos()
