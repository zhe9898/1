from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.kernel.topology.node_auth import authenticate_node_request, hash_node_token
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _node(**overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
        node_id="node-a",
        name="runner-a",
        node_type="runner",
        address=None,
        profile="go-runner",
        executor="go-native",
        os="darwin",
        arch="arm64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash="",
        auth_token_version=1,
        enrollment_status="approved",
        status="online",
        capabilities=["connector.invoke"],
        metadata_json={"runtime": "go"},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _hash_token(monkeypatch: pytest.MonkeyPatch, token: str) -> str:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    return hash_node_token(token)


@pytest.mark.asyncio
async def test_authenticate_node_request_rejects_unknown_node() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    with pytest.raises(HTTPException) as exc:
        await authenticate_node_request(db, "node-a", "node-token", require_active=False)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_node_request_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(_node(auth_token_hash=_hash_token(monkeypatch, "node-token")))

    with pytest.raises(HTTPException) as exc:
        await authenticate_node_request(db, "node-a", "wrong-token", require_active=False)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_node_request_rejects_pending_node_for_job_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(
        _node(enrollment_status="pending", auth_token_hash=_hash_token(monkeypatch, "node-token")),
    )

    with pytest.raises(HTTPException) as exc:
        await authenticate_node_request(db, "node-a", "node-token", require_active=True)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticate_node_request_rejects_rejected_node(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(
        _node(enrollment_status="rejected", auth_token_hash=_hash_token(monkeypatch, "node-token")),
    )

    with pytest.raises(HTTPException) as exc:
        await authenticate_node_request(db, "node-a", "node-token", require_active=False)

    assert exc.value.status_code == 401
