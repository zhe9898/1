"""Add assets table

Revision ID: 0021_assets
Revises: 0020_triggers
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_assets"
down_revision = "0020_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("asset_type", sa.String(32), nullable=True),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column("camera", sa.String(128), nullable=True),
        sa.Column("event_id", sa.String(128), nullable=True),
        sa.Column("embedding_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("ai_tags", sa.JSON(), nullable=True),
        sa.Column("is_emotion_highlight", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_tenant_id", "assets", ["tenant_id"])
    op.create_index("ix_assets_asset_type", "assets", ["asset_type"])
    op.create_index("ix_assets_event_id", "assets", ["event_id"])
    op.create_index("ix_assets_embedding_status", "assets", ["embedding_status"])
    op.create_index("ix_assets_is_deleted", "assets", ["is_deleted"])


def downgrade() -> None:
    op.drop_index("ix_assets_is_deleted", "assets")
    op.drop_index("ix_assets_embedding_status", "assets")
    op.drop_index("ix_assets_event_id", "assets")
    op.drop_index("ix_assets_asset_type", "assets")
    op.drop_index("ix_assets_tenant_id", "assets")
    op.drop_table("assets")
