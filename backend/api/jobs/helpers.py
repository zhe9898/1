import datetime
import uuid

from backend.api.action_contracts import ControlAction, optional_reason_field
from backend.api.ui_contracts import StatusView
from backend.core.control_plane_state import job_attention_reason, job_lease_state, job_lease_state_view, job_status_view
from backend.core.job_status import (
    canonicalize_job_status_input,
    normalize_job_attempt_status,
    normalize_job_status,
)
from backend.core.safe_error_projection import project_safe_error
from backend.core.worker_pool import resolve_job_queue_contract_from_record
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .models import JobAttemptResponse, JobLeaseResponse, JobResponse


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _new_lease_token() -> str:
    return uuid.uuid4().hex


def _normalize_job_status_filter(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"pending", "running", "completed", "failed", "cancelled"}:
        return normalized
    try:
        return job_status_view(canonicalize_job_status_input(normalized))["key"]
    except ValueError:
        return normalized


def _build_job_actions(job: Job, *, now: datetime.datetime) -> list[ControlAction]:
    job_status = normalize_job_status(job.status) or "pending"
    can_cancel = job_status in {"pending", "leased"}
    can_retry = job_status in {"failed", "completed", "cancelled"}
    lease_state = job_lease_state(status=job_status, leased_until=job.leased_until, now=now)
    cancel_reason: str | None = None
    if not can_cancel:
        cancel_reason = f"Only pending or leased jobs can be cancelled (current: {job_status})"
    elif lease_state == "stale":
        cancel_reason = None
    retry_reason: str | None = None
    if not can_retry:
        retry_reason = f"Only terminal jobs can be retried manually (current: {job_status})"
    return [
        ControlAction(
            key="cancel",
            label="Cancel",
            endpoint=f"/v1/jobs/{job.job_id}/cancel",
            enabled=can_cancel,
            reason=cancel_reason,
            confirmation="Cancel this job and clear any active lease?",
            fields=[optional_reason_field()],
        ),
        ControlAction(
            key="retry",
            label="Retry Now",
            endpoint=f"/v1/jobs/{job.job_id}/retry",
            enabled=can_retry,
            reason=retry_reason,
            confirmation="Queue a fresh attempt for this job?",
            fields=[optional_reason_field()],
        ),
        ControlAction(
            key="explain",
            label="Explain Placement",
            endpoint=f"/v1/jobs/{job.job_id}/explain",
            method="GET",
            enabled=True,
            requires_admin=False,
            reason=None,
            confirmation=None,
            fields=[],
        ),
    ]


