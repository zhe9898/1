"""Tenant Scheduling Policy — DB-backed governance replacing YAML-only quotas.

Upgrades the ``GlobalFairScheduler`` from reading system.yaml to a
DB-first model with YAML as seed/fallback.  Each tenant can have:

- ``service_class`` (premium / standard / economy / batch)
- ``max_jobs_per_round`` override
- ``fair_share_weight`` override
- ``priority_boost`` (shift applied to all jobs from this tenant)
- ``max_concurrent_jobs`` (hard cap on leased jobs)
- ``placement_policy`` (per-tenant policy override name)
- ``enabled`` flag (disable scheduling for a tenant without deletion)

The corresponding CRUD helpers live in ``core/scheduling_governance.py``.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class TenantSchedulingPolicy(Base):
    """Per-tenant scheduling governance record."""

    __tablename__ = "tenant_scheduling_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Service class (maps to SERVICE_CLASS_CONFIG in queue_stratification)
    service_class: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")

    # Fair-share parameters
    max_jobs_per_round: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    fair_share_weight: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)

    # Priority governance
    priority_boost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    # -1 = unlimited (defer to resource quotas)

    # Per-tenant placement override (empty = use system default)
    placement_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Administrative
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Version tracking — incremented on every policy change for audit trail
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
