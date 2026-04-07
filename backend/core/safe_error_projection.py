from __future__ import annotations

from dataclasses import dataclass

from backend.core.job_status import normalize_job_like_status


@dataclass(frozen=True, slots=True)
class SafeErrorProjection:
    code: str
    hint: str


_SAFE_ERROR_HINTS = {
    "cancelled": "The job was cancelled before completion.",
    "deadline_expired": "The job exceeded its scheduling deadline. Review timeout and deadline policy before retrying.",
    "execution_error": "The workload failed inside the execution environment. Review runner or audit logs for internal details.",
    "invalid_payload": "The submitted payload does not match the worker contract. Fix the request and retry.",
    "lease_expired": "The runner lost its lease before completion. Review renew intervals and executor health.",
    "missing_dependency": "The execution environment is missing a required dependency. Repair the runtime and retry.",
    "network_error": "The execution path hit a network problem. Check runner or connector reachability and retry.",
    "node_drained": "The assigned node was drained before completion. Retry after capacity becomes available.",
    "node_unhealthy": "The assigned node became unhealthy. Retry after the node recovers.",
    "permanent": "The job failed with a non-retryable error. Review audit or runner logs before retrying.",
    "permission_denied": "The job was blocked by a permission boundary. Review scopes or runtime credentials.",
    "resource_exhausted": "The executor ran out of required resources. Adjust placement or capacity and retry.",
    "timeout": "The job exceeded its execution window. Review timeout policy and retry if appropriate.",
    "transient": "A transient runtime error occurred. Retry is allowed if budget remains.",
    "unknown": "The job failed with an internal runtime error. Review audit or runner logs for details.",
}


def project_safe_error(
    *,
    failure_category: str | None,
    status: str,
    error_message: str | None,
) -> SafeErrorProjection | None:
    category = _normalize_failure_category(failure_category)
    if category:
        return SafeErrorProjection(
            code=_to_safe_error_code(category),
            hint=_SAFE_ERROR_HINTS.get(category, _SAFE_ERROR_HINTS["unknown"]),
        )
    normalized_status = normalize_job_like_status(status, fallback="")
    if normalized_status == "cancelled":
        return SafeErrorProjection(code="ZEN-JOB-CANCELLED", hint=_SAFE_ERROR_HINTS["cancelled"])
    if error_message:
        return SafeErrorProjection(code="ZEN-JOB-UNKNOWN", hint=_SAFE_ERROR_HINTS["unknown"])
    return None


def _normalize_failure_category(value: str | None) -> str | None:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized == "canceled":
        return "cancelled"
    return normalized or None


def _to_safe_error_code(category: str) -> str:
    return f"ZEN-JOB-{category.replace('_', '-').upper()}"
