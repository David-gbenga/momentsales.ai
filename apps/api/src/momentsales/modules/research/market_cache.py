"""Market-data gateway for agents (ADR-0019): shared cache, cost cap, monthly budget,
source snapshots and tool-call records in one place.

Order for each request:
1. Shared cache: the newest row for the same (data type, country, query) younger than
   `MARKET_CACHE_MAX_AGE_HOURS`. A hit costs nothing.
2. Otherwise the run's cost cap (`BudgetExceededError`) and the platform's monthly
   budget (`MonthlyBudgetReachedError`) are checked, the provider is called, and the
   result is charged and written to the cache.
Either way the raw response is stored as this run's own `SourceSnapshot` (AC-3), with
the original URL and fetch time.

The cache holds only the public query and the provider's public result: no workspace,
run or user. A seller's own data never enters it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select

from momentsales.adapters.market_data.base import DataType, MarketDataPort
from momentsales.agents.base import AgentContext
from momentsales.core.logging import get_logger
from momentsales.db.research import MarketDataCache

log = get_logger(__name__)

R = TypeVar("R", bound=BaseModel)


class MonthlyBudgetReachedError(Exception):
    """The platform's monthly market-data budget would be passed: use the cache only."""


def query_key(data_type: DataType, country: str, query: dict[str, Any]) -> str:
    blob = json.dumps({"t": data_type, "c": country, "q": query}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def budget_key(now: datetime) -> str:
    return f"market-data-spend:{now:%Y-%m}"


@dataclass(frozen=True, slots=True)
class Fetched[T]:
    result: T
    snapshot_id: uuid.UUID
    cache_hit: bool


class MarketGateway:
    def __init__(
        self, ctx: AgentContext, port: MarketDataPort, *, category: str | None = None
    ) -> None:
        self.ctx = ctx
        self.port = port
        self.category = category
        self.max_age = timedelta(hours=ctx.settings.market_cache_max_age_hours)

    async def get(
        self,
        data_type: DataType,
        query: dict[str, Any],
        model: type[R],
        fetch: Callable[[], Awaitable[R]],
    ) -> Fetched[R]:
        ctx = self.ctx
        country = ctx.run.country
        key = query_key(data_type, country, query)
        now = datetime.now(UTC)
        row = await ctx.session.scalar(
            select(MarketDataCache)
            .where(
                MarketDataCache.data_type == data_type,
                MarketDataCache.country == country,
                MarketDataCache.query_key == key,
                MarketDataCache.fetched_at >= now - self.max_age,
            )
            .order_by(MarketDataCache.fetched_at.desc())
            .limit(1)
        )
        if row is not None:
            result = model.model_validate(row.result)
            snapshot_id = await self._snapshot(data_type, result)
            ctx.record_call(
                "market_data",
                data_type=data_type,
                provider=row.provider,
                query=query,
                cache_hit=True,
                cost_micros=0,
            )
            return Fetched(result, snapshot_id, True)

        estimate = self.port.estimate_micros(data_type)
        ctx.meter.ensure(estimate)  # BudgetExceededError: the agent stops, keeps its data
        await self._check_monthly(estimate, now)
        result = await fetch()
        cost = int(getattr(result, "cost_micros", 0))
        provider = str(getattr(result, "provider", self.port.name))
        ctx.meter.charge(provider, cost)
        await self._add_monthly(cost, now)
        snapshot_id = await self._snapshot(data_type, result)
        ctx.session.add(
            MarketDataCache(
                data_type=data_type,
                country=country,
                category=self.category,
                iso_week=iso_week(now.date()),
                query_key=key,
                query=query,
                provider=provider,
                url=str(getattr(result, "url", "")),
                result=result.model_dump(mode="json"),
                cost_micros=cost,
                fetched_at=getattr(result, "fetched_at", now),
            )
        )
        raw = getattr(result, "raw", None)
        ctx.record_call(
            "market_data",
            data_type=data_type,
            provider=provider,
            query=query,
            cache_hit=False,
            cost_micros=cost,
            failover=provider != self.port.name,
            provider_cost_usd=raw.get("cost") if isinstance(raw, dict) else None,
        )
        return Fetched(result, snapshot_id, False)

    async def _snapshot(self, data_type: DataType, result: BaseModel) -> uuid.UUID:
        return await self.ctx.snapshot(
            provider=str(getattr(result, "provider", "")),
            kind=data_type,
            url=str(getattr(result, "url", "")),
            payload=getattr(result, "raw", None),
            fetched_at=getattr(result, "fetched_at", datetime.now(UTC)),
        )

    async def _check_monthly(self, estimate: int, now: datetime) -> None:
        redis = self.ctx.redis
        budget = self.ctx.settings.market_data_monthly_budget_micros
        if redis is None or estimate == 0:
            return
        spent = int(await redis.get(budget_key(now)) or 0)
        if spent + estimate > budget:
            log.warning("market_data_monthly_budget_reached", spent=spent, budget=budget)
            raise MonthlyBudgetReachedError(f"monthly market-data budget reached ({spent})")

    async def _add_monthly(self, cost: int, now: datetime) -> None:
        redis = self.ctx.redis
        if redis is None or cost == 0:
            return
        key = budget_key(now)
        await redis.incrby(key, cost)
        await redis.expire(key, 40 * 24 * 3600)
