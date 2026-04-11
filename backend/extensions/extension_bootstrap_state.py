"""Bootstrap runtime state for extension orchestration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ExtensionBootstrapState:
    builtins_registered: bool = False
    bootstrapped_manifest_dir: str | None = None

    def is_bootstrapped_for(self, manifest_dir: str) -> bool:
        return self.bootstrapped_manifest_dir == manifest_dir

    def mark_builtins_registered(self) -> None:
        self.builtins_registered = True

    def mark_manifest_dir_bootstrapped(self, manifest_dir: str) -> None:
        self.bootstrapped_manifest_dir = manifest_dir
