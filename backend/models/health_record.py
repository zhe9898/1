"""Health record model for health-pack ingestion."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class HealthRecord(Base):
    """Tenant-scoped health measurement ingested from native clients.

    Designed for HealthKit (iOS) and Health Connect (Android) data
    submitted through the gateway's health ingestion endpoint.
    """

    __tablename__ = "health_records"
    __table_args__ = (
        Index("ix_health_records_tenant_user", "tenant_id", "user_id"),
        Index("ix_health_records_metric_type", "metric_type"),
        Index("ix_health_records_recorded_at", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Source device that submitted this measurement

    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "steps", "heart_rate", "sleep", "blood_oxygen", "weight", "blood_pressure"

    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    # e.g. "count", "bpm", "minutes", "percent", "kg", "mmHg"

    # When the measurement was recorded by the device
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        index=True,
    )
    # When the measurement was ingested by the gateway
    ingested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )

    source_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "ios" | "android" | "manual"

    source_app: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # e.g. "com.apple.Health", "com.google.android.apps.fitness"

    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Flexible metadata: device model, accuracy, context, etc.
