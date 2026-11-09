"""Unit tests for scoring, profit, compliance rules and the report "why" guard (ADR-0021)."""

from __future__ import annotations

from typing import Any

from momentsales.modules.research import compliance_rules, scoring
from momentsales.modules.research import report_build as rb
from momentsales.modules.research.profit import ad_cost_bps, profit_forecast

WEIGHTS = {
    "rising": 0.25,
    "competition": 0.15,
    "sales_rank": 0.15,
    "seasonal": 0.15,
    "profit": 0.20,
    "supplier": 0.10,
}


def cand(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "keyword": "heated throw",
        "category": "home",
        "source": "seed",
        "trend": {"growth_rate": 0.66, "peak": False},
        "market": {"competition_score": 40},
        "supplier": {
            "status": "matched",
            "arrives_too_late": False,
            "match": {"delivery_max_days": 4},
        },
        "profit": {"margin_bps": 3000},
    }
    return {**base, **kw}


def test_parts() -> None:
    assert scoring.rising_part({"growth_rate": 0.5, "peak": False}) == 0.5
    assert scoring.rising_part({"growth_rate": 2.0, "peak": False}) == 1.0
    assert scoring.rising_part({"growth_rate": -0.2, "peak": False}) == 0.0
    assert scoring.rising_part({"growth_rate": 0.5, "peak": True}) == 0.25
    assert scoring.rising_part(None) == 0.0
    assert scoring.competition_part({"competition_score": 30}) == 0.7
    assert scoring.competition_part(None) == 0.0
    assert scoring.seasonal_part({"arrives_too_late": True}) == 0.0
    assert scoring.seasonal_part({"arrives_too_late": False}) == 1.0
    assert scoring.seasonal_part(None) == 0.5
    assert scoring.profit_part({"margin_bps": 2000}) == 0.5
    assert scoring.profit_part({"margin_bps": -100}) == 0.0
    assert scoring.profit_part(None) == 0.0
    for days, part in ((2, 1.0), (5, 0.75), (7, 0.5), (12, 0.2)):
        assert (
            scoring.supplier_part({"status": "matched", "match": {"delivery_max_days": days}})
            == part
        )
    assert scoring.supplier_part({"status": "no_verified_supplier", "match": {}}) == 0.0


def test_sales_rank_is_dropped_and_weights_rescaled() -> None:
    result = scoring.score(cand(), WEIGHTS, [])
    assert result["dropped"] == ["sales_rank"]
    assert "sales_rank" not in result["weights"]
    assert abs(sum(result["weights"].values()) - 1.0) < 0.001
    assert result["weights"]["rising"] == round(0.25 / 0.85, 4)


def test_crowded_and_late_rank_below_otherwise_equal() -> None:
    rising = scoring.score(cand(), WEIGHTS, [])["score"]
    crowded = scoring.score(cand(trend={"growth_rate": 0.66, "peak": True}), WEIGHTS, [])["score"]
    late = scoring.score(
        cand(
            supplier={
                "status": "matched",
                "arrives_too_late": True,
                "match": {"delivery_max_days": 4},
            }
        ),
        WEIGHTS,
        [],
    )["score"]
    assert crowded < rising and late < rising


def test_missing_data_never_beats_data() -> None:
    empty = cand(
        market=None, supplier=None, profit=None, trend={"growth_rate": None, "peak": False}
    )
    assert scoring.score(empty, WEIGHTS, [])["score"] < scoring.score(cand(), WEIGHTS, [])["score"]


def test_audience_fit() -> None:
    assert scoring.audience_fit("camping chair", "outdoor", "seed", ["camping"]) == 5
    assert scoring.audience_fit("camping chair", "outdoor", "audience", ["camping"]) == 10
    assert scoring.audience_fit("camping chair", "outdoor", "seed", []) == 0
    assert scoring.audience_fit("mug", "home", "seed", ["hiking"]) == 0


def test_dedup_merges_into_the_one_with_more_data() -> None:
    a = cand(keyword="heated throws", supplier=None, market=None)
    b = cand(keyword="heated throw")
    c = cand(keyword="fairy lights")
    kept = scoring.dedup([a, b, c])
    assert [k["keyword"] for k in kept] == ["heated throw", "fairy lights"]
    assert kept[0]["merged"] == ["heated throws"]


