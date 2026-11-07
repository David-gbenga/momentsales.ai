"""Heartbeat: proves the worker and beat schedule are alive."""

from __future__ import annotations

from datetime import UTC, datetime

from momentsales.core.logging import get_logger
from momentsales_worker.celery_app import app

log = get_logger(__name__)


@app.task(name="momentsales_worker.tasks.heartbeat.heartbeat")
def heartbeat() -> str:
    now = datetime.now(UTC).isoformat()
    log.info("worker_heartbeat", at=now)
    return now
