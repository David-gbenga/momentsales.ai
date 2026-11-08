import { describe, expect, it } from "vitest";

import { formatDay, seasonOf, TERMINAL, type Run } from "@/lib/research";

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
});
