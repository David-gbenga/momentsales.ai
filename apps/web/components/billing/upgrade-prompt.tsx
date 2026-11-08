"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import type { PlanLimit } from "@/lib/billing";

/** Shown when the API returns 402 plan_limit_reached (the API enforces the limit). */
export function UpgradePrompt({ limit }: { limit: PlanLimit }) {
  const t = useTranslations("upgrade");
  return (
    <div
      role="alert"
      className="grid gap-2 rounded-md border border-primary/40 bg-accent p-3 text-sm"
    >
      <p className="font-medium">{t("title")}</p>
      <p>
        {t("body", {
          allowed: limit.allowed,
          limit: t(`limits.${limit.limit}`),
        })}
      </p>
      <Link
        href="/app/billing"
        className="w-fit font-medium text-primary underline-offset-4 hover:underline"
      >
        {t("action")}
      </Link>
    </div>
  );
}
