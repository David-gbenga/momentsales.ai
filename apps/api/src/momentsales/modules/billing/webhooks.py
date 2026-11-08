"""Billing webhooks (ADR-0016): platform-account events from /webhooks/stripe.

`apply` runs inside the shared webhook job (modules/payments/service.process_event) for
events with no Connect `account`. It finds the workspace (metadata first, then the stored
Stripe customer), switches to that workspace's RLS context and applies the event
idempotently. Older events never overwrite newer state (`last_event_at`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.email.base import EmailMessage, EmailSendError
from momentsales.core.config import Settings
from momentsales.core.db import set_tenant_context
from momentsales.core.logging import get_logger
from momentsales.db.billing import (
    AddonKind,
    AddonPurchase,
    PurchaseStatus,
    SaasInvoice,
    Subscription,
    SubscriptionStatus,
)
from momentsales.db.ledger import WebhookEvent
from momentsales.db.models import Membership, MembershipStatus, Role, User, Workspace
from momentsales.modules.billing import entitlements as ent
from momentsales.modules.billing import service

log = get_logger(__name__)

SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
    }
)
BILLING_EVENTS = SUBSCRIPTION_EVENTS | {
    "customer.subscription.trial_will_end",
    "invoice.paid",
    "invoice.payment_failed",
    "checkout.session.completed",
}

STATUS_MAP = {
    "trialing": SubscriptionStatus.TRIALING,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "unpaid": SubscriptionStatus.PAST_DUE,
    "paused": SubscriptionStatus.PAUSED,
    "canceled": SubscriptionStatus.CANCELED,
    "incomplete_expired": SubscriptionStatus.CANCELED,
    "incomplete": SubscriptionStatus.INCOMPLETE,
}


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _workspace_hint(event_type: str, obj: dict[str, Any]) -> str | None:
    if event_type.startswith("invoice."):
        details = (obj.get("parent") or {}).get("subscription_details") or {}
        return (details.get("metadata") or {}).get("workspace_id")
    if event_type == "checkout.session.completed":
        return obj.get("client_reference_id") or (obj.get("metadata") or {}).get("workspace_id")
    return (obj.get("metadata") or {}).get("workspace_id")


async def _resolve_workspace(
    session: AsyncSession, event_type: str, obj: dict[str, Any]
) -> uuid.UUID | None:
    """Platform context: metadata names the workspace; the customer id confirms or finds
    it. A hint that doesn't match an existing workspace is ignored."""
    hint = _workspace_hint(event_type, obj)
    if hint:
        try:
            ws = uuid.UUID(str(hint))
        except ValueError:
            ws = None
        if ws and await session.get(Workspace, ws) is not None:
            return ws
    customer = obj.get("customer")
    if customer:
        found = await session.scalar(
            select(Subscription.workspace_id).where(Subscription.stripe_customer_id == customer)
        )
        if found:
            return found
    return None


async def apply(
    session: AsyncSession,
    row: WebhookEvent,
    *,
    settings: Settings,
    email: Any | None = None,
) -> str:
    """Apply one billing event. `row` is locked by the caller; platform context is set."""
    obj: dict[str, Any] = row.payload.get("data", {}).get("object", {})
    occurred = datetime.fromtimestamp(int(row.payload.get("created", 0)), UTC)
    workspace_id = await _resolve_workspace(session, row.type, obj)
    if workspace_id is None:
        log.warning("billing_event_unmatched", event_id=row.event_id, type=row.type)
        return "unmatched"
    row.workspace_id = workspace_id
    await set_tenant_context(session, workspace_id=workspace_id, platform=True)
    if row.type in SUBSCRIPTION_EVENTS:
        return await _on_subscription(session, workspace_id, row.type, obj, occurred)
    if row.type == "invoice.paid":
        return await _on_invoice(session, workspace_id, obj, paid=True)
    if row.type == "invoice.payment_failed":
        outcome = await _on_invoice(session, workspace_id, obj, paid=False)
        if outcome == "past_due" and email is not None:
            await _email_owner(
                session,
                email,
                settings,
                workspace_id,
                subject="Your momentsales.ai payment didn't go through",
                text=(
                    "We couldn't take your subscription payment. Stripe will retry it, but "
                    "until it's paid your workspace is read-only (your storefront keeps "
                    "selling). Update your card from the Billing page: "
                    f"{settings.web_base_url.rstrip('/')}/app/billing"
                ),
                key=f"payment-failed-{obj.get('id')}",
            )
        return outcome
    if row.type == "checkout.session.completed":
        return await _on_checkout(session, workspace_id, obj)
    return "stored"  # trial_will_end: our own reminders run from the billing sweep


async def _account(session: AsyncSession, workspace_id: uuid.UUID) -> Subscription:
    sub = await service.account(session, workspace_id)
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
    return sub


