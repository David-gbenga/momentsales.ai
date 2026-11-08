"""Contract tests for the Nager.Date v4 holidays adapter against the documented response
model (docs/integrations/nager-date.md)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from momentsales.adapters.holidays.base import HolidaysError
from momentsales.adapters.holidays.mock import MockHolidaysAdapter
from momentsales.adapters.holidays.nager import BASE_URL, NagerHolidaysAdapter

# Fields from the v4 "Response Model": date, name, countryCode, subdivisionCodes,
# nationalHoliday, holidayTypes.
RECORDED = [
    {
        "date": "2026-12-25",
        "name": "Christmas Day",
        "countryCode": "GB",
        "subdivisionCodes": [],
        "nationalHoliday": True,
        "holidayTypes": ["Public"],
    },
    {
        "date": "2026-03-17",
        "name": "Saint Patrick's Day",
        "countryCode": "GB",
        "subdivisionCodes": ["GB-NIR"],
        "nationalHoliday": False,
        "holidayTypes": ["Public"],
    },
]


@respx.mock
async def test_nager_request_and_parse() -> None:
    route = respx.get(f"{BASE_URL}/Holidays/GB/2026").mock(
        return_value=httpx.Response(200, json=RECORDED)
    )
    async with httpx.AsyncClient() as http:
        result = await NagerHolidaysAdapter(http).holidays("GB", 2026)
    assert route.called
    assert result.url == f"{BASE_URL}/Holidays/GB/2026"
    assert result.raw == RECORDED and result.fetched_at.tzinfo is not None
    assert [(h.date, h.name, h.national) for h in result.holidays] == [
        (date(2026, 12, 25), "Christmas Day", True),
        (date(2026, 3, 17), "Saint Patrick's Day", False),
    ]


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, json=[{"name": "no date"}]),
        httpx.Response(200, text="not json"),
    ],
)
async def test_nager_errors(response: httpx.Response) -> None:
    respx.get(f"{BASE_URL}/Holidays/GB/2026").mock(return_value=response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(HolidaysError):
            await NagerHolidaysAdapter(http).holidays("GB", 2026)


@respx.mock
async def test_nager_network_error() -> None:
    respx.get(f"{BASE_URL}/Holidays/GB/2026").mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(HolidaysError):
            await NagerHolidaysAdapter(http).holidays("GB", 2026)


async def test_mock_holidays() -> None:
    gb = await MockHolidaysAdapter().holidays("GB", 2026)
    names = {h.name: h.date for h in gb.holidays}
    assert names["Good Friday"] == date(2026, 4, 3)
    assert names["Spring Bank Holiday"] == date(2026, 5, 25)
    with pytest.raises(HolidaysError):
        await MockHolidaysAdapter().holidays("US", 2026)
