"""
ZEN70 Nodes API 鈥?Helper functions.

Split from nodes.py for maintainability.  Contains response builders,
contract application, filtering, and database helpers.
Schema and bootstrap helpers are in ``nodes_schema.py``.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.action_contracts import ControlAction, optional_reason_field
from backend.api.ui_contracts import StatusView
from backend.core.compatibility_adapter import normalize_persisted_status
from backend.core.control_plane_state import (
    node_attention_reason,
    node_capacity_state,
    node_capacity_state_view,
    node_drain_status_view,
    node_enrollment_status_view,
    node_heartbeat_state,
    node_heartbeat_state_view,
    node_status_view,
)
from backend.core.errors import zen
from backend.core.node_auth import generate_node_token, hash_node_token
from backend.core.protocol_version import validate_lease_version, validate_protocol_version
from backend.core.worker_pool import infer_node_worker_pools
from backend.models.job import Job
from backend.models.node import Node

from .nodes_models import (
    NodeContractPayload,
    NodeResponse,
    _utcnow,
)


def _build_node_actions(node: Node) -> list[ControlAction]:
    enrollment_status = normalize_persisted_status("nodes.enrollment_status", node.enrollment_status) or "pending"
    can_drain = enrollment_status == "approved" and (node.drain_status or "active") == "active"
    can_undrain = (node.drain_status or "active") != "active"
    can_rotate = enrollment_status != "rejected"
    can_revoke = enrollment_status != "rejected"
    return [
        ControlAction(
            key="rotate_token",
            label="Rotate Token",
            endpoint=f"/v1/nodes/{node.node_id}/token",
            enabled=can_rotate,
            reason=None if can_rotate else "Rejected nodes must be reprovisioned before rotating credentials",
            confirmation="Generate a new node token and invalidate the old one?",
        ),
        ControlAction(
            key="revoke",
            label="Revoke",
            endpoint=f"/v1/nodes/{node.node_id}/revoke",
            enabled=can_revoke,
            reason=None if can_revoke else "Node is already rejected",
            confirmation="Revoke this node and block it from pulling more work?",
        ),
        ControlAction(
            key="drain",
            label="Drain",
            endpoint=f"/v1/nodes/{node.node_id}/drain",
            enabled=can_drain,
            reason=(
                None
                if can_drain
                else ("Node must be approved before it can be drained" if enrollment_status != "approved" else "Node is already draining")
            ),
            confirmation="Stop assigning new jobs to this node?",
            fields=[optional_reason_field()],
        ),
        ControlAction(
            key="undrain",
            label="Undrain",
            endpoint=f"/v1/nodes/{node.node_id}/undrain",
            enabled=can_undrain,
            reason=None if can_undrain else "Node is already active",
            confirmation="Return this node to the scheduler?",
            fields=[optional_reason_field()],
        ),
    ]


def _to_response(node: Node, *, active_lease_count: int = 0, now: datetime.datetime | None = None) -> NodeResponse:
    current_time = now or _utcnow()
    enrollment_status = normalize_persisted_status("nodes.enrollment_status", node.enrollment_status) or "pending"
    max_concurrency = max(int(node.max_concurrency or 1), 1)
    drain_status = node.drain_status or "active"
    heartbeat_state = node_heartbeat_state(node.last_seen_at, current_time)
    capacity_state = node_capacity_state(active_lease_count, max_concurrency)
    return NodeResponse(
        node_id=node.node_id,
        name=node.name,
        node_type=node.node_type,
        address=node.address,
        profile=node.profile,
        executor=node.executor,
        os=node.os,
        arch=node.arch,
        zone=node.zone,
        protocol_version=node.protocol_version,
        lease_version=node.lease_version,
        agent_version=node.agent_version,
        max_concurrency=max_concurrency,
        active_lease_count=max(int(active_lease_count), 0),
        cpu_cores=max(int(node.cpu_cores or 0), 0),
        memory_mb=max(int(node.memory_mb or 0), 0),
        gpu_vram_mb=max(int(node.gpu_vram_mb or 0), 0),
        storage_mb=max(int(node.storage_mb or 0), 0),
        drain_status=drain_status,
        drain_status_view=StatusView(**node_drain_status_view(drain_status)),
        health_reason=node.health_reason,
        heartbeat_state=heartbeat_state,
        heartbeat_state_view=StatusView(**node_heartbeat_state_view(heartbeat_state)),
        capacity_state=capacity_state,
        capacity_state_view=StatusView(**node_capacity_state_view(capacity_state)),
        attention_reason=node_attention_reason(
            enrollment_status=enrollment_status,
            status=node.status,
            drain_status=drain_status,
            heartbeat_state=heartbeat_state,
            capacity_state=capacity_state,
            health_reason=node.health_reason,
        ),
        enrollment_status=enrollment_status,
        enrollment_status_view=StatusView(**node_enrollment_status_view(enrollment_status)),
        status=node.status,
        status_view=StatusView(**node_status_view(node.status)),
        capabilities=list(node.capabilities or []),
        worker_pools=infer_node_worker_pools(
            worker_pools=getattr(node, "worker_pools", None),
            accepted_kinds=getattr(node, "accepted_kinds", None),
            capabilities=node.capabilities,
            gpu_vram_mb=node.gpu_vram_mb,
            profile=node.profile,
            metadata=dict(node.metadata_json or {}),
        ),
        metadata=dict(node.metadata_json or {}),
        actions=_build_node_actions(node),
        registered_at=node.registered_at,
        last_seen_at=node.last_seen_at,
    )


def _apply_contract(node: Node, payload: NodeContractPayload, status: str, now: datetime.datetime) -> None:
    # Validate protocol versions before applying contract
    try:
        validated_protocol_version = validate_protocol_version(payload.protocol_version)
        validated_lease_version = validate_lease_version(payload.lease_version)
    except ValueError as e:
        raise zen(
            "ZEN-NODE-4001",
            str(e),
            status_code=400,
            recovery_hint="Upgrade runner-agent to a supported version",
            details={
                "node_id": payload.node_id,
                "protocol_version": payload.protocol_version,
                "lease_version": payload.lease_version,
            },
        ) from e

    node.tenant_id = payload.tenant_id
    node.name = payload.name
    node.node_type = payload.node_type
    node.address = payload.address
    node.profile = payload.profile
    node.executor = payload.executor
    node.os = payload.os
    node.arch = payload.arch
    node.zone = payload.zone
    node.protocol_version = validated_protocol_version
    node.lease_version = validated_lease_version
    node.agent_version = payload.agent_version
    node.max_concurrency = payload.max_concurrency
    node.cpu_cores = payload.cpu_cores
    node.memory_mb = payload.memory_mb
    node.gpu_vram_mb = payload.gpu_vram_mb
    node.storage_mb = payload.storage_mb
    node.status = status
    node.capabilities = payload.capabilities
    node.metadata_json = payload.metadata
    # Edge computing attributes (optional, with defaults)
    node.accepted_kinds = getattr(payload, "accepted_kinds", None) or []
    try:
        node.worker_pools = infer_node_worker_pools(
            worker_pools=getattr(payload, "worker_pools", None),
            accepted_kinds=node.accepted_kinds,
            capabilities=node.capabilities,
            gpu_vram_mb=node.gpu_vram_mb,
            profile=node.profile,
            metadata=node.metadata_json,
            strict=True,
        )
    except ValueError as e:
        raise zen(
            "ZEN-NODE-4002",
            str(e),
            status_code=400,
            recovery_hint="Use worker pool names that match the published node contract",
            details={"node_id": payload.node_id, "worker_pools": getattr(payload, "worker_pools", None)},
        ) from e
    node.network_latency_ms = getattr(payload, "network_latency_ms", None)
    node.bandwidth_mbps = getattr(payload, "bandwidth_mbps", None)
    node.cached_data_keys = getattr(payload, "cached_data_keys", None) or []
    node.power_capacity_watts = getattr(payload, "power_capacity_watts", None)
    node.current_power_watts = getattr(payload, "current_power_watts", None)
    node.thermal_state = getattr(payload, "thermal_state", None)
    node.cloud_connectivity = getattr(payload, "cloud_connectivity", None)
    node.last_seen_at = now
    node.updated_at = now

    # ── Device profile: honour explicit value or auto-infer ──────────────
    current_meta: dict[str, object] = dict(node.metadata_json or {})
    explicit_profile = str(current_meta.get("device_profile", "")).strip()
    if not explicit_profile:
        from backend.core.device_profiles import apply_profile_defaults, get_device_profile, infer_device_profile

        inferred = infer_device_profile(
            os=node.os or "",
            arch=node.arch or "",
            memory_mb=int(node.memory_mb or 0),
            executor=node.executor or "",
            capabilities=list(node.capabilities or []),
        )
        current_meta["device_profile"] = inferred
        profile_obj = get_device_profile(inferred)
    else:
        from backend.core.device_profiles import apply_profile_defaults, get_device_profile

        profile_obj = get_device_profile(explicit_profile)
    if profile_obj is not None:
        overrides = apply_profile_defaults(
            profile_obj,
            executor=node.executor or "",
            zone=node.zone,
            max_concurrency=int(node.max_concurrency or 1),
        )
        if "executor" in overrides:
            node.executor = str(overrides["executor"])
        if "zone" in overrides:
            node.zone = str(overrides["zone"])
        if "max_concurrency" in overrides:
            node.max_concurrency = int(overrides["max_concurrency"])
    node.metadata_json = current_meta


def _matches_node_list_filters(
    node: Node,
    *,
    active_lease_count: int,
    now: datetime.datetime,
    node_type: str | None,
    executor: str | None,
    os_name: str | None,
    zone: str | None,
    enrollment_status: str | None,
    drain_status: str | None,
    heartbeat_state: str | None,
    capacity_state: str | None,
    attention: str | None,
) -> bool:
    normalized_enrollment_status = normalize_persisted_status("nodes.enrollment_status", node.enrollment_status) or "pending"
    node_drain = node.drain_status or "active"
    node_heartbeat = node_heartbeat_state(node.last_seen_at, now)
    node_capacity = node_capacity_state(active_lease_count, max(int(node.max_concurrency or 1), 1))
    attention_reason = node_attention_reason(
        enrollment_status=normalized_enrollment_status,
        status=node.status,
        drain_status=node_drain,
        heartbeat_state=node_heartbeat,
        capacity_state=node_capacity,
        health_reason=node.health_reason,
    )
    if node_type and node.node_type != node_type:
        return False
    if executor and node.executor != executor:
        return False
    if os_name and node.os != os_name:
        return False
    if zone and (node.zone or "") != zone:
        return False
    if enrollment_status and normalized_enrollment_status != enrollment_status:
        return False
    if drain_status and node_drain != drain_status:
        return False
    if heartbeat_state and node_heartbeat != heartbeat_state:
        return False
    if capacity_state and node_capacity != capacity_state:
        return False
    if attention == "attention" and attention_reason is None:
        return False
    return True


async def _get_node_by_id(db: AsyncSession, tenant_id: str, node_id: str) -> Node:
    result = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id))
    node = result.scalars().first()
    if node is None:
        raise zen("ZEN-NODE-4040", "node not found", status_code=404)
    return node


async def _get_active_lease_counts(
    db: AsyncSession,
    *,
    tenant_id: str,
    node_ids: list[str],
    now: datetime.datetime,
) -> dict[str, int]:
    if not node_ids:
        return {}
    result = await db.execute(
        select(Job.node_id, func.count())
        .where(
            Job.tenant_id == tenant_id,
            Job.node_id.in_(node_ids),
            Job.status == "leased",
            Job.leased_until.is_not(None),
            Job.leased_until > now,
        )
        .group_by(Job.node_id)
    )
    return {str(node_id): int(count or 0) for node_id, count in result.all() if node_id}


def _provision_token(node: Node) -> tuple[str, int]:
    token = generate_node_token()
    next_version = int(node.auth_token_version or 0) + 1
    node.auth_token_hash = hash_node_token(token)
    node.auth_token_version = next_version
    return token, next_version
