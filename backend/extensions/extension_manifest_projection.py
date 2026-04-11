"""Projection helpers from file manifest contracts to runtime contracts."""

from __future__ import annotations

from .extension_contracts import CompatibilityPolicy, ConnectorKindSpec, ExtensionManifest, JobKindSpec, WorkflowTemplateSpec
from .extension_manifest_file_contracts import (
    FileCompatibilityPolicy,
    FileConnectorKindSpec,
    FileExtensionManifest,
    FileJobKindSpec,
    FileWorkflowTemplateSpec,
)
from .extension_manifest_schema_refs import load_model_ref


def build_compatibility_policy(policy: FileCompatibilityPolicy) -> CompatibilityPolicy:
    return CompatibilityPolicy(
        min_kernel_version=policy.min_kernel_version,
        max_kernel_version=policy.max_kernel_version,
        supported_api_versions=tuple(policy.supported_api_versions),
        compatibility_mode=policy.compatibility_mode,
        notes=policy.notes,
    )


def build_job_kind_spec(spec: FileJobKindSpec) -> JobKindSpec:
    return JobKindSpec(
        kind=spec.kind,
        payload_schema=load_model_ref(spec.payload_schema_ref),
        result_schema=load_model_ref(spec.result_schema_ref),
        schema_version=spec.schema_version,
        stability=spec.stability,
        description=spec.description,
    )


def build_connector_kind_spec(spec: FileConnectorKindSpec) -> ConnectorKindSpec:
    return ConnectorKindSpec(
        kind=spec.kind,
        config_schema=load_model_ref(spec.config_schema_ref),
        schema_version=spec.schema_version,
        stability=spec.stability,
        description=spec.description,
    )


def build_workflow_template_spec(spec: FileWorkflowTemplateSpec) -> WorkflowTemplateSpec:
    return WorkflowTemplateSpec(
        template_id=spec.template_id,
        steps=tuple(dict(step) for step in spec.steps),
        parameters_schema=load_model_ref(spec.parameters_schema_ref),
        version=spec.version,
        schema_version=spec.schema_version,
        stability=spec.stability,
        display_name=spec.display_name,
        description=spec.description,
        labels=tuple(spec.labels),
        default_parameters=dict(spec.default_parameters),
    )


def project_file_manifest(file_manifest: FileExtensionManifest, *, source_manifest_path: str) -> ExtensionManifest:
    return ExtensionManifest(
        extension_id=file_manifest.extension_id,
        version=file_manifest.version,
        sdk_version=file_manifest.sdk_version,
        name=file_manifest.name,
        publisher=file_manifest.publisher,
        description=file_manifest.description,
        stability=file_manifest.stability,
        compatibility=build_compatibility_policy(file_manifest.compatibility),
        job_kinds=tuple(build_job_kind_spec(spec) for spec in file_manifest.job_kinds),
        connector_kinds=tuple(build_connector_kind_spec(spec) for spec in file_manifest.connector_kinds),
        workflow_templates=tuple(build_workflow_template_spec(spec) for spec in file_manifest.workflow_templates),
        source_manifest_path=source_manifest_path,
    )
