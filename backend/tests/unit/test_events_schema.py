"""switch:events 契约：序列化/反序列化与 effective_switch_name 兼容性。"""

from __future__ import annotations

import json

from backend.core.events_schema import (
    SwitchEventPayload,
    TriggerEventPayload,
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


def test_trigger_event_payload_parses_delivery_snapshot() -> None:
    payload = {
        "event_id": "evt-1",
        "action": "fired",
        "ts": "2026-04-01T00:00:00+00:00",
        "trigger": {
            "trigger_id": "trigger-1",
            "kind": "manual",
            "status": "active",
            "last_delivery_status": "accepted",
            "last_delivery_id": "delivery-1",
            "last_delivery_target_kind": "job",
            "last_delivery_target_id": "job-1",
        },
        "delivery": {
            "delivery_id": "delivery-1",
            "status": "accepted",
            "source_kind": "manual",
            "target_kind": "job",
            "target_id": "job-1",
            "error_message": None,
            "fired_at": "2026-04-01T00:00:00+00:00",
            "delivered_at": "2026-04-01T00:00:01+00:00",
        },
    }

    parsed = TriggerEventPayload.from_redis_message(json.dumps(payload))

    assert parsed is not None
    assert parsed.action == "fired"
    assert parsed.trigger.trigger_id == "trigger-1"
    assert parsed.delivery is not None
    assert parsed.delivery.delivery_id == "delivery-1"


def test_trigger_event_payload_rejects_missing_trigger_snapshot() -> None:
    payload = {"event_id": "evt-1", "action": "fired", "delivery": {"delivery_id": "delivery-1", "status": "accepted"}}

    assert TriggerEventPayload.from_redis_message(payload) is None
