"""Research run, season calendar and audience profile endpoints (ADR-0018)."""

from __future__ import annotations

import datetime as dt
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.core.jobs import schedule
from momentsales.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, page, paginate
from momentsales.db.models import Workspace
from momentsales.db.research import (
    TERMINAL_STATUSES,
    AgentStep,
    AudienceProfile,
    ResearchRun,
    RunMode,
    RunStatus,
    StepStatus,
)
from momentsales.deps import AppRedis, AppSettings, Flags
from momentsales.modules.research import season as engine
from momentsales.modules.research import service
from momentsales.modules.research.events import channel
from momentsales.modules.research.jobs import RUN_JOB
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["research"])

STREAM_MAX_SECONDS = 15 * 60
STREAM_POLL_SECONDS = 5.0


class PriceBand(BaseModel):
    min_minor: int = Field(ge=0)
    max_minor: int = Field(ge=0)
    currency: Literal["GBP"] = "GBP"

    @model_validator(mode="after")
    def _ordered(self) -> PriceBand:
        if self.min_minor > self.max_minor:
            raise ValueError("min_minor must not exceed max_minor")
        return self


class RunCreate(BaseModel):
    mode: RunMode = RunMode.SEASONAL
    country: str = Field(pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=80)
    price_band: PriceBand | None = None
    window_weeks: int = Field(
        default=engine.DEFAULT_WINDOW_WEEKS, ge=engine.MIN_WINDOW_WEEKS, le=engine.MAX_WINDOW_WEEKS
    )
    season_override: engine.Season | None = None
    use_audience_profile: bool = True
    # Later roadmap (L1): rejected unless module.audience_research is on.
    audience_source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    cost_cap_minor: int | None = Field(default=None, ge=1)
    # Local testing aid (dev tools only): run as if on this UK date.
    as_of: date | None = None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent: str
    position: int
    status: StepStatus
    summary: str | None
    cost_minor: int
    duration_ms: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class OfferOut(BaseModel):
    """A shopping offer seen on Google: demand evidence and a purchase link only."""

    title: str
    merchant: str | None
    price_minor: int | None
    currency: str | None
    url: str | None
    snapshot_id: uuid.UUID


class TrendOut(BaseModel):
    growth_rate: float | None
    peak: bool
    last_year_lift: float | None
    recent_mean: float | None
    baseline_mean: float | None
    max_52w: int | None
    insufficient: bool
    points: int
    provider: str
    snapshot_id: uuid.UUID
    url: str
    fetched_at: datetime


class MarketOut(BaseModel):
    total_results: int | None
    paid_ads: int
    shopping_ads: int
    merchants: int
    price_min_minor: int | None
    price_max_minor: int | None
    currency: str | None
    offers: list[OfferOut]
    offers_outside_band: int = 0
    # 0-100, lower is better; None when nothing could be measured.
    competition_score: int | None
    competition_parts: dict[str, float]
    competition_missing: list[str]
    provider: str
    snapshot_id: uuid.UUID
    url: str
    fetched_at: datetime


class MoneyOut(BaseModel):
    amount_minor: int | None
    currency: str | None


class ShippingOut(BaseModel):
    name: str
    amount_minor: int | None
    currency: str | None
    # api: the supplier's freight quote; published: the supplier's published service tier.
    source: Literal["api", "published"]


class FxOut(BaseModel):
    base: str
    quote: str
    rate: str
    rate_date: date
    provider: str
    snapshot_id: uuid.UUID


class MarginOut(BaseModel):
    result: Literal["passed", "failed", "not_checked"]
    sell_price_minor: int | None
    sell_price_source: Literal["market_median_estimate"]
    unit_cost_minor: int | None
    shipping_minor: int | None
    stripe_fee_minor: int | None
    platform_fee_minor: int | None
    returns_allowance_minor: int | None
    net_minor: int | None
    margin_bps: int | None
    floor_bps: int
    missing: list[str]
    currency: Literal["GBP"]


class EvidenceOut(BaseModel):
    passed: bool
    evidence_url: str
    verified_at: datetime


class SupplierMatchOut(BaseModel):
    provider: str
    external_id: str
    variant_id: str | None
    title: str
    url: str | None
    image_url: str | None
    confidence: float
    unit_cost: MoneyOut
    unit_cost_gbp_minor: int | None
    shipping: ShippingOut | None
    shipping_gbp_minor: int | None
    landed_cost_minor: int | None
    fx: FxOut | None
    delivery_min_days: int | None
    delivery_max_days: int | None
    warehouse_country: str | None
    uk_stock: int | None
    checks: dict[str, Literal["passed", "failed", "not_checked"]]
    evidence: dict[str, EvidenceOut]
    margin: MarginOut
    verified: bool
    arrives_too_late: bool | None
    search_snapshot_id: uuid.UUID
    freight_snapshot_id: uuid.UUID
    fetched_at: datetime