async def _on_subscription(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    event_type: str,
    obj: dict[str, Any],
    occurred: datetime,
) -> str:
    sub = await _account(session, workspace_id)
    if sub.stripe_subscription_id and sub.stripe_subscription_id != obj.get("id"):
        if event_type == "customer.subscription.deleted":
            return "stale_subscription"  # an older subscription ended; keep the current one
        if sub.status not in (SubscriptionStatus.CANCELED, SubscriptionStatus.INCOMPLETE):
            log.warning("billing_second_subscription", workspace_id=str(workspace_id))
    if sub.last_event_at and occurred < sub.last_event_at:
        return "out_of_order"
    status = STATUS_MAP.get(str(obj.get("status")), SubscriptionStatus.INCOMPLETE)
    if event_type == "customer.subscription.deleted":
        status = SubscriptionStatus.CANCELED
    if status is SubscriptionStatus.PAST_DUE and sub.status is SubscriptionStatus.SUSPENDED:
        status = SubscriptionStatus.SUSPENDED  # ours: only a paid invoice lifts it
    items = (obj.get("items") or {}).get("data") or []
    item = items[0] if items else {}
    price_id = (item.get("price") or {}).get("id")
    if price_id:
        found = await service.plan_for_price_id(session, str(price_id))
        if found is None:
            log.error("billing_unknown_price", price_id=price_id)
            return "unknown_price"
        plan, price = found
        sub.plan_id, sub.interval, sub.currency = plan.id, price.interval, price.currency
    sub.stripe_subscription_id = str(obj["id"])
    if obj.get("customer"):
        sub.stripe_customer_id = str(obj["customer"])
    sub.status = status
    sub.trial_end = _ts(obj.get("trial_end"))
    if status is SubscriptionStatus.TRIALING or sub.trial_end:
        sub.trial_used = True
    sub.current_period_start = _ts(item.get("current_period_start")) or sub.current_period_start
    sub.current_period_end = _ts(item.get("current_period_end")) or sub.current_period_end
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    if status is SubscriptionStatus.PAST_DUE and sub.past_due_since is None:
        sub.past_due_since = occurred
    if status in ent.FULL_ACCESS:
        sub.past_due_since = None
        sub.pause_until = None
    sub.last_event_at = occurred
    return status.value


async def _on_invoice(
    session: AsyncSession, workspace_id: uuid.UUID, obj: dict[str, Any], *, paid: bool
) -> str:
    invoice_id = str(obj["id"])
    invoice = await session.scalar(
        select(SaasInvoice).where(SaasInvoice.stripe_invoice_id == invoice_id)
    )
    if invoice is None:
        invoice = SaasInvoice(workspace_id=workspace_id, stripe_invoice_id=invoice_id, status="")
        session.add(invoice)
    invoice.number = obj.get("number")
    invoice.status = "paid" if paid else str(obj.get("status") or "open")
    invoice.total_minor = int(obj.get("total") or 0)
    invoice.tax_minor = sum(int(t.get("amount") or 0) for t in obj.get("total_taxes") or [])
    invoice.currency = str(obj.get("currency") or "gbp").upper()
    invoice.billing_reason = obj.get("billing_reason")
    invoice.hosted_invoice_url = obj.get("hosted_invoice_url")
    invoice.pdf_url = obj.get("invoice_pdf")
    invoice.period_start = _ts(obj.get("period_start"))
    invoice.period_end = _ts(obj.get("period_end"))
    invoice.issued_at = _ts(obj.get("created")) or invoice.issued_at
    sub = await service.account(session, workspace_id)
    if sub is None:
        return "invoice_stored"
    if paid:
        sub.past_due_since = None
        if sub.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED):
            sub.status = SubscriptionStatus.ACTIVE  # paying the invoice restores access
        return "invoice_paid"
    if sub.status is not SubscriptionStatus.SUSPENDED:
        sub.status = SubscriptionStatus.PAST_DUE
    if sub.past_due_since is None:
        sub.past_due_since = datetime.now(UTC)
    return "past_due"


async def _on_checkout(session: AsyncSession, workspace_id: uuid.UUID, obj: dict[str, Any]) -> str:
    if obj.get("mode") == "subscription":
        sub = await _account(session, workspace_id)
        if obj.get("customer"):
            sub.stripe_customer_id = str(obj["customer"])
        if obj.get("subscription"):
            sub.stripe_subscription_id = str(obj["subscription"])
        return "subscription_checkout"
    if obj.get("payment_status") != "paid":
        return "awaiting_payment"
    purchase = await session.scalar(
        select(AddonPurchase)
        .where(AddonPurchase.stripe_checkout_session_id == str(obj["id"]))
        .with_for_update()
    )
    if purchase is None:
        purchase_id = (obj.get("metadata") or {}).get("purchase_id")
        if purchase_id:
            purchase = await session.get(AddonPurchase, uuid.UUID(str(purchase_id)))
    if purchase is None:
        return "unknown_purchase"
    if purchase.status is PurchaseStatus.PAID:
        return "duplicate"
    purchase.status = PurchaseStatus.PAID
    purchase.paid_at = datetime.now(UTC)
    purchase.stripe_checkout_session_id = str(obj["id"])
    await ent.grant_runs(
        session,
        workspace_id,
        pack=purchase.runs if purchase.kind is AddonKind.RUN_PACK else 0,
        single=purchase.runs if purchase.kind is AddonKind.SINGLE_REPORT else 0,
    )
    return f"{purchase.kind.value}_granted"


async def owner_emails(session: AsyncSession, workspace_id: uuid.UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                select(User.email)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.workspace_id == workspace_id,
                    Membership.role == Role.OWNER,
                    Membership.status == MembershipStatus.ACTIVE,
                )
            )
        ).all()
    )


async def _email_owner(
    session: AsyncSession,
    email: Any,
    settings: Settings,
    workspace_id: uuid.UUID,
    *,
    subject: str,
    text: str,
    key: str,
) -> None:
    for address in await owner_emails(session, workspace_id):
        try:
            await email.send(
                EmailMessage(
                    to=address,
                    subject=subject,
                    text=text,
                    idempotency_key=f"{key}-{address}",
                    tags={"category": "billing"},
                )
            )
        except EmailSendError as exc:  # the banner still shows; don't fail the event
            log.error("billing_email_failed", error=str(exc), workspace_id=str(workspace_id))
