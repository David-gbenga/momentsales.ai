import { describe, expect, it } from "vitest";

import {
  bestPrice,
  competitionOf,
  complianceOf,
  growthPct,
  offersOf,
  priceRange,
  profitOf,
  supplierOf,
  type ReportItem,
} from "@/lib/reports";

const item = {
  rank: 1,
  keyword: "heated throw",
  category: "home",
  score: 72,
  score_breakdown: {},
  why: "Rising.",
  why_source: "llm",
  image_url: null,
  best_offer: { merchant: "Shop A", price_minor: 1999, currency: "GBP", url: "https://example.com/a" },
  offers: [
    { merchant: "Shop A", price_minor: 1999, currency: "GBP", url: "https://example.com/a" },
    { merchant: "Shop B", price_minor: 2499, currency: "GBP", url: null },
  ],
  sizes: [],
  trend: { growth_rate: 0.664 },
  market: { price_min_minor: 1999, price_max_minor: 2499, currency: "GBP", competition_score: 41 },
  supplier: { status: "matched", match: { provider: "cj", delivery_min_days: 2, delivery_max_days: 4 } },
  profit: { profit_minor: 620, break_even_ad_cost_minor: 1020 },
  compliance: { status: "needs_documents", required_documents: ["UKCA or CE marking"] },
  flags: ["needs_documents"],
  fetched_at: null,
} as unknown as ReportItem;

describe("report helpers", () => {
  it("formats prices and the best offer", () => {
    expect(bestPrice(item)).toEqual({ price: "£19.99", platform: "Shop A" });
    expect(priceRange(item)).toBe("£19.99–£24.99");
    expect(offersOf(item)).toHaveLength(1);
  });

  it("reads supplier, profit, growth, competition and compliance", () => {
    expect(supplierOf(item).match?.delivery_max_days).toBe(4);
    expect(profitOf(item)).toEqual({ profit: "£6.20", breakEven: "£10.20" });
    expect(growthPct(item)).toBe(66);
    expect(competitionOf(item)).toBe(41);
    expect(complianceOf(item)).toEqual({ status: "needs_documents", documents: ["UKCA or CE marking"] });
  });

  it("says not available instead of guessing", () => {
    const empty = { ...item, best_offer: null, market: null, profit: null, trend: null, supplier: null } as ReportItem;
    expect(bestPrice(empty)).toBeNull();
    expect(priceRange(empty)).toBeNull();
    expect(profitOf(empty)).toBeNull();
    expect(growthPct(empty)).toBeNull();
    expect(supplierOf(empty)).toEqual({ status: "not_checked", match: null });
  });
});
