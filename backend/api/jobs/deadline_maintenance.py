from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
from threading import Lock

from sqlalchemy import select

from backend.kernel.execution.job_lifecycle_service import JobLifecycleService
from backend.core.redis_client import RedisClient
from backend.core.rls import set_tenant_context
from backend.kernel.policy.policy_store import get_policy_store
from backend.models.job import Job

from .database import _append_log, move_to_dead_letter_queue

logger = logging.getLogger(__name__)

_state_lock = Lock()
_in_flight_tenants: set[str] = set()
_last_sweep_monotonic: dict[str, float] = {}


def _deadline_sweep_interval_seconds() -> float:
    raw = os.getenv("JOB_DEADLINE_SWEEP_INTERVAL_SECONDS", "15")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 15.0
    return max(value, 1.0)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def maybe_schedule_deadline_dlq_sweep(tenant_id: str, redis: RedisClient | None) -> None:
    """Schedule tenant-scoped deadline sweep outside pull request hot-path."""
    now = time.monotonic()
    interval = _deadline_sweep_interval_seconds()
    should_schedule = False
    with _state_lock:
        if tenant_id not in _in_flight_tenants:
            last = _last_sweep_monotonic.get(tenant_id, 0.0)
            if now - last >= interval:
                _in_flight_tenants.add(tenant_id)
                _last_sweep_monotonic[tenant_id] = now
                should_schedule = True
    if not should_schedule:
        return

    task = asyncio.create_task(
        _run_deadline_dlq_sweep(tenant_id, redis),
        name=f"jobs-deadline-sweep:{tenant_id}",
    )

    def _on_done(done_task: asyncio.Task[int]) -> None:
        with _state_lock:
            _in_flight_tenants.discard(tenant_id)
        exc = done_task.exception()
        if exc is not None:
            logger.warning("deadline DLQ sweep failed for tenant=%s: %s", tenant_id, exc)

    task.add_done_callback(_on_done)


async def _run_deadline_dlq_sweep(tenant_id: str, redis: RedisClient | None) -> int:
    from backend.db import _async_session_factory

    if _async_session_factory is None:
        return 0

    now = _utcnow()
    scan_limit = get_policy_store().active.dispatch.dlq_scan_limit
    moved = 0

    async with _async_session_factory() as db:
        try:
            await set_tenant_context(db, tenant_id)
            dlq_query = (
                select(Job)
                .where(
                    Job.tenant_id == tenant_id,
                    Job.status == "pending",
                    Job.deadline_at.is_not(None),
                    Job.deadline_at <= now,
                )
                .with_for_update(skip_locked=True)
                .limit(scan_limit)
            )
            dlq_result = await db.execute(dlq_query)
            expired_jobs = list(dlq_result.scalars().all())
            for job in expired_jobs:
                assert job.deadline_at is not None
                await JobLifecycleService.expire_deadline(db, job=job, now=now)
                await move_to_dead_letter_queue(redis, db, job)
                await _append_log(
                    db,
                    job.job_id,
                    f"deadline expired: moved to DLQ ({job.deadline_at.isoformat()})",
                    tenant_id=job.tenant_id,
                )
                moved += 1
            await db.commit()
            return moved
        except Exception:
            await db.rollback()
            raise


def _reset_deadline_sweep_state_for_tests() -> None:
    with _state_lock:
        _in_flight_tenants.clear()
        _last_sweep_monotonic.clear()
