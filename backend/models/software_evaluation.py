from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class SoftwareEvaluation(Base):
    """Software evaluation records for all branches and all software components."""

    __tablename__ = "software_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "evaluation_id", name="ux_software_evaluations_tenant_eval_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    evaluation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    software_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="main", index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general", index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluator: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted", index=True)
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
