"""Database-level isolation (AC-19 groundwork): in workspace B's context, workspace A's
rows cannot be read, changed or deleted, and nothing can be written into A."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import pytest
from helpers import (
    add_member,
    create_workspace,
    seed_billing_rows,
    seed_phase1_rows,
    seed_research_rows,
    sign_in,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.base import TENANT_TABLES
from momentsales.db.ledger import APPEND_ONLY_TABLES


@dataclass
class Tenants:
    a: uuid.UUID
    b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID


@pytest.fixture
async def tenants(client: httpx.AsyncClient) -> Tenants:
    owner_a, owner_b = await sign_in(client), await sign_in(client)
    a = await create_workspace(owner_a, "Tenant A")
    b = await create_workspace(owner_b, "Tenant B")
    await add_member(owner_a, a, "analyst")  # creates invitations + more rows in A
    await seed_phase1_rows(uuid.UUID(a), uuid.UUID(owner_a.user_id))
    await seed_billing_rows(uuid.UUID(a), uuid.UUID(owner_a.user_id))
    await seed_research_rows(uuid.UUID(a), uuid.UUID(owner_a.user_id))
    return Tenants(
        uuid.UUID(a), uuid.UUID(b), uuid.UUID(owner_a.user_id), uuid.UUID(owner_b.user_id)
    )


def _ws_col(table: str) -> str:
    return "id" if table == "workspaces" else "workspace_id"


async def _count(table: str, ws_filter: uuid.UUID, ctx_ws: uuid.UUID, ctx_user: uuid.UUID) -> int:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=ctx_user, workspace_id=ctx_ws)
        n = await s.scalar(
            text(f"SELECT count(*) FROM {table} WHERE {_ws_col(table)} = :w"),  # noqa: S608
            {"w": str(ws_filter)},
        )
    return int(n or 0)


@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
async def test_cannot_read_other_tenant(table: str, tenants: Tenants) -> None:
    if table != "feature_flag_overrides":  # A has no overrides by default
        assert await _count(table, tenants.a, tenants.a, tenants.user_a) > 0, "fixture sanity"
    assert await _count(table, tenants.a, tenants.b, tenants.user_b) == 0


@pytest.mark.parametrize("table", sorted(TENANT_TABLES))
async def test_cannot_read_without_context(table: str, tenants: Tenants) -> None:
    async with get_sessionmaker()() as s, s.begin():
        n = await s.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    assert n == 0


@pytest.mark.parametrize("table", ["workspaces", "memberships", "invitations"])
async def test_cannot_update_other_tenant(table: str, tenants: Tenants) -> None:
    column = {"workspaces": "name", "memberships": "role", "invitations": "email"}[table]
    value = {"workspaces": "'Hacked'", "memberships": "'owner'", "invitations": "'x@evil.test'"}[
        table
    ]
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=tenants.user_b, workspace_id=tenants.b)
        result = await s.execute(
            text(f"UPDATE {table} SET {column} = {value} WHERE {_ws_col(table)} = :w"),  # noqa: S608
            {"w": str(tenants.a)},
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]


async def test_cannot_delete_other_tenant_invitations(tenants: Tenants) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=tenants.user_b, workspace_id=tenants.b)
        result = await s.execute(
            text("DELETE FROM invitations WHERE workspace_id = :w"), {"w": str(tenants.a)}
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]
    assert await _count("invitations", tenants.a, tenants.a, tenants.user_a) > 0


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO memberships (id, workspace_id, user_id, role, status, created_at, updated_at)"
        " VALUES (gen_random_uuid(), :a, :u, 'owner', 'active', now(), now())",
        "INSERT INTO touches (id, workspace_id, actor_user_id, category, description, minutes,"
        " auto, created_at) VALUES (gen_random_uuid(), :a, :u, 'pricing', 'x', 0, false, now())",
        "INSERT INTO invitations (id, workspace_id, email, role, token_hash, expires_at,"
        " invited_by, created_at, updated_at)"
        " VALUES (gen_random_uuid(), :a, 'x@evil.test', 'admin',"
        " md5(random()::text), now() + interval '1 day', :u, now(), now())",
        "INSERT INTO ledger_entries (id, workspace_id, type, amount_minor, currency, occurred_on,"
        " source, source_ref, description, created_at) VALUES (gen_random_uuid(), :a, 'sale',"
        " 100, 'GBP', current_date, 'manual', md5(random()::text), 'x', now())",
        "INSERT INTO spends (id, workspace_id, amount_minor, tax_minor, currency,"
        " base_amount_minor, spent_on, vendor, category, payment_method, source, created_by,"
        " created_at) VALUES (gen_random_uuid(), :a, 1, 0, 'GBP', 1, current_date, 'x', 'other',"
        " 'card', 'manual', :u, now())",
        "INSERT INTO orders (id, workspace_id, channel, external_id, status, currency,"
        " gross_minor, tax_minor, fees_minor, refunded_minor, created_at, updated_at) VALUES"
        " (gen_random_uuid(), :a, 'manual', md5(random()::text), 'paid', 'GBP', 1, 0, 0, 0,"
        " now(), now())",
        "INSERT INTO webhook_events (id, provider, event_id, type, livemode, workspace_id,"
        " payload, received_at, attempts) VALUES (gen_random_uuid(), 'stripe',"
        " md5(random()::text), 'x', false, :a, '{}', now(), 0)",
        "INSERT INTO subscriptions (id, workspace_id, plan_id, interval, currency, status,"
        " trial_used, cancel_at_period_end, trial_reminders_sent, created_at, updated_at)"
        " SELECT gen_random_uuid(), :a, id, 'month', 'GBP', 'active', false, false, '[]',"
        " now(), now() FROM plans WHERE code = 'pro'",
        "INSERT INTO run_credits (id, workspace_id, pack_runs, single_runs, created_at,"
        " updated_at) VALUES (gen_random_uuid(), :a, 1000, 0, now(), now())",
        "INSERT INTO saas_invoices (id, workspace_id, stripe_invoice_id, status, total_minor,"
        " tax_minor, currency, created_at, updated_at) VALUES (gen_random_uuid(), :a,"
        " md5(random()::text), 'paid', 1, 0, 'GBP', now(), now())",
    ],
)
async def test_cannot_insert_into_other_tenant(statement: str, tenants: Tenants) -> None:
    async with get_sessionmaker()() as s:
        await s.begin()
        await set_tenant_context(s, user_id=tenants.user_b, workspace_id=tenants.b)
        with pytest.raises(DBAPIError, match="row-level security"):
            await s.execute(text(statement), {"a": str(tenants.a), "u": str(tenants.user_b)})
        await s.rollback()


async def test_member_lists_only_own_workspaces(tenants: Tenants) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=tenants.user_b)
        ids = set((await s.scalars(text("SELECT id FROM workspaces"))).all())
    assert ids == {tenants.b}


@pytest.mark.parametrize("table", sorted(APPEND_ONLY_TABLES))
@pytest.mark.parametrize(
    "statement", ["UPDATE {t} SET workspace_id = workspace_id", "DELETE FROM {t}"]
)
async def test_append_only_tables_refuse_changes(
    table: str, statement: str, tenants: Tenants
) -> None:
    """Own-tenant rows can't be changed either: no grant, no policy, and a trigger (ADR-0010)."""
    async with get_sessionmaker()() as s:
        await s.begin()
        await set_tenant_context(s, user_id=tenants.user_a, workspace_id=tenants.a)
        with pytest.raises(DBAPIError, match=r"permission denied|append-only"):
            await s.execute(text(statement.format(t=table)))
        await s.rollback()
    assert await _count(table, tenants.a, tenants.a, tenants.user_a) > 0


