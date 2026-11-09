"""Research runs: creation (role, country, cost cap, run metering), the season calendar and
the audience profile (FR-1, FR-2, FR-12, ADR-0018)."""

from __future__ import annotations

import statistics
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.agents.season import load_moments, moment_json
from momentsales.core.config import Settings
from momentsales.core.errors import ProblemError
from momentsales.core.ids import uuid7
from momentsales.db.research import AudienceProfile, ResearchRun, RunMode, RunStatus
from momentsales.modules.audit import service as audit
from momentsales.modules.billing import entitlements
from momentsales.modules.flags.service import COUNTRY_NAMES, FeatureFlagService
from momentsales.modules.ledger.service import uk_date
from momentsales.modules.research import season as engine

AUDIENCE_FLAG = "module.audience_research"


def _unavailable(code: str, title: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(422, code, title, detail, **extra)


async def create_run(
    session: AsyncSession,
    flags: FeatureFlagService,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    mode: RunMode,
    country: str,
    region: str | None,
    params: dict[str, Any],
    window_weeks: int,
    cost_cap_minor: int | None,
    audience_source_ids: list[uuid.UUID],
    use_audience_profile: bool,
    as_of: date | None,
    idempotency_key: str | None,
) -> tuple[ResearchRun, bool]:
    """Create a queued run and spend one research run. Returns (run, created)."""
    if idempotency_key:
        existing = await session.scalar(
            select(ResearchRun).where(
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False
    if mode is RunMode.AUDIENCE or audience_source_ids:
        effective = await flags.effective(session, workspace_id)
        if not effective.get(AUDIENCE_FLAG, False):
            raise _unavailable(
                "feature_not_available",
                "Audience research is not available yet",
                "Audience research from social accounts is on the roadmap. Use the optional "
                "'describe your audience' form with a seasonal run instead.",
            )
    enabled = await flags.enabled_countries(session)
    if country not in enabled or country not in engine.COUNTRY_HEMISPHERE:
        raise _unavailable(
            "country_not_available",
            "Country not available yet",
            f"{COUNTRY_NAMES.get(country, country)} is not available yet. "
            "momentsales.ai is UK-only at launch.",
            available=enabled,
        )
    cap = settings.run_cost_cap_minor
    if cost_cap_minor is not None and cost_cap_minor > cap:
        raise _unavailable(
            "cost_cap_too_high",
            "Cost cap too high",
            f"The most a run can cost is {cap} ({settings.run_cost_cap_currency} minor units).",
            max_cost_cap_minor=cap,
        )
    if as_of is not None and not settings.dev_tools_enabled:
        raise _unavailable(
            "as_of_not_allowed", "Date override not allowed", "as_of is a local testing aid."
        )
    if use_audience_profile:
        profile = await session.scalar(
            select(AudienceProfile).where(AudienceProfile.workspace_id == workspace_id)
        )
        if profile is not None:
            params = {**params, "audience": audience_json(profile)}
    run_id = uuid7()
    run = ResearchRun(
        id=run_id,
        workspace_id=workspace_id,
        mode=mode,
        status=RunStatus.QUEUED,
        country=country,
        region=region,
        params=params,
        window_weeks=window_weeks,
        as_of=(datetime.combine(as_of, time(12), tzinfo=UTC) if as_of else datetime.now(UTC)),
        cost_minor=0,
        cost_cap_minor=cost_cap_minor if cost_cap_minor is not None else cap,
        cost_currency=settings.run_cost_cap_currency,
        consumption_key=f"research-run-{run_id}",
        idempotency_key=idempotency_key,
        created_by=actor_user_id,
    )
    # Spend the run first: 402 plan_limit_reached rolls everything back (AC-15).
    pool = await entitlements.consume_run(session, workspace_id, run.consumption_key)
    session.add(run)
    await session.flush()
    await audit.record(
        session,
        action="research_run.created",
        object_type="research_run",
        object_id=run.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "mode": mode.value,
            "country": country,
            "window_weeks": window_weeks,
            "cost_cap_minor": run.cost_cap_minor,
            "run_pool": pool.value,
        },
    )
    return run, True


def audience_json(profile: AudienceProfile) -> dict[str, Any]:
    return {
        "age_range": profile.age_range,
        "gender_focus": profile.gender_focus,
        "interests": list(profile.interests),
        "budget_band": profile.budget_band,
        "style": profile.style,
    }


async def supplier_delivery_days(session: AsyncSession) -> int | None:
    """Median `delivery_max_days` of matched suppliers in the latest finished run (RLS
    scopes the query to the current workspace)."""
    run = await session.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.status.in_([RunStatus.COMPLETED, RunStatus.PARTIAL]),
            ResearchRun.demand.is_not(None),
        )
        .order_by(ResearchRun.finished_at.desc().nulls_last(), ResearchRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return None
    days = [
        int(c["supplier"]["match"]["delivery_max_days"])
        for c in (run.demand or {}).get("candidates") or []
        if (c.get("supplier") or {}).get("status") == "matched"
        and c["supplier"]["match"].get("delivery_max_days") is not None
    ]
    return int(statistics.median_low(days)) if days else None


async def season_calendar(
    session: AsyncSession, settings: Settings, *, country: str, on: date | None, count: int = 3
) -> dict[str, Any]:
    """FR-12, AC-26: the next retail moments with act-by dates. The delivery time is the
    median slowest delivery of the matched suppliers in the workspace's latest finished run;
    without one it is the configured assumption, labelled so."""
    today = on or await uk_date(session, datetime.now(UTC))
    matched = await supplier_delivery_days(session)
    delivery = matched if matched is not None else settings.research_assumed_delivery_days
    moments = engine.upcoming(
        await load_moments(session, country), today, delivery_max_days=delivery
    )
    return {
        "country": country,
        "as_of": today.isoformat(),
        "delivery_days": delivery,
        "delivery_source": "supplier" if matched is not None else "assumed",
        "moments": [moment_json(m) for m in moments[:count]],
    }
