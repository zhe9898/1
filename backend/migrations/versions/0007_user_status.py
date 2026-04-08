"""Add user status management fields."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0007_user_status"
down_revision = "0006_failure_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())

    guard.add_column_if_missing(
        "users",
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
    )
    guard.add_column_if_missing("users", sa.Column("suspended_at", sa.DateTime(), nullable=True))
    guard.add_column_if_missing("users", sa.Column("suspended_by", sa.String(128), nullable=True))
    guard.add_column_if_missing("users", sa.Column("suspended_reason", sa.String(255), nullable=True))
    guard.add_column_if_missing("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    guard.create_index_if_missing("users", "ix_users_status", ["status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("users"):
        return
    op.drop_index("ix_users_status", "users")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "suspended_reason")
    op.drop_column("users", "suspended_by")
    op.drop_column("users", "suspended_at")
    op.drop_column("users", "status")

