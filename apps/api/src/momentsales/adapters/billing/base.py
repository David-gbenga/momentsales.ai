"""Billing port (FR-10, ADR-0015/0016): Stripe Billing for SaaS subscriptions.

Separate from the payments port: Billing is how sellers pay momentsales.ai; Connect is how
shoppers pay sellers. Field names follow docs/integrations/stripe-billing.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class BillingError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class CheckoutCreated:
    session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class SubscriptionCheckout:
    customer_id: str
    price_id: str
    workspace_id: str
    trial_days: int | None
    success_url: str
    cancel_url: str
    automatic_tax: bool
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PaymentCheckout:
    customer_id: str
    price_id: str
    quantity: int
    workspace_id: str
    metadata: dict[str, str]
    success_url: str
    cancel_url: str
    automatic_tax: bool
    idempotency_key: str


class BillingPort(Protocol):
    name: str

    async def create_customer(self, *, workspace_id: str, email: str, name: str) -> str: ...

    async def subscription_checkout(self, req: SubscriptionCheckout) -> CheckoutCreated: ...

    async def payment_checkout(self, req: PaymentCheckout) -> CheckoutCreated: ...

    async def portal_session(
        self, *, customer_id: str, return_url: str, configuration_id: str | None
    ) -> str: ...

    async def pause(self, subscription_id: str) -> None: ...

    async def resume(self, subscription_id: str) -> None: ...

    async def set_cancel_at_period_end(self, subscription_id: str, cancel: bool) -> None: ...

    async def apply_coupon(self, subscription_id: str, coupon_id: str) -> None: ...

    async def set_trial_end(self, subscription_id: str, trial_end: datetime) -> None: ...

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        """Return the event as a dict, or raise WebhookSignatureError."""
        ...


def mock_price_id(plan_code: str, currency: str, interval: str) -> str:
    """Price ids used in mock mode, where no Stripe catalogue exists."""
    return f"price_mock_{plan_code}_{currency.lower()}_{interval}"


def mock_addon_price_id(kind: str, currency: str) -> str:
    return f"price_mock_addon_{kind}_{currency.lower()}"
