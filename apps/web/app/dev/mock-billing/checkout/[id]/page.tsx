"use client";

import { formatMoney } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { notFound, useParams } from "next/navigation";
import { useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

interface MockBillingSession {
  id: string;
  mode: "subscription" | "payment";
  status: string;
  trial_days: number | null;
  name: string;
  amount_minor: number;
  currency: string;
  interval: "month" | "year" | null;
  cancel_url: string;
}

/** Stand-in for Stripe Checkout (subscriptions, packs, reports) when PROVIDER_MODE=mock. */
export default function MockBillingCheckoutPage() {
  const t = useTranslations("mockBilling");
  const { id } = useParams<{ id: string }>();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const session = useQuery({
    queryKey: ["mock-billing", id],
    queryFn: () =>
      api<MockBillingSession>(`/dev/mock-billing/checkout/${id}`, {
        auth: false,
        workspace: false,
      }),
    enabled: IS_MOCK_MODE,
  });
  if (!IS_MOCK_MODE) notFound();

  async function pay() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ redirect_url: string }>(
        `/dev/mock-billing/checkout/${id}/pay`,
        {
          method: "POST",
          auth: false,
          workspace: false,
        },
      );
      const next = new URL(res.redirect_url);
      window.location.assign(next.pathname + next.search);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  const s = session.data;
  const price = s ? formatMoney(s.amount_minor, s.currency) : "";
  return (
    <Centered title={t("checkoutTitle")}>
      <p className="text-sm text-muted-foreground">{t("checkoutBody")}</p>
      {s ? (
        <div className="grid gap-4">
          <p className="font-medium">{s.name}</p>
          <p className="text-sm" data-testid="mock-billing-terms">
            {s.mode === "payment"
              ? t("oneOff", { price })
              : s.trial_days
                ? t("trial", {
                    days: s.trial_days,
                    price,
                    interval: s.interval ?? "month",
                  })
                : t("subscribe", { price, interval: s.interval ?? "month" })}
          </p>
          <Button
            type="button"
            disabled={busy || s.status !== "open"}
            onClick={() => void pay()}
          >
            {s.mode === "subscription" && s.trial_days
              ? t("start")
              : t("pay", { price })}
          </Button>
          <a
            href={s.cancel_url}
            className="text-sm underline-offset-4 hover:underline"
          >
            {t("back")}
          </a>
        </div>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Centered>
  );
}
