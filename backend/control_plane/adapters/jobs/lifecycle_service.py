from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.control_events import publish_control_event
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.platform.redis.client import CHANNEL_JOB_EVENTS, CHANNEL_RESERVATION_EVENTS, RedisClient
from backend.runtime.execution.failure_taxonomy import FailureCategory, infer_failure_category, should_retry_job
from backend.runtime.execution.job_lifecycle_service import JobLifecycleService
from backend.runtime.execution.job_status import normalize_job_status
from backend.runtime.execution.lease_service import LeaseService
from backend.runtime.scheduling.backfill_scheduling import get_reservation_manager
from backend.runtime.scheduling.failure_control_plane import get_failure_control_plane
from backend.runtime.topology.node_auth import authenticate_node_request

from .auto_tune_feedback import log_tuner_feedback_failure, record_job_outcome_for_tuner
from .database import (
    _append_log,
    _assert_valid_lease_owner,
    _get_attempt_for_callback,
    _get_current_attempt,
    _get_job_by_id,
    _get_job_by_id_for_update,
    move_to_dead_letter_queue,
)
from .helpers import _to_lease_response, _to_response, _utcnow
from .models import (
    JobActionRequest,
    JobFailRequest,
    JobLeaseResponse,
    JobProgressRequest,
    JobRenewRequest,
    JobResponse,
    JobResultRequest,
)


@dataclass(frozen=True, slots=True)
class JobLifecycleDependencies:
    authenticate_node_request: Callable[..., Awaitable[Any]]
    get_reservation_manager: Callable[[], Any]
    get_failure_control_plane: Callable[[], Any]
    infer_failure_category: Callable[..., Any]
    should_retry_job: Callable[..., bool]
    append_log: Callable[..., Awaitable[None]]
    assert_valid_lease_owner: Callable[..., None]
    get_attempt_for_callback: Callable[..., Awaitable[Any]]
    get_current_attempt: Callable[..., Awaitable[Any]]
    get_job_by_id: Callable[..., Awaitable[Any]]
    get_job_by_id_for_update: Callable[..., Awaitable[Any]]
    move_to_dead_letter_queue: Callable[..., Awaitable[None]]
    publish_control_event: Callable[..., Awaitable[None]]
    record_job_outcome_for_tuner: Callable[..., Awaitable[None]]
    log_tuner_feedback_failure: Callable[[str], None]
    to_response: Callable[..., JobResponse]
    to_lease_response: Callable[..., JobLeaseResponse]
    utcnow: Callable[[], datetime.datetime]


def build_default_job_lifecycle_dependencies() -> JobLifecycleDependencies:
    return JobLifecycleDependencies(
        authenticate_node_request=authenticate_node_request,
        get_reservation_manager=get_reservation_manager,
        get_failure_control_plane=get_failure_control_plane,
        infer_failure_category=infer_failure_category,
        should_retry_job=should_retry_job,
        append_log=_append_log,
        assert_valid_lease_owner=_assert_valid_lease_owner,
        get_attempt_for_callback=_get_attempt_for_callback,
        get_current_attempt=_get_current_attempt,
        get_job_by_id=_get_job_by_id,
        get_job_by_id_for_update=_get_job_by_id_for_update,
        move_to_dead_letter_queue=move_to_dead_letter_queue,
        publish_control_event=publish_control_event,
        record_job_outcome_for_tuner=record_job_outcome_for_tuner,
        log_tuner_feedback_failure=log_tuner_feedback_failure,
        to_response=_to_response,
        to_lease_response=_to_lease_response,
        utcnow=_utcnow,
    )


async def _cancel_job_reservation(
    redis: RedisClient | None,
    job_id: str,
    *,
    reason: str,
    deps: JobLifecycleDependencies,
) -> None:
    reservation_mgr = deps.get_reservation_manager()
    reservation = reservation_mgr.get_reservation(job_id)
    if reservation is None:
        return
    if not reservation_mgr.cancel_reservation(job_id):
        return
    await deps.publish_control_event(
        CHANNEL_RESERVATION_EVENTS,
        "canceled",
        {
            "reservation": reservation.to_dict(),
            "reason": reason,
            "source": "job_lifecycle",
        },
        tenant_id=getattr(reservation, "tenant_id", None),
    )


async def _record_tuner_feedback(
    db: AsyncSession,
    *,
    job: Any,
    attempt: Any,
    node_id: str,
    success: bool,
    now: datetime.datetime,
    deps: JobLifecycleDependencies,
) -> None:
    try:
        await deps.record_job_outcome_for_tuner(
            db,
            job=job,
            attempt=attempt,
            node_id=node_id,
            success=success,
            now=now,
        )
    except Exception:
        deps.log_tuner_feedback_failure(str(getattr(job, "job_id", "<unknown>") or "<unknown>"))
        raise