class SupplierResultOut(BaseModel):
    """Supplier matching (FR-11, ADR-0020) for one candidate."""

    status: Literal[
        "matched", "no_verified_supplier", "no_supplier", "not_checked", "not_available"
    ]
    match: SupplierMatchOut | None
    reasons: list[str]
    arrives_too_late: bool | None
    deadline: date | None
    snapshot_ids: list[uuid.UUID]


class CandidateOut(BaseModel):
    keyword: str
    # seed: a starting term for a retail moment; rising: a rising related search;
    # category / audience: from the run form or the audience profile.
    source: Literal["seed", "rising", "category", "audience"]
    category: str
    rising_from: str | None = None
    rising_value: int | None = None
    breakout: bool = False
    trend: TrendOut | None  # None: not available
    market: MarketOut | None = None  # None: not checked or not available
    supplier: SupplierResultOut | None = None  # None: runs from before Phase 2c
    # Phase 2d: demand score, profit forecast and compliance (ADR-0021).
    score: dict[str, Any] | None = None
    profit: dict[str, Any] | None = None
    compliance: dict[str, Any] | None = None
    merged: list[str] = []
    merged_into: str | None = None


class DemandOut(BaseModel):
    """Demand intelligence (FR-12, ADR-0019). Every number links to a source snapshot."""

    as_of: date
    window_end: date
    candidates: list[CandidateOut]
    mock: bool
    stopped_at_cap: bool
    trend_available: bool
    market_available: bool
    supplier_available: bool = False
    supplier_provider: str | None = None
    report_id: uuid.UUID | None = None  # the Top 10 report (Phase 2d)
    ranking: list[str] = []


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mode: RunMode
    status: RunStatus
    country: str
    region: str | None
    params: dict[str, Any]
    window_weeks: int
    as_of: datetime
    season: dict[str, Any] | None
    demand: DemandOut | None = None
    cost_minor: int
    cost_cap_minor: int
    cost_currency: str
    run_refunded: bool
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    steps: list[StepOut] = Field(default_factory=list)


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None


class MomentOut(BaseModel):
    key: str
    name: str
    date: date
    days_away: int
    act_by: dt.date
    act_by_passed: bool
    category_hints: list[str]


class CalendarOut(BaseModel):
    country: str
    as_of: date
    delivery_days: int
    # "assumed" until a supplier is matched (Phase 2c), then "supplier".
    delivery_source: Literal["assumed", "supplier"]
    moments: list[MomentOut]


AgeRange = Literal["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GenderFocus = Literal["any", "women", "men"]
BudgetBand = Literal["budget", "mid", "premium"]


class AudienceIn(BaseModel):
    """Preferences only (FR-12): no names, emails or other personal data."""

    age_range: AgeRange | None = None
    gender_focus: GenderFocus | None = None
    interests: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=10
    )
    budget_band: BudgetBand | None = None
    style: str | None = Field(default=None, max_length=60)

    @field_validator("interests")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in (v.strip() for v in value):
            if item and item.lower() not in (s.lower() for s in seen):
                seen.append(item)
        return seen


class AudienceOut(AudienceIn):
    updated_at: datetime | None = None


async def _run_out(session: Any, run: ResearchRun) -> RunOut:
    steps = (
        await session.scalars(
            select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.position)
        )
    ).all()
    out = RunOut.model_validate(run)
    out.steps = [StepOut.model_validate(s) for s in steps]
    return out


@router.post("/research-runs", status_code=201, response_model=RunOut)
async def create_research_run(
    body: RunCreate,
    background: BackgroundTasks,
    session: DbSession,
    settings: AppSettings,
    flags: Flags,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.RESEARCH_RUN))],
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> RunOut:
    """Start a research run. Spends one research run (402 plan_limit_reached when none are
    left); a run that fails before spending anything on providers gives it back."""
    params: dict[str, Any] = {
        "category": body.category,
        "price_band": body.price_band.model_dump() if body.price_band else None,
        "season_override": body.season_override.value if body.season_override else None,
    }
    run, created = await service.create_run(
        session,
        flags,
        settings,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        mode=body.mode,
        country=body.country,
        region=body.region,
        params=params,
        window_weeks=body.window_weeks,
        cost_cap_minor=body.cost_cap_minor,
        audience_source_ids=body.audience_source_ids,
        use_audience_profile=body.use_audience_profile,
        as_of=body.as_of,
        idempotency_key=idempotency_key,
    )
    if created:
        schedule(
            background,
            settings,
            RUN_JOB,
            {"workspace_id": str(ctx.workspace_id), "run_id": str(run.id)},
        )
    return await _run_out(session, run)


