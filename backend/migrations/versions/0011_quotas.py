"""Add quotas table

Revision ID: 0011_quotas
Revises: 0010_sessions
Create Date: 2026-03-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_quotas"
down_revision = "0010_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quotas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quotas_tenant_resource", "quotas", ["tenant_id", "resource_type"], unique=True)
    op.create_index("ix_quotas_tenant_id", "quotas", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_quotas_tenant_id", "quotas")
    op.drop_index("ix_quotas_tenant_resource", "quotas")
    op.drop_table("quotas")
