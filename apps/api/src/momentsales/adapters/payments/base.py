"""Payments port (FR-6, ADR-0011): Stripe Connect accounts, Checkout, fees, webhooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class PaymentsError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class WebhookSignatureError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccountState:
    account_id: str
    details_submitted: bool
    charges_enabled: bool
    payouts_enabled: bool
    requirements: dict[str, Any] = field(default_factory=dict)
    livemode: bool = False


@dataclass(frozen=True, slots=True)
class LineItem:
    name: str
    quantity: int
    unit_amount_minor: int


@dataclass(frozen=True, slots=True)
class CheckoutCreated:
    session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class ChargeInfo:
    charge_id: str
    amount_minor: int
    currency: str
    fee_minor: int  # Stripe's own fee, from the balance transaction's fee_details
    amount_refunded_minor: int = 0
    # Platform fee (ADR-0017): the `application_fee` part of fee_details, and the
    # application fee object (created asynchronously, so it may not exist yet).
    platform_fee_minor: int = 0
    application_fee_id: str | None = None


class PaymentsPort(Protocol):
    name: str

    async def create_account(self, *, workspace_id: str, email: str | None) -> str: ...

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str: ...

    async def retrieve_account(self, account_id: str) -> AccountState: ...

    async def create_checkout(
        self,
        account_id: str,
        *,
        currency: str,
        items: list[LineItem],
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
        idempotency_key: str,
        application_fee_minor: int = 0,
    ) -> CheckoutCreated: ...

    async def charge_for_payment_intent(
        self, account_id: str, payment_intent_id: str
    ) -> ChargeInfo: ...

    async def refund_application_fee(
        self, fee_id: str, amount_minor: int, *, idempotency_key: str
    ) -> None:
        """Refund part of a platform fee to the seller (platform account call)."""
        ...

    async def application_fee_refunded(self, fee_id: str) -> int:
        """How much of a platform fee has already been refunded, per the provider."""
        ...

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        """Return the event as a dict, or raise WebhookSignatureError."""
        ...


def account_status(state: AccountState) -> str:
    """Our integration status (docs/integrations/stripe-connect.md)."""
    if not state.details_submitted:
        return "pending"
    if state.charges_enabled and state.payouts_enabled and not state.requirements.get("past_due"):
        return "active"
    return "restricted"
