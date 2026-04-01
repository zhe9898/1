"""Kernel Capability Registry.

Declares what the gateway kernel provides as stable contracts.
Business packs discover capabilities via GET /api/v1/capabilities.

Architecture:
  - Kernel capabilities are IMMUTABLE contracts (versioned)
  - Pack capabilities are DECLARED in pack_registry.py
  - This module is the single source of truth for kernel surface

Capability naming: <domain>.<resource>.<action>
  identity.auth.login
  identity.auth.webauthn
  control.nodes.manage
  control.jobs.schedule
  control.connectors.invoke
  platform.audit.query
  platform.sessions.manage
  platform.quotas.enforce
  platform.alerts.evaluate
  platform.permissions.grant
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class KernelCapability:
    key: str
    version: str
    description: str
    endpoints: tuple[str, ...] = field(default_factory=tuple)
    scopes: tuple[str, ...] = field(default_factory=tuple)
    # Scopes required to use this capability
    stable: bool = True
    # False = experimental, may change without notice


KERNEL_CAPABILITIES: Final[dict[str, KernelCapability]] = {
    # ── Identity ──────────────────────────────────────────────────────────
    "identity.auth.login": KernelCapability(
        key="identity.auth.login",
        version="v1",
        description="Password, PIN, and WebAuthn authentication",
        endpoints=(
            "POST /api/v1/auth/password/login",
            "POST /api/v1/auth/pin/login",
            "POST /api/v1/auth/webauthn/login/begin",
            "POST /api/v1/auth/webauthn/login/complete",
        ),
        scopes=(),
    ),
    "identity.auth.register": KernelCapability(
        key="identity.auth.register",
        version="v1",
        description="WebAuthn credential registration and invite-based onboarding",
        endpoints=(
            "POST /api/v1/auth/webauthn/register/begin",
            "POST /api/v1/auth/webauthn/register/complete",
            "POST /api/v1/auth/invites",
        ),
        scopes=("admin:users",),
    ),
    "identity.sessions.manage": KernelCapability(
        key="identity.sessions.manage",
        version="v1",
        description="View and revoke active login sessions",
        endpoints=(
            "GET /api/v1/sessions/me",
            "DELETE /api/v1/sessions/me/{id}",
            "DELETE /api/v1/sessions/me",
        ),
        scopes=(),
    ),
    "identity.permissions.grant": KernelCapability(
        key="identity.permissions.grant",
        version="v1",
        description="Fine-grained scope-based permission management",
        endpoints=(
            "POST /api/v1/permissions",
            "DELETE /api/v1/permissions/{id}",
            "GET /api/v1/permissions/users/{uid}",
        ),
        scopes=("admin:users",),
    ),
    "identity.users.lifecycle": KernelCapability(
        key="identity.users.lifecycle",
        version="v1",
        description="User suspend, activate, and soft-delete",
        endpoints=(
            "POST /api/v1/users/{id}/suspend",
            "POST /api/v1/users/{id}/activate",
            "DELETE /api/v1/users/{id}",
        ),
        scopes=("admin:users",),
    ),
    # ── Control Plane ─────────────────────────────────────────────────────
    "control.nodes.manage": KernelCapability(
        key="control.nodes.manage",
        version="v1",
        description="Node registration, heartbeat, drain, and approval workflow",
        endpoints=(
            "POST /api/v1/nodes/register",
            "POST /api/v1/nodes/heartbeat",
            "GET /api/v1/nodes/pending",
            "POST /api/v1/nodes/{id}/approve",
            "POST /api/v1/nodes/{id}/reject",
        ),
        scopes=("read:nodes", "write:nodes"),
    ),
    "control.jobs.schedule": KernelCapability(
        key="control.jobs.schedule",
        version="v1",
        description="Job creation, scheduling, lifecycle, DLQ, and stratification",
        endpoints=(
            "POST /api/v1/jobs",
            "GET /api/v1/jobs",
            "POST /api/v1/jobs/pull",
            "GET /api/v1/jobs/queue/stats",
            "GET /api/v1/jobs/dead-letter",
        ),
        scopes=("read:jobs", "write:jobs"),
    ),
    "control.connectors.invoke": KernelCapability(
        key="control.connectors.invoke",
        version="v1",
        description="Connector registration, testing, and invocation",
        endpoints=(
            "POST /api/v1/connectors",
            "GET /api/v1/connectors",
            "POST /api/v1/connectors/{id}/invoke",
            "POST /api/v1/connectors/{id}/test",
        ),
        scopes=("read:connectors", "write:connectors"),
    ),
    # ── Platform ──────────────────────────────────────────────────────────
    "platform.audit.query": KernelCapability(
        key="platform.audit.query",
        version="v1",
        description="Query audit logs for compliance and troubleshooting",
        endpoints=("GET /api/v1/audit-logs",),
        scopes=("admin:audit",),
    ),
    "platform.quotas.enforce": KernelCapability(
        key="platform.quotas.enforce",
        version="v1",
        description="Tenant resource quota management and enforcement",
        endpoints=(
            "GET /api/v1/quotas",
            "PUT /api/v1/quotas",
        ),
        scopes=("admin:quotas",),
    ),
    "platform.alerts.evaluate": KernelCapability(
        key="platform.alerts.evaluate",
        version="v1",
        description="Alert rule management and condition evaluation",
        endpoints=(
            "GET /api/v1/alerts/rules",
            "POST /api/v1/alerts/rules",
            "GET /api/v1/alerts",
            "POST /api/v1/alerts/evaluate",
        ),
        scopes=("admin:alerts",),
    ),
}


def get_capability(key: str) -> KernelCapability | None:
    return KERNEL_CAPABILITIES.get(key)


def list_capabilities(*, stable_only: bool = False) -> list[KernelCapability]:
    caps = list(KERNEL_CAPABILITIES.values())
    if stable_only:
        caps = [c for c in caps if c.stable]
    return caps


def capability_keys() -> list[str]:
    return sorted(KERNEL_CAPABILITIES.keys())
