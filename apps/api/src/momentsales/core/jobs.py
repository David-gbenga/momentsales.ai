"""Background jobs: handlers live in the API package so they're testable; the worker runs them.

- `schedule()` queues a job to run after the request's transaction commits (it's added as a
  FastAPI background task, which runs after the response, and `get_db` commits before that).
- `JOBS_MODE=celery` sends it to the worker through Redis (one generic Celery task,
  `momentsales.run_job`). `JOBS_MODE=inline` runs it in-process (tests only).
- Handlers must be idempotent: Celery retries, and Beat sweepers re-queue stuck work.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from fastapi import BackgroundTasks

from momentsales.core.config import Settings
from momentsales.core.logging import get_logger

log = get_logger(__name__)

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]
_HANDLERS: dict[str, JobHandler] = {}
# Modules that register handlers with @job; imported on first use.
JOB_MODULES = (
    "momentsales.modules.receipts.jobs",
    "momentsales.modules.payments.jobs",
    "momentsales.modules.exports.jobs",
    "momentsales.modules.research.jobs",
)
CELERY_TASK = "momentsales.run_job"


def job(name: str) -> Callable[[JobHandler], JobHandler]:
    def register(fn: JobHandler) -> JobHandler:
        _HANDLERS[name] = fn
        return fn

    return register


def _load() -> None:
    for module in JOB_MODULES:
        importlib.import_module(module)


async def run_job(name: str, payload: dict[str, Any]) -> None:
    _load()
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown job {name!r}")
    await handler(payload)


@lru_cache(maxsize=4)
def _celery(broker_url: str) -> Any:
    from celery import Celery

    return Celery("momentsales-api", broker=broker_url)


def _send(settings: Settings, name: str, payload: dict[str, Any]) -> None:
    try:
        _celery(settings.redis_url).send_task(CELERY_TASK, args=[name, payload])
    except Exception as exc:  # the Beat sweepers re-queue anything that didn't go out
        log.error("job_enqueue_failed", job=name, error=str(exc))


def schedule(
    background: BackgroundTasks, settings: Settings, name: str, payload: dict[str, Any]
) -> None:
    if settings.jobs_mode == "inline":
        background.add_task(run_job, name, payload)
    else:
        background.add_task(_send, settings, name, payload)


def enqueue_now(settings: Settings, name: str, payload: dict[str, Any]) -> None:
    """Queue from outside a request (worker sweepers). Celery mode only."""
    _send(settings, name, payload)
