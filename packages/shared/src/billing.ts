/** SaaS billing helpers shared by the web app (FR-10). Prices are integer minor units. */

export const CANCEL_REASONS = [
  "no_sales",
  "too_expensive",
  "missing_feature",
  "seasonal_break",
  "other",
] as const;
export type CancelReason = (typeof CANCEL_REASONS)[number];

export const PAUSE_MONTHS = [1, 2, 3] as const;

/** Subscription states that can use the app normally. */
export const FULL_ACCESS_STATUSES = new Set(["trialing", "active"]);
/** States where the workspace is read-only until the Owner acts (ADR-0016). */
export const READ_ONLY_STATUSES = new Set(["past_due", "suspended", "paused"]);
/** No usable plan: the app shows the plan picker. */
export const NO_PLAN_STATUSES = new Set(["none", "incomplete"]);

export interface PlanPrice {
  currency: string;
  interval: "month" | "year";
  amount_minor: number;
}

export function priceFor(prices: readonly PlanPrice[], interval: "month" | "year", currency = "GBP") {
  return prices.find((p) => p.interval === interval && p.currency === currency) ?? null;
}

/** What paying annually saves compared with 12 monthly payments (0 if it doesn't). */
export function annualSaving(prices: readonly PlanPrice[], currency = "GBP"): number {
  const month = priceFor(prices, "month", currency);
  const year = priceFor(prices, "year", currency);
  if (!month || !year) return 0;
  return Math.max(0, month.amount_minor * 12 - year.amount_minor);
}

/** Whole days left until `end` (rounded up; 0 once it has passed). */
export function daysLeft(end: string | Date, now: Date = new Date()): number {
  const ms = new Date(end).getTime() - now.getTime();
  return ms <= 0 ? 0 : Math.ceil(ms / 86_400_000);
}

/** Share of a limit used, 0–100, for usage bars. Unlimited (null) is 0. */
export function usagePercent(used: number, allowed: number | null | undefined): number {
  if (allowed === null || allowed === undefined) return 0;
  if (allowed <= 0) return used > 0 ? 100 : 0;
  return Math.min(100, Math.round((used / allowed) * 100));
}
