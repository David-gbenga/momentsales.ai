"""Idempotency-Key handling for money writes (ADR-0010).

Same key + same body -> the stored response, without running the handler again.
Same key + different body -> 422. A transaction-scoped advisory lock serialises concurrent
requests that use the same key.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.errors import ProblemError
from momentsales.db.ledger import IdempotencyKey

MAX_KEY_LENGTH = 255


def request_hash(body: Any) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def run_idempotent(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    key: str | None,
    body: Any,
    handler: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> JSONResponse:
    if key is None:
        status, payload = await handler()
        return JSONResponse(payload, status_code=status)
    if not 1 <= len(key) <= MAX_KEY_LENGTH:
        raise ProblemError(
            400, "invalid_idempotency_key", "Invalid Idempotency-Key", "Use 1-255 characters."
        )
    digest = request_hash(body)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{workspace_id}:{key}"},
    )
    existing = await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.workspace_id == workspace_id, IdempotencyKey.key == key
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise ProblemError(
                422,
                "idempotency_key_reused",
                "Idempotency-Key reused",
                "This key was already used for a different request.",
            )
        return JSONResponse(
            existing.response_body,
            status_code=existing.response_status,
            headers={"Idempotent-Replayed": "true"},
        )
    status, payload = await handler()
    session.add(
        IdempotencyKey(
            workspace_id=workspace_id,
            key=key,
            request_hash=digest,
            response_status=status,
            response_body=json.loads(json.dumps(payload, default=str)),
        )
    )
    await session.flush()
    return JSONResponse(json.loads(json.dumps(payload, default=str)), status_code=status)
