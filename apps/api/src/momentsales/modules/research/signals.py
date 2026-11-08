"""Demand signals (FR-12, ADR-0019): pure functions, no I/O.

- Growth rate: mean interest over the last `recent` weeks against the `baseline` weeks
  before. Google Trends values are relative within one request, so a keyword is only
  compared with its own series.
- Peak ("crowded"): recent interest is already near the 52-week high.
- Last-year lift: how interest moved last year from today's date to the window's end.
- Competition score 0-100 (lower is better) from Google results data.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "results": 0.25,
    "ads": 0.35,
    "merchants": 0.2,
    "spread": 0.2,
}
# Where each competition part reaches its maximum (1.0).
RESULTS_SATURATION = 1_000_000_000  # Google results count, log scale
ADS_SATURATION = 12  # text ads + shopping ads on page one
MERCHANTS_SATURATION = 8  # distinct sellers among shopping offers
SPREAD_SATURATION = 0.5  # coefficient of variation; at or above this, no price war


@dataclass(frozen=True, slots=True)
class TrendSignals:
    growth_rate: float | None  # 0.25 = +25%
    peak: bool
    last_year_lift: float | None
    recent_mean: float | None
    baseline_mean: float | None
    max_52w: int | None
    insufficient: bool
    points: int


def _nearest(points: Sequence[tuple[date, int]], target: date) -> int | None:
    best: tuple[int, int] | None = None
    for day, value in points:
        gap = abs((day - target).days)
        if gap <= 7 and (best is None or gap < best[0]):
            best = (gap, value)
    return best[1] if best else None


def trend_signals(
    points: Sequence[tuple[date, int | None]],
    *,
    as_of: date,
    window_end: date,
    recent_weeks: int = 3,
    baseline_weeks: int = 6,
    peak_ratio: float = 0.9,
    min_interest: int = 5,
) -> TrendSignals:
    clean = sorted((d, v) for d, v in points if v is not None)
    values = [v for _, v in clean]
    if len(values) < recent_weeks + baseline_weeks:
        return TrendSignals(
            None, False, None, None, None, max(values, default=None), True, len(values)
        )
    recent = statistics.fmean(values[-recent_weeks:])
    baseline = statistics.fmean(values[-(recent_weeks + baseline_weeks) : -recent_weeks])
    top = max(values)
    insufficient = max(recent, baseline) < min_interest
    growth = None if insufficient else round((recent - baseline) / max(baseline, 1.0), 4)
    peak = not insufficient and top > 0 and recent >= peak_ratio * top
    year = timedelta(days=364)
    ly_now = _nearest(clean, as_of - year)
    ly_end = _nearest(clean, window_end - year)
    lift = (
        round((ly_end - ly_now) / max(ly_now, 1), 4)
        if ly_now is not None and ly_end is not None and not insufficient
        else None
    )
    return TrendSignals(
        growth_rate=growth,
        peak=peak,
        last_year_lift=lift,
        recent_mean=round(recent, 2),
        baseline_mean=round(baseline, 2),
        max_52w=top,
        insufficient=insufficient,
        points=len(values),
    )


@dataclass(frozen=True, slots=True)
class CompetitionScore:
    score: int | None  # 0-100, lower is better; None when nothing was measured
    parts: dict[str, float]  # each part 0-1, higher = more competition
    missing: list[str]


def price_spread(prices_minor: Sequence[int]) -> float | None:
    """Coefficient of variation of offer prices; None with fewer than two prices."""
    if len(prices_minor) < 2:
        return None
    mean = statistics.fmean(prices_minor)
    return statistics.pstdev(prices_minor) / mean if mean > 0 else None


def competition_score(
    *,
    total_results: int | None,
    paid_ads: int | None,
    shopping_ads: int | None,
    merchants: int | None,
    spread: float | None,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> CompetitionScore:
    parts: dict[str, float] = {}
    if total_results is not None:
        parts["results"] = min(
            1.0, math.log10(max(total_results, 1)) / math.log10(RESULTS_SATURATION)
        )
    if paid_ads is not None or shopping_ads is not None:
        parts["ads"] = min(1.0, ((paid_ads or 0) + (shopping_ads or 0)) / ADS_SATURATION)
    if merchants is not None:
        parts["merchants"] = min(1.0, merchants / MERCHANTS_SATURATION)
    if spread is not None:
        # A tight price spread means sellers compete on price.
        parts["spread"] = 1.0 - min(1.0, spread / SPREAD_SATURATION)
    used = {k: w for k, w in weights.items() if k in parts and w > 0}
    missing = sorted(k for k in weights if k not in parts)
    if not used:
        return CompetitionScore(None, {}, missing)
    total = sum(used.values())
    score = round(100 * sum(parts[k] * w for k, w in used.items()) / total)
    return CompetitionScore(score, {k: round(v, 4) for k, v in parts.items()}, missing)
