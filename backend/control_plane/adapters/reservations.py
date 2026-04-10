from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_redis, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.job import Job
from backend.models.node import Node
from backend.platform.redis.client import CHANNEL_RESERVATION_EVENTS, RedisClient
from backend.runtime.scheduling.backfill_scheduling import ResourceReservation, get_reservation_manager
from backend.runtime.scheduling.job_scheduler import build_node_snapshot
from backend.runtime.scheduling.reservation_runtime import estimate_node_next_slot_at

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _normalize_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


class ReservationResponse(BaseModel):
    job_id: str
    node_id: str
    start_at: datetime.datetime
    end_at: datetime.datetime
    priority: int
    cpu_cores: float
    memory_mb: float
    gpu_vram_mb: float
    slots: int


class ReservationCreateRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=128)
    node_id: str = Field(..., min_length=1, max_length=128)
    start_at: datetime.datetime
    estimated_duration_s: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=255)


class ReservationCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ReservationStatsResponse(BaseModel):
    tenant_id: str
    active_reservations: int
    store_backend: str
    node_counts: dict[str, int] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class BackfillWindowResponse(BaseModel):
    node_id: str
    required_duration_s: int
    estimated_node_available_at: datetime.datetime
    window_start_at: datetime.datetime | None = None
    window_end_at: datetime.datetime | None = None
    reservation_count: int
    has_window: bool


def _to_reservation_response(reservation: ResourceReservation) -> ReservationResponse:
    return ReservationResponse(
        job_id=reservation.job_id,
        node_id=reservation.node_id,
        start_at=reservation.start_at,
        end_at=reservation.end_at,
        priority=reservation.priority,
        cpu_cores=reservation.cpu_cores,
        memory_mb=reservation.memory_mb,
        gpu_vram_mb=reservation.gpu_vram_mb,
        slots=reservation.slots,
    )


async def _publish_reservation_event(
    redis: RedisClient | None,
    action: str,
    reservation: ResourceReservation,
    *,
    reason: str | None = None,
    source: str = "api",
) -> None:
    payload: dict[str, Any] = {
        "reservation": reservation.to_dict(),
        "source": source,
    }
    if reason:
        payload["reason"] = reason
    await publish_control_event(CHANNEL_RESERVATION_EVENTS, action, payload, tenant_id=reservation.tenant_id)


async def _get_job_for_tenant(db: AsyncSession, tenant_id: str, job_id: str) -> Job:
    result = await db.execute(select(Job).where(Job.tenant_id == tenant_id, Job.job_id == job_id))
    job = result.scalars().first()
    if job is None:
        raise zen(
            "ZEN-RES-4040",
            "Job not found",
            status_code=404,
            recovery_hint="Refresh the job list and retry",
            details={"job_id": job_id},
        )
    return job


async def _get_node_for_tenant(db: AsyncSession, tenant_id: str, node_id: str) -> Node:
    result = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id))
    node = result.scalars().first()
    if node is None:
        raise zen(
            "ZEN-RES-4041",
            "Node not found",
            status_code=404,
            recovery_hint="Refresh the node list and retry",
            details={"node_id": node_id},
        )
    return node


@router.get("/stats", response_model=ReservationStatsResponse)
async def get_reservation_stats(
    current_user: dict[str, object] = Depends(get_current_user),
) -> ReservationStatsResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    manager = get_reservation_manager()
    reservations = manager.list_reservations(tenant_id=tenant_id)
    node_counts = dict(sorted(Counter(r.node_id for r in reservations).items()))
    return ReservationStatsResponse(
        tenant_id=tenant_id,
        active_reservations=len(reservations),
        store_backend=manager.store_backend,
        node_counts=node_counts,
        config=asdict(manager.config),
    )


