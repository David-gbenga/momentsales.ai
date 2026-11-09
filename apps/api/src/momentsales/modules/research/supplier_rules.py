"""Supplier matching and the Verified UK supplier rule (FR-11, ADR-0020). Pure functions.

Money is integer minor units (GBP). Fees and allowances round up, against the seller, so
the margin check never flatters a product. A check that cannot be made ("not checked")
does not pass.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from momentsales.adapters.market_data.base import normalise_keyword
from momentsales.adapters.supplier.base import FreightOption, SupplierItem

CHECKS = ("dropship_no_moq", "auto_stock_cost", "tracked_uk", "uk_returns", "margin")
CheckResult = Literal["passed", "failed", "not_checked"]


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for t in normalise_keyword(text).split():
        if len(t) < 2:
            continue
        stem = t[:-1] if len(t) > 3 and t.endswith("s") else t
        if stem not in out:
            out.append(stem)
    return out


def match_confidence(keyword: str, title: str) -> float:
    """Share of the keyword's tokens found in the title (plurals ignored), 0-1."""
    wanted = _tokens(keyword)
    if not wanted:
        return 0.0
    have = set(_tokens(title))
    return round(sum(1 for t in wanted if t in have) / len(wanted), 3)


def qualifies(item: SupplierItem, keyword: str, country: str, min_score: float) -> bool:
    return (
        match_confidence(keyword, item.title) >= min_score
        and (item.uk_stock or 0) > 0
        and item.warehouse_country == country
        and item.variant_id is not None
    )


def best_tracked(options: list[FreightOption]) -> FreightOption | None:
    """The cheapest tracked option; with no cost known, the fastest tracked one."""
    tracked = [o for o in options if o.tracked is True]
    if not tracked:
        return None
    return min(
        tracked,
        key=lambda o: (
            o.cost_minor is None,
            o.cost_minor or 0,
            o.max_days if o.max_days is not None else math.inf,
        ),
    )


def estimated_sell_price(market: dict[str, Any] | None) -> int | None:
    """Median of the in-band GBP offer prices the Marketplace agent stored."""
    if not market:
        return None
    prices = [
        int(o["price_minor"])
        for o in market.get("offers") or []
        if o.get("currency") == "GBP" and o.get("price_minor")
    ]
    if not prices:
        return None
    return int(statistics.median_low(prices))


def _bps(amount: int, bps: int) -> int:
    """`bps` basis points of `amount`, rounded up."""
    return -(-amount * bps // 10_000)


@dataclass(frozen=True, slots=True)
class Margin:
    result: CheckResult
    sell_price_minor: int | None = None
    unit_cost_minor: int | None = None
    shipping_minor: int | None = None
    stripe_fee_minor: int | None = None
    platform_fee_minor: int | None = None
    returns_allowance_minor: int | None = None
    net_minor: int | None = None
    margin_bps: int | None = None
    floor_bps: int = 0
    missing: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "sell_price_minor": self.sell_price_minor,
            "sell_price_source": "market_median_estimate",
            "unit_cost_minor": self.unit_cost_minor,
            "shipping_minor": self.shipping_minor,
            "stripe_fee_minor": self.stripe_fee_minor,
            "platform_fee_minor": self.platform_fee_minor,
            "returns_allowance_minor": self.returns_allowance_minor,
            "net_minor": self.net_minor,
            "margin_bps": self.margin_bps,
            "floor_bps": self.floor_bps,
            "missing": self.missing,
            "currency": "GBP",
        }


def margin_check(
    *,
    sell_price_minor: int | None,
    unit_cost_minor: int | None,
    shipping_minor: int | None,
    stripe_fee_bps: int,
    stripe_fee_fixed_minor: int,
    platform_fee_bps: int,
    returns_allowance_bps: int,
    floor_bps: int,
) -> Margin:
    """Check 5: net after cost, shipping, Stripe fee, platform fee and a returns allowance
    must be at least `floor_bps` of the selling price."""
    missing = [
        name
        for name, value in (
            ("sell_price", sell_price_minor),
            ("unit_cost", unit_cost_minor),
            ("shipping", shipping_minor),
        )
        if value is None
    ]
    if missing or not sell_price_minor:
        return Margin(
            "not_checked",
            sell_price_minor=sell_price_minor,
            unit_cost_minor=unit_cost_minor,
            shipping_minor=shipping_minor,
            floor_bps=floor_bps,
            missing=missing or ["sell_price"],
        )
    assert unit_cost_minor is not None and shipping_minor is not None
    stripe = _bps(sell_price_minor, stripe_fee_bps) + stripe_fee_fixed_minor
    platform = _bps(sell_price_minor, platform_fee_bps)
    returns = _bps(sell_price_minor, returns_allowance_bps)
    net = sell_price_minor - unit_cost_minor - shipping_minor - stripe - platform - returns
    margin = net * 10_000 // sell_price_minor  # floor: never rounds a fail into a pass
    return Margin(
        "passed" if margin >= floor_bps else "failed",
        sell_price_minor=sell_price_minor,
        unit_cost_minor=unit_cost_minor,
        shipping_minor=shipping_minor,
        stripe_fee_minor=stripe,
        platform_fee_minor=platform,
        returns_allowance_minor=returns,
        net_minor=net,
        margin_bps=margin,
        floor_bps=floor_bps,
    )


def verification_results(
    evidence: dict[str, dict[str, Any]], tracked_option: bool
) -> dict[str, CheckResult]:
    """Checks 1-4 from the newest admin-recorded evidence per check. No row fails. Check 3
    also needs a tracked UK delivery option for this item."""
    out: dict[str, CheckResult] = {}
    for check in CHECKS[:4]:
        row = evidence.get(check)
        out[check] = "passed" if row and row.get("passed") else "failed"
    if not tracked_option:
        out["tracked_uk"] = "failed"
    return out


def reasons(checks: dict[str, CheckResult]) -> list[str]:
    return [f"{name}:{result}" for name, result in checks.items() if result != "passed"]


def moment_deadline(category: str | None, moments: list[dict[str, Any]], window_end: date) -> date:
    """The date a product must arrive by: the latest moment in the window whose category
    hints include the candidate's category, otherwise the window end."""
    key = (category or "").strip().lower()
    dates = [
        date.fromisoformat(m["date"])
        for m in moments
        if key and key in [h.lower() for h in m.get("category_hints") or []]
    ]
    return max(dates) if dates else window_end


def arrives_too_late(as_of: date, delivery_max_days: int | None, deadline: date) -> bool | None:
    """True when the supplier's slowest delivery lands after the deadline (FR-11)."""
    if delivery_max_days is None:
        return None
    return as_of + timedelta(days=delivery_max_days) > deadline
