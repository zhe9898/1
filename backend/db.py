"""
ZEN70 async database engine and session factory.

Uses SQLAlchemy 2.x with asyncpg. A plain ``postgresql://`` DSN is rewritten to
``postgresql+asyncpg://`` automatically for async usage.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.models.registry import load_canonical_model_metadata

logger = logging.getLogger(__name__)

_DSN = os.getenv("POSTGRES_DSN") or ""
_ASYNC_DSN = _DSN.replace("postgresql://", "postgresql+asyncpg://", 1) if _DSN else ""

_engine: AsyncEngine | None
_async_session_factory: async_sessionmaker[AsyncSession] | None

if _ASYNC_DSN:
    _engine = create_async_engine(
        _ASYNC_DSN,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
        max_overflow=int(os.getenv("DB_POOL_MAX_OVERFLOW", "30")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
        echo=os.getenv("SQL_ECHO", "").lower() in {"1", "true"},
        connect_args={
            "command_timeout": int(os.getenv("DB_COMMAND_TIMEOUT", "30")),
            # TCP keepalive: prevents silent connection drops through NAT/firewalls.
            # Without this, stale pooled connections hang silently until pool_timeout.
            # idle=30s → probe every 10s → give up after 5 failures (total ~80s to detect dead conn).
            "tcp_keepalives_idle": int(os.getenv("DB_TCP_KEEPALIVE_IDLE", "30")),
            "tcp_keepalives_interval": int(os.getenv("DB_TCP_KEEPALIVE_INTERVAL", "10")),
            "tcp_keepalives_count": int(os.getenv("DB_TCP_KEEPALIVE_COUNT", "5")),
            "server_settings": {
                "statement_timeout": os.getenv("DB_STATEMENT_TIMEOUT", "30000"),
            },
        },
    )
    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
else:
    _engine = None
    _async_session_factory = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session or returns 503 when DB is absent."""
    if _async_session_factory is None:
        from backend.core.errors import zen

        raise zen(
            "ZEN-BUS-5030",
            "Database unavailable: POSTGRES_DSN not configured",
            status_code=503,
            recovery_hint="Configure POSTGRES_DSN before starting the gateway",
        )

    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                logger.exception("database rollback failed while handling session error")
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialize schema for local/bootstrap environments.

    Production schema evolution must still flow through Alembic migrations and
    RLS setup, not request-path or bootstrap-time hard-coded DDL.
    """
    if _engine is None:
        return

    metadata = load_canonical_model_metadata()
    async with _engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(metadata.create_all)

    from backend.core.rls import apply_rls_policies

    async with _async_session_factory() as session:  # type: ignore[misc]
        await apply_rls_policies(session)
