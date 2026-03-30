"""switch:events 契约：序列化/反序列化与 effective_switch_name 兼容性。"""

from __future__ import annotations

import json

from backend.core.events_schema import (
    SwitchEventPayload,
    build_switch_event,
)


def test_build_switch_event_shape() -> None:
    payload = build_switch_event("media_engine", "ON", reason="test", updated_by="unit")  # type: ignore[func-returns-value]
    assert payload["switch"] == "media_engine"
    assert payload["name"] == "media_engine"
    assert payload["state"] == "ON"
    assert payload["reason"] == "test"
    assert "updated_at" in payload
    assert payload["updated_by"] == "unit"


def test_from_redis_message_dict_with_switch() -> None:
    obj = {"switch": "jellyfin", "state": "OFF"}
    p = SwitchEventPayload.from_redis_message(obj)  # type: ignore[arg-type]
    assert p is not None
    assert p.effective_switch_name() == "jellyfin"
    assert p.state == "OFF"


def test_from_redis_message_dict_with_name_only() -> None:
    obj = {"name": "frigate", "state": "ON"}
    p = SwitchEventPayload.from_redis_message(obj)  # type: ignore[arg-type]
    assert p is not None
    assert p.effective_switch_name() == "frigate"


def test_from_redis_message_string() -> None:
    s = json.dumps({"switch": "llm", "state": "RESTART"})
    p = SwitchEventPayload.from_redis_message(s)
    assert p is not None
    assert p.effective_switch_name() == "llm"
    assert p.state == "RESTART"


def test_from_redis_message_invalid_returns_none() -> None:
    assert SwitchEventPayload.from_redis_message("not json") is None
    assert SwitchEventPayload.from_redis_message({}) is None
    assert SwitchEventPayload.from_redis_message({"no": "state"}) is None
