"""Phase 2d Top 10 report (ADR-0021): scoring, compliance, the report itself (AC-1, AC-3),
rankings (AC-21, AC-25), exports, share links and isolation (AC-19)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from helpers import Session, add_member, create_workspace, sign_in
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from momentsales.adapters.llm.base import LlmError
from momentsales.adapters.llm.mock import MockLlmAdapter
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.research import SourceSnapshot

API = "/api/v1"
UK_6_NOV = {"country": "GB", "as_of": "2026-11-06"}


async def owner_sql(sql: str) -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean(client: httpx.AsyncClient) -> AsyncIterator[None]:
    await owner_sql("DELETE FROM market_data_cache")
    await owner_sql("DELETE FROM supplier_verifications")
    r = await client.post(f"{API}/dev/mock-suppliers/verify")
    assert r.status_code == 200
    yield


async def new_shop(client: httpx.AsyncClient) -> tuple[Session, str]:
    owner = await sign_in(client)
    return owner, await create_workspace(owner, "Report Shop", plan="pro")


async def run_in(owner: Session, ws: str, **body: Any) -> dict[str, Any]:
    r = await owner.client.post(
        f"{API}/research-runs", json={**UK_6_NOV, **body}, headers=owner.headers(ws)
    )
    assert r.status_code == 201, r.text
    got = await owner.client.get(f"{API}/research-runs/{r.json()['id']}", headers=owner.headers(ws))
    return dict(got.json())


async def report_of(owner: Session, ws: str, run: dict[str, Any]) -> dict[str, Any]:
    report_id = run["demand"]["report_id"]
    assert report_id, run["steps"]
    r = await owner.client.get(f"{API}/reports/{report_id}", headers=owner.headers(ws))
    assert r.status_code == 200, r.text
    return dict(r.json())


def step(run: dict[str, Any], agent: str) -> dict[str, Any]:
    return next(s for s in run["steps"] if s["agent"] == agent)


async def test_ac1_uk_report_has_ten_complete_items(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    run = await run_in(owner, ws, category="home")
    assert run["status"] == "completed", run
    assert [s["status"] for s in run["steps"][-3:]] == ["completed"] * 3, run["steps"]
    report = await report_of(owner, ws, run)
    items = report["items"]
    assert len(items) == 10 and [i["rank"] for i in items] == list(range(1, 11))
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    for i in items:
        assert i["why"] and i["why_source"] in {"llm", "template"}
        assert len(i["offers"]) >= 2 and all(o["url"] for o in i["offers"])
        assert i["best_offer"]["price_minor"] == min(o["price_minor"] for o in i["offers"])
        assert isinstance(i["sizes"], list)  # "not available" when empty
        assert i["compliance"]["status"] in {"ok", "needs_documents"}
        assert set(i["score_breakdown"]["parts"]) == {
            "rising",
            "competition",
            "seasonal",
            "profit",
            "supplier",
        }
        assert i["score_breakdown"]["dropped"] == ["sales_rank"]
    matched = [i for i in items if (i["supplier"] or {}).get("status") == "matched"]
    assert matched
    for i in matched:
        m = i["supplier"]["match"]
        assert i["image_url"] and m["delivery_max_days"] is not None
        assert i["profit"]["profit_minor"] < i["profit"]["break_even_ad_cost_minor"]
        assert i["profit"]["sell_price_source"] == "market_median_estimate"
    # Electrical goods need safety documents before they can be listed.
    heated = next(i for i in items if i["keyword"] == "heated throw")
    assert heated["compliance"]["status"] == "needs_documents"
    assert "needs_documents" in heated["flags"]
    summary = report["summary"]
    assert summary["mock"] and "sales rank is not scored" in summary["method"]
    assert summary["season"]["current"] == "autumn" and summary["sources"]
    assert summary["cost_minor"] == run["cost_minor"]


async def test_ac3_report_facts_trace_to_snapshots(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    run = await run_in(owner, ws, category="home")
    report = await report_of(owner, ws, run)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=uuid.UUID(ws))
        ids = {
            str(i)
            for i in (
                await s.scalars(select(SourceSnapshot.id).where(SourceSnapshot.run_id == run["id"]))
            ).all()
        }
    for i in report["items"]:
        refs = [o["snapshot_id"] for o in i["offers"]] + [i["trend"]["snapshot_id"]]
        if i["market"]:
            refs.append(i["market"]["snapshot_id"])
        if (i["supplier"] or {}).get("match"):
            refs += [
                i["supplier"]["match"]["search_snapshot_id"],
                i["supplier"]["match"]["freight_snapshot_id"],
            ]
        assert set(refs) <= ids


async def test_ac21_ac25_late_and_crowded_rank_below_equal_peers(
    client: httpx.AsyncClient,
) -> None:
    owner, ws = await new_shop(client)
    run = await run_in(owner, ws, category="home")
    found = {c["keyword"]: c for c in run["demand"]["candidates"]}
    # electric blanket: rising but its mock supplier takes 45-60 days (too late).
    late = found["electric blanket"]
    assert late["supplier"]["arrives_too_late"] is True
    assert late["score"]["parts"]["seasonal"] == 0
    ranking = run["demand"]["ranking"]
    assert ranking.index("heated throw") < ranking.index("electric blanket")


async def test_compliance_veto_excludes_and_replaces(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A product the classifier marks restricted is vetoed; the report still fills."""
    original = MockLlmAdapter.complete_json

    async def flag_heated(self: MockLlmAdapter, **kw: Any) -> Any:
        result = await original(self, **kw)
        if kw["task"] == "compliance":
            for p in result.data["products"]:
                if p["id"] == "0":
                    p["restricted"] = True
                    p["reason"] = "test veto"
        return result

    monkeypatch.setattr(MockLlmAdapter, "complete_json", flag_heated)
    owner, ws = await new_shop(client)
    run = await run_in(owner, ws, category="home")
    top_ranked = run["demand"]["ranking"][0]
    report = await report_of(owner, ws, run)
    assert top_ranked not in [i["keyword"] for i in report["items"]]
    assert report["excluded"][0]["keyword"] == top_ranked
    assert "restricted_item" in report["excluded"][0]["flags"]
    assert len(report["items"]) == 10


