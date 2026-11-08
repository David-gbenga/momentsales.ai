"""Billing sweep (ADR-0016), run hourly by Celery Beat; every step is idempotent.

- Trial reminders on trial day 10 and 13 (PRD), each sent once, stating the price and the
  date of the first charge (UK subscription rules: clear renewal terms before charging).
- past_due for SUSPEND_AFTER_DAYS (30) -> suspended.
- Paused subscriptions past `pause_until` -> resumed in Stripe and locally.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.billing.base import BillingError
from momentsales.core.config import Settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.logging import get_logger
from momentsales.core.runtime import runtime
from momentsales.db.billing import Plan, PlanPrice, Subscription, SubscriptionStatus
from momentsales.modules.billing import service
from momentsales.modules.billing.webhooks import _email_owner

log = get_logger(__name__)

REMINDER_DAYS = (10, 13)  # PRD: trial reminder emails at day 10 and day 13


def _money(minor: int, currency: str) -> str:
    symbol = {"GBP": "£", "EUR": "€", "USD": "$", "AUD": "A$"}.get(currency, f"{currency} ")
    return f"{symbol}{minor / 100:,.2f}"


async def _reminder(
    session: AsyncSession,
    settings: Settings,
    email: Any,
    sub: Subscription,
    day: int,
) -> None:
    plan = await session.get(Plan, sub.plan_id) if sub.plan_id else None
    price = (
        await session.scalar(
            select(PlanPrice).where(
                PlanPrice.plan_id == sub.plan_id,
                PlanPrice.interval == sub.interval,
                PlanPrice.currency == sub.currency,
            )
        )
        if sub.plan_id and sub.interval
        else None
    )
    assert sub.trial_end is not None
    every = sub.interval.value if sub.interval else "month"
    charge = (
        f"{_money(price.amount_minor, price.currency)} per {every}" if price else "the plan price"
    )

    plan_name = plan.name if plan else "your plan"
    text = (
        f"Your momentsales.ai free trial ends on {sub.trial_end:%d %B %Y}. "
        f"On that date we'll charge {charge} for {plan_name}, and then every "
        f"{every} until you cancel. "
        f"You can change plan, pause or cancel any time from the Billing page: "
        f"{settings.web_base_url.rstrip('/')}/app/billing"
    )
    await _email_owner(
        session,
        email,
        settings,
        sub.workspace_id,
        subject=f"Your free trial ends on {sub.trial_end:%d %B}",
        text=text,
        key=f"trial-reminder-{day}-{sub.id}",
    )


async def sweep(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts = {"reminders": 0, "suspended": 0, "resumed": 0}
    async with runtime() as rt:
        settings = rt.settings
        async with get_sessionmaker()() as session, session.begin():
            await set_tenant_context(session, platform=True)
            rows = (
                await session.execute(
                    select(Subscription.workspace_id, Subscription.status).where(
                        Subscription.status.in_(
                            [
                                SubscriptionStatus.TRIALING,
                                SubscriptionStatus.PAST_DUE,
                                SubscriptionStatus.PAUSED,
                            ]
                        )
                    )
                )
            ).all()
        for workspace_id, _status in rows:
            try:
                await _sweep_one(rt, settings, workspace_id, now, counts)
            except Exception as exc:  # one workspace never blocks the others
                log.error("billing_sweep_failed", workspace_id=str(workspace_id), error=str(exc))
    if any(counts.values()):
        log.info("billing_sweep", **counts)
    return counts


async def _sweep_one(
    rt: Any, settings: Settings, workspace_id: uuid.UUID, now: datetime, counts: dict[str, int]
) -> None:
    async with get_sessionmaker()() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        sub = await session.scalar(
            select(Subscription).where(Subscription.workspace_id == workspace_id).with_for_update()
        )
        if sub is None:
            return
        if sub.status is SubscriptionStatus.TRIALING and sub.trial_end:
            sent = set(sub.trial_reminders_sent or [])
            for day in REMINDER_DAYS:
                due = sub.trial_end - timedelta(days=settings.trial_days - day)
                if day not in sent and now >= due and now < sub.trial_end:
                    await _reminder(session, settings, await rt.registry.email(session), sub, day)
                    sent.add(day)
                    counts["reminders"] += 1
            sub.trial_reminders_sent = sorted(sent)
        elif sub.status is SubscriptionStatus.PAST_DUE and sub.past_due_since:
            if now - sub.past_due_since >= timedelta(days=settings.suspend_after_days):
                sub.status = SubscriptionStatus.SUSPENDED
                counts["suspended"] += 1
                await _email_owner(
                    session,
                    await rt.registry.email(session),
                    settings,
                    workspace_id,
                    subject="Your momentsales.ai workspace is suspended",
                    text=(
                        f"Your subscription has been unpaid for {settings.suspend_after_days} "
                        "days, so your storefront is paused. Your data is kept. Pay the open "
                        "invoice from the Billing page to restore access: "
                        f"{settings.web_base_url.rstrip('/')}/app/billing"
                    ),
                    key=f"suspended-{sub.id}-{sub.past_due_since:%Y%m%d}",
                )
        elif sub.status is SubscriptionStatus.PAUSED and sub.pause_until and now >= sub.pause_until:
            try:
                await service.resume(
                    session,
                    await rt.registry.billing(session),
                    workspace_id=workspace_id,
                    actor_user_id=None,
                )
                counts["resumed"] += 1
            except BillingError as exc:
                log.error("billing_resume_failed", workspace_id=str(workspace_id), error=str(exc))
