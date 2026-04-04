"""Add feature_flags and system_config tables

Revision ID: 0017_feature_flags_system_config
Revises: 0016_connectors
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_feature_flags_system_config"
down_revision = "0016_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False, server_default="general"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_feature_flags_category", "feature_flags", ["category"])

    op.create_table(
        "system_config",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_config")
    op.drop_index("ix_feature_flags_category", "feature_flags")
    op.drop_table("feature_flags")
