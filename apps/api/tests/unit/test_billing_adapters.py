"""Stripe Billing adapter contract tests: requests match docs/integrations/stripe-billing.md
(checked against the official docs, API 2026-10-28.endive); responses are Stripe-shaped.
Also covers the mock's Stripe-shaped objects."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from momentsales.adapters.billing.base import (
    BillingError,
    PaymentCheckout,
    SubscriptionCheckout,
    mock_price_id,
)
from momentsales.adapters.billing.mock import MockBillingAdapter
from momentsales.adapters.billing.stripe import RETENTION_COUPON_ID, StripeBillingAdapter
from momentsales.core.config import Settings
from momentsales.core.redis import get_redis

STRIPE = "https://api.stripe.com"


@pytest.fixture
def adapter() -> StripeBillingAdapter:
    return StripeBillingAdapter.from_key("sk_test_contract")


def form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def session_response(cs_id: str = "cs_test_1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": cs_id,
            "object": "checkout.session",
            "url": f"https://checkout.stripe.com/{cs_id}",
        },
    )


def test_refuses_live_keys() -> None:
    with pytest.raises(BillingError, match="test-mode"):
        StripeBillingAdapter.from_key("sk_live_nope")


@respx.mock
async def test_create_customer(adapter: StripeBillingAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1", "object": "customer"})
    )
    assert await adapter.create_customer(workspace_id="ws-1", email="o@x.co", name="Shop") == (
        "cus_1"
    )
    body = form(route.calls.last.request)
    assert body == {"email": ["o@x.co"], "name": ["Shop"], "metadata[workspace_id]": ["ws-1"]}
    assert route.calls.last.request.headers["Idempotency-Key"] == "customer-ws-1"


@respx.mock
async def test_trial_checkout_requires_a_card(adapter: StripeBillingAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/checkout/sessions").mock(return_value=session_response())
    created = await adapter.subscription_checkout(
        SubscriptionCheckout(
            customer_id="cus_1",
            price_id="price_growth_m",
            workspace_id="ws-1",
            trial_days=14,
            success_url="http://app/billing?ok",
            cancel_url="http://app/billing",
            automatic_tax=False,
            idempotency_key="sub-ws-1",
        )
    )
    assert created.url.startswith("https://checkout.stripe.com/")
    body = form(route.calls.last.request)
    assert body["mode"] == ["subscription"]
    assert body["customer"] == ["cus_1"]
    assert body["line_items[0][price]"] == ["price_growth_m"]
    assert body["line_items[0][quantity]"] == ["1"]
    assert body["payment_method_collection"] == ["always"]
    assert body["subscription_data[trial_period_days]"] == ["14"]
    assert body["subscription_data[metadata][workspace_id]"] == ["ws-1"]
    assert body["client_reference_id"] == ["ws-1"]
    assert body["customer_update[address]"] == ["auto"]
    assert body["tax_id_collection[enabled]"] == ["true"]
    assert body["automatic_tax[enabled]"] == ["false"]
    assert route.calls.last.request.headers["Idempotency-Key"] == "sub-ws-1"


@respx.mock
async def test_no_trial_when_already_used(adapter: StripeBillingAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/checkout/sessions").mock(return_value=session_response())
    await adapter.subscription_checkout(
        SubscriptionCheckout("cus_1", "p", "ws", None, "http://s", "http://c", True, "k")
    )
    body = form(route.calls.last.request)
    assert "subscription_data[trial_period_days]" not in body
    assert body["automatic_tax[enabled]"] == ["true"]


@respx.mock
async def test_pack_checkout_is_a_one_off_payment(adapter: StripeBillingAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/checkout/sessions").mock(return_value=session_response())
    await adapter.payment_checkout(
        PaymentCheckout(
            customer_id="cus_1",
            price_id="price_pack",
            quantity=1,
            workspace_id="ws-1",
            metadata={"kind": "addon", "purchase_id": "p-1"},
            success_url="http://s",
            cancel_url="http://c",
            automatic_tax=False,
            idempotency_key="pack-1",
        )
    )
    body = form(route.calls.last.request)
    assert body["mode"] == ["payment"]
    assert body["line_items[0][price]"] == ["price_pack"]
    assert body["metadata[purchase_id]"] == ["p-1"]
    assert body["metadata[workspace_id]"] == ["ws-1"]
    assert "payment_method_collection" not in body  # subscription mode only


@respx.mock
async def test_portal_session(adapter: StripeBillingAdapter) -> None:
    route = respx.post(f"{STRIPE}/v1/billing_portal/sessions").mock(
        return_value=httpx.Response(
            200, json={"id": "bps_1", "object": "billing_portal.session", "url": "https://bp/1"}
        )
    )
    url = await adapter.portal_session(
        customer_id="cus_1", return_url="http://app/billing", configuration_id="bpc_1"
    )
    assert url == "https://bp/1"
    assert form(route.calls.last.request) == {
        "customer": ["cus_1"],
        "return_url": ["http://app/billing"],
        "configuration": ["bpc_1"],
    }


@respx.mock
async def test_pause_resume_cancel_coupon_trial(adapter: StripeBillingAdapter) -> None:
    sub = {"id": "sub_1", "object": "subscription"}
    pause = respx.post(f"{STRIPE}/v1/subscriptions/sub_1/pause").mock(
        return_value=httpx.Response(200, json=sub)
    )
    resume = respx.post(f"{STRIPE}/v1/subscriptions/sub_1/resume").mock(
        return_value=httpx.Response(200, json=sub)
    )
    update = respx.post(f"{STRIPE}/v1/subscriptions/sub_1").mock(
        return_value=httpx.Response(200, json=sub)
    )
    await adapter.pause("sub_1")
    assert form(pause.calls.last.request) == {"type": ["subscription"]}
    await adapter.resume("sub_1")
    assert resume.called
    await adapter.set_cancel_at_period_end("sub_1", True)
    assert form(update.calls.last.request) == {"cancel_at_period_end": ["true"]}
    await adapter.apply_coupon("sub_1", RETENTION_COUPON_ID)
    assert form(update.calls.last.request) == {"discounts[0][coupon]": [RETENTION_COUPON_ID]}
    end = datetime(2026, 12, 1, tzinfo=UTC)
    await adapter.set_trial_end("sub_1", end)
    assert form(update.calls.last.request) == {
        "trial_end": [str(int(end.timestamp()))],
        "proration_behavior": ["none"],
    }


@respx.mock
async def test_stripe_errors_are_wrapped(adapter: StripeBillingAdapter) -> None:
    respx.post(f"{STRIPE}/v1/subscriptions/sub_1/pause").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"type": "invalid_request_error", "message": "has a schedule"}},
        )
    )
    with pytest.raises(BillingError) as err:
        await adapter.pause("sub_1")
    assert not err.value.retryable


@respx.mock
async def test_catalogue_and_portal_configuration(adapter: StripeBillingAdapter) -> None:
    respx.post(f"{STRIPE}/v1/products").mock(
        return_value=httpx.Response(200, json={"id": "prod_1", "object": "product"})
    )
    price = respx.post(f"{STRIPE}/v1/prices").mock(
        return_value=httpx.Response(200, json={"id": "price_1", "object": "price"})
    )
    respx.get(f"{STRIPE}/v1/coupons/{RETENTION_COUPON_ID}").mock(
        return_value=httpx.Response(
            404, json={"error": {"type": "invalid_request_error", "message": "No such coupon"}}
        )
    )
    coupon = respx.post(f"{STRIPE}/v1/coupons").mock(
        return_value=httpx.Response(200, json={"id": RETENTION_COUPON_ID, "object": "coupon"})
    )
    config = respx.post(f"{STRIPE}/v1/billing_portal/configurations").mock(
        return_value=httpx.Response(
            200, json={"id": "bpc_1", "object": "billing_portal.configuration"}
        )
    )
    assert await adapter.create_product(name="Growth", metadata={"plan_code": "growth"}) == (
        "prod_1"
    )
    await adapter.create_price(
        product_id="prod_1",
        currency="GBP",
        unit_amount=7900,
        interval="month",
        lookup_key="growth_gbp_month",
    )
    assert form(price.calls.last.request) == {
        "product": ["prod_1"],
        "currency": ["gbp"],
        "unit_amount": ["7900"],
        "lookup_key": ["growth_gbp_month"],
        "recurring[interval]": ["month"],
    }
    assert await adapter.ensure_retention_coupon() == RETENTION_COUPON_ID
    assert form(coupon.calls.last.request) == {
        "id": [RETENTION_COUPON_ID],
        "name": ["One month free"],
        "percent_off": ["100"],
        "duration": ["once"],
    }
    await adapter.create_portal_configuration(
        [{"product": "prod_1", "prices": ["price_1"]}], "http://app/billing"
    )
    body = form(config.calls.last.request)
    assert body["features[subscription_update][enabled]"] == ["true"]
    assert body["features[subscription_update][proration_behavior]"] == ["always_invoice"]
    assert body["features[subscription_update][schedule_at_period_end][conditions][0][type]"] == [
        "decreasing_item_amount"
    ]
    assert body["features[subscription_cancel][enabled]"] == ["false"]
    assert body["features[subscription_pause][enabled]"] == ["false"]
    assert body["features[subscription_update][products][0][prices][0]"] == ["price_1"]


async def test_mock_checkout_emits_stripe_shaped_events() -> None:
    mock = MockBillingAdapter(get_redis(Settings().redis_url), "http://web")
    ws, key = str(uuid.uuid4()), f"k-{uuid.uuid4()}"  # Redis state outlives a test run
    customer = await mock.create_customer(workspace_id=ws, email="o@x.co", name="S")
    assert customer == await mock.create_customer(workspace_id=ws, email="o@x.co", name="S")
    price = mock_price_id("growth", "GBP", "month")
    created = await mock.subscription_checkout(
        SubscriptionCheckout(customer, price, ws, 14, "http://s", "http://c", False, key)
    )
    again = await mock.subscription_checkout(
        SubscriptionCheckout(customer, price, ws, 14, "http://s", "http://c", False, key)
    )
    assert again == created  # idempotent
    events = await mock.complete_checkout(
        created.session_id, interval="month", amount_minor=7900, currency="GBP"
    )
    types = [e["type"] for e in events]
    assert types == ["checkout.session.completed", "customer.subscription.created", "invoice.paid"]
    sub = events[1]["data"]["object"]
    assert sub["status"] == "trialing"
    item = sub["items"]["data"][0]
    assert item["price"]["id"] == price
    assert item["current_period_end"] - item["current_period_start"] == 14 * 24 * 3600
    invoice = events[2]["data"]["object"]
    assert invoice["parent"]["subscription_details"]["subscription"] == sub["id"]
    assert invoice["total"] == 0  # trial
    assert all(e["account"] is None for e in events)  # platform-account events

    failed = await mock.fail_payment(sub["id"])
    assert [e["type"] for e in failed] == [
        "invoice.payment_failed",
        "customer.subscription.updated",
    ]
    assert failed[1]["data"]["object"]["status"] == "past_due"
    paid = await mock.pay_open_invoice(sub["id"])
    assert paid[1]["data"]["object"]["status"] == "active"

    await mock.set_cancel_at_period_end(sub["id"], True)
    ended = await mock.renew(sub["id"])
    assert [e["type"] for e in ended] == ["customer.subscription.deleted"]