@router.get("/nodes/{node_id}/backfill-window", response_model=BackfillWindowResponse)
async def get_backfill_window(
    node_id: str,
    required_duration_s: int = Query(..., ge=1, le=86400),
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> BackfillWindowResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    node = await _get_node_for_tenant(db, tenant_id, node_id)
    manager = get_reservation_manager()
    now = _utcnow()

    leased_result = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.node_id == node_id,
            Job.status == "leased",
        )
    )
    active_jobs = list(leased_result.scalars().all())
    node_snapshot = build_node_snapshot(
        node,
        active_lease_count=len(active_jobs),
        reliability_score=1.0,
    )
    available_at = estimate_node_next_slot_at(
        node_snapshot,
        active_jobs,
        now=now,
        default_duration_s=manager.config.default_estimated_duration_s,
    )
    window = manager.find_backfill_window(
        node_snapshot,
        tenant_id=tenant_id,
        now=available_at,
        required_duration_s=required_duration_s,
    )
    reservations = manager.get_node_reservations(node_id, tenant_id=tenant_id, after=now)
    if window is None:
        return BackfillWindowResponse(
            node_id=node_id,
            required_duration_s=required_duration_s,
            estimated_node_available_at=available_at,
            reservation_count=len(reservations),
            has_window=False,
        )
    return BackfillWindowResponse(
        node_id=node_id,
        required_duration_s=required_duration_s,
        estimated_node_available_at=available_at,
        window_start_at=window[0],
        window_end_at=window[1],
        reservation_count=len(reservations),
        has_window=True,
    )


@router.get("", response_model=list[ReservationResponse])
async def list_reservations(
    node_id: str | None = Query(default=None),
    after: datetime.datetime | None = Query(default=None),
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[ReservationResponse]:
    tenant_id = require_current_user_tenant_id(current_user)
    manager = get_reservation_manager()
    normalized_after = _normalize_utc(after) if after is not None else None
    reservations = manager.list_reservations(tenant_id=tenant_id, node_id=node_id, after=normalized_after)
    return [_to_reservation_response(reservation) for reservation in reservations]


@router.post("", response_model=ReservationResponse)
async def create_reservation(
    payload: ReservationCreateRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> ReservationResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    job = await _get_job_for_tenant(db, tenant_id, payload.job_id)
    node = await _get_node_for_tenant(db, tenant_id, payload.node_id)
    if job.status not in {"pending", "leased"}:
        raise zen(
            "ZEN-RES-4090",
            "Only pending or leased jobs can hold a reservation",
            status_code=409,
            recovery_hint="Retry only after the job returns to a schedulable state",
            details={"job_id": job.job_id, "status": job.status},
        )
    manager = get_reservation_manager()
    leased_result = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.node_id == payload.node_id,
            Job.status == "leased",
        )
    )
    active_leases = list(leased_result.scalars().all())
    reservation = manager.create_reservation(
        job,
        build_node_snapshot(node, active_lease_count=len(active_leases), reliability_score=1.0),
        start_at=_normalize_utc(payload.start_at),
        estimated_duration_s=payload.estimated_duration_s,
    )
    if reservation is None:
        raise zen(
            "ZEN-RES-4091",
            "Reservation table is at capacity",
            status_code=409,
            recovery_hint="Cancel stale reservations or increase backfill.max_reservations",
        )
    await _publish_reservation_event(redis, "created", reservation, reason=payload.reason or "manual_create")
    return _to_reservation_response(reservation)


@router.get("/{job_id}", response_model=ReservationResponse)
async def get_reservation(
    job_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> ReservationResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    reservation = get_reservation_manager().get_reservation(job_id)
    if reservation is None or reservation.tenant_id != tenant_id:
        raise zen(
            "ZEN-RES-4042",
            "Reservation not found",
            status_code=404,
            recovery_hint="Refresh the reservation list and retry",
            details={"job_id": job_id},
        )
    return _to_reservation_response(reservation)


@router.post("/{job_id}/cancel", response_model=ReservationResponse)
async def cancel_reservation(
    job_id: str,
    payload: ReservationCancelRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    redis: RedisClient | None = Depends(get_redis),
) -> ReservationResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    manager = get_reservation_manager()
    reservation = manager.get_reservation(job_id)
    if reservation is None or reservation.tenant_id != tenant_id:
        raise zen(
            "ZEN-RES-4042",
            "Reservation not found",
            status_code=404,
            recovery_hint="Refresh the reservation list and retry",
            details={"job_id": job_id},
        )
    if not manager.cancel_reservation(job_id):
        raise zen(
            "ZEN-RES-4092",
            "Reservation could not be canceled",
            status_code=409,
            recovery_hint="Retry after refreshing reservation state",
            details={"job_id": job_id},
        )
    await _publish_reservation_event(redis, "canceled", reservation, reason=payload.reason or "manual_cancel")
    return _to_reservation_response(reservation)
