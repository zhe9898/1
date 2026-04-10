from __future__ import annotations

import importlib
import os
import signal as signal_module
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from unittest.mock import AsyncMock, MagicMock, patch


pytestmark = pytest.mark.integration


def _mock_signal_module() -> MagicMock:
    mock_signal = MagicMock()
    mock_signal.SIGTERM = signal_module.SIGTERM
    mock_signal.getsignal.return_value = MagicMock()
    return mock_signal


def _admin_dsn() -> str:
    return os.getenv("RLS_TEST_POSTGRES_ADMIN_DSN", "postgresql://zen70:testpass@localhost:5432/postgres")


def _replace_database(dsn: str, database: str) -> str:
    parsed = urlsplit(dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


@asynccontextmanager
async def _temporary_database():
    asyncpg = pytest.importorskip("asyncpg")
    admin_dsn = _admin_dsn()
    try:
        admin_conn = await asyncpg.connect(admin_dsn)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not available for RLS integration test: {exc}")

    database_name = f"zen70_rls_{uuid.uuid4().hex[:8]}"
    await admin_conn.execute(f'CREATE DATABASE "{database_name}"')
    await admin_conn.close()

    database_dsn = _replace_database(admin_dsn, database_name)
    try:
        yield database_dsn
    finally:
        admin_conn = await asyncpg.connect(admin_dsn)
        await admin_conn.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await admin_conn.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        await admin_conn.close()


def _reload_runtime_modules(monkeypatch: pytest.MonkeyPatch, database_dsn: str):
    monkeypatch.setenv("POSTGRES_DSN", database_dsn)
    monkeypatch.setenv("JWT_SECRET_CURRENT", "test-secret-current-32bytes!!!!!")
    monkeypatch.setenv("ZEN70_ENV", "development")
    monkeypatch.delenv("ZEN70_RLS_ALLOW_SOFT_FAIL", raising=False)

    import backend.control_plane.auth.jwt as jwt_mod
    import backend.platform.db.rls as rls_mod
    import backend.db as db_mod
    import backend.control_plane.adapters.deps as deps_mod
    import backend.control_plane.app.health as health_mod
    import backend.control_plane.app.lifespan as lifespan_mod
    import backend.control_plane.app.factory as factory_mod
    import backend.control_plane.app.entrypoint as entrypoint_mod

    importlib.reload(jwt_mod)
    importlib.reload(rls_mod)
    importlib.reload(db_mod)
    importlib.reload(deps_mod)
    importlib.reload(health_mod)
    importlib.reload(lifespan_mod)
    importlib.reload(factory_mod)
    importlib.reload(entrypoint_mod)
    return db_mod, deps_mod, entrypoint_mod, lifespan_mod


@pytest.mark.asyncio
async def test_lifespan_requires_real_rls_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _temporary_database() as database_dsn:
        db_mod, _deps_mod, entrypoint_mod, lifespan_mod = _reload_runtime_modules(monkeypatch, database_dsn)
        await db_mod.init_db()

        asyncpg = pytest.importorskip("asyncpg")
        conn = await asyncpg.connect(database_dsn)
        for table_name in ("users", "push_subscriptions", "nodes", "jobs", "job_attempts", "job_logs", "connectors"):
            record = await conn.fetchrow(
                "SELECT c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = $1",
                table_name,
            )
            assert record is not None
            assert record["relrowsecurity"] is True
            assert record["relforcerowsecurity"] is True
            policy_count = await conn.fetchval(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = $1 AND policyname = $2",
                table_name,
                f"zen70_tenant_isolation_{table_name}",
            )
            assert int(policy_count or 0) == 1
        await conn.close()

        lifespan = lifespan_mod.build_lifespan(
            redis_connector=AsyncMock(return_value=None),
            signal_module=_mock_signal_module(),
        )
        with patch("backend.capabilities.clear_lru_cache"):
            async with lifespan(entrypoint_mod.app):
                assert entrypoint_mod.app.state.rls_ready is True
        await db_mod._engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_get_tenant_db_and_startup_reject_when_rls_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _temporary_database() as database_dsn:
        db_mod, deps_mod, entrypoint_mod, lifespan_mod = _reload_runtime_modules(monkeypatch, database_dsn)
        from backend.models import Base

        async with db_mod._engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.run_sync(Base.metadata.create_all)

        async with db_mod._async_session_factory() as session:  # type: ignore[misc]
            with pytest.raises(HTTPException) as exc_info:
                await deps_mod.get_tenant_db({"tenant_id": "tenant-a"}, session)
            assert exc_info.value.status_code == 503

        lifespan = lifespan_mod.build_lifespan(
            redis_connector=AsyncMock(return_value=None),
            signal_module=_mock_signal_module(),
        )
        with patch("backend.capabilities.clear_lru_cache"):
            with pytest.raises(RuntimeError, match="RLS readiness check failed"):
                async with lifespan(entrypoint_mod.app):
                    raise AssertionError("unreachable")
        await db_mod._engine.dispose()  # type: ignore[union-attr]
