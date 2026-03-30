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


def register_job_kind(
    kind: str,
    *,
    payload_schema: type[BaseModel] | None = None,
    result_schema: type[BaseModel] | None = None,
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


def unregister_job_kind(kind: str) -> None:
    """Unregister a job kind.

    Args:
        kind: Job kind identifier
    """
    _JOB_KIND_REGISTRY.pop(kind, None)
    _JOB_RESULT_REGISTRY.pop(kind, None)


def is_job_kind_registered(kind: str) -> bool:
    """Check if a job kind is registered.

    Args:
        kind: Job kind identifier

    Returns:
        True if registered, False otherwise
    """
    return kind in _JOB_KIND_REGISTRY or kind in _JOB_RESULT_REGISTRY


def get_registered_job_kinds() -> list[str]:
    """Get list of all registered job kinds.

    Returns:
        List of job kind identifiers
    """
    all_kinds = set(_JOB_KIND_REGISTRY.keys()) | set(_JOB_RESULT_REGISTRY.keys())
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
        raise ValueError(
            f"Job payload validation failed for kind '{kind}': "
            f"{len(error_details)} error(s) - {error_details[0]['msg']}"
        ) from e


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
        raise ValueError(
            f"Job result validation failed for kind '{kind}': "
            f"{len(error_details)} error(s) - {error_details[0]['msg']}"
        ) from e


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

    return {
        "kind": kind,
        "has_payload_schema": payload_schema is not None,
        "has_result_schema": result_schema is not None,
        "payload_schema": payload_schema.model_json_schema() if payload_schema else None,
        "result_schema": result_schema.model_json_schema() if result_schema else None,
    }


def list_job_kinds() -> list[dict[str, Any]]:
    """List all registered job kinds with their schemas.

    Returns:
        List of job kind information dictionaries
    """
    return [get_job_kind_info(kind) for kind in get_registered_job_kinds()]
