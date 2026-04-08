"""Backfill job and job-attempt statuses to canonical values."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0029_canonical_job_statuses"
down_revision = "0028_canonical_trigger_workflow_statuses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("jobs"):
        op.execute(sa.text("UPDATE jobs SET status = 'cancelled' WHERE status = 'canceled'"))
    if guard.has_table("job_attempts"):
        op.execute(sa.text("UPDATE job_attempts SET status = 'cancelled' WHERE status = 'canceled'"))


def downgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("jobs"):
        op.execute(sa.text("UPDATE jobs SET status = 'canceled' WHERE status = 'cancelled'"))
    if guard.has_table("job_attempts"):
        op.execute(sa.text("UPDATE job_attempts SET status = 'canceled' WHERE status = 'cancelled'"))

