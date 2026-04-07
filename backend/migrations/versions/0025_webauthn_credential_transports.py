"""Add transports to WebAuthn credentials

Revision ID: 0025_webauthn_credential_transports
Revises: 0024_job_preferred_device_profile
Create Date: 2026-04-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_webauthn_credential_transports"
down_revision = "0024_job_preferred_device_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webauthn_credentials", sa.Column("transports", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("webauthn_credentials", "transports")
