import { annualSaving, formatMoney, priceFor } from "@momentsales/shared";
import type { components } from "@momentsales/shared/api";
import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

type Catalog = components["schemas"]["CatalogOut"];
type Plan = components["schemas"]["PlanOut"];

// Prices come from the plans table at request time (a Platform admin can change them).
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pricing");
  return {
    title: `${t("title")} · momentsales.ai`,
    description: t("metaDescription"),
  };
}

async function loadCatalog(): Promise<Catalog | null> {
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${base}/api/v1/plans`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as Catalog) : null;
  } catch {
    return null;
  }
}

function PlanCard({
  plan,
  t,
}: {
  plan: Plan;
  t: Awaited<ReturnType<typeof getTranslations<"pricing">>>;
}) {
  const year = priceFor(plan.prices, "year");
  const month = priceFor(plan.prices, "month");
  const saving = annualSaving(plan.prices);
  const features = plan.features as { export_level?: string; support?: string };
  return (
    <li
      className="grid content-start gap-3 rounded-xl border bg-card p-6 shadow-sm"
      data-testid={`price-${plan.code}`}
    >
      <h2 className="text-lg font-semibold">{plan.name}</h2>
      {year ? (
        <p>
          <span className="text-2xl font-semibold tabular-nums">
            {t("perYear", {
              price: formatMoney(year.amount_minor, year.currency),
            })}
          </span>
          {saving > 0 ? (
            <span className="block text-sm font-medium text-primary">
              {t("saving", { amount: formatMoney(saving, year.currency) })}
            </span>
          ) : null}
        </p>
      ) : null}
      {month ? (
        <p className="text-sm text-muted-foreground">
          {t("monthly")}:{" "}
          {t("perMonth", {
            price: formatMoney(month.amount_minor, month.currency),
          })}
        </p>
      ) : null}
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
        {features.export_level ? (
          <li>
            {t(`export.${features.export_level}` as "export.monthly_csv")}
          </li>
        ) : null}
        {features.support ? (
          <li>{t(`support.${features.support}` as "support.email")}</li>
        ) : null}
      </ul>
      <Link
        href="/sign-in"
        className="mt-auto inline-flex min-h-11 items-center justify-center rounded-md bg-primary px-4 font-medium text-primary-foreground hover:bg-primary/90"
      >
        {t("startTrial")}
      </Link>
    </li>
  );
}

export default async function PricingPage() {
  const t = await getTranslations("pricing");
  const catalog = await loadCatalog();
  const pack = catalog?.addons.find((a) => a.kind === "run_pack");
  const report = catalog?.addons.find((a) => a.kind === "single_report");
  return (
    <div className="mx-auto flex min-h-dvh max-w-6xl flex-col px-4 sm:px-6">
      <header className="flex items-center justify-between py-6">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          momentsales.ai
        </Link>
        <Link
          href="/sign-in"
          className="text-sm font-medium underline-offset-4 hover:underline"
        >
          {t("signIn")}
        </Link>
      </header>
      <main id="main" className="grid gap-8 py-10">
        <div className="grid max-w-2xl gap-3">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {t("title")}
          </h1>
          <p className="text-muted-foreground">
            {t("intro", { runs: catalog?.trial.runs ?? 3 })}
          </p>
        </div>
        {catalog ? (
          <>
            <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {catalog.plans.map((plan) => (
                <PlanCard key={plan.code} plan={plan} t={t} />
              ))}
            </ul>
            <div className="grid gap-2 text-sm text-muted-foreground">
              {pack ? (
                <p>
                  {t("packs", {
                    runs: pack.runs,
                    price: formatMoney(pack.amount_minor, pack.currency),
                  })}
                </p>
              ) : null}
              {report ? (
                <p>
                  {t("report", {
                    price: formatMoney(report.amount_minor, report.currency),
                  })}
                </p>
              ) : null}
              <p>{t("currency")}</p>
            </div>
          </>
        ) : (
          <p role="status" className="rounded-md border p-3 text-sm">
            {t("unavailable")}
          </p>
        )}
      </main>
    </div>
  );
}
