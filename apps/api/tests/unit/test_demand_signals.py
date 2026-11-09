"""Demand signals (FR-12, ADR-0019), the micros cost meter and candidate selection."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from momentsales.agents.base import BudgetExceededError, CostMeter
from momentsales.agents.trend import ordered_seeds, usable_rising
from momentsales.modules.research.market_cache import iso_week, query_key
from momentsales.modules.research.signals import (
    competition_score,
    price_spread,
    trend_signals,
)

AS_OF = date(2026, 11, 6)
END = AS_OF + timedelta(weeks=8)


def weekly(values: list[int | None], last: date = AS_OF) -> list[tuple[date, int | None]]:
    start = last - timedelta(weeks=len(values) - 1)
    return [(start + timedelta(weeks=i), v) for i, v in enumerate(values)]


def test_rising_series_has_positive_growth_and_is_not_crowded() -> None:
    # Last year's Christmas peak (100) near the start, then a climb over the last weeks.
    values = [20] * 52
    for i in range(3, 9):
        values[i] = 100
    for i in range(43, 52):
        values[i] = 20 + (i - 42) * 6
    sig = trend_signals(weekly(values), as_of=AS_OF, window_end=END)
    assert sig.growth_rate is not None and sig.growth_rate == pytest.approx(0.6585, abs=1e-3)
    assert sig.peak is False and sig.insufficient is False
    assert sig.max_52w == 100 and sig.points == 52


def test_series_at_its_high_is_crowded() -> None:
    values = [30] * 44 + [50, 70, 90, 100, 100, 100, 100, 100]
    sig = trend_signals(weekly(values), as_of=AS_OF, window_end=END)
    assert sig.peak is True and sig.growth_rate is not None and sig.growth_rate > 0


def test_falling_series_has_negative_growth() -> None:
    sig = trend_signals(weekly([max(10, 70 - i) for i in range(52)]), as_of=AS_OF, window_end=END)
    assert sig.growth_rate is not None and sig.growth_rate < 0 and not sig.peak


def test_low_interest_and_short_series_are_insufficient() -> None:
    low = trend_signals(weekly([i % 2 for i in range(52)]), as_of=AS_OF, window_end=END)
    assert low.insufficient and low.growth_rate is None and not low.peak
    short = trend_signals(weekly([50] * 5), as_of=AS_OF, window_end=END)
    assert short.insufficient and short.points == 5


def test_missing_points_are_ignored_not_zeroed() -> None:
    values: list[int | None] = [40] * 52
    values[-1] = None
    sig = trend_signals(weekly(values), as_of=AS_OF, window_end=END)
    assert sig.points == 51 and sig.growth_rate == 0


def test_last_year_lift_compares_last_year_at_both_dates() -> None:
    values = [20] * 52
    # a year before the window's end (late Dec 2025) interest was 4x a year before as_of
    pts = weekly(values)
    target = END - timedelta(days=364)
    pts = [(d, 80 if abs((d - target).days) <= 3 else v) for d, v in pts]
    sig = trend_signals(pts, as_of=AS_OF, window_end=END)
    assert sig.last_year_lift == pytest.approx(3.0)


def test_competition_score_bounds_and_missing_parts() -> None:
    crowded = competition_score(
        total_results=1_000_000_000, paid_ads=8, shopping_ads=8, merchants=10, spread=0.0
    )
    assert crowded.score == 100 and crowded.missing == []
    quiet = competition_score(total_results=1, paid_ads=0, shopping_ads=0, merchants=0, spread=0.6)
    assert quiet.score == 0
    partial = competition_score(
        total_results=None, paid_ads=6, shopping_ads=0, merchants=None, spread=None
    )
    assert partial.score == 50 and partial.missing == ["merchants", "results", "spread"]
    nothing = competition_score(
        total_results=None, paid_ads=None, shopping_ads=None, merchants=None, spread=None
    )
    assert nothing.score is None


def test_price_spread() -> None:
    assert price_spread([1000]) is None
    assert price_spread([1000, 1000]) == 0
    spread = price_spread([1000, 3000])
    assert spread is not None and spread == pytest.approx(0.5)


def test_meter_counts_micros_and_rounds_up_to_pennies() -> None:
    meter = CostMeter(cap_micros=1_000_000)
    meter.charge("dataforseo", 9_000)
    meter.charge("dataforseo", 2_000)
    meter.charge("serpapi", 20_000)
    assert meter.spent_micros == 31_000 and meter.spent_minor == 4  # 3.1p shown as 4p
    assert meter.by_provider == {"dataforseo": 11_000, "serpapi": 20_000}
    meter.ensure(969_000)
    with pytest.raises(BudgetExceededError):
        meter.ensure(969_001)
    assert CostMeter(cap_micros=100).spent_minor == 0


def test_seed_order_round_robins_and_dedupes() -> None:
    pool = ordered_seeds(
        [
            ("category", "home", ["Heated Throw", "candles"]),
            ("audience", "hiking", ["hiking"]),
            ("seed", "gifts", ["advent calendar", "heated throw", "mug"]),
        ]
    )
    assert [p["keyword"] for p in pool] == [
        "heated throw",
        "hiking",
        "advent calendar",
        "candles",
        "mug",
    ]
    assert pool[1]["source"] == "audience"


def test_rising_filter() -> None:
    assert usable_rising("heated throw dunelm")
    assert not usable_rising("how to wash a heated throw")
    assert not usable_rising("heated throw near me")
    assert not usable_rising("x" * 61)


def test_cache_key_is_provider_neutral_and_stable() -> None:
    a = query_key("trends_series", "GB", {"keywords": ["a", "b"], "range": "12m"})
    b = query_key("trends_series", "GB", {"range": "12m", "keywords": ["a", "b"]})
    assert a == b and len(a) == 64
    assert a != query_key("trends_series", "AU", {"keywords": ["a", "b"], "range": "12m"})
    assert iso_week(date(2026, 11, 6)) == "2026-W45"
