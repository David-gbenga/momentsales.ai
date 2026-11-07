# ADR-0005: Celery on Redis for background jobs

- Status: Accepted (Phase 0)
- Date: 2026-11-07

## Context
Agent research runs (Phase 2) must run off the request path. Scheduled jobs (trial reminders, weekly email, stock and cost sync) need a scheduler. The PRD allows Celery or Arq.

## Decision
Celery 5 with Redis as the broker, and Celery Beat for schedules. Arq is in maintenance-only mode. Celery is mature, widely operated and has Beat built in. Tasks are JSON-serialised, use late acks and a prefetch of 1 (long agent runs). Phase 0 ships only a heartbeat task on a 60-second Beat schedule.

## Consequences
- Async code inside tasks uses `asyncio.run` per task. Agent orchestration in Phase 2 will decide on resumable state (the PRD says runs must be resumable and idempotent).
- Locally, compose runs the worker with an embedded Beat (`--beat`). Production runs Beat as a single separate process.
