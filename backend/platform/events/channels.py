from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.platform.redis.constants import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_HARDWARE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_SWITCH_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
)

CHANNEL_SWITCH_COMMANDS: str = "switch:commands"
CHANNEL_SENTINEL_SIGNALS: str = "sentinel:signals"
CHANNEL_ROUTING_MELTDOWN: str = "routing:meltdown"


@dataclass(frozen=True, slots=True)
class EventChannelSpec:
    subject: str
    plane: Literal["control-plane", "redis-internal"]
    browser_realtime: bool
    description: str


_CHANNEL_SPECS: tuple[EventChannelSpec, ...] = (
    EventChannelSpec(
        subject=CHANNEL_HARDWARE_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Browser-visible hardware state changes authored by control-plane producers.",
    ),
    EventChannelSpec(
        subject=CHANNEL_SWITCH_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Browser-visible switch state changes authored by control-plane producers.",
    ),
    EventChannelSpec(
        subject=CHANNEL_NODE_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Node lifecycle control-plane events.",
    ),
    EventChannelSpec(
        subject=CHANNEL_JOB_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Job lifecycle control-plane events.",
    ),
    EventChannelSpec(
        subject=CHANNEL_CONNECTOR_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Connector lifecycle control-plane events.",
    ),
    EventChannelSpec(
        subject=CHANNEL_RESERVATION_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Reservation lifecycle control-plane events.",
    ),
    EventChannelSpec(
        subject=CHANNEL_TRIGGER_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Trigger lifecycle control-plane events.",
    ),
    EventChannelSpec(
        subject=CHANNEL_SWITCH_COMMANDS,
        plane="redis-internal",
        browser_realtime=False,
        description="Redis-only switch command fan-out consumed by topology sentinel and related workers.",
    ),
    EventChannelSpec(
        subject=CHANNEL_SENTINEL_SIGNALS,
        plane="redis-internal",
        browser_realtime=False,
        description="Redis-only sentinel/guardian coordination signals.",
    ),
    EventChannelSpec(
        subject=CHANNEL_ROUTING_MELTDOWN,
        plane="redis-internal",
        browser_realtime=False,
        description="Redis-only routing operator coordination channel.",
    ),
)

CHANNEL_SPECS: dict[str, EventChannelSpec] = {spec.subject: spec for spec in _CHANNEL_SPECS}

CONTROL_PLANE_EVENT_CHANNELS: tuple[str, ...] = tuple(spec.subject for spec in _CHANNEL_SPECS if spec.plane == "control-plane")
CONTROL_PLANE_REALTIME_CHANNELS: tuple[str, ...] = tuple(spec.subject for spec in _CHANNEL_SPECS if spec.plane == "control-plane" and spec.browser_realtime)
REDIS_INTERNAL_SIGNAL_CHANNELS: tuple[str, ...] = tuple(spec.subject for spec in _CHANNEL_SPECS if spec.plane == "redis-internal")


def is_control_plane_channel(subject: str) -> bool:
    spec = CHANNEL_SPECS.get(subject)
    return spec is not None and spec.plane == "control-plane"


def is_redis_internal_signal(subject: str) -> bool:
    spec = CHANNEL_SPECS.get(subject)
    return spec is not None and spec.plane == "redis-internal"


def export_event_channel_contract() -> dict[str, object]:
    return {
        "control_plane_event_channels": list(CONTROL_PLANE_EVENT_CHANNELS),
        "browser_realtime_event_channels": list(CONTROL_PLANE_REALTIME_CHANNELS),
        "internal_coordination_channels": list(REDIS_INTERNAL_SIGNAL_CHANNELS),
    }


__all__ = (
    "CHANNEL_ROUTING_MELTDOWN",
    "CHANNEL_SENTINEL_SIGNALS",
    "CHANNEL_SPECS",
    "CHANNEL_SWITCH_COMMANDS",
    "CONTROL_PLANE_EVENT_CHANNELS",
    "CONTROL_PLANE_REALTIME_CHANNELS",
    "EventChannelSpec",
    "REDIS_INTERNAL_SIGNAL_CHANNELS",
    "export_event_channel_contract",
    "is_control_plane_channel",
    "is_redis_internal_signal",
)
