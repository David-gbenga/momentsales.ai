"""Season engine (FR-2): meteorological season by hemisphere, and retail moments in a
look-ahead window. Pure functions, so the rules are fully unit-tested.

Moment rules (stored in `season_moments.rule`):
- `fixed:MM-DD`
- `easter:+N` / `easter:-N`: days from Western Easter Sunday
- `nth_weekday:MONTH:WEEKDAY:N[:+DAYS]`: the Nth weekday (0 = Monday) of the month, N = -1
  for the last one, optionally shifted by DAYS (Black Friday = 4th Thursday of Nov + 1).
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Literal

Hemisphere = Literal["north", "south"]

# Countries the engine knows. Only enabled country flags are selectable (UK at launch).
COUNTRY_HEMISPHERE: dict[str, Hemisphere] = {
    "GB": "north",
    "IE": "north",
    "US": "north",
    "DE": "north",
    "FR": "north",
    "ES": "north",
    "IT": "north",
    "NL": "north",
    "AU": "south",
}

MIN_WINDOW_WEEKS = 2
MAX_WINDOW_WEEKS = 12
DEFAULT_WINDOW_WEEKS = 8
# Selling time between the stock arriving and the moment (FR-12 act-by).
SELLING_DAYS = 21


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


_NORTH = {
    12: Season.WINTER,
    1: Season.WINTER,
    2: Season.WINTER,
    3: Season.SPRING,
    4: Season.SPRING,
    5: Season.SPRING,
    6: Season.SUMMER,
    7: Season.SUMMER,
    8: Season.SUMMER,
    9: Season.AUTUMN,
    10: Season.AUTUMN,
    11: Season.AUTUMN,
}
_OPPOSITE = {
    Season.WINTER: Season.SUMMER,
    Season.SUMMER: Season.WINTER,
    Season.SPRING: Season.AUTUMN,
    Season.AUTUMN: Season.SPRING,
}
_ORDER = [Season.SPRING, Season.SUMMER, Season.AUTUMN, Season.WINTER]


class UnknownCountryError(ValueError):
    pass


class BadRuleError(ValueError):
    pass


def hemisphere(country: str) -> Hemisphere:
    try:
        return COUNTRY_HEMISPHERE[country]
    except KeyError as exc:
        raise UnknownCountryError(country) from exc


def season_for(country: str, on: date) -> Season:
    """Meteorological season: in the north Dec-Feb is winter; the south is the opposite."""
    north = _NORTH[on.month]
    return north if hemisphere(country) == "north" else _OPPOSITE[north]


def next_season(season: Season) -> Season:
    return _ORDER[(_ORDER.index(season) + 1) % 4]


def season_ends(country: str, on: date) -> date:
    """Last day of the season `on` falls in (seasons are whole months, at most 3)."""
    current = season_for(country, on)
    day = on
    while season_for(country, day + timedelta(days=1)) is current:
        day += timedelta(days=1)
    return day


def easter_sunday(year: int) -> date:
    """Western Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n == -1:
        last = date(year, month, calendar.monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def moment_date(rule: str, year: int) -> date:
    """The date a moment rule gives in `year`."""
    kind, _, rest = rule.partition(":")
    try:
        if kind == "fixed":
            month, day = (int(x) for x in rest.split("-"))
            return date(year, month, day)
        if kind == "easter":
            return easter_sunday(year) + timedelta(days=int(rest))
        if kind == "nth_weekday":
            parts = rest.split(":")
            month, weekday, n = int(parts[0]), int(parts[1]), int(parts[2])
            shift = int(parts[3]) if len(parts) > 3 else 0
            if not (0 <= weekday <= 6 and (1 <= n <= 5 or n == -1)):
                raise BadRuleError(rule)
            return _nth_weekday(year, month, weekday, n) + timedelta(days=shift)
    except (ValueError, IndexError) as exc:
        raise BadRuleError(rule) from exc
    raise BadRuleError(rule)


def next_occurrence(rule: str, on: date) -> date:
    """The first date on or after `on` that the rule gives."""
    this_year = moment_date(rule, on.year)
    return this_year if this_year >= on else moment_date(rule, on.year + 1)


@dataclass(frozen=True, slots=True)
class MomentDef:
    key: str
    name: str
    rule: str
    category_hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpcomingMoment:
    key: str
    name: str
    date: date
    days_away: int
    act_by: dt.date
    act_by_passed: bool
    category_hints: tuple[str, ...]


def act_by(moment: date, delivery_max_days: int) -> date:
    """FR-12: moment date - (supplier delivery max + 21 days of selling time)."""
    return moment - timedelta(days=delivery_max_days + SELLING_DAYS)


def upcoming(
    moments: list[MomentDef], on: date, *, delivery_max_days: int, within_days: int | None = None
) -> list[UpcomingMoment]:
    """Moments from `on` onwards, soonest first, optionally limited to a window."""
    out: list[UpcomingMoment] = []
    for m in moments:
        when = next_occurrence(m.rule, on)
        days = (when - on).days
        if within_days is not None and days > within_days:
            continue
        deadline = act_by(when, delivery_max_days)
        out.append(
            UpcomingMoment(
                key=m.key,
                name=m.name,
                date=when,
                days_away=days,
                act_by=deadline,
                act_by_passed=deadline < on,
                category_hints=m.category_hints,
            )
        )
    return sorted(out, key=lambda u: (u.date, u.key))


@dataclass(frozen=True, slots=True)
class SeasonSummary:
    country: str
    hemisphere: Hemisphere
    as_of: date
    window_weeks: int
    window_end: date
    current: Season
    next: Season
    current_ends: date
    detected: Season
    overridden: bool
    moments: list[UpcomingMoment]


def summarise(
    country: str,
    on: date,
    moments: list[MomentDef],
    *,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    delivery_max_days: int,
    override: Season | None = None,
) -> SeasonSummary:
    if not MIN_WINDOW_WEEKS <= window_weeks <= MAX_WINDOW_WEEKS:
        raise ValueError(f"window_weeks must be {MIN_WINDOW_WEEKS}-{MAX_WINDOW_WEEKS}")
    detected = season_for(country, on)
    current = override or detected
    return SeasonSummary(
        country=country,
        hemisphere=hemisphere(country),
        as_of=on,
        window_weeks=window_weeks,
        window_end=on + timedelta(weeks=window_weeks),
        current=current,
        next=next_season(current),
        current_ends=season_ends(country, on),
        detected=detected,
        overridden=override is not None and override is not detected,
        moments=upcoming(
            moments, on, delivery_max_days=delivery_max_days, within_days=window_weeks * 7
        ),
    )
