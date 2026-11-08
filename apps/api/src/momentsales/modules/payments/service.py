"""Stripe Connect onboarding, test checkouts and webhook processing (FR-6, ADR-0011).

Webhooks are verified and stored first (`ingest`), then applied by `process_event` in a job.
Every handler is idempotent: ledger writes are keyed by Stripe ids, and an event that has
been processed is never applied twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.payments.base import (
    AccountState,
    LineItem,
    PaymentsError,
    PaymentsPort,
    account_status,
)
from momentsales.core.config import Settings, get_settings
from momentsales.core.db import set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.core.logging import get_logger
from momentsales.db.ledger import (
    CheckoutSession,
    CheckoutStatus,
    Integration,
    IntegrationStatus,
    IntegrationType,
    LedgerType,
    Order,
    OrderChannel,
    OrderItem,
    OrderStatus,
    WebhookEvent,
)
from momentsales.modules.audit import service as audit
from momentsales.modules.billing import entitlements as billing_entitlements
from momentsales.modules.billing import webhooks as billing_webhooks
from momentsales.modules.billing.webhooks import BILLING_EVENTS
from momentsales.modules.ledger import service as ledger
from momentsales.modules.ledger.money import BASE_CURRENCY

log = get_logger(__name__)

PROVIDER = "stripe"
MAX_ATTEMPTS = 10


# --- Onboarding ------------------------------------------------------------------------


async def get_integration(session: AsyncSession) -> Integration | None:
    return await session.scalar(
        select(Integration).where(Integration.type == IntegrationType.STRIPE)
    )


def apply_state(integration: Integration, state: AccountState) -> None:
    integration.status = IntegrationStatus(account_status(state))
    integration.details_submitted = state.details_submitted
    integration.charges_enabled = state.charges_enabled
    integration.payouts_enabled = state.payouts_enabled
    integration.requirements = state.requirements
    integration.livemode = state.livemode
    integration.last_sync_at = datetime.now(UTC)


async def start_onboarding(
    session: AsyncSession,
    payments: PaymentsPort,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    email: str | None,
) -> str:
    """`/stripe connect`: create the seller's account once, then hand out an onboarding link."""
    integration = await get_integration(session)
    try:
        if integration is None:
            account_id = await payments.create_account(workspace_id=str(workspace_id), email=email)
            integration = Integration(
                workspace_id=workspace_id,
                type=IntegrationType.STRIPE,
                external_id=account_id,
                status=IntegrationStatus.PENDING,
                requirements={},
                connected_by=actor_user_id,
            )
            session.add(integration)
            await session.flush()
            await audit.record(
                session,
                action="stripe.account_created",
                object_type="integration",
                object_id=integration.id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                after={"account": account_id},
            )
        base = settings.web_base_url.rstrip("/")
        return await payments.account_link(
            integration.external_id,
            refresh_url=f"{base}/app/integrations?stripe=refresh",
            return_url=f"{base}/app/integrations?stripe=return",
        )
    except PaymentsError as exc:
        raise _unavailable(exc) from exc


async def refresh_account(session: AsyncSession, payments: PaymentsPort) -> Integration | None:
    integration = await get_integration(session)
    if integration is None:
        return None
    try:
        apply_state(integration, await payments.retrieve_account(integration.external_id))
    except PaymentsError as exc:
        raise _unavailable(exc) from exc
    return integration


def _unavailable(exc: PaymentsError) -> ProblemError:
    log.warning("stripe_call_failed", error=str(exc))
    return ProblemError(
        503 if exc.retryable else 502,
        "payments_unavailable",
        "Stripe is unavailable",
        "We couldn't reach Stripe. Please try again in a minute.",
    )


# --- Test checkout ---------------------------------------------------------------------


