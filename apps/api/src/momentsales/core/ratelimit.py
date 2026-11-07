"""Fixed-window rate limiting in Redis (per client IP, per route name).

Fails open if Redis is unreachable, with a warning, so an outage does not take
sign-in down. Keys hold only an HMAC of the IP.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from redis.exceptions import RedisError

from momentsales.core.errors import ProblemError
from momentsales.core.logging import get_logger
from momentsales.core.security import hash_ip

log = get_logger(__name__)


def limit(
    name: str, max_requests: int, window_seconds: int
) -> Callable[[Request], Awaitable[None]]:
    async def _dep(request: Request) -> None:
        state = request.app.state
        if not state.settings.rate_limit_enabled:
            return
        ip = request.client.host if request.client else "unknown"
        key = f"rl:{name}:{hash_ip(ip, state.settings)}"
        try:
            pipe = state.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = await pipe.execute()
        except RedisError:
            log.warning("rate_limit_unavailable", name=name)
            return
        if int(count) > max_requests:
            raise ProblemError(
                429,
                "rate_limited",
                "Too many requests",
                "Please wait a moment and try again.",
                retry_after_seconds=window_seconds,
            )

    return _dep
