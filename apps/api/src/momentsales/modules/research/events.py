"""Live run progress (ADR-0018): Redis pub/sub notifies, the database is the record.

Publishing is best effort. The stream re-reads the run from the database on every message
and on a short timer, so a missed message only delays an update.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis

from momentsales.core.logging import get_logger

log = get_logger(__name__)


def channel(run_id: uuid.UUID) -> str:
    return f"research-run:{run_id}"


async def publish(redis: Redis, run_id: uuid.UUID, event: dict[str, Any]) -> None:
    try:
        await redis.publish(channel(run_id), json.dumps(event, default=str))
    except Exception as exc:  # progress is a convenience; never fail a run over it
        log.warning("research_event_publish_failed", run_id=str(run_id), error=str(exc))
