"""
ZEN70 MQTT Worker - Frigate 事件处理。
"""

from __future__ import annotations

import base64
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import _async_session_factory
from backend.kernel.contracts.tenant_claims import coalesce_tenant_claim

logger = logging.getLogger("zen70.mqtt_worker")
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_segment(raw: object, *, default: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return default
    cleaned = _SAFE_SEGMENT.sub("_", value)
    cleaned = cleaned.strip("._")
    return cleaned or default


def _safe_join_under_root(root: Path, segment: str) -> Path:
    candidate = (root / segment).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes media root") from exc
    return candidate


async def get_media_path(session: AsyncSession) -> str:
    from backend.models.feature_flag import SystemConfig

    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "media_path"))
    config = result.scalar_one_or_none()
    if config is not None:
        return str(config.value)

    default = os.getenv("MEDIA_PATH", "/mnt/media")
    return f"{default}/frigate_snapshots"


def _resolve_event_tenant_id(event: Mapping[str, object], after: Mapping[str, object]) -> str | None:
    return coalesce_tenant_claim(after.get("tenant_id"), event.get("tenant_id"))


def export_mqtt_worker_tenant_contract() -> dict[str, object]:
    return {
        "entrypoint": "backend.workers.mqtt_worker.process_event",
        "tenant_resolver": "backend.workers.mqtt_worker._resolve_event_tenant_id",
        "tenant_sources": ["event.after.tenant_id", "event.tenant_id"],
        "default_tenant_fallback_allowed": False,
        "missing_tenant_behavior": "drop-and-log",
    }


async def process_event(event: dict[str, Any]) -> None:
    raw_after = event.get("after")
    after: Mapping[str, object] = raw_after if isinstance(raw_after, Mapping) else {}
    if not after.get("has_snapshot", False):
        return

    event_id = _sanitize_segment(after.get("id", "unknown"), default="unknown")
    label = str(after.get("label") or "unknown")
    camera = _sanitize_segment(after.get("camera", "unknown"), default="unknown")
    tenant_id = _resolve_event_tenant_id(event, after)
    if tenant_id is None:
        logger.error("mqtt worker skipped event_id=%s: tenant scope is required in event payload", event_id)
        return

    raw_snapshot_b64 = after.get("snapshot")
    snapshot_b64 = raw_snapshot_b64 if isinstance(raw_snapshot_b64, str) else ""
    if not snapshot_b64:
        return

    try:
        snapshot_bytes = base64.b64decode(snapshot_b64, validate=True)
    except (ValueError, TypeError):
        logger.warning("discarding invalid snapshot payload for event_id=%s", event_id)
        return

    async with _async_session_factory() as session:  # type: ignore[misc]
        media_path = await get_media_path(session)
        media_root = Path(media_path).resolve(strict=False)
        output_dir = _safe_join_under_root(media_root, camera)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = _safe_join_under_root(output_dir, f"{event_id}.jpg")
        with output_path.open("wb") as handle:
            handle.write(snapshot_bytes)

        from backend.models.asset import Asset

        asset = Asset(
            tenant_id=tenant_id,
            file_path=str(output_path),
            label=label,
            camera=camera,
            event_id=event_id,
        )
        session.add(asset)
        await session.commit()
