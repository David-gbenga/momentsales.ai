"""Stripe Connect adapter (test mode only). See docs/integrations/stripe-connect.md and
docs/integrations/stripe-checkout-webhooks.md.

Accounts use the Standard-equivalent controller properties; Checkout Sessions are direct
charges on the seller's account (`Stripe-Account` header via `stripe_account`).
"""

from __future__ import annotations

from typing import Any

import stripe

from momentsales.adapters.payments.base import (
    AccountState,
    ChargeInfo,
    CheckoutCreated,
    LineItem,
    PaymentsError,
    WebhookSignatureError,
)

# Standard-equivalent: Stripe carries losses, the seller pays Stripe's fees, Stripe collects
# KYC through hosted onboarding, and the seller gets the full Dashboard (ADR-0011).
STANDARD_CONTROLLER: dict[str, Any] = {
    "losses": {"payments": "stripe"},
    "fees": {"payer": "account"},
    "requirement_collection": "stripe",
    "stripe_dashboard": {"type": "full"},
}
REQUIREMENT_FIELDS = (
    "currently_due",
    "eventually_due",
    "past_due",
    "pending_verification",
    "disabled_reason",
    "current_deadline",
    "errors",
)


def _wrap(exc: stripe.StripeError) -> PaymentsError:
    retryable = (
        isinstance(exc, stripe.RateLimitError | stripe.APIConnectionError)
        or (getattr(exc, "http_status", None) or 0) >= 500
    )
    return PaymentsError(
        "stripe", f"{type(exc).__name__}: {exc.user_message or exc}", retryable=retryable
    )


class StripePaymentsAdapter:
    name = "stripe"

    def __init__(self, client: stripe.StripeClient) -> None:
        self._client = client

    @classmethod
    def from_key(cls, secret_key: str) -> StripePaymentsAdapter:
        if not secret_key.startswith(("sk_test_", "rk_test_")):
            raise PaymentsError("stripe", "only test-mode keys are allowed", retryable=False)
        return cls(stripe.StripeClient(secret_key, http_client=stripe.HTTPXClient()))

    async def create_account(self, *, workspace_id: str, email: str | None) -> str:
        params: dict[str, Any] = {
            "country": "GB",
            "controller": STANDARD_CONTROLLER,
            "metadata": {"workspace_id": workspace_id},
        }
        if email:
            params["email"] = email
        try:
            account = await self._client.v1.accounts.create_async(
                params,  # type: ignore[arg-type]
                options={"idempotency_key": f"account-{workspace_id}"},
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(account.id)

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str:
        try:
            link = await self._client.v1.account_links.create_async(
                {
                    "account": account_id,
                    "refresh_url": refresh_url,
                    "return_url": return_url,
                    "type": "account_onboarding",
                }
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return str(link.url)

    async def retrieve_account(self, account_id: str) -> AccountState:
        try:
            account = await self._client.v1.accounts.retrieve_async(account_id)
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return state_from_account(account.to_dict())

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
    ) -> CheckoutCreated:
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "product_data": {"name": i.name},
                        "unit_amount": i.unit_amount_minor,
                    },
                    "quantity": i.quantity,
                }
                for i in items
            ],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
        }
        if application_fee_minor > 0:
            # Direct charge: the fee moves from the seller's account to the platform.
            params["payment_intent_data"] = {"application_fee_amount": application_fee_minor}
        try:
            session = await self._client.v1.checkout.sessions.create_async(
                params,  # type: ignore[arg-type]
                options={"stripe_account": account_id, "idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        if not session.url:
            raise PaymentsError("stripe", "checkout session has no URL", retryable=False)
        return CheckoutCreated(session_id=str(session.id), url=str(session.url))

    async def charge_for_payment_intent(
        self, account_id: str, payment_intent_id: str
    ) -> ChargeInfo:
        opts: Any = {"stripe_account": account_id}
        try:
            intent = await self._client.v1.payment_intents.retrieve_async(
                payment_intent_id, options=opts
            )
            charge_id = intent.latest_charge
            if not charge_id:
                raise PaymentsError("stripe", "payment has no charge yet", retryable=True)
            charge = await self._client.v1.charges.retrieve_async(
                charge_id if isinstance(charge_id, str) else charge_id.id,
                {"expand": ["balance_transaction"]},
                options=opts,
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc
        return charge_info(charge.to_dict())

    async def refund_application_fee(
        self, fee_id: str, amount_minor: int, *, idempotency_key: str
    ) -> None:
        # Application fees live on the platform account: no Stripe-Account header.
        try:
            await self._client.v1.application_fees.refunds.create_async(
                fee_id, {"amount": amount_minor}, options={"idempotency_key": idempotency_key}
            )
        except stripe.StripeError as exc:
            raise _wrap(exc) from exc

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        return verify_signature(payload, signature, secret)


def verify_signature(payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
    """Stripe's own verification (timestamp tolerance 300 s). Used in mock mode too."""
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise WebhookSignatureError(str(exc)) from exc
    result: dict[str, Any] = event.to_dict()
    return result


def state_from_account(account: dict[str, Any]) -> AccountState:
    requirements = account.get("requirements") or {}
    return AccountState(
        account_id=str(account["id"]),
        details_submitted=bool(account.get("details_submitted")),
        charges_enabled=bool(account.get("charges_enabled")),
        payouts_enabled=bool(account.get("payouts_enabled")),
        requirements={k: requirements.get(k) for k in REQUIREMENT_FIELDS if k in requirements},
        livemode=bool(account.get("livemode", False)),
    )


def charge_info(charge: dict[str, Any]) -> ChargeInfo:
    """Fees from the expanded balance transaction; never computed. On a direct charge the
    connected account's `fee` includes the application fee, so `fee_details` splits it
    into the platform fee and Stripe's own fees (ADR-0017)."""
    txn = charge.get("balance_transaction")
    if not isinstance(txn, dict):
        raise PaymentsError("stripe", "balance transaction not available yet", retryable=True)
    total = int(txn.get("fee", 0))
    platform = sum(
        int(d.get("amount", 0))
        for d in txn.get("fee_details") or []
        if d.get("type") == "application_fee"
    )
    fee_id = charge.get("application_fee")
    if isinstance(fee_id, dict):
        fee_id = fee_id.get("id")
    return ChargeInfo(
        charge_id=str(charge["id"]),
        amount_minor=int(charge["amount"]),
        currency=str(charge["currency"]).upper(),
        fee_minor=total - platform,
        amount_refunded_minor=int(charge.get("amount_refunded", 0)),
        platform_fee_minor=platform,
        application_fee_id=str(fee_id) if fee_id else None,
    )
