"""Backfill node, attempt, and workflow-step statuses to canonical values."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0030_canonical_node_attempt_step_statuses"
down_revision = "0029_canonical_job_statuses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("nodes"):
        op.execute(sa.text("UPDATE nodes SET enrollment_status = 'approved' WHERE enrollment_status = 'active'"))
        op.execute(sa.text("UPDATE nodes SET enrollment_status = 'rejected' WHERE enrollment_status = 'revoked'"))
    if guard.has_table("job_attempts"):
        op.execute(sa.text("UPDATE job_attempts SET status = 'timeout' WHERE status = 'expired'"))
    if guard.has_table("workflow_steps"):
        op.execute(sa.text("UPDATE workflow_steps SET status = 'waiting' WHERE status = 'pending'"))


def downgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("nodes"):
        op.execute(sa.text("UPDATE nodes SET enrollment_status = 'active' WHERE enrollment_status = 'approved'"))
        op.execute(sa.text("UPDATE nodes SET enrollment_status = 'revoked' WHERE enrollment_status = 'rejected'"))
    if guard.has_table("job_attempts"):
        op.execute(sa.text("UPDATE job_attempts SET status = 'expired' WHERE status = 'timeout'"))
    if guard.has_table("workflow_steps"):
        op.execute(sa.text("UPDATE workflow_steps SET status = 'pending' WHERE status = 'waiting'"))

