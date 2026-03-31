"""Quota model for tenant-level resource limits."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Quota(Base):
    """Tenant resource quotas.

    Enforced at creation time for nodes, jobs, and connectors.
    Defaults are generous; admins can tighten per-tenant.
    """

    __tablename__ = "quotas"
    __table_args__ = (
        Index("ix_quotas_tenant_resource", "tenant_id", "resource_type", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # resource_type: nodes | jobs_per_hour | connectors | jobs_concurrent

    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    # -1 = unlimited

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


# Default limits applied when no explicit quota exists
DEFAULT_QUOTAS: dict[str, int] = {
    "nodes": 50,
    "connectors": 100,
    "jobs_concurrent": 200,
    "jobs_per_hour": 10_000,
    "jobs_per_kind": 100,  # per-kind concurrent jobs (key pattern: jobs_per_kind:<kind>)
}
