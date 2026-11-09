"""Market-data port (FR-12, FR-15, ADR-0019): Google Trends interest, rising related
queries and a Google results overview, through a licensed provider.

Provider-neutral so Google's official Trends API can be added later. Results are Pydantic
models so the shared cache can store and reload them. Every result carries what a source
snapshot needs (AC-3): the provider that answered, a URL without secrets, the fetch time
and the raw response.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

DataType = Literal["trends_series", "related_queries", "serp_overview"]

# Max keywords per interest-over-time request (both providers).
MAX_BATCH = 5


class MarketDataError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    url: str
    fetched_at: datetime
    raw: Any
    cost_micros: int = 0


# Public name for other ports whose results are stored as source snapshots (AC-3).
SourcedResult = _Result


class TrendPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    value: int | None  # 0-100; None where the provider has too little data


class InterestSeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    keyword: str
    points: list[TrendPoint]


class InterestResult(_Result):
    series: list[InterestSeries]


class RelatedQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    value: int | None  # rise in percent
    breakout: bool = False


class RelatedResult(_Result):
    keyword: str
    rising: list[RelatedQuery]


class ShoppingOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    merchant: str | None
    price_minor: int | None
    currency: str | None
    url: str | None


class SerpOverview(_Result):
    keyword: str
    total_results: int | None
    paid_ads: int
    shopping_ads: int
    offers: list[ShoppingOffer]


class MarketDataPort(Protocol):
    name: str

    def estimate_micros(self, data_type: DataType) -> int:
        """Expected GBP cost of one call, for the run's cost cap."""
        ...

    async def interest_over_time(self, keywords: list[str], country: str) -> InterestResult: ...

    async def related_queries(self, keyword: str, country: str) -> RelatedResult: ...

    async def serp_overview(self, keyword: str, country: str) -> SerpOverview: ...


_UNSAFE = re.compile(r"[-+=~!:*()\[\]<>|\",]")
_SPACES = re.compile(r"\s+")


def normalise_keyword(text: str) -> str:
    """Lower-case, strip symbols providers reject, collapse spaces, max 100 characters."""
    return _SPACES.sub(" ", _UNSAFE.sub(" ", text.lower())).strip()[:100]


def to_minor(amount: float | int | str | None) -> int | None:
    """A provider's decimal price to integer minor units (half up). None if unusable."""
    if amount is None or isinstance(amount, bool):
        return None
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}


def currency_from_text(text: str | None) -> str | None:
    """Currency from a displayed price ("£19.99"). Never guessed: None if no known symbol."""
    if not text:
        return None
    for symbol, code in _SYMBOLS.items():
        if symbol in text:
            return code
    upper = text.upper()
    for code in ("GBP", "EUR", "USD"):
        if code in upper:
            return code
    return None
