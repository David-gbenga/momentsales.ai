"""Phase 2c supplier matching (ADR-0020): the Supplier agent in real runs, the Verified UK
supplier rule (AC-20), arrives too late (AC-21 data part), the supplier switch without
automatic failover (AC-34), Avasam per seller, the shared CJ cache, admin evidence and the
season calendar's supplier delivery time (AC-26)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from helpers import Session, create_workspace, sign_in
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from momentsales.adapters.supplier.base import SupplierError
from momentsales.adapters.supplier.mock import MockSupplierAdapter
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.ledger import Integration
from momentsales.db.research import AgentStep, MarketDataCache, SourceSnapshot

API = "/api/v1"
UK_6_NOV = {"country": "GB", "as_of": "2026-11-06"}
STATUSES = {"matched", "no_verified_supplier", "no_supplier", "not_checked", "not_available"}


async def owner_sql(sql: str) -> None:
    """Append-only tables: only the owner role can clear them between tests."""
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean() -> AsyncIterator[None]:
    await owner_sql("DELETE FROM market_data_cache")
    await owner_sql("DELETE FROM supplier_verifications")
    yield


async def verify_mock_suppliers(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{API}/dev/mock-suppliers/verify")
    assert r.status_code == 200, r.text


async def new_shop(client: httpx.AsyncClient) -> tuple[Session, str]:
    owner = await sign_in(client)
    return owner, await create_workspace(owner, "Supplier Shop", plan="pro")


async def run_in(owner: Session, ws: str, **body: Any) -> dict[str, Any]:
    r = await owner.client.post(
        f"{API}/research-runs", json={**UK_6_NOV, **body}, headers=owner.headers(ws)
    )
    assert r.status_code == 201, r.text
    got = await owner.client.get(f"{API}/research-runs/{r.json()['id']}", headers=owner.headers(ws))
    return dict(got.json())


async def run_once(client: httpx.AsyncClient, **body: Any) -> tuple[Session, str, dict[str, Any]]:
    owner, ws = await new_shop(client)
    return owner, ws, await run_in(owner, ws, **body)


def by_keyword(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["keyword"]: c for c in run["demand"]["candidates"]}


def step(run: dict[str, Any], agent: str) -> dict[str, Any]:
    return next(s for s in run["steps"] if s["agent"] == agent)


async def snapshots(ws: str, run_id: str) -> dict[str, SourceSnapshot]:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        rows = (
            await s.scalars(select(SourceSnapshot).where(SourceSnapshot.run_id == run_id))
        ).all()
    return {str(r.id): r for r in rows}


async def supplier_step(ws: str, run_id: str) -> AgentStep:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        row = await s.scalar(
            select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.agent == "supplier")
        )
    assert row is not None
    return row


async def cache_types() -> set[str]:
    async with get_sessionmaker()() as s:
        return set((await s.scalars(select(MarketDataCache.data_type))).all())


async def test_ac20_uk_run_matches_verified_uk_suppliers(client: httpx.AsyncClient) -> None:
    await verify_mock_suppliers(client)
    _, ws, run = await run_once(client, category="home")
    assert run["status"] == "completed", run
    assert step(run, "supplier")["status"] == "completed"
    demand = run["demand"]
    assert demand["supplier_available"] is True and demand["supplier_provider"] == "cj"
    found = by_keyword(run)
    statuses = {c["supplier"]["status"] for c in demand["candidates"]}
    assert statuses <= STATUSES

    heated = found["heated throw"]["supplier"]
    assert heated["status"] == "matched", heated
    m = heated["match"]
    assert m["provider"] == "cj" and m["warehouse_country"] == "GB" and m["uk_stock"] > 0
    assert m["unit_cost"]["currency"] == "USD" and m["unit_cost_gbp_minor"] > 0
    assert m["shipping_gbp_minor"] > 0 and m["shipping"]["name"] == "Mock Tracked 48"
    assert (m["delivery_min_days"], m["delivery_max_days"]) == (2, 4)
    assert m["landed_cost_minor"] == m["unit_cost_gbp_minor"] + m["shipping_gbp_minor"]
    assert m["fx"]["rate"] == "0.75" and m["fx"]["base"] == "USD"
    assert m["margin"]["result"] == "passed" and m["margin"]["margin_bps"] >= 2500
    assert m["verified"] and set(m["checks"].values()) == {"passed"}
    assert heated["arrives_too_late"] is False

    # AC-3: the search, freight and FX numbers link to this run's own snapshots.
    snaps = await snapshots(ws, run["id"])
    for ref in (m["search_snapshot_id"], m["freight_snapshot_id"], m["fx"]["snapshot_id"]):
        assert snaps[ref].url and snaps[ref].payload and snaps[ref].fetched_at
    assert snaps[m["search_snapshot_id"]].kind == "supplier_search"

    # AC-21 (data part): a 45-60 day supplier misses Christmas.
    blanket = found["electric blanket"]["supplier"]
    assert blanket["arrives_too_late"] is True and blanket["match"]["delivery_max_days"] == 60

    # Searches beyond the top N are not checked, never guessed.
    top_n = get_settings().research_supplier_top_n
    checked = [c for c in demand["candidates"] if c["supplier"]["status"] != "not_checked"]
    assert len(checked) <= top_n


async def test_failing_checks_flag_no_verified_supplier(client: httpx.AsyncClient) -> None:
    """With no recorded evidence every check 1-4 fails: nothing can be matched."""
    _, _, run = await run_once(client)
    found = by_keyword(run)
    heated = found["heated throw"]["supplier"]
    assert heated["status"] == "no_verified_supplier"
    assert "dropship_no_moq:failed" in heated["reasons"]
    assert heated["match"]["margin"]["result"] == "passed"  # only the evidence is missing
    assert not any(c["supplier"]["status"] == "matched" for c in run["demand"]["candidates"])


async def test_untracked_and_low_margin_items_are_not_verified(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_mock_suppliers(client)
    from momentsales.adapters.supplier import mock

    monkeypatch.setitem(mock.CASES, "heated throw", "untracked")
    monkeypatch.setitem(mock.CASES, "electric blanket", "low_margin")
    monkeypatch.setattr(mock, "_DEFAULT", ("no_uk_stock",))  # every other search
    _, _, run = await run_once(client, category="home")
    found = by_keyword(run)
    untracked = found["heated throw"]["supplier"]
    assert untracked["status"] == "no_verified_supplier"
    assert "tracked_uk:failed" in untracked["reasons"]
    low = found["electric blanket"]["supplier"]
    assert low["status"] == "no_verified_supplier" and "margin:failed" in low["reasons"]
    assert low["match"]["margin"]["margin_bps"] < 2500
    others = [
        c["supplier"]
        for k, c in found.items()
        if k not in mock.CASES and c["supplier"]["status"] != "not_checked"
    ]
    assert others
    assert all(o["status"] == "no_supplier" and o["reasons"] == ["no_uk_stock"] for o in others)


async def test_ac34_switch_to_avasam_per_seller(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-34 (supplier half): the same flow runs with PROVIDER_SUPPLIER=avasam. Avasam uses
    the seller's own keys and is never written to the shared cache."""
    await verify_mock_suppliers(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_supplier", "avasam")
    monkeypatch.setattr(settings, "provider_supplier_secondary", "cj")
    owner, ws = await new_shop(client)

    # Not connected: the step is degraded and the run still completes.
    run = await run_in(owner, ws)
    assert run["status"] == "completed"
    assert step(run, "supplier")["status"] == "degraded"
    assert "Connect Avasam" in step(run, "supplier")["summary"]
    assert {c["supplier"]["status"] for c in run["demand"]["candidates"]} == {"not_available"}

    r = await owner.client.post(
        f"{API}/integrations/avasam/connect",
        json={"consumer_key": "ck-test", "secret_key": "sk-test-secret"},
        headers=owner.headers(ws),
    )
    assert r.status_code == 200 and r.json()["connected"] is True, r.text
    await owner_sql("DELETE FROM market_data_cache")
    run = await run_in(owner, ws)
    heated = by_keyword(run)["heated throw"]["supplier"]
    assert run["demand"]["supplier_provider"] == "avasam"
    assert heated["match"]["provider"] == "avasam"
    assert heated["match"]["unit_cost"]["currency"] == "GBP" and heated["match"]["fx"] is None
    # Avasam's API has no shipping cost: the margin cannot be checked, so not verified.
    assert heated["status"] == "no_verified_supplier"
    assert heated["match"]["margin"]["result"] == "not_checked"
    assert heated["match"]["shipping"]["source"] == "published"
    assert not (await cache_types()) & {"supplier_search", "supplier_freight"}


async def test_supplier_error_does_not_fail_over(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: MockSupplierAdapter, *args: object, **kwargs: object) -> None:
        raise SupplierError(self.name, "HTTP 503", retryable=True)

    monkeypatch.setattr(MockSupplierAdapter, "search", down)
    _, ws, run = await run_once(client)
    assert run["status"] == "completed"
    assert step(run, "supplier")["status"] == "degraded"
    checked = [c for c in run["demand"]["candidates"] if c["supplier"]["status"] != "not_checked"]
    assert checked and {c["supplier"]["status"] for c in checked} == {"not_available"}
    row = await supplier_step(ws, run["id"])
    assert not any(c.get("provider") == "avasam" for c in row.tool_calls)


async def test_cj_results_are_shared_across_workspaces(client: httpx.AsyncClient) -> None:
    await verify_mock_suppliers(client)
    _, ws_a, first = await run_once(client)
    _, ws_b, second = await run_once(client)
    assert by_keyword(second)["heated throw"]["supplier"]["status"] == "matched"
    calls = (await supplier_step(ws_b, second["id"])).tool_calls
    supplier_calls = [c for c in calls if c["data_type"].startswith("supplier_")]
    assert supplier_calls and all(c["cache_hit"] for c in supplier_calls)
    # Each run keeps its own snapshots.
    a = by_keyword(first)["heated throw"]["supplier"]["match"]["search_snapshot_id"]
    b = by_keyword(second)["heated throw"]["supplier"]["match"]["search_snapshot_id"]
    assert a != b and b in await snapshots(ws_b, second["id"])
    assert a not in await snapshots(ws_b, second["id"])
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(MarketDataCache))).all()
    for row in rows:
        blob = str(row.result) + str(row.query)
        assert ws_a not in blob and ws_b not in blob


