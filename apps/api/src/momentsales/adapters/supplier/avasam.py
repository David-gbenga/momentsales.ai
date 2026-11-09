"""Avasam seller API (docs/integrations/avasam.md), with the workspace's own keys.

The API lists only the products the seller has sourced into their Avasam account, with no
keyword search, so the adapter pages through them and filters locally. Results are never
shared across workspaces (`shared_cache = False`). The API returns no currency, dispatch
country, delivery time or shipping cost:
- prices are read as GBP and the warehouse as GB (Avasam's UK-supplier model, to confirm);
- the freight quote is Avasam's published "standard tracked" tier, with no cost.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from momentsales.adapters.market_data.base import normalise_keyword, to_minor
from momentsales.adapters.supplier.base import (
    FreightOption,
    FreightQuote,
    SupplierError,
    SupplierItem,
    SupplierOp,
    SupplierSearch,
    SupplierVariant,
)

BASE_URL = "https://app.avasam.com"
TOKEN_PATH = "/api/auth/request-token"  # noqa: S105 - a path, not a secret
PRODUCTS_PATH = "/apiseeker/Products/GetSellerProductList"
STOCK_PATH = "/apiseeker/Products/SellerStockList"
PUBLISHED_TIERS_URL = "https://www.avasam.com/faqs/"
PAGE_SIZE = 100


def _tokens(text: str) -> set[str]:
    return {t.rstrip("s") for t in normalise_keyword(text).split() if len(t) > 1}


class AvasamAdapter:
    name = "avasam"
    shared_cache = False

    def __init__(
        self,
        http: httpx.AsyncClient,
        consumer_key: str,
        secret_key: str,
        *,
        base_url: str = BASE_URL,
        max_pages: int = 5,
    ) -> None:
        self._http = http
        self._consumer_key = consumer_key
        self._secret_key = secret_key
        self._base = base_url.rstrip("/")
        self._max_pages = max_pages
        self._token: tuple[str, datetime] | None = None

    def estimate_micros(self, op: SupplierOp) -> int:
        return 0

    def _check(self, response: httpx.Response) -> Any:
        if response.status_code == 429 or response.status_code >= 500:
            raise SupplierError(self.name, f"HTTP {response.status_code}", retryable=True)
        if response.status_code != 200:
            raise SupplierError(self.name, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise SupplierError(self.name, "response is not JSON", retryable=True) from exc
        if isinstance(body, dict) and int(body.get("ErrorCode") or 0) != 0:
            raise SupplierError(self.name, str(body.get("Message", "error"))[:200])
        return body

    async def verify(self) -> None:
        """Check the keys with a token call (used when a seller connects Avasam)."""
        self._token = None
        await self._access_token()

    async def _access_token(self) -> str:
        now = datetime.now(UTC)
        if self._token and self._token[1] > now:
            return self._token[0]
        try:
            response = await self._http.post(
                f"{self._base}{TOKEN_PATH}",
                json={"consumer_key": self._consumer_key, "secret_key": self._secret_key},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SupplierError(self.name, f"token request failed: {exc}", retryable=True) from exc
        body = self._check(response)
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise SupplierError(self.name, "token response has no access_token")
        expires = now + timedelta(minutes=30)
        raw_expiry = body.get("expires_at")
        if raw_expiry:
            try:
                parsed = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
                expires = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
        self._token = (str(token), expires - timedelta(minutes=1))
        return str(token)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[str, Any]:
        token = await self._access_token()
        url = f"{self._base}{path}"
        try:
            response = await self._http.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            raise SupplierError(self.name, f"request failed: {exc}", retryable=True) from exc
        return str(response.request.url), self._check(response)

    async def search(self, keyword: str, country: str, *, limit: int = 5) -> SupplierSearch:
        term = normalise_keyword(keyword)
        wanted = _tokens(term)
        pages: list[Any] = []
        products: list[dict[str, Any]] = []
        first_url = ""
        for page in range(1, self._max_pages + 1):
            url, body = await self._get(PRODUCTS_PATH, {"Page": page, "Limit": PAGE_SIZE})
            first_url = first_url or url
            pages.append(body)
            rows = _rows(body)
            products.extend(rows)
            if len(rows) < PAGE_SIZE:
                break
        _, stock_body = await self._get(STOCK_PATH)
        stock = {
            str(r.get("SKU")): int(r.get("Stock") or 0)
            for r in _rows(stock_body)
            if r.get("SKU") is not None
        }
        matches = [p for p in products if wanted & _tokens(str(p.get("Title") or ""))]
        items = [_item(p, stock) for p in matches[:limit]]
        return SupplierSearch(
            provider=self.name,
            url=first_url,
            fetched_at=datetime.now(UTC),
            raw={"pages": pages, "stock": stock_body, "matched": len(matches)},
            keyword=term,
            country=country,
            items=items,
        )

    async def freight(self, variant_id: str, dest_country: str) -> FreightQuote:
        # No freight call in the API: Avasam's published standard tracked tier, no cost.
        option = FreightOption(
            name="Standard tracked (published tier)", min_days=2, max_days=4, tracked=True
        )
        return FreightQuote(
            provider=self.name,
            url=PUBLISHED_TIERS_URL,
            fetched_at=datetime.now(UTC),
            raw={"published_tier": option.model_dump(), "source": PUBLISHED_TIERS_URL},
            variant_id=variant_id,
            dest_country=dest_country,
            options=[option],
            source="published",
        )


def _rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for key in ("Data", "data", "Products", "Items", "Result"):
            value = body.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _item(product: dict[str, Any], stock: dict[str, int]) -> SupplierItem:
    sku = str(product.get("SKU") or "")
    variants = [
        SupplierVariant(
            variant_id=str(v.get("SKU")),
            name=v.get("Title"),
            unit_cost_minor=to_minor(v.get("Price")),
            currency="GBP",
            uk_stock=stock.get(str(v.get("SKU"))),
        )
        for v in product.get("Variations") or []
        if isinstance(v, dict) and v.get("SKU")
    ]
    images = product.get("ProductImage") or []
    return SupplierItem(
        external_id=sku,
        title=str(product.get("Title") or ""),
        url=None,
        image_url=product.get("Image") or (images[0] if images else None),
        unit_cost_minor=to_minor(product.get("Price")),
        currency="GBP",
        warehouse_country="GB",
        uk_stock=stock.get(sku),
        variant_id=sku or None,
        variants=variants,
    )
