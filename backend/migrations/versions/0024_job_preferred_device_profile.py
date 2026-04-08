"""Add preferred_device_profile to jobs table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0024_job_preferred_device_profile"
down_revision = "0023_software_evaluations_system_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    guard.add_column_if_missing("jobs", sa.Column("preferred_device_profile", sa.String(64), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("jobs"):
        op.drop_column("jobs", "preferred_device_profile")
