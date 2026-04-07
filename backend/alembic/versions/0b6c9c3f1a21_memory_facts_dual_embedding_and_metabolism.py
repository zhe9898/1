"""Memory facts: dual embedding field + metabolism flags

Revision ID: 0b6c9c3f1a21
Revises: 8c2f1a6b4d10
Create Date: 2026-03-18 00:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


class Vector(sa.types.UserDefinedType):
    """pgvector VECTOR 类型适配器"""

    cache_ok = True

    def __init__(self, dim: int = 512):
        self.dim = dim

    def get_col_spec(self) -> str:
        return f"VECTOR({self.dim})"


revision: str = "0b6c9c3f1a21"
down_revision: Union[str, Sequence[str], None] = "8c2f1a6b4d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 新增双擎向量场字段（facts 专用 384 维 + 可选 CLIP 512 维）
    op.add_column(
        "memory_facts",
        sa.Column("text_embedding", Vector(384), nullable=True),
    )
    op.add_column(
        "memory_facts",
        sa.Column(
            "text_embedding_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "memory_facts",
        sa.Column("image_text_embedding", Vector(512), nullable=True),
    )
    op.add_column(
        "memory_facts",
        sa.Column(
            "image_text_embedding_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
    )

    # 新陈代谢字段
    op.add_column(
        "memory_facts",
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "memory_facts",
        sa.Column("superseded_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_memory_facts_deprecated", "memory_facts", ["deprecated"])
    op.create_index("ix_memory_facts_superseded_by", "memory_facts", ["superseded_by"])

    # 新索引：facts 文本语义向量
    op.create_index(
        "ix_memory_fact_text_embedding",
        "memory_facts",
        ["text_embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"text_embedding": "vector_cosine_ops"},
    )

    # 旧字段兼容：embedding/embedding_status 保留（历史/回滚用）


def downgrade() -> None:
    op.drop_index("ix_memory_fact_text_embedding", table_name="memory_facts")
    op.drop_index("ix_memory_facts_superseded_by", table_name="memory_facts")
    op.drop_index("ix_memory_facts_deprecated", table_name="memory_facts")

    op.drop_column("memory_facts", "superseded_by")
    op.drop_column("memory_facts", "deprecated")
    op.drop_column("memory_facts", "image_text_embedding_status")
    op.drop_column("memory_facts", "image_text_embedding")
    op.drop_column("memory_facts", "text_embedding_status")
    op.drop_column("memory_facts", "text_embedding")
