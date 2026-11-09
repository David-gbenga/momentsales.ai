"""Contract tests for the supplier and FX adapters (ADR-0020) against the documented
response shapes (docs/integrations/cj-dropshipping.md, avasam.md, frankfurter.md)."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from momentsales.adapters.fx.base import FxError, convert_minor
from momentsales.adapters.fx.frankfurter import BASE_URL as FX
from momentsales.adapters.fx.frankfurter import FrankfurterAdapter
from momentsales.adapters.fx.mock import MockFxAdapter
from momentsales.adapters.supplier.avasam import BASE_URL as AVA
from momentsales.adapters.supplier.avasam import (
    PRODUCTS_PATH,
    STOCK_PATH,
    AvasamAdapter,
)
from momentsales.adapters.supplier.avasam import TOKEN_PATH as AVA_TOKEN
from momentsales.adapters.supplier.base import SupplierError, parse_days
from momentsales.adapters.supplier.cj import BASE_URL as CJ
from momentsales.adapters.supplier.cj import (
    FREIGHT_PATH,
    SEARCH_PATH,
    TOKEN_KEY,
    TOKEN_PATH,
    VARIANTS_PATH,
    CjAdapter,
)
from momentsales.adapters.supplier.mock import MockSupplierAdapter, case_of

API_KEY = "CJ123@api@secretvalue"
TOKEN = "cj-access-token-abc"


def cj_ok(data: object, **extra: object) -> dict[str, object]:
    return {"code": 200, "result": True, "message": "Success", "data": data, **extra}


TOKEN_BODY = cj_ok(
    {
        "openId": 1,
        "accessToken": TOKEN,
        "accessTokenExpiryDate": "2027-05-01T00:00:00+08:00",
        "refreshToken": "refresh",
        "refreshTokenExpiryDate": "2027-05-01T00:00:00+08:00",
    }
)
LIST_BODY = cj_ok(
    {
        "pageSize": 5,
        "pageNumber": 1,
        "totalRecords": 2,
        "content": [
            {
                "productList": [
                    {
                        "id": "P1",
                        "nameEn": "Heated Throw Blanket Electric",
                        "sku": "CJ1",
                        "bigImage": "https://cf.cjdropshipping.com/p1.jpg",
                        "sellPrice": "8.50",
                    },
                    {"id": "P2", "nameEn": "Heated Throw XL", "sellPrice": "9.10"},
                ]
            }
        ],
    },
    pointsInfo={"usedToday": 60, "remaining": 49940, "total": 50000},
)
VARIANTS_P1 = cj_ok(
    [
        {
            "vid": "V1",
            "pid": "P1",
            "variantNameEn": "Grey",
            "variantSellPrice": 8.5,
            "inventories": [
                {"countryCode": "GB", "totalInventoryNum": 30},
                {"countryCode": "CN", "totalInventoryNum": 999},
            ],
        },
        {
            "vid": "V2",
            "pid": "P1",
            "variantNameEn": "Blue",
            "variantSellPrice": 7.9,
            "inventories": [{"countryCode": "CN", "totalInventoryNum": 50}],
        },
    ]
)
VARIANTS_P2 = cj_ok(
    [{"vid": "V9", "variantSellPrice": 9.1, "inventories": [{"countryCode": "CN"}]}]
)
FREIGHT_BODY = cj_ok(
    [
        {"logisticName": "CJPacket UK", "logisticPrice": 2.1, "logisticAging": "3-6"},
        {"logisticName": "UK Tracked 48", "logisticPrice": 3.4, "logisticAging": "2-4"},
    ]
)


def cj_routes(mock: respx.MockRouter) -> dict[str, respx.Route]:
    token = mock.post(f"{CJ}{TOKEN_PATH}").respond(json=TOKEN_BODY)
    listing = mock.get(f"{CJ}{SEARCH_PATH}").respond(json=LIST_BODY)
    v1 = mock.get(f"{CJ}{VARIANTS_PATH}", params={"pid": "P1"}).respond(json=VARIANTS_P1)
    v2 = mock.get(f"{CJ}{VARIANTS_PATH}", params={"pid": "P2"}).respond(json=VARIANTS_P2)
    freight = mock.post(f"{CJ}{FREIGHT_PATH}").respond(json=FREIGHT_BODY)
    return {"token": token, "list": listing, "v1": v1, "v2": v2, "freight": freight}


class MemoryRedis:
    """Just enough of redis.asyncio for the token cache."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[bytes, int]] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data[key][0] if key in self.data else None

    async def set(self, key: str, value: str, ex: int) -> None:
        self.data[key] = (value.encode(), ex)


@pytest.fixture
async def http() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as client:
        yield client


# --- helpers ----------------------------------------------------------------------------


def test_parse_days() -> None:
    assert parse_days("2-5") == (2, 5)
    assert parse_days("3" + chr(8211) + "6") == (3, 6)
    assert parse_days("7") == (7, 7)
    assert parse_days("5-2") == (2, 5)
    assert parse_days(None) == (None, None)
    assert parse_days("n/a") == (None, None)


