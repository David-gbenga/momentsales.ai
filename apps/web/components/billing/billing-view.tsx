"use client";

import {
  CANCEL_REASONS,
  formatMoney,
  PAUSE_MONTHS,
  usagePercent,
  type CancelReason,
} from "@momentsales/shared";
import { useTranslations, useFormatter } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { PlanPicker } from "@/components/billing/plan-picker";
import { UpgradePrompt } from "@/components/billing/upgrade-prompt";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import {
  hasFullAccess,
  needsPlan,
  planLimitOf,
  useCatalog,
  useInvoices,
  useRefreshBilling,
  useSubscription,
  useUsage,
  type PlanLimit,
  type Subscription,
} from "@/lib/billing";
import { useWorkspace } from "@/lib/ledger";
import { IS_MOCK_MODE } from "@/lib/utils";

export function BillingView() {
  const t = useTranslations("billing");
  const params = useSearchParams();
  const checkout = params.get("checkout");
  const purchase = params.get("purchase");
  const ws = useWorkspace();
  // Back from Checkout: poll until the webhook has updated the subscription.
  const sub = useSubscription(checkout === "success");
  const catalog = useCatalog();
  const isOwner = ws.can("billing.manage");
  const s = sub.data;

  return (
    <section aria-labelledby="billing-title" className="grid max-w-5xl gap-6">
      <h1 id="billing-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      {checkout === "success" && !hasFullAccess(s?.status) ? (
        <Notice>{t("checkoutDone")}</Notice>
      ) : null}
      {checkout === "cancelled" || purchase === "cancelled" ? (
        <Notice>{t("checkoutCancelled")}</Notice>
      ) : null}
      {purchase === "success" ? (
        <Notice tone="success">{t("purchaseDone")}</Notice>
      ) : null}
      {!s ? (
        <p role="status">{t("loading")}</p>
      ) : (
        <>
          <StatusCard sub={s} isOwner={isOwner} />
          {needsPlan(s.status) || s.status === "canceled" ? (
            <Card className="grid gap-4">
              <h2 className="text-lg font-semibold">{t("choosePlan")}</h2>
              {isOwner ? (
                <>
                  <p className="text-sm text-muted-foreground">
                    {s.trial_used
                      ? t("afterTrial")
                      : t("choosePlanHelp", {
                          runs: catalog.data?.trial.runs ?? 3,
                        })}
                  </p>
                  {catalog.data ? (
                    <PlanPicker
                      plans={catalog.data.plans}
                      trialAvailable={!s.trial_used}
                      canChoose
                      initialPlan={params.get("plan")}
                      initialInterval={
                        params.get("interval") === "month" ? "month" : "year"
                      }
                    />
                  ) : null}
                </>
              ) : (
                <Notice>{t("waitingForOwner")}</Notice>
              )}
            </Card>
          ) : null}
          {ws.can("billing.usage.read") && !needsPlan(s.status) ? (
            <UsageCard />
          ) : null}
          {isOwner ? <OwnerActions sub={s} /> : null}
          {ws.can("billing.invoices.read") ? <Invoices /> : null}
          {IS_MOCK_MODE && ws.can("research.run") ? <DevRunTool /> : null}
        </>
      )}
    </section>
  );
}

