"""Public-holidays port (FR-2 season engine context). Not a critical category (FR-15):
one live adapter plus a mock. A failure degrades the Season agent, never the run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


class HolidaysError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


@dataclass(frozen=True, slots=True)
class Holiday:
    date: date
    name: str
    national: bool


@dataclass(frozen=True, slots=True)
class HolidayList:
    """Holidays plus what we need for a source snapshot (AC-3)."""

    provider: str
    url: str
    fetched_at: datetime
    holidays: list[Holiday]
    raw: Any


class HolidaysPort(Protocol):
    name: str

    async def holidays(self, country: str, year: int) -> HolidayList: ...
