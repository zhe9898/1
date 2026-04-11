"""Projection helpers for published extension metadata and discovery info."""

from __future__ import annotations

from typing import Any

from .extension_contracts import ExtensionManifest


def build_extension_kind_metadata(
    manifest: ExtensionManifest,
    *,
    schema_version: str,
    stability: str,
    description: str,
    source: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "extension_id": manifest.extension_id,
        "extension_name": manifest.name,
        "extension_version": manifest.version,
        "publisher": manifest.publisher,
        "sdk_version": manifest.sdk_version,
        "schema_version": schema_version,
        "stability": stability,
        "description": description,
        "compatibility": manifest.compatibility.to_dict(),
        "source_manifest_path": manifest.source_manifest_path,
    }


def build_extension_info(manifest: ExtensionManifest) -> dict[str, Any]:
    return {
        "extension_id": manifest.extension_id,
        "version": manifest.version,
        "sdk_version": manifest.sdk_version,
        "name": manifest.name,
        "publisher": manifest.publisher,
        "description": manifest.description,
        "stability": manifest.stability,
        "compatibility": manifest.compatibility.to_dict(),
        "job_kinds": [spec.kind for spec in manifest.job_kinds],
        "connector_kinds": [spec.kind for spec in manifest.connector_kinds],
        "workflow_templates": [spec.template_id for spec in manifest.workflow_templates],
        "source_manifest_path": manifest.source_manifest_path,
    }
