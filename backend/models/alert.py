"""AlertRule and Alert models for monitoring."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class AlertRule(Base):
    """Configurable alert rule for monitoring nodes and jobs.

    Condition examples:
      {"type": "node_offline", "threshold_seconds": 120}
      {"type": "job_failure_rate", "threshold_pct": 20, "window_minutes": 60}
      {"type": "job_stuck", "threshold_seconds": 3600}
      {"type": "quota_pct", "resource": "nodes", "threshold_pct": 90}

    Action examples:
      {"type": "webhook", "url": "https://hooks.slack.com/..."}
      {"type": "log"}
    """

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_tenant_enabled", "tenant_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    action: Mapped[dict] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="warning", index=True)
    # severity: info | warning | error | critical
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
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


class Alert(Base):
    """Fired alert instance."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_tenant_rule", "tenant_id", "rule_id"),
        Index("ix_alerts_tenant_resolved", "tenant_id", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    triggered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
