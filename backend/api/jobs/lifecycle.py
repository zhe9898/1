"""
ZEN70 Jobs API – Lifecycle endpoints.

Split from routes.py for maintainability. Contains the state-machine
transition endpoints: complete, fail, progress, renew, cancel, retry.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_admin,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.core.backfill_scheduling import get_reservation_manager
from backend.core.errors import zen
from backend.core.failure_control_plane import get_failure_control_plane
from backend.core.failure_taxonomy import FailureCategory, infer_failure_category, should_retry_job
from backend.core.node_auth import authenticate_node_request
from backend.core.redis_client import CHANNEL_JOB_EVENTS, CHANNEL_RESERVATION_EVENTS, RedisClient

from .database import (
    _append_log,
    _assert_valid_lease_owner,
    _get_attempt_for_callback,
    _get_current_attempt,
    _get_job_by_id,
    _get_job_by_id_for_update,
    move_to_dead_letter_queue,
)
from .helpers import (
    _to_lease_response,
    _to_response,
    _utcnow,
)
from .models import (
    JobActionRequest,
    JobFailRequest,
    JobLeaseResponse,
    JobProgressRequest,
    JobRenewRequest,
    JobResponse,
    JobResultRequest,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


# ── Self-learning outcome feedback helper ────────────────────────────


def _record_tuner_outcome(
    job: object,
    *,
    node_id: str,
    success: bool,
    now: datetime.datetime,
) -> None:
    """Build an OutcomeSignal and feed it to the scheduler auto-tuner.

    Best-effort: never raises — scheduling must not block on learning.
    """
    try:
        from backend.core.scheduler_auto_tune import OutcomeSignal, get_scheduler_tuner

        started = getattr(job, "started_at", None)
        latency_ms = (now - started).total_seconds() * 1000.0 if started else 0.0

        signal = OutcomeSignal(
            job_id=getattr(job, "job_id", ""),
            node_id=node_id,
            kind=getattr(job, "kind", "unknown"),
            strategy=getattr(job, "scheduling_strategy", None) or "spread",
            tenant_id=getattr(job, "tenant_id", "default"),
            score_breakdown={},  # not available at lifecycle time
            success=success,
            latency_ms=latency_ms,
            retry_count=int(getattr(job, "retry_count", 0) or 0),
            node_utilisation=0.0,  # not available at lifecycle time
            timestamp=now,
        )
        get_scheduler_tuner().record_outcome(signal)
    except Exception:  # noqa: BLE001 — best-effort, never crash lifecycle
        pass


async def _cancel_job_reservation(
    redis: RedisClient | None,
    job_id: str,
    *,
    reason: str,
) -> None:
    reservation_mgr = get_reservation_manager()
    reservation = reservation_mgr.get_reservation(job_id)
    if reservation is None:
        return
    if not reservation_mgr.cancel_reservation(job_id):
        return
    await publish_control_event(
        redis,
        CHANNEL_RESERVATION_EVENTS,
        "canceled",
        {
            "reservation": reservation.to_dict(),
            "reason": reason,
            "source": "job_lifecycle",
        },
    )


@router.post("/{id}/result", response_model=JobResponse)
async def complete_job(
    id: str,
    payload: JobResultRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as completed with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "result")
    if job.status == "completed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "completed"
    attempt.result_summary = payload.result
    attempt.error_message = None
    attempt.completed_at = now
    attempt.updated_at = now
    job.status = "completed"
    job.result = payload.result
    job.error_message = None
    job.completed_at = now
    job.leased_until = None
    job.updated_at = now
    await db.flush()
    await _cancel_job_reservation(redis, job.job_id, reason="completed")
    await _append_log(
        db,
        job.job_id,
        payload.log or f"job completed by {payload.node_id} attempt={payload.attempt}",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "completed",
        {"job": response.model_dump(mode="json")},
    )

    # Reset node failure counter on success
    _fcp = get_failure_control_plane()
    await _fcp.record_success(node_id=payload.node_id, now=now)

    # Close kind circuit breaker if it was half-open (success proves kind is healthy)
    job_kind = getattr(job, "kind", None)
    if job_kind:
        kind_state = await _fcp.get_kind_circuit_state(job_kind, now=now)
        if kind_state == "half-open":
            await _fcp.reset_kind_circuit(job_kind)

    # ── Self-learning feedback: record successful outcome ──────────────
    _record_tuner_outcome(job, node_id=payload.node_id, success=True, now=now)

    return response


@router.post("/{id}/fail", response_model=JobResponse)
async def fail_job(
    id: str,
    payload: JobFailRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as failed with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "fail")
    if job.status == "failed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()

    # Infer failure category if not provided
    failure_category_str = (
        payload.failure_category
        or infer_failure_category(
            error_message=payload.error,
            exit_code=payload.error_details.get("exit_code") if payload.error_details else None,  # type: ignore[arg-type]
            error_details=payload.error_details,
        ).value
    )

    # ── Failure control plane: track node/connector/kind failures ────
    _fcp = get_failure_control_plane()
    await _fcp.record_failure(
        node_id=payload.node_id,
        job_id=job.job_id,
        category=failure_category_str,
        connector_id=getattr(job, "connector_id", None),
        kind=job.kind,
        now=now,
    )

    attempt.status = "failed"
    attempt.error_message = payload.error
    attempt.failure_category = failure_category_str
    attempt.completed_at = now
    attempt.updated_at = now

    # Decide if should retry based on failure category
    failure_category = FailureCategory(failure_category_str)
    should_retry = should_retry_job(job, failure_category)

    if should_retry:
        # Calculate retry delay with exponential backoff based on failure category
        from backend.core.failure_taxonomy import calculate_retry_delay_seconds

        retry_delay_seconds = calculate_retry_delay_seconds(
            failure_category,
            int(job.retry_count or 0),
        )
        retry_at = now + datetime.timedelta(seconds=retry_delay_seconds)

        job.retry_count = int(job.retry_count or 0) + 1
        job.status = "pending"
        job.node_id = None
        job.lease_token = None
        job.error_message = payload.error
        job.failure_category = failure_category_str
        job.completed_at = None
        job.started_at = None
        job.leased_until = None
        job.retry_at = retry_at
        job.updated_at = now
        await db.flush()
        await _cancel_job_reservation(redis, job.job_id, reason="requeued")
        await _append_log(
            db,
            job.job_id,
            payload.log
            or (
                f"job failed on {payload.node_id}; requeued retry={job.retry_count}/{job.max_retries}"
                f" category={failure_category_str} retry_at={retry_at.isoformat()}"
            ),
            level="warning",
            tenant_id=job.tenant_id,
        )
        response = _to_response(job, now=now)
        await publish_control_event(
            redis,
            CHANNEL_JOB_EVENTS,
            "requeued",
            {"job": response.model_dump(mode="json"), "failure_category": failure_category_str},
        )
        await db.commit()
        return response

    job.status = "failed"
    job.error_message = payload.error
    job.failure_category = failure_category_str
    job.completed_at = now
    job.leased_until = None
    job.updated_at = now
    await db.flush()
    await _cancel_job_reservation(redis, job.job_id, reason="failed")
    await _append_log(
        db,
        job.job_id,
        payload.log or f"job failed permanently on {payload.node_id} category={failure_category_str}",
        level="error",
        tenant_id=job.tenant_id,
    )

    # Move to dead-letter queue
    await move_to_dead_letter_queue(redis, db, job)

    # ── Self-learning feedback: record failed outcome ───────────────
    _record_tuner_outcome(job, node_id=payload.node_id, success=False, now=now)

    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "failed",
        {"job": response.model_dump(mode="json"), "failure_category": failure_category_str, "will_retry": False},
    )
    await db.commit()
    return response


@router.post("/{id}/progress", response_model=JobResponse)
async def report_job_progress(
    id: str,
    payload: JobProgressRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)
    job = await _get_job_by_id(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "progress")
    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting progress",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "running"
    attempt.result_summary = {"progress": payload.progress, "message": payload.message}
    attempt.updated_at = now
    job.started_at = job.started_at or now
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"progress={payload.progress}% node={payload.node_id} message={payload.message or '-'}",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "progress",
        {"job": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/renew", response_model=JobLeaseResponse)
async def renew_job_lease(
    id: str,
    payload: JobRenewRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobLeaseResponse:
    """Renew job lease with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "renew")
    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before renewing",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "running"
    attempt.updated_at = now
    job.leased_until = now + datetime.timedelta(seconds=payload.extend_seconds)
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"lease renewed by {payload.node_id} extend={payload.extend_seconds}s",
        tenant_id=job.tenant_id,
    )
    response = _to_lease_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "renewed",
        {"job": _to_response(job, now=now).model_dump(mode="json")},
    )
    return response


