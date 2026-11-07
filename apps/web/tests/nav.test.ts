import { can, type Role } from "@momentsales/shared";
import { describe, expect, it } from "vitest";

import { NAV } from "@/lib/nav";

const visible = (role: Role) => NAV.filter((i) => can(role, i.permission)).map((i) => i.key);

describe("navigation gating", () => {
  it("shows owners every screen", () => {
    expect(visible("owner")).toHaveLength(NAV.length);
  });

  it("hides billing, ledger and store from analysts", () => {
    const items = visible("analyst");
    expect(items).toEqual(expect.arrayContaining(["research", "reports", "listings"]));
    expect(items).not.toEqual(expect.arrayContaining(["billing"]));
    expect(items).not.toContain("ledger");
    expect(items).not.toContain("store");
  });

  it("gives accountants the ledger but not research", () => {
    const items = visible("accountant");
    expect(items).toContain("ledger");
    expect(items).not.toContain("research");
    expect(items).not.toContain("billing");
  });
});
