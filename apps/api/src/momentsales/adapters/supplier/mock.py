"""Mock suppliers for PROVIDER_MODE=mock (ADR-0020).

Deterministic and labelled: every URL is `mock://supplier/...` and images point at
example.com. The mock takes the configured provider's name, so switching suppliers can be
tested without keys (AC-34). "cj" prices in USD (to exercise FX); "avasam" in GBP and, like
the real adapter, quotes its published tier with no shipping cost.

Cases by keyword (others are spread across "good", "good", "low_margin" and "none"):
- good: UK stock, tracked 2-4 days, low cost
- slow: UK stock, tracked but 45-60 days (arrives too late)
- untracked: UK stock, only an untracked option
- low_margin: UK stock, cost too high for the 25% margin floor
- no_uk_stock: stock only outside the UK
- none: nothing that matches the keyword
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from momentsales.adapters.market_data.base import normalise_keyword
from momentsales.adapters.supplier.base import (
    FreightOption,
    FreightQuote,
    SupplierItem,
    SupplierOp,
    SupplierSearch,
)

CASES: dict[str, str] = {
    "heated throw": "good",
    "secret santa gifts": "good",
    "electric blanket": "slow",
    "advent calendar": "untracked",
    "christmas jumper": "low_margin",
    "fairy lights": "no_uk_stock",
    "fidget toy": "none",
}
_DEFAULT = ("good", "good", "low_margin", "none")


def _digest(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def case_of(keyword: str) -> str:
    key = normalise_keyword(keyword)
    return CASES.get(key) or _DEFAULT[_digest(key) % len(_DEFAULT)]


class MockSupplierAdapter:
    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.shared_cache = name != "avasam"
        self.currency = "GBP" if name == "avasam" else "USD"

    def estimate_micros(self, op: SupplierOp) -> int:
        return 0

    def _url(self, kind: str, ref: str) -> str:
        return f"mock://supplier/{self.name}/{kind}/{ref.replace(' ', '+')}"

    async def search(self, keyword: str, country: str, *, limit: int = 5) -> SupplierSearch:
        term = normalise_keyword(keyword)
        case = case_of(term)
        h = _digest(term)
        items: list[SupplierItem] = []
        if case == "none":
            items.append(self._item(f"{h % 1000}-x", "Mock unrelated gadget", 500, 40, country))
        else:
            cost = 1600 + h % 400 if case == "low_margin" else 300 + h % 300
            stock_country = "CN" if case == "no_uk_stock" else country
            items.append(
                self._item(
                    f"{case}-{h % 10_000}",
                    f"{term.title()} (mock supplier item)",
                    cost,
                    25 + h % 200,
                    stock_country,
                )
            )
        return SupplierSearch(
            provider=self.name,
            url=self._url("search", term),
            fetched_at=datetime.now(UTC),
            raw={"mock": True, "case": case, "items": [i.model_dump() for i in items]},
            keyword=term,
            country=country,
            items=items[:limit],
        )

    def _item(self, ref: str, title: str, cost: int, stock: int, warehouse: str) -> SupplierItem:
        return SupplierItem(
            external_id=ref,
            title=title,
            url=self._url("product", ref),
            image_url=f"https://example.com/mock/supplier/{ref}.jpg",
            unit_cost_minor=cost,
            currency=self.currency,
            warehouse_country=warehouse,
            uk_stock=stock if warehouse == "GB" else 0,
            variant_id=f"{ref}-v1",
        )

    async def freight(self, variant_id: str, dest_country: str) -> FreightQuote:
        case = variant_id.split("-", 1)[0]
        if self.name == "avasam":
            options = [
                FreightOption(
                    name="Standard tracked (published tier)", min_days=2, max_days=4, tracked=True
                )
            ]
            source = "published"
        elif case == "slow":
            options = [
                FreightOption(
                    name="Mock Tracked Economy",
                    cost_minor=250,
                    currency=self.currency,
                    min_days=45,
                    max_days=60,
                    tracked=True,
                )
            ]
            source = "api"
        elif case == "untracked":
            options = [
                FreightOption(
                    name="Mock Post",
                    cost_minor=150,
                    currency=self.currency,
                    min_days=3,
                    max_days=5,
                    tracked=None,
                )
            ]
            source = "api"
        else:
            options = [
                FreightOption(
                    name="Mock Post",
                    cost_minor=150,
                    currency=self.currency,
                    min_days=3,
                    max_days=6,
                    tracked=None,
                ),
                FreightOption(
                    name="Mock Tracked 48",
                    cost_minor=290,
                    currency=self.currency,
                    min_days=2,
                    max_days=4,
                    tracked=True,
                ),
            ]
            source = "api"
        return FreightQuote(
            provider=self.name,
            url=self._url("freight", variant_id),
            fetched_at=datetime.now(UTC),
            raw={"mock": True, "options": [o.model_dump() for o in options]},
            variant_id=variant_id,
            dest_country=dest_country,
            options=options,
            source="published" if source == "published" else "api",
        )
