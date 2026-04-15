"""Manifest file contracts for external extension definitions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .extension_contracts import SDK_VERSION


class FileCompatibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_kernel_version: str = "1.0.0"
    max_kernel_version: str | None = None
    supported_api_versions: list[str] = Field(default_factory=lambda: ["v1"])
    compatibility_mode: str = "same-major"
    notes: str = ""


class FileJobKindSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    payload_schema_ref: str | None = None
    result_schema_ref: str | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


class FileConnectorKindSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    config_schema_ref: str | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


class FileWorkflowTemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    steps: list[dict[str, Any]]
    parameters_schema_ref: str | None = None
    version: str = "1.0.0"
    schema_version: str = "1.0.0"
    stability: str = "stable"
    display_name: str = ""
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    default_parameters: dict[str, Any] = Field(default_factory=dict)


class FileExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str
    version: str
    name: str
    publisher: str
    description: str
    sdk_version: str = SDK_VERSION
    stability: str = "stable"
    compatibility: FileCompatibilityPolicy = Field(default_factory=FileCompatibilityPolicy)
    job_kinds: list[FileJobKindSpec] = Field(default_factory=list)
    connector_kinds: list[FileConnectorKindSpec] = Field(default_factory=list)
    workflow_templates: list[FileWorkflowTemplateSpec] = Field(default_factory=list)
