"""Session model for tracking active user login sessions."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.user import Base


class Session(Base):
    """Active user session tracking.

    Created on every successful login, destroyed on logout or expiry.
    Used for:
    - Session listing (user can see all active sessions)
    - Session revocation (kick out suspicious sessions)
    - Concurrent session limiting
    - Device fingerprinting
    """

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_tenant_user", "tenant_id", "user_id"),
        Index("ix_sessions_jti", "jti", unique=True),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    jti: Mapped[str] = mapped_column(String(64), nullable=False)
    # JWT jti — used to revoke the token when session is killed

    # Client context
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Derived from user_agent: "Chrome on macOS", "Safari on iPhone", etc.

    # Auth method for display
    auth_method: Mapped[str] = mapped_column(String(32), nullable=False, default="password")
    # password | pin | webauthn | invite

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        index=True,
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # username of admin who revoked, or "self" for user's own logout
