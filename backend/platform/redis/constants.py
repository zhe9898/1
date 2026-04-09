"""Redis channel and key contracts owned by the platform infrastructure layer."""

from __future__ import annotations

CHANNEL_SWITCH_EVENTS: str = "switch:events"
CHANNEL_HARDWARE_EVENTS: str = "hardware:events"
CHANNEL_NODE_EVENTS: str = "node:events"
CHANNEL_JOB_EVENTS: str = "job:events"
CHANNEL_CONNECTOR_EVENTS: str = "connector:events"
CHANNEL_TRIGGER_EVENTS: str = "trigger:events"
CHANNEL_RESERVATION_EVENTS: str = "reservation:events"

KEY_CAPABILITIES: str = "capabilities"
KEY_NODE_PREFIX: str = "cluster:nodes:"
KEY_SWITCH_PREFIX: str = "switch:"
KEY_SYSTEM_READONLY_DISK: str = "zen70:disk:readonly"
KEY_SYSTEM_UPS_STATUS: str = "zen70:topology:ups"
KEY_HW_PREFIX: str = "hw:"
KEY_HARDWARE_GPU_STATE: str = "hw:gpu"
KEY_LOCK_PREFIX: str = "lock:"
KEY_AUTH_CHALLENGE_PREFIX: str = "auth:challenge:"
KEY_DB_MIGRATION_LOCK: str = "zen70:DB_MIGRATION_LOCK"
KEY_INVITE_TOKEN_PREFIX: str = "zen70:invite:"
KEY_CAPABILITIES_PREFIX: str = "zen70:capability:"
KEY_DISK_TAINT: str = "zen70:taint:disk_pressure"
KEY_SENTINEL_OVERRIDE_PREFIX: str = "zen70:sentinel:override:"

__all__ = (
    "CHANNEL_CONNECTOR_EVENTS",
    "CHANNEL_HARDWARE_EVENTS",
    "CHANNEL_JOB_EVENTS",
    "CHANNEL_NODE_EVENTS",
    "CHANNEL_RESERVATION_EVENTS",
    "CHANNEL_SWITCH_EVENTS",
    "CHANNEL_TRIGGER_EVENTS",
    "KEY_AUTH_CHALLENGE_PREFIX",
    "KEY_CAPABILITIES",
    "KEY_CAPABILITIES_PREFIX",
    "KEY_DB_MIGRATION_LOCK",
    "KEY_DISK_TAINT",
    "KEY_HARDWARE_GPU_STATE",
    "KEY_HW_PREFIX",
    "KEY_INVITE_TOKEN_PREFIX",
    "KEY_LOCK_PREFIX",
    "KEY_NODE_PREFIX",
    "KEY_SENTINEL_OVERRIDE_PREFIX",
    "KEY_SWITCH_PREFIX",
    "KEY_SYSTEM_READONLY_DISK",
    "KEY_SYSTEM_UPS_STATUS",
)