@router.post("/{id}/cancel", response_model=JobResponse)
async def cancel_job(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    job = await _get_job_by_id(db, tenant_id, id)
    if job.status == "canceled":
        return _to_response(job)
    if job.status not in {"pending", "leased"}:
        raise zen(
            "ZEN-JOB-4094",
            "Only pending or leased jobs can be canceled",
            status_code=409,
            recovery_hint="Retry only after the job returns to a cancelable state",
            details={"job_id": job.job_id, "status": job.status},
        )

    now = _utcnow()
    attempt = await _get_current_attempt(db, job)
    if attempt is not None and attempt.status in {"leased", "running"}:
        attempt.status = "canceled"
        attempt.error_message = payload.reason or "canceled by operator"
        attempt.completed_at = now
        attempt.updated_at = now

    job.status = "canceled"
    job.error_message = payload.reason or "canceled by operator"
    job.leased_until = None
    job.lease_token = None
    job.completed_at = now
    job.updated_at = now
    await db.flush()
    await _cancel_job_reservation(redis, job.job_id, reason="canceled")
    await _append_log(
        db,
        job.job_id,
        payload.reason or "job canceled by operator",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "canceled",
        {"job": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/retry", response_model=JobResponse)
async def retry_job_now(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    job = await _get_job_by_id(db, tenant_id, id)
    if job.status == "pending":
        return _to_response(job)
    if job.status not in {"failed", "completed", "canceled"}:
        raise zen(
            "ZEN-JOB-4095",
            "Only terminal jobs can be retried manually",
            status_code=409,
            recovery_hint="Cancel or wait for the current lease before retrying",
            details={"job_id": job.job_id, "status": job.status},
        )

    now = _utcnow()
    job.status = "pending"
    job.node_id = None
    job.lease_token = None
    job.leased_until = None
    job.retry_at = None
    job.result = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.retry_count = 0
    job.attempt_count = 0
    job.updated_at = now
    await db.flush()
    await _cancel_job_reservation(redis, job.job_id, reason="manual_retry")
    await _append_log(
        db,
        job.job_id,
        payload.reason or "job queued for manual retry",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "manual-retry",
        {"job": response.model_dump(mode="json")},
    )
    return response


# ── Failure control plane admin endpoints ────────────────────────────


@router.post("/control-plane/release-quarantine/{node_id}")
async def release_quarantine(
    node_id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    """Manually release a node from quarantine (admin only)."""
    _fcp = get_failure_control_plane()
    released = await _fcp.release_quarantine(node_id)
    if not released:
        raise zen(
            "ZEN-FCP-4041",
            f"Node {node_id} is not currently quarantined",
            status_code=404,
            recovery_hint="Check the control plane snapshot for quarantined nodes",
        )
    return {"node_id": node_id, "quarantine": "released"}


@router.get("/control-plane/snapshot")
async def control_plane_snapshot(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    """Return a diagnostic snapshot of all failure control plane state."""
    _fcp = get_failure_control_plane()
    now = _utcnow()
    return await _fcp.snapshot(now=now)


@router.get("/control-plane/governance/timeline")
async def governance_timeline(
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    since_hours: float | None = None,
    limit: int = 200,
    current_user: dict[str, object] = Depends(get_current_admin),
) -> list[dict[str, object]]:
    """Query governance event timeline with optional filters (admin only).

    Returns quarantine, cooling, circuit, burst events in reverse chronological order.
    """
    _fcp = get_failure_control_plane()
    since = None
    if since_hours is not None:
        since = _utcnow() - datetime.timedelta(hours=since_hours)
    return await _fcp.governance_timeline(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        since=since,
        limit=min(limit, 1000),
    )


@router.get("/control-plane/governance/stats")
async def governance_stats(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    """Return aggregate governance KPIs (admin only)."""
    _fcp = get_failure_control_plane()
    return await _fcp.governance_stats(now=_utcnow())


@router.get("/control-plane/fair-share/config")
async def fair_share_config(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    """Return current tenant fair-share configuration (admin only)."""
    from backend.core.queue_stratification import (
        SERVICE_CLASS_CONFIG,
        get_fair_scheduler,
    )

    fs = get_fair_scheduler()
    quotas = fs.get_all_quotas()
    return {
        "service_classes": SERVICE_CLASS_CONFIG,
        "default_service_class": fs._default_service_class,
        "tenant_quotas": {
            tid: {
                "max_jobs_per_round": q.max_jobs_per_round,
                "weight": q.weight,
                "service_class": q.service_class,
            }
            for tid, q in quotas.items()
        },
    }
