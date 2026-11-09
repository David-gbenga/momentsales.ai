"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { DemandResults } from "@/components/research/demand-results";
import { Badge, Card, Notice } from "@/components/ui/primitives";
import { DataTable, Td } from "@/components/ui/table";
import { formatDay, seasonOf, TERMINAL, useRun, type Run } from "@/lib/research";

const KNOWN_AGENTS = new Set(["season", "trend", "marketplace", "supplier", "scoring", "compliance", "report"]);

/** One run: live status and steps, spend against the cap, then the season results. */
export function RunProgress({ runId }: { runId: string }) {
  const t = useTranslations("research");
  const { data: run, error } = useRun(runId);
  if (error) return <Notice tone="error">{error.message}</Notice>;
  if (!run) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  return (
    <section aria-labelledby="run-title" className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="run-title" className="text-lg font-semibold">
          {t("runOf", { date: formatDay(run.as_of.slice(0, 10)) })}
        </h2>
        <Badge data-testid="run-status">{t(`status.${run.status}`)}</Badge>
        <span className="text-sm text-muted-foreground" data-testid="run-spend">
          {t("spend", {
            spent: formatMoney(run.cost_minor, run.cost_currency),
            cap: formatMoney(run.cost_cap_minor, run.cost_currency),
          })}
        </span>
      </div>
      <Steps run={run} />
      {run.status === "partial" ? <Notice>{t("partialNote")}</Notice> : null}
      {run.status === "failed" ? (
        <Notice tone="error">
          {t("failed")} {run.run_refunded ? t("refunded") : null}
        </Notice>
      ) : null}
      <SeasonResults run={run} />
      <DemandResults run={run} />
      {run.demand?.report_id ? (
        <Link
          href={`/app/reports/${run.demand.report_id}`}
          className="w-fit rounded-md border px-4 py-2 text-sm font-medium underline-offset-4 hover:underline"
          data-testid="view-report"
        >
          {t("viewReport")}
        </Link>
      ) : TERMINAL.has(run.status) && run.status !== "failed" ? (
        <Notice>{t("noReport")}</Notice>
      ) : null}
    </section>
  );
}

function Steps({ run }: { run: Run }) {
  const t = useTranslations("research");
  return (
    <Card className="p-4">
      <h3 className="mb-2 text-sm font-medium">{t("progress")}</h3>
      <ol className="grid gap-2" aria-live="polite" data-testid="run-steps">
        {(run.steps ?? []).length === 0 ? (
          <li className="text-sm text-muted-foreground">{t(`status.${run.status}`)}</li>
        ) : null}
        {(run.steps ?? []).map((step) => (
          <li key={step.agent} className="grid gap-0.5">
            <span className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">
                {KNOWN_AGENTS.has(step.agent) ? t(`agents.${step.agent}`) : step.agent}
              </span>
              <Badge>{t(`stepStatus.${step.status}`)}</Badge>
            </span>
            {step.summary ? <span className="text-sm text-muted-foreground">{step.summary}</span> : null}
            {step.error ? <span className="text-sm text-destructive">{step.error}</span> : null}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function SeasonResults({ run }: { run: Run }) {
  const t = useTranslations("research");
  const season = seasonOf(run);
  if (!season) return null;
  const seasonName = (s: string) => t(`seasonNames.${s}`);
  return (
    <div className="grid gap-4">
      <Card className="grid gap-1 p-4" data-testid="season-summary">
        <p className="text-base font-medium">
          {t("seasonNow", { season: seasonName(season.current) })} ·{" "}
          {t("seasonNext", { season: seasonName(season.next) })}
        </p>
        {season.overridden ? (
          <p className="text-sm text-muted-foreground">
            {t("overridden", { detected: seasonName(season.detected) })}
          </p>
        ) : null}
      </Card>
      {season.moments.length ? (
        <DataTable
          caption={t("moments", { weeks: season.window_weeks })}
          head={[t("moment"), t("date"), t("actBy")]}
        >
          {season.moments.map((m) => (
            <tr key={m.key}>
              <Td>{m.name}</Td>
              <Td>{formatDay(m.date)}</Td>
              <Td>
                {formatDay(m.act_by)}
                {m.act_by_passed ? <Badge className="ml-2">{t("actByPassed")}</Badge> : null}
              </Td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Notice>{t("noMoments")}</Notice>
      )}
      <p className="text-sm text-muted-foreground">{t("actByHelp", { days: season.assumed_delivery_days })}</p>
      <div className="grid gap-1">
        <h3 className="text-sm font-medium">{t("holidays")}</h3>
        {season.public_holidays === null ? (
          <p className="text-sm" data-testid="holidays-unavailable">
            {t("notAvailable")}
          </p>
        ) : season.public_holidays.length ? (
          <ul className="text-sm">
            {season.public_holidays.map((h) => (
              <li key={`${h.date}-${h.name}`}>
                {formatDay(h.date)}: {h.name}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm">{t("holidaysNone")}</p>
        )}
        {season.sources.map((s) => (
          <p key={s.snapshot_id} className="text-xs text-muted-foreground">
            {t("source", { provider: s.provider, when: formatDay(s.fetched_at.slice(0, 10)) })}
          </p>
        ))}
      </div>
    </div>
  );
}