async def create_test_checkout(
    session: AsyncSession,
    payments: PaymentsPort,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    items: list[ledger.ItemIn],
) -> CheckoutSession:
    if not settings.stripe_test_mode:
        raise ProblemError(
            403, "test_mode_only", "Test mode only", "Test checkouts need Stripe test mode."
        )
    integration = await get_integration(session)
    if integration is None or not integration.charges_enabled:
        raise ProblemError(
            409,
            "stripe_not_ready",
            "Connect Stripe first",
            "Finish Stripe onboarding before taking payments (/stripe connect).",
        )
    bps = await billing_entitlements.platform_fee_bps(session, workspace_id)
    application_fee = billing_entitlements.platform_fee_minor(sum(i.total for i in items), bps)
    checkout = CheckoutSession(
        workspace_id=workspace_id,
        status=CheckoutStatus.OPEN,
        currency=BASE_CURRENCY,
        application_fee_minor=application_fee,
        items=[
            {
                "description": i.description,
                "quantity": i.quantity,
                "unit_amount_minor": i.unit_amount_minor,
                "product_key": i.product_key,
            }
            for i in items
        ],
        created_by=actor_user_id,
    )
    session.add(checkout)
    await session.flush()
    base = settings.web_base_url.rstrip("/")
    try:
        created = await payments.create_checkout(
            integration.external_id,
            currency=BASE_CURRENCY,
            items=[LineItem(i.description, i.quantity, i.unit_amount_minor) for i in items],
            success_url=f"{base}/app/sales?checkout=success",
            cancel_url=f"{base}/app/sales?checkout=cancelled",
            metadata={"workspace_id": str(workspace_id), "checkout_id": str(checkout.id)},
            idempotency_key=f"checkout-{checkout.id}",
            application_fee_minor=application_fee,
        )
    except PaymentsError as exc:
        raise _unavailable(exc) from exc
    checkout.provider_session_id = created.session_id
    checkout.url = created.url
    await audit.record(
        session,
        action="checkout.test_created",
        object_type="checkout_session",
        object_id=checkout.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "amount_minor": sum(i.total for i in items),
            "items": len(items),
            "application_fee_minor": application_fee,
        },
    )
    return checkout


# --- Webhooks: intake ------------------------------------------------------------------


