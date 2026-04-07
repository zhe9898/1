"""Add health_records table for health-pack ingestion

Revision ID: 0014_health_records
Revises: 0013_workflows
Create Date: 2026-03-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_health_records"
down_revision = "0013_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=True),
        sa.Column("metric_type", sa.String(64), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("source_platform", sa.String(32), nullable=True),
        sa.Column("source_app", sa.String(128), nullable=True),
        sa.Column("meta_info", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_health_records_tenant_id", "health_records", ["tenant_id"])
    op.create_index("ix_health_records_user_id", "health_records", ["user_id"])
    op.create_index("ix_health_records_tenant_user", "health_records", ["tenant_id", "user_id"])
    op.create_index("ix_health_records_metric_type", "health_records", ["metric_type"])
    op.create_index("ix_health_records_recorded_at", "health_records", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_health_records_recorded_at", table_name="health_records")
    op.drop_index("ix_health_records_metric_type", table_name="health_records")
    op.drop_index("ix_health_records_tenant_user", table_name="health_records")
    op.drop_index("ix_health_records_user_id", table_name="health_records")
    op.drop_index("ix_health_records_tenant_id", table_name="health_records")
    op.drop_table("health_records")
