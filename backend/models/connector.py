from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Connector(Base):
    """Connector registry for external systems and future mobile clients."""

    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("tenant_id", "connector_id", name="ux_connectors_tenant_connector_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    connector_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured", index=True)
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    last_test_ok: Mapped[bool | None] = mapped_column(nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_test_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_invoke_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_invoke_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_invoke_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_invoke_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
