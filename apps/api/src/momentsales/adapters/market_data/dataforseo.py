"""DataForSEO v3 (docs/integrations/dataforseo.md): Google Trends Explore live and SERP
Google organic live advanced. HTTP Basic auth; errors are mostly HTTP 200 with a body
`status_code`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from momentsales.adapters.market_data.base import (
    MAX_BATCH,
    DataType,
    InterestResult,
    InterestSeries,
    MarketDataError,
    RelatedQuery,
    RelatedResult,
    SerpOverview,
    ShoppingOffer,
    TrendPoint,
    normalise_keyword,
    to_minor,
)

BASE_URL = "https://api.dataforseo.com/v3"
TRENDS_PATH = "/keywords_data/google_trends/explore/live"
SERP_PATH = "/serp/google/organic/live/advanced"

LOCATIONS = {"GB": "United Kingdom"}

OK = 20000
# Rate limit, too many simultaneous requests, and server-side failures.
RETRYABLE = frozenset({40202, 40209, 50000, 50301, 50303, 50401, 50402})


class DataForSeoAdapter:
    name = "dataforseo"

    def __init__(
        self,
        http: httpx.AsyncClient,
        login: str,
        password: str,
        *,
        trends_micros: int,
        serp_micros: int,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._auth = httpx.BasicAuth(login, password)
        self._base = base_url.rstrip("/")
        self._costs: dict[DataType, int] = {
            "trends_series": trends_micros,
            "related_queries": trends_micros,
            "serp_overview": serp_micros,
        }

    def estimate_micros(self, data_type: DataType) -> int:
        return self._costs[data_type]

    @staticmethod
    def _location(country: str) -> str:
        try:
            return LOCATIONS[country]
        except KeyError as exc:
            raise MarketDataError("dataforseo", f"country {country} not supported") from exc

    async def _post(self, path: str, task: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
        url = f"{self._base}{path}"
        try:
            response = await self._http.post(url, json=[task], auth=self._auth, timeout=60.0)
        except httpx.HTTPError as exc:
            raise MarketDataError(self.name, f"request failed: {exc}", retryable=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise MarketDataError(self.name, f"HTTP {response.status_code}", retryable=True)
        if response.status_code != 200:
            raise MarketDataError(self.name, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise MarketDataError(self.name, "response is not JSON", retryable=True) from exc
        for code, message in self._codes(body):
            if code != OK:
                raise MarketDataError(
                    self.name, f"status {code}: {message}", retryable=code in RETRYABLE
                )
        try:
            result = body["tasks"][0]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise MarketDataError(self.name, "response has no result") from exc
        return url, result, body

    @staticmethod
    def _codes(body: Any) -> list[tuple[int, str]]:
        if not isinstance(body, dict):
            return [(0, "unexpected body")]
        codes = [(int(body.get("status_code", 0)), str(body.get("status_message", "")))]
        for task in body.get("tasks") or []:
            codes.append((int(task.get("status_code", 0)), str(task.get("status_message", ""))))
        return codes

    @staticmethod
    def _items(result: dict[str, Any], item_type: str) -> list[dict[str, Any]]:
        return [i for i in result.get("items") or [] if i.get("type") == item_type]

    async def interest_over_time(self, keywords: list[str], country: str) -> InterestResult:
        terms = [normalise_keyword(k) for k in keywords][:MAX_BATCH]
        url, result, body = await self._post(
            TRENDS_PATH,
            {
                "keywords": terms,
                "location_name": self._location(country),
                "language_code": "en",
                "type": "web",
                "time_range": "past_12_months",
                "item_types": ["google_trends_graph"],
            },
        )
        points: list[list[TrendPoint]] = [[] for _ in terms]
        try:
            for graph in self._items(result, "google_trends_graph"):
                for row in graph.get("data") or []:
                    day = date.fromisoformat(str(row["date_from"])[:10])
                    values = row.get("values") or []
                    for i in range(len(terms)):
                        raw = values[i] if i < len(values) else None
                        value = None if raw is None or row.get("missing_data") else int(raw)
                        points[i].append(TrendPoint(date=day, value=value))
        except (KeyError, ValueError, TypeError) as exc:
            raise MarketDataError(self.name, f"unexpected trends data: {exc}") from exc
        return InterestResult(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._costs["trends_series"],
            series=[
                InterestSeries(keyword=t, points=p) for t, p in zip(terms, points, strict=True)
            ],
        )

    async def related_queries(self, keyword: str, country: str) -> RelatedResult:
        term = normalise_keyword(keyword)
        url, result, body = await self._post(
            TRENDS_PATH,
            {
                "keywords": [term],
                "location_name": self._location(country),
                "language_code": "en",
                "type": "web",
                "time_range": "past_90_days",
                "item_types": ["google_trends_queries_list"],
            },
        )
        rising: list[RelatedQuery] = []
        try:
            for item in self._items(result, "google_trends_queries_list"):
                for row in (item.get("data") or {}).get("rising") or []:
                    value = row.get("value")
                    rising.append(
                        RelatedQuery(
                            query=str(row["query"]),
                            value=int(value) if value is not None else None,
                        )
                    )
        except (KeyError, ValueError, TypeError) as exc:
            raise MarketDataError(self.name, f"unexpected queries data: {exc}") from exc
        return RelatedResult(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._costs["related_queries"],
            keyword=term,
            rising=rising,
        )

    async def serp_overview(self, keyword: str, country: str) -> SerpOverview:
        term = normalise_keyword(keyword)
        url, result, body = await self._post(
            SERP_PATH,
            {
                "keyword": term,
                "location_name": self._location(country),
                "language_code": "en",
                "device": "desktop",
                "depth": 10,
            },
        )
        paid = len(self._items(result, "paid"))
        shopping_ads = 0
        offers: list[ShoppingOffer] = []
        for block in self._items(result, "shopping"):
            for el in block.get("items") or []:
                shopping_ads += 1
                offers.append(_offer(el, el.get("source")))
        for block in self._items(result, "popular_products"):
            for el in block.get("items") or []:
                offers.append(_offer(el, el.get("seller") or el.get("domain")))
        total = result.get("se_results_count")
        return SerpOverview(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._costs["serp_overview"],
            keyword=term,
            total_results=int(total) if isinstance(total, int | float) else None,
            paid_ads=paid,
            shopping_ads=shopping_ads,
            offers=offers,
        )


def _offer(el: dict[str, Any], merchant: Any) -> ShoppingOffer:
    price = el.get("price") if isinstance(el.get("price"), dict) else {}
    currency = price.get("currency") if isinstance(price.get("currency"), str) else None
    minor = to_minor(price.get("current"))
    return ShoppingOffer(
        title=str(el.get("title") or "")[:300],
        merchant=str(merchant)[:120] if merchant else None,
        price_minor=minor if currency else None,
        currency=currency.upper() if currency and minor is not None else None,
        url=str(el["url"]) if el.get("url") else None,
    )
