"""SaaS billing (FR-10): catalogue, checkout, Portal, packs, pause, cancel survey, summaries.

Subscription state is driven by verified webhooks (modules/billing/webhooks.py). Actions the
Owner takes here are also recorded locally straight away, so the app reflects them before
Stripe's event arrives; the webhook then re-applies the same state (idempotent).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.billing.base import (
    BillingError,
    BillingPort,
    PaymentCheckout,
    SubscriptionCheckout,
    mock_addon_price_id,
    mock_price_id,
)
from momentsales.adapters.billing.stripe import RETENTION_COUPON_ID
from momentsales.core.config import Settings
from momentsales.core.errors import ProblemError
from momentsales.db.billing import (
    AddonKind,
    AddonProduct,
    AddonPurchase,
    BillingInterval,
    CancelSurvey,
    Plan,
    PlanPrice,
    PurchaseStatus,
    SaasInvoice,
    Subscription,
    SubscriptionPause,
    SubscriptionStatus,
)
from momentsales.db.models import TouchCategory, User, Workspace
from momentsales.modules.audit import service as audit
from momentsales.modules.billing import entitlements as ent
from momentsales.modules.touches.service import record_auto_touch

CANCEL_REASONS = ("no_sales", "too_expensive", "missing_feature", "seasonal_break", "other")
# PRD FR-10: offer a pause or a one-month discount depending on the reason.
OFFER_FOR_REASON = {
    "no_sales": "pause",
    "seasonal_break": "pause",
    "too_expensive": "discount",
    "missing_feature": "none",
    "other": "none",
}


def _billing_error(exc: BillingError) -> ProblemError:
    return ProblemError(
        503 if exc.retryable else 400,
        "billing_unavailable" if exc.retryable else "billing_error",
        "Billing problem",
        "Stripe couldn't complete that just now. Please try again in a minute."
        if exc.retryable
        else str(exc),
    )


def _web(settings: Settings, path: str) -> str:
    return f"{settings.web_base_url.rstrip('/')}{path}"


# --- Catalogue ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogPlan:
    plan: Plan
    prices: list[PlanPrice]


async def catalog(session: AsyncSession, *, active_only: bool = True) -> list[CatalogPlan]:
    query = select(Plan).order_by(Plan.sort_order)
    if active_only:
        query = query.where(Plan.active.is_(True))
    plans = list((await session.scalars(query)).all())
    prices = list(
        (
            await session.scalars(
                select(PlanPrice).where(PlanPrice.plan_id.in_([p.id for p in plans]))
            )
        ).all()
    )
    return [
        CatalogPlan(
            p,
            sorted(
                (x for x in prices if x.plan_id == p.id and (x.active or not active_only)),
                key=lambda x: (x.currency, x.interval.value),
            ),
        )
        for p in plans
    ]


async def addons(session: AsyncSession) -> list[AddonProduct]:
    return list(
        (
            await session.scalars(
                select(AddonProduct)
                .where(AddonProduct.active.is_(True))
                .order_by(AddonProduct.runs)
            )
        ).all()
    )


def price_ref(settings: Settings, plan: Plan, price: PlanPrice) -> str:
    if price.stripe_price_id:
        return price.stripe_price_id
    if settings.provider_mode.value == "mock":
        return mock_price_id(plan.code, price.currency, price.interval.value)
    raise ProblemError(
        503,
        "billing_not_configured",
        "Billing isn't set up",
        "Stripe prices are missing. Run the billing seed (tools/stripe_billing_seed.py).",
    )


def addon_price_ref(settings: Settings, addon: AddonProduct) -> str:
    if addon.stripe_price_id:
        return addon.stripe_price_id
    if settings.provider_mode.value == "mock":
        return mock_addon_price_id(addon.kind.value, addon.currency)
    raise ProblemError(
        503,
        "billing_not_configured",
        "Billing isn't set up",
        "Stripe prices are missing. Run the billing seed (tools/stripe_billing_seed.py).",
    )


async def plan_for_price_id(session: AsyncSession, price_id: str) -> tuple[Plan, PlanPrice] | None:
    row = (
        await session.execute(
            select(Plan, PlanPrice)
            .join(PlanPrice, PlanPrice.plan_id == Plan.id)
            .where(PlanPrice.stripe_price_id == price_id)
        )
    ).first()
    if row is not None:
        return row[0], row[1]
    if price_id.startswith("price_mock_") and not price_id.startswith("price_mock_addon_"):
        code, currency, interval = price_id.removeprefix("price_mock_").rsplit("_", 2)
        row = (
            await session.execute(
                select(Plan, PlanPrice)
                .join(PlanPrice, PlanPrice.plan_id == Plan.id)
                .where(
                    Plan.code == code,
                    PlanPrice.currency == currency.upper(),
                    PlanPrice.interval == BillingInterval(interval),
                )
            )
        ).first()
        if row is not None:
            return row[0], row[1]
    return None


# --- Billing account ---------------------------------------------------------------------


async def account(session: AsyncSession, workspace_id: uuid.UUID) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )


async def _ensure_account(
    session: AsyncSession,
    billing: BillingPort,
    workspace_id: uuid.UUID,
    owner: User,
) -> Subscription:
    sub = await account(session, workspace_id)
    if sub is None:
        sub = Subscription(
            workspace_id=workspace_id,
            currency="GBP",
            status=SubscriptionStatus.INCOMPLETE,
            trial_used=False,
            cancel_at_period_end=False,
            trial_reminders_sent=[],
        )
        session.add(sub)
        await session.flush()
    if not sub.stripe_customer_id:
        workspace = await session.get(Workspace, workspace_id)
        try:
            sub.stripe_customer_id = await billing.create_customer(
                workspace_id=str(workspace_id),
                email=owner.email,
                name=(workspace.legal_name or workspace.name) if workspace else owner.email,
            )
        except BillingError as exc:
            raise _billing_error(exc) from exc
    return sub


# --- Checkout ----------------------------------------------------------------------------


async def start_subscription_checkout(
    session: AsyncSession,
    billing: BillingPort,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    owner: User,
    plan_code: str,
    interval: BillingInterval,
    currency: str,
) -> str:
    plan = await session.scalar(select(Plan).where(Plan.code == plan_code, Plan.active.is_(True)))
    if plan is None:
        raise ProblemError(404, "plan_not_found", "No such plan")
    price = await session.scalar(
        select(PlanPrice).where(
            PlanPrice.plan_id == plan.id,
            PlanPrice.currency == currency.upper(),
            PlanPrice.interval == interval,
            PlanPrice.active.is_(True),
        )
    )
    if price is None:
        raise ProblemError(
            422,
            "price_not_available",
            "Price not available",
            "That plan isn't sold in this currency.",
        )
    sub = await _ensure_account(session, billing, workspace_id, owner)
    if sub.status in (
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.SUSPENDED,
        SubscriptionStatus.PAUSED,
    ):
        raise ProblemError(
            409,
            "already_subscribed",
            "Already subscribed",
            "Change plans from Manage billing.",
        )
    assert sub.stripe_customer_id is not None
    try:
        created = await billing.subscription_checkout(
            SubscriptionCheckout(
                customer_id=sub.stripe_customer_id,
                price_id=price_ref(settings, plan, price),
                workspace_id=str(workspace_id),
                trial_days=None if sub.trial_used else settings.trial_days,
                success_url=_web(settings, "/app/billing?checkout=success"),
                cancel_url=_web(settings, "/app/billing?checkout=cancelled"),
                automatic_tax=settings.stripe_tax_enabled,
                idempotency_key=f"subscription-{workspace_id}-{uuid.uuid4()}",
            )
        )
    except BillingError as exc:
        raise _billing_error(exc) from exc
    return created.url


async def start_addon_checkout(
    session: AsyncSession,
    billing: BillingPort,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    owner: User,
    kind: AddonKind,
    currency: str = "GBP",
) -> str:
    addon = await session.scalar(
        select(AddonProduct).where(
            AddonProduct.kind == kind,
            AddonProduct.currency == currency.upper(),
            AddonProduct.active.is_(True),
        )
    )
    if addon is None:
        raise ProblemError(404, "addon_not_found", "Not available")
    if kind is AddonKind.RUN_PACK:
        state = await ent.plan_state(session, workspace_id)
        if state.status not in ent.FULL_ACCESS:
            raise ProblemError(
                409,
                "subscription_required",
                "Run packs need a plan",
                "Run packs are for active subscriptions. Buy a single report instead.",
            )
    sub = await _ensure_account(session, billing, workspace_id, owner)
    assert sub.stripe_customer_id is not None
    purchase = AddonPurchase(
        workspace_id=workspace_id,
        addon_id=addon.id,
        kind=addon.kind,
        quantity=1,
        runs=addon.runs,
        amount_minor=addon.amount_minor,
        currency=addon.currency,
        status=PurchaseStatus.OPEN,
        created_by=owner.id,
    )
    session.add(purchase)
    await session.flush()
    try:
        created = await billing.payment_checkout(
            PaymentCheckout(
                customer_id=sub.stripe_customer_id,
                price_id=addon_price_ref(settings, addon),
                quantity=1,
                workspace_id=str(workspace_id),
                metadata={"kind": "addon", "purchase_id": str(purchase.id)},
                success_url=_web(settings, "/app/billing?purchase=success"),
                cancel_url=_web(settings, "/app/billing?purchase=cancelled"),
                automatic_tax=settings.stripe_tax_enabled,
                idempotency_key=f"addon-{purchase.id}",
            )
        )
    except BillingError as exc:
        raise _billing_error(exc) from exc
    purchase.stripe_checkout_session_id = created.session_id
    return created.url


async def portal_url(
    session: AsyncSession, billing: BillingPort, settings: Settings, workspace_id: uuid.UUID
) -> str:
    sub = await account(session, workspace_id)
    if sub is None or not sub.stripe_customer_id:
        raise ProblemError(
            409, "no_billing_account", "No billing account yet", "Choose a plan first."
        )
    try:
        return await billing.portal_session(
            customer_id=sub.stripe_customer_id,
            return_url=_web(settings, "/app/billing"),
            configuration_id=settings.stripe_portal_configuration_id,
        )
    except BillingError as exc:
        raise _billing_error(exc) from exc


# --- Pause, cancel, keep -----------------------------------------------------------------


async def _active_subscription(session: AsyncSession, workspace_id: uuid.UUID) -> Subscription:
    sub = await account(session, workspace_id)
    if sub is None or not sub.stripe_subscription_id:
        raise ProblemError(409, "no_subscription", "No subscription", "Choose a plan first.")
    return sub


async def _log(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor: uuid.UUID,
    action: str,
    description: str,
    after: dict[str, object],
    object_id: uuid.UUID,
) -> None:
    await record_auto_touch(
        session,
        workspace_id=workspace_id,
        actor_user_id=actor,
        category=TouchCategory.BILLING,
        description=description,
        object_type="subscription",
        object_id=object_id,
    )
    await audit.record(
        session,
        action=action,
        object_type="subscription",
        object_id=object_id,
        workspace_id=workspace_id,
        actor_user_id=actor,
        after=after,
    )


async def pause(
    session: AsyncSession,
    billing: BillingPort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    months: int,
) -> Subscription:
    if not 1 <= months <= 3:
        raise ProblemError(422, "invalid_pause", "Pause for 1 to 3 months")
    sub = await _active_subscription(session, workspace_id)
    if sub.status is not SubscriptionStatus.ACTIVE:
        raise ProblemError(
            409,
            "cannot_pause",
            "Can't pause now",
            "Only an active, paid subscription can be paused.",
        )
    assert sub.stripe_subscription_id is not None
    try:
        await billing.pause(sub.stripe_subscription_id)
    except BillingError as exc:
        raise _billing_error(exc) from exc
    now = datetime.now(UTC)
    until = now + timedelta(days=30 * months)
    sub.status, sub.pause_until = SubscriptionStatus.PAUSED, until
    session.add(
        SubscriptionPause(
            workspace_id=workspace_id,
            months=months,
            starts_at=now,
            resumes_at=until,
            created_by=actor_user_id,
        )
    )
    await _log(
        session,
        workspace_id=workspace_id,
        actor=actor_user_id,
        action="subscription.paused",
        description=f"Paused the subscription for {months} month(s)",
        after={"months": months, "resumes_at": until.isoformat()},
        object_id=sub.id,
    )
    return sub


async def resume(
    session: AsyncSession,
    billing: BillingPort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
) -> Subscription:
    sub = await _active_subscription(session, workspace_id)
    if sub.status is not SubscriptionStatus.PAUSED:
        raise ProblemError(409, "not_paused", "Not paused")
    assert sub.stripe_subscription_id is not None
    try:
        await billing.resume(sub.stripe_subscription_id)
    except BillingError as exc:
        raise _billing_error(exc) from exc
    sub.status, sub.pause_until = SubscriptionStatus.ACTIVE, None
    pause_row = await session.scalar(
        select(SubscriptionPause)
        .where(
            SubscriptionPause.workspace_id == workspace_id,
            SubscriptionPause.resumed_at.is_(None),
        )
        .order_by(SubscriptionPause.created_at.desc())
    )
    if pause_row is not None:
        pause_row.resumed_at = datetime.now(UTC)
    if actor_user_id is not None:
        await _log(
            session,
            workspace_id=workspace_id,
            actor=actor_user_id,
            action="subscription.resumed",
            description="Resumed the subscription",
            after={"status": "active"},
            object_id=sub.id,
        )
    return sub


@dataclass(frozen=True, slots=True)
class SurveyResult:
    offer: str  # pause | discount | none
    outcome: str | None  # None when only previewing the offer


async def offer_for(session: AsyncSession, workspace_id: uuid.UUID, reason: str) -> str:
    offer = OFFER_FOR_REASON[reason]
    if offer == "discount":
        used = await session.scalar(
            select(CancelSurvey.id).where(
                CancelSurvey.workspace_id == workspace_id,
                CancelSurvey.outcome == "accepted_discount",
            )
        )
        if used is not None:
            return "none"  # the one-month discount is offered once
    return offer


async def cancel_survey(
    session: AsyncSession,
    billing: BillingPort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str,
    text: str | None,
    decision: str | None,
) -> SurveyResult:
    """Without `decision`: preview the retention offer (no change). With `decision`
    (`cancel` or `accept_offer`): store the answer and act on it."""
    if reason not in CANCEL_REASONS:
        raise ProblemError(422, "invalid_reason", "Pick a reason")
    sub = await _active_subscription(session, workspace_id)
    offer = await offer_for(session, workspace_id, reason)
    if decision is None:
        return SurveyResult(offer=offer, outcome=None)
    if decision == "accept_offer" and offer == "none":
        raise ProblemError(409, "no_offer", "No offer for this reason")
    assert sub.stripe_subscription_id is not None
    if decision == "accept_offer" and offer == "pause":
        await pause(
            session, billing, workspace_id=workspace_id, actor_user_id=actor_user_id, months=1
        )
        outcome = "accepted_pause"
    elif decision == "accept_offer":
        try:
            await billing.apply_coupon(sub.stripe_subscription_id, RETENTION_COUPON_ID)
        except BillingError as exc:
            raise _billing_error(exc) from exc
        outcome = "accepted_discount"
        await _log(
            session,
            workspace_id=workspace_id,
            actor=actor_user_id,
            action="subscription.discount_applied",
            description="Accepted one month free instead of cancelling",
            after={"coupon": RETENTION_COUPON_ID},
            object_id=sub.id,
        )
    elif decision == "cancel":
        try:
            await billing.set_cancel_at_period_end(sub.stripe_subscription_id, True)
        except BillingError as exc:
            raise _billing_error(exc) from exc
        sub.cancel_at_period_end = True
        outcome = "canceled"
        await _log(
            session,
            workspace_id=workspace_id,
            actor=actor_user_id,
            action="subscription.cancel_requested",
            description="Cancelled the subscription at the end of the period",
            after={"reason": reason, "cancel_at_period_end": True},
            object_id=sub.id,
        )
    else:
        raise ProblemError(422, "invalid_decision", "Choose cancel or accept_offer")
    session.add(
        CancelSurvey(
            workspace_id=workspace_id,
            reason=reason,
            text=(text or None),
            offer_made=offer,
            outcome=outcome,
            created_by=actor_user_id,
        )
    )
    return SurveyResult(offer=offer, outcome=outcome)


async def keep_subscription(
    session: AsyncSession,
    billing: BillingPort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> Subscription:
    """Undo a cancellation scheduled for the period end."""
    sub = await _active_subscription(session, workspace_id)
    if not sub.cancel_at_period_end:
        return sub
    assert sub.stripe_subscription_id is not None
    try:
        await billing.set_cancel_at_period_end(sub.stripe_subscription_id, False)
    except BillingError as exc:
        raise _billing_error(exc) from exc
    sub.cancel_at_period_end = False
    await _log(
        session,
        workspace_id=workspace_id,
        actor=actor_user_id,
        action="subscription.cancel_withdrawn",
        description="Kept the subscription",
        after={"cancel_at_period_end": False},
        object_id=sub.id,
    )
    return sub


# --- Summaries ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsageSummary:
    runs: ent.RunUsage
    seats_used: int
    seats_allowed: int | None
    listings_used: int
    listings_allowed: int | None


async def usage_summary(session: AsyncSession, workspace_id: uuid.UUID) -> UsageSummary:
    state = await ent.plan_state(session, workspace_id)
    plan = state.plan if state.status in ent.FULL_ACCESS else None
    return UsageSummary(
        runs=await ent.run_usage(session, workspace_id),
        seats_used=await ent.seats_used(session, workspace_id),
        seats_allowed=plan.seats if plan else None,
        listings_used=0,  # listings arrive in Phase 3
        listings_allowed=plan.live_listings if plan else None,
    )


async def invoices(session: AsyncSession, workspace_id: uuid.UUID) -> list[SaasInvoice]:
    return list(
        (
            await session.scalars(
                select(SaasInvoice)
                .where(SaasInvoice.workspace_id == workspace_id)
                .order_by(SaasInvoice.issued_at.desc().nulls_last(), SaasInvoice.created_at.desc())
                .limit(100)
            )
        ).all()
    )


def monthly_amount_minor(price: PlanPrice) -> int:
    """Monthly-equivalent price (annual / 12, rounded down) for MRR."""
    return (
        price.amount_minor if price.interval is BillingInterval.MONTH else price.amount_minor // 12
    )
