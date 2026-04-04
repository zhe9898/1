"""Add connectors table

Revision ID: 0016_connectors
Revises: 0015_tenants
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_connectors"
down_revision = "0015_tenants"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "connector_id", name="ux_connectors_tenant_connector_id"),
    )
    op.create_index("ix_connectors_tenant_id", "connectors", ["tenant_id"])
    op.create_index("ix_connectors_connector_id", "connectors", ["connector_id"])
    op.create_index("ix_connectors_kind", "connectors", ["kind"])
    op.create_index("ix_connectors_status", "connectors", ["status"])


def downgrade() -> None:
    op.drop_index("ix_connectors_status", "connectors")
    op.drop_index("ix_connectors_kind", "connectors")
    op.drop_index("ix_connectors_connector_id", "connectors")
    op.drop_index("ix_connectors_tenant_id", "connectors")
    op.drop_table("connectors")
