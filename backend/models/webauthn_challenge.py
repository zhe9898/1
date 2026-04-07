from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class WebAuthnChallenge(Base):
    """Persisted WebAuthn challenge bound to browser flow session and tenant identity."""

    __tablename__ = "webauthn_challenges"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="ux_webauthn_challenges_challenge_id"),
        Index("ix_webauthn_challenges_session_binding", "session_id", "tenant_id", "user_id", "flow"),
        Index("ix_webauthn_challenges_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    flow: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=False), nullable=False, index=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
