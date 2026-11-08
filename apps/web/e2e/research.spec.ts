import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { createWorkspace, signInWithMagicLink, uniqueEmail } from "./helpers";

async function expectNoSeriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
}

test("J2 (Phase 2a): season calendar on Home, then a seasonal run with live progress", async ({ page }) => {
  test.setTimeout(120_000);
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
  await page.getByRole("button", { name: "Run research" }).click();
  await page.waitForURL(/\/app\/research\?run=/);
  await expect(page.getByTestId("run-status")).toHaveText("Completed", { timeout: 30_000 });
  await expect(page.getByTestId("run-steps")).toContainText("Season and retail calendar");
  await expect(page.getByTestId("season-summary")).toContainText("Now:");
  await expect(page.getByTestId("run-spend")).toContainText("of £1.00");
  await expect(page.getByTestId("recent-runs").getByRole("listitem")).toHaveCount(1);
  await expectNoSeriousViolations(page);
});
