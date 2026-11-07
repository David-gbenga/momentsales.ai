"""EntitlementService: the one place plan limits are checked (FR-10).

Every plan-limited action calls `EntitlementService.check(...)` before acting.
Phase 0 has no plans yet, so the default policy allows everything; Phase 1B
replaces `UnrestrictedPolicy` with a plan-backed policy and the callers stay the same.
Over the limit the API returns 402 `plan_limit_reached`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.errors import ProblemError


class Limit(StrEnum):
    RESEARCH_RUNS = "research_runs"
    LIVE_LISTINGS = "live_listings"
    SEATS = "seats"


@dataclass(frozen=True, slots=True)
class Usage:
    used: int
    allowed: int | None  # None = unlimited


class PlanLimitReached(ProblemError):
    def __init__(self, limit: Limit, used: int, allowed: int, upgrade_url: str) -> None:
        super().__init__(
            402,
            "plan_limit_reached",
            "Plan limit reached",
            f"Your plan allows {allowed} {limit.value.replace('_', ' ')}.",
            limit=limit.value,
            used=used,
            allowed=allowed,
            upgrade_url=upgrade_url,
        )


class EntitlementPolicy(Protocol):
    async def usage(
        self, session: AsyncSession, workspace_id: uuid.UUID, limit: Limit
    ) -> Usage: ...


class UnrestrictedPolicy:
    """Phase 0 placeholder: no plans exist yet, so nothing is limited."""

    async def usage(self, session: AsyncSession, workspace_id: uuid.UUID, limit: Limit) -> Usage:
        return Usage(used=0, allowed=None)


class EntitlementService:
    def __init__(self, policy: EntitlementPolicy | None = None, upgrade_url: str = "/billing"):
        self._policy = policy or UnrestrictedPolicy()
        self._upgrade_url = upgrade_url

    async def check(
        self, session: AsyncSession, workspace_id: uuid.UUID, limit: Limit, amount: int = 1
    ) -> None:
        usage = await self._policy.usage(session, workspace_id, limit)
        if usage.allowed is not None and usage.used + amount > usage.allowed:
            raise PlanLimitReached(limit, usage.used, usage.allowed, self._upgrade_url)


_service = EntitlementService()


def get_entitlements() -> EntitlementService:
    return _service
