"""Research orchestrator (FR-1, ADR-0018).

Runs the agents in order, one transaction per step, so progress is visible and a retried
job resumes after the last finished step (steps are unique per run and agent). It enforces
the run's cost cap: reaching it stops the run as `partial`, with what was found so far.

Phase 2b runs the Season, Trend and Marketplace agents. Phases 2c-2d add the supplier,
scoring, compliance and report agents to `PIPELINE`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from momentsales.agents.base import (
    MICROS_PER_MINOR,
    Agent,
    AgentContext,
    BudgetExceededError,
    CostMeter,
)
from momentsales.agents.marketplace import MarketplaceAgent
from momentsales.agents.season import SeasonAgent
from momentsales.agents.trend import TrendAgent
from momentsales.core.config import Settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.logging import get_logger
from momentsales.db.research import (
    TERMINAL_STATUSES,
    AgentStep,
    ResearchRun,
    RunStatus,
    StepStatus,
)
from momentsales.modules.billing import entitlements
from momentsales.modules.ledger.service import uk_date
from momentsales.modules.providers.registry import ProviderRegistry
from momentsales.modules.research.events import publish

log = get_logger(__name__)

PIPELINE: tuple[Agent, ...] = (SeasonAgent(), TrendAgent(), MarketplaceAgent())
DONE_STEPS = frozenset({StepStatus.COMPLETED, StepStatus.DEGRADED})


def _now() -> datetime:
    return datetime.now(UTC)


async def execute(
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    registry: ProviderRegistry,
    settings: Settings,
    redis: Redis,
    pipeline: Sequence[Agent] | None = None,
) -> RunStatus:
    maker = get_sessionmaker()
    async with maker() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        run = await session.get(ResearchRun, run_id, with_for_update=True)
        if run is None:
            return RunStatus.FAILED
        if run.status in TERMINAL_STATUSES:
            return run.status
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or _now()
        meter = CostMeter(
            cap_micros=run.cost_cap_minor * MICROS_PER_MINOR,
            spent_micros=run.cost_micros,
            by_provider=dict(run.cost_by_provider or {}),
        )
    await publish(redis, run_id, {"type": "run", "status": RunStatus.RUNNING.value})

    outputs: dict[str, dict[str, Any]] = {}
    pipeline = PIPELINE if pipeline is None else pipeline
    for position, agent in enumerate(pipeline):
        try:
            outcome, exhausted = await _run_step(
                maker,
                run_id,
                workspace_id,
                position,
                agent,
                outputs,
                meter,
                registry,
                settings,
                redis,
            )
        except BudgetExceededError as exc:
            return await _finish(maker, redis, run_id, workspace_id, RunStatus.PARTIAL, str(exc))
        except Exception as exc:
            log.error("research_step_failed", run_id=str(run_id), agent=agent.name, error=str(exc))
            await _record_failure(maker, run_id, workspace_id, position, agent, exc)
            await publish(redis, run_id, {"type": "step", "agent": agent.name, "status": "failed"})
            return await _finish(
                maker, redis, run_id, workspace_id, RunStatus.FAILED, f"{agent.name}: {exc}"
            )
        await publish(
            redis,
            run_id,
            {
                "type": "step",
                "agent": agent.name,
                "status": outcome.status.value,
                "summary": outcome.summary,
                "cost_minor": meter.spent_minor,
            },
        )
        if exhausted:
            return await _finish(
                maker,
                redis,
                run_id,
                workspace_id,
                RunStatus.PARTIAL,
                f"{agent.name}: cost cap reached; results so far are kept",
            )
    return await _finish(maker, redis, run_id, workspace_id, RunStatus.COMPLETED, None)


async def _run_step(
    maker: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    position: int,
    agent: Agent,
    outputs: dict[str, dict[str, Any]],
    meter: CostMeter,
    registry: ProviderRegistry,
    settings: Settings,
    redis: Redis | None,
) -> tuple[AgentStep, bool]:
    async with maker() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        step = await session.scalar(
            select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.agent == agent.name)
        )
        if step is not None and step.status in DONE_STEPS:
            outputs[agent.name] = step.output or {}
            return step, False  # resumed: this step finished before a retry
        run = await session.get(ResearchRun, run_id, with_for_update=True)
        assert run is not None
        if step is None:
            step = AgentStep(
                workspace_id=workspace_id,
                run_id=run_id,
                agent=agent.name,
                position=position,
                status=StepStatus.RUNNING,
                input={"params": run.params},
            )
            session.add(step)
        ctx = AgentContext(
            session=session,
            run=run,
            as_of=await uk_date(session, run.as_of),
            registry=registry,
            settings=settings,
            meter=meter,
            outputs=outputs,
            redis=redis,
        )
        started = time.monotonic()
        spent_before = meter.spent_micros
        result = await asyncio.wait_for(agent.run(ctx), timeout=agent.timeout_seconds)
        step.status = StepStatus.DEGRADED if result.degraded else StepStatus.COMPLETED
        step.output = result.output
        step.summary = result.summary
        step.tool_calls = ctx.tool_calls
        step.cost_micros = meter.spent_micros - spent_before
        step.cost_minor = -(-step.cost_micros // MICROS_PER_MINOR)
        step.duration_ms = int((time.monotonic() - started) * 1000)
        step.finished_at = _now()
        run.cost_micros = meter.spent_micros
        run.cost_minor = meter.spent_minor
        run.cost_by_provider = dict(meter.by_provider)
        run.updated_at = _now()  # the sweeper treats a quiet run as stuck
        outputs[agent.name] = result.output
        return step, result.budget_exhausted


async def _record_failure(
    maker: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    position: int,
    agent: Agent,
    exc: BaseException,
) -> None:
    message = f"{type(exc).__name__}: {exc}"[:2000]
    async with maker() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        step = await session.scalar(
            select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.agent == agent.name)
        )
        if step is None:
            step = AgentStep(
                workspace_id=workspace_id,
                run_id=run_id,
                agent=agent.name,
                position=position,
                status=StepStatus.FAILED,
                input={},
            )
            session.add(step)
        step.status = StepStatus.FAILED
        step.error = message
        step.finished_at = _now()


async def _finish(
    maker: async_sessionmaker[AsyncSession],
    redis: Redis,
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    status: RunStatus,
    error: str | None,
) -> RunStatus:
    async with maker() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        run = await session.get(ResearchRun, run_id, with_for_update=True)
        assert run is not None
        if run.status in TERMINAL_STATUSES:
            return run.status
        run.status = status
        run.error = error
        run.finished_at = _now()
        # A run that failed before spending anything gives the run back (ADR-0015).
        if status is RunStatus.FAILED and run.cost_micros == 0:
            run.run_refunded = await entitlements.refund_run(
                session, workspace_id, run.consumption_key
            )
    await publish(redis, run_id, {"type": "run", "status": status.value, "error": error})
    return status
