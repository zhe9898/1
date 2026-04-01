"""
ZEN70 Memory Model - 记忆事实存储。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from backend.models import Base


class MemoryFact(Base):  # type: ignore[misc, unused-ignore]
    __tablename__ = "memory_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    user_sub = Column(String(128), nullable=False, index=True)
    text = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    deprecated = Column(Boolean, default=False)
    superseded_by = Column(UUID(as_uuid=True), nullable=True)
    vec384: Column = Column(ARRAY(Float), nullable=True)
