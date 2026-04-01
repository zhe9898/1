"""Failure taxonomy for job retry strategy.

This module provides failure classification to distinguish transient failures
(should retry) from permanent failures (should not retry).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.models.job import Job


class FailureCategory(str, Enum):
    """Job failure classification for retry strategy."""

    # Transient failures - should retry immediately
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    NODE_UNHEALTHY = "node_unhealthy"
    NETWORK_ERROR = "network_error"

    # Permanent failures - should not retry
    PERMANENT = "permanent"
    EXECUTION_ERROR = "execution_error"
    INVALID_PAYLOAD = "invalid_payload"
    MISSING_DEPENDENCY = "missing_dependency"
    PERMISSION_DENIED = "permission_denied"

    # System failures
    LEASE_EXPIRED = "lease_expired"
    NODE_DRAINED = "node_drained"
    CANCELED = "canceled"

    # Unknown
    UNKNOWN = "unknown"


def infer_failure_category(  # noqa: C901
    error_message: str,
    exit_code: int | None = None,
    error_details: dict[str, Any] | None = None,
) -> FailureCategory:
    """Infer failure category from error message, exit code, and context.

    Args:
        error_message: Error message from job execution
        exit_code: Process exit code (if available)
        error_details: Additional error context (e.g., {"oom_killed": True, "signal": "SIGKILL"})

    Returns:
        FailureCategory enum value

    Examples:
        >>> infer_failure_category("connection timeout")
        FailureCategory.TIMEOUT

        >>> infer_failure_category("out of memory", exit_code=137)
        FailureCategory.RESOURCE_EXHAUSTED

        >>> infer_failure_category("permission denied")
        FailureCategory.PERMISSION_DENIED
    """
    msg_lower = error_message.lower()

    # Priority 1: Use error_details if available (highest confidence)
    if error_details:
        if error_details.get("oom_killed"):
            return FailureCategory.RESOURCE_EXHAUSTED

        signal = error_details.get("signal")
        if signal in ("SIGTERM", "SIGKILL"):
            reason = error_details.get("reason", "")
            if reason == "node_drain":
                return FailureCategory.NODE_DRAINED
            if reason == "oom":
                return FailureCategory.RESOURCE_EXHAUSTED
            # Conservative: treat as transient
            return FailureCategory.TRANSIENT

    # Priority 2: Timeout patterns (high confidence)
    if any(p in msg_lower for p in ["timeout", "timed out", "deadline exceeded", "context deadline"]):
        return FailureCategory.TIMEOUT

    # Priority 3: Resource exhaustion (high confidence)
    if any(p in msg_lower for p in ["out of memory", "oom", "memory limit", "cannot allocate memory"]):
        return FailureCategory.RESOURCE_EXHAUSTED

    if any(p in msg_lower for p in ["disk full", "no space left", "enospc", "quota exceeded"]):
        return FailureCategory.RESOURCE_EXHAUSTED

    if any(p in msg_lower for p in ["too many open files", "emfile", "enfile", "file descriptor"]):
        return FailureCategory.RESOURCE_EXHAUSTED

    # Priority 4: Network errors (medium confidence)
    network_patterns = [
        "connection refused",
        "connection reset",
        "connection timeout",
        "network unreachable",
        "host unreachable",
        "no route to host",
        "econnrefused",
        "econnreset",
        "etimedout",
        "ehostunreach",
    ]
    if any(p in msg_lower for p in network_patterns):
        # Exclude false positives like "connection not found"
        if "not found" not in msg_lower:
            return FailureCategory.NETWORK_ERROR

    if any(p in msg_lower for p in ["dns", "name resolution", "getaddrinfo", "resolve"]):
        # Ensure it's actually DNS-related
        if any(ctx in msg_lower for ctx in ["dns", "hostname", "address"]):
            return FailureCategory.NETWORK_ERROR

    # Priority 5: Permission errors (high confidence)
    if any(p in msg_lower for p in ["permission denied", "access denied", "forbidden", "unauthorized", "eacces", "eperm", "401", "403"]):
        return FailureCategory.PERMISSION_DENIED

    # Priority 6: Missing dependency (medium confidence)
    if any(p in msg_lower for p in ["no such file", "no such directory", "command not found", "enoent"]):
        # Exclude network-related "not found"
        if not any(net in msg_lower for net in ["connection", "host", "network"]):
            return FailureCategory.MISSING_DEPENDENCY

    if any(p in msg_lower for p in ["module not found", "package not found", "import error", "cannot import"]):
        return FailureCategory.MISSING_DEPENDENCY

    # Priority 7: Invalid payload (medium confidence)
    if any(p in msg_lower for p in ["invalid", "malformed", "parse error", "syntax error", "bad request", "400"]):
        # Exclude execution errors
        if "panic" not in msg_lower and "fatal" not in msg_lower:
            return FailureCategory.INVALID_PAYLOAD

    # Priority 8: Execution errors (low confidence)
    if any(p in msg_lower for p in ["panic", "fatal", "segmentation fault", "core dumped", "sigsegv"]):
        return FailureCategory.EXECUTION_ERROR

    # Priority 9: Exit code analysis
    if exit_code is not None:
        if exit_code == 137:  # SIGKILL
            # Could be OOM or manual kill - default to resource exhausted
            return FailureCategory.RESOURCE_EXHAUSTED
        elif exit_code in (1, 2, 127):  # Common execution errors
            return FailureCategory.EXECUTION_ERROR
        elif exit_code != 0:
            # Non-zero exit code, but unknown reason
            return FailureCategory.UNKNOWN

    # Default: unknown (conservative)
    return FailureCategory.UNKNOWN


def should_retry_job(job: Job, failure_category: FailureCategory) -> bool:
    """Decide if job should be retried based on failure category.

    Args:
        job: Job instance
        failure_category: Failure category

    Returns:
        True if job should be retried, False otherwise

    Examples:
        >>> job = Job(retry_count=0, max_retries=3, attempt_count=0)
        >>> should_retry_job(job, FailureCategory.TIMEOUT)
        True

        >>> should_retry_job(job, FailureCategory.EXECUTION_ERROR)
        False
    """
    # Never retry these categories (permanent failures)
    if failure_category in {
        FailureCategory.PERMANENT,
        FailureCategory.EXECUTION_ERROR,
        FailureCategory.INVALID_PAYLOAD,
        FailureCategory.MISSING_DEPENDENCY,
        FailureCategory.PERMISSION_DENIED,
        FailureCategory.CANCELED,
    }:
        return False

    # Check retry count
    retry_count = int(job.retry_count or 0)
    max_retries = int(job.max_retries or 0)

    if retry_count >= max_retries:
        return False

    # Check attempt count (global limit to prevent infinite retries)
    attempt_count = int(getattr(job, "attempt_count", 0) or 0)
    if attempt_count >= max_retries + 1:
        return False

    return True


def calculate_retry_delay_seconds(
    failure_category: FailureCategory,
    retry_count: int,
    *,
    base_delay: int | None = None,
    max_delay: int | None = None,
) -> int:
    """Calculate retry delay with exponential backoff and category-aware scaling.

    Transient / timeout → gentle backoff (base * 2^n, cap max)
    Resource / node_unhealthy → heavier backoff (base*multiplier * 2^n, cap max)
    Lease_expired / node_drained → short fixed delays
    """
    if base_delay is None or max_delay is None:
        from backend.core.scheduling_policy_store import get_policy_store

        rp = get_policy_store().active.retry
        if base_delay is None:
            base_delay = rp.base_delay_seconds
        if max_delay is None:
            max_delay = rp.max_delay_seconds

    if failure_category in (FailureCategory.LEASE_EXPIRED, FailureCategory.NODE_DRAINED):
        return min(base_delay, max_delay)

    if failure_category in (FailureCategory.RESOURCE_EXHAUSTED, FailureCategory.NODE_UNHEALTHY):
        from backend.core.scheduling_policy_store import get_policy_store

        _rp = get_policy_store().active.retry
        multiplier = _rp.resource_exhausted_multiplier
        delay = base_delay * multiplier * (2 ** min(retry_count, _rp.max_exponent))
    else:
        from backend.core.scheduling_policy_store import get_policy_store as _gps

        delay = base_delay * (2 ** min(retry_count, _gps().active.retry.max_exponent))

    return int(min(delay, max_delay))
