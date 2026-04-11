from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.control_plane.adapters.agent import AgentActionItem, AgentActRequest, agent_act


@pytest.mark.asyncio
async def test_agent_act_relies_on_set_switch_single_publish_path() -> None:
    redis = SimpleNamespace(
        switches=SimpleNamespace(set=AsyncMock(return_value=True)),
        pubsub=SimpleNamespace(publish=AsyncMock()),
    )

    with (
        patch("backend.control_plane.adapters.agent._agent_enabled", return_value=True),
        patch("backend.control_plane.adapters.agent._is_feature_flag_enabled", new=AsyncMock(return_value=True)),
        patch("backend.control_plane.adapters.agent._get_allowed_switches", return_value=["media"]),
    ):
        response = await agent_act(
            request=AsyncMock(),
            body=AgentActRequest(
                actions=[AgentActionItem(switch="media", state="OFF", reason="test")],
                idempotency_key=None,
            ),
            redis=redis,
            db=AsyncMock(),
            x_idempotency_key=None,
            current_user={"sub": "admin-1", "role": "admin", "tenant_id": "default"},
        )

    redis.switches.set.assert_awaited_once_with(
        "media",
        "OFF",
        reason="test",
        updated_by="agent:admin-1",
    )
    redis.pubsub.publish.assert_not_called()
    assert response.results[0].ok is True
