"""Add IoT Device Model for Zero-Hardcoding compliance

Revision ID: 7b9fa39e00a0
Revises:
Create Date: 2026-03-17 21:08:00.342871

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b9fa39e00a0"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 建立设备的物理结构 (DDL)
    device_table = op.create_table(
        "devices",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False),
        sa.Column("room", sa.String(length=64), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 初始化设备数据 (DML，等效于原来的硬编码项)
    op.bulk_insert(
        device_table,
        [
            {
                "id": "light_living_1",
                "type": "light",
                "name": "客厅主灯",
                "icon": "💡",
                "room": "客厅",
            },
            {
                "id": "light_bedroom_1",
                "type": "light",
                "name": "卧室床头灯",
                "icon": "🛏️",
                "room": "卧室",
            },
            {
                "id": "sensor_temp_1",
                "type": "sensor",
                "name": "婴儿房温湿度",
                "icon": "🌡️",
                "room": "婴儿房",
            },
            {
                "id": "curtain_1",
                "type": "curtain",
                "name": "落地窗帘",
                "icon": "🪟",
                "room": "客厅",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("devices")
