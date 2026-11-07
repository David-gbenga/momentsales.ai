import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { createWorkspace, signInWithMagicLink, uniqueEmail } from "./helpers";

const PNG = Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), Buffer.alloc(32)]);

async function noSeriousViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, `${label}: ${serious.map((v) => `${v.id} (${v.nodes.length})`).join(", ")}`).toEqual([]);
}

async function openCommand(page: Page, text: string) {
  await page.locator("body").press("/");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox").fill(text);
  await page.keyboard.press("Enter");
}

test("connect Stripe (mock), take a test payment and see it counted within 10 s (AC-6)", async ({ page }) => {
  test.setTimeout(120_000);
  await signInWithMagicLink(page, uniqueEmail("seller"));
  await createWorkspace(page, "Payments Shop");
  const nav = page.getByRole("navigation", { name: "Main" });

  // /stripe connect from the command bar lands on Integrations; mock onboarding finishes it
  await openCommand(page, "/stripe connect");
  await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
  await page.getByRole("button", { name: "Connect Stripe" }).click();
  await expect(page.getByRole("heading", { name: "Mock Stripe onboarding" })).toBeVisible();
  await page.getByRole("button", { name: "Finish onboarding" }).click();
  await expect(page.getByTestId("stripe-status")).toHaveText("Connected");

  // Test checkout on Sales & Orders, paid on the mock Checkout page
  await nav.getByRole("link", { name: "Sales & Orders" }).click();
  await page.getByLabel("Item").first().fill("Soy candle");
  await page.getByLabel("Quantity").first().fill("2");
  await page.getByLabel("Price each (GBP)").first().fill("12.50");
  await page.getByLabel("Product key").first().fill("candle");
  await page.getByRole("button", { name: "Create checkout" }).click();
  await page.getByRole("link", { name: "Open checkout" }).click();
  await expect(page.getByRole("heading", { name: "Mock Stripe Checkout" })).toBeVisible();
  await page.getByRole("button", { name: /Pay £25\.00/ }).click();

  // Back on Sales: the counter shows the sale within 10 seconds (webhook -> worker -> ledger)
  await expect(page.getByTestId("sales-count")).toHaveText("1", { timeout: 10_000 });
  await expect(page.getByTestId("sales-gross")).toHaveText("£25.00");
  await expect(page.getByRole("cell", { name: "2 × Soy candle" })).toBeVisible();

  // Home shows it too
  await nav.getByRole("link", { name: "Home" }).click();
  await expect(page.getByTestId("home-today")).toHaveText("£25.00", { timeout: 10_000 });
});

test("/spend with a receipt, /touch and an audit pack (AC-8, AC-9, AC-10)", async ({ page }) => {
  test.setTimeout(120_000);
  await signInWithMagicLink(page, uniqueEmail("books"));
  await createWorkspace(page, "Books Shop");

  // AC-8: the command pre-fills the form; saving needs a click
  await openCommand(page, "/spend 24.99 GBP shipping vendor:Royal Mail");
  await expect(page.getByLabel("Amount", { exact: true })).toHaveValue("24.99");
  await expect(page.getByLabel("Vendor")).toHaveValue("Royal Mail");
  await expect(page.getByLabel("Category").first()).toHaveValue("shipping");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Spend saved.")).toBeVisible();
  await expect(page.getByText("Missing receipt: 1")).toBeVisible();

  // Upload a receipt; the mock OCR pre-fills from the fixture, the user attaches it
  await page.getByRole("tab", { name: "Receipts" }).click();
  const fixture = Buffer.concat([
    PNG,
    Buffer.from(`MOCK-OCR:${JSON.stringify({ vendor: "Royal Mail", total: "£24.99" })}`),
  ]);
  await page.getByLabel("Upload a receipt").setInputFiles({ name: "receipt.png", mimeType: "image/png", buffer: fixture });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText("Check the details we read from the receipt, then save.")).toBeVisible();
  await expect(page.getByLabel("Vendor")).toHaveValue("Royal Mail");
  await expect(page.getByLabel("Amount", { exact: true })).toHaveValue("24.99");
  await page.getByLabel("Or attach to an existing spend").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Attach receipt" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.getByRole("tab", { name: "Spends" }).click();
  await expect(page.getByText("Missing receipt: 0")).toBeVisible();

  // AC-9
  await openCommand(page, '/touch pricing 10m "adjusted margin on hoodie"');
  await expect(page.getByLabel("What you did")).toHaveValue("adjusted margin on hoodie");
  await expect(page.getByLabel("Minutes")).toHaveValue("10");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Touch saved.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "adjusted margin on hoodie" })).toBeVisible();
  await expect(page.getByText(/1 human touches \(10 min\)/)).toBeVisible();

  // P&L reflects the spend
  await page.getByRole("tab", { name: "P&L" }).click();
  await expect(page.getByTestId("pnl-profit")).toHaveText("-£24.99");

  // AC-10: the audit pack builds in the worker and every file downloads
  await page.getByRole("tab", { name: "Export" }).click();
  await page.getByRole("button", { name: "Build audit pack" }).click();
  await expect(page.getByText("Hash chain verified")).toBeVisible({ timeout: 30_000 });
  for (const name of ["ledger.csv", "summary.pdf", "receipts.zip", "audit-log.jsonl", "manifest.json"]) {
    await expect(page.getByRole("link", { name })).toBeVisible();
  }
  const href = await page.getByRole("link", { name: "summary.pdf" }).getAttribute("href");
  const pdf = await page.request.get(href ?? "");
  expect(pdf.ok()).toBe(true);
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
});

test("ledger screens have no serious axe violations, light and dark", async ({ page }) => {
  test.setTimeout(120_000);
  await signInWithMagicLink(page, uniqueEmail("axe"));
  await createWorkspace(page, "Axe Ledger");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/app/ledger");
    for (const tab of ["Spends", "Touches", "Receipts", "Entries", "P&L", "Export"]) {
      await page.getByRole("tab", { name: tab }).click();
      await noSeriousViolations(page, `${scheme} ledger ${tab}`);
    }
    for (const path of ["/app/sales", "/app/integrations", "/app"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await noSeriousViolations(page, `${scheme} ${path}`);
    }
  }
});
