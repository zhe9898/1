"""Add user status management fields

Revision ID: 0007_user_status
Revises: 0006_failure_taxonomy
Create Date: 2026-03-28

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_user_status"
down_revision = "0006_failure_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add user status management fields
    op.add_column("users", sa.Column("status", sa.String(32), nullable=False, server_default="active"))
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("suspended_by", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("suspended_reason", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    # Create index on status
    op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    # Drop index
    op.drop_index("ix_users_status", "users")

    # Drop columns
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "suspended_reason")
    op.drop_column("users", "suspended_by")
    op.drop_column("users", "suspended_at")
    op.drop_column("users", "status")
