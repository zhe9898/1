"""Add persisted WebAuthn challenge store."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.platform.db.schema_guard import SchemaGuard

revision = "0027_webauthn_challenge_store"
down_revision = "0026_dual_chain_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if not guard.has_table("webauthn_challenges"):
        op.create_table(
            "webauthn_challenges",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("challenge_id", sa.String(length=255), nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("flow", sa.String(length=32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("challenge_id", name="ux_webauthn_challenges_challenge_id"),
        )
        guard.refresh()
    guard.create_index_if_missing("webauthn_challenges", "ix_webauthn_challenges_challenge_id", ["challenge_id"])
    guard.create_index_if_missing(
        "webauthn_challenges",
        "ix_webauthn_challenges_session_binding",
        ["session_id", "tenant_id", "user_id", "flow"],
    )
    guard.create_index_if_missing("webauthn_challenges", "ix_webauthn_challenges_expires_at", ["expires_at"])
    guard.create_index_if_missing("webauthn_challenges", "ix_webauthn_challenges_created_at", ["created_at"])


def downgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    if guard.has_table("webauthn_challenges"):
        op.drop_table("webauthn_challenges")

