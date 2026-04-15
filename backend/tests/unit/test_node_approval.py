from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.control_plane.adapters.node_approval import list_pending_nodes
from backend.models.node import Node


def _scalar_result(values: list[Node]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _node(node_id: str) -> Node:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    return Node(
        tenant_id="tenant-a",
        node_id=node_id,
        name=node_id,
        node_type="runner",
        profile="go-runner",
        executor="unknown",
        os="linux",
        arch="amd64",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        status="online",
        max_concurrency=1,
        cpu_cores=1,
        memory_mb=1024,
        gpu_vram_mb=0,
        storage_mb=1024,
        drain_status="active",
        enrollment_status="pending",
        capabilities=[],
        accepted_kinds=[],
        worker_pools=[],
        metadata_json={},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_pending_nodes_applies_limit_and_offset() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result([_node("node-1")])

    response = await list_pending_nodes(
        limit=25,
        offset=10,
        current_user={"tenant_id": "tenant-a", "username": "admin"},
        db=db,
    )

    stmt = db.execute.await_args.args[0]
    rendered = str(stmt)
    assert " LIMIT " in rendered.upper()
    assert " OFFSET " in rendered.upper()
    assert len(response) == 1
