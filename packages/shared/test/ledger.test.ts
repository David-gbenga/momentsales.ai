import { describe, expect, it } from "vitest";

import {
  COMMANDS,
  can,
  formatMoney,
  parseSaleArgs,
  parseSpendArgs,
  parseTouchArgs,
  spendFormSchema,
  tokenize,
} from "../src";

describe("command arguments", () => {
  it("parses the AC-8 /spend example", () => {
    expect(parseSpendArgs("24.99 GBP shipping vendor:Royal Mail")).toEqual({
      amount: "24.99",
      currency: "GBP",
      category: "shipping",
      vendor: "Royal Mail",
    });
  });

  it("parses ads spend with a product and a currency symbol", () => {
    expect(parseSpendArgs("€15 ads vendor:Meta product:Hoodie vat:2.50")).toMatchObject({
      amount: "15",
      currency: "EUR",
      category: "ads",
      vendor: "Meta",
      product_key: "hoodie",
      tax_amount: "2.50",
    });
  });

  it("parses the AC-9 /touch example", () => {
    expect(parseTouchArgs('pricing 10m "adjusted margin on hoodie"')).toEqual({
      category: "pricing",
      minutes: 10,
      description: "adjusted margin on hoodie",
    });
    expect(parseTouchArgs("service 1h replied to a customer")).toEqual({
      category: "customer_service",
      minutes: 60,
      description: "replied to a customer",
    });
  });

  it("parses /sale", () => {
    expect(parseSaleArgs("£30 2026-11-06 Etsy ref:1234")).toEqual({
      amount: "30",
      sold_on: "2026-11-06",
      channel: "Etsy",
      order_ref: "1234",
    });
  });

  it("tokenises quoted phrases", () => {
    expect(tokenize('a "b c" “d e”')).toEqual(["a", "b c", "d e"]);
  });
});

describe("money and forms", () => {
  it("formats minor units for en-GB", () => {
    expect(formatMoney(2499)).toBe("£24.99");
    expect(formatMoney(-150)).toBe("-£1.50");
    expect(formatMoney(500, "JPY")).toBe("JP¥500");
  });

  it("requires a GBP amount for foreign spends and a product for ads", () => {
    const base = { amount: "20", currency: "USD", spent_on: "2026-11-06", vendor: "Canva", category: "ads", payment_method: "card" } as const;
    const result = spendFormSchema.safeParse(base);
    expect(result.success).toBe(false);
    const paths = result.error?.issues.map((i) => i.path.join(".")) ?? [];
    expect(paths).toContain("base_amount");
    expect(paths).toContain("product_key");
    expect(spendFormSchema.safeParse({ ...base, base_amount: "15.87", product_key: "hoodie" }).success).toBe(true);
  });

  it("gates commands by role like the API", () => {
    const permission = (name: string) => COMMANDS.find((c) => c.name === name)?.permission ?? "flags.read";
    expect(can("admin", permission("/touch"))).toBe(true);
    expect(can("analyst", permission("/touch"))).toBe(false);
    expect(can("accountant", permission("/export"))).toBe(true);
    expect(COMMANDS.every((c) => c.enabled)).toBe(true);
  });
});
