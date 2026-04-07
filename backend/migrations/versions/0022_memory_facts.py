"""Add memory_facts table with pgvector support

Revision ID: 0022_memory_facts
Revises: 0021_assets
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0022_memory_facts"
down_revision = "0021_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure the pgvector extension is available; ignore if already present.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "memory_facts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_sub", sa.String(128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("deprecated", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
        sa.Column("vec384", ARRAY(sa.Float()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_facts_tenant_id", "memory_facts", ["tenant_id"])
    op.create_index("ix_memory_facts_user_sub", "memory_facts", ["user_sub"])


def downgrade() -> None:
    op.drop_index("ix_memory_facts_user_sub", "memory_facts")
    op.drop_index("ix_memory_facts_tenant_id", "memory_facts")
    op.drop_table("memory_facts")
