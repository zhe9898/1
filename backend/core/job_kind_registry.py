"""Job kind registry and payload schema validation.

Provides schema registration and validation for job payloads, plus shared
submission policy metadata so privileged runner kinds can be gated in one
place instead of per-endpoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.core.alert_actions import normalize_alert_action
from backend.core.errors import zen
from backend.core.security_policy import normalize_local_filesystem_path as _shared_normalize_local_filesystem_path
from backend.core.security_policy import normalize_managed_uri as _shared_normalize_managed_uri
from backend.core.security_policy import normalize_nonempty_string as _shared_normalize_nonempty_string
from backend.core.security_policy import normalize_public_network_url as _shared_normalize_public_network_url

# ============================================================================
# Job Kind Registry
# ============================================================================

_JOB_KIND_REGISTRY: dict[str, type[BaseModel]] = {}
_JOB_RESULT_REGISTRY: dict[str, type[BaseModel]] = {}
_JOB_KIND_METADATA_REGISTRY: dict[str, dict[str, Any]] = {}

JOB_WRITE_SCOPE = "write:jobs"
JOB_ADMIN_SCOPE = "admin:jobs"
_ADMIN_ROLES = frozenset({"admin", "superadmin"})
_SAFE_JOB_KINDS = frozenset({"noop"})


def _job_metadata(*, requires_admin: bool, risk: str) -> dict[str, Any]:
    return {
        "source": "core",
        "requires_admin": requires_admin,
        "required_scope": JOB_ADMIN_SCOPE if requires_admin else JOB_WRITE_SCOPE,
        "risk": risk,
    }


def _has_admin_role(current_user: Mapping[str, object]) -> bool:
    return str(current_user.get("role") or "").strip().lower() in _ADMIN_ROLES


def _normalized_scopes(current_user: Mapping[str, object]) -> set[str]:
    scopes = current_user.get("scopes", [])
    if not isinstance(scopes, list):
        return set()
    return {str(scope).strip().lower() for scope in scopes if isinstance(scope, str) and scope.strip()}


def get_job_submission_policy(kind: str) -> dict[str, Any]:
    metadata = dict(_JOB_KIND_METADATA_REGISTRY.get(kind, {}))
    requires_admin = bool(metadata.get("requires_admin", kind not in _SAFE_JOB_KINDS))
    metadata.setdefault("source", "core")
    metadata["requires_admin"] = requires_admin
    metadata.setdefault("required_scope", JOB_ADMIN_SCOPE if requires_admin else JOB_WRITE_SCOPE)
    metadata.setdefault("risk", "remote-execution" if requires_admin else "safe")
    return metadata


def assert_job_submission_authorized(kind: str, current_user: Mapping[str, object]) -> None:
    policy = get_job_submission_policy(kind)
    required_scope = str(policy["required_scope"]).strip().lower()
    if policy["requires_admin"] and not _has_admin_role(current_user):
        raise zen(
            "ZEN-JOB-4031",
            f"Job kind '{kind}' requires admin privileges",
            status_code=403,
            recovery_hint="Use an admin account for privileged runner job kinds",
            details={"kind": kind, "required_scope": required_scope, "risk": policy.get("risk")},
        )
    if _has_admin_role(current_user):
        return
    if required_scope not in _normalized_scopes(current_user):
        raise zen(
            "ZEN-JOB-4032",
            f"Missing required permission for job kind '{kind}'",
            status_code=403,
            recovery_hint=f"Request the {required_scope} permission or use an admin account",
            details={"kind": kind, "required_scope": required_scope, "risk": policy.get("risk")},
        )


def register_job_kind(
    kind: str,
    *,
    payload_schema: type[BaseModel] | None = None,
    result_schema: type[BaseModel] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if payload_schema is not None:
        _JOB_KIND_REGISTRY[kind] = payload_schema
    if result_schema is not None:
        _JOB_RESULT_REGISTRY[kind] = result_schema
    if metadata is not None:
        _JOB_KIND_METADATA_REGISTRY[kind] = dict(metadata)
    else:
        _JOB_KIND_METADATA_REGISTRY.setdefault(kind, {"source": "core"})


def unregister_job_kind(kind: str) -> None:
    _JOB_KIND_REGISTRY.pop(kind, None)
    _JOB_RESULT_REGISTRY.pop(kind, None)
    _JOB_KIND_METADATA_REGISTRY.pop(kind, None)


def is_job_kind_registered(kind: str) -> bool:
    return kind in _JOB_KIND_REGISTRY or kind in _JOB_RESULT_REGISTRY or kind in _JOB_KIND_METADATA_REGISTRY


def get_registered_job_kinds() -> list[str]:
    all_kinds = set(_JOB_KIND_REGISTRY) | set(_JOB_RESULT_REGISTRY) | set(_JOB_KIND_METADATA_REGISTRY)
    return sorted(all_kinds)


def validate_job_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    schema = _JOB_KIND_REGISTRY.get(kind)
    if schema is None:
        return payload
    try:
        validated = schema(**payload)
        return validated.model_dump(mode="python")
    except ValidationError as exc:
        error_details = exc.errors()
        raise ValueError(f"Job payload validation failed for kind '{kind}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}") from exc


def validate_job_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    schema = _JOB_RESULT_REGISTRY.get(kind)
    if schema is None:
        return result
    try:
        validated = schema(**result)
        return validated.model_dump(mode="python")
    except ValidationError as exc:
        error_details = exc.errors()
        raise ValueError(f"Job result validation failed for kind '{kind}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}") from exc


def _normalize_string(value: str, *, field_name: str) -> str:
    return _shared_normalize_nonempty_string(value, field_name=field_name)


def _normalize_local_filesystem_path(value: str, *, field_name: str) -> str:
    return _shared_normalize_local_filesystem_path(value, field_name=field_name)


def _normalize_public_network_url(value: str, *, field_name: str, allowed_schemes: set[str]) -> str:
    return _shared_normalize_public_network_url(
        value,
        field_name=field_name,
        allowed_schemes=allowed_schemes,
    )


def _normalize_managed_uri(
    value: str,
    *,
    field_name: str,
    allowed_schemes: set[str],
    allow_public_http: bool = False,
    require_suffix: str | None = None,
) -> str:
    return _shared_normalize_managed_uri(
        value,
        field_name=field_name,
        allowed_schemes=allowed_schemes,
        allow_public_http=allow_public_http,
        require_suffix=require_suffix,
    )


# ============================================================================
# Built-in Job Kinds
# ============================================================================


class NoopPayload(BaseModel):
    delay_ms: float = 0.0


class ShellExecPayload(BaseModel):
    command: str = Field(..., min_length=1)
    timeout: int = 300
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None


class ShellExecResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class HttpRequestPayload(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    timeout: int = 30

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        return _normalize_public_network_url(value, field_name="url", allowed_schemes={"http", "https"})

    @field_validator("method")
    @classmethod
    def _normalize_method(cls, value: str) -> str:
        return _normalize_string(value, field_name="method").upper()


class HttpRequestResult(BaseModel):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    duration_seconds: float = 0.0


class ConnectorInvokePayload(BaseModel):
    connector_id: str = Field(..., min_length=1)
    connector_kind: str | None = None
    action: str = Field(..., min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    endpoint: str | None = None

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_public_network_url(value, field_name="endpoint", allowed_schemes={"http", "https"})


class DockerExecPayload(BaseModel):
    container: str = Field(..., min_length=1)
    command: str | list[str]
    work_dir: str | None = None


class CronTriggerPayload(BaseModel):
    webhook_url: str
    cron_name: str | None = None
    body: Any | None = None

    @field_validator("webhook_url")
    @classmethod
    def _validate_webhook_url(cls, value: str) -> str:
        return _normalize_public_network_url(value, field_name="webhook_url", allowed_schemes={"http", "https"})


class ContainerRunPayload(BaseModel):
    image: str = Field(..., min_length=1)
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None
    timeout: int = 600
    pull_policy: str = "IfNotPresent"
    memory_limit_mb: int | None = None
    cpu_limit_millicores: int | None = None


class ContainerRunResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    container_id: str = ""
    duration_seconds: float = 0.0


class HealthcheckPayload(BaseModel):
    target: str = Field(..., min_length=1)
    check_type: str = "http"
    timeout: int = 10
    expected_status: int = 200
    interval_seconds: int = 0


class HealthcheckResult(BaseModel):
    healthy: bool
    latency_ms: float = 0.0
    status_code: int | None = None
    message: str = ""


class MLInferencePayload(BaseModel):
    model_id: str = Field(..., min_length=1)
    input_data: dict[str, Any] = Field(default_factory=dict)
    input_uri: str | None = None
    runtime: str = "onnx"
    batch_size: int = 1
    timeout: int = 120
    precision: str = "fp32"

    @field_validator("input_uri")
    @classmethod
    def _validate_input_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_managed_uri(
            value,
            field_name="input_uri",
            allowed_schemes={"https", "s3"},
            allow_public_http=True,
        )


class MLInferenceResult(BaseModel):
    predictions: list[Any] = Field(default_factory=list)
    output_uri: str | None = None
    inference_time_ms: float = 0.0
    model_version: str = ""


class MediaTranscodePayload(BaseModel):
    input_uri: str
    output_uri: str
    codec: str = "h264"
    resolution: str | None = None
    bitrate_kbps: int | None = None
    timeout: int = 1800
    hardware_accel: bool = False

    @field_validator("input_uri", "output_uri")
    @classmethod
    def _validate_media_uri(cls, value: str, info: Any) -> str:
        return _normalize_managed_uri(
            value,
            field_name=str(info.field_name),
            allowed_schemes={"https", "s3"},
            allow_public_http=True,
        )


class MediaTranscodeResult(BaseModel):
    output_uri: str = ""
    duration_seconds: float = 0.0
    output_size_bytes: int = 0
    codec_used: str = ""


class ScriptRunPayload(BaseModel):
    interpreter: Literal["bash", "python", "node", "powershell"] = "bash"
    script: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None
    timeout: int = 300

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_command(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "script" not in data and isinstance(data.get("command"), str):
            patched = dict(data)
            patched["script"] = data["command"]
            if "working_dir" not in patched and isinstance(data.get("work_dir"), str):
                patched["working_dir"] = data["work_dir"]
            return patched
        return data


class ScriptRunResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class WasmRunPayload(BaseModel):
    module_uri: str
    function: str = "_start"
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: int = 60
    memory_pages: int = 256

    @field_validator("module_uri")
    @classmethod
    def _validate_module_uri(cls, value: str) -> str:
        return _normalize_managed_uri(
            value,
            field_name="module_uri",
            allowed_schemes={"https"},
            allow_public_http=True,
            require_suffix=".wasm",
        )


class WasmRunResult(BaseModel):
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class CronTickPayload(BaseModel):
    schedule_id: str = Field(..., min_length=1)
    cron_expression: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    action_payload: dict[str, Any] = Field(default_factory=dict)
    timeout: int = 120


class CronTickResult(BaseModel):
    triggered: bool = True
    action_result: dict[str, Any] = Field(default_factory=dict)
    next_fire_at: str | None = None


class DataSyncPayload(BaseModel):
    source_uri: str
    dest_uri: str
    direction: str = "push"
    filters: list[str] = Field(default_factory=list)
    conflict_resolution: str = "latest-wins"
    timeout: int = 600
    bandwidth_limit_kbps: int | None = None

    @field_validator("source_uri", "dest_uri")
    @classmethod
    def _validate_sync_uri(cls, value: str, info: Any) -> str:
        return _normalize_managed_uri(
            value,
            field_name=str(info.field_name),
            allowed_schemes={"rsync"},
        )


class DataSyncResult(BaseModel):
    files_transferred: int = 0
    bytes_transferred: int = 0
    conflicts: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


class FileTransferPayload(BaseModel):
    src: str
    dst: str
    overwrite: bool = False
    verify_sha256: str | None = None
    mkdir: bool = True

    @field_validator("src", "dst")
    @classmethod
    def _validate_transfer_path(cls, value: str, info: Any) -> str:
        return _normalize_local_filesystem_path(value, field_name=str(info.field_name))


class FileTransferResult(BaseModel):
    src: str = ""
    dst: str = ""
    bytes: int = 0
    sha256: str = ""
    duration_ms: float = 0.0
    throughput_mbs: float = 0.0


class AlertNotifyPayload(BaseModel):
    alert_id: int
    rule_name: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any]
    triggered_at: str = Field(..., min_length=1)

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: dict[str, Any]) -> dict[str, Any]:
        return normalize_alert_action(value)


class AlertNotifyResult(BaseModel):
    delivered: bool
    status_code: int | None = None
    body: str = ""


register_job_kind(
    "noop",
    payload_schema=NoopPayload,
    metadata=_job_metadata(requires_admin=False, risk="safe"),
)
register_job_kind(
    "shell.exec",
    payload_schema=ShellExecPayload,
    result_schema=ShellExecResult,
    metadata=_job_metadata(requires_admin=True, risk="remote-execution"),
)
register_job_kind(
    "http.request",
    payload_schema=HttpRequestPayload,
    result_schema=HttpRequestResult,
    metadata=_job_metadata(requires_admin=True, risk="network-egress"),
)
register_job_kind(
    "connector.invoke",
    payload_schema=ConnectorInvokePayload,
    metadata=_job_metadata(requires_admin=True, risk="network-egress"),
)
register_job_kind(
    "docker.exec",
    payload_schema=DockerExecPayload,
    metadata=_job_metadata(requires_admin=True, risk="remote-execution"),
)
register_job_kind(
    "cron.trigger",
    payload_schema=CronTriggerPayload,
    metadata=_job_metadata(requires_admin=True, risk="network-egress"),
)
register_job_kind(
    "container.run",
    payload_schema=ContainerRunPayload,
    result_schema=ContainerRunResult,
    metadata=_job_metadata(requires_admin=True, risk="container-execution"),
)
register_job_kind(
    "healthcheck",
    payload_schema=HealthcheckPayload,
    result_schema=HealthcheckResult,
    metadata=_job_metadata(requires_admin=True, risk="network-probe"),
)
register_job_kind(
    "ml.inference",
    payload_schema=MLInferencePayload,
    result_schema=MLInferenceResult,
    metadata=_job_metadata(requires_admin=True, risk="data-ingress"),
)
register_job_kind(
    "media.transcode",
    payload_schema=MediaTranscodePayload,
    result_schema=MediaTranscodeResult,
    metadata=_job_metadata(requires_admin=True, risk="data-egress"),
)
register_job_kind(
    "script.run",
    payload_schema=ScriptRunPayload,
    result_schema=ScriptRunResult,
    metadata=_job_metadata(requires_admin=True, risk="remote-execution"),
)
register_job_kind(
    "wasm.run",
    payload_schema=WasmRunPayload,
    result_schema=WasmRunResult,
    metadata=_job_metadata(requires_admin=True, risk="remote-execution"),
)
register_job_kind(
    "cron.tick",
    payload_schema=CronTickPayload,
    result_schema=CronTickResult,
    metadata=_job_metadata(requires_admin=True, risk="scheduler-control"),
)
register_job_kind(
    "data.sync",
    payload_schema=DataSyncPayload,
    result_schema=DataSyncResult,
    metadata=_job_metadata(requires_admin=True, risk="filesystem-sync"),
)
register_job_kind(
    "file.transfer",
    payload_schema=FileTransferPayload,
    result_schema=FileTransferResult,
    metadata=_job_metadata(requires_admin=True, risk="filesystem-access"),
)
register_job_kind(
    "alert.notify",
    payload_schema=AlertNotifyPayload,
    result_schema=AlertNotifyResult,
    metadata=_job_metadata(requires_admin=True, risk="network-egress"),
)


def get_job_kind_info(kind: str) -> dict[str, Any]:
    payload_schema = _JOB_KIND_REGISTRY.get(kind)
    result_schema = _JOB_RESULT_REGISTRY.get(kind)
    return {
        "kind": kind,
        "has_payload_schema": payload_schema is not None,
        "has_result_schema": result_schema is not None,
        "payload_schema": payload_schema.model_json_schema() if payload_schema else None,
        "result_schema": result_schema.model_json_schema() if result_schema else None,
        "metadata": get_job_submission_policy(kind),
    }


def list_job_kinds() -> list[dict[str, Any]]:
    return [get_job_kind_info(kind) for kind in get_registered_job_kinds()]
