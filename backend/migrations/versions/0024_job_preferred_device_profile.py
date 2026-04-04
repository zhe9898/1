"""Add preferred_device_profile to jobs table

Revision ID: 0024_job_preferred_device_profile
Revises: 0023_software_evaluations_system_logs
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_job_preferred_device_profile"
down_revision = "0023_software_evaluations_system_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("preferred_device_profile", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "preferred_device_profile")
