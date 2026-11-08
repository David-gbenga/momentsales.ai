"""Stripe Billing adapter (test mode only). See docs/integrations/stripe-billing.md.

Every request here uses the parameters checked against the official docs on 2026-11-08
(API version 2026-10-28.endive, the pinned SDK's version).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import stripe

from momentsales.adapters.billing.base import (
    BillingError,
    CheckoutCreated,
    PaymentCheckout,
    SubscriptionCheckout,
)
from momentsales.adapters.payments.stripe import verify_signature

RETENTION_COUPON_ID = "retention_one_month"


def _wrap(exc: stripe.StripeError) -> BillingError:
    retryable = (
        isinstance(exc, stripe.RateLimitError | stripe.APIConnectionError)
        or (getattr(exc, "http_status", None) or 0) >= 500
    )
    return BillingError(
        "stripe", f"{type(exc).__name__}: {exc.user_message or exc}", retryable=retryable
    )


def subscription_checkout_params(req: SubscriptionCheckout) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": "subscription",
        "customer": req.customer_id,
        "line_items": [{"price": req.price_id, "quantity": 1}],
        # The PRD requires a card for the trial; `always` is also Stripe's default.
        "payment_method_collection": "always",
        "client_reference_id": req.workspace_id,
        "subscription_data": {"metadata": {"workspace_id": req.workspace_id}},
        "customer_update": {"address": "auto", "name": "auto"},
        "tax_id_collection": {"enabled": True},
        "automatic_tax": {"enabled": req.automatic_tax},
        "success_url": req.success_url,
        "cancel_url": req.cancel_url,
        "metadata": {"workspace_id": req.workspace_id, "kind": "subscription"},
    }
    if req.trial_days:
        params["subscription_data"]["trial_period_days"] = req.trial_days
    return params


def payment_checkout_params(req: PaymentCheckout) -> dict[str, Any]:
    return {
        "mode": "payment",
        "customer": req.customer_id,
        "line_items": [{"price": req.price_id, "quantity": req.quantity}],
        "client_reference_id": req.workspace_id,
        "customer_update": {"address": "auto", "name": "auto"},
        "tax_id_collection": {"enabled": True},
        "automatic_tax": {"enabled": req.automatic_tax},
        "success_url": req.success_url,
        "cancel_url": req.cancel_url,
        "metadata": {"workspace_id": req.workspace_id, **req.metadata},
    }


def portal_configuration_params(products: list[dict[str, Any]], return_url: str) -> dict[str, Any]:
    """Portal: plan changes (downgrades at period end); cancel and pause stay in our app."""
    return {
        "name": "momentsales.ai",
        "default_return_url": return_url,
        "features": {
            "subscription_update": {
                "enabled": True,
                "default_allowed_updates": ["price"],
                "products": products,
                "proration_behavior": "always_invoice",
                "schedule_at_period_end": {
                    "conditions": [
                        {"type": "decreasing_item_amount"},
                        {"type": "shortening_interval"},
                    ]
                },
            },
            "subscription_cancel": {"enabled": False},
            "subscription_pause": {"enabled": False},
            "payment_method_update": {"enabled": True},
            "invoice_history": {"enabled": True},
            "customer_update": {"enabled": True, "allowed_updates": ["address", "name", "tax_id"]},
        },
    }


class StripeBillingAdapter:
    name = "stripe"

    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    @classmethod
    def from_key(cls, secret_key: str) -> StripeBillingAdapter:
        if not secret_key.startswith(("sk_test_", "rk_test_")):
            raise BillingError("stripe", "only test-mode keys are allowed", retryable=False)
        return cls(stripe.StripeClient(secret_key, http_client=stripe.HTTPXClient()))

    async def create_customer(self, *, workspace_id: str, email: str, name: str) -> str:
        try:
            customer = await self._client.v1.customers.create_async(
                {"email": email, "name": name, "metadata": {"workspace_id": workspace_id}},
                options={"idempotency_key": f"customer-{workspace_id}"},
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(customer.id)

    async def _checkout(self, params: dict[str, Any], key: str) -> CheckoutCreated:
        try:
            session = await self._client.v1.checkout.sessions.create_async(
                params,  # type: ignore[arg-type]
                options={"idempotency_key": key},
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        if not session.url:
            raise BillingError("stripe", "checkout session has no URL", retryable=False)
        return CheckoutCreated(session_id=str(session.id), url=str(session.url))

    async def subscription_checkout(self, req: SubscriptionCheckout) -> CheckoutCreated:
        return await self._checkout(subscription_checkout_params(req), req.idempotency_key)

    async def payment_checkout(self, req: PaymentCheckout) -> CheckoutCreated:
        return await self._checkout(payment_checkout_params(req), req.idempotency_key)

    async def portal_session(
        self, *, customer_id: str, return_url: str, configuration_id: str | None
    ) -> str:
        params: dict[str, Any] = {"customer": customer_id, "return_url": return_url}
        if configuration_id:
            params["configuration"] = configuration_id
        try:
            portal = await self._client.v1.billing_portal.sessions.create_async(
                params  # type: ignore[arg-type]
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(portal.url)

    async def _update(self, subscription_id: str, params: dict[str, Any]) -> None:
        try:
            await self._client.v1.subscriptions.update_async(
                subscription_id,
                params,  # type: ignore[arg-type]
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc

    async def pause(self, subscription_id: str) -> None:
        try:
            await self._client.v1.subscriptions.pause_async(
                subscription_id,
                {"type": "subscription"},
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc

    async def resume(self, subscription_id: str) -> None:
        try:
            await self._client.v1.subscriptions.resume_async(subscription_id)
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc

    async def set_cancel_at_period_end(self, subscription_id: str, cancel: bool) -> None:
        await self._update(subscription_id, {"cancel_at_period_end": cancel})

    async def apply_coupon(self, subscription_id: str, coupon_id: str) -> None:
        await self._update(subscription_id, {"discounts": [{"coupon": coupon_id}]})

    async def set_trial_end(self, subscription_id: str, trial_end: datetime) -> None:
        await self._update(
            subscription_id,
            {"trial_end": int(trial_end.timestamp()), "proration_behavior": "none"},
        )

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        return verify_signature(payload, signature, secret)

    # --- Catalogue (seed script only; test mode) ---------------------------------------

    async def create_product(self, *, name: str, metadata: dict[str, str]) -> str:
        try:
            product = await self._client.v1.products.create_async(
                {"name": name, "metadata": metadata}
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(product.id)

    async def create_price(
        self,
        *,
        product_id: str,
        currency: str,
        unit_amount: int,
        interval: str | None,
        lookup_key: str,
    ) -> str:
        params: dict[str, Any] = {
            "product": product_id,
            "currency": currency.lower(),
            "unit_amount": unit_amount,
            "lookup_key": lookup_key,
        }
        if interval:
            params["recurring"] = {"interval": interval}
        try:
            price = await self._client.v1.prices.create_async(params)  # type: ignore[arg-type]
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(price.id)

    async def ensure_retention_coupon(self) -> str:
        try:
            await self._client.v1.coupons.retrieve_async(RETENTION_COUPON_ID)
        except stripe.InvalidRequestError:
            try:
                await self._client.v1.coupons.create_async(
                    {
                        "id": RETENTION_COUPON_ID,
                        "name": "One month free",
                        "percent_off": 100,
                        "duration": "once",
                    }
                )
            except stripe.StripeError as exc:
                raise _wrap(exc) from exc
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return RETENTION_COUPON_ID

    async def create_portal_configuration(
        self, products: list[dict[str, Any]], return_url: str
    ) -> str:
        try:
            config = await self._client.v1.billing_portal.configurations.create_async(
                portal_configuration_params(products, return_url)  # type: ignore[arg-type]
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(config.id)
