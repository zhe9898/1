from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.models.connector import Connector


def connector_stmt_for_tenant(tenant_id: str) -> Select[tuple[Connector]]:
    return select(Connector).where(Connector.tenant_id == tenant_id)


async def load_connector_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: str,
    connector_id: str,
) -> Connector | None:
    result = await db.execute(connector_stmt_for_tenant(tenant_id).where(Connector.connector_id == connector_id))
    return result.scalars().first()
