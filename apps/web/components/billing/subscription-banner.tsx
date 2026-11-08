"use client";

import { daysLeft } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import Link from "next/link";

import type { Subscription } from "@/lib/billing";
import { cn } from "@/lib/utils";

/** Trial countdown, or why the workspace is read-only (ADR-0016). */
export function SubscriptionBanner({ sub }: { sub: Subscription }) {
  const t = useTranslations("banner");
  let text: string | null = null;
  let warn = false;
  if (sub.status === "trialing" && sub.trial_end) {
    text = t("trial", { days: daysLeft(sub.trial_end) });
  } else if (
    ["past_due", "suspended", "paused", "canceled"].includes(sub.status)
  ) {
    text = t(sub.status as "past_due" | "suspended" | "paused" | "canceled");
    warn = sub.status !== "paused";
  }
  if (!text) return null;
  return (
    <div
      role="status"
      data-testid="subscription-banner"
      className={cn(
        "mb-6 flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm",
        warn
          ? "border-destructive/50"
          : "border-primary/40 bg-accent text-accent-foreground",
      )}
    >
      <span>{text}</span>
      <Link
        href="/app/billing"
        className="font-medium underline-offset-4 hover:underline"
      >
        {sub.status === "trialing" ? t("trialAction") : t("fix")}
      </Link>
    </div>
  );
}
