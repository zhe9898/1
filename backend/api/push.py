from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, field_validator
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.platform.security.normalization import normalize_webpush_endpoint
from backend.models.user import PushSubscription

router = APIRouter()
logger = logging.getLogger("zen70.push")

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "admin@zen70.local")


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeInput(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: str | None = None

    @field_validator("endpoint")
    @classmethod
    def _endpoint_valid(cls, value: str) -> str:
        return normalize_webpush_endpoint(value, field_name="endpoint")


class PushPayload(BaseModel):
    title: str
    body: str
    icon: str | None = "/pwa-192x192.png"
    url: str | None = "/"


@router.get("/vapid-public-key")
async def get_vapid_public_key() -> dict[str, str]:
    """Return the VAPID public key used by the browser Service Worker."""
    if not VAPID_PUBLIC_KEY:
        raise zen(
            "ZEN-PUSH-5030",
            "VAPID keys not configured on server",
            status_code=503,
            recovery_hint="Configure VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY, then retry.",
        )
    return {"vapid_public_key": VAPID_PUBLIC_KEY}


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_push(
    sub_data: PushSubscribeInput,
    current_user: dict[str, str] = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Persist a browser push subscription for the current tenant-scoped user."""
    raw_sub = current_user.get("sub", "")
    try:
        user_id = int(raw_sub)
    except (TypeError, ValueError):
        raise zen(
            "ZEN-PUSH-4001",
            "Invalid user identity in token",
            status_code=400,
            recovery_hint="JWT sub must be a numeric user id.",
        )

    tenant_id = str(current_user.get("tenant_id") or "default")
    existing = await session.execute(
        select(PushSubscription).where(
            PushSubscription.tenant_id == tenant_id,
            PushSubscription.endpoint == sub_data.endpoint,
        )
    )
    sub = existing.scalar_one_or_none()

    if sub:
        sub.tenant_id = tenant_id
        sub.user_id = user_id
        sub.p256dh = sub_data.keys.p256dh
        sub.auth = sub_data.keys.auth
        sub.user_agent = sub_data.user_agent
    else:
        sub = PushSubscription(
            tenant_id=tenant_id,
            user_id=user_id,
            endpoint=sub_data.endpoint,
            p256dh=sub_data.keys.p256dh,
            auth=sub_data.keys.auth,
            user_agent=sub_data.user_agent,
        )
        session.add(sub)

    await session.flush()
    return {"status": "ok", "message": "Subscription saved successfully"}


@router.post("/test-trigger")
async def test_trigger_push(
    payload: PushPayload,
    current_user: dict[str, str] = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict[str, object]:
    """Send a test Web Push notification to the current user's subscriptions."""
    if not VAPID_PRIVATE_KEY:
        raise zen(
            "ZEN-PUSH-5031",
            "VAPID keys not configured",
            status_code=503,
            recovery_hint="Configure VAPID_PRIVATE_KEY, then retry.",
        )

    raw_sub = current_user.get("sub", "")
    try:
        user_id = int(raw_sub)
    except (TypeError, ValueError):
        raise zen(
            "ZEN-PUSH-4001",
            "Invalid user identity in token",
            status_code=400,
            recovery_hint="JWT sub must be a numeric user id.",
        )

    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await session.execute(
        select(PushSubscription).where(
            PushSubscription.tenant_id == tenant_id,
            PushSubscription.user_id == user_id,
        )
    )
    subscriptions = result.scalars().all()

    if not subscriptions:
        raise zen(
            "ZEN-PUSH-4040",
            "No push subscriptions found for this user",
            status_code=404,
            recovery_hint="Allow browser notifications and subscribe before retrying.",
        )

    success_count = 0
    fail_count = 0

    for sub in subscriptions:
        try:
            normalized_endpoint = normalize_webpush_endpoint(sub.endpoint, field_name="endpoint")
        except ValueError:
            fail_count += 1
            logger.warning("push subscription rejected during dispatch due to invalid endpoint: %s", sub.endpoint)
            await session.delete(sub)
            continue
        sub_info = {
            "endpoint": normalized_endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=sub_info,
                data=payload.model_dump_json(),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
            )
            success_count += 1
        except WebPushException as exc:
            fail_count += 1
            if exc.response and exc.response.status_code in [404, 410]:
                await session.delete(sub)
        except Exception:
            # Isolate per-subscription failures so a single bad endpoint
            # does not abort the entire batch dispatch.
            fail_count += 1
            logger.exception("push dispatch failed for endpoint=%s", sub.endpoint)

    await session.flush()
    return {"status": "ok", "dispatched": success_count, "failed": fail_count}
