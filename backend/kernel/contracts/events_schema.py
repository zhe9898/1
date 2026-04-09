"""Canonical Redis event payload contracts shared across runtime components."""

from __future__ import annotations

import json
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


def _load_message_object(data: str | bytes | dict[str, object]) -> dict[str, object] | None:
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


class HardwareStateEventPayload(BaseModel):
    """Contract for formal `hardware:events` payloads."""

    path: str = Field(..., description="Canonical hardware mount or device path")
    state: str = Field(..., description="online | offline | pending | unknown")
    reason: str = Field(default="", description="Human-readable reason")
    uuid: str | None = Field(default=None, description="Optional stable device identifier")
    timestamp: float | None = Field(default=None, description="Unix timestamp emitted by the producer")

    @classmethod
    def from_message(cls, data: str | bytes | dict[str, object]) -> HardwareStateEventPayload | None:
        obj = _load_message_object(data)
        if obj is None:
            return None
        try:
            return cls.model_validate(obj)
        except ValidationError:
            return None

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> HardwareStateEventPayload | None:
        return cls.from_message(data)


def build_hardware_state_event(
    path: str,
    state: str,
    *,
    reason: str = "",
    uuid_val: str | None = None,
    timestamp: float | None = None,
) -> dict[str, object]:
    emitted_at = time.time() if timestamp is None else float(timestamp)
    payload: dict[str, object] = {
        "path": path,
        "state": state,
        "reason": reason,
        "timestamp": emitted_at,
    }
    if uuid_val:
        payload["uuid"] = uuid_val
    return payload


class SwitchStateEventPayload(BaseModel):
    """Contract for formal `switch:events` payloads consumed by browser realtime clients."""

    state: Literal["ON", "OFF", "PENDING"] = Field(..., description="Observed or persisted switch state")
    switch: str | None = Field(default=None, description="Canonical switch name")
    name: str | None = Field(default=None, description="Legacy switch field kept for compatibility")
    reason: str = Field(default="", description="Human-readable reason")
    updated_at: str | None = None
    updated_by: str | None = None

    def effective_switch_name(self) -> str | None:
        return self.switch or self.name or None

    @classmethod
    def from_message(cls, data: str | bytes | dict[str, object]) -> SwitchStateEventPayload | None:
        obj = _load_message_object(data)
        if obj is None or "state" not in obj:
            return None
        try:
            return cls.model_validate(obj)
        except ValidationError:
            return None

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> SwitchStateEventPayload | None:
        return cls.from_message(data)


def build_switch_state_event(
    switch_name: str,
    state: Literal["ON", "OFF", "PENDING"],
    *,
    reason: str = "",
    updated_by: str = "system",
    updated_at: float | None = None,
) -> dict[str, str]:
    return {
        "switch": switch_name,
        "name": switch_name,
        "state": state,
        "reason": reason,
        "updated_at": str(time.time() if updated_at is None else float(updated_at)),
        "updated_by": updated_by,
    }


class SwitchCommandSignalPayload(BaseModel):
    """Contract for Redis-internal `switch:commands` signals."""

    state: Literal["ON", "OFF", "RESTART", "PAUSE"] = Field(..., description="Desired switch command")
    switch: str | None = Field(default=None, description="Canonical switch name")
    name: str | None = Field(default=None, description="Legacy switch field kept for compatibility")
    reason: str = Field(default="", description="Human-readable reason")
    updated_at: str | None = None
    updated_by: str | None = None

    def effective_switch_name(self) -> str | None:
        return self.switch or self.name or None

    @classmethod
    def from_message(cls, data: str | bytes | dict[str, object]) -> SwitchCommandSignalPayload | None:
        obj = _load_message_object(data)
        if obj is None or "state" not in obj:
            return None
        try:
            return cls.model_validate(obj)
        except ValidationError:
            return None

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> SwitchCommandSignalPayload | None:
        return cls.from_message(data)


def build_switch_command_signal(
    switch_name: str,
    state: Literal["ON", "OFF", "RESTART", "PAUSE"],
    *,
    reason: str = "",
    updated_by: str = "system",
    updated_at: float | None = None,
) -> dict[str, str]:
    return {
        "switch": switch_name,
        "name": switch_name,
        "state": state,
        "reason": reason,
        "updated_at": str(time.time() if updated_at is None else float(updated_at)),
        "updated_by": updated_by,
    }


# Backward-compatible alias for legacy internal switch command payload handling.
SwitchEventPayload = SwitchCommandSignalPayload


