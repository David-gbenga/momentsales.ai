"""/touch events (FR-7). Phase 0 writes automatic touches for permission changes.

The ledger UI and the manual /touch command arrive in Phase 1.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.db.models import Touch, TouchCategory


async def record_auto_touch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    category: TouchCategory,
    description: str,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
) -> Touch:
    touch = Touch(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        category=category,
        description=description,
        minutes=0,
        object_type=object_type,
        object_id=object_id,
        auto=True,
    )
    session.add(touch)
    await session.flush()
    return touch
