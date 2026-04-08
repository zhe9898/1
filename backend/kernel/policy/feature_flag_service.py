from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.feature_flag import FeatureFlag


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class FeatureFlagService:
    @staticmethod
    async def get_flag(db: AsyncSession, key: str) -> FeatureFlag | None:
        result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
        return result.scalars().first()

    @staticmethod
    async def set_flag(
        db: AsyncSession,
        *,
        key: str,
        enabled: bool,
        description: str | None = None,
        category: str = "general",
        updated_by: str | None = None,
    ) -> FeatureFlag:
        flag = await FeatureFlagService.get_flag(db, key)
        now = _utcnow()
        if flag is None:
            flag = FeatureFlag(
                key=key,
                enabled=enabled,
                description=description,
                category=category,
                updated_at=now,
                updated_by=updated_by,
            )
            db.add(flag)
        else:
            flag.enabled = enabled
            if description is not None:
                flag.description = description
            flag.category = category or flag.category
            flag.updated_at = now
            flag.updated_by = updated_by
        await db.flush()
        return flag

    @staticmethod
    async def toggle_flag(
        db: AsyncSession,
        *,
        key: str,
        updated_by: str | None = None,
    ) -> FeatureFlag:
        flag = await FeatureFlagService.get_flag(db, key)
        if flag is None:
            raise ValueError(f"Unknown feature flag '{key}'")
        return await FeatureFlagService.set_flag(
            db,
            key=key,
            enabled=not bool(flag.enabled),
            description=flag.description,
            category=flag.category,
            updated_by=updated_by,
        )