def _to_response(job: Job, *, now: datetime.datetime | None = None) -> JobResponse:
    current_time = now or _utcnow()
    job_status = normalize_job_status(job.status) or str(job.status or "pending")
    lease_state = job_lease_state(status=job_status, leased_until=job.leased_until, now=current_time)
    status_view = job_status_view(job_status)
    queue_class, worker_pool = resolve_job_queue_contract_from_record(job)
    safe_error = project_safe_error(
        failure_category=getattr(job, "failure_category", None),
        status=job_status,
        error_message=job.error_message,
    )
    return JobResponse(
        job_id=job.job_id,
        kind=job.kind,
        status=job_status,
        status_view=StatusView(**status_view),
        node_id=job.node_id,
        connector_id=job.connector_id,
        idempotency_key=job.idempotency_key,
        priority=job.priority,
        queue_class=queue_class,
        worker_pool=worker_pool,
        target_os=job.target_os,
        target_arch=job.target_arch,
        target_executor=job.target_executor,
        required_capabilities=list(job.required_capabilities or []),
        target_zone=job.target_zone,
        required_cpu_cores=job.required_cpu_cores,
        required_memory_mb=job.required_memory_mb,
        required_gpu_vram_mb=job.required_gpu_vram_mb,
        required_storage_mb=job.required_storage_mb,
        timeout_seconds=job.timeout_seconds,
        max_retries=job.max_retries,
        retry_count=job.retry_count,
        attempt_count=int(getattr(job, "attempt_count", 0) or 0),
        failure_category=getattr(job, "failure_category", None),
        estimated_duration_s=job.estimated_duration_s,
        source=job.source,
        attempt=job.attempt,
        payload=dict(job.payload or {}),
        result=dict(job.result) if job.result else None,
        error_message=None,
        safe_error_code=safe_error.code if safe_error else None,
        safe_error_hint=safe_error.hint if safe_error else None,
        lease_seconds=job.lease_seconds,
        leased_until=job.leased_until,
        lease_state=lease_state,
        lease_state_view=StatusView(**job_lease_state_view(lease_state)),
        attention_reason=job_attention_reason(
            status=job_status,
            priority=int(job.priority or 0),
            leased_until=job.leased_until,
            now=current_time,
        ),
        actions=_build_job_actions(job, now=current_time),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        # Edge computing
        data_locality_key=getattr(job, "data_locality_key", None),
        max_network_latency_ms=getattr(job, "max_network_latency_ms", None),
        prefer_cached_data=bool(getattr(job, "prefer_cached_data", False)),
        power_budget_watts=getattr(job, "power_budget_watts", None),
        thermal_sensitivity=getattr(job, "thermal_sensitivity", None),
        cloud_fallback_enabled=bool(getattr(job, "cloud_fallback_enabled", False)),
        # Scheduling strategy and affinity
        scheduling_strategy=getattr(job, "scheduling_strategy", None),
        affinity_labels=dict(getattr(job, "affinity_labels", None) or {}),
        affinity_rule=getattr(job, "affinity_rule", None),
        anti_affinity_key=getattr(job, "anti_affinity_key", None),
        # Business scheduling
        parent_job_id=getattr(job, "parent_job_id", None),
        depends_on=list(getattr(job, "depends_on", None) or []),
        gang_id=getattr(job, "gang_id", None),
        batch_key=getattr(job, "batch_key", None),
        preemptible=bool(getattr(job, "preemptible", True)),
        deadline_at=getattr(job, "deadline_at", None),
        sla_seconds=getattr(job, "sla_seconds", None),
        retry_at=getattr(job, "retry_at", None),
    )


def _to_lease_response(job: Job, *, now: datetime.datetime | None = None) -> JobLeaseResponse:
    if not job.lease_token:
        raise ValueError("leased job is missing lease_token")
    return JobLeaseResponse(
        **_to_response(job, now=now).model_dump(mode="python"),
        lease_token=job.lease_token,
    )


def _to_attempt_response(attempt: JobAttempt) -> JobAttemptResponse:
    attempt_status = normalize_job_attempt_status(attempt.status) or str(attempt.status or "leased")
    safe_error = project_safe_error(
        failure_category=getattr(attempt, "failure_category", None),
        status=attempt_status,
        error_message=attempt.error_message,
    )
    return JobAttemptResponse(
        attempt_id=attempt.attempt_id,
        job_id=attempt.job_id,
        node_id=attempt.node_id,
        lease_token=attempt.lease_token,
        attempt_no=attempt.attempt_no,
        status=attempt_status,
        status_view=StatusView(**job_status_view(attempt_status)),
        score=attempt.score,
        error_message=None,
        safe_error_code=safe_error.code if safe_error else None,
        safe_error_hint=safe_error.hint if safe_error else None,
        result_summary=dict(attempt.result_summary) if attempt.result_summary else None,
        created_at=attempt.created_at,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
    )


def _matches_job_status_filter(job: Job, status_filter: str, *, now: datetime.datetime) -> bool:
    return job_status_view(job.status)["key"] == status_filter


def _matches_job_list_filters(
    job: Job,
    *,
    now: datetime.datetime,
    status: str | None,
    lease_state: str | None,
    priority_bucket: str | None,
    target_executor: str | None,
    target_zone: str | None,
    required_capability: str | None,
) -> bool:
    normalized_status_filter = _normalize_job_status_filter(status) if status else None
    if normalized_status_filter and not _matches_job_status_filter(job, normalized_status_filter, now=now):
        return False
    normalized_job_status = normalize_job_status(job.status) or str(job.status or "pending")
    if lease_state and job_lease_state(status=normalized_job_status, leased_until=job.leased_until, now=now) != lease_state:
        return False
    if priority_bucket == "high" and int(job.priority or 0) < 80:
        return False
    if target_executor and (job.target_executor or "") != target_executor:
        return False
    if target_zone and (job.target_zone or "") != target_zone:
        return False
    if required_capability and required_capability not in list(job.required_capabilities or []):
        return False
    return True
