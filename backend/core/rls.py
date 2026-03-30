from __future__ import annotations

import logging
import os
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("zen70.rls")

_TENANT_TABLES: Final[tuple[str, ...]] = (
    "users",
    "assets",
    "push_subscriptions",
    "nodes",
    "jobs",
    "job_attempts",
    "job_logs",
    "connectors",
    "family_messages",
    "conversation_memories",
    "memory_facts",
    "conversation_daily_summaries",
)

_POLICY_PREFIX: Final[str] = "zen70_tenant_isolation"
_SESSION_READY_FLAG: Final[str] = "zen70.rls.ready"


def _mark_session_ready(session: AsyncSession) -> None:
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info[_SESSION_READY_FLAG] = True


def _is_production_env() -> bool:
    return os.getenv("ZEN70_ENV", "").strip().lower() == "production"


def _soft_fail_requested() -> bool:
    return os.getenv("ZEN70_RLS_ALLOW_SOFT_FAIL", "").strip().lower() in {"1", "true", "yes", "on"}


def _allow_soft_fail() -> bool:
    return _soft_fail_requested() and not _is_production_env()


def validate_rls_runtime_mode() -> None:
    if _is_production_env() and _soft_fail_requested():
        raise RuntimeError("ZEN70_RLS_ALLOW_SOFT_FAIL cannot be enabled in production")


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    result = await session.execute(
        text("SELECT EXISTS (" "  SELECT 1 FROM information_schema.tables " "  WHERE table_schema = 'public' AND table_name = :table_name" ")"),
        {"table_name": table_name},
    )
    return bool(result.scalar())


async def _tenant_column_exists(session: AsyncSession, table_name: str) -> bool:
    result = await session.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_schema = 'public' "
            "    AND table_name = :table_name "
            "    AND column_name = 'tenant_id'"
            ")"
        ),
        {"table_name": table_name},
    )
    return bool(result.scalar())


async def _rls_flags(session: AsyncSession, table_name: str) -> tuple[bool, bool]:
    result = await session.execute(
        text(
            "SELECT c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :table_name"
        ),
        {"table_name": table_name},
    )
    row = result.first()
    if row is None:
        return False, False
    return bool(row[0]), bool(row[1])


async def _policy_exists(session: AsyncSession, table_name: str, policy_name: str) -> bool:
    result = await session.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_policies "
            "  WHERE schemaname = 'public' "
            "    AND tablename = :table_name "
            "    AND policyname = :policy_name"
            ")"
        ),
        {"table_name": table_name, "policy_name": policy_name},
    )
    return bool(result.scalar())


async def assert_rls_ready(session: AsyncSession) -> None:
    validate_rls_runtime_mode()
    info = getattr(session, "info", None)
    if isinstance(info, dict) and info.get(_SESSION_READY_FLAG):
        return

    missing: list[str] = []
    for table_name in _TENANT_TABLES:
        if not await _table_exists(session, table_name):
            missing.append(f"{table_name}:missing_table")
            continue
        if not await _tenant_column_exists(session, table_name):
            missing.append(f"{table_name}:missing_tenant_id")
            continue
        enabled, forced = await _rls_flags(session, table_name)
        if not enabled:
            missing.append(f"{table_name}:rls_disabled")
        if not forced:
            missing.append(f"{table_name}:rls_not_forced")
        policy_name = f"{_POLICY_PREFIX}_{table_name}"
        if not await _policy_exists(session, table_name, policy_name):
            missing.append(f"{table_name}:missing_policy")

    if missing:
        raise RuntimeError("RLS readiness check failed: " + ", ".join(missing))

    # Functional verification: confirm RLS policy actually filters rows.
    # We set an impossible tenant and verify that COUNT returns 0 for the
    # first available protected table. This catches policy syntax errors that
    # metadata checks cannot detect.
    probe_table = next(
        (t for t in _TENANT_TABLES if not any(f.startswith(t + ":") for f in missing)),
        None,
    )
    if probe_table:
        try:
            await session.execute(
                text("SET LOCAL zen70.current_tenant = '__rls_probe_tenant__'")
            )
            result = await session.execute(
                text(f"SELECT COUNT(*) FROM {probe_table}")  # noqa: S608
            )
            count = result.scalar() or 0
            if count != 0:
                raise RuntimeError(
                    f"RLS policy functional check FAILED on table '{probe_table}': "
                    f"expected 0 rows with impossible tenant, got {count}. "
                    "Policy may not be enforcing tenant isolation."
                )
            # Reset to no tenant context
            await session.execute(
                text("SET LOCAL zen70.current_tenant = ''")
            )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"RLS functional probe failed on table '{probe_table}': {exc}"
            ) from exc

    _mark_session_ready(session)


async def apply_rls_policies(session: AsyncSession) -> None:
    validate_rls_runtime_mode()
    applied_count = 0

    for table_name in _TENANT_TABLES:
        try:
            if not await _table_exists(session, table_name):
                logger.debug("RLS skip: table '%s' not found in public schema", table_name)
                continue

            if not await _tenant_column_exists(session, table_name):
                logger.warning("RLS skip: table '%s' exists but lacks tenant_id column", table_name)
                continue

            await session.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;"))
            await session.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;"))

            policy_name = f"{_POLICY_PREFIX}_{table_name}"
            await session.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name};"))
            await session.execute(
                text(
                    f"CREATE POLICY {policy_name} ON {table_name} "
                    "AS PERMISSIVE FOR ALL TO PUBLIC "
                    "USING (tenant_id = current_setting('zen70.current_tenant', true)) "
                    "WITH CHECK (tenant_id = current_setting('zen70.current_tenant', true));"
                )
            )
            applied_count += 1
            logger.info("RLS policy '%s' applied to table '%s'", policy_name, table_name)
        except (OSError, RuntimeError, TypeError, ValueError, SQLAlchemyError) as exc:
            await session.rollback()
            protected_tables = ", ".join(_TENANT_TABLES)
            logger.error(
                "RLS policy application failed for table '%s': %s; expected protected tables: %s",
                table_name,
                exc,
                protected_tables,
            )
            if _allow_soft_fail():
                return
            raise RuntimeError(f"RLS initialization failed on table '{table_name}'; expected protected tables: {protected_tables}") from exc

    if applied_count > 0:
        await session.commit()
        _mark_session_ready(session)
        logger.info("RLS initialization complete: %d/%d tables protected", applied_count, len(_TENANT_TABLES))
    else:
        logger.warning("RLS initialization: no tables were protected (DB may not be initialized yet)")


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    normalized_tenant_id = (tenant_id or "").strip() or "default"
    await session.execute(text("SET LOCAL zen70.current_tenant = :tenant"), {"tenant": normalized_tenant_id})
