"""Add job_logs table

Revision ID: 0018_job_logs
Revises: 0017_feature_flags_system_config
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_job_logs"
down_revision = "0017_feature_flags_system_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_logs_tenant_id", "job_logs", ["tenant_id"])
    op.create_index("ix_job_logs_job_id", "job_logs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_logs_job_id", "job_logs")
    op.drop_index("ix_job_logs_tenant_id", "job_logs")
    op.drop_table("job_logs")
