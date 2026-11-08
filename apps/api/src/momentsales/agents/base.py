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

from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import Settings
from momentsales.db.research import ResearchRun, SourceSnapshot
from momentsales.modules.providers.registry import ProviderRegistry


class BudgetExceededError(Exception):
    """The run's cost cap would be passed; the orchestrator stops with partial results."""


@dataclass(slots=True)
class CostMeter:
    """Run spend in the cap currency's minor units (FR-1). Cache hits cost nothing."""

    cap_minor: int
    spent_minor: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)

    @property
    def remaining_minor(self) -> int:
        return max(self.cap_minor - self.spent_minor, 0)

    def ensure(self, estimate_minor: int) -> None:
        """Check before a paid call that it fits under the cap."""
        if self.spent_minor + estimate_minor > self.cap_minor:
            raise BudgetExceededError(
                f"cost cap reached: spent {self.spent_minor}, next call ~{estimate_minor}, "
                f"cap {self.cap_minor}"
            )

    def charge(self, provider: str, amount_minor: int) -> None:
        self.spent_minor += amount_minor
        self.by_provider[provider] = self.by_provider.get(provider, 0) + amount_minor


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


class Agent(Protocol):
    name: str
    # Adapter categories this agent may call.
    tools: tuple[str, ...]
    timeout_seconds: float

    async def run(self, ctx: AgentContext) -> AgentResult: ...
