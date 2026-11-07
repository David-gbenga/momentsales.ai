import { expect, test } from "@playwright/test";

import { createWorkspace, linkFromOutbox, signInWithMagicLink, uniqueEmail } from "./helpers";

test("sign up, create a UK workspace, invite an analyst who sees less", async ({
  page,
  browser,
}) => {
  const owner = uniqueEmail("owner");
  await signInWithMagicLink(page, owner);
  await createWorkspace(page, "E2E Candles");

  const nav = page.getByRole("navigation", { name: "Main" });
  for (const item of ["Home", "Research", "Ledger", "Billing", "Settings"]) {
    await expect(nav.getByRole("link", { name: item })).toBeVisible();
  }

  // Command bar opens from the keyboard and autocompletes FR-7 commands
  await page.locator("body").press("/");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("/spend")).toBeVisible();
  await dialog.getByRole("combobox").fill("/to");
  await expect(dialog.getByText("/touch")).toBeVisible();
  await expect(dialog.getByText("/spend")).toHaveCount(0);
  await page.keyboard.press("Escape");

  // Invite an analyst
  await nav.getByRole("link", { name: "Settings" }).click();
  const analyst = uniqueEmail("analyst");
  await page.getByLabel("Email", { exact: true }).fill(analyst);
  await page.getByLabel("Role", { exact: true }).selectOption("analyst");
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText(`Invitation sent to ${analyst}.`)).toBeVisible();
  const inviteLink = await linkFromOutbox(page.request, analyst, "/invite/accept");

  // The analyst signs in in a separate browser and accepts
  const ctx = await browser.newContext();
  const ap = await ctx.newPage();
  await signInWithMagicLink(ap, analyst);
  await ap.goto(inviteLink);
  const anav = ap.getByRole("navigation", { name: "Main" });
  await expect(anav.getByRole("link", { name: "Research" })).toBeVisible();
  await expect(anav.getByRole("link", { name: "Billing" })).toHaveCount(0);
  await expect(anav.getByRole("link", { name: "Ledger" })).toHaveCount(0);
  await expect(ap.getByText("Analyst", { exact: true })).toBeVisible();
  await anav.getByRole("link", { name: "Settings" }).click();
  await expect(ap.getByText("Only Owners and Admins can see the team.")).toBeVisible();
  await ctx.close();

  // Owner sees the analyst in the team list
  await page.reload();
  await expect(page.getByRole("cell", { name: analyst })).toBeVisible();
});

test("continue with Google (mock identity provider)", async ({ page }) => {
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "Continue with Google" }).click();
  await expect(page.getByRole("heading", { name: "Mock Google sign-in" })).toBeVisible();
  await page.getByLabel("Google account email").fill(uniqueEmail("google"));
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Create your workspace" })).toBeVisible();
});
