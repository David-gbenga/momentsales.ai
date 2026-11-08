"""Test helpers: sign up through the real magic-link flow and read the mock outbox."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import httpx

from momentsales.adapters.email.mock import read_outbox
from momentsales.core.config import get_settings
from momentsales.core.redis import get_redis

XRW = {"X-Requested-With": "momentsales"}


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


async def last_email_to(address: str) -> dict[str, object]:
    for msg in await read_outbox(get_redis(get_settings().redis_url), 200):
        if str(msg["to"]).lower() == address.lower():
            return msg
    raise AssertionError(f"no email to {address}")


async def link_token(address: str, path: str) -> str:
    msg = await last_email_to(address)
    match = re.search(rf"{re.escape(path)}\?token=([^\s]+)", str(msg["text"]))
    assert match, f"no {path} link in email"
    return match.group(1)


@dataclass
class Session:
    email: str
    access_token: str
    user_id: str
    client: httpx.AsyncClient

    def headers(self, workspace_id: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.access_token}"}
        if workspace_id:
            h["X-Workspace-Id"] = workspace_id
        return h


async def sign_in(client: httpx.AsyncClient, email: str | None = None) -> Session:
    email = email or unique_email()
    r = await client.post("/api/v1/auth/magic-link", json={"email": email})
    assert r.status_code == 202, r.text
    token = await link_token(email, "/auth/verify")
    r = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "signed_in", body
    return Session(email, body["access_token"], body["user"]["id"], client)


async def create_workspace(
    s: Session, name: str = "Test Shop", *, plan: str | None = "pro", status: str = "active"
) -> str:
    """Create a workspace. By default it gets an active subscription on `plan` (written
    directly, as a verified webhook would), so tests reach features past the billing gate
    (ADR-0016). Pass `plan=None` for a workspace with no subscription."""
    r = await s.client.post("/api/v1/workspaces", json={"name": name}, headers=s.headers())
    assert r.status_code == 201, r.text
    workspace_id = str(r.json()["id"])
    if plan is not None:
        await set_subscription(uuid.UUID(workspace_id), plan=plan, status=status)
    return workspace_id


async def set_subscription(
    workspace_id: uuid.UUID,
    *,
    plan: str = "pro",
    status: str = "active",
    interval: str = "month",
    days_into_period: int = 0,
) -> None:
    """Create or replace the workspace's subscription row (test setup only)."""
    from sqlalchemy import text

    from momentsales.core.db import get_sessionmaker, set_tenant_context

    async with get_sessionmaker()() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        await session.execute(
            text(
                "INSERT INTO subscriptions (id, workspace_id, plan_id, interval, currency,"
                " status, trial_used, cancel_at_period_end, trial_reminders_sent,"
                " current_period_start, current_period_end, created_at, updated_at)"
                " SELECT gen_random_uuid(), :w, id, CAST(:i AS billing_interval), 'GBP',"
                " CAST(:st AS subscription_status), false, false, '[]',"
                " date_trunc('second', now()) - make_interval(days => :d),"
                " date_trunc('second', now()) - make_interval(days => :d) + interval '30 days',"
                " now(), now() FROM plans WHERE code = :p"
                " ON CONFLICT (workspace_id) DO UPDATE SET plan_id = EXCLUDED.plan_id,"
                " status = EXCLUDED.status, interval = EXCLUDED.interval"
            ),
            {"w": str(workspace_id), "p": plan, "st": status, "i": interval, "d": days_into_period},
        )


async def add_member(owner: Session, workspace_id: str, role: str) -> Session:
    """Invite a fresh user with `role` and accept as them."""
    member_email = unique_email(role)
    r = await owner.client.post(
        "/api/v1/invitations",
        json={"email": member_email, "role": role},
        headers=owner.headers(workspace_id),
    )
    assert r.status_code == 201, r.text
    token = await link_token(member_email, "/invite/accept")
    member = await sign_in(owner.client, member_email)
    r = await owner.client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=member.headers()
    )
    assert r.status_code == 200, r.text
    return member


