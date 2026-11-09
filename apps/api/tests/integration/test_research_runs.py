"""Research runs (Phase 2a): creation rules, metering, the Season agent, the orchestrator's
failure, partial and resume behaviour, live events, the season calendar and the audience
profile."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from helpers import Session, add_member, create_workspace, sign_in
from sqlalchemy import select, text, update

from momentsales.adapters.holidays.base import HolidaysError
from momentsales.adapters.holidays.mock import MockHolidaysAdapter
from momentsales.agents import orchestrator
from momentsales.agents.base import AgentContext, AgentResult
from momentsales.agents.season import SeasonAgent
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.redis import get_redis
from momentsales.db.billing import RunConsumption
from momentsales.db.research import AgentStep, ResearchRun, RunStatus, SourceSnapshot, StepStatus
from momentsales.modules.providers.registry import ProviderRegistry
from momentsales.modules.research import jobs as research_jobs
from momentsales.modules.research import router as research_router

API = "/api/v1"
UK_6_NOV = {"country": "GB", "as_of": "2026-11-06"}


async def shop(client: httpx.AsyncClient, plan: str | None = "pro") -> tuple[Session, str]:
    owner = await sign_in(client)
    return owner, await create_workspace(owner, "Research Shop", plan=plan)


async def start(owner: Session, ws: str, **body: Any) -> httpx.Response:
    headers = owner.headers(ws)
    if key := body.pop("idempotency_key", None):
        headers["Idempotency-Key"] = key
    return await owner.client.post(
        f"{API}/research-runs", json={**UK_6_NOV, **body}, headers=headers
    )


async def snapshots_for(ws: str, run_id: str) -> dict[str, SourceSnapshot]:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        rows = (
            await s.scalars(select(SourceSnapshot).where(SourceSnapshot.run_id == run_id))
        ).all()
    return {str(r.id): r for r in rows}


async def test_uk_seasonal_run_on_6_november(client: httpx.AsyncClient) -> None:
    """AC-1 (season part) and AC-3 for the facts the Season agent shows."""
    owner, ws = await shop(client)
    r = await start(owner, ws)
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]

    run = (await client.get(f"{API}/research-runs/{run_id}", headers=owner.headers(ws))).json()
    assert run["status"] == "completed", run
    season = run["season"]
    assert (season["current"], season["next"]) == ("autumn", "winter")
    names = [m["name"] for m in season["moments"]]
    assert "Black Friday" in names and "Christmas" in names
    assert [s["agent"] for s in run["steps"]] == ["season", "trend", "marketplace", "supplier"]
    assert {s["status"] for s in run["steps"]} == {"completed"}
    # Mock market data charges the primary's estimates (nothing when the shared cache
    # already holds the results; see test_demand_runs.py).
    assert 0 <= run["cost_minor"] <= run["cost_cap_minor"] == 100

    # Every holiday shown traces to a stored source snapshot with URL and fetch time.
    holidays = season["public_holidays"]
    assert {h["name"] for h in holidays} >= {"Christmas Day", "Boxing Day"}
    snaps = await snapshots_for(ws, run_id)
    for h in holidays:
        snap = snaps[h["snapshot_id"]]
        assert snap.url and snap.fetched_at and snap.kind == "public_holidays"
    assert {s["snapshot_id"] for s in season["sources"]} <= set(snaps)


async def test_runs_are_metered_and_stop_at_the_plan_limit(client: httpx.AsyncClient) -> None:
    """AC-15: runs spend the plan allowance and then get 402 plan_limit_reached."""
    owner, ws = await shop(client, plan="lite")
    statuses = []
    for _ in range(30):
        r = await start(owner, ws)
        statuses.append(r.status_code)
        if r.status_code == 402:
            body = r.json()
            assert body["type"] == "plan_limit_reached" and body["limit"] == "research_runs"
            break
    assert statuses[-1] == 402 and set(statuses[:-1]) == {201}
    runs = (await client.get(f"{API}/research-runs", headers=owner.headers(ws))).json()
    assert len(runs["items"]) == len(statuses) - 1


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"country": "US"}, "country_not_available"),
        ({"country": "AU"}, "country_not_available"),  # engine knows AU; the flag is off
        ({"mode": "audience"}, "feature_not_available"),
        ({"audience_source_ids": [str(uuid.uuid4())]}, "feature_not_available"),
        ({"cost_cap_minor": 101}, "cost_cap_too_high"),
    ],
)
async def test_rejected_runs_spend_nothing(
    client: httpx.AsyncClient, body: dict[str, Any], code: str
) -> None:
    owner, ws = await shop(client)
    r = await start(owner, ws, **body)
    assert r.status_code == 422, r.text
    assert r.json()["type"] == code
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        assert await s.scalar(select(RunConsumption)) is None


async def test_window_bounds(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    assert (await start(owner, ws, window_weeks=1)).status_code == 422
    assert (await start(owner, ws, window_weeks=13)).status_code == 422
    r = await start(owner, ws, window_weeks=2, season_override="winter")
    assert r.status_code == 201
    run = (
        await client.get(f"{API}/research-runs/{r.json()['id']}", headers=owner.headers(ws))
    ).json()
    assert run["season"]["current"] == "winter" and run["season"]["overridden"] is True
    assert [m["name"] for m in run["season"]["moments"]] == ["Singles' Day"]


async def test_idempotency_key_returns_the_same_run(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    a = await start(owner, ws, idempotency_key="run-once")
    b = await start(owner, ws, idempotency_key="run-once")
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        assert len((await s.scalars(select(RunConsumption))).all()) == 1


async def test_roles(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    analyst = await add_member(owner, ws, "analyst")
    accountant = await add_member(owner, ws, "accountant")
    assert (await start(analyst, ws)).status_code == 201
    r = await start(accountant, ws)
    assert r.status_code == 403
    r = await client.get(f"{API}/research-runs", headers=accountant.headers(ws))
    assert r.status_code == 403


async def test_other_workspace_cannot_read_a_run(client: httpx.AsyncClient) -> None:
    owner_a, ws_a = await shop(client)
    owner_b, ws_b = await shop(client)
    run_id = (await start(owner_a, ws_a)).json()["id"]
    r = await client.get(f"{API}/research-runs/{run_id}", headers=owner_b.headers(ws_b))
    assert r.status_code == 404
    r = await client.get(f"{API}/research-runs/{run_id}/events", headers=owner_b.headers(ws_b))
    assert r.status_code == 404


class _Boom:
    name = "boom"
    tools: tuple[str, ...] = ()
    timeout_seconds = 5.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        raise RuntimeError("provider exploded")


class _OverBudget:
    name = "over_budget"
    tools: tuple[str, ...] = ()
    timeout_seconds = 5.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        ctx.meter.ensure(ctx.meter.cap_micros + 1)
        raise AssertionError("unreachable")


async def _usage(owner: Session, ws: str) -> int:
    r = await owner.client.get(f"{API}/billing/usage", headers=owner.headers(ws))
    assert r.status_code == 200, r.text
    return int(r.json()["research_runs"]["used"])


async def test_failed_run_before_any_spend_gives_the_run_back(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, ws = await shop(client, plan="growth")
    before = await _usage(owner, ws)
    monkeypatch.setattr(orchestrator, "PIPELINE", (SeasonAgent(), _Boom()))
    run_id = (await start(owner, ws)).json()["id"]
    run = (await client.get(f"{API}/research-runs/{run_id}", headers=owner.headers(ws))).json()
    assert run["status"] == "failed" and "provider exploded" in run["error"]
    assert run["run_refunded"] is True
    assert [(s["agent"], s["status"]) for s in run["steps"]] == [
        ("season", "completed"),
        ("boom", "failed"),
    ]
    assert await _usage(owner, ws) == before


async def test_cost_cap_stops_the_run_as_partial(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, ws = await shop(client)
    monkeypatch.setattr(orchestrator, "PIPELINE", (SeasonAgent(), _OverBudget()))
    run_id = (await start(owner, ws)).json()["id"]
    run = (await client.get(f"{API}/research-runs/{run_id}", headers=owner.headers(ws))).json()
    assert run["status"] == "partial" and "cost cap" in run["error"]
    assert run["season"] is not None  # what was found before the cap is kept
    assert run["run_refunded"] is False


async def test_holidays_outage_degrades_the_step_not_the_run(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: MockHolidaysAdapter, country: str, year: int) -> Any:
        raise HolidaysError("mock", "503")

    monkeypatch.setattr(MockHolidaysAdapter, "holidays", down)
    owner, ws = await shop(client)
    run_id = (await start(owner, ws)).json()["id"]
    run = (await client.get(f"{API}/research-runs/{run_id}", headers=owner.headers(ws))).json()
    assert run["status"] == "completed"
    assert run["steps"][0]["status"] == "degraded"
    assert run["season"]["public_holidays"] is None  # shown as "not available"
    assert "not available" in run["steps"][0]["summary"]


class _Counter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: tuple[str, ...] = ()
        self.timeout_seconds = 5.0
        self.calls = 0

    async def run(self, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        return AgentResult(output={"n": self.calls}, summary=f"{self.name} done")


async def test_a_retried_run_resumes_after_finished_steps(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(research_router, "schedule", lambda *a, **k: None)  # don't run yet
    owner, ws = await shop(client)
    run_id = uuid.UUID((await start(owner, ws)).json()["id"])
    first, second = _Counter("first"), _Counter("second")
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        s.add(
            AgentStep(
                workspace_id=uuid.UUID(ws),
                run_id=run_id,
                agent="first",
                position=0,
                status=StepStatus.COMPLETED,
                input={},
                output={"n": 99},
            )
        )
        await s.execute(
            update(ResearchRun).where(ResearchRun.id == run_id).values(status=RunStatus.RUNNING)
        )
    settings = get_settings()
    async with httpx.AsyncClient() as http:
        status = await orchestrator.execute(
            run_id,
            uuid.UUID(ws),
            registry=ProviderRegistry(settings, get_redis(settings.redis_url), http),
            settings=settings,
            redis=get_redis(settings.redis_url),
            pipeline=(first, second),
        )
    assert status is RunStatus.COMPLETED
    assert (first.calls, second.calls) == (0, 1)
    # Running it again changes nothing: the run is finished.
    async with httpx.AsyncClient() as http:
        again = await orchestrator.execute(
            run_id,
            uuid.UUID(ws),
            registry=ProviderRegistry(settings, get_redis(settings.redis_url), http),
            settings=settings,
            redis=get_redis(settings.redis_url),
            pipeline=(first, second),
        )
    assert again is RunStatus.COMPLETED and second.calls == 1


async def test_sweeper_requeues_stuck_runs(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(research_router, "schedule", lambda *a, **k: None)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(research_jobs, "enqueue_now", lambda s, name, p: sent.append(p))
    owner, ws = await shop(client)
    run_id = (await start(owner, ws)).json()["id"]
    assert (await research_jobs.sweep())["research_runs"] == 0 or run_id not in str(sent)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        await s.execute(
            text("UPDATE research_runs SET created_at = :t WHERE id = :r"),
            {"t": datetime.now(UTC) - timedelta(minutes=5), "r": run_id},
        )
    sent.clear()
    await research_jobs.sweep()
    assert {"workspace_id": ws, "run_id": run_id} in sent


async def test_events_stream_sends_the_run_then_ends(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    run_id = (await start(owner, ws)).json()["id"]
    r = await client.get(f"{API}/research-runs/{run_id}/events", headers=owner.headers(ws))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: run\ndata: " in r.text and '"status":"completed"' in r.text
    assert r.text.rstrip().endswith("event: end\ndata: {}")


async def test_season_calendar_lists_the_next_three_moments(client: httpx.AsyncClient) -> None:
    """AC-26 (calendar part): act-by = moment - (delivery max + 21 days)."""
    owner, ws = await shop(client, plan=None)  # Home works without a plan
    r = await client.get(
        f"{API}/season-calendar", params={"as_of": "2026-11-06"}, headers=owner.headers(ws)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["country"] == "GB" and body["delivery_source"] == "assumed"
    assert [m["name"] for m in body["moments"]] == ["Singles' Day", "Black Friday", "Cyber Monday"]
    days = body["delivery_days"] + 21
    for m in body["moments"]:
        moment = datetime.fromisoformat(m["date"])
        assert (moment - datetime.fromisoformat(m["act_by"])).days == days


async def test_audience_profile_is_saved_and_used_by_runs(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    r = await client.get(f"{API}/audience-profile", headers=owner.headers(ws))
    assert r.json()["interests"] == []
    profile = {
        "age_range": "25-34",
        "gender_focus": "any",
        "interests": ["Hiking", "hiking", " camping "],
        "budget_band": "mid",
        "style": "outdoorsy",
    }
    r = await client.put(f"{API}/audience-profile", json=profile, headers=owner.headers(ws))
    assert r.status_code == 200, r.text
    assert r.json()["interests"] == ["Hiking", "camping"]
    bad = {**profile, "age_range": "12-15"}
    r = await client.put(f"{API}/audience-profile", json=bad, headers=owner.headers(ws))
    assert r.status_code == 422
    run = (await start(owner, ws)).json()
    assert run["params"]["audience"]["interests"] == ["Hiking", "camping"]
    run = (await start(owner, ws, use_audience_profile=False)).json()
    assert "audience" not in run["params"]
