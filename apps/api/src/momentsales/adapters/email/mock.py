"""Mock email adapter: no network. Messages go to a capped Redis list (the dev outbox)
so developers and Playwright can read magic links at GET /api/v1/dev/outbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from redis.asyncio import Redis

from momentsales.adapters.email.base import EmailMessage, SendResult
from momentsales.core.ids import uuid7
from momentsales.core.logging import get_logger

log = get_logger(__name__)
OUTBOX_KEY = "dev:outbox"
OUTBOX_MAX = 200


class MockEmailAdapter:
    name = "mock"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def send(self, message: EmailMessage) -> SendResult:
        message_id = str(uuid7())
        record = {
            "id": message_id,
            "to": message.to,
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        await self._redis.lpush(OUTBOX_KEY, json.dumps(record))
        await self._redis.ltrim(OUTBOX_KEY, 0, OUTBOX_MAX - 1)
        log.info("mock_email_sent", to=message.to, subject=message.subject)
        return SendResult(provider=self.name, message_id=message_id)


async def read_outbox(redis: Redis, limit: int = 50) -> list[dict[str, object]]:
    raw = await redis.lrange(OUTBOX_KEY, 0, limit - 1)
    return [json.loads(r) for r in raw]
