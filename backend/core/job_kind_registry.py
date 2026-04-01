"""Job kind registry and payload schema validation.

Provides schema registration and validation for job payloads to ensure
type safety and prevent business logic coupling in the platform kernel.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

# ============================================================================
# Job Kind Registry
# ============================================================================

# Registry mapping job kind to payload schema validator
_JOB_KIND_REGISTRY: dict[str, type[BaseModel]] = {}

# Registry mapping job kind to result schema validator
_JOB_RESULT_REGISTRY: dict[str, type[BaseModel]] = {}

# Registry mapping job kind to extension/discovery metadata
_JOB_KIND_METADATA_REGISTRY: dict[str, dict[str, Any]] = {}


def register_job_kind(
    kind: str,
    *,
    payload_schema: type[BaseModel] | None = None,
    result_schema: type[BaseModel] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Register a job kind with optional payload and result schemas.

    Args:
        kind: Job kind identifier (e.g., "shell.exec", "http.request")
        payload_schema: Pydantic model for payload validation
        result_schema: Pydantic model for result validation

    Examples:
        >>> class ShellExecPayload(BaseModel):
        ...     command: str
        ...     timeout: int = 300
        >>> register_job_kind("shell.exec", payload_schema=ShellExecPayload)
    """
    if payload_schema is not None:
        _JOB_KIND_REGISTRY[kind] = payload_schema

    if result_schema is not None:
        _JOB_RESULT_REGISTRY[kind] = result_schema

    if metadata is not None:
        _JOB_KIND_METADATA_REGISTRY[kind] = dict(metadata)
    else:
        _JOB_KIND_METADATA_REGISTRY.setdefault(kind, {"source": "core"})


def unregister_job_kind(kind: str) -> None:
    """Unregister a job kind.

    Args:
        kind: Job kind identifier
    """
    _JOB_KIND_REGISTRY.pop(kind, None)
    _JOB_RESULT_REGISTRY.pop(kind, None)
    _JOB_KIND_METADATA_REGISTRY.pop(kind, None)


def is_job_kind_registered(kind: str) -> bool:
    """Check if a job kind is registered.

    Args:
        kind: Job kind identifier

    Returns:
        True if registered, False otherwise
    """
    return kind in _JOB_KIND_REGISTRY or kind in _JOB_RESULT_REGISTRY or kind in _JOB_KIND_METADATA_REGISTRY


def get_registered_job_kinds() -> list[str]:
    """Get list of all registered job kinds.

    Returns:
        List of job kind identifiers
    """
    all_kinds = set(_JOB_KIND_REGISTRY.keys()) | set(_JOB_RESULT_REGISTRY.keys()) | set(_JOB_KIND_METADATA_REGISTRY.keys())
    return sorted(all_kinds)


def validate_job_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate job payload against registered schema.

    Args:
        kind: Job kind identifier
        payload: Job payload dictionary

    Returns:
        Validated payload dictionary

    Raises:
        ValueError: If validation fails or kind not registered

    Examples:
        >>> register_job_kind("shell.exec", payload_schema=ShellExecPayload)
        >>> validate_job_payload("shell.exec", {"command": "ls"})
        {'command': 'ls', 'timeout': 300}
    """
    schema = _JOB_KIND_REGISTRY.get(kind)
    if schema is None:
        # No schema registered - allow any payload (backward compatibility)
        return payload

    try:
        validated = schema(**payload)
        return validated.model_dump(mode="python")
    except ValidationError as e:
        error_details = e.errors()
        raise ValueError(f"Job payload validation failed for kind '{kind}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}") from e


def validate_job_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    """Validate job result against registered schema.

    Args:
        kind: Job kind identifier
        result: Job result dictionary

    Returns:
        Validated result dictionary

    Raises:
        ValueError: If validation fails or kind not registered
    """
    schema = _JOB_RESULT_REGISTRY.get(kind)
    if schema is None:
        # No schema registered - allow any result (backward compatibility)
        return result

    try:
        validated = schema(**result)
        return validated.model_dump(mode="python")
    except ValidationError as e:
        error_details = e.errors()
        raise ValueError(f"Job result validation failed for kind '{kind}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}") from e


# ============================================================================
# Built-in Job Kinds
# ============================================================================


class ShellExecPayload(BaseModel):
    """Payload schema for shell.exec job kind."""

    command: str
    timeout: int = 300
    env: dict[str, str] = {}
    working_dir: str | None = None


class ShellExecResult(BaseModel):
    """Result schema for shell.exec job kind."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float


