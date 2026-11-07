import { expect, test } from "@playwright/test";

import { linkFromOutbox, uniqueEmail } from "./helpers";

test("landing page waitlist with double opt-in @mobile", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Find what");
  const email = uniqueEmail("wl");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel(/Email me about the momentsales.ai launch/).check();
  await page.getByRole("button", { name: "Join the waitlist" }).click();
  await expect(page.getByText(/confirm your email/i)).toBeVisible();
  await page.goto(await linkFromOutbox(page.request, email, "/waitlist/confirm"));
  await expect(page.getByText(/on the list/)).toBeVisible();
});

test("waitlist requires consent", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill(uniqueEmail("wl"));
  await page.getByRole("button", { name: "Join the waitlist" }).click();
  await expect(page.getByText("Tick the box to join the waitlist")).toBeVisible();
});
