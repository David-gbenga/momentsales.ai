"""Supplier agent (FR-1, FR-11, ADR-0020): a dropship supplier with UK stock for each top
candidate, checked against the Verified UK supplier rule.

For the top `RESEARCH_SUPPLIER_TOP_N` measured candidates:
1. search the configured supplier (CJ through the shared cache; a seller's own Avasam
   products without it);
2. keep items that match the keyword, hold UK stock and ship from a GB warehouse;
3. quote GB-to-GB freight for the best few and keep the cheapest tracked option;
4. convert costs to GBP with a stored, dated FX rate;
5. apply checks 1-4 (admin-recorded evidence) and check 5 (margin), then pick the best
   verified match, or flag "no verified supplier" / "no supplier";
6. flag "arrives too late" when the slowest delivery lands after the candidate's moment.
Suppliers never fail over automatically (FR-15). Nothing is guessed: a value a provider
does not return stays None and the UI says "not available".
"""

from __future__ import annotations

from datetime import date
from functools import partial
from typing import Any

from sqlalchemy import select

from momentsales.adapters.fx.base import FxError, FxPort, FxRate, convert_minor
from momentsales.adapters.supplier.base import (
    FreightQuote,
    SupplierError,
    SupplierItem,
    SupplierNotConnectedError,
    SupplierPort,
    SupplierSearch,
)
from momentsales.agents.base import AgentContext, AgentResult, BudgetExceededError
from momentsales.db.research import SupplierVerification
from momentsales.modules.billing.entitlements import platform_fee_bps
from momentsales.modules.research import supplier_rules as rules
from momentsales.modules.research.market_cache import CachedGateway

BASE_CURRENCY = "GBP"


