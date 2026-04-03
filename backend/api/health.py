"""Health ingestion and query APIs."""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_redis, get_tenant_db
from backend.core.redis_client import RedisClient

router = APIRouter(prefix="/api/v1/health", tags=["health"])

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
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional client key for deduplicating retried ingest requests",
    )


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


def _normalized_iso(value: datetime.datetime) -> str:
    normalized = value.astimezone(datetime.UTC) if value.tzinfo else value
    return normalized.replace(tzinfo=None).isoformat()


def _measurement_fingerprint(measurement: HealthMeasurement) -> str:
    return "|".join(
        [
            measurement.metric_type,
            f"{measurement.value}",
            measurement.unit,
            _normalized_iso(measurement.recorded_at),
            measurement.source_platform or "",
            measurement.source_app or "",
            json.dumps(measurement.meta_info or {}, sort_keys=True, separators=(",", ":")),
        ]
    )


def _ingest_fingerprint(tenant_id: str, user_id: str, payload: HealthIngestRequest) -> str:
    canonical = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "node_id": payload.node_id or "",
        "measurements": [_measurement_fingerprint(m) for m in payload.measurements],
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@router.post("/ingest", response_model=HealthIngestResponse)
async def ingest_health_data(
    payload: HealthIngestRequest,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> HealthIngestResponse:
    """Ingest health measurements from native clients."""
    from backend.models.health_record import HealthRecord

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    tenant_id = str(current_user.get("tenant_id") or "default")
    user_id = str(current_user.get("sub") or "")
    ingested = 0
    rejected = 0
    errors: list[str] = []

    dedupe_key = payload.idempotency_key or _ingest_fingerprint(tenant_id, user_id, payload)
    if redis is not None:
        cache_key = f"health:ingest:{tenant_id}:{user_id}:{dedupe_key}"
        accepted = await redis.set(cache_key, now.isoformat(), nx=True, ex=3600)
        if not accepted:
            return HealthIngestResponse(ingested=0, rejected=0, errors=["Duplicate ingest batch ignored"])

    seen: set[str] = set()

    for measurement in payload.measurements:
        if measurement.metric_type not in VALID_METRIC_TYPES:
            rejected += 1
            errors.append(f"Unknown metric_type: {measurement.metric_type}")
            continue

        fingerprint = _measurement_fingerprint(measurement)
        if fingerprint in seen:
            rejected += 1
            errors.append(f"Duplicate measurement in batch: {measurement.metric_type}@{_normalized_iso(measurement.recorded_at)}")
            continue
        seen.add(fingerprint)

        record = HealthRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            node_id=payload.node_id,
            metric_type=measurement.metric_type,
            value=measurement.value,
            unit=measurement.unit,
            recorded_at=measurement.recorded_at.replace(tzinfo=None) if measurement.recorded_at.tzinfo else measurement.recorded_at,
            ingested_at=now,
            source_platform=measurement.source_platform,
            source_app=measurement.source_app,
            meta_info=measurement.meta_info,
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
            id=record.id,
            metric_type=record.metric_type,
            value=record.value,
            unit=record.unit,
            recorded_at=record.recorded_at.isoformat(),
            ingested_at=record.ingested_at.isoformat(),
            source_platform=record.source_platform,
            source_app=record.source_app,
        )
        for record in records
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
