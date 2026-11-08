"""Plan limits, run metering and the subscription access gate (ADR-0015, ADR-0016)."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from helpers import create_workspace, set_subscription, sign_in, unique_email

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.billing import RunPool
from momentsales.modules.billing import entitlements as ent
from momentsales.modules.entitlements.service import Limit, PlanLimitReached
from momentsales.tenancy.permissions import Action

SPEND = {
    "amount": "5.00",
    "currency": "GBP",
    "spent_on": "2026-11-01",
    "vendor": "Royal Mail",
    "category": "shipping",
    "payment_method": "card",
}


async def _ws(client: httpx.AsyncClient, **kw: object) -> tuple[uuid.UUID, object]:
    owner = await sign_in(client)
    ws = await create_workspace(owner, **kw)  # type: ignore[arg-type]
    return uuid.UUID(ws), owner


async def consume(ws: uuid.UUID, key: str) -> RunPool:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        return await ent.consume_run(s, ws, key)


async def refund(ws: uuid.UUID, key: str) -> bool:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        return await ent.refund_run(s, ws, key)


async def grant(ws: uuid.UUID, *, pack: int = 0, single: int = 0) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        await ent.grant_runs(s, ws, pack=pack, single=single)


def test_every_action_is_classified_for_the_gate() -> None:
    """A new write action must be added to GATED_ACTIONS or to an explicit exemption."""
    reads = {a for a in Action if a.value.endswith((".read", ".export"))}
    exempt = {
        Action.WORKSPACE_UPDATE,
        Action.WORKSPACE_DELETE,
        Action.MEMBERS_REMOVE,
        Action.BILLING_MANAGE,
        Action.FULFILMENT_APPROVE,
        Action.RESEARCH_RUN,  # metered by consume_run (pay-per-report needs no plan)
    }
    assert ent.GATED_ACTIONS | reads | exempt == set(Action)
    assert not ent.GATED_ACTIONS & (reads | exempt)


async def test_no_subscription_blocks_writes_not_reads(client: httpx.AsyncClient) -> None:
    ws, owner = await _ws(client, plan=None)
    h = owner.headers(str(ws))  # type: ignore[attr-defined]
    r = await owner.client.post("/api/v1/spends", json=SPEND, headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 403 and r.json()["type"] == "subscription_required"
    r = await owner.client.get("/api/v1/spends", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    # Workspace settings stay editable
    r = await owner.client.patch(  # type: ignore[attr-defined]
        f"/api/v1/workspaces/{ws}", json={"name": "Renamed"}, headers=h
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("status", ["past_due", "suspended", "paused"])
async def test_read_only_states(client: httpx.AsyncClient, status: str) -> None:
    ws, owner = await _ws(client, status=status)
    h = owner.headers(str(ws))  # type: ignore[attr-defined]
    r = await owner.client.post("/api/v1/spends", json=SPEND, headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 403
    assert r.json()["type"] == "subscription_read_only"
    assert r.json()["subscription_status"] == status
    with pytest.raises(Exception, match="read-only"):
        await consume(ws, "run-1")


async def test_active_plan_allows_writes(client: httpx.AsyncClient) -> None:
    ws, owner = await _ws(client, plan="starter")
    h = owner.headers(str(ws)) | {"Idempotency-Key": "spend-1"}  # type: ignore[attr-defined]
    r = await owner.client.post("/api/v1/spends", json=SPEND, headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 201, r.text


async def test_starter_runs_then_pack_then_402(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan="starter")
    for i in range(10):
        assert await consume(ws, f"run-{i}") is RunPool.PLAN
    with pytest.raises(PlanLimitReached) as err:
        await consume(ws, "run-10")
    assert err.value.status == 402
    assert err.value.extra == {
        "limit": "research_runs",
        "used": 10,
        "allowed": 10,
        "upgrade_url": "/app/billing",
    }
    await grant(ws, pack=10)
    assert await consume(ws, "run-10") is RunPool.PACK
    # Idempotent: the same key doesn't spend twice
    assert await consume(ws, "run-10") is RunPool.PACK
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        usage = await ent.run_usage(s, ws)
    assert (usage.plan_used, usage.plan_allowed, usage.pack_runs) == (10, 10, 9)
    # Refund returns the run to the pool it came from, once
    assert await refund(ws, "run-10") is True
    assert await refund(ws, "run-10") is False
    assert await refund(ws, "run-3") is True
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        usage = await ent.run_usage(s, ws)
    assert (usage.plan_used, usage.pack_runs) == (9, 10)


async def test_trial_is_capped_at_three_runs(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan="growth", status="trialing")
    for i in range(3):
        await consume(ws, f"t-{i}")
    with pytest.raises(PlanLimitReached):
        await consume(ws, "t-3")


async def test_annual_plans_get_two_bonus_runs(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan="lite")
    await set_subscription(ws, plan="lite", interval="year")
    for i in range(5):  # Lite: 3 + 2 annual bonus
        await consume(ws, f"a-{i}")
    with pytest.raises(PlanLimitReached):
        await consume(ws, "a-5")


async def test_single_report_runs_need_no_subscription(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan=None)
    with pytest.raises(PlanLimitReached):
        await consume(ws, "r-0")
    await grant(ws, single=1, pack=5)
    assert await consume(ws, "r-0") is RunPool.SINGLE
    with pytest.raises(PlanLimitReached):  # packs need an active plan
        await consume(ws, "r-1")


async def test_concurrent_runs_cannot_overspend(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan="lite")
    await consume(ws, "c-0")
    await consume(ws, "c-1")
    results = await asyncio.gather(
        consume(ws, "c-2"), consume(ws, "c-3"), consume(ws, "c-4"), return_exceptions=True
    )
    assert sum(1 for r in results if r is RunPool.PLAN) == 1
    assert sum(1 for r in results if isinstance(r, PlanLimitReached)) == 2


async def test_seat_limit_on_invite(client: httpx.AsyncClient) -> None:
    ws, owner = await _ws(client, plan="starter")  # 1 seat: the owner
    r = await owner.client.post(  # type: ignore[attr-defined]
        "/api/v1/invitations",
        json={"email": unique_email("seat"), "role": "admin"},
        headers=owner.headers(str(ws)),  # type: ignore[attr-defined]
    )
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["type"] == "plan_limit_reached" and body["limit"] == "seats"
    assert (body["used"], body["allowed"]) == (1, 1)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        usage = await ent.PlanPolicy().usage(s, ws, Limit.LIVE_LISTINGS)
    assert (usage.used, usage.allowed) == (0, 25)


async def _listing_allowance(ws: uuid.UUID) -> int | None:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        return (await ent.PlanPolicy().usage(s, ws, Limit.LIVE_LISTINGS)).allowed


async def test_trial_is_capped_at_five_live_listings(client: httpx.AsyncClient) -> None:
    ws, _ = await _ws(client, plan="growth", status="trialing")
    assert await _listing_allowance(ws) == 5
    await set_subscription(ws, plan="growth", status="active")
    assert await _listing_allowance(ws) == 100


async def _fee_bps(ws: uuid.UUID) -> int:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        return await ent.platform_fee_bps(s, ws)


@pytest.mark.parametrize(
    ("plan", "status", "bps"),
    [
        ("lite", "active", 200),
        ("starter", "active", 0),
        ("growth", "active", 0),
        ("pro", "active", 0),
        ("growth", "trialing", 0),
        (None, "active", 200),  # pay-per-report: no subscription
        ("starter", "past_due", 200),  # no active plan
    ],
)
async def test_platform_fee_rate(
    client: httpx.AsyncClient, plan: str | None, status: str, bps: int
) -> None:
    ws, _ = await _ws(client, plan=plan, status=status)
    assert await _fee_bps(ws) == bps


def test_platform_fee_rounds_down() -> None:
    assert ent.platform_fee_minor(2500, 200) == 50
    assert ent.platform_fee_minor(1249, 200) == 24  # 24.98p -> 24p, never over-charged
    assert ent.platform_fee_minor(2500, 0) == 0
