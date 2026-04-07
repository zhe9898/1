"""Add audit_logs table

Revision ID: 0008_audit_logs
Revises: 0007_user_status
Create Date: 2026-03-28

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_audit_logs"
down_revision = "0007_user_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
    op.create_index("ix_audit_logs_result", "audit_logs", ["result"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_tenant_user", "audit_logs", ["tenant_id", "user_id"])
    op.create_index("ix_audit_logs_tenant_action", "audit_logs", ["tenant_id", "action"])
    op.create_index("ix_audit_logs_tenant_resource", "audit_logs", ["tenant_id", "resource_type", "resource_id"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_audit_logs_tenant_resource", "audit_logs")
    op.drop_index("ix_audit_logs_tenant_action", "audit_logs")
    op.drop_index("ix_audit_logs_tenant_user", "audit_logs")
    op.drop_index("ix_audit_logs_created_at", "audit_logs")
    op.drop_index("ix_audit_logs_result", "audit_logs")
    op.drop_index("ix_audit_logs_resource_type", "audit_logs")
    op.drop_index("ix_audit_logs_action", "audit_logs")
    op.drop_index("ix_audit_logs_tenant_id", "audit_logs")

    # Drop table
    op.drop_table("audit_logs")
