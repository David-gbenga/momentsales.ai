"""Trend agent (FR-1, FR-12, ADR-0019): which searches are rising in the UK, and which have
already peaked.

1. Anchors: the run's category, the audience interests, then seed terms for the category
   hints of moments in the window, taken in turn from each group.
2. Rising related queries for up to `RESEARCH_MAX_ANCHORS` anchors.
3. Up to `RESEARCH_MAX_CANDIDATES` candidates; 12 months of weekly interest in batches.
4. Growth rate, peak ("crowded"), last-year lift for each candidate.

Every number comes from a provider response stored as a source snapshot (AC-3). Seed terms
only choose what to look up.
"""

from __future__ import annotations

from datetime import date
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.market_data.base import (
    MAX_BATCH,
    InterestResult,
    MarketDataError,
    RelatedResult,
    normalise_keyword,
)
from momentsales.agents.base import AgentContext, AgentResult, BudgetExceededError
from momentsales.db.research import CategorySeedTerm
from momentsales.modules.research import signals
from momentsales.modules.research.market_cache import MarketGateway, MonthlyBudgetReachedError

RISING_PER_ANCHOR = 2
_SKIP_WORDS = ("how ", "what ", "when ", "why ", "where ", "near me", "meaning", "lyrics")


async def load_seed_terms(
    session: AsyncSession, country: str, categories: list[str]
) -> dict[str, list[str]]:
    rows = (
        await session.scalars(
            select(CategorySeedTerm)
            .where(
                CategorySeedTerm.country == country,
                CategorySeedTerm.active,
                CategorySeedTerm.category.in_(categories),
            )
            .order_by(CategorySeedTerm.created_at, CategorySeedTerm.id)
        )
    ).all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.category, []).append(r.term)
    return out


