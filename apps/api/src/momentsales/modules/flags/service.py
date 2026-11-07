"""Feature flags (ADR-0007): global default + per-workspace override, cached in Redis.

Countries are flags too: a country is selectable only if `country.<ISO2>` is on.
"""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.logging import get_logger
from momentsales.db.models import FeatureFlag, FeatureFlagOverride

log = get_logger(__name__)
CACHE_TTL_SECONDS = 30
COUNTRY_PREFIX = "country."

# Display names for the countries we hold flags for (ISO 3166-1 alpha-2).
COUNTRY_NAMES = {
    "GB": "United Kingdom",
    "US": "United States",
    "AU": "Australia",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
}
# Base currency per country (used as the workspace default).
COUNTRY_CURRENCY = {
    "GB": "GBP",
    "US": "USD",
    "AU": "AUD",
    "IE": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "ES": "EUR",
    "IT": "EUR",
    "NL": "EUR",
}


def _cache_key(workspace_id: uuid.UUID | None) -> str:
    return f"flags:{workspace_id or 'global'}"


class FeatureFlagService:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    async def effective(
        self, session: AsyncSession, workspace_id: uuid.UUID | None
    ) -> dict[str, bool]:
        cached = await self._cache_get(workspace_id)
        if cached is not None:
            return cached
        flags = {f.key: f.default_enabled for f in await session.scalars(select(FeatureFlag))}
        if workspace_id is not None:
            overrides = await session.scalars(
                select(FeatureFlagOverride).where(FeatureFlagOverride.workspace_id == workspace_id)
            )
            for o in overrides:
                flags[o.flag_key] = o.enabled
        await self._cache_set(workspace_id, flags)
        return flags

    async def is_enabled(
        self, session: AsyncSession, key: str, workspace_id: uuid.UUID | None = None
    ) -> bool:
        return (await self.effective(session, workspace_id)).get(key, False)

    async def enabled_countries(self, session: AsyncSession) -> list[str]:
        flags = await self.effective(session, None)
        return sorted(
            k.removeprefix(COUNTRY_PREFIX)
            for k, on in flags.items()
            if k.startswith(COUNTRY_PREFIX) and on
        )

    async def invalidate(self, workspace_id: uuid.UUID | None) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(_cache_key(workspace_id))
        except RedisError:
            log.warning("flag_cache_invalidate_failed", workspace_id=str(workspace_id))

    async def _cache_get(self, workspace_id: uuid.UUID | None) -> dict[str, bool] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_cache_key(workspace_id))
        except RedisError:
            return None
        if raw is None:
            return None
        data: dict[str, bool] = json.loads(raw)
        return data

    async def _cache_set(self, workspace_id: uuid.UUID | None, flags: dict[str, bool]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(_cache_key(workspace_id), json.dumps(flags), ex=CACHE_TTL_SECONDS)
        except RedisError:
            log.warning("flag_cache_set_failed")
