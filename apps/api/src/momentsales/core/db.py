"""Async database engine, sessions and tenant context (ADR-0004).

Every request runs in one transaction. Tenant context is set with
`set_config(..., is_local => true)`, so it lives only for that transaction and can
never leak to another request through the connection pool.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from momentsales.core.config import get_settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )


@lru_cache(maxsize=4)
def get_sessionmaker(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(url), expire_on_commit=False, autoflush=True)


async def set_tenant_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    platform: bool = False,
) -> None:
    """Set the RLS context for the current transaction."""
    await session.execute(
        text(
            "SELECT set_config('app.user_id', :u, true),"
            " set_config('app.workspace_id', :w, true),"
            " set_config('app.platform', :p, true)"
        ),
        {
            "u": str(user_id) if user_id else "",
            "w": str(workspace_id) if workspace_id else "",
            "p": "on" if platform else "off",
        },
    )


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session + transaction per request.

    Use with `Depends(get_db, scope="function")` so the commit happens before the
    response is sent (a client never sees a 2xx for an uncommitted write).
    """
    async with get_sessionmaker()() as session, session.begin():
        yield session
