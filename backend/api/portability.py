"""Data portability and secure shredding APIs."""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
import os
import secrets
import stat
import subprocess
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_tenant_db
from backend.core.errors import zen
from backend.models.asset import Asset
from backend.models.system import SystemLog

router = APIRouter(prefix="/api/v1/portability", tags=["Portability & Security"])
logger = logging.getLogger("zen70.portability")

_EXPORT_BATCH_SIZE = 200
_EXPORT_MAX_ASSETS = 50_000


def _normalize_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.UTC).replace(tzinfo=None)


def _resolve_media_root() -> Path | None:
    media_path = os.environ.get("MEDIA_PATH", "").strip()
    if not media_path:
        return None
    try:
        return Path(media_path).resolve(strict=False)
    except OSError:
        return None


def _resolve_allowed_asset_path(filepath: str) -> Path | None:
    media_root = _resolve_media_root()
    if media_root is None:
        return None
    try:
        candidate = Path(filepath).resolve(strict=False)
    except OSError:
        return None
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None
    return candidate


async def zip_stream_generator(  # noqa: C901
    session: AsyncSession,
    tenant_id: str,
    user_id: str,
    *,
    created_after: datetime.datetime | None = None,
    created_before: datetime.datetime | None = None,
    limit: int | None = None,
) -> AsyncIterator[bytes]:
    """Stream tenant-scoped assets as ZIP without loading all files in memory."""
    del user_id

    normalized_after = _normalize_utc(created_after)
    normalized_before = _normalize_utc(created_before)
    exported = 0
    offset = 0

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)

    while True:
        if limit is not None and exported >= limit:
            break

        stmt = select(Asset).where(
            Asset.tenant_id == tenant_id,
            Asset.is_deleted.is_(False),
        )
        if normalized_after is not None:
            stmt = stmt.where(Asset.created_at >= normalized_after)
        if normalized_before is not None:
            stmt = stmt.where(Asset.created_at <= normalized_before)

        remaining = None if limit is None else max(limit - exported, 0)
        page_size = _EXPORT_BATCH_SIZE if remaining is None else min(_EXPORT_BATCH_SIZE, remaining)
        if page_size <= 0:
            break

        result = await session.execute(stmt.order_by(Asset.id.asc()).offset(offset).limit(page_size))
        assets = result.scalars().all()
        if not assets:
            break

        for asset in assets:
            p = _resolve_allowed_asset_path(asset.file_path)
            if p is None:
                logger.warning("skipping asset outside MEDIA_PATH: asset_id=%s path=%s", asset.id, asset.file_path)
                continue
            if not p.exists():
                logger.warning("skipping missing asset file: asset_id=%s path=%s", asset.id, asset.file_path)
                continue

            original_name = getattr(asset, "original_filename", p.name) or p.name
            arc_name = f"assets/{asset.id}_{original_name}"
            try:
                chunk_size = 1024 * 1024
                info = zipfile.ZipInfo(arc_name)
                info.file_size = p.stat().st_size

                def _write_chunked(zf_ref: zipfile.ZipFile, zi: zipfile.ZipInfo, fp: Path) -> None:
                    with zf_ref.open(zi, "w") as dest, fp.open("rb") as src:
                        while True:
                            block = src.read(chunk_size)
                            if not block:
                                break
                            dest.write(block)

                await asyncio.to_thread(_write_chunked, zf, info, p)

                buf.seek(0)
                chunk = buf.read()
                if chunk:
                    yield chunk
                buf.seek(0)
                buf.truncate(0)
                await asyncio.sleep(0)
            except (OSError, zipfile.BadZipFile) as exc:
                logger.error("failed to add asset %s to export zip: %s", asset.id, exc)

        offset += len(assets)
        exported += len(assets)

    zf.close()
    buf.seek(0)
    tail = buf.read()
    if tail:
        yield tail
    buf.close()


@router.get("/export")
async def export_all_data(
    max_assets: int | None = Query(default=None, ge=1, le=_EXPORT_MAX_ASSETS),
    created_after: datetime.datetime | None = Query(default=None),
    created_before: datetime.datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    current_user: dict = Depends(get_current_admin),
) -> StreamingResponse:
    """Export tenant assets as a streamed ZIP archive."""
    tenant_id = str(current_user.get("tenant_id") or "default")
    logger.info("user=%s started tenant export tenant=%s", current_user.get("sub"), tenant_id)

    return StreamingResponse(
        zip_stream_generator(
            session,
            tenant_id,
            str(current_user.get("sub") or ""),
            created_after=created_after,
            created_before=created_before,
            limit=max_assets,
        ),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=zen70_family_archive.zip"},
    )


def secure_shred_file(filepath: str, passes: int = 3) -> bool:
    """Securely wipe a file path constrained under MEDIA_PATH."""
    p = _resolve_allowed_asset_path(filepath)
    if p is None:
        logger.error("secure shred blocked: path is outside MEDIA_PATH (%s)", filepath)
        return False
    if not p.exists():
        return True

    try:
        cmd = ["shred", "-u", "-z", "-n", str(passes), str(p)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30.0)
        logger.warning("secure shred command completed: %s", p)
        return True
    except subprocess.TimeoutExpired:
        logger.error("secure shred command timed out: %s", p)
        return False
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            length = p.stat().st_size
            if length == 0:
                p.unlink()
                logger.warning("empty file deleted without overwrite: %s", p)
                return True

            p.chmod(stat.S_IWRITE | stat.S_IREAD)
            shred_chunk = 1024 * 1024
            with p.open("r+b", buffering=0) as f:
                for _ in range(passes):
                    f.seek(0)
                    remaining = length
                    while remaining > 0:
                        size = min(shred_chunk, remaining)
                        f.write(secrets.token_bytes(size))
                        remaining -= size
                    f.flush()
                    os.fsync(f.fileno())

                f.seek(0)
                remaining = length
                zero_block = b"\x00" * shred_chunk
                while remaining > 0:
                    size = min(shred_chunk, remaining)
                    f.write(zero_block[:size])
                    remaining -= size
                f.flush()
                os.fsync(f.fileno())
                f.truncate(length)

            p.unlink()
            logger.warning("python fallback shred completed: %s", p)
            return True
        except OSError as exc:
            logger.error("python fallback shred failed: %s error=%s", p, exc)
            return False
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("secure shred failed: %s error=%s", p, exc)
        return False


@router.post("/shred/{asset_id}")
async def wipe_asset_permanently(
    asset_id: str,
    session: AsyncSession = Depends(get_tenant_db),
    current_user: dict = Depends(get_current_admin),
) -> dict:
    """Physically shred a tenant asset and remove its metadata."""
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await session.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()

    if not asset:
        raise zen(
            "ZEN-ASSET-4040",
            "Asset not found",
            status_code=404,
            recovery_hint="Refresh the asset list and retry",
            details={"asset_id": asset_id},
        )

    shred_ok = await asyncio.to_thread(secure_shred_file, asset.file_path)
    if not shred_ok:
        raise zen(
            "ZEN-ASSET-5001",
            "Secure shredding failed at disk level",
            status_code=500,
            recovery_hint="Verify MEDIA_PATH and file permissions, then retry",
            details={"asset_id": asset_id},
        )

    await session.delete(asset)
    session.add(
        SystemLog(
            level="CRITICAL",
            action="SECURE_SHRED",
            operator=current_user.get("sub", "unknown"),
            details=f"Asset '{asset.original_filename}' shredded and removed permanently.",
        )
    )
    await session.flush()

    return {"status": "shredded", "message": "The data is unrecoverable forever."}
