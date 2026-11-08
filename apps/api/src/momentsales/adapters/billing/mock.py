"""Mock Stripe Billing for PROVIDER_MODE=mock (no network, no keys).

State lives in Redis. Dev pages (`/dev/mock-billing/*`) drive it: each action returns events
with the shapes Stripe sends (subscription items carry the period; invoices name their
subscription under `parent.subscription_details`), which the dev routes sign with the
platform webhook secret and push through the real receiver. Port calls made by our own
billing service (pause, cancel, coupon, trial end) only update the mock's state: the
service records those changes locally itself.

Invoice amounts come from the caller (our plan prices); the mock never invents prices.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from momentsales.adapters.billing.base import (
    BillingError,
    CheckoutCreated,
    PaymentCheckout,
    SubscriptionCheckout,
)
from momentsales.adapters.payments.mock import build_event
from momentsales.adapters.payments.stripe import verify_signature

_TTL = 30 * 24 * 3600
DAY = 24 * 3600
PERIOD = {"month": 30 * DAY, "year": 365 * DAY}


def _id(prefix: str) -> str:
    return f"{prefix}_mock_{secrets.token_hex(8)}"


def _event(event_type: str, obj: dict[str, Any]) -> dict[str, Any]:
    return build_event(event_type, obj, account=None)  # platform-account event


class MockBillingAdapter:
    name = "mock"

    def __init__(self, redis: Redis, web_base_url: str) -> None:
        self._redis = redis
        self._web = web_base_url.rstrip("/")

    async def _get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mockbilling:{key}")
        return json.loads(raw) if raw else None

    async def _put(self, key: str, value: dict[str, Any]) -> None:
        await self._redis.set(f"mockbilling:{key}", json.dumps(value), ex=_TTL)

    async def _sub(self, subscription_id: str) -> dict[str, Any]:
        sub = await self._get(subscription_id)
        if sub is None:
            raise BillingError("mock", "no such subscription", retryable=False)
        return sub

    # --- BillingPort -------------------------------------------------------------------

    async def create_customer(self, *, workspace_id: str, email: str, name: str) -> str:
        key = f"cust:{workspace_id}"
        existing = await self._get(key)
        if existing:
            return str(existing["id"])
        customer_id = _id("cus")
        customer: dict[str, Any] = {
            "id": customer_id,
            "email": email,
            "name": name,
            "metadata": {"workspace_id": workspace_id},
        }
        await self._put(customer_id, customer)
        await self._put(key, customer)
        return customer_id

    async def _checkout(self, key: str, session: dict[str, Any]) -> CheckoutCreated:
        existing = await self._redis.get(f"mockbilling:idem:{key}")
        if existing:
            sid = existing.decode() if isinstance(existing, bytes) else existing
            stored = await self._get(sid)
            assert stored is not None
            return CheckoutCreated(stored["id"], stored["url"])
        await self._put(session["id"], session)
        await self._redis.set(f"mockbilling:idem:{key}", session["id"], ex=_TTL)
        return CheckoutCreated(session["id"], session["url"])

    async def subscription_checkout(self, req: SubscriptionCheckout) -> CheckoutCreated:
        sid = _id("cs")
        return await self._checkout(
            req.idempotency_key,
            {
                "id": sid,
                "object": "checkout.session",
                "mode": "subscription",
                "customer": req.customer_id,
                "client_reference_id": req.workspace_id,
                "price": req.price_id,
                "trial_days": req.trial_days,
                "metadata": {"workspace_id": req.workspace_id, "kind": "subscription"},
                "status": "open",
                "payment_status": "unpaid",
                "success_url": req.success_url,
                "cancel_url": req.cancel_url,
                "url": f"{self._web}/dev/mock-billing/checkout/{sid}",
            },
        )

    async def payment_checkout(self, req: PaymentCheckout) -> CheckoutCreated:
        sid = _id("cs")
        return await self._checkout(
            req.idempotency_key,
            {
                "id": sid,
                "object": "checkout.session",
                "mode": "payment",
                "customer": req.customer_id,
                "client_reference_id": req.workspace_id,
                "price": req.price_id,
                "quantity": req.quantity,
                "metadata": {"workspace_id": req.workspace_id, **req.metadata},
                "status": "open",
                "payment_status": "unpaid",
                "success_url": req.success_url,
                "cancel_url": req.cancel_url,
                "url": f"{self._web}/dev/mock-billing/checkout/{sid}",
            },
        )

    async def portal_session(
        self, *, customer_id: str, return_url: str, configuration_id: str | None
    ) -> str:
        await self._put(f"portal:{customer_id}", {"return_url": return_url})
        return f"{self._web}/dev/mock-billing/portal?customer={customer_id}"

    async def pause(self, subscription_id: str) -> None:
        sub = await self._sub(subscription_id)
        sub["status"] = "paused"
        await self._put(subscription_id, sub)

    async def resume(self, subscription_id: str) -> None:
        sub = await self._sub(subscription_id)
        sub["status"] = "active"
        await self._put(subscription_id, sub)

    async def set_cancel_at_period_end(self, subscription_id: str, cancel: bool) -> None:
        sub = await self._sub(subscription_id)
        sub["cancel_at_period_end"] = cancel
        await self._put(subscription_id, sub)

    async def apply_coupon(self, subscription_id: str, coupon_id: str) -> None:
        sub = await self._sub(subscription_id)
        sub["discount_coupon"] = coupon_id
        await self._put(subscription_id, sub)

    async def set_trial_end(self, subscription_id: str, trial_end: datetime) -> None:
        sub = await self._sub(subscription_id)
        sub["trial_end"] = int(trial_end.timestamp())
        sub["period_end"] = sub["trial_end"]
        await self._put(subscription_id, sub)

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        return verify_signature(payload, signature, secret)

    # --- Stripe-shaped objects ---------------------------------------------------------

    @staticmethod
    def subscription_object(sub: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": sub["id"],
            "object": "subscription",
            "customer": sub["customer"],
            "status": sub["status"],
            "trial_end": sub.get("trial_end"),
            "cancel_at_period_end": sub.get("cancel_at_period_end", False),
            "metadata": sub["metadata"],
            "items": {
                "object": "list",
                "data": [
                    {
                        "id": sub["item_id"],
                        "object": "subscription_item",
                        "price": {"id": sub["price"]},
                        "current_period_start": sub["period_start"],
                        "current_period_end": sub["period_end"],
                    }
                ],
            },
        }

    @staticmethod
    def invoice_object(
        sub: dict[str, Any], *, status: str, amount_minor: int, currency: str, reason: str
    ) -> dict[str, Any]:
        inv_id = _id("in")
        return {
            "id": inv_id,
            "object": "invoice",
            "customer": sub["customer"],
            "number": f"MOCK-{inv_id[-6:].upper()}",
            "status": status,
            "total": amount_minor,
            "total_taxes": [],
            "currency": currency.lower(),
            "billing_reason": reason,
            "hosted_invoice_url": None,
            "invoice_pdf": None,
            "period_start": sub["period_start"],
            "period_end": sub["period_end"],
            "created": int(time.time()),
            "parent": {
                "type": "subscription_details",
                "subscription_details": {
                    "subscription": sub["id"],
                    "metadata": sub["metadata"],
                },
            },
        }

    # --- Dev-page actions (produce events) ---------------------------------------------

    async def session(self, session_id: str) -> dict[str, Any] | None:
        return await self._get(session_id)

    async def subscription(self, subscription_id: str) -> dict[str, Any] | None:
        return await self._get(subscription_id)

    async def complete_checkout(
        self, session_id: str, *, interval: str, amount_minor: int, currency: str
    ) -> list[dict[str, Any]]:
        """Pay a mock Checkout Session. For subscriptions, `interval` and the first
        invoice amount come from our plan price."""
        session = await self._get(session_id)
        if session is None or session["status"] != "open":
            raise BillingError("mock", "session is not open", retryable=False)
        session.update(status="complete", payment_status="paid")
        events: list[dict[str, Any]] = []
        if session["mode"] == "subscription":
            now = int(time.time())
            trial_days = session.get("trial_days")
            end = now + (trial_days * DAY if trial_days else PERIOD[interval])
            sub = {
                "id": _id("sub"),
                "item_id": _id("si"),
                "customer": session["customer"],
                "status": "trialing" if trial_days else "active",
                "trial_end": end if trial_days else None,
                "cancel_at_period_end": False,
                "metadata": session["metadata"],
                "price": session["price"],
                "interval": interval,
                "amount_minor": amount_minor,
                "currency": currency,
                "period_start": now,
                "period_end": end,
            }
            await self._put(sub["id"], sub)
            session["subscription"] = sub["id"]
            events.append(_event("customer.subscription.created", self.subscription_object(sub)))
            events.append(
                _event(
                    "invoice.paid",
                    self.invoice_object(
                        sub,
                        status="paid",
                        amount_minor=0 if trial_days else amount_minor,
                        currency=currency,
                        reason="subscription_create",
                    ),
                )
            )
        await self._put(session_id, session)
        public = {k: v for k, v in session.items() if k not in ("price", "trial_days", "url")}
        events.insert(0, _event("checkout.session.completed", public))
        return events

    async def change_price(
        self, subscription_id: str, price_id: str, *, at_period_end: bool, amount_minor: int
    ) -> list[dict[str, Any]]:
        sub = await self._sub(subscription_id)
        if at_period_end:  # Portal schedules downgrades for the period end
            sub["pending_price"] = price_id
            sub["pending_amount_minor"] = amount_minor
            await self._put(subscription_id, sub)
            return []
        sub.update(price=price_id, amount_minor=amount_minor, pending_price=None)
        await self._put(subscription_id, sub)
        return [_event("customer.subscription.updated", self.subscription_object(sub))]

    async def fail_payment(self, subscription_id: str) -> list[dict[str, Any]]:
        sub = await self._sub(subscription_id)
        sub["status"] = "past_due"
        await self._put(subscription_id, sub)
        invoice = self.invoice_object(
            sub,
            status="open",
            amount_minor=sub["amount_minor"],
            currency=sub["currency"],
            reason="subscription_cycle",
        )
        sub["open_invoice"] = invoice
        await self._put(subscription_id, sub)
        return [
            _event("invoice.payment_failed", invoice),
            _event("customer.subscription.updated", self.subscription_object(sub)),
        ]

    async def pay_open_invoice(self, subscription_id: str) -> list[dict[str, Any]]:
        sub = await self._sub(subscription_id)
        invoice = sub.pop("open_invoice", None)
        if invoice is None:
            raise BillingError("mock", "no open invoice", retryable=False)
        invoice["status"] = "paid"
        sub["status"] = "active"
        await self._put(subscription_id, sub)
        return [
            _event("invoice.paid", invoice),
            _event("customer.subscription.updated", self.subscription_object(sub)),
        ]

    async def renew(self, subscription_id: str) -> list[dict[str, Any]]:
        """Advance to the next period: ends the trial, applies a scheduled downgrade, or
        ends a subscription set to cancel at period end."""
        sub = await self._sub(subscription_id)
        if sub.get("cancel_at_period_end"):
            sub["status"] = "canceled"
            await self._put(subscription_id, sub)
            return [_event("customer.subscription.deleted", self.subscription_object(sub))]
        if sub.get("pending_price"):
            sub["price"] = sub.pop("pending_price")
            sub["amount_minor"] = sub.pop("pending_amount_minor")
        start = sub["period_end"]
        sub.update(status="active", period_start=start, period_end=start + PERIOD[sub["interval"]])
        amount = 0 if sub.pop("discount_coupon", None) else sub["amount_minor"]
        await self._put(subscription_id, sub)
        return [
            _event(
                "invoice.paid",
                self.invoice_object(
                    sub,
                    status="paid",
                    amount_minor=amount,
                    currency=sub["currency"],
                    reason="subscription_cycle",
                ),
            ),
            _event("customer.subscription.updated", self.subscription_object(sub)),
        ]