class HttpRequestPayload(BaseModel):
    """Payload schema for http.request job kind."""

    url: str
    method: str = "GET"
    headers: dict[str, str] = {}
    body: str | None = None
    timeout: int = 30


class HttpRequestResult(BaseModel):
    """Result schema for http.request job kind."""

    status_code: int
    headers: dict[str, str] = {}
    body: str = ""
    duration_seconds: float


# Register built-in job kinds
register_job_kind(
    "shell.exec",
    payload_schema=ShellExecPayload,
    result_schema=ShellExecResult,
)

register_job_kind(
    "http.request",
    payload_schema=HttpRequestPayload,
    result_schema=HttpRequestResult,
)


# ============================================================================
# Extended built-in job kinds for edge-computing platform
# ============================================================================


class ContainerRunPayload(BaseModel):
    """Payload schema for container.run job kind."""

    image: str
    command: list[str] = []
    env: dict[str, str] = {}
    working_dir: str | None = None
    timeout: int = 600
    pull_policy: str = "IfNotPresent"  # Always | IfNotPresent | Never
    memory_limit_mb: int | None = None
    cpu_limit_millicores: int | None = None


class ContainerRunResult(BaseModel):
    """Result schema for container.run job kind."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    container_id: str = ""
    duration_seconds: float = 0.0


class HealthcheckPayload(BaseModel):
    """Payload schema for healthcheck job kind."""

    target: str  # URL, host:port, or service name
    check_type: str = "http"  # http | tcp | dns | exec
    timeout: int = 10
    expected_status: int = 200
    interval_seconds: int = 0  # 0 = one-shot


class HealthcheckResult(BaseModel):
    """Result schema for healthcheck job kind."""

    healthy: bool
    latency_ms: float = 0.0
    status_code: int | None = None
    message: str = ""


class MLInferencePayload(BaseModel):
    """Payload schema for ml.inference job kind."""

    model_id: str
    input_data: dict[str, Any] = {}
    input_uri: str | None = None
    runtime: str = "onnx"  # onnx | tensorrt | openvino | pytorch
    batch_size: int = 1
    timeout: int = 120
    precision: str = "fp32"  # fp32 | fp16 | int8


class MLInferenceResult(BaseModel):
    """Result schema for ml.inference job kind."""

    predictions: list[Any] = []
    output_uri: str | None = None
    inference_time_ms: float = 0.0
    model_version: str = ""


class MediaTranscodePayload(BaseModel):
    """Payload schema for media.transcode job kind."""

    input_uri: str
    output_uri: str
    codec: str = "h264"  # h264 | h265 | vp9 | av1
    resolution: str | None = None  # e.g. "1920x1080"
    bitrate_kbps: int | None = None
    timeout: int = 1800
    hardware_accel: bool = False


class MediaTranscodeResult(BaseModel):
    """Result schema for media.transcode job kind."""

    output_uri: str = ""
    duration_seconds: float = 0.0
    output_size_bytes: int = 0
    codec_used: str = ""


class ScriptRunPayload(BaseModel):
    """Payload schema for script.run job kind."""

    interpreter: str = "bash"  # bash | python | node | powershell
    script: str  # inline script content
    args: list[str] = []
    env: dict[str, str] = {}
    timeout: int = 300


class ScriptRunResult(BaseModel):
    """Result schema for script.run job kind."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class WasmRunPayload(BaseModel):
    """Payload schema for wasm.run job kind."""

    module_uri: str  # URL or local path to .wasm file
    function: str = "_start"
    args: list[str] = []
    env: dict[str, str] = {}
    timeout: int = 60
    memory_pages: int = 256  # WASM linear memory pages (64KB each)


