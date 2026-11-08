"""Mock holidays: a fixed England and Wales bank-holiday pattern, computed locally."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from momentsales.adapters.holidays.base import Holiday, HolidayList, HolidaysError
from momentsales.modules.research.season import easter_sunday, moment_date


class MockHolidaysAdapter:
    name = "mock"

    async def holidays(self, country: str, year: int) -> HolidayList:
        if country not in ("GB", "AU"):
            raise HolidaysError(self.name, f"no mock data for {country}")
        easter = easter_sunday(year)
        if country == "GB":
            days = [
                (date(year, 1, 1), "New Year's Day"),
                (easter - timedelta(days=2), "Good Friday"),
                (easter + timedelta(days=1), "Easter Monday"),
                (moment_date("nth_weekday:5:0:1", year), "Early May Bank Holiday"),
                (moment_date("nth_weekday:5:0:-1", year), "Spring Bank Holiday"),
                (moment_date("nth_weekday:8:0:-1", year), "Summer Bank Holiday"),
                (date(year, 12, 25), "Christmas Day"),
                (date(year, 12, 26), "Boxing Day"),
            ]
        else:
            days = [
                (date(year, 1, 1), "New Year's Day"),
                (date(year, 1, 26), "Australia Day"),
                (easter - timedelta(days=2), "Good Friday"),
                (date(year, 12, 25), "Christmas Day"),
                (date(year, 12, 26), "Boxing Day"),
            ]
        holidays = [Holiday(d, n, True) for d, n in days]
        raw = [
            {"date": h.date.isoformat(), "name": h.name, "nationalHoliday": True} for h in holidays
        ]
        return HolidayList(
            self.name, f"mock://holidays/{country}/{year}", datetime.now(UTC), holidays, raw
        )