async def _make_platform_admin(email: str) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            text("UPDATE users SET is_platform_admin = true WHERE email = :e"), {"e": email}
        )


async def test_verifications_are_admin_only_and_append_only(client: httpx.AsyncClient) -> None:
    body = {
        "provider": "cj",
        "check": "uk_returns",
        "passed": True,
        "evidence_url": "https://example.com/cj-returns",
        "evidence_note": "UK returns address confirmed by email",
    }
    seller, ws = await new_shop(client)
    r = await seller.client.post(
        f"{API}/admin/supplier-verifications", json=body, headers=seller.headers(ws)
    )
    assert r.status_code == 403
    staff = await sign_in(client)
    await _make_platform_admin(staff.email)
    r = await staff.client.post(
        f"{API}/admin/supplier-verifications", json=body, headers=staff.headers()
    )
    assert r.status_code == 201, r.text
    assert r.json()["verified_by"] == staff.user_id
    r = await staff.client.post(
        f"{API}/admin/supplier-verifications",
        json={**body, "provider": "nope"},
        headers=staff.headers(),
    )
    assert r.status_code == 404
    listed = await staff.client.get(
        f"{API}/admin/supplier-verifications?provider=cj", headers=staff.headers()
    )
    assert [v["check"] for v in listed.json()] == ["uk_returns"]
    suppliers = await staff.client.get(f"{API}/admin/suppliers", headers=staff.headers())
    assert {s["provider"] for s in suppliers.json()} == {"cj", "avasam"}

    with pytest.raises(DBAPIError):
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text("UPDATE supplier_verifications SET passed = false"))
    with pytest.raises(DBAPIError):
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(text("DELETE FROM supplier_verifications"))


