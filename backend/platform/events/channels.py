from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from backend.platform.redis.constants import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_HARDWARE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_SESSION_EVENTS,
    CHANNEL_SWITCH_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    CHANNEL_USER_EVENTS,
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
        subject=CHANNEL_SESSION_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Tenant-scoped session mutation control-plane events.",
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
        subject=CHANNEL_USER_EVENTS,
        plane="control-plane",
        browser_realtime=True,
        description="Tenant-scoped user lifecycle control-plane events.",
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
TENANT_SCOPED_REALTIME_CHANNELS: tuple[str, ...] = (
    CHANNEL_NODE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_SESSION_EVENTS,
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    CHANNEL_USER_EVENTS,
)
BROWSER_PUBLIC_REALTIME_CHANNELS: tuple[str, ...] = tuple(
    subject for subject in CONTROL_PLANE_REALTIME_CHANNELS if subject not in TENANT_SCOPED_REALTIME_CHANNELS
)
REDIS_INTERNAL_SIGNAL_CHANNELS: tuple[str, ...] = tuple(spec.subject for spec in _CHANNEL_SPECS if spec.plane == "redis-internal")
TENANT_REALTIME_SUBJECT_SEGMENT = "tenant"
CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS: tuple[str, ...] = (
    "event_id",
    "revision",
    "action",
    "ts",
    "tenant_id",
)
_TENANT_SUBJECT_TOKEN_RE = re.compile(r"^[0-9a-f]+$")


def is_control_plane_channel(subject: str) -> bool:
    spec = CHANNEL_SPECS.get(subject)
    return spec is not None and spec.plane == "control-plane"


def is_redis_internal_signal(subject: str) -> bool:
    spec = CHANNEL_SPECS.get(subject)
    return spec is not None and spec.plane == "redis-internal"


def is_tenant_scoped_realtime_channel(subject: str) -> bool:
    return subject in TENANT_SCOPED_REALTIME_CHANNELS


def tenant_subject_token(tenant_id: str) -> str:
    normalized_tenant_id = str(tenant_id).strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for tenant realtime subjects")
    return normalized_tenant_id.encode("utf-8").hex()


def tenant_realtime_subject(subject: str, tenant_id: str) -> str:
    if not is_tenant_scoped_realtime_channel(subject):
        raise ValueError(f"subject is not a tenant-scoped realtime channel: {subject}")
    return f"{subject}.{TENANT_REALTIME_SUBJECT_SEGMENT}.{tenant_subject_token(tenant_id)}"


def parse_tenant_realtime_subject(subject: str) -> tuple[str, str] | None:
    normalized_subject = str(subject).strip()
    marker = f".{TENANT_REALTIME_SUBJECT_SEGMENT}."
    base_subject, separator, token = normalized_subject.partition(marker)
    if not separator:
        return None
    if not is_tenant_scoped_realtime_channel(base_subject):
        return None
    normalized_token = token.strip().lower()
    if not normalized_token or not _TENANT_SUBJECT_TOKEN_RE.fullmatch(normalized_token) or len(normalized_token) % 2 != 0:
        return None
    return base_subject, normalized_token


def is_tenant_realtime_subject(subject: str) -> bool:
    return parse_tenant_realtime_subject(subject) is not None


def is_registered_control_plane_subject(subject: str) -> bool:
    return control_plane_subject_channel(subject) is not None


def control_plane_subject_channel(subject: str) -> str | None:
    normalized_subject = str(subject).strip()
    if is_control_plane_channel(normalized_subject):
        return normalized_subject
    parsed = parse_tenant_realtime_subject(normalized_subject)
    if parsed is None:
        return None
    base_subject, _ = parsed
    return base_subject


def subject_targets_tenant(subject: str, tenant_id: str) -> bool:
    parsed = parse_tenant_realtime_subject(subject)
    if parsed is None:
        return False
    _, subject_token = parsed
    return subject_token == tenant_subject_token(tenant_id)


def browser_realtime_subscription_subjects(tenant_id: str) -> tuple[str, ...]:
    return (
        *BROWSER_PUBLIC_REALTIME_CHANNELS,
        *(tenant_realtime_subject(subject, tenant_id) for subject in TENANT_SCOPED_REALTIME_CHANNELS),
    )


def control_plane_publish_subjects(subject: str, *, tenant_id: str | None = None) -> tuple[str, ...]:
    normalized_subject = str(subject).strip()
    if not is_control_plane_channel(normalized_subject):
        raise ValueError(f"subject is not a registered control-plane event channel: {subject}")
    if not is_tenant_scoped_realtime_channel(normalized_subject):
        return (normalized_subject,)
    if tenant_id is None:
        raise ValueError(f"tenant-scoped control-plane event '{normalized_subject}' must include tenant_id")
    return (normalized_subject, tenant_realtime_subject(normalized_subject, tenant_id))


def export_event_channel_contract() -> dict[str, object]:
    return {
        "control_plane_event_channels": list(CONTROL_PLANE_EVENT_CHANNELS),
        "browser_realtime_event_channels": list(CONTROL_PLANE_REALTIME_CHANNELS),
        "browser_public_realtime_event_channels": list(BROWSER_PUBLIC_REALTIME_CHANNELS),
        "tenant_scoped_realtime_event_channels": list(TENANT_SCOPED_REALTIME_CHANNELS),
        "control_event_envelope_contract": {
            "publisher_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
            "reserved_fields": list(CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS),
            "tenant_scoped_channels_require_tenant_id": True,
        },
        "tenant_realtime_subject_contract": {
            "segment": TENANT_REALTIME_SUBJECT_SEGMENT,
            "tenant_id_encoding": "utf8-hex",
            "subject_entrypoint": "backend.platform.events.channels.tenant_realtime_subject",
            "subscription_entrypoint": "backend.platform.events.channels.browser_realtime_subscription_subjects",
        },
        "internal_coordination_channels": list(REDIS_INTERNAL_SIGNAL_CHANNELS),
    }


__all__ = (
    "CHANNEL_ROUTING_MELTDOWN",
    "CHANNEL_SENTINEL_SIGNALS",
    "CHANNEL_SPECS",
    "CHANNEL_SWITCH_COMMANDS",
    "BROWSER_PUBLIC_REALTIME_CHANNELS",
    "CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS",
    "CONTROL_PLANE_EVENT_CHANNELS",
    "CONTROL_PLANE_REALTIME_CHANNELS",
    "EventChannelSpec",
    "REDIS_INTERNAL_SIGNAL_CHANNELS",
    "TENANT_SCOPED_REALTIME_CHANNELS",
    "TENANT_REALTIME_SUBJECT_SEGMENT",
    "browser_realtime_subscription_subjects",
    "control_plane_publish_subjects",
    "control_plane_subject_channel",
    "export_event_channel_contract",
    "is_control_plane_channel",
    "is_registered_control_plane_subject",
    "is_redis_internal_signal",
    "is_tenant_realtime_subject",
    "is_tenant_scoped_realtime_channel",
    "parse_tenant_realtime_subject",
    "subject_targets_tenant",
    "tenant_realtime_subject",
    "tenant_subject_token",
)
