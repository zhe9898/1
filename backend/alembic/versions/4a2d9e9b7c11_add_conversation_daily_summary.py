"""Add conversation daily summary table

Revision ID: 4a2d9e9b7c11
Revises: 0b6c9c3f1a21
Create Date: 2026-03-18 00:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4a2d9e9b7c11"
down_revision: Union[str, Sequence[str], None] = "0b6c9c3f1a21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_daily_summaries",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("user_sub", sa.String(length=255), nullable=False),
        sa.Column("day_utc", sa.String(length=10), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("meta_info", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversation_daily_summaries_tenant_id",
        "conversation_daily_summaries",
        ["tenant_id"],
    )
    op.create_index(
        "ix_conversation_daily_summaries_user_sub",
        "conversation_daily_summaries",
        ["user_sub"],
    )
    op.create_index(
        "ix_conversation_daily_summaries_day_utc",
        "conversation_daily_summaries",
        ["day_utc"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_daily_summaries_day_utc",
        table_name="conversation_daily_summaries",
    )
    op.drop_index(
        "ix_conversation_daily_summaries_user_sub",
        table_name="conversation_daily_summaries",
    )
    op.drop_index(
        "ix_conversation_daily_summaries_tenant_id",
        table_name="conversation_daily_summaries",
    )
    op.drop_table("conversation_daily_summaries")
