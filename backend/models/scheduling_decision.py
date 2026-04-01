"""Scheduling Decision Audit Trail — per-dispatch trace records.

Every ``pull_jobs`` dispatch cycle writes a summary record so that
operators can answer "why was job X placed on node Y?" weeks later.

The model stores:
- Which node pulled, how many candidates, how many selected
- The winning job+score+breakdown for each placement
- Any policy rejections or preemptions that occurred
- Timing for the dispatch cycle

Retention: governed by standard audit-log rotation (default 90 days).
"""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class SchedulingDecision(Base):
    """Single dispatch cycle audit record."""

    __tablename__ = "scheduling_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Dispatch cycle metadata
    cycle_ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        index=True,
    )
    candidates_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preemptions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Policy trace
    placement_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    policy_rejections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Winning placements: [{job_id, score, breakdown, eligible_nodes}]
    placements_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    # Rejection reasons: [{job_id, reason}]
    rejections_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    # Timing
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Free-form context (burst_active, circuit_states, etc.)
    context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
