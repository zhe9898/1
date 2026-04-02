"""External extension SDK and runtime bootstrap.

This module formalises how external teams publish new job kinds, connector
kinds, and workflow templates without editing the kernel registries directly.

Quality bar for this layer:
1. Explicit bootstrap instead of hidden import-time side effects
2. Versioned manifests with compatibility policy
3. Published schema metadata for discovery APIs
4. File-based external manifest loading from ``contracts/extensions``
5. Strict parsing and source-path traceability
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.core.connector_kind_registry import (
    HttpConnectorConfig,
    MqttConnectorConfig,
    WebhookConnectorConfig,
    get_connector_kind_info,
    list_connector_kinds,
    register_connector_kind,
    unregister_connector_kind,
)
from backend.core.job_kind_registry import (
    ContainerRunPayload,
    ContainerRunResult,
    CronTickPayload,
    CronTickResult,
    DataSyncPayload,
    DataSyncResult,
    FileTransferPayload,
    FileTransferResult,
    HealthcheckPayload,
    HealthcheckResult,
    HttpRequestPayload,
    HttpRequestResult,
    MediaTranscodePayload,
    MediaTranscodeResult,
    MLInferencePayload,
    MLInferenceResult,
    ScriptRunPayload,
    ScriptRunResult,
    ShellExecPayload,
    ShellExecResult,
    WasmRunPayload,
    WasmRunResult,
    get_job_kind_info,
    list_job_kinds,
    register_job_kind,
    unregister_job_kind,
)
from backend.core.version import get_runtime_version
from backend.core.workflow_template_registry import (
    get_workflow_template_info,
    list_workflow_templates,
    register_workflow_template,
    unregister_workflow_template,
)

SDK_VERSION = "1.0.0"
DEFAULT_EXTENSION_MANIFESTS_DIR = Path("contracts/extensions")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def _require_semver(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _SEMVER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a semantic version (x.y.z), got '{value}'")
    return normalized


def _best_effort_semver(value: str, *, default: str) -> str:
    normalized = str(value or "").strip()
    return normalized if _SEMVER_RE.fullmatch(normalized) else default


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def _load_model_ref(ref: str | None) -> type[BaseModel] | None:
    normalized = str(ref or "").strip()
    if not normalized:
        return None
    if ":" not in normalized:
        raise ValueError(f"Schema ref '{normalized}' must use module.path:ClassName format")
    module_name, attr_name = normalized.split(":", 1)
    module = importlib.import_module(module_name)
    resolved = getattr(module, attr_name, None)
    if resolved is None:
        raise ValueError(f"Schema ref '{normalized}' could not be resolved")
    if not isinstance(resolved, type) or not issubclass(resolved, BaseModel):
        raise ValueError(f"Schema ref '{normalized}' must resolve to a Pydantic BaseModel subclass")
    return resolved


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


class _FileCompatibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_kernel_version: str = "1.0.0"
    max_kernel_version: str | None = None
    supported_api_versions: list[str] = Field(default_factory=lambda: ["v1"])
    compatibility_mode: str = "same-major"
    notes: str = ""


class _FileJobKindSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    payload_schema_ref: str | None = None
    result_schema_ref: str | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


class _FileConnectorKindSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    config_schema_ref: str | None = None
    schema_version: str = "1.0.0"
    stability: str = "stable"
    description: str = ""


class _FileWorkflowTemplateSpec(BaseModel):
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


class _FileExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str
    version: str
    name: str
    publisher: str
    description: str
    sdk_version: str = SDK_VERSION
    stability: str = "stable"
    compatibility: _FileCompatibilityPolicy = Field(default_factory=_FileCompatibilityPolicy)
    job_kinds: list[_FileJobKindSpec] = Field(default_factory=list)
    connector_kinds: list[_FileConnectorKindSpec] = Field(default_factory=list)
    workflow_templates: list[_FileWorkflowTemplateSpec] = Field(default_factory=list)


_EXTENSION_REGISTRY: dict[str, ExtensionManifest] = {}
_BUILTINS_REGISTERED = False
_BOOTSTRAPPED_MANIFEST_DIR: str | None = None


def _kind_metadata(
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


def _validate_manifest_versions(manifest: ExtensionManifest) -> None:
    _require_semver(manifest.version, field_name="manifest.version")
    _require_semver(manifest.sdk_version, field_name="manifest.sdk_version")
    _require_semver(manifest.compatibility.min_kernel_version, field_name="compatibility.min_kernel_version")
    if manifest.compatibility.max_kernel_version:
        _require_semver(manifest.compatibility.max_kernel_version, field_name="compatibility.max_kernel_version")


def _register_manifest_kinds(manifest: ExtensionManifest) -> None:
    for job_spec in manifest.job_kinds:
        register_job_kind(
            job_spec.kind,
            payload_schema=job_spec.payload_schema,
            result_schema=job_spec.result_schema,
            metadata=_kind_metadata(
                manifest,
                schema_version=_require_semver(job_spec.schema_version, field_name=f"{job_spec.kind}.schema_version"),
                stability=job_spec.stability,
                description=job_spec.description,
                source="core" if manifest.extension_id == "zen70.core" else "extension",
            ),
        )

    for connector_spec in manifest.connector_kinds:
        register_connector_kind(
            connector_spec.kind,
            config_schema=connector_spec.config_schema,
            metadata=_kind_metadata(
                manifest,
                schema_version=_require_semver(
                    connector_spec.schema_version,
                    field_name=f"{connector_spec.kind}.schema_version",
                ),
                stability=connector_spec.stability,
                description=connector_spec.description,
                source="core" if manifest.extension_id == "zen70.core" else "extension",
            ),
        )


def _register_manifest_templates(manifest: ExtensionManifest) -> None:
    for spec in manifest.workflow_templates:
        register_workflow_template(
            spec.template_id,
            version=_require_semver(spec.version, field_name=f"{spec.template_id}.version"),
            schema_version=_require_semver(spec.schema_version, field_name=f"{spec.template_id}.schema_version"),
            sdk_version=manifest.sdk_version,
            display_name=spec.display_name or spec.template_id,
            description=spec.description,
            parameters_schema=spec.parameters_schema,
            default_parameters=spec.default_parameters,
            steps=list(spec.steps),
            labels=list(spec.labels),
            metadata=_kind_metadata(
                manifest,
                schema_version=spec.schema_version,
                stability=spec.stability,
                description=spec.description,
                source="core" if manifest.extension_id == "zen70.core" else "extension",
            ),
        )


def _unregister_manifest(manifest: ExtensionManifest) -> None:
    for template_spec in manifest.workflow_templates:
        unregister_workflow_template(template_spec.template_id)
    for job_spec in manifest.job_kinds:
        unregister_job_kind(job_spec.kind)
    for connector_spec in manifest.connector_kinds:
        unregister_connector_kind(connector_spec.kind)


def register_extension_manifest(manifest: ExtensionManifest, *, replace_existing: bool = False) -> None:
    extension_id = str(manifest.extension_id or "").strip()
    if not extension_id:
        raise ValueError("extension_id is required")
    existing_manifest = _EXTENSION_REGISTRY.get(extension_id)
    if existing_manifest is not None and not replace_existing:
        raise ValueError(f"Extension '{extension_id}' is already registered")

    _validate_manifest_versions(manifest)
    if existing_manifest is not None:
        _unregister_manifest(existing_manifest)
    _register_manifest_kinds(manifest)
    _register_manifest_templates(manifest)
    _EXTENSION_REGISTRY[extension_id] = manifest


def _parse_file_manifest(payload: dict[str, Any], *, source_manifest_path: str) -> ExtensionManifest:
    file_manifest = _FileExtensionManifest.model_validate(payload)
    return ExtensionManifest(
        extension_id=file_manifest.extension_id,
        version=file_manifest.version,
        sdk_version=file_manifest.sdk_version,
        name=file_manifest.name,
        publisher=file_manifest.publisher,
        description=file_manifest.description,
        stability=file_manifest.stability,
        compatibility=CompatibilityPolicy(
            min_kernel_version=file_manifest.compatibility.min_kernel_version,
            max_kernel_version=file_manifest.compatibility.max_kernel_version,
            supported_api_versions=tuple(file_manifest.compatibility.supported_api_versions),
            compatibility_mode=file_manifest.compatibility.compatibility_mode,
            notes=file_manifest.compatibility.notes,
        ),
        job_kinds=tuple(
            JobKindSpec(
                kind=spec.kind,
                payload_schema=_load_model_ref(spec.payload_schema_ref),
                result_schema=_load_model_ref(spec.result_schema_ref),
                schema_version=spec.schema_version,
                stability=spec.stability,
                description=spec.description,
            )
            for spec in file_manifest.job_kinds
        ),
        connector_kinds=tuple(
            ConnectorKindSpec(
                kind=spec.kind,
                config_schema=_load_model_ref(spec.config_schema_ref),
                schema_version=spec.schema_version,
                stability=spec.stability,
                description=spec.description,
            )
            for spec in file_manifest.connector_kinds
        ),
        workflow_templates=tuple(
            WorkflowTemplateSpec(
                template_id=spec.template_id,
                steps=tuple(dict(step) for step in spec.steps),
                parameters_schema=_load_model_ref(spec.parameters_schema_ref),
                version=spec.version,
                schema_version=spec.schema_version,
                stability=spec.stability,
                display_name=spec.display_name,
                description=spec.description,
                labels=tuple(spec.labels),
                default_parameters=dict(spec.default_parameters),
            )
            for spec in file_manifest.workflow_templates
        ),
        source_manifest_path=source_manifest_path,
    )


def _is_manifest_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".") or ".example." in path.name:
        return False
    return path.suffix.lower() in {".json", ".yaml", ".yml"}


def _read_manifest_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        import json

        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError(f"Extension manifest '{path}' must decode to an object")
    return payload


def _resolve_manifests_dir(manifests_dir: str | Path | None = None) -> Path:
    raw = manifests_dir or os.getenv("ZEN70_EXTENSION_MANIFESTS_DIR") or DEFAULT_EXTENSION_MANIFESTS_DIR
    return Path(raw).resolve()


def load_extension_manifests_from_dir(manifests_dir: str | Path | None = None) -> list[ExtensionManifest]:
    directory = _resolve_manifests_dir(manifests_dir)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError(f"Extension manifests path '{directory}' must be a directory")

    manifests: list[ExtensionManifest] = []
    seen_extension_ids: dict[str, Path] = {}

    for path in sorted(candidate for candidate in directory.rglob("*") if _is_manifest_candidate(candidate)):
        manifest = _parse_file_manifest(_read_manifest_file(path), source_manifest_path=str(path))
        previous = seen_extension_ids.get(manifest.extension_id)
        if previous is not None:
            raise ValueError(f"Duplicate extension_id '{manifest.extension_id}' in '{previous}' and '{path}'")
        seen_extension_ids[manifest.extension_id] = path
        manifests.append(manifest)

    return manifests


def bootstrap_extension_runtime(
    manifests_dir: str | Path | None = None,
    *,
    force_reload_external: bool = False,
) -> tuple[dict[str, Any], ...]:
    global _BOOTSTRAPPED_MANIFEST_DIR

    ensure_builtin_extensions_registered()
    resolved_dir = str(_resolve_manifests_dir(manifests_dir))

    if not force_reload_external and _BOOTSTRAPPED_MANIFEST_DIR == resolved_dir:
        return tuple(get_extension_info(extension_id) for extension_id in sorted(_EXTENSION_REGISTRY.keys()))

    for extension_id, manifest in list(_EXTENSION_REGISTRY.items()):
        if extension_id == "zen70.core":
            continue
        _unregister_manifest(manifest)
        _EXTENSION_REGISTRY.pop(extension_id, None)

    manifests = load_extension_manifests_from_dir(resolved_dir)
    for manifest in manifests:
        if manifest.extension_id == "zen70.core":
            raise ValueError("External manifests must not reuse reserved extension_id 'zen70.core'")

    for manifest in manifests:
        _validate_manifest_versions(manifest)
        if manifest.extension_id in _EXTENSION_REGISTRY and not force_reload_external:
            raise ValueError(f"Extension '{manifest.extension_id}' is already registered")

    for manifest in manifests:
        _register_manifest_kinds(manifest)

    for manifest in manifests:
        _register_manifest_templates(manifest)
        _EXTENSION_REGISTRY[manifest.extension_id] = manifest

    _BOOTSTRAPPED_MANIFEST_DIR = resolved_dir
    return tuple(get_extension_info(extension_id) for extension_id in sorted(_EXTENSION_REGISTRY.keys()))


def list_extensions() -> list[dict[str, Any]]:
    bootstrap_extension_runtime()
    return [get_extension_info(extension_id) for extension_id in sorted(_EXTENSION_REGISTRY.keys())]


def get_extension_info(extension_id: str) -> dict[str, Any]:
    manifest = _EXTENSION_REGISTRY.get(extension_id)
    if manifest is None:
        raise ValueError(f"Extension '{extension_id}' is not registered")
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


class HttpHealthcheckTemplateParams(BaseModel):
    target: str
    expected_status: int = 200
    timeout: int = 10


class FileTransferTemplateParams(BaseModel):
    src: str
    dst: str
    overwrite: bool = False
    mkdir: bool = True
    verify_sha256: str | None = None


def ensure_builtin_extensions_registered() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return

    runtime_version = _best_effort_semver(get_runtime_version(), default="1.58.0")
    core_manifest = ExtensionManifest(
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
        job_kinds=(
            JobKindSpec(
                "shell.exec",
                payload_schema=ShellExecPayload,
                result_schema=ShellExecResult,
                description="Execute a shell command on a worker node.",
            ),
            JobKindSpec("http.request", payload_schema=HttpRequestPayload, result_schema=HttpRequestResult, description="Execute an HTTP request."),
            JobKindSpec("container.run", payload_schema=ContainerRunPayload, result_schema=ContainerRunResult, description="Run a container image."),
            JobKindSpec("healthcheck", payload_schema=HealthcheckPayload, result_schema=HealthcheckResult, description="Run a health probe."),
            JobKindSpec("ml.inference", payload_schema=MLInferencePayload, result_schema=MLInferenceResult, description="Run ML inference."),
            JobKindSpec(
                "media.transcode",
                payload_schema=MediaTranscodePayload,
                result_schema=MediaTranscodeResult,
                description="Run media transcode workloads.",
            ),
            JobKindSpec("script.run", payload_schema=ScriptRunPayload, result_schema=ScriptRunResult, description="Run an interpreted script."),
            JobKindSpec("wasm.run", payload_schema=WasmRunPayload, result_schema=WasmRunResult, stability="beta", description="Run WebAssembly workloads."),
            JobKindSpec("cron.tick", payload_schema=CronTickPayload, result_schema=CronTickResult, description="Execute a scheduled cron trigger."),
            JobKindSpec("data.sync", payload_schema=DataSyncPayload, result_schema=DataSyncResult, description="Synchronise data across boundaries."),
            JobKindSpec(
                "file.transfer",
                payload_schema=FileTransferPayload,
                result_schema=FileTransferResult,
                description="Transfer files with integrity checks.",
            ),
            JobKindSpec("connector.invoke", description="Control-plane connector invocation kind kept permissive for compatibility."),
        ),
        connector_kinds=(
            ConnectorKindSpec("http", config_schema=HttpConnectorConfig, description="HTTP connector runtime."),
            ConnectorKindSpec("mqtt", config_schema=MqttConnectorConfig, description="MQTT connector runtime."),
            ConnectorKindSpec("webhook", config_schema=WebhookConnectorConfig, description="Webhook connector runtime."),
        ),
        workflow_templates=(
            WorkflowTemplateSpec(
                template_id="ops.http-healthcheck",
                version="1.0.0",
                schema_version="1.0.0",
                display_name="HTTP Healthcheck",
                description="Probe an HTTP endpoint through the workflow engine.",
                parameters_schema=HttpHealthcheckTemplateParams,
                labels=("ops", "healthcheck"),
                steps=(
                    {
                        "id": "probe",
                        "kind": "healthcheck",
                        "payload": {
                            "target": "${target}",
                            "check_type": "http",
                            "expected_status": "${expected_status}",
                            "timeout": "${timeout}",
                        },
                    },
                ),
            ),
            WorkflowTemplateSpec(
                template_id="edge.file-transfer",
                version="1.0.0",
                schema_version="1.0.0",
                display_name="File Transfer",
                description="Copy a file through the workflow engine with optional checksum verification.",
                parameters_schema=FileTransferTemplateParams,
                labels=("edge", "transfer"),
                steps=(
                    {
                        "id": "transfer",
                        "kind": "file.transfer",
                        "payload": {
                            "src": "${src}",
                            "dst": "${dst}",
                            "overwrite": "${overwrite}",
                            "mkdir": "${mkdir}",
                            "verify_sha256": "${verify_sha256}",
                        },
                    },
                ),
            ),
        ),
    )
    register_extension_manifest(core_manifest, replace_existing=True)
    _BUILTINS_REGISTERED = True


def get_published_job_kind(kind: str) -> dict[str, Any]:
    bootstrap_extension_runtime()
    return get_job_kind_info(kind)


def list_published_job_kinds() -> list[dict[str, Any]]:
    bootstrap_extension_runtime()
    return list_job_kinds()


def get_published_connector_kind(kind: str) -> dict[str, Any]:
    bootstrap_extension_runtime()
    return get_connector_kind_info(kind)


def list_published_connector_kinds() -> list[dict[str, Any]]:
    bootstrap_extension_runtime()
    return list_connector_kinds()


def get_published_workflow_template(template_id: str) -> dict[str, Any]:
    bootstrap_extension_runtime()
    return get_workflow_template_info(template_id)


def list_published_workflow_templates() -> list[dict[str, Any]]:
    bootstrap_extension_runtime()
    return list_workflow_templates()
