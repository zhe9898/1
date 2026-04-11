from __future__ import annotations

import json

from backend.kernel.contracts.events_schema import (
    HardwareStateEventPayload,
    ReservationEventPayload,
    SwitchEventPayload,
    SwitchStateEventPayload,
    TriggerEventPayload,
    build_hardware_state_event,
    build_switch_event,
    build_switch_state_event,
)


def test_build_switch_event_shape() -> None:
    payload = build_switch_event("media_engine", "ON", reason="test", updated_by="unit")
    assert payload["switch"] == "media_engine"
    assert payload["name"] == "media_engine"
    assert payload["state"] == "ON"
    assert payload["reason"] == "test"
    assert "updated_at" in payload
    assert payload["updated_by"] == "unit"


def test_build_switch_state_event_shape() -> None:
    payload = build_switch_state_event("media_engine", "PENDING", reason="syncing", updated_by="sentinel")
    assert payload["switch"] == "media_engine"
    assert payload["state"] == "PENDING"
    assert payload["reason"] == "syncing"
    assert payload["updated_by"] == "sentinel"


def test_build_hardware_state_event_shape() -> None:
    payload = build_hardware_state_event("/mnt/media", "online", reason="mounted", uuid_val="disk-1", timestamp=123.0)
    assert payload["path"] == "/mnt/media"
    assert payload["state"] == "online"
    assert payload["reason"] == "mounted"
    assert payload["uuid"] == "disk-1"
    assert payload["timestamp"] == 123.0


def test_switch_event_payload_prefers_switch_field() -> None:
    payload = SwitchEventPayload(state="OFF", switch="jellyfin", name="legacy")
    assert payload.effective_switch_name() == "jellyfin"


def test_switch_event_payload_accepts_legacy_name_field() -> None:
    payload = SwitchEventPayload(state="ON", name="frigate")
    assert payload.effective_switch_name() == "frigate"


def test_switch_event_payload_parses_json_string() -> None:
    payload = SwitchEventPayload.from_redis_message(json.dumps({"switch": "llm", "state": "RESTART"}))
    assert payload is not None
    assert payload.effective_switch_name() == "llm"
    assert payload.state == "RESTART"


def test_switch_event_payload_rejects_invalid_messages() -> None:
    assert SwitchEventPayload.from_redis_message("not json") is None
    assert SwitchEventPayload.from_redis_message({}) is None
    assert SwitchEventPayload.from_redis_message({"no": "state"}) is None


def test_switch_state_event_payload_parses_json_string() -> None:
    payload = SwitchStateEventPayload.from_redis_message(json.dumps({"switch": "media", "state": "PENDING"}))
    assert payload is not None
    assert payload.effective_switch_name() == "media"
    assert payload.state == "PENDING"


def test_hardware_state_event_payload_parses_json_string() -> None:
    payload = HardwareStateEventPayload.from_redis_message(json.dumps({"path": "/mnt/media", "state": "online", "reason": "mounted"}))
    assert payload is not None
    assert payload.path == "/mnt/media"
    assert payload.state == "online"


def test_trigger_event_payload_parses_delivery_snapshot() -> None:
    payload = {
        "event_id": "evt-1",
        "action": "fired",
        "ts": "2026-04-01T00:00:00+00:00",
        "trigger": {
            "trigger_id": "trigger-1",
            "kind": "manual",
            "status": "active",
            "last_delivery_status": "delivered",
            "last_delivery_id": "delivery-1",
            "last_delivery_target_kind": "job",
            "last_delivery_target_id": "job-1",
        },
        "delivery": {
            "delivery_id": "delivery-1",
            "status": "delivered",
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
    payload = {"event_id": "evt-1", "action": "fired", "delivery": {"delivery_id": "delivery-1", "status": "delivered"}}
    assert TriggerEventPayload.from_redis_message(payload) is None


def test_reservation_event_payload_parses_snapshot() -> None:
    payload = {
        "event_id": "evt-2",
        "action": "created",
        "ts": "2026-04-01T00:00:00+00:00",
        "reservation": {
            "job_id": "job-1",
            "tenant_id": "default",
            "node_id": "node-a",
            "start_at": "2026-04-01T00:05:00+00:00",
            "end_at": "2026-04-01T00:10:00+00:00",
            "priority": 90,
            "cpu_cores": 4.0,
            "memory_mb": 2048.0,
            "gpu_vram_mb": 0.0,
            "slots": 1,
        },
        "reason": "dispatch_backfill_plan",
        "source": "dispatch",
    }
    parsed = ReservationEventPayload.from_redis_message(json.dumps(payload))
    assert parsed is not None
    assert parsed.action == "created"
    assert parsed.reservation.job_id == "job-1"
    assert parsed.reservation.node_id == "node-a"


def test_reservation_event_payload_rejects_missing_snapshot() -> None:
    assert ReservationEventPayload.from_redis_message({"event_id": "evt-2", "action": "created"}) is None
