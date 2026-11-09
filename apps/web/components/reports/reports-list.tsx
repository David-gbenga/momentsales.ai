"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { Badge } from "@/components/ui/primitives";
import { formatDay } from "@/lib/research";
import { useReports } from "@/lib/reports";

/** Recent Top 10 reports for this workspace. */
export function ReportsList({ heading = "h2" }: { heading?: "h1" | "h2" }) {
  const Heading = heading;
  const t = useTranslations("reports");
  const reports = useReports();
  return (
    <section aria-labelledby="reports-title" className="grid gap-2">
      <Heading id="reports-title" className={heading === "h1" ? "text-2xl font-semibold tracking-tight" : "text-lg font-semibold"}>
        {t("recent")}
      </Heading>
      {reports.data?.length ? (
        <ul className="grid gap-2" data-testid="recent-reports">
          {reports.data.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 text-sm">
              <Link href={`/app/reports/${r.id}`} className="underline">
                {t("asOf", { date: formatDay(r.generated_at.slice(0, 10)) })}
                {r.top ? ` · ${r.top}` : ""}
              </Link>
              <Badge>{t("items", { count: r.item_count })}</Badge>
              {r.mock ? <Badge>{t("mockBadge")}</Badge> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{t("recentEmpty")}</p>
      )}
    </section>
  );
}
