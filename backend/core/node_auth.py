from __future__ import annotations

import os
import secrets
from typing import cast

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.models.node import Node

_TOKEN_PREFIX = "zkn_"
_TOKEN_SIZE = 32


def _bcrypt_rounds() -> int:
    raw = os.getenv("NODE_TOKEN_BCRYPT_ROUNDS", "12").strip()
    try:
        rounds = int(raw)
    except ValueError:
        return 12
    return rounds if rounds >= 4 else 4


def generate_node_token() -> str:
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_SIZE)}"


def hash_node_token(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=_bcrypt_rounds())).decode("utf-8")


def verify_node_token(token: str, token_hash: str | None) -> bool:
    if not token_hash:
        return False
    try:
        return bcrypt.checkpw(token.encode("utf-8"), token_hash.encode("utf-8"))
    except ValueError:
        return False


async def authenticate_node_request(
    db: AsyncSession,
    node_id: str,
    presented_token: str,
    *,
    require_active: bool,
    tenant_id: str | None = None,
) -> Node:
    stmt = select(Node).where(Node.node_id == node_id)
    if tenant_id:
        stmt = stmt.where(Node.tenant_id == tenant_id)
    result = await db.execute(stmt)
    node = result.scalars().first()

    if node is None or not verify_node_token(presented_token, node.auth_token_hash):
        raise zen(
            "ZEN-NODE-4010",
            "Invalid node credentials",
            status_code=401,
            recovery_hint="Provision a node token from the control plane and retry registration",
        )

    if node.enrollment_status == "revoked":
        raise zen(
            "ZEN-NODE-4010",
            "Invalid node credentials",
            status_code=401,
            recovery_hint="Re-provision the node from the control plane before reconnecting",
        )

    if require_active and node.enrollment_status != "active":
        raise zen(
            "ZEN-NODE-4030",
            "Node enrollment is not active",
            status_code=403,
            recovery_hint="Complete node registration before requesting or reporting jobs",
            details={"node_id": node_id, "enrollment_status": node.enrollment_status},
        )

    return cast("Node", node)