async def load_evidence(ctx: AgentContext, provider: str) -> dict[str, dict[str, Any]]:
    """The newest verification row per check for a provider (append-only table)."""
    rows = await ctx.session.scalars(
        select(SupplierVerification)
        .where(SupplierVerification.provider == provider)
        .order_by(SupplierVerification.verified_at.desc(), SupplierVerification.created_at.desc())
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(
            r.check.value,
            {
                "passed": r.passed,
                "evidence_url": r.evidence_url,
                "verified_at": r.verified_at.isoformat(),
            },
        )
    return out


class _Fx:
    """GBP conversion with one stored rate per currency per run."""

    def __init__(self, ctx: AgentContext, port: FxPort) -> None:
        self.port = port
        self.gateway = CachedGateway(
            ctx, provider=port.name, estimate=lambda _op: 0, tool="fx", shared=True
        )
        self.rates: dict[str, dict[str, Any] | None] = {}

    async def rate(self, currency: str) -> dict[str, Any] | None:
        if currency not in self.rates:
            try:
                got = await self.gateway.get(
                    "fx_rate",
                    {"base": currency, "quote": BASE_CURRENCY},
                    FxRate,
                    partial(self.port.rate, currency, BASE_CURRENCY),
                )
            except FxError:
                self.rates[currency] = None
            else:
                r = got.result
                self.rates[currency] = {
                    "base": r.base,
                    "quote": r.quote,
                    "rate": r.rate,
                    "rate_date": r.rate_date.isoformat(),
                    "provider": r.provider,
                    "snapshot_id": str(got.snapshot_id),
                }
        return self.rates[currency]

    async def to_gbp(
        self, amount: int | None, currency: str | None
    ) -> tuple[int | None, dict[str, Any] | None]:
        if amount is None or currency is None:
            return None, None
        if currency == BASE_CURRENCY:
            return amount, None
        fx = await self.rate(currency)
        if fx is None:
            return None, None
        return convert_minor(amount, fx["rate"]), fx


class SupplierAgent:
    name: str = "supplier"
    tools: tuple[str, ...] = ("supplier", "fx")
    timeout_seconds: float = 300.0

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        s = ctx.settings
        demand = dict(run.demand or {})
        candidates: list[dict[str, Any]] = [dict(c) for c in demand.get("candidates") or []]
        top = [c for c in candidates if c.get("trend") and not c["trend"]["insufficient"]][
            : s.research_supplier_top_n
        ]
        season = run.season or {}
        as_of = ctx.as_of
        window_end = date.fromisoformat(season["window_end"]) if season.get("window_end") else as_of
        moments = list(season.get("moments") or [])

        if not top:
            self._write(run, candidates, None, available=False)
            return AgentResult(
                output={"checked": 0, "matched": 0, "errors": [], "stopped_at_cap": False},
                summary="No measured searches to match with suppliers",
                degraded=True,
            )

        try:
            port = await ctx.registry.supplier(ctx.session, run.workspace_id)
        except SupplierNotConnectedError as exc:
            for c in candidates:
                c["supplier"] = _status("not_available", reasons=["supplier_not_connected"])
            self._write(run, candidates, exc.provider, available=False)
            return AgentResult(
                output={"checked": 0, "matched": 0, "errors": [str(exc)], "not_connected": True},
                summary=f"Connect {exc.provider.title()} to match suppliers",
                degraded=True,
            )

        fx = _Fx(ctx, await ctx.registry.fx(ctx.session))
        gateway = CachedGateway(
            ctx,
            provider=port.name,
            estimate=port.estimate_micros,
            tool="supplier",
            shared=port.shared_cache,
            category=(run.params.get("category") or None),
        )
        evidence = await load_evidence(ctx, port.name)
        fee_bps = await platform_fee_bps(ctx.session, run.workspace_id)
        errors: list[str] = []
        degraded = False
        exhausted = False
        checked = 0
        for c in top:
            try:
                c["supplier"] = await self._match(
                    ctx, c, port, gateway, fx, evidence, fee_bps, as_of, window_end, moments
                )
                checked += 1
            except BudgetExceededError as exc:
                exhausted = True
                errors.append(str(exc)[:200])
                break
            except SupplierError as exc:
                degraded = True
                errors.append(str(exc)[:200])
                c["supplier"] = _status("not_available", reasons=["provider_error"])

        for c in candidates:
            c.setdefault("supplier", _status("not_checked"))
        self._write(run, candidates, port.name, available=checked > 0, exhausted=exhausted)
        statuses = [c["supplier"]["status"] for c in candidates]
        matched = statuses.count("matched")
        late = sum(1 for c in candidates if c["supplier"].get("arrives_too_late"))
        text = (
            f"Matched a verified UK supplier for {matched} of {len(top)} searches "
            f"({port.name}); {statuses.count('no_verified_supplier')} without a verified "
            f"supplier, {statuses.count('no_supplier')} with none"
        )
        if late:
            text += f"; {late} would arrive too late"
        if exhausted:
            text += " (stopped at the cost cap)"
        return AgentResult(
            output={
                "provider": port.name,
                "checked": checked,
                "matched": matched,
                "errors": errors,
                "stopped_at_cap": exhausted,
            },
            summary=text[:500],
            degraded=degraded or checked == 0,
            budget_exhausted=exhausted,
        )

    @staticmethod
    def _write(
        run: Any,
        candidates: list[dict[str, Any]],
        provider: str | None,
        *,
        available: bool,
        exhausted: bool = False,
    ) -> None:
        demand = dict(run.demand or {})
        demand.update(
            candidates=candidates,
            supplier_available=available,
            supplier_provider=provider,
            stopped_at_cap=bool(demand.get("stopped_at_cap")) or exhausted,
        )
        run.demand = demand

    async def _match(
        self,
        ctx: AgentContext,
        c: dict[str, Any],
        port: SupplierPort,
        gateway: CachedGateway,
        fx: _Fx,
        evidence: dict[str, dict[str, Any]],
        fee_bps: int,
        as_of: date,
        window_end: date,
        moments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        s = ctx.settings
        country = ctx.run.country
        kw = c["keyword"]
        got = await gateway.get(
            "supplier_search",
            {"keyword": kw, "country": country},
            SupplierSearch,
            partial(port.search, kw, country),
        )
        snapshots = [str(got.snapshot_id)]
        items = sorted(
            (
                i
                for i in got.result.items
                if rules.qualifies(i, kw, country, s.supplier_match_min_score)
            ),
            key=lambda i: (-rules.match_confidence(kw, i.title), i.unit_cost_minor or 0),
        )
        if not items:
            similar = [
                i
                for i in got.result.items
                if rules.match_confidence(kw, i.title) >= s.supplier_match_min_score
            ]
            why = ["no_uk_stock"] if similar else ["no_match"]
            return _status("no_supplier", reasons=why, snapshot_ids=snapshots)

        sell = rules.estimated_sell_price(c.get("market"))
        deadline = rules.moment_deadline(c.get("category"), moments, window_end)
        evaluated: list[dict[str, Any]] = []
        for item in items[: s.supplier_freight_candidates]:
            assert item.variant_id is not None
            quote = await gateway.get(
                "supplier_freight",
                {"variant_id": item.variant_id, "dest": country},
                FreightQuote,
                partial(port.freight, item.variant_id, country),
            )
            snapshots.append(str(quote.snapshot_id))
            evaluated.append(
                await self._evaluate(
                    ctx,
                    kw,
                    item,
                    got.result,
                    quote.result,
                    str(got.snapshot_id),
                    str(quote.snapshot_id),
                    fx,
                    evidence,
                    fee_bps,
                    sell,
                    as_of,
                    deadline,
                )
            )

        verified = [m for m in evaluated if m["verified"]]
        pool = verified or evaluated
        best = min(
            pool,
            key=lambda m: (
                -m["confidence"],
                m["landed_cost_minor"] if m["landed_cost_minor"] is not None else 10**12,
            ),
        )
        return {
            "status": "matched" if verified else "no_verified_supplier",
            "match": best,
            "reasons": [] if verified else rules.reasons(best["checks"]),
            "arrives_too_late": best["arrives_too_late"],
            "deadline": deadline.isoformat(),
            "snapshot_ids": snapshots,
        }

    async def _evaluate(
        self,
        ctx: AgentContext,
        kw: str,
        item: SupplierItem,
        search: SupplierSearch,
        quote: FreightQuote,
        search_snapshot: str,
        quote_snapshot: str,
        fx: _Fx,
        evidence: dict[str, dict[str, Any]],
        fee_bps: int,
        sell: int | None,
        as_of: date,
        deadline: date,
    ) -> dict[str, Any]:
        s = ctx.settings
        option = rules.best_tracked(quote.options)
        unit_gbp, unit_fx = await fx.to_gbp(item.unit_cost_minor, item.currency)
        ship_gbp, ship_fx = (
            await fx.to_gbp(option.cost_minor, option.currency) if option else (None, None)
        )
        checks = rules.verification_results(evidence, tracked_option=option is not None)
        margin = rules.margin_check(
            sell_price_minor=sell,
            unit_cost_minor=unit_gbp,
            shipping_minor=ship_gbp,
            stripe_fee_bps=s.stripe_fee_bps,
            stripe_fee_fixed_minor=s.stripe_fee_fixed_minor,
            platform_fee_bps=fee_bps,
            returns_allowance_bps=s.supplier_returns_allowance_bps,
            floor_bps=s.supplier_min_margin_bps,
        )
        checks["margin"] = margin.result
        max_days = option.max_days if option else None
        landed = unit_gbp + ship_gbp if unit_gbp is not None and ship_gbp is not None else None
        return {
            "provider": search.provider,
            "external_id": item.external_id,
            "variant_id": item.variant_id,
            "title": item.title,
            "url": item.url,
            "image_url": item.image_url,
            "confidence": rules.match_confidence(kw, item.title),
            "unit_cost": {"amount_minor": item.unit_cost_minor, "currency": item.currency},
            "unit_cost_gbp_minor": unit_gbp,
            "shipping": (
                {
                    "name": option.name,
                    "amount_minor": option.cost_minor,
                    "currency": option.currency,
                    "source": quote.source,
                }
                if option
                else None
            ),
            "shipping_gbp_minor": ship_gbp,
            "landed_cost_minor": landed,
            "fx": unit_fx or ship_fx,
            "delivery_min_days": option.min_days if option else None,
            "delivery_max_days": max_days,
            "warehouse_country": item.warehouse_country,
            "uk_stock": item.uk_stock,
            "variants": [v.model_dump() for v in item.variants][:20],
            "checks": checks,
            "evidence": evidence,
            "margin": margin.as_json(),
            "verified": all(v == "passed" for v in checks.values()),
            "arrives_too_late": rules.arrives_too_late(as_of, max_days, deadline),
            "search_snapshot_id": search_snapshot,
            "freight_snapshot_id": quote_snapshot,
            "fetched_at": search.fetched_at.isoformat(),
        }


def _status(
    status: str, *, reasons: list[str] | None = None, snapshot_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "status": status,
        "match": None,
        "reasons": reasons or [],
        "arrives_too_late": None,
        "deadline": None,
        "snapshot_ids": snapshot_ids or [],
    }
