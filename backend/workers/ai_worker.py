"""
ZEN70 AI Worker - 资产嵌入向量处理。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.db import _async_session_factory as AsyncSessionLocal

logger = logging.getLogger("zen70.ai_worker")

model_instance: Any = None
HAS_MODEL: bool = True


def get_model() -> Any:
    global model_instance, HAS_MODEL
    if model_instance is not None:
        return model_instance

    try:
        import sentence_transformers

        if sentence_transformers is None:
            HAS_MODEL = False  # type: ignore[unreachable]
            return None
        model_instance = sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
        return model_instance
    except Exception:
        HAS_MODEL = False
        return None


try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]


async def process_pending_assets() -> int:
    from backend.models.asset import Asset

    async with AsyncSessionLocal() as session:  # type: ignore[misc]
        result = await session.execute(select(Asset).where(Asset.embedding_status == "pending").limit(50))
        assets = result.scalars().all()

        if not assets:
            return 0

        model = get_model()
        if model is None or not HAS_MODEL:
            for asset in assets:
                asset.embedding_status = "failed"
            await session.commit()
            return 0

        count = 0
        for asset in assets:
            try:
                if not Path(asset.file_path).exists():
                    asset.embedding_status = "failed"
                    continue
                Image.open(asset.file_path)
                asset.embedding_status = "done"
                count += 1
            except Exception:
                asset.embedding_status = "failed"

        await session.commit()
        return count
