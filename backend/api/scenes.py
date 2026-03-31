"""ZEN70 Scenes — scene automation Pydantic contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SceneAction(BaseModel):
    switch: str
    state: str
    delay_ms: int = Field(default=0, ge=0, le=30000)


class SceneCreateRequest(BaseModel):
    name: str = Field(..., max_length=128)
    actions: list[SceneAction] = Field(..., min_length=1)
    trigger_type: Literal["manual", "schedule", "event"] = "manual"
    cron_expr: str | None = None


class SceneUpdateRequest(BaseModel):
    name: str | None = None
    actions: list[SceneAction] | None = None
    is_active: bool | None = None


class SceneResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    icon: str | None = None
    actions: list[Any] = Field(default_factory=list)
    trigger_type: str = "manual"
    cron_expr: str | None = None
    is_active: bool = True
    created_by: int | None = None


class SceneExecuteResponse(BaseModel):
    scene_id: int
    scene_name: str
    execution_id: str
    actions_dispatched: int
    status: str