def build_switch_event(
    switch_name: str,
    state: Literal["ON", "OFF", "RESTART", "PAUSE"],
    *,
    reason: str = "",
    updated_by: str = "system",
    updated_at: float | None = None,
) -> dict[str, str]:
    return build_switch_command_signal(
        switch_name,
        state,
        reason=reason,
        updated_by=updated_by,
        updated_at=updated_at,
    )


class SchedulerEventPayload(BaseModel):
    job_id: str = Field(..., description="Job ID")
    type: str = Field(..., description="manual_trigger | completed | failed")
    triggered_by: str = Field(default="system", description="Trigger origin")
    triggered_at: float | None = None
    status: str = Field(default="", description="Execution status")
    error: str = Field(default="", description="Error message")


class TriggerEventTriggerSnapshot(BaseModel):
    trigger_id: str = Field(..., description="Trigger ID")
    kind: str = Field(..., description="Trigger kind")
    status: str = Field(..., description="Trigger status")
    last_delivery_status: str | None = None
    last_delivery_id: str | None = None
    last_delivery_target_kind: str | None = None
    last_delivery_target_id: str | None = None


class TriggerEventDeliverySnapshot(BaseModel):
    delivery_id: str = Field(..., description="Trigger delivery ID")
    status: str = Field(..., description="dispatching | delivered | failed | retrying")
    source_kind: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    error_message: str | None = None
    fired_at: str | None = None
    delivered_at: str | None = None


class TriggerEventPayload(BaseModel):
    event_id: str | None = Field(default=None, description="Control event ID")
    action: str = Field(..., description="upserted | updated | paused | activated | fired | delivery_failed")
    ts: str | None = Field(default=None, description="Event timestamp")
    trigger: TriggerEventTriggerSnapshot = Field(..., description="Trigger snapshot")
    delivery: TriggerEventDeliverySnapshot | None = Field(
        default=None,
        description="Delivery snapshot for fired and delivery_failed actions",
    )

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> TriggerEventPayload | None:
        obj = _load_message_object(data)
        if obj is None:
            return None
        if not isinstance(obj.get("trigger"), dict):
            return None
        delivery = obj.get("delivery")
        if delivery is not None and not isinstance(delivery, dict):
            return None
        try:
            return cls.model_validate(obj)
        except ValidationError:
            return None


class ReservationEventSnapshot(BaseModel):
    job_id: str = Field(..., description="Reserved job ID")
    tenant_id: str = Field(..., description="Tenant owning the reservation")
    node_id: str = Field(..., description="Reserved node ID")
    start_at: str = Field(..., description="Reservation start time")
    end_at: str = Field(..., description="Reservation end time")
    priority: int = Field(..., description="Reserved job priority")
    cpu_cores: float = 0.0
    memory_mb: float = 0.0
    gpu_vram_mb: float = 0.0
    slots: int = 1


class ReservationEventPayload(BaseModel):
    event_id: str | None = Field(default=None, description="Control event ID")
    action: str = Field(..., description="created | canceled | expired")
    ts: str | None = Field(default=None, description="Event timestamp")
    reservation: ReservationEventSnapshot = Field(..., description="Reservation snapshot")
    reason: str | None = Field(default=None, description="Why the reservation changed")
    source: str | None = Field(default=None, description="Originating runtime component")

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> ReservationEventPayload | None:
        obj = _load_message_object(data)
        if obj is None or not isinstance(obj.get("reservation"), dict):
            return None
        try:
            return cls.model_validate(obj)
        except ValidationError:
            return None


class VoiceEventPayload(BaseModel):
    type: str = Field(default="voice_result", description="Event type")
    request_id: str = Field(..., description="Request ID")
    username: str = Field(default="", description="Username")
    status: str = Field(..., description="ok | error | timeout")
    text: str = Field(default="", description="Transcribed text")
    language: str = Field(default="", description="Detected language")
    duration: float = Field(default=0.0, description="Audio duration in seconds")
    error: str = Field(default="", description="Error message")
    timestamp: float = Field(default=0.0, description="Unix timestamp")


__all__ = [
    "HardwareStateEventPayload",
    "ReservationEventPayload",
    "ReservationEventSnapshot",
    "SchedulerEventPayload",
    "SwitchCommandSignalPayload",
    "SwitchStateEventPayload",
    "SwitchEventPayload",
    "TriggerEventDeliverySnapshot",
    "TriggerEventPayload",
    "TriggerEventTriggerSnapshot",
    "VoiceEventPayload",
    "build_hardware_state_event",
    "build_switch_command_signal",
    "build_switch_state_event",
    "build_switch_event",
]
