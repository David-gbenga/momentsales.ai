"""API-level isolation: every workspace-scoped route (found automatically) returns 404
when a user targets a workspace they don't belong to — by header or by path."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from helpers import create_workspace, sign_in

from momentsales.main import API_PREFIX, ROUTERS
from momentsales.tenancy.deps import TENANT_DEPENDENCIES


def _uses_tenant_dep(dependant: Any) -> bool:
    if dependant.call in TENANT_DEPENDENCIES:
        return True
    return any(_uses_tenant_dep(d) for d in dependant.dependencies)


def tenant_routes(app: FastAPI) -> list[tuple[str, str]]:
    routes = []
    for router in ROUTERS:
        for r in router.routes:
            if isinstance(r, APIRoute) and _uses_tenant_dep(r.dependant):
                for method in sorted(r.methods):
                    routes.append((method, API_PREFIX + r.path))
    return routes


async def test_tenant_routes_discovered(app: FastAPI) -> None:
    paths = {p for _, p in tenant_routes(app)}
    assert {"/api/v1/members", "/api/v1/invitations", "/api/v1/flags"} <= paths
    assert "/api/v1/workspaces/{workspace_id}" in paths


async def test_every_tenant_route_hides_foreign_workspace(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    owner_a, intruder = await sign_in(client), await sign_in(client)
    ws_a = await create_workspace(owner_a, "Victim")
    await create_workspace(intruder, "Intruder")
    fake_id = str(uuid.uuid4())
    checked = 0
    for method, path in tenant_routes(app):
        url = (
            path.replace("{workspace_id}", ws_a)
            .replace("{membership_id}", fake_id)
            .replace("{invitation_id}", fake_id)
        )
        r = await client.request(method, url, headers=intruder.headers(ws_a), json={})
        assert r.status_code == 404, f"{method} {path} → {r.status_code} {r.text}"
        assert r.json()["type"] == "workspace_not_found"
        checked += 1
    assert checked >= 8


@pytest.mark.parametrize("header", ["not-a-uuid", ""])
async def test_bad_workspace_header(client: httpx.AsyncClient, header: str) -> None:
    s = await sign_in(client)
    h = s.headers()
    h["X-Workspace-Id"] = header
    r = await client.get("/api/v1/members", headers=h)
    assert r.status_code in (400, 404)
