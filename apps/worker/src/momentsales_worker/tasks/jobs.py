"""Runs API-package job handlers (momentsales.core.jobs) and the stuck-work sweeper.

Each task runs one asyncio loop; the DB engine and Redis pool are released afterwards so
their connections don't outlive the loop that created them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from momentsales.core.db import get_engine
from momentsales.core.jobs import CELERY_TASK, run_job
from momentsales.core.logging import get_logger
from momentsales.core.redis import get_redis
from momentsales_worker.celery_app import app, settings

log = get_logger(__name__)


def _run[T](fn: Callable[[], Awaitable[T]]) -> T:
    async def main() -> T:
        try:
            return await fn()
        finally:
            await get_engine().dispose()
            await get_redis(settings.redis_url).connection_pool.disconnect()

    return asyncio.run(main())


@app.task(
    name=CELERY_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=8,
    acks_late=True,
)
def run(name: str, payload: dict[str, Any]) -> None:
    _run(lambda: run_job(name, payload))


@app.task(name="momentsales_worker.tasks.jobs.sweep")
def sweep() -> dict[str, int]:
    from momentsales.modules.payments.jobs import sweep as do_sweep

    counts = _run(do_sweep)
    if any(counts.values()):
        log.info("sweep_requeued", **counts)
    return counts


@app.task(name="momentsales_worker.tasks.jobs.billing_sweep")
def billing_sweep() -> dict[str, int]:
    """Trial reminders, dunning suspension and pause resumption (ADR-0016)."""
    from momentsales.modules.billing.jobs import sweep as do_billing_sweep

    return _run(do_billing_sweep)
