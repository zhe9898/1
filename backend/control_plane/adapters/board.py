"""ZEN70 Board — message board Pydantic contracts."""

from __future__ import annotations

import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# Contracts only: route handlers are not mounted in this module.
MODULE_STATUS = "contracts-only"


class BoardMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    is_pinned: bool = False
    meta_info: dict[str, Any] | None = None


class AuthorInfo(BaseModel):
    id: int
    username: str
    role: str
    display_name: str | None = None


class BoardMessageResponse(BaseModel):
    id: UUID
    content: str
    is_pinned: bool
    created_at: datetime.datetime
    author: AuthorInfo
