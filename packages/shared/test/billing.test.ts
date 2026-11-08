import { describe, expect, it } from "vitest";

import { annualSaving, CANCEL_REASONS, daysLeft, priceFor, usagePercent } from "../src/billing";

const month = { currency: "GBP", interval: "month" as const, amount_minor: 7900 };
const year = { currency: "GBP", interval: "year" as const, amount_minor: 79000 };
const growth = [month, year];

describe("billing helpers", () => {
  it("finds a price by interval and currency", () => {
    expect(priceFor(growth, "year")?.amount_minor).toBe(79000);
    expect(priceFor(growth, "month", "USD")).toBeNull();
  });

  it("annual saving is 12 x monthly minus annual (two months free)", () => {
    expect(annualSaving(growth)).toBe(15800);
    expect(annualSaving([month])).toBe(0);
    expect(annualSaving([month, { ...year, amount_minor: 99999 }])).toBe(0);
  });

  it("counts days left, rounding up", () => {
    const now = new Date("2026-11-08T12:00:00Z");
    expect(daysLeft("2026-11-22T12:00:00Z", now)).toBe(14);
    expect(daysLeft("2026-11-08T13:00:00Z", now)).toBe(1);
    expect(daysLeft("2026-11-01T00:00:00Z", now)).toBe(0);
  });

  it("usage percent caps at 100 and treats unlimited as 0", () => {
    expect(usagePercent(3, 10)).toBe(30);
    expect(usagePercent(12, 10)).toBe(100);
    expect(usagePercent(5, null)).toBe(0);
    expect(usagePercent(1, 0)).toBe(100);
  });

  it("cancel reasons match the API", () => {
    expect(CANCEL_REASONS).toEqual([
      "no_sales",
      "too_expensive",
      "missing_feature",
      "seasonal_break",
      "other",
    ]);
  });
});
