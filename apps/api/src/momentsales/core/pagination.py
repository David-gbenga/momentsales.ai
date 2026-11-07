"""Cursor pagination (ADR-0009). Ids are uuid7, so they sort by creation time and the last
id seen is the cursor: `?cursor=<id>&limit=50` returns older items."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select
from sqlalchemy.orm import InstrumentedAttribute

from momentsales.core.errors import ProblemError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def parse_cursor(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        return uuid.UUID(cursor)
    except ValueError as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid cursor", "Start again.") from exc


def paginate(
    stmt: Select[Any], id_column: InstrumentedAttribute[uuid.UUID], cursor: str | None, limit: int
) -> Select[Any]:
    after = parse_cursor(cursor)
    if after is not None:
        stmt = stmt.where(id_column < after)
    return stmt.order_by(id_column.desc()).limit(limit + 1)


def page(rows: list[Any], limit: int, id_of: Any = lambda r: r.id) -> tuple[list[Any], str | None]:
    """Trim the extra row fetched by `paginate` and return (items, next_cursor)."""
    if len(rows) > limit:
        rows = rows[:limit]
        return rows, str(id_of(rows[-1]))
    return rows, None
