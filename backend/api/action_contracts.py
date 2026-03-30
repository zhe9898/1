from __future__ import annotations

from pydantic import BaseModel, Field


class ControlActionField(BaseModel):
    key: str
    label: str
    input_type: str = "text"
    required: bool = False
    placeholder: str | None = None
    value: str | int | bool | None = None


class ControlAction(BaseModel):
    key: str
    label: str
    endpoint: str
    method: str = "POST"
    enabled: bool = True
    requires_admin: bool = True
    reason: str | None = None
    confirmation: str | None = None
    fields: list[ControlActionField] = Field(default_factory=list)


def optional_reason_field() -> ControlActionField:
    return ControlActionField(
        key="reason",
        label="Reason",
        input_type="text",
        required=False,
        placeholder="Optional operator note",
    )
