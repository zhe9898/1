from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.control_plane.adapters.control_events import publish_control_event
from backend.platform.events.channels import tenant_realtime_subject
from backend.platform.redis.constants import CHANNEL_HARDWARE_EVENTS, CHANNEL_JOB_EVENTS


@pytest.mark.asyncio
async def test_publish_control_event_publishes_base_and_tenant_subjects_for_tenant_scoped_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("backend.control_plane.adapters.control_events.get_runtime_event_bus", lambda: event_bus)

    await publish_control_event(
        CHANNEL_JOB_EVENTS,
        "updated",
        {"job": {"job_id": "job-1"}},
        tenant_id="tenant-a",
    )

    subjects = [call.args[0] for call in event_bus.publish.await_args_list]
    payloads = [json.loads(call.args[1]) for call in event_bus.publish.await_args_list]

    assert subjects == [CHANNEL_JOB_EVENTS, tenant_realtime_subject(CHANNEL_JOB_EVENTS, "tenant-a")]
    assert payloads[0]["tenant_id"] == "tenant-a"
    assert payloads[1] == payloads[0]


@pytest.mark.asyncio
async def test_publish_control_event_keeps_public_channels_single_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("backend.control_plane.adapters.control_events.get_runtime_event_bus", lambda: event_bus)

    await publish_control_event(
        CHANNEL_HARDWARE_EVENTS,
        "updated",
        {"hardware": {"hardware_id": "hw-1"}},
    )

    subjects = [call.args[0] for call in event_bus.publish.await_args_list]
    assert subjects == [CHANNEL_HARDWARE_EVENTS]


@pytest.mark.asyncio
async def test_publish_control_event_rejects_reserved_envelope_fields_in_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = SimpleNamespace(publish=AsyncMock())
    monkeypatch.setattr("backend.control_plane.adapters.control_events.get_runtime_event_bus", lambda: event_bus)

    with pytest.raises(ValueError, match="reserved envelope fields"):
        await publish_control_event(
            CHANNEL_JOB_EVENTS,
            "updated",
            {"action": "override"},
            tenant_id="tenant-a",
        )

    event_bus.publish.assert_not_awaited()
