"""Mock FX for PROVIDER_MODE=mock: a fixed, labelled rate (1 USD = 0.75 GBP)."""

from __future__ import annotations

from datetime import UTC, datetime

from momentsales.adapters.fx.base import FxError, FxRate

RATES = {("USD", "GBP"): "0.75", ("EUR", "GBP"): "0.85"}


class MockFxAdapter:
    name = "mock"

    async def rate(self, base: str, quote: str) -> FxRate:
        value = RATES.get((base, quote))
        if value is None:
            raise FxError(self.name, f"no mock rate for {base}/{quote}")
        now = datetime.now(UTC)
        return FxRate(
            provider=self.name,
            url=f"mock://fx/{base}-{quote}",
            fetched_at=now,
            raw={"mock": True, "base": base, "rates": {quote: value}},
            base=base,
            quote=quote,
            rate=value,
            rate_date=now.date(),
        )
