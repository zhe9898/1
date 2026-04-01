"""Workflow (DAG) model for multi-step job orchestration."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Workflow(Base):
    """A named DAG template defining ordered job steps.

    steps: list of step definitions:
    [
      {
        "id": "step_a",
        "kind": "shell.exec",
        "payload": {"command": "echo hello"},
        "depends_on": []
      },
      {
        "id": "step_b",
        "kind": "http.request",
        "payload": {"url": "..."},
        "depends_on": ["step_a"]
      }
    ]

    Gateway owns: DAG validation, step ordering, dependency resolution.
    Runner owns:  actual step execution (via Job dispatch).
    """

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_id", name="ux_workflows_tenant_id"),
        Index("ix_workflows_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)
    # DAG step definitions (see docstring)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    # pending | running | completed | failed | canceled
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Shared context passed between steps (outputs of completed steps)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


class WorkflowStep(Base):
    """Tracks execution state of each step within a workflow run."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id_fk", "step_id", name="ux_workflow_steps_id"),
        Index("ix_workflow_steps_workflow_status", "workflow_id_fk", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id_fk: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # FK to workflows.id (no SQLAlchemy FK for schema flexibility)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # The Job dispatched for this step
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting", index=True)
    # waiting | pending | running | completed | failed | skipped
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
