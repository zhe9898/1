from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.api.settings import get_settings_schema, list_config, list_flags


class _Result:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> object:
        class _Scalars:
            def __init__(self, values: list[object]) -> None:
                self._values = values

            def all(self) -> list[object]:
                return self._values

        return _Scalars(self._values)


@pytest.mark.asyncio
async def test_list_flags_read_path_has_no_writes() -> None:
    db = AsyncMock()
    db.execute.return_value = _Result([])
    db.flush = AsyncMock()

    response = await list_flags(db=db, current_user={"role": "superadmin", "tenant_id": "admin"})

    assert response["status"] == "ok"
    assert response["count"] >= 1
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_config_read_path_has_no_writes() -> None:
    db = AsyncMock()
    db.execute.return_value = _Result([])
    db.flush = AsyncMock()

    response = await list_config(db=db, current_user={"role": "superadmin", "tenant_id": "admin"})

    assert response["status"] == "ok"
    assert response["data"]
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_settings_schema_read_path_has_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.execute.return_value = _Result([])
    db.flush = AsyncMock()

    monkeypatch.setattr("backend.api.settings.get_runtime_settings", lambda: {"cors_origins": []})

    response = await get_settings_schema(db=db, current_user={"role": "superadmin", "tenant_id": "admin"})

    assert response.sections
    db.flush.assert_not_awaited()
