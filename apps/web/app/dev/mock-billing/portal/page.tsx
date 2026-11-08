"use client";

import { formatMoney } from "@momentsales/shared";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { notFound, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Card, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

interface PortalData {
  workspace_id: string;
  plan_code: string | null;
  interval: "month" | "year" | null;
  status: string;
  plans: {
    code: string;
    name: string;
    prices: {
      interval: "month" | "year";
      amount_minor: number;
      currency: string;
    }[];
  }[];
}

/** Stand-in for the Stripe Customer Portal when PROVIDER_MODE=mock. Plan switches go
 * through the same signed-webhook path as Stripe; the test tools simulate renewals and
 * failed payments (AC-16, AC-17). */
function Portal() {
  const t = useTranslations("mockBilling");
  const params = useSearchParams();
  const customer = params.get("customer") ?? "";
  const qc = useQueryClient();
  const [message, setMessage] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);
  const data = useQuery({
    queryKey: ["mock-portal", customer],
    queryFn: () =>
      api<PortalData>(
        `/dev/mock-billing/portal?customer=${encodeURIComponent(customer)}`,
        { auth: false, workspace: false },
      ),
    enabled: IS_MOCK_MODE && !!customer,
    // Webhooks are processed by the worker; poll so the page shows the result.
    refetchInterval: 1000,
  });
  if (!IS_MOCK_MODE) notFound();

  async function run(path: string, body: unknown) {
    setMessage(null);
    try {
      await api(path, { body, auth: false, workspace: false });
      setMessage({ tone: "success", text: t("done") });
      await qc.invalidateQueries({ queryKey: ["mock-portal", customer] });
    } catch (err) {
      setMessage({
        tone: "error",
        text: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const d = data.data;
  return (
    <Centered title={t("portalTitle")}>
      <p className="text-sm text-muted-foreground">{t("portalBody")}</p>
      {d ? (
        <div className="grid gap-4">
          <p data-testid="mock-portal-current">
            {t("current")}: {d.plan_code ?? "—"} ({d.interval ?? "—"},{" "}
            {d.status})
          </p>
          <ul className="grid gap-2">
            {d.plans.flatMap((p) =>
              p.prices
                .filter(
                  (x) => !(p.code === d.plan_code && x.interval === d.interval),
                )
                .map((x) => (
                  <li key={`${p.code}-${x.interval}`}>
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full justify-between"
                      onClick={() =>
                        void run("/dev/mock-billing/portal/change-plan", {
                          customer,
                          plan_code: p.code,
                          interval: x.interval,
                        })
                      }
                    >
                      <span>
                        {t("switchTo", { plan: p.name, interval: x.interval })}
                      </span>
                      <span className="tabular-nums">
                        {formatMoney(x.amount_minor, x.currency)}
                      </span>
                    </Button>
                  </li>
                )),
            )}
          </ul>
          <Card className="grid gap-2">
            <h2 className="font-medium">{t("devTools")}</h2>
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                void run("/dev/mock-billing/renew", {
                  workspace_id: d.workspace_id,
                })
              }
            >
              {t("renew")}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                void run("/dev/mock-billing/fail-payment", {
                  workspace_id: d.workspace_id,
                })
              }
            >
              {t("fail")}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                void run("/dev/mock-billing/pay-invoice", {
                  workspace_id: d.workspace_id,
                })
              }
            >
              {t("payInvoice")}
            </Button>
          </Card>
          <Link
            href="/app/billing"
            className="text-sm underline-offset-4 hover:underline"
          >
            {t("back")}
          </Link>
        </div>
      ) : null}
      {message ? <Notice tone={message.tone}>{message.text}</Notice> : null}
    </Centered>
  );
}

export default function MockBillingPortalPage() {
  return (
    <Suspense>
      <Portal />
    </Suspense>
  );
}
