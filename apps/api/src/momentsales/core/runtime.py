"""Shared resources for code that runs outside a request (jobs, sweepers, scripts)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from momentsales.core.config import Settings, get_settings
from momentsales.core.redis import get_redis
from momentsales.modules.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    registry: ProviderRegistry


@asynccontextmanager
async def runtime() -> AsyncIterator[Runtime]:
    settings = get_settings()
    async with httpx.AsyncClient(headers={"User-Agent": "momentsales-worker/0.1"}) as http:
        yield Runtime(settings, ProviderRegistry(settings, get_redis(settings.redis_url), http))
