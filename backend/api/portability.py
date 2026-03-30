"""
ZEN70 数据携带权与安全粉碎销毁 API
法典准则 §3.3.2:
1. 资产全量打流导出 (防止 OOM)
2. 安全物理覆写销毁 (Secure Shredding)，禁用不可逆的 os.remove，采取填 0 覆写磁道。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import secrets
import stat
import subprocess
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_tenant_db
from backend.core.errors import zen
from backend.models.asset import Asset
from backend.models.system import SystemLog

router = APIRouter(prefix="/api/v1/portability", tags=["Portability & Security"])
logger = logging.getLogger("zen70.portability")

# --- 1. 流式打包全域资产 ---


async def zip_stream_generator(session: AsyncSession, tenant_id: str, user_id: str) -> AsyncIterator[bytes]:
    """
    流式传输真实 ZIP 归档。

    使用 Python 标准库 zipfile 写入 BytesIO 缓冲区，
    逐 asset 追加并 yield 增量字节。每个文件以 1MB 分块读取，防 OOM。
    最终 yield 包含 Central Directory 的合法 ZIP 尾部。
    """

    result = await session.execute(
        select(Asset).where(
            Asset.tenant_id == tenant_id,
            Asset.is_deleted.is_(False),
        )
    )
    assets = result.scalars().all()

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)

    for asset in assets:
        p = Path(asset.file_path)
        if not p.exists():
            logger.warning(
                "导出跳过不存在的资产文件: asset_id=%s, path=%s",
                asset.id,
                asset.file_path,
            )
            continue

        # 归档路径: assets/{asset_id}_{原始文件名}
        original_name = getattr(asset, "original_filename", p.name) or p.name
        arc_name = f"assets/{asset.id}_{original_name}"

        try:
            # 法典 §3.3.2: 1MB 分块读取防 OOM，严禁一次性 read_bytes
            _chunk_size = 1024 * 1024
            info = zipfile.ZipInfo(arc_name)
            info.file_size = p.stat().st_size

            def _write_chunked(zf_ref: zipfile.ZipFile, zi: zipfile.ZipInfo, fp: Path) -> None:
                with zf_ref.open(zi, "w") as dest, fp.open("rb") as src:
                    while True:
                        blk = src.read(_chunk_size)
                        if not blk:
                            break
                        dest.write(blk)

            await asyncio.to_thread(_write_chunked, zf, info, p)

            # yield 缓冲区中新增的字节
            buf.seek(0)
            chunk = buf.read()
            if chunk:
                yield chunk
            # 重置缓冲区（保留 ZipFile 内部状态）
            buf.seek(0)
            buf.truncate(0)
            await asyncio.sleep(0)  # 让出事件循环
        except (OSError, zipfile.BadZipFile) as e:
            logger.error("Failed to read asset %s for export: %s", asset.id, e)

    # 写入 Central Directory 并关闭 ZIP
    zf.close()
    buf.seek(0)
    tail = buf.read()
    if tail:
        yield tail
    buf.close()


@router.get("/export")
async def export_all_data(
    session: AsyncSession = Depends(get_tenant_db),
    current_user: dict = Depends(get_current_admin),
) -> StreamingResponse:
    """一键打包所有家庭数据并流式下载，防 OOM"""
    tenant_id = str(current_user.get("tenant_id") or "default")
    logger.info("User %s started tenant-scoped data export for tenant %s", current_user.get("sub"), tenant_id)

    return StreamingResponse(
        zip_stream_generator(session, tenant_id, str(current_user.get("sub") or "")),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=zen70_family_archive.zip"},
    )


# --- 2. 物理极刑：数据碎片化覆写 ---


def secure_shred_file(filepath: str, passes: int = 3) -> bool:
    """
    物理级安全销毁: 不依赖文件表解绑。
    Linux 下使用 shred，Windows 下使用伪随机字节覆写。
    """
    p = Path(filepath)
    if not p.exists():
        return True

    try:
        # 工业级首选：依赖底层的 shred 物理防腐。
        cmd = ["shred", "-u", "-z", "-n", str(passes), filepath]
        # 法典 4.0: 强制注入硬超时，绝对禁止由于底层磁盘 I/O 挂死导致工作线程无限期挂起 (OOM)
        subprocess.run(cmd, check=True, capture_output=True, timeout=30.0)
        logger.warning("☢️ 核心级 shred 执行完毕: %s", filepath)
        return True
    except subprocess.TimeoutExpired:
        logger.error(
            "☢️ 核心级 shred 执行超时 (30s大闸拦截), 回退到单机抹除伪随机流: %s",
            filepath,
        )
        return False
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Fallback (落地性/可行性)：当由于非标环境缺失 shred 能力或命令失败时，依赖 Python 原生执行退避级别的伪随机块覆写

        try:
            length = p.stat().st_size
            if length == 0:
                p.unlink()
                logger.warning("☢️ 空文件直接删除: %s", filepath)
                return True
            p.chmod(stat.S_IWRITE | stat.S_IREAD)
            # P0 修复: "r+b" 替代 "ba+"
            # "ba+" 是追加模式，POSIX 规定 seek(0) 对写入位置无效（写入始终追加到 EOF）
            # "r+b" 是读写模式，seek(0) 将写指针定位到字节 0，真正覆盖原始扇区数据
            _shred_chunk = 1024 * 1024  # 1MB 分块防大文件内存峰值
            with Path(filepath).open("r+b", buffering=0) as f:
                for _ in range(passes):
                    f.seek(0)
                    remaining = length
                    while remaining > 0:
                        sz = min(_shred_chunk, remaining)
                        f.write(secrets.token_bytes(sz))
                        remaining -= sz
                    f.flush()
                    os.fsync(f.fileno())  # 强制刷盘到物理磁道
                # 最后一轮全零覆写
                f.seek(0)
                remaining = length
                zero_blk = b"\x00" * _shred_chunk
                while remaining > 0:
                    sz = min(_shred_chunk, remaining)
                    f.write(zero_blk[:sz])
                    remaining -= sz
                f.flush()
                os.fsync(f.fileno())
                f.truncate(length)  # 确保文件大小不变
            p.unlink()
            logger.warning("☢️ Native Python 级碎片覆写完成: %s", filepath)
            return True
        except OSError as e:
            logger.error("退避物理销毁失败: %s. Error: %s", filepath, e)
            return False
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.error("物理销毁总线异常: %s. Error: %s", filepath, e)
        return False


@router.post("/shred/{asset_id}")
async def wipe_asset_permanently(
    asset_id: str,
    session: AsyncSession = Depends(get_tenant_db),
    current_user: dict = Depends(get_current_admin),
) -> dict:
    """
    危险操作：底层随机覆写磁道，神仙难救。
    """
    result = await session.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()

    if not asset:
        raise zen(
            "ZEN-ASSET-4040",
            "Asset not found",
            status_code=404,
            recovery_hint="请刷新页面后重试",
            details={"asset_id": asset_id},
        )

    filepath = asset.file_path

    # 1. 物理覆写（投递线程池，防阻塞事件循环；shred 最长 30s）
    shredred_ok = await asyncio.to_thread(secure_shred_file, filepath)
    if not shredred_ok:
        raise zen(
            "ZEN-ASSET-5001",
            "Secure shredding failed at disk level.",
            status_code=500,
            recovery_hint="请检查文件权限与磁盘健康状况后重试",
            details={"asset_id": asset_id},
        )

    # 2. 毁灭数据库指纹
    await session.delete(asset)

    # 3. 记录最高危审计日志
    audit = SystemLog(
        level="CRITICAL",
        action="SECURE_SHRED",
        operator=current_user.get("sub", "unknown"),
        details=f"The physical sectors of file '{asset.original_filename}' have been wiped and zero-filled.",
    )
    session.add(audit)
    # flush 而非 commit — get_db_session 依赖退出时统一 commit
    await session.flush()

    return {"status": "shredded", "message": "The data is unrecoverable forever."}
