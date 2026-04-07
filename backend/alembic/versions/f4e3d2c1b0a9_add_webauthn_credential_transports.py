"""Add transports to WebAuthn credentials and merge Alembic heads.

Revision ID: f4e3d2c1b0a9
Revises: a9b8c7d6e5f4, c13c4b7d9a12
Create Date: 2026-04-07 00:30:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4e3d2c1b0a9"
down_revision: Union[str, Sequence[str], None] = ("a9b8c7d6e5f4", "c13c4b7d9a12")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def upgrade() -> None:
    if _has_table("webauthn_credentials") and not _has_column("webauthn_credentials", "transports"):
        op.add_column("webauthn_credentials", sa.Column("transports", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_table("webauthn_credentials") and _has_column("webauthn_credentials", "transports"):
        op.drop_column("webauthn_credentials", "transports")
