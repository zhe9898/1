"""ZEN70 Assets — file upload/delete API with security validation."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

MEDIA_PATH: str | None = os.environ.get("MEDIA_PATH", None)
# Maximum upload size: 50 MB (configurable via env)
MAX_UPLOAD_SIZE: int = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(50 * 1024 * 1024)))

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
        # Videos
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        # Documents
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".txt",
        ".csv",
        ".json",
    }
)

_ALLOWED_MIME_PREFIXES: frozenset[str] = frozenset(
    {
        "image/",
        "video/",
        "audio/",
        "application/pdf",
    }
)


async def upload_asset(
    request: Request,
    file: UploadFile,
    db: AsyncSession,
    current_user: dict[str, Any],
) -> dict[str, Any]:
    """Upload a file with extension and MIME validation."""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "code": "ZEN-ASSET-4150",
                "message": f"File extension '{ext}' is not allowed",
                "recovery_hint": f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
            },
        )

    content_type = file.content_type or ""
    if not any(content_type.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=415,
            detail={
                "code": "ZEN-ASSET-4151",
                "message": f"MIME type '{content_type}' is not allowed",
                "recovery_hint": "Allowed MIME prefixes: image/, video/, audio/, application/pdf",
            },
        )

    if not MEDIA_PATH:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ZEN-ASSET-5030",
                "message": "Media storage path not configured",
            },
        )

    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest = Path(MEDIA_PATH) / safe_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Chunked write with size limit to prevent memory exhaustion DoS
    total_size = 0
    chunk_size = 64 * 1024  # 64 KB chunks
    with dest.open("wb") as out_file:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                # Clean up partial file
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "ZEN-ASSET-4130",
                        "message": f"File exceeds maximum upload size of {MAX_UPLOAD_SIZE} bytes",
                        "recovery_hint": f"Maximum allowed file size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB",
                    },
                )
            out_file.write(chunk)

    return {"filename": safe_name, "size": total_size}


async def delete_asset(
    asset_id: str,
    db: AsyncSession,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Delete an asset by UUID with tenant isolation."""
    try:
        uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ZEN-ASSET-4000",
                "message": f"Invalid asset ID: {asset_id}",
                "recovery_hint": "Provide a valid UUID",
            },
        )

    from backend.models.asset import Asset

    result = await db.execute(select(Asset).where(Asset.id == asset_id, Asset.tenant_id == tenant_id))
    asset = result.scalars().first()
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ZEN-ASSET-4040",
                "message": f"Asset {asset_id} not found",
            },
        )

    asset.is_deleted = True
    await db.flush()
    return {"deleted": asset_id}