def test_convert_minor_half_up() -> None:
    assert convert_minor(850, "0.74711") == 635  # 635.04
    assert convert_minor(1, "0.5") == 1  # 0.5 rounds half up
    assert convert_minor(0, "0.75") == 0


# --- CJ -----------------------------------------------------------------------------------


@respx.mock
async def test_cj_search_reads_gb_stock_and_cheapest_stocked_variant(
    http: httpx.AsyncClient,
) -> None:
    routes = cj_routes(respx.mock)
    result = await CjAdapter(http, API_KEY).search("Heated Throw!", "GB")
    assert result.provider == "cj" and result.keyword == "heated throw"
    first, second = result.items
    assert first.external_id == "P1" and first.title == "Heated Throw Blanket Electric"
    assert first.uk_stock == 30 and first.warehouse_country == "GB"
    # V2 is cheaper but has no GB stock, so V1 is the variant used.
    assert first.variant_id == "V1" and first.unit_cost_minor == 850 and first.currency == "USD"
    assert first.url is None  # the API returns no product page URL
    assert second.uk_stock == 0 and second.warehouse_country is None
    query = routes["list"].calls.last.request.url.params
    assert query["keyWord"] == "heated throw" and query["countryCode"] == "GB"
    assert routes["list"].calls.last.request.headers["CJ-Access-Token"] == TOKEN
    assert json.loads(routes["token"].calls.last.request.content) == {"apiKey": API_KEY}
    assert result.raw["points"]["remaining"] == 49940
    assert TOKEN not in result.url and API_KEY not in json.dumps(result.raw)


@respx.mock
async def test_cj_freight_parses_days_and_tracking(http: httpx.AsyncClient) -> None:
    routes = cj_routes(respx.mock)
    quote = await CjAdapter(http, API_KEY).freight("V1", "GB")
    body = json.loads(routes["freight"].calls.last.request.content)
    assert body == {
        "startCountryCode": "GB",
        "endCountryCode": "GB",
        "products": [{"vid": "V1", "quantity": 1}],
    }
    packet, tracked = quote.options
    assert packet.tracked is None and (packet.min_days, packet.max_days) == (3, 6)
    assert tracked.tracked is True and tracked.cost_minor == 340 and tracked.currency == "USD"


@respx.mock
async def test_cj_token_is_cached_in_redis(http: httpx.AsyncClient) -> None:
    routes = cj_routes(respx.mock)
    redis = MemoryRedis()
    await CjAdapter(http, API_KEY, redis=redis).freight("V1", "GB")  # type: ignore[arg-type]
    assert await redis.get(TOKEN_KEY) == TOKEN.encode()
    assert redis.data[TOKEN_KEY][1] > 0  # expires an hour before CJ's expiry
    # A new adapter (another worker) reuses the cached token.
    await CjAdapter(http, API_KEY, redis=redis).freight("V1", "GB")  # type: ignore[arg-type]
    assert routes["token"].call_count == 1


@pytest.mark.parametrize(
    ("status", "body", "retryable"),
    [
        (200, {"code": 1600200, "result": False, "message": "Too much request"}, True),
        (200, {"code": 16900500, "result": False, "message": "Insufficient points"}, True),
        (429, {"code": 429, "result": False, "message": "Insufficient points"}, True),
        (502, {}, True),
        (200, {"code": 1600001, "result": False, "message": "Invalid API key"}, False),
        (200, {"code": 1600014, "result": False, "message": "API access disabled"}, False),
    ],
)
@respx.mock
async def test_cj_error_codes(
    http: httpx.AsyncClient, status: int, body: dict[str, object], retryable: bool
) -> None:
    respx.post(f"{CJ}{TOKEN_PATH}").respond(json=TOKEN_BODY)
    respx.post(f"{CJ}{FREIGHT_PATH}").respond(status, json=body)
    with pytest.raises(SupplierError) as err:
        await CjAdapter(http, API_KEY).freight("V1", "GB")
    assert err.value.retryable is retryable


@respx.mock
async def test_cj_success_without_code_field(http: httpx.AsyncClient) -> None:
    respx.post(f"{CJ}{TOKEN_PATH}").respond(json=TOKEN_BODY)
    respx.post(f"{CJ}{FREIGHT_PATH}").respond(json={"data": []})
    quote = await CjAdapter(http, API_KEY).freight("V1", "GB")
    assert quote.options == []


@respx.mock
async def test_cj_bad_key_on_token_is_not_retryable(http: httpx.AsyncClient) -> None:
    respx.post(f"{CJ}{TOKEN_PATH}").respond(
        json={"code": 1600001, "result": False, "message": "Invalid API key"}
    )
    with pytest.raises(SupplierError) as err:
        await CjAdapter(http, API_KEY).search("x", "GB")
    assert not err.value.retryable


# --- Avasam -------------------------------------------------------------------------------

