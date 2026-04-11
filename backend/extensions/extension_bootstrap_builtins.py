"""Builtin extension bootstrap helpers."""

from __future__ import annotations

from backend.kernel.contracts.runtime_version import get_runtime_version

from .extension_bootstrap_state import ExtensionBootstrapState
from .extension_builtin_catalog import build_core_extension_manifest
from .extension_contracts import best_effort_semver
from .extension_registry import ExtensionRegistry


class ExtensionBuiltinBootstrap:
    def ensure_builtin_extensions_registered(
        self,
        registry: ExtensionRegistry,
        *,
        state: ExtensionBootstrapState,
    ) -> None:
        if state.builtins_registered:
            return

        runtime_version = best_effort_semver(get_runtime_version(), default="1.58.0")
        registry.register_manifest(build_core_extension_manifest(runtime_version), replace_existing=True)
        state.mark_builtins_registered()
