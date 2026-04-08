"""Add connectors table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0016_connectors"
down_revision = "0015_tenants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())

    if not guard.has_table("connectors"):
        op.create_table(
            "connectors",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("connector_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("kind", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
            sa.Column("endpoint", sa.String(255), nullable=True),
            sa.Column("profile", sa.String(64), nullable=False, server_default="manual"),
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_test_ok", sa.Boolean(), nullable=True),
            sa.Column("last_test_status", sa.String(32), nullable=True),
            sa.Column("last_test_message", sa.String(255), nullable=True),
            sa.Column("last_test_at", sa.DateTime(), nullable=True),
            sa.Column("last_invoke_status", sa.String(32), nullable=True),
            sa.Column("last_invoke_message", sa.String(255), nullable=True),
            sa.Column("last_invoke_job_id", sa.String(128), nullable=True),
            sa.Column("last_invoke_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "connector_id", name="ux_connectors_tenant_connector_id"),
        )
        guard.refresh()
    else:
        for column in (
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("connector_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False, server_default=""),
            sa.Column("kind", sa.String(64), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
            sa.Column("endpoint", sa.String(255), nullable=True),
            sa.Column("profile", sa.String(64), nullable=False, server_default="manual"),
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_test_ok", sa.Boolean(), nullable=True),
            sa.Column("last_test_status", sa.String(32), nullable=True),
            sa.Column("last_test_message", sa.String(255), nullable=True),
            sa.Column("last_test_at", sa.DateTime(), nullable=True),
            sa.Column("last_invoke_status", sa.String(32), nullable=True),
            sa.Column("last_invoke_message", sa.String(255), nullable=True),
            sa.Column("last_invoke_job_id", sa.String(128), nullable=True),
            sa.Column("last_invoke_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ):
            guard.add_column_if_missing("connectors", column)

    guard.create_unique_constraint_if_missing("connectors", "ux_connectors_tenant_connector_id", ["tenant_id", "connector_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_connector_id", ["connector_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_kind", ["kind"])
    guard.create_index_if_missing("connectors", "ix_connectors_status", ["status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("connectors"):
        return
    op.drop_index("ix_connectors_status", "connectors")
    op.drop_index("ix_connectors_kind", "connectors")
    op.drop_index("ix_connectors_connector_id", "connectors")
    op.drop_index("ix_connectors_tenant_id", "connectors")
    op.drop_table("connectors")
