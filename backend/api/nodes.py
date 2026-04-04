"""
ZEN70 Nodes API 鈥?Route handlers only.

Models live in nodes_models.py; helpers live in nodes_helpers.py.
This module wires them together behind FastAPI route definitions and
re-exports all public names so existing ``from backend.api.nodes import 鈥`
statements keep working.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_admin,
    get_current_user,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.api.nodes_helpers import (  # noqa: F401 鈥?re-exported for consumers
    _apply_contract,
    _bootstrap_notes,
    _bootstrap_token_value,
    _build_bootstrap_commands,
    _build_bootstrap_receipts,
    _build_node_actions,
    _get_active_lease_counts,
    _get_node_by_id,
    _matches_node_list_filters,
    _provision_token,
    _resource_schema,
    _to_response,
)

# 鈹€鈹€ Re-exports (backward-compat) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
from backend.api.nodes_models import (  # noqa: F401 鈥?re-exported for consumers
    BootstrapReceipt,
    NodeContractPayload,
    NodeDrainRequest,
    NodeHeartbeatRequest,
    NodeProvisionRequest,
    NodeProvisionResponse,
    NodeRegisterRequest,
    NodeResponse,
    _utcnow,
)
from backend.api.ui_contracts import ResourceSchemaResponse
from backend.core.errors import zen
from backend.core.node_auth import authenticate_node_request
from backend.core.quota import check_node_quota
from backend.core.redis_client import CHANNEL_NODE_EVENTS, RedisClient
from backend.models.node import Node

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_node_schema(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=NodeProvisionResponse)
async def provision_node(
    payload: NodeProvisionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeProvisionResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    await check_node_quota(db, tenant_id)
    existing = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == payload.node_id))
    if existing.scalars().first() is not None:
        raise zen(
            "ZEN-NODE-4090",
            "node already exists",
            status_code=409,
            recovery_hint="Use token rotation for an existing node instead of provisioning again",
            details={"node_id": payload.node_id},
        )

    now = _utcnow()
    node = Node(
        tenant_id=tenant_id,
        node_id=payload.node_id,
        registered_at=now,
        last_seen_at=now,
        enrollment_status="pending",
        status="offline",
        max_concurrency=payload.max_concurrency,
        drain_status="active",
    )
    _apply_contract(node, payload.model_copy(update={"tenant_id": tenant_id}), "offline", now)
    node.enrollment_status = "pending"
    token, version = _provision_token(node)
    db.add(node)
    await db.flush()
    return NodeProvisionResponse(
        node=_to_response(node, now=now),
        node_token=_bootstrap_token_value(token),
        auth_token_version=version,
        bootstrap_commands=_build_bootstrap_commands(node, token),
        bootstrap_notes=_bootstrap_notes(),
        bootstrap_receipts=_build_bootstrap_receipts(node, token),
    )


@router.post("/{id}/token", response_model=NodeProvisionResponse)
async def rotate_node_token(
    id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeProvisionResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    token, version = _provision_token(node)
    node.enrollment_status = "pending"
    node.status = "offline"
    now = _utcnow()
    node.updated_at = now
    await db.flush()
    return NodeProvisionResponse(
        node=_to_response(node, now=now),
        node_token=_bootstrap_token_value(token),
        auth_token_version=version,
        bootstrap_commands=_build_bootstrap_commands(node, token),
        bootstrap_notes=_bootstrap_notes(),
        bootstrap_receipts=_build_bootstrap_receipts(node, token),
    )


@router.post("/{id}/revoke", response_model=NodeResponse)
async def revoke_node(
    id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.auth_token_hash = None
    node.auth_token_version = int(node.auth_token_version or 0) + 1
    node.enrollment_status = "revoked"
    node.status = "offline"
    now = _utcnow()
    node.updated_at = now
    await db.flush()
    return _to_response(node, now=now)


@router.post("/{id}/drain", response_model=NodeResponse)
async def drain_node(
    id: str,
    payload: NodeDrainRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.drain_status = "draining"
    node.health_reason = payload.reason or node.health_reason
    node.updated_at = _utcnow()
    await db.flush()
    response = _to_response(node, now=node.updated_at)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "drain",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/undrain", response_model=NodeResponse)
async def undrain_node(
    id: str,
    payload: NodeDrainRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.drain_status = "active"
    node.health_reason = payload.reason
    node.updated_at = _utcnow()
    await db.flush()
    response = _to_response(node, now=node.updated_at)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "undrain",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/register", response_model=NodeResponse)
async def register_node(
    payload: NodeRegisterRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> NodeResponse:
    node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=False,
        tenant_id=payload.tenant_id,
    )
    event_action = "updated" if node.enrollment_status == "active" else "registered"
    now = _utcnow()
    _apply_contract(node, payload, "online", now)

    # 鈹€鈹€ Executor contract validation (non-blocking) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    from backend.core.executor_registry import get_executor_registry

    _exec_warnings = get_executor_registry().validate_node_executor(
        node.executor,
        memory_mb=node.memory_mb,
        cpu_cores=node.cpu_cores,
        gpu_vram_mb=node.gpu_vram_mb,
    )
    if _exec_warnings:
        import logging as _log

        _log.getLogger("api.nodes").warning(
            "Executor contract warnings for node %s: %s",
            node.node_id,
            "; ".join(_exec_warnings),
        )

    # Enrollment approval: new nodes stay pending until admin approves.
    # Re-registration of already-active nodes keeps active status.
    if node.enrollment_status not in ("active",):
        node.enrollment_status = "pending"
    node.drain_status = "active"
    node.health_reason = None

    await db.flush()
    response = _to_response(node, now=now)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        event_action,
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/heartbeat", response_model=NodeResponse)
async def heartbeat_node(
    payload: NodeHeartbeatRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> NodeResponse:
    node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=False,
        tenant_id=payload.tenant_id,
    )

    # ADR-0047 WP-P0: heartbeat must not bypass enrollment approval.
    # pending -> active must only happen through admin approval endpoint.
    # revoked nodes must be reprovisioned and registered with a new token.
    if node.enrollment_status == "pending":
        raise zen(
            "ZEN-NODE-4031",
            "Node is pending enrollment approval and cannot send heartbeats yet",
            status_code=403,
            recovery_hint="Wait for an admin to approve this node via POST /api/v1/nodes/{node_id}/approve",
            details={"node_id": node.node_id, "enrollment_status": node.enrollment_status},
        )
    if node.enrollment_status == "revoked":
        raise zen(
            "ZEN-NODE-4032",
            "Revoked node cannot send heartbeats; provision and re-register with a new token",
            status_code=403,
            recovery_hint="Provision a new node token and re-register before sending heartbeats",
            details={"node_id": node.node_id, "enrollment_status": node.enrollment_status},
        )
    # 鈹€鈹€ 浠?active 鑺傜偣鍙画娲伙紝enrollment_status 淇濇寔涓嶅彉 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    now = _utcnow()
    _apply_contract(node, payload, payload.status, now)
    node.health_reason = payload.health_reason

    await db.flush()
    active_counts = await _get_active_lease_counts(db, tenant_id=payload.tenant_id, node_ids=[node.node_id], now=now)
    response = _to_response(node, active_lease_count=active_counts.get(node.node_id, 0), now=now)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "heartbeat",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.get("", response_model=list[NodeResponse])
async def list_nodes(
    node_id: str | None = None,
    node_type: str | None = None,
    executor: str | None = None,
    os: str | None = None,
    zone: str | None = None,
    enrollment_status: str | None = None,
    drain_status: str | None = None,
    heartbeat_state: str | None = None,
    capacity_state: str | None = None,
    attention: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[NodeResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = select(Node).where(Node.tenant_id == tenant_id)
    if node_id:
        query = query.where(Node.node_id == node_id)
    if node_type:
        query = query.where(Node.node_type == node_type)
    if executor:
        query = query.where(Node.executor == executor)
    if os:
        query = query.where(Node.os == os)
    if zone:
        query = query.where(Node.zone == zone)
    if enrollment_status:
        query = query.where(Node.enrollment_status == enrollment_status)
    result = await db.execute(query.order_by(Node.last_seen_at.desc()))
    nodes = list(result.scalars().all())
    now = _utcnow()
    counts = await _get_active_lease_counts(db, tenant_id=tenant_id, node_ids=[node.node_id for node in nodes], now=now)
    filtered = [
        node
        for node in nodes
        if _matches_node_list_filters(
            node,
            active_lease_count=counts.get(node.node_id, 0),
            now=now,
            node_type=node_type,
            executor=executor,
            os_name=os,
            zone=zone,
            enrollment_status=enrollment_status,
            drain_status=drain_status,
            heartbeat_state=heartbeat_state,
            capacity_state=capacity_state,
            attention=attention,
        )
    ]
    return [_to_response(node, active_lease_count=counts.get(node.node_id, 0), now=now) for node in filtered]


@router.get("/{id}", response_model=NodeResponse)
async def get_node(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    now = _utcnow()
    counts = await _get_active_lease_counts(db, tenant_id=tenant_id, node_ids=[node.node_id], now=now)
    return _to_response(node, active_lease_count=counts.get(node.node_id, 0), now=now)
