"""Add failure taxonomy and attempt tracking."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0006_failure_taxonomy"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add failure_category, attempt_count, and drain_until fields."""
    guard = SchemaGuard(op.get_bind())

    guard.add_column_if_missing("jobs", sa.Column("failure_category", sa.String(32), nullable=True))
    guard.create_index_if_missing("jobs", "idx_jobs_failure_category", ["failure_category"])
    guard.add_column_if_missing(
        "jobs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )

    guard.add_column_if_missing("job_attempts", sa.Column("failure_category", sa.String(32), nullable=True))
    guard.create_index_if_missing("job_attempts", "idx_job_attempts_failure_category", ["failure_category"])

    guard.add_column_if_missing("nodes", sa.Column("drain_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("nodes"):
        op.drop_column("nodes", "drain_until")

    if sa.inspect(op.get_bind()).has_table("job_attempts"):
        op.drop_index("idx_job_attempts_failure_category", "job_attempts")
        op.drop_column("job_attempts", "failure_category")

    if sa.inspect(op.get_bind()).has_table("jobs"):
        op.drop_column("jobs", "attempt_count")
        op.drop_index("idx_jobs_failure_category", "jobs")
        op.drop_column("jobs", "failure_category")

