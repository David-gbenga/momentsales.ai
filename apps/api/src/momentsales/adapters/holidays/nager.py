"""Nager.Date public holidays, v4 API (docs/integrations/nager-date.md).

GET https://nagerholidays.com/api/v4/Holidays/{CountryCode}/{Year}; no key, no stated rate
limit. Fields used: `date`, `name`, `nationalHoliday`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from momentsales.adapters.holidays.base import Holiday, HolidayList, HolidaysError

BASE_URL = "https://nagerholidays.com/api/v4"


class NagerHolidaysAdapter:
    name = "nager"

    def __init__(self, http: httpx.AsyncClient, base_url: str = BASE_URL) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def holidays(self, country: str, year: int) -> HolidayList:
        url = f"{self._base}/Holidays/{country}/{year}"
        try:
            response = await self._http.get(url, timeout=10.0)
        except httpx.HTTPError as exc:
            raise HolidaysError(self.name, f"request failed: {exc}") from exc
        if response.status_code != 200:
            raise HolidaysError(self.name, f"HTTP {response.status_code}")
        try:
            data = response.json()
            items = [
                Holiday(
                    date=date.fromisoformat(str(h["date"])[:10]),
                    name=str(h["name"]),
                    national=bool(h.get("nationalHoliday", False)),
                )
                for h in data
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise HolidaysError(self.name, f"unexpected response: {exc}") from exc
        return HolidayList(self.name, url, datetime.now(UTC), items, data)
