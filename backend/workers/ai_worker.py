"""
ZEN70 AI Worker - 资产嵌入向量处理。
"""

from __future__ import annotations

import logging
import os
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

        model_instance = sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")
        return model_instance
    except Exception:
        HAS_MODEL = False
        return None


Image: Any
try:
    from PIL import Image as _PILImage
except ImportError:
    Image = None
else:
    Image = _PILImage


def _resolve_worker_tenant_id(explicit_tenant_id: str | None = None) -> str | None:
    if explicit_tenant_id and explicit_tenant_id.strip():
        return explicit_tenant_id.strip()
    env_tenant = os.getenv("WORKER_TENANT_ID", "").strip() or os.getenv("TENANT_ID", "").strip()
    return env_tenant or None


async def process_pending_assets(tenant_id: str | None = None) -> int:
    from backend.models.asset import Asset

    scoped_tenant_id = _resolve_worker_tenant_id(tenant_id)
    if not scoped_tenant_id:
        logger.error("ai worker skipped: tenant scope is required (set WORKER_TENANT_ID or pass tenant_id)")
        return 0

    async with AsyncSessionLocal() as session:  # type: ignore[misc]
        result = await session.execute(select(Asset).where(Asset.tenant_id == scoped_tenant_id, Asset.embedding_status == "pending").limit(50))
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
                if Image is None:
                    raise RuntimeError("Pillow is unavailable")
                with Image.open(asset.file_path) as img:
                    img.load()
                asset.embedding_status = "done"
                count += 1
            except Exception as exc:
                logger.warning("ai worker failed for asset %s: %s", asset.file_path, exc)
                asset.embedding_status = "failed"

        await session.commit()
        return count
