"""Node approval workflow API endpoints.

Implements pending → approved → active enrollment lifecycle.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import get_current_admin, get_redis, get_tenant_db
from backend.core.errors import zen
from backend.core.redis_client import CHANNEL_NODE_EVENTS, RedisClient
from backend.models.node import Node

router = APIRouter(prefix="/api/v1/nodes", tags=["node-approval"])


class NodeApprovalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class NodeApprovalResponse(BaseModel):
    node_id: str
    name: str
    enrollment_status: str
    approved_by: str | None
    approved_at: str | None
    rejection_reason: str | None


def _to_approval_response(node: Node) -> NodeApprovalResponse:
    return NodeApprovalResponse(
        node_id=node.node_id,
        name=node.name,
        enrollment_status=node.enrollment_status,
        approved_by=getattr(node, "approved_by", None),
        approved_at=getattr(node, "approved_at", None) and getattr(node, "approved_at").isoformat(),
        rejection_reason=getattr(node, "rejection_reason", None),
    )


async def _get_node(db: AsyncSession, tenant_id: str, node_id: str) -> Node:
    result = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id))
    node = result.scalars().first()
    if node is None:
        raise zen("ZEN-NODE-4040", "Node not found", status_code=404)
    return node


@router.get("/pending", response_model=list[NodeApprovalResponse])
async def list_pending_nodes(
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[NodeApprovalResponse]:
    """List nodes awaiting approval (admin only)."""
    tenant_id = current_user["tenant_id"]
    result = await db.execute(
        select(Node)
        .where(
            Node.tenant_id == tenant_id,
            Node.enrollment_status == "pending",
        )
        .order_by(Node.registered_at.asc())
    )
    nodes = result.scalars().all()
    return [_to_approval_response(n) for n in nodes]


@router.post("/{node_id}/approve", response_model=NodeApprovalResponse)
async def approve_node(
    node_id: str,
    payload: NodeApprovalRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeApprovalResponse:
    """Approve a pending node (admin only).

    Transitions: pending → active
    Once approved the node can receive and execute jobs.
    """
    tenant_id = current_user["tenant_id"]
    node = await _get_node(db, tenant_id, node_id)

    if node.enrollment_status == "active":
        raise zen("ZEN-NODE-4091", "Node is already active", status_code=409)
    if node.enrollment_status == "revoked":
        raise zen("ZEN-NODE-4091", "Cannot approve a revoked node", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    node.enrollment_status = "active"
    node.drain_status = "active"
    # Store approval metadata in metadata_json (no schema change needed)
    meta = dict(node.metadata_json or {})
    meta["approved_by"] = current_user["username"]
    meta["approved_at"] = now.isoformat()
    meta["approval_reason"] = payload.reason or "approved by admin"
    node.metadata_json = meta
    node.updated_at = now

    await db.flush()
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "approved",
        {"node_id": node.node_id, "approved_by": current_user["username"]},
    )

    response = _to_approval_response(node)
    response.approved_by = current_user["username"]
    response.approved_at = now.isoformat()
    return response


@router.post("/{node_id}/reject", response_model=NodeApprovalResponse)
async def reject_node(
    node_id: str,
    payload: NodeApprovalRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeApprovalResponse:
    """Reject and revoke a pending node (admin only).

    Transitions: pending → revoked
    Rejected nodes cannot register again with the same node_id.
    """
    tenant_id = current_user["tenant_id"]
    node = await _get_node(db, tenant_id, node_id)

    if node.enrollment_status == "revoked":
        raise zen("ZEN-NODE-4091", "Node is already revoked", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    node.enrollment_status = "revoked"
    meta = dict(node.metadata_json or {})
    meta["rejected_by"] = current_user["username"]
    meta["rejected_at"] = now.isoformat()
    meta["rejection_reason"] = payload.reason or "rejected by admin"
    node.metadata_json = meta
    node.updated_at = now

    await db.flush()
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "rejected",
        {"node_id": node.node_id, "rejected_by": current_user["username"]},
    )

    response = _to_approval_response(node)
    response.rejection_reason = payload.reason
    return response