async def _authenticate_and_load_callback_job(
    id: str,
    payload: JobResultRequest | JobFailRequest | JobProgressRequest | JobRenewRequest,
    *,
    action: str,
    db: AsyncSession,
    node_token: str,
    deps: JobLifecycleDependencies,
    lock_job: bool,
) -> Any:
    await deps.authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=True,
        tenant_id=payload.tenant_id,
    )
    if lock_job:
        job = await deps.get_job_by_id_for_update(db, payload.tenant_id, id)
    else:
        job = await deps.get_job_by_id(db, payload.tenant_id, id)
    deps.assert_valid_lease_owner(job, payload, action)
    return job


async def _load_callback_attempt(
    db: AsyncSession,
    *,
    job: Any,
    payload: JobResultRequest | JobFailRequest | JobProgressRequest | JobRenewRequest,
    action: str,
    deps: JobLifecycleDependencies,
) -> Any:
    attempt = await deps.get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint=f"Pull a fresh job lease before reporting {action}",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )
    return attempt


async def complete_job_callback(
    id: str,
    payload: JobResultRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: JobLifecycleDependencies,
) -> JobResponse:
    job = await _authenticate_and_load_callback_job(
        id,
        payload,
        action="result",
        db=db,
        node_token=node_token,
        deps=deps,
        lock_job=True,
    )
    if job.status == "completed":
        return deps.to_response(job)
    attempt = await _load_callback_attempt(db, job=job, payload=payload, action="terminal state", deps=deps)

    now = deps.utcnow()
    await JobLifecycleService.complete_job(db, job=job, attempt=attempt, result=payload.result, now=now)
    await _cancel_job_reservation(redis, job.job_id, reason="completed", deps=deps)
    await deps.append_log(
        db,
        job.job_id,
        payload.log or f"job completed by {payload.node_id} attempt={payload.attempt}",
        tenant_id=job.tenant_id,
    )
    await _record_tuner_feedback(
        db,
        job=job,
        attempt=attempt,
        node_id=payload.node_id,
        success=True,
        now=now,
        deps=deps,
    )
    response = deps.to_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "completed",
        {"job": response.model_dump(mode="json")},
        tenant_id=job.tenant_id,
    )

    failure_control_plane = deps.get_failure_control_plane()
    await failure_control_plane.record_success(node_id=payload.node_id, now=now)
    job_kind = getattr(job, "kind", None)
    if job_kind:
        kind_state = await failure_control_plane.get_kind_circuit_state(job_kind, now=now)
        if kind_state == "half-open":
            await failure_control_plane.reset_kind_circuit(job_kind)
    return response


async def fail_job_callback(
    id: str,
    payload: JobFailRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: JobLifecycleDependencies,
) -> JobResponse:
    job = await _authenticate_and_load_callback_job(
        id,
        payload,
        action="fail",
        db=db,
        node_token=node_token,
        deps=deps,
        lock_job=True,
    )
    if job.status == "failed":
        return deps.to_response(job)
    attempt = await _load_callback_attempt(db, job=job, payload=payload, action="terminal state", deps=deps)

    now = deps.utcnow()
    failure_category_str = (
        payload.failure_category
        or deps.infer_failure_category(
            error_message=payload.error,
            exit_code=payload.error_details.get("exit_code") if payload.error_details else None,
            error_details=payload.error_details,
        ).value
    )

    failure_control_plane = deps.get_failure_control_plane()
    await failure_control_plane.record_failure(
        node_id=payload.node_id,
        job_id=job.job_id,
        category=failure_category_str,
        connector_id=getattr(job, "connector_id", None),
        kind=job.kind,
        now=now,
    )

    attempt.failure_category = failure_category_str
    failure_category = FailureCategory(failure_category_str)
    should_retry = deps.should_retry_job(job, failure_category)
    if should_retry:
        from backend.runtime.execution.failure_taxonomy import calculate_retry_delay_seconds

        retry_delay_seconds = calculate_retry_delay_seconds(failure_category, int(job.retry_count or 0))
        retry_at = now + datetime.timedelta(seconds=retry_delay_seconds)

        await JobLifecycleService.requeue_after_failure(
            db,
            job=job,
            attempt=attempt,
            error_message=payload.error,
            failure_category=failure_category_str,
            retry_at=retry_at,
            now=now,
        )
        await _cancel_job_reservation(redis, job.job_id, reason="requeued", deps=deps)
        await deps.append_log(
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
        await _record_tuner_feedback(
            db,
            job=job,
            attempt=attempt,
            node_id=payload.node_id,
            success=False,
            now=now,
            deps=deps,
        )
        response = deps.to_response(job, now=now)
        await db.commit()
        await deps.publish_control_event(
            CHANNEL_JOB_EVENTS,
            "requeued",
            {"job": response.model_dump(mode="json"), "failure_category": failure_category_str},
            tenant_id=job.tenant_id,
        )
        return response

    await JobLifecycleService.fail_job(
        db,
        job=job,
        attempt=attempt,
        error_message=payload.error,
        failure_category=failure_category_str,
        now=now,
    )
    await _cancel_job_reservation(redis, job.job_id, reason="failed", deps=deps)
    await deps.append_log(
        db,
        job.job_id,
        payload.log or f"job failed permanently on {payload.node_id} category={failure_category_str}",
        level="error",
        tenant_id=job.tenant_id,
    )
    await deps.move_to_dead_letter_queue(redis, db, job)
    await _record_tuner_feedback(
        db,
        job=job,
        attempt=attempt,
        node_id=payload.node_id,
        success=False,
        now=now,
        deps=deps,
    )
    response = deps.to_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "failed",
        {"job": response.model_dump(mode="json"), "failure_category": failure_category_str, "will_retry": False},
        tenant_id=job.tenant_id,
    )
    return response


