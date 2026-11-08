"""Marketplace agent (FR-1, FR-12, ADR-0019): how crowded each rising search is.

For the top `RESEARCH_MARKETPLACE_TOP_N` measured candidates it fetches one Google results
overview (text ads, shopping ads and offers, results count) and computes the competition
score (0-100, lower is better). Shopping offers are demand evidence and purchase links
only: never a source to buy from (FR-11). eBay and Amazon are not used (ADR-0019).
"""

from __future__ import annotations

from functools import partial
from typing import Any

from momentsales.adapters.market_data.base import MarketDataError, SerpOverview
from momentsales.agents.base import AgentContext, AgentResult, BudgetExceededError
from momentsales.modules.research import signals
from momentsales.modules.research.market_cache import MarketGateway, MonthlyBudgetReachedError

MAX_OFFERS_SHOWN = 5


def in_band(price_minor: int | None, currency: str | None, band: dict[str, Any] | None) -> bool:
    if not band:
        return True
    if price_minor is None or currency != band.get("currency", "GBP"):
        return False
    return int(band["min_minor"]) <= price_minor <= int(band["max_minor"])


def market_summary(
    overview: SerpOverview,
    snapshot_id: str,
    band: dict[str, Any] | None,
    weights: dict[str, float],
) -> dict[str, Any]:
    gbp = [o.price_minor for o in overview.offers if o.currency == "GBP" and o.price_minor]
    merchants = {o.merchant.strip().lower() for o in overview.offers if o.merchant}
    comp = signals.competition_score(
        total_results=overview.total_results,
        paid_ads=overview.paid_ads,
        shopping_ads=overview.shopping_ads,
        merchants=len(merchants) if overview.offers else None,
        spread=signals.price_spread(gbp),
        weights=weights,
    )
    shown = [o for o in overview.offers if in_band(o.price_minor, o.currency, band) and o.url]
    return {
        "total_results": overview.total_results,
        "paid_ads": overview.paid_ads,
        "shopping_ads": overview.shopping_ads,
        "merchants": len(merchants),
        "price_min_minor": min(gbp) if gbp else None,
        "price_max_minor": max(gbp) if gbp else None,
        "currency": "GBP" if gbp else None,
        "offers": [
            {**o.model_dump(), "snapshot_id": snapshot_id} for o in shown[:MAX_OFFERS_SHOWN]
        ],
        "offers_outside_band": len(overview.offers) - len(shown) if band else 0,
        "competition_score": comp.score,
        "competition_parts": comp.parts,
        "competition_missing": comp.missing,
        "provider": overview.provider,
        "snapshot_id": snapshot_id,
        "url": overview.url,
        "fetched_at": overview.fetched_at.isoformat(),
    }


class MarketplaceAgent:
    name: str = "marketplace"
    tools: tuple[str, ...] = ("market_data",)
    timeout_seconds: float = 300.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        trend = ctx.outputs.get("trend") or {}
        candidates: list[dict[str, Any]] = [dict(c) for c in trend.get("candidates") or []]
        top = [c for c in candidates if c.get("trend") and not c["trend"]["insufficient"]][
            : ctx.settings.research_marketplace_top_n
        ]
        if not top:
            return AgentResult(
                output={"checked": 0, "errors": [], "stopped_at_cap": False},
                summary="No measured searches to check",
                degraded=True,
            )

        band = run.params.get("price_band")
        weights = dict(ctx.settings.research_competition_weights)
        port = await ctx.registry.market_data(ctx.session)
        gateway = MarketGateway(ctx, port, category=(run.params.get("category") or None))
        errors: list[str] = []
        degraded = False
        exhausted = False
        checked = 0
        for c in top:
            kw = c["keyword"]
            try:
                got = await gateway.get(
                    "serp_overview",
                    {"keyword": kw},
                    SerpOverview,
                    partial(port.serp_overview, kw, run.country),
                )
            except BudgetExceededError as exc:
                exhausted = True
                errors.append(str(exc)[:200])
                break
            except (MarketDataError, MonthlyBudgetReachedError) as exc:
                degraded = True
                errors.append(str(exc)[:200])
                continue
            c["market"] = market_summary(got.result, str(got.snapshot_id), band, weights)
            checked += 1

        for c in candidates:
            c.setdefault("market", None)  # "not available" when not checked
        demand = dict(run.demand or {})
        demand.update(
            candidates=candidates,
            market_available=checked > 0,
            stopped_at_cap=bool(demand.get("stopped_at_cap")) or exhausted,
        )
        run.demand = demand
        if checked < len(top) and not exhausted:
            degraded = True
        scores = [c["market"]["competition_score"] for c in candidates if c.get("market")]
        scored = [s for s in scores if s is not None]
        text = f"Checked competition for {checked} of {len(top)} searches"
        if scored:
            text += f"; scores {min(scored)}-{max(scored)} (lower is better)"
        if exhausted:
            text += " (stopped at the cost cap)"
        return AgentResult(
            output={"checked": checked, "errors": errors, "stopped_at_cap": exhausted},
            summary=text[:500],
            degraded=degraded,
            budget_exhausted=exhausted,
        )
