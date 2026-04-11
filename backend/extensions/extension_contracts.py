"""Extension SDK contracts and version helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

SDK_VERSION = "1.0.0"
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def require_semver(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _SEMVER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a semantic version (x.y.z), got '{value}'")
    return normalized


def best_effort_semver(value: str, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _SEMVER_RE.fullmatch(normalized) else default


@dataclass(frozen=True, slots=True)
class CompatibilityPolicy:
    min_kernel_version: str = "1.0.0"
    max_kernel_version: str | None = None
    supported_api_versions: tuple[str, ...] = ("v1",)
    compatibility_mode: str = "same-major"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_kernel_version": self.min_kernel_version,
            "max_kernel_version": self.max_kernel_version,
            "supported_api_versions": list(self.supported_api_versions),
            "compatibility_mode": self.compatibility_mode,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class JobKindSpec:
    kind: str
    payload_schema: type[BaseModel] | None = None
    result_schema: type[BaseModel] | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


@dataclass(frozen=True, slots=True)
class ConnectorKindSpec:
    kind: str
    config_schema: type[BaseModel] | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowTemplateSpec:
    template_id: str
    steps: tuple[dict[str, Any], ...]
    parameters_schema: type[BaseModel] | None = None
    version: str = "1.0.0"
    schema_version: str = "1.0.0"
    stability: str = "stable"
    display_name: str = ""
    description: str = ""
    labels: tuple[str, ...] = ()
    default_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    extension_id: str
    version: str
    name: str
    publisher: str
    description: str
    sdk_version: str = SDK_VERSION
    stability: str = "stable"
    compatibility: CompatibilityPolicy = field(default_factory=CompatibilityPolicy)
    job_kinds: tuple[JobKindSpec, ...] = ()
    connector_kinds: tuple[ConnectorKindSpec, ...] = ()
    workflow_templates: tuple[WorkflowTemplateSpec, ...] = ()
    source_manifest_path: str | None = None


def validate_extension_manifest_versions(manifest: ExtensionManifest) -> None:
    require_semver(manifest.version, field_name="manifest.version")
    require_semver(manifest.sdk_version, field_name="manifest.sdk_version")
    require_semver(manifest.compatibility.min_kernel_version, field_name="compatibility.min_kernel_version")
    if manifest.compatibility.max_kernel_version:
        require_semver(manifest.compatibility.max_kernel_version, field_name="compatibility.max_kernel_version")
