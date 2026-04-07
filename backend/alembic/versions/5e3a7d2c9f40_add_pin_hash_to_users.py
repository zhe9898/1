"""Add pin_hash column to users table

Revision ID: 5e3a7d2c9f40
Revises: 4a2d9e9b7c11
Create Date: 2026-03-21 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e3a7d2c9f40"
down_revision: Union[str, Sequence[str], None] = "4a2d9e9b7c11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("pin_hash", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "pin_hash")
