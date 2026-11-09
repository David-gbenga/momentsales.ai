"""Frankfurter v1 (docs/integrations/frankfurter.md): ECB reference rates, no key."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from momentsales.adapters.fx.base import FxError, FxRate

BASE_URL = "https://api.frankfurter.dev/v1"


class FrankfurterAdapter:
    name = "frankfurter"

    def __init__(self, http: httpx.AsyncClient, *, base_url: str = BASE_URL) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def rate(self, base: str, quote: str) -> FxRate:
        url = f"{self._base}/latest"
        try:
            response = await self._http.get(
                url, params={"base": base, "symbols": quote}, timeout=20.0
            )
        except httpx.HTTPError as exc:
            raise FxError(self.name, f"request failed: {exc}", retryable=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise FxError(self.name, f"HTTP {response.status_code}", retryable=True)
        if response.status_code != 200:
            raise FxError(self.name, f"HTTP {response.status_code}")
        try:
            body = response.json()
            value = Decimal(str(body["rates"][quote]))
            rate_date = date.fromisoformat(str(body["date"]))
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise FxError(self.name, f"unexpected response: {exc}") from exc
        if str(body.get("base")) != base or not value.is_finite() or value <= 0:
            raise FxError(self.name, "unexpected base or rate")
        return FxRate(
            provider=self.name,
            url=str(response.request.url),
            fetched_at=datetime.now(UTC),
            raw=body,
            base=base,
            quote=quote,
            rate=str(value),
            rate_date=rate_date,
        )
