"""Add job_logs table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0018_job_logs"
down_revision = "0017_feature_flags_system_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())

    if not guard.has_table("job_logs"):
        op.create_table(
            "job_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("job_id", sa.String(128), nullable=False),
            sa.Column("level", sa.String(16), nullable=False, server_default="info"),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        guard.refresh()
    else:
        for column in (
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("job_id", sa.String(128), nullable=False),
            sa.Column("level", sa.String(16), nullable=False, server_default="info"),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ):
            guard.add_column_if_missing("job_logs", column)

    guard.create_index_if_missing("job_logs", "ix_job_logs_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("job_logs", "ix_job_logs_job_id", ["job_id"])
    guard.create_foreign_key_if_missing(
        "job_logs",
        "fk_job_logs_job_id_jobs",
        "jobs",
        ["job_id"],
        ["job_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("job_logs"):
        return
    op.drop_index("ix_job_logs_job_id", "job_logs")
    op.drop_index("ix_job_logs_tenant_id", "job_logs")
    op.drop_table("job_logs")

