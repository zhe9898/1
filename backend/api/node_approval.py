"""Node approval workflow API endpoints."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import get_current_admin, get_redis, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.kernel.topology.node_enrollment_service import NodeEnrollmentService
from backend.models.node import Node
from backend.platform.redis.client import CHANNEL_NODE_EVENTS, RedisClient

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
    meta = dict(node.metadata_json or {})
    approved_by = meta.get("approved_by")
    approved_at = meta.get("approved_at")
    rejection_reason = meta.get("rejection_reason")
    return NodeApprovalResponse(
        node_id=node.node_id,
        name=node.name,
        enrollment_status=node.enrollment_status,
        approved_by=str(approved_by) if isinstance(approved_by, str) else None,
        approved_at=str(approved_at) if isinstance(approved_at, str) else None,
        rejection_reason=str(rejection_reason) if isinstance(rejection_reason, str) else None,
    )


async def _get_node(db: AsyncSession, tenant_id: str, node_id: str) -> Node:
    result = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id))
    node = result.scalars().first()
    if node is None:
        raise zen("ZEN-NODE-4040", "Node not found", status_code=404)
    return node


@router.get("/pending", response_model=list[NodeApprovalResponse])
async def list_pending_nodes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=10_000),
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
        .limit(limit)
        .offset(offset)
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

    Transitions: pending 鈫?approved
    Once approved the node can receive and execute jobs.
    """
    tenant_id = current_user["tenant_id"]
    node = await _get_node(db, tenant_id, node_id)

    if node.enrollment_status == "approved":
        raise zen("ZEN-NODE-4091", "Node is already approved", status_code=409)
    if node.enrollment_status == "rejected":
        raise zen("ZEN-NODE-4091", "Cannot approve a rejected node", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    NodeEnrollmentService.approve(
        node,
        actor=current_user["username"],
        now=now,
        reason=payload.reason,
    )

    await db.flush()
    response = _to_approval_response(node)
    await db.commit()
    await publish_control_event(
        CHANNEL_NODE_EVENTS,
        "approved",
        {"node_id": node.node_id, "approved_by": current_user["username"]},
    )
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

    Transitions: pending 鈫?rejected
    Rejected nodes cannot register again with the same node_id.
    """
    tenant_id = current_user["tenant_id"]
    node = await _get_node(db, tenant_id, node_id)

    if node.enrollment_status == "rejected":
        raise zen("ZEN-NODE-4091", "Node is already rejected", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    NodeEnrollmentService.reject(
        node,
        actor=current_user["username"],
        now=now,
        reason=payload.reason,
    )

    await db.flush()
    response = _to_approval_response(node)
    await db.commit()
    await publish_control_event(
        CHANNEL_NODE_EVENTS,
        "rejected",
        {"node_id": node.node_id, "rejected_by": current_user["username"]},
    )
    return response
