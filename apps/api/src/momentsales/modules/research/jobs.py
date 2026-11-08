"""Research run job and its sweeper (ADR-0018). Runs in the worker; inline in tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from momentsales.agents import orchestrator
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.jobs import enqueue_now, job
from momentsales.core.redis import get_redis
from momentsales.core.runtime import runtime
from momentsales.db.research import ResearchRun, RunStatus

RUN_JOB = "research.run"


@job(RUN_JOB)
async def run_research(payload: dict[str, Any]) -> None:
    async with runtime() as rt:
        await orchestrator.execute(
            uuid.UUID(payload["run_id"]),
            uuid.UUID(payload["workspace_id"]),
            registry=rt.registry,
            settings=rt.settings,
            redis=get_redis(rt.settings.redis_url),
        )


async def sweep() -> dict[str, int]:
    """Re-queue runs that were never picked up or stopped mid-way (worker restart).
    The orchestrator resumes after the last finished step."""
    async with runtime() as rt, get_sessionmaker()() as session, session.begin():
        await set_tenant_context(session, platform=True)
        now = datetime.now(UTC)
        stuck = (
            await session.execute(
                select(ResearchRun.workspace_id, ResearchRun.id)
                .where(
                    or_(
                        (ResearchRun.status == RunStatus.QUEUED)
                        & (ResearchRun.created_at < now - timedelta(minutes=1)),
                        (ResearchRun.status == RunStatus.RUNNING)
                        & (
                            ResearchRun.updated_at
                            < now - timedelta(minutes=rt.settings.research_stuck_after_minutes)
                        ),
                    )
                )
                .limit(100)
            )
        ).all()
    for workspace_id, run_id in stuck:
        enqueue_now(
            rt.settings, RUN_JOB, {"workspace_id": str(workspace_id), "run_id": str(run_id)}
        )
    return {"research_runs": len(stuck)}