class WasmRunResult(BaseModel):
    """Result schema for wasm.run job kind."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class CronTickPayload(BaseModel):
    """Payload schema for cron.tick job kind — scheduled trigger execution."""

    schedule_id: str
    cron_expression: str  # e.g. "*/5 * * * *"
    action: str  # logical action name to invoke
    action_payload: dict[str, Any] = {}
    timeout: int = 120


class CronTickResult(BaseModel):
    """Result schema for cron.tick job kind."""

    triggered: bool = True
    action_result: dict[str, Any] = {}
    next_fire_at: str | None = None  # ISO8601


class DataSyncPayload(BaseModel):
    """Payload schema for data.sync job kind — edge↔cloud data synchronisation."""

    source_uri: str
    dest_uri: str
    direction: str = "push"  # push | pull | bidirectional
    filters: list[str] = []  # glob patterns for selective sync
    conflict_resolution: str = "latest-wins"  # latest-wins | source-wins | manual
    timeout: int = 600
    bandwidth_limit_kbps: int | None = None


class DataSyncResult(BaseModel):
    """Result schema for data.sync job kind."""

    files_transferred: int = 0
    bytes_transferred: int = 0
    conflicts: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = []


register_job_kind(
    "container.run",
    payload_schema=ContainerRunPayload,
    result_schema=ContainerRunResult,
)

register_job_kind(
    "healthcheck",
    payload_schema=HealthcheckPayload,
    result_schema=HealthcheckResult,
)

register_job_kind(
    "ml.inference",
    payload_schema=MLInferencePayload,
    result_schema=MLInferenceResult,
)

register_job_kind(
    "media.transcode",
    payload_schema=MediaTranscodePayload,
    result_schema=MediaTranscodeResult,
)

register_job_kind(
    "script.run",
    payload_schema=ScriptRunPayload,
    result_schema=ScriptRunResult,
)

register_job_kind(
    "wasm.run",
    payload_schema=WasmRunPayload,
    result_schema=WasmRunResult,
)

register_job_kind(
    "cron.tick",
    payload_schema=CronTickPayload,
    result_schema=CronTickResult,
)

register_job_kind(
    "data.sync",
    payload_schema=DataSyncPayload,
    result_schema=DataSyncResult,
)


class FileTransferPayload(BaseModel):
    """Payload schema for file.transfer job kind — local file copy with integrity."""

    src: str
    dst: str
    overwrite: bool = False
    verify_sha256: str | None = None
    mkdir: bool = True


class FileTransferResult(BaseModel):
    """Result schema for file.transfer job kind."""

    src: str = ""
    dst: str = ""
    bytes: int = 0
    sha256: str = ""
    duration_ms: float = 0.0
    throughput_mbs: float = 0.0


register_job_kind(
    "file.transfer",
    payload_schema=FileTransferPayload,
    result_schema=FileTransferResult,
)


# ============================================================================
# Job Kind Discovery
# ============================================================================


def get_job_kind_info(kind: str) -> dict[str, Any]:
    """Get information about a registered job kind.

    Args:
        kind: Job kind identifier

    Returns:
        Dictionary with kind information
    """
    payload_schema = _JOB_KIND_REGISTRY.get(kind)
    result_schema = _JOB_RESULT_REGISTRY.get(kind)
    metadata = dict(_JOB_KIND_METADATA_REGISTRY.get(kind, {}))

    return {
        "kind": kind,
        "has_payload_schema": payload_schema is not None,
        "has_result_schema": result_schema is not None,
        "payload_schema": payload_schema.model_json_schema() if payload_schema else None,
        "result_schema": result_schema.model_json_schema() if result_schema else None,
        "metadata": metadata,
    }


def list_job_kinds() -> list[dict[str, Any]]:
    """List all registered job kinds with their schemas.

    Returns:
        List of job kind information dictionaries
    """
    return [get_job_kind_info(kind) for kind in get_registered_job_kinds()]