async def report_job_progress_callback(
    id: str,
    payload: JobProgressRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: JobLifecycleDependencies,
) -> JobResponse:
    job = await _authenticate_and_load_callback_job(
        id,
        payload,
        action="progress",
        db=db,
        node_token=node_token,
        deps=deps,
        lock_job=False,
    )
    attempt = await _load_callback_attempt(db, job=job, payload=payload, action="progress", deps=deps)
    now = deps.utcnow()
    await LeaseService.mark_attempt_running(db, job=job, attempt=attempt, now=now)
    attempt.result_summary = {"progress": payload.progress, "message": payload.message}
    await db.flush()
    await deps.append_log(
        db,
        job.job_id,
        payload.log or f"progress={payload.progress}% node={payload.node_id} message={payload.message or '-'}",
        tenant_id=job.tenant_id,
    )
    response = deps.to_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "progress",
        {"job": response.model_dump(mode="json")},
        tenant_id=job.tenant_id,
    )
    return response


async def renew_job_lease_callback(
    id: str,
    payload: JobRenewRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: JobLifecycleDependencies,
) -> JobLeaseResponse:
    job = await _authenticate_and_load_callback_job(
        id,
        payload,
        action="renew",
        db=db,
        node_token=node_token,
        deps=deps,
        lock_job=True,
    )
    attempt = await _load_callback_attempt(db, job=job, payload=payload, action="renew", deps=deps)
    now = deps.utcnow()
    await LeaseService.renew_lease(db, job=job, attempt=attempt, now=now, extend_seconds=payload.extend_seconds)
    await deps.append_log(
        db,
        job.job_id,
        payload.log or f"lease renewed by {payload.node_id} extend={payload.extend_seconds}s",
        tenant_id=job.tenant_id,
    )
    response = deps.to_lease_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "renewed",
        {"job": deps.to_response(job, now=now).model_dump(mode="json")},
        tenant_id=job.tenant_id,
    )
    return response


async def cancel_job_by_operator(
    id: str,
    payload: JobActionRequest,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    redis: RedisClient | None,
    deps: JobLifecycleDependencies,
) -> JobResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    job = await deps.get_job_by_id_for_update(db, tenant_id, id)
    job_status = normalize_job_status(job.status) or "pending"
    if job_status == "cancelled":
        return deps.to_response(job)
    if job_status not in {"pending", "leased"}:
        raise zen(
            "ZEN-JOB-4094",
            "Only pending or leased jobs can be cancelled",
            status_code=409,
            recovery_hint="Retry only after the job returns to a cancelable state",
            details={"job_id": job.job_id, "status": job_status},
        )

    now = deps.utcnow()
    attempt = await deps.get_current_attempt(db, job)
    await JobLifecycleService.cancel_job(
        db,
        job=job,
        attempt=attempt,
        reason=payload.reason or "cancelled by operator",
        now=now,
    )
    await _cancel_job_reservation(redis, job.job_id, reason="cancelled", deps=deps)
    await deps.append_log(
        db,
        job.job_id,
        payload.reason or "job cancelled by operator",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = deps.to_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "canceled",
        {"job": response.model_dump(mode="json")},
        tenant_id=job.tenant_id,
    )
    return response


async def retry_job_by_operator(
    id: str,
    payload: JobActionRequest,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    redis: RedisClient | None,
    deps: JobLifecycleDependencies,
) -> JobResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    job = await deps.get_job_by_id_for_update(db, tenant_id, id)
    job_status = normalize_job_status(job.status) or "pending"
    if job_status == "pending":
        return deps.to_response(job)
    if job_status not in {"failed", "completed", "cancelled"}:
        raise zen(
            "ZEN-JOB-4095",
            "Only terminal jobs can be retried manually",
            status_code=409,
            recovery_hint="Cancel or wait for the current lease before retrying",
            details={"job_id": job.job_id, "status": job_status},
        )

    now = deps.utcnow()
    await JobLifecycleService.retry_job(db, job=job, now=now)
    await _cancel_job_reservation(redis, job.job_id, reason="manual_retry", deps=deps)
    await deps.append_log(
        db,
        job.job_id,
        payload.reason or "job queued for manual retry",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = deps.to_response(job, now=now)
    await db.commit()
    await deps.publish_control_event(
        CHANNEL_JOB_EVENTS,
        "manual-retry",
        {"job": response.model_dump(mode="json")},
        tenant_id=job.tenant_id,
    )
    return response
