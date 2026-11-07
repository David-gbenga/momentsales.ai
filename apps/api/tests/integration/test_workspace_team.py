from __future__ import annotations

import uuid

import httpx
from helpers import add_member, create_workspace, link_token, sign_in, unique_email
from sqlalchemy import select

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.models import AuditLog, Touch, TouchCategory
from momentsales.modules.audit.service import verify_chain


async def _touches_and_chain(workspace_id: str) -> tuple[list[Touch], bool, list[str]]:
    ws = uuid.UUID(workspace_id)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        touches = list((await s.scalars(select(Touch).order_by(Touch.created_at))).all())
        actions = [a.action for a in await s.scalars(select(AuditLog).order_by(AuditLog.seq))]
        ok = (await verify_chain(s, ws)).ok
    return touches, ok, actions


async def test_create_uk_workspace(client: httpx.AsyncClient) -> None:
    s = await sign_in(client)
    r = await client.post(
        "/api/v1/workspaces", json={"name": "Cosy Candles UK"}, headers=s.headers()
    )
    assert r.status_code == 201
    body = r.json()
    assert body["country"] == "GB" and body["base_currency"] == "GBP"
    assert body["slug"].startswith("cosy-candles-uk")
    r = await client.get("/api/v1/workspaces", headers=s.headers())
    assert [w["role"] for w in r.json()] == ["owner"]
    touches, ok, actions = await _touches_and_chain(body["id"])
    assert ok and actions == ["workspace.created", "membership.created"]
    assert touches[0].category is TouchCategory.TEAM_AND_PERMISSIONS and touches[0].auto


async def test_only_uk_selectable(client: httpx.AsyncClient) -> None:
    assert [c["code"] for c in (await client.get("/api/v1/countries")).json()] == ["GB"]
    s = await sign_in(client)
    r = await client.post(
        "/api/v1/workspaces", json={"name": "US Shop", "country": "US"}, headers=s.headers()
    )
    assert r.status_code == 422 and r.json()["type"] == "country_not_available"


async def test_duplicate_names_get_unique_slugs(client: httpx.AsyncClient) -> None:
    a, b = await sign_in(client), await sign_in(client)
    name = f"Same Name {uuid.uuid4().hex[:6]}"
    r1 = await client.post("/api/v1/workspaces", json={"name": name}, headers=a.headers())
    r2 = await client.post("/api/v1/workspaces", json={"name": name}, headers=b.headers())
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["slug"] != r2.json()["slug"]


async def test_invite_accept_change_role_remove(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner)
    analyst = await add_member(owner, ws, "analyst")
    r = await client.get("/api/v1/members", headers=owner.headers(ws))
    members = {m["email"]: m for m in r.json()}
    assert members[analyst.email]["role"] == "analyst"
    mid = members[analyst.email]["id"]
    r = await client.patch(
        f"/api/v1/members/{mid}", json={"role": "admin"}, headers=owner.headers(ws)
    )
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert (
        await client.delete(f"/api/v1/members/{mid}", headers=owner.headers(ws))
    ).status_code == 204
    r = await client.get("/api/v1/flags", headers=analyst.headers(ws))
    assert r.status_code == 404  # removed members lose access
    touches, ok, actions = await _touches_and_chain(ws)
    assert ok
    assert actions[-4:] == [
        "invitation.created",
        "invitation.accepted",
        "membership.role_changed",
        "membership.removed",
    ]
    assert all(t.auto and t.category is TouchCategory.TEAM_AND_PERMISSIONS for t in touches)


async def test_last_owner_protected(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner)
    me = (await client.get("/api/v1/members", headers=owner.headers(ws))).json()[0]
    r = await client.patch(
        f"/api/v1/members/{me['id']}", json={"role": "admin"}, headers=owner.headers(ws)
    )
    assert r.status_code == 409 and r.json()["type"] == "last_owner"


async def test_invitation_must_match_email_and_is_single_use(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner)
    invitee = unique_email("inv")
    await client.post(
        "/api/v1/invitations",
        json={"email": invitee, "role": "accountant"},
        headers=owner.headers(ws),
    )
    token = await link_token(invitee, "/invite/accept")
    stranger = await sign_in(client)
    r = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=stranger.headers()
    )
    assert r.status_code == 403
    right = await sign_in(client, invitee)
    ok = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=right.headers()
    )
    assert ok.status_code == 200 and ok.json()["role"] == "accountant"
    again = await client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=right.headers()
    )
    assert again.status_code == 400


async def test_cannot_invite_owner_or_existing_member(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner)
    r = await client.post(
        "/api/v1/invitations",
        json={"email": unique_email(), "role": "owner"},
        headers=owner.headers(ws),
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/invitations",
        json={"email": owner.email, "role": "admin"},
        headers=owner.headers(ws),
    )
    assert r.status_code == 409


async def test_patch_workspace_audited(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = await create_workspace(owner)
    r = await client.patch(
        f"/api/v1/workspaces/{ws}",
        json={"legal_name": "Cosy Candles Ltd", "tax_ids": {"vat": "GB123456789"}},
        headers=owner.headers(),
    )
    assert r.status_code == 200 and r.json()["legal_name"] == "Cosy Candles Ltd"
    _, ok, actions = await _touches_and_chain(ws)
    assert ok and actions[-1] == "workspace.updated"
