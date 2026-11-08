"""Season agent (FR-1, FR-2): current and next season for the country and hemisphere,
retail moments in the look-ahead window, and public holidays in the window."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.holidays.base import HolidaysError
from momentsales.agents.base import AgentContext, AgentResult
from momentsales.db.research import SeasonMoment
from momentsales.modules.research import season as engine


async def load_moments(session: AsyncSession, country: str) -> list[engine.MomentDef]:
    rows = (
        await session.scalars(
            select(SeasonMoment).where(SeasonMoment.country == country, SeasonMoment.active)
        )
    ).all()
    return [engine.MomentDef(r.key, r.name, r.rule, tuple(r.category_hints)) for r in rows]


def moment_json(m: engine.UpcomingMoment) -> dict[str, Any]:
    return {
        "key": m.key,
        "name": m.name,
        "date": m.date.isoformat(),
        "days_away": m.days_away,
        "act_by": m.act_by.isoformat(),
        "act_by_passed": m.act_by_passed,
        "category_hints": list(m.category_hints),
    }


def summary_json(summary: engine.SeasonSummary, assumed_delivery_days: int) -> dict[str, Any]:
    return {
        "country": summary.country,
        "hemisphere": summary.hemisphere,
        "as_of": summary.as_of.isoformat(),
        "window_weeks": summary.window_weeks,
        "window_end": summary.window_end.isoformat(),
        "current": summary.current.value,
        "next": summary.next.value,
        "current_ends": summary.current_ends.isoformat(),
        "detected": summary.detected.value,
        "overridden": summary.overridden,
        "assumed_delivery_days": assumed_delivery_days,
        "moments": [moment_json(m) for m in summary.moments],
    }


class SeasonAgent:
    name: str = "season"
    tools: tuple[str, ...] = ("holidays",)
    timeout_seconds: float = 30.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        override = run.params.get("season_override")
        delivery = ctx.settings.research_assumed_delivery_days
        summary = engine.summarise(
            run.country,
            ctx.as_of,
            await load_moments(ctx.session, run.country),
            window_weeks=run.window_weeks,
            delivery_max_days=delivery,
            override=engine.Season(override) if override else None,
        )
        output = summary_json(summary, delivery)
        found: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        degraded = False
        provider = await ctx.registry.holidays(ctx.session)
        end = summary.as_of + timedelta(weeks=run.window_weeks)
        try:
            for year in sorted({summary.as_of.year, end.year}):
                listing = await provider.holidays(run.country, year)
                ctx.record_call("holidays", provider=listing.provider, url=listing.url)
                snapshot_id = await ctx.snapshot(
                    provider=listing.provider,
                    kind="public_holidays",
                    url=listing.url,
                    payload=listing.raw,
                    fetched_at=listing.fetched_at,
                )
                sources.append(
                    {
                        "provider": listing.provider,
                        "snapshot_id": str(snapshot_id),
                        "url": listing.url,
                        "fetched_at": listing.fetched_at.isoformat(),
                    }
                )
                found += [
                    {"date": h.date.isoformat(), "name": h.name, "snapshot_id": str(snapshot_id)}
                    for h in listing.holidays
                    if summary.as_of <= h.date <= end
                ]
        except HolidaysError as exc:
            ctx.record_call("holidays", error=str(exc))
            degraded = True
        # None means "not available" (provider failed); [] means none in the window.
        output["public_holidays"] = None if degraded else sorted(found, key=lambda h: h["date"])
        output["sources"] = sources
        run.season = output
        names = ", ".join(m.name for m in summary.moments[:3]) or "no retail moments"
        text = (
            f"{summary.current.value.title()} now, {summary.next.value} next; "
            f"{len(summary.moments)} moment(s) in {run.window_weeks} weeks: {names}"
        )
        if degraded:
            text += " (public holidays not available)"
        return AgentResult(output=output, summary=text[:500], degraded=degraded)
