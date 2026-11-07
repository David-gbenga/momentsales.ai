"""Shared Redis client."""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from momentsales.core.config import get_settings


@lru_cache(maxsize=4)
def get_redis(url: str | None = None) -> Redis:
    return Redis.from_url(url or get_settings().redis_url, decode_responses=True)
