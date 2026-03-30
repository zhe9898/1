import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

# Ensure backend module is discoverable by Alembic
# 强制注入项目根目录到 sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

# 尝试加载当前目录上一级的下一级（即项目根目录）的 .env
load_dotenv(root_dir / ".env")

# Set config sqlalchemy.url dynamically
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
if POSTGRES_DSN:
    if POSTGRES_DSN.startswith("postgresql://"):
        POSTGRES_DSN = POSTGRES_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
    if os.getenv("DB_OFFLINE_LOCAL") == "1":
        # Force rewrite for Windows port-forwarded offline tasks.
        # Use POSTGRES_HOST from IAC-injected .env to avoid hardcoding container names.
        # Falls back to 'postgres' (default container name) if not set.
        pg_host = os.getenv("POSTGRES_HOST", "postgres")
        pgbouncer_host = os.getenv("PGBOUNCER_HOST", "pgbouncer")
        POSTGRES_DSN = POSTGRES_DSN.replace(f"@{pgbouncer_host}:5432/", "@localhost:5432/").replace(f"@{pg_host}:5432/", "@localhost:5432/")
    config.set_main_option("sqlalchemy.url", POSTGRES_DSN)


# Import all models to ensure they are registered with Base.metadata
from backend.models.user import Base  # noqa: E402

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


import logging  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
from typing import Any  # noqa: E402

_migration_logger = logging.getLogger("alembic.env.migration_lock")

# 法典 3.5：upgrade head 前必须向 Redis 申请全局互斥锁 DB_MIGRATION_LOCK，防并发 DDL 脑裂
DB_MIGRATION_LOCK_KEY = "zen70:DB_MIGRATION_LOCK"
DB_MIGRATION_LOCK_TIMEOUT = 120  # 初始锁持有时间（秒）
_LOCK_IDENTITY = f"pid={os.getpid()}@{socket.gethostname()}"


def _watchdog_thread(redis_client: Any, lock: Any, stop_event: threading.Event) -> None:
    """守护线程：每 10 秒为迁移锁自动续期，防长耗时 DDL 超时脑裂"""
    while not stop_event.is_set():
        try:
            # PEXPIRE 续期 120 秒
            redis_client.pexpire(lock.name, DB_MIGRATION_LOCK_TIMEOUT * 1000)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            # 仅记录或静默，若 Redis 崩溃，锁终将自然释放
            _migration_logger.debug("Migration lock renew failed [%s]: %s", _LOCK_IDENTITY, exc)
        # 每隔 10 秒心跳一次
        stop_event.wait(10)


def _acquire_migration_lock() -> "tuple[object, object, threading.Event, threading.Thread] | None":
    """尝试连接 Redis 并获取迁移锁；不可用时返回 None（离线模式可跳过）。"""
    try:
        import redis
    except ImportError:
        return None
    try:
        host = os.environ.get("REDIS_HOST", "redis")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        password = os.environ.get("REDIS_PASSWORD") or None
        user = os.environ.get("REDIS_USER", "default")
        r = redis.Redis(
            host=host,
            port=port,
            password=password,
            username=user if password else None,
            socket_connect_timeout=5,
            decode_responses=True,
        )
        r.ping()
        lock = r.lock(DB_MIGRATION_LOCK_KEY, timeout=DB_MIGRATION_LOCK_TIMEOUT)
        _migration_logger.info(
            "正在获取 DB 迁移锁 [%s] key=%s blocking_timeout=60s...",
            _LOCK_IDENTITY,
            DB_MIGRATION_LOCK_KEY,
        )
        if lock.acquire(blocking=True, blocking_timeout=60):
            _migration_logger.info(
                "✓ 迁移锁已获取 [%s] key=%s ttl=%ds",
                _LOCK_IDENTITY,
                DB_MIGRATION_LOCK_KEY,
                DB_MIGRATION_LOCK_TIMEOUT,
            )
            # 成功获取后，拉起看门狗
            stop_event = threading.Event()
            watchdog = threading.Thread(target=_watchdog_thread, args=(r, lock, stop_event), daemon=True)
            watchdog.start()
            return (r, lock, stop_event, watchdog)
        _migration_logger.error(
            "✗ 迁移锁获取超时 [%s] key=%s — 可能有其他节点正在执行迁移",
            _LOCK_IDENTITY,
            DB_MIGRATION_LOCK_KEY,
        )
        raise RuntimeError(
            f"ZEN-DB-MIGRATION-LOCKED: 无法在 60s 内获取 {DB_MIGRATION_LOCK_KEY} " f"[{_LOCK_IDENTITY}]。可能有其他节点正在执行 Alembic 迁移，请稍后重试。"
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError, ConnectionError):
        if os.getenv("SKIP_DB_MIGRATION_LOCK"):
            _migration_logger.warning(
                "SKIP_DB_MIGRATION_LOCK=1, 跳过迁移锁获取 [%s]",
                _LOCK_IDENTITY,
            )
            return None
        raise


def run_migrations_online() -> None:
    """Run migrations in 'online' mode. 法典 3.5：先获取 Redis 迁移锁及看门狗再执行。"""
    lock_bundle = _acquire_migration_lock()
    try:
        asyncio.run(run_async_migrations())
    finally:
        if lock_bundle is not None:
            _r, lock, stop_event, watchdog = lock_bundle
            try:
                stop_event.set()  # 通知看门狗停转
                watchdog.join(timeout=2.0)
                lock.release()  # type: ignore
                _migration_logger.info(
                    "✓ 迁移锁已释放 [%s] key=%s",
                    _LOCK_IDENTITY,
                    DB_MIGRATION_LOCK_KEY,
                )
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                _migration_logger.warning(
                    "迁移锁释放异常 [%s]: %s（锁将在 TTL 后自动消散）",
                    _LOCK_IDENTITY,
                    e,
                )
            try:
                _r.close()  # type: ignore
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                _migration_logger.debug("redis close failed in migration cleanup: %s", e)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
