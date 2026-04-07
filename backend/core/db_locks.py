from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

LockSpec = tuple[str, tuple[object, ...]]


def _normalize_component(value: object) -> str:
    if value is None:
        return "<none>"
    return str(value)


def advisory_lock_id(namespace: str, *components: object) -> int:
    material = "|".join([namespace, *(_normalize_component(component) for component in components)])
    digest = hashlib.blake2b(material.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_transaction_advisory_locks(db: AsyncSession, lock_specs: Iterable[LockSpec]) -> None:
    lock_ids = sorted({advisory_lock_id(namespace, *components) for namespace, components in lock_specs})
    for lock_id in lock_ids:
        await db.execute(select(func.pg_advisory_xact_lock(lock_id)))
