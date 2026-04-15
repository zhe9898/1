from __future__ import annotations

import json
import time
from typing import Any

import pytest
import requests

from backend.kernel.contracts.events_schema import build_hardware_state_event, build_switch_state_event
from backend.platform.events.publisher import SyncEventPublisher
from tests.integration.conftest import BASE_URL, GATEWAY_OK, NATS_OK, NATS_URL, _no_proxy

_STREAM_TIMEOUT_SECONDS = 15


def _read_next_sse_frame(lines_iter: Any, deadline: float) -> dict[str, str] | None:
    event_name = "message"
    data_lines: list[str] = []

    while time.time() < deadline:
        try:
            line = next(lines_iter)
        except StopIteration:
            return None
        if line is None:
            continue
        text = str(line)
        if text == "":
            if data_lines or event_name != "message":
                return {"event": event_name, "data": "\n".join(data_lines)}
            continue
        if text.startswith(":"):
            continue
        if text.startswith("event:"):
            event_name = text.split(":", 1)[1].strip()
            continue
        if text.startswith("data:"):
            data_lines.append(text.split(":", 1)[1].lstrip())

    return None


@pytest.mark.skipif(not (GATEWAY_OK and NATS_OK), reason="Gateway and NATS are required")
def test_control_plane_formal_events_flow_from_nats_to_sse() -> None:
    publisher = SyncEventPublisher(
        settings={
            "event_bus_backend": "nats",
            "nats_url": NATS_URL,
            "nats_connect_timeout": 5.0,
        }
    )
    hardware_payload = build_hardware_state_event("/mnt/test-nats", "online", reason="integration-smoke", uuid_val="disk-nats")
    switch_payload = build_switch_state_event("media", "PENDING", reason="integration-smoke", updated_by="integration-test")

    try:
        with requests.get(f"{BASE_URL}/api/v1/events", stream=True, timeout=_STREAM_TIMEOUT_SECONDS, proxies=_no_proxy()) as response:
            assert response.status_code == 200

            deadline = time.time() + _STREAM_TIMEOUT_SECONDS
            lines_iter = response.iter_lines(decode_unicode=True)

            connected = False
            while time.time() < deadline and not connected:
                frame = _read_next_sse_frame(lines_iter, deadline)
                if frame is not None and frame["event"] == "connected":
                    connected = True
                    break
            assert connected, "SSE did not emit the initial connected frame before publish"

            assert publisher.publish_control("hardware:events", json.dumps(hardware_payload, ensure_ascii=False)) is True
            assert publisher.publish_control("switch:events", json.dumps(switch_payload, ensure_ascii=False)) is True

            seen_hardware = False
            seen_switch = False
            while time.time() < deadline and not (seen_hardware and seen_switch):
                frame = _read_next_sse_frame(lines_iter, deadline)
                if frame is None:
                    break
                if frame["event"] == "hardware:events":
                    payload = json.loads(frame["data"])
                    if payload.get("path") == hardware_payload["path"] and payload.get("state") == hardware_payload["state"]:
                        seen_hardware = True
                if frame["event"] == "switch:events":
                    payload = json.loads(frame["data"])
                    if payload.get("switch") == switch_payload["switch"] and payload.get("state") == switch_payload["state"]:
                        seen_switch = True

            assert seen_hardware, "hardware:events did not reach /api/v1/events over NATS"
            assert seen_switch, "switch:events did not reach /api/v1/events over NATS"
    finally:
        publisher.close()
