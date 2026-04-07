"""Permission model for fine-grained access control."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Permission(Base):
    """Fine-grained permissions for resource-level access control.

    Permissions are scopes like:
    - read:jobs, write:jobs, delete:jobs, admin:jobs
    - read:nodes, write:nodes, admin:nodes
    - read:connectors, write:connectors, admin:connectors

    Permissions can be:
    - Global: resource_type and resource_id are None
    - Type-level: resource_type is set, resource_id is None
    - Resource-level: both resource_type and resource_id are set
    """

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "scope", "resource_type", "resource_id", name="ux_permissions_unique"),
        Index("ix_permissions_tenant_user", "tenant_id", "user_id"),
        Index("ix_permissions_tenant_scope", "tenant_id", "scope"),
        Index("ix_permissions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Scopes: read:jobs, write:jobs, delete:jobs, admin:jobs, etc.
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Resource types: jobs, nodes, connectors, users, etc.
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Specific resource ID, or None for type-level permission
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    # Username of admin who granted this permission
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    # Optional expiration date for temporary permissions
