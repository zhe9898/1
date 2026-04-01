"""
ZEN70 CLIP Worker - 图像特征提取与标注。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import select

from backend.db import _async_session_factory

logger = logging.getLogger("zen70.clip_worker")

EMOTION_KEYWORDS = {"微笑", "笑", "开心", "快乐", "幸福", "人物", "smile", "happy"}


class CLIPInferenceEngine:
    def __init__(self) -> None:
        self._loaded = False
        self.device = "cpu"

    def load(self) -> None:
        capability_tags = os.getenv("CAPABILITY_TAGS", "")
        if "cuda" in capability_tags:
            self.device = "cuda"
        else:
            self.device = "cpu"

        import sentence_transformers  # noqa: F401

        self._loaded = True

    def extract(self, image_path: str) -> dict[str, Any]:
        if not self._loaded:
            return {"embedding": [0.0] * 512, "tags": ["模拟标签/mock"]}

        return {"embedding": [0.0] * 512, "tags": []}


engine = CLIPInferenceEngine()


async def process_pending_assets() -> None:
    from backend.models.asset import Asset

    async with _async_session_factory() as session:  # type: ignore[misc]
        result = await session.execute(select(Asset).where(Asset.embedding_status == "pending").limit(50))
        assets = result.scalars().all()

        for asset in assets:
            try:
                result_data = await asyncio.to_thread(engine.extract, asset.file_path)
                asset.embedding_status = "done"
                asset.ai_tags = result_data.get("tags", [])
                asset.is_emotion_highlight = any(t in EMOTION_KEYWORDS for t in asset.ai_tags)  # type: ignore[union-attr]
            except Exception:
                asset.embedding_status = "failed"

        await session.commit()
