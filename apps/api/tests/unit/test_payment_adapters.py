"""Stripe adapter contract tests: requests match the documented API (docs/integrations/
stripe-connect.md, stripe-checkout-webhooks.md); responses are shaped like Stripe's."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from momentsales.adapters.payments.base import (
    AccountState,
    LineItem,
    PaymentsError,
    WebhookSignatureError,
    account_status,
)
from momentsales.adapters.payments.mock import build_event, sign_payload
from momentsales.adapters.payments.stripe import StripePaymentsAdapter, verify_signature

STRIPE = "https://api.stripe.com"


@pytest.fixture
def adapter() -> StripePaymentsAdapter:
    return StripePaymentsAdapter.from_key("sk_test_contract")


def form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def test_refuses_live_keys() -> None:
    with pytest.raises(PaymentsError, match="test-mode"):
        StripePaymentsAdapter.from_key("sk_live_nope")


@respx.mock
async def test_create_account_sends_standard_controller(adapter: StripePaymentsAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/accounts").mock(
        return_value=httpx.Response(200, json={"id": "acct_123", "object": "account"})
    )
    assert await adapter.create_account(workspace_id="ws-1", email="o@example.com") == "acct_123"
    body = form(route.calls.last.request)
    assert body["controller[losses][payments]"] == ["stripe"]
    assert body["controller[fees][payer]"] == ["account"]
    assert body["controller[requirement_collection]"] == ["stripe"]
    assert body["controller[stripe_dashboard][type]"] == ["full"]
    assert body["country"] == ["GB"]
    assert "type" not in body  # deprecated account types are not used
    assert route.calls.last.request.headers["Idempotency-Key"] == "account-ws-1"
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk_test_contract"


@respx.mock
async def test_account_link(adapter: StripePaymentsAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/account_links").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "account_link",
                "url": "https://connect.stripe.com/setup/s/abc",
                "created": 1,
                "expires_at": 301,
            },
        )
    )
    url = await adapter.account_link("acct_1", refresh_url="http://r", return_url="http://b")
    assert url.startswith("https://connect.stripe.com/")
    body = form(route.calls.last.request)
    assert body["type"] == ["account_onboarding"]
    assert body["account"] == ["acct_1"]


@respx.mock
async def test_retrieve_account_maps_status(adapter: StripePaymentsAdapter) -> None:
    respx.get(f"{STRIPE}/v1/accounts/acct_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "acct_1",
                "object": "account",
                "details_submitted": True,
                "charges_enabled": True,
                "payouts_enabled": False,
                "requirements": {"currently_due": ["external_account"], "past_due": []},
                "livemode": False,
            },
        )
    )
    state = await adapter.retrieve_account("acct_1")
    assert account_status(state) == "restricted"
    assert state.requirements["currently_due"] == ["external_account"]


@respx.mock
async def test_checkout_is_a_direct_charge_on_the_seller(adapter: StripePaymentsAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "cs_test_1",
                "object": "checkout.session",
                "url": "https://checkout.stripe.com/c/pay/cs_test_1",
            },
        )
    )
    created = await adapter.create_checkout(
        "acct_seller",
        currency="GBP",
        items=[LineItem("Candle", 2, 1250)],
        success_url="http://s",
        cancel_url="http://c",
        metadata={"workspace_id": "w", "checkout_id": "c1"},
        idempotency_key="checkout-c1",
    )
    assert created.session_id == "cs_test_1"
    request = route.calls.last.request
    assert request.headers["Stripe-Account"] == "acct_seller"
    assert request.headers["Idempotency-Key"] == "checkout-c1"
    body = form(request)
    assert body["mode"] == ["payment"]
    assert body["line_items[0][price_data][currency]"] == ["gbp"]
    assert body["line_items[0][price_data][unit_amount]"] == ["1250"]
    assert body["line_items[0][price_data][product_data][name]"] == ["Candle"]
    assert body["line_items[0][quantity]"] == ["2"]
    assert not any(k.startswith("payment_intent_data[application_fee_amount]") for k in body)


@respx.mock
async def test_fee_comes_from_the_balance_transaction(adapter: StripePaymentsAdapter) -> None:
    respx.get(f"{STRIPE}/v1/payment_intents/pi_1").mock(
        return_value=httpx.Response(
            200, json={"id": "pi_1", "object": "payment_intent", "latest_charge": "ch_1"}
        )
    )
    charge_route = respx.get(url__regex=rf"{STRIPE}/v1/charges/ch_1.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ch_1",
                "object": "charge",
                "amount": 2500,
                "amount_refunded": 0,
                "currency": "gbp",
                "balance_transaction": {
                    "id": "txn_1",
                    "object": "balance_transaction",
                    "fee": 58,
                    "fee_details": [],
                },
            },
        )
    )
    info = await adapter.charge_for_payment_intent("acct_seller", "pi_1")
    assert (info.charge_id, info.amount_minor, info.currency, info.fee_minor) == (
        "ch_1",
        2500,
        "GBP",
        58,
    )
    request = charge_route.calls.last.request
    assert (
        "expand" in request.url.query.decode()
        and "balance_transaction" in request.url.query.decode()
    )
    assert request.headers["Stripe-Account"] == "acct_seller"


@respx.mock
async def test_unexpanded_balance_transaction_is_retryable(adapter: StripePaymentsAdapter) -> None:
    respx.get(f"{STRIPE}/v1/payment_intents/pi_2").mock(
        return_value=httpx.Response(
            200, json={"id": "pi_2", "object": "payment_intent", "latest_charge": "ch_2"}
        )
    )
    respx.get(url__regex=rf"{STRIPE}/v1/charges/ch_2.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ch_2",
                "object": "charge",
                "amount": 1,
                "currency": "gbp",
                "balance_transaction": None,
            },
        )
    )
    with pytest.raises(PaymentsError) as err:
        await adapter.charge_for_payment_intent("acct", "pi_2")
    assert err.value.retryable


@respx.mock
async def test_rate_limit_is_retryable() -> None:
    import stripe

    adapter = StripePaymentsAdapter(
        stripe.StripeClient(
            "sk_test_contract", http_client=stripe.HTTPXClient(), max_network_retries=0
        )
    )
    respx.post(f"{STRIPE}/v1/accounts").mock(
        return_value=httpx.Response(
            429, json={"error": {"type": "rate_limit_error", "message": "slow"}}
        )
    )
    with pytest.raises(PaymentsError) as err:
        await adapter.create_account(workspace_id="w", email=None)
    assert err.value.retryable


def test_signature_verification_round_trip() -> None:
    event = build_event("payout.paid", {"id": "po_1"}, account="acct_1")
    payload = json.dumps(event).encode()
    assert (
        verify_signature(payload, sign_payload(payload, "whsec_a"), "whsec_a")["id"] == event["id"]
    )
    with pytest.raises(WebhookSignatureError):
        verify_signature(payload, sign_payload(payload, "whsec_b"), "whsec_a")
    with pytest.raises(WebhookSignatureError):
        verify_signature(payload + b" ", sign_payload(payload, "whsec_a"), "whsec_a")


@pytest.mark.parametrize(
    ("submitted", "charges", "payouts", "past_due", "status"),
    [
        (False, False, False, [], "pending"),
        (True, True, True, [], "active"),
        (True, True, False, [], "restricted"),
        (True, True, True, ["x"], "restricted"),
    ],
)
def test_account_status(
    submitted: bool, charges: bool, payouts: bool, past_due: list[str], status: str
) -> None:
    state = AccountState("acct", submitted, charges, payouts, {"past_due": past_due})
    assert account_status(state) == status


@respx.mock
async def test_checkout_sends_the_application_fee(adapter: StripePaymentsAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/checkout/sessions").mock(
        return_value=httpx.Response(
            200, json={"id": "cs_f", "object": "checkout.session", "url": "https://x/cs_f"}
        )
    )
    await adapter.create_checkout(
        "acct_seller",
        currency="GBP",
        items=[LineItem("Candle", 2, 1250)],
        success_url="http://s",
        cancel_url="http://c",
        metadata={},
        idempotency_key="checkout-f",
        application_fee_minor=50,
    )
    body = form(route.calls.last.request)
    assert body["payment_intent_data[application_fee_amount]"] == ["50"]
    assert route.calls.last.request.headers["Stripe-Account"] == "acct_seller"


def test_fee_details_split_stripe_and_platform_fees() -> None:
    from momentsales.adapters.payments.stripe import charge_info

    info = charge_info(
        {
            "id": "ch_9",
            "amount": 2500,
            "currency": "gbp",
            "application_fee": "fee_9",
            "balance_transaction": {
                "fee": 108,
                "fee_details": [
                    {"amount": 58, "type": "stripe_fee", "currency": "gbp"},
                    {"amount": 50, "type": "application_fee", "currency": "gbp"},
                ],
            },
        }
    )
    assert (info.fee_minor, info.platform_fee_minor, info.application_fee_id) == (
        58,
        50,
        "fee_9",
    )


@respx.mock
async def test_application_fee_refund_runs_on_the_platform(
    adapter: StripePaymentsAdapter,
) -> None:
    route = respx.post(f"{STRIPE}/v1/application_fees/fee_9/refunds").mock(
        return_value=httpx.Response(
            200, json={"id": "fr_1", "object": "fee_refund", "amount": 20, "fee": "fee_9"}
        )
    )
    await adapter.refund_application_fee("fee_9", 20, idempotency_key="appfee-refund-fee_9-20")
    request = route.calls.last.request
    assert form(request)["amount"] == ["20"]
    assert request.headers["Idempotency-Key"] == "appfee-refund-fee_9-20"
    assert "Stripe-Account" not in request.headers


@respx.mock
async def test_application_fee_refunded_reads_the_platform_fee(
    adapter: StripePaymentsAdapter,
) -> None:
    route = respx.get(f"{STRIPE}/v1/application_fees/fee_9").mock(
        return_value=httpx.Response(
            200,
            json={"id": "fee_9", "object": "application_fee", "amount": 50, "amount_refunded": 20},
        )
    )
    assert await adapter.application_fee_refunded("fee_9") == 20
    assert "Stripe-Account" not in route.calls.last.request.headers
