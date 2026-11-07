"""Mock Stripe for PROVIDER_MODE=mock (no network, no keys).

State lives in Redis. The dev pages (`/dev/mock-stripe/*`) drive it: they build events with
the same JSON shape Stripe sends, sign them with the configured webhook secret and push them
through the real webhook receiver, so processing code is identical in mock and live mode.

The mock fee is a fixed, clearly fake amount so the fee path is exercised; it is not a
Stripe price.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from redis.asyncio import Redis

from momentsales.adapters.payments.base import (
    AccountState,
    ChargeInfo,
    CheckoutCreated,
    LineItem,
    PaymentsError,
)
from momentsales.adapters.payments.stripe import verify_signature

MOCK_FEE_MINOR = 25
_TTL = 7 * 24 * 3600


def _id(prefix: str) -> str:
    return f"{prefix}_mock_{secrets.token_hex(8)}"


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """A `Stripe-Signature` header: t=<ts>,v1=HMAC-SHA256(secret, "<ts>.<payload>")."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def build_event(event_type: str, obj: dict[str, Any], *, account: str | None) -> dict[str, Any]:
    return {
        "id": _id("evt"),
        "object": "event",
        "api_version": "2026-10-28.endive",
        "created": int(time.time()),
        "type": event_type,
        "livemode": False,
        "account": account,
        "data": {"object": obj},
    }


class MockPaymentsAdapter:
    name = "mock"

    def __init__(self, redis: Redis, web_base_url: str) -> None:
        self._redis = redis
        self._web = web_base_url.rstrip("/")

    async def _get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mockstripe:{key}")
        return json.loads(raw) if raw else None

    async def _put(self, key: str, value: dict[str, Any]) -> None:
        await self._redis.set(f"mockstripe:{key}", json.dumps(value), ex=_TTL)

    # --- PaymentsPort ------------------------------------------------------------------

    async def create_account(self, *, workspace_id: str, email: str | None) -> str:
        account_id = _id("acct")
        await self._put(
            account_id,
            {
                "id": account_id,
                "object": "account",
                "details_submitted": False,
                "charges_enabled": False,
                "payouts_enabled": False,
                "requirements": {"currently_due": ["business_profile.url"], "past_due": []},
                "livemode": False,
                "metadata": {"workspace_id": workspace_id},
            },
        )
        return account_id

    async def account_link(self, account_id: str, *, refresh_url: str, return_url: str) -> str:
        if await self._get(account_id) is None:
            raise PaymentsError("mock", "no such account", retryable=False)
        return f"{self._web}/dev/mock-stripe/onboard?account={account_id}"

    async def retrieve_account(self, account_id: str) -> AccountState:
        from momentsales.adapters.payments.stripe import state_from_account

        account = await self._get(account_id)
        if account is None:
            raise PaymentsError("mock", "no such account", retryable=False)
        return state_from_account(account)

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
    ) -> CheckoutCreated:
        existing = await self._redis.get(f"mockstripe:idem:{idempotency_key}")
        if existing:
            session = await self._get(
                existing.decode() if isinstance(existing, bytes) else existing
            )
            assert session is not None
            return CheckoutCreated(session["id"], session["url"])
        session_id = _id("cs")
        session = {
            "id": session_id,
            "object": "checkout.session",
            "account": account_id,
            "mode": "payment",
            "currency": currency.lower(),
            "amount_total": sum(i.quantity * i.unit_amount_minor for i in items),
            "line_items": [dataclasses.asdict(i) for i in items],
            "metadata": metadata,
            "status": "open",
            "payment_status": "unpaid",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "url": f"{self._web}/dev/mock-stripe/checkout/{session_id}",
        }
        await self._put(session_id, session)
        await self._redis.set(f"mockstripe:idem:{idempotency_key}", session_id, ex=_TTL)
        return CheckoutCreated(session_id, session["url"])

    async def charge_for_payment_intent(
        self, account_id: str, payment_intent_id: str
    ) -> ChargeInfo:
        intent = await self._get(payment_intent_id)
        if intent is None or intent["account"] != account_id:
            raise PaymentsError("mock", "no such payment", retryable=False)
        charge = await self._get(intent["latest_charge"])
        assert charge is not None
        return ChargeInfo(
            charge_id=charge["id"],
            amount_minor=charge["amount"],
            currency=charge["currency"].upper(),
            fee_minor=charge["fee"],
            amount_refunded_minor=charge["amount_refunded"],
        )

    def verify_webhook(self, payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
        return verify_signature(payload, signature, secret)

    # --- Dev-page actions (produce events) ---------------------------------------------

    async def session(self, session_id: str) -> dict[str, Any] | None:
        return await self._get(session_id)

    async def complete_onboarding(self, account_id: str) -> dict[str, Any]:
        account = await self._get(account_id)
        if account is None:
            raise PaymentsError("mock", "no such account", retryable=False)
        account.update(
            details_submitted=True,
            charges_enabled=True,
            payouts_enabled=True,
            requirements={"currently_due": [], "eventually_due": [], "past_due": []},
        )
        await self._put(account_id, account)
        return build_event("account.updated", account, account=account_id)

    async def pay(self, session_id: str, email: str) -> dict[str, Any]:
        session = await self._get(session_id)
        if session is None or session["status"] != "open":
            raise PaymentsError("mock", "session is not open", retryable=False)
        charge_id, intent_id = _id("ch"), _id("pi")
        await self._put(
            charge_id,
            {
                "id": charge_id,
                "account": session["account"],
                "amount": session["amount_total"],
                "currency": session["currency"],
                "fee": MOCK_FEE_MINOR,
                "amount_refunded": 0,
                "payment_intent": intent_id,
            },
        )
        await self._put(
            intent_id, {"id": intent_id, "account": session["account"], "latest_charge": charge_id}
        )
        session.update(
            status="complete",
            payment_status="paid",
            payment_intent=intent_id,
            customer_details={"email": email},
        )
        await self._put(session_id, session)
        return build_event("checkout.session.completed", session, account=session["account"])

    async def expire(self, session_id: str) -> dict[str, Any]:
        session = await self._get(session_id)
        if session is None:
            raise PaymentsError("mock", "no such session", retryable=False)
        session.update(status="expired")
        await self._put(session_id, session)
        return build_event("checkout.session.expired", session, account=session["account"])

    async def refund(self, charge_id: str, amount_minor: int | None) -> dict[str, Any]:
        charge = await self._get(charge_id)
        if charge is None:
            raise PaymentsError("mock", "no such charge", retryable=False)
        remaining = charge["amount"] - charge["amount_refunded"]
        amount = remaining if amount_minor is None else min(amount_minor, remaining)
        if amount <= 0:
            raise PaymentsError("mock", "nothing left to refund", retryable=False)
        charge["amount_refunded"] += amount
        await self._put(charge_id, charge)
        obj = {
            "id": charge_id,
            "object": "charge",
            "amount": charge["amount"],
            "amount_refunded": charge["amount_refunded"],
            "currency": charge["currency"],
            "refunded": charge["amount_refunded"] >= charge["amount"],
            "payment_intent": charge["payment_intent"],
        }
        return build_event("charge.refunded", obj, account=charge["account"])

    async def payout(self, account_id: str, amount_minor: int, currency: str) -> dict[str, Any]:
        obj = {
            "id": _id("po"),
            "object": "payout",
            "amount": amount_minor,
            "currency": currency.lower(),
            "arrival_date": int(time.time()),
            "status": "paid",
        }
        return build_event("payout.paid", obj, account=account_id)
