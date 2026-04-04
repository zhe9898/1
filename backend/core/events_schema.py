# Redis Pub/Sub 事件契约：网关与探针共用，禁止隐式 JSON 约定。

"""

法典 ADR 0006：switch:events 等通道的 payload 显式契约。

网关发布与探针消费使用同一模型，避免字段漂移导致静默失效。

"""

from __future__ import annotations

import json
import time
from typing import Literal, cast

from pydantic import BaseModel, Field


class SwitchEventPayload(BaseModel):
    """switch:events 通道 payload；state 为期望状态，探针据此执行 Reconcile。"""

    state: str = Field(..., description="ON | OFF | RESTART")

    switch: str | None = Field(None, description="开关名，与 name 二选一")

    name: str | None = Field(None, description="开关名（兼容 set_switch 历史格式）")

    reason: str = Field("", description="原因说明")

    updated_at: str | None = None

    updated_by: str | None = None

    def effective_switch_name(self) -> str | None:
        """探针消费时统一取开关名：优先 switch，否则 name。"""

        return self.switch or self.name or None

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> SwitchEventPayload | None:
        """从 Redis 消息反序列化；无效则返回 None。"""

        if isinstance(data, bytes):

            data = data.decode("utf-8")

        if isinstance(data, str):

            try:

                data = json.loads(data)

            except json.JSONDecodeError:

                return None

        if not isinstance(data, dict) or "state" not in data:

            return None

        try:

            return cls(
                state=str(data.get("state", "")),
                switch=cast(str | None, data.get("switch")),
                name=cast(str | None, data.get("name")),
                reason=str(data.get("reason", "")),
                updated_at=(str(data["updated_at"]) if data.get("updated_at") is not None else None),
                updated_by=cast(str | None, data.get("updated_by")),
            )

        except (KeyError, TypeError, ValueError):

            return None


def build_switch_event(
    switch_name: str,
    state: Literal["ON", "OFF", "RESTART"],
    reason: str = "",
    updated_by: str = "system",
) -> dict[str, str]:
    """网关发布前构建统一 payload，保证探针能解析。"""

    return {
        "switch": switch_name,
        "name": switch_name,
        "state": state,
        "reason": reason,
        "updated_at": str(time.time()),
        "updated_by": updated_by,
    }


class SchedulerEventPayload(BaseModel):
    """scheduler:events 通道 payload；定时任务执行状态变更。"""

    job_id: str = Field(..., description="任务 ID")

    type: str = Field(..., description="manual_trigger | completed | failed")

    triggered_by: str = Field("system", description="触发人")

    triggered_at: float | None = None

    status: str = Field("", description="执行结果")

    error: str = Field("", description="错误信息")


class TriggerEventTriggerSnapshot(BaseModel):
    """Trigger snapshot embedded in trigger:events payloads."""

    trigger_id: str = Field(..., description="Trigger ID")

    kind: str = Field(..., description="Trigger kind")

    status: str = Field(..., description="Trigger status")

    last_delivery_status: str | None = None

    last_delivery_id: str | None = None

    last_delivery_target_kind: str | None = None

    last_delivery_target_id: str | None = None


class TriggerEventDeliverySnapshot(BaseModel):
    """Delivery snapshot embedded in fired and failed trigger events."""

    delivery_id: str = Field(..., description="Trigger delivery ID")

    status: str = Field(..., description="dispatching | accepted | failed")

    source_kind: str | None = None

    target_kind: str | None = None

    target_id: str | None = None

    error_message: str | None = None

    fired_at: str | None = None

    delivered_at: str | None = None


class TriggerEventPayload(BaseModel):
    """trigger:events payload for unified trigger lifecycle events."""

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
        """Deserialize a trigger:events Redis payload into the contract model."""

        if isinstance(data, bytes):
            data = data.decode("utf-8")

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None

        trigger_snapshot = data.get("trigger")
        if not isinstance(trigger_snapshot, dict):
            return None

        normalized = dict(data)
        delivery_snapshot = normalized.get("delivery")
        if delivery_snapshot is not None and not isinstance(delivery_snapshot, dict):
            return None

        try:
            return cls.model_validate(normalized)
        except (KeyError, TypeError, ValueError):
            return None


class ReservationEventSnapshot(BaseModel):
    """Reservation snapshot embedded in reservation:events payloads."""

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
    """reservation:events payload for reservation lifecycle and planning events."""

    event_id: str | None = Field(default=None, description="Control event ID")
    action: str = Field(..., description="created | canceled | expired")
    ts: str | None = Field(default=None, description="Event timestamp")
    reservation: ReservationEventSnapshot = Field(..., description="Reservation snapshot")
    reason: str | None = Field(default=None, description="Why the reservation changed")
    source: str | None = Field(default=None, description="Originating runtime component")

    @classmethod
    def from_redis_message(cls, data: str | bytes | dict[str, object]) -> ReservationEventPayload | None:
        """Deserialize a reservation:events Redis payload into the contract model."""

        if isinstance(data, bytes):
            data = data.decode("utf-8")

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None

        reservation_snapshot = data.get("reservation")
        if not isinstance(reservation_snapshot, dict):
            return None

        try:
            return cls.model_validate(data)
        except (KeyError, TypeError, ValueError):
            return None


class VoiceEventPayload(BaseModel):
    """voice:events 通道 payload；语音转文字结果回推。"""

    type: str = Field("voice_result", description="事件类型")

    request_id: str = Field(..., description="请求 ID")

    username: str = Field("", description="用户名")

    status: str = Field(..., description="ok | error | timeout")

    text: str = Field("", description="转文字结果")

    language: str = Field("", description="检测到的语言")

    duration: float = Field(0, description="音频时长(秒)")

    error: str = Field("", description="错误信息")

    timestamp: float = Field(0, description="时间戳")
