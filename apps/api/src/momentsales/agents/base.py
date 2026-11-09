"""Agent contract (ADR-0018).

Each agent has one job, a typed result, a tool allow-list (the adapter categories it may
call), a timeout and a cost budget. Agents never invent prices, sizes or dates: every such
fact is stored as a `SourceSnapshot` first and referenced by its id (AC-3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import Settings
from momentsales.db.research import ResearchRun, SourceSnapshot
from momentsales.modules.providers.registry import ProviderRegistry


class BudgetExceededError(Exception):
    """The run's cost cap would be passed; the orchestrator stops with partial results."""


MICROS_PER_MINOR = 10_000  # GBP micros (1,000,000 = £1) per penny


@dataclass(slots=True)
class CostMeter:
    """Run spend in GBP micros (FR-1, ADR-0019): one provider call costs a fraction of a
    penny, so minor units would round every call up. Cache hits cost nothing."""

    cap_micros: int
    spent_micros: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)

    @property
    def spent_minor(self) -> int:
        """Spend rounded up to whole minor units (what the API and UI show)."""
        return -(-self.spent_micros // MICROS_PER_MINOR)

    @property
    def remaining_micros(self) -> int:
        return max(self.cap_micros - self.spent_micros, 0)

    def ensure(self, estimate_micros: int) -> None:
        """Check before a paid call that it fits under the cap."""
        if self.spent_micros + estimate_micros > self.cap_micros:
            raise BudgetExceededError(
                f"cost cap reached: spent {self.spent_micros} micros, next call "
                f"~{estimate_micros}, cap {self.cap_micros}"
            )

    def charge(self, provider: str, amount_micros: int) -> None:
        if amount_micros == 0:
            return  # free calls (suppliers, FX, cache hits) do not appear in spend by provider
        self.spent_micros += amount_micros
        self.by_provider[provider] = self.by_provider.get(provider, 0) + amount_micros


@dataclass(slots=True)
class AgentContext:
    session: AsyncSession
    run: ResearchRun
    as_of: date
    registry: ProviderRegistry
    settings: Settings
    meter: CostMeter
    # Outputs of earlier agents in this run, by agent name.
    outputs: dict[str, dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Shared Redis (monthly market-data budget, ADR-0019); None in some unit tests.
    redis: Redis | None = None

    async def snapshot(
        self, *, provider: str, kind: str, url: str, payload: Any, fetched_at: datetime
    ) -> uuid.UUID:
        row = SourceSnapshot(
            workspace_id=self.run.workspace_id,
            run_id=self.run.id,
            provider=provider,
            kind=kind,
            url=url,
            payload=payload,
            fetched_at=fetched_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    def record_call(self, tool: str, **details: Any) -> None:
        self.tool_calls.append({"tool": tool, **details})


@dataclass(frozen=True, slots=True)
class AgentResult:
    output: dict[str, Any]
    summary: str
    degraded: bool = False
    # The agent stopped at the run's cost cap but kept what it had already paid for; the
    # orchestrator saves the step, then finishes the run as `partial` (ADR-0019).
    budget_exhausted: bool = False


class Agent(Protocol):
    name: str
    # Adapter categories this agent may call.
    tools: tuple[str, ...]
    timeout_seconds: float

    async def run(self, ctx: AgentContext) -> AgentResult: ...
