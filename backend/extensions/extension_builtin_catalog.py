"""Builtin core extension manifest assembler."""

from __future__ import annotations

from .extension_builtin_kinds import build_core_connector_kinds, build_core_job_kinds
from .extension_builtin_templates import build_core_workflow_templates
from .extension_contracts import SDK_VERSION, CompatibilityPolicy, ExtensionManifest


def build_core_extension_manifest(runtime_version: str) -> ExtensionManifest:
    return ExtensionManifest(
        extension_id="zen70.core",
        version=runtime_version,
        sdk_version=SDK_VERSION,
        name="ZEN70 Core Extension Pack",
        publisher="ZEN70",
        description="Built-in control-plane job kinds, connector kinds, and reusable workflow templates.",
        compatibility=CompatibilityPolicy(
            min_kernel_version=runtime_version,
            supported_api_versions=("v1",),
            compatibility_mode="same-major",
            notes="Built-in contracts are semver-protected within the active major version.",
        ),
        job_kinds=build_core_job_kinds(),
        connector_kinds=build_core_connector_kinds(),
        workflow_templates=build_core_workflow_templates(),
    )
