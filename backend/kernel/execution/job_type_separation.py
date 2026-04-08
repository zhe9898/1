"""
Scheduled/Background Job Separation Module

Provides clear separation between scheduled jobs (time-sensitive, cron-based)
and background jobs (event-driven, async processing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from backend.kernel.execution.job_status import normalize_job_status

if TYPE_CHECKING:
    from backend.models.job import Job

# ============================================================================
# Job Type Definitions
# ============================================================================

JOB_TYPE_SCHEDULED: Final[str] = "scheduled"
JOB_TYPE_BACKGROUND: Final[str] = "background"

# Sources that indicate scheduled jobs
SCHEDULED_JOB_SOURCES: Final[frozenset[str]] = frozenset({"scheduler", "cron", "timer"})


def get_job_type(job: Job) -> str:
    """Get job type (scheduled or background).

    Args:
        job: Job instance

    Returns:
        "scheduled" or "background"

    Examples:
        >>> job = Job(source="scheduler")
        >>> get_job_type(job)
        'scheduled'

        >>> job = Job(source="api")
        >>> get_job_type(job)
        'background'
    """
    source = (job.source or "").lower().strip()
    if source in SCHEDULED_JOB_SOURCES:
        return JOB_TYPE_SCHEDULED
    return JOB_TYPE_BACKGROUND


def is_scheduled_job(job: Job) -> bool:
    """Check if job is a scheduled job.

    Args:
        job: Job instance

    Returns:
        True if scheduled job, False otherwise
    """
    return get_job_type(job) == JOB_TYPE_SCHEDULED


def is_background_job(job: Job) -> bool:
    """Check if job is a background job.

    Args:
        job: Job instance

    Returns:
        True if background job, False otherwise
    """
    return get_job_type(job) == JOB_TYPE_BACKGROUND


# ============================================================================
# Job Type Configuration
# ============================================================================

JOB_TYPE_CONFIG: Final[dict[str, dict[str, object]]] = {
    JOB_TYPE_SCHEDULED: {
        "default_priority": 70,  # High layer
        "max_concurrent_global": 10,
        "max_concurrent_per_tenant": 5,
        "max_concurrent_per_connector": 3,
        "timeout_multiplier": 1.0,  # Strict timeout
        "default_max_retries": 1,  # Conservative retry
        "retry_delay_seconds": 300,  # 5 minutes
    },
    JOB_TYPE_BACKGROUND: {
        "default_priority": 50,  # Normal layer
        "max_concurrent_global": 100,
        "max_concurrent_per_tenant": 50,
        "max_concurrent_per_connector": 20,
        "timeout_multiplier": 2.0,  # Relaxed timeout
        "default_max_retries": 3,  # Aggressive retry
        "retry_delay_seconds": 60,  # 1 minute
    },
}


def get_job_type_config(job_type: str, key: str, default: object = None) -> object:
    """Get configuration value for a job type.

    Args:
        job_type: "scheduled" or "background"
        key: Configuration key
        default: Default value if not found

    Returns:
        Configuration value

    Examples:
        >>> get_job_type_config("scheduled", "default_priority")
        70

        >>> get_job_type_config("background", "max_concurrent_global")
        100
    """
    config = JOB_TYPE_CONFIG.get(job_type, {})
    return config.get(key, default)


def apply_job_type_defaults(job: Job) -> None:
    """Apply default configuration based on job type.

    Modifies job in place to set appropriate defaults for scheduled vs background jobs.

    Args:
        job: Job instance (modified in place)

    Examples:
        >>> job = Job(source="scheduler")
        >>> apply_job_type_defaults(job)
        >>> job.priority
        70
        >>> job.max_retries
        1

        >>> job = Job(source="api")
        >>> apply_job_type_defaults(job)
        >>> job.priority
        50
        >>> job.max_retries
        3
    """
    job_type = get_job_type(job)

    # Apply default priority if not set or is default value (50)
    if job.priority is None or job.priority == 50:
        default_priority = get_job_type_config(job_type, "default_priority", 50)
        job.priority = int(default_priority)  # type: ignore[call-overload]

    # Apply default max_retries if not set
    if job.max_retries is None or job.max_retries == 0:
        default_max_retries = get_job_type_config(job_type, "default_max_retries", 0)
        job.max_retries = int(default_max_retries)  # type: ignore[call-overload]


def get_job_type_stats(jobs: list[Job]) -> dict[str, dict[str, int]]:
    """Get statistics about jobs grouped by type and status.

    Args:
        jobs: List of job instances

    Returns:
        Dictionary mapping job type to status counts:
        {
            "scheduled": {"pending": 5, "leased": 3, "completed": 100, "failed": 2},
            "background": {"pending": 50, "leased": 20, "completed": 1000, "failed": 10}
        }
    """
    stats: dict[str, dict[str, int]] = {
        JOB_TYPE_SCHEDULED: {"pending": 0, "leased": 0, "completed": 0, "failed": 0, "cancelled": 0},
        JOB_TYPE_BACKGROUND: {"pending": 0, "leased": 0, "completed": 0, "failed": 0, "cancelled": 0},
    }

    for job in jobs:
        job_type = get_job_type(job)
        status = normalize_job_status(job.status) or "pending"

        if status in stats[job_type]:
            stats[job_type][status] += 1

    return stats


# ============================================================================
# Concurrent Limit Helpers
# ============================================================================


def get_max_concurrent_limit(job_type: str, scope: str = "global") -> int:
    """Get maximum concurrent limit for a job type and scope.

    Args:
        job_type: "scheduled" or "background"
        scope: "global", "per_tenant", or "per_connector"

    Returns:
        Maximum concurrent limit

    Examples:
        >>> get_max_concurrent_limit("scheduled", "global")
        10

        >>> get_max_concurrent_limit("background", "per_tenant")
        50
    """
    key = f"max_concurrent_{scope}"
    default = 100 if job_type == JOB_TYPE_BACKGROUND else 10
    raw = get_job_type_config(job_type, key, default)
    return int(str(raw))


def format_concurrent_limit_error(
    job_type: str,
    current: int,
    max_limit: int,
    scope: str = "global",
) -> str:
    """Format a user-friendly error message for concurrent limit reached.

    Args:
        job_type: "scheduled" or "background"
        current: Current concurrent count
        max_limit: Maximum allowed
        scope: "global", "per_tenant", or "per_connector"

    Returns:
        Formatted error message
    """
    scope_desc = {
        "global": "system-wide",
        "per_tenant": "for your tenant",
        "per_connector": "for this connector",
    }.get(scope, scope)

    return (
        f"Concurrent {job_type} job limit reached ({current}/{max_limit} {scope_desc}). "
        f"Please wait for running jobs to complete or contact administrator to increase limit."
    )
