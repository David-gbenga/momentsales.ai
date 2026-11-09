"""Phase 2b demand intelligence (ADR-0019): trend and marketplace agents in real runs,
source snapshots (AC-3), rising vs crowded (AC-25 data part), the provider switch and
failover (AC-34), the shared cache, the cost cap and the monthly budget."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from helpers import Session, create_workspace, sign_in
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from momentsales.adapters.market_data.base import MarketDataError
from momentsales.adapters.market_data.mock import MockMarketDataAdapter
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.redis import get_redis
from momentsales.db.research import AgentStep, MarketDataCache, SourceSnapshot
from momentsales.modules.research.market_cache import budget_key

API = "/api/v1"
UK_6_NOV = {"country": "GB", "as_of": "2026-11-06"}


async def clear_cache() -> None:
    """The app role cannot delete cache rows (append-only); the owner role can."""
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM market_data_cache"))
    await engine.dispose()


async def age_cache(hours: int) -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE market_data_cache SET fetched_at = now() - make_interval(hours => :h)"),
            {"h": hours},
        )
    await engine.dispose()


@pytest.fixture(autouse=True)
async def cold_cache() -> AsyncIterator[None]:
    await clear_cache()
    yield


async def run_once(client: httpx.AsyncClient, **body: Any) -> tuple[Session, str, dict[str, Any]]:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Demand Shop", plan="pro")
    r = await owner.client.post(
        f"{API}/research-runs", json={**UK_6_NOV, **body}, headers=owner.headers(ws)
    )
    assert r.status_code == 201, r.text
    run = (
        await client.get(f"{API}/research-runs/{r.json()['id']}", headers=owner.headers(ws))
    ).json()
    return owner, ws, run


async def snapshots(ws: str, run_id: str) -> dict[str, SourceSnapshot]:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        rows = (
            await s.scalars(select(SourceSnapshot).where(SourceSnapshot.run_id == run_id))
        ).all()
    return {str(r.id): r for r in rows}


async def steps(ws: str, run_id: str) -> dict[str, AgentStep]:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        rows = (await s.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()
    return {r.agent: r for r in rows}


def by_keyword(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["keyword"]: c for c in run["demand"]["candidates"]}


async def assert_traceable(ws: str, run: dict[str, Any]) -> None:
    """AC-3: every trend and market number links to this run's stored snapshot."""
    snaps = await snapshots(ws, run["id"])
    for c in run["demand"]["candidates"]:
        refs = []
        if c["trend"]:
            refs.append(c["trend"]["snapshot_id"])
        if c["market"]:
            refs.append(c["market"]["snapshot_id"])
            refs += [o["snapshot_id"] for o in c["market"]["offers"]]
        for ref in refs:
            snap = snaps[ref]
            assert snap.url and snap.fetched_at and snap.payload


async def test_uk_run_finds_rising_and_crowded_searches(client: httpx.AsyncClient) -> None:
    _, ws, run = await run_once(client)
    assert run["status"] == "completed", run
    demand = run["demand"]
    assert demand["mock"] is True and demand["trend_available"] and demand["market_available"]
    found = by_keyword(run)
    # Seed terms for the moments in the window (Black Friday, Christmas, ...).
    assert {"heated throw", "advent calendar", "wireless earbuds"} <= set(found)
    rising, crowded = found["heated throw"], found["advent calendar"]
    assert rising["trend"]["growth_rate"] > 0 and rising["trend"]["peak"] is False
    assert crowded["trend"]["peak"] is True  # "crowded" badge
    # Rising searches around the anchors are added as candidates.
    assert any(c["source"] == "rising" and c["rising_from"] for c in found.values())
    # Competition for measured searches, and a crowded search scores as more crowded.
    assert 0 <= rising["market"]["competition_score"] <= 100
    assert crowded["market"]["competition_score"] > rising["market"]["competition_score"]
    offers = crowded["market"]["offers"]
    assert offers and all(o["url"].startswith("https://example.com/mock/") for o in offers)
    await assert_traceable(ws, run)
    assert run["cost_minor"] > 0
    st = await steps(ws, run["id"])
    assert st["trend"].cost_micros > 0 and st["marketplace"].cost_micros > 0


