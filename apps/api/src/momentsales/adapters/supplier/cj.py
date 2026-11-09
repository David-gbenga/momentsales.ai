"""CJ Dropshipping API 2.0 (docs/integrations/cj-dropshipping.md): one platform account
searches the catalogue for UK-warehouse stock and quotes GB-to-GB freight. Prices are USD.

The access token lasts 180 days; it is kept in Redis (or memory) until an hour before it
expires and never appears in URLs, snapshots or logs. Errors come as an envelope
`{"code", "result", "message", "data"}`; success is HTTP 200 with code 200, 0 or absent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from redis.asyncio import Redis

from momentsales.adapters.market_data.base import normalise_keyword, to_minor
from momentsales.adapters.supplier.base import (
    FreightOption,
    FreightQuote,
    SupplierError,
    SupplierItem,
    SupplierOp,
    SupplierSearch,
    SupplierVariant,
    parse_days,
)

BASE_URL = "https://developers.cjdropshipping.com/api2.0/v1"
TOKEN_PATH = "/authentication/getAccessToken"  # noqa: S105 - a path, not a secret
SEARCH_PATH = "/product/listV2"
VARIANTS_PATH = "/product/variant/query"
FREIGHT_PATH = "/logistic/freightCalculate"
TOKEN_KEY = "cj:access-token"  # noqa: S105 - a Redis key name

OK_CODES = frozenset({200, 0})
RETRYABLE = frozenset({429, 1600200, 16900500, 1608002})
NOT_FOUND = frozenset({1602000, 1602001})


class CjAdapter:
    name = "cj"
    shared_cache = True

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        *,
        redis: Redis | None = None,
        call_micros: int = 0,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._redis = redis
        self._base = base_url.rstrip("/")
        self._call_micros = call_micros
        self._token: tuple[str, datetime] | None = None
        self._lock = asyncio.Lock()

    def estimate_micros(self, op: SupplierOp) -> int:
        return self._call_micros

    # --- transport -------------------------------------------------------------------

    def _check(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 429 or response.status_code >= 500:
            raise SupplierError(self.name, f"HTTP {response.status_code}", retryable=True)
        try:
            body = response.json()
        except ValueError as exc:
            raise SupplierError(self.name, "response is not JSON", retryable=True) from exc
        if not isinstance(body, dict):
            raise SupplierError(self.name, "unexpected body")
        code = body.get("code")
        if response.status_code != 200 or (code is not None and int(code) not in OK_CODES):
            num = int(code) if code is not None else response.status_code
            message = str(body.get("message", ""))[:200]
            raise SupplierError(self.name, f"code {num}: {message}", retryable=num in RETRYABLE)
        return body

    async def _access_token(self) -> str:
        now = datetime.now(UTC)
        if self._token and self._token[1] > now:
            return self._token[0]
        async with self._lock:
            if self._redis is not None:
                cached = await self._redis.get(TOKEN_KEY)
                if cached:
                    token = cached.decode() if isinstance(cached, bytes) else str(cached)
                    self._token = (token, now + timedelta(hours=1))
                    return token
            try:
                response = await self._http.post(
                    f"{self._base}{TOKEN_PATH}", json={"apiKey": self._api_key}, timeout=30.0
                )
            except httpx.HTTPError as exc:
                raise SupplierError(
                    self.name, f"token request failed: {exc}", retryable=True
                ) from exc
            data = self._check(response).get("data") or {}
            fresh = data.get("accessToken")
            if not fresh:
                raise SupplierError(self.name, "token response has no accessToken")
            token = str(fresh)
            expires = _parse_time(data.get("accessTokenExpiryDate")) or now + timedelta(days=1)
            ttl = int((expires - now).total_seconds()) - 3600
            if ttl <= 0:
                raise SupplierError(self.name, "access token already expired")
            if self._redis is not None:
                await self._redis.set(TOKEN_KEY, token, ex=ttl)
            self._token = (str(token), now + timedelta(seconds=min(ttl, 3600)))
            return str(token)

    async def _call(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> tuple[str, dict[str, Any]]:
        token = await self._access_token()
        url = f"{self._base}{path}"
        try:
            response = await self._http.request(
                method,
                url,
                params=params,
                json=json,
                headers={"CJ-Access-Token": token},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SupplierError(self.name, f"request failed: {exc}", retryable=True) from exc
        body = self._check(response)
        return str(response.request.url), body

    # --- port ------------------------------------------------------------------------

    async def search(self, keyword: str, country: str, *, limit: int = 5) -> SupplierSearch:
        term = normalise_keyword(keyword)
        url, listing = await self._call(
            "GET",
            SEARCH_PATH,
            params={"keyWord": term, "countryCode": country, "page": 1, "size": limit},
        )
        products: list[dict[str, Any]] = []
        for block in (listing.get("data") or {}).get("content") or []:
            products.extend(block.get("productList") or [])
        items: list[SupplierItem] = []
        variant_bodies: dict[str, Any] = {}
        for product in products[:limit]:
            pid = str(product.get("id") or "")
            if not pid:
                continue
            try:
                _, body = await self._call(
                    "GET", VARIANTS_PATH, params={"pid": pid, "countryCode": country}
                )
            except SupplierError as exc:
                if exc.retryable:
                    raise
                continue  # a product removed since the search
            variant_bodies[pid] = body
            items.append(_item(product, body.get("data") or [], country))
        return SupplierSearch(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw={
                "list": listing,
                "variants": variant_bodies,
                "points": listing.get("pointsInfo"),
            },
            cost_micros=self._call_micros,
            keyword=term,
            country=country,
            items=items,
        )

    async def freight(self, variant_id: str, dest_country: str) -> FreightQuote:
        url, body = await self._call(
            "POST",
            FREIGHT_PATH,
            json={
                "startCountryCode": dest_country,
                "endCountryCode": dest_country,
                "products": [{"vid": variant_id, "quantity": 1}],
            },
        )
        options: list[FreightOption] = []
        for row in body.get("data") or []:
            name = str(row.get("logisticName") or "")
            low, high = parse_days(row.get("logisticAging"))
            options.append(
                FreightOption(
                    name=name,
                    cost_minor=to_minor(row.get("logisticPrice")),
                    currency="USD",
                    min_days=low,
                    max_days=high,
                    # The API does not say; only an explicit name counts (see the note).
                    tracked=True if "tracked" in name.lower() else None,
                )
            )
        return FreightQuote(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._call_micros,
            variant_id=variant_id,
            dest_country=dest_country,
            options=options,
        )


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _country_stock(variant: dict[str, Any], country: str) -> int:
    total = 0
    for inv in variant.get("inventories") or []:
        if str(inv.get("countryCode", "")).upper() == country:
            try:
                total += int(inv.get("totalInventoryNum") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _item(product: dict[str, Any], variants: list[dict[str, Any]], country: str) -> SupplierItem:
    parsed = [
        SupplierVariant(
            variant_id=str(v.get("vid")),
            name=v.get("variantNameEn"),
            unit_cost_minor=to_minor(v.get("variantSellPrice")),
            currency="USD",
            uk_stock=_country_stock(v, country),
        )
        for v in variants
        if v.get("vid")
    ]
    stocked = [v for v in parsed if (v.uk_stock or 0) > 0 and v.unit_cost_minor is not None]
    best = min(stocked, key=lambda v: v.unit_cost_minor or 0) if stocked else None
    return SupplierItem(
        external_id=str(product.get("id")),
        title=str(product.get("nameEn") or ""),
        url=None,  # CJ's API returns no product page URL
        image_url=product.get("bigImage"),
        unit_cost_minor=best.unit_cost_minor if best else to_minor(product.get("sellPrice")),
        currency="USD",
        warehouse_country=country if best else None,
        uk_stock=sum(v.uk_stock or 0 for v in parsed),
        variant_id=best.variant_id if best else None,
        variants=parsed,
    )