async def ingest(
    session: AsyncSession,
    payments: PaymentsPort,
    *,
    payload: bytes,
    signature: str | None,
    secret: str,
) -> tuple[str, bool]:
    """Verify and store. Returns (event_id, is_new). Runs in platform context."""
    from momentsales.adapters.payments.base import WebhookSignatureError

    try:
        event = payments.verify_webhook(payload, signature, secret)
    except WebhookSignatureError as exc:
        raise ProblemError(
            400, "invalid_signature", "Invalid signature", "Bad webhook signature."
        ) from exc
    if event.get("livemode"):
        log.error("stripe_live_event_rejected", event_id=event.get("id"))
        raise ProblemError(
            400, "live_event", "Live event refused", "This deployment is test mode only."
        )
    await set_tenant_context(session, platform=True)
    inserted = await session.scalar(
        pg_insert(WebhookEvent)
        .values(
            id=uuid.uuid4(),
            provider=PROVIDER,
            event_id=str(event["id"]),
            type=str(event["type"]),
            account=event.get("account"),
            livemode=False,
            payload=event,
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(WebhookEvent.id)
    )
    return str(event["id"]), inserted is not None


# --- Webhooks: processing --------------------------------------------------------------


async def process_event(
    session: AsyncSession,
    payments: PaymentsPort,
    event_id: str,
    *,
    settings: Settings | None = None,
    email: Any | None = None,
) -> str:
    """Apply one stored event. Returns its outcome. Must run inside a transaction."""
    await set_tenant_context(session, platform=True)
    row = await session.scalar(
        select(WebhookEvent)
        .where(WebhookEvent.event_id == event_id)
        .with_for_update(skip_locked=True)
    )
    if row is None or row.processed_at is not None:
        return "skipped"
    row.attempts += 1
    if row.account is None and row.type in BILLING_EVENTS:
        # Platform-account events: SaaS billing (ADR-0016), not a seller's Connect account.
        outcome = await billing_webhooks.apply(
            session, row, settings=settings or get_settings(), email=email
        )
        row.processed_at = datetime.now(UTC)
        row.outcome = outcome
        row.last_error = None
        return outcome
    account = row.account or (
        row.payload.get("data", {}).get("object", {}).get("id")
        if row.type.startswith("account.")
        else None
    )
    integration = (
        await session.scalar(select(Integration).where(Integration.external_id == account))
        if account
        else None
    )
    if integration is None:
        row.processed_at = datetime.now(UTC)
        row.outcome = "unmatched"
        log.warning("stripe_event_unmatched", event_id=event_id, type=row.type)
        return "unmatched"
    row.workspace_id = integration.workspace_id
    # Tenant tables need the workspace; webhook_events needs platform. Set both.
    await set_tenant_context(session, workspace_id=integration.workspace_id, platform=True)
    handler = HANDLERS.get(row.type)
    obj: dict[str, Any] = row.payload.get("data", {}).get("object", {})
    occurred = datetime.fromtimestamp(int(row.payload.get("created", 0)), UTC)
    outcome = "ignored"
    if handler is not None:
        outcome = await handler(session, payments, integration, obj, occurred, event_id)
    row.processed_at = datetime.now(UTC)
    row.outcome = outcome
    row.last_error = None
    return outcome


async def _on_checkout_completed(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    if obj.get("payment_status") != "paid":
        return "awaiting_payment"  # delayed methods finish with async_payment_succeeded
    session_id = str(obj["id"])
    checkout = await session.scalar(
        select(CheckoutSession).where(CheckoutSession.provider_session_id == session_id)
    )
    if checkout is None:
        return "unknown_checkout"
    existing = await session.scalar(
        select(Order).where(Order.channel == OrderChannel.STRIPE, Order.external_id == session_id)
    )
    if existing is not None:
        return "duplicate"
    charge = await payments.charge_for_payment_intent(
        integration.external_id, str(obj["payment_intent"])
    )
    currency = str(obj.get("currency", "gbp")).upper()
    order = Order(
        workspace_id=integration.workspace_id,
        channel=OrderChannel.STRIPE,
        external_id=session_id,
        status=OrderStatus.PAID,
        customer_email=(obj.get("customer_details") or {}).get("email"),
        currency=currency,
        gross_minor=int(obj.get("amount_total") or charge.amount_minor),
        fees_minor=charge.fee_minor + charge.platform_fee_minor,
        platform_fee_minor=charge.platform_fee_minor,
        platform_fee_id=charge.application_fee_id,
        paid_at=occurred,
        charge_id=charge.charge_id,
    )
    session.add(order)
    await session.flush()
    items = [
        OrderItem(
            workspace_id=integration.workspace_id,
            order_id=order.id,
            description=str(i["description"]),
            quantity=int(i["quantity"]),
            unit_amount_minor=int(i["unit_amount_minor"]),
            product_key=i.get("product_key"),
        )
        for i in checkout.items
    ]
    session.add_all(items)
    await session.flush()
    await ledger.post_order_sale(
        session,
        order,
        items,
        source=PROVIDER,
        ref=session_id,
        occurred_on=await ledger.uk_date(session, occurred),
        fee_minor=charge.fee_minor,
        platform_fee_minor=charge.platform_fee_minor,
    )
    checkout.status = CheckoutStatus.COMPLETED
    checkout.order_id = order.id
    await audit.record(
        session,
        action="sale.stripe",
        object_type="order",
        object_id=order.id,
        workspace_id=integration.workspace_id,
        actor_user_id=None,
        after={
            "event": event_id,
            "gross_minor": order.gross_minor,
            "fee_minor": charge.fee_minor,
            "platform_fee_minor": charge.platform_fee_minor,
        },
    )
    return "order_created"


async def _on_checkout_closed(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
    *,
    status: CheckoutStatus,
) -> str:
    checkout = await session.scalar(
        select(CheckoutSession).where(CheckoutSession.provider_session_id == str(obj["id"]))
    )
    if checkout is not None and checkout.status is CheckoutStatus.OPEN:
        checkout.status = status
    return status.value


async def _on_expired(*args: Any) -> str:
    return await _on_checkout_closed(*args, status=CheckoutStatus.EXPIRED)


async def _on_async_failed(*args: Any) -> str:
    return await _on_checkout_closed(*args, status=CheckoutStatus.FAILED)


async def _order_for_charge(session: AsyncSession, charge_id: str) -> Order | None:
    return await session.scalar(select(Order).where(Order.charge_id == charge_id))


async def _items(session: AsyncSession, order: Order) -> list[OrderItem]:
    return list(
        (await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
    )


async def _refund_platform_fee(
    session: AsyncSession,
    payments: PaymentsPort,
    order: Order,
    items: list[OrderItem],
    charge: dict[str, Any],
    total_refunded: int,
    *,
    occurred_on: date,
) -> None:
    """Give back the platform fee in proportion to what the seller refunded (ADR-0017).

    Refunding a direct charge leaves the application fee with the platform, so we refund it
    ourselves. The target is cumulative and the Stripe call is keyed by it, so a replay, or
    a retry after a failed commit, refunds nothing twice. A failed call raises and the
    webhook job retries the whole event (the ledger writes roll back with it)."""
    if order.platform_fee_minor <= 0 or order.gross_minor <= 0:
        return
    refunded = min(total_refunded, order.gross_minor)
    target = order.platform_fee_minor * refunded // order.gross_minor
    if total_refunded >= order.gross_minor:
        target = order.platform_fee_minor  # a full refund gives back the whole fee
    amount = target - order.platform_fee_refunded_minor
    if amount <= 0:
        return
    fee_id = charge.get("application_fee") or order.platform_fee_id
    if isinstance(fee_id, dict):
        fee_id = fee_id.get("id")
    if not fee_id:
        raise PaymentsError("stripe", "application fee not available yet", retryable=True)
    await payments.refund_application_fee(
        str(fee_id), amount, idempotency_key=f"appfee-refund-{fee_id}-{target}"
    )
    await ledger.post_platform_fee_refund(
        session,
        order,
        items,
        source=PROVIDER,
        ref=f"{charge['id']}:{target}",
        amount_minor=amount,
        occurred_on=occurred_on,
    )
    order.platform_fee_id = str(fee_id)
    order.platform_fee_refunded_minor = target


async def _on_refunded(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    """`amount_refunded` is cumulative, so we post the change since the last refund we saw.
    Keyed by the cumulative amount, a replay or out-of-order duplicate writes nothing."""
    order = await _order_for_charge(session, str(obj["id"]))
    if order is None:
        return "unknown_charge"
    total_refunded = int(obj.get("amount_refunded", 0))
    delta = total_refunded - order.refunded_minor
    if delta <= 0:
        return "no_change"
    items = await _items(session, order)
    occurred_on = await ledger.uk_date(session, occurred)
    await ledger.post_order_refund(
        session,
        order,
        items,
        source=PROVIDER,
        ref=f"{obj['id']}:{total_refunded}",
        amount_minor=delta,
        occurred_on=occurred_on,
    )
    await _refund_platform_fee(
        session, payments, order, items, obj, total_refunded, occurred_on=occurred_on
    )
    order.refunded_minor = total_refunded
    order.status = (
        OrderStatus.REFUNDED
        if total_refunded >= order.gross_minor
        else OrderStatus.PARTIALLY_REFUNDED
    )
    return "refund_recorded"


async def _on_dispute_created(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    order = await _order_for_charge(session, str(obj.get("charge")))
    if order is None:
        return "unknown_charge"
    order.status = OrderStatus.DISPUTED
    return "order_disputed"


async def _dispute_funds(
    session: AsyncSession,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    *,
    sign: int,
    label: str,
) -> str:
    order = await _order_for_charge(session, str(obj.get("charge")))
    await ledger.post_entry(
        session,
        workspace_id=integration.workspace_id,
        type=LedgerType.ADJUSTMENT,
        amount_minor=sign * int(obj.get("amount", 0)),
        currency=str(obj.get("currency", "gbp")).upper(),
        occurred_on=await ledger.uk_date(session, occurred),
        source=PROVIDER,
        source_ref=f"{obj['id']}:{label}",
        description=f"Dispute {label.replace('_', ' ')}",
        order_id=order.id if order else None,
    )
    return f"dispute_{label}"


async def _on_funds_withdrawn(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    return await _dispute_funds(
        session, integration, obj, occurred, sign=-1, label="funds_withdrawn"
    )


async def _on_funds_reinstated(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    return await _dispute_funds(
        session, integration, obj, occurred, sign=1, label="funds_reinstated"
    )


async def _on_payout_paid(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    arrival = datetime.fromtimestamp(int(obj.get("arrival_date") or occurred.timestamp()), UTC)
    await ledger.post_entry(
        session,
        workspace_id=integration.workspace_id,
        type=LedgerType.PAYOUT,
        amount_minor=-int(obj.get("amount", 0)),
        currency=str(obj.get("currency", "gbp")).upper(),
        occurred_on=await ledger.uk_date(session, arrival),
        source=PROVIDER,
        source_ref=str(obj["id"]),
        description="Payout to bank",
    )
    return "payout_recorded"


async def _on_account_updated(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    from momentsales.adapters.payments.stripe import state_from_account

    apply_state(integration, state_from_account(obj))
    return f"account_{integration.status.value}"


async def _on_deauthorized(
    session: AsyncSession,
    payments: PaymentsPort,
    integration: Integration,
    obj: dict[str, Any],
    occurred: datetime,
    event_id: str,
) -> str:
    integration.status = IntegrationStatus.DISCONNECTED
    integration.charges_enabled = False
    integration.payouts_enabled = False
    await audit.record(
        session,
        action="stripe.disconnected",
        object_type="integration",
        object_id=integration.id,
        workspace_id=integration.workspace_id,
        actor_user_id=None,
        after={"event": event_id},
    )
    return "disconnected"


HANDLERS: dict[str, Any] = {
    "checkout.session.completed": _on_checkout_completed,
    "checkout.session.async_payment_succeeded": _on_checkout_completed,
    "checkout.session.async_payment_failed": _on_async_failed,
    "checkout.session.expired": _on_expired,
    "charge.refunded": _on_refunded,
    "charge.dispute.created": _on_dispute_created,
    "charge.dispute.funds_withdrawn": _on_funds_withdrawn,
    "charge.dispute.funds_reinstated": _on_funds_reinstated,
    "payout.paid": _on_payout_paid,
    "account.updated": _on_account_updated,
    "account.application.deauthorized": _on_deauthorized,
}
