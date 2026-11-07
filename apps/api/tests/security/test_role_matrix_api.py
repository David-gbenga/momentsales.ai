"""Role checks through the API for every Phase 0 action x role (AC-12 groundwork)."""

from __future__ import annotations

import httpx
import pytest
from helpers import Session, add_member, create_workspace, sign_in, unique_email

ROLES = ["owner", "admin", "analyst", "accountant"]


@pytest.fixture
async def team(client: httpx.AsyncClient) -> tuple[str, dict[str, Session]]:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Role Matrix")
    sessions = {"owner": owner}
    for role in ROLES[1:]:
        sessions[role] = await add_member(owner, ws, role)
    return ws, sessions


CASES: list[tuple[str, str, str, dict[str, object] | None, set[str]]] = [
    # (name, method, path, json, roles allowed)
    ("workspace.read", "GET", "/api/v1/workspaces/{ws}", None, set(ROLES)),
    ("workspace.update", "PATCH", "/api/v1/workspaces/{ws}", {"legal_name": "X Ltd"}, {"owner"}),
    ("members.read", "GET", "/api/v1/members", None, {"owner", "admin"}),
    ("invitations.read", "GET", "/api/v1/invitations", None, {"owner", "admin"}),
    ("members.invite", "POST", "/api/v1/invitations", {"role": "analyst"}, {"owner"}),
    ("flags.read", "GET", "/api/v1/flags", None, set(ROLES)),
]


@pytest.mark.parametrize(("name", "method", "path", "body", "allowed"), CASES)
async def test_role_matrix(
    client: httpx.AsyncClient,
    team: tuple[str, dict[str, Session]],
    name: str,
    method: str,
    path: str,
    body: dict[str, object] | None,
    allowed: set[str],
) -> None:
    ws, sessions = team
    for role, s in sessions.items():
        payload = dict(body) if body else None
        if payload is not None and name == "members.invite":
            payload["email"] = unique_email()
        r = await client.request(
            method, path.replace("{ws}", ws), headers=s.headers(ws), json=payload
        )
        if role in allowed:
            assert r.status_code < 400, f"{role} should be allowed {name}: {r.text}"
        else:
            assert r.status_code == 403, f"{role} should be denied {name}: {r.status_code}"
            assert r.json()["type"] == "forbidden" and r.json()["action"]


async def test_only_owner_changes_roles(
    client: httpx.AsyncClient, team: tuple[str, dict[str, Session]]
) -> None:
    ws, sessions = team
    members = (await client.get("/api/v1/members", headers=sessions["owner"].headers(ws))).json()
    analyst_id = next(m["id"] for m in members if m["email"] == sessions["analyst"].email)
    for role in ("admin", "analyst", "accountant"):
        r = await client.patch(
            f"/api/v1/members/{analyst_id}",
            json={"role": "admin"},
            headers=sessions[role].headers(ws),
        )
        assert r.status_code == 403
        r = await client.delete(f"/api/v1/members/{analyst_id}", headers=sessions[role].headers(ws))
        assert r.status_code == 403


async def test_admin_providers_requires_platform_admin(client: httpx.AsyncClient) -> None:
    s = await sign_in(client)
    assert (await client.get("/api/v1/admin/providers", headers=s.headers())).status_code == 403