def ordered_seeds(groups: list[tuple[str, str, list[str]]]) -> list[dict[str, str]]:
    """Round-robin across groups so every category and interest is represented."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    longest = max((len(t) for _, _, t in groups), default=0)
    for i in range(longest):
        for source, category, terms in groups:
            if i < len(terms):
                kw = normalise_keyword(terms[i])
                if kw and kw not in seen:
                    seen.add(kw)
                    out.append({"keyword": kw, "source": source, "category": category})
    return out


def usable_rising(query: str) -> bool:
    q = f"{query.lower()} "
    return 2 < len(query) <= 60 and not any(w in f" {q}" for w in _SKIP_WORDS)


def _source_ref(result: Any, snapshot_id: Any) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "snapshot_id": str(snapshot_id),
        "url": result.url,
        "fetched_at": result.fetched_at.isoformat(),
    }


class TrendAgent:
    name: str = "trend"
    tools: tuple[str, ...] = ("market_data",)
    timeout_seconds: float = 300.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        s = ctx.settings
        season = ctx.outputs.get("season") or run.season or {}
        as_of = date.fromisoformat(season.get("as_of") or ctx.as_of.isoformat())
        window_end = date.fromisoformat(season["window_end"]) if season.get("window_end") else as_of

        category = (run.params.get("category") or "").strip()
        interests = list((run.params.get("audience") or {}).get("interests") or [])
        hints: list[str] = []
        for m in season.get("moments") or []:
            hints += [h for h in m.get("category_hints") or [] if h not in hints]
        lookup = [c for c in [category.lower(), *hints] if c]
        seeds = await load_seed_terms(ctx.session, run.country, lookup)
        groups: list[tuple[str, str, list[str]]] = []
        if category:
            groups.append(("category", category, seeds.get(category.lower()) or [category]))
        groups += [("audience", i, [i]) for i in interests]
        groups += [("seed", h, seeds[h]) for h in hints if h in seeds]
        pool = ordered_seeds(groups)
        anchors = pool[: s.research_max_anchors]

        port = await ctx.registry.market_data(ctx.session)
        gateway = MarketGateway(ctx, port, category=category.lower() or None)
        degraded = False
        exhausted = False
        errors: list[str] = []
        rising: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        known = {c["keyword"] for c in pool}
        candidates: list[dict[str, Any]] = []

        try:
            for anchor in anchors:
                kw = anchor["keyword"]
                try:
                    got = await gateway.get(
                        "related_queries",
                        {"keyword": kw, "range": "3m"},
                        RelatedResult,
                        partial(port.related_queries, kw, run.country),
                    )
                except (MarketDataError, MonthlyBudgetReachedError) as exc:
                    degraded = True
                    errors.append(str(exc)[:200])
                    continue
                sources.append(_source_ref(got.result, got.snapshot_id))
                best = sorted(got.result.rising, key=lambda r: -(r.value or 0))
                added = 0
                for r in best:
                    q = normalise_keyword(r.query)
                    if added >= RISING_PER_ANCHOR or not usable_rising(q) or q in known:
                        continue
                    known.add(q)
                    added += 1
                    rising.append(
                        {
                            "keyword": q,
                            "source": "rising",
                            "category": anchor["category"],
                            "rising_from": kw,
                            "rising_value": r.value,
                            "breakout": r.breakout,
                            "rising_snapshot_id": str(got.snapshot_id),
                        }
                    )

            # Anchors first, then what is rising around them, then the other seed terms.
            candidates = (anchors + rising + pool[len(anchors) :])[: s.research_max_candidates]
            for c in candidates:
                c.setdefault("rising_from", None)
                c["trend"] = None  # "not available" until a provider answers
            for start in range(0, len(candidates), MAX_BATCH):
                batch = candidates[start : start + MAX_BATCH]
                words = [c["keyword"] for c in batch]
                try:
                    got_i = await gateway.get(
                        "trends_series",
                        {"keywords": words, "range": "12m"},
                        InterestResult,
                        partial(port.interest_over_time, words, run.country),
                    )
                except (MarketDataError, MonthlyBudgetReachedError) as exc:
                    degraded = True
                    errors.append(str(exc)[:200])
                    continue
                ref = _source_ref(got_i.result, got_i.snapshot_id)
                sources.append(ref)
                by_kw = {sr.keyword: sr for sr in got_i.result.series}
                for c in batch:
                    series = by_kw.get(c["keyword"])
                    if series is None:
                        continue
                    sig = signals.trend_signals(
                        [(p.date, p.value) for p in series.points],
                        as_of=as_of,
                        window_end=window_end,
                        recent_weeks=s.research_growth_recent_weeks,
                        baseline_weeks=s.research_growth_baseline_weeks,
                        peak_ratio=s.research_peak_ratio,
                        min_interest=s.research_min_interest,
                    )
                    c["trend"] = {
                        "growth_rate": sig.growth_rate,
                        "peak": sig.peak,
                        "last_year_lift": sig.last_year_lift,
                        "recent_mean": sig.recent_mean,
                        "baseline_mean": sig.baseline_mean,
                        "max_52w": sig.max_52w,
                        "insufficient": sig.insufficient,
                        "points": sig.points,
                        **ref,
                    }
        except BudgetExceededError as exc:
            exhausted = True
            errors.append(str(exc)[:200])
            if not candidates:  # stopped while finding rising queries
                candidates = [{**c, "rising_from": None, "trend": None} for c in anchors + rising]

        candidates = sorted(candidates, key=_rank)
        measured = [c for c in candidates if c.get("trend")]
        if not measured:
            degraded = True
        mock = any(src["url"].startswith("mock://") for src in sources)
        output: dict[str, Any] = {
            "as_of": as_of.isoformat(),
            "window_end": window_end.isoformat(),
            "anchors": [a["keyword"] for a in anchors],
            "candidates": candidates,
            "sources": sources,
            "errors": errors,
            "mock": mock,
            "stopped_at_cap": exhausted,
        }
        run.demand = {
            "as_of": output["as_of"],
            "window_end": output["window_end"],
            "candidates": candidates,
            "mock": mock,
            "stopped_at_cap": exhausted,
            "trend_available": bool(measured),
            "market_available": False,
        }
        rising_now = [c for c in measured if (c["trend"]["growth_rate"] or 0) > 0]
        crowded = [c for c in measured if c["trend"]["peak"]]
        text = (
            f"{len(measured)} of {len(candidates)} searches measured; "
            f"{len(rising_now)} rising, {len(crowded)} crowded"
        )
        if not measured:
            text = "Search trends not available"
        if exhausted:
            text += " (stopped at the cost cap)"
        return AgentResult(
            output=output, summary=text[:500], degraded=degraded, budget_exhausted=exhausted
        )


def _rank(c: dict[str, Any]) -> tuple[int, float]:
    trend = c.get("trend")
    if not trend:
        return (2, 0.0)
    if trend["insufficient"] or trend["growth_rate"] is None:
        return (1, 0.0)
    return (0, -float(trend["growth_rate"]))
