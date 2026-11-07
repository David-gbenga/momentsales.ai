"""Celery application (ADR-0005).

`momentsales.run_job` runs the API package's job handlers (webhook processing, receipt OCR,
audit exports); Beat runs the heartbeat and the stuck-work sweeper every minute.
"""

from __future__ import annotations

from celery import Celery

from momentsales.core.config import get_settings

settings = get_settings()

app = Celery("momentsales", broker=settings.redis_url, backend=None)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "heartbeat": {"task": "momentsales_worker.tasks.heartbeat.heartbeat", "schedule": 60.0},
        "sweep": {"task": "momentsales_worker.tasks.jobs.sweep", "schedule": 60.0},
    },
)
app.autodiscover_tasks(["momentsales_worker.tasks"], related_name="heartbeat")
app.autodiscover_tasks(["momentsales_worker.tasks"], related_name="jobs")
