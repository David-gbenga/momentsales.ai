"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { Card } from "@/components/ui/primitives";

/** Workspaces without a plan see this instead of app screens (Billing and Settings stay
 * open). The API refuses gated writes anyway (403 subscription_required). */
export function PlanGate({ isOwner }: { isOwner: boolean }) {
  const t = useTranslations("gate");
  return (
    <section aria-labelledby="gate-title" className="grid max-w-2xl gap-4">
      <h1 id="gate-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      <Card className="grid gap-3">
        <p>{isOwner ? t("body") : t("waiting")}</p>
        {isOwner ? (
          <Link
            href="/app/billing"
            className="inline-flex min-h-11 w-fit items-center rounded-md bg-primary px-4 font-medium text-primary-foreground hover:bg-primary/90"
          >
            {t("action")}
          </Link>
        ) : null}
      </Card>
    </section>
  );
}