# One row in every Phase 1 tenant table, written directly in the workspace's own context.
# Isolation tests use it so each table has data that the other tenant must not see.
_PHASE1_SEED_SQL = (
    "INSERT INTO integrations (id, workspace_id, type, external_id, status, charges_enabled,"
    " payouts_enabled, details_submitted, requirements, livemode, created_at, updated_at)"
    " VALUES (gen_random_uuid(), :w, 'stripe', 'acct_' || md5(random()::text), 'pending',"
    " false, false, false, '{}', false, now(), now())",
    "INSERT INTO idempotency_keys (id, workspace_id, key, request_hash, response_status,"
    " response_body, created_at) VALUES (gen_random_uuid(), :w, md5(random()::text),"
    " repeat('0', 64), 201, '{}', now())",
    "INSERT INTO orders (id, workspace_id, channel, external_id, status, currency, gross_minor,"
    " tax_minor, fees_minor, refunded_minor, created_at, updated_at) VALUES (:o, :w, 'manual',"
    " md5(random()::text), 'paid', 'GBP', 1000, 0, 0, 0, now(), now())",
    "INSERT INTO order_items (id, workspace_id, order_id, description, quantity,"
    " unit_amount_minor) VALUES (gen_random_uuid(), :w, :o, 'Candle', 1, 1000)",
    "INSERT INTO checkout_sessions (id, workspace_id, status, currency, items, created_at,"
    " updated_at) VALUES (gen_random_uuid(), :w, 'open', 'GBP', '[]', now(), now())",
    "INSERT INTO csv_imports (id, workspace_id, kind, filename, sha256, column_mapping,"
    " rows_total, rows_imported, rows_skipped, errors, status, created_by, created_at,"
    " updated_at) VALUES (gen_random_uuid(), :w, 'spends', 'a.csv', md5(random()::text), '{}',"
    " 0, 0, 0, '[]', 'completed', :u, now(), now())",
    "INSERT INTO spends (id, workspace_id, amount_minor, tax_minor, currency, base_amount_minor,"
    " spent_on, vendor, category, payment_method, source, created_by, created_at)"
    " VALUES (:s, :w, 2499, 0, 'GBP', 2499, current_date, 'Royal Mail', 'shipping', 'card',"
    " 'manual', :u, now())",
    "INSERT INTO receipts (id, workspace_id, file_key, sha256, mime, size_bytes, original_name,"
    " ocr_status, uploaded_by, created_at) VALUES (:r, :w, :fk,"
    " repeat('a', 64), 'image/png', 10, 'r.png', 'skipped', :u, now())",
    "INSERT INTO spend_receipts (id, workspace_id, spend_id, receipt_id, linked_by, created_at)"
    " VALUES (gen_random_uuid(), :w, :s, :r, :u, now())",
    "INSERT INTO ledger_entries (id, workspace_id, type, amount_minor, currency, occurred_on,"
    " spend_id, source, source_ref, description, created_by, created_at) VALUES"
    " (gen_random_uuid(), :w, 'spend', -2499, 'GBP', current_date, :s, 'spend', :sref, 'seed',"
    " :u, now())",
    "INSERT INTO spend_voids (id, workspace_id, spend_id, reason, voided_by, created_at)"
    " VALUES (gen_random_uuid(), :w, :s, 'seed', :u, now())",
    "INSERT INTO exports (id, workspace_id, period_start, period_end, status, files,"
    " requested_by, created_at, updated_at) VALUES (gen_random_uuid(), :w, current_date,"
    " current_date, 'pending', '[]', :u, now(), now())",
)
_WEBHOOK_SEED_SQL = (
    "INSERT INTO webhook_events (id, provider, event_id, type, livemode, workspace_id, payload,"
    " received_at, attempts) VALUES (gen_random_uuid(), 'stripe', 'evt_' || md5(random()::text),"
    " 'account.updated', false, :w, '{}', now(), 0)"
)


async def seed_phase1_rows(workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from sqlalchemy import text

    from momentsales.core.db import get_sessionmaker, set_tenant_context

    params = {
        "w": str(workspace_id),
        "u": str(user_id),
        "o": str(uuid.uuid4()),
        "s": str(uuid.uuid4()),
        "r": str(uuid.uuid4()),
    }
    params["fk"] = f"{workspace_id}/receipts/{params['r']}"
    params["sref"] = params["s"]
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=user_id, workspace_id=workspace_id)
        for stmt in _PHASE1_SEED_SQL:
            await s.execute(text(stmt), params)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=user_id, platform=True)
        await s.execute(text(_WEBHOOK_SEED_SQL), params)


_BILLING_SEED_SQL = (
    "INSERT INTO subscriptions (id, workspace_id, plan_id, interval, currency, status,"
    " trial_used, cancel_at_period_end, trial_reminders_sent, created_at, updated_at)"
    " SELECT gen_random_uuid(), :w, id, 'month', 'GBP', 'active', false, false, '[]', now(),"
    " now() FROM plans WHERE code = 'growth' ON CONFLICT (workspace_id) DO NOTHING",
    "INSERT INTO usage_counters (id, workspace_id, period_start, period_end, research_runs_used,"
    " created_at, updated_at) VALUES (gen_random_uuid(), :w, now() - interval '1 day',"
    " now() + interval '29 days', 1, now(), now()) ON CONFLICT DO NOTHING",
    "INSERT INTO run_credits (id, workspace_id, pack_runs, single_runs, created_at, updated_at)"
    " VALUES (gen_random_uuid(), :w, 10, 0, now(), now()) ON CONFLICT DO NOTHING",
    "INSERT INTO run_consumptions (id, workspace_id, key, pool, created_at)"
    " VALUES (gen_random_uuid(), :w, md5(random()::text), 'plan', now())",
    "INSERT INTO addon_purchases (id, workspace_id, addon_id, kind, quantity, runs,"
    " amount_minor, currency, status, created_by, created_at, updated_at)"
    " SELECT gen_random_uuid(), :w, id, kind, 1, runs, amount_minor, currency, 'paid', :u,"
    " now(), now() FROM addon_products WHERE kind = 'run_pack'",
    "INSERT INTO saas_invoices (id, workspace_id, stripe_invoice_id, status, total_minor,"
    " tax_minor, currency, created_at, updated_at) VALUES (gen_random_uuid(), :w,"
    " 'in_' || md5(random()::text), 'paid', 7900, 0, 'GBP', now(), now())",
    "INSERT INTO trial_extensions (id, workspace_id, days, reason, applied_by, created_at)"
    " VALUES (gen_random_uuid(), :w, 7, 'seed', :u, now())",
    "INSERT INTO cancel_surveys (id, workspace_id, reason, offer_made, outcome, created_by,"
    " created_at) VALUES (gen_random_uuid(), :w, 'other', 'none', 'canceled', :u, now())",
    "INSERT INTO subscription_pauses (id, workspace_id, months, starts_at, resumes_at,"
    " created_by, created_at) VALUES (gen_random_uuid(), :w, 1, now(),"
    " now() + interval '1 month', :u, now())",
)


async def seed_billing_rows(workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """One row in every Phase 1B tenant table (isolation tests)."""
    from sqlalchemy import text

    from momentsales.core.db import get_sessionmaker, set_tenant_context

    params = {"w": str(workspace_id), "u": str(user_id)}
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=user_id, workspace_id=workspace_id)
        for stmt in _BILLING_SEED_SQL:
            await s.execute(text(stmt), params)
