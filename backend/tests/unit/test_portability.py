from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestSecureShredFile:
    def test_nonexistent_file_returns_true_within_media_root(self, tmp_path: Path) -> None:
        from backend.control_plane.adapters.portability import secure_shred_file

        os.environ["MEDIA_PATH"] = str(tmp_path)
        assert secure_shred_file(str(tmp_path / "ghost.txt")) is True

    @patch("backend.control_plane.adapters.portability.subprocess.run")
    def test_shred_timeout_returns_false(self, mock_run: object, tmp_path: Path) -> None:
        import subprocess

        from backend.control_plane.adapters.portability import secure_shred_file

        os.environ["MEDIA_PATH"] = str(tmp_path)
        f = tmp_path / "timeout.bin"
        f.write_bytes(b"data")
        mock_run.side_effect = subprocess.TimeoutExpired("shred", 30.0)  # type: ignore[attr-defined]

        assert secure_shred_file(str(f)) is False

    def test_python_fallback_overwrites_and_deletes(self, tmp_path: Path) -> None:
        from backend.control_plane.adapters.portability import secure_shred_file

        os.environ["MEDIA_PATH"] = str(tmp_path)
        f = tmp_path / "secret.bin"
        f.write_bytes(b"top secret")

        with patch("backend.control_plane.adapters.portability.subprocess.run", side_effect=FileNotFoundError("shred not found")):
            assert secure_shred_file(str(f)) is True

        assert not f.exists()

    def test_secure_shred_file_blocks_path_outside_media_root(self, tmp_path: Path) -> None:
        from backend.control_plane.adapters.portability import secure_shred_file

        media_root = tmp_path / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        os.environ["MEDIA_PATH"] = str(media_root)

        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"data")

        with patch("backend.control_plane.adapters.portability.subprocess.run") as mock_run:
            assert secure_shred_file(str(outside)) is False
            mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_zip_stream_generator_scopes_asset_query_by_tenant() -> None:
    from backend.control_plane.adapters.portability import zip_stream_generator

    session = AsyncMock()
    first_result = MagicMock()
    first_result.scalars.return_value.all.return_value = []
    session.execute.return_value = first_result

    chunks = []
    async for chunk in zip_stream_generator(session, "tenant-a", "42"):
        chunks.append(chunk)

    rendered = str(session.execute.await_args.args[0])
    assert "assets.tenant_id" in rendered
    assert "assets.is_deleted" in rendered


@pytest.mark.asyncio
async def test_wipe_asset_permanently_queries_with_explicit_tenant() -> None:
    from backend.control_plane.adapters.portability import wipe_asset_permanently

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    with pytest.raises(HTTPException) as exc:
        await wipe_asset_permanently(
            123,
            session=session,
            current_user={"tenant_id": "tenant-a", "sub": "admin"},
        )

    assert exc.value.status_code == 404
    rendered = str(session.execute.await_args.args[0])
    assert "assets.tenant_id" in rendered