async def test_unmatched_webhook_events_need_platform_context(tenants: Tenants) -> None:
    async def count(**ctx: object) -> int:
        async with get_sessionmaker()() as s, s.begin():
            await set_tenant_context(s, **ctx)  # type: ignore[arg-type]
            n = await s.scalar(
                text("SELECT count(*) FROM webhook_events WHERE workspace_id = :w"),
                {"w": str(tenants.a)},
            )
        return int(n or 0)

    assert await count(user_id=tenants.user_a, workspace_id=tenants.a) == 1
    assert await count(user_id=tenants.user_b, workspace_id=tenants.b) == 0
    assert await count(platform=True) == 1


async def test_platform_reads_billing_across_tenants_but_cannot_write(tenants: Tenants) -> None:
    """Billing sweep and admin API (ADR-0016): platform context reads, never writes."""
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, platform=True)
        for table in ("subscriptions", "usage_counters", "addon_purchases", "cancel_surveys"):
            n = await s.scalar(
                text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                {"w": str(tenants.a)},
            )
            assert n == 1, table
        assert await s.scalar(
            text("SELECT count(*) FROM workspaces WHERE id = :w"), {"w": str(tenants.a)}
        )
        # Not readable from platform context: run credits and invoices stay tenant-only
        assert not await s.scalar(
            text("SELECT count(*) FROM run_credits WHERE workspace_id = :w"),
            {"w": str(tenants.a)},
        )
    async with get_sessionmaker()() as s:
        await s.begin()
        await set_tenant_context(s, platform=True)
        # Updates see no rows (only a SELECT policy applies) ...
        updated = await s.execute(
            text("UPDATE subscriptions SET status = 'canceled' WHERE workspace_id = :w"),
            {"w": str(tenants.a)},
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        # ... and inserts are refused outright.
        with pytest.raises(DBAPIError, match="row-level security"):
            await s.execute(
                text(
                    "INSERT INTO run_credits (id, workspace_id, pack_runs, single_runs,"
                    " created_at, updated_at) VALUES (gen_random_uuid(), :w, 5, 0, now(), now())"
                ),
                {"w": str(tenants.a)},
            )
        await s.rollback()