async def test_llm_down_uses_rules_and_templates(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: MockLlmAdapter, **kw: Any) -> Any:
        raise LlmError("claude", "HTTP 529", retryable=True)

    monkeypatch.setattr(MockLlmAdapter, "complete_json", down)
    owner, ws = await new_shop(client)
    run = await run_in(owner, ws, category="home")
    assert run["status"] == "completed"
    assert step(run, "compliance")["status"] == "degraded"
    assert step(run, "report")["status"] == "degraded"
    report = await report_of(owner, ws, run)
    assert report["summary"]["why_source"] == "template"
    assert all(i["why_source"] == "template" for i in report["items"])
    heated = next(i for i in report["items"] if i["keyword"] == "heated throw")
    assert heated["compliance"]["source"] == "rules"
    assert heated["compliance"]["status"] == "needs_documents"


async def test_invented_numbers_in_reasons_are_replaced(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = MockLlmAdapter.complete_json

    async def invent(self: MockLlmAdapter, **kw: Any) -> Any:
        result = await original(self, **kw)
        if kw["task"] == "reasons":
            result.data["items"][0]["why"] = "Sells for £19.99 and ships in 1 day."
        return result

    monkeypatch.setattr(MockLlmAdapter, "complete_json", invent)
    owner, ws = await new_shop(client)
    report = await report_of(owner, ws, await run_in(owner, ws, category="home"))
    first = report["items"][0]
    assert first["why_source"] == "template" and "19.99" not in first["why"]
    assert all(i["why_source"] == "llm" for i in report["items"][1:])


async def test_exports_csv_and_pdf(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    report = await report_of(owner, ws, await run_in(owner, ws, category="home"))
    r = await owner.client.get(
        f"{API}/reports/{report['id']}/export.csv", headers=owner.headers(ws)
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 10 and rows[0]["product"] == report["items"][0]["keyword"]
    assert rows[0]["purchase_links"].startswith("https://")
    r = await owner.client.get(
        f"{API}/reports/{report['id']}/export.pdf", headers=owner.headers(ws)
    )
    if r.status_code == 503:
        assert r.json()["type"] == "pdf_unavailable"
        pytest.skip("WeasyPrint native libraries are not installed here")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


async def test_share_links(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    report = await report_of(owner, ws, await run_in(owner, ws, category="home"))
    base = f"{API}/reports/{report['id']}/shares"
    r = await owner.client.post(base, json={"days": 7}, headers=owner.headers(ws))
    assert r.status_code == 201, r.text
    share = r.json()
    token = share["url"].rsplit("/r/", 1)[1]
    public = await client.get(f"{API}/public/reports/{token}")
    assert public.status_code == 200 and public.headers["cache-control"] == "no-store"
    body = public.json()
    assert len(body["items"]) == 10
    assert "cost_minor" not in body["summary"] and "audience" not in body["summary"]["params"]
    listed = await owner.client.get(base, headers=owner.headers(ws))
    assert listed.json()[0]["active"] and "url" not in listed.json()[0]
    assert (
        await owner.client.post(base, json={"days": 400}, headers=owner.headers(ws))
    ).status_code == 422

    r = await owner.client.delete(f"{base}/{share['id']}", headers=owner.headers(ws))
    assert r.status_code == 204
    assert (await client.get(f"{API}/public/reports/{token}")).status_code == 404
    assert (await client.get(f"{API}/public/reports/not-a-token")).status_code == 404

    # Expired links stop working.
    r = await owner.client.post(base, json={}, headers=owner.headers(ws))
    token2 = r.json()["url"].rsplit("/r/", 1)[1]
    assert (await client.get(f"{API}/public/reports/{token2}")).status_code == 200
    past = datetime.now(UTC) - timedelta(minutes=1)
    async with get_sessionmaker()() as session, session.begin():  # RLS: the share's workspace
        await set_tenant_context(session, workspace_id=uuid.UUID(ws))
        await session.execute(
            text("UPDATE report_shares SET expires_at = :t WHERE id = :i"),
            {"t": past, "i": r.json()["id"]},
        )
    assert (await client.get(f"{API}/public/reports/{token2}")).status_code == 404


async def test_share_permissions(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    report = await report_of(owner, ws, await run_in(owner, ws, category="home"))
    analyst = await add_member(owner, ws, "analyst")
    accountant = await add_member(owner, ws, "accountant")
    base = f"{API}/reports/{report['id']}/shares"
    assert (
        await analyst.client.post(base, json={}, headers=analyst.headers(ws))
    ).status_code == 201
    assert (
        await accountant.client.post(base, json={}, headers=accountant.headers(ws))
    ).status_code == 403
    assert (
        await accountant.client.get(f"{API}/reports/{report['id']}", headers=accountant.headers(ws))
    ).status_code == 403


async def test_ac19_reports_are_isolated(client: httpx.AsyncClient) -> None:
    owner, ws = await new_shop(client)
    report = await report_of(owner, ws, await run_in(owner, ws, category="home"))
    other, other_ws = await new_shop(client)
    h = other.headers(other_ws)
    assert (await other.client.get(f"{API}/reports/{report['id']}", headers=h)).status_code == 404
    assert (
        await other.client.get(f"{API}/reports/{report['id']}/export.csv", headers=h)
    ).status_code == 404
    assert (
        await other.client.post(f"{API}/reports/{report['id']}/shares", json={}, headers=h)
    ).status_code == 404
    assert (await other.client.get(f"{API}/reports", headers=h)).json() == []
    mine = await owner.client.get(f"{API}/reports", headers=owner.headers(ws))
    assert [r["id"] for r in mine.json()] == [report["id"]] and mine.json()[0]["item_count"] == 10
