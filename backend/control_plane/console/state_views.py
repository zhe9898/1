from __future__ import annotations

import datetime
from typing import TypedDict

from backend.kernel.contracts.status import normalize_persisted_status
from backend.kernel.execution.job_status import normalize_job_like_status

NODE_STALE_AFTER_SECONDS = 45


class StatusViewSpec(TypedDict):
    key: str
    label: str
    tone: str


def _view(key: str, label: str, tone: str) -> StatusViewSpec:
    return {"key": key, "label": label, "tone": tone}


def _normalize_node_enrollment_status(enrollment_status: str) -> str:
    normalized = str(enrollment_status or "").strip().lower() or "pending"
    try:
        return normalize_persisted_status("nodes.enrollment_status", normalized) or "pending"
    except ValueError:
        return normalized


def node_heartbeat_state(last_seen_at: datetime.datetime, now: datetime.datetime) -> str:
    if (now - last_seen_at).total_seconds() > NODE_STALE_AFTER_SECONDS:
        return "stale"
    return "fresh"


def node_capacity_state(active_lease_count: int, max_concurrency: int) -> str:
    if active_lease_count >= max(max_concurrency, 1):
        return "saturated"
    return "available"


def tone_view(tone: str) -> StatusViewSpec:
    normalized = tone.strip().lower() or "neutral"
    labels = {
        "info": "Info",
        "success": "Success",
        "warning": "Warning",
        "danger": "Critical",
        "neutral": "Neutral",
    }
    return _view(normalized, labels.get(normalized, normalized.replace("_", " ").title()), normalized)


def severity_view(severity: str) -> StatusViewSpec:
    normalized = severity.strip().lower() or "info"
    tone = "danger" if normalized == "critical" else "warning" if normalized == "warning" else "info"
    return _view(normalized, normalized.title(), tone)


def eligibility_view(eligible: bool) -> StatusViewSpec:
    return _view("eligible", "Eligible", "success") if eligible else _view("blocked", "Blocked", "warning")


def node_status_view(status: str) -> StatusViewSpec:
    normalized = status.strip().lower() or "unknown"
    if normalized == "online":
        return _view("online", "Online", "success")
    if normalized in {"offline", "error"}:
        return _view(normalized, normalized.title(), "danger")
    if normalized == "degraded":
        return _view(normalized, "Degraded", "warning")
    return _view(normalized, normalized.title(), "neutral")


def node_enrollment_status_view(enrollment_status: str) -> StatusViewSpec:
    normalized = _normalize_node_enrollment_status(enrollment_status)
    if normalized == "approved":
        return _view("approved", "Approved", "success")
    if normalized == "pending":
        return _view("pending", "Pending", "warning")
    if normalized == "rejected":
        return _view("rejected", "Rejected", "danger")
    return _view(normalized, normalized.title(), "neutral")


def node_drain_status_view(drain_status: str) -> StatusViewSpec:
    normalized = drain_status.strip().lower() or "active"
    if normalized == "active":
        return _view("active", "Active", "info")
    if normalized in {"draining", "paused"}:
        return _view(normalized, normalized.title(), "warning")
    return _view(normalized, normalized.title(), "neutral")


def node_heartbeat_state_view(heartbeat_state: str) -> StatusViewSpec:
    normalized = heartbeat_state.strip().lower() or "fresh"
    if normalized == "fresh":
        return _view("fresh", "Fresh", "success")
    if normalized == "stale":
        return _view("stale", "Stale", "warning")
    return _view(normalized, normalized.title(), "neutral")


def node_capacity_state_view(capacity_state: str) -> StatusViewSpec:
    normalized = capacity_state.strip().lower() or "available"
    if normalized == "available":
        return _view("available", "Available", "success")
    if normalized == "saturated":
        return _view("saturated", "Saturated", "warning")
    return _view(normalized, normalized.title(), "neutral")


def job_status_view(status: str) -> StatusViewSpec:
    normalized = normalize_job_like_status(status, fallback="pending")
    if normalized in {"leased", "running"}:
        return _view("running", "Running", "warning")
    if normalized == "completed":
        return _view("completed", "Completed", "success")
    if normalized == "cancelled":
        return _view("cancelled", "Cancelled", "neutral")
    if normalized in {"failed", "timeout"}:
        return _view("failed", "Failed", "danger")
    return _view("pending", "Pending", "neutral")


def job_lease_state_view(lease_state: str) -> StatusViewSpec:
    normalized = lease_state.strip().lower() or "none"
    if normalized == "active":
        return _view("active", "Active", "warning")
    if normalized == "stale":
        return _view("stale", "Stale", "danger")
    return _view("none", "None", "neutral")


def connector_status_view(status: str) -> StatusViewSpec:
    normalized = status.strip().lower() or "unknown"
    if normalized in {"healthy", "online"}:
        return _view(normalized, normalized.title(), "success")
    if normalized in {"configured", "auth_required"}:
        return _view(normalized, normalized.replace("_", " ").title(), "warning")
    if normalized == "error":
        return _view("error", "Error", "danger")
    if normalized == "degraded":
        return _view("degraded", "Degraded", "warning")
    return _view(normalized, normalized.replace("_", " ").title(), "neutral")


def node_attention_reason(
    *,
    enrollment_status: str,
    status: str,
    drain_status: str,
    heartbeat_state: str,
    capacity_state: str,
    health_reason: str | None,
) -> str | None:
    normalized_enrollment_status = _normalize_node_enrollment_status(enrollment_status)
    if normalized_enrollment_status == "rejected":
        return "node rejected by control plane"
    if normalized_enrollment_status == "pending":
        return "waiting for initial register or heartbeat"
    if status != "online":
        return f"node reported status={status}"
    if drain_status != "active":
        return f"node is {drain_status}"
    if heartbeat_state == "stale":
        return "heartbeat is stale"
    if health_reason:
        return health_reason
    if capacity_state == "saturated":
        return "node is at declared concurrency"
    return None


def job_lease_state(
    *,
    status: str,
    leased_until: datetime.datetime | None,
    now: datetime.datetime,
) -> str:
    if status != "leased":
        return "none"
    if leased_until and leased_until < now:
        return "stale"
    return "active"


def job_attention_reason(
    *,
    status: str,
    priority: int,
    leased_until: datetime.datetime | None,
    now: datetime.datetime,
) -> str | None:
    normalized_status = normalize_job_like_status(status, fallback="pending")
    lease_state = job_lease_state(status=normalized_status, leased_until=leased_until, now=now)
    if lease_state == "stale":
        return "lease expired before completion"
    if normalized_status == "failed":
        return "terminal failure needs retry or triage"
    if normalized_status == "timeout":
        return "job exceeded its execution window"
    if normalized_status == "cancelled":
        return "job cancelled by operator"
    if normalized_status == "pending" and priority >= 80:
        return "high-priority backlog waiting for placement"
    if normalized_status == "pending":
        return "waiting for an eligible runner"
    return None
