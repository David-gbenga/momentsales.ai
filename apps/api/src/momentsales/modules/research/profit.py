"""Profit forecast per sale (FR-13, ADR-0021). Pure functions, integer minor units (GBP).

profit = selling price - unit cost - shipping - Stripe fee - platform fee - ad cost
break-even ad cost = profit before ads (the most a sale can spend on ads and break even)

The selling price is the median price seen on Google and the ad cost is a configured share
of it: both are estimates and labelled so. Fees round up, against the seller.
"""

from __future__ import annotations

from typing import Any


def _bps(amount: int, bps: int) -> int:
    return -(-amount * bps // 10_000)


def ad_cost_bps(category: str | None, default_bps: int, by_category: dict[str, int]) -> int:
    key = (category or "").strip().lower()
    return int(by_category.get(key, default_bps))


def profit_forecast(
    *,
    sell_price_minor: int | None,
    unit_cost_minor: int | None,
    shipping_minor: int | None,
    stripe_fee_bps: int,
    stripe_fee_fixed_minor: int,
    platform_fee_bps: int,
    ad_cost_bps: int,
) -> dict[str, Any] | None:
    """None when any input is missing: the report then says "not available"."""
    if not sell_price_minor or unit_cost_minor is None or shipping_minor is None:
        return None
    stripe = _bps(sell_price_minor, stripe_fee_bps) + stripe_fee_fixed_minor
    platform = _bps(sell_price_minor, platform_fee_bps)
    ads = _bps(sell_price_minor, ad_cost_bps)
    before_ads = sell_price_minor - unit_cost_minor - shipping_minor - stripe - platform
    profit = before_ads - ads
    return {
        "currency": "GBP",
        "sell_price_minor": sell_price_minor,
        "sell_price_source": "market_median_estimate",
        "unit_cost_minor": unit_cost_minor,
        "shipping_minor": shipping_minor,
        "stripe_fee_minor": stripe,
        "platform_fee_minor": platform,
        "ad_cost_minor": ads,
        "ad_cost_bps": ad_cost_bps,
        "ad_cost_source": "configured_estimate",
        "profit_minor": profit,
        "break_even_ad_cost_minor": before_ads,
        "margin_bps": profit * 10_000 // sell_price_minor,
    }
