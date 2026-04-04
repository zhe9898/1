"""
ZEN70 Agent Action Router - AI 代理执行软开关操作。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_db, get_redis
from backend.core.redis_client import RedisClient

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _agent_enabled() -> bool:
    return os.getenv("AGENT_ENABLED", "0") == "1"


async def _is_feature_flag_enabled(db: AsyncSession, key: str) -> bool:
    from sqlalchemy import select

    from backend.models.feature_flag import FeatureFlag

    result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    flag = result.scalars().first()
    return flag.enabled if flag else False


def _get_allowed_switches() -> list[str]:
    raw = os.getenv("AGENT_ALLOWED_SWITCHES", "media,voice,clip")
    return [s.strip() for s in raw.split(",") if s.strip()]


class AgentActionItem(BaseModel):
    switch: str
    state: str = Field(..., pattern="^(ON|OFF)$")
    reason: str = ""


class AgentActRequest(BaseModel):
    actions: list[AgentActionItem]
    idempotency_key: str | None = None


class AgentActionResult(BaseModel):
    switch: str
    ok: bool
    message: str = ""


class AgentActResponse(BaseModel):
    results: list[AgentActionResult]


@router.post("/act", response_model=AgentActResponse)
async def agent_act(
    request: Request,
    body: AgentActRequest,
    redis: RedisClient | None = Depends(get_redis),
    db: AsyncSession | None = Depends(get_db),
    x_idempotency_key: str | None = Header(None),
    current_user: dict = Depends(get_current_admin),
) -> AgentActResponse:
    if not _agent_enabled():
        return AgentActResponse(results=[AgentActionResult(switch=a.switch, ok=False, message="agent disabled") for a in body.actions])

    allowed = _get_allowed_switches()
    results: list[AgentActionResult] = []
    user_sub = current_user.get("sub", "unknown")

    for action in body.actions:
        if action.switch not in allowed:
            results.append(AgentActionResult(switch=action.switch, ok=False, message="switch not allowed"))
            continue

        if redis is not None:
            await redis.set_switch(
                action.switch,
                action.state,
                reason=action.reason,
                updated_by=f"agent:{user_sub}",
            )

        results.append(AgentActionResult(switch=action.switch, ok=True))

    return AgentActResponse(results=results)
