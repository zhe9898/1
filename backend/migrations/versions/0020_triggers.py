"""Add triggers and trigger_deliveries tables

Revision ID: 0020_triggers
Revises: 0019_scheduling_decisions_tenant_policies
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_triggers"
down_revision = "0019_scheduling_decisions_tenant_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("trigger_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("target", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("input_defaults", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_fired_at", sa.DateTime(), nullable=True),
        sa.Column("last_delivery_status", sa.String(32), nullable=True),
        sa.Column("last_delivery_message", sa.String(255), nullable=True),
        sa.Column("last_delivery_id", sa.String(128), nullable=True),
        sa.Column("last_delivery_target_kind", sa.String(64), nullable=True),
        sa.Column("last_delivery_target_id", sa.String(128), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "trigger_id", name="ux_triggers_tenant_trigger_id"),
    )
    op.create_index("ix_triggers_tenant_id", "triggers", ["tenant_id"])
    op.create_index("ix_triggers_trigger_id", "triggers", ["trigger_id"])
    op.create_index("ix_triggers_tenant_status", "triggers", ["tenant_id", "status"])
    op.create_index("ix_triggers_tenant_kind", "triggers", ["tenant_id", "kind"])
    op.create_index("ix_triggers_next_run_at", "triggers", ["next_run_at"])
    op.create_index("ix_triggers_created_at", "triggers", ["created_at"])

    op.create_table(
        "trigger_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("delivery_id", sa.String(128), nullable=False),
        sa.Column("trigger_id", sa.String(128), nullable=False),
        sa.Column("trigger_kind", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(32), nullable=False, server_default="dispatching"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("target_kind", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("target_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("fired_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="ux_trigger_deliveries_delivery_id"),
    )
    op.create_index("ix_trigger_deliveries_tenant_id", "trigger_deliveries", ["tenant_id"])
    op.create_index("ix_trigger_deliveries_delivery_id", "trigger_deliveries", ["delivery_id"])
    op.create_index("ix_trigger_deliveries_trigger_id", "trigger_deliveries", ["trigger_id"])
    op.create_index("ix_trigger_deliveries_trigger_kind", "trigger_deliveries", ["trigger_kind"])
    op.create_index("ix_trigger_deliveries_status", "trigger_deliveries", ["status"])
    op.create_index("ix_trigger_deliveries_fired_at", "trigger_deliveries", ["fired_at"])
    op.create_index("ix_trigger_deliveries_tenant_trigger", "trigger_deliveries", ["tenant_id", "trigger_id"])
    op.create_index("ix_trigger_deliveries_tenant_status", "trigger_deliveries", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_trigger_deliveries_tenant_status", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_tenant_trigger", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_fired_at", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_status", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_trigger_kind", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_trigger_id", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_delivery_id", "trigger_deliveries")
    op.drop_index("ix_trigger_deliveries_tenant_id", "trigger_deliveries")
    op.drop_table("trigger_deliveries")

    op.drop_index("ix_triggers_created_at", "triggers")
    op.drop_index("ix_triggers_next_run_at", "triggers")
    op.drop_index("ix_triggers_tenant_kind", "triggers")
    op.drop_index("ix_triggers_tenant_status", "triggers")
    op.drop_index("ix_triggers_trigger_id", "triggers")
    op.drop_index("ix_triggers_tenant_id", "triggers")
    op.drop_table("triggers")
