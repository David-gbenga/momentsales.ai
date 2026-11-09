"""Plan limits, run metering and the subscription access gate (ADR-0015, ADR-0016).

`PlanPolicy` plugs into `EntitlementService` (modules/entitlements/service.py), so every
plan-limited action keeps calling `EntitlementService.check`. Research runs are spent with
`consume_run` (plan allowance first, then pack runs, then single-report runs) and given back
with `refund_run`; both are idempotent per key and lock the workspace's counter row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import get_settings
from momentsales.core.errors import ProblemError
from momentsales.db.billing import (
    BillingInterval,
    Plan,
    RunConsumption,
    RunCredit,
    RunPool,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
)
from momentsales.db.models import Invitation, Membership, MembershipStatus
from momentsales.modules.entitlements.service import Limit, PlanLimitReached, Usage
from momentsales.tenancy.permissions import Action

FULL_ACCESS = frozenset({SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE})
READ_ONLY = frozenset(
    {SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAUSED}
)

# Writes a workspace can only make with a trialing or active subscription. Reads and
# exports stay open (data export on exit); billing, workspace settings, removing members and
# fulfilling already-paid orders are never blocked (ADR-0016). Research runs are metered by
# consume_run instead (pay-per-report works without a plan; read-only states are refused).
GATED_ACTIONS = frozenset(
    {
        Action.MEMBERS_INVITE,
        Action.MEMBERS_CHANGE_ROLE,
        Action.LISTINGS_DRAFT,
        Action.LISTINGS_PUBLISH,
        Action.STORE_MANAGE,
        Action.LEDGER_WRITE,
        Action.RECEIPTS_UPLOAD,
        Action.INTEGRATIONS_PAYMENTS_MANAGE,
        Action.INTEGRATIONS_SUPPLIERS_MANAGE,
        Action.CHECKOUT_TEST,
        Action.SALES_RECORD,
        Action.TOUCHES_WRITE,
    }
)


@dataclass(frozen=True, slots=True)
class PlanState:
    subscription: Subscription | None
    plan: Plan | None  # the plan whose limits apply (the trial plan while trialing)

    @property
    def status(self) -> SubscriptionStatus | None:
        return self.subscription.status if self.subscription else None


async def plan_state(session: AsyncSession, workspace_id: uuid.UUID) -> PlanState:
    sub = await session.scalar(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    if sub is None:
        return PlanState(None, None)
    if sub.status is SubscriptionStatus.TRIALING:
        plan = await session.scalar(select(Plan).where(Plan.code == get_settings().trial_plan_code))
    else:
        plan = await session.get(Plan, sub.plan_id) if sub.plan_id else None
    return PlanState(sub, plan)


async def ensure_writable(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """The access gate (ADR-0016), called by `require()` for GATED_ACTIONS."""
    state = await plan_state(session, workspace_id)
    if state.status in FULL_ACCESS:
        return
    if state.status in READ_ONLY:
        raise ProblemError(
            403,
            "subscription_read_only",
            "Workspace is read-only",
            "Your subscription is "
            + (state.status.value.replace("_", " ") if state.status else "inactive")
            + ". Only the owner can fix this from the Billing page.",
            subscription_status=state.status.value if state.status else None,
        )
    raise ProblemError(
        403,
        "subscription_required",
        "Choose a plan",
        "Start a free trial or choose a plan on the Billing page to use this workspace.",
    )


def period_of(sub: Subscription | None) -> tuple[datetime, datetime]:
    """The metering period: the subscription item's current period (Stripe), else a
    rolling 30 days from now (no subscription yet)."""
    if sub and sub.current_period_start and sub.current_period_end:
        return sub.current_period_start, sub.current_period_end
    now = datetime.now(UTC).replace(microsecond=0)
    return now, now + timedelta(days=30)


def plan_run_allowance(state: PlanState) -> int:
    if state.subscription is None or state.status not in FULL_ACCESS:
        return 0
    if state.status is SubscriptionStatus.TRIALING:
        return get_settings().trial_runs
    plan = state.plan
    if plan is None or plan.research_runs is None:
        return 10**9  # unlimited
    bonus = plan.annual_bonus_runs if state.subscription.interval is BillingInterval.YEAR else 0
    return plan.research_runs + bonus


async def _counter(
    session: AsyncSession, workspace_id: uuid.UUID, sub: Subscription | None, *, lock: bool
) -> UsageCounter:
    start, end = period_of(sub)
    query = select(UsageCounter).where(
        UsageCounter.workspace_id == workspace_id, UsageCounter.period_start == start
    )
    counter = await session.scalar(query.with_for_update() if lock else query)
    if counter is None:
        counter = UsageCounter(
            workspace_id=workspace_id, period_start=start, period_end=end, research_runs_used=0
        )
        session.add(counter)
        await session.flush()
        if lock:
            counter = await session.scalar(query.with_for_update())
            assert counter is not None
    return counter


async def _credits(session: AsyncSession, workspace_id: uuid.UUID, *, lock: bool) -> RunCredit:
    query = select(RunCredit).where(RunCredit.workspace_id == workspace_id)
    credit = await session.scalar(query.with_for_update() if lock else query)
    if credit is None:
        credit = RunCredit(workspace_id=workspace_id, pack_runs=0, single_runs=0)
        session.add(credit)
        await session.flush()
    return credit


async def grant_runs(
    session: AsyncSession, workspace_id: uuid.UUID, *, pack: int = 0, single: int = 0
) -> RunCredit:
    credit = await _credits(session, workspace_id, lock=True)
    credit.pack_runs += pack
    credit.single_runs += single
    return credit


@dataclass(frozen=True, slots=True)
class RunUsage:
    plan_used: int
    plan_allowed: int | None  # None = unlimited
    pack_runs: int
    single_runs: int
    period_start: datetime
    period_end: datetime


async def run_usage(session: AsyncSession, workspace_id: uuid.UUID) -> RunUsage:
    state = await plan_state(session, workspace_id)
    counter = await _counter(session, workspace_id, state.subscription, lock=False)
    credit = await _credits(session, workspace_id, lock=False)
    allowance = plan_run_allowance(state)
    return RunUsage(
        plan_used=counter.research_runs_used,
        plan_allowed=None if allowance >= 10**9 else allowance,
        pack_runs=credit.pack_runs,
        single_runs=credit.single_runs,
        period_start=counter.period_start,
        period_end=counter.period_end,
    )


async def consume_run(session: AsyncSession, workspace_id: uuid.UUID, key: str) -> RunPool:
    """Spend one research run. Idempotent per `key`. Raises 402 when nothing is left.

    Phase 2's research runs call this before starting (and `refund_run` if a run fails
    before producing a result). Pack and single-report runs are spent only once the plan
    allowance for the period is used up; single-report runs also work without a
    subscription (pay-per-report).
    """
    existing = await session.scalar(
        select(RunConsumption).where(
            RunConsumption.workspace_id == workspace_id, RunConsumption.key == key
        )
    )
    if existing is not None and existing.refunded_at is None:
        return existing.pool
    state = await plan_state(session, workspace_id)
    if state.status in READ_ONLY:
        await ensure_writable(session, workspace_id)  # raises the read-only problem
    counter = await _counter(session, workspace_id, state.subscription, lock=True)
    credit = await _credits(session, workspace_id, lock=True)
    allowance = plan_run_allowance(state)
    if counter.research_runs_used < allowance:
        pool = RunPool.PLAN
        counter.research_runs_used += 1
    elif credit.pack_runs > 0 and state.status in FULL_ACCESS:
        pool = RunPool.PACK
        credit.pack_runs -= 1
    elif credit.single_runs > 0:
        pool = RunPool.SINGLE
        credit.single_runs -= 1
    else:
        raise PlanLimitReached(
            Limit.RESEARCH_RUNS,
            counter.research_runs_used,
            min(allowance, 10**9),
            "/app/billing",
        )
    if existing is not None:  # a refunded key spends again
        existing.pool, existing.refunded_at = pool, None
        existing.period_start = counter.period_start
    else:
        session.add(
            RunConsumption(
                workspace_id=workspace_id, key=key, pool=pool, period_start=counter.period_start
            )
        )
    await session.flush()
    return pool


async def refund_run(session: AsyncSession, workspace_id: uuid.UUID, key: str) -> bool:
    """Give back a run that failed before producing a result. Idempotent."""
    row = await session.scalar(
        select(RunConsumption)
        .where(RunConsumption.workspace_id == workspace_id, RunConsumption.key == key)
        .with_for_update()
    )
    if row is None or row.refunded_at is not None:
        return False
    if row.pool is RunPool.PLAN:
        counter = await session.scalar(
            select(UsageCounter)
            .where(
                UsageCounter.workspace_id == workspace_id,
                UsageCounter.period_start == row.period_start,
            )
            .with_for_update()
        )
        if counter is not None and counter.research_runs_used > 0:
            counter.research_runs_used -= 1
    else:
        await grant_runs(
            session,
            workspace_id,
            pack=1 if row.pool is RunPool.PACK else 0,
            single=1 if row.pool is RunPool.SINGLE else 0,
        )
    row.refunded_at = datetime.now(UTC)
    return True


async def platform_fee_bps(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """The platform fee rate on seller sales (ADR-0017): the plan's rate for any current
    subscriber (trialing, active, or past due, paused or suspended, so a failed card
    retry never raises a paying seller's fee), otherwise the no-plan rate
    (pay-per-report, cancelled or never subscribed)."""
    state = await plan_state(session, workspace_id)
    if state.status in FULL_ACCESS | READ_ONLY and state.plan is not None:
        return state.plan.platform_fee_bps
    return get_settings().platform_fee_bps_no_plan


def platform_fee_minor(gross_minor: int, bps: int) -> int:
    """Rounded down, so a seller is never over-charged."""
    return max(gross_minor, 0) * bps // 10_000


async def seats_used(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    members = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.workspace_id == workspace_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > func.now(),
        )
    )
    return int(members or 0) + int(pending or 0)


class PlanPolicy:
    """EntitlementPolicy backed by the workspace's plan (replaces UnrestrictedPolicy)."""

    async def usage(self, session: AsyncSession, workspace_id: uuid.UUID, limit: Limit) -> Usage:
        state = await plan_state(session, workspace_id)
        plan = state.plan
        if limit is Limit.SEATS:
            allowed = plan.seats if plan and state.status in FULL_ACCESS else None
            used = await seats_used(session, workspace_id)
            if state.status not in FULL_ACCESS:
                allowed = used  # no new seats without an active plan
            return Usage(used=used, allowed=allowed)
        if limit is Limit.LIVE_LISTINGS:
            # Listings arrive in Phase 3; the count starts at 0 until then.
            if state.status is SubscriptionStatus.TRIALING:
                return Usage(used=0, allowed=get_settings().trial_live_listings)
            listings = plan.live_listings if plan and state.status in FULL_ACCESS else 0
            return Usage(used=0, allowed=listings)
        usage = await run_usage(session, workspace_id)
        allowed_total = (
            None
            if usage.plan_allowed is None
            else usage.plan_allowed + usage.pack_runs + usage.single_runs
        )
        return Usage(used=usage.plan_used, allowed=allowed_total)
