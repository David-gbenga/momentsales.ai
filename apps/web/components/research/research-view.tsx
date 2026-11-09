"use client";

import { formatMoney, LAUNCH_COUNTRY } from "@momentsales/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ReportsList } from "@/components/reports/reports-list";
import { AudienceForm } from "@/components/research/audience-form";
import { RunProgress } from "@/components/research/run-progress";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";
import { formatDay, SEASONS, useRuns, type Run, type RunCreate } from "@/lib/research";

const WINDOWS = [2, 4, 6, 8, 10, 12] as const;
/** Run cost cap (FR-1, £1.00). The API is the authority; this only words the help text. */
const COST_CAP_MINOR = 100;

function poundsToMinor(value: string): number | null {
  const n = Number(value);
  return value.trim() && Number.isFinite(n) && n >= 0 ? Math.round(n * 100) : null;
}

/** Seasonal research (J2): set up a run, watch it live, and see recent runs. */
export function ResearchView() {
  const t = useTranslations("research");
  const ws = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const runId = params.get("run");
  const runs = useRuns();

  const [windowWeeks, setWindowWeeks] = useState("8");
  const [season, setSeason] = useState("");
  const [category, setCategory] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [useAudience, setUseAudience] = useState(true);
  // One key per form submission, reused if the same submission is retried.
  const [attemptKey, setAttemptKey] = useState(newIdempotencyKey);

  const priceError = useMemo(() => {
    const lo = poundsToMinor(minPrice);
    const hi = poundsToMinor(maxPrice);
    if ((minPrice && lo === null) || (maxPrice && hi === null)) return t("priceInvalid");
    if (lo !== null && hi !== null && lo > hi) return t("priceOrder");
    return undefined;
  }, [minPrice, maxPrice, t]);

  const start = useMutation({
    mutationFn: () => {
      const lo = poundsToMinor(minPrice);
      const hi = poundsToMinor(maxPrice);
      const body: RunCreate = {
        mode: "seasonal",
        country: LAUNCH_COUNTRY.code,
        window_weeks: Number(windowWeeks),
        season_override: (season || null) as RunCreate["season_override"],
        category: category.trim() || null,
        price_band:
          lo !== null || hi !== null ? { min_minor: lo ?? 0, max_minor: hi ?? lo ?? 0, currency: "GBP" } : null,
        use_audience_profile: useAudience,
      };
      return api<Run>("/research-runs", { body, headers: { "Idempotency-Key": attemptKey } });
    },
    onSuccess: async (run) => {
      setAttemptKey(newIdempotencyKey());
      await qc.invalidateQueries({ queryKey: ["research", "runs", ws.id] });
      await qc.invalidateQueries({ queryKey: ["billing", "usage", ws.id] });
      router.push(`${pathname}?run=${run.id}`);
    },
  });
  const limitReached = start.error instanceof ApiError && start.error.type === "plan_limit_reached";

  return (
    <section aria-labelledby="research-title" className="grid gap-8">
      <div className="grid gap-1">
        <h1 id="research-title" className="text-2xl font-semibold tracking-tight">
          {t("title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("intro")}</p>
      </div>

      <Card className="p-4">
        <form
          className="grid gap-4"
          aria-labelledby="new-run-title"
          onSubmit={(e) => {
            e.preventDefault();
            if (!priceError) start.mutate();
          }}
        >
          <h2 id="new-run-title" className="text-lg font-semibold">
            {t("newRun")}
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="run-window" label={t("window")} help={t("windowHelp")}>
              <Select
                id="run-window"
                aria-describedby="run-window-help"
                value={windowWeeks}
                onChange={(e) => setWindowWeeks(e.target.value)}
              >
                {WINDOWS.map((w) => (
                  <option key={w} value={w}>
                    {t("weeks", { count: w })}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="run-season" label={t("season")} help={t("seasonHelp")}>
              <Select
                id="run-season"
                aria-describedby="run-season-help"
                value={season}
                onChange={(e) => setSeason(e.target.value)}
              >
                <option value="">{t("seasonAuto")}</option>
                {SEASONS.map((s) => (
                  <option key={s} value={s}>
                    {t(`seasonNames.${s}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="run-category" label={t("category")} help={t("categoryHelp")}>
              <Input
                id="run-category"
                aria-describedby="run-category-help"
                maxLength={80}
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field id="run-min" label={t("priceMin")} error={priceError}>
                <Input
                  id="run-min"
                  inputMode="decimal"
                  value={minPrice}
                  aria-invalid={!!priceError}
                  onChange={(e) => setMinPrice(e.target.value)}
                />
              </Field>
              <Field id="run-max" label={t("priceMax")}>
                <Input
                  id="run-max"
                  inputMode="decimal"
                  value={maxPrice}
                  aria-invalid={!!priceError}
                  onChange={(e) => setMaxPrice(e.target.value)}
                />
              </Field>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={useAudience} onChange={(e) => setUseAudience(e.target.checked)} />
            {t("useAudience")}
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={start.isPending || !!priceError}>
              {start.isPending ? t("starting") : t("run")}
            </Button>
            <span className="text-sm text-muted-foreground">
              {t("costCap", { cap: formatMoney(COST_CAP_MINOR, "GBP") })}
            </span>
          </div>
          {limitReached ? (
            <Notice tone="error">
              {t("limitReached")}{" "}
              <Link href="/app/billing" className="underline">
                {t("upgrade")}
              </Link>
            </Notice>
          ) : start.error ? (
            <Notice tone="error">{start.error.message}</Notice>
          ) : null}
        </form>
      </Card>

      {runId ? <RunProgress runId={runId} /> : null}

      <section aria-labelledby="recent-title" className="grid gap-2">
        <h2 id="recent-title" className="text-lg font-semibold">
          {t("recent")}
        </h2>
        {runs.data?.items.length ? (
          <ul className="grid gap-2" data-testid="recent-runs">
            {runs.data.items.map((run) => (
              <li key={run.id} className="flex flex-wrap items-center gap-3 text-sm">
                <Link href={`${pathname}?run=${run.id}`} className="underline">
                  {t("runOf", { date: formatDay(run.as_of.slice(0, 10)) })}
                </Link>
                <Badge>{t(`status.${run.status}`)}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{t("noRuns")}</p>
        )}
      </section>

      <ReportsList />

      <Card className="p-4">
        <AudienceForm />
      </Card>
    </section>
  );
}
