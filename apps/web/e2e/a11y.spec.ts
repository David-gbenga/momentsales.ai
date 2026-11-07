import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { createWorkspace, signInWithMagicLink, uniqueEmail } from "./helpers";

async function expectNoSeriousViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  const summary = serious.map((v) => `${v.id} (${v.nodes.length})`).join(", ");
  expect(serious, `${label}: ${summary}`).toEqual([]);
}

test("public pages have no serious axe violations", async ({ page }) => {
  for (const path of ["/", "/sign-in"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expectNoSeriousViolations(page, path);
  }
});

test("dashboard shell screens have no serious axe violations in light and dark", async ({
  page,
}) => {
  test.setTimeout(180_000); // 20 axe scans
  await signInWithMagicLink(page, uniqueEmail("a11y"));
  await createWorkspace(page, "A11y Shop");
  const nav = page.getByRole("navigation", { name: "Main" });
  const screens = [
    "Home",
    "Research",
    "Reports",
    "Listings",
    "Store",
    "Sales & Orders",
    "Ledger",
    "Integrations",
    "Billing",
    "Settings",
  ];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const name of screens) {
      await nav.getByRole("link", { name, exact: true }).click();
      await expect(nav.getByRole("link", { name, exact: true })).toHaveAttribute(
        "aria-current",
        "page",
      );
      await expectNoSeriousViolations(page, `${scheme} ${name}`);
    }
  }
});
