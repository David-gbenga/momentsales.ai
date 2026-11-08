"""Contract tests for the market-data adapters (ADR-0019) against the documented response
shapes (docs/integrations/dataforseo.md, serpapi.md), plus failover (FR-15, AC-34)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from momentsales.adapters.market_data.base import (
    MarketDataError,
    currency_from_text,
    normalise_keyword,
    to_minor,
)
from momentsales.adapters.market_data.dataforseo import (
    BASE_URL as DFS,
)
from momentsales.adapters.market_data.dataforseo import (
    SERP_PATH,
    TRENDS_PATH,
    DataForSeoAdapter,
)
from momentsales.adapters.market_data.mock import MockMarketDataAdapter, shape_of
from momentsales.adapters.market_data.serpapi import SerpApiAdapter
from momentsales.modules.providers.registry import FailoverMarketData

SECRET = "sk-serp-secret-123"


def dfs_body(items: list[dict[str, Any]], code: int = 20000, task_code: int = 20000) -> Any:
    return {
        "version": "0.1",
        "status_code": code,
        "status_message": "Ok." if code == 20000 else "error",
        "cost": 0.011,
        "tasks": [
            {
                "id": "t1",
                "status_code": task_code,
                "status_message": "Ok." if task_code == 20000 else "error",
                "cost": 0.011,
                "result": [{"keywords": ["x"], "type": "trends", "items": items}],
            }
        ],
    }


DFS_GRAPH = {
    "position": 1,
    "type": "google_trends_graph",
    "title": "Interest over time",
    "keywords": ["heated throw", "advent calendar"],
    "data": [
        {
            "date_from": "2026-10-25",
            "date_to": "2026-10-31",
            "timestamp": 1792886400,
            "missing_data": False,
            "values": [40, 90],
        },
        {
            "date_from": "2026-11-01",
            "date_to": "2026-11-07",
            "timestamp": 1793491200,
            "missing_data": False,
            "values": [55, None],
        },
    ],
    "averages": [],
}
DFS_QUERIES = {
    "position": 1,
    "type": "google_trends_queries_list",
    "title": "Related queries",
    "data": {
        "top": [{"query": "heated throw blanket", "value": 100}],
        "rising": [{"query": "heated throw dunelm", "value": 350}],
    },
}
DFS_SERP = {
    "keyword": "heated throw",
    "se_results_count": 1250000,
    "items": [
        {"type": "paid", "title": "Heated throws", "domain": "shop.example", "url": "https://a"},
        {"type": "paid", "title": "Cosy throws", "domain": "b.example", "url": "https://b"},
        {"type": "organic", "title": "Best heated throws", "url": "https://c"},
        {
            "type": "shopping",
            "title": "Shopping",
            "items": [
                {
                    "type": "shopping_element",
                    "title": "Electric heated throw",
                    "price": {"current": 39.99, "currency": "GBP", "displayed_price": "£39.99"},
                    "source": "Argos",
                    "url": "https://argos.example/throw",
                },
                {"type": "shopping_element", "title": "No price", "source": "Shop", "url": None},
            ],
        },
        {
            "type": "popular_products",
            "items": [
                {
                    "type": "popular_products_element",
                    "title": "Throw 2",
                    "seller": "Dunelm",
                    "url": "https://dunelm.example/2",
                    "price": {"current": 49.5, "currency": "GBP"},
                }
            ],
        },
    ],
}


def dfs() -> tuple[httpx.AsyncClient, DataForSeoAdapter]:
    http = httpx.AsyncClient()
    return http, DataForSeoAdapter(http, "login", "pw", trends_micros=9000, serp_micros=2000)


@respx.mock
async def test_dataforseo_interest_over_time() -> None:
    route = respx.post(f"{DFS}{TRENDS_PATH}").mock(
        return_value=httpx.Response(200, json=dfs_body([DFS_GRAPH]))
    )
    http, adapter = dfs()
    async with http:
        result = await adapter.interest_over_time(["Heated Throw!", "advent calendar"], "GB")
    sent = json.loads(route.calls.last.request.content)
    assert sent == [
        {
            "keywords": ["heated throw", "advent calendar"],
            "location_name": "United Kingdom",
            "language_code": "en",
            "type": "web",
            "time_range": "past_12_months",
            "item_types": ["google_trends_graph"],
        }
    ]
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")
    assert result.provider == "dataforseo" and result.cost_micros == 9000
    assert result.url == f"{DFS}{TRENDS_PATH}"
    heated, advent = result.series
    assert [(p.date, p.value) for p in heated.points] == [
        (date(2026, 10, 25), 40),
        (date(2026, 11, 1), 55),
    ]
    assert [p.value for p in advent.points] == [90, None]


@respx.mock
async def test_dataforseo_related_and_serp() -> None:
    respx.post(f"{DFS}{TRENDS_PATH}").mock(
        return_value=httpx.Response(200, json=dfs_body([DFS_QUERIES]))
    )
    respx.post(f"{DFS}{SERP_PATH}").mock(
        return_value=httpx.Response(
            200,
            json=dfs_body([], code=20000)
            | {"tasks": [{"status_code": 20000, "status_message": "Ok.", "result": [DFS_SERP]}]},
        )
    )
    http, adapter = dfs()
    async with http:
        related = await adapter.related_queries("heated throw", "GB")
        serp = await adapter.serp_overview("heated throw", "GB")
    assert [(r.query, r.value) for r in related.rising] == [("heated throw dunelm", 350)]
    assert serp.total_results == 1250000
    assert (serp.paid_ads, serp.shopping_ads) == (2, 2)
    assert [(o.merchant, o.price_minor, o.currency) for o in serp.offers] == [
        ("Argos", 3999, "GBP"),
        ("Shop", None, None),  # no price: never guessed
        ("Dunelm", 4950, "GBP"),
    ]
    assert serp.cost_micros == 2000


@respx.mock
@pytest.mark.parametrize(
    ("response", "retryable"),
    [
        (httpx.Response(200, json=dfs_body([], code=40202)), True),  # rate limit
        (httpx.Response(200, json=dfs_body([], task_code=40209)), True),
        (httpx.Response(200, json=dfs_body([], code=50301)), True),
        (httpx.Response(200, json=dfs_body([], code=40210)), False),  # out of funds
        (httpx.Response(200, json=dfs_body([], task_code=40501)), False),
        (httpx.Response(401), False),
        (httpx.Response(429), True),
        (httpx.Response(503), True),
        (httpx.Response(200, text="<html>"), True),
    ],
)
async def test_dataforseo_errors(response: httpx.Response, retryable: bool) -> None:
    respx.post(f"{DFS}{TRENDS_PATH}").mock(return_value=response)
    http, adapter = dfs()
    async with http:
        with pytest.raises(MarketDataError) as info:
            await adapter.interest_over_time(["x"], "GB")
    assert info.value.retryable is retryable


@respx.mock
async def test_dataforseo_network_error_is_retryable() -> None:
    respx.post(f"{DFS}{TRENDS_PATH}").mock(side_effect=httpx.ConnectError("down"))
    http, adapter = dfs()
    async with http:
        with pytest.raises(MarketDataError) as info:
            await adapter.interest_over_time(["x"], "GB")
    assert info.value.retryable


SERP_TRENDS = {
    "search_metadata": {"id": "abc", "status": "Success"},
    "search_parameters": {"engine": "google_trends", "q": "heated throw", "api_key": SECRET},
    "interest_over_time": {
        "timeline_data": [
            {
                "date": "Oct 25 – 31, 2026",
                "timestamp": "1792886400",
                "values": [{"query": "heated throw", "value": "40", "extracted_value": 40}],
            },
            {
                "date": "Nov 1 – 7, 2026",
                "timestamp": "1793491200",
                "partial_data": True,
                "values": [{"query": "heated throw", "value": "60", "extracted_value": 60}],
            },
        ]
    },
}
SERP_RELATED = {
    "related_queries": {
        "rising": [
            {"query": "heated throw dunelm", "value": "+350%", "extracted_value": 350},
            {"query": "usb heated throw", "value": "Breakout", "extracted_value": 8700},
        ],
        "top": [{"query": "heated blanket", "value": "100", "extracted_value": 100}],
    }
}
SERP_GOOGLE = {
    "search_information": {"total_results": 980000},
    "ads": [{"position": 1, "block_position": "top", "title": "Ad", "link": "https://ad"}],
    "shopping_results": [
        {
            "position": 1,
            "title": "Heated throw",
            "price": "£34.99",
            "extracted_price": 34.99,
            "link": "https://shop.example/t",
            "source": "John Lewis",
        },
        {"position": 2, "title": "Other", "price": "$20", "extracted_price": 20, "source": "US"},
        {"position": 3, "title": "Unknown", "price": "20", "extracted_price": 20, "source": "X"},
    ],
}


def serp() -> tuple[httpx.AsyncClient, SerpApiAdapter]:
    http = httpx.AsyncClient()
    return http, SerpApiAdapter(http, SECRET, search_micros=20000)


@respx.mock
async def test_serpapi_trends_never_stores_the_key() -> None:
    route = respx.get("https://serpapi.com/search.json").mock(
        return_value=httpx.Response(200, json=json.loads(json.dumps(SERP_TRENDS)))
    )
    http, adapter = serp()
    async with http:
        result = await adapter.interest_over_time(["heated throw"], "GB")
    params = route.calls.last.request.url.params
    assert params["engine"] == "google_trends" and params["data_type"] == "TIMESERIES"
    assert params["geo"] == "GB" and params["date"] == "today 12-m"
    assert params["api_key"] == SECRET  # sent to SerpApi ...
    assert SECRET not in result.url and SECRET not in json.dumps(result.raw)  # ... never stored
    # the partial (unfinished) week is dropped
    assert [(p.date, p.value) for p in result.series[0].points] == [(date(2026, 10, 25), 40)]


@respx.mock
async def test_serpapi_related_and_search() -> None:
    route = respx.get("https://serpapi.com/search.json")
    route.side_effect = [
        httpx.Response(200, json=SERP_RELATED),
        httpx.Response(200, json=SERP_GOOGLE),
    ]
    http, adapter = serp()
    async with http:
        related = await adapter.related_queries("heated throw", "GB")
        overview = await adapter.serp_overview("heated throw", "GB")
    assert [(r.query, r.value, r.breakout) for r in related.rising] == [
        ("heated throw dunelm", 350, False),
        ("usb heated throw", 8700, True),
    ]
    search = route.calls.last.request.url.params
    assert (search["gl"], search["google_domain"]) == ("uk", "google.co.uk")
    assert overview.total_results == 980000 and overview.paid_ads == 1
    assert [(o.merchant, o.price_minor, o.currency) for o in overview.offers] == [
        ("John Lewis", 3499, "GBP"),
        ("US", 2000, "USD"),
        ("X", None, None),  # no currency symbol: the price is not used
    ]


@respx.mock
@pytest.mark.parametrize(
    ("response", "retryable"),
    [
        (httpx.Response(429, json={"error": "Your account has run out of searches."}), True),
        (httpx.Response(503, json={"error": "busy"}), True),
        (httpx.Response(401, json={"error": "Invalid API key."}), False),
        (httpx.Response(400, json={"error": "Missing query `q` parameter."}), False),
        (httpx.Response(200, json={"error": "Something else"}), False),
    ],
)
async def test_serpapi_errors(response: httpx.Response, retryable: bool) -> None:
    respx.get("https://serpapi.com/search.json").mock(return_value=response)
    http, adapter = serp()
    async with http:
        with pytest.raises(MarketDataError) as info:
            await adapter.serp_overview("x", "GB")
    assert info.value.retryable is retryable
    assert SECRET not in str(info.value)


@respx.mock
async def test_serpapi_no_results_is_empty_not_an_error() -> None:
    respx.get("https://serpapi.com/search.json").mock(
        return_value=httpx.Response(
            200, json={"error": "Google hasn't returned any results for this query."}
        )
    )
    http, adapter = serp()
    async with http:
        overview = await adapter.serp_overview("zzqx", "GB")
    assert (overview.paid_ads, overview.offers, overview.total_results) == (0, [], None)


class _Down(MockMarketDataAdapter):
    def __init__(self, retryable: bool) -> None:
        super().__init__("dataforseo", {"trends_series": 9000})
        self.retryable = retryable

    async def interest_over_time(self, keywords: list[str], country: str) -> Any:
        raise MarketDataError(self.name, "503", retryable=self.retryable)


async def test_failover_uses_the_secondary_on_retryable_errors_only() -> None:
    secondary = MockMarketDataAdapter("serpapi", {"trends_series": 20000})
    result = await FailoverMarketData(_Down(True), secondary, auto=True).interest_over_time(
        ["heated throw"], "GB"
    )
    assert result.provider == "serpapi" and result.cost_micros == 20000
    with pytest.raises(MarketDataError):
        await FailoverMarketData(_Down(False), secondary, auto=True).interest_over_time(["x"], "GB")
    with pytest.raises(MarketDataError):  # manual failover: no automatic switch
        await FailoverMarketData(_Down(True), secondary, auto=False).interest_over_time(["x"], "GB")
    port = FailoverMarketData(_Down(True), secondary, auto=True)
    assert port.estimate_micros("trends_series") == 20000  # the dearer of the two


async def test_mock_is_deterministic_and_labelled() -> None:
    mock = MockMarketDataAdapter("dataforseo")
    a = await mock.interest_over_time(["heated throw", "advent calendar"], "GB")
    b = await mock.interest_over_time(["heated throw", "advent calendar"], "GB")
    assert a.series == b.series and len(a.series[0].points) == 52
    assert a.url.startswith("mock://") and shape_of("advent calendar") == "peaked"
    overview = await mock.serp_overview("heated throw", "GB")
    assert all(o.url and o.url.startswith("https://example.com/mock/") for o in overview.offers)


def test_helpers() -> None:
    assert normalise_keyword("  Kids' Craft-Kits: (UK)! ") == "kids' craft kits uk"
    assert to_minor(19.99) == 1999 and to_minor("0.005") == 1 and to_minor(None) is None
    assert to_minor(-1) is None and to_minor("abc") is None and to_minor(True) is None
    assert currency_from_text("£12") == "GBP" and currency_from_text("12") is None