async def test_avasam_keys_are_encrypted_scoped_and_never_returned(
    client: httpx.AsyncClient,
) -> None:
    owner, ws = await new_shop(client)
    secret = "sk-very-secret-value"
    r = await owner.client.post(
        f"{API}/integrations/avasam/connect",
        json={"consumer_key": "ck-1", "secret_key": secret},
        headers=owner.headers(ws),
    )
    assert r.status_code == 200 and secret not in r.text
    status = await owner.client.get(f"{API}/integrations/avasam", headers=owner.headers(ws))
    assert status.json()["connected"] is True and secret not in status.text
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        row = await s.scalar(select(Integration).where(Integration.type == "avasam"))
    assert row is not None and row.credentials_encrypted
    assert secret not in row.credentials_encrypted and "ck-1" not in row.credentials_encrypted

    # Another workspace cannot see it (RLS) and starts disconnected.
    other_owner, other_ws = await new_shop(client)
    other = await other_owner.client.get(
        f"{API}/integrations/avasam", headers=other_owner.headers(other_ws)
    )
    assert other.json()["connected"] is False

    r = await owner.client.delete(f"{API}/integrations/avasam", headers=owner.headers(ws))
    assert r.status_code == 204
    status = await owner.client.get(f"{API}/integrations/avasam", headers=owner.headers(ws))
    assert status.json()["connected"] is False
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        row = await s.scalar(select(Integration).where(Integration.type == "avasam"))
    assert row is not None and row.credentials_encrypted is None


async def test_ac26_calendar_uses_matched_supplier_delivery(client: httpx.AsyncClient) -> None:
    await verify_mock_suppliers(client)
    owner, ws = await new_shop(client)
    url = f"{API}/season-calendar?as_of=2026-11-06"
    before = (await owner.client.get(url, headers=owner.headers(ws))).json()
    assert before["delivery_source"] == "assumed"
    run = await run_in(owner, ws)
    days = sorted(
        c["supplier"]["match"]["delivery_max_days"]
        for c in run["demand"]["candidates"]
        if c["supplier"]["status"] == "matched"
    )
    assert days
    after = (await owner.client.get(url, headers=owner.headers(ws))).json()
    assert after["delivery_source"] == "supplier"
    assert after["delivery_days"] == days[(len(days) - 1) // 2]
    # The act-by dates move with the delivery time.
    if after["delivery_days"] != before["delivery_days"]:
        assert after["moments"][0]["act_by"] != before["moments"][0]["act_by"]
