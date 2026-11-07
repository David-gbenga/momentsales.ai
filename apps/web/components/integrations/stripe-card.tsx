"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Badge, Card, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useLedgerQuery, useWorkspace, type StripeStatus } from "@/lib/ledger";

/** `/stripe connect` lives here. Only the Owner can start or refresh onboarding. */
export function StripeCard() {
  const t = useTranslations("integrations");
  const ws = useWorkspace();
  const qc = useQueryClient();
  const params = useSearchParams();
  const status = useLedgerQuery<StripeStatus>("stripe", "/integrations/stripe", "workspace.read");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isOwner = ws.can("integrations.payments.manage");
  const handled = useRef(false);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api<{ url: string }>("/integrations/stripe/connect", { method: "POST" });
      window.location.assign(url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      const data = await api<StripeStatus>("/integrations/stripe/refresh", { method: "POST" });
      qc.setQueryData(["stripe", ws.id, "/integrations/stripe"], data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  // Back from Stripe: the return URL carries no state, so ask Stripe for the account.
  // An expired or used link lands on ?stripe=refresh: start a fresh link.
  const flow = params.get("stripe");
  useEffect(() => {
    if (handled.current || !isOwner || !ws.id) return;
    handled.current = true;
    // Deferred so state updates happen after the effect, not inside it.
    if (flow === "return") void Promise.resolve().then(refresh);
    else if (flow === "refresh") void Promise.resolve().then(connect);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow, isOwner, ws.id]);

  const s = status.data;
  const state = s?.connected ? (s.status ?? "pending") : "none";
  const due = (s?.requirements?.currently_due as string[] | undefined) ?? [];
  return (
    <Card className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{t("stripe")}</h2>
        <Badge data-testid="stripe-status">{t(`status.${state}`)}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{t("stripeHelp")}</p>
      {s?.test_mode ? <Notice>{t("testMode")}</Notice> : null}
      {s?.connected ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt>{t("account")}</dt>
          <dd className="font-mono">{s.account_id}</dd>
          <dt>{t("charges")}</dt>
          <dd>{s.charges_enabled ? t("enabled") : t("disabled")}</dd>
          <dt>{t("payouts")}</dt>
          <dd>{s.payouts_enabled ? t("enabled") : t("disabled")}</dd>
        </dl>
      ) : null}
      {due.length ? <Notice tone="error">{t("needsAction", { items: due.join(", ") })}</Notice> : null}
      {isOwner ? (
        <div className="flex flex-wrap gap-2">
          {state !== "active" ? (
            <Button onClick={() => void connect()} disabled={busy}>
              {s?.connected ? t("continue") : t("connect")}
            </Button>
          ) : null}
          {s?.connected ? (
            <Button variant="outline" onClick={() => void refresh()} disabled={busy}>
              {t("refresh")}
            </Button>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("ownerOnly")}</p>
      )}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}
