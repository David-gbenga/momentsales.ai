"use client";

import { annualSaving, formatMoney, priceFor } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import type { CatalogPlan } from "@/lib/billing";
import { cn } from "@/lib/utils";

type Interval = "month" | "year";

/** Plan cards with an annual/monthly switch (annual shown first, PRD FR-10). */
export function PlanPicker({
  plans,
  trialAvailable,
  canChoose,
  initialPlan,
  initialInterval = "year",
}: {
  plans: CatalogPlan[];
  trialAvailable: boolean;
  canChoose: boolean;
  initialPlan?: string | null;
  initialInterval?: Interval;
}) {
  const t = useTranslations("pricing");
  const [interval, setInterval] = useState<Interval>(initialInterval);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(code: string) {
    setBusy(code);
    setError(null);
    try {
      const { url } = await api<{ url: string }>("/billing/checkout", {
        body: { plan_code: code, interval, currency: "GBP" },
      });
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(null);
    }
  }

  return (
    <div className="grid gap-4">
      <div
        role="radiogroup"
        aria-label={t("title")}
        className="flex w-fit rounded-md border p-1"
      >
        {(["year", "month"] as const).map((i) => (
          <button
            key={i}
            type="button"
            role="radio"
            aria-checked={interval === i}
            onClick={() => setInterval(i)}
            className={cn(
              "min-h-9 rounded px-3 text-sm",
              interval === i
                ? "bg-primary text-primary-foreground"
                : "hover:bg-accent",
            )}
          >
            {i === "year" ? t("annual") : t("monthly")}
          </button>
        ))}
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {plans.map((plan) => {
          const price = priceFor(plan.prices, interval);
          const saving = annualSaving(plan.prices);
          return (
            <li key={plan.code}>
              <Card
                className={cn(
                  "grid h-full content-start gap-3",
                  initialPlan === plan.code && "border-primary",
                )}
                data-testid={`plan-${plan.code}`}
              >
                <h3 className="text-lg font-semibold">{plan.name}</h3>
                {price ? (
                  <p className="text-2xl font-semibold tabular-nums">
                    {interval === "year"
                      ? t("perYear", {
                          price: formatMoney(
                            price.amount_minor,
                            price.currency,
                          ),
                        })
                      : t("perMonth", {
                          price: formatMoney(
                            price.amount_minor,
                            price.currency,
                          ),
                        })}
                  </p>
                ) : null}
                {interval === "year" && saving > 0 ? (
                  <p className="text-sm font-medium text-primary">
                    {t("saving", { amount: formatMoney(saving, "GBP") })}
                  </p>
                ) : null}
                <PlanFeatures plan={plan} />
                {canChoose ? (
                  <Button
                    type="button"
                    className="mt-auto"
                    disabled={busy !== null}
                    onClick={() => void choose(plan.code)}
                  >
                    {trialAvailable
                      ? t("startTrial")
                      : t("choose", { plan: plan.name })}
                  </Button>
                ) : null}
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** What a plan includes, from the plans table (never hard-coded). */
export function PlanFeatures({ plan }: { plan: CatalogPlan }) {
  const t = useTranslations("pricing");
  const features = plan.features as { export_level?: string; support?: string };
  return (
    <ul className="grid gap-1 text-sm text-muted-foreground">
      {plan.research_runs !== null ? (
        <li>{t("runs", { count: plan.research_runs })}</li>
      ) : null}
      {plan.annual_bonus_runs > 0 ? (
        <li>{t("runsAnnual", { count: plan.annual_bonus_runs })}</li>
      ) : null}
      <li>
        {plan.live_listings === null
          ? t("listingsUnlimited")
          : t("listings", { count: plan.live_listings })}
      </li>
      {plan.seats !== null ? (
        <li>{t("seats", { count: plan.seats })}</li>
      ) : null}
      <li>
        {plan.platform_fee_bps > 0
          ? t("platformFee", { percent: plan.platform_fee_bps / 100 })
          : t("noPlatformFee")}
      </li>
      {features.export_level ? (
        <li>{t(`export.${features.export_level}` as "export.monthly_csv")}</li>
      ) : null}
      {features.support ? (
        <li>{t(`support.${features.support}` as "support.email")}</li>
      ) : null}
    </ul>
  );
}
