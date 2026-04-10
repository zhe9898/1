"""ZEN70 assets API with upload persistence and tenant-scoped soft delete."""

from __future__ import annotations

import inspect
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_tenant_db
from backend.models.asset import Asset

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

_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"})
_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".webm", ".mov", ".avi", ".mkv"})
_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv", ".json"})


def _infer_asset_type(ext: str) -> str:
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    if ext in _DOCUMENT_EXTENSIONS:
        return "document"
    return "file"


def _asset_id_validation_error(asset_id: object) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "ZEN-ASSET-4000",
            "message": f"Invalid asset ID: {asset_id}",
            "recovery_hint": "提供有效的整数资产 ID",
        },
    )


def _remove_uploaded_file(dest: Path) -> None:
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass


async def _close_upload_file(file: UploadFile) -> None:
    close = getattr(file, "close", None)
    if close is None:
        return
    maybe_awaitable = close()
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


@router.post("/upload")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload a file, persist metadata, and return the new asset id."""
    del request

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

    tenant_id = str((current_user or {}).get("tenant_id") or "default")
    total_size = 0
    chunk_size = 64 * 1024

    try:
        with dest.open("wb") as out_file:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    _remove_uploaded_file(dest)
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "ZEN-ASSET-4130",
                            "message": f"File exceeds maximum upload size of {MAX_UPLOAD_SIZE} bytes",
                            "recovery_hint": f"Maximum allowed file size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB",
                        },
                    )
                out_file.write(chunk)

        asset = Asset(
            tenant_id=tenant_id,
            file_path=str(dest),
            original_filename=filename or None,
            asset_type=_infer_asset_type(ext),
        )
        db.add(asset)
        await db.flush()
    except HTTPException:
        raise
    except (OSError, SQLAlchemyError):
        _remove_uploaded_file(dest)
        raise
    finally:
        await _close_upload_file(file)

    return {"id": asset.id, "filename": safe_name, "size": total_size}


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Soft-delete an asset by integer primary key with tenant isolation."""
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id < 1:
        raise _asset_id_validation_error(asset_id)

    tenant_id = str((current_user or {}).get("tenant_id") or "default")
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
    return {"deleted": asset.id}
