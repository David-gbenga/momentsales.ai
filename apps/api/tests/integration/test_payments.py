"""Stripe Connect, test checkout and webhook pipeline in mock mode (AC-6, AC-7).

The mock produces Stripe-shaped events, signed with the configured secret, and pushes them
through the real receiver and processor.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from helpers import Session, add_member, create_workspace, set_subscription, sign_in
from sqlalchemy import func, select

from momentsales.adapters.payments.mock import (
    MOCK_FEE_MINOR,
    MockPaymentsAdapter,
    build_event,
    sign_payload,
)
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.redis import get_redis
from momentsales.db.ledger import LedgerEntry, Order

API = "/api/v1"


async def connected_shop(client: httpx.AsyncClient, plan: str = "pro") -> tuple[Session, str, str]:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Stripe Shop", plan=plan)
    r = await client.post(f"{API}/integrations/stripe/connect", headers=owner.headers(ws))
    assert r.status_code == 200, r.text
    account = r.json()["url"].split("account=")[1]
    status = (await client.get(f"{API}/integrations/stripe", headers=owner.headers(ws))).json()
    assert status["status"] == "pending" and not status["charges_enabled"]
    r = await client.post(f"{API}/dev/mock-stripe/onboard", json={"account": account})
    assert r.status_code == 200, r.text
    status = (await client.get(f"{API}/integrations/stripe", headers=owner.headers(ws))).json()
    assert status["status"] == "active", status
    assert status["test_mode"] is True
    return owner, ws, account


async def checkout(
    owner: Session, ws: str, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    items = items or [
        {
            "description": "Soy candle",
            "quantity": 2,
            "unit_amount": "12.50",
            "product_key": "candle",
        }
    ]
    r = await owner.client.post(
        f"{API}/checkout/test-sessions", json={"items": items}, headers=owner.headers(ws)
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def pay(client: httpx.AsyncClient, url: str) -> str:
    session_id = url.rsplit("/", 1)[1]
    r = await client.post(
        f"{API}/dev/mock-stripe/checkout/{session_id}/pay", json={"email": "shopper@example.com"}
    )
    assert r.status_code == 200, r.text
    return session_id


async def counts(ws: str, user_id: str) -> tuple[int, int]:
    import uuid

    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=uuid.UUID(user_id), workspace_id=uuid.UUID(ws))
        orders = await s.scalar(select(func.count(Order.id)))
        entries = await s.scalar(select(func.count(LedgerEntry.id)))
    return int(orders or 0), int(entries or 0)


async def test_ac6_checkout_creates_one_order_and_replay_is_ignored(
    client: httpx.AsyncClient,
) -> None:
    owner, ws, _ = await connected_shop(client)
    created = await checkout(owner, ws)
    await pay(client, created["url"])

    orders = (await client.get(f"{API}/orders", headers=owner.headers(ws))).json()["items"]
    assert len(orders) == 1
    order = orders[0]
    assert order["channel"] == "stripe"
    assert order["status"] == "paid"
    assert order["gross_minor"] == 2500
    assert order["fees_minor"] == MOCK_FEE_MINOR
    assert order["net_minor"] == 2500 - MOCK_FEE_MINOR
    assert order["customer_email"] == "shopper@example.com"

    entries = (await client.get(f"{API}/ledger", headers=owner.headers(ws))).json()["items"]
    assert sorted(e["type"] for e in entries) == ["fee", "sale"]
    assert {e["product_key"] for e in entries} == {"candle"}

    summary = (await client.get(f"{API}/sales/summary", headers=owner.headers(ws))).json()
    assert summary["totals"]["orders"] == 1
    assert summary["totals"]["net_minor"] == 2500 - MOCK_FEE_MINOR

    # Replaying the exact same signed delivery creates nothing new
    before = await counts(ws, owner.user_id)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, platform=True)
        from momentsales.db.ledger import WebhookEvent

        row = await s.scalar(
            select(WebhookEvent)
            .where(
                WebhookEvent.type == "checkout.session.completed",
                WebhookEvent.workspace_id.is_not(None),
            )
            .order_by(WebhookEvent.received_at.desc())
        )
        assert row is not None
        payload = json.dumps(row.payload).encode()
    secret = get_settings().stripe_connect_webhook_secret.get_secret_value()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret)},
    )
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert await counts(ws, owner.user_id) == before

    # A second event id for the same session (Stripe retry with a new id) is also a no-op
    event = json.loads(payload)
    event["id"] = event["id"] + "x"
    payload2 = json.dumps(event).encode()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload2,
        headers={"Stripe-Signature": sign_payload(payload2, secret)},
    )
    assert r.status_code == 200 and r.json()["duplicate"] is False
    assert await counts(ws, owner.user_id) == before


async def test_ac7_refund_creates_reversing_entries_and_updates_pnl(
    client: httpx.AsyncClient,
) -> None:
    owner, ws, _ = await connected_shop(client)
    await pay(client, (await checkout(owner, ws))["url"])
    order = (await client.get(f"{API}/orders", headers=owner.headers(ws))).json()["items"][0]
    charge_id = await _charge_id(ws, owner.user_id)

    pnl = (await client.get(f"{API}/pnl", headers=owner.headers(ws))).json()["total"]
    assert pnl["revenue_minor"] == 2500 and pnl["refunds_minor"] == 0

    # Partial refund, then the rest
    r = await client.post(
        f"{API}/dev/mock-stripe/refund", json={"charge_id": charge_id, "amount_minor": 1000}
    )
    assert r.status_code == 200, r.text
    detail = (await client.get(f"{API}/orders/{order['id']}", headers=owner.headers(ws))).json()
    assert detail["status"] == "partially_refunded" and detail["refunded_minor"] == 1000
    assert (await client.get(f"{API}/pnl", headers=owner.headers(ws))).json()["total"][
        "refunds_minor"
    ] == -1000

    await client.post(f"{API}/dev/mock-stripe/refund", json={"charge_id": charge_id})
    detail = (await client.get(f"{API}/orders/{order['id']}", headers=owner.headers(ws))).json()
    assert detail["status"] == "refunded" and detail["refunded_minor"] == 2500
    pnl = (await client.get(f"{API}/pnl", headers=owner.headers(ws))).json()["total"]
    assert pnl["refunds_minor"] == -2500
    assert pnl["profit_minor"] == -MOCK_FEE_MINOR  # the Stripe fee isn't returned

    refunds = (await client.get(f"{API}/ledger?type=refund", headers=owner.headers(ws))).json()
    assert sum(e["amount_minor"] for e in refunds["items"]) == -2500


async def _charge_id(ws: str, user_id: str) -> str:
    import uuid

    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=uuid.UUID(user_id), workspace_id=uuid.UUID(ws))
        charge = await s.scalar(select(Order.charge_id))
    assert charge
    return charge


async def test_payout_recorded_but_not_in_pnl(client: httpx.AsyncClient) -> None:
    owner, ws, account = await connected_shop(client)
    await pay(client, (await checkout(owner, ws))["url"])
    r = await client.post(
        f"{API}/dev/mock-stripe/payout", json={"account": account, "amount_minor": 2475}
    )
    assert r.status_code == 200, r.text
    payouts = (await client.get(f"{API}/ledger?type=payout", headers=owner.headers(ws))).json()
    assert [e["amount_minor"] for e in payouts["items"]] == [-2475]
    assert (await client.get(f"{API}/pnl", headers=owner.headers(ws))).json()["total"][
        "profit_minor"
    ] == 2500 - MOCK_FEE_MINOR

    # AC-12: an Analyst can't see orders, the ledger or payouts
    analyst = await add_member(owner, ws, "analyst")
    for path in ("/orders", "/ledger", "/ledger?type=payout", "/pnl", "/sales/summary"):
        r = await client.get(f"{API}{path}", headers=analyst.headers(ws))
        assert r.status_code == 403, path


async def test_checkout_needs_finished_onboarding(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Not Ready")
    r = await client.post(
        f"{API}/checkout/test-sessions",
        json={"items": [{"description": "x", "unit_amount": "1.00"}]},
        headers=owner.headers(ws),
    )
    assert r.status_code == 409
    assert r.json()["type"] == "stripe_not_ready"


async def test_only_owner_can_connect_stripe(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Roles")
    admin = await add_member(owner, ws, "admin")
    r = await client.post(f"{API}/integrations/stripe/connect", headers=admin.headers(ws))
    assert r.status_code == 403


async def test_webhook_rejects_bad_signature_and_live_events(client: httpx.AsyncClient) -> None:
    secret = get_settings().stripe_connect_webhook_secret.get_secret_value()
    event = build_event("payout.paid", {"id": "po_x", "amount": 1}, account="acct_x")
    payload = json.dumps(event).encode()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, "whsec_wrong")},
    )
    assert r.status_code == 400 and r.json()["type"] == "invalid_signature"
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret, timestamp=1_000_000_000)},
    )
    assert r.status_code == 400  # outside the 5-minute tolerance
    r = await client.post(f"{API}/webhooks/stripe-connect", content=payload)
    assert r.status_code == 400  # no signature
    # The platform endpoint uses its own secret
    r = await client.post(
        f"{API}/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret)},
    )
    assert r.status_code == 400

    live = dict(event, livemode=True, id="evt_live_1")
    live_payload = json.dumps(live).encode()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=live_payload,
        headers={"Stripe-Signature": sign_payload(live_payload, secret)},
    )
    assert r.status_code == 400 and r.json()["type"] == "live_event"


async def test_event_for_unknown_account_is_stored_as_unmatched(client: httpx.AsyncClient) -> None:
    secret = get_settings().stripe_connect_webhook_secret.get_secret_value()
    event = build_event("payout.paid", {"id": "po_y", "amount": 5}, account="acct_unknown")
    payload = json.dumps(event).encode()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret)},
    )
    assert r.status_code == 200
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, platform=True)
        from momentsales.db.ledger import WebhookEvent

        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event["id"]))
    assert row is not None and row.outcome == "unmatched" and row.processed_at is not None


async def test_test_checkout_idempotency(client: httpx.AsyncClient) -> None:
    owner, ws, _ = await connected_shop(client)
    body = {"items": [{"description": "Mug", "unit_amount": "9.00"}]}
    h = owner.headers(ws) | {"Idempotency-Key": "co-1"}
    a = await client.post(f"{API}/checkout/test-sessions", json=body, headers=h)
    b = await client.post(f"{API}/checkout/test-sessions", json=body, headers=h)
    assert a.status_code == b.status_code == 201
    assert a.json()["url"] == b.json()["url"]


async def _fee_state(ws: str, user_id: str) -> tuple[int, int, str | None]:
    import uuid

    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=uuid.UUID(user_id), workspace_id=uuid.UUID(ws))
        order = await s.scalar(select(Order))
    assert order is not None
    return order.platform_fee_minor, order.platform_fee_refunded_minor, order.platform_fee_id


async def _mock_fee(client: httpx.AsyncClient, fee_id: str) -> dict[str, Any]:
    payments = MockPaymentsAdapter(get_redis(), get_settings().web_base_url)
    fee = await payments.application_fee(fee_id)
    assert fee is not None
    return dict(fee)


async def test_pro_plan_pays_no_platform_fee(client: httpx.AsyncClient) -> None:
    owner, ws, _ = await connected_shop(client, plan="pro")
    await pay(client, (await checkout(owner, ws))["url"])
    assert (await _fee_state(ws, owner.user_id))[0] == 0
    entries = (await client.get(f"{API}/ledger?type=fee", headers=owner.headers(ws))).json()
    assert [e["description"] for e in entries["items"]] == ["Stripe fee: Soy candle"]


async def test_lite_pays_two_percent_and_gets_it_back_on_refunds(
    client: httpx.AsyncClient,
) -> None:
    owner, ws, _ = await connected_shop(client, plan="lite")
    await pay(client, (await checkout(owner, ws))["url"])  # £25.00 -> 2% = 50p
    fee, refunded, fee_id = await _fee_state(ws, owner.user_id)
    assert (fee, refunded) == (50, 0) and fee_id
    order = (await client.get(f"{API}/orders", headers=owner.headers(ws))).json()["items"][0]
    assert order["fees_minor"] == MOCK_FEE_MINOR + 50
    fees = (await client.get(f"{API}/ledger?type=fee", headers=owner.headers(ws))).json()
    by_desc = {e["description"]: e["amount_minor"] for e in fees["items"]}
    assert by_desc == {"Stripe fee: Soy candle": -MOCK_FEE_MINOR, "Platform fee: Soy candle": -50}

    charge_id = await _charge_id(ws, owner.user_id)
    # Partial refund of £10.00 (40%) gives back 20p
    r = await client.post(
        f"{API}/dev/mock-stripe/refund", json={"charge_id": charge_id, "amount_minor": 1000}
    )
    assert r.status_code == 200, r.text
    assert (await _fee_state(ws, owner.user_id))[:2] == (50, 20)
    assert (await _mock_fee(client, fee_id))["amount_refunded"] == 20

    # Replaying the refund event refunds nothing twice
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, platform=True)
        from momentsales.db.ledger import WebhookEvent

        row = await s.scalar(
            select(WebhookEvent)
            .where(WebhookEvent.type == "charge.refunded")
            .order_by(WebhookEvent.received_at.desc())
        )
        assert row is not None
        event = dict(row.payload)
    event["id"] = event["id"] + "-retry"
    payload = json.dumps(event).encode()
    secret = get_settings().stripe_connect_webhook_secret.get_secret_value()
    r = await client.post(
        f"{API}/webhooks/stripe-connect",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret)},
    )
    assert r.status_code == 200, r.text
    assert (await _fee_state(ws, owner.user_id))[:2] == (50, 20)
    assert (await _mock_fee(client, fee_id))["amount_refunded"] == 20

    # Refunding the rest gives back the whole fee
    await client.post(f"{API}/dev/mock-stripe/refund", json={"charge_id": charge_id})
    assert (await _fee_state(ws, owner.user_id))[:2] == (50, 50)
    assert (await _mock_fee(client, fee_id))["amount_refunded"] == 50
    pnl = (await client.get(f"{API}/pnl", headers=owner.headers(ws))).json()["total"]
    assert pnl["profit_minor"] == -MOCK_FEE_MINOR  # only Stripe's own fee is kept


async def test_trial_pays_no_platform_fee(client: httpx.AsyncClient) -> None:
    owner, ws, _ = await connected_shop(client, plan="lite")
    await set_subscription(__import__("uuid").UUID(ws), plan="growth", status="trialing")
    created = await checkout(owner, ws)
    await pay(client, created["url"])
    assert (await _fee_state(ws, owner.user_id))[0] == 0


async def test_fee_already_refunded_at_stripe_is_not_sent_again(
    client: httpx.AsyncClient,
) -> None:
    """A Stripe fee refund that succeeded before our commit rolled back is not repeated,
    even once Stripe's idempotency key has expired."""
    owner, ws, _ = await connected_shop(client, plan="lite")
    await pay(client, (await checkout(owner, ws))["url"])  # 50p platform fee
    _, _, fee_id = await _fee_state(ws, owner.user_id)
    assert fee_id
    mock = MockPaymentsAdapter(get_redis(), get_settings().web_base_url)
    # Stripe already gave back the whole fee under a key we no longer remember
    await mock.refund_application_fee(fee_id, 50, idempotency_key="lost-key")
    charge_id = await _charge_id(ws, owner.user_id)
    r = await client.post(f"{API}/dev/mock-stripe/refund", json={"charge_id": charge_id})
    assert r.status_code == 200, r.text
    assert (await _fee_state(ws, owner.user_id))[:2] == (50, 50)  # ledger catches up
    assert (await _mock_fee(client, fee_id))["amount_refunded"] == 50  # nothing sent twice
    fees = (await client.get(f"{API}/ledger?type=fee", headers=owner.headers(ws))).json()
    refunds = [e for e in fees["items"] if e["description"].startswith("Platform fee refund")]
    assert sum(e["amount_minor"] for e in refunds) == 50
