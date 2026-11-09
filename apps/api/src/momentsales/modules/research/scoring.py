"""Demand score (FR-3, FR-12, ADR-0021). Pure functions.

Each part is 0-1. "sales_rank" has no data source (eBay and Amazon are not used), so it is
dropped and the remaining weights are rescaled. Any other missing part counts as 0, so a
product with no data never ranks above one with data. A crowded search has its rising part
halved (AC-25); a supplier that arrives too late scores 0 for seasonal fit (AC-21).
"""

from __future__ import annotations

from typing import Any

from momentsales.adapters.market_data.base import normalise_keyword
from momentsales.modules.research.supplier_rules import match_confidence

PARTS = ("rising", "competition", "sales_rank", "seasonal", "profit", "supplier")
NO_SOURCE = frozenset({"sales_rank"})
FULL_GROWTH = 1.0  # +100% growth scores 1
FULL_MARGIN_BPS = 4000  # 40% profit margin scores 1
AUDIENCE_MAX = 10


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def rising_part(trend: dict[str, Any] | None) -> float:
    if not trend or trend.get("growth_rate") is None:
        return 0.0
    part = _clamp(float(trend["growth_rate"]) / FULL_GROWTH)
    return part * 0.5 if trend.get("peak") else part


def competition_part(market: dict[str, Any] | None) -> float:
    if not market or market.get("competition_score") is None:
        return 0.0
    return _clamp(1 - int(market["competition_score"]) / 100)


def seasonal_part(supplier: dict[str, Any] | None) -> float:
    late = (supplier or {}).get("arrives_too_late")
    if late is True:
        return 0.0
    if late is False:
        return 1.0
    return 0.5  # delivery time unknown


def profit_part(profit: dict[str, Any] | None) -> float:
    if not profit:
        return 0.0
    return _clamp(int(profit["margin_bps"]) / FULL_MARGIN_BPS)


def supplier_part(supplier: dict[str, Any] | None) -> float:
    if not supplier or supplier.get("status") != "matched":
        return 0.0
    days = (supplier.get("match") or {}).get("delivery_max_days")
    if days is None:
        return 0.0
    if days <= 3:
        return 1.0
    if days <= 5:
        return 0.75
    if days <= 7:
        return 0.5
    return 0.2


def audience_fit(
    keyword: str, category: str | None, source: str | None, interests: list[str]
) -> int:
    """Up to +10 points when the search matches the audience's interests."""
    if not interests:
        return 0
    text = f"{keyword} {category or ''}"
    best = max((match_confidence(i, text) for i in interests), default=0.0)
    points = round(best * 5) + (5 if source == "audience" else 0)
    return min(AUDIENCE_MAX, points)


def score(
    candidate: dict[str, Any], weights: dict[str, float], interests: list[str]
) -> dict[str, Any]:
    parts = {
        "rising": rising_part(candidate.get("trend")),
        "competition": competition_part(candidate.get("market")),
        "seasonal": seasonal_part(candidate.get("supplier")),
        "profit": profit_part(candidate.get("profit")),
        "supplier": supplier_part(candidate.get("supplier")),
    }
    used = {k: float(w) for k, w in weights.items() if k in parts and k not in NO_SOURCE}
    total = sum(used.values()) or 1.0
    base = sum(parts[k] * w for k, w in used.items()) / total * 100
    fit = audience_fit(
        candidate["keyword"], candidate.get("category"), candidate.get("source"), interests
    )
    value = int(max(0, min(100, round(base + fit))))
    return {
        "score": value,
        "parts": {k: round(v, 3) for k, v in parts.items()},
        "weights": {k: round(w / total, 4) for k, w in used.items()},
        "dropped": sorted(k for k in weights if k in NO_SOURCE),
        "audience_fit": fit,
    }


def _signature(keyword: str) -> frozenset[str]:
    return frozenset(
        t[:-1] if len(t) > 3 and t.endswith("s") else t
        for t in normalise_keyword(keyword).split()
        if len(t) > 1
    )


def _data_rank(c: dict[str, Any]) -> tuple[int, int, float]:
    supplier = 1 if (c.get("supplier") or {}).get("status") == "matched" else 0
    market = 1 if c.get("market") else 0
    growth = float((c.get("trend") or {}).get("growth_rate") or 0)
    return (supplier, market, growth)


def dedup(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge near-duplicate searches into the one with more data; record merged names."""
    kept: list[dict[str, Any]] = []
    for c in candidates:
        twin = next(
            (
                k
                for k in kept
                if _signature(k["keyword"]) == _signature(c["keyword"])
                or min(
                    match_confidence(k["keyword"], c["keyword"]),
                    match_confidence(c["keyword"], k["keyword"]),
                )
                >= 0.8
            ),
            None,
        )
        if twin is None:
            kept.append(dict(c))
            continue
        merged = sorted({*twin.get("merged", []), c["keyword"]})
        if _data_rank(c) > _data_rank(twin):
            index = kept.index(twin)
            kept[index] = {**c, "merged": sorted({*merged, twin["keyword"]} - {c["keyword"]})}
        else:
            twin["merged"] = merged
    return kept
