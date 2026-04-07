"""Add tenants table

Revision ID: 0015_tenants
Revises: 0014_health_records
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_tenants"
down_revision = "0014_health_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default="Home"),
        sa.Column("plan", sa.String(32), nullable=False, server_default="home"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_tenants_is_active", "tenants")
    op.drop_table("tenants")
