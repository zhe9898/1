"""Add memory_facts table with array-based embeddings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from backend.core.migration_schema_guard import SchemaGuard

revision = "0022_memory_facts"
down_revision = "0021_assets"
branch_labels = None
depends_on = None


def _backfill_memory_text() -> None:
    op.execute(
        sa.text(
            """
            UPDATE memory_facts
            SET text = fact_text
            WHERE (text IS NULL OR text = '')
              AND COALESCE(fact_text, '') <> ''
            """
        )
    )


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())

    if not guard.has_table("memory_facts"):
        op.create_table(
            "memory_facts",
            sa.Column("id", UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("user_sub", sa.String(128), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
            sa.Column("vec384", ARRAY(sa.Float()), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
        guard.refresh()
    else:
        if not guard.has_column("memory_facts", "text"):
            op.add_column(
                "memory_facts",
                sa.Column("text", sa.Text(), nullable=False, server_default=""),
            )
            guard.refresh()
            if guard.has_column("memory_facts", "fact_text"):
                _backfill_memory_text()
            op.alter_column("memory_facts", "text", server_default=None)
            guard.refresh()

        guard.add_column_if_missing(
            "memory_facts",
            sa.Column("confidence", sa.Float(), nullable=True, server_default="0.0"),
        )
        guard.add_column_if_missing(
            "memory_facts",
            sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        guard.add_column_if_missing(
            "memory_facts",
            sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
        )
        guard.add_column_if_missing(
            "memory_facts",
            sa.Column("vec384", ARRAY(sa.Float()), nullable=True),
        )
        guard.add_column_if_missing(
            "memory_facts",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    guard.create_index_if_missing("memory_facts", "ix_memory_facts_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("memory_facts", "ix_memory_facts_user_sub", ["user_sub"])
    guard.create_index_if_missing("memory_facts", "ix_memory_facts_created_at", ["created_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("memory_facts"):
        return
    op.drop_index("ix_memory_facts_created_at", "memory_facts")
    op.drop_index("ix_memory_facts_user_sub", "memory_facts")
    op.drop_index("ix_memory_facts_tenant_id", "memory_facts")
    op.drop_table("memory_facts")
