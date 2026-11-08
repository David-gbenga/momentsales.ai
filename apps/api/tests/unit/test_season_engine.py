"""Season engine rules (FR-2): seasons by hemisphere, moment date rules, act-by dates."""

from __future__ import annotations

from datetime import date

import pytest

from momentsales.modules.research import season as s

UK_MOMENTS = [
    s.MomentDef("bonfire_night", "Bonfire Night", "fixed:11-05", ()),
    s.MomentDef("singles_day", "Singles' Day", "fixed:11-11", ()),
    s.MomentDef("black_friday", "Black Friday", "nth_weekday:11:3:4:+1", ("electronics",)),
    s.MomentDef("cyber_monday", "Cyber Monday", "nth_weekday:11:3:4:+4", ()),
    s.MomentDef("christmas", "Christmas", "fixed:12-25", ("gifts",)),
    s.MomentDef("boxing_day", "Boxing Day sales", "fixed:12-26", ()),
    s.MomentDef("valentines", "Valentine's Day", "fixed:02-14", ()),
    s.MomentDef("mothering_sunday", "Mother's Day (UK)", "easter:-21", ()),
]
AU_MOMENTS = [
    s.MomentDef("black_friday", "Black Friday", "nth_weekday:11:3:4:+1", ()),
    s.MomentDef("christmas", "Christmas", "fixed:12-25", ("beachwear",)),
    s.MomentDef("boxing_day", "Boxing Day sales", "fixed:12-26", ()),
    s.MomentDef("back_to_school", "Back to school (typical)", "fixed:01-28", ()),
]


@pytest.mark.parametrize(
    ("country", "on", "expected"),
    [
        ("GB", date(2026, 11, 6), s.Season.AUTUMN),
        ("GB", date(2026, 12, 1), s.Season.WINTER),
        ("GB", date(2027, 2, 28), s.Season.WINTER),
        ("GB", date(2027, 3, 1), s.Season.SPRING),
        ("GB", date(2027, 7, 15), s.Season.SUMMER),
        ("AU", date(2026, 11, 6), s.Season.SPRING),
        ("AU", date(2026, 12, 1), s.Season.SUMMER),
        ("AU", date(2027, 7, 15), s.Season.WINTER),
    ],
)
def test_season_by_hemisphere(country: str, on: date, expected: s.Season) -> None:
    assert s.season_for(country, on) is expected


def test_unknown_country() -> None:
    with pytest.raises(s.UnknownCountryError):
        s.season_for("ZZ", date(2026, 1, 1))


def test_season_ends_and_next() -> None:
    assert s.season_ends("GB", date(2026, 11, 6)) == date(2026, 11, 30)
    assert s.season_ends("GB", date(2026, 12, 25)) == date(2027, 2, 28)
    assert s.season_ends("GB", date(2028, 1, 10)) == date(2028, 2, 29)  # leap year
    assert s.next_season(s.Season.AUTUMN) is s.Season.WINTER
    assert s.next_season(s.Season.WINTER) is s.Season.SPRING


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter(year: int, expected: date) -> None:
    assert s.easter_sunday(year) == expected


@pytest.mark.parametrize(
    ("rule", "year", "expected"),
    [
        ("fixed:12-25", 2026, date(2026, 12, 25)),
        ("nth_weekday:11:3:4:+1", 2026, date(2026, 11, 27)),  # Black Friday
        ("nth_weekday:11:3:4:+1", 2027, date(2027, 11, 26)),
        ("nth_weekday:11:3:4:+4", 2026, date(2026, 11, 30)),  # Cyber Monday
        ("easter:-21", 2026, date(2026, 3, 15)),  # Mothering Sunday
        ("easter:-21", 2027, date(2027, 3, 7)),
        ("nth_weekday:6:6:3", 2026, date(2026, 6, 21)),  # Father's Day (UK)
        ("nth_weekday:5:0:-1", 2026, date(2026, 5, 25)),  # last Monday in May
        ("nth_weekday:9:6:1", 2026, date(2026, 9, 6)),  # Father's Day (AU)
    ],
)
def test_moment_rules(rule: str, year: int, expected: date) -> None:
    assert s.moment_date(rule, year) == expected


@pytest.mark.parametrize(
    "rule", ["", "fixed:13-01", "fixed:xx", "easter:x", "nth_weekday:11:9:1", "weekly:1", "nth"]
)
def test_bad_rules(rule: str) -> None:
    with pytest.raises(s.BadRuleError):
        s.moment_date(rule, 2026)


def test_next_occurrence_rolls_to_next_year() -> None:
    assert s.next_occurrence("fixed:02-14", date(2026, 11, 6)) == date(2027, 2, 14)
    assert s.next_occurrence("fixed:11-06", date(2026, 11, 6)) == date(2026, 11, 6)


def test_uk_on_6_november_is_autumn_into_winter_with_black_friday_and_christmas() -> None:
    """AC-1 (season part)."""
    summary = s.summarise("GB", date(2026, 11, 6), UK_MOMENTS, delivery_max_days=5)
    assert (summary.current, summary.next) == (s.Season.AUTUMN, s.Season.WINTER)
    assert summary.window_end == date(2027, 1, 1)
    keys = [m.key for m in summary.moments]
    assert keys == ["singles_day", "black_friday", "cyber_monday", "christmas", "boxing_day"]
    christmas = next(m for m in summary.moments if m.key == "christmas")
    assert christmas.days_away == 49
    assert christmas.act_by == date(2026, 11, 29)  # 25 Dec - (5 + 21) days
    assert not christmas.act_by_passed
    black_friday = next(m for m in summary.moments if m.key == "black_friday")
    assert black_friday.act_by == date(2026, 11, 1) and black_friday.act_by_passed


def test_australia_on_the_same_date_is_spring_into_summer() -> None:
    """AC-2 at engine level (Australia stays behind its country flag)."""
    summary = s.summarise("AU", date(2026, 11, 6), AU_MOMENTS, delivery_max_days=5)
    assert (summary.current, summary.next) == (s.Season.SPRING, s.Season.SUMMER)
    assert summary.hemisphere == "south"
    assert [m.key for m in summary.moments] == ["black_friday", "christmas", "boxing_day"]


def test_override_and_window() -> None:
    summary = s.summarise(
        "GB",
        date(2026, 11, 6),
        UK_MOMENTS,
        window_weeks=2,
        delivery_max_days=10,
        override=s.Season.WINTER,
    )
    assert summary.current is s.Season.WINTER and summary.next is s.Season.SPRING
    assert summary.detected is s.Season.AUTUMN and summary.overridden
    assert [m.key for m in summary.moments] == ["singles_day"]
    same = s.summarise(
        "GB", date(2026, 11, 6), UK_MOMENTS, delivery_max_days=5, override=s.Season.AUTUMN
    )
    assert not same.overridden
    with pytest.raises(ValueError, match="window_weeks"):
        s.summarise("GB", date(2026, 11, 6), UK_MOMENTS, window_weeks=13, delivery_max_days=5)


def test_act_by_moves_with_delivery_time() -> None:
    """AC-26 groundwork: a slower supplier brings the act-by date forward."""
    fast = s.upcoming(UK_MOMENTS, date(2026, 11, 6), delivery_max_days=3)
    slow = s.upcoming(UK_MOMENTS, date(2026, 11, 6), delivery_max_days=12)
    for a, b in zip(fast, slow, strict=True):
        assert (a.act_by - b.act_by).days == 9