def test_profit_forecast_minor_units() -> None:
    p = profit_forecast(
        sell_price_minor=2000,
        unit_cost_minor=600,
        shipping_minor=290,
        stripe_fee_bps=150,
        stripe_fee_fixed_minor=20,
        platform_fee_bps=200,
        ad_cost_bps=2000,
    )
    assert p is not None
    # Stripe 50, platform 40, ads 400: before ads 1020, profit 620.
    assert (p["stripe_fee_minor"], p["platform_fee_minor"], p["ad_cost_minor"]) == (50, 40, 400)
    assert p["break_even_ad_cost_minor"] == 1020 and p["profit_minor"] == 620
    assert p["margin_bps"] == 3100
    assert (
        profit_forecast(
            sell_price_minor=None,
            unit_cost_minor=1,
            shipping_minor=1,
            stripe_fee_bps=0,
            stripe_fee_fixed_minor=0,
            platform_fee_bps=0,
            ad_cost_bps=0,
        )
        is None
    )
    assert ad_cost_bps("Beauty", 2000, {"beauty": 3000}) == 3000
    assert ad_cost_bps("home", 2000, {"beauty": 3000}) == 2000


def test_compliance_rules() -> None:
    flags = {h["flag"] for h in compliance_rules.check(["Heated throw", "home", ""])}
    assert flags == {"electrical"}
    blocked = compliance_rules.check(["Kitchen knife set"])
    assert blocked[0]["kind"] == "blocked"
    assert {h["flag"] for h in compliance_rules.check(["Lego style bricks"])} == {"trademark_risk"}
    assert compliance_rules.check(["scented candles", "home"]) == []
    # Whole words only: "plush" is a toy word, "plushy" text still matches only whole words.
    assert compliance_rules.check(["pledge"]) == []


def test_merge_llm_adds_but_never_removes() -> None:
    rules = compliance_rules.check(["Heated throw"])
    merged = compliance_rules.merge(rules, {"restricted": False, "categories": []})
    assert merged["status"] == "needs_documents"
    assert "UKCA or CE marking" in merged["required_documents"]
    vetoed = compliance_rules.merge(
        [], {"restricted": True, "reason": "age-restricted", "categories": ["toy"]}
    )
    assert vetoed["status"] == "blocked"
    assert {f["flag"] for f in vetoed["flags"]} == {"restricted_item", "toy"}
    assert compliance_rules.merge([], None)["status"] == "ok"
    unsure = compliance_rules.merge([], {"unsure": True})
    assert unsure["llm_unsure"] and unsure["status"] == "ok"


def test_why_guard_rejects_invented_numbers() -> None:
    facts = {"growth_pct": 66, "growth_window_weeks": 3, "delivery_max_days": 4}
    assert rb.why_is_grounded("Searches are up 66% in 3 weeks; delivery in 4 days.", facts, "x")
    assert not rb.why_is_grounded("Sells for £19.99.", facts, "x")
    assert not rb.why_is_grounded("Up 70% this month.", facts, "x")
    assert not rb.why_is_grounded("", facts, "x")
    assert rb.why_is_grounded("A 3 in 1 tool that fits Christmas.", {}, "3 in 1 tool")
    template = rb.template_why(facts)
    assert "66%" in template and rb.why_is_grounded(template, facts, "x")  # templates pass too


def test_pick_top_skips_blocked_and_prefers_market_data() -> None:
    cands = [
        {"keyword": "a", "market": {"x": 1}, "compliance": {"status": "blocked"}},
        {"keyword": "b", "market": None, "compliance": {"status": "ok"}},
        {"keyword": "c", "market": {"x": 1}, "compliance": {"status": "needs_documents"}},
    ]
    top, blocked = rb.pick_top(cands, ["a", "b", "c"], 10)
    assert [c["keyword"] for c in top] == ["c", "b"]
    assert [c["keyword"] for c in blocked] == ["a"]
