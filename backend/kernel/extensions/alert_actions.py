from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from backend.platform.security.normalization import normalize_nonempty_string, normalize_public_network_url

_HEADER_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")


class LogAlertAction(BaseModel):
    type: Literal["log"] = "log"


class WebhookAlertAction(BaseModel):
    type: Literal["webhook"]
    url: str
    method: Literal["POST"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        return normalize_public_network_url(value, field_name="url", allowed_schemes={"http", "https"})

    @field_validator("headers")
    @classmethod
    def _normalize_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_header_value in value.items():
            header_name = normalize_nonempty_string(str(raw_name), field_name="header_name")
            if any(char not in _HEADER_NAME_CHARS for char in header_name):
                raise ValueError("header_name contains unsupported characters")
            header_value = normalize_nonempty_string(str(raw_header_value), field_name=f"header {header_name}")
            if "\r" in header_value or "\n" in header_value:
                raise ValueError(f"header {header_name} must not contain CR/LF")
            normalized[header_name] = header_value
        return normalized


AlertActionModel = Annotated[LogAlertAction | WebhookAlertAction, Field(discriminator="type")]
_ALERT_ACTION_ADAPTER: TypeAdapter[LogAlertAction | WebhookAlertAction] = TypeAdapter(AlertActionModel)


def normalize_alert_action(raw_action: Mapping[str, object] | BaseModel) -> dict[str, Any]:
    source: Mapping[str, object]
    if isinstance(raw_action, BaseModel):
        source = raw_action.model_dump(mode="python")
    else:
        source = raw_action
    try:
        normalized = _ALERT_ACTION_ADAPTER.validate_python(dict(source))
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    normalized_dict = normalized.model_dump(mode="python")
    return dict(normalized_dict)
