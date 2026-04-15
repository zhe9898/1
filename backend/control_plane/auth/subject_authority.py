from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import current_user_tenant_id
from backend.models.user import User
from backend.platform.db.rls import set_tenant_context


async def assert_token_subject_active(db: AsyncSession, payload: Mapping[str, object]) -> None:
    subject = str(payload.get("sub") or "").strip()
    tenant_id = current_user_tenant_id(payload)
    if not subject:
        raise zen("ZEN-AUTH-401", "Invalid token subject", status_code=401)
    if tenant_id is None:
        raise zen("ZEN-AUTH-401", "Invalid token tenant claim", status_code=401)

    await set_tenant_context(db, tenant_id)
    query = select(User).where(User.tenant_id == tenant_id)
    if subject.isdigit():
        query = query.where(User.id == int(subject))
    else:
        query = query.where(User.username == subject)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise zen("ZEN-AUTH-401", "Token subject no longer exists", status_code=401)

    is_active = bool(getattr(user, "is_active", False))
    raw_status = getattr(user, "status", None)
    status_value = raw_status.lower() if isinstance(raw_status, str) and raw_status else "active"
    if not is_active or status_value != "active":
        raise zen("ZEN-AUTH-401", "Account is disabled", status_code=401, recovery_hint="Re-authenticate after account reactivation")
