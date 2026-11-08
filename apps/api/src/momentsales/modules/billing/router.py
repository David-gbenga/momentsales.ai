"""SaaS billing endpoints (FR-10, J8). Only the Owner changes billing; Admins see usage."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from momentsales.db.billing import AddonKind, BillingInterval
from momentsales.deps import AppSettings, Registry
from momentsales.modules.billing import service
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["billing"])


class PriceOut(BaseModel):
    currency: str
    interval: BillingInterval
    amount_minor: int


class PlanOut(BaseModel):
    code: str
    name: str
    research_runs: int | None
    live_listings: int | None
    seats: int | None
    annual_bonus_runs: int
    features: dict[str, Any]
    prices: list[PriceOut]


class AddonOut(BaseModel):
    kind: AddonKind
    name: str
    runs: int
    currency: str
    amount_minor: int


class TrialOut(BaseModel):
    days: int
    runs: int
    plan_code: str


class CatalogOut(BaseModel):
    plans: list[PlanOut]
    addons: list[AddonOut]
    trial: TrialOut


@router.get("/plans", response_model=CatalogOut)
async def list_plans(session: DbSession, settings: AppSettings) -> CatalogOut:
    """Public: plans, limits and prices for the pricing page (from the plans table)."""
    plans = await service.catalog(session)
    return CatalogOut(
        plans=[
            PlanOut(
                code=c.plan.code,
                name=c.plan.name,
                research_runs=c.plan.research_runs,
                live_listings=c.plan.live_listings,
                seats=c.plan.seats,
                annual_bonus_runs=c.plan.annual_bonus_runs,
                features=c.plan.features,
                prices=[
                    PriceOut(currency=p.currency, interval=p.interval, amount_minor=p.amount_minor)
                    for p in c.prices
                ],
            )
            for c in plans
        ],
        addons=[
            AddonOut(
                kind=a.kind,
                name=a.name,
                runs=a.runs,
                currency=a.currency,
                amount_minor=a.amount_minor,
            )
            for a in await service.addons(session)
        ],
        trial=TrialOut(
            days=settings.trial_days, runs=settings.trial_runs, plan_code=settings.trial_plan_code
        ),
    )


class CheckoutIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=30)
    interval: BillingInterval = BillingInterval.MONTH
    currency: str = Field(default="GBP", pattern="^[A-Z]{3}$")


class AddonCheckoutIn(BaseModel):
    kind: AddonKind = AddonKind.RUN_PACK


class UrlOut(BaseModel):
    url: str


@router.post("/billing/checkout", response_model=UrlOut)
async def checkout(
    body: CheckoutIn,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> UrlOut:
    """Start the 14-day trial (first time) or a subscription: a Stripe Checkout URL."""
    url = await service.start_subscription_checkout(
        session,
        await registry.billing(session),
        settings,
        workspace_id=ctx.workspace_id,
        owner=ctx.principal.user,
        plan_code=body.plan_code,
        interval=body.interval,
        currency=body.currency,
    )
    return UrlOut(url=url)


@router.post("/billing/packs/checkout", response_model=UrlOut)
async def pack_checkout(
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> UrlOut:
    url = await service.start_addon_checkout(
        session,
        await registry.billing(session),
        settings,
        workspace_id=ctx.workspace_id,
        owner=ctx.principal.user,
        kind=AddonKind.RUN_PACK,
    )
    return UrlOut(url=url)


@router.post("/billing/report-checkout", response_model=UrlOut)
async def report_checkout(
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> UrlOut:
    """Pay-per-report: one research run, no subscription needed."""
    url = await service.start_addon_checkout(
        session,
        await registry.billing(session),
        settings,
        workspace_id=ctx.workspace_id,
        owner=ctx.principal.user,
        kind=AddonKind.SINGLE_REPORT,
    )
    return UrlOut(url=url)


@router.post("/billing/portal", response_model=UrlOut)
async def portal(
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> UrlOut:
    """Stripe Customer Portal: change plan, card, tax details; download invoices."""
    url = await service.portal_url(
        session, await registry.billing(session), settings, ctx.workspace_id
    )
    return UrlOut(url=url)


class PlanSummary(BaseModel):
    code: str
    name: str


class SubscriptionOut(BaseModel):
    status: str  # none | incomplete | trialing | active | past_due | suspended | paused | canceled
    plan: PlanSummary | None = None
    interval: BillingInterval | None = None
    trial_end: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    pause_until: datetime | None = None
    past_due_since: datetime | None = None
    trial_used: bool = False
    has_billing_account: bool = False


@router.get("/billing/subscription", response_model=SubscriptionOut)
async def get_subscription(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.WORKSPACE_READ))],
) -> SubscriptionOut:
    """Every member sees the plan and status (the app uses it for banners and the gate)."""
    from momentsales.modules.billing import entitlements as ent

    state = await ent.plan_state(session, ctx.workspace_id)
    sub = state.subscription
    if sub is None:
        return SubscriptionOut(status="none")
    plan = None
    if sub.plan_id:
        from momentsales.db.billing import Plan

        p = await session.get(Plan, sub.plan_id)
        plan = PlanSummary(code=p.code, name=p.name) if p else None
    return SubscriptionOut(
        status=sub.status.value,
        plan=plan,
        interval=sub.interval,
        trial_end=sub.trial_end,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        pause_until=sub.pause_until,
        past_due_since=sub.past_due_since,
        trial_used=sub.trial_used,
        has_billing_account=bool(sub.stripe_customer_id),
    )


class RunsOut(BaseModel):
    used: int
    allowed: int | None
    pack_remaining: int
    single_remaining: int
    period_start: datetime
    period_end: datetime


class LimitOut(BaseModel):
    used: int
    allowed: int | None


class UsageOut(BaseModel):
    research_runs: RunsOut
    seats: LimitOut
    live_listings: LimitOut


@router.get("/billing/usage", response_model=UsageOut)
async def get_usage(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_USAGE_READ))],
) -> UsageOut:
    u = await service.usage_summary(session, ctx.workspace_id)
    return UsageOut(
        research_runs=RunsOut(
            used=u.runs.plan_used,
            allowed=u.runs.plan_allowed,
            pack_remaining=u.runs.pack_runs,
            single_remaining=u.runs.single_runs,
            period_start=u.runs.period_start,
            period_end=u.runs.period_end,
        ),
        seats=LimitOut(used=u.seats_used, allowed=u.seats_allowed),
        live_listings=LimitOut(used=u.listings_used, allowed=u.listings_allowed),
    )


class InvoiceOut(BaseModel):
    id: str
    number: str | None
    status: str
    total_minor: int
    tax_minor: int
    currency: str
    hosted_invoice_url: str | None
    pdf_url: str | None
    issued_at: datetime | None


@router.get("/billing/invoices", response_model=list[InvoiceOut])
async def list_invoices(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_INVOICES_READ))],
) -> list[InvoiceOut]:
    return [
        InvoiceOut(
            id=i.stripe_invoice_id,
            number=i.number,
            status=i.status,
            total_minor=i.total_minor,
            tax_minor=i.tax_minor,
            currency=i.currency,
            hosted_invoice_url=i.hosted_invoice_url,
            pdf_url=i.pdf_url,
            issued_at=i.issued_at,
        )
        for i in await service.invoices(session, ctx.workspace_id)
    ]


class PauseIn(BaseModel):
    months: int = Field(ge=1, le=3)


@router.post("/billing/pause", response_model=SubscriptionOut)
async def pause(
    body: PauseIn,
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> SubscriptionOut:
    """Pause for 1 to 3 months instead of cancelling: research off, data kept."""
    await service.pause(
        session,
        await registry.billing(session),
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        months=body.months,
    )
    return await get_subscription(session, ctx)


@router.post("/billing/resume", response_model=SubscriptionOut)
async def resume(
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> SubscriptionOut:
    await service.resume(
        session,
        await registry.billing(session),
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
    )
    return await get_subscription(session, ctx)


class CancelSurveyIn(BaseModel):
    reason: Literal["no_sales", "too_expensive", "missing_feature", "seasonal_break", "other"]
    text: str | None = Field(default=None, max_length=1000)
    # Omit to preview the retention offer; then send `cancel` or `accept_offer`.
    decision: Literal["cancel", "accept_offer"] | None = None


class CancelSurveyOut(BaseModel):
    offer: Literal["pause", "discount", "none"]
    outcome: str | None
    subscription: SubscriptionOut


@router.post("/billing/cancel-survey", response_model=CancelSurveyOut)
async def cancel_survey(
    body: CancelSurveyIn,
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> CancelSurveyOut:
    result = await service.cancel_survey(
        session,
        await registry.billing(session),
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        reason=body.reason,
        text=body.text,
        decision=body.decision,
    )
    return CancelSurveyOut(
        offer=result.offer,
        outcome=result.outcome,
        subscription=await get_subscription(session, ctx),
    )


@router.post("/billing/keep", response_model=SubscriptionOut)
async def keep(
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.BILLING_MANAGE))],
) -> SubscriptionOut:
    """Undo a cancellation that is scheduled for the end of the period."""
    await service.keep_subscription(
        session,
        await registry.billing(session),
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
    )
    return await get_subscription(session, ctx)