async def test_provider_switch_runs_the_same_flow(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-34: PROVIDER_MARKET_DATA set to the secondary passes the same research test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_market_data", "serpapi")
    monkeypatch.setattr(settings, "provider_market_data_secondary", "dataforseo")
    _, ws, run = await run_once(client)
    assert run["status"] == "completed"
    found = by_keyword(run)
    assert found["heated throw"]["trend"]["provider"] == "serpapi"
    assert found["advent calendar"]["trend"]["peak"] is True
    await assert_traceable(ws, run)
    st = await steps(ws, run["id"])
    providers = {c["provider"] for c in st["trend"].tool_calls}
    assert providers == {"serpapi"}


async def test_primary_error_fails_over_automatically(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-34: a primary data-provider error fails over to the secondary."""
    originals = {
        m: getattr(MockMarketDataAdapter, m)
        for m in ("interest_over_time", "related_queries", "serp_overview")
    }

    def broken(method: str) -> Any:
        async def call(self: MockMarketDataAdapter, *args: Any) -> Any:
            if self.name == "dataforseo":
                raise MarketDataError("dataforseo", "status 50301", retryable=True)
            return await originals[method](self, *args)

        return call

    for m in originals:
        monkeypatch.setattr(MockMarketDataAdapter, m, broken(m))
    _, ws, run = await run_once(client)
    assert run["status"] == "completed"
    assert {s["status"] for s in run["steps"]} == {"completed"}
    assert by_keyword(run)["heated throw"]["trend"]["provider"] == "serpapi"
    st = await steps(ws, run["id"])
    calls = st["trend"].tool_calls + st["marketplace"].tool_calls
    assert calls and all(c["failover"] and c["provider"] == "serpapi" for c in calls)


async def test_both_providers_down_degrades_without_failing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: MockMarketDataAdapter, *args: Any) -> Any:
        raise MarketDataError(self.name, "HTTP 503", retryable=True)

    for m in ("interest_over_time", "related_queries", "serp_overview"):
        monkeypatch.setattr(MockMarketDataAdapter, m, down)
    _, _, run = await run_once(client)
    assert run["status"] == "completed"
    assert [(s["agent"], s["status"]) for s in run["steps"]] == [
        ("season", "completed"),
        ("trend", "degraded"),
        ("marketplace", "degraded"),
        ("supplier", "degraded"),
    ]
    assert run["demand"]["trend_available"] is False
    assert all(c["trend"] is None and c["market"] is None for c in run["demand"]["candidates"])
    assert run["cost_minor"] == 0


async def test_shared_cache_serves_the_next_workspace_for_free(
    client: httpx.AsyncClient,
) -> None:
    _, ws_a, first = await run_once(client)
    assert first["cost_minor"] > 0
    _, ws_b, second = await run_once(client)
    assert second["status"] == "completed" and second["cost_minor"] == 0
    st = await steps(ws_b, second["id"])
    assert all(c["cache_hit"] for c in st["trend"].tool_calls + st["marketplace"].tool_calls)
    # Workspace B gets its own snapshots, with the original URL and fetch time.
    a = by_keyword(first)["heated throw"]["trend"]
    b = by_keyword(second)["heated throw"]["trend"]
    assert a["snapshot_id"] != b["snapshot_id"]
    assert (a["url"], a["fetched_at"]) == (b["url"], b["fetched_at"])
    await assert_traceable(ws_b, second)
    assert (await snapshots(ws_a, second["id"])) == {}  # and A cannot see B's


async def test_cache_older_than_24_hours_is_fetched_again(client: httpx.AsyncClient) -> None:
    await run_once(client)
    await age_cache(25)
    _, _, again = await run_once(client)
    assert again["cost_minor"] > 0


async def test_cache_holds_no_workspace_data(client: httpx.AsyncClient) -> None:
    owner, ws, run = await run_once(client)
    columns = {c.key for c in inspect(MarketDataCache).columns}
    assert not columns & {"workspace_id", "run_id", "user_id", "created_by"}
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.connect() as conn:
        dump = " ".join(
            str(r) for r in (await conn.execute(text("SELECT * FROM market_data_cache"))).all()
        )
    await engine.dispose()
    assert dump and ws not in dump and run["id"] not in dump and owner.email not in dump


async def test_cost_cap_keeps_what_was_paid_for(client: httpx.AsyncClient) -> None:
    # 3p cap. The meter checks the dearer provider's estimate (2p, SerpApi) before each
    # call and charges what answered (0.9p, DataForSEO): two calls fit, the third does not.
    _, ws, run = await run_once(client, cost_cap_minor=3)
    assert run["status"] == "partial" and "cost cap" in run["error"]
    assert run["cost_minor"] == 2 and run["demand"]["stopped_at_cap"] is True
    assert [s["agent"] for s in run["steps"]] == ["season", "trend"]  # marketplace not run
    snaps = await snapshots(ws, run["id"])
    assert any(s.kind == "related_queries" for s in snaps.values())  # paid data kept


async def test_monthly_budget_reached_uses_the_cache_only(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    redis = get_redis(settings.redis_url)
    key = budget_key(datetime.now(UTC))
    await redis.set(key, settings.market_data_monthly_budget_micros)
    try:
        _, _, run = await run_once(client)
    finally:
        await redis.delete(key)
    assert run["status"] == "completed" and run["cost_minor"] == 0
    assert {s["agent"]: s["status"] for s in run["steps"]}["trend"] == "degraded"


async def test_run_stores_cost_by_provider(client: httpx.AsyncClient) -> None:
    _, ws, run = await run_once(client)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        row = (
            await s.execute(
                text("SELECT cost_micros, cost_by_provider FROM research_runs WHERE id = :i"),
                {"i": run["id"]},
            )
        ).one()
    micros, by_provider = row
    assert set(by_provider) == {"dataforseo"} and sum(by_provider.values()) == micros > 0
    assert run["cost_minor"] == -(-micros // 10_000)


async def test_price_band_limits_the_offers_shown(client: httpx.AsyncClient) -> None:
    _, _, run = await run_once(
        client, price_band={"min_minor": 1000, "max_minor": 2500, "currency": "GBP"}
    )
    for c in run["demand"]["candidates"]:
        for o in (c["market"] or {}).get("offers", []):
            assert 1000 <= o["price_minor"] <= 2500
