"""Kind registration write operations for extension manifests."""

from __future__ import annotations

from backend.extensions.connector_kind_registry import register_connector_kind, unregister_connector_kind
from backend.extensions.job_kind_registry import register_job_kind, unregister_job_kind

from .extension_contracts import ExtensionManifest, require_semver
from .extension_registry_projection import build_extension_kind_metadata


class ExtensionKindRegistryWriter:
    def register_manifest_kinds(self, manifest: ExtensionManifest) -> None:
        source = "core" if manifest.extension_id == "zen70.core" else "extension"
        for job_spec in manifest.job_kinds:
            register_job_kind(
                job_spec.kind,
                payload_schema=job_spec.payload_schema,
                result_schema=job_spec.result_schema,
                metadata=build_extension_kind_metadata(
                    manifest,
                    schema_version=require_semver(job_spec.schema_version, field_name=f"{job_spec.kind}.schema_version"),
                    stability=job_spec.stability,
                    description=job_spec.description,
                    source=source,
                ),
            )

        for connector_spec in manifest.connector_kinds:
            register_connector_kind(
                connector_spec.kind,
                config_schema=connector_spec.config_schema,
                metadata=build_extension_kind_metadata(
                    manifest,
                    schema_version=require_semver(connector_spec.schema_version, field_name=f"{connector_spec.kind}.schema_version"),
                    stability=connector_spec.stability,
                    description=connector_spec.description,
                    source=source,
                ),
            )

    def unregister_manifest_kinds(self, manifest: ExtensionManifest) -> None:
        for job_spec in manifest.job_kinds:
            unregister_job_kind(job_spec.kind)
        for connector_spec in manifest.connector_kinds:
            unregister_connector_kind(connector_spec.kind)
