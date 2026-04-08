"""Add transports to WebAuthn credentials."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0025_webauthn_credential_transports"
down_revision = "0024_job_preferred_device_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    guard.add_column_if_missing("webauthn_credentials", sa.Column("transports", sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("webauthn_credentials"):
        op.drop_column("webauthn_credentials", "transports")

