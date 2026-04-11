"""Workflow template registration write operations for extension manifests."""

from __future__ import annotations

from backend.extensions.workflow_template_registry import register_workflow_template, unregister_workflow_template

from .extension_contracts import ExtensionManifest, require_semver
from .extension_registry_projection import build_extension_kind_metadata


class ExtensionTemplateRegistryWriter:
    def register_manifest_templates(self, manifest: ExtensionManifest) -> None:
        source = "core" if manifest.extension_id == "zen70.core" else "extension"
        for spec in manifest.workflow_templates:
            register_workflow_template(
                spec.template_id,
                version=require_semver(spec.version, field_name=f"{spec.template_id}.version"),
                schema_version=require_semver(spec.schema_version, field_name=f"{spec.template_id}.schema_version"),
                sdk_version=manifest.sdk_version,
                display_name=spec.display_name or spec.template_id,
                description=spec.description,
                parameters_schema=spec.parameters_schema,
                default_parameters=spec.default_parameters,
                steps=list(spec.steps),
                labels=list(spec.labels),
                metadata=build_extension_kind_metadata(
                    manifest,
                    schema_version=spec.schema_version,
                    stability=spec.stability,
                    description=spec.description,
                    source=source,
                ),
            )

    def unregister_manifest_templates(self, manifest: ExtensionManifest) -> None:
        for template_spec in manifest.workflow_templates:
            unregister_workflow_template(template_spec.template_id)
