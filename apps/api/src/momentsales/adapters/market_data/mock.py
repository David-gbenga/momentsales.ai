"""Mock market data for PROVIDER_MODE=mock (ADR-0019).

Deterministic and clearly labelled: every URL is `mock://market-data/...` and every
merchant link points at example.com. The mock carries the configured provider's name and
charges that provider's cost estimates, so switching, failover and the run's cost cap can
be tested without keys. Shapes (52 weekly points, oldest first, last year's Christmas
near the start):
- rising: flat, then climbing over the last 9 weeks, below last year's peak
- peaked: at its 52-week high now ("crowded")
- flat, falling, low (too little interest to trust)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from momentsales.adapters.market_data.base import (
    MAX_BATCH,
    DataType,
    InterestResult,
    InterestSeries,
    RelatedQuery,
    RelatedResult,
    SerpOverview,
    ShoppingOffer,
    TrendPoint,
    normalise_keyword,
)

WEEKS = 52

# Fixed shapes for some UK seed terms, so tests and demos are predictable.
SHAPES: dict[str, str] = {
    "heated throw": "rising",
    "electric blanket": "rising",
    "advent calendar": "peaked",
    "christmas jumper": "peaked",
    "secret santa gifts": "rising",
    "fairy lights": "flat",
    "halloween costume": "falling",
    "fidget toy": "low",
}
_DEFAULT_ORDER = ("rising", "flat", "peaked", "falling")


def _digest(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def shape_of(keyword: str) -> str:
    key = normalise_keyword(keyword)
    return SHAPES.get(key) or _DEFAULT_ORDER[_digest(key) % len(_DEFAULT_ORDER)]


def _value(shape: str, i: int) -> int:
    last_christmas = 3 <= i <= 8
    if shape == "rising":
        return 100 if last_christmas else (20 if i < 43 else 20 + (i - 42) * 6)
    if shape == "peaked":
        return 70 if last_christmas else (30 if i < 44 else min(100, 30 + (i - 43) * 20))
    if shape == "flat":
        return 55 if last_christmas else 40 + (i % 3)
    if shape == "falling":
        return 50 if last_christmas else max(10, 70 - i)
    return i % 2  # low


class MockMarketDataAdapter:
    def __init__(self, name: str = "mock", costs: dict[DataType, int] | None = None) -> None:
        self.name = name
        self._costs: dict[DataType, int] = costs or {}

    def estimate_micros(self, data_type: DataType) -> int:
        return self._costs.get(data_type, 0)

    def _url(self, kind: str, term: str) -> str:
        return f"mock://market-data/{self.name}/{kind}?q={term.replace(' ', '+')}"

    async def interest_over_time(self, keywords: list[str], country: str) -> InterestResult:
        terms = [normalise_keyword(k) for k in keywords][:MAX_BATCH]
        today = datetime.now(UTC).date()
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
        series = [
            InterestSeries(
                keyword=t,
                points=[
                    TrendPoint(date=start + timedelta(weeks=i), value=_value(shape_of(t), i))
                    for i in range(WEEKS)
                ],
            )
            for t in terms
        ]
        raw = {"mock": True, "series": [s.model_dump(mode="json") for s in series]}
        return InterestResult(
            provider=self.name,
            url=self._url("trends", ",".join(terms)),
            fetched_at=datetime.now(UTC),
            raw=raw,
            cost_micros=self.estimate_micros("trends_series"),
            series=series,
        )

    async def related_queries(self, keyword: str, country: str) -> RelatedResult:
        term = normalise_keyword(keyword)
        rising = [
            RelatedQuery(query=f"personalised {term}", value=250),
            RelatedQuery(query=f"{term} uk", value=120),
        ]
        return RelatedResult(
            provider=self.name,
            url=self._url("related", term),
            fetched_at=datetime.now(UTC),
            raw={"mock": True, "rising": [r.model_dump() for r in rising]},
            cost_micros=self.estimate_micros("related_queries"),
            keyword=term,
            rising=rising,
        )

    async def serp_overview(self, keyword: str, country: str) -> SerpOverview:
        term = normalise_keyword(keyword)
        h = _digest(term)
        crowded = shape_of(term) == "peaked"
        n_offers = 6 if crowded else 2 + h % 4
        base = 1200 + h % 3300
        offers = [
            ShoppingOffer(
                title=f"{term.title()} (mock offer {i + 1})",
                merchant=f"Mock retailer {i + 1}",
                price_minor=base + (i * 37 if crowded else i * 450),
                currency="GBP",
                url=f"https://example.com/mock/{term.replace(' ', '-')}/{i + 1}",
            )
            for i in range(n_offers)
        ]
        return SerpOverview(
            provider=self.name,
            url=self._url("serp", term),
            fetched_at=datetime.now(UTC),
            raw={"mock": True, "offers": [o.model_dump() for o in offers]},
            cost_micros=self.estimate_micros("serp_overview"),
            keyword=term,
            total_results=(40_000_000 if crowded else 300_000 + h % 2_000_000),
            paid_ads=4 if crowded else h % 3,
            shopping_ads=n_offers,
            offers=offers,
        )