PRODUCTS = [
    {
        "SKU": "AV1",
        "Title": "Heated Throws Fleece",
        "Price": 11.5,
        "RetailPrice": 29.99,
        "Image": "https://img.avasam.com/av1.jpg",
        "Variations": [{"SKU": "AV1-G", "Title": "Grey", "Price": 11.5}],
    },
    {"SKU": "AV2", "Title": "Garden Gnome", "Price": 4.0},
]


@respx.mock
async def test_avasam_search_filters_locally_and_reads_stock(http: httpx.AsyncClient) -> None:
    token = respx.post(f"{AVA}{AVA_TOKEN}").respond(
        json={"access_token": "ava-token", "expires_at": "2099-01-01T00:00:00Z"}
    )
    products = respx.get(f"{AVA}{PRODUCTS_PATH}").respond(json=PRODUCTS)
    respx.get(f"{AVA}{STOCK_PATH}").respond(
        json=[{"SKU": "AV1", "Stock": 12}, {"SKU": "AV1-G", "Stock": 12}]
    )
    result = await AvasamAdapter(http, "ck", "sk").search("heated throw", "GB")
    assert [i.external_id for i in result.items] == ["AV1"]
    item = result.items[0]
    assert item.unit_cost_minor == 1150 and item.currency == "GBP"
    assert item.uk_stock == 12 and item.warehouse_country == "GB"
    assert item.variants[0].variant_id == "AV1-G"
    assert json.loads(token.calls.last.request.content) == {
        "consumer_key": "ck",
        "secret_key": "sk",
    }
    assert products.calls.last.request.headers["Authorization"] == "Bearer ava-token"
    assert "sk" not in result.url


async def test_avasam_freight_is_published_tier_without_cost(http: httpx.AsyncClient) -> None:
    quote = await AvasamAdapter(http, "ck", "sk").freight("AV1", "GB")
    assert quote.source == "published"
    (option,) = quote.options
    assert option.tracked is True and option.cost_minor is None
    assert (option.min_days, option.max_days) == (2, 4)


@respx.mock
async def test_avasam_errors(http: httpx.AsyncClient) -> None:
    respx.post(f"{AVA}{AVA_TOKEN}").respond(401, json={"message": "bad keys"})
    with pytest.raises(SupplierError) as err:
        await AvasamAdapter(http, "ck", "bad").verify()
    assert not err.value.retryable
    respx.post(f"{AVA}{AVA_TOKEN}").respond(503)
    with pytest.raises(SupplierError) as err:
        await AvasamAdapter(http, "ck", "sk").verify()
    assert err.value.retryable


# --- Frankfurter ----------------------------------------------------------------------------


@respx.mock
async def test_frankfurter_latest(http: httpx.AsyncClient) -> None:
    route = respx.get(f"{FX}/latest").respond(
        json={"amount": 1.0, "base": "USD", "date": "2026-11-06", "rates": {"GBP": 0.74711}}
    )
    rate = await FrankfurterAdapter(http).rate("USD", "GBP")
    assert rate.rate == "0.74711" and rate.rate_date == date(2026, 11, 6)
    assert route.calls.last.request.url.params["base"] == "USD"
    assert route.calls.last.request.url.params["symbols"] == "GBP"


@respx.mock
async def test_frankfurter_errors(http: httpx.AsyncClient) -> None:
    respx.get(f"{FX}/latest").respond(503)
    with pytest.raises(FxError) as err:
        await FrankfurterAdapter(http).rate("USD", "GBP")
    assert err.value.retryable
    respx.get(f"{FX}/latest").respond(json={"base": "USD", "date": "2026-11-06", "rates": {}})
    with pytest.raises(FxError) as err:
        await FrankfurterAdapter(http).rate("USD", "GBP")
    assert not err.value.retryable


# --- Mocks ----------------------------------------------------------------------------------


async def test_mock_supplier_cases_and_names() -> None:
    cj = MockSupplierAdapter("cj")
    ava = MockSupplierAdapter("avasam")
    assert cj.shared_cache and not ava.shared_cache
    good = await cj.search("heated throw", "GB")
    assert case_of("heated throw") == "good"
    assert good.items[0].currency == "USD" and good.items[0].uk_stock
    assert good.url.startswith("mock://supplier/cj/")
    away = await cj.search("fairy lights", "GB")
    assert away.items[0].warehouse_country == "CN" and away.items[0].uk_stock == 0
    quote = await cj.freight(good.items[0].variant_id or "", "GB")
    assert any(o.tracked for o in quote.options)
    ava_quote = await ava.freight("good-1-v1", "GB")
    assert ava_quote.source == "published" and ava_quote.options[0].cost_minor is None
    assert (await ava.search("heated throw", "GB")).items[0].currency == "GBP"


async def test_mock_fx() -> None:
    rate = await MockFxAdapter().rate("USD", "GBP")
    assert rate.rate == "0.75" and rate.url.startswith("mock://")
    with pytest.raises(FxError):
        await MockFxAdapter().rate("JPY", "GBP")
