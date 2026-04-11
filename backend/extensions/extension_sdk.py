"""Public facade for the extension SDK."""

from __future__ import annotations

from pathlib import Path

from backend.extensions.connector_kind_registry import get_connector_kind_info, list_connector_kinds
from backend.extensions.job_kind_registry import get_job_kind_info, list_job_kinds
from backend.extensions.workflow_template_registry import get_workflow_template_info, list_workflow_templates

from .extension_bootstrap import ExtensionBootstrapper
from .extension_contracts import (
    SDK_VERSION,
    CompatibilityPolicy,
    ConnectorKindSpec,
    ExtensionManifest,
    JobKindSpec,
    WorkflowTemplateSpec,
)
from .extension_manifest_loader import DEFAULT_EXTENSION_MANIFESTS_DIR, ExtensionManifestLoader
from .extension_registry import ExtensionRegistry

_MANIFEST_LOADER = ExtensionManifestLoader()
_REGISTRY = ExtensionRegistry()
_BOOTSTRAPPER = ExtensionBootstrapper(loader=_MANIFEST_LOADER, registry=_REGISTRY)


def register_extension_manifest(manifest: ExtensionManifest, *, replace_existing: bool = False) -> None:
    _REGISTRY.register_manifest(manifest, replace_existing=replace_existing)


def load_extension_manifests_from_dir(manifests_dir: str | Path | None = None) -> list[ExtensionManifest]:
    return _MANIFEST_LOADER.load_from_dir(manifests_dir)


def bootstrap_extension_runtime(
    manifests_dir: str | Path | None = None,
    *,
    force_reload_external: bool = False,
) -> tuple[dict[str, object], ...]:
    return _BOOTSTRAPPER.bootstrap_runtime(manifests_dir, force_reload_external=force_reload_external)


def list_extensions() -> list[dict[str, object]]:
    bootstrap_extension_runtime()
    return list(_REGISTRY.list_extension_infos())


def get_extension_info(extension_id: str) -> dict[str, object]:
    return _REGISTRY.get_extension_info(extension_id)


def get_published_job_kind(kind: str) -> dict[str, object]:
    bootstrap_extension_runtime()
    return get_job_kind_info(kind)


def list_published_job_kinds() -> list[dict[str, object]]:
    bootstrap_extension_runtime()
    return list_job_kinds()


def get_published_connector_kind(kind: str) -> dict[str, object]:
    bootstrap_extension_runtime()
    return get_connector_kind_info(kind)


def list_published_connector_kinds() -> list[dict[str, object]]:
    bootstrap_extension_runtime()
    return list_connector_kinds()


def get_published_workflow_template(template_id: str) -> dict[str, object]:
    bootstrap_extension_runtime()
    return get_workflow_template_info(template_id)


def list_published_workflow_templates() -> list[dict[str, object]]:
    bootstrap_extension_runtime()
    return list_workflow_templates()


__all__ = [
    "CompatibilityPolicy",
    "ConnectorKindSpec",
    "DEFAULT_EXTENSION_MANIFESTS_DIR",
    "ExtensionManifest",
    "JobKindSpec",
    "SDK_VERSION",
    "WorkflowTemplateSpec",
    "bootstrap_extension_runtime",
    "get_extension_info",
    "get_published_connector_kind",
    "get_published_job_kind",
    "get_published_workflow_template",
    "list_extensions",
    "list_published_connector_kinds",
    "list_published_job_kinds",
    "list_published_workflow_templates",
    "load_extension_manifests_from_dir",
    "register_extension_manifest",
]
