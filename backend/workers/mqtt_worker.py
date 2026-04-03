"""
ZEN70 MQTT Worker - Frigate 事件处理。
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import _async_session_factory

logger = logging.getLogger("zen70.mqtt_worker")


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

        event_id = after.get("id", "unknown")
        label = after.get("label", "unknown")
        camera = after.get("camera", "unknown")

        snapshot_b64 = after.get("snapshot", "")
        if not snapshot_b64:
            return

        snapshot_bytes = base64.b64decode(snapshot_b64)
        output_dir = Path(media_path) / camera
        Path.mkdir(output_dir, parents=True, exist_ok=True)

        output_path = output_dir / f"{event_id}.jpg"
        Path.open(output_path, "wb").write(snapshot_bytes)

        from backend.models.asset import Asset

        asset = Asset(
            file_path=str(output_path),
            label=label,
            camera=camera,
            event_id=event_id,
        )
        session.add(asset)
        await session.commit()
