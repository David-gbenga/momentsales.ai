import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { createWorkspace, signInWithMagicLink, uniqueEmail } from "./helpers";

async function expectNoSeriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}

test("J2 (Phase 2): season calendar, a seasonal run, UK suppliers and the Top 10 report", async ({ page, browser }) => {
  test.setTimeout(180_000);
  // Mock evidence for the Verified UK supplier rule (a Platform admin records real evidence).
  expect((await page.request.post("/api/v1/dev/mock-suppliers/verify")).ok()).toBeTruthy();
  await signInWithMagicLink(page, uniqueEmail("research"));
  await createWorkspace(page, "Research Shop");

  // Home: the next three retail moments with act-by dates (AC-26).
  await page.goto("/app");
  const calendar = page.getByTestId("season-calendar");
  await expect(calendar.getByRole("heading", { name: "Coming up" })).toBeVisible();
  await expect(calendar.getByRole("listitem")).toHaveCount(3);
  await expect(calendar).toContainText("Act by");

  // Describe the audience, then start a run.
  await page.goto("/app/research");
  await expect(page.getByRole("heading", { name: "Research", level: 1 })).toBeVisible();
  await page.getByLabel("Age range").selectOption("25-34");
  await page.getByLabel("Interests").fill("hiking, camping");
  await page.getByRole("button", { name: "Save audience" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();

  await page.getByLabel("Look-ahead window").selectOption("8");
  await page.getByLabel("Category (optional)").fill("gifts");
  await page.getByRole("button", { name: "Run research" }).click();
  await page.waitForURL(/\/app\/research\?run=/);
  await expect(page.getByTestId("run-status")).toHaveText("Completed", { timeout: 30_000 });
  await expect(page.getByTestId("run-steps")).toContainText("Season and retail calendar");
  await expect(page.getByTestId("season-summary")).toContainText("Now:");
  await expect(page.getByTestId("run-spend")).toContainText("of £1.00");

  // Rising searches (Phase 2b, AC-25 data part): growth, competition, and a "Crowded"
  // badge on a search already at its high (mock data, labelled as such).
  const demand = page.getByTestId("demand-results");
  await expect(demand.getByRole("heading", { name: "Rising searches" })).toBeVisible();
  await expect(demand).toContainText("Mock data");
  await expect(demand.getByTestId("demand-row").first()).toBeVisible();
  await expect(
    demand.locator('[data-keyword="advent calendar"]').getByTestId("badge-crowded"),
  ).toHaveText("Crowded");
  // Fastest growing first: the top row is rising.
  await expect(demand.getByTestId("demand-row").first().locator("td").nth(1)).toContainText("+");
  await expect(page.getByTestId("run-steps")).toContainText("Search trends");
  await expect(page.getByTestId("run-steps")).toContainText("Competition");
  // UK suppliers (Phase 2c, AC-20): every checked search shows a supplier result.
  await expect(page.getByTestId("run-steps")).toContainText("UK suppliers");
  const cells = demand.getByTestId("supplier-cell");
  await expect(cells.first()).toBeVisible();
  await expect(demand.locator('[data-status="matched"]').first()).toContainText("landed");
  await expect(demand.locator('[data-status="matched"]').first()).toContainText("GB warehouse");
  // Any search without a usable supplier says so, with a reason.
  await expect(demand.locator('[data-status="no_verified_supplier"], [data-status="no_supplier"]').first()).toContainText("Why:");
  await expect(page.getByTestId("recent-runs").getByRole("listitem")).toHaveCount(1);
  await expectNoSeriousViolations(page);

  // Top 10 report (Phase 2d, AC-1): ten products, each with a reason, links and a score.
  await expect(page.getByTestId("run-steps")).toContainText("Top 10 report");
  await page.getByTestId("view-report").click();
  await page.waitForURL(/\/app\/reports\//);
  const items = page.getByTestId("report-item");
  await expect(items).toHaveCount(10);
  await expect(items.first().getByTestId("item-why")).not.toBeEmpty();
  await expect(items.first().getByTestId("item-links").getByRole("link")).not.toHaveCount(0);
  await expect(page.getByText("How we scored these")).toBeVisible();
  await expectNoSeriousViolations(page);

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download CSV" }).click();
  expect((await download).suggestedFilename()).toMatch(/^top10-.*\.csv$/);

  // A read-only share link works without signing in and hides the run cost.
  await page.getByRole("button", { name: "Create link" }).click();
  const shareUrl = await page.getByTestId("share-url").getAttribute("href");
  expect(shareUrl).toContain("/r/");
  const visitor = await browser.newContext();
  const shared = await visitor.newPage();
  await shared.goto(new URL(shareUrl!).pathname);
  await expect(shared.getByTestId("report-item")).toHaveCount(10);
  await expect(shared.getByTestId("report-summary")).not.toContainText("Run cost");
  await expectNoSeriousViolations(shared);
  await visitor.close();
});
