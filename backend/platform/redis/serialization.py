from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from backend.platform.redis.types import NodeInfo


def node_to_redis(info: NodeInfo) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in info.items():
        if value is None:
            continue
        if key in {"capabilities", "resources", "load"}:
            out[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        elif key == "last_seen":
            out[key] = str(float(cast(Any, value)))
        else:
            out[key] = str(value)
    return out


def redis_to_node(data: dict[str, str]) -> NodeInfo:
    out: dict[str, object] = {}
    for key, value in data.items():
        if not value:
            continue
        if key in {"capabilities", "resources", "load"}:
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                out[key] = value
        elif key == "last_seen":
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = 0.0
        else:
            out[key] = value
    return cast(NodeInfo, out)


def as_redis_hset_mapping(data: dict[str, str]) -> Mapping[str | bytes, bytes | float | int | str]:
    return cast(Mapping[str | bytes, bytes | float | int | str], data)


__all__ = ("as_redis_hset_mapping", "node_to_redis", "redis_to_node")
