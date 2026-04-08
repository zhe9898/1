"""Canonical job and job-attempt status helpers."""

from __future__ import annotations

from backend.kernel.contracts.status import canonicalize_transport_status, normalize_persisted_status

JOB_STATUS_DOMAIN = "jobs.status"
JOB_ATTEMPT_STATUS_DOMAIN = "job_attempts.status"


def canonicalize_job_status_input(value: str) -> str:
    return canonicalize_transport_status(JOB_STATUS_DOMAIN, value)


def normalize_job_status(value: str | None) -> str | None:
    return normalize_persisted_status(JOB_STATUS_DOMAIN, value)


def canonicalize_job_attempt_status_input(value: str) -> str:
    return canonicalize_transport_status(JOB_ATTEMPT_STATUS_DOMAIN, value)


def normalize_job_attempt_status(value: str | None) -> str | None:
    return normalize_persisted_status(JOB_ATTEMPT_STATUS_DOMAIN, value)


def normalize_job_like_status(value: str | None, *, fallback: str = "pending") -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return fallback
    for normalizer in (normalize_job_status, normalize_job_attempt_status):
        try:
            canonical = normalizer(normalized)
        except ValueError:
            continue
        if canonical:
            return canonical
    return normalized
