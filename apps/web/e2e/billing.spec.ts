import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { createWorkspace, signInWithMagicLink, startFreeTrial, uniqueEmail } from "./helpers";

async function expectNoSeriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}

async function openPortal(page: Page) {
  await page.goto("/app/billing");
  await page.getByRole("button", { name: "Manage billing" }).click();
  await page.waitForURL(/\/dev\/mock-billing\/portal/);
}

async function portalTool(page: Page, name: string) {
  await page.getByRole("button", { name }).click();
  await expect(page.getByText("Done.")).toBeVisible();
}

test("public pricing page lists the plans from the plans table @mobile", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
  for (const plan of ["lite", "starter", "growth", "pro"]) {
    await expect(page.getByTestId(`price-${plan}`)).toBeVisible();
  }
  await expect(page.getByTestId("price-growth")).toContainText("£790.00/year");
  await expect(page.getByTestId("price-growth")).toContainText("Save £158.00 a year");
  await expect(page.getByText(/single research report is £9\.00/)).toBeVisible();
  await expectNoSeriousViolations(page);
});

test("trial sign-up: gate, Checkout, trialing with a countdown and a 3-run cap (AC-14)", async ({ page }) => {
  test.setTimeout(90_000);
  await signInWithMagicLink(page, uniqueEmail("trial"));
  await createWorkspace(page, "Trial Shop", { startTrial: false });
  // No plan yet: app screens show the plan gate; Billing stays open
  await expect(page.getByRole("heading", { name: "Choose a plan to get started" })).toBeVisible();
  await page.getByRole("link", { name: "Choose a plan" }).click();
  await expect(page).toHaveURL(/\/app\/billing/);
  await expect(page.getByTestId("subscription-status")).toHaveText("No plan");

  await startFreeTrial(page, "growth");
  await expect(page.getByTestId("subscription-banner")).toContainText("14 days left in your free trial");
  await expect(page.getByTestId("subscription-dates")).toContainText("Trial ends");
  await expect(page.getByTestId("usage-runs")).toHaveText("0 of 3");
  await expect(page.getByTestId("usage-seats")).toHaveText("1 of 3");
  // The gate is gone
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Home" }).click();
  await expect(page.getByRole("heading", { name: "Choose a plan to get started" })).toHaveCount(0);
});

test("plan limit shows an upgrade prompt; a run pack lets the next run start (AC-15)", async ({ page }) => {
  test.setTimeout(90_000);
  await signInWithMagicLink(page, uniqueEmail("limits"));
  await createWorkspace(page, "Limits Shop");
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Use one research run" }).click();
    await expect(page.getByText("Run used (from your plan).")).toBeVisible();
  }
  await expect(page.getByTestId("usage-runs")).toHaveText("3 of 3");
  await page.getByRole("button", { name: "Use one research run" }).click();
  await expect(page.getByText("Plan limit reached")).toBeVisible();
  await expect(page.getByText("Your plan allows 3 research runs.")).toBeVisible();

  await page.getByRole("button", { name: /Buy run pack/ }).click();
  await page.waitForURL(/\/dev\/mock-billing\/checkout\//);
  await page.getByRole("button", { name: "Pay £15.00" }).click();
  await page.waitForURL(/\/app\/billing\?purchase=success/);
  await expect(page.getByTestId("usage-extra")).toContainText("10 from packs", { timeout: 10_000 });
  await page.getByRole("button", { name: "Use one research run" }).click();
  await expect(page.getByText("Run used (from a run pack).")).toBeVisible();
  await expect(page.getByTestId("usage-extra")).toContainText("9 from packs");
});

test("Portal upgrade, then dunning: failed payment, read-only banner, paid again (AC-16, AC-17)", async ({ page }) => {
  test.setTimeout(120_000);
  await signInWithMagicLink(page, uniqueEmail("dunning"));
  await createWorkspace(page, "Dunning Shop");

  // End the trial, then upgrade in the Portal: it applies straight away
  await openPortal(page);
  await portalTool(page, "Start the next billing period");
  await page.getByRole("button", { name: /Switch to Pro \(year\)/ }).click();
  await expect(page.getByTestId("mock-portal-current")).toContainText("pro (year, active)", { timeout: 10_000 });
  await page.getByRole("link", { name: "Back to Billing" }).click();
  await expect(page.getByTestId("subscription-status")).toHaveText("Active");
  await expect(page.getByRole("heading", { name: /Plan: Pro · Annual/ })).toBeVisible();

  // A failed renewal: read-only with a banner (the API refuses writes)
  await openPortal(page);
  await portalTool(page, "Fail the next payment");
  await page.getByRole("link", { name: "Back to Billing" }).click();
  await expect(page.getByTestId("subscription-status")).toHaveText("Payment failed");
  await expect(page.getByTestId("subscription-banner")).toContainText("We couldn't take your last payment");

  // Paying the invoice restores access
  await openPortal(page);
  await portalTool(page, "Pay the open invoice");
  await page.getByRole("link", { name: "Back to Billing" }).click();
  await expect(page.getByTestId("subscription-status")).toHaveText("Active");
  await expect(page.getByTestId("subscription-banner")).toHaveCount(0);
});

test("billing screens have no serious axe violations, light and dark", async ({ page }) => {
  test.setTimeout(90_000);
  await signInWithMagicLink(page, uniqueEmail("axebill"));
  await createWorkspace(page, "Axe Billing", { startTrial: false });
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/app/billing");
    await expect(page.getByTestId("plan-growth")).toBeVisible();
    await expectNoSeriousViolations(page);
  }
  await startFreeTrial(page);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/app/billing");
    await expect(page.getByTestId("usage-runs")).toBeVisible();
    await expectNoSeriousViolations(page);
  }
});
