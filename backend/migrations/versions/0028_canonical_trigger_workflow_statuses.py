"""Backfill trigger and workflow statuses to canonical values."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0028_canonical_trigger_workflow_statuses"
down_revision = "0027_webauthn_challenge_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("triggers"):
        op.execute(sa.text("UPDATE triggers SET status = 'inactive' WHERE status = 'paused'"))
        op.execute(sa.text("UPDATE triggers SET last_delivery_status = 'delivered' WHERE last_delivery_status = 'accepted'"))
    if guard.has_table("trigger_deliveries"):
        op.execute(sa.text("UPDATE trigger_deliveries SET status = 'delivered' WHERE status = 'accepted'"))
    if guard.has_table("workflows"):
        op.execute(sa.text("UPDATE workflows SET status = 'cancelled' WHERE status = 'canceled'"))


def downgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("triggers"):
        op.execute(sa.text("UPDATE triggers SET status = 'paused' WHERE status = 'inactive'"))
        op.execute(sa.text("UPDATE triggers SET last_delivery_status = 'accepted' WHERE last_delivery_status = 'delivered'"))
    if guard.has_table("trigger_deliveries"):
        op.execute(sa.text("UPDATE trigger_deliveries SET status = 'accepted' WHERE status = 'delivered'"))
    if guard.has_table("workflows"):
        op.execute(sa.text("UPDATE workflows SET status = 'canceled' WHERE status = 'cancelled'"))