@router.get("/research-runs", response_model=RunPage)
async def list_research_runs(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.REPORTS_READ))],
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> RunPage:
    limit = max(1, min(limit, MAX_LIMIT))
    rows = list(
        (await session.scalars(paginate(select(ResearchRun), ResearchRun.id, cursor, limit))).all()
    )
    items, next_cursor = page(rows, limit)
    return RunPage(items=[RunOut.model_validate(r) for r in items], next_cursor=next_cursor)


async def _get_run(session: Any, run_id: uuid.UUID) -> ResearchRun:
    run = await session.get(ResearchRun, run_id)
    if run is None:  # RLS hides other workspaces' runs
        raise ProblemError(404, "not_found", "Run not found", "No such research run.")
    assert isinstance(run, ResearchRun)
    return run


@router.get("/research-runs/{run_id}", response_model=RunOut)
async def get_research_run(
    run_id: uuid.UUID,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.REPORTS_READ))],
) -> RunOut:
    return await _run_out(session, await _get_run(session, run_id))


@router.get(
    "/research-runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def research_run_events(
    run_id: uuid.UUID,
    session: DbSession,
    redis: AppRedis,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.REPORTS_READ))],
) -> StreamingResponse:
    """Server-Sent Events: a `run` event with the whole run (status, steps, spend) whenever
    it changes, then `end` once it finishes. Clients may poll GET /research-runs/{id}."""
    await _get_run(session, run_id)
    workspace_id = ctx.workspace_id

    async def load() -> RunOut:
        async with get_sessionmaker()() as s, s.begin():
            await set_tenant_context(s, workspace_id=workspace_id)
            return await _run_out(s, await _get_run(s, run_id))

    async def stream() -> AsyncIterator[str]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel(run_id))
        started = time.monotonic()
        last = ""
        try:
            while time.monotonic() - started < STREAM_MAX_SECONDS:
                out = await load()
                data = out.model_dump_json()
                if data != last:
                    yield f"event: run\ndata: {data}\n\n"
                    last = data
                if out.status in TERMINAL_STATUSES:
                    yield "event: end\ndata: {}\n\n"
                    return
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=STREAM_POLL_SECONDS
                )
                if message is None:
                    yield ": keep-alive\n\n"
        finally:
            await pubsub.unsubscribe(channel(run_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/season-calendar", response_model=CalendarOut)
async def get_season_calendar(
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.WORKSPACE_READ))],
    as_of: date | None = None,
) -> CalendarOut:
    """The next three retail moments for the workspace's country, with act-by dates."""
    if as_of is not None and not settings.dev_tools_enabled:
        raise ProblemError(
            422, "as_of_not_allowed", "Date override not allowed", "as_of is a local testing aid."
        )
    workspace = await session.get(Workspace, ctx.workspace_id)
    assert workspace is not None
    data = await service.season_calendar(session, settings, country=workspace.country, on=as_of)
    return CalendarOut.model_validate(data)


@router.get("/audience-profile", response_model=AudienceOut)
async def get_audience_profile(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.REPORTS_READ))],
) -> AudienceOut:
    profile = await session.scalar(
        select(AudienceProfile).where(AudienceProfile.workspace_id == ctx.workspace_id)
    )
    if profile is None:
        return AudienceOut()
    return AudienceOut(**service.audience_json(profile), updated_at=profile.updated_at)


@router.put("/audience-profile", response_model=AudienceOut)
async def put_audience_profile(
    body: AudienceIn,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.RESEARCH_RUN))],
) -> AudienceOut:
    """Save the optional 'describe your audience' preferences used by seasonal runs."""
    profile = await session.scalar(
        select(AudienceProfile).where(AudienceProfile.workspace_id == ctx.workspace_id)
    )
    if profile is None:
        profile = AudienceProfile(workspace_id=ctx.workspace_id, updated_by=ctx.user_id)
        session.add(profile)
    profile.age_range = body.age_range
    profile.gender_focus = body.gender_focus
    profile.interests = body.interests
    profile.budget_band = body.budget_band
    profile.style = body.style
    profile.updated_by = ctx.user_id
    await session.flush()
    await session.refresh(profile)
    return AudienceOut(**service.audience_json(profile), updated_at=profile.updated_at)
