"""Release blocker (ADR-0004): every tenant table has RLS enabled AND forced, with policies."""

from __future__ import annotations

from sqlalchemy import text

from momentsales.core.db import get_sessionmaker
from momentsales.db import models  # noqa: F401
from momentsales.db.base import TENANT_TABLES, Base


def test_tenant_table_list_matches_metadata() -> None:
    with_workspace_id = {t.name for t in Base.metadata.tables.values() if "workspace_id" in t.c} | {
        "workspaces"
    }
    assert with_workspace_id == set(TENANT_TABLES), (
        "A table has workspace_id but is not in TENANT_TABLES (or vice versa). "
        "Add RLS in a migration and list it."
    )


async def test_rls_enabled_forced_and_has_policies() -> None:
    async with get_sessionmaker()() as s, s.begin():
        rows = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
                    " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            )
        ).all()
    state = {name: (enabled, forced, policies) for name, enabled, forced, policies in rows}
    for table in TENANT_TABLES:
        enabled, forced, policies = state[table]
        assert enabled and forced, f"{table}: RLS must be ENABLE + FORCE"
        assert policies > 0, f"{table}: no RLS policy"


async def test_runtime_role_cannot_bypass_rls() -> None:
    async with get_sessionmaker()() as s, s.begin():
        row = (
            await s.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row == (False, False)
