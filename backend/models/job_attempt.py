from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class JobAttempt(Base):
    """Immutable-ish execution attempts for leased jobs."""

    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", "lease_token", name="ux_job_attempts_lease"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    attempt_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="leased", index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
