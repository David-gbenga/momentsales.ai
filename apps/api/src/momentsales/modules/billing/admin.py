"""Platform-admin billing API (FR-10, J9; console UI comes in Phase 4).

Reads run in platform context, which RLS limits to billing metadata (subscriptions, usage
counters, purchases, surveys, workspace names and members). Writes switch to the target
workspace's own context and are logged as a /touch and in the audit log.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from momentsales.core.config import Settings
from momentsales.core.db import set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.db.billing import (
    BillingInterval,
    Plan,
    PlanPrice,
    Subscription,
    SubscriptionStatus,
    TrialExtension,
    UsageCounter,
)
from momentsales.db.models import Membership, MembershipStatus, TouchCategory, Workspace
from momentsales.deps import AppSettings, Registry
from momentsales.modules.audit import service as audit
from momentsales.modules.billing import service
from momentsales.modules.touches.service import record_auto_touch
from momentsales.tenancy.deps import DbSession, Principal, require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])
PlatformAdmin = Annotated[Principal, Depends(require_platform_admin)]


class TenantOut(BaseModel):
    workspace_id: uuid.UUID
    name: str
    plan_code: str | None
    status: str
    interval: BillingInterval | None
    trial_end: datetime | None
    current_period_end: datetime | None
    research_runs_used: int
    seats_used: int
    mrr_minor: int
    currency: str


class TenantsOut(BaseModel):
    tenants: list[TenantOut]
    mrr_minor: dict[str, int]  # per currency: sum of active subscriptions


@router.get("/tenants", response_model=TenantsOut)
async def list_tenants(session: DbSession, admin: PlatformAdmin) -> TenantsOut:
    await set_tenant_context(session, user_id=admin.user_id, platform=True)
    workspaces = list(
        (await session.scalars(select(Workspace).order_by(Workspace.created_at.desc()))).all()
    )
    subs = {s.workspace_id: s for s in (await session.scalars(select(Subscription))).all()}
    plans = {p.id: p for p in (await session.scalars(select(Plan))).all()}
    prices = list((await session.scalars(select(PlanPrice))).all())
    seats = dict(
        (
            await session.execute(
                select(Membership.workspace_id, func.count())
                .where(Membership.status == MembershipStatus.ACTIVE)
                .group_by(Membership.workspace_id)
            )
        ).all()
    )
    tenants: list[TenantOut] = []
    totals: dict[str, int] = {}
    for ws in workspaces:
        sub = subs.get(ws.id)
        runs = 0
        mrr = 0
        currency = sub.currency if sub else "GBP"
        if sub and sub.current_period_start:
            runs = int(
                await session.scalar(
                    select(UsageCounter.research_runs_used).where(
                        UsageCounter.workspace_id == ws.id,
                        UsageCounter.period_start == sub.current_period_start,
                    )
                )
                or 0
            )
        if sub and sub.status is SubscriptionStatus.ACTIVE and sub.plan_id and sub.interval:
            price = next(
                (
                    p
                    for p in prices
                    if p.plan_id == sub.plan_id
                    and p.interval is sub.interval
                    and p.currency == sub.currency
                ),
                None,
            )
            if price is not None:
                mrr = service.monthly_amount_minor(price)
                totals[currency] = totals.get(currency, 0) + mrr
        plan = plans.get(sub.plan_id) if sub and sub.plan_id else None
        tenants.append(
            TenantOut(
                workspace_id=ws.id,
                name=ws.name,
                plan_code=plan.code if plan else None,
                status=sub.status.value if sub else "none",
                interval=sub.interval if sub else None,
                trial_end=sub.trial_end if sub else None,
                current_period_end=sub.current_period_end if sub else None,
                research_runs_used=runs,
                seats_used=int(seats.get(ws.id, 0)),
                mrr_minor=mrr,
                currency=currency,
            )
        )
    return TenantsOut(tenants=tenants, mrr_minor=totals)


class AdminPriceOut(BaseModel):
    currency: str
    interval: BillingInterval
    amount_minor: int
    stripe_price_id: str | None
    active: bool


class AdminPlanOut(BaseModel):
    code: str
    name: str
    active: bool
    research_runs: int | None
    live_listings: int | None
    seats: int | None
    annual_bonus_runs: int
    platform_fee_bps: int
    features: dict[str, Any]
    prices: list[AdminPriceOut]
    warnings: list[str]


def unit_economics_warnings(settings: Settings, plan: Plan, prices: list[PlanPrice]) -> list[str]:
    """FR-10: monthly price must exceed (included runs x run cost cap) x 3. Prices in a
    different currency from the cap are not compared (no exchange rate is assumed)."""
    warnings: list[str] = []
    runs = plan.research_runs
    for price in prices:
        if price.interval is not BillingInterval.MONTH or not price.active:
            continue
        if runs is None:
            warnings.append(f"{price.currency}: unlimited runs, so the guard can't be checked")
            continue
        if price.currency != settings.run_cost_cap_currency:
            warnings.append(
                f"{price.currency}: not checked: the run cost cap is in "
                f"{settings.run_cost_cap_currency} (set RUN_COST_CAP_CURRENCY to compare)"
            )
            continue
        floor = runs * settings.run_cost_cap_minor * 3
        if price.amount_minor <= floor:
            warnings.append(
                f"{price.currency}: monthly price {price.amount_minor} must exceed {floor} "
                f"({runs} runs x cap {settings.run_cost_cap_minor} x 3)"
            )
    return warnings


def _admin_plan(settings: Settings, c: service.CatalogPlan) -> AdminPlanOut:
    return AdminPlanOut(
        code=c.plan.code,
        name=c.plan.name,
        active=c.plan.active,
        research_runs=c.plan.research_runs,
        live_listings=c.plan.live_listings,
        seats=c.plan.seats,
        annual_bonus_runs=c.plan.annual_bonus_runs,
        platform_fee_bps=c.plan.platform_fee_bps,
        features=c.plan.features,
        prices=[
            AdminPriceOut(
                currency=p.currency,
                interval=p.interval,
                amount_minor=p.amount_minor,
                stripe_price_id=p.stripe_price_id,
                active=p.active,
            )
            for p in c.prices
        ],
        warnings=unit_economics_warnings(settings, c.plan, c.prices),
    )


@router.get("/plans", response_model=list[AdminPlanOut])
async def admin_plans(
    session: DbSession, settings: AppSettings, admin: PlatformAdmin
) -> list[AdminPlanOut]:
    return [_admin_plan(settings, c) for c in await service.catalog(session, active_only=False)]


class PriceIn(BaseModel):
    currency: str = Field(pattern="^[A-Z]{3}$")
    interval: BillingInterval
    amount_minor: int = Field(gt=0)


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    active: bool | None = None
    research_runs: int | None = Field(default=None, ge=0)
    live_listings: int | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=1)
    unlimited: list[str] | None = None  # e.g. ["live_listings"] to set a limit to unlimited
    annual_bonus_runs: int | None = Field(default=None, ge=0)
    platform_fee_bps: int | None = Field(default=None, ge=0, le=5000)
    features: dict[str, Any] | None = None
    prices: list[PriceIn] | None = None


@router.patch("/plans/{code}", response_model=AdminPlanOut)
async def update_plan(
    code: str, body: PlanPatch, session: DbSession, settings: AppSettings, admin: PlatformAdmin
) -> AdminPlanOut:
    """Edit limits, features, fee and prices without a code change. A changed price drops
    its Stripe price id: run the billing seed to create the new Stripe Price."""
    await set_tenant_context(session, user_id=admin.user_id, platform=True)
    plan = await session.scalar(select(Plan).where(Plan.code == code))
    if plan is None:
        raise ProblemError(404, "plan_not_found", "No such plan")
    before = {
        "research_runs": plan.research_runs,
        "live_listings": plan.live_listings,
        "seats": plan.seats,
        "platform_fee_bps": plan.platform_fee_bps,
    }
    data = body.model_dump(exclude_unset=True, exclude={"prices", "unlimited"})
    for key, value in data.items():
        setattr(plan, key, value)
    for key in body.unlimited or []:
        if key not in ("research_runs", "live_listings", "seats"):
            raise ProblemError(422, "invalid_limit", f"{key} can't be unlimited")
        setattr(plan, key, None)
    for p in body.prices or []:
        price = await session.scalar(
            select(PlanPrice).where(
                PlanPrice.plan_id == plan.id,
                PlanPrice.currency == p.currency,
                PlanPrice.interval == p.interval,
            )
        )
        if price is None:
            session.add(
                PlanPrice(
                    plan_id=plan.id,
                    currency=p.currency,
                    interval=p.interval,
                    amount_minor=p.amount_minor,
                    active=True,
                )
            )
        elif price.amount_minor != p.amount_minor:
            price.amount_minor, price.stripe_price_id = p.amount_minor, None
    await session.flush()
    await audit.record(
        session,
        action="plan.updated",
        object_type="plan",
        object_id=plan.id,
        workspace_id=None,
        actor_user_id=admin.user_id,
        before=before,
        after=body.model_dump(mode="json", exclude_unset=True),
    )
    catalog = await service.catalog(session, active_only=False)
    return _admin_plan(settings, next(c for c in catalog if c.plan.id == plan.id))


class TrialExtensionIn(BaseModel):
    days: int = Field(ge=1, le=60)
    reason: str = Field(min_length=3, max_length=500)


class TrialExtensionOut(BaseModel):
    trial_end: datetime


@router.post("/tenants/{workspace_id}/trial-extension", response_model=TrialExtensionOut)
async def extend_trial(
    workspace_id: uuid.UUID,
    body: TrialExtensionIn,
    session: DbSession,
    registry: Registry,
    admin: PlatformAdmin,
) -> TrialExtensionOut:
    await set_tenant_context(session, user_id=admin.user_id, workspace_id=workspace_id)
    sub = await service.account(session, workspace_id)
    if sub is None or sub.status is not SubscriptionStatus.TRIALING or not sub.trial_end:
        raise ProblemError(409, "not_trialing", "Only a trialing workspace can be extended")
    new_end = sub.trial_end + timedelta(days=body.days)
    if sub.stripe_subscription_id:
        from momentsales.adapters.billing.base import BillingError

        try:
            await (await registry.billing(session)).set_trial_end(
                sub.stripe_subscription_id, new_end
            )
        except BillingError as exc:
            raise ProblemError(503, "billing_unavailable", "Stripe didn't accept that") from exc
    sub.trial_end = new_end
    if sub.current_period_end and sub.current_period_end < new_end:
        sub.current_period_end = new_end
    session.add(
        TrialExtension(
            workspace_id=workspace_id, days=body.days, reason=body.reason, applied_by=admin.user_id
        )
    )
    await record_auto_touch(
        session,
        workspace_id=workspace_id,
        actor_user_id=admin.user_id,
        category=TouchCategory.BILLING,
        description=f"Platform admin extended the trial by {body.days} days: {body.reason}",
        object_type="subscription",
        object_id=sub.id,
    )
    await audit.record(
        session,
        action="trial.extended",
        object_type="subscription",
        object_id=sub.id,
        workspace_id=workspace_id,
        actor_user_id=admin.user_id,
        after={"days": body.days, "trial_end": new_end.isoformat(), "reason": body.reason},
    )
    return TrialExtensionOut(trial_end=new_end.astimezone(UTC))
