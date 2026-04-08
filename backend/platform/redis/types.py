from __future__ import annotations

from typing import TypedDict


class Capability(TypedDict, total=False):
    endpoint: str
    models: list[str] | None
    status: str
    reason: str | None


class NodeInfo(TypedDict, total=False):
    node_id: str
    hostname: str
    role: str
    capabilities: list[str]
    resources: dict[str, object]
    endpoint: str
    last_seen: float
    load: dict[str, float]


class SwitchState(TypedDict, total=False):
    state: str
    reason: str | None
    updated_at: float
    updated_by: str | None


class HardwareState(TypedDict, total=False):
    path: str
    uuid: str | None
    state: str
    timestamp: float
    reason: str | None


__all__ = ("Capability", "NodeInfo", "SwitchState", "HardwareState")
