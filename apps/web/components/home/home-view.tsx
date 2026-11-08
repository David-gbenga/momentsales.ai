"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { BarChartCard } from "@/components/charts/bar-chart";
import { SeasonCalendarCard } from "@/components/home/season-calendar";
import { EmptyState } from "@/components/sections/empty-state";
import { Card, Notice } from "@/components/ui/primitives";
import { DataTable, Num, Td } from "@/components/ui/table";
import { monthStart, ukToday, useLedgerQuery, useWorkspace, type SalesSummary, type SpendPage } from "@/lib/ledger";

/** KPI cards and this month's sales. Refreshes every 5 s so a new sale shows up well inside
 * the 10-second target (AC-6). */
export function HomeView() {
  const t = useTranslations("home");
  const ws = useWorkspace();
  const today = ukToday();
  const month = useLedgerQuery<SalesSummary>(
    "home",
    `/sales/summary?period=day&start=${monthStart(today)}&end=${today}`,
    "orders.read",
    5000,
  );
  const spends = useLedgerQuery<SpendPage>("home", "/spends?limit=1", "ledger.read", 15000);

  if (!ws.can("orders.read")) {
    return (
      <div className="grid gap-4">
        <SeasonCalendarCard />
        <EmptyState section="home" />
        <Notice>{t("viewerNote")}</Notice>
      </div>
    );
  }
  const buckets = month.data?.buckets ?? [];
  const todayBucket = buckets.find((b) => b.period_start === today);
  const cards: [string, string, string][] = [
    ["todaySales", formatMoney(todayBucket?.gross_minor ?? 0), "home-today"],
    ["monthNet", formatMoney(month.data?.totals.net_minor ?? 0), "home-month-net"],
  ];
  if (spends.data) cards.push(["missingReceipts", String(spends.data.missing_receipts), "home-missing"]);

  return (
    <section aria-labelledby="home-title" className="grid gap-6">
      <h1 id="home-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      <SeasonCalendarCard />
      <dl className="grid gap-3 sm:grid-cols-3" aria-live="polite">
        {cards.map(([key, value, testId]) => (
          <Card key={key} className="p-4">
            <dt className="text-sm text-muted-foreground">{t(key)}</dt>
            <dd className="text-2xl font-semibold tabular-nums" data-testid={testId}>
              {value}
            </dd>
          </Card>
        ))}
      </dl>
      {buckets.length ? (
        <>
          <BarChartCard
            title={t("salesChart")}
            data={buckets.map((b) => ({ label: b.period_start.slice(8), value: b.net_minor }))}
            valueLabel={t("monthNet")}
          />
          <DataTable caption={t("salesChart")} captionHidden head={["", "Net"]}>
            {buckets.map((b) => (
              <tr key={b.period_start}>
                <Td>{b.period_start}</Td>
                <Num>{formatMoney(b.net_minor)}</Num>
              </tr>
            ))}
          </DataTable>
        </>
      ) : (
        <Notice>{t("noSales")}</Notice>
      )}
      {ws.can("ledger.read") ? (
        <Link href="/app/ledger" className="text-sm underline">
          {t("openLedger")}
        </Link>
      ) : null}
    </section>
  );
}
