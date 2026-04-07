"""ZEN70 Asset model — media/file storage with AI embedding support."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Asset(Base):
    """Tenant-scoped media asset with AI embedding lifecycle."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    asset_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    camera: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    embedding_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
    )  # pending | done | failed
    ai_tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_emotion_highlight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
    )
