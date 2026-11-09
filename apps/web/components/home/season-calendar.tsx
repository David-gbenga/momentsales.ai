"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { Badge, Card } from "@/components/ui/primitives";
import { useWorkspace } from "@/lib/ledger";
import { formatDay, useSeasonCalendar } from "@/lib/research";

/** Home: the next three UK retail moments with act-by dates (FR-12, AC-26). */
export function SeasonCalendarCard() {
  const t = useTranslations("calendar");
  const ws = useWorkspace();
  const { data } = useSeasonCalendar();
  if (!data) return null;
  return (
    <Card className="grid gap-3 p-4" data-testid="season-calendar">
      <h2 className="text-lg font-semibold">{t("title")}</h2>
      {data.moments.length ? (
        <ul className="grid gap-3 sm:grid-cols-3">
          {data.moments.map((m) => (
            <li key={m.key} className="grid gap-0.5">
              <span className="font-medium">{m.name}</span>
              <span className="text-sm">
                {formatDay(m.date)} · {t("daysAway", { count: m.days_away })}
              </span>
              <span className="text-sm text-muted-foreground">
                {t("actBy", { date: formatDay(m.act_by) })}
                {m.act_by_passed ? <Badge className="ml-2">{t("passed")}</Badge> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      )}
      <p className="text-xs text-muted-foreground" data-testid="calendar-delivery">
        {t(data.delivery_source === "supplier" ? "supplier" : "assumed", { days: data.delivery_days })}
      </p>
      {ws.can("research.run") ? (
        <Link href="/app/research" className="text-sm underline">
          {t("start")}
        </Link>
      ) : null}
    </Card>
  );
}
