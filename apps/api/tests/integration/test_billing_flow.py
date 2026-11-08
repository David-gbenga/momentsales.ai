"""Phase 1B acceptance criteria end to end through the API and the mock Stripe Billing
(signed Stripe-shaped events -> real webhook receiver -> job, inline in tests).

AC-14 trial sign-up, AC-15 plan limits and run packs, AC-16 upgrade/downgrade/cancel,
AC-17 dunning, AC-18 admin tenants/MRR/trial extension, AC-19 billing isolation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from helpers import Session, add_member, create_workspace, last_email_to, sign_in
from sqlalchemy import text

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.modules.billing import jobs as billing_jobs

API = "/api/v1"
SPEND = {
    "amount": "5.00",
    "currency": "GBP",
    "spent_on": "2026-11-01",
    "vendor": "Royal Mail",
    "category": "shipping",
    "payment_method": "card",
}


async def call(
    s: Session,
    ws: str,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    **kw: object,
) -> httpx.Response:
    h = s.headers(ws) | (headers or {})
    return await s.client.request(method, f"{API}{path}", headers=h, **kw)  # type: ignore[arg-type]


async def subscription(s: Session, ws: str) -> dict[str, object]:
    r = await call(s, ws, "GET", "/billing/subscription")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def pay(s: Session, url: str) -> None:
    session_id = url.rsplit("/", 1)[-1]
    r = await s.client.post(f"{API}/dev/mock-billing/checkout/{session_id}/pay")
    assert r.status_code == 200, r.text


async def dev(s: Session, path: str, **body: object) -> httpx.Response:
    r = await s.client.post(f"{API}/dev/mock-billing/{path}", json=body)
    assert r.status_code == 200, r.text
    return r


async def subscribe(s: Session, ws: str, plan: str, interval: str = "month") -> None:
    r = await call(
        s, ws, "POST", "/billing/checkout", json={"plan_code": plan, "interval": interval}
    )
    assert r.status_code == 200, r.text
    await pay(s, r.json()["url"])


async def test_public_plans_from_the_table(client: httpx.AsyncClient) -> None:
    r = await client.get(f"{API}/plans")
    assert r.status_code == 200
    body = r.json()
    assert [p["code"] for p in body["plans"]] == ["lite", "starter", "growth", "pro"]
    growth = next(p for p in body["plans"] if p["code"] == "growth")
    assert (growth["research_runs"], growth["live_listings"], growth["seats"]) == (40, 100, 3)
    assert {(x["interval"], x["amount_minor"]) for x in growth["prices"]} == {
        ("month", 7900),
        ("year", 79000),
    }
    assert body["trial"] == {"days": 14, "runs": 3, "plan_code": "growth"}
    assert {a["kind"]: a["amount_minor"] for a in body["addons"]} == {
        "run_pack": 1500,
        "single_report": 900,
    }


async def test_ac14_trial_sign_up(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    assert (await subscription(owner, ws))["status"] == "none"
    r = await call(owner, ws, "POST", "/spends", json=SPEND)
    assert r.json()["type"] == "subscription_required"

    await subscribe(owner, ws, "growth")
    sub = await subscription(owner, ws)
    assert sub["status"] == "trialing"
    assert sub["plan"] == {"code": "growth", "name": "Growth"}
    trial_end = datetime.fromisoformat(str(sub["trial_end"]))
    assert timedelta(days=13, hours=23) < trial_end - datetime.now(UTC) <= timedelta(days=14)
    usage = (await call(owner, ws, "GET", "/billing/usage")).json()
    assert usage["research_runs"]["allowed"] == 3  # trial cap
    assert usage["seats"]["allowed"] == 3  # Growth features
    r = await call(owner, ws, "POST", "/spends", json=SPEND, headers={"Idempotency-Key": "s1"})
    assert r.status_code == 201, r.text  # the gate is open
    # A second checkout is refused; changes go through the Portal
    r = await call(owner, ws, "POST", "/billing/checkout", json={"plan_code": "pro"})
    assert r.status_code == 409


async def test_ac15_limit_then_run_pack(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "starter")
    await dev(owner, "renew", workspace_id=ws)  # end the trial: Starter's 10 runs apply
    assert (await subscription(owner, ws))["status"] == "active"
    for _ in range(10):
        r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
        assert r.status_code == 200 and r.json()["pool"] == "plan", r.text
    r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
    assert r.status_code == 402
    assert r.json() | {"upgrade_url": "x"} == r.json() | {
        "type": "plan_limit_reached",
        "limit": "research_runs",
        "used": 10,
        "allowed": 10,
        "upgrade_url": "x",
    }
    r = await call(owner, ws, "POST", "/billing/packs/checkout")
    assert r.status_code == 200, r.text
    await pay(owner, r.json()["url"])
    r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
    assert r.status_code == 200 and r.json()["pool"] == "pack"
    assert (await call(owner, ws, "GET", "/billing/usage")).json()["research_runs"][
        "pack_remaining"
    ] == 9


async def test_pay_per_report_without_a_plan(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    r = await call(owner, ws, "POST", "/billing/packs/checkout")
    assert r.status_code == 409  # packs need a plan
    r = await call(owner, ws, "POST", "/billing/report-checkout")
    assert r.status_code == 200, r.text
    await pay(owner, r.json()["url"])
    r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
    assert r.status_code == 200 and r.json()["pool"] == "single"
    r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
    assert r.status_code == 402


async def _customer(ws: str) -> str:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        return str(
            await s.scalar(
                text("SELECT stripe_customer_id FROM subscriptions WHERE workspace_id = :w"),
                {"w": ws},
            )
        )


async def test_ac16_upgrade_now_downgrade_and_cancel_at_period_end(
    client: httpx.AsyncClient,
) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "starter")
    await dev(owner, "renew", workspace_id=ws)
    customer = await _customer(ws)
    r = await call(owner, ws, "POST", "/billing/portal")
    assert r.status_code == 200 and "mock-billing/portal" in r.json()["url"]

    # Upgrade applies immediately
    await dev(owner, "portal/change-plan", customer=customer, plan_code="growth")
    sub = await subscription(owner, ws)
    assert sub["plan"]["code"] == "growth"  # type: ignore[index]
    assert (await call(owner, ws, "GET", "/billing/usage")).json()["seats"]["allowed"] == 3
    # Downgrade waits for the period end
    await dev(owner, "portal/change-plan", customer=customer, plan_code="starter")
    assert (await subscription(owner, ws))["plan"]["code"] == "growth"  # type: ignore[index]
    await dev(owner, "renew", workspace_id=ws)
    assert (await subscription(owner, ws))["plan"]["code"] == "starter"  # type: ignore[index]

    # Cancel survey: preview the offer, then cancel at period end
    r = await call(owner, ws, "POST", "/billing/cancel-survey", json={"reason": "other"})
    assert r.json()["offer"] == "none" and r.json()["outcome"] is None
    r = await call(
        owner, ws, "POST", "/billing/cancel-survey", json={"reason": "other", "decision": "cancel"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["subscription"]["cancel_at_period_end"] is True
    assert (await subscription(owner, ws))["status"] == "active"  # until the period ends
    await dev(owner, "renew", workspace_id=ws)
    assert (await subscription(owner, ws))["status"] == "canceled"
    r = await call(owner, ws, "POST", "/spends", json=SPEND)
    assert r.json()["type"] == "subscription_required"
    # Data stays readable after cancelling
    assert (await call(owner, ws, "GET", "/spends")).status_code == 200


async def test_retention_offers_and_pause(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "growth")
    await dev(owner, "renew", workspace_id=ws)
    r = await call(owner, ws, "POST", "/billing/cancel-survey", json={"reason": "too_expensive"})
    assert r.json()["offer"] == "discount"
    r = await call(
        owner,
        ws,
        "POST",
        "/billing/cancel-survey",
        json={"reason": "too_expensive", "decision": "accept_offer"},
    )
    assert r.json()["outcome"] == "accepted_discount"
    # The discount is offered once
    r = await call(owner, ws, "POST", "/billing/cancel-survey", json={"reason": "too_expensive"})
    assert r.json()["offer"] == "none"
    # Seasonal break -> pause offer; accepting pauses for a month (read-only, research off)
    r = await call(
        owner,
        ws,
        "POST",
        "/billing/cancel-survey",
        json={"reason": "seasonal_break", "decision": "accept_offer"},
    )
    assert r.json()["outcome"] == "accepted_pause"
    sub = r.json()["subscription"]
    assert sub["status"] == "paused" and sub["pause_until"]
    r = await call(owner, ws, "POST", "/spends", json=SPEND)
    assert r.json()["type"] == "subscription_read_only"
    r = await owner.client.post(f"{API}/dev/billing/simulate-run", headers=owner.headers(ws))
    assert r.status_code == 403
    # The billing sweep resumes it after the pause ends
    counts = await billing_jobs.sweep(now=datetime.now(UTC) + timedelta(days=31))
    assert counts["resumed"] >= 1
    assert (await subscription(owner, ws))["status"] == "active"


async def test_ac17_dunning(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "growth")
    await dev(owner, "renew", workspace_id=ws)
    await dev(owner, "fail-payment", workspace_id=ws)
    sub = await subscription(owner, ws)
    assert sub["status"] == "past_due" and sub["past_due_since"]
    r = await call(owner, ws, "POST", "/spends", json=SPEND)
    assert r.json()["type"] == "subscription_read_only"
    email = await last_email_to(owner.email)
    assert "payment didn't go through" in str(email["subject"])
    # 30 days unpaid -> suspended (our state; Stripe still says past_due)
    await billing_jobs.sweep(now=datetime.now(UTC) + timedelta(days=31))
    assert (await subscription(owner, ws))["status"] == "suspended"
    # Paying the invoice restores access
    await dev(owner, "pay-invoice", workspace_id=ws)
    sub = await subscription(owner, ws)
    assert sub["status"] == "active" and sub["past_due_since"] is None
    invoices = (await call(owner, ws, "GET", "/billing/invoices")).json()
    assert {i["status"] for i in invoices} >= {"paid"}


async def test_trial_reminders_on_day_10_and_13(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "growth")
    start = datetime.now(UTC)
    await billing_jobs.sweep(now=start + timedelta(days=9))
    assert (await subscription(owner, ws))["status"] == "trialing"
    await billing_jobs.sweep(now=start + timedelta(days=10, hours=1))
    first = await last_email_to(owner.email)
    assert "Your free trial ends" in str(first["subject"])
    assert "£79.00 per month" in str(first["text"])
    await billing_jobs.sweep(now=start + timedelta(days=10, hours=2))  # not sent twice
    assert (await last_email_to(owner.email))["id"] == first["id"]
    await billing_jobs.sweep(now=start + timedelta(days=13, hours=1))
    assert (await last_email_to(owner.email))["id"] != first["id"]


async def test_webhook_replay_and_out_of_order_events(client: httpx.AsyncClient) -> None:
    from momentsales.adapters.billing.mock import MockBillingAdapter
    from momentsales.adapters.payments.mock import build_event, sign_payload
    from momentsales.core.config import Settings

    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "growth")
    await dev(owner, "renew", workspace_id=ws)
    settings = Settings()
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        sub_id = await s.scalar(
            text("SELECT stripe_subscription_id FROM subscriptions WHERE workspace_id = :w"),
            {"w": ws},
        )
    from momentsales.core.redis import get_redis

    mock = MockBillingAdapter(get_redis(settings.redis_url), settings.web_base_url)
    stored = await mock.subscription(str(sub_id))
    assert stored is not None
    old = build_event(
        "customer.subscription.updated",
        MockBillingAdapter.subscription_object(stored | {"status": "past_due"}),
        account=None,
    )
    old["created"] -= 3600  # an hour older than what we've applied
    payload = __import__("json").dumps(old).encode()
    secret = settings.stripe_webhook_secret.get_secret_value()
    headers = {"Stripe-Signature": sign_payload(payload, secret)}
    for _ in range(2):  # the replay is stored once
        r = await client.post(f"{API}/webhooks/stripe", content=payload, headers=headers)
        assert r.status_code == 200, r.text
    assert (await subscription(owner, ws))["status"] == "active"  # not regressed


async def test_billing_roles(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan="pro")
    admin = await add_member(owner, ws, "admin")
    analyst = await add_member(owner, ws, "analyst")
    assert (await call(admin, ws, "GET", "/billing/usage")).status_code == 200
    assert (await call(admin, ws, "GET", "/billing/invoices")).status_code == 403
    assert (await call(admin, ws, "POST", "/billing/portal")).status_code == 403
    assert (
        await call(admin, ws, "POST", "/billing/checkout", json={"plan_code": "pro"})
    ).status_code == 403
    assert (await call(analyst, ws, "GET", "/billing/usage")).status_code == 403
    assert (await call(analyst, ws, "GET", "/billing/subscription")).status_code == 200


async def _make_platform_admin(email: str) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            text("UPDATE users SET is_platform_admin = true WHERE email = :e"), {"e": email}
        )


async def test_ac18_admin_tenants_mrr_and_trial_extension(client: httpx.AsyncClient) -> None:
    staff = await sign_in(client)
    await _make_platform_admin(staff.email)
    owner = await sign_in(client)
    trialing = await create_workspace(owner, plan=None)
    await subscribe(owner, trialing, "growth")
    paying = await create_workspace(owner, plan=None)
    await subscribe(owner, paying, "starter", interval="year")
    await dev(owner, "renew", workspace_id=paying)

    r = await client.get(f"{API}/admin/tenants", headers=staff.headers())
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {t["workspace_id"]: t for t in body["tenants"]}
    assert rows[trialing]["status"] == "trialing" and rows[trialing]["mrr_minor"] == 0
    assert rows[paying]["status"] == "active" and rows[paying]["mrr_minor"] == 29000 // 12
    active_sum = sum(t["mrr_minor"] for t in body["tenants"] if t["status"] == "active")
    assert body["mrr_minor"]["GBP"] == active_sum  # AC-18: MRR = sum of active subscriptions

    before = datetime.fromisoformat(str((await subscription(owner, trialing))["trial_end"]))
    r = await client.post(
        f"{API}/admin/tenants/{trialing}/trial-extension",
        json={"days": 7, "reason": "Seasonal launch delayed"},
        headers=staff.headers(),
    )
    assert r.status_code == 200, r.text
    after = datetime.fromisoformat(str((await subscription(owner, trialing))["trial_end"]))
    assert after - before == timedelta(days=7)
    touches = (await call(owner, trialing, "GET", "/touches")).json()
    assert any("extended the trial by 7 days" in t["description"] for t in touches["items"])

    # Not for regular users
    r = await client.get(f"{API}/admin/tenants", headers=owner.headers())
    assert r.status_code == 403


async def test_admin_plan_edit_and_unit_economics_guard(client: httpx.AsyncClient) -> None:
    staff = await sign_in(client)
    await _make_platform_admin(staff.email)
    r = await client.get(f"{API}/admin/plans", headers=staff.headers())
    lite = next(p for p in r.json() if p["code"] == "lite")
    # PRD cap is $2.00/run but prices are GBP: compared only when currencies match
    assert any("not checked" in w for w in lite["warnings"])
    try:
        r = await client.patch(
            f"{API}/admin/plans/lite",
            json={
                "research_runs": 4,
                "prices": [{"currency": "GBP", "interval": "month", "amount_minor": 1300}],
            },
            headers=staff.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["research_runs"] == 4
        month = next(p for p in r.json()["prices"] if p["interval"] == "month")
        assert month["amount_minor"] == 1300 and month["stripe_price_id"] is None
    finally:  # restore the seeded placeholders for other tests
        await client.patch(
            f"{API}/admin/plans/lite",
            json={
                "research_runs": 3,
                "prices": [{"currency": "GBP", "interval": "month", "amount_minor": 1200}],
            },
            headers=staff.headers(),
        )


@pytest.mark.parametrize("path", ["/billing/usage", "/billing/invoices", "/billing/subscription"])
async def test_ac19_billing_is_isolated(client: httpx.AsyncClient, path: str) -> None:
    owner_a, owner_b = await sign_in(client), await sign_in(client)
    a = await create_workspace(owner_a, plan=None)
    await subscribe(owner_a, a, "growth")
    r = await call(owner_b, a, "GET", path)
    assert r.status_code == 404  # not a member: existence isn't revealed


async def test_keep_after_cancel_and_manual_resume(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    await subscribe(owner, ws, "growth")
    await dev(owner, "renew", workspace_id=ws)
    await call(
        owner, ws, "POST", "/billing/cancel-survey", json={"reason": "other", "decision": "cancel"}
    )
    r = await call(owner, ws, "POST", "/billing/keep")
    assert r.status_code == 200 and r.json()["cancel_at_period_end"] is False
    await dev(owner, "renew", workspace_id=ws)
    assert (await subscription(owner, ws))["status"] == "active"  # kept
    r = await call(owner, ws, "POST", "/billing/pause", json={"months": 2})
    assert r.json()["status"] == "paused"
    assert (await call(owner, ws, "POST", "/billing/pause", json={"months": 1})).status_code == 409
    r = await call(owner, ws, "POST", "/billing/resume")
    assert r.json()["status"] == "active"
    assert (await call(owner, ws, "POST", "/billing/resume")).status_code == 409
    touches = (await call(owner, ws, "GET", "/touches")).json()["items"]
    descriptions = {t["description"] for t in touches}
    assert {"Kept the subscription", "Resumed the subscription"} <= descriptions


async def _post_event(client: httpx.AsyncClient, event: dict[str, object]) -> str:
    import json

    from momentsales.adapters.payments.mock import sign_payload
    from momentsales.core.config import Settings

    payload = json.dumps(event).encode()
    secret = Settings().stripe_webhook_secret.get_secret_value()
    r = await client.post(
        f"{API}/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": sign_payload(payload, secret)},
    )
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, platform=True)
        outcome = await s.scalar(
            text("SELECT outcome FROM webhook_events WHERE event_id = :e"), {"e": event["id"]}
        )
    return str(outcome)


async def test_unmatched_and_unpaid_billing_events(client: httpx.AsyncClient) -> None:
    from momentsales.adapters.payments.mock import build_event

    unknown = build_event(
        "invoice.payment_failed",
        {"id": "in_x", "object": "invoice", "customer": "cus_nobody", "total": 100},
        account=None,
    )
    assert await _post_event(client, unknown) == "unmatched"
    bogus_hint = build_event(
        "customer.subscription.updated",
        {"id": "sub_x", "status": "active", "metadata": {"workspace_id": str(uuid.uuid4())}},
        account=None,
    )
    assert await _post_event(client, bogus_hint) == "unmatched"

    owner = await sign_in(client)
    ws = await create_workspace(owner, plan=None)
    r = await call(owner, ws, "POST", "/billing/report-checkout")
    session_id = r.json()["url"].rsplit("/", 1)[-1]
    unpaid = build_event(
        "checkout.session.completed",
        {
            "id": session_id,
            "mode": "payment",
            "payment_status": "unpaid",
            "client_reference_id": ws,
            "customer": await _customer(ws),
        },
        account=None,
    )
    assert await _post_event(client, unpaid) == "awaiting_payment"
    unknown_price = build_event(
        "customer.subscription.updated",
        {
            "id": "sub_y",
            "status": "active",
            "customer": await _customer(ws),
            "metadata": {"workspace_id": ws},
            "items": {"data": [{"price": {"id": "price_not_ours"}}]},
        },
        account=None,
    )
    assert await _post_event(client, unknown_price) == "unknown_price"
    assert (await subscription(owner, ws))["status"] == "incomplete"


async def test_live_mode_needs_seeded_prices(client: httpx.AsyncClient) -> None:
    from momentsales.core.config import ProviderMode, Settings
    from momentsales.core.errors import ProblemError
    from momentsales.db.billing import BillingInterval, Plan, PlanPrice
    from momentsales.modules.billing import service

    live = Settings().model_copy(update={"provider_mode": ProviderMode.LIVE})
    plan = Plan(code="growth", name="Growth")
    price = PlanPrice(currency="GBP", interval=BillingInterval.MONTH, amount_minor=7900)
    with pytest.raises(ProblemError, match="isn't set up"):
        service.price_ref(live, plan, price)
    price.stripe_price_id = "price_real"
    assert service.price_ref(live, plan, price) == "price_real"
