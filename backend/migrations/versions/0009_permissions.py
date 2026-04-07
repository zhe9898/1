"""Add permissions table

Revision ID: 0009_permissions
Revises: 0008_audit_logs
Create Date: 2026-03-28

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_permissions"
down_revision = "0008_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create permissions table
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("granted_by", sa.String(128), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "scope", "resource_type", "resource_id", name="ux_permissions_unique"),
    )

    # Create indexes
    op.create_index("ix_permissions_tenant_id", "permissions", ["tenant_id"])
    op.create_index("ix_permissions_user_id", "permissions", ["user_id"])
    op.create_index("ix_permissions_scope", "permissions", ["scope"])
    op.create_index("ix_permissions_tenant_user", "permissions", ["tenant_id", "user_id"])
    op.create_index("ix_permissions_tenant_scope", "permissions", ["tenant_id", "scope"])
    op.create_index("ix_permissions_expires_at", "permissions", ["expires_at"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_permissions_expires_at", "permissions")
    op.drop_index("ix_permissions_tenant_scope", "permissions")
    op.drop_index("ix_permissions_tenant_user", "permissions")
    op.drop_index("ix_permissions_scope", "permissions")
    op.drop_index("ix_permissions_user_id", "permissions")
    op.drop_index("ix_permissions_tenant_id", "permissions")

    # Drop table
    op.drop_table("permissions")
