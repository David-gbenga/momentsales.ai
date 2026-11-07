import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const uniqueEmail = (prefix: string) =>
  `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;

interface OutboxMessage {
  to: string;
  subject: string;
  text: string;
}

/** Latest link to `path` emailed to `to`, read from the mock outbox (local mock mode only). */
export async function linkFromOutbox(
  request: APIRequestContext,
  to: string,
  path: string,
): Promise<string> {
  const pattern = new RegExp(`https?://\\S+${path.replace(/\//g, "\\/")}\\?token=\\S+`);
  let found = "";
  await expect
    .poll(
      async () => {
        const res = await request.get("/api/v1/dev/outbox?limit=200");
        const messages = (await res.json()) as OutboxMessage[];
        const msg = messages.find(
          (m) => m.to.toLowerCase() === to.toLowerCase() && m.text.includes(path),
        );
        found = msg?.text.match(pattern)?.[0] ?? "";
        return found;
      },
      { timeout: 10_000 },
    )
    .not.toBe("");
  const url = new URL(found);
  return url.pathname + url.search;
}

export async function signInWithMagicLink(page: Page, email: string): Promise<void> {
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Email me a sign-in link" }).click();
  await expect(page.getByText(/Check your inbox/)).toBeVisible();
  await page.goto(await linkFromOutbox(page.request, email, "/auth/verify"));
  // Wait until the verify page has exchanged the link for a session and redirected;
  // navigating away earlier aborts the exchange and leaves the browser signed out.
  await page.waitForURL(/\/(app|onboarding\/workspace)(\?|$)/);
}

export async function createWorkspace(page: Page, name: string): Promise<void> {
  await expect(page.getByRole("heading", { name: "Create your workspace" })).toBeVisible();
  await expect(page.getByLabel("Country")).toHaveValue("GB");
  await page.getByLabel("Shop name").fill(name);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}
