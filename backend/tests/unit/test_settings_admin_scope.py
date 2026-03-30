from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.settings import list_config, list_flags, system_info
from backend.models.feature_flag import FeatureFlag


@pytest.mark.asyncio
async def test_settings_list_config_allows_superadmin() -> None:
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    session.execute.return_value = MagicMock(scalars=MagicMock(return_value=scalar_result))

    with patch("backend.api.settings._ensure_defaults", new=AsyncMock(return_value=session)):
        response = await list_config(
            db=AsyncMock(),
            current_user={"sub": "1", "role": "superadmin", "tenant_id": "default"},
        )

    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_settings_list_config_rejects_tenant_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await list_config(
            db=AsyncMock(),
            current_user={"sub": "1", "role": "admin", "tenant_id": "tenant-a"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_settings_list_flags_shows_disabled_flags_to_superadmin() -> None:
    session = AsyncMock()
    disabled_flag = FeatureFlag(key="test.flag", enabled=False, description="hidden flag", category="ops")
    scalar_result = MagicMock()
    scalar_result.all.return_value = [disabled_flag]
    session.execute.return_value = MagicMock(scalars=MagicMock(return_value=scalar_result))

    with patch("backend.api.settings._ensure_defaults", new=AsyncMock(return_value=session)):
        response = await list_flags(
            db=AsyncMock(),
            current_user={"sub": "1", "role": "superadmin", "tenant_id": "default"},
        )

    assert response["count"] == 1
    assert response["data"][0]["key"] == "test.flag"
    assert response["data"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_system_info_uses_shared_runtime_version_source() -> None:
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    session.execute.return_value = MagicMock(scalars=MagicMock(return_value=scalar_result))

    provider_registry = MagicMock()
    provider_registry.health_all = AsyncMock(return_value={})

    with (
        patch("backend.api.settings._ensure_defaults", new=AsyncMock(return_value=session)),
        patch("backend.api.settings.get_model_registry", return_value=provider_registry),
        patch("backend.api.settings.get_runtime_version", return_value="3.41.0"),
    ):
        response = await system_info(
            db=AsyncMock(),
            current_user={"sub": "1", "role": "superadmin", "tenant_id": "default"},
        )

    assert response["version"] == "3.41.0"
