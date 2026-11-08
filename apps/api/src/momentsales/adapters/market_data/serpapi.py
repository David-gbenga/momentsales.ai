"""SerpApi (docs/integrations/serpapi.md): engine=google_trends (TIMESERIES,
RELATED_QUERIES) and engine=google (ads, shopping_results). The api_key is a query
parameter, so it is removed from every URL we store."""

from __future__ import annotations

from datetime import UTC, datetime
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
    currency_from_text,
    normalise_keyword,
    to_minor,
)

BASE_URL = "https://serpapi.com"
NO_RESULTS = "hasn't returned any results"

# Google Trends uses ISO codes; Google Search uses "uk" for the United Kingdom.
TRENDS_GEO = {"GB": "GB"}
SEARCH = {"GB": {"gl": "uk", "hl": "en", "google_domain": "google.co.uk"}}


class SerpApiAdapter:
    name = "serpapi"

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        *,
        search_micros: int,
        base_url: str = BASE_URL,
    ) -> None:
        self._http = http
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._micros = search_micros

    def estimate_micros(self, data_type: DataType) -> int:
        return self._micros

    async def _get(self, params: dict[str, str]) -> tuple[str, dict[str, Any]]:
        endpoint = f"{self._base}/search.json"
        public_url = str(httpx.URL(endpoint, params=params))  # no api_key: safe to store
        try:
            response = await self._http.get(
                endpoint, params={**params, "api_key": self._key}, timeout=60.0
            )
        except httpx.HTTPError as exc:
            raise MarketDataError(
                self.name, f"request failed: {type(exc).__name__}", retryable=True
            ) from exc
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if status == 429 or status >= 500:
            raise MarketDataError(
                self.name, f"HTTP {status}: {error or ''}".strip(), retryable=True
            )
        if status != 200 or not isinstance(body, dict):
            raise MarketDataError(self.name, f"HTTP {status}: {error or 'unexpected body'}")
        if error and NO_RESULTS not in str(error):
            raise MarketDataError(self.name, str(error)[:300])
        params_echo = body.get("search_parameters")
        if isinstance(params_echo, dict):
            params_echo.pop("api_key", None)  # never store the key in a snapshot
        return public_url, body

    @staticmethod
    def _geo(country: str) -> str:
        if country not in TRENDS_GEO:
            raise MarketDataError("serpapi", f"country {country} not supported")
        return TRENDS_GEO[country]

    async def interest_over_time(self, keywords: list[str], country: str) -> InterestResult:
        terms = [normalise_keyword(k) for k in keywords][:MAX_BATCH]
        url, body = await self._get(
            {
                "engine": "google_trends",
                "data_type": "TIMESERIES",
                "q": ",".join(terms),
                "geo": self._geo(country),
                "date": "today 12-m",
                "tz": "0",
            }
        )
        points: list[list[TrendPoint]] = [[] for _ in terms]
        try:
            timeline = (body.get("interest_over_time") or {}).get("timeline_data") or []
            for row in timeline:
                if row.get("partial_data"):
                    continue  # the unfinished current week
                day = datetime.fromtimestamp(int(row["timestamp"]), UTC).date()
                values = row.get("values") or []
                for i in range(len(terms)):
                    v = values[i].get("extracted_value") if i < len(values) else None
                    points[i].append(TrendPoint(date=day, value=int(v) if v is not None else None))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise MarketDataError(self.name, f"unexpected trends data: {exc}") from exc
        return InterestResult(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._micros,
            series=[
                InterestSeries(keyword=t, points=p) for t, p in zip(terms, points, strict=True)
            ],
        )

    async def related_queries(self, keyword: str, country: str) -> RelatedResult:
        term = normalise_keyword(keyword)
        url, body = await self._get(
            {
                "engine": "google_trends",
                "data_type": "RELATED_QUERIES",
                "q": term,  # single query only for this data type
                "geo": self._geo(country),
                "date": "today 3-m",
                "tz": "0",
            }
        )
        rising: list[RelatedQuery] = []
        try:
            for row in (body.get("related_queries") or {}).get("rising") or []:
                v = row.get("extracted_value")
                rising.append(
                    RelatedQuery(
                        query=str(row["query"]),
                        value=int(v) if v is not None else None,
                        breakout=str(row.get("value", "")).lower() == "breakout",
                    )
                )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise MarketDataError(self.name, f"unexpected queries data: {exc}") from exc
        return RelatedResult(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._micros,
            keyword=term,
            rising=rising,
        )

    async def serp_overview(self, keyword: str, country: str) -> SerpOverview:
        if country not in SEARCH:
            raise MarketDataError(self.name, f"country {country} not supported")
        term = normalise_keyword(keyword)
        url, body = await self._get({"engine": "google", "q": term, **SEARCH[country]})
        ads = body.get("ads") or []
        shopping = body.get("shopping_results") or []
        offers: list[ShoppingOffer] = []
        for el in shopping if isinstance(shopping, list) else []:
            currency = currency_from_text(el.get("price"))
            minor = to_minor(el.get("extracted_price")) if currency else None
            offers.append(
                ShoppingOffer(
                    title=str(el.get("title") or "")[:300],
                    merchant=str(el["source"])[:120] if el.get("source") else None,
                    price_minor=minor,
                    currency=currency if minor is not None else None,
                    url=str(el["link"]) if el.get("link") else None,
                )
            )
        total = (body.get("search_information") or {}).get("total_results")
        return SerpOverview(
            provider=self.name,
            url=url,
            fetched_at=datetime.now(UTC),
            raw=body,
            cost_micros=self._micros,
            keyword=term,
            total_results=int(total) if isinstance(total, int | float) else None,
            paid_ads=len(ads) if isinstance(ads, list) else 0,
            shopping_ads=len(offers),
            offers=offers,
        )
