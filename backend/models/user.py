"""ZEN70 user, WebAuthn credential, and Web Push subscription models."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class User(Base):
    """Tenant-scoped users with RBAC roles and password/PIN auth material."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="ux_users_tenant_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="family")

    # AI route preference (law 9.4): local, cloud, auto.
    ai_route_preference: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)

    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # User lifecycle management
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    # Status: active, suspended, deleted
    suspended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    suspended_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suspended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    credentials: Mapped[list["WebAuthnCredential"]] = relationship(
        "WebAuthnCredential",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        "PushSubscription",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class WebAuthnCredential(Base):
    """Registered WebAuthn credentials owned by a user."""

    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transports: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="credentials")


class PushSubscription(Base):
    """Tenant-scoped Web Push subscriptions for browser notifications."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "endpoint", name="ux_push_subscriptions_tenant_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    p256dh: Mapped[str] = mapped_column(String(512), nullable=False)
    auth: Mapped[str] = mapped_column(String(256), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="push_subscriptions")
