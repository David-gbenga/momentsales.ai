from momentsales_worker.celery_app import app
from momentsales_worker.tasks.heartbeat import heartbeat


def test_heartbeat_runs_eagerly() -> None:
    app.conf.task_always_eager = True
    result = heartbeat.delay()
    assert result.get().endswith("+00:00")


def test_beat_schedule_has_heartbeat() -> None:
    assert "heartbeat" in app.conf.beat_schedule
