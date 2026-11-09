"""FX rate port (ADR-0020): converts supplier costs to GBP with a stored, dated rate."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from momentsales.adapters.market_data.base import SourcedResult


class FxError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class FxRate(SourcedResult):
    base: str
    quote: str
    rate: str  # decimal text, e.g. "0.74711": 1 base = rate quote
    rate_date: date


class FxPort(Protocol):
    name: str

    async def rate(self, base: str, quote: str) -> FxRate: ...


def convert_minor(amount_minor: int, rate: str) -> int:
    """Minor units in the base currency to minor units in the quote currency (half up).
    Both currencies have two decimals (USD, EUR, GBP)."""
    return int((Decimal(amount_minor) * Decimal(rate)).quantize(Decimal(1), ROUND_HALF_UP))
