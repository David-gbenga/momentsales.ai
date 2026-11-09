import { describe, expect, it } from "vitest";

import {
  demandOf,
  failedChecks,
  formatBps,
  formatDay,
  formatDelivery,
  formatGrowth,
  isStale,
  measured,
  seasonOf,
  STALE_AFTER_MS,
  TERMINAL,
  type Demand,
  type Run,
  type SupplierResult,
} from "@/lib/research";

describe("research helpers", () => {
  it("formats ISO days in UK style without shifting the date", () => {
    expect(formatDay("2026-12-25")).toBe("Fri, 25 Dec 2026");
    expect(formatDay("2027-01-01")).toBe("Fri, 1 Jan 2027");
  });

  it("reads the season result from a run, or null before the Season agent runs", () => {
    expect(seasonOf(undefined)).toBeNull();
    expect(seasonOf({ season: null } as Run)).toBeNull();
    const run = { season: { current: "autumn", next: "winter", moments: [] } } as unknown as Run;
    expect(seasonOf(run)?.next).toBe("winter");
  });

  it("treats completed, partial and failed runs as finished", () => {
    expect([...TERMINAL].sort()).toEqual(["completed", "failed", "partial"]);
  });

  it("formats growth with a sign, and null as not available", () => {
    expect(formatGrowth(0.6585)).toBe("+66%");
    expect(formatGrowth(-0.124)).toBe("−12%");
    expect(formatGrowth(0.001)).toBe("0%");
    expect(formatGrowth(null)).toBeNull();
    expect(formatGrowth(Number.NaN)).toBeNull();
  });

  it("marks data older than 24 hours as stale", () => {
    const now = Date.parse("2026-11-08T12:00:00Z");
    expect(isStale(new Date(now - STALE_AFTER_MS + 1000).toISOString(), now)).toBe(false);
    expect(isStale(new Date(now - STALE_AFTER_MS - 1000).toISOString(), now)).toBe(true);
    expect(isStale("not a date", now)).toBe(false);
  });

  it("shows only searches with enough data", () => {
    expect(demandOf(undefined)).toBeNull();
    const trend = (insufficient: boolean) => ({ insufficient, growth_rate: 0.1, peak: false });
    const demand = {
      candidates: [
        { keyword: "a", trend: trend(false) },
        { keyword: "b", trend: trend(true) },
        { keyword: "c", trend: null },
      ],
    } as unknown as Demand;
    expect(measured(demand).map((c) => c.keyword)).toEqual(["a"]);
    expect(measured(null)).toEqual([]);
  });
});

describe("supplier helpers", () => {
  it("formats a delivery range", () => {
    expect(formatDelivery(2, 4)).toBe("2–4");
    expect(formatDelivery(3, 3)).toBe("3");
    expect(formatDelivery(null, 5)).toBe("5");
    expect(formatDelivery(2, null)).toBeNull();
  });

  it("formats basis points as a percentage", () => {
    expect(formatBps(2500)).toBe("25%");
    expect(formatBps(4649)).toBe("46%");
    expect(formatBps(null)).toBeNull();
  });

  it("lists the failed checks once, without their result", () => {
    const supplier = {
      status: "no_verified_supplier",
      reasons: ["margin:failed", "uk_returns:failed", "margin:not_checked", "no_uk_stock"],
    } as unknown as SupplierResult;
    expect(failedChecks(supplier)).toEqual(["margin", "uk_returns", "no_uk_stock"]);
    expect(failedChecks(null)).toEqual([]);
  });
});
