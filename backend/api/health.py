"""
ZEN70 Health Pack – Ingestion API.

Provides data ingestion and query endpoints for health measurements
submitted by native iOS/Android clients via HealthKit / Health Connect.

Architecture boundary:
  - Health data collection happens on the native client (Swift/Kotlin)
  - This router receives pre-processed measurements via HTTPS
  - No HealthKit/Health Connect SDK dependency in the Python runtime
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_tenant_db

router = APIRouter(prefix="/api/v1/health", tags=["health"])


# ── Request / Response Models ────────────────────────────────────────

VALID_METRIC_TYPES = frozenset(
    {
        "steps",
        "heart_rate",
        "sleep",
        "blood_oxygen",
        "weight",
        "blood_pressure",
        "calories",
        "distance",
        "active_minutes",
        "body_temperature",
        "respiratory_rate",
    }
)


class HealthMeasurement(BaseModel):
    metric_type: str = Field(..., description="Type of measurement (steps, heart_rate, sleep, ...)")
    value: float = Field(..., description="Measured value")
    unit: str = Field(..., max_length=32, description="Unit of measurement (count, bpm, minutes, ...)")
    recorded_at: datetime.datetime = Field(..., description="When the measurement was taken (device time)")
    source_platform: Literal["ios", "android", "manual"] | None = None
    source_app: str | None = Field(None, max_length=128)
    meta_info: dict | None = None


class HealthIngestRequest(BaseModel):
    measurements: list[HealthMeasurement] = Field(..., min_length=1, max_length=500)
    node_id: str | None = Field(None, description="Source device node ID (optional)")


class HealthRecordResponse(BaseModel):
    id: int
    metric_type: str
    value: float
    unit: str
    recorded_at: str
    ingested_at: str
    source_platform: str | None
    source_app: str | None


class HealthIngestResponse(BaseModel):
    ingested: int
    rejected: int
    errors: list[str] = Field(default_factory=list)


class HealthSummaryItem(BaseModel):
    metric_type: str
    count: int
    min_value: float
    max_value: float
    avg_value: float
    latest_at: str


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/ingest", response_model=HealthIngestResponse)
async def ingest_health_data(
    payload: HealthIngestRequest,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> HealthIngestResponse:
    """Ingest health measurements from native clients.

    Accepts a batch of measurements and stores them in the
    health_records table. Invalid metric types are rejected
    but do not fail the entire batch.
    """
    from backend.models.health_record import HealthRecord

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    tenant_id = str(current_user.get("tenant_id") or "default")
    user_id = str(current_user.get("sub") or "")
    ingested = 0
    rejected = 0
    errors: list[str] = []

    for m in payload.measurements:
        if m.metric_type not in VALID_METRIC_TYPES:
            rejected += 1
            errors.append(f"Unknown metric_type: {m.metric_type}")
            continue

        record = HealthRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            node_id=payload.node_id,
            metric_type=m.metric_type,
            value=m.value,
            unit=m.unit,
            recorded_at=m.recorded_at.replace(tzinfo=None) if m.recorded_at.tzinfo else m.recorded_at,
            ingested_at=now,
            source_platform=m.source_platform,
            source_app=m.source_app,
            meta_info=m.meta_info,
        )
        db.add(record)
        ingested += 1

    if ingested > 0:
        await db.flush()

    return HealthIngestResponse(ingested=ingested, rejected=rejected, errors=errors)


@router.get("/records", response_model=list[HealthRecordResponse])
async def list_health_records(
    metric_type: str | None = Query(None),
    since: datetime.datetime | None = Query(None),
    until: datetime.datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[HealthRecordResponse]:
    """List health records for the current user with optional filters."""
    from backend.models.health_record import HealthRecord

    tenant_id = str(current_user.get("tenant_id") or "default")
    user_id = str(current_user.get("sub") or "")

    query = (
        select(HealthRecord)
        .where(
            HealthRecord.tenant_id == tenant_id,
            HealthRecord.user_id == user_id,
        )
        .order_by(HealthRecord.recorded_at.desc())
        .limit(limit)
    )
    if metric_type:
        query = query.where(HealthRecord.metric_type == metric_type)
    if since:
        query = query.where(HealthRecord.recorded_at >= since)
    if until:
        query = query.where(HealthRecord.recorded_at <= until)

    result = await db.execute(query)
    records = result.scalars().all()

    return [
        HealthRecordResponse(
            id=r.id,
            metric_type=r.metric_type,
            value=r.value,
            unit=r.unit,
            recorded_at=r.recorded_at.isoformat(),
            ingested_at=r.ingested_at.isoformat(),
            source_platform=r.source_platform,
            source_app=r.source_app,
        )
        for r in records
    ]


@router.get("/summary", response_model=list[HealthSummaryItem])
async def health_summary(
    since: datetime.datetime | None = Query(None),
    until: datetime.datetime | None = Query(None),
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[HealthSummaryItem]:
    """Get aggregated health summary by metric type for the current user."""
    from backend.models.health_record import HealthRecord

    tenant_id = str(current_user.get("tenant_id") or "default")
    user_id = str(current_user.get("sub") or "")

    query = (
        select(
            HealthRecord.metric_type,
            func.count().label("count"),
            func.min(HealthRecord.value).label("min_value"),
            func.max(HealthRecord.value).label("max_value"),
            func.avg(HealthRecord.value).label("avg_value"),
            func.max(HealthRecord.recorded_at).label("latest_at"),
        )
        .where(
            HealthRecord.tenant_id == tenant_id,
            HealthRecord.user_id == user_id,
        )
        .group_by(HealthRecord.metric_type)
    )
    if since:
        query = query.where(HealthRecord.recorded_at >= since)
    if until:
        query = query.where(HealthRecord.recorded_at <= until)

    result = await db.execute(query)
    rows = result.all()

    return [
        HealthSummaryItem(
            metric_type=row.metric_type,
            count=int(str(row.count)),
            min_value=row.min_value,
            max_value=row.max_value,
            avg_value=float(row.avg_value),
            latest_at=row.latest_at.isoformat() if row.latest_at else "",
        )
        for row in rows
    ]
