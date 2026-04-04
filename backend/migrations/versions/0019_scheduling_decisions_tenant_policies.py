"""Add scheduling_decisions and tenant_scheduling_policies tables

Revision ID: 0019_scheduling_decisions_tenant_policies
Revises: 0018_job_logs
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_scheduling_decisions_tenant_policies"
down_revision = "0018_job_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduling_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("cycle_ts", sa.DateTime(), nullable=False),
        sa.Column("candidates_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preemptions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placement_policy", sa.String(64), nullable=False, server_default="default"),
        sa.Column("policy_rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placements_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("rejections_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduling_decisions_tenant_id", "scheduling_decisions", ["tenant_id"])
    op.create_index("ix_scheduling_decisions_node_id", "scheduling_decisions", ["node_id"])
    op.create_index("ix_scheduling_decisions_cycle_ts", "scheduling_decisions", ["cycle_ts"])

    op.create_table(
        "tenant_scheduling_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("service_class", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("max_jobs_per_round", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("fair_share_weight", sa.Float(), nullable=False, server_default="2.0"),
        sa.Column("priority_boost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("placement_policy", sa.String(64), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="ux_tenant_scheduling_policies_tenant_id"),
    )
    op.create_index("ix_tenant_scheduling_policies_tenant_id", "tenant_scheduling_policies", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_scheduling_policies_tenant_id", "tenant_scheduling_policies")
    op.drop_table("tenant_scheduling_policies")

    op.drop_index("ix_scheduling_decisions_cycle_ts", "scheduling_decisions")
    op.drop_index("ix_scheduling_decisions_node_id", "scheduling_decisions")
    op.drop_index("ix_scheduling_decisions_tenant_id", "scheduling_decisions")
    op.drop_table("scheduling_decisions")
