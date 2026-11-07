from __future__ import annotations

import uuid

import httpx
import pytest
from helpers import create_workspace, sign_in
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.models import AuditLog
from momentsales.modules.audit.service import verify_entries


async def _entries(ws: uuid.UUID) -> list[AuditLog]:
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, workspace_id=ws)
        return list((await s.scalars(select(AuditLog).order_by(AuditLog.seq))).all())


async def test_tampering_is_detected(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = uuid.UUID(await create_workspace(owner))
    entries = await _entries(ws)
    assert verify_entries(entries).ok
    entries[0].after = {"name": "Forged"}
    result = verify_entries(entries)
    assert not result.ok and result.first_bad_seq == entries[0].seq


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'x'",
        "DELETE FROM audit_log",
        "UPDATE touches SET description = 'x'",
        "DELETE FROM touches",
    ],
)
async def test_append_only_tables_refuse_changes(client: httpx.AsyncClient, statement: str) -> None:
    owner = await sign_in(client)
    ws = uuid.UUID(await create_workspace(owner))
    async with get_sessionmaker()() as s:
        await s.begin()
        await set_tenant_context(s, workspace_id=ws)
        with pytest.raises(DBAPIError):
            await s.execute(text(statement))
        await s.rollback()


async def test_append_only_even_for_schema_owner(client: httpx.AsyncClient) -> None:
    """The owner is blocked too: no UPDATE/DELETE policy (FORCE RLS) and a TRUNCATE trigger."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from momentsales.core.config import get_settings

    owner = await sign_in(client)
    ws = uuid.UUID(await create_workspace(owner))
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :w, false)"), {"w": str(ws)}
            )
            deleted = await conn.execute(text("DELETE FROM audit_log"))
            assert deleted.rowcount == 0
            with pytest.raises(DBAPIError, match="append-only"):
                await conn.execute(text("TRUNCATE audit_log"))
    finally:
        await engine.dispose()
