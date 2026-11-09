"""Dropship supplier port (FR-11, FR-15, ADR-0020): catalogue search and freight quotes.

Results carry what a source snapshot needs (AC-3): the provider, a URL without secrets,
the fetch time and the raw response. Money is integer minor units plus a currency code;
a value the provider does not return stays None ("not available"), never guessed.
`shared_cache` says whether results may enter the shared, cross-workspace cache: CJ's
platform catalogue may; a seller's own Avasam products never do.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from momentsales.adapters.market_data.base import SourcedResult

SupplierOp = Literal["supplier_search", "supplier_freight"]


class SupplierError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class SupplierNotConnectedError(SupplierError):
    """The workspace has not connected its own account for a per-seller supplier."""


class SupplierVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant_id: str
    name: str | None = None
    unit_cost_minor: int | None = None
    currency: str | None = None
    uk_stock: int | None = None


class SupplierItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    title: str
    url: str | None = None
    image_url: str | None = None
    unit_cost_minor: int | None = None
    currency: str | None = None
    warehouse_country: str | None = None
    uk_stock: int | None = None
    variant_id: str | None = None  # the variant used for freight quotes
    variants: list[SupplierVariant] = []


class SupplierSearch(SourcedResult):
    keyword: str
    country: str
    items: list[SupplierItem]


class FreightOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    cost_minor: int | None = None
    currency: str | None = None
    min_days: int | None = None
    max_days: int | None = None
    tracked: bool | None = None  # None: the provider does not say


class FreightQuote(SourcedResult):
    variant_id: str
    dest_country: str
    options: list[FreightOption]
    # "api": from the provider's freight endpoint; "published": from the provider's
    # published service tiers (URL in `url`), when its API has no freight call.
    source: Literal["api", "published"] = "api"


class SupplierPort(Protocol):
    name: str
    shared_cache: bool

    def estimate_micros(self, op: SupplierOp) -> int:
        """Expected GBP cost of one call, for the run's cost cap."""
        ...

    async def search(self, keyword: str, country: str, *, limit: int = 5) -> SupplierSearch: ...

    async def freight(self, variant_id: str, dest_country: str) -> FreightQuote: ...


# "2-5", "2 to 5", or a dash variant between two numbers.
_DAYS = re.compile(r"(\d+)(?:\s*(?:to|\D)\s*(\d+))?")


def parse_days(text: object) -> tuple[int | None, int | None]:
    """A delivery range such as "2-5" or "7" to (min, max) days. (None, None) if unusable."""
    if text is None:
        return None, None
    match = _DAYS.search(str(text))
    if not match:
        return None, None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    return (low, high) if low <= high else (high, low)
