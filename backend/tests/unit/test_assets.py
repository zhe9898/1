"""Unit tests for backend.api.assets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError


class TestAllowedExtensions:
    def test_allowed_image_extensions(self) -> None:
        from backend.api.assets import _ALLOWED_EXTENSIONS

        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"]:
            assert ext in _ALLOWED_EXTENSIONS

    def test_allowed_video_extensions(self) -> None:
        from backend.api.assets import _ALLOWED_EXTENSIONS

        for ext in [".mp4", ".webm", ".mov", ".avi", ".mkv"]:
            assert ext in _ALLOWED_EXTENSIONS

    def test_allowed_document_extensions(self) -> None:
        from backend.api.assets import _ALLOWED_EXTENSIONS

        for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv", ".json"]:
            assert ext in _ALLOWED_EXTENSIONS

    def test_dangerous_extensions_blocked(self) -> None:
        from backend.api.assets import _ALLOWED_EXTENSIONS

        for ext in [".exe", ".sh", ".bat", ".cmd", ".ps1", ".py", ".js", ".php", ".jsp", ".dll", ".so"]:
            assert ext not in _ALLOWED_EXTENSIONS


class TestAllowedMime:
    def test_allowed_mime_prefixes(self) -> None:
        from backend.api.assets import _ALLOWED_MIME_PREFIXES

        for prefix in ["image/", "video/", "audio/", "application/pdf"]:
            assert prefix in _ALLOWED_MIME_PREFIXES

    def test_dangerous_mime_blocked(self) -> None:
        from backend.api.assets import _ALLOWED_MIME_PREFIXES

        dangerous = "application/x-executable"
        assert not any(dangerous.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES)


class TestUploadAsset:
    def test_router_exposes_upload_and_delete_endpoints(self) -> None:
        from backend.api.assets import router

        assert any(route.path == "/api/v1/assets/upload" and "POST" in route.methods for route in router.routes)
        assert any(route.path == "/api/v1/assets/{asset_id}" and "DELETE" in route.methods for route in router.routes)

    @pytest.mark.anyio
    async def test_reject_exe_extension(self) -> None:
        from backend.api.assets import upload_asset

        file = MagicMock()
        file.filename = "malware.exe"
        file.content_type = "application/octet-stream"

        request = MagicMock()
        db = AsyncMock()
        user = {"sub": "test", "tenant_id": "default"}

        with pytest.raises(HTTPException) as exc_info:
            await upload_asset(request, file, db, user)
        assert exc_info.value.status_code == 415
        assert exc_info.value.detail["code"] == "ZEN-ASSET-4150"

    @pytest.mark.anyio
    async def test_reject_wrong_mime(self) -> None:
        from backend.api.assets import upload_asset

        file = MagicMock()
        file.filename = "trojan.jpg"
        file.content_type = "application/x-executable"

        request = MagicMock()
        db = AsyncMock()
        user = {"sub": "test", "tenant_id": "default"}

        with pytest.raises(HTTPException) as exc_info:
            await upload_asset(request, file, db, user)
        assert exc_info.value.status_code == 415
        assert exc_info.value.detail["code"] == "ZEN-ASSET-4151"

    @pytest.mark.anyio
    async def test_no_media_path_returns_503(self) -> None:
        from backend.api.assets import upload_asset

        file = MagicMock()
        file.filename = "photo.jpg"
        file.content_type = "image/jpeg"

        request = MagicMock()
        db = AsyncMock()
        user = {"sub": "test", "tenant_id": "default"}

        with patch("backend.api.assets.MEDIA_PATH", None):
            with pytest.raises(HTTPException) as exc_info:
                await upload_asset(request, file, db, user)
        assert exc_info.value.status_code == 503

    @pytest.mark.anyio
    async def test_upload_persists_asset_record_and_returns_id(self, tmp_path: Path) -> None:
        from backend.api.assets import upload_asset
        from backend.models.asset import Asset

        file = MagicMock()
        file.filename = "photo.jpg"
        file.content_type = "image/jpeg"
        file.read = AsyncMock(side_effect=[b"frame-data", b""])
        file.close = AsyncMock()

        request = MagicMock()
        db = MagicMock()
        db.add = MagicMock()

        def assign_asset_id() -> None:
            asset = db.add.call_args.args[0]
            asset.id = 17

        db.flush = AsyncMock(side_effect=assign_asset_id)
        user = {"sub": "test", "tenant_id": "tenant-a"}

        with patch("backend.api.assets.MEDIA_PATH", str(tmp_path)):
            result = await upload_asset(request, file, db, user)

        stored_asset = db.add.call_args.args[0]
        assert isinstance(stored_asset, Asset)
        assert stored_asset.tenant_id == "tenant-a"
        assert stored_asset.asset_type == "image"
        assert stored_asset.file_path == str(tmp_path / result["filename"])
        assert (tmp_path / result["filename"]).read_bytes() == b"frame-data"
        assert result["id"] == 17
        assert result["size"] == len(b"frame-data")
        file.close.assert_awaited()

    @pytest.mark.anyio
    async def test_upload_removes_file_when_db_flush_fails(self, tmp_path: Path) -> None:
        from backend.api.assets import upload_asset

        file = MagicMock()
        file.filename = "photo.jpg"
        file.content_type = "image/jpeg"
        file.read = AsyncMock(side_effect=[b"frame-data", b""])
        file.close = AsyncMock()

        request = MagicMock()
        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock(side_effect=SQLAlchemyError("flush failed"))
        user = {"sub": "test", "tenant_id": "tenant-a"}

        with patch("backend.api.assets.MEDIA_PATH", str(tmp_path)):
            with pytest.raises(SQLAlchemyError):
                await upload_asset(request, file, db, user)

        assert list(tmp_path.iterdir()) == []
        file.close.assert_awaited()

    @pytest.mark.anyio
    async def test_uuid_filename_prevents_path_traversal(self) -> None:
        import uuid

        evil_filenames = [
            "../../../etc/passwd.jpg",
            "..\\..\\windows\\system32\\config.jpg",
            "/etc/shadow.png",
            "normal.jpg",
        ]

        for filename in evil_filenames:
            ext = Path(filename).suffix
            safe_name = f"{uuid.uuid4().hex}{ext}"
            assert "/" not in safe_name
            assert "\\" not in safe_name
            assert ".." not in safe_name
            assert len(Path(safe_name).stem) == 32


class TestDeleteAsset:
    @pytest.mark.anyio
    async def test_invalid_asset_id_returns_400(self) -> None:
        from backend.api.assets import delete_asset

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await delete_asset("not-an-int", db, {"tenant_id": "default"})
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["recovery_hint"] == "提供有效的整数资产 ID"

    @pytest.mark.anyio
    async def test_non_positive_asset_id_returns_400(self) -> None:
        from backend.api.assets import delete_asset

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await delete_asset(0, db, {"tenant_id": "default"})
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["code"] == "ZEN-ASSET-4000"

    @pytest.mark.anyio
    async def test_nonexistent_asset_returns_404(self) -> None:
        from backend.api.assets import delete_asset

        db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await delete_asset(123, db, {"tenant_id": "default"})
        assert exc_info.value.status_code == 404

    @pytest.mark.anyio
    async def test_delete_asset_marks_record_deleted(self) -> None:
        from backend.api.assets import delete_asset

        db = MagicMock()
        asset = MagicMock()
        asset.id = 42
        asset.is_deleted = False
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = asset
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        result = await delete_asset(42, db, {"tenant_id": "tenant-a"})

        assert asset.is_deleted is True
        assert result == {"deleted": 42}
        db.flush.assert_awaited()
