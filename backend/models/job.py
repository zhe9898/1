from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Job(Base):
    """Control-plane jobs dispatched to runner agents."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "ux_jobs_tenant_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    job_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    connector_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50, index=True)
    queue_class: Mapped[str] = mapped_column(String(32), nullable=False, default="batch", index=True)
    worker_pool: Mapped[str] = mapped_column(String(64), nullable=False, default="batch", index=True)
    target_os: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_arch: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_executor: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_zone: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    required_cpu_cores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_gpu_vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_storage_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    estimated_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Edge computing scheduling factors
    data_locality_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    max_network_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefer_cached_data: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    power_budget_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thermal_sensitivity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cloud_fallback_enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    preferred_device_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Scheduling strategy and affinity
    scheduling_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    affinity_labels: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    affinity_rule: Mapped[str | None] = mapped_column(String(32), nullable=True)
    anti_affinity_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Business scheduling
    parent_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    depends_on: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    gang_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    batch_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    preemptible: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    deadline_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="console")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leased_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    retry_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
