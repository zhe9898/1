from __future__ import annotations

import datetime

from backend.api.nodes_helpers import _apply_contract, _provision_token
from backend.api.nodes_models import NodeContractPayload
from backend.core.compatibility_adapter import canonicalize_status
from backend.models.node import Node


class NodeEnrollmentService:
    @staticmethod
    def rotate_token(node: Node, *, now: datetime.datetime) -> tuple[str, int]:
        token, version = _provision_token(node)
        node.enrollment_status = canonicalize_status("nodes.enrollment_status", "pending")
        node.status = "offline"
        node.updated_at = now
        return token, version

    @staticmethod
    def revoke(node: Node, *, now: datetime.datetime) -> None:
        node.auth_token_hash = None
        node.auth_token_version = int(node.auth_token_version or 0) + 1
        node.enrollment_status = canonicalize_status("nodes.enrollment_status", "rejected")
        node.status = "offline"
        node.updated_at = now

    @staticmethod
    def set_drain(
        node: Node,
        *,
        drain_status: str,
        now: datetime.datetime,
        reason: str | None = None,
        drain_until: datetime.datetime | None = None,
    ) -> None:
        node.drain_status = drain_status
        node.drain_until = drain_until
        node.health_reason = reason or node.health_reason
        node.updated_at = now

    @staticmethod
    def register_or_refresh(
        node: Node,
        payload: NodeContractPayload,
        *,
        now: datetime.datetime,
        cloud_auto_approved: bool,
    ) -> str:
        event_action = "updated" if node.enrollment_status == "approved" else "registered"
        _apply_contract(node, payload, "online", now)
        if node.enrollment_status == "approved":
            node.enrollment_status = "approved"
        else:
            node.enrollment_status = "approved" if cloud_auto_approved else "pending"
        node.drain_status = "active"
        node.health_reason = None
        if node.metadata_json:
            node.metadata_json = {k: v for k, v in node.metadata_json.items() if k != "cloud_token"}
        return event_action

    @staticmethod
    def approve(node: Node, *, actor: str, now: datetime.datetime, reason: str | None = None) -> None:
        node.enrollment_status = canonicalize_status("nodes.enrollment_status", "approved")
        node.drain_status = "active"
        meta = dict(node.metadata_json or {})
        meta["approved_by"] = actor
        meta["approved_at"] = now.isoformat()
        meta["approval_reason"] = reason or "approved by admin"
        node.metadata_json = meta
        node.updated_at = now

    @staticmethod
    def reject(node: Node, *, actor: str, now: datetime.datetime, reason: str | None = None) -> None:
        node.enrollment_status = canonicalize_status("nodes.enrollment_status", "rejected")
        meta = dict(node.metadata_json or {})
        meta["rejected_by"] = actor
        meta["rejected_at"] = now.isoformat()
        meta["rejection_reason"] = reason or "rejected by admin"
        node.metadata_json = meta
        node.updated_at = now
