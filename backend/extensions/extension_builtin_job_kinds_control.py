"""Builtin control and scheduling job kind catalog."""

from __future__ import annotations

from backend.extensions.job_kind_registry import CronTickPayload, CronTickResult, HealthcheckPayload, HealthcheckResult

from .extension_contracts import JobKindSpec


def build_core_control_job_kinds() -> tuple[JobKindSpec, ...]:
    return (
        JobKindSpec("healthcheck", payload_schema=HealthcheckPayload, result_schema=HealthcheckResult, description="Run a health probe."),
        JobKindSpec("cron.tick", payload_schema=CronTickPayload, result_schema=CronTickResult, description="Execute a scheduled cron trigger."),
    )
