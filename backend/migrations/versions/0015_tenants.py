"""Add tenants table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.migration_schema_guard import SchemaGuard

revision = "0015_tenants"
down_revision = "0014_health_records"
branch_labels = None
depends_on = None


def _insert_default_tenant() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO tenants (tenant_id, display_name, plan, is_active, created_at)
            VALUES ('default', 'Default Home', 'home', true, NOW())
            ON CONFLICT (tenant_id) DO NOTHING
            """
        )
    )


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())

    if not guard.has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("display_name", sa.String(128), nullable=False, server_default="Home"),
            sa.Column("plan", sa.String(32), nullable=False, server_default="home"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("metadata_json", JSONB(), nullable=True),
            sa.PrimaryKeyConstraint("tenant_id"),
        )
        guard.refresh()
    else:
        guard.add_column_if_missing(
            "tenants",
            sa.Column("display_name", sa.String(128), nullable=False, server_default="Home"),
        )
        guard.add_column_if_missing(
            "tenants",
            sa.Column("plan", sa.String(32), nullable=False, server_default="home"),
        )
        guard.add_column_if_missing(
            "tenants",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        guard.add_column_if_missing(
            "tenants",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        guard.add_column_if_missing("tenants", sa.Column("metadata_json", JSONB(), nullable=True))

    guard.create_index_if_missing("tenants", "ix_tenants_is_active", ["is_active"])
    _insert_default_tenant()


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("tenants"):
        return
    op.drop_index("ix_tenants_is_active", "tenants")
    op.drop_table("tenants")
