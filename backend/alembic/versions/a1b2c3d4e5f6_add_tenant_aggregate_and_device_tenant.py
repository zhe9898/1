"""
ADR-0047 WP-P2b: 引入 Tenant 聚合根，修复 Device 租户隔离缺口。

Revision ID: a1b2c3d4e5f6
Revises: 9f2c7a1d4e61
Create Date: 2026-03-29

Changes:
  1. 创建 tenants 表（最小可行 Tenant 聚合根）
  2. 为 devices 表补充 tenant_id 列和索引
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str = "9f2c7a1d4e61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. 创建 tenants 表 ─────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default="Home"),
        sa.Column("plan", sa.String(32), nullable=False, server_default="home"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metadata_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenants"),
    )

    # ── 2. 为 devices 表补充 tenant_id（ADR-0047 RLS 修复）─────────────────────
    # 存量数据补 "default" 租户，后续可按需迁移
    op.add_column(
        "devices",
        sa.Column(
            "tenant_id",
            sa.String(64),
            nullable=False,
            server_default="default",
        ),
    )
    op.create_index(
        "ix_devices_tenant_id",
        "devices",
        ["tenant_id"],
    )

    # ── 3. 为 tenant_id 补 "default" 租户行（使 FK 参照完整性就绪）──────────────
    # 注意：不强制 FK 约束（保留异步扩展灵活性），仅确保 default 租户存在
    op.execute("""
        INSERT INTO tenants (tenant_id, display_name, plan, is_active, created_at)
        VALUES ('default', 'Default Home', 'home', true, NOW())
        ON CONFLICT (tenant_id) DO NOTHING
        """)


def downgrade() -> None:
    # ── 回滚：移除 devices.tenant_id，删除 tenants 表 ─────────────────────────
    op.drop_index("ix_devices_tenant_id", table_name="devices")
    op.drop_column("devices", "tenant_id")
    op.drop_table("tenants")
