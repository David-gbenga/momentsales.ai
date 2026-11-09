"""Unit tests for supplier matching and the Verified UK supplier rule (FR-11, ADR-0020)."""

from __future__ import annotations

from datetime import date

from momentsales.adapters.supplier.base import FreightOption, SupplierItem
from momentsales.modules.research import supplier_rules as rules

MARGIN = {
    "stripe_fee_bps": 150,
    "stripe_fee_fixed_minor": 20,
    "platform_fee_bps": 200,
    "returns_allowance_bps": 500,
    "floor_bps": 2500,
}


def item(**kw: object) -> SupplierItem:
    base: dict[str, object] = {
        "external_id": "1",
        "title": "Heated Throw Blanket",
        "unit_cost_minor": 500,
        "currency": "GBP",
        "warehouse_country": "GB",
        "uk_stock": 10,
        "variant_id": "v1",
    }
    return SupplierItem.model_validate({**base, **kw})


def test_match_confidence_ignores_plurals_and_case() -> None:
    assert rules.match_confidence("heated throw", "Heated Throws, Grey") == 1.0
    assert rules.match_confidence("heated throw", "Electric Heated Blanket") == 0.5
    assert rules.match_confidence("heated throw", "Garden Gnome") == 0.0
    assert rules.match_confidence("", "anything") == 0.0


def test_qualifies_needs_match_uk_stock_gb_warehouse_and_variant() -> None:
    assert rules.qualifies(item(), "heated throw", "GB", 0.5)
    assert not rules.qualifies(item(uk_stock=0), "heated throw", "GB", 0.5)
    assert not rules.qualifies(item(warehouse_country="CN"), "heated throw", "GB", 0.5)
    assert not rules.qualifies(item(variant_id=None), "heated throw", "GB", 0.5)
    assert not rules.qualifies(item(title="Garden Gnome"), "heated throw", "GB", 0.5)


def test_best_tracked_prefers_cheapest_tracked() -> None:
    options = [
        FreightOption(name="Post", cost_minor=100, tracked=None),
        FreightOption(name="Tracked 48", cost_minor=300, max_days=4, tracked=True),
        FreightOption(name="Tracked 24", cost_minor=250, max_days=2, tracked=True),
    ]
    best = rules.best_tracked(options)
    assert best is not None and best.name == "Tracked 24"
    assert rules.best_tracked([FreightOption(name="Post", tracked=None)]) is None
    # No cost known: the fastest tracked option.
    no_cost = [
        FreightOption(name="A", max_days=5, tracked=True),
        FreightOption(name="B", max_days=3, tracked=True),
    ]
    picked = rules.best_tracked(no_cost)
    assert picked is not None and picked.name == "B"


def test_estimated_sell_price_is_median_of_gbp_offers() -> None:
    market = {
        "offers": [
            {"price_minor": 2000, "currency": "GBP"},
            {"price_minor": 3000, "currency": "GBP"},
            {"price_minor": 9999, "currency": "USD"},
            {"price_minor": 2500, "currency": "GBP"},
        ]
    }
    assert rules.estimated_sell_price(market) == 2500
    assert rules.estimated_sell_price({"offers": []}) is None
    assert rules.estimated_sell_price(None) is None


def test_margin_maths_in_minor_units() -> None:
    m = rules.margin_check(sell_price_minor=2000, unit_cost_minor=600, shipping_minor=290, **MARGIN)
    # Stripe 30 + 20, platform 40, returns 100: net 2000-600-290-50-40-100 = 920 (46%).
    assert (m.stripe_fee_minor, m.platform_fee_minor, m.returns_allowance_minor) == (50, 40, 100)
    assert m.net_minor == 920 and m.margin_bps == 4600 and m.result == "passed"


def test_margin_rounds_against_the_seller() -> None:
    m = rules.margin_check(sell_price_minor=999, unit_cost_minor=0, shipping_minor=0, **MARGIN)
    assert m.stripe_fee_minor == 15 + 20  # 14.985 rounds up
    assert m.returns_allowance_minor == 50  # 49.95 rounds up


def test_margin_below_floor_fails() -> None:
    m = rules.margin_check(
        sell_price_minor=2000, unit_cost_minor=1200, shipping_minor=300, **MARGIN
    )
    assert m.result == "failed" and m.margin_bps is not None and m.margin_bps < 2500


def test_margin_with_missing_inputs_is_not_checked() -> None:
    m = rules.margin_check(
        sell_price_minor=2000, unit_cost_minor=500, shipping_minor=None, **MARGIN
    )
    assert m.result == "not_checked" and m.missing == ["shipping"]
    m = rules.margin_check(sell_price_minor=None, unit_cost_minor=500, shipping_minor=100, **MARGIN)
    assert m.result == "not_checked" and m.missing == ["sell_price"]


def test_verification_results_missing_evidence_fails() -> None:
    evidence = {
        "dropship_no_moq": {"passed": True},
        "auto_stock_cost": {"passed": True},
        "tracked_uk": {"passed": True},
        "uk_returns": {"passed": False},
    }
    checks = rules.verification_results(evidence, tracked_option=True)
    assert checks["uk_returns"] == "failed" and checks["dropship_no_moq"] == "passed"
    assert set(rules.verification_results({}, tracked_option=True).values()) == {"failed"}
    # Evidence for tracked delivery is not enough without a tracked option for this item.
    assert rules.verification_results(evidence, tracked_option=False)["tracked_uk"] == "failed"
    assert rules.reasons(checks) == ["uk_returns:failed"]


def test_moment_deadline_and_arrives_too_late() -> None:
    moments = [
        {"date": "2026-11-27", "category_hints": ["Electronics", "gifts"]},
        {"date": "2026-12-25", "category_hints": ["gifts", "home"]},
    ]
    end = date(2026, 12, 31)
    assert rules.moment_deadline("gifts", moments, end) == date(2026, 12, 25)
    assert rules.moment_deadline("electronics", moments, end) == date(2026, 11, 27)
    assert rules.moment_deadline("garden", moments, end) == end
    as_of = date(2026, 11, 6)
    assert rules.arrives_too_late(as_of, 4, date(2026, 11, 27)) is False
    assert rules.arrives_too_late(as_of, 60, date(2026, 12, 25)) is True
    assert rules.arrives_too_late(as_of, None, end) is None
