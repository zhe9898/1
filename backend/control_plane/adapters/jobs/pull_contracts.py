from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from backend.models.job import Job

from .models import JobLeaseResponse


@dataclass(frozen=True, slots=True)
class PullJobsDependencies:
    authenticate_node_request: Callable[..., Awaitable[Any]]
    acquire_transaction_advisory_locks: Callable[..., Awaitable[None]]
    get_reservation_manager: Callable[[], Any]
    get_governance_facade: Callable[[], Any]
    maybe_schedule_deadline_dlq_sweep: Callable[[str, Any], None]
    get_failure_control_plane: Callable[[], Any]
    load_node_metrics: Callable[..., Awaitable[tuple[list[Any], dict[str, int], dict[str, float]]]]
    build_snapshots: Callable[..., list[Any]]
    build_job_concurrency_window: Callable[..., Any]
    load_recent_failed_job_ids: Callable[..., Awaitable[set[str]]]
    async_build_time_budgeted_placement_plan: Callable[..., Awaitable[Any]]
    select_jobs_for_node: Callable[..., list[Any]]
    append_log: Callable[..., Awaitable[None]]
    get_current_attempt: Callable[..., Awaitable[Any]]
    publish_control_event: Callable[..., Awaitable[None]]
    to_response: Callable[..., Any]
    to_lease_response: Callable[..., JobLeaseResponse]
    utcnow: Callable[[], Any]


@dataclass(slots=True)
class PullCandidateContext:
    candidates: list[Job]
    active_jobs_by_node: dict[str, list[Job]]
    recent_failed_job_ids: set[str]
    available_slots: int


@dataclass(frozen=True, slots=True)
class PullFeatureFlags:
    decision_audit: bool
    placement_policies: bool
    preemption: bool
    executor_validation: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "decision_audit": self.decision_audit,
            "placement_policies": self.placement_policies,
            "preemption": self.preemption,
            "executor_validation": self.executor_validation,
        }


@dataclass(slots=True)
class PullRuntimeContext:
    requesting_node: Any
    now: Any
    reservation_mgr: Any
    governance: Any
    feature_flags: PullFeatureFlags
    audit: Any
    failure_control_plane: Any
    node_snapshot: Any
    active_node_snapshots: list[Any]
    reliability_score: float
    accepted_kinds: set[str]
    candidate_limit: int
    concurrency_window: Any


@dataclass(slots=True)
class PullSelectionContext:
    candidates: list[Job]
    active_jobs_by_node: dict[str, list[Job]]
    active_jobs_on_node: list[Job]
    recent_failed_job_ids: set[str]
    available_slots: int
    selected: list[Any]
    placement_plan: Any