function StatusCard({ sub, isOwner }: { sub: Subscription; isOwner: boolean }) {
  const t = useTranslations("billing");
  const format = useFormatter();
  const refresh = useRefreshBilling();
  const [error, setError] = useState<string | null>(null);
  const date = (d: string) =>
    format.dateTime(new Date(d), { dateStyle: "long" });

  async function keep() {
    setError(null);
    try {
      await api("/billing/keep", { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Card className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          {t("plan")}: {sub.plan?.name ?? t("status.none")}
          {sub.interval ? ` · ${t(`interval.${sub.interval}`)}` : null}
        </h2>
        <Badge data-testid="subscription-status">
          {t(`status.${sub.status}` as "status.none")}
        </Badge>
      </div>
      <p
        className="text-sm text-muted-foreground"
        data-testid="subscription-dates"
      >
        {sub.status === "trialing" && sub.trial_end
          ? t("trialEnds", { date: date(sub.trial_end) })
          : sub.status === "paused" && sub.pause_until
            ? t("pausedUntil", { date: date(sub.pause_until) })
            : sub.current_period_end && sub.status !== "canceled"
              ? sub.cancel_at_period_end
                ? t("endsOn", { date: date(sub.current_period_end) })
                : t("renews", { date: date(sub.current_period_end) })
              : null}
      </p>
      {sub.cancel_at_period_end && sub.current_period_end ? (
        <div className="grid gap-2">
          <Notice>
            {t("cancelScheduled", { date: date(sub.current_period_end) })}
          </Notice>
          {isOwner ? (
            <Button
              type="button"
              variant="outline"
              className="w-fit"
              onClick={() => void keep()}
            >
              {t("keep")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}

function UsageBar({
  label,
  used,
  allowed,
  testId,
}: {
  label: string;
  used: number;
  allowed: number | null;
  testId: string;
}) {
  const t = useTranslations("billing");
  const pct = usagePercent(used, allowed);
  const text =
    allowed === null
      ? t("usedUnlimited", { used })
      : t("usedOf", { used, allowed });
  return (
    <div className="grid gap-1">
      <div className="flex justify-between gap-2 text-sm">
        <span>{label}</span>
        <span className="tabular-nums" data-testid={testId}>
          {text}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={text}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function UsageCard() {
  const t = useTranslations("billing");
  const usage = useUsage();
  const u = usage.data;
  if (!u) return null;
  return (
    <Card className="grid gap-4">
      <h2 className="text-lg font-semibold">{t("usage")}</h2>
      <UsageBar
        label={t("runs")}
        used={u.research_runs.used}
        allowed={u.research_runs.allowed}
        testId="usage-runs"
      />
      {u.research_runs.pack_remaining + u.research_runs.single_remaining > 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="usage-extra">
          {t("extraRuns", {
            pack: u.research_runs.pack_remaining,
            single: u.research_runs.single_remaining,
          })}
        </p>
      ) : null}
      <UsageBar
        label={t("seats")}
        used={u.seats.used}
        allowed={u.seats.allowed}
        testId="usage-seats"
      />
      <UsageBar
        label={t("listings")}
        used={u.live_listings.used}
        allowed={u.live_listings.allowed}
        testId="usage-listings"
      />
      <p className="text-xs text-muted-foreground">{t("listingsLater")}</p>
    </Card>
  );
}

function OwnerActions({ sub }: { sub: Subscription }) {
  const t = useTranslations("billing");
  const catalog = useCatalog();
  const refresh = useRefreshBilling();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [months, setMonths] = useState<number>(1);
  const [cancelling, setCancelling] = useState(false);
  const pack = catalog.data?.addons.find((a) => a.kind === "run_pack");
  const report = catalog.data?.addons.find((a) => a.kind === "single_report");
  const subscribed = hasFullAccess(sub.status);

  async function go(path: string) {
    setError(null);
    try {
      const { url } = await api<{ url: string }>(path, { method: "POST" });
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function post(path: string, body?: unknown) {
    setError(null);
    try {
      await api(path, { method: "POST", body });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Card className="grid gap-4">
      {sub.has_billing_account && !needsPlan(sub.status) ? (
        <div className="grid gap-2">
          <Button
            type="button"
            className="w-fit"
            onClick={() => void go("/billing/portal")}
          >
            {t("manage")}
          </Button>
          <p className="text-sm text-muted-foreground">{t("manageHelp")}</p>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {pack && subscribed ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => void go("/billing/packs/checkout")}
          >
            {t("buyPack", {
              runs: pack.runs,
              price: formatMoney(pack.amount_minor, pack.currency),
            })}
          </Button>
        ) : null}
        {report ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => void go("/billing/report-checkout")}
          >
            {t("buyReport", {
              price: formatMoney(report.amount_minor, report.currency),
            })}
          </Button>
        ) : null}
      </div>
      {sub.status === "paused" ? (
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          onClick={() => void post("/billing/resume")}
        >
          {t("resume")}
        </Button>
      ) : null}
      {sub.status === "active" && !sub.cancel_at_period_end ? (
        <div className="grid gap-2 border-t pt-4">
          <h3 className="font-medium">{t("pause")}</h3>
          <p className="text-sm text-muted-foreground">{t("pauseHelp")}</p>
          <div className="flex flex-wrap items-end gap-2">
            <Field id="pause-months" label={t("pause")}>
              <Select
                id="pause-months"
                value={months}
                onChange={(e) => setMonths(Number(e.target.value))}
              >
                {PAUSE_MONTHS.map((m) => (
                  <option key={m} value={m}>
                    {t("pauseMonths", { count: m })}
                  </option>
                ))}
              </Select>
            </Field>
            <Button
              type="button"
              variant="outline"
              onClick={() => void post("/billing/pause", { months })}
            >
              {t("pauseConfirm")}
            </Button>
          </div>
        </div>
      ) : null}
      {(sub.status === "active" || sub.status === "trialing") &&
      !sub.cancel_at_period_end ? (
        <div className="grid gap-2 border-t pt-4">
          {cancelling ? (
            <CancelSurvey
              onDone={(text) => {
                setCancelling(false);
                setNotice(text);
                void refresh();
              }}
              onBack={() => setCancelling(false)}
            />
          ) : (
            <Button
              type="button"
              variant="ghost"
              className="w-fit"
              onClick={() => setCancelling(true)}
            >
              {t("cancel")}
            </Button>
          )}
        </div>
      ) : null}
      {notice ? <Notice tone="success">{notice}</Notice> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}

function CancelSurvey({
  onDone,
  onBack,
}: {
  onDone: (text: string) => void;
  onBack: () => void;
}) {
  const t = useTranslations("billing.survey");
  const [reason, setReason] = useState<CancelReason>("no_sales");
  const [text, setText] = useState("");
  const [offer, setOffer] = useState<"pause" | "discount" | "none" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  async function send(decision?: "cancel" | "accept_offer") {
    setError(null);
    try {
      const res = await api<{
        offer: "pause" | "discount" | "none";
        outcome: string | null;
      }>("/billing/cancel-survey", {
        body: { reason, text: text || null, decision: decision ?? null },
      });
      if (!decision) {
        setOffer(res.offer);
        return;
      }
      onDone(
        res.outcome === "accepted_pause"
          ? t("pausedDone")
          : res.outcome === "accepted_discount"
            ? t("discountApplied")
            : t("cancelled"),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div className="grid gap-3" aria-labelledby="survey-title">
      <h3 id="survey-title" className="font-medium">
        {t("title")}
      </h3>
      {offer === null ? (
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <fieldset className="grid gap-2">
            <legend className="text-sm font-medium">{t("question")}</legend>
            {CANCEL_REASONS.map((r) => (
              <label
                key={r}
                className="flex min-h-9 items-center gap-2 text-sm"
              >
                <input
                  type="radio"
                  name="cancel-reason"
                  value={r}
                  checked={reason === r}
                  onChange={() => setReason(r)}
                />
                {t(`reasons.${r}`)}
              </label>
            ))}
          </fieldset>
          <Field id="cancel-text" label={t("details")}>
            <textarea
              id="cancel-text"
              value={text}
              maxLength={1000}
              onChange={(e) => setText(e.target.value)}
              className="min-h-20 rounded-md border border-input bg-background p-2 text-sm"
            />
          </Field>
          <div className="flex gap-2">
            <Button type="submit">{t("next")}</Button>
            <Button type="button" variant="ghost" onClick={onBack}>
              {t("back")}
            </Button>
          </div>
        </form>
      ) : (
        <div className="grid gap-3">
          {offer !== "none" ? (
            <Notice>
              {offer === "pause" ? t("offerPause") : t("offerDiscount")}
            </Notice>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {offer !== "none" ? (
              <Button type="button" onClick={() => void send("accept_offer")}>
                {offer === "pause" ? t("acceptPause") : t("acceptDiscount")}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="destructive"
              onClick={() => void send("cancel")}
            >
              {t("confirmCancel")}
            </Button>
            <Button type="button" variant="ghost" onClick={onBack}>
              {t("back")}
            </Button>
          </div>
        </div>
      )}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </div>
  );
}

function Invoices() {
  const t = useTranslations("billing");
  const format = useFormatter();
  const invoices = useInvoices();
  const rows = invoices.data ?? [];
  return (
    <Card className="grid gap-3 p-0">
      <h2 className="px-6 pt-6 text-lg font-semibold">{t("invoices")}</h2>
      {rows.length === 0 ? (
        <p className="px-6 pb-6 text-sm text-muted-foreground">
          {t("noInvoices")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">{t("invoices")}</caption>
            <thead className="border-b text-left text-muted-foreground">
              <tr>
                <th scope="col" className="p-3 font-medium">
                  {t("invoiceNumber")}
                </th>
                <th scope="col" className="p-3 font-medium">
                  {t("invoiceDate")}
                </th>
                <th scope="col" className="p-3 text-right font-medium">
                  {t("invoiceTotal")}
                </th>
                <th scope="col" className="p-3 font-medium">
                  {t("invoiceStatus")}
                </th>
                <th scope="col" className="p-3 font-medium">
                  <span className="sr-only">{t("invoiceOpen")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.id} className="border-b last:border-0">
                  <td className="p-3 font-mono">{i.number ?? "—"}</td>
                  <td className="p-3">
                    {i.issued_at
                      ? format.dateTime(new Date(i.issued_at), {
                          dateStyle: "medium",
                        })
                      : "—"}
                  </td>
                  <td className="p-3 text-right tabular-nums">
                    {formatMoney(i.total_minor, i.currency)}
                  </td>
                  <td className="p-3">{i.status}</td>
                  <td className="p-3">
                    {i.hosted_invoice_url ? (
                      <a
                        href={i.hosted_invoice_url}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        {t("invoiceOpen")}
                      </a>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** Mock mode only: spend one metered research run (AC-15 before Phase 2's research). */
function DevRunTool() {
  const t = useTranslations("billing");
  const refresh = useRefreshBilling();
  const [result, setResult] = useState<string | null>(null);
  const [limit, setLimit] = useState<PlanLimit | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function spendRun() {
    setResult(null);
    setLimit(null);
    setError(null);
    try {
      const { pool } = await api<{ pool: string }>("/dev/billing/simulate-run", { method: "POST" });
      setResult(t("runUsed", { pool }));
      await refresh();
    } catch (err) {
      const hit = planLimitOf(err);
      if (hit) setLimit(hit);
      else setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Card className="grid gap-3">
      <h2 className="text-lg font-semibold">{t("devTools")}</h2>
      <p className="text-sm text-muted-foreground">{t("devToolsHelp")}</p>
      <Button type="button" variant="outline" className="w-fit" onClick={() => void spendRun()}>
        {t("useRun")}
      </Button>
      {result ? <Notice tone="success">{result}</Notice> : null}
      {limit ? <UpgradePrompt limit={limit} /> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}
