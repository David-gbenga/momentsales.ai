"""Database-level isolation (AC-19 groundwork): in workspace B's context, workspace A's
rows cannot be read, changed or deleted, and nothing can be written into A."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx
import pytest
from helpers import add_member, create_workspace, sign_in
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.base import TENANT_TABLES


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
