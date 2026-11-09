"""Scoring agent (FR-1, FR-3, FR-13, ADR-0021): profit forecast, dedup, demand score and
ranking for every measured search. No external calls; runs after the cost cap too."""

from __future__ import annotations

from typing import Any

from momentsales.agents.base import AgentContext, AgentResult
from momentsales.modules.billing.entitlements import platform_fee_bps
from momentsales.modules.research import profit as profits
from momentsales.modules.research import scoring
from momentsales.modules.research.supplier_rules import estimated_sell_price


def _growth(c: dict[str, Any]) -> float:
    return float((c.get("trend") or {}).get("growth_rate") or 0)


class ScoringAgent:
    name: str = "scoring"
    tools: tuple[str, ...] = ()
    timeout_seconds: float = 60.0
    runs_after_cap: bool = True

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        s = ctx.settings
        demand = dict(run.demand or {})
        candidates: list[dict[str, Any]] = [dict(c) for c in demand.get("candidates") or []]
        measured = [c for c in candidates if c.get("trend") and not c["trend"]["insufficient"]]
        if not measured:
            demand.update(ranking=[], scored=0)
            run.demand = demand
            return AgentResult(
                output={"scored": 0}, summary="No measured searches to score", degraded=True
            )

        fee_bps = await platform_fee_bps(ctx.session, run.workspace_id)
        for c in measured:
            match = (c.get("supplier") or {}).get("match") or {}
            c["profit"] = profits.profit_forecast(
                sell_price_minor=estimated_sell_price(c.get("market")),
                unit_cost_minor=match.get("unit_cost_gbp_minor"),
                shipping_minor=match.get("shipping_gbp_minor"),
                stripe_fee_bps=s.stripe_fee_bps,
                stripe_fee_fixed_minor=s.stripe_fee_fixed_minor,
                platform_fee_bps=fee_bps,
                ad_cost_bps=profits.ad_cost_bps(
                    c.get("category"), s.research_ad_cost_bps, s.research_ad_cost_bps_by_category
                ),
            )
        interests = list((run.params.get("audience") or {}).get("interests") or [])
        kept = scoring.dedup(measured)
        for c in kept:
            c["score"] = scoring.score(c, dict(s.research_score_weights), interests)
        ranked = sorted(kept, key=lambda c: (-c["score"]["score"], -_growth(c), c["keyword"]))

        by_keyword = {c["keyword"]: c for c in ranked}
        merged_into = {m: c["keyword"] for c in ranked for m in c.get("merged", [])}
        out: list[dict[str, Any]] = []
        for c in candidates:
            if c["keyword"] in by_keyword:
                out.append(by_keyword[c["keyword"]])
            elif c["keyword"] in merged_into:
                out.append({**c, "merged_into": merged_into[c["keyword"]]})
            else:
                out.append(c)
        demand.update(candidates=out, ranking=[c["keyword"] for c in ranked], scored=len(ranked))
        run.demand = demand
        top = ranked[0]
        return AgentResult(
            output={"scored": len(ranked), "merged": len(merged_into), "top": top["keyword"]},
            summary=(
                f"Scored {len(ranked)} searches"
                + (f" ({len(merged_into)} near-duplicates merged)" if merged_into else "")
                + f"; top: {top['keyword']} ({top['score']['score']}/100)"
            )[:500],
        )
