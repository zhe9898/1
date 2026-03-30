#!/usr/bin/env python3
"""
为指定用户设置或覆盖 PIN（bcrypt 哈希写入 users.pin_hash）。
用于系统初始化或运维；需 POSTGRES_DSN。
用法：PYTHONPATH=. python backend/scripts/set_pin.py <username> <6位PIN>
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 项目根加入 path
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from backend.models.user import User  # noqa: E402


def _hash_pin(pin: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


async def _main() -> None:
    if len(sys.argv) != 3:
        print(
            "Usage: PYTHONPATH=. python backend/scripts/set_pin.py <username> <6-digit-PIN>",
            file=sys.stderr,
        )
        sys.exit(2)
    username, pin = sys.argv[1], sys.argv[2]
    if len(pin) != 6 or not pin.isdigit():
        print("PIN must be 6 digits", file=sys.stderr)
        sys.exit(2)

    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        print("POSTGRES_DSN not set", file=sys.stderr)
        sys.exit(1)
    async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(async_dsn, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            print(f"User not found: {username}", file=sys.stderr)
            sys.exit(3)
        user.pin_hash = _hash_pin(pin)
        await session.commit()
    await engine.dispose()
    print("PIN set successfully.")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
