from __future__ import annotations

from pydantic import BaseModel, Field

from backend.api.action_contracts import ControlAction


class StatusView(BaseModel):
    key: str
    label: str
    tone: str = "neutral"


class FormFieldOption(BaseModel):
    value: str
    label: str


class FormFieldSchema(BaseModel):
    key: str
    label: str
    input_type: str = "text"
    required: bool = False
    description: str | None = None
    placeholder: str | None = None
    value: str | int | bool | None = None
    options: list[FormFieldOption] = Field(default_factory=list)


class FormSectionSchema(BaseModel):
    id: str
    label: str
    description: str | None = None
    fields: list[FormFieldSchema] = Field(default_factory=list)


class ResourceSchemaResponse(BaseModel):
    product: str
    profile: str
    runtime_profile: str
    resource: str
    title: str
    description: str | None = None
    empty_state: str | None = None
    policies: dict[str, object] = Field(default_factory=dict)
    submit_action: ControlAction | None = None
    sections: list[FormSectionSchema] = Field(default_factory=list)
