from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestGetDbSession:
    @pytest.mark.asyncio
    async def test_raises_503_when_no_dsn(self) -> None:
        with patch("backend.db._async_session_factory", None):
            from backend.db import get_db_session

            gen = get_db_session()
            with pytest.raises(HTTPException) as exc_info:
                await gen.__anext__()

            assert exc_info.value.status_code == 503
            assert "ZEN-BUS-5030" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_commits_on_success(self) -> None:
        mock_session = AsyncMock()
        mock_factory = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.db._async_session_factory", mock_factory):
            from backend.db import get_db_session

            gen = get_db_session()
            session = await gen.__anext__()
            assert session is mock_session

            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

            mock_session.commit.assert_awaited_once()
            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        mock_factory = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.db._async_session_factory", mock_factory):
            from backend.db import get_db_session

            gen = get_db_session()
            session = await gen.__anext__()
            assert session is mock_session

            with pytest.raises(RuntimeError, match="commit failed"):
                await gen.__anext__()

            mock_session.rollback.assert_awaited_once()
            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_does_not_hide_original_exception(self) -> None:
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        mock_session.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))
        mock_factory = MagicMock(return_value=mock_session)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.db._async_session_factory", mock_factory):
            from backend.db import get_db_session

            gen = get_db_session()
            _ = await gen.__anext__()

            with pytest.raises(RuntimeError, match="commit failed"):
                await gen.__anext__()

            mock_session.rollback.assert_awaited_once()
            mock_session.close.assert_awaited_once()


class TestDbConfig:
    def test_dsn_conversion(self) -> None:
        from backend.db import _ASYNC_DSN

        if _ASYNC_DSN:
            assert "asyncpg" in _ASYNC_DSN
        else:
            assert _ASYNC_DSN == ""
