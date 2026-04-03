"""
ZEN70 MQTT Worker - Frigate 事件处理。
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import _async_session_factory

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
        return cast(str, config.value)

    default = os.getenv("MEDIA_PATH", "/mnt/media")
    return f"{default}/frigate_snapshots"


async def process_event(event: dict[str, Any]) -> None:
    after = event.get("after", {})
    if not after.get("has_snapshot", False):
        return

    async with _async_session_factory() as session:  # type: ignore[misc]
        media_path = await get_media_path(session)

        event_id = _sanitize_segment(after.get("id", "unknown"), default="unknown")
        label = after.get("label", "unknown")
        camera = _sanitize_segment(after.get("camera", "unknown"), default="unknown")
        tenant_id = str(after.get("tenant_id") or event.get("tenant_id") or "default")

        snapshot_b64 = after.get("snapshot", "")
        if not snapshot_b64:
            return

        try:
            snapshot_bytes = base64.b64decode(snapshot_b64, validate=True)
        except (ValueError, TypeError):
            logger.warning("discarding invalid snapshot payload for event_id=%s", event_id)
            return

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
