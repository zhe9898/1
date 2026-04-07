from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Trigger(Base):
    """Unified trigger contract for timers, webhooks, and internal events."""

    __tablename__ = "triggers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "trigger_id", name="ux_triggers_tenant_trigger_id"),
        Index("ix_triggers_tenant_status", "tenant_id", "status"),
        Index("ix_triggers_tenant_kind", "tenant_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    trigger_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    target: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    input_defaults: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    last_fired_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_delivery_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_delivery_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_delivery_target_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_delivery_target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )


class TriggerDelivery(Base):
    """Trigger fire history and downstream dispatch results."""

    __tablename__ = "trigger_deliveries"
    __table_args__ = (
        Index("ix_trigger_deliveries_tenant_trigger", "tenant_id", "trigger_id"),
        Index("ix_trigger_deliveries_tenant_status", "tenant_id", "status"),
        Index(
            "ux_trigger_deliveries_tenant_trigger_idempotency",
            "tenant_id",
            "trigger_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    trigger_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="dispatching", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    context: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    target_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    fired_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
