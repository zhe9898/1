"""Trigger kind registry for the unified control-plane trigger surface."""

from __future__ import annotations

import re
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

_TRIGGER_KIND_SCHEMA_REGISTRY: dict[str, type[BaseModel] | None] = {}
_TRIGGER_KIND_METADATA_REGISTRY: dict[str, dict[str, Any]] = {}
_BUILTINS_REGISTERED = False
_CRON_FIELD_RE = re.compile(r"^[\d\*/,\-]+$")


def _validate_cron_expression(value: str) -> str:
    normalized = str(value or "").strip()
    parts = normalized.split()
    if len(parts) not in {5, 6}:
        raise ValueError("Cron schedule must contain 5 or 6 fields")
    for part in parts:
        if not _CRON_FIELD_RE.fullmatch(part):
            raise ValueError("Cron schedule contains unsupported characters")
    return normalized


class ManualTriggerConfig(BaseModel):
    allow_api_fire: bool = True


class CronTriggerConfig(BaseModel):
    schedule: str = Field(..., min_length=1)
    timezone: str = "UTC"
    jitter_seconds: int = Field(default=0, ge=0, le=3600)
    misfire_policy: Literal["skip", "fire_once"] = "skip"

    @field_validator("schedule")
    @classmethod
    def _schedule_valid(cls, value: str) -> str:
        return _validate_cron_expression(value)

    @field_validator("timezone")
    @classmethod
    def _timezone_valid(cls, value: str) -> str:
        ZoneInfo(value)
        return value


class WebhookTriggerConfig(BaseModel):
    accepted_methods: list[str] = Field(default_factory=lambda: ["POST"])
    secret: str | None = Field(default=None, min_length=8, max_length=255)
    secret_header: str = Field(default="X-ZEN70-Webhook-Secret", min_length=1, max_length=128)
    idempotency_header: str = Field(default="X-Idempotency-Key", min_length=1, max_length=128)

    @field_validator("accepted_methods")
    @classmethod
    def _methods_valid(cls, value: list[str]) -> list[str]:
        normalized = [str(item or "").strip().upper() for item in value if str(item or "").strip()]
        if not normalized:
            raise ValueError("accepted_methods must contain at least one HTTP method")
        allowed = {"POST", "PUT", "PATCH"}
        invalid = [item for item in normalized if item not in allowed]
        if invalid:
            raise ValueError(f"Webhook methods must be one of {sorted(allowed)}, got {invalid}")
        return normalized


class EventTriggerConfig(BaseModel):
    source: str = Field(..., min_length=1, max_length=128)
    event_types: list[str] = Field(default_factory=list)
    subject_patterns: list[str] = Field(default_factory=list)


def ensure_builtin_trigger_kinds_registered() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return

    _TRIGGER_KIND_SCHEMA_REGISTRY["manual"] = ManualTriggerConfig
    _TRIGGER_KIND_METADATA_REGISTRY["manual"] = {
        "stability": "stable",
        "description": "Explicit operator or API fire.",
    }
    _TRIGGER_KIND_SCHEMA_REGISTRY["cron"] = CronTriggerConfig
    _TRIGGER_KIND_METADATA_REGISTRY["cron"] = {
        "stability": "beta",
        "description": "Cron-like timer trigger contract.",
    }
    _TRIGGER_KIND_SCHEMA_REGISTRY["webhook"] = WebhookTriggerConfig
    _TRIGGER_KIND_METADATA_REGISTRY["webhook"] = {
        "stability": "beta",
        "description": "Inbound HTTP webhook trigger contract.",
    }
    _TRIGGER_KIND_SCHEMA_REGISTRY["event"] = EventTriggerConfig
    _TRIGGER_KIND_METADATA_REGISTRY["event"] = {
        "stability": "beta",
        "description": "Internal event-bus trigger contract.",
    }
    _BUILTINS_REGISTERED = True


def register_trigger_kind(
    kind: str,
    *,
    config_schema: type[BaseModel] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_builtin_trigger_kinds_registered()
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        raise ValueError("Trigger kind must be a non-empty string")
    _TRIGGER_KIND_SCHEMA_REGISTRY[normalized_kind] = config_schema
    _TRIGGER_KIND_METADATA_REGISTRY[normalized_kind] = dict(metadata or {})


def unregister_trigger_kind(kind: str) -> None:
    ensure_builtin_trigger_kinds_registered()
    normalized_kind = str(kind or "").strip()
    _TRIGGER_KIND_SCHEMA_REGISTRY.pop(normalized_kind, None)
    _TRIGGER_KIND_METADATA_REGISTRY.pop(normalized_kind, None)


def validate_trigger_config(kind: str, config: dict[str, object] | None) -> dict[str, object]:
    ensure_builtin_trigger_kinds_registered()
    normalized_kind = str(kind or "").strip()
    schema = _TRIGGER_KIND_SCHEMA_REGISTRY.get(normalized_kind)
    if normalized_kind not in _TRIGGER_KIND_SCHEMA_REGISTRY:
        raise ValueError(f"Trigger kind '{normalized_kind}' is not registered")
    if schema is None:
        return dict(config or {})
    try:
        model = schema(**(config or {}))
    except Exception as exc:
        raise ValueError(f"Invalid config for trigger kind '{normalized_kind}': {exc}") from exc
    return cast(dict[str, Any], model.model_dump(mode="json"))


def get_trigger_kind_info(kind: str) -> dict[str, Any]:
    ensure_builtin_trigger_kinds_registered()
    normalized_kind = str(kind or "").strip()
    if normalized_kind not in _TRIGGER_KIND_SCHEMA_REGISTRY:
        raise ValueError(f"Trigger kind '{normalized_kind}' is not registered")
    schema = _TRIGGER_KIND_SCHEMA_REGISTRY[normalized_kind]
    return {
        "kind": normalized_kind,
        "has_config_schema": schema is not None,
        "config_schema": schema.model_json_schema() if schema is not None else None,
        "metadata": dict(_TRIGGER_KIND_METADATA_REGISTRY.get(normalized_kind, {})),
    }


def list_trigger_kinds() -> list[dict[str, Any]]:
    ensure_builtin_trigger_kinds_registered()
    return [get_trigger_kind_info(kind) for kind in sorted(_TRIGGER_KIND_SCHEMA_REGISTRY.keys())]
