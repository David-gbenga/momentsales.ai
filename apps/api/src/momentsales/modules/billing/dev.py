"""Mock Stripe Billing dev routes (local + PROVIDER_MODE=mock only, like modules/devtools).

Each action builds Stripe-shaped events in the mock adapter, signs them with the platform
webhook secret and pushes them through the real receiver and job, so processing is the same
code as with Stripe test mode. Amounts come from our plan and add-on prices.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import select

from momentsales.adapters.billing.base import BillingError
from momentsales.adapters.billing.mock import MockBillingAdapter
from momentsales.adapters.payments.mock import MockPaymentsAdapter, sign_payload
from momentsales.core.db import set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.core.jobs import schedule
from momentsales.db.billing import (
    AddonProduct,
    BillingInterval,
    Plan,
    PlanPrice,
    Subscription,
)
from momentsales.deps import AppRedis, AppSettings
from momentsales.modules.billing import entitlements as ent
from momentsales.modules.billing import service
from momentsales.modules.payments import service as payments_service
from momentsales.modules.payments.jobs import PROCESS_JOB
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(prefix="/dev", tags=["dev"])


class EventsOut(BaseModel):
    event_ids: list[str]
    redirect_url: str | None = None


def _mock(redis: AppRedis, settings: AppSettings) -> MockBillingAdapter:
    return MockBillingAdapter(redis, settings.web_base_url)


async def _deliver(
    events: list[dict[str, Any]],
    session: DbSession,
    settings: AppSettings,
    redis: AppRedis,
    background: BackgroundTasks,
) -> list[str]:
    verifier = MockPaymentsAdapter(redis, settings.web_base_url)
    secret = settings.stripe_webhook_secret.get_secret_value()  # platform endpoint
    ids: list[str] = []
    for event in events:
        payload = json.dumps(event).encode()
        event_id, is_new = await payments_service.ingest(
            session,
            verifier,
            payload=payload,
            signature=sign_payload(payload, secret),
            secret=secret,
        )
        if is_new:
            schedule(background, settings, PROCESS_JOB, {"event_id": event_id})
        ids.append(event_id)
    return ids


def _wrap(exc: BillingError) -> ProblemError:
    return ProblemError(400, "mock_billing_error", "Mock billing", str(exc))


@router.get("/mock-billing/checkout/{session_id}")
async def checkout_details(
    session_id: str, session: DbSession, redis: AppRedis, settings: AppSettings
) -> dict[str, Any]:
    data = await _mock(redis, settings).session(session_id)
    if data is None:
        raise ProblemError(404, "not_found", "No such mock checkout")
    item = await _describe(session, str(data["price"]))
    return {
        "id": data["id"],
        "mode": data["mode"],
        "status": data["status"],
        "trial_days": data.get("trial_days"),
        "cancel_url": data["cancel_url"],
        **item,
    }


async def _describe(session: DbSession, price_id: str) -> dict[str, Any]:
    found = await service.plan_for_price_id(session, price_id)
    if found is not None:
        plan, price = found
        return {
            "name": plan.name,
            "amount_minor": price.amount_minor,
            "currency": price.currency,
            "interval": price.interval.value,
        }
    for addon in (await session.scalars(select(AddonProduct))).all():
        if price_id in (
            addon.stripe_price_id,
            f"price_mock_addon_{addon.kind.value}_{addon.currency.lower()}",
        ):
            return {
                "name": addon.name,
                "amount_minor": addon.amount_minor,
                "currency": addon.currency,
                "interval": None,
            }
    raise ProblemError(404, "unknown_price", "Unknown mock price")


@router.post("/mock-billing/checkout/{session_id}/pay", response_model=EventsOut)
async def pay_checkout(
    session_id: str,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> EventsOut:
    mock = _mock(redis, settings)
    data = await mock.session(session_id)
    if data is None:
        raise ProblemError(404, "not_found", "No such mock checkout")
    item = await _describe(session, str(data["price"]))
    try:
        events = await mock.complete_checkout(
            session_id,
            interval=item["interval"] or "month",
            amount_minor=int(item["amount_minor"]) * int(data.get("quantity") or 1),
            currency=str(item["currency"]),
        )
    except BillingError as exc:
        raise _wrap(exc) from exc
    ids = await _deliver(events, session, settings, redis, background)
    return EventsOut(event_ids=ids, redirect_url=str(data["success_url"]))


class WorkspaceIn(BaseModel):
    workspace_id: uuid.UUID


async def _subscription(session: DbSession, workspace_id: uuid.UUID) -> Subscription:
    await set_tenant_context(session, platform=True)
    sub = await session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    if sub is None or not sub.stripe_subscription_id:
        raise ProblemError(404, "no_subscription", "No mock subscription for this workspace")
    return sub


@router.post("/mock-billing/fail-payment", response_model=EventsOut)
async def fail_payment(
    body: WorkspaceIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> EventsOut:
    """A renewal charge fails (as a declining test card would): invoice.payment_failed."""
    sub = await _subscription(session, body.workspace_id)
    try:
        events = await _mock(redis, settings).fail_payment(str(sub.stripe_subscription_id))
    except BillingError as exc:
        raise _wrap(exc) from exc
    return EventsOut(event_ids=await _deliver(events, session, settings, redis, background))


@router.post("/mock-billing/pay-invoice", response_model=EventsOut)
async def pay_invoice(
    body: WorkspaceIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> EventsOut:
    sub = await _subscription(session, body.workspace_id)
    try:
        events = await _mock(redis, settings).pay_open_invoice(str(sub.stripe_subscription_id))
    except BillingError as exc:
        raise _wrap(exc) from exc
    return EventsOut(event_ids=await _deliver(events, session, settings, redis, background))


@router.post("/mock-billing/renew", response_model=EventsOut)
async def renew(
    body: WorkspaceIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> EventsOut:
    """Advance to the next billing period (ends a trial; applies scheduled changes)."""
    sub = await _subscription(session, body.workspace_id)
    try:
        events = await _mock(redis, settings).renew(str(sub.stripe_subscription_id))
    except BillingError as exc:
        raise _wrap(exc) from exc
    return EventsOut(event_ids=await _deliver(events, session, settings, redis, background))


@router.get("/mock-billing/portal")
async def portal_details(customer: str, session: DbSession) -> dict[str, Any]:
    await set_tenant_context(session, platform=True)
    sub = await session.scalar(
        select(Subscription).where(Subscription.stripe_customer_id == customer)
    )
    if sub is None:
        raise ProblemError(404, "not_found", "No such mock customer")
    plan = await session.get(Plan, sub.plan_id) if sub.plan_id else None
    plans = [
        {
            "code": c.plan.code,
            "name": c.plan.name,
            "prices": [
                {
                    "interval": p.interval.value,
                    "amount_minor": p.amount_minor,
                    "currency": p.currency,
                }
                for p in c.prices
            ],
        }
        for c in await service.catalog(session)
    ]
    return {
        "workspace_id": str(sub.workspace_id),
        "plan_code": plan.code if plan else None,
        "interval": sub.interval.value if sub.interval else None,
        "status": sub.status.value,
        "plans": plans,
    }


class ChangePlanIn(BaseModel):
    customer: str
    plan_code: str
    interval: BillingInterval = BillingInterval.MONTH


@router.post("/mock-billing/portal/change-plan", response_model=EventsOut)
async def change_plan(
    body: ChangePlanIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> EventsOut:
    """Portal plan switch: upgrades apply now; cheaper plans wait for the period end
    (Portal `schedule_at_period_end`: decreasing_item_amount, shortening_interval)."""
    await set_tenant_context(session, platform=True)
    sub = await session.scalar(
        select(Subscription).where(Subscription.stripe_customer_id == body.customer)
    )
    if sub is None or not sub.stripe_subscription_id or not sub.plan_id or not sub.interval:
        raise ProblemError(404, "no_subscription", "No mock subscription")
    current = await session.scalar(
        select(PlanPrice).where(
            PlanPrice.plan_id == sub.plan_id,
            PlanPrice.interval == sub.interval,
            PlanPrice.currency == sub.currency,
        )
    )
    plan = await session.scalar(select(Plan).where(Plan.code == body.plan_code))
    new = (
        await session.scalar(
            select(PlanPrice).where(
                PlanPrice.plan_id == plan.id,
                PlanPrice.interval == body.interval,
                PlanPrice.currency == sub.currency,
            )
        )
        if plan
        else None
    )
    if plan is None or new is None or current is None:
        raise ProblemError(404, "plan_not_found", "No such plan or price")
    cheaper = service.monthly_amount_minor(new) < service.monthly_amount_minor(current)
    shorter = sub.interval is BillingInterval.YEAR and body.interval is BillingInterval.MONTH
    try:
        events = await _mock(redis, settings).change_price(
            sub.stripe_subscription_id,
            service.price_ref(settings, plan, new),
            at_period_end=cheaper or shorter,
            amount_minor=new.amount_minor,
        )
    except BillingError as exc:
        raise _wrap(exc) from exc
    return EventsOut(event_ids=await _deliver(events, session, settings, redis, background))


class SimulatedRunOut(BaseModel):
    pool: str


@router.post("/billing/simulate-run", response_model=SimulatedRunOut)
async def simulate_run(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.RESEARCH_RUN))],
) -> SimulatedRunOut:
    """Spend one metered research run, as Phase 2's research runs will (AC-15 in mock mode
    before research exists). Real metering: 402 plan_limit_reached when nothing is left."""
    pool = await ent.consume_run(session, ctx.workspace_id, f"dev-run-{uuid.uuid4()}")
    return SimulatedRunOut(pool=pool.value)
