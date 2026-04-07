"""Audit log model for tracking all identity and control plane operations."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class AuditLog(Base):
    """Audit log for security compliance and troubleshooting.

    Records all identity operations (login, logout, permission changes)
    and control plane operations (node registration, job creation, etc.).
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_user", "tenant_id", "user_id"),
        Index("ix_audit_logs_tenant_action", "tenant_id", "action"),
        Index("ix_audit_logs_tenant_resource", "tenant_id", "resource_type", "resource_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # None for system actions
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Actions: login, logout, create_job, update_node, suspend_user, etc.
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Resource types: user, job, node, connector, etc.
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Results: success, failure
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Additional context: old_value, new_value, reason, etc.
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
