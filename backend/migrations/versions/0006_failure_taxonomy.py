"""Add failure taxonomy and attempt tracking

Revision ID: 0006_failure_taxonomy
Revises: 0005
Create Date: 2026-03-28

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_failure_taxonomy"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add failure_category, attempt_count, and drain_until fields."""

    # Add failure_category to jobs table
    op.add_column("jobs", sa.Column("failure_category", sa.String(32), nullable=True))
    op.create_index("idx_jobs_failure_category", "jobs", ["failure_category"])

    # Add attempt_count to jobs table
    op.add_column("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))

    # Add failure_category to job_attempts table
    op.add_column("job_attempts", sa.Column("failure_category", sa.String(32), nullable=True))
    op.create_index("idx_job_attempts_failure_category", "job_attempts", ["failure_category"])

    # Add drain_until to nodes table
    op.add_column("nodes", sa.Column("drain_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove failure taxonomy fields."""

    # Remove from nodes
    op.drop_column("nodes", "drain_until")

    # Remove from job_attempts
    op.drop_index("idx_job_attempts_failure_category", "job_attempts")
    op.drop_column("job_attempts", "failure_category")

    # Remove from jobs
    op.drop_column("jobs", "attempt_count")
    op.drop_index("idx_jobs_failure_category", "jobs")
    op.drop_column("jobs", "failure_category")
